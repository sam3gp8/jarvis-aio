"""Entity exclusion: the is_excluded predicate and its application at a
representative choke point (presence detection — the reporter's scenario)."""
import json
import re
import types

from fakes import FakeHass


def _hass(const, **rc):
    h = FakeHass()
    h.data = {const.DOMAIN: {"e1": {"runtime_config": dict(rc)}}}
    return h


def test_no_exclusions_returns_false(load):
    ef, const = load("entity_filter"), load("const")
    assert ef.is_excluded(_hass(const), "light.kitchen") is False


def test_exclude_by_entity_id(load):
    ef, const = load("entity_filter"), load("const")
    h = _hass(const, excluded_entities=["light.kitchen"])
    assert ef.is_excluded(h, "light.kitchen") is True
    assert ef.is_excluded(h, "light.den") is False


def test_exclude_by_domain(load):
    ef, const = load("entity_filter"), load("const")
    h = _hass(const, excluded_domains=["switch"])
    assert ef.is_excluded(h, "switch.fan") is True
    assert ef.is_excluded(h, "light.den") is False


def test_exclude_accepts_json_string(load):
    # Config may arrive as a JSON string (config.json) or a list (runtime).
    ef, const = load("entity_filter"), load("const")
    h = _hass(const, excluded_entities=json.dumps(["binary_sensor.ghost"]))
    assert ef.is_excluded(h, "binary_sensor.ghost") is True


def test_exclude_by_label(load, monkeypatch):
    ef, const = load("entity_filter"), load("const")
    h = _hass(const, excluded_labels=["lbl_night"])

    class _Ent:
        labels = {"lbl_night"}

    class _Reg:
        def async_get(self, eid):
            return _Ent() if eid == "light.x" else None

    monkeypatch.setattr(ef, "er",
                        types.SimpleNamespace(async_get=lambda _h: _Reg()))
    assert ef.is_excluded(h, "light.x") is True
    assert ef.is_excluded(h, "light.y") is False


def test_empty_entity_id_is_safe(load):
    ef, const = load("entity_filter"), load("const")
    assert ef.is_excluded(_hass(const, excluded_domains=["light"]), "") is False
    assert ef.is_excluded(_hass(const, excluded_domains=["light"]), None) is False


def test_presence_summary_skips_excluded_occupancy(load):
    """Reporter's case: a virtual occupancy sensor excluded from JARVIS must not
    register as room presence."""
    presence, const = load("presence"), load("const")
    h = FakeHass()
    h.data = {const.DOMAIN: {"e1": {"runtime_config": {
        "excluded_entities": ["binary_sensor.ghost_occupancy"]}}}}
    h.states.set("binary_sensor.kitchen_presence", "on",
                 device_class="occupancy", friendly_name="Kitchen Presence")
    h.states.set("binary_sensor.ghost_occupancy", "on",
                 device_class="occupancy", friendly_name="Ghost Occupancy")
    rooms = presence.get_presence_summary(h).get("rooms", {})
    assert "kitchen" in rooms
    assert "ghost" not in rooms


def test_presence_summary_includes_sensor_when_not_excluded(load):
    presence, const = load("presence"), load("const")
    h = FakeHass()
    h.data = {const.DOMAIN: {"e1": {"runtime_config": {}}}}
    h.states.set("binary_sensor.ghost_occupancy", "on",
                 device_class="occupancy", friendly_name="Ghost Occupancy")
    rooms = presence.get_presence_summary(h).get("rooms", {})
    assert "ghost" in rooms



def test_room_card_path_applies_exclusion():
    """The room-card area enumeration must consult the exclusion filter, so an
    excluded entity is never counted (light count) or listed (capabilities)."""
    import pathlib
    src = pathlib.Path("custom_components/jarvis/websocket.py").read_text()
    m = re.search(r"def _entities_in_area\(.*?\n(.*?)\n\n\ndef ", src, re.S)
    assert m, "could not isolate _entities_in_area"
    body = m.group(1)
    assert "is_excluded" in body, "_entities_in_area must filter excluded entities"


def test_area_command_path_applies_exclusion():
    """Area/group commands ('turn on the living-room lights') must skip excluded
    entities so they don't participate in group logic."""
    import pathlib
    src = pathlib.Path("custom_components/jarvis/local_engine.py").read_text()
    m = re.search(r"def _find_entities_in_area\(.*?\n(.*?)\n\n\ndef ", src, re.S)
    assert m, "could not isolate _find_entities_in_area"
    body = m.group(1)
    assert "is_excluded" in body, "_find_entities_in_area must filter excluded entities"
