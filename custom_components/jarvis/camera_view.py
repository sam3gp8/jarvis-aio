"""Where an outdoor camera is looking, read from the floor plan (pure geometry).

The camera path used to learn "what this camera sees" from the saved
``coverage.covered`` list. That list answers a different question — which rooms a
camera frames at least ~70% of, well enough to *confirm a person* for intrusion —
and as a description of the frame it is wrong twice over:

* a camera standing in a large yard, framing only part of it, is described as
  covering some other, smaller area that happens to fall wholly inside its cone
  (a Front Yard camera becomes "this camera covers the Driveway"); and
* whatever lies **beyond the property line** — the street, a neighbour's
  driveway — is never mentioned, because it is either a sliver of a huge zone or
  not a drawn zone at all. The ``floor_plan_property`` polygon the owner drew was
  not consulted anywhere.

This module answers the right question from that same plan. It casts rays across
the camera's field of view and walks each one outward over the ground, recording
what it crosses: the home's own named outdoor areas, named public areas (a
Street), and land outside the property polygon (a neighbour). From that it
reports, for the left / centre / right of the frame, which of the home's areas
are in view, how far the property reaches, and what lies past it.

Conventions match the panel's editor: bearings are degrees with 0 = +x and
90 = +y (y grows downward on the plan), so a larger bearing is further to the
camera's right, i.e. further right in the picture; one editor unit is 0.2 ft.

Pure: no Home Assistant import and no I/O — the three floor-plan values are
passed in, as dicts or as the JSON strings they are stored as. Never raises.
"""
from __future__ import annotations

import json
import math
from typing import Optional

UNIT_FT = 0.2           # editor units → feet (the panel's _fpToReal)
_RAY_STEP = 3.0         # sample spacing along a ray, in editor units (0.6 ft)
_MIN_RAYS = 33          # rays across the field of view (odd → a true centre ray)
_MOUNT_TOL = 8.0        # ignore a wall this close to the lens (it is mounted on it)
_SECTOR_QUORUM = 0.34   # share of a sector's rays that must agree to be reported
_MIN_SHARE = 0.02       # an area below this share of the view is not "in view"

SCOPE_PROPERTY = "property"   # this home's own ground
SCOPE_PUBLIC = "public"       # a named public area (street, sidewalk, …)
SCOPE_OFFSITE = "offsite"     # outside the property polygon (a neighbour)
SCOPE_UNMAPPED = "unmapped"   # no property polygon drawn, and no named area there

SIDES = ("left", "centre", "right")

# Outdoor areas whose NAME marks them as public. Shared with camera.py so the
# name rule lives in one place. "easement" is deliberately absent: an easement is
# the owner's own land, on-property unless tagged otherwise (v9.83.0).
PUBLIC_AREA_PATTERNS = (
    "street", "road", "sidewalk", "pavement", "alley",
    "right of way", "right-of-way", "curb", "public",
)

_CACHE: dict = {}
_CACHE_MAX = 24


# ── small pure helpers ──────────────────────────────────────────────────────
def _as_obj(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}
    return raw or {}


def _points(room) -> list:
    """A room/zone outline: its polygon when drawn, else its rectangle."""
    pts = room.get("points")
    if isinstance(pts, list) and len(pts) >= 3:
        out = []
        for p in pts:
            try:
                out.append((float(p[0]), float(p[1])))
            except Exception:
                return []
        return out
    try:
        x, y = float(room.get("x", 0)), float(room.get("y", 0))
        w, h = float(room.get("w", 0)), float(room.get("h", 0))
    except Exception:
        return []
    if w <= 0 or h <= 0:
        return []
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _area(pts) -> float:
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _inside(x: float, y: float, pts) -> bool:
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _ray_hit(cx, cy, dx, dy, x0, y0, x1, y1) -> Optional[float]:
    """Distance along the ray (cx,cy)+t·(dx,dy) to the segment, or None."""
    ex, ey = x1 - x0, y1 - y0
    den = dx * ey - dy * ex
    if abs(den) < 1e-9:
        return None
    t = ((x0 - cx) * ey - (y0 - cy) * ex) / den
    u = ((x0 - cx) * dy - (y0 - cy) * dx) / den
    if t < 0 or u < 0 or u > 1:
        return None
    return t


def area_scope(room) -> Optional[str]:
    """An area's scope by explicit tag or by name, or None to let the property
    polygon decide. Mirrors the name rule the reasoning step already used: an
    explicit ``scope``/``public`` tag wins, then a public-sounding outdoor name,
    then "easement" (the owner's land)."""
    try:
        if room.get("scope") == "property" or room.get("public") is False:
            return SCOPE_PROPERTY
        if room.get("scope") == "public" or room.get("public") is True:
            return SCOPE_PUBLIC
        name = str(room.get("name") or "").strip().lower()
        outdoor = str(room.get("type") or "").lower() == "outdoor"
        if outdoor and any(p in name for p in PUBLIC_AREA_PATTERNS):
            return SCOPE_PUBLIC
        if "easement" in name:
            return SCOPE_PROPERTY
    except Exception:
        pass
    return None


def find_camera(cameras_raw, entity_id: str):
    """(floor, camera dict) for an entity placed on the plan, else None."""
    data = _as_obj(cameras_raw)
    if not isinstance(data, dict):
        return None
    for floor, cams in data.items():
        if not isinstance(cams, list):
            continue
        for cam in cams:
            if isinstance(cam, dict) and cam.get("entity") == entity_id:
                return floor, cam
    return None


def _mostly_inside(pts, bbox, prop, grid: int = 8) -> bool:
    """True when at least half of a zone's area lies within the property."""
    x0, y0, x1, y1 = bbox
    hit = tot = 0
    for i in range(grid):
        for j in range(grid):
            px = x0 + (i + 0.5) / grid * (x1 - x0)
            py = y0 + (j + 0.5) / grid * (y1 - y0)
            if not _inside(px, py, pts):
                continue
            tot += 1
            if _inside(px, py, prop):
                hit += 1
    return tot == 0 or hit * 2 >= tot


def _median(vals) -> float:
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# ── the profile ─────────────────────────────────────────────────────────────
def view_profile(rooms_raw, cameras_raw, property_raw, entity_id: str,
                 *, units: str = "imperial") -> Optional[dict]:
    """What an OUTDOOR camera placed on the floor plan actually frames.

    Returns None when the camera is not on the plan, is an indoor camera, or the
    plan gives nothing to reason about — callers then keep their old behaviour.
    Otherwise a dict:

    ``on_property``  the home's named areas in view, largest share of view first
    ``public``       named public areas the view reaches (e.g. "Street")
    ``offsite``      True when the view reaches land outside the property polygon
    ``offsite_named`` owner-drawn areas that lie outside the polygon
    ``beyond``       True when any of the view lies beyond the property
    ``sectors``      for "left" / "centre" / "right" of the frame: the home's
                     areas crossed (name, from, to), ``limit`` — how far the
                     property reaches — and ``beyond`` — what lies past it
    ``metric``       distances are metres when True, else feet
    Distances are in real units already (feet or metres), not editor units.
    """
    try:
        key = (json.dumps(_as_obj(rooms_raw), sort_keys=True),
               json.dumps(_as_obj(cameras_raw), sort_keys=True),
               json.dumps(_as_obj(property_raw), sort_keys=True),
               str(entity_id), str(units))
    except Exception:
        key = None
    if key is not None and key in _CACHE:
        return _CACHE[key]
    try:
        out = _compute(rooms_raw, cameras_raw, property_raw, entity_id, units)
    except Exception:
        out = None
    if key is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def _compute(rooms_raw, cameras_raw, property_raw, entity_id, units) -> Optional[dict]:
    found = find_camera(cameras_raw, entity_id)
    if not found:
        return None
    floor, cam = found
    if cam.get("indoor") is not False:          # the editor's default is indoor
        return None
    plan = _as_obj(rooms_raw)
    fdata = plan.get(floor) if isinstance(plan, dict) else None
    rooms = [r for r in ((fdata or {}).get("rooms") or []) if isinstance(r, dict)]

    prop = []
    praw = _as_obj(property_raw)
    for p in (praw.get("points") if isinstance(praw, dict) else None) or []:
        try:
            prop.append((float(p[0]), float(p[1])))
        except Exception:
            prop = []
            break
    if len(prop) < 3:
        prop = []

    zones, walls = [], []
    for r in rooms:
        pts = _points(r)
        if len(pts) < 3:
            continue
        rtype = str(r.get("type") or "").lower()
        if rtype == "outdoor":
            name = str(r.get("name") or "").strip()
            if not name:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            scope = area_scope(r)
            if scope is None:
                # A named area is the owner's own or it is not — judged as a
                # whole by where most of it lies, so a hand-drawn edge that
                # strays a little over the property line does not split it.
                scope = (SCOPE_OFFSITE if prop and not _mostly_inside(pts, bbox, prop)
                         else SCOPE_PROPERTY)
            zones.append({"name": name, "pts": pts, "scope": scope,
                          "area": _area(pts), "bbox": bbox})
        elif rtype not in ("stairs", "door"):   # a building room: its walls block
            for i in range(len(pts)):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                walls.append((a[0], a[1], b[0], b[1]))
    if not zones and not prop:
        return None                              # nothing on the plan to read
    zones.sort(key=lambda z: z["area"])          # the smallest containing zone wins

    cx, cy = float(cam.get("x", 0)), float(cam.get("y", 0))
    ang = float(cam.get("angle", 270) if cam.get("angle") is not None else 270)
    fov = max(5.0, min(float(cam.get("fov", 90) or 90), 359.0))
    rng = max(float(cam.get("range", 55) or 55), 5.0)

    def classify(x, y):
        hit = None
        for z in zones:
            b = z["bbox"]
            if b[0] <= x <= b[2] and b[1] <= y <= b[3] and _inside(x, y, z["pts"]):
                hit = z
                break
        if hit is not None:
            return hit["name"], hit["scope"]
        if not prop:
            return None, SCOPE_UNMAPPED
        return None, (SCOPE_PROPERTY if _inside(x, y, prop) else SCOPE_OFFSITE)

    n = max(_MIN_RAYS, int(fov // 3) | 1)
    rays, counts, total = [], {}, 0
    for i in range(n):
        a = math.radians(ang - fov / 2.0 + fov * i / (n - 1))
        dx, dy = math.cos(a), math.sin(a)
        reach = rng
        for w in walls:
            t = _ray_hit(cx, cy, dx, dy, *w)
            if t is not None and _MOUNT_TOL < t < reach:
                reach = t
        segs, t = [], _RAY_STEP / 2.0
        while t < reach:
            label = classify(cx + dx * t, cy + dy * t)
            counts[label] = counts.get(label, 0) + 1
            total += 1
            if segs and segs[-1][0] == label:
                segs[-1][2] = t + _RAY_STEP / 2.0
            else:
                segs.append([label, t - _RAY_STEP / 2.0, t + _RAY_STEP / 2.0])
            t += _RAY_STEP
        rays.append(segs)
    if not total:
        return None

    metric = str(units or "").lower() == "metric"
    scale = UNIT_FT * (0.3048 if metric else 1.0)

    def share(label):
        return counts.get(label, 0) / total

    on_property = sorted(
        (lbl for lbl in counts
         if lbl[1] == SCOPE_PROPERTY and lbl[0] and share(lbl) >= _MIN_SHARE),
        key=lambda lbl: -share(lbl))
    public = sorted(
        (lbl for lbl in counts if lbl[1] == SCOPE_PUBLIC and lbl[0]),
        key=lambda lbl: -share(lbl))
    offsite_named = sorted(
        (lbl for lbl in counts if lbl[1] == SCOPE_OFFSITE and lbl[0]),
        key=lambda lbl: -share(lbl))
    offsite = sum(c for lbl, c in counts.items() if lbl[1] == SCOPE_OFFSITE) / total >= 0.01

    sectors = []
    for si, side in enumerate(SIDES):
        chunk = rays[si * n // 3:(si + 1) * n // 3]
        quorum = max(1, int(math.ceil(len(chunk) * _SECTOR_QUORUM)))
        exits, beyond_votes, spans, unknown = [], {}, {}, []
        for segs in chunk:
            seen_own, unmapped_at = False, None
            for label, d0, d1 in segs:
                if label[1] == SCOPE_PROPERTY:
                    seen_own = True
                    if label[0]:
                        spans.setdefault(label[0], []).append((d0, d1))
                    continue
                # Unmapped land (no property line drawn there) is unknown, not a
                # boundary: note where it starts and keep walking past it.
                if label[1] == SCOPE_UNMAPPED:
                    if seen_own and unmapped_at is None:
                        unmapped_at = d0
                    continue
                if unmapped_at is not None:
                    # Unmarked ground came first, so the property ended somewhere
                    # before this — where is unknown. Report that, not a street
                    # reached further out as if the land in between were the home's.
                    break
                exits.append(d0)
                beyond_votes[label] = beyond_votes.get(label, 0) + 1
                break
            if unmapped_at is not None:
                unknown.append(unmapped_at)
        areas = []
        for name, ds in spans.items():
            if len(ds) < quorum:
                continue
            areas.append({"name": name,
                          "from": _median([d[0] for d in ds]) * scale,
                          "to": _median([d[1] for d in ds]) * scale})
        areas.sort(key=lambda a: a["from"])
        sector = {"side": side, "areas": areas, "limit": None, "beyond": None,
                  "unmapped_from": None}
        # Report what most of this third of the frame does. (With a property line
        # drawn there is never unmarked ground, so `unknown` is always empty.)
        if len(unknown) >= quorum and len(unknown) > len(exits):
            sector["unmapped_from"] = _median(unknown) * scale
        elif len(exits) >= quorum:
            top = max(beyond_votes.items(), key=lambda kv: kv[1])[0]
            sector["limit"] = _median(exits) * scale
            sector["beyond"] = {"name": top[0], "scope": top[1]}
        sectors.append(sector)

    unmapped = any(s["unmapped_from"] is not None for s in sectors)
    return {
        "entity": entity_id,
        "on_property": [lbl[0] for lbl in on_property],
        "public": [lbl[0] for lbl in public],
        "offsite_named": [lbl[0] for lbl in offsite_named],
        "offsite": bool(offsite),
        "unmapped": unmapped,
        "beyond": bool(public or offsite or unmapped),
        "sectors": sectors,
        "metric": metric,
        "has_property_line": bool(prop),
    }


# ── wording ─────────────────────────────────────────────────────────────────
def _join(names) -> str:
    names = [f"the {n}" for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _dist(value: float, metric: bool) -> str:
    """A rounded, human distance: nearest 5 ft (or whole metre)."""
    try:
        v = max(0.0, float(value))
    except Exception:
        v = 0.0
    if metric:
        return f"{max(1, int(round(v)))} m"
    return f"{max(5, int(round(v / 5.0)) * 5)} ft"


def _beyond_phrase(beyond: dict) -> str:
    name, scope = beyond.get("name"), beyond.get("scope")
    if scope == SCOPE_PUBLIC:
        return f"the {name} — public, not part of the property"
    if name:
        return f"the {name} — not part of this property"
    return "a neighbouring property — not this home"


def areas_in_view(profile: dict) -> tuple:
    """(on-property area names, off-property labels) for the reasoning step's
    "Areas in view" line. The off-property labels are the named public areas plus
    "a neighbouring property" when the view reaches past the property polygon."""
    if not profile:
        return [], []
    off = list(profile.get("public") or []) + list(profile.get("offsite_named") or [])
    if profile.get("offsite") and not profile.get("offsite_named"):
        off.append("a neighbouring property")
    return list(profile.get("on_property") or []), off


def briefing(profile: dict) -> str:
    """The frame, in words a vision or reasoning model can use: which of the
    home's areas are in view and, per third of the frame, where the property ends
    and what lies beyond. Empty string when there is no profile."""
    if not profile:
        return ""
    metric = bool(profile.get("metric"))
    own = profile.get("on_property") or []
    head = ("On this home's property, this camera overlooks "
            + (_join(own) if own else "the home's own grounds") + ".")
    if not profile.get("beyond"):
        return head + " Its whole view lies within the property."

    lines = []
    for sector in profile.get("sectors") or []:
        side = sector.get("side")
        where = f"{side} of the frame"
        areas = sector.get("areas") or []
        if sector.get("limit") is None or not sector.get("beyond"):
            if sector.get("unmapped_from") is not None:
                # No property line is drawn on this side — say so rather than
                # claim the view stays on the property.
                named = _join(a["name"] for a in areas) or "the home's own ground"
                lines.append(
                    f"- {where}: {named}; the floor plan marks no property line "
                    f"there, so treat anything clearly beyond those areas (past "
                    f"about {_dist(sector['unmapped_from'], metric)}) as not this "
                    f"home's.")
            elif areas:
                lines.append(
                    f"- {where}: {_join(a['name'] for a in areas)}; "
                    "the view there stays within the property.")
            continue
        parts = []
        for i, a in enumerate(areas):
            if i == 0:
                parts.append(f"the {a['name']}")
            else:
                parts.append(
                    f"the {a['name']} (from about {_dist(a['from'], metric)} out)")
        ground = ", then ".join(parts) if parts else "the home's own ground"
        lines.append(
            f"- {where}: {ground}; the property ends about "
            f"{_dist(sector['limit'], metric)} from the camera, and past that is "
            f"{_beyond_phrase(sector['beyond'])}.")
    if not lines:
        return head
    return (
        head
        + " Where the property ends, per the home's floor plan (approximate ground "
        "distance from the camera):\n" + "\n".join(lines) + "\n"
        "Only what is inside those limits belongs to this home. A road, a vehicle "
        "parked on or across the street, and a neighbour's house, yard, driveway or "
        "vehicles are OFF the property even though this camera can see them. If two "
        "driveways or yards are visible, only the nearer one — inside the limits "
        "above — is this home's.")
