"""
JARVIS — Pattern Analyzer (v5.9.00).

Reads state_changes and commands from patterns.db, identifies repeating
behavioral patterns, and proposes automations. Runs periodically (every
6 hours) once enough data is accumulated (7+ days).

Pattern types detected:
  1. Time-based routines: "Lights turned off every night around 10:30 PM"
  2. Sequence patterns: "Front door locks 5 min after garage closes"
  3. Repeated commands: "Turn off kitchen lights" said 3x/day at similar times
  4. Temperature preferences: thermostat adjusted to same temp at same times
  5. Presence-triggered: lights on when arriving, off when leaving

Each detected pattern gets a confidence score (0-1). Patterns above 0.7
become suggestions stored in the suggestions table. The user approves
or dismisses via conversation or the panel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .paths import config_path_str

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DB_PATH = config_path_str("jarvis", "patterns.db")
MIN_DAYS = 7           # Don't analyze until we have this much data
MIN_OCCURRENCES = 5    # Pattern must repeat this many times
CONFIDENCE_THRESHOLD = 0.65  # Minimum to create a suggestion
ANALYSIS_INTERVAL = 21600    # 6 hours between analyses
KNOWLEDGE_FACT_CONFIDENCE = 0.75  # routines/commands above this also become observed facts
PERSON_DOMINANCE_RATIO = 0.8      # a person must account for this share of a
                                   # pattern's occurrences to own it, vs. household


def set_thresholds(min_occurrences: int | None = None,
                   confidence: float | None = None) -> None:
    """Loosen/tighten the pattern engine at runtime (panel-configurable). The
    cognitive tick calls this with the user's settings before each analysis."""
    global MIN_OCCURRENCES, CONFIDENCE_THRESHOLD
    if min_occurrences is not None:
        try:
            MIN_OCCURRENCES = max(2, int(min_occurrences))
        except Exception:
            pass
    if confidence is not None:
        try:
            CONFIDENCE_THRESHOLD = min(0.95, max(0.3, float(confidence)))
        except Exception:
            pass


# Adaptive suggestion threshold (opt-in): when enabled, how welcome recent
# suggestions were nudges the confidence bar for creating new ones — mostly
# dismissed as unneeded → stricter, almost all acted on → slightly looser. The
# delta is bounded and the result is clamped, so it can never run away, and it
# is derived ONLY from "suggestion" outcomes — it never touches intrusion,
# lockdown, or any security/safety decision.
_ADAPT_CACHE = {"ts": 0.0, "delta": 0.0}
_ADAPT_MIN_JUDGED = 5           # need this many judged suggestions before moving
_ADAPT_WINDOW_S = 30 * 86400.0  # look back a month


def _learned_threshold_delta() -> float:
    """Bounded adjustment (in [-0.07, +0.15]) to the suggestion confidence bar,
    learned from how recent suggestions were received. Returns 0.0 when the
    opt-in is off, on any error, or with too little evidence. Cached 5 min."""
    try:
        from . import jarvis_config
        if not jarvis_config.get("adaptive_suggestion_threshold", False):
            return 0.0
    except Exception:
        return 0.0
    now = time.time()
    if now - _ADAPT_CACHE["ts"] < 300.0:
        return _ADAPT_CACHE["delta"]
    delta = 0.0
    try:
        from . import decision_record
        r = decision_record.outcome_rate("suggestion", window_s=_ADAPT_WINDOW_S)
        if int(r.get("judged", 0)) >= _ADAPT_MIN_JUDGED:
            uw = r.get("unwelcome_rate") or 0.0
            if uw >= 0.5:
                delta = 0.15        # mostly unwelcome → much more selective
            elif uw >= 0.3:
                delta = 0.07        # somewhat unwelcome → more selective
            elif uw <= 0.1:
                delta = -0.07       # almost all welcome → a little more generous
    except Exception:
        delta = 0.0
    _ADAPT_CACHE.update(ts=now, delta=delta)
    return delta


def _effective_threshold() -> float:
    """CONFIDENCE_THRESHOLD adjusted by the learned delta, clamped [0.3, 0.95]."""
    return min(0.95, max(0.3, CONFIDENCE_THRESHOLD + _learned_threshold_delta()))


@dataclass
class DetectedPattern:
    pattern_type: str      # time_routine, sequence, repeated_command, temp_pref, presence
    description: str
    entity_ids: list[str]
    confidence: float
    occurrences: int
    coverage: float = 0.0  # positive days / opportunity days (0 = not computed)
    details: dict = field(default_factory=dict)


# Domains that expose only observable state and no actuating service — an
# "action" that targets one of these can never turn into a real automation step.
_READ_ONLY_ACTION_DOMAINS = frozenset({
    "binary_sensor", "sensor", "device_tracker", "person", "sun", "weather",
    "zone", "geo_location", "air_quality", "update", "schedule", "stt",
})


def _is_actuating_action(action) -> bool:
    """Whether a single automation action actually actuates a device (a real
    service call on a controllable domain), as opposed to a delay/template or a
    service pointed at a read-only entity (e.g. ``binary_sensor.turn_on``).
    Pure, best-effort."""
    if not isinstance(action, dict):
        return False  # delays, templated steps, etc. don't actuate on their own
    svc = action.get("action") or action.get("service")
    if not isinstance(svc, str) or "." not in svc:
        return False
    return svc.split(".", 1)[0] not in _READ_ONLY_ACTION_DOMAINS


def normalize_suggestion_automation(stored_yaml: str) -> dict:
    """
    Pure: turn a suggestion's stored automation JSON into structured args for
    automation_creator.create_automation, or explain why it can't (v6.52.0).

    Closes the pattern-engine loop: the analyzer generates these blobs, the
    user approves, and this converts the blob into an installable automation.
    Handles the legacy trigger/action shape the generator emits — HA modernized
    'platform'→'trigger' and 'service'→'action', so we translate both — and
    refuses the non-actionable 'manual_review' markers honestly instead of
    fabricating an automation from a vague note.

    Returns either:
        {"installable": True, "alias", "trigger": [...], "action": [...]}
        {"installable": False, "reason": "..."}
    """
    if not stored_yaml:
        return {"installable": False, "reason": "no automation payload"}
    try:
        data = json.loads(stored_yaml)
    except Exception:
        return {"installable": False, "reason": "payload is not valid JSON"}

    if not isinstance(data, dict):
        return {"installable": False, "reason": "payload is not an object"}
    if data.get("type") == "manual_review" or "note" in data and "trigger" not in data:
        return {"installable": False,
                "reason": "advisory only — needs a human to design the automation"}

    alias = data.get("alias")
    trigger = data.get("trigger")
    action = data.get("action")
    if not alias or not trigger or not action:
        return {"installable": False, "reason": "missing alias, trigger, or action"}

    def _modernize_trigger(t: dict) -> dict:
        t = dict(t)
        if "platform" in t and "trigger" not in t:
            t["trigger"] = t.pop("platform")
        return t

    def _modernize_action(a: dict) -> dict:
        a = dict(a)
        if "service" in a and "action" not in a:
            a["action"] = a.pop("service")
        return a

    triggers = [trigger] if isinstance(trigger, dict) else list(trigger)
    actions = [action] if isinstance(action, dict) else list(action)
    triggers = [_modernize_trigger(t) if isinstance(t, dict) else t for t in triggers]
    # action items can be delays or service calls; only modernize the dicts
    norm_actions = []
    for a in actions:
        if isinstance(a, dict):
            norm_actions.append(_modernize_action(a))
        else:
            norm_actions.append(a)

    # An automation is only real if at least one action actually actuates a
    # device. Read-only domains (binary_sensor, sensor, device_tracker, …) never
    # have a turn_on/off service, so a suggestion whose every "action" targets
    # one of them is an observed correlation, not an automatable outcome — refuse
    # it here so the store's gate and the periodic purge both drop it.
    if not any(_is_actuating_action(a) for a in norm_actions):
        return {"installable": False,
                "reason": "no actuating action — target is a read-only entity"}

    out = {"installable": True, "alias": alias,
           "trigger": triggers, "action": norm_actions}
    # Preserve a learned "And if" condition (e.g. a time window) so it reaches
    # the installed automation.
    cond = data.get("condition")
    if cond:
        out["condition"] = [cond] if isinstance(cond, dict) else list(cond)
    # Preserve the execution mode — a "hold until unoccupied" / confirmation
    # choreography needs `restart` so a re-trigger re-arms the wait.
    mode = data.get("mode")
    if mode in ("single", "restart", "queued", "parallel"):
        out["mode"] = mode
    return out


def service_for(entity_id: str, state: str) -> Optional[dict]:
    """
    Map an entity + desired state to the correct HA service call (v6.52.1).
    The pattern generator used to build every action as `{domain}.turn_{state}`,
    which is only valid for on/off domains — it would emit `lock.turn_on` for a
    learned door-lock routine (the module's own flagship example) and write a
    broken automation. Now each domain gets its real service; anything without a
    clean mapping returns None so the caller can mark it advisory instead of
    installing garbage.

    Returns {"service": "domain.service", "entity_id": ...} or None.
    """
    if not entity_id or "." not in entity_id:
        return None
    domain = entity_id.split(".")[0]
    s = str(state).lower().strip()

    onoff = {"light", "switch", "fan", "input_boolean", "humidifier", "siren"}
    if domain in onoff and s in ("on", "off"):
        return {"service": f"{domain}.turn_{s}", "entity_id": entity_id}

    if domain == "lock" and s in ("locked", "unlocked"):
        return {"service": f"lock.{'lock' if s == 'locked' else 'unlock'}",
                "entity_id": entity_id}

    if domain == "cover" and s in ("open", "closed", "opening", "closing"):
        # settle transient states to the intended end state
        want_open = s in ("open", "opening")
        return {"service": f"cover.{'open' if want_open else 'close'}_cover",
                "entity_id": entity_id}

    if domain in ("switch", "input_boolean") and s in ("on", "off"):
        return {"service": f"{domain}.turn_{s}", "entity_id": entity_id}

    # a scene target is always activated via scene.turn_on
    if domain == "scene":
        return {"service": "scene.turn_on", "entity_id": entity_id}

    # climate, media_player, and everything else need parameters we don't infer
    # from a bare state — better to advise than to guess.
    return None


def _pretty_entity(entity_id: str) -> str:
    """Human label from an entity_id: drop the domain, de-underscore, and collapse
    Home Assistant's frequent duplicate-slug tails (``eliana_s_room_eliana_s_room``
    -> ``eliana s room``). Pure, best-effort — falls back to the raw id."""
    if not entity_id:
        return entity_id
    name = entity_id.split(".", 1)[-1] if "." in entity_id else entity_id
    words = name.replace("_", " ").split()
    for size in range(len(words) // 2, 0, -1):
        if words[:size] == words[size:2 * size]:
            words = words[size:]
            break
    return " ".join(words).strip() or entity_id


def _action_verb(entity_id: str, state: str) -> str:
    """The action an automation would take to reach (entity, state) — 'turn on',
    'unlock', 'close', 'activate' — or '' when there is no clean device action
    (a passive sensor/camera state), so callers can describe the pattern as an
    observed correlation rather than imply an automatable outcome. Pure."""
    if not service_for(entity_id, state):
        return ""
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    s = str(state).lower().strip()
    if domain == "lock":
        return "lock" if s == "locked" else "unlock"
    if domain == "cover":
        return "open" if s in ("open", "opening") else "close"
    if domain == "scene":
        return "activate"
    return f"turn {s}"


def explain_suggestion(pattern_type: str, details: dict, count: int) -> dict:
    """Turn a suggestion's evidence into a human 'why' for the review UI
    (v6.80.0). Returns {headline, evidence:[...]} — the observations that led to
    the proposal, so approving is an informed choice rather than a leap. Pure,
    never raises."""
    d = details or {}
    ev: list[str] = []
    headline = ""
    try:
        if pattern_type == "time_routine":
            hour = d.get("hour")
            state = d.get("state")
            consistency = d.get("coverage", d.get("consistency"))
            observed = d.get("observed_days")
            opportunity = d.get("opportunity_days")
            person = d.get("person")
            when = f"{int(hour):02d}:00" if hour is not None else "a regular time"
            headline = f"A daily routine around {when}"
            if state is not None:
                ev.append(f"Observed turning {state} near {when}")
            # Prefer the honest coverage framing — how many days it happened out
            # of how many it could have, so the negative evidence is visible too.
            if observed is not None and opportunity:
                missed = max(0, int(opportunity) - int(observed))
                line = f"Happened on {int(observed)} of {int(opportunity)} days"
                if missed:
                    line += f" (missed {missed})"
                ev.append(line)
            else:
                ev.append(f"Happened {count} times in the last 30 days")
            if consistency is not None:
                ev.append(f"Consistent on about {int(float(consistency) * 100)}% of days")
            if person:
                ev.append(f"Specifically when {person} is home")
        elif pattern_type == "sequence":
            headline = "One action reliably follows another"
            first = d.get("first")
            then = d.get("then")
            trig = d.get("trigger") if isinstance(d.get("trigger"), dict) else None
            act = d.get("action") if isinstance(d.get("action"), dict) else None
            # Trigger side: accept a pre-formatted string (first) or the stored
            # {entity, state} dict — the dict path is what production actually
            # stores, and formatting it raw used to leak "{'entity': ...}" into
            # the review card.
            if isinstance(first, str) and first:
                trig_txt = first
            elif trig and trig.get("entity"):
                trig_txt = _trigger_phrase(trig["entity"], trig["state"])
            else:
                trig_txt = ""
            # Outcome side: lead with what an automation would DO, not just the
            # observed following state.
            if isinstance(then, str) and then:
                outcome = f"{then} usually follows"
            elif act and act.get("entity"):
                verb = _action_verb(act["entity"], act.get("state", ""))
                label = _pretty_entity(act["entity"])
                outcome = (f"JARVIS can {verb} {label}" if verb
                           else f"{label} usually turns {act.get('state', '')} "
                                f"(a correlation with no device action to automate)")
            else:
                outcome = ""
            if trig_txt and outcome:
                ev.append(f"{trig_txt}, {outcome}")
            elif outcome:
                ev.append(outcome[0].upper() + outcome[1:])
            ev.append(f"Seen {count} times in 30 days")
            if d.get("window_seconds"):
                ev.append(f"Usually within {int(d['window_seconds'])}s")
            until = d.get("until_unoccupied")
            if isinstance(until, dict) and until.get("entity_id"):
                ev.append(f"…and stays on until {_pretty_entity(until['entity_id'])} "
                          f"is clear (learned from when it's normally turned off)")
            for c in (d.get("condition") or []) if isinstance(d.get("condition"), list) else []:
                if isinstance(c, dict) and c.get("condition") == "state" and c.get("state") == "on":
                    ev.append(f"Only while {_pretty_entity(c.get('entity_id',''))} is occupied")
        elif pattern_type == "confirm_sequence":
            headline = "⚠ A confirmed sequence (safety-sensitive)"
            trig = d.get("trigger", {})
            confirm = d.get("confirm", {})
            opn = d.get("open", {})
            ev.append(f"When {_pretty_entity(trig.get('entity',''))} arrives home, "
                      f"{_pretty_entity(opn.get('entity',''))} opens")
            ev.append(f"It then closes once {_pretty_entity(confirm.get('entity',''))} "
                      f"confirms")
            ev.append(f"Observed {count} times in 30 days")
            ev.append("Closes a cover automatically — review carefully before enabling")
        elif pattern_type == "numeric_trigger":
            headline = "A threshold routine"
            if d.get("trigger_sensor") is not None:
                ev.append(f"When {_pretty_entity(str(d.get('trigger_sensor','')))} "
                          f"goes {d.get('op','')} {d.get('threshold','')}")
            act = d.get("action") if isinstance(d.get("action"), dict) else None
            if act and act.get("entity"):
                verb = _action_verb(act["entity"], act.get("state", ""))
                if verb:
                    ev.append(f"JARVIS can {verb} {_pretty_entity(act['entity'])}")
            for c in (d.get("condition") or []) if isinstance(d.get("condition"), list) else []:
                if isinstance(c, dict) and c.get("condition") == "state" and c.get("state") == "on":
                    ev.append(f"Only while {_pretty_entity(c.get('entity_id',''))} is occupied")
            ev.append(f"Seen {count} times in 30 days")
        elif pattern_type == "repeated_command":
            headline = "A command you give often"
            cmd = d.get("command") or d.get("text")
            if cmd:
                ev.append(f"You've asked '{cmd}' {count} times")
            if d.get("hour") is not None:
                ev.append(f"Most often around {int(d['hour']):02d}:00")
        elif pattern_type == "temp_pref":
            headline = "A temperature preference"
            if d.get("target") is not None:
                ev.append(f"Set to {d['target']}° repeatedly")
            ev.append(f"Observed {count} times")
        elif pattern_type == "presence":
            headline = "A presence-linked pattern"
            ev.append(f"Correlated {count} times over 30 days")
        else:
            headline = "A learned pattern"
            ev.append(f"Observed {count} times in 30 days")
    except Exception:
        headline = headline or "A learned pattern"
        if not ev:
            ev.append(f"Observed {count} times")
    return {"headline": headline, "evidence": ev}


def _time_window_condition(epochs: list) -> Optional[dict]:
    """If the action times cluster into a clear daily window — a contiguous span
    with a quiet period of at least 8 hours around it — return a Home Assistant
    time condition ``{"condition": "time", "after": "HH:MM:SS", "before": ...}``;
    otherwise None. Uses local clock hours from the stored timestamps. Pure.

    This is the tractable "And if": a motion→light pair that only ever happens in
    the evening gets a time window so the suggested automation won't fire the
    light at noon. Handles overnight windows (the HA time condition wraps).
    """
    if len(epochs) < MIN_OCCURRENCES:
        return None
    hours = sorted({datetime.fromtimestamp(e).hour for e in epochs})
    if len(hours) == 1:
        h = hours[0]
        return {"condition": "time",
                "after": f"{(h - 1) % 24:02d}:00:00",
                "before": f"{(h + 1) % 24:02d}:00:00"}
    # Largest circular gap between consecutive occurrence hours = the quiet period;
    # the active window is its complement.
    largest_gap = 0
    gap_start = gap_end = hours[0]
    for i in range(len(hours)):
        cur = hours[i]
        nxt = hours[(i + 1) % len(hours)]
        gap = (nxt - cur) % 24
        if gap > largest_gap:
            largest_gap, gap_start, gap_end = gap, cur, nxt
    if largest_gap < 8:
        return None                       # spread across the day: no clear window
    return {"condition": "time",
            "after": f"{gap_end:02d}:00:00",
            "before": f"{(gap_start + 1) % 24:02d}:00:00"}


def _is_dark_at(epoch: float, lat: float, lon: float) -> Optional[bool]:
    """True if the sun was below the horizon at ``epoch`` for the given location,
    False if above, None if it can't be determined (astral missing/failure).
    Thin wrapper around astral; the decision logic lives in _sun_condition so it
    stays testable without astral."""
    try:
        from astral import LocationInfo
        from astral.sun import sun as astral_sun
        from datetime import timezone
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        loc = LocationInfo(latitude=float(lat), longitude=float(lon))
        s = astral_sun(loc.observer, date=dt.date(), tzinfo=timezone.utc)
        return dt < s["sunrise"] or dt > s["sunset"]
    except Exception:
        return None


def _sun_condition(epochs: list, lat, lon) -> Optional[dict]:
    """If the action consistently happens after dark, return an HA sun condition
    ``{"condition": "sun", "after": "sunset", "before": "sunrise"}``; else None.
    More precise than a fixed time window for "when it's dark" patterns because
    it tracks the seasonal sunrise/sunset instead of a fixed clock time.
    """
    if lat is None or lon is None or len(epochs) < MIN_OCCURRENCES:
        return None
    dark = 0
    total = 0
    for e in epochs:
        d = _is_dark_at(e, lat, lon)
        if d is None:
            continue
        total += 1
        if d:
            dark += 1
    if total < MIN_OCCURRENCES:
        return None
    if dark / total >= 0.8:               # consistently after dark
        return {"condition": "sun", "after": "sunset", "before": "sunrise"}
    return None


def _condition_phrase(cond) -> str:
    """Human tail for a pattern description given its learned condition(s).
    Accepts a single condition dict, a list of them (ANDed), or None."""
    if isinstance(cond, list):
        return "".join(_condition_phrase(c) for c in cond)
    if not isinstance(cond, dict):
        return ""
    kind = cond.get("condition")
    if kind == "sun":
        return ", mostly after dark"
    if kind == "time":
        return f", mostly between {cond.get('after', '')[:5]} and {cond.get('before', '')[:5]}"
    if kind == "numeric_state":
        ent = cond.get("entity_id", "")
        if "below" in cond:
            return f", mostly while {ent} is below {cond['below']:g}"
        if "above" in cond:
            return f", mostly while {ent} is above {cond['above']:g}"
    if kind == "state":
        ent = cond.get("entity_id", "")
        st = cond.get("state", "")
        return f", only when {ent} is {st}"
    return ""


def _trigger_for(entity: str, state: str) -> dict:
    """HA trigger for a learned sequence's trigger entity. A person or
    device_tracker crossing home/away is emitted as a semantic *zone* trigger
    (HA's recommended way to fire on arrival/departure); an event.* entity
    (button/remote) fires on every event, so it becomes a state trigger on the
    entity with the specific press matched by a companion template condition (see
    ``_trigger_extra_conditions``); everything else stays a state trigger."""
    dom = entity.split(".")[0] if "." in entity else ""
    if dom in ("person", "device_tracker"):
        if state == "not_home":
            return {"platform": "zone", "entity_id": entity,
                    "zone": "zone.home", "event": "leave"}
        if state == "home":
            return {"platform": "zone", "entity_id": entity,
                    "zone": "zone.home", "event": "enter"}
    if dom == "event":
        return {"platform": "state", "entity_id": entity}
    if dom == "scene":
        # scene .state is a timestamp; any change to it is an activation
        return {"platform": "state", "entity_id": entity}
    return {"platform": "state", "entity_id": entity, "to": state}


def _trigger_extra_conditions(entity: str, state: str) -> list:
    """Companion conditions a trigger requires beyond the learned ones. An
    event.* entity fires on every press, so the specific press type is matched
    by a template condition on ``event_type`` — the reliable, integration-
    agnostic HA form for stateless event entities."""
    dom = entity.split(".")[0] if "." in entity else ""
    if dom == "event":
        return [{"condition": "template",
                 "value_template":
                     "{{ trigger.to_state.attributes.event_type == '%s' }}" % state}]
    return []


def _trigger_phrase(entity: str, state: str) -> str:
    """Readable lead-in for a sequence description given its trigger."""
    dom = entity.split(".")[0] if "." in entity else ""
    if dom in ("person", "device_tracker"):
        if state == "not_home":
            return f"When {entity} leaves home"
        if state == "home":
            return f"When {entity} arrives home"
    if dom == "event":
        return f"When {entity} is pressed ({state})"
    if dom == "scene":
        return f"When {entity} is activated"
    return f"When {entity} turns {state}"


def _numeric_value_at(epochs: list, values: list, t: float):
    """Value of a numeric series (sorted epochs + parallel values) at/just before
    time ``t``; None if ``t`` precedes the first reading. Pure, bisect-based."""
    import bisect
    if not epochs:
        return None
    i = bisect.bisect_right(epochs, t) - 1
    if i < 0:
        return None
    return values[i]


def _nice_threshold(v: float, op: str) -> float:
    """Round a raw boundary to a whole-number threshold that still *includes* the
    observed side: for 'below', one above the floor; for 'above', one below the
    ceil."""
    import math
    return float(math.floor(v) + 1) if op == "below" else float(math.ceil(v) - 1)


def _numeric_trigger_from(occ: list, baseline: list) -> Optional[dict]:
    """Given a sensor's values AT an action's occurrences (``occ``) and its
    overall values (``baseline``), decide whether the action consistently fires
    on one side of a threshold. Returns ``{"below": T}`` / ``{"above": T}`` / None.

    Guards against spurious correlation: the occurrence values must sit clearly
    in the low (or high) part of the sensor's range AND the sensor must spend
    real time on the *other* side of the threshold (a genuine crossing) — so a
    sensor that is simply always low never yields a bogus "below" trigger.
    """
    import statistics
    if len(occ) < MIN_OCCURRENCES or len(baseline) < 10:
        return None
    occ_s = sorted(occ)
    base_s = sorted(baseline)

    def pct(a, p):
        return a[min(len(a) - 1, max(0, int(round(p / 100.0 * (len(a) - 1)))))]

    base_med = statistics.median(base_s)
    occ_med = statistics.median(occ_s)
    base_p10, base_p90 = pct(base_s, 10), pct(base_s, 90)
    rng = base_p90 - base_p10
    if rng <= 0:
        return None                                   # flat/constant sensor

    # BELOW: occurrences concentrated low; sensor clearly rises above the bound.
    occ_p90 = pct(occ_s, 90)
    if occ_med < base_med and occ_p90 < base_med:
        T = _nice_threshold(occ_p90, "below")
        if base_p90 > T + 0.1 * rng:
            return {"below": T}

    # ABOVE: mirror image.
    occ_p10 = pct(occ_s, 10)
    if occ_med > base_med and occ_p10 > base_med:
        T = _nice_threshold(occ_p10, "above")
        if base_p10 < T - 0.1 * rng:
            return {"above": T}

    return None


def _numeric_condition(times: list, sensor_hist: dict) -> Optional[dict]:
    """Best numeric_state *condition* for an action whose occurrences (``times``)
    consistently coincide with a sensor sitting on one side of a threshold — e.g.
    "…and only while the temperature is below 62". Returns a self-describing HA
    condition dict, or None. Reuses the same scorer as the numeric trigger, so it
    shares the anti-spurious guards. ``sensor_hist`` maps sensor_id -> ``[(epoch,
    float)]``."""
    if not sensor_hist or len(times) < MIN_OCCURRENCES:
        return None
    best = None
    best_cover = 0
    for s_ent, events in sensor_hist.items():
        ev = sorted((e, v) for e, v in events if isinstance(v, (int, float)))
        if len(ev) < 10:
            continue
        epochs = [e for e, _ in ev]
        values = [v for _, v in ev]
        occ = [v for v in (_numeric_value_at(epochs, values, t) for t in times)
               if v is not None]
        if len(occ) < MIN_OCCURRENCES:
            continue
        trig = _numeric_trigger_from(occ, values)
        if not trig:
            continue
        op, T = next(iter(trig.items()))
        # Prefer the sensor whose readings cover the most occurrences.
        if len(occ) > best_cover:
            best_cover = len(occ)
            best = {"condition": "numeric_state", "entity_id": s_ent, op: T}
    return best


# ── Occupancy-aware, IFTTT-style suggestions (v7.100.0) ──────────────────────
# Turn the engine's raw correlations into automations that respect who is
# actually in the room: gate an action on presence, hold a light on until the
# area empties, or confirm a step (car in the garage) before the next. The
# analyzer pre-fetches occupancy context on the event loop and passes it to the
# pure finders as ``occ_ctx`` so they stay unit-testable off a live HA:
#   occ_ctx = {
#     "hist": {sensor_id: [(epoch, is_on_bool), ...]},   # occupancy sensor history
#     "entity_area": {entity_id: area_id},               # action entity -> area
#     "area_sensors": {area_id: [occupancy_sensor_id, ...]},  # presence-first order
#   }

def _bool_state_at(epochs: list, states: list, t: float):
    """Boolean state of an on/off history (sorted epochs + parallel bools) at or
    just before time ``t``; None if ``t`` precedes the first reading. Pure."""
    import bisect
    if not epochs:
        return None
    i = bisect.bisect_right(epochs, t) - 1
    if i < 0:
        return None
    return states[i]


def _occupancy_condition(action_entity: str, times: list, occ_ctx: dict) -> Optional[dict]:
    """A "while the room is occupied" condition for an action whose occurrences
    (``times``) consistently coincide with an occupancy sensor in the action's
    area reading ``on`` — and where that sensor genuinely varies (spends real
    time ``off`` too), so it's a real gate and not an always-on sensor. Returns
    an HA state-condition dict or None. Pure."""
    if not occ_ctx or len(times) < MIN_OCCURRENCES:
        return None
    hist = occ_ctx.get("hist") or {}
    area = (occ_ctx.get("entity_area") or {}).get(action_entity)
    if not area:
        return None
    best = None
    best_cover = 0
    for s_ent in (occ_ctx.get("area_sensors") or {}).get(area, []):
        series = hist.get(s_ent)
        if not series:
            continue
        epochs = [e for e, _ in series]
        states = [bool(v) for _, v in series]
        if not any(states) or all(states):        # must actually vary
            continue
        on_ct = sum(1 for t in times if _bool_state_at(epochs, states, t))
        need = max(MIN_OCCURRENCES, int(round(0.8 * len(times))))
        if on_ct >= need and on_ct > best_cover:
            best_cover = on_ct
            best = {"condition": "state", "entity_id": s_ent, "state": "on"}
    return best


def _occupancy_hold(action_entity: str, off_times: list, occ_ctx: dict) -> Optional[dict]:
    """If an entity's OFF events (``off_times``) consistently happen shortly after
    an occupancy sensor in its area clears, this is a "keep it on until the room
    empties" pattern. Returns ``{"entity_id": sensor, "for_seconds": N}`` (a small
    settle delay so a brief exit doesn't kill it) or None. Pure."""
    import bisect
    import statistics
    if not occ_ctx or len(off_times) < MIN_OCCURRENCES:
        return None
    hist = occ_ctx.get("hist") or {}
    area = (occ_ctx.get("entity_area") or {}).get(action_entity)
    if not area:
        return None
    HOLD_WINDOW = 900.0        # the light goes off within 15 min of the room clearing
    best = None
    best_cover = 0
    for s_ent in (occ_ctx.get("area_sensors") or {}).get(area, []):
        series = sorted(hist.get(s_ent) or [])
        clears = [series[i][0] for i in range(1, len(series))
                  if series[i - 1][1] and not series[i][1]]      # on -> off
        if len(clears) < MIN_OCCURRENCES:
            continue
        cl = sorted(clears)
        matched, lags = 0, []
        for t in off_times:
            j = bisect.bisect_right(cl, t) - 1
            if j >= 0 and 0 <= t - cl[j] <= HOLD_WINDOW:
                matched += 1
                lags.append(t - cl[j])
        need = max(MIN_OCCURRENCES, int(round(0.6 * len(off_times))))
        if matched >= need and matched > best_cover:
            best_cover = matched
            settle = int(min(600, max(0, round(statistics.median(lags) / 30.0) * 30)))
            best = {"entity_id": s_ent, "for_seconds": settle}
    return best


def _secs_to_hms(seconds) -> str:
    """Whole seconds -> 'HH:MM:SS' for an HA delay/for/timeout field. Pure."""
    s = max(0, int(seconds or 0))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _garage_confirm_enabled() -> bool:
    """Whether the user opted into Phase-3 confirmation-sequence suggestions
    (safety-sensitive; off by default). Config read — call off the event loop."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("suggest_garage_confirmation", False))
    except Exception:
        return False


class PatternAnalyzer:
    """Analyzes accumulated state change data for behavioral patterns."""

    def __init__(self):
        self._last_analysis: float = 0.0
        self._last_result: dict = {}
        self._db = DB_PATH
        self._occ_ctx: dict = {}

    def _connect(self) -> Optional[sqlite3.Connection]:
        """Open a connection to patterns.db.

        Connections are created and consumed by the same executor worker in
        :meth:`_run_all_finders`. Other synchronous callers use this helper
        entirely within their own calling thread.
        """
        try:
            if not Path(self._db).exists():
                return None
            conn = sqlite3.connect(self._db, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:
            return None

    def should_analyze(self) -> bool:
        """Check if enough data and time has passed for analysis."""
        if (time.time() - self._last_analysis) < ANALYSIS_INTERVAL:
            return False
        conn = self._connect()
        if not conn:
            return False
        try:
            oldest = conn.execute(
                "SELECT MIN(timestamp) FROM state_changes"
            ).fetchone()[0]
            if not oldest:
                return False
            days = (datetime.now() - datetime.fromisoformat(oldest)).days
            count = conn.execute("SELECT COUNT(*) FROM state_changes").fetchone()[0]
            conn.close()
            return days >= MIN_DAYS and count >= 50
        except Exception:
            return False

    def pattern_diagnostic(self) -> dict:
        """Explain why routines may not be forming: the busiest sources (flood
        check) and the strongest routine CANDIDATES with their distinct-day
        coverage — so a near-miss (seen on almost enough days, or split across
        adjacent hours) is visible instead of just "0 found". Pure DB read.
        """
        out = {"top_sources": [], "candidates": [], "total_days": 0,
               "min_days": 0, "min_occurrences": MIN_OCCURRENCES}
        conn = self._connect()
        if not conn:
            return out
        try:
            total_days = int((conn.execute(
                "SELECT COUNT(DISTINCT date(timestamp)) FROM state_changes "
                "WHERE timestamp > datetime('now', '-30 days')").fetchone()[0]) or 0)
            out["total_days"] = total_days
            # coverage >= 0.3 -> need ceil(0.3 * total_days) distinct days
            out["min_days"] = (total_days * 3 + 9) // 10 if total_days else 0
            for e, c in conn.execute(
                    "SELECT entity_id, COUNT(*) c FROM state_changes "
                    "WHERE timestamp > datetime('now', '-30 days') "
                    "GROUP BY entity_id ORDER BY c DESC LIMIT 10"):
                out["top_sources"].append({"entity_id": e, "changes": int(c)})
            for e, s, h, cnt, days in conn.execute(
                    "SELECT entity_id, new_state, hour, COUNT(*) cnt, "
                    "COUNT(DISTINCT date(timestamp)) days FROM state_changes "
                    "WHERE timestamp > datetime('now', '-30 days') "
                    "GROUP BY entity_id, new_state, hour HAVING cnt >= 2 "
                    "ORDER BY days DESC, cnt DESC LIMIT 12"):
                out["candidates"].append({
                    "entity_id": e, "state": s, "hour": int(h),
                    "occurrences": int(cnt), "days": int(days),
                    "coverage": round(int(days) / total_days, 2) if total_days else 0.0,
                })
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return out

    def _run_all_finders(self, person_map: dict,
                         lat, lon, sensor_hist: dict,
                         occ_ctx: dict = None,
                         confirm_enabled: bool = None) -> list[DetectedPattern]:
        """Open the DB, run every finder, and close it on one executor thread.

        Everything from first use to close of the connection happens on this
        one executor thread. If ``analyze()`` is cancelled while awaiting this
        job, the underlying worker thread still runs to completion (a running
        executor job cannot be interrupted), so closing ``conn`` here — rather
        than back on the event-loop thread once the await is abandoned — means
        the close can never race a still-running query on this connection.
        """
        conn = self._connect()
        if not conn:
            return []
        # The safety-sensitive confirmation finder is opt-in; when the caller
        # doesn't pass an explicit choice, read it here on the executor thread so
        # analyze() gains no extra await point.
        if confirm_enabled is None:
            confirm_enabled = _garage_confirm_enabled()
        patterns: list[DetectedPattern] = []
        try:
            occ = occ_ctx or {}
            finders = [
                ("time routines", lambda: self._find_time_routines(conn, person_map)),
                ("repeated commands", lambda: self._find_repeated_commands(conn)),
                ("sequence patterns", lambda: self._find_sequence_patterns(
                    conn, lat, lon, sensor_hist, occ)),
                ("numeric triggers", lambda: self._find_numeric_triggers(conn, sensor_hist, occ)),
                ("presence patterns", lambda: self._find_presence_patterns(conn)),
            ]
            # Phase 3 is safety-sensitive (it can suggest auto-closing a cover), so
            # it only runs when the user has opted in under Settings.
            if confirm_enabled:
                finders.append(
                    ("confirmation sequences",
                     lambda: self._find_confirmation_sequences(conn, occ)))
            for name, finder in finders:
                try:
                    patterns.extend(finder())
                except Exception as exc:
                    _LOGGER.warning("Pattern finder %s failed: %s", name, exc)
        finally:
            conn.close()
        return patterns

    async def analyze(self, hass: HomeAssistant) -> list[DetectedPattern]:
        """Run full pattern analysis. Returns detected patterns."""
        self._last_analysis = time.time()
        patterns = []
        try:
            person_map = self._person_entity_map(hass)
            try:
                _sensor_hist = await self._fetch_numeric_sensor_history(hass)
            except Exception:
                _sensor_hist = {}
            _lat = getattr(hass.config, "latitude", None)
            _lon = getattr(hass.config, "longitude", None)
            try:
                _occ_ctx = await self._fetch_occupancy_context(hass)
            except Exception:
                _occ_ctx = {}
            executor_job = hass.async_add_executor_job(
                self._run_all_finders, person_map, _lat, _lon, _sensor_hist, _occ_ctx)
            # Shielded: if this await is cancelled, only our wait on the job
            # stops — the job itself is not cancelled, so it can't be pulled
            # out of the executor queue before _run_all_finders starts (which
            # would skip its finally and leak conn). The job still runs to
            # completion and closes conn itself.
            patterns = await asyncio.shield(executor_job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.warning("Pattern analysis error: %s", exc)

        # Clear any pending suggestions that aren't actionable automations —
        # legacy rows stored before the actionability filter, so the review list
        # only ever shows real trigger+action automations.
        await hass.async_add_executor_job(self._purge_non_actionable_suggestions)

        # Store high-confidence patterns as suggestions
        new_suggestions = 0
        new_person_patterns = 0
        _eff_threshold = await hass.async_add_executor_job(_effective_threshold)
        near_misses: list = []
        # A sequence stores when count/(MIN_OCCURRENCES*3) >= threshold; surface
        # how many recurrences a not-yet-stored one still needs.
        _seq_needed = int(_eff_threshold * MIN_OCCURRENCES * 3)
        if _eff_threshold * MIN_OCCURRENCES * 3 > _seq_needed:
            _seq_needed += 1
        for p in patterns:
            if p.confidence >= _eff_threshold:
                stored = await hass.async_add_executor_job(
                    self._store_suggestion, p)
                if stored:
                    new_suggestions += 1
                # v6.41.0: patterns confidently owned by one person also land
                # in person_patterns — the dedicated per-person routine store
                # (independent of the household suggestions/automations flow).
                if p.details.get("person"):
                    if await hass.async_add_executor_job(
                            self._store_person_pattern, p):
                        new_person_patterns += 1
            elif p.occurrences >= MIN_OCCURRENCES and len(near_misses) < 8:
                # Detected but below the store bar — show it building so "not
                # enough data yet" is distinguishable from "nothing detected".
                near_misses.append({
                    "type": p.pattern_type,
                    "description": p.description,
                    "occurrences": p.occurrences,
                    "needed": _seq_needed if p.pattern_type == "sequence" else None,
                })

        # Promote the most reliable routines/commands into the curated knowledge
        # store as *observed* facts, so they surface in the Memory tab (marked ~)
        # and inject into conversation. Sequences/presence stay as automations only.
        # v6.41.0: a pattern confidently owned by one person is attributed to
        # that person's knowledge subject rather than "household".
        promoted = await hass.async_add_executor_job(
            self._promote_to_knowledge, patterns)

        # Record the outcome of this pass so the panel can show "last analysis:
        # ran at T, N found, M stored" — the difference between "never ran" and
        # "ran, found nothing worth surfacing".
        self._last_result = {
            "ts": time.time(),
            "patterns_found": len(patterns),
            "new_suggestions": new_suggestions,
            "person_routines": new_person_patterns,
            "facts": promoted,
            "near_misses": near_misses,
        }

        if patterns:
            _LOGGER.info(
                "Pattern analysis: %d patterns found, %d new suggestions, "
                "%d facts learned, %d person routines (threshold=%.0f%%)",
                len(patterns), new_suggestions, promoted, new_person_patterns,
                _eff_threshold * 100,
            )

        return patterns

    def _person_entity_map(self, hass) -> dict:
        """Map every way a person might be recorded (friendly name, normalized
        name, or entity_id) -> that person's entity_id, so a routine's learned
        owner can be resolved to a conditionable ``person.*`` entity."""
        out: dict = {}
        try:
            from .identity import normalize
        except Exception:
            def normalize(n):
                return "_".join((n or "").strip().lower().split())
        try:
            for st in hass.states.async_all("person"):
                ent = st.entity_id
                fn = st.attributes.get("friendly_name") or ent.split(".", 1)[-1]
                for k in (fn, normalize(fn), ent):
                    if k:
                        out[k] = ent
        except Exception:
            return {}
        return out

    def _find_time_routines(self, conn: sqlite3.Connection,
                            person_map: dict = None) -> list[DetectedPattern]:
        """Find entities that change state at similar times each day."""
        patterns = []

        # Group state changes by entity + action, look for time clustering
        rows = conn.execute("""
            SELECT entity_id, new_state, hour, day_of_week, COUNT(*) as cnt
            FROM state_changes
            WHERE timestamp > datetime('now', '-30 days')
            GROUP BY entity_id, new_state, hour
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, (MIN_OCCURRENCES,)).fetchall()

        # Opportunity days: distinct days we were observing at all (constant
        # across rows — computed once, was previously re-run per row and made a
        # large history crawl).
        total_days = conn.execute("""
            SELECT COUNT(DISTINCT date(timestamp)) FROM state_changes
            WHERE timestamp > datetime('now', '-30 days')
        """).fetchone()[0] or 1

        for row in rows:
            entity = row["entity_id"]
            state = row["new_state"]
            hour = row["hour"]
            count = row["cnt"]

            # Positive days: distinct days this routine ACTUALLY happened. Using
            # distinct days (not raw event count) so several same-hour events on
            # one day count once — the honest "on N of M days" numerator.
            positive_days = conn.execute("""
                SELECT COUNT(DISTINCT date(timestamp)) FROM state_changes
                WHERE entity_id = ? AND new_state = ? AND hour = ?
                  AND timestamp > datetime('now', '-30 days')
            """, (entity, state, hour)).fetchone()[0] or 0

            # Coverage weighs the negative evidence: a routine on 42 of 45 days
            # (0.93) is far stronger than one on 42 of 120 days (0.35), even
            # though both were "seen 42 times".
            coverage = positive_days / total_days if total_days else 0.0
            negative_days = max(0, total_days - positive_days)
            if coverage < 0.3:
                continue

            # Confidence = coverage, discounted for a small sample so a 3-of-3
            # (1.0) can't outrank a 40-of-45 (0.89) on three data points.
            sample_factor = min(1.0, positive_days / MIN_OCCURRENCES)
            confidence = round(coverage * sample_factor, 3)

            time_str = f"{hour:02d}:00"
            details = {
                "hour": hour, "state": state,
                "coverage": round(coverage, 2),
                "consistency": round(coverage, 2),   # back-compat key
                "observed_days": positive_days,
                "opportunity_days": total_days,
                "skipped_days": negative_days,
            }

            # v6.41.0: a single sole-occupant person can own this routine
            # outright; otherwise it stays household-wide, unchanged.
            person = self._dominant_person(conn, "state_changes", entity=entity,
                                            state=state, hour=hour)
            days_str = f"on {positive_days} of {total_days} days"
            if person:
                details["person"] = person
                # If the owner resolves to a person entity, gate the routine on
                # their presence — a time trigger has no inherent presence, so
                # "only when home" is a real guard (and the user still approves
                # it, so a deliberately away-running routine can be declined).
                ent = (person_map or {}).get(person)
                if ent:
                    details["condition"] = {"condition": "state",
                                            "entity_id": ent, "state": "home"}
                desc = (f"{entity} turns {state} around {time_str} {days_str} "
                        f"when {person} is home")
            elif state in ("on", "off"):
                desc = f"{entity} turns {state} around {time_str} {days_str}"
            else:
                desc = f"{entity} changes to '{state}' around {time_str} {days_str}"

            patterns.append(DetectedPattern(
                pattern_type="time_routine",
                description=desc,
                entity_ids=[entity],
                confidence=confidence,
                occurrences=count,
                coverage=round(coverage, 3),
                details=details,
            ))

        return patterns[:20]  # Cap at 20

    def _find_repeated_commands(self, conn: sqlite3.Connection) -> list[DetectedPattern]:
        """Find voice commands that repeat at similar times."""
        patterns = []

        try:
            rows = conn.execute("""
                SELECT text, hour, COUNT(*) as cnt
                FROM commands
                WHERE timestamp > datetime('now', '-30 days')
                GROUP BY text, hour
                HAVING cnt >= ?
                ORDER BY cnt DESC
                LIMIT 20
            """, (MIN_OCCURRENCES,)).fetchall()
        except Exception:
            return patterns

        for row in rows:
            text = row["text"]
            hour = row["hour"]
            count = row["cnt"]

            total_same_cmd = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE text = ?", (text,)
            ).fetchone()[0]

            confidence = min(1.0, (count / total_same_cmd) * 0.8 + 0.2)
            details = {"command": text, "hour": hour}

            person = self._dominant_person(conn, "commands", text=text, hour=hour)
            if person:
                details["person"] = person
                desc = (f"{person} says '{text}' around {hour:02d}:00 regularly "
                        f"({count} times)")
            else:
                desc = f"'{text}' is said around {hour:02d}:00 regularly ({count} times)"

            patterns.append(DetectedPattern(
                pattern_type="repeated_command",
                description=desc,
                entity_ids=[],
                confidence=confidence,
                occurrences=count,
                details=details,
            ))

        return patterns

    def _find_sequence_patterns(self, conn: sqlite3.Connection,
                                lat=None, lon=None,
                                sensor_hist=None, occ_ctx=None) -> list[DetectedPattern]:
        """Find state changes that consistently follow each other within 10 min.

        Single-pass sliding window. This replaced an O(N^2) SQL self-join whose
        datetime()-wrapped comparison also defeated the timestamp index — on a
        large history (100k+ rows) it never finished, stalling the whole
        analyze() pass so no suggestions were ever stored. This reads rows in
        indexed time order and counts cross-entity pairs inside the window, with
        a hard window cap so an activity burst can't blow up the pairing.

        Pairs are cross-DOMAIN (a switch can trigger a light, a cover a fan): a
        real "when X, do Y" automation rarely stays within one domain. The
        typical lag between trigger and action is measured so the suggested
        automation carries the real delay instead of a fixed guess.
        """
        from collections import deque, Counter
        patterns: list = []
        try:
            rows = conn.execute(
                "SELECT timestamp, entity_id, domain, new_state FROM state_changes "
                "WHERE timestamp > datetime('now', '-30 days') ORDER BY timestamp"
            ).fetchall()
        except Exception:
            return patterns

        window_s = 600.0        # pairs within 10 minutes
        window_cap = 200        # bound pairing work during activity bursts
        win: deque = deque()    # (epoch, entity, domain, state)
        pair_counts: Counter = Counter()
        pair_lag: dict = {}     # (ea,sa,eb,sb) -> [sum_seconds, count] for mean lag
        pair_times: dict = {}   # (ea,sa,eb,sb) -> [action epochs] (capped) for time window

        for r in rows:
            try:
                epoch = datetime.fromisoformat(r["timestamp"]).timestamp()
            except (ValueError, TypeError):
                continue
            ent = r["entity_id"]
            dom = r["domain"]
            st = r["new_state"]
            cutoff = epoch - window_s
            while win and win[0][0] < cutoff:
                win.popleft()
            for a_epoch, a_ent, a_dom, a_st in win:
                if a_ent != ent:                       # cross-domain allowed
                    key = (a_ent, a_st, ent, st)
                    pair_counts[key] += 1
                    slot = pair_lag.get(key)
                    lag = epoch - a_epoch
                    if slot is None:
                        pair_lag[key] = [lag, 1]
                    else:
                        slot[0] += lag
                        slot[1] += 1
                    tl = pair_times.get(key)
                    if tl is None:
                        pair_times[key] = [epoch]
                    elif len(tl) < 40:
                        tl.append(epoch)
            win.append((epoch, ent, dom, st))
            if len(win) > window_cap:
                win.popleft()

        for (ea, sa, eb, sb), count in pair_counts.most_common(15):
            if count < MIN_OCCURRENCES:
                break
            slot = pair_lag.get((ea, sa, eb, sb), [0.0, 1])
            mean_lag = int(round(slot[0] / max(1, slot[1])))
            times = pair_times.get((ea, sa, eb, sb), [])
            # Accumulate every discriminator that consistently holds; HA ANDs a
            # list of conditions. Prefer a sun condition ("after dark") over a
            # fixed time window (it tracks the season), then add a numeric-state
            # condition ("…while it's below/above X") when a sensor consistently
            # sits on one side at the action times.
            conds: list = []
            tw = _sun_condition(times, lat, lon) or _time_window_condition(times)
            if tw:
                conds.append(tw)
            nc = _numeric_condition(times, sensor_hist or {})
            if nc:
                conds.append(nc)
            # Phase 1: gate the action on room occupancy when the action's area
            # was consistently occupied at the action times (example: lights only
            # while the room is occupied).
            oc = _occupancy_condition(eb, times, occ_ctx or {})
            if oc:
                conds.append(oc)
            cond = conds if conds else None
            _verb = _action_verb(eb, sb)
            # Phase 2: if this action turns something ON and that entity is
            # consistently turned OFF once its area empties, suggest a "hold on
            # until unoccupied" choreography instead of a bare on.
            until = None
            if _verb and str(sb).lower() == "on":
                until = _occupancy_hold(eb, self._entity_off_times(conn, eb),
                                        occ_ctx or {})
            _when = f"({count} times in 30 days, ~{mean_lag}s later)"
            if until:
                _area_sensor = _pretty_entity(until["entity_id"])
                desc = (f"{_trigger_phrase(ea, sa)}, JARVIS will {_verb} {eb} and "
                        f"keep it on until {_area_sensor} is clear "
                        f"{_when}" + _condition_phrase(cond))
            elif _verb:
                # Lead with the outcome the automation would produce.
                desc = (f"{_trigger_phrase(ea, sa)}, JARVIS will {_verb} {eb} "
                        f"{_when}" + _condition_phrase(cond))
            else:
                # No clean device action (e.g. two camera/sensor states that just
                # co-occur) — say so plainly instead of implying it can be automated.
                desc = (f"{_trigger_phrase(ea, sa)}, {eb} usually turns {sb} "
                        f"shortly after — no device action to automate {_when}"
                        + _condition_phrase(cond))
            patterns.append(DetectedPattern(
                pattern_type="sequence",
                description=desc,
                entity_ids=[ea, eb],
                confidence=min(1.0, count / (MIN_OCCURRENCES * 3)),
                occurrences=count,
                details={"trigger": {"entity": ea, "state": sa},
                         "action": {"entity": eb, "state": sb},
                         "delay_seconds": mean_lag,
                         "condition": cond,
                         "until_unoccupied": until},
            ))

        return patterns

    def _entity_off_times(self, conn: sqlite3.Connection, entity_id: str) -> list:
        """Epochs at which ``entity_id`` turned off in the last 30 days (for the
        'hold until unoccupied' off-edge). Bounded, failure-tolerant → []."""
        out: list = []
        try:
            rows = conn.execute(
                "SELECT timestamp FROM state_changes WHERE entity_id = ? "
                "AND new_state = 'off' AND timestamp > datetime('now','-30 days') "
                "ORDER BY timestamp LIMIT 400", (entity_id,)).fetchall()
            for r in rows:
                try:
                    out.append(datetime.fromisoformat(r["timestamp"]).timestamp())
                except (ValueError, TypeError):
                    continue
        except Exception:
            pass
        return out

    def _find_numeric_triggers(self, conn: sqlite3.Connection,
                               sensor_hist: dict, occ_ctx=None) -> list[DetectedPattern]:
        """Learn "when a sensor crosses a threshold, an action happens" from
        history. ``sensor_hist`` maps sensor_id -> chronological ``[(epoch,
        float)]`` (fetched from the recorder by the caller and passed in, so this
        stays unit-testable without the recorder). Bounded: the most active
        actions only, few numeric sensors, strong consistency in the scorer.
        """
        patterns: list = []
        if not sensor_hist:
            return patterns
        _ACT = ("light", "switch", "cover", "lock", "climate", "fan",
                "media_player", "humidifier", "water_heater", "valve")
        try:
            rows = conn.execute(
                "SELECT entity_id, new_state, timestamp FROM state_changes "
                "WHERE timestamp > datetime('now', '-30 days') AND domain IN ({}) "
                "ORDER BY timestamp".format(",".join("'%s'" % d for d in _ACT))
            ).fetchall()
        except Exception:
            return patterns

        action_times: dict = {}
        for r in rows:
            st = r["new_state"]
            if st in ("unavailable", "unknown"):
                continue
            try:
                ep = datetime.fromisoformat(r["timestamp"]).timestamp()
            except (ValueError, TypeError):
                continue
            action_times.setdefault((r["entity_id"], st), []).append(ep)

        prepared: dict = {}
        for s_ent, events in sensor_hist.items():
            ev = [(e, v) for e, v in events if isinstance(v, (int, float))]
            if len(ev) >= 10:
                ev.sort()
                prepared[s_ent] = ([e for e, _ in ev], [v for _, v in ev])
        if not prepared:
            return patterns

        # Most active actions only — bounds the sensor×action correlation work.
        ranked = sorted(action_times.items(), key=lambda kv: len(kv[1]),
                        reverse=True)[:20]
        for (a_ent, a_st), times in ranked:
            if len(times) < MIN_OCCURRENCES:
                continue
            for s_ent, (epochs, values) in prepared.items():
                occ = []
                for t in times:
                    v = _numeric_value_at(epochs, values, t)
                    if v is not None:
                        occ.append(v)
                if len(occ) < MIN_OCCURRENCES:
                    continue
                trig = _numeric_trigger_from(occ, values)
                if not trig:
                    continue
                op, T = next(iter(trig.items()))
                # Phase 1: only while the room is occupied (example: lumens drop
                # -> lights on, but only if someone is actually in the room).
                oc = _occupancy_condition(a_ent, times, occ_ctx or {})
                occ_txt = (f" while {_pretty_entity(oc['entity_id'])} is occupied"
                           if oc else "")
                patterns.append(DetectedPattern(
                    pattern_type="numeric_trigger",
                    description=(f"When {s_ent} goes {op} {T:g}, {a_ent} turns "
                                 f"{a_st}{occ_txt} ({len(occ)} times in 30 days)"),
                    entity_ids=[s_ent, a_ent],
                    confidence=min(1.0, len(occ) / (MIN_OCCURRENCES * 3)),
                    occurrences=len(occ),
                    details={"trigger_sensor": s_ent, "op": op, "threshold": T,
                             "action": {"entity": a_ent, "state": a_st},
                             "condition": [oc] if oc else None},
                ))
        return patterns

    def _find_confirmation_sequences(self, conn: sqlite3.Connection,
                                     occ_ctx=None) -> list[DetectedPattern]:
        """Phase 3 (safety-sensitive): learn a *confirmed* choreography —
        trigger (someone arrives) → open a cover → a confirmation sensor turns on
        (e.g. the car is detected inside) → close the cover. Emitted only for
        cover targets and always routed through the review/approval UI, with a
        wait-timeout so a missing confirmation leaves the cover open. Conservative
        (high recurrence, bounded windows). Never raises."""
        import bisect
        import statistics
        from collections import Counter
        patterns: list = []
        try:
            rows = conn.execute(
                "SELECT timestamp, entity_id, domain, new_state FROM state_changes "
                "WHERE timestamp > datetime('now','-30 days') AND domain IN "
                "('cover','person','device_tracker','binary_sensor') ORDER BY timestamp"
            ).fetchall()
        except Exception:
            return patterns

        cover_ev: dict = defaultdict(list)   # cover -> [(epoch, state)]
        arrivals: list = []                  # [(epoch, entity)]
        on_events: dict = defaultdict(list)  # binary_sensor -> [epoch]
        for r in rows:
            try:
                ep = datetime.fromisoformat(r["timestamp"]).timestamp()
            except (ValueError, TypeError):
                continue
            dom, ent, st = r["domain"], r["entity_id"], r["new_state"]
            if dom == "cover":
                cover_ev[ent].append((ep, st))
            elif dom in ("person", "device_tracker") and st == "home":
                arrivals.append((ep, ent))
            elif dom == "binary_sensor" and st == "on":
                on_events[ent].append(ep)

        arrivals.sort()
        arr_ep = [e for e, _ in arrivals]
        OPEN, CLOSE = ("open", "opening"), ("closed", "closing")
        OPEN_CLOSE_MAX, ARRIVE_BEFORE = 900.0, 300.0
        entity_area = (occ_ctx or {}).get("entity_area") or {}
        area_sensors = (occ_ctx or {}).get("area_sensors") or {}
        combos: Counter = Counter()
        combo_lags: dict = defaultdict(list)

        for cover, evs in cover_ev.items():
            evs.sort()
            last_open = None
            for ep, st in evs:
                if st in OPEN:
                    last_open = ep
                elif st in CLOSE and last_open is not None and 30 <= ep - last_open <= OPEN_CLOSE_MAX:
                    open_ep, close_ep, last_open = last_open, ep, None
                    j = bisect.bisect_right(arr_ep, open_ep) - 1
                    if not (j >= 0 and 0 <= open_ep - arr_ep[j] <= ARRIVE_BEFORE):
                        continue
                    trig_entity = arrivals[j][1]
                    # confirmation sensor: on between open & close, area sensor first
                    confirm = None
                    for s in area_sensors.get(entity_area.get(cover), []):
                        if any(open_ep <= t <= close_ep for t in on_events.get(s, [])):
                            confirm = s
                            break
                    if not confirm:
                        for s, ts in on_events.items():
                            if any(open_ep <= t <= close_ep for t in ts):
                                confirm = s
                                break
                    if not confirm:
                        continue
                    key = (cover, trig_entity, confirm)
                    combos[key] += 1
                    combo_lags[key].append(close_ep - open_ep)

        for (cover, trig_entity, confirm), cnt in combos.most_common(5):
            if cnt < MIN_OCCURRENCES:
                break
            typ = int(statistics.median(combo_lags[(cover, trig_entity, confirm)]))
            patterns.append(DetectedPattern(
                pattern_type="confirm_sequence",
                description=(f"⚠ When {trig_entity} arrives home, open {cover}, then "
                             f"close it once {confirm} confirms ({cnt} times in 30 "
                             f"days) — SAFETY-SENSITIVE, review carefully"),
                entity_ids=[trig_entity, cover, confirm],
                confidence=min(1.0, cnt / (MIN_OCCURRENCES * 3)),
                occurrences=cnt,
                details={"trigger": {"entity": trig_entity, "state": "home"},
                         "open": {"entity": cover, "state": "open"},
                         "close": {"entity": cover, "state": "closed"},
                         "confirm": {"entity": confirm, "state": "on"},
                         "confirm_timeout": max(60, min(600, typ + 60))},
            ))
        return patterns

    async def _fetch_numeric_sensor_history(self, hass) -> dict:
        """Fetch recent recorder history for numeric sensors likely to drive
        automations (temperature / humidity / illuminance). Returns
        {sensor_id: [(epoch, float)]}. Bounded and failure-tolerant (→ {})."""
        out: dict = {}
        try:
            from homeassistant.components.recorder import get_instance, history
            from homeassistant.util import dt as dt_util
        except Exception:
            return out
        wanted = ("temperature", "humidity", "illuminance")
        ids: list = []
        try:
            for st in hass.states.async_all("sensor"):
                if st.attributes.get("device_class") in wanted:
                    ids.append(st.entity_id)
        except Exception:
            return out
        ids = ids[:30]
        if not ids:
            return out
        end = dt_util.utcnow()
        start = end - timedelta(days=30)

        def _fetch():
            return history.get_significant_states(
                hass, start, end, ids, minimal_response=True, no_attributes=True)

        try:
            raw = await get_instance(hass).async_add_executor_job(_fetch)
        except Exception:
            return out
        for eid, states in (raw or {}).items():
            series: list = []
            for s in states:
                try:
                    val = getattr(s, "state", None)
                    when = (getattr(s, "last_changed", None)
                            or getattr(s, "last_updated", None))
                    if val is None and isinstance(s, dict):
                        val = s.get("state")
                        when = s.get("last_changed") or s.get("last_updated")
                    fv = float(val)
                    ep = when.timestamp() if hasattr(when, "timestamp") else None
                    if ep is not None:
                        series.append((ep, fv))
                except (TypeError, ValueError):
                    continue
            if len(series) >= 10:
                out[eid] = series
        return out

    async def _fetch_occupancy_context(self, hass) -> dict:
        """Build the occupancy context the occupancy-aware finders need:
          hist:         {occupancy_sensor: [(epoch, is_on_bool)]}  (recorder)
          entity_area:  {entity_id: area_id}  for occupancy sensors AND
                        controllable action entities (light/switch/cover/…)
          area_sensors: {area_id: [occupancy_sensor, …]}  presence/occupancy first
        Bounded and failure-tolerant (→ empty context)."""
        ctx: dict = {"hist": {}, "entity_area": {}, "area_sensors": {}}
        try:
            from homeassistant.components.recorder import get_instance, history
            from homeassistant.util import dt as dt_util
            from homeassistant.helpers import entity_registry as er
        except Exception:
            return ctx
        try:
            ent_reg = er.async_get(hass)
        except Exception:
            return ctx

        def _area_of(entity_id):
            try:
                e = ent_reg.async_get(entity_id)
                if not e:
                    return None
                if e.area_id:
                    return e.area_id
                if e.device_id:
                    from homeassistant.helpers import device_registry as dr
                    dev = dr.async_get(hass).async_get(e.device_id)
                    return dev.area_id if dev else None
            except Exception:
                return None
            return None

        occ_ids: list = []
        try:
            for st in hass.states.async_all("binary_sensor"):
                dc = st.attributes.get("device_class")
                if dc not in ("occupancy", "presence", "motion"):
                    continue
                area = _area_of(st.entity_id)
                if not area:
                    continue
                occ_ids.append(st.entity_id)
                ctx["entity_area"][st.entity_id] = area
                bucket = ctx["area_sensors"].setdefault(area, [])
                if dc in ("occupancy", "presence"):   # they linger — prefer them
                    bucket.insert(0, st.entity_id)
                else:
                    bucket.append(st.entity_id)
        except Exception:
            return ctx

        try:
            for dom in ("light", "switch", "fan", "cover", "climate", "lock",
                        "media_player"):
                for st in hass.states.async_all(dom):
                    area = _area_of(st.entity_id)
                    if area:
                        ctx["entity_area"][st.entity_id] = area
        except Exception:
            pass

        occ_ids = occ_ids[:40]
        if not occ_ids:
            return ctx
        end = dt_util.utcnow()
        start = end - timedelta(days=30)

        def _fetch():
            return history.get_significant_states(
                hass, start, end, occ_ids, minimal_response=True, no_attributes=True)

        try:
            raw = await get_instance(hass).async_add_executor_job(_fetch)
        except Exception:
            return ctx
        for eid, states in (raw or {}).items():
            series: list = []
            for s in states:
                try:
                    val = getattr(s, "state", None)
                    when = (getattr(s, "last_changed", None)
                            or getattr(s, "last_updated", None))
                    if val is None and isinstance(s, dict):
                        val = s.get("state")
                        when = s.get("last_changed") or s.get("last_updated")
                    ep = when.timestamp() if hasattr(when, "timestamp") else None
                    if ep is not None and val in ("on", "off"):
                        series.append((ep, val == "on"))
                except Exception:
                    continue
            if len(series) >= 4:
                ctx["hist"][eid] = series
        return ctx

    def _find_presence_patterns(self, conn: sqlite3.Connection) -> list[DetectedPattern]:
        """Find state changes correlated with person arrivals/departures."""
        patterns = []

        # Look for state changes that happen within 5 min of person state changes
        try:
            rows = conn.execute("""
                SELECT
                    a.entity_id as person_entity,
                    a.new_state as person_state,
                    b.entity_id as device_entity,
                    b.new_state as device_state,
                    COUNT(*) as cnt
                FROM state_changes a
                JOIN state_changes b ON
                    b.timestamp > a.timestamp AND
                    b.timestamp <= datetime(a.timestamp, '+5 minutes') AND
                    a.entity_id != b.entity_id
                WHERE a.timestamp > datetime('now', '-30 days')
                    AND a.domain = 'person'
                GROUP BY a.entity_id, a.new_state, b.entity_id, b.new_state
                HAVING cnt >= ?
                ORDER BY cnt DESC
                LIMIT 10
            """, (max(3, MIN_OCCURRENCES // 2),)).fetchall()
        except Exception:
            return patterns

        for row in rows:
            person = row["person_entity"]
            p_state = row["person_state"]
            device = row["device_entity"]
            d_state = row["device_state"]
            count = row["cnt"]

            action_word = "arrives" if p_state == "home" else "leaves"
            confidence = min(1.0, count / MIN_OCCURRENCES * 0.7)

            patterns.append(DetectedPattern(
                pattern_type="presence",
                description=(
                    f"When {person} {action_word}, {device} turns {d_state} "
                    f"({count} times)"
                ),
                entity_ids=[person, device],
                confidence=confidence,
                occurrences=count,
                details={"trigger_person": person, "trigger_state": p_state,
                         "action_entity": device, "action_state": d_state},
            ))

        return patterns

    def _dominant_person(self, conn: sqlite3.Connection, table: str, *,
                         hour: int, entity: str | None = None,
                         state: str | None = None,
                         text: str | None = None) -> Optional[str]:
        """
        If one known person accounts for most of a pattern's occurrences,
        return them; else None, meaning the pattern stays household-wide.
        `table` is "state_changes" (match on entity+state+hour) or
        "commands" (match on text+hour). Defensive: an unmigrated DB
        missing the `person` column just falls back to household (None).
        """
        # v6.77.0: weight each event by how CONFIDENT the attribution was, so a
        # room-scoped "probably Eliana (0.62)" contributes proportionally instead
        # of being thrown away. Commands keep full weight — the conversation path
        # runs the full identity resolver, so those attributions are strong.
        try:
            if table == "state_changes":
                rows = conn.execute("""
                    SELECT person, COUNT(*) as cnt,
                           SUM(COALESCE(NULLIF(person_confidence, 0), 0.5)) as wt
                    FROM state_changes
                    WHERE entity_id = ? AND new_state = ? AND hour = ?
                        AND timestamp > datetime('now', '-30 days')
                    GROUP BY person ORDER BY wt DESC
                """, (entity, state, hour)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT person, COUNT(*) as cnt, COUNT(*) * 1.0 as wt
                    FROM commands
                    WHERE text = ? AND hour = ?
                        AND timestamp > datetime('now', '-30 days')
                    GROUP BY person ORDER BY wt DESC
                """, (text, hour)).fetchall()
        except Exception:
            # older DB without the confidence column — fall back to raw counts
            try:
                if table == "state_changes":
                    rows = conn.execute("""
                        SELECT person, COUNT(*) as cnt, COUNT(*) * 1.0 as wt
                        FROM state_changes
                        WHERE entity_id = ? AND new_state = ? AND hour = ?
                            AND timestamp > datetime('now', '-30 days')
                        GROUP BY person ORDER BY wt DESC
                    """, (entity, state, hour)).fetchall()
                else:
                    return None
            except Exception:
                return None

        if not rows:
            return None
        # Ignore the unknown bucket rather than aborting on it: previously a
        # dominant 'unknown' killed the whole pattern, so multi-occupant houses
        # (where sole-occupancy rarely holds) never produced per-person routines.
        named = [r for r in rows if r["person"] and r["person"] != "unknown"]
        if not named:
            return None
        total = sum(float(r["wt"] or 0.0) for r in named)
        top = named[0]
        top_wt = float(top["wt"] or 0.0)
        if total <= 0:
            return None
        if (top_wt / total >= PERSON_DOMINANCE_RATIO
                and top["cnt"] >= MIN_OCCURRENCES):
            return top["person"]
        return None

    def _entity_label(self, entity_id: str) -> str:
        """Readable label from an entity_id (no friendly name available here)."""
        name = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        return name.replace("_", " ").strip()

    def _fact_for(self, pattern: "DetectedPattern"):
        """
        Map a detected pattern to an observed knowledge fact, or None if it's not
        the kind of thing worth stating as butler-knowledge. Returns
        (subject, kind, key, value). Deterministic so re-analysis upserts in place.
        """
        if pattern.pattern_type == "time_routine" and pattern.entity_ids:
            label = self._entity_label(pattern.entity_ids[0])
            state = str(pattern.details.get("state", "")).strip()
            hour = pattern.details.get("hour")
            if hour is None or not label:
                return None
            when = f"around {hour:02d}:00 most days"
            subject = self._subject_for_pattern(pattern)
            if state in ("on", "off"):
                return (subject, "fact", f"{label} turns {state}", when)
            return (subject, "fact", f"{label} set to {state}", when)
        if pattern.pattern_type == "repeated_command":
            text = str(pattern.details.get("command", "")).strip()
            hour = pattern.details.get("hour")
            if not text or hour is None:
                return None
            subject = self._subject_for_pattern(pattern)
            return (subject, "fact", f'asks "{text[:60]}"',
                    f"usually around {hour:02d}:00")
        return None

    def _subject_for_pattern(self, pattern: "DetectedPattern") -> str:
        """
        The knowledge subject to attribute a promoted fact to: a specific
        person's subject when the pattern is confidently theirs alone
        (v6.41.0), else "household" — identical to pre-6.41 behavior.
        """
        person = pattern.details.get("person")
        if not person:
            return "household"
        try:
            from . import identity
            return identity.normalize(person)
        except Exception:
            return "household"

    def _promote_to_knowledge(self, patterns: list) -> int:
        """Write the most reliable routines/commands as observed facts. SYNC."""
        try:
            from . import knowledge
        except Exception:
            return 0
        written = 0
        for p in patterns:
            if p.confidence < KNOWLEDGE_FACT_CONFIDENCE:
                continue
            mapped = self._fact_for(p)
            if not mapped:
                continue
            subject, kind, key, value = mapped
            try:
                stored = knowledge.remember(
                    key, value, subject=subject, kind=kind, source="observed",
                    confidence=round(float(p.confidence), 3), salience=0.8,
                    respect_stated=True,
                )
                if stored:
                    written += 1
            except Exception as exc:
                _LOGGER.debug("knowledge promote failed for %r: %s", key, exc)
        return written

    def _store_person_pattern(self, pattern: DetectedPattern) -> bool:
        """Upsert a person-owned routine into the person_patterns store (now
        owned by the person_patterns module). Deterministic key
        (person, pattern_type, description) so re-analysis refreshes in place."""
        person = pattern.details.get("person")
        if not person:
            return False
        from . import person_patterns
        return person_patterns.store(
            person, pattern.pattern_type, pattern.description,
            data=pattern.details, confidence=pattern.confidence,
            occurrences=pattern.occurrences, db_path=self._db,
        )

    def get_person_patterns(self, person: Optional[str] = None) -> list[dict]:
        """Read stored per-person routines (person_patterns module), optionally
        filtered to one person (matched on the already-normalized id)."""
        from . import person_patterns
        return person_patterns.read(person, db_path=self._db)

    def _purge_non_actionable_suggestions(self) -> int:
        """Delete pending suggestions that can never become an automation — a
        trigger with no device action to take. Clears rows stored before the
        actionability filter existed (and any a generator change later renders
        non-installable). Returns how many were removed. Never raises."""
        try:
            conn = sqlite3.connect(self._db)
        except Exception:
            return 0
        removed = 0
        try:
            rows = conn.execute(
                "SELECT id, automation_yaml FROM suggestions "
                "WHERE status = 'pending'"
            ).fetchall()
            for rid, yml in rows:
                if not normalize_suggestion_automation(yml or "").get("installable"):
                    conn.execute("DELETE FROM suggestions WHERE id = ?", (rid,))
                    removed += 1
            if removed:
                conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
        return removed

    def _store_suggestion(self, pattern: DetectedPattern) -> bool:
        """Store a pattern as a suggestion in the DB. Returns True if new.

        A suggestion is only worth surfacing if it becomes a real automation —
        a trigger AND a device action to take. A pattern whose "action" has no
        service to call (two sensors or cameras that merely change state around
        the same time) is a correlation, not an automation, so it is never
        stored for review."""
        auto_yaml = self._generate_automation(pattern)
        if not normalize_suggestion_automation(auto_yaml).get("installable"):
            return False
        try:
            conn = sqlite3.connect(self._db)
            # Check if similar suggestion already exists
            existing = conn.execute(
                "SELECT id FROM suggestions WHERE description = ?",
                (pattern.description,)
            ).fetchone()
            if existing:
                # Update occurrence count and confidence
                conn.execute(
                    "UPDATE suggestions SET confidence = ?, pattern_count = ? WHERE id = ?",
                    (pattern.confidence, pattern.occurrences, existing[0]),
                )
                conn.commit()
                conn.close()
                return False

            _cur = conn.execute(
                "INSERT INTO suggestions (created, description, automation_yaml, "
                "confidence, pattern_count, pattern_type, entity_ids, details, "
                "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (datetime.now().isoformat(), pattern.description,
                 auto_yaml, pattern.confidence, pattern.occurrences,
                 pattern.pattern_type,
                 json.dumps(pattern.entity_ids or []),
                 json.dumps(pattern.details or {})),
            )
            _new_sid = _cur.lastrowid
            conn.commit()
            conn.close()
            try:
                from . import decision_record
                decision_record.record(
                    "suggestion",
                    observation={"pattern_type": pattern.pattern_type,
                                 "entities": pattern.entity_ids or [],
                                 "occurrences": pattern.occurrences},
                    interpretation={"suggested": pattern.description},
                    decision="propose automation",
                    reason="recurring observed behavior",
                    confidence=pattern.confidence,
                    ref="suggestion:%d" % _new_sid,
                )
            except Exception:
                pass
            return True
        except Exception as exc:
            _LOGGER.debug("Store suggestion error: %s", exc)
            return False

    # ── Home Assistant trigger / condition taxonomy (roadmap reference) ──────
    # The long-term goal is for JARVIS to learn and emit the FULL range of HA
    # triggers and conditions, not just the handful below. Keep this list current
    # as coverage grows so future work knows the target.
    #
    # HA TRIGGER platforms:
    #   state ✓(sequence, presence) · time ✓(time_routine) ·
    #   numeric_state ✓(numeric_trigger) · zone ✓(sequence departure/arrival) ·
    #   event ✓(button/remote presses via event.* entities) ·
    #   time_pattern · sun · geo_location · template · homeassistant ·
    #   mqtt · webhook · device · calendar · tag · conversation ·
    #   persistent_notification
    #   (device: the device_id/type/subtype form is integration-specific and not
    #    emitted; modern buttons/remotes surface as event.* entities, which is the
    #    general path used here — a state trigger + a template on event_type.)
    # HA CONDITION types:
    #   time ✓(sequence) · sun ✓(sequence) · numeric_state ✓(sequence) ·
    #   state ✓(time_routine) · template ✓(event-press match) ·
    #   zone · trigger · device · and · or · not
    #
    # Emitted today: TRIGGERS {state, time, numeric_state, zone, event};
    #   CONDITIONS {time, sun, numeric_state, state, template} (ANDed as a list).
    # Backlog (no longer the active list — revisit as desired):
    #   • calendar / time_pattern — schedule-driven routines.
    #   • state (presence) condition on sequences — the "away" direction only.
    #   • numeric_state condition on time routines ("at 7pm, only if below 65").
    # Each is its own focused build: mine the discriminator from history, attach
    # only when it consistently holds, keep the HA dict self-describing so
    # _generate_automation and normalize pass it through unchanged.
    def _generate_automation(self, pattern: DetectedPattern) -> str:
        """Generate HA automation YAML from a detected pattern."""
        p = pattern
        d = p.details

        if p.pattern_type == "time_routine" and d.get("state") in ("on", "off"):
            # Only a controllable target is a real automation. A read-only entity
            # (binary_sensor, device_tracker, sensor, …) that merely CHANGES to
            # on/off at a regular time has no device action to take — emitting
            # `binary_sensor.turn_on` would be nonsense, so mark it advisory and
            # let the store's actionability gate drop it.
            svc = service_for(p.entity_ids[0], d["state"])
            if not svc:
                return json.dumps({
                    "type": "manual_review",
                    "note": (f"Consider automating: {p.entity_ids[0]} → "
                             f"{d['state']} around {d['hour']:02d}:00"),
                }, indent=2)
            auto = {
                "alias": f"JARVIS Learned: {p.entity_ids[0]} {d['state']} at {d['hour']:02d}:00",
                "trigger": {"platform": "time", "at": f"{d['hour']:02d}:00:00"},
                "action": svc,
            }
            cond = d.get("condition")
            conds = [c for c in (cond if isinstance(cond, list) else [cond])
                     if isinstance(c, dict) and c.get("condition")]
            if conds:
                auto["condition"] = conds
            return json.dumps(auto, indent=2)

        if p.pattern_type == "sequence":
            trigger = d.get("trigger", {})
            action = d.get("action", {})
            svc = service_for(action.get("entity", ""), action.get("state", ""))
            if not svc:
                return json.dumps({
                    "note": f"Consider automating: {action.get('entity','?')} → "
                            f"{action.get('state','?')} after "
                            f"{trigger.get('entity','?')} "
                            f"{trigger.get('state','?')}",
                    "type": "manual_review",
                }, indent=2)
            # Use the measured typical lag (rounded to 5s); omit a delay under 15s
            # so near-immediate reactions don't get an awkward tiny wait.
            lag = int(d.get("delay_seconds", 60) or 0)
            seq_action: list = []
            if lag >= 15:
                lag = int(round(lag / 5.0) * 5)
                seq_action.append({"delay": f"00:{lag // 60:02d}:{lag % 60:02d}"})
            seq_action.append(svc)
            trig = _trigger_for(trigger["entity"], trigger["state"])
            extra = _trigger_extra_conditions(trigger["entity"], trigger["state"])
            # Phase 2: "…and keep it on until the room empties". Turn the action
            # on, wait for the area's occupancy sensor to clear (for a small
            # settle window so a brief exit doesn't kill it), then turn it off.
            # `mode: restart` re-arms the hold if the trigger fires again.
            until = d.get("until_unoccupied") if isinstance(d.get("until_unoccupied"), dict) else None
            mode = "single"
            if until:
                off_svc = service_for(action["entity"], "off")
                if off_svc:
                    seq_action.append({
                        "wait_for_trigger": [{
                            "platform": "state",
                            "entity_id": until["entity_id"], "to": "off",
                            "for": _secs_to_hms(until.get("for_seconds", 0)),
                        }],
                    })
                    seq_action.append(off_svc)
                    mode = "restart"
            if trig.get("platform") == "zone":
                verb = "leaves" if trig["event"] == "leave" else "arrives"
                alias = f"JARVIS Learned: {action['entity']} when {trigger['entity']} {verb} home"
            elif (trigger["entity"].split(".")[0] if "." in trigger["entity"]
                    else "") == "event":
                alias = f"JARVIS Learned: {action['entity']} on {trigger['entity']} press"
            elif until:
                alias = f"JARVIS Learned: {action['entity']} on {trigger['entity']} until unoccupied"
            else:
                alias = f"JARVIS Learned: {action['entity']} after {trigger['entity']}"
            auto = {
                "alias": alias,
                "trigger": trig,
                "action": seq_action,
            }
            if mode != "single":
                auto["mode"] = mode
            cond = d.get("condition")
            conds = [c for c in (cond if isinstance(cond, list) else [cond])
                     if isinstance(c, dict) and c.get("condition")] + extra
            if conds:
                auto["condition"] = conds
            return json.dumps(auto, indent=2)

        if p.pattern_type == "numeric_trigger":
            action = d.get("action", {})
            svc = service_for(action.get("entity", ""), action.get("state", ""))
            if not svc:
                return json.dumps({
                    "type": "manual_review",
                    "note": (f"Consider: {action.get('entity','?')} when "
                             f"{d.get('trigger_sensor','?')} {d.get('op','?')} "
                             f"{d.get('threshold','?')}"),
                }, indent=2)
            trig = {"platform": "numeric_state",
                    "entity_id": d["trigger_sensor"], d["op"]: d["threshold"]}
            auto = {
                "alias": (f"JARVIS Learned: {action['entity']} when "
                          f"{d['trigger_sensor']} {d['op']} {d['threshold']:g}"),
                "trigger": trig,
                "action": [svc],
            }
            cond = d.get("condition")
            conds = [c for c in (cond if isinstance(cond, list) else [cond])
                     if isinstance(c, dict) and c.get("condition")]
            if conds:
                auto["condition"] = conds
            return json.dumps(auto, indent=2)

        if p.pattern_type == "confirm_sequence":
            # Phase 3 (safety-sensitive): trigger -> open a cover -> WAIT for a
            # confirmation sensor -> close it. Only emitted for cover targets, and
            # always through the review/approval UI. The wait has a timeout with
            # continue_on_timeout=False so a missed confirmation leaves the cover
            # open rather than closing on an unconfirmed step.
            trg = d.get("trigger", {})
            opn = d.get("open", {})
            close = d.get("close", {})
            confirm = d.get("confirm", {})
            open_svc = service_for(opn.get("entity", ""), opn.get("state", ""))
            close_svc = service_for(close.get("entity", ""), close.get("state", ""))
            if not (open_svc and close_svc and confirm.get("entity")):
                return json.dumps({
                    "type": "manual_review",
                    "note": (f"Consider: after {trg.get('entity','?')}, open "
                             f"{opn.get('entity','?')}, and close it once "
                             f"{confirm.get('entity','?')} confirms"),
                }, indent=2)
            trig = _trigger_for(trg["entity"], trg["state"])
            timeout = _secs_to_hms(min(600, int(d.get("confirm_timeout", 120) or 120)))
            return json.dumps({
                "alias": (f"JARVIS Learned (review): close {close['entity']} after "
                          f"{confirm['entity']} confirms"),
                "description": ("SAFETY-SENSITIVE: closes a cover automatically. "
                                "Review carefully before enabling."),
                "trigger": trig,
                "action": [
                    open_svc,
                    {"wait_for_trigger": [{
                        "platform": "state",
                        "entity_id": confirm["entity"],
                        "to": confirm.get("state", "on")}],
                     "timeout": timeout, "continue_on_timeout": False},
                    close_svc,
                ],
                "mode": "restart",
            }, indent=2)

        if p.pattern_type == "repeated_command":
            return json.dumps({
                "note": f"Consider automating: '{d.get('command', '')}' at {d.get('hour', 0):02d}:00",
                "type": "manual_review",
            }, indent=2)

        if p.pattern_type == "presence":
            svc = service_for(d.get("action_entity", ""), d.get("action_state", ""))
            if not svc:
                return json.dumps({
                    "note": f"Consider automating: {d.get('action_entity','?')} → "
                            f"{d.get('action_state','?')} when "
                            f"{d.get('trigger_person','?')} "
                            f"{d.get('trigger_state','?')}",
                    "type": "manual_review",
                }, indent=2)
            return json.dumps({
                "alias": f"JARVIS Learned: {d['action_entity']} when {d['trigger_person']} {d['trigger_state']}",
                "trigger": {
                    "platform": "state",
                    "entity_id": d["trigger_person"],
                    "to": d["trigger_state"],
                },
                "action": svc,
            }, indent=2)

        return json.dumps({"note": p.description}, indent=2)

    def get_pending_suggestions(self) -> list[dict]:
        """Get all pending suggestions for the user to review."""
        conn = self._connect()
        if not conn:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM suggestions WHERE status = 'pending' "
                "ORDER BY confidence DESC LIMIT 20"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def get_suggestion(self, suggestion_id: int) -> Optional[dict]:
        """One suggestion row by id, or None."""
        conn = self._connect()
        if not conn:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
        finally:
            conn.close()

    def mark_installed(self, suggestion_id: int, automation_id: str) -> None:
        """Record that an approved suggestion became a live automation."""
        conn = self._connect()
        if not conn:
            return
        try:
            # widen status vocabulary without a migration: 'installed' is just
            # another string the UI can render distinctly from 'approved'.
            conn.execute(
                "UPDATE suggestions SET status = 'installed', "
                "approved_at = ? WHERE id = ?",
                (datetime.now().isoformat(), suggestion_id),
            )
            try:  # Decision Record outcome (v7.40.0): an installed suggestion was useful
                from . import decision_record
                decision_record.set_outcome_by_ref(
                    "suggestion:%d" % suggestion_id, "good", "installed")
            except Exception:
                pass
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def approve_suggestion(self, suggestion_id: int) -> bool:
        """Mark a suggestion as approved."""
        conn = self._connect()
        if not conn:
            return False
        try:
            conn.execute(
                "UPDATE suggestions SET status = 'approved', "
                "approved_at = ? WHERE id = ?",
                (datetime.now().isoformat(), suggestion_id),
            )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def dismiss_suggestion(self, suggestion_id: int) -> bool:
        """Mark a suggestion as dismissed."""
        conn = self._connect()
        if not conn:
            return False
        try:
            conn.execute(
                "UPDATE suggestions SET status = 'dismissed', "
                "dismissed_at = ? WHERE id = ?",
                (datetime.now().isoformat(), suggestion_id),
            )
            conn.commit()
            try:  # Decision Record outcome (v7.40.0): a dismissed suggestion was unnecessary
                from . import decision_record
                decision_record.set_outcome_by_ref(
                    "suggestion:%d" % suggestion_id, "unnecessary", "dismiss_suggestion")
            except Exception:
                pass
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Return analysis statistics."""
        conn = self._connect()
        if not conn:
            return {"available": False}
        try:
            stats = {
                "available": True,
                "state_changes": conn.execute(
                    "SELECT COUNT(*) FROM state_changes").fetchone()[0],
                "commands": conn.execute(
                    "SELECT COUNT(*) FROM commands").fetchone()[0],
                "pending_suggestions": conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='pending'"
                ).fetchone()[0],
                "approved": conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='approved'"
                ).fetchone()[0],
                "dismissed": conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='dismissed'"
                ).fetchone()[0],
            }
            oldest = conn.execute(
                "SELECT MIN(timestamp) FROM state_changes"
            ).fetchone()[0]
            if oldest:
                stats["days_of_data"] = (
                    datetime.now() - datetime.fromisoformat(oldest)
                ).days
                stats["ready_for_analysis"] = stats["days_of_data"] >= MIN_DAYS
            else:
                stats["days_of_data"] = 0
                stats["ready_for_analysis"] = False
            return stats
        except Exception:
            return {"available": False}
        finally:
            conn.close()


# ── Singleton ───────────────────────────────────────────────────────────────

_ANALYZER = PatternAnalyzer()


def get_analyzer() -> PatternAnalyzer:
    return _ANALYZER


async def install_approved_suggestion(hass, suggestion_id: int) -> dict:
    """
    Close the pattern-engine loop (v6.52.0): approve a suggestion AND actually
    install its automation into Home Assistant, instead of only flagging it
    approved. Returns a dict the caller relays:

        {"ok": True, "installed": True, "automation_id": "...", "alias": "..."}
        {"ok": True, "installed": False, "reason": "..."}   # approved, advisory
        {"ok": False, "error": "..."}                        # not found / failed

    Advisory suggestions (repeated-command notes with no concrete trigger) are
    still marked approved — the user acknowledged them — but nothing is written
    to HA, and the reason says so plainly.
    """
    analyzer = get_analyzer()
    try:
        sug = await hass.async_add_executor_job(analyzer.get_suggestion, suggestion_id)
        if not sug:
            return {"ok": False, "error": f"suggestion #{suggestion_id} not found"}

        # Always record the user's approval first.
        await hass.async_add_executor_job(analyzer.approve_suggestion, suggestion_id)

        norm = normalize_suggestion_automation(sug.get("automation_yaml", ""))
        if not norm.get("installable"):
            return {"ok": True, "installed": False,
                    "reason": norm.get("reason", "not installable"),
                    "suggestion_id": suggestion_id}

        from .automation_creator import create_automation
        result = await create_automation(
            hass,
            alias=norm["alias"],
            description=sug.get("description", ""),
            trigger=norm["trigger"],
            condition=norm.get("condition"),
            action=norm["action"],
            mode=norm.get("mode", "single"),
        )
        if result.get("success"):
            await hass.async_add_executor_job(
                analyzer.mark_installed, suggestion_id, result["automation_id"])
            try:
                from .websocket import jarvis_log
                jarvis_log("LEARN", f"Installed learned automation "
                                    f"'{result['alias']}' from suggestion "
                                    f"#{suggestion_id}")
            except Exception:
                pass
            return {"ok": True, "installed": True,
                    "automation_id": result["automation_id"],
                    "alias": result["alias"], "suggestion_id": suggestion_id}
        return {"ok": True, "installed": False,
                "reason": f"automation write failed: {result.get('error')}",
                "suggestion_id": suggestion_id}
    except Exception as exc:
        _LOGGER.exception("install_approved_suggestion failed: %s", exc)
        return {"ok": False, "error": str(exc)}

