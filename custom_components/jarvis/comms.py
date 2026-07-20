"""
JARVIS Communication Agent (v6.51.0).

The blueprint's "Communication Agent": proactively surface calendar conflicts
and upcoming commitments. Email is deliberately NOT touched here — reading a
user's inbox from inside the HA process is a privacy/security weight this home
butler shouldn't carry; anyone who wants it can expose specific mail via an HA
sensor and JARVIS will read that like any other entity.

What this does, using the `calendar.*` entities HA already provides (Google
Calendar, CalDAV, Local Calendar, etc.):
  - Reads every calendar entity's current + next event.
  - Detects OVERLAPS across calendars (two things booked at once).
  - Detects TIGHT transitions (back-to-back with < a configurable gap).
  - Produces a plain-language agenda + conflict list the agent speaks.

Dependency-light, registry-guarded, never raises. Times are compared as naive
ISO strings from HA's calendar attributes; we parse defensively and skip
anything unparseable rather than guessing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TIGHT_GAP_MIN = 15   # back-to-back closer than this = "tight"


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        val = jarvis_config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def _parse(ts: Optional[str]) -> Optional[datetime]:
    """Parse an HA calendar timestamp (date or datetime, with/without TZ) to a
    naive datetime for comparison. None if unparseable."""
    if not ts:
        return None
    s = str(ts).strip()
    try:
        # datetime forms
        if "T" in s or " " in s and ":" in s:
            s2 = s.replace(" ", "T")
            if s2.endswith("Z"):
                s2 = s2[:-1]
            # drop timezone offset if present (compare wall-clock)
            for sep in ("+",):
                if sep in s2[11:]:
                    s2 = s2[:11] + s2[11:].split(sep)[0]
            return datetime.fromisoformat(s2[:19])
        # date-only → midnight
        return datetime.fromisoformat(s[:10])
    except Exception:
        return None


def gather_events(hass) -> list[dict]:
    """All calendar entities' current+next events as
    {calendar, title, start(dt), end(dt), all_day}. Skips unparseable."""
    out: list[dict] = []
    if hass is None:
        return out
    try:
        states = hass.states.async_all("calendar")
    except Exception:
        return out
    for st in states:
        a = st.attributes or {}
        title = a.get("message") or a.get("friendly_name") or st.entity_id
        start = _parse(a.get("start_time"))
        end = _parse(a.get("end_time"))
        if start is None:
            continue
        out.append({
            "calendar": st.entity_id,
            "title": str(title),
            "start": start,
            "end": end or (start + timedelta(hours=1)),
            "all_day": bool(a.get("all_day")),
            "active": st.state == "on",
        })
    return out


def find_conflicts(events: list[dict], tight_gap_min: int) -> dict:
    """Pure conflict analysis over event dicts. Returns
    {overlaps: [...], tight: [...]}. Timed events only — all-day events don't
    'conflict' with timed ones."""
    timed = sorted(
        [e for e in events if not e["all_day"]],
        key=lambda e: e["start"],
    )
    overlaps, tight = [], []
    gap = timedelta(minutes=max(0, int(tight_gap_min)))
    for i in range(len(timed)):
        a = timed[i]
        for j in range(i + 1, len(timed)):
            b = timed[j]
            if b["start"] >= a["end"]:
                # b starts after a ends → check tightness, then stop (sorted)
                if b["start"] - a["end"] < gap and b["start"] >= a["end"]:
                    tight.append((a, b))
                break
            # b starts before a ends → overlap
            overlaps.append((a, b))
    return {"overlaps": overlaps, "tight": tight}


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p") if hasattr(dt, "strftime") else str(dt)


def agenda(hass, horizon_hours: int = 24) -> dict:
    """Human-facing agenda + conflicts for the next `horizon_hours`.
    Returns {events: [...str], conflicts: [...str], count}. Never raises."""
    try:
        gap = int(_cfg("calendar_tight_gap_min", _DEFAULT_TIGHT_GAP_MIN))
        now = datetime.now()
        horizon = now + timedelta(hours=max(1, int(horizon_hours)))
        evts = [e for e in gather_events(hass)
                if e["start"] <= horizon and e["end"] >= now]
        evts.sort(key=lambda e: e["start"])

        lines = []
        for e in evts:
            when = "all day" if e["all_day"] else _fmt_time(e["start"])
            lines.append(f"{e['title']} — {when}")

        conf = find_conflicts(evts, gap)
        cflines = []
        for a, b in conf["overlaps"]:
            cflines.append(
                f"conflict: “{a['title']}” and “{b['title']}” overlap "
                f"({_fmt_time(a['start'])} / {_fmt_time(b['start'])})")
        for a, b in conf["tight"]:
            mins = int((b["start"] - a["end"]).total_seconds() // 60)
            cflines.append(
                f"tight: only {mins} min between “{a['title']}” and "
                f"“{b['title']}”")

        return {"events": lines, "conflicts": cflines, "count": len(evts)}
    except Exception as exc:
        _LOGGER.debug("comms.agenda failed: %s", exc)
        return {"events": [], "conflicts": [], "count": 0, "error": str(exc)}
