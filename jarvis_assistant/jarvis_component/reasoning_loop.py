"""
JARVIS — Observer Tier 2: the reasoning loop (v5.7.00).

When the classifier flags an event as worth considering, this tier takes a
full contextual look. Local templates handle 95%+ of events — LLM fallback
is now reserved for genuinely ambiguous multi-factor decisions only.
  - The event that was flagged
  - The current home state summary
  - JARVIS's persona and prime directive
  - The urgency the classifier suggested
  - A list of recent announcements (so it doesn't repeat itself)

It returns either:
  - A "speak" decision: {"speak": true, "message": "...", "urgency": "..."}
  - A "stay silent" decision: {"speak": false, "reason": "..."}

The model is Gemini Flash by default — more capable than Flash-Lite but
still cheap. Uses thinking_budget normally (lets it reason).

The observer calls this, then hands the result (if speak=true) to the
output gate for rate limiting and routing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .directive_helper import build_system_prompt

_LOGGER = logging.getLogger(__name__)

# A safety sensor is only a genuine emergency when it ENTERS an active state.
# Going unavailable/unknown or returning to normal (off/dry/clear) is not.
_ACTIVE_TRIGGER_STATES = {"on", "detected", "wet", "triggered", "unsafe"}


def _summary_new_state(evt: str):
    """Post-transition state from a 'changed from X to Y' event summary."""
    m = re.search(r"\bto\s+([a-z_]+)\b", evt)
    return m.group(1) if m else None


def _parse_summary(evt: str):
    """Best-effort structured fields from 'FNAME (entity_id) ... from X to Y'."""
    friendly = ""
    m = re.match(r"(.+?)\s*\(", evt)
    if m:
        friendly = m.group(1).strip()
    entity_id = ""
    m = re.search(r"\(([\w.]+)\)", evt)
    if m:
        entity_id = m.group(1)
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    from_s = to_s = ""
    m = re.search(r"changed from (\S+) to (\S+)", evt)
    if m:
        from_s, to_s = m.group(1), m.group(2)
    return friendly, entity_id, domain, from_s, to_s


async def _decision_from_cache(hass, cached: dict, *, honorific: str,
                               friendly_name: str, entity_id: str,
                               device_class: str, to_state: str,
                               anyone_home: bool) -> dict:
    """Build a decision from a learned cache entry (no cloud call). The verdict
    comes from the cache; the VOICE comes from the Local Mind's composer, with a
    quick history lookup so even replayed decisions carry novelty context."""
    if not cached.get("speak"):
        return {"speak": False, "reason": "learned: routine pattern (local cache)"}
    from . import local_mind
    from datetime import datetime
    hour = datetime.now().hour
    grade = "unknown"
    try:
        prof = await hass.async_add_executor_job(
            local_mind.history_profile, entity_id, to_state, hour)
        grade = prof.get("grade", "unknown")
    except Exception:
        pass
    urgency = cached.get("urgency", "medium")
    msg = local_mind.compose_announcement(
        honorific, friendly_name, entity_id, to_state, device_class,
        hour=hour, novelty=grade, away=not anyone_home,
        escalated=urgency in ("high", "critical"), urgency=urgency)
    return {
        "speak": True,
        "message": msg,
        "urgency": urgency,
        "reason": "learned (local cache)",
    }


def _local_fallback(urgency: str, friendly_name: str, to_state: str,
                    honorific: str) -> dict:
    """
    Deterministic local decision when the cloud is unavailable and nothing is
    cached. Surfaces important events; stays quiet for routine ones.
    """
    if urgency in ("critical", "high"):
        try:
            from . import local_mind
            msg = local_mind.compose_announcement(
                honorific, friendly_name, "", to_state, escalated=True)
        except Exception:
            name = friendly_name or "A device"
            msg = f"{honorific.title()}, attention required: {name}."
        return {
            "speak": True, "message": msg, "urgency": urgency,
            "reason": "local fallback (cloud unavailable)",
        }
    return {"speak": False, "reason": "local fallback: non-urgent, cloud unavailable"}


# ── Local reasoning templates (zero API cost) ────────────────────────────────

def _lm_compose(honorific, friendly_name, *, device_class="", to_state="",
                away=False, escalated=False) -> str:
    """Compose template speech through the Local Mind's voice; never raises."""
    try:
        from . import local_mind
        return local_mind.compose_announcement(
            honorific, friendly_name, "", to_state, device_class,
            away=away, escalated=escalated)
    except Exception:
        return f"{honorific.title()}, {friendly_name or 'a device'} requires attention."


def _try_local_reasoning(
    event_summary: str,
    urgency: str,
    category: str,
    honorific: str,
    recent_announcements: list[str],
    anyone_home: bool = False,
) -> Optional[dict]:
    """
    Handle common events with templated responses (v5.7.00).
    Expanded to cover 95%+ of observer events locally — LLM fallback
    is now rare (genuinely ambiguous multi-factor decisions only).

    Returns a decision dict or None (fall through to LLM).
    """
    evt = event_summary.lower()

    # Don't repeat recent announcements
    for ann in recent_announcements[-5:]:
        if ann.lower()[:40] in evt[:40]:
            return {"speak": False, "reason": "recently announced similar event"}

    # ── Safety-critical (only when actually TRIGGERED) ───────────────
    for kw in ("smoke", "carbon_monoxide", "co_alarm", "gas", "leak",
               "moisture", "flood", "glass_break"):
        if kw in evt:
            new_st = _summary_new_state(evt)
            active = (new_st in _ACTIVE_TRIGGER_STATES) if new_st else \
                     any(w in evt for w in ("detected", "triggered", " wet"))
            if not active:
                # Sensor went unavailable/unknown or returned to normal — a
                # connectivity blip or all-clear is NOT an emergency.
                return {"speak": False,
                        "reason": f"{kw} sensor not in triggered state ({new_st or 'n/a'})"}
            m_dev = re.search(r"([\w\s]+?)\s*\(", evt)
            dev = m_dev.group(1).strip().title() if m_dev else ""
            src = f" from {dev}" if dev else ""
            return {
                "speak": True,
                "message": f"{honorific.title()}, a {kw.replace('_', ' ')} alert{src} — immediate attention required.",
                "urgency": "critical",
            }

    # ── Alarm triggered ──────────────────────────────────────────────
    if category == "security" and urgency == "critical":
        # A real alarm-panel trigger is an emergency regardless of occupancy.
        # But open doors/windows and unlocked locks are NORMAL when a registered
        # user is home — don't raise those as a "security alert".
        if "alarm_control_panel." not in evt and anyone_home:
            return {"speak": False,
                    "reason": "security event but a registered user is home — normal"}
        return {
            "speak": True,
            "message": f"{honorific.title()}, a security alert has been triggered. Immediate attention required.",
            "urgency": "critical",
        }

    # ── Person arrived ───────────────────────────────────────────────
    m = re.search(r"(\w+)\s+(?:arrived|came)\s+home|person\.(\w+).*not_home.*→.*home", evt)
    if m or ("arrived" in evt and category == "presence"):
        name = "Someone"
        nm = re.search(r"person\.(\w+)", evt)
        if nm:
            name = nm.group(1).replace("_", " ").title()
        return {
            "speak": True,
            "message": f"{honorific.title()}, {name} has arrived home.",
            "urgency": "medium",
        }

    # ── Person left ──────────────────────────────────────────────────
    if ("left" in evt or "not_home" in evt) and category == "presence":
        name = "Someone"
        nm = re.search(r"person\.(\w+)", evt)
        if nm:
            name = nm.group(1).replace("_", " ").title()
        return {
            "speak": True,
            "message": f"{honorific.title()}, {name} has left the premises.",
            "urgency": "low",
        }

    # ── Door/window opened ───────────────────────────────────────────
    if category == "doors_windows" and (_summary_new_state(evt) == "on"
                                         or "open" in evt or "off → on" in evt
                                         or "off→on" in evt):
        # A door/window opening is normal household activity when someone is
        # home — only worth surfacing when away (possible entry) or critical.
        if anyone_home and urgency != "critical":
            return {"speak": False, "reason": "door/window opened but a user is home — normal"}
        # Extract device name from the event summary
        m_dev = re.search(r"([\w\s]+?)\s*\(", evt)
        dev_name = m_dev.group(1).strip().title() if m_dev else "A door"
        if urgency in ("high", "critical"):
            return {
                "speak": True,
                "message": _lm_compose(honorific, dev_name, device_class="door",
                                       to_state="open", away=not anyone_home,
                                       escalated=urgency in ("high", "critical")),
                "urgency": urgency,
            }
        if urgency == "medium":
            return {
                "speak": True,
                "message": _lm_compose(honorific, dev_name, device_class="door",
                                       to_state="open", away=not anyone_home,
                                       escalated=urgency in ("high", "critical")),
                "urgency": "medium",
            }
        # Low urgency door open → silent
        return {"speak": False, "reason": "low urgency door event — logged only"}

    # ── Door/window closed ───────────────────────────────────────────
    if category == "doors_windows" and (_summary_new_state(evt) == "off"
                                         or "close" in evt or "on → off" in evt
                                         or "on→off" in evt):
        return {"speak": False, "reason": "door/window closed — normal operation"}

    # ── Lock unlocked ────────────────────────────────────────────────
    if category == "security" and "unlock" in evt:
        # Normal for a lock to be unlocked while a registered user is home.
        if anyone_home:
            return {"speak": False, "reason": "lock unlocked but a user is home — normal"}
        m_dev = re.search(r"([\w\s]+?)\s*\(", evt)
        dev_name = m_dev.group(1).strip().title() if m_dev else "A lock"
        return {
            "speak": True,
            "message": _lm_compose(honorific, dev_name, device_class="lock",
                                   to_state="unlocked", away=not anyone_home,
                                   escalated=True),
            "urgency": "medium",
        }

    # ── Lock locked ──────────────────────────────────────────────────
    if category == "security" and ("locked" in evt and "unlock" not in evt):
        return {"speak": False, "reason": "lock secured — normal operation"}

    # ── Motion detected ──────────────────────────────────────────────
    if "motion" in evt or "occupancy" in evt:
        if urgency in ("medium", "high"):
            m_area = re.search(r"([\w\s]+?)\s*\(", evt)
            area_name = m_area.group(1).strip().title() if m_area else "an area"
            return {
                "speak": True,
                "message": _lm_compose(honorific, area_name, device_class="motion",
                                       to_state="on", away=not anyone_home,
                                       escalated=urgency == "high"),
                "urgency": urgency,
            }
        return {"speak": False, "reason": "routine motion — logged only"}

    # ── Garage door ──────────────────────────────────────────────────
    if "garage" in evt:
        if "open" in evt:
            return {
                "speak": True,
                "message": f"{honorific.title()}, the garage door has been opened.",
                "urgency": urgency if urgency != "low" else "medium",
            }
        if "close" in evt or "closing" in evt:
            return {"speak": False, "reason": "garage closing — normal operation"}

    # ── Battery low ──────────────────────────────────────────────────
    if category == "other" and ("battery" in evt or "low_battery" in evt):
        m_dev = re.search(r"([\w\s]+?)\s*\(", evt)
        dev_name = m_dev.group(1).strip().title() if m_dev else "A device"
        return {
            "speak": True,
            "message": f"{honorific.title()}, {dev_name}'s battery is running low.",
            "urgency": "low",
        }

    # ── Appliance done (washer, dryer, dishwasher) ───────────────────
    for appliance in ("washer", "dryer", "dishwasher", "washing_machine"):
        if appliance in evt and ("idle" in evt or "off" in evt
                                  or "complete" in evt or "not_running" in evt):
            nice = appliance.replace("_", " ").title()
            return {
                "speak": True,
                "message": f"{honorific.title()}, the {nice} cycle appears to be complete.",
                "urgency": "medium",
            }

    # ── Climate alerts ───────────────────────────────────────────────
    if category == "climate":
        # Temperature extremes
        try:
            temp_match = re.search(r"(\d+(?:\.\d+)?)", evt)
            if temp_match:
                temp = float(temp_match.group(1))
                if temp > 90:
                    return {
                        "speak": True,
                        "message": f"{honorific.title()}, indoor temperature has reached {temp}°. You may want to check the climate control.",
                        "urgency": "medium",
                    }
                if temp < 55:
                    return {
                        "speak": True,
                        "message": f"{honorific.title()}, indoor temperature has dropped to {temp}°. Heating may need attention.",
                        "urgency": "medium",
                    }
        except (ValueError, TypeError):
            pass
        return {"speak": False, "reason": "climate change within normal range"}

    # ── Power spike ──────────────────────────────────────────────────
    if category == "energy" and "power" in evt:
        return {"speak": False, "reason": "power spike noted but not urgent enough to announce"}

    # ── Low urgency events → stay silent (safe local default) ────────
    if urgency == "low":
        return {"speak": False, "reason": "low urgency — logged but not announced"}

    # ── Medium / high without a specific template ────────────────────
    # These are ambiguous. Rather than announce blindly (a major source of
    # unnecessary chatter), fall through to the learned cache → cloud path so
    # JARVIS learns the right call for the pattern and announces less over time.
    # If the cloud is unavailable, decide() applies a safe local fallback
    # (high speaks; medium stays quiet).
    return None


REASONING_SYSTEM_APPENDIX = """
You are currently operating in OBSERVER MODE — not responding to a direct \
request, but deciding whether to proactively speak to the user about something \
happening in the house.

You receive a flagged event and must decide: is this worth announcing RIGHT \
NOW? If yes, how would you phrase it in character?

STRONG BIAS TOWARD SILENCE. The user did not ask. Interrupting is costly. \
Only speak if a reasonable butler in your position would feel it's their duty \
to mention this. When in doubt, stay silent.

Consider:
- Is this something the user would want to know NOW, or can it wait?
- Is this something they'd consider useful or annoying?
- Has this been announced recently? (If yes, stay silent unless truly changed.)
- Is the user likely busy / not listening right now?
- If `presence_context` says the user is sleeping, the bar for speaking is \
  MUCH higher. Only speak if it's a real emergency.

URGENCY DEFINITIONS (be strict — misclassifying spams the user):

- "critical" — immediate physical danger requiring the user to wake up / act NOW.
  Examples: smoke alarm, CO alarm, gas leak, water leak, glass break while \
  armed, active intrusion. NOTHING ELSE IS CRITICAL. A door opening is NOT \
  critical. A light turning on is NOT critical. Motion detected is NOT critical.

- "high" — important, user would want to know within the hour but can handle \
  asynchronously. Examples: package delivery, doorbell while away, mail arrival, \
  unexpected window open while away.

- "medium" — useful to mention if the user is present and awake. Examples: \
  laundry cycle complete, garage door left open more than 15 minutes, family \
  member arrived home.

- "low" — minor ambient observation. Examples: lights left on in unoccupied \
  room, indoor temperature drift, routine sensor state change.

DEFAULT TO "low" OR "medium" UNLESS YOU HAVE STRONG EVIDENCE OTHERWISE.

HOW JARVIS REASONS (when you do speak):
- Don't merely report the event — convey what it means. "The garage has been \
open twenty minutes" is better as "The garage has been open twenty minutes, \
{honorific} — worth a glance before dark." You connect the fact to its \
implication.
- Anticipate. If a window is open and the temperature is dropping, the useful \
observation is the combination, not either fact alone.
- Lead with the thing that matters. One clause of substance beats two of padding.
- Stay understated even when flagging something real. JARVIS does not alarm; he \
informs, calmly, and trusts the user to act.

Your response must be JSON only:
  {"speak": false, "reason": "why staying silent"}
  OR
  {"speak": true, "message": "what to say", "urgency": "low|medium|high|critical"}

Keep messages short. One or two sentences. In character. No preamble. No \
exclamation marks unless it is a genuine emergency.
"""


def _parse_reasoning_json(raw: str) -> dict:
    """Parse the LLM's JSON response defensively."""
    raw = raw.strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        _LOGGER.debug("Reasoning: could not parse JSON from: %s", raw[:300])
        return {"speak": False, "reason": "parse_failure"}


def _rich_mode(hass) -> bool:
    """Live read of the panel's Rich Reasoning toggle (runtime_config), with the
    persisted store as fallback. Defaults off — efficiency stays the baseline."""
    try:
        from .const import DOMAIN
        for data in (hass.data.get(DOMAIN) or {}).values():
            if isinstance(data, dict) and isinstance(data.get("runtime_config"), dict):
                v = data["runtime_config"].get("rich_reasoning")
                if v is not None:
                    return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                break
    except Exception:
        pass
    return False


async def decide(
    hass,
    provider,
    *,
    honorific: str,
    event_summary: str,
    home_state_summary: str,
    classifier_urgency: str,
    classifier_category: str,
    recent_announcements: list[str],
    presence_context: str = "",
    anyone_home: bool = False,
    entity_id: str = "",
    device_class: str = "",
    from_state: str = "",
    to_state: str = "",
    friendly_name: str = "",
) -> dict:
    """
    Decide whether to speak about a flagged event.
    Tries local templates first (zero cost), then a learned cache of past cloud
    decisions, then the cloud LLM (whose decision is then learned). When the
    connectivity breaker is OPEN, skips the cloud and decides locally.
    """
    # ── Rich Reasoning mode ───────────────────────────────────────────
    # With API spend a non-issue, the user can flip "Rich Reasoning" on: medium/
    # high events go cloud-FIRST for full-context judgment instead of being
    # short-circuited by local templates or the learned cache. Low-urgency events
    # stay local (a template suffices), the connectivity breaker still guards the
    # call, and any cloud failure falls back through the cache/local path below.
    rich = _rich_mode(hass) and classifier_urgency in ("medium", "high")

    # ── Local reasoning shortcuts (no API call) ──────────────────────
    if not rich:
        local = _try_local_reasoning(
            event_summary, classifier_urgency, classifier_category,
            honorific, recent_announcements, anyone_home,
        )
        if local is not None:
            _LOGGER.info("Reasoning local: %s", local.get("message", local.get("reason", ""))[:80])
            return local

    # ── Learned cache + connectivity breaker (reduce cloud calls) ────
    from . import connectivity, reasoning_cache

    # Backfill structured fields from the summary if not provided.
    if not (domain := (entity_id.split(".", 1)[0] if "." in entity_id else "")):
        f2, e2, d2, fs2, ts2 = _parse_summary(event_summary)
        friendly_name = friendly_name or f2
        entity_id = entity_id or e2
        domain = d2
        from_state = from_state or fs2
        to_state = to_state or ts2

    sig = reasoning_cache.signature(
        domain, device_class, classifier_category, from_state, to_state,
        anyone_home, classifier_urgency)

    cached = reasoning_cache.get(sig)
    if cached is not None and not rich:
        reasoning_cache.note_hit(sig)
        dec = await _decision_from_cache(
            hass, cached, honorific=honorific, friendly_name=friendly_name,
            entity_id=entity_id, device_class=device_class, to_state=to_state,
            anyone_home=anyone_home)
        _LOGGER.info("Reasoning cache hit [%s]: speak=%s", sig, dec.get("speak"))
        return dec

    # No fresh cache → we'd call the cloud. Respect the breaker.
    if not connectivity.allow_request():
        reasoning_cache.note_hit(sig)
        _LOGGER.info("Reasoning: breaker OPEN — Local Mind for [%s]", sig)
        try:
            from . import local_mind
            return await local_mind.assess(
                hass, honorific=honorific, entity_id=entity_id, domain=domain,
                device_class=device_class, category=classifier_category,
                from_state=from_state, to_state=to_state,
                friendly_name=friendly_name, urgency=classifier_urgency,
                anyone_home=anyone_home,
                recent_announcements=recent_announcements)
        except Exception as exc:
            _LOGGER.debug("Local Mind error (%s) — basic fallback", exc)
            return _local_fallback(classifier_urgency, friendly_name, to_state, honorific)

    reasoning_cache.note_cloud_call()
    # Build a directive-infused system prompt using the same helper used
    # by conversation — so the observer's voice matches JARVIS's character.
    base_system = build_system_prompt(
        hass,
        honorific=honorific,
        task_context="observer",
    )

    system = base_system + "\n\n" + REASONING_SYSTEM_APPENDIX

    recent_block = ""
    if recent_announcements:
        recent_block = "\n\nRecent announcements (avoid repeating):\n" + "\n".join(
            f"- {a}" for a in recent_announcements[-5:]
        )

    presence_block = f"\n\nPresence: {presence_context}" if presence_context else ""

    user_msg = (
        f"FLAGGED EVENT:\n{event_summary}\n\n"
        f"Classifier suggested urgency: {classifier_urgency}\n"
        f"Category: {classifier_category}\n\n"
        f"Home state summary:\n{home_state_summary}"
        f"{presence_block}"
        f"{recent_block}\n\n"
        "Decide: speak or stay silent? Respond with JSON only."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    try:
        # provider.chat is SYNC — wrap in executor to avoid blocking event loop.
        # Retry on transient 503 (Gemini "high demand") — 2 retries with backoff.
        response = None
        last_err = None
        for attempt in range(3):
            try:
                response = await hass.async_add_executor_job(
                    lambda: provider.chat(messages, temperature=0.4, max_tokens=200)
                )
                break
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                # Transient errors worth retrying: 503 (overloaded), 429 (rate limit),
                # 500 (internal), timeout. Others fail fast.
                is_transient = (
                    "503" in err_str
                    or "UNAVAILABLE" in err_str
                    or "429" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "500" in err_str
                    or "timeout" in err_str.lower()
                )
                if not is_transient or attempt == 2:
                    raise
                import asyncio
                backoff = 2 ** attempt  # 1s, 2s
                _LOGGER.info(
                    "Reasoning loop transient error (attempt %d), backing off %ds: %s",
                    attempt + 1, backoff, err_str[:120],
                )
                await asyncio.sleep(backoff)

        if response is None:
            raise last_err or RuntimeError("no response after retries")

        # Network call succeeded — close the breaker.
        connectivity.record_success()

        # chat() returns {"text": ..., "tool_calls": [...], "raw": ...}
        content = (
            response.get("text") if isinstance(response, dict)
            else (getattr(response, "content", None)
                  or getattr(response, "text", None)
                  or str(response))
        )
        result = _parse_reasoning_json(content)

        if not isinstance(result, dict):
            return {"speak": False, "reason": "invalid_response"}

        if not result.get("speak"):
            # Learn this "stay silent" decision so the pattern is handled locally next time.
            reasoning_cache.remember(sig, False, classifier_urgency)
            return {
                "speak": False,
                "reason": result.get("reason", "reasoning declined"),
            }

        message = (result.get("message") or "").strip()
        if not message:
            return {"speak": False, "reason": "empty_message"}

        urgency = result.get("urgency", classifier_urgency)
        if urgency not in ("low", "medium", "high", "critical"):
            urgency = classifier_urgency

        # Learn the speak decision (urgency) for this pattern.
        reasoning_cache.remember(sig, True, urgency)

        return {
            "speak": True,
            "message": message,
            "urgency": urgency,
        }
    except Exception as exc:
        _LOGGER.warning("Reasoning loop failed: %s", exc)
        connectivity.record_failure()
        # Fall back to a stale learned decision if we have one, else the Local Mind.
        stale = reasoning_cache.get(sig, ignore_age=True)
        if stale is not None:
            reasoning_cache.note_hit(sig)
            return await _decision_from_cache(
                hass, stale, honorific=honorific, friendly_name=friendly_name,
                entity_id=entity_id, device_class=device_class, to_state=to_state,
                anyone_home=anyone_home)
        try:
            from . import local_mind
            return await local_mind.assess(
                hass, honorific=honorific, entity_id=entity_id, domain=domain,
                device_class=device_class, category=classifier_category,
                from_state=from_state, to_state=to_state,
                friendly_name=friendly_name, urgency=classifier_urgency,
                anyone_home=anyone_home,
                recent_announcements=recent_announcements)
        except Exception as exc:
            _LOGGER.debug("Local Mind error (%s) — basic fallback", exc)
            return _local_fallback(classifier_urgency, friendly_name, to_state, honorific)
