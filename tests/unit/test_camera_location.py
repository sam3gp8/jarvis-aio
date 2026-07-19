"""Tests for camera indoor/outdoor designation (v6.49.0): the pure pin/unpin
helpers, mode derivation, and — the part that matters — pins written to the
exact config keys the WS command uses actually governing outdoor.is_outdoor
over its own name/area heuristics."""
import pytest


@pytest.fixture
def od(load):
    return load("outdoor")


# ── set_entity_location / location_mode (pure) ───────────────────────────────

def test_pin_outdoor_moves_between_lists(od):
    new_in, new_out = od.set_entity_location(
        ["camera.den"], ["binary_sensor.gate"], "camera.den", "outdoor")
    assert "camera.den" not in new_in
    assert "camera.den" in new_out
    assert "binary_sensor.gate" in new_out          # neighbours preserved


def test_pin_indoor_and_back_to_auto(od):
    new_in, new_out = od.set_entity_location([], [], "camera.backyard", "indoor")
    assert new_in == ["camera.backyard"] and new_out == []
    new_in, new_out = od.set_entity_location(new_in, new_out, "camera.backyard", "auto")
    assert new_in == [] and new_out == []


def test_pin_preserves_globs(od):
    new_in, new_out = od.set_entity_location(
        ["camera.guest*"], ["camera.yard*"], "camera.front_walk", "outdoor")
    assert "camera.guest*" in new_in
    assert set(new_out) == {"camera.yard*", "camera.front_walk"}


def test_pin_idempotent(od):
    a = od.set_entity_location([], [], "camera.x", "outdoor")
    b = od.set_entity_location(a[0], a[1], "camera.x", "outdoor")
    assert b == a


def test_location_mode_exact_only(od):
    assert od.location_mode("camera.den", ["camera.den"], []) == "indoor"
    assert od.location_mode("camera.den", [], ["camera.den"]) == "outdoor"
    assert od.location_mode("camera.den", [], []) == "auto"
    # a glob classifies via is_outdoor but is not a per-camera pin
    assert od.location_mode("camera.denright", ["camera.den*"], []) == "auto"


def test_location_mode_case_insensitive(od):
    assert od.location_mode("camera.Den", ["camera.den"], []) == "indoor"


# ── end-to-end precedence through is_outdoor ────────────────────────────────

def _wire_cfg(od, monkeypatch, load, indoor, outdoor_l):
    jcfg = load("jarvis_config")
    store = {"indoor_entities": indoor, "outdoor_entities": outdoor_l}
    monkeypatch.setattr(jcfg, "get", lambda k, d=None: store.get(k, d))


def test_indoor_pin_beats_outdoor_name_keyword(od, load, monkeypatch, fake_hass):
    """camera.backyard_playroom would classify outdoor by name — an INDOOR
    pin (what the ✎ overlay writes) must win, exactly as outdoor.py's layer
    order promises."""
    new_in, new_out = od.set_entity_location([], [], "camera.backyard_playroom", "indoor")
    _wire_cfg(od, monkeypatch, load, new_in, new_out)
    assert od.is_outdoor(fake_hass, "camera.backyard_playroom") is False


def test_outdoor_pin_beats_indoor_default(od, load, monkeypatch, fake_hass):
    """camera.hallway_east has no outdoor signal — an OUTDOOR pin makes the
    whole stack treat it as outside."""
    new_in, new_out = od.set_entity_location([], [], "camera.hallway_east", "outdoor")
    _wire_cfg(od, monkeypatch, load, new_in, new_out)
    assert od.is_outdoor(fake_hass, "camera.hallway_east") is True


def test_auto_unpin_restores_heuristics(od, load, monkeypatch, fake_hass):
    new_in, new_out = od.set_entity_location([], [], "camera.driveway_cam", "indoor")
    _wire_cfg(od, monkeypatch, load, new_in, new_out)
    assert od.is_outdoor(fake_hass, "camera.driveway_cam") is False
    new_in, new_out = od.set_entity_location(new_in, new_out, "camera.driveway_cam", "auto")
    _wire_cfg(od, monkeypatch, load, new_in, new_out)
    assert od.is_outdoor(fake_hass, "camera.driveway_cam") is True   # name keyword again
