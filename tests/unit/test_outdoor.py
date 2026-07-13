"""Tests for the outdoor classifier + notable-event policy (v6.39.0)."""
import pytest


@pytest.fixture
def outdoor(load):
    return load("outdoor")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


# ── name-keyword layer ───────────────────────────────────────────────────────

@pytest.mark.parametrize("eid", [
    "binary_sensor.patio_motion", "binary_sensor.deck_occupancy",
    "binary_sensor.shed_motion", "binary_sensor.doorbell_person",
    "binary_sensor.backyard_cam_person", "camera.driveway",
    "binary_sensor.street_motion", "sensor.mailbox", "cover.side_gate",
    "binary_sensor.pool_area_motion", "binary_sensor.front_yard_motion",
])
def test_outdoor_names(outdoor, cfg, fake_hass, eid):
    assert outdoor.is_outdoor(fake_hass, eid) is True


@pytest.mark.parametrize("eid", [
    "binary_sensor.living_room_motion", "binary_sensor.front_door",
    "lock.front_door", "cover.garage_door", "binary_sensor.bedroom_motion",
    "binary_sensor.hallway_cam_person", "binary_sensor.gateway_status",
])
def test_indoor_names(outdoor, cfg, fake_hass, eid):
    # front_door contacts and the garage are the house ENVELOPE — never
    # classified as scenery. 'gateway' must not trip the 'gate' keyword.
    assert outdoor.is_outdoor(fake_hass, eid) is False


def test_friendly_name_classifies(outdoor, cfg, fake_hass):
    fake_hass.states.set("binary_sensor.zone_3", "on", friendly_name="Patio Motion")
    assert outdoor.is_outdoor(fake_hass, "binary_sensor.zone_3") is True


# ── config layers ────────────────────────────────────────────────────────────

def test_outdoor_entities_glob_forces(outdoor, cfg, fake_hass):
    cfg["outdoor_entities"] = ["binary_sensor.zone_*"]
    assert outdoor.is_outdoor(fake_hass, "binary_sensor.zone_7") is True


def test_indoor_override_wins_over_keyword(outdoor, cfg, fake_hass):
    cfg["indoor_entities"] = ["binary_sensor.patio_playroom*"]
    assert outdoor.is_outdoor(fake_hass, "binary_sensor.patio_playroom_motion") is False


def test_area_layer_with_config_extension(outdoor, cfg, fake_hass, monkeypatch):
    monkeypatch.setattr(outdoor, "_area_slug", lambda h, e: "chicken_coop")
    # not in the default outdoor set, no config → indoor
    assert outdoor.is_outdoor(fake_hass, "binary_sensor.nondescript") is False
    # user declares the area outdoor → classified outdoor
    cfg["outdoor_areas"] = ["chicken_coop"]
    assert outdoor.is_outdoor(fake_hass, "binary_sensor.nondescript") is True


# ── notable policy ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("dtype,area,expected", [
    ("person", "backyard", True),
    ("package", "porch", True),
    ("mail", "front_door", True),
    ("damage", "driveway", True),
    ("motion", "backyard", False),      # wind / shadows
    ("vehicle", "driveway", False),     # cars come and go
    ("animal", "backyard", False),
    ("person", "living_room", False),   # not outdoor → not this filter's call
])
def test_notable_policy(outdoor, cfg, fake_hass, dtype, area, expected):
    assert outdoor.notable(fake_hass, "binary_sensor.x", dtype, area_name=area) is expected


def test_cognitive_delegate_compat(load, cfg):
    cc = load("cognitive_core")
    assert cc.is_outdoor_notable("binary_sensor.x", "Backyard", "person") is True
    assert cc.is_outdoor_notable("binary_sensor.x", "Backyard", "motion") is False
