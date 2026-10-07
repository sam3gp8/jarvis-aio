"""#247 — JARVIS must not offer to close/open/lock/unlock things it cannot
actuate. `briefing._gather_open_things` annotates sensor-only openings so the
briefing LLM reports them without offering control it lacks; a door/window/
garage that has a real cover or lock behind the same name stays actionable."""
import types

import pytest


@pytest.fixture
def briefing(load):
    return load("briefing")


def _st(entity_id, state, **attrs):
    return types.SimpleNamespace(entity_id=entity_id, state=state, attributes=attrs)


def _wire(monkeypatch, fake_hass, *, binary=(), covers=(), locks=()):
    def _all(domain=None):
        return {"binary_sensor": list(binary), "cover": list(covers),
                "lock": list(locks)}.get(domain, [])
    monkeypatch.setattr(fake_hass.states, "async_all", _all)


def test_sensor_only_window_is_marked_not_actionable(briefing, fake_hass, monkeypatch):
    _wire(monkeypatch, fake_hass, binary=[
        _st("binary_sensor.office_window", "on",
            device_class="window", friendly_name="Office Window")])
    items = briefing._gather_open_things(fake_hass)
    assert items == ["Office Window is open (monitored only — no actuator to close it)"]


def test_unlocked_lock_stays_actionable(briefing, fake_hass, monkeypatch):
    _wire(monkeypatch, fake_hass, locks=[
        _st("lock.front", "unlocked", friendly_name="Front Door")])
    items = briefing._gather_open_things(fake_hass)
    assert items == ["Front Door is unlocked"]
    assert "monitored only" not in items[0]


def test_sensor_backed_by_matching_cover_is_actionable(briefing, fake_hass, monkeypatch):
    # A garage binary_sensor whose friendly name matches a real cover IS
    # controllable, so it must not be mislabeled monitor-only.
    _wire(monkeypatch, fake_hass,
          binary=[_st("binary_sensor.garage", "on",
                      device_class="garage_door", friendly_name="Garage")],
          covers=[_st("cover.garage", "open", friendly_name="Garage")])
    items = briefing._gather_open_things(fake_hass)
    assert items == ["Garage is open"]


def test_closed_and_locked_things_are_absent(briefing, fake_hass, monkeypatch):
    _wire(monkeypatch, fake_hass,
          binary=[_st("binary_sensor.door", "off",
                      device_class="door", friendly_name="Back Door")],
          locks=[_st("lock.front", "locked", friendly_name="Front Door")])
    assert briefing._gather_open_things(fake_hass) == []
