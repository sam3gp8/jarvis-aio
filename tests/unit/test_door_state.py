"""Tests for door-state resolution (v6.30.0) — the Residence model's door slots."""
import pytest


@pytest.fixture
def door_state(load):
    return load("door_state")


# ── explicit mapping (the reliable path) ─────────────────────────────────────

def test_explicit_mapping_cover_open(door_state, fake_hass):
    fake_hass.states.set("cover.overhead_1", "open")   # no device_class, odd name
    res = door_state.get_door_states(fake_hass, {"garage": "cover.overhead_1"})
    assert res["garage"] == "open"


def test_explicit_mapping_cover_closed(door_state, fake_hass):
    fake_hass.states.set("cover.overhead_1", "closed")
    res = door_state.get_door_states(fake_hass, {"garage": "cover.overhead_1"})
    assert res["garage"] == "closed"


def test_explicit_mapping_binary_sensor(door_state, fake_hass):
    fake_hass.states.set("binary_sensor.front_contact", "on")
    res = door_state.get_door_states(fake_hass, {"front": "binary_sensor.front_contact"})
    assert res["front"] == "open"


def test_explicit_mapping_lock(door_state, fake_hass):
    fake_hass.states.set("lock.cellar", "unlocked")
    res = door_state.get_door_states(fake_hass, {"cellar": "lock.cellar"})
    assert res["cellar"] == "open"


def test_explicit_mapping_overrides_autodetect(door_state, fake_hass):
    # an auto-detectable garage cover says open, but the explicit mapping points
    # at a different entity that is closed → explicit wins.
    fake_hass.states.set("cover.garage_door", "open", device_class="garage")
    fake_hass.states.set("cover.my_garage", "closed")
    res = door_state.get_door_states(fake_hass, {"garage": "cover.my_garage"})
    assert res["garage"] == "closed"


def test_unknown_mapped_entity_is_skipped(door_state, fake_hass):
    res = door_state.get_door_states(fake_hass, {"garage": "cover.does_not_exist"})
    assert "garage" not in res   # missing entity → slot absent, no crash


# ── auto-detection fallback ──────────────────────────────────────────────────

def test_autodetect_garage_cover_with_device_class(door_state, fake_hass):
    fake_hass.states.set("cover.garage_door", "open", device_class="garage")
    res = door_state.get_door_states(fake_hass, {})
    assert res["garage"] == "open"


def test_autodetect_garage_cover_without_device_class(door_state, fake_hass):
    # THE bug: a garage cover exposed with no device_class must still be found.
    fake_hass.states.set("cover.garage_door", "open")   # device_class missing
    res = door_state.get_door_states(fake_hass, {})
    assert res["garage"] == "open"


def test_window_covering_named_garage_is_excluded(door_state, fake_hass):
    # a shade that happens to mention 'garage' must NOT be treated as a door.
    fake_hass.states.set("cover.garage_shade", "open", device_class="shade")
    res = door_state.get_door_states(fake_hass, {})
    assert "garage" not in res


def test_door_binary_sensor_autodetect(door_state, fake_hass):
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    res = door_state.get_door_states(fake_hass, {})
    assert res["front"] == "open"


# ── classify routing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("eid,name,slot", [
    ("cover.cellar_bulkhead", "", "cellar"),
    ("binary_sensor.basement_door", "", "basement"),
    ("cover.side_garage", "", "garage_rear"),
    ("binary_sensor.kitchen_garage", "", "kitchen_garage"),
    ("cover.garage", "", "garage"),
    ("binary_sensor.front", "Front Door", "front"),
    ("binary_sensor.bedroom_window", "", ""),
])
def test_classify(door_state, eid, name, slot):
    assert door_state.classify(eid, name) == slot


# ── entity_is_open domain logic ──────────────────────────────────────────────

@pytest.mark.parametrize("eid,state,expected", [
    ("cover.x", "open", True),
    ("cover.x", "closed", False),
    ("cover.x", "opening", True),
    ("cover.x", "unknown", False),
    ("binary_sensor.x", "on", True),
    ("binary_sensor.x", "off", False),
    ("lock.x", "unlocked", True),
    ("lock.x", "locked", False),
    ("switch.x", "on", True),
])
def test_entity_is_open(door_state, fake_hass, eid, state, expected):
    st = fake_hass.states.set(eid, state)
    assert door_state.entity_is_open(st) is expected
