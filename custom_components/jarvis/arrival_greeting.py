"""Arrival greeting — greet a resident by name when they come through a door the
household chose, within chosen hours. Opt-in, default OFF (v9.84.0).

"Who arrived" is resolved two ways, either sufficing:
  1. a fresh, confident face recognition of a flagged resident
     (``recognition.resident_present`` — works when the door has a camera), else
  2. a resident whose ``person``/``device_tracker`` just flipped to ``home``.
When no resident can be named, JARVIS stays SILENT — it never greets a stranger
or guesses who it is. A per-person cooldown stops a busy door from repeating the
greeting.

"Welcome home" must mean they actually *came* home: a resident is only greeted
when they have a recorded **departure** (their ``person``/``device_tracker`` went
``not_home``/away) that hasn't been consumed yet — so someone who was home all
along is never greeted when a watched door merely opens. The departure is cleared
when they're greeted, so the next greeting needs a fresh leave→return. This gates
*in combination with* the per-person cooldown. The ledger is process-local, so a
restart requires one more leave→return before the first greeting (conservative —
better to miss one than to greet someone who never left).

Everything here is pure and defensive (never raises); the Home Assistant wiring —
the door state-change listener and the spoken announcement — lives in
``__init__.py`` alongside the other event listeners.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from typing import Optional

DEFAULT_COOLDOWN_MIN = 30

# resident name (lower-cased) -> monotonic seconds of the last greeting, so a
# door that opens repeatedly (or several watched doors) can't re-greet the same
# person within the cooldown. Process-local; cleared on reload.
_last_greeted: dict[str, float] = {}

# resident name (lower-cased) -> monotonic seconds when they were last seen to
# LEAVE the property (person/device_tracker went not_home/away). A greeting is
# only allowed for someone with an unconsumed departure here, so "welcome home"
# never fires for a resident who was home all along. Process-local; cleared on
# reload. Fed by the presence listener in __init__.py; consumed on greeting.
_departed: dict[str, float] = {}


def _parse_hhmm(s) -> Optional[int]:
    """'HH:MM' -> minutes since midnight, or None when blank/invalid."""
    try:
        s = str(s or "").strip()
        if not s:
            return None
        hh, mm = s.split(":", 1)
        h, m = int(hh), int(mm)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except Exception:
        pass
    return None


def within_hours(now_minutes: int, start, end) -> bool:
    """Is ``now_minutes`` (minutes since midnight) within the [start, end] window?
    A blank bound is open on that side (blank/blank = always). A window that wraps
    past midnight (start > end, e.g. 22:00–06:00) is handled. Never raises."""
    s = _parse_hhmm(start)
    e = _parse_hhmm(end)
    if s is None and e is None:
        return True
    if s is None:
        return now_minutes <= e
    if e is None:
        return now_minutes >= s
    if s <= e:
        return s <= now_minutes <= e
    return now_minutes >= s or now_minutes <= e   # wraps midnight


def _is_resident(name: str) -> bool:
    try:
        from . import face_roster
        return bool(name) and bool(face_roster.is_resident(name))
    except Exception:
        return False


def _recent_home_resident(hass, within_secs: int) -> Optional[str]:
    """A flagged resident whose ``person``/``device_tracker`` flipped to ``home``
    within ``within_secs`` (the most recent arrival wins). None when nobody
    qualifies — which keeps JARVIS silent rather than guessing. Never raises."""
    try:
        now = datetime.now(timezone.utc)
        best = None  # (age_seconds, name)
        for domain in ("person", "device_tracker"):
            try:
                states = hass.states.async_all(domain)
            except Exception:
                continue
            for st in states or []:
                try:
                    if str(getattr(st, "state", "")).lower() != "home":
                        continue
                    lc = getattr(st, "last_changed", None)
                    if lc is None:
                        continue   # can't tell when they arrived → don't guess
                    age = (now - lc).total_seconds()
                    if age < 0 or age > within_secs:
                        continue
                    attrs = getattr(st, "attributes", {}) or {}
                    name = attrs.get("friendly_name") \
                        or str(getattr(st, "entity_id", "")).split(".", 1)[-1]
                    if not _is_resident(name):
                        continue
                    if best is None or age < best[0]:
                        best = (age, name)
                except Exception:
                    continue
        return best[1] if best else None
    except Exception:
        return None


def identify_arrival(hass, within_secs: int = 120) -> Optional[str]:
    """Name of the resident who just arrived, or None. Recognition first (a
    flagged resident seen confidently just now — precise 'you walked in'), then
    presence (a resident whose tracker just went home). None → stay silent.
    Never raises."""
    try:
        from . import recognition
        name = recognition.resident_present(hass)   # already resident-gated + fresh
        if name:
            return name
    except Exception:
        pass
    return _recent_home_resident(hass, within_secs)


def greeting_text(name: str, time_greeting: str = "") -> str:
    """The spoken line. With a time-of-day greeting: 'Good evening, Sam. Welcome
    home.'; without: 'Welcome home, Sam.' Empty when there's no name."""
    name = str(name or "").strip()
    if not name:
        return ""
    tg = str(time_greeting or "").strip()
    return f"{tg}, {name}. Welcome home." if tg else f"Welcome home, {name}."


def allow_greeting(name: str, cooldown_min: int, now: float = None) -> bool:
    """Per-person cooldown gate. Returns True (and records the time) when ``name``
    hasn't been greeted within ``cooldown_min`` minutes; False otherwise. Never
    raises."""
    try:
        if not name:
            return False
        key = str(name).strip().lower()
        n = now if now is not None else _time.monotonic()
        cd = max(0, int(cooldown_min)) * 60
        if (n - _last_greeted.get(key, float("-inf"))) < cd:
            return False
        _last_greeted[key] = n
        return True
    except Exception:
        return False


def note_departure(name: str, now: float = None) -> None:
    """Record that resident ``name`` has left the property (their tracker went
    ``not_home``/away). Called from the presence listener. Idempotent while they
    stay away. Never raises."""
    try:
        key = str(name or "").strip().lower()
        if not key:
            return
        _departed[key] = now if now is not None else _time.monotonic()
    except Exception:
        pass


def has_departed(name: str) -> bool:
    """True when resident ``name`` has an unconsumed departure on record — i.e.
    they left the property and haven't been greeted home since. The 'they left
    first' half of the greeting gate. Never raises."""
    try:
        return str(name or "").strip().lower() in _departed
    except Exception:
        return False


def clear_departure(name: str) -> None:
    """Consume ``name``'s departure (after greeting them home), so the next
    greeting requires a fresh leave→return. Never raises."""
    try:
        _departed.pop(str(name or "").strip().lower(), None)
    except Exception:
        pass


def reset_cooldowns() -> None:
    """Clear the per-person cooldown table (used on unload and in tests)."""
    _last_greeted.clear()


def reset_departures() -> None:
    """Clear the departure ledger (used on unload and in tests)."""
    _departed.clear()
