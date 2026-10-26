"""Auto-extract room polygons from a vacuum/map image (#405 follow-up).

floorplan_segment segments a map's flat room-fill colours and traces each room
into a polygon — pure Pillow + numpy, no OpenCV. These build synthetic maps with
known rooms and pin the pipeline: colour detection drops the background and grey
walls, the contour trace + Douglas-Peucker recover the right corner counts, and
the whole layout scales into editor units with relative positions preserved.
"""
import io

import pytest

np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype("uint8"), "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _blank(h=200, w=200, color=(245, 245, 245)):
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[:, :] = color
    return a


@pytest.fixture
def seg(load):
    return load("floorplan_segment")


# ── core: trace + simplify on a known mask ───────────────────────────────────

def test_trace_and_simplify_square(seg):
    mask = np.zeros((100, 100), dtype=bool)
    mask[20:80, 20:80] = True            # a solid 60×60 square
    poly = seg._polygon(mask, simplify_frac=0.02)
    assert 4 <= len(poly) <= 6, f"square should simplify to ~4 corners, got {len(poly)}"
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    # bbox recovers the square (±a few px from morphology-free trace)
    assert min(xs) <= 22 and max(xs) >= 78
    assert min(ys) <= 22 and max(ys) >= 78


def test_trace_and_simplify_L_shape(seg):
    mask = np.zeros((200, 200), dtype=bool)
    mask[30:170, 30:100] = True          # vertical bar of the L
    mask[100:170, 30:170] = True         # horizontal foot of the L
    poly = seg._polygon(mask, simplify_frac=0.01)
    # an L is a hexagon — allow a little slack for the trace start vertex
    assert 5 <= len(poly) <= 8, f"L-shape should be ~6 corners, got {len(poly)}"


# ── colour detection ─────────────────────────────────────────────────────────

def test_detect_colors_drops_background_and_grey(seg):
    a = _blank()                          # white background (border-dominant)
    a[20:90, 20:90] = (230, 90, 60)       # room A (saturated)
    a[20:90, 110:180] = (60, 120, 230)    # room B (saturated)
    a[150:170, 20:180] = (70, 70, 70)     # a grey "wall" strip (near-grey)
    rgb = np.asarray(a, dtype=np.int16)
    colors = seg.detect_room_colors(rgb)
    cset = {tuple(c) for c in colors}
    assert (230, 90, 60) in cset and (60, 120, 230) in cset
    assert (245, 245, 245) not in cset    # background excluded
    assert (70, 70, 70) not in cset       # grey wall excluded (low saturation)


# ── full pipeline ────────────────────────────────────────────────────────────

def test_extract_two_rooms(seg):
    a = _blank()
    a[20:90, 20:90] = (230, 90, 60)       # left room
    a[20:90, 110:180] = (60, 120, 230)    # right room
    out = seg.extract_rooms(_png(a), target_span=60.0)
    assert out["ok"] is True
    assert out["count"] == 2
    for r in out["rooms"]:
        assert r["type"] == "room"
        assert len(r["points"]) >= 3
        assert r["name"].startswith("Room ")
        # every point is within the scaled target span (+ margin), off the origin
        for px, py in r["points"]:
            assert 0 <= px <= 60 + 5 and 0 <= py <= 60 + 5
    # relative layout preserved: one room sits clearly left of the other
    cx = [sum(p[0] for p in r["points"]) / len(r["points"]) for r in out["rooms"]]
    assert abs(cx[0] - cx[1]) > 10


def test_extract_scales_to_target_span(seg):
    a = _blank()
    a[20:90, 20:90] = (230, 90, 60)
    a[20:90, 110:180] = (60, 120, 230)
    out = seg.extract_rooms(_png(a), target_span=100.0)
    allx = [p[0] for r in out["rooms"] for p in r["points"]]
    ally = [p[1] for r in out["rooms"] for p in r["points"]]
    span = max(max(allx) - min(allx), max(ally) - min(ally))
    assert 90 <= span <= 101, f"layout should span ~100 units, got {span:.1f}"


def test_extract_no_rooms_on_flat_image(seg):
    out = seg.extract_rooms(_png(_blank()))   # all one background colour
    assert out["ok"] is False
    assert out["error"] == "no_rooms"


def test_extract_bad_bytes_is_graceful(seg):
    out = seg.extract_rooms(b"not an image")
    assert out["ok"] is False
    assert out["error"] in ("decode_failed", "segmentation_failed")


def test_available(seg):
    assert seg.available() is True        # numpy + PIL present in the test env
