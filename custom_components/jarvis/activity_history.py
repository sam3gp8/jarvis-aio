"""HA activity history for JARVIS (v6.72.0).

Two capabilities, both reading Home Assistant's *native* records (not JARVIS's
own patterns.db):

  - entity history — the recorder timeline for one or more entities over a
    window: every state change with timestamps, plus a count. Answers "when did
    the front door open?", "how many times did the garage open today?", "what
    was the thermostat overnight?".
  - logbook — HA's human-readable activity narrative over a window, optionally
    scoped to an entity. Answers "what happened while I was out?", "what's been
    going on in the house?".

Recorder/logbook internals vary by HA version and this integration touches them
in only one other place (panel sparklines), so everything here is wrapped
defensively and via the recorder's own executor — a failure returns an empty
result, never an exception and never a fabricated event. Same discipline as the
rest of JARVIS: no crying wolf, no inventing data.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_MAX_ENTITIES = 20        # cap breadth so a query can't drag the recorder
_MAX_ROWS = 200           # cap timeline rows returned to the model
_MAX_LOGBOOK = 100        # cap logbook entries returned


def _resolve_entities(hass, entity: str | None, area: str | None) -> list[str]:
    """Turn an entity_id, a friendly name, or an area into concrete entity_ids.
    Best-effort and defensive."""
    ids: list[str] = []
    try:
        if entity:
            # exact entity_id?
            if hass.states.get(entity) is not None:
                return [entity]
            # else match by friendly name (case-insensitive substring)
            needle = entity.lower()
            for st in hass.states.async_all():
                fn = (st.attributes.get("friendly_name") or "").lower()
                if needle in fn or needle in st.entity_id.lower():
                    ids.append(st.entity_id)
                    if len(ids) >= _MAX_ENTITIES:
                        break
            return ids
        if area:
            # entities in an area, via the area/entity registries
            try:
                from homeassistant.helpers import (area_registry as ar,
                                                    entity_registry as er)
                areareg = ar.async_get(hass)
                entreg = er.async_get(hass)
                target = None
                al = area.lower()
                for a in areareg.async_list_areas():
                    if a.name and al in a.name.lower():
                        target = a.id
                        break
                if target:
                    for ent in er.async_entries_for_area(entreg, target):
                        ids.append(ent.entity_id)
                        if len(ids) >= _MAX_ENTITIES:
                            break
            except Exception as exc:
                _LOGGER.debug("activity: area resolve failed: %s", exc)
            return ids
    except Exception as exc:
        _LOGGER.debug("activity: entity resolve error: %s", exc)
    return ids


async def entity_history(hass, entity: str | None = None, area: str | None = None,
                         hours: float = 24.0) -> dict:
    """Recorder timeline for the resolved entities over the last `hours`.
    Returns {entities, hours, timeline:[{entity_id, state, when}], counts:{eid:n}}
    or {error}. Never raises."""
    try:
        from homeassistant.components.recorder import get_instance, history
        from homeassistant.util import dt as dt_util
    except Exception:
        return {"error": "history recorder is not available"}

    ids = _resolve_entities(hass, entity, area)
    if not ids:
        return {"error": f"no matching entities for "
                         f"{'entity=' + entity if entity else 'area=' + str(area)}"}

    try:
        hours = max(0.1, min(float(hours), 24 * 30))     # clamp 0.1h–30d
    except Exception:
        hours = 24.0
    end = dt_util.utcnow()
    start = end - timedelta(hours=hours)

    def _fetch():
        return history.get_significant_states(
            hass, start, end, ids, minimal_response=True, no_attributes=True)

    try:
        raw = await get_instance(hass).async_add_executor_job(_fetch)
    except Exception as exc:
        _LOGGER.debug("activity: history fetch failed: %s", exc)
        return {"error": "could not read history for that period"}
    if not raw:
        return {"entities": ids, "hours": hours, "timeline": [],
                "counts": {}, "note": "no recorded changes in that window"}

    timeline: list[dict] = []
    counts: dict = {}
    for eid, states in raw.items():
        n = 0
        for s in states:
            # get_significant_states rows are State objects or minimal dicts
            try:
                st = getattr(s, "state", None)
                when = getattr(s, "last_changed", None) or getattr(s, "last_updated", None)
                if st is None and isinstance(s, dict):
                    st = s.get("state")
                    when = s.get("last_changed") or s.get("last_updated")
                if st is None:
                    continue
                n += 1
                timeline.append({
                    "entity_id": eid,
                    "state": st,
                    "when": when.isoformat() if hasattr(when, "isoformat") else str(when),
                })
            except Exception:
                continue
        counts[eid] = n

    # newest first, capped
    try:
        timeline.sort(key=lambda r: r["when"], reverse=True)
    except Exception:
        pass
    timeline = timeline[:_MAX_ROWS]
    return {"entities": ids, "hours": hours, "timeline": timeline, "counts": counts}


async def logbook(hass, entity: str | None = None, hours: float = 24.0) -> dict:
    """HA logbook narrative over the last `hours`, optionally for one entity.
    Returns {hours, entries:[{when, name, message, entity_id?}]} or {error}.
    Never raises."""
    try:
        from homeassistant.util import dt as dt_util
    except Exception:
        return {"error": "time utilities unavailable"}

    try:
        hours = max(0.1, min(float(hours), 24 * 14))     # clamp 0.1h–14d
    except Exception:
        hours = 24.0
    end = dt_util.utcnow()
    start = end - timedelta(hours=hours)

    ent_id = None
    if entity:
        resolved = _resolve_entities(hass, entity, None)
        ent_id = resolved[0] if resolved else entity

    # The logbook programmatic API differs across HA versions; try the modern
    # processor, fall back gracefully. All wrapped — a miss returns empty.
    try:
        from homeassistant.components import logbook as lb
        from homeassistant.components.recorder import get_instance
    except Exception:
        return {"error": "logbook is not available"}

    def _fetch():
        # async_get_events(hass, start, end, entity_ids=None, ...) is the current
        # shape; older builds expose get_events / _get_events. Probe in order.
        entity_ids = [ent_id] if ent_id else None
        for fname in ("async_get_events", "get_events", "_get_events"):
            fn = getattr(lb, fname, None)
            if fn is None:
                continue
            try:
                if entity_ids:
                    return list(fn(hass, start, end, entity_ids))
                return list(fn(hass, start, end))
            except TypeError:
                try:
                    return list(fn(hass, start, end))
                except Exception:
                    continue
            except Exception:
                continue
        return None

    try:
        rows = await get_instance(hass).async_add_executor_job(_fetch)
    except Exception as exc:
        _LOGGER.debug("activity: logbook fetch failed: %s", exc)
        rows = None

    if not rows:
        return {"hours": hours, "entries": [],
                "note": "no logbook entries for that window (or logbook "
                        "unavailable on this HA build)"}

    entries: list[dict] = []
    for r in rows[:_MAX_LOGBOOK]:
        try:
            if isinstance(r, dict):
                entries.append({
                    "when": str(r.get("when") or r.get("timestamp") or ""),
                    "name": r.get("name") or "",
                    "message": r.get("message") or r.get("state") or "",
                    "entity_id": r.get("entity_id"),
                })
        except Exception:
            continue
    return {"hours": hours, "entries": entries}
