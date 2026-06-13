"""
JARVIS Local Mind — the offline reasoning brain.

When the cloud is unreachable (connectivity breaker open, or a cloud call
fails with nothing cached), decisions used to fall to a two-line urgency
switch. This module replaces that with an engine that approximates the
*procedure* a frontier model follows when judging a household event:

  1. Self-awareness        — am I repeating myself? Is this sensor flapping?
  2. Historical grounding  — patterns.db: how often does THIS entity reach
                             THIS state at THIS hour? (novel / unusual-hour /
                             occasional / routine)
  3. Case-based memory     — reasoning_cache: what did the cloud decide for
                             materially similar events in the past?
  4. Situational judgment  — urgency × novelty × presence × security posture,
                             with escalation when entry points open while the
                             house is empty
  5. Persona verbalization — JARVIS-voiced phrasing with context clauses and
                             deterministic variation, never robotic repeats

Every decision carries a reason chain, so the Logs tab shows *why* the local
mind spoke or stayed silent — the same transparency the cloud path has.

Honest scope: this replicates the decision procedure and, for the event
taxonomy a household actually produces, lands where the cloud would land the
great majority of the time. It does not replicate open-ended understanding of
genuinely novel situations — that remains the cloud's job, and soon the GPU
server's. The module is a dependency-free leaf (sqlite3 + stdlib only).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from collections import deque
from typing import Optional

_LOGGER = logging.getLogger(__name__)

DB_PATH = "/config/jarvis/patterns.db"

# History lookups are cached briefly so a chatty entity doesn't hammer sqlite.
_HIST_TTL = 600.0          # seconds
_DAYS_TTL = 3600.0
_hist_cache: dict[tuple, tuple[float, dict]] = {}
_days_cache: tuple[float, float] = (0.0, 0.0)   # (expires, days)

# Flap detection: recent event timestamps per entity.
_FLAP_WINDOW = 300.0       # seconds
_FLAP_COUNT = 3            # events within window → flapping
_recent_events: dict[str, deque] = {}

# Local-mind decision counters (surfaced via stats()).
_stats = {"decisions": 0, "spoke": 0, "silent": 0}


# ── 2. Historical grounding ──────────────────────────────────────────────────

def _connect() -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _data_days() -> float:
    """How many days of state history exist (cached ~1h)."""
    global _days_cache
    now = time.monotonic()
    if _days_cache[0] > now:
        return _days_cache[1]
    days = 0.0
    conn = _connect()
    if conn:
        try:
            row = conn.execute(
                "SELECT (julianday(MAX(timestamp)) - julianday(MIN(timestamp))) AS d "
                "FROM state_changes"
            ).fetchone()
            if row and row["d"]:
                days = float(row["d"])
        except Exception:
            days = 0.0
        finally:
            conn.close()
    _days_cache = (now + _DAYS_TTL, days)
    return days


def history_profile(entity_id: str, new_state: str, hour: int) -> dict:
    """
    Ground an event in observed history. Synchronous (call in executor).

    Returns {"days": float, "at_hour": int, "total": int, "grade": str}
    where grade ∈ unknown | novel | unusual_hour | occasional | common | routine.
    """
    key = (entity_id, str(new_state), int(hour))
    now = time.monotonic()
    hit = _hist_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]

    days = _data_days()
    out = {"days": round(days, 1), "at_hour": 0, "total": 0, "grade": "unknown"}
    if days >= 3:
        conn = _connect()
        if conn:
            try:
                hours = ((hour - 1) % 24, hour % 24, (hour + 1) % 24)
                row = conn.execute(
                    "SELECT "
                    " SUM(CASE WHEN hour IN (?,?,?) THEN 1 ELSE 0 END) AS at_hour,"
                    " COUNT(*) AS total "
                    "FROM state_changes WHERE entity_id = ? AND new_state = ?",
                    (*hours, entity_id, str(new_state)),
                ).fetchone()
                if row:
                    out["at_hour"] = int(row["at_hour"] or 0)
                    out["total"] = int(row["total"] or 0)
                rate = out["at_hour"] / days
                if out["total"] == 0:
                    out["grade"] = "novel"
                elif out["at_hour"] == 0:
                    out["grade"] = "unusual_hour"
                elif rate >= 0.5:
                    out["grade"] = "routine"
                elif rate >= 0.15:
                    out["grade"] = "common"
                else:
                    out["grade"] = "occasional"
            except Exception as exc:
                _LOGGER.debug("local_mind history query: %s", exc)
            finally:
                conn.close()

    _hist_cache[key] = (now + _HIST_TTL, out)
    if len(_hist_cache) > 800:
        for k in list(_hist_cache)[:200]:
            _hist_cache.pop(k, None)
    return out


# ── 1. Self-awareness ────────────────────────────────────────────────────────

def _note_event(entity_id: str) -> bool:
    """Record this event; return True if the entity is flapping."""
    dq = _recent_events.setdefault(entity_id, deque(maxlen=8))
    now = time.monotonic()
    dq.append(now)
    recent = [t for t in dq if now - t <= _FLAP_WINDOW]
    return len(recent) >= _FLAP_COUNT


def _is_duplicate(friendly_name: str, entity_id: str,
                  recent_announcements: list[str]) -> bool:
    """Have we already told the user about this device very recently?"""
    needles = set()
    if friendly_name:
        needles.add(friendly_name.lower())
    if entity_id and "." in entity_id:
        needles.add(entity_id.split(".", 1)[1].replace("_", " ").lower())
    if not needles:
        return False
    for ann in (recent_announcements or [])[-6:]:
        a = str(ann).lower()
        if any(n in a for n in needles):
            return True
    return False


# ── 4. Situational judgment ──────────────────────────────────────────────────

_SECURITY_CLASSES = {"door", "window", "garage_door", "lock", "opening", "motion"}
_SECURITY_DOMAINS = {"lock", "cover"}
_OPENING_STATES = {"on", "open", "opening", "unlocked", "detected", "true"}


def _security_relevant(domain: str, device_class: str, to_state: str,
                       entity_id: str) -> bool:
    s = str(to_state or "").lower()
    if s not in _OPENING_STATES:
        return False
    if (device_class or "").lower() in _SECURITY_CLASSES:
        return True
    if (domain or "").lower() in _SECURITY_DOMAINS:
        return True
    eid = (entity_id or "").lower()
    return any(k in eid for k in ("door", "window", "garage", "lock", "gate"))


def _case_prior(domain: str, device_class: str, category: str,
                anyone_home: bool) -> tuple[int, int]:
    """Case-based memory: tally past cloud decisions for similar events."""
    try:
        from . import reasoning_cache
        return reasoning_cache.similar(domain, device_class, category, anyone_home)
    except Exception:
        return (0, 0)


# ── 5. Persona verbalization ─────────────────────────────────────────────────

_STATE_PHRASES = {
    "on": "is on", "off": "is off", "open": "is open", "closed": "is closed",
    "opening": "is opening", "closing": "is closing", "unlocked": "is unlocked",
    "locked": "is locked", "detected": "has detected activity",
    "home": "has arrived home", "not_home": "has left",
    "unavailable": "has gone unresponsive", "unknown": "is in an unknown state",
}


def _state_phrase(to_state: str, device_class: str = "", entity_id: str = "") -> str:
    s = str(to_state or "").strip().lower()
    # Binary sensors report on/off even for doors and motion — phrase them the
    # way a person would.
    dc = (device_class or "").lower()
    eid = (entity_id or "").lower()
    opening_like = dc in ("door", "window", "garage_door", "opening") or any(
        k in eid for k in ("door", "window", "garage", "gate"))
    motion_like = dc in ("motion", "occupancy", "presence") or "motion" in eid or "occupancy" in eid
    safety_on = {
        "smoke": "is detecting smoke",
        "carbon_monoxide": "is detecting carbon monoxide",
        "gas": "is detecting gas",
        "moisture": "is detecting water",
        "heat": "is detecting excessive heat",
        "tamper": "has been tampered with",
        "vibration": "is detecting vibration",
        "sound": "is detecting sound",
        "problem": "is reporting a problem",
        "safety": "has raised an alert",
    }
    leak_like = dc in ("moisture",) or any(k in eid for k in ("leak", "flood", "water_"))
    if s == "on":
        if dc in safety_on:
            return safety_on[dc]
        if leak_like:
            return "is detecting water"
        if opening_like:
            return "is open"
        if motion_like:
            return "has detected motion"
    elif s == "off":
        if dc in safety_on or leak_like:
            return "has cleared"
        if opening_like:
            return "is closed"
        if motion_like:
            return "has gone quiet"
    # Numeric readings: batteries get percentage phrasing, sensors get "reads".
    try:
        v = float(s)
        if dc == "battery" or "battery" in eid:
            return f"battery is at {int(v) if v == int(v) else v}%"
        if eid.startswith("sensor."):
            return f"reads {to_state}"
    except (TypeError, ValueError):
        pass
    return _STATE_PHRASES.get(s, f"is now {to_state}" if to_state else "needs attention")


def _pick(variants: list[str], entity_id: str, hour: int) -> str:
    h = int(hashlib.md5(f"{entity_id}|{hour}".encode()).hexdigest()[:6], 16)
    return variants[h % len(variants)]


def _compose(honorific: str, friendly_name: str, entity_id: str, to_state: str,
             hour: int, *, novelty: str, away: bool, escalated: bool,
             device_class: str = "") -> str:
    name = friendly_name or (entity_id.split(".", 1)[-1].replace("_", " ").title()
                             if entity_id else "A device")
    core = f"{name} {_state_phrase(to_state, device_class, entity_id)}"
    clauses = []
    if away:
        clauses.append("while no one is home")
    if novelty == "novel":
        clauses.append(_pick(["the first time I've observed this",
                              "I haven't seen this before",
                              "a first in my records"], entity_id, hour))
    elif novelty == "unusual_hour":
        if 0 <= hour <= 5:
            clauses.append("which is unusual at this hour of the night")
        else:
            clauses.append("which is out of pattern for this time of day")
    tail = (" — " + ", ".join(clauses)) if clauses else ""
    openers_urgent = [f"{honorific.title()}, your attention please:",
                      f"{honorific.title()}, you should know:"]
    openers_info = [f"{honorific.title()},", f"For your awareness, {honorific} —"]
    opener = _pick(openers_urgent if escalated else openers_info, entity_id, hour + 7)
    return f"{opener} {core}{tail}."


def compose_announcement(honorific: str, friendly_name: str, entity_id: str = "",
                         to_state: str = "", device_class: str = "", *,
                         hour: Optional[int] = None, novelty: str = "unknown",
                         away: bool = False, escalated: bool = False) -> str:
    """
    Public verbalizer — the single voice for ALL local speech. The learned-cache
    replay, the legacy templates, and the last-ditch fallback all compose through
    here so local announcements sound like one mind, not three vintages of
    template. Device-aware, context-claused, hash-varied.
    """
    if hour is None:
        from datetime import datetime
        hour = datetime.now().hour
    return _compose(honorific, friendly_name, entity_id, to_state, int(hour),
                    novelty=novelty, away=away, escalated=escalated,
                    device_class=device_class)


# ── The assessment ───────────────────────────────────────────────────────────

def assess_core(*, honorific: str, entity_id: str, domain: str,
                device_class: str, category: str, from_state: str,
                to_state: str, friendly_name: str, urgency: str,
                anyone_home: bool, recent_announcements: list[str],
                hour: int, history: dict,
                prior: tuple[int, int]) -> dict:
    """Pure decision core (no I/O) — unit-testable."""
    urgency = (urgency or "medium").lower()
    grade = history.get("grade", "unknown")
    away = not anyone_home
    flapping = _note_event(entity_id or friendly_name or "?")
    duplicate = _is_duplicate(friendly_name, entity_id, recent_announcements)
    security = _security_relevant(domain, device_class, to_state, entity_id)
    speak_n, silent_n = prior

    def decision(speak: bool, out_urgency: str, why: str,
                 escalated: bool = False) -> dict:
        _stats["decisions"] += 1
        _stats["spoke" if speak else "silent"] += 1
        d = {"speak": speak, "urgency": out_urgency,
             "reason": f"local mind: {why}"}
        if speak:
            d["message"] = _compose(
                honorific, friendly_name, entity_id, to_state, hour,
                novelty=grade, away=away, escalated=escalated,
                device_class=device_class)
        return d

    # Critical always surfaces — even repetition is worth hearing at critical.
    if urgency == "critical":
        return decision(True, "critical", "critical urgency — always voiced",
                        escalated=True)

    # Self-awareness gates.
    if duplicate:
        return decision(False, urgency, "already announced this device recently")
    if flapping:
        return decision(False, urgency,
                        f"{friendly_name or entity_id} is flapping "
                        f"(≥{_FLAP_COUNT} events in {int(_FLAP_WINDOW/60)}m) — suppressed")

    # Security escalation: an entry point opening while the house is empty
    # outranks the classifier's 'medium'.
    if security and away and urgency in ("medium", "high"):
        return decision(True, "high",
                        f"entry point active while away ({grade}) — escalated",
                        escalated=True)

    if urgency == "high":
        return decision(True, "high", f"high urgency ({grade})", escalated=True)

    if urgency == "medium":
        # Case-based memory first — actual past cloud judgments outrank heuristics.
        if silent_n >= 2 and speak_n == 0:
            return decision(False, "medium",
                            f"{silent_n} similar past events judged routine (case memory)")
        if speak_n >= 2 and silent_n == 0:
            return decision(True, "medium",
                            f"{speak_n} similar past events voiced (case memory)")
        # Historical grounding.
        if grade == "novel":
            return decision(True, "medium", "novel event — never observed before")
        if grade == "unusual_hour":
            return decision(True, "medium",
                            f"out of hourly pattern (seen {history.get('total', 0)}× "
                            f"overall, never near this hour)")
        if grade in ("routine", "common"):
            return decision(False, "medium",
                            f"{grade} at this hour "
                            f"(~{history.get('at_hour', 0)}× in {history.get('days', 0)}d)")
        # occasional / unknown: quiet at home, surfaced when away (routes to push).
        if away:
            return decision(True, "medium", f"{grade} while away — surfaced")
        return decision(False, "medium", f"{grade} while home — not worth voicing")

    return decision(False, "low", "low urgency — silent")


async def assess(hass, *, honorific: str, entity_id: str = "", domain: str = "",
                 device_class: str = "", category: str = "", from_state: str = "",
                 to_state: str = "", friendly_name: str = "",
                 urgency: str = "medium", anyone_home: bool = False,
                 recent_announcements: Optional[list[str]] = None) -> dict:
    """Full assessment: history lookup in executor, then the pure core."""
    from datetime import datetime
    hour = datetime.now().hour
    try:
        history = await hass.async_add_executor_job(
            history_profile, entity_id, to_state, hour)
    except Exception:
        history = {"grade": "unknown", "days": 0, "at_hour": 0, "total": 0}
    prior = _case_prior(domain, device_class, category, anyone_home)
    dec = assess_core(
        honorific=honorific, entity_id=entity_id, domain=domain,
        device_class=device_class, category=category, from_state=from_state,
        to_state=to_state, friendly_name=friendly_name, urgency=urgency,
        anyone_home=anyone_home,
        recent_announcements=recent_announcements or [],
        hour=hour, history=history, prior=prior)
    try:
        from .websocket import jarvis_log
        jarvis_log("LOCAL", f"{entity_id or friendly_name}: "
                            f"{'SPEAK' if dec['speak'] else 'silent'} — "
                            f"{dec['reason'].replace('local mind: ', '')}")
    except Exception:
        pass
    return dec


def stats() -> dict:
    return dict(_stats)
