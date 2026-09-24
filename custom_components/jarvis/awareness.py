"""A first-person sense of what JARVIS has been noticing lately.

``situation.py`` answers *what is true right now* — who's home, the weather,
the next calendar event — as present-tense facts for the agent to reason over.
This module answers a different, more sentient question: *what have I been
noticing lately?* It reads the recent semantic camera events that
``camera_learning`` has been recording into the pattern store and folds them
into a short first-person recollection — the difference between a status
readout and a butler who remembers that a vehicle has been turning into the
driveway most evenings and that it's usually Sam at the front door around six.

It is deliberately narrow and non-duplicative:

  * It reads only the learnable ``camera_event.*`` rows (what perception has
    *taught* JARVIS), not the live entity inventory ``situation`` already
    covers, so the two blocks never say the same thing.
  * It reports *remembered regularity over the last few days*, in the past
    tense, from the same ``state_changes`` store the pattern miner reads — so
    JARVIS's spoken awareness and its learned automations come from one memory,
    not two.
  * The ranking and phrasing are pure functions (``rank_observations`` /
    ``compose_reflection``) so they're testable without a database or hass; the
    only impure part is ``reflect``, which reads the rows and never raises.

The result is injected into the agent's system prompt as a short
"## What I've noticed lately" block, giving JARVIS a memory of the household's
rhythm to reason from — continuity, not just a live snapshot.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# How far back the recollection reaches, and how many lines it may speak. Kept
# short on purpose: this is "lately", a handful of salient rhythms, not a log.
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MAX_LINES = 5
# A rhythm has to have happened at least this many times in the window before
# JARVIS will claim to have "noticed" it — one sighting is not a pattern.
MIN_OCCURRENCES = 2

# Semantic label → the noun JARVIS uses when recalling it.
_LABEL_NOUN = {
    "person": "someone",
    "vehicle": "a vehicle",
    "animal": "an animal",
    "package": "a package",
    "activity": "movement",
}


def _location_phrase(entity_id: str) -> str:
    """``camera_event.front_yard`` → 'the front yard' for natural recall."""
    obj = str(entity_id or "").split(".", 1)[-1]
    words = obj.replace("_", " ").strip()
    if not words:
        return "one of the cameras"
    return f"the {words}"


def _hour_phrase(hour: Optional[int]) -> str:
    """A modal hour (0–23) → 'around 6pm' / 'around midnight'. '' if unknown."""
    if hour is None:
        return ""
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return ""
    if not 0 <= h <= 23:
        return ""
    if h == 0:
        return "around midnight"
    if h == 12:
        return "around noon"
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"around {h12}{suffix}"


def _daypart_phrase(hours: list) -> str:
    """A coarse part-of-day from the sightings, e.g. 'in the evenings'."""
    if not hours:
        return ""
    avg = sum(hours) / len(hours)
    if avg < 5:
        return "overnight"
    if avg < 11:
        return "in the mornings"
    if avg < 14:
        return "around midday"
    if avg < 18:
        return "in the afternoons"
    if avg < 22:
        return "in the evenings"
    return "late at night"


def _weekday_skew(days: list) -> str:
    """'on weekdays' / 'on weekends' when the sightings clearly skew, else ''."""
    if len(days) < 3:
        return ""
    weekend = sum(1 for d in days if d in (5, 6))
    frac = weekend / len(days)
    if frac <= 0.15:
        return "on weekdays"
    if frac >= 0.85:
        return "on weekends"
    return ""


def rank_observations(rows, now: Optional[datetime] = None,
                      min_occurrences: int = MIN_OCCURRENCES) -> list:
    """Fold raw ``camera_event`` rows into ranked recollections. Pure.

    ``rows`` is an iterable of mappings with (at least) ``entity_id``,
    ``new_state`` (the semantic label), and optionally ``hour``,
    ``day_of_week``, ``person`` and ``timestamp``. Rows are grouped by
    (location, label, person); a group is kept only once it has been seen
    ``min_occurrences`` times, then ranked so the most regular, most recent
    rhythms come first. Returns a list of observation dicts."""
    groups: dict = defaultdict(lambda: {
        "count": 0, "hours": [], "days": [], "last_ts": ""})
    for row in rows or []:
        try:
            entity_id = str(row.get("entity_id") or "")
            label = str(row.get("new_state") or "").strip().lower()
            if not entity_id or not label:
                continue
            person = str(row.get("person") or "unknown").strip() or "unknown"
            key = (entity_id, label, person)
            g = groups[key]
            g["count"] += 1
            h = row.get("hour")
            if h is not None:
                try:
                    g["hours"].append(int(h))
                except (TypeError, ValueError):
                    pass
            d = row.get("day_of_week")
            if d is not None:
                try:
                    g["days"].append(int(d))
                except (TypeError, ValueError):
                    pass
            ts = str(row.get("timestamp") or "")
            if ts > g["last_ts"]:
                g["last_ts"] = ts
        except Exception:
            continue

    out = []
    for (entity_id, label, person), g in groups.items():
        if g["count"] < max(1, int(min_occurrences)):
            continue
        modal_hour = None
        if g["hours"]:
            modal_hour = Counter(g["hours"]).most_common(1)[0][0]
        out.append({
            "entity_id": entity_id,
            "label": label,
            "person": person,
            "count": g["count"],
            "modal_hour": modal_hour,
            "hours": g["hours"],
            "days": g["days"],
            "last_ts": g["last_ts"],
        })

    # Most regular first; break ties by most recently seen.
    out.sort(key=lambda o: (o["count"], o["last_ts"]), reverse=True)
    return out


def _observation_sentence(obs: dict) -> str:
    """One first-person recollection line for a ranked observation."""
    where = _location_phrase(obs["entity_id"])
    label = obs["label"]
    person = obs.get("person") or "unknown"
    named = person.lower() not in ("unknown", "none", "")
    count = obs["count"]

    when = _hour_phrase(obs.get("modal_hour")) or _daypart_phrase(obs.get("hours") or [])
    skew = _weekday_skew(obs.get("days") or [])
    tail_bits = [b for b in (when, skew) if b]
    tail = (" " + " ".join(tail_bits)) if tail_bits else ""
    times = "once" if count == 1 else f"{count} times"

    if label == "person" and named:
        return f"I've been seeing {person} at {where}{tail} ({times} lately)."
    noun = _LABEL_NOUN.get(label, "something")
    if label == "person":
        return f"{where.capitalize()} regularly has someone{tail} ({times} lately)."
    return f"I keep noticing {noun} at {where}{tail} ({times} lately)."


def compose_reflection(observations, max_lines: int = DEFAULT_MAX_LINES) -> str:
    """Ranked observations → a short first-person recollection. Pure.

    Returns '' when there's nothing worth recalling, so the caller can drop the
    block entirely rather than print an empty heading."""
    if not observations:
        return ""
    lines = []
    for obs in observations[: max(1, int(max_lines))]:
        try:
            s = _observation_sentence(obs)
        except Exception:
            s = ""
        if s:
            lines.append("- " + s)
    return "\n".join(lines)


def _load_rows(db_path: str, lookback_days: int):
    """Read recent ``camera_event.*`` rows from the pattern store. Never raises."""
    import sqlite3
    cutoff = ""
    try:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=int(lookback_days))).isoformat()
    except Exception:
        cutoff = ""
    rows = []
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT entity_id, new_state, area_id, hour, day_of_week, "
                "person, timestamp FROM state_changes "
                "WHERE entity_id LIKE 'camera_event.%' AND timestamp >= ? "
                "ORDER BY timestamp DESC LIMIT 5000",
                (cutoff,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    return rows


def reflect(hass=None, *, db_path: Optional[str] = None,
            lookback_days: int = DEFAULT_LOOKBACK_DAYS,
            max_lines: int = DEFAULT_MAX_LINES) -> str:
    """Compose JARVIS's "what I've noticed lately" recollection, or ''. Never
    raises. ``hass`` is accepted for a uniform call signature but unused; the
    memory lives in the pattern DB, not live state."""
    try:
        path = db_path
        if not path:
            try:
                from . import cognitive_core
                core = getattr(cognitive_core, "_CORE", None)
                path = getattr(core, "_db_path", None) if core else None
            except Exception:
                path = None
            from .paths import config_path_str
            path = path or config_path_str("jarvis", "patterns.db", hass=hass)
        rows = _load_rows(path, lookback_days)
        if not rows:
            return ""
        ranked = rank_observations(rows)
        return compose_reflection(ranked, max_lines=max_lines)
    except Exception:
        _LOGGER.debug("awareness: reflect failed", exc_info=True)
        return ""
