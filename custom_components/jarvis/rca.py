"""
JARVIS root cause analysis — answering *why* something happened, not just that
it did.

Evidence comes from history JARVIS already keeps (all local, no network):
  • state_changes + commands  in /config/jarvis/patterns.db   (the StateLogger)
  • activity_log              in /config/jarvis/conversations.db (JARVIS's own
                              announcements and actions)

``analyze()`` gathers everything around a focal event, builds a merged timeline,
and ranks candidate causes with simple, *explainable* heuristics:

  attributed   the StateLogger recorded who/what triggered the change
  cascade      an upstream/related device went unavailable just before
  command      someone asked for it (voice/text) shortly before
  jarvis       JARVIS itself acted on it (or its room) shortly before
  schedule     the same change recurs at this hour — likely an automation
  area         something else changed in the same room moments earlier
  unknown      insufficient evidence in the window

Deliberately deterministic: no LLM in here. The agent exposes this as a tool, so
the reasoning model receives the structured findings and narrates them — the
narrative comes for free, and the engine stays testable.

SYNC sqlite — call via executor. Never raises; missing DBs/tables yield empty
evidence and an honest "insufficient evidence" verdict.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

_LOGGER = logging.getLogger(__name__)

PATTERNS_DB = "/config/jarvis/patterns.db"
ACTIVITY_DB = "/config/jarvis/conversations.db"

DEFAULT_WINDOW_SECS = 1800     # look back 30 min before the event
AFTER_WINDOW_SECS = 120        # small tail after, for context
CASCADE_WINDOW_SECS = 90       # upstream unavailability this close ⇒ cascade
NEAR_WINDOW_SECS = 300         # commands / JARVIS actions "shortly before"
SCHEDULE_LOOKBACK_DAYS = 14    # recurrence horizon for the schedule heuristic
SCHEDULE_MIN_HITS = 4          # same change at this hour ≥ this many days ⇒ schedule
TIMELINE_CAP = 40
CANDIDATE_CAP = 5

_GENERIC_TOKENS = {
    "sensor", "binary", "light", "switch", "cover", "lock", "climate", "fan",
    "media", "player", "the", "a", "an", "status", "state", "device", "main",
    "on", "off", "level", "mode",
}


# ── small utilities ──────────────────────────────────────────────────────────

def _dt(ts) -> Optional[datetime]:
    """Parse the ISO-ish TEXT timestamps in our DBs (T or space separator,
    optional Z / offset). Returns a NAIVE datetime for safe comparison."""
    if not ts:
        return None
    try:
        s = str(ts).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=None)
    except Exception:
        return None


def _tokens(entity_id: str) -> set:
    """Distinctive name tokens of an entity (device-family fingerprint):
    'binary_sensor.garage_hub_motion' -> {'garage', 'hub', 'motion'}."""
    obj = (entity_id or "").split(".", 1)[-1]
    return {t for t in re.split(r"[^a-z0-9]+", obj.lower())
            if len(t) > 2 and t not in _GENERIC_TOKENS}


def _rows(db_path: str, sql: str, params: tuple) -> list[dict]:
    """Query a DB tolerantly: no file / no table / bad SQL ⇒ []."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


# ── evidence gathering ───────────────────────────────────────────────────────

def _focal_event(patterns_db: str, entity_id: str,
                 event_time: Optional[str]) -> Optional[dict]:
    """The state_changes row under analysis: the latest for the entity, or the
    nearest at/just-before an explicit event_time."""
    if event_time:
        rows = _rows(patterns_db,
                     "SELECT * FROM state_changes WHERE entity_id=? AND "
                     "datetime(timestamp)<=datetime(?) "
                     "ORDER BY datetime(timestamp) DESC LIMIT 1",
                     (entity_id, event_time))
    else:
        rows = _rows(patterns_db,
                     "SELECT * FROM state_changes WHERE entity_id=? "
                     "ORDER BY datetime(timestamp) DESC LIMIT 1", (entity_id,))
    return rows[0] if rows else None


def gather(entity_id: str, event_time: Optional[str] = None,
           window_secs: int = DEFAULT_WINDOW_SECS,
           patterns_db: str = PATTERNS_DB,
           activity_db: str = ACTIVITY_DB) -> dict:
    """All evidence around the focal event. Never raises."""
    focal = _focal_event(patterns_db, entity_id, event_time)
    if focal is None and event_time:
        focal = {"entity_id": entity_id, "timestamp": event_time,
                 "old_state": None, "new_state": None, "area_id": None,
                 "domain": entity_id.split(".", 1)[0], "triggered_by": None,
                 "hour": None}
    t0 = _dt(focal["timestamp"]) if focal else None
    ev = {"entity_id": entity_id, "focal": focal, "t0": t0,
          "changes": [], "commands": [], "actions": [], "hour_hits": 0}
    if t0 is None:
        return ev

    start = (t0 - timedelta(seconds=window_secs)).isoformat(sep=" ")
    end = (t0 + timedelta(seconds=AFTER_WINDOW_SECS)).isoformat(sep=" ")
    # Timestamps may be T- or space-separated depending on writer age; normalize
    # both sides through sqlite's datetime() so comparisons are correct.
    ev["changes"] = _rows(
        patterns_db,
        "SELECT * FROM state_changes WHERE datetime(timestamp) >= datetime(?) "
        "AND datetime(timestamp) <= datetime(?) ORDER BY datetime(timestamp) ASC "
        "LIMIT 500", (start, end))
    ev["commands"] = _rows(
        patterns_db,
        "SELECT * FROM commands WHERE datetime(timestamp) >= datetime(?) "
        "AND datetime(timestamp) <= datetime(?) ORDER BY datetime(timestamp) ASC",
        (start, end))
    ev["actions"] = _rows(
        activity_db,
        "SELECT * FROM activity_log WHERE datetime(timestamp) >= datetime(?) "
        "AND datetime(timestamp) <= datetime(?) ORDER BY datetime(timestamp) ASC",
        (start, end))

    # Schedule heuristic input: how often has this exact transition happened at
    # this hour over the lookback horizon (excluding the focal row itself)?
    if focal.get("new_state") is not None and focal.get("hour") is not None:
        horizon = (t0 - timedelta(days=SCHEDULE_LOOKBACK_DAYS)).isoformat(sep=" ")
        hits = _rows(
            patterns_db,
            "SELECT COUNT(*) AS n FROM state_changes WHERE entity_id=? AND "
            "new_state=? AND hour=? AND datetime(timestamp) >= datetime(?) "
            "AND datetime(timestamp) < datetime(?)",
            (entity_id, focal["new_state"], focal["hour"], horizon,
             t0.isoformat(sep=" ")))
        ev["hour_hits"] = int(hits[0]["n"]) if hits else 0
    return ev


# ── candidate causes ─────────────────────────────────────────────────────────

def _secs_before(t0: datetime, ts) -> Optional[float]:
    d = _dt(ts)
    if d is None or d > t0:
        return None
    return (t0 - d).total_seconds()


def candidates(ev: dict) -> list[dict]:
    """Ranked candidate causes with confidence + human-readable evidence."""
    out: list[dict] = []
    focal, t0 = ev.get("focal"), ev.get("t0")
    if not focal or t0 is None:
        return [{"kind": "unknown", "confidence": 0.0,
                 "cause": "no recorded history for this entity",
                 "evidence": "the state logger has no rows for it yet"}]
    eid = ev["entity_id"]
    toks = _tokens(eid)
    area = focal.get("area_id")

    # 1) The logger already recorded the trigger at the moment of the change.
    trig = (focal.get("triggered_by") or "").strip()
    if trig and trig.lower() != "system":
        out.append({"kind": "attributed", "confidence": 0.95,
                    "cause": f"triggered by '{trig}'",
                    "evidence": "recorded by the state logger at the moment of the change"})

    # 2) Upstream-unavailable cascade.
    for row in ev["changes"]:
        if row["entity_id"] == eid:
            continue
        if str(row.get("new_state", "")).lower() not in ("unavailable", "unknown"):
            continue
        secs = _secs_before(t0, row["timestamp"])
        if secs is None or secs > CASCADE_WINDOW_SECS:
            continue
        shared = _tokens(row["entity_id"]) & toks
        same_area = bool(area) and row.get("area_id") == area
        if shared or same_area:
            conf = 0.9 if shared else 0.8
            why = (f"shares the device name '{sorted(shared)[0]}'" if shared
                   else "same room")
            out.append({"kind": "cascade", "confidence": conf,
                        "cause": f"{row['entity_id']} went {row['new_state']} "
                                 f"{int(secs)}s earlier",
                        "evidence": why + " — likely an upstream device or hub failure",
                        "timestamp": row["timestamp"]})

    # 3) A person asked for it.
    area_toks = _tokens(f"x.{area}") if area else set()
    for cmd in ev["commands"]:
        secs = _secs_before(t0, cmd["timestamp"])
        if secs is None or secs > NEAR_WINDOW_SECS:
            continue
        text = str(cmd.get("text", "")).lower()
        if any(t in text for t in toks) or any(t in text for t in area_toks) \
                or (focal.get("domain") and str(focal["domain"]) in text):
            who = cmd.get("person") or "someone"
            out.append({"kind": "command", "confidence": 0.85,
                        "cause": f"{who} asked: \"{cmd['text']}\" {int(secs)}s earlier",
                        "evidence": "the request names this device, its room, or its type",
                        "timestamp": cmd["timestamp"]})

    # 4) JARVIS itself acted.
    for act in ev["actions"]:
        secs = _secs_before(t0, act["timestamp"])
        if secs is None or secs > NEAR_WINDOW_SECS:
            continue
        msg = str(act.get("message", "")).lower()
        if act.get("entity_id") == eid or any(t in msg for t in toks) \
                or (area and str(area).lower() in msg):
            out.append({"kind": "jarvis", "confidence": 0.8,
                        "cause": f"JARVIS acted {int(secs)}s earlier: "
                                 f"\"{str(act.get('message', ''))[:120]}\"",
                        "evidence": f"category {act.get('category', 'action')}",
                        "timestamp": act["timestamp"]})

    # 5) Recurring at this hour ⇒ likely an automation/schedule.
    if ev.get("hour_hits", 0) >= SCHEDULE_MIN_HITS:
        out.append({"kind": "schedule", "confidence": 0.55,
                    "cause": f"this same change has occurred at this hour on "
                             f"{ev['hour_hits']} recent days",
                    "evidence": "a recurring pattern — likely a Home Assistant "
                                "automation or schedule"})

    # 6) Same-room activity moments earlier.
    if area:
        for row in ev["changes"]:
            if row["entity_id"] == eid or row.get("area_id") != area:
                continue
            secs = _secs_before(t0, row["timestamp"])
            if secs is None or secs > 120:
                continue
            out.append({"kind": "area", "confidence": 0.5,
                        "cause": f"{row['entity_id']} → {row['new_state']} "
                                 f"{int(secs)}s earlier in the same room",
                        "evidence": "related activity in the same area",
                        "timestamp": row["timestamp"]})
            break   # one representative area clue is enough

    if not out:
        out.append({"kind": "unknown", "confidence": 0.0,
                    "cause": "no plausible cause found in the window",
                    "evidence": "no attributed trigger, upstream failure, command, "
                                "JARVIS action, or recurring pattern nearby"})
    out.sort(key=lambda c: c["confidence"], reverse=True)
    return out[:CANDIDATE_CAP]


# ── timeline + top-level analyze ─────────────────────────────────────────────

def _timeline(ev: dict) -> list[dict]:
    items: list[dict] = []
    for row in ev["changes"]:
        items.append({"t": row["timestamp"], "src": "change",
                      "text": f"{row['entity_id']}: "
                              f"{row.get('old_state')} → {row.get('new_state')}"})
    for cmd in ev["commands"]:
        items.append({"t": cmd["timestamp"], "src": "command",
                      "text": f"{cmd.get('person', 'someone')}: \"{cmd['text']}\""})
    for act in ev["actions"]:
        items.append({"t": act["timestamp"], "src": "jarvis",
                      "text": str(act.get("message", ""))[:140]})
    items.sort(key=lambda i: (_dt(i["t"]) or datetime.min))
    if len(items) > TIMELINE_CAP:
        items = items[-TIMELINE_CAP:]
    return items


def analyze(entity_id: str, event_time: Optional[str] = None,
            window_secs: int = DEFAULT_WINDOW_SECS,
            patterns_db: str = PATTERNS_DB,
            activity_db: str = ACTIVITY_DB) -> dict:
    """Full root-cause analysis for an entity's (latest or specified) change."""
    try:
        ev = gather(entity_id, event_time, window_secs, patterns_db, activity_db)
        cands = candidates(ev)
        focal = ev.get("focal") or {}
        top = cands[0]
        if top["kind"] == "unknown":
            summary = (f"I couldn't determine why {entity_id} changed — "
                       f"{top['evidence']}.")
        else:
            summary = (f"Most likely cause: {top['cause']} "
                       f"({int(top['confidence'] * 100)}% confident — "
                       f"{top['evidence']}).")
        return {
            "entity_id": entity_id,
            "event": {
                "timestamp": focal.get("timestamp"),
                "old_state": focal.get("old_state"),
                "new_state": focal.get("new_state"),
                "area_id": focal.get("area_id"),
            },
            "candidates": cands,
            "timeline": _timeline(ev),
            "summary": summary,
        }
    except Exception as exc:   # absolute backstop — RCA must never take JARVIS down
        _LOGGER.exception("rca.analyze failed: %s", exc)
        return {"entity_id": entity_id, "event": {}, "candidates": [],
                "timeline": [], "summary": f"analysis failed: {exc}"}
