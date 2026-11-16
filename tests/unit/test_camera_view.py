"""Where an outdoor camera is looking, read from the floor plan (camera_view).

Regression for a real report: a Front Yard camera logged "a vehicle is parked on
the driveway" for a car on the NEIGHBOUR's driveway. Two causes, both pinned here:

* the frame was described from the saved ``coverage.covered`` list — which rooms
  a camera frames ≥70% of, for intrusion confirmation — so a camera standing in a
  big yard was told it "covers the Driveway"; and
* the ``floor_plan_property`` polygon was never consulted, so nothing said that
  part of the frame is the street or the lot next door.

The plan below is synthetic (same shape as a typical lot: house, front yard with
a driveway in it, a street along the front, an easement strip down one side).
North is up (−y); one editor unit is 0.2 ft.
"""
import json

import pytest

ROOMS = {"1f": {"rooms": [
    {"name": "House", "x": 0, "y": 0, "w": 300, "h": 100, "type": "room"},
    {"name": "Front Yard", "type": "outdoor", "x": -100, "y": -150, "w": 500, "h": 150,
     "points": [[-100, -150], [400, -150], [400, 0], [-100, 0]]},
    {"name": "Driveway", "type": "outdoor", "x": 200, "y": -150, "w": 100, "h": 150},
    {"name": "Backyard", "type": "outdoor", "x": -100, "y": 100, "w": 500, "h": 200},
    # hand-drawn 6 units past the west property line — still the owner's yard
    {"name": "Side Yard", "type": "outdoor", "x": -106, "y": 0, "w": 106, "h": 100},
    {"name": "Street", "type": "outdoor", "x": -500, "y": -220, "w": 1300, "h": 70},
    # outside the drawn property line, but an easement is the owner's own land
    {"name": "Easement", "type": "outdoor", "x": 400, "y": -150, "w": 30, "h": 450},
]}}
PROPERTY = {"points": [[-100, -150], [400, -150], [400, 300], [-100, 300]]}
CAMERAS = {"1f": [
    # on the house's front-left corner, looking north-east across the yard
    {"entity": "camera.front", "x": 5, "y": -5, "angle": 315, "fov": 90,
     "range": 900, "indoor": False,
     # what the old path used: the ≥70% "confirmable" list — the driveway only
     "coverage": {"covered": ["Driveway"], "source": "llm"}},
    # out in the yard, pointed back at the house
    {"entity": "camera.at_house", "x": 150, "y": -40, "angle": 90, "fov": 60,
     "range": 600, "indoor": False},
    # on the west wall, looking west over the side yard
    {"entity": "camera.side", "x": -5, "y": 50, "angle": 180, "fov": 60,
     "range": 300, "indoor": False},
    {"entity": "camera.hall", "x": 100, "y": 50, "angle": 0, "fov": 90,
     "range": 80, "indoor": True},
    {"entity": "camera.default_indoor", "x": 100, "y": 50, "angle": 0, "fov": 90,
     "range": 80},                                    # no flag → the editor's default
]}


@pytest.fixture
def cv(load):
    mod = load("camera_view")
    mod._CACHE.clear()
    return mod


def _sector(profile, side):
    return next(s for s in profile["sectors"] if s["side"] == side)


# ── the two bugs ────────────────────────────────────────────────────────────
def test_frame_is_described_by_what_is_in_view_not_the_confirmable_list(cv):
    p = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front")
    # The yard the camera stands in comes first — the old path said "Driveway".
    assert p["on_property"][0] == "Front Yard"
    assert "Driveway" in p["on_property"]


def test_view_past_the_property_line_is_reported(cv):
    p = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front")
    assert p["public"] == ["Street"]
    assert p["offsite"] is True and p["beyond"] is True
    own, off = cv.areas_in_view(p)
    assert own[:2] == ["Front Yard", "Driveway"]
    assert off == ["Street", "a neighbouring property"]


# ── where the property ends, per third of the frame ─────────────────────────
def test_left_of_frame_ends_at_the_street(cv):
    left = _sector(cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front"), "left")
    assert left["beyond"] == {"name": "Street", "scope": "public"}
    assert 25 <= left["limit"] <= 35           # the street is ~29 ft north
    assert [a["name"] for a in left["areas"]] == ["Front Yard"]


def test_right_of_frame_ends_at_the_neighbour_beyond_the_homes_own_driveway(cv):
    right = _sector(cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front"), "right")
    # A larger bearing is further right in the picture: the lot next door (east)
    # is on the RIGHT of a camera facing north-east, never the left.
    assert right["beyond"] == {"name": None, "scope": "offsite"}
    names = [a["name"] for a in right["areas"]]
    assert names == ["Front Yard", "Driveway", "Easement"]
    drive = next(a for a in right["areas"] if a["name"] == "Driveway")
    # the home's own driveway lies INSIDE the limit; the neighbour's is past it
    assert drive["from"] < drive["to"] < right["limit"]
    assert 80 <= right["limit"] <= 95          # east line + easement ≈ 86 ft out


def test_briefing_says_where_the_property_ends(cv):
    text = cv.briefing(cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front"))
    assert text.startswith(
        "On this home's property, this camera overlooks the Front Yard")
    assert "left of the frame" in text and "right of the frame" in text
    assert "the Street — public, not part of the property" in text
    assert "a neighbouring property — not this home" in text
    assert "Only what is inside those limits belongs to this home" in text
    assert "only the nearer one" in text       # two driveways in one frame


# ── how an area's side of the line is decided ───────────────────────────────
def test_easement_is_the_owners_land_even_outside_the_drawn_line(cv):
    p = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front")
    assert "Easement" not in p["public"] and "Easement" not in p["offsite_named"]
    assert any(a["name"] == "Easement" for a in _sector(p, "right")["areas"])


def test_easement_tagged_public_becomes_the_boundary(cv):
    rooms = json.loads(json.dumps(ROOMS))
    next(r for r in rooms["1f"]["rooms"] if r["name"] == "Easement")["scope"] = "public"
    right = _sector(cv.view_profile(rooms, CAMERAS, PROPERTY, "camera.front"), "right")
    assert right["beyond"] == {"name": "Easement", "scope": "public"}
    assert "Easement" not in [a["name"] for a in right["areas"]]


def test_a_named_area_drawn_slightly_over_the_line_is_not_split(cv):
    p = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.side")
    assert p["on_property"] == ["Side Yard"]
    assert p["offsite_named"] == []            # not "Side Yard" on both sides
    centre = _sector(p, "centre")
    assert centre["beyond"] == {"name": None, "scope": "offsite"}
    assert 15 <= centre["limit"] <= 25         # ~20 ft to the west line


def test_a_named_area_wholly_outside_the_line_is_off_property(cv):
    rooms = json.loads(json.dumps(ROOMS))
    rooms["1f"]["rooms"].append(
        {"name": "Neighbour Drive", "type": "outdoor", "x": 430, "y": -150,
         "w": 150, "h": 150})
    p = cv.view_profile(rooms, CAMERAS, PROPERTY, "camera.front")
    assert "Neighbour Drive" in p["offsite_named"]
    assert "Neighbour Drive" not in p["on_property"]
    right = _sector(p, "right")
    assert right["beyond"] == {"name": "Neighbour Drive", "scope": "offsite"}
    assert "the Neighbour Drive — not part of this property" in cv.briefing(p)
    assert "Neighbour Drive" in cv.areas_in_view(p)[1]


# ── walls ───────────────────────────────────────────────────────────────────
def test_the_building_blocks_the_view(cv):
    p = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.at_house")
    assert p["on_property"] == ["Front Yard"]  # never the Backyard behind the house
    assert p["beyond"] is False
    assert cv.briefing(p).endswith("Its whole view lies within the property.")


# ── no property line drawn ──────────────────────────────────────────────────
def test_without_a_property_line_it_does_not_claim_the_view_is_all_property(cv):
    p = cv.view_profile(ROOMS, CAMERAS, {}, "camera.front")
    assert p["has_property_line"] is False
    assert p["public"] == ["Street"] and p["offsite"] is False
    right = _sector(p, "right")
    assert right["beyond"] is None and right["unmapped_from"] is not None
    assert p["unmapped"] is True and p["beyond"] is True
    text = cv.briefing(p)
    assert "the floor plan marks no property line there" in text
    assert "right of the frame: " in text
    assert "stays within the property" not in text.split("right of the frame")[1]


# ── which cameras get a profile ─────────────────────────────────────────────
def test_only_outdoor_cameras_placed_on_the_plan(cv):
    assert cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.hall") is None
    assert cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.default_indoor") is None
    assert cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.not_placed") is None


def test_nothing_on_the_plan_to_read(cv):
    bare = {"1f": {"rooms": [{"name": "House", "x": 0, "y": 0, "w": 300, "h": 100,
                              "type": "room"}]}}
    assert cv.view_profile(bare, CAMERAS, {}, "camera.front") is None


# ── inputs ──────────────────────────────────────────────────────────────────
def test_accepts_the_stored_json_strings(cv):
    a = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front")
    b = cv.view_profile(json.dumps(ROOMS), json.dumps(CAMERAS), json.dumps(PROPERTY),
                        "camera.front")
    assert a["on_property"] == b["on_property"] and a["sectors"] == b["sectors"]


def test_metric_distances(cv):
    p = cv.view_profile(ROOMS, CAMERAS, PROPERTY, "camera.front", units="metric")
    assert p["metric"] is True
    left = _sector(p, "left")
    assert 7.5 <= left["limit"] <= 10.5        # ~29 ft ≈ 8.8 m
    assert " m from the camera" in cv.briefing(p)
    assert " ft" not in cv.briefing(p)


@pytest.mark.parametrize("rooms,cams,prop", [
    ("not json", "not json", "not json"),
    (None, None, None),
    ({"1f": "bogus"}, {"1f": "bogus"}, {"points": "bogus"}),
    (ROOMS, {"1f": [{"entity": "camera.front", "indoor": False, "x": "a"}]}, PROPERTY),
    (ROOMS, CAMERAS, {"points": [[1, 2]]}),
])
def test_never_raises_on_junk(cv, rooms, cams, prop):
    out = cv.view_profile(rooms, cams, prop, "camera.front")
    assert out is None or isinstance(out, dict)


def test_helpers_are_safe_on_empty(cv):
    assert cv.briefing(None) == "" and cv.briefing({}) == ""
    assert cv.areas_in_view(None) == ([], [])
    assert cv.find_camera(CAMERAS, "camera.hall")[0] == "1f"
    assert cv.find_camera("{}", "camera.hall") is None
