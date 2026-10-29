"""#405 — import a floor plan from a vacuum-robot map / dashboard image entity.

Covers the pure, HA-free logic: which entities are offered as sources, source
enumeration, and the floor_plan_bg merge. The actual image fetch needs a live HA
image/camera entity, so it's exercised only for its defensive shape.
"""
import json
import sys

import pytest

from fakes import FakeHass


@pytest.fixture
def fp(load):
    return load("floorplan_import")


# ── discovery heuristic ───────────────────────────────────────────────────────

def test_image_domain_is_always_a_source(fp):
    assert fp.is_floorplan_source("image.cleaner_living_room_map_0") is True
    assert fp.is_floorplan_source("image.anything") is True


def test_camera_needs_a_map_token(fp):
    assert fp.is_floorplan_source("camera.robot_map", "Robot Map") is True
    assert fp.is_floorplan_source("camera.valetudo_map") is True
    assert fp.is_floorplan_source("camera.front_door", "Front Door") is False


def test_non_image_non_camera_rejected(fp):
    assert fp.is_floorplan_source("sensor.cleaner_map") is False
    assert fp.is_floorplan_source("binary_sensor.vacuum_map") is False
    assert fp.is_floorplan_source("") is False


def test_camera_matches_by_friendly_name(fp):
    # id has no token, but the friendly name names a floor plan.
    assert fp.is_floorplan_source("camera.xyz", "Downstairs Floor Plan") is True


# ── source enumeration ────────────────────────────────────────────────────────

def test_list_sources_collects_and_sorts(fp):
    h = FakeHass()
    h.states.set("image.cleaner_map", "idle", friendly_name="Cleaner Map")
    h.states.set("camera.robot_map", "idle", friendly_name="Robot Map")
    h.states.set("camera.front_door", "idle", friendly_name="Front Door")
    h.states.set("sensor.temp", "20", friendly_name="Temp")
    out = fp.list_sources(h)
    ids = [s["entity_id"] for s in out]
    assert "image.cleaner_map" in ids
    assert "camera.robot_map" in ids
    assert "camera.front_door" not in ids   # no map token
    assert "sensor.temp" not in ids          # wrong domain
    # images first, then cameras
    assert out[0]["domain"] == "image"


# ── floor_plan_bg merge ───────────────────────────────────────────────────────

def test_merge_into_empty(fp):
    out = fp._merge_background(None, "1f", "data:image/png;base64,AAA")
    assert json.loads(out) == {"1f": "data:image/png;base64,AAA"}


def test_merge_preserves_other_floors(fp):
    prior = json.dumps({"2f": "data:image/png;base64,OLD"})
    out = fp._merge_background(prior, "1f", "data:image/png;base64,NEW")
    parsed = json.loads(out)
    assert parsed["2f"] == "data:image/png;base64,OLD"
    assert parsed["1f"] == "data:image/png;base64,NEW"


def test_merge_accepts_dict_and_overwrites_floor(fp):
    out = fp._merge_background({"1f": "old"}, "1f", "new")
    assert json.loads(out) == {"1f": "new"}


def test_merge_tolerates_garbage_prior(fp):
    out = fp._merge_background("not json", "1f", "data:image/png;base64,X")
    assert json.loads(out) == {"1f": "data:image/png;base64,X"}


# ── image fetch: in-process domain helper preferred (#405) ────────────────────

class _FakeImg:
    def __init__(self, content, content_type="image/png"):
        self.content = content
        self.content_type = content_type


async def test_fetch_uses_in_process_image_helper(fp, monkeypatch):
    # #405 regression: a Roborock-style image.* entity imports via the in-process
    # image helper, independent of the entity_picture proxy (whose stale token was
    # the failure). The proxy is NOT consulted when the helper succeeds.
    h = FakeHass()
    h.states.set(
        "image.cleaner_living_room_map_0", "2026-01-01T00:00:00+00:00",
        friendly_name="Cleaner Map",
        entity_picture="/api/image_proxy/image.cleaner_living_room_map_0?token=stale")
    seen = {}

    async def _fake(hass, entity_id, timeout=10):
        seen["entity_id"] = entity_id
        return _FakeImg(b"\x89PNG\r\n" + b"map-bytes" * 300, "image/png")

    monkeypatch.setattr(sys.modules["homeassistant.components.image"], "async_get_image", _fake)
    content, ctype = await fp._fetch_image_bytes(h, "image.cleaner_living_room_map_0")
    assert seen["entity_id"] == "image.cleaner_living_room_map_0"
    assert content.startswith(b"\x89PNG") and ctype == "image/png"


async def test_fetch_uses_in_process_camera_helper(fp, monkeypatch):
    h = FakeHass()
    h.states.set("camera.robot_map", "idle", friendly_name="Robot Map")

    async def _fake(hass, entity_id, timeout=10):
        return _FakeImg(b"\xff\xd8" + b"jpeg-map" * 200, "image/jpeg")

    monkeypatch.setattr(sys.modules["homeassistant.components.camera"], "async_get_image", _fake)
    content, ctype = await fp._fetch_image_bytes(h, "camera.robot_map")
    assert content.startswith(b"\xff\xd8") and ctype == "image/jpeg"


async def test_fetch_defaults_content_type_when_helper_omits_it(fp, monkeypatch):
    h = FakeHass()
    h.states.set("image.cleaner_map", "2026-01-01", friendly_name="Cleaner Map")

    async def _fake(hass, entity_id, timeout=10):
        return _FakeImg(b"\x89PNG" + b"x" * 2000, content_type=None)

    monkeypatch.setattr(sys.modules["homeassistant.components.image"], "async_get_image", _fake)
    _content, ctype = await fp._fetch_image_bytes(h, "image.cleaner_map")
    assert ctype == "image/png"   # image domain default when entity gives none


async def test_fetch_falls_through_to_clear_error_when_empty(fp, monkeypatch):
    # Helper returns nothing and there's no entity_picture → the panel-facing
    # ValueError, not a traceback.
    h = FakeHass()
    h.states.set("image.cleaner_map", "unknown", friendly_name="Cleaner Map")

    async def _empty(hass, entity_id, timeout=10):
        return _FakeImg(None)

    monkeypatch.setattr(sys.modules["homeassistant.components.image"], "async_get_image", _empty)
    with pytest.raises(ValueError, match="couldn't read an image"):
        await fp._fetch_image_bytes(h, "image.cleaner_map")


async def test_fetch_missing_entity_raises_available(fp):
    h = FakeHass()
    with pytest.raises(ValueError, match="not available"):
        await fp._fetch_image_bytes(h, "image.nope")
