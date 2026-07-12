"""
Residence graph — derives room adjacency from the Residence-tab floor plan so
the intrusion investigator can follow a plausible route from the breach point
inward, rather than treating any two motion zones as equivalent.

Floor-plan rooms are stored as boxes ({name, x, y, w, h} per floor). Two rooms
are adjacent when their boxes touch or nearly touch. Rooms are matched to Home
Assistant areas by name/slug so motion (which we know by area) can be located on
the plan. Everything degrades gracefully: no plan, or names that don't line up,
just yields an empty adjacency and the caller falls back to anchoring on the
breach area alone.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_ADJ_GAP = 10.0   # plan units; boxes within this of touching count as adjacent


def slug(s) -> str:
    return "_".join(str(s or "").strip().lower().split())


def _boxes(config: dict) -> dict:
    """floor_plan_rooms -> {floor: [(name, x, y, w, h)]}. Tolerant of shapes."""
    raw = config.get("floor_plan_rooms") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    out: dict = {}
    for floor, fdata in (raw or {}).items():
        rooms = fdata.get("rooms", []) if isinstance(fdata, dict) else fdata
        boxes = []
        for r in rooms or []:
            try:
                boxes.append((r["name"], float(r["x"]), float(r["y"]),
                              float(r.get("w", 0)), float(r.get("h", 0))))
            except Exception:
                continue
        out[floor] = boxes
    return out


def _touch(b1, b2, gap: float = _ADJ_GAP) -> bool:
    """Do two boxes overlap or sit within `gap` of touching?"""
    _, x1, y1, w1, h1 = b1
    _, x2, y2, w2, h2 = b2
    return (x1 - gap < x2 + w2 and x1 + w1 + gap > x2 and
            y1 - gap < y2 + h2 and y1 + h1 + gap > y2)


def room_adjacency(config: dict) -> dict:
    """{room_slug: set(adjacent room_slugs)} across all floors."""
    adj: dict = {}
    for boxes in _boxes(config).values():
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _touch(boxes[i], boxes[j]):
                    a, b = slug(boxes[i][0]), slug(boxes[j][0])
                    if a == b:
                        continue
                    adj.setdefault(a, set()).add(b)
                    adj.setdefault(b, set()).add(a)
    return adj


def _area_slug(hass, area_id: str) -> str:
    """An HA area_id → a room slug (the area's display name slugged, else the id)."""
    try:
        from homeassistant.helpers import area_registry as ar
        area = ar.async_get(hass).async_get_area(area_id)
        if area and area.name:
            return slug(area.name)
    except Exception:
        pass
    return slug(area_id)


def _rooms_to_areas(hass, room_slugs: set) -> set:
    """Map a set of room slugs back to HA area_ids."""
    result: set = set()
    if not room_slugs:
        return result
    try:
        from homeassistant.helpers import area_registry as ar
        for area in ar.async_get(hass).async_list_areas():
            if slug(area.name) in room_slugs:
                result.add(area.id)
    except Exception:
        pass
    return result


def adjacent_areas(hass, config: dict, breach_area: Optional[str]) -> set:
    """HA area_ids adjacent to the breach area per the floor plan. Best-effort;
    empty when there's no plan or the names don't map."""
    if not breach_area:
        return set()
    adj = room_adjacency(config)
    if not adj:
        return set()
    neighbors = adj.get(_area_slug(hass, breach_area), set())
    return _rooms_to_areas(hass, neighbors)
