"""
JARVIS Communication Agent (v6.51.0).

The blueprint's "Communication Agent": proactively surface calendar conflicts
and upcoming commitments. This module handles calendars only; read-only email
access lives in mail.py (v6.81.0) — fetched on request via IMAP and sanitized,
superseding the earlier stance that the inbox should not be read in-process.

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
            "location": a.get("location"),
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


# ── range lookup (#554) ──────────────────────────────────────────────────────
# gather_events() above reads each calendar entity's *attributes*, which only
# expose its current + next event — so anything further out (next week's waste
# collection) is invisible, and an add-on like Annuals, whose entity STATE is a
# number (an age/countdown, e.g. 42), reads back as that number rather than the
# event's date. list_events() instead calls the `calendar.get_events` service,
# which returns the real event list over an arbitrary window, with true dates.

def _calendar_entities(hass, entity_id: Optional[str] = None) -> list[str]:
    """Resolve target calendar entity_ids. With ``entity_id`` (an exact id, or a
    substring of an id or friendly name, case-insensitive) return the matches;
    without it, every ``calendar.*`` entity. An unmatched name returns []."""
    try:
        states = hass.states.async_all("calendar")
    except Exception:
        return []
    if not entity_id:
        return [s.entity_id for s in states]
    needle = str(entity_id).strip().lower()
    for s in states:                                  # exact id first
        if s.entity_id.lower() == needle:
            return [s.entity_id]
    matches = []
    for s in states:
        fn = str((s.attributes or {}).get("friendly_name") or "").lower()
        if needle in s.entity_id.lower() or (fn and needle in fn):
            matches.append(s.entity_id)
    return matches


def _is_all_day(raw) -> bool:
    """A calendar event is all-day when its start is a bare date (no time)."""
    s = str(raw or "")
    return "T" not in s and ":" not in s and len(s.strip()) <= 10


async def list_events(hass, days_ahead: int = 31, query: Optional[str] = None,
                      entity_id: Optional[str] = None) -> dict:
    """Actual events across the household calendars over the next ``days_ahead``
    days, via the ``calendar.get_events`` service (the real list with true dates,
    not the entity's current+next-event attributes). Optionally filtered to one
    calendar and/or to events whose title/description contains ``query``.

    Returns ``{events: [{calendar,title,date,weekday,time,start,end,all_day,
    location}], count}`` with JSON-safe values. Never raises (#554)."""
    out: dict = {"events": [], "count": 0}
    if hass is None:
        return out
    try:
        days = max(1, min(int(days_ahead or 31), 400))
    except Exception:
        days = 31
    now = datetime.now()
    end_dt = now + timedelta(days=days)
    ents = _calendar_entities(hass, entity_id)
    if not ents:
        return {"events": [], "count": 0,
                "error": "no matching calendar entity" if entity_id
                         else "no calendar entities found"}
    collected: list[dict] = []
    for eid in ents:
        try:
            res = await hass.services.async_call(
                "calendar", "get_events",
                {"entity_id": eid,
                 "start_date_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                 "end_date_time": end_dt.strftime("%Y-%m-%d %H:%M:%S")},
                blocking=True, return_response=True,
            )
        except Exception as exc:
            _LOGGER.debug("calendar.get_events failed for %s: %s", eid, exc)
            continue
        data = (res or {}).get(eid) or {}
        for ev in (data.get("events") or []):
            start = _parse(ev.get("start"))
            if start is None:
                continue
            end = _parse(ev.get("end"))
            collected.append({
                "calendar": eid,
                "title": str(ev.get("summary") or ev.get("message") or ""),
                "start": start,
                "end": end or (start + timedelta(hours=1)),
                "all_day": _is_all_day(ev.get("start")),
                "location": ev.get("location"),
                "description": ev.get("description"),
            })
    if query:
        q = str(query).strip().lower()
        collected = [e for e in collected
                     if q in e["title"].lower()
                     or q in str(e.get("description") or "").lower()]
    collected.sort(key=lambda e: e["start"])
    events = []
    for e in collected[:50]:
        events.append({
            "calendar": e["calendar"],
            "title": e["title"],
            "date": e["start"].strftime("%Y-%m-%d"),
            "weekday": e["start"].strftime("%A"),
            "time": None if e["all_day"] else e["start"].strftime("%H:%M"),
            "start": e["start"].isoformat(),
            "end": e["end"].isoformat() if e["end"] else None,
            "all_day": e["all_day"],
            "location": e.get("location"),
        })
    return {"events": events, "count": len(events)}


async def create_event(hass, summary: str, start: str, end: Optional[str] = None,
                       all_day: bool = False, entity_id: Optional[str] = None,
                       description: Optional[str] = None,
                       location: Optional[str] = None) -> dict:
    """Create an event on a household calendar via the ``calendar.create_event``
    service (#554). ``start``/``end`` are ``'YYYY-MM-DD HH:MM:SS'`` (timed) or
    ``'YYYY-MM-DD'`` (all-day); a missing end defaults to +1h (timed) or the next
    day (all-day). Writes to the named calendar, else the only one, else the
    first — the chosen calendar is returned so the caller can confirm it. Returns
    ``{ok, calendar, summary, start, end}`` or ``{ok: False, error}``. This is a
    calendar data write: it creates a calendar entry and commands no device.
    Never raises."""
    if hass is None:
        return {"ok": False, "error": "no hass"}
    summary = str(summary or "").strip()
    start = str(start or "").strip()
    if not summary or not start:
        return {"ok": False, "error": "summary and start are required"}
    ents = _calendar_entities(hass, entity_id)
    if not ents:
        return {"ok": False,
                "error": ("no calendar matches %r" % entity_id) if entity_id
                         else "no calendar entity found to write to"}
    target = ents[0]
    data: dict = {"entity_id": target, "summary": summary}
    if description:
        data["description"] = str(description)
    if location:
        data["location"] = str(location)
    if all_day or _is_all_day(start):
        s = _parse(start)
        data["start_date"] = s.strftime("%Y-%m-%d") if s else start[:10]
        if end:
            e = _parse(end)
            data["end_date"] = e.strftime("%Y-%m-%d") if e else str(end)[:10]
        else:
            # HA treats end_date as exclusive — a one-day event ends next day.
            data["end_date"] = ((s + timedelta(days=1)).strftime("%Y-%m-%d")
                                if s else start[:10])
    else:
        s = _parse(start)
        if s is None:
            return {"ok": False, "error": "could not parse start %r" % start}
        data["start_date_time"] = s.strftime("%Y-%m-%d %H:%M:%S")
        e = _parse(end) if end else None
        data["end_date_time"] = (e or (s + timedelta(hours=1))).strftime(
            "%Y-%m-%d %H:%M:%S")
    try:
        await hass.services.async_call("calendar", "create_event", data,
                                       blocking=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "calendar": target}
    return {"ok": True, "calendar": target, "summary": summary,
            "all_day": bool(all_day or _is_all_day(start)),
            "start": data.get("start_date") or data.get("start_date_time"),
            "end": data.get("end_date") or data.get("end_date_time")}
