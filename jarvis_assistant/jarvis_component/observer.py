"""
JARVIS — Observer main loop (v5.3).

Subscribes to Home Assistant's state_changed event bus. On each change that
passes pre-filtering + debouncing, routes through the tiered LLM pipeline
and decides whether to speak.

v5.3 changes from v5.2:
  - Audio routing reads HA's area registry instead of flat entity lists
  - Sleep detection uses bedroom_area_ids flag, not separate sleep entity list
  - No observer_ignore list — user shushes via service calls after the fact
  - Observer watches ALL areas (no excluded_areas config)

Pipeline:
  1. Pre-filter (domain whitelist + device_class filtering)
  2. Debounce per entity (no repeats within 30s)
  3. Classifier (Gemini Flash-Lite) — worth considering?
  4. Reasoning (Gemini Flash) — speak or stay silent, in character
  5. Output gate (rate limits, dedupe, mute memory)
  6. Routing (audio_routing.observer_speak_target)
  7. Speak (tts.speak to selected targets) or notify (phone push)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Optional

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.util import dt as dt_util

from . import audio_routing, classifier, output_gate, reasoning_loop, sleep_detection
from .const import (
    CONF_HONORIFIC, CONF_NOTIFY_SERVICE,
    DEFAULT_OBSERVER_QUIET_END, DEFAULT_OBSERVER_QUIET_START,
)
from .llm_provider import create_tier_provider

_LOGGER = logging.getLogger(__name__)


# ─── Pre-filter rules (no LLM cost) ──────────────────────────────────────────
#
# Philosophy: PRE-FILTER AGGRESSIVELY, classify EXPENSIVELY.
# It costs zero dollars to pre-filter an event. It costs real Gemini quota
# every time we let one through. Bias STRONGLY toward dropping.

# Domains we ignore entirely — too noisy or not observation-worthy
IGNORED_DOMAINS = {
    "sun", "zone", "weather", "person", "group", "input_text", "input_number",
    "automation", "script", "scene", "conversation", "stt", "tts",
    "assist_satellite", "assist_pipeline", "update", "button", "number",
    "select", "text", "timer", "counter", "device_tracker", "calendar",
    "sensor",       # re-admitted below ONLY for discrete-state device classes
    "media_player", # you know when you're playing music — don't classify it
    "light",        # you turned it on; not observation-worthy
    "switch",       # same
}

# Sensor device_classes we DO care about. ONLY discrete-state / safety sensors.
# Numeric sensors (power/energy/voltage/current/temperature/humidity/etc.) are
# EXCLUDED because they fire constantly and would burn Gemini quota.
INTERESTING_SENSOR_CLASSES = {
    "moisture",         # water leak
    "smoke",
    "gas",
    "carbon_monoxide",
    "problem",
    "safety",
    "tamper",
    "running",          # appliance cycle complete (discrete)
    # NOTE: battery/power/energy/voltage/current/temperature/humidity explicitly excluded
}

# Motion/occupancy/presence classes — high-frequency by nature. Long debounce.
HIGH_FREQ_BINARY_CLASSES = {"motion", "occupancy", "presence", "moving"}

# Domains we actively watch for observation-worthy transitions
WATCHED_DOMAINS = {
    "binary_sensor",        # doors, moisture, motion (with debounce)
    "lock",                 # locked/unlocked
    "cover",                # garage doors, windows
    "alarm_control_panel",  # armed/disarmed
    "vacuum",               # cleaning cycle state
}

# Entity ID substring blocklist — anything matching is dropped before anything
# else. Covers measurement noise that often has no device_class set.
ENTITY_ID_NOISE_SUBSTRINGS = (
    "_w",               # watts (suffix check, not contains — see below)
    "_kwh", "_wh",
    "_voltage", "_volt", "_volts",
    "_current", "_amp", "_amps", "_amperage",
    "_power", "_energy",
    "_battery", "_batt",
    "_rssi", "_signal", "_linkquality", "_lqi", "_link_quality",
    "_wifi", "_wlan", "_bluetooth", "_ble",
    "_uptime", "_cpu", "_memory", "_ram", "_disk",
    "_temperature", "_temp_", "_humidity", "_humid",
    "_pressure", "_dewpoint", "_illuminance", "_lux",
    "_frequency", "_freq", "_ping",
    "_cache", "_counter", "_count",
    "_runtime", "_duration",
)

# Only these media_player states would have been interesting (kept for legacy)
INTERESTING_MEDIA_STATES = {"off", "idle", "playing"}

# Debounce windows (seconds)
DEBOUNCE_DEFAULT_S   = 60.0    # was 30 — doubled
DEBOUNCE_MOTION_S    = 300.0   # was 180 — raised to 5 min for motion/occupancy

# Minimum time an entity's previous state must have been held. Anything
# flapping faster than this is noise.
MIN_PREVIOUS_STATE_HOLD_S = 10.0

# Global rate limit — hard cap on classifier calls across the whole system
GLOBAL_CLASSIFIER_RATE_LIMIT_PER_HOUR = 30


def _entity_id_looks_noisy(entity_id: str) -> bool:
    """Cheap substring check against known-noisy entity patterns."""
    eid_lower = entity_id.lower()
    # Special case: _w suffix only (not e.g. "basement_window")
    if eid_lower.endswith("_w"):
        return True
    for needle in ENTITY_ID_NOISE_SUBSTRINGS:
        if needle.startswith("_") and needle.endswith("_"):
            if needle in eid_lower:
                return True
        elif needle.startswith("_"):
            if needle in eid_lower:
                # But skip suffix check we did above to avoid double-match
                if not (needle == "_w" and eid_lower.endswith("_w")):
                    return True
    return False


# ─── Module state ────────────────────────────────────────────────────────────

class _ObserverState:
    def __init__(self):
        self.running = False
        self.unsub = None
        self.last_seen: dict[str, float] = {}
        self.recent_events: deque = deque(maxlen=50)
        self.classifier_provider = None
        self.reasoning_provider = None
        self.hass = None
        self.config: dict = {}
        # Global rate limit tracking for classifier calls
        self.classifier_timestamps: deque = deque(maxlen=5000)
        self.rate_limit_warn_logged: bool = False

    def reset(self):
        self.running = False
        self.unsub = None
        self.last_seen.clear()
        self.recent_events.clear()
        self.classifier_timestamps.clear()
        self.rate_limit_warn_logged = False


_STATE = _ObserverState()


# ─── Pre-filter ──────────────────────────────────────────────────────────────

def _should_pre_filter(event: Event) -> bool:
    """Return True if this event should be dropped before any LLM call."""
    entity_id = event.data.get("entity_id", "")
    if not entity_id:
        return True

    # Cheap entity-id substring blocklist — catches measurement noise
    if _entity_id_looks_noisy(entity_id):
        return True

    domain = entity_id.split(".", 1)[0]

    if domain in IGNORED_DOMAINS:
        # Re-admit sensor if device_class is interesting
        if domain == "sensor":
            new_state = event.data.get("new_state")
            if new_state is None:
                return True
            dclass = new_state.attributes.get("device_class")
            if dclass in INTERESTING_SENSOR_CLASSES:
                return False
        return True

    if domain not in WATCHED_DOMAINS:
        return True

    # Note: media_player/light/switch now live in IGNORED_DOMAINS — no special case needed

    old_state = event.data.get("old_state")
    new_state = event.data.get("new_state")
    if old_state is None or new_state is None:
        return True
    if old_state.state == new_state.state:
        return True
    if new_state.state in ("unknown", "unavailable", "none"):
        return True
    if old_state.state in ("unknown", "unavailable", "none"):
        return True  # startup transitions, not real events

    # Drop flapping sensors — if previous state held < MIN_PREVIOUS_STATE_HOLD_S,
    # this is noise (e.g. a presence sensor oscillating)
    try:
        prev_held_s = (new_state.last_changed - old_state.last_changed).total_seconds()
        if prev_held_s < MIN_PREVIOUS_STATE_HOLD_S:
            return True
    except Exception:
        pass

    return False


def _effective_rate_limit() -> int:
    """
    The hourly classifier-call cap. User-configurable via `classifier_rate_limit`:
    runtime_config (panel, live) → addon/entry config → default. A value of 0
    (or negative) means UNLIMITED — appropriate for local LLMs (Ollama) or paid
    tiers with high quotas, where throttling at 30/hr makes no sense.
    """
    from .const import DOMAIN
    hass = _STATE.hass
    if hass:
        for _eid, data in hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                rc = data.get("runtime_config", {})
                if "classifier_rate_limit" in rc:
                    try:
                        return int(rc["classifier_rate_limit"])
                    except (TypeError, ValueError):
                        break
    try:
        return int((_STATE.config or {}).get(
            "classifier_rate_limit", GLOBAL_CLASSIFIER_RATE_LIMIT_PER_HOUR))
    except (TypeError, ValueError):
        return GLOBAL_CLASSIFIER_RATE_LIMIT_PER_HOUR


def _classifier_rate_limited() -> bool:
    """
    Return True if we've hit the configured hourly classifier-call cap in the
    last hour. Uses _STATE.classifier_timestamps (bounded deque). A cap of 0
    (or negative) means unlimited and never throttles.
    """
    limit = _effective_rate_limit()
    now = time.time()
    # Purge entries older than 1 hour (keeps the displayed count accurate)
    while _STATE.classifier_timestamps and _STATE.classifier_timestamps[0] < now - 3600:
        _STATE.classifier_timestamps.popleft()
    if limit <= 0:
        return False  # unlimited
    return len(_STATE.classifier_timestamps) >= limit


def _record_classifier_call() -> None:
    """Record a classifier call for the rate limiter."""
    _STATE.classifier_timestamps.append(time.time())


def _debounced(entity_id: str, min_interval_s: float = 30.0) -> bool:
    now = time.time()
    last = _STATE.last_seen.get(entity_id, 0.0)
    if now - last < min_interval_s:
        return True
    _STATE.last_seen[entity_id] = now
    return False


def _record_for_context(event: Event) -> None:
    entity_id = event.data.get("entity_id", "")
    new_state = event.data.get("new_state")
    old_state = event.data.get("old_state")
    if new_state is None:
        return
    _STATE.recent_events.append({
        "ts": time.time(),
        "entity_id": entity_id,
        "old": old_state.state if old_state else None,
        "new": new_state.state,
        "fname": new_state.attributes.get("friendly_name", entity_id),
        "area":  audio_routing.entity_area(_STATE.hass, entity_id),
    })


def get_recent_context(seconds: float = 600) -> str:
    """Human-readable summary of recent events (state changes + camera reviews)."""
    cutoff = time.time() - seconds
    lines = []
    for ev in list(_STATE.recent_events):
        if ev["ts"] < cutoff:
            continue
        age_s = int(time.time() - ev["ts"])
        age = f"{age_s}s ago" if age_s < 60 else f"{age_s // 60}m ago"
        if ev.get("kind") == "camera":
            flag = "⚠ " if ev.get("notable") else ""
            lines.append(f"  [{age}] {flag}Camera {ev.get('camera','')}: {ev.get('summary','')}")
        else:
            area_tag = f" [{ev['area']}]" if ev.get('area') else ""
            lines.append(f"  [{age}]{area_tag} {ev['fname']}: {ev['old']} → {ev['new']}")
    if not lines:
        return "quiet — no notable recent activity"
    return "\n".join(lines[-15:])


def record_camera_event(
    camera_name: str, summary: str, category: str = "other", notable: bool = False,
) -> None:
    """
    Record a camera review into the observer's recent-events buffer so it's
    incorporated into the rest of JARVIS's awareness — briefings, "what happened
    outside" queries, and the unified reasoning context. Called by camera.py
    after the camera-reasoning step.
    """
    try:
        if _STATE is None or _STATE.recent_events is None:
            return
        _STATE.recent_events.append({
            "ts": time.time(),
            "kind": "camera",
            "camera": camera_name,
            "summary": summary,
            "category": category,
            "notable": bool(notable),
            "area": None,
        })
    except Exception:
        pass


# ─── Main pipeline ──────────────────────────────────────────────────────────

def _cognition_enabled() -> bool:
    """Whether the local cognition layer is active (runtime_config → config → default ON)."""
    from .const import DOMAIN
    hass = _STATE.hass
    if hass:
        for _eid, data in hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                rc = data.get("runtime_config", {})
                if "cognition_enabled" in rc:
                    return bool(rc["cognition_enabled"])
    return bool((_STATE.config or {}).get("cognition_enabled", True))


def _cognition_threshold() -> float:
    """Salience threshold for cognition anomaly escalation (default 0.6)."""
    from .const import DOMAIN
    hass = _STATE.hass
    if hass:
        for _eid, data in hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                rc = data.get("runtime_config", {})
                if "cognition_threshold" in rc:
                    try:
                        return float(rc["cognition_threshold"])
                    except (TypeError, ValueError):
                        break
    try:
        return float((_STATE.config or {}).get("cognition_threshold", 0.6))
    except (TypeError, ValueError):
        return 0.6


@callback
def _on_state_changed(event: Event) -> None:
    """Non-blocking HA event handler."""
    if not _STATE.running:
        return
    # NOTE: announcements_enabled is NOT checked here. Events still get
    # classified and logged when announcements are off — they just don't
    # get spoken. This keeps the activity feed populated for observability.
    # The speak gate is in _process_event, after classification.

    # Local cognition observes EVERY event (learning + triage) at zero cloud
    # cost. Its decision is ADDITIVE: it can only elevate an anomaly the static
    # filter would have dropped — it never suppresses what the filter escalates.
    cog_escalate = False
    cog_reason = ""
    if _cognition_enabled():
        try:
            from . import cognition
            _decision = cognition.process(event, _cognition_threshold())
            cog_escalate = _decision.escalate
            cog_reason = _decision.reason
        except Exception:
            cog_escalate = False

    if _should_pre_filter(event):
        # Static filter would drop this; escalate only on a cognition anomaly.
        if not cog_escalate:
            return
        try:
            from .websocket import jarvis_log
            jarvis_log(
                "CLASSIFY",
                f"cognition escalated {event.data.get('entity_id','')}: {cog_reason}",
            )
        except Exception:
            pass

    entity_id = event.data.get("entity_id", "")

    # v5.8.03: Cognitive core ignore check
    try:
        from . import cognitive_core
        if cognitive_core.is_ignored(entity_id):
            return
    except Exception:
        pass

    # Longer debounce for motion/occupancy — they fire very often
    new_state = event.data.get("new_state")
    dclass = new_state.attributes.get("device_class") if new_state else None
    interval = DEBOUNCE_MOTION_S if dclass in HIGH_FREQ_BINARY_CLASSES else DEBOUNCE_DEFAULT_S
    if _debounced(entity_id, interval):
        return

    # Hourly rate limit — user-configurable cap to bound API cost
    if _classifier_rate_limited():
        if not _STATE.rate_limit_warn_logged:
            _LOGGER.warning(
                "JARVIS Observer: hit rate limit of %d classifier calls/hour. "
                "Dropping further events until the window clears. "
                "(Raise or disable via classifier_rate_limit; 0 = unlimited.)",
                _effective_rate_limit(),
            )
            _STATE.rate_limit_warn_logged = True
        return
    # Clear the warning flag when we're back under the limit
    _STATE.rate_limit_warn_logged = False
    _record_classifier_call()

    _record_for_context(event)
    _STATE.hass.async_create_task(_process_event(event))


async def _process_event(event: Event) -> None:
    """Full async pipeline for one flagged state change."""
    try:
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or old_state is None:
            return

        friendly_name = new_state.attributes.get("friendly_name", entity_id)
        device_class = new_state.attributes.get("device_class")
        now_hhmm = dt_util.now().strftime("%H:%M")
        recent_summary = get_recent_context(300)
        entity_area_id = audio_routing.entity_area(_STATE.hass, entity_id)

        # Tier 1: classify
        classification = await classifier.classify(
            _STATE.hass,
            _STATE.classifier_provider,
            entity_id=entity_id,
            old_state=old_state.state,
            new_state=new_state.state,
            friendly_name=friendly_name,
            device_class=device_class,
            now_hhmm=now_hhmm,
            recent_activity_summary=recent_summary,
        )

        if not classification.get("worth_considering"):
            # Still log to activity feed so the panel shows observer is working
            try:
                from .database import save_activity
                save_activity(
                    entity_id=entity_id,
                    category="classified",
                    urgency="low",
                    message=f"{friendly_name}: {old_state.state} → {new_state.state} — not worth considering",
                    was_spoken=False,
                    source="observer",
                )
            except Exception:
                pass
            return

        urgency_hint = classification["urgency"]
        category = classification["category"]

        # Log the flagged event to activity feed
        try:
            from .database import save_activity
            save_activity(
                entity_id=entity_id,
                category=category,
                urgency=urgency_hint,
                message=f"{friendly_name}: {old_state.state} → {new_state.state} — flagged for reasoning",
                was_spoken=False,
                source="observer",
            )
        except Exception:
            pass

        _LOGGER.info(
            "Observer flagged %s [%s]: %s → %s (urgency=%s, cat=%s)",
            entity_id, entity_area_id or "no-area",
            old_state.state, new_state.state, urgency_hint, category,
        )

        # Sleep + presence context for reasoning
        bedroom_areas = _STATE.config.get("bedroom_areas", []) or []
        _quiet_start = _STATE.config.get("observer_quiet_start", DEFAULT_OBSERVER_QUIET_START)
        _quiet_end = _STATE.config.get("observer_quiet_end", DEFAULT_OBSERVER_QUIET_END)
        sleeping, sleep_reason = sleep_detection.is_sleeping(
            _STATE.hass,
            bedroom_area_ids=bedroom_areas,
            quiet_start=_quiet_start,
            quiet_end=_quiet_end,
        )
        # Quiet hours are time-based and do NOT require bedroom presence. During
        # quiet hours only CRITICAL events may speak — everything else is held to
        # a phone notification at most. This is independent of sleep detection so
        # announcements can't slip through just because no one is in a bedroom.
        in_quiet_hours = sleep_detection._in_quiet_hours(_quiet_start, _quiet_end)
        occupied_areas = audio_routing.currently_occupied_areas(_STATE.hass)
        presence_context = (
            f"user appears to be sleeping ({sleep_reason})"
            if sleeping else
            f"awake; occupied areas: {occupied_areas or 'none detected'}"
        )

        # Whether a registered user is home — open windows / unlocked doors are
        # normal household state when someone's in, only notable when away.
        anyone_home = any(
            s.state == "home" for s in _STATE.hass.states.async_all("person")
        ) or any(
            s.state == "home" for s in _STATE.hass.states.async_all("device_tracker")
        )

        # Tier 2: reason
        decision = await reasoning_loop.decide(
            _STATE.hass,
            _STATE.reasoning_provider,
            honorific=_STATE.config.get(CONF_HONORIFIC, "sir"),
            event_summary=(
                f"{friendly_name} ({entity_id})"
                f"{' in ' + entity_area_id if entity_area_id else ''} "
                f"changed from {old_state.state} to {new_state.state}"
            ),
            home_state_summary=recent_summary,
            classifier_urgency=urgency_hint,
            classifier_category=category,
            recent_announcements=output_gate.recent_announcements(5),
            presence_context=presence_context,
            anyone_home=anyone_home,
            entity_id=entity_id,
            device_class=(new_state.attributes.get("device_class") or ""),
            from_state=old_state.state,
            to_state=new_state.state,
            friendly_name=friendly_name,
        )

        if not decision.get("speak"):
            _LOGGER.debug(
                "Observer silent for %s: %s",
                entity_id, decision.get("reason"),
            )
            return

        message = decision["message"]
        final_urgency = decision["urgency"]

        # SAFETY CAP: reasoning tier LLMs over-classify as "critical".
        # Only allow critical if the classifier (which checks device_class
        # against URGENCY_CEILINGS — smoke/CO/gas/moisture/glass_break) also
        # said critical. Otherwise downgrade to high. This prevents the
        # "critical bypasses sleep" path from firing on door/motion events.
        if final_urgency == "critical" and urgency_hint != "critical":
            _LOGGER.info(
                "Observer: downgrading urgency critical→high for %s "
                "(reasoning said critical but classifier said %s)",
                entity_id, urgency_hint,
            )
            final_urgency = "high"

        # EXTRA SAFETY: during sleep, suppress anything below critical that
        # would broadcast. HIGH during sleep already routes to notify_only,
        # so this is redundant but defensive.
        if sleeping and final_urgency not in ("critical",):
            _LOGGER.info(
                "Observer: user sleeping, suppressing %s urgency message '%s'",
                final_urgency, message[:80],
            )
            output_gate.record_announcement(
                entity_id=entity_id, category=category,
                urgency=final_urgency, message=message, was_spoken=False,
            )
            return

        # Output gate
        allowed, gate_reason = output_gate.can_announce(
            entity_id=entity_id, category=category,
            urgency=final_urgency, message=message,
        )
        if not allowed:
            _LOGGER.info("Observer suppressed '%s' — %s", message, gate_reason)
            output_gate.record_announcement(
                entity_id=entity_id, category=category,
                urgency=final_urgency, message=message, was_spoken=False,
            )
            return

        # Route audio based on urgency + presence + sleep
        broadcast_group = _STATE.config.get("broadcast_group") or None
        ann_speakers = _get_announcement_speakers()
        _LOGGER.warning(
            "Observer routing: urgency=%s, broadcast_group=%s, "
            "ann_speakers=%s, sleeping=%s",
            final_urgency, broadcast_group, ann_speakers, sleeping,
        )
        # During quiet hours, force non-critical to stay quiet by routing as if
        # asleep (critical still broadcasts; high→notify; medium/low→suppressed).
        if in_quiet_hours and final_urgency != "critical":
            try:
                from .websocket import jarvis_log
                jarvis_log("GATE", f"quiet hours — holding {final_urgency} announcement (not critical)")
            except Exception:
                pass
        targets, mode = audio_routing.observer_speak_target(
            _STATE.hass,
            urgency=final_urgency,
            broadcast_group=broadcast_group,
            announcement_speakers=ann_speakers,
            is_sleeping=(sleeping or in_quiet_hours),
        )
        _LOGGER.warning(
            "Observer routing result: targets=%s, mode=%s", targets, mode,
        )

        if mode == "suppressed":
            _LOGGER.info("Observer route-suppressed '%s'", message)
            output_gate.record_announcement(
                entity_id=entity_id, category=category,
                urgency=final_urgency, message=message, was_spoken=False,
            )
            return

        if mode == "notify_only":
            _LOGGER.info("Observer notify-only '%s'", message)
            await _send_notification(message, urgency=final_urgency)
            output_gate.record_announcement(
                entity_id=entity_id, category=category,
                urgency=final_urgency, message=message, was_spoken=False,
            )
            return

        if not targets:
            _LOGGER.debug("Observer had mode=%s but no targets — skipping", mode)
            return

        # v5.5.2: Check announcements_enabled BEFORE speaking but AFTER
        # classification and logging. This way the activity feed populates
        # even when announcements are off.
        _ann_enabled = _is_announcements_enabled()
        if not _ann_enabled:
            _LOGGER.debug(
                "Observer: announcements disabled, logging but not speaking: %s",
                message[:80],
            )
            output_gate.record_announcement(
                entity_id=entity_id, category=category,
                urgency=final_urgency, message=message, was_spoken=False,
            )
            # v5.6.2: Still push phone notification for high/critical
            # even when announcements (voice) are disabled
            if final_urgency in ("high", "critical"):
                await _send_notification(message, urgency=final_urgency)
            return

        # Actually speak
        await _speak(message, targets=targets)

        output_gate.record_announcement(
            entity_id=entity_id, category=category,
            urgency=final_urgency, message=message, was_spoken=True,
        )

        # Additionally push phone for high/critical
        if final_urgency in ("high", "critical"):
            await _send_notification(message, urgency=final_urgency)

    except Exception as exc:
        _LOGGER.exception("Observer pipeline error: %s", exc)


# ─── Announcement gate helper ─────────────────────────────────────────────

def _is_announcements_enabled() -> bool:
    """
    Check whether announcements are globally enabled.
    Reads from runtime_config (panel toggles) first, then config (addon/entry).
    """
    from .const import DOMAIN
    hass = _STATE.hass
    if not hass:
        return False
    # Check runtime_config first (set by panel Settings toggles)
    for eid, data in hass.data.get(DOMAIN, {}).items():
        if isinstance(data, dict):
            rc = data.get("runtime_config", {})
            if "announcements_enabled" in rc:
                return bool(rc["announcements_enabled"])
    # Fall back to _STATE.config (from addon config / entry options)
    return bool((_STATE.config or {}).get("announcements_enabled", True))


def _get_announcement_speakers() -> list[str] | None:
    """
    Read announcement_speakers from runtime_config (panel Settings toggles).
    Returns the list if set and non-empty, else None (let audio_routing decide).
    """
    import json as _json
    from .const import DOMAIN
    hass = _STATE.hass
    if not hass:
        return None
    try:
        for eid, data in hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                rc = data.get("runtime_config", {})
                raw = rc.get("announcement_speakers")
                if raw:
                    speakers = _json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(speakers, list) and speakers:
                        return speakers
    except Exception as exc:
        _LOGGER.debug("Error reading announcement_speakers: %s", exc)
    return None


# ─── Speaking + notification ────────────────────────────────────────────────

async def _speak(message: str, *, targets: list[str]) -> None:
    """Call tts.speak for each target using configured TTS engine."""
    hass = _STATE.hass
    _LOGGER.info("Observer speaking → %s: %s", targets, message)

    # Resolve TTS entity from config (same as the rest of JARVIS)
    tts_entity = "tts.piper"  # fallback default
    try:
        from .tts_helper import resolve_tts_for_context
        cfg = _STATE.config or {}
        regular = cfg.get("tts_engine", "auto")
        premium = cfg.get("tts_premium_engine") or None
        premium_contexts = cfg.get("tts_premium_contexts") or []
        resolved = resolve_tts_for_context(
            hass, "sentinel", regular, premium, premium_contexts,
        )
        if resolved:
            tts_entity = resolved
    except Exception as exc:
        _LOGGER.debug("Observer TTS resolve fallback to tts.piper: %s", exc)

    for target in targets:
        try:
            await hass.services.async_call(
                "tts", "speak",
                {
                    "entity_id": tts_entity,
                    "media_player_entity_id": target,
                    "message": message,
                    "cache": False,
                },
                blocking=False,
            )
        except Exception as exc:
            _LOGGER.warning("tts.speak to %s failed: %s", target, exc)


async def _send_notification(message: str, *, urgency: str) -> None:
    """Send push notification via configured notify service."""
    # v5.6.2: Check runtime_config first (panel Settings dropdown), then config
    notify_service = None
    try:
        from .const import DOMAIN
        for eid, data in _STATE.hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                rc = data.get("runtime_config", {})
                if rc.get("notify_service"):
                    notify_service = rc["notify_service"]
                    break
    except Exception:
        pass
    if not notify_service:
        notify_service = (_STATE.config or {}).get(CONF_NOTIFY_SERVICE)
    if not notify_service:
        return
    try:
        domain, service = notify_service.split(".", 1)
        title = "JARVIS" if urgency != "critical" else "⚠ JARVIS URGENT"
        await _STATE.hass.services.async_call(
            domain, service,
            {"title": title, "message": message},
            blocking=False,
        )
    except Exception as exc:
        _LOGGER.warning("notification failed: %s", exc)


# ─── Lifecycle ──────────────────────────────────────────────────────────────

async def start(hass: HomeAssistant, config: dict) -> None:
    """Begin observing. Safe to call multiple times."""
    if _STATE.running:
        await stop()

    _STATE.hass = hass
    _STATE.config = config

    # The boot-time observer config is built from entry.data/options only, which
    # predates anything saved from the panel (the appliance-profile lesson). Merge
    # the live runtime AI-model keys in so tier providers honor panel selections —
    # critically llm_base_url for the Ollama/GPU-server migration.
    try:
        from .const import DOMAIN as _DOM
        for _data in (hass.data.get(_DOM) or {}).values():
            if isinstance(_data, dict) and isinstance(_data.get("runtime_config"), dict):
                _rc = _data["runtime_config"]
                config = {**config, **{
                    k: v for k, v in _rc.items()
                    if k == "llm_base_url" or k.endswith(("_provider", "_model", "_base_url"))
                }}
                break
    except Exception as _exc:
        _LOGGER.debug("Observer: runtime AI-key merge note: %s", _exc)
    _STATE.config = config

    try:
        # Both providers instantiate HTTPS clients which load SSL certs from
        # disk — a blocking operation. Must run in executor, not event loop.
        _STATE.classifier_provider = await hass.async_add_executor_job(
            create_tier_provider, config, "classifier"
        )
        _STATE.reasoning_provider = await hass.async_add_executor_job(
            create_tier_provider, config, "reasoning"
        )
    except Exception as exc:
        _LOGGER.error("Observer: failed to create tier providers: %s", exc)
        return

    _STATE.running = True
    _STATE.unsub = hass.bus.async_listen("state_changed", _on_state_changed)
    _LOGGER.info(
        "JARVIS Observer v5.7.00 started (classifier=%s, reasoning=%s, bedrooms=%s)",
        config.get("classifier_model", "default"),
        config.get("reasoning_model", "default"),
        config.get("bedroom_areas", []),
    )

    # v5.7.00: Start appliance cycle monitor alongside observer
    try:
        from . import appliance_monitor
        await appliance_monitor.start(hass, config)
    except Exception as exc:
        _LOGGER.warning("Appliance monitor start failed (non-fatal): %s", exc)

    # v5.8.01: Start proactive briefing system
    try:
        from . import proactive_briefing
        await proactive_briefing.start(hass, config)
    except Exception as exc:
        _LOGGER.warning("Proactive briefing start failed (non-fatal): %s", exc)

    # v5.8.03: Start cognitive core
    try:
        from . import cognitive_core
        await cognitive_core.start(hass, config)
    except Exception as exc:
        _LOGGER.warning("Cognitive core start failed (non-fatal): %s", exc)


async def stop() -> None:
    if _STATE.unsub is not None:
        try:
            _STATE.unsub()
        except Exception:
            pass
    # v5.7.00: Stop appliance monitor
    try:
        from . import appliance_monitor
        await appliance_monitor.stop()
    except Exception:
        pass
    # v5.8.01: Stop proactive briefing
    try:
        from . import proactive_briefing
        await proactive_briefing.stop()
    except Exception:
        pass
    # v5.8.03: Stop cognitive core
    try:
        from . import cognitive_core
        await cognitive_core.stop()
    except Exception:
        pass
    _STATE.reset()
    _LOGGER.info("JARVIS Observer stopped")


def is_running() -> bool:
    return _STATE.running
