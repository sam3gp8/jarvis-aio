"""Auto-extract room polygons from a vacuum / map image (#405 follow-up).

The background-image import (``floorplan_import``) handles "pull the map in". This
is the harder half the original issue asked for: detecting the room SHAPES from
the map so rooms are placed for you, no manual drawing. A robot-vacuum map fills
each room with a distinct flat colour; this segments those colours and traces
each room's outline into a polygon the floor-plan editor already renders (a
room's ``points``, which drive both the 2D editor and the 3D view).

Pure image maths on **Pillow + numpy** — both already present in a Home Assistant
install — with **no OpenCV / SciPy dependency**: colour masking, morphology
(Pillow Max/MinFilter), the largest-connected-component, a Moore-neighbour
contour trace and Douglas-Peucker simplification are all implemented here. numpy
is imported lazily; a stripped-down install without it gets a clear
``needs_numpy`` result, never a crash.

Best-effort throughout: a map that doesn't segment cleanly yields fewer rooms (or
none), never an exception into the caller — the user reviews and adjusts what it
finds. Rooms come back as ``{name, x, y, w, h, type, points}`` in editor units,
named ``Room N`` (vacuum-map PNGs rarely carry room names; the user renames).
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

_LOGGER = logging.getLogger(__name__)

# Downscale cap for the longest side: keeps the per-room flood-fill fast and
# smooths the thin cleaning-path lines that otherwise fragment a room's colour.
_MAX_SIDE = 480
# Smallest region (fraction of the image) that counts as a room, not a speck.
_MIN_AREA_FRAC = 0.012
# Colour-match tolerance (sum of per-channel abs diff) for a room's fill.
_COLOR_TOL = 24
# Low-saturation cut: walls, plan lines and background shades are near-grey.
_MIN_SAT = 18
# Douglas-Peucker epsilon as a fraction of the room's contour perimeter.
_SIMPLIFY_FRAC = 0.012
# Default editor-unit span the whole detected layout is scaled to fit.
_TARGET_SPAN = 60.0
_MARGIN = 2.0


def available() -> bool:
    """True when the image stack this needs (numpy + Pillow) is importable."""
    try:
        import importlib.util
        return bool(importlib.util.find_spec("numpy")
                    and importlib.util.find_spec("PIL"))
    except Exception:
        return False


# ── image → colour regions ──────────────────────────────────────────────────

def _load_rgb(image_bytes: bytes):
    """Decode to an (H, W, 3) int16 numpy array, downscaled so the longest side
    is <= ``_MAX_SIDE`` (NEAREST, so room fill colours stay flat)."""
    import io
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_SIDE:
        s = _MAX_SIDE / float(max(w, h))
        img = img.resize((max(1, int(round(w * s))), max(1, int(round(h * s)))),
                         Image.NEAREST)
    return np.asarray(img, dtype=np.int16)


def detect_room_colors(rgb, *, min_area_frac: float = _MIN_AREA_FRAC,
                       max_rooms: int = 16) -> List[Tuple[int, int, int]]:
    """The dominant flat, saturated fill colours that look like rooms — the
    border-dominant background colour and near-grey walls/lines are excluded."""
    import numpy as np
    h, w = rgb.shape[:2]
    total = h * w
    flat = rgb.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    # The colour that dominates the 1px border is the map background.
    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    bcols, bcounts = np.unique(border, axis=0, return_counts=True)
    bg = tuple(int(v) for v in bcols[int(np.argmax(bcounts))])
    out: List[Tuple[Tuple[int, int, int], int]] = []
    for col, cnt in zip(colors, counts):
        if cnt < min_area_frac * total:
            continue
        r, g, b = int(col[0]), int(col[1]), int(col[2])
        if (r, g, b) == bg:
            continue
        if max(r, g, b) - min(r, g, b) < _MIN_SAT:   # near-grey: wall/line/bg
            continue
        out.append(((r, g, b), int(cnt)))
    out.sort(key=lambda t: -t[1])
    return [c for c, _ in out[:max_rooms]]


def mask_for_color(rgb, color: Sequence[int], tol: int = _COLOR_TOL):
    """Boolean (H, W) mask of pixels within ``tol`` (summed abs diff) of ``color``."""
    import numpy as np
    d = np.abs(rgb - np.array(color, dtype=np.int16)).sum(axis=2)
    return d <= tol


def _close_open(mask, close: int = 7, open_: int = 5):
    """Morphological close (fill the cleaning-path gaps) then open (drop specks),
    via Pillow's Max/MinFilter. Kernel sizes must be odd."""
    import numpy as np
    from PIL import Image, ImageFilter
    im = Image.fromarray((mask.astype("uint8")) * 255, mode="L")
    if close and close >= 3:
        im = im.filter(ImageFilter.MaxFilter(close)).filter(ImageFilter.MinFilter(close))
    if open_ and open_ >= 3:
        im = im.filter(ImageFilter.MinFilter(open_)).filter(ImageFilter.MaxFilter(open_))
    return np.asarray(im) > 127


def largest_component(mask):
    """Boolean mask of the largest 4-connected component of ``mask`` (or None).
    Iterative flood fill; touches each foreground pixel once."""
    import numpy as np
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=bool)
    best: Optional[list] = None
    best_size = 0
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if visited[y0, x0]:
            continue
        stack = [(y0, x0)]
        visited[y0, x0] = True
        comp = []
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if len(comp) > best_size:
            best_size, best = len(comp), comp
    if not best:
        return None
    out = np.zeros((h, w), dtype=bool)
    idx = np.array(best)
    out[idx[:, 0], idx[:, 1]] = True
    return out


# ── boundary tracing + simplification ───────────────────────────────────────

# Moore 8-neighbourhood, clockwise, starting West.
_NBRS = [(0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1)]


def trace_contour(mask) -> List[Tuple[int, int]]:
    """Ordered outer-boundary pixels of ``mask`` as (x, y), via Moore-neighbour
    tracing with Jacob's stopping criterion. Assumes one filled component."""
    import numpy as np
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return []
    y0 = int(ys.min())
    x0 = int(xs[ys == y0].min())      # topmost, then leftmost
    start = (y0, x0)

    def is_fg(p):
        y, x = p
        return 0 <= y < h and 0 <= x < w and bool(mask[y, x])

    # Single-pixel component.
    if sum(1 for d in _NBRS if is_fg((y0 + d[0], x0 + d[1]))) == 0:
        return [(x0, y0)]

    contour = [start]
    cur = start
    b_idx = 0                          # backtrack points West (background side)
    second = None
    max_steps = 4 * h * w + 16
    for _ in range(max_steps):
        found = False
        for k in range(1, 9):          # scan clockwise from just after backtrack
            idx = (b_idx + k) % 8
            dy, dx = _NBRS[idx]
            nxt = (cur[0] + dy, cur[1] + dx)
            if is_fg(nxt):
                b_idx = (idx + 4) % 8  # came from the opposite direction
                cur = nxt
                found = True
                break
        if not found:
            break
        if second is None:
            second = cur
        # Jacob: back at start about to retrace the first step → done.
        if cur == start:
            break
        contour.append(cur)
    # return as (x, y)
    return [(x, y) for (y, x) in contour]


def _perp(p, a, b) -> float:
    """Perpendicular distance from point ``p`` to the segment a–b."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _dp_open(points: List[Tuple[float, float]], eps: float) -> List[Tuple[float, float]]:
    """Douglas-Peucker on an open polyline (iterative, no recursion limit)."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        dmax, idx = -1.0, -1
        for k in range(i + 1, j):
            d = _perp(points[k], points[i], points[j])
            if d > dmax:
                dmax, idx = d, k
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [points[k] for k in range(n) if keep[k]]


def simplify_polygon(poly: List[Tuple[float, float]], eps: float) -> List[Tuple[float, float]]:
    """Douglas-Peucker on a CLOSED polygon: split at the vertex farthest from the
    first, simplify each arc, and stitch back without duplicate joints."""
    n = len(poly)
    if n < 4:
        return list(poly)
    a0 = poly[0]
    far = max(range(n), key=lambda i: (poly[i][0] - a0[0]) ** 2 + (poly[i][1] - a0[1]) ** 2)
    if far < 2 or far > n - 2:
        far = n // 2
    arc1 = poly[:far + 1]
    arc2 = poly[far:] + [poly[0]]
    s1 = _dp_open(arc1, eps)
    s2 = _dp_open(arc2, eps)
    return s1[:-1] + s2[:-1]


def _perimeter(poly: Sequence[Tuple[float, float]]) -> float:
    if len(poly) < 2:
        return 0.0
    tot = 0.0
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        tot += ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return tot


def _polygon(mask, simplify_frac: float = _SIMPLIFY_FRAC) -> List[Tuple[float, float]]:
    """One room's simplified boundary polygon (pixel coords), or []."""
    contour = trace_contour(mask)
    if len(contour) < 4:
        return []
    eps = max(1.0, simplify_frac * _perimeter(contour))
    poly = simplify_polygon([(float(x), float(y)) for x, y in contour], eps)
    return poly if len(poly) >= 3 else []


# ── public entrypoint ───────────────────────────────────────────────────────

def extract_rooms(image_bytes: bytes, *, target_span: float = _TARGET_SPAN,
                  min_area_frac: float = _MIN_AREA_FRAC,
                  color_tol: int = _COLOR_TOL,
                  simplify_frac: float = _SIMPLIFY_FRAC,
                  max_rooms: int = 16) -> dict:
    """Detect room polygons in a vacuum/map image.

    Returns ``{"ok": True, "rooms": [{name, x, y, w, h, type, points}, ...],
    "count": n}`` with coordinates in editor units — the whole detected layout is
    scaled (aspect preserved) so its larger dimension spans ``target_span`` and
    offset off the origin by a small margin, so relative room positions are kept.
    ``{"ok": False, "error": ...}`` on no-numpy or when nothing segments. Never
    raises."""
    if not available():
        return {"ok": False, "error": "needs_numpy",
                "message": "Room detection needs numpy + Pillow, which this Home "
                           "Assistant install doesn't have."}
    try:
        rgb = _load_rgb(image_bytes)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("floorplan segment: image decode failed: %s", exc)
        return {"ok": False, "error": "decode_failed",
                "message": "Couldn't read that image for room detection."}

    try:
        colors = detect_room_colors(rgb, min_area_frac=min_area_frac,
                                    max_rooms=max_rooms)
        polys: List[List[Tuple[float, float]]] = []
        for col in colors:
            mask = _close_open(mask_for_color(rgb, col, tol=color_tol))
            comp = largest_component(mask)
            if comp is None:
                continue
            poly = _polygon(comp, simplify_frac=simplify_frac)
            if poly:
                polys.append(poly)
    except Exception as exc:  # noqa: BLE001 - segmentation is best-effort
        _LOGGER.debug("floorplan segment: extraction failed: %s", exc)
        return {"ok": False, "error": "segmentation_failed",
                "message": "Couldn't detect rooms in that map."}

    if not polys:
        return {"ok": False, "error": "no_rooms",
                "message": "No distinct room colours found in that map — it may "
                           "not be a segmented vacuum map."}

    # Scale the whole layout into editor units (aspect preserved, off the origin).
    minx = min(x for poly in polys for x, _ in poly)
    miny = min(y for poly in polys for _, y in poly)
    maxx = max(x for poly in polys for x, _ in poly)
    maxy = max(y for poly in polys for _, y in poly)
    span = max(maxx - minx, maxy - miny) or 1.0
    scale = float(target_span) / span

    rooms = []
    for i, poly in enumerate(polys, start=1):
        pts = [[round((x - minx) * scale + _MARGIN, 1),
                round((y - miny) * scale + _MARGIN, 1)] for x, y in poly]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        rx, ry = min(xs), min(ys)
        rooms.append({
            "name": f"Room {i}",
            "x": round(rx, 1), "y": round(ry, 1),
            "w": round(max(xs) - rx, 1), "h": round(max(ys) - ry, 1),
            "type": "room",
            "points": pts,
        })
    # Largest room first — a stable, sensible order for the panel.
    rooms.sort(key=lambda r: -(r["w"] * r["h"]))
    return {"ok": True, "rooms": rooms, "count": len(rooms)}
