"""MCU Phase C (C3): the home-summary builders read the world through the kernel
WorldModel facade instead of raw `hass.states.async_all` sweeps.

Both `home_state._build_summary` (the system-prompt snapshot) and the
`get_home_summary` agent tool (`agent._exec_home_summary`) are pure read-context
builders. The facade reads the SAME source and preserves each entity's `state`
and `attributes` verbatim, so the rendered summary / JSON must be unchanged.
These tests seed a known world and pin the output.
"""
import json

import pytest


def _seed_world(hass):
    hass.states.set("person.sam", "home", friendly_name="Sam")
    hass.states.set("person.alex", "not_home", friendly_name="Alex")
    hass.states.set("light.kitchen", "on", friendly_name="Kitchen Light")
    hass.states.set("light.hall", "off", friendly_name="Hall Light")
    hass.states.set("lock.front", "unlocked", friendly_name="Front Door Lock")
    hass.states.set("lock.back", "locked", friendly_name="Back Door Lock")
    hass.states.set("cover.garage", "open", friendly_name="Garage Door")
    hass.states.set("binary_sensor.front_door", "on",
                    device_class="door", friendly_name="Front Door")
    hass.states.set("binary_sensor.motion", "on",
                    device_class="motion", friendly_name="Hall Motion")
    hass.states.set("alarm_control_panel.home", "disarmed", friendly_name="Alarm")
    hass.states.set("media_player.tv", "playing",
                    friendly_name="Living Room TV", media_title="The Matrix")


# ── home_state._build_summary (system-prompt snapshot) ──────────────────────────

def test_build_summary_reflects_seeded_world(load, fake_hass):
    home_state = load("home_state")
    _seed_world(fake_hass)
    summary = home_state._build_summary(fake_hass)

    # Lights: 1 on of 2
    assert "Lights on (1/2): Kitchen Light" in summary
    # Locks: only the unlocked one is named
    assert "Unlocked: Front Door Lock" in summary
    # Covers: the open garage
    assert "Open covers: Garage Door" in summary
    # Security: the open door (motion binary_sensor must NOT be counted)
    assert "Open doors/windows: Front Door" in summary
    assert "Hall Motion" not in summary
    # Alarm state verbatim
    assert "Alarm: disarmed" in summary
    # Media with title
    assert "Playing media: Living Room TV (The Matrix)" in summary


def test_build_summary_all_lights_off_phrasing(load, fake_hass):
    home_state = load("home_state")
    fake_hass.states.set("light.a", "off", friendly_name="A")
    fake_hass.states.set("light.b", "off", friendly_name="B")
    summary = home_state._build_summary(fake_hass)
    assert "All 2 lights are off" in summary


def test_build_summary_temperature_room_filter(load, fake_hass):
    home_state = load("home_state")
    # Room-level temp is included; a device-internal temp is not.
    fake_hass.states.set("sensor.bedroom_temp", "68",
                         device_class="temperature",
                         friendly_name="Bedroom Temperature",
                         unit_of_measurement="°F")
    fake_hass.states.set("sensor.cpu_temp", "55",
                         device_class="temperature",
                         friendly_name="CPU Core Temp",
                         unit_of_measurement="°C")
    summary = home_state._build_summary(fake_hass)
    assert "Bedroom Temperature: 68°F" in summary
    assert "CPU Core Temp" not in summary


# ── agent._exec_home_summary (the get_home_summary tool) ────────────────────────

async def test_exec_home_summary_json_reflects_seeded_world(load, fake_hass):
    agent = load("agent")
    _seed_world(fake_hass)
    fake_hass.states.set("climate.main", "heat",
                         friendly_name="Thermostat",
                         current_temperature=70, temperature=72)
    fake_hass.states.set("weather.home", "sunny",
                         friendly_name="Weather", temperature=75, humidity=40)

    out = json.loads(await agent._exec_home_summary(fake_hass, {}))

    # People: both, with verbatim states
    by_name = {p["name"]: p["state"] for p in out["people"]}
    assert by_name == {"Sam": "home", "Alex": "not_home"}
    # Lights on
    assert out["lights_on"] == ["Kitchen Light"]
    assert out["lights_on_count"] == 1
    # Locks unlocked
    assert out["locks_unlocked"] == ["Front Door Lock"]
    # Open doors/windows: the door sensor + the open cover (not the motion sensor)
    assert set(out["open_doors_windows"]) == {"Front Door", "Garage Door"}
    # Climate carries current/target temps verbatim
    assert out["climate"] == [{
        "name": "Thermostat", "state": "heat",
        "current_temp": 70, "target_temp": 72,
    }]
    # Weather (first entity)
    assert out["weather"] == {
        "condition": "sunny", "temperature": 75, "humidity": 40,
    }


async def test_exec_home_summary_empty_world_is_valid_json(load, fake_hass):
    agent = load("agent")
    out = json.loads(await agent._exec_home_summary(fake_hass, {}))
    assert out["people"] == []
    assert out["lights_on"] == []
    assert out["lights_on_count"] == 0
    assert out["locks_unlocked"] == []
    assert out["open_doors_windows"] == []
    assert out["climate"] == []
    assert "weather" not in out
