"""Focused tests for appliance identification and cycle tracking."""
import asyncio
import json
import sys
import types

import pytest


@pytest.fixture
def monitor(load):
    mod = load("appliance_monitor")
    mod._MON = mod._MonitorState()
    return mod


@pytest.mark.parametrize(("entity_id", "friendly_name", "expected"), [
    ("sensor.washer_power", "Laundry", "WASHER"),
    ("sensor.power", "Tumble Dryer Power", "DRYER"),
    ("sensor.dish_washer_power", "", "DISHWASHER"),
    ("sensor.range_power", "", "OVEN"),
    ("sensor.microwave_watts", "", "MICROWAVE"),
    ("sensor.unknown", "", None),
])
def test_classify_appliance_from_entity_or_name(monitor, entity_id, friendly_name, expected):
    result = monitor._classify_appliance(entity_id, friendly_name)
    assert getattr(result, "name", None) == expected


@pytest.mark.parametrize(("watts", "expected"), [
    (100, None), (200, "WASHER"), (600, "MICROWAVE"),
    (1200, "DISHWASHER"), (1800, "DRYER"), (6000, "DRYER"),
    (6001, "OVEN"),
])
def test_power_fingerprint_boundaries(monitor, watts, expected):
    result = monitor._fingerprint_from_power(watts)
    assert getattr(result[0], "name", None) == expected if result else expected is None


@pytest.mark.parametrize(("type_name", "expected"), [
    ("washer", "WASHER"), ("washing machine", "WASHER"),
    ("Dishwasher", "DISHWASHER"), ("appliance", "GENERIC"),
    ("unknown-device", "GENERIC"), (None, "GENERIC"),
])
def test_declared_type_normalization(monitor, type_name, expected):
    assert monitor._type_to_appliance(type_name).name == expected


def test_normalize_profile_accepts_json_and_discards_invalid_entries(monitor):
    config = {"appliance_profile": json.dumps([
        {"name": "  Washer ", "type": " Washer ", "entity": " sensor.washer ", "watts": "520"},
        {"name": "", "type": "dryer", "watts": 500},
        "not-an-object",
        {"name": "Unknown watts", "type": "", "watts": "bad"},
    ])}

    profile = monitor._normalize_profile(config)

    assert profile == [
        {"name": "Washer", "type": "washer", "entity": "sensor.washer",
         "watts": 520.0, "learned_w": 520.0, "observed_n": 0},
        {"name": "Unknown watts", "type": "appliance", "entity": "",
         "watts": 0.0, "learned_w": 0.0, "observed_n": 0},
    ]
    assert monitor._normalize_profile({"appliance_profile": "not-json"}) == []
    assert monitor._normalize_profile({"appliance_profile": {"name": "Washer"}}) == []


def test_declared_matching_tolerance_ambiguity_and_claimed_sensor(monitor):
    washer = {"name": "Washer", "watts": 500, "learned_w": 500}
    dryer = {"name": "Dryer", "watts": 550, "learned_w": 550}
    monitor._MON.disagg = [washer]
    found, error, ambiguous = monitor._match_declared(600)
    assert found is washer
    assert error == pytest.approx(0.2)
    assert ambiguous is False
    assert monitor._match_declared(900) == (None, None, False)

    monitor._MON.disagg = [washer, dryer]
    found, _, ambiguous = monitor._match_declared(525)
    assert found in (washer, dryer)
    assert ambiguous is True

    running = monitor._SensorState(
        "sensor.washer_power", "Washer", monitor.ApplianceType.WASHER,
        phase="running", peak_power=500)
    monitor._MON.sensors = {running.entity_id: running}
    monitor._MON.claimed = {running.entity_id}
    assert monitor._match_declared(510) == (None, None, False)


def test_learn_watts_updates_only_confident_nearby_observations(monitor):
    profile = {"watts": 500, "learned_w": 500, "observed_n": 0}

    monitor._learn_watts(profile, 550, ambiguous=False)
    assert profile == {"watts": 500, "learned_w": 510.0, "observed_n": 1}

    monitor._learn_watts(profile, 900, ambiguous=False)
    monitor._learn_watts(profile, 520, ambiguous=True)
    monitor._learn_watts(profile, 0, ambiguous=False)
    assert profile == {"watts": 500, "learned_w": 510.0, "observed_n": 1}


@pytest.mark.parametrize(("appliance", "label", "friendly", "expected"), [
    ("OVEN", "Oven", "Kitchen", True),
    ("WASHER", "appliance", "Fridge", True),
    ("WASHER", "appliance", "A/C unit", True),
    ("WASHER", "Washer", "Laundry", False),
])
def test_never_announce_filter(monitor, appliance, label, friendly, expected):
    assert monitor._is_never_announce(
        getattr(monitor.ApplianceType, appliance), label, friendly) is expected


def test_sensor_cycle_requires_sustain_and_settle_then_fingerprints(monitor, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(monitor.time, "time", lambda: now[0])
    sensor = monitor._SensorState(
        "sensor.unknown_power", "Unknown", monitor.ApplianceType.GENERIC,
        identified=False)

    assert monitor._process_reading(sensor, 400) is None
    assert sensor.phase == "idle"
    now[0] = 220.0
    assert monitor._process_reading(sensor, 500) is None
    assert sensor.phase == "running"
    assert sensor.peak_power == 500

    now[0] = 221.0
    assert monitor._process_reading(sensor, 5) is None
    now[0] = 281.0
    assert monitor._process_reading(sensor, 5) == "washer"
    assert sensor.phase == "idle"
    assert sensor.identified is True
    assert sensor.appliance is monitor.ApplianceType.WASHER
    assert sensor.discovery_method == "fingerprint:500W"
    assert sensor.announced is True


def test_idle_sensor_resets_sustain_and_running_sensor_resets_settle(monitor, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(monitor.time, "time", lambda: now[0])
    sensor = monitor._SensorState(
        "sensor.washer_power", "Washer", monitor.ApplianceType.WASHER)

    monitor._process_reading(sensor, 60)
    now[0] = 150.0
    monitor._process_reading(sensor, 10)
    assert sensor.run_start == 0.0

    sensor.phase = "running"
    sensor.peak_power = 100
    monitor._process_reading(sensor, 5)
    assert sensor.settle_start == now[0]
    now[0] = 160.0
    monitor._process_reading(sensor, 50)
    assert sensor.settle_start == 0.0


def test_native_completion_announces_once_and_resets_for_next_cycle(monitor):
    calls = []

    async def announce(sensor, label):
        calls.append((sensor.entity_id, label))

    class FakeHass:
        def async_create_task(self, coroutine):
            asyncio.run(coroutine)

    monitor._MON.running = True
    monitor._MON.hass = FakeHass()
    monitor._MON.natives["sensor.washer_run_state"] = monitor._NativeAppliance(
        "sensor.washer_run_state", "Washer", monitor.ApplianceType.WASHER,
        frozenset({"finished"}), last_state="running")
    monitor._announce_done = announce

    def event(state):
        monitor._on_state_changed(types.SimpleNamespace(data={
            "entity_id": "sensor.washer_run_state",
            "new_state": types.SimpleNamespace(state=state),
        }))

    event("finished")
    event("finished")
    assert calls == [("sensor.washer_run_state", "washer")]
    event("running")
    event("finished")
    assert calls == [("sensor.washer_run_state", "washer")] * 2


def test_power_sensor_event_converts_kw_and_ignores_invalid_states(monitor, monkeypatch):
    readings = []

    def process_reading(sensor, watts):
        readings.append(watts)
        return None

    monitor._process_reading = process_reading
    monitor._MON.running = True
    monitor._MON.sensors["sensor.washer_power"] = monitor._SensorState(
        "sensor.washer_power", "Washer", monitor.ApplianceType.WASHER)

    def event(state, unit="W"):
        monitor._on_state_changed(types.SimpleNamespace(data={
            "entity_id": "sensor.washer_power",
            "new_state": types.SimpleNamespace(
                state=state, attributes={"unit_of_measurement": unit}),
        }))

    event("1.25", "kW")
    event("unknown")
    event("not-a-number")
    assert readings == [1250.0]


def test_whole_home_delta_tracks_jump_and_announces_matching_fingerprint(
        monitor, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(monitor.time, "time", lambda: now[0])
    calls = []

    async def announce(sensor, label):
        calls.append((sensor.entity_id, label, sensor.discovery_method))

    class FakeHass:
        def async_create_task(self, coroutine):
            asyncio.run(coroutine)

    monitor._MON.hass = FakeHass()
    monitor._MON.delta = monitor._DeltaTracker(
        "sensor.whole_home_power", baseline_w=100,
        samples=[100] * monitor._DELTA_MAX_SAMPLES)
    monitor._announce_done = announce

    monitor._process_delta(100)
    monitor._process_delta(500)
    assert len(monitor._MON.delta.active_deltas) == 1
    now[0] = 131.0
    monitor._process_delta(100)

    assert monitor._MON.delta.active_deltas == {}
    assert calls == [(
        "sensor.whole_home_power", "washer", "whole_home_delta:380W")]


def test_whole_home_delta_matches_declared_appliance_and_learns(monitor, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(monitor.time, "time", lambda: now[0])
    calls = []

    async def announce(sensor, label):
        calls.append((sensor.entity_id, label, sensor.discovery_method, sensor.peak_power))

    class FakeHass:
        def async_create_task(self, coroutine):
            asyncio.run(coroutine)

    profile = {"name": "Washer", "type": "washer", "entity": "",
               "watts": 500, "learned_w": 500, "observed_n": 0}
    monitor._MON.hass = FakeHass()
    monitor._MON.profile = [profile]
    monitor._MON.disagg = [profile]
    monitor._MON.delta = monitor._DeltaTracker(
        "sensor.whole_home_power", baseline_w=100,
        samples=[100] * monitor._DELTA_MAX_SAMPLES)
    monitor._announce_done = announce

    monitor._process_delta(500)
    now[0] = 110.0
    monitor._process_delta(600)
    now[0] = 131.0
    monitor._process_delta(100)

    assert profile["learned_w"] == 476.0
    assert profile["observed_n"] == 1
    assert calls == [(
        "sensor.whole_home_power", "Washer", "whole_home_match:380W", 600)]
    assert monitor._MON.delta.active_deltas == {}


def test_whole_home_meter_prefers_unsuffixed_main_sensor(monitor, fake_hass):
    fake_hass.states.set(
        "sensor.electric_consumption_1", "0.8", friendly_name="Electric Consumption (1)",
        unit_of_measurement="kW")
    fake_hass.states.set(
        "sensor.electric_consumption", "1.2", friendly_name="Electric Consumption",
        unit_of_measurement="kW")

    tracker = monitor._discover_whole_home_meter(fake_hass)

    assert tracker.entity_id == "sensor.electric_consumption"
    assert tracker.baseline_w == 1200.0
    assert tracker.last_reading == 1200.0


def test_discover_power_sensors_uses_keyword_sibling_area_and_generic_paths(
        monitor, fake_hass, monkeypatch):
    excluded = types.ModuleType("jc.entity_filter")
    excluded.is_excluded = lambda hass, entity_id: entity_id == "sensor.excluded_power"
    monkeypatch.setitem(sys.modules, "jc.entity_filter", excluded)

    fake_hass.states.set(
        "sensor.washer_power", "50", friendly_name="", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.power_meter", "100", friendly_name="Meter", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.plug_power", "70", friendly_name="Plug", unit_of_measurement="W")
    fake_hass.states.set(
        "switch.dishwasher", "on", friendly_name="Dishwasher Plug")
    fake_hass.states.set(
        "sensor.utility_meter", "45", friendly_name="Utility Meter", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.unknown_power", "100", friendly_name="", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.grid_power", "100", friendly_name="Grid power", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.oversized_power", "6001", friendly_name="", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.excluded_power", "100", friendly_name="", unit_of_measurement="W")
    fake_hass.states.set(
        "sensor.temperature", "20", unit_of_measurement="°C")

    siblings = {
        "sensor.power_meter": ("Tumble Dryer", ["switch.dryer"]),
        "sensor.plug_power": (None, ["switch.dishwasher"]),
    }
    monkeypatch.setattr(
        monitor, "_get_device_siblings",
        lambda hass, entity_id: siblings.get(entity_id, (None, [])))
    monkeypatch.setattr(
        monitor, "_get_entity_area",
        lambda hass, entity_id: "utility" if entity_id == "sensor.utility_meter" else None)

    found = monitor._discover_sensors(fake_hass)

    assert found["sensor.washer_power"].appliance is monitor.ApplianceType.WASHER
    assert found["sensor.washer_power"].discovery_method == "keyword"
    assert found["sensor.power_meter"].appliance is monitor.ApplianceType.DRYER
    assert found["sensor.power_meter"].discovery_method == "device_sibling"
    assert found["sensor.plug_power"].appliance is monitor.ApplianceType.DISHWASHER
    assert found["sensor.plug_power"].discovery_method == "sibling_name"
    assert found["sensor.utility_meter"].discovery_method == "area:utility"
    assert found["sensor.unknown_power"].identified is False
    assert found["sensor.unknown_power"].discovery_method == "unidentified"
    assert "sensor.grid_power" not in found
    assert "sensor.oversized_power" not in found
    assert "sensor.excluded_power" not in found
    assert "sensor.temperature" not in found


def test_discover_native_appliances_deduplicates_per_device_and_infers_type(
        monitor, fake_hass, monkeypatch):
    fake_hass.states.set(
        "binary_sensor.washer_run_completed", "off", friendly_name="Washer complete")
    fake_hass.states.set(
        "sensor.washer_run_state", "running", friendly_name="Washer run state")
    fake_hass.states.set(
        "sensor.dryer_job_state", "running", friendly_name="Job state")
    fake_hass.states.set(
        "sensor.mystery_job_state", "unknown", friendly_name="Job state")
    fake_hass.states.set(
        "sensor.unrelated", "on", friendly_name="Unrelated")

    names = {
        "binary_sensor.washer_run_completed": "Washer",
        "sensor.washer_run_state": "Washer",
        "sensor.dryer_job_state": "Smart dryer",
        "sensor.mystery_job_state": "",
    }
    monkeypatch.setattr(
        monitor, "_get_device_siblings",
        lambda hass, entity_id: (names.get(entity_id), []))

    found = monitor._discover_native_appliances(fake_hass)

    assert set(found) == {
        "binary_sensor.washer_run_completed",
        "sensor.dryer_job_state",
        "sensor.mystery_job_state",
    }
    assert found["binary_sensor.washer_run_completed"].appliance is monitor.ApplianceType.WASHER
    assert found["binary_sensor.washer_run_completed"].trigger_states == frozenset({"on"})
    assert found["sensor.dryer_job_state"].appliance is monitor.ApplianceType.DRYER
    assert found["sensor.dryer_job_state"].trigger_states == frozenset({"finished", "end"})
    assert found["sensor.mystery_job_state"].appliance is monitor.ApplianceType.GENERIC


@pytest.mark.asyncio
async def test_start_uses_runtime_profile_and_stop_clears_state(
        monitor, fake_hass, monkeypatch):
    runtime_config = {
        "appliance_profile": [
            {"name": "Washer", "type": "washer",
             "entity": "sensor.declared_washer_power", "watts": 500},
            {"name": "Dryer", "type": "dryer", "entity": "", "watts": 2200},
        ],
        "appliance_announce_unknown": True,
        "appliance_power_guessing": False,
    }
    fake_hass.data["jarvis"] = {"entry-1": {"runtime_config": runtime_config}}
    fake_hass.states.set(
        "sensor.declared_washer_power", "0", unit_of_measurement="W")
    monkeypatch.setattr(monitor, "_discover_sensors", lambda hass: {})
    monkeypatch.setattr(monitor, "_discover_native_appliances", lambda hass: {})
    monkeypatch.setattr(monitor, "_discover_whole_home_meter", lambda hass: None)
    monkeypatch.setattr(monitor, "_get_device_siblings", lambda hass, eid: (None, []))

    await monitor.start(fake_hass, {
        "appliance_profile": [{"name": "Old profile", "watts": 100}],
        "appliance_power_guessing": True,
        "appliance_sensors": {"sensor.extra_dryer_power": "Dryer"},
    })

    assert monitor.is_running() is True
    assert monitor._MON.power_guessing is False
    assert monitor._MON.announce_unknown is True
    assert {ap["name"] for ap in monitor._MON.profile} == {"Washer", "Dryer"}
    assert monitor._MON.claimed == {"sensor.declared_washer_power"}
    assert [ap["name"] for ap in monitor._MON.disagg] == ["Dryer"]
    assert monitor._MON.sensors["sensor.declared_washer_power"].discovery_method == "declared_entity"
    assert monitor._MON.sensors["sensor.extra_dryer_power"].discovery_method == "explicit_config"

    await monitor.stop()
    assert monitor.is_running() is False
    assert monitor._MON.sensors == {}
    assert monitor._MON.natives == {}
    assert monitor._MON.delta is None


@pytest.mark.asyncio
async def test_start_returns_when_no_appliance_sources_exist(monitor, fake_hass, monkeypatch):
    config_module = types.ModuleType("jc.jarvis_config")
    config_module.get_all = lambda: {
        "appliance_profile": [{"name": "Persisted Washer", "type": "washer", "watts": 500}],
        "appliance_announce_unknown": True,
    }
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", config_module)
    monkeypatch.setattr(monitor, "_discover_sensors", lambda hass: {})
    monkeypatch.setattr(monitor, "_discover_native_appliances", lambda hass: {})
    monkeypatch.setattr(monitor, "_discover_whole_home_meter", lambda hass: None)

    await monitor.start(fake_hass, {})

    assert monitor.is_running() is False
    assert monitor._MON.sensors == {}
    assert monitor._MON.profile[0]["name"] == "Persisted Washer"
    assert monitor._MON.announce_unknown is True


@pytest.mark.asyncio
async def test_announce_done_suppresses_oven_and_untrusted_guess(
        monitor, fake_hass, monkeypatch):
    entity_filter = types.ModuleType("jc.entity_filter")
    entity_filter.is_excluded = lambda hass, entity_id: False
    monkeypatch.setitem(sys.modules, "jc.entity_filter", entity_filter)
    monkeypatch.setattr(sys.modules["jc"], "entity_filter", entity_filter, raising=False)
    monitor._MON.hass = fake_hass
    monitor._MON.config = {}

    oven = monitor._SensorState(
        "sensor.oven_power", "Kitchen oven", monitor.ApplianceType.OVEN,
        discovery_method="native_status")
    await monitor._announce_done(oven, "oven")

    guessed = monitor._SensorState(
        "sensor.unknown_power", "Unknown", monitor.ApplianceType.WASHER,
        discovery_method="fingerprint:500W")
    await monitor._announce_done(guessed, "washer")
