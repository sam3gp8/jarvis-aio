"""Tests for biometric/wellbeing context (v6.63.0). Covers entity discovery and
classification across wearables, the sleep signal that enriches sleep detection,
opt-in gating, and — most importantly — that the output is non-clinical: plain
readings with a disclaimer, no thresholds, diagnoses, or health alarms."""
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def bio(load, monkeypatch):
    m = load("biometrics")
    return m


def _cfg_map(mapping):
    return lambda k, d=None: mapping.get(k, d)


class _State:
    def __init__(self, entity_id, state, unit="", device_class="", name=""):
        self.entity_id = entity_id
        self.state = state
        self.attributes = {
            "unit_of_measurement": unit,
            "device_class": device_class,
            "friendly_name": name or entity_id,
        }


class _Hass:
    def __init__(self, sensors=None, binary=None):
        self._sensors = sensors or []
        self._binary = binary or []
    @property
    def states(self):
        outer = self
        class _S:
            def async_all(self, domain):
                return outer._sensors if domain == "sensor" else outer._binary
        return _S()


# ── opt-in gating ────────────────────────────────────────────────────────────

def test_disabled_by_default(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio.is_enabled() is False


def test_enabled_when_configured(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    assert bio.is_enabled() is True


def test_wellbeing_empty_when_disabled(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    res = bio.wellbeing_context(_Hass())
    assert res["available"] is False


def test_sleep_signal_none_when_disabled(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio.sleep_signal(_Hass()) is None


# ── discovery & classification ───────────────────────────────────────────────

def test_classify_heart_rate_by_name(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio._classify("sensor.watch_heart_rate", "Heart Rate", "bpm", "") == "heart_rate"


def test_classify_heart_rate_by_unit(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio._classify("sensor.hr", "HR", "bpm", "") == "heart_rate"


def test_classify_spo2(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio._classify("sensor.blood_oxygen", "Blood Oxygen", "%", "") == "spo2"


def test_classify_steps(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio._classify("sensor.daily_steps", "Steps", "steps", "") == "steps"


def test_explicit_mapping_overrides(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({
        "biometric_entities": {"heart_rate": "sensor.custom_thing"}}))
    assert bio._classify("sensor.custom_thing", "Whatever", "", "") == "heart_rate"


def test_non_biometric_ignored(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    assert bio._classify("sensor.living_room_temp", "Living Room Temperature", "°C", "temperature") is None


def test_discover_groups_by_kind(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({}))
    hass = _Hass(sensors=[
        _State("sensor.watch_hr", "62", "bpm", name="Heart Rate"),
        _State("sensor.daily_steps", "8400", "steps", name="Steps"),
        _State("sensor.living_room_temp", "21", "°C", "temperature", "Living Room"),
    ])
    found = bio.discover(hass)
    assert "heart_rate" in found and "steps" in found
    assert "temperature" not in found or all(
        "living" not in e["entity"] for e in found.get("temperature", []))


# ── sleep signal (enriches sleep_detection) ──────────────────────────────────

def test_sleep_signal_asleep(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    hass = _Hass(sensors=[_State("sensor.sleep_stage", "deep", name="Sleep Stage")])
    assert bio.sleep_signal(hass) is True


def test_sleep_signal_awake(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    hass = _Hass(sensors=[_State("sensor.sleep_state", "awake", name="Sleep State")])
    assert bio.sleep_signal(hass) is False


def test_sleep_signal_binary_asleep(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    hass = _Hass(binary=[_State("binary_sensor.in_bed", "on", name="In Bed")])
    assert bio.sleep_signal(hass) is True


def test_sleep_signal_none_without_sensor(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    hass = _Hass(sensors=[_State("sensor.watch_hr", "62", "bpm", name="Heart Rate")])
    assert bio.sleep_signal(hass) is None      # no sleep entity → no signal


def test_sleep_signal_ignores_unknown_value(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    hass = _Hass(sensors=[_State("sensor.sleep_stage", "unavailable", name="Sleep Stage")])
    assert bio.sleep_signal(hass) is None


# ── non-medical output guarantee ─────────────────────────────────────────────

def test_wellbeing_output_is_non_clinical(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    hass = _Hass(sensors=[
        _State("sensor.watch_hr", "58", "bpm", name="Heart Rate"),
        _State("sensor.sleep_stage", "light_sleep", name="Sleep Stage"),
    ])
    res = bio.wellbeing_context(hass)
    assert res["available"] is True
    # carries an explicit non-medical disclaimer
    assert "not medical" in res["disclaimer"].lower()
    # summary states readings plainly, with no diagnostic/alarm language
    low = res["summary"].lower()
    for banned in ("abnormal", "danger", "warning", "risk", "concerning",
                   "high", "low", "critical", "arrhythmia", "diagnos"):
        assert banned not in low
    # readings are just value+unit, no interpretation field
    assert res["readings"]["heart_rate"]["value"] == "58"


def test_wellbeing_no_entities_message(bio, monkeypatch):
    monkeypatch.setattr(bio, "_cfg", _cfg_map({"biometrics_enabled": True}))
    res = bio.wellbeing_context(_Hass())
    assert res["available"] is False
    assert "wearable" in res["summary"].lower()


@pytest.mark.asyncio
async def test_ws_biometrics_call_uses_executor_wrapped_callable(load, fake_hass, monkeypatch):
    components = sys.modules.setdefault("homeassistant.components", type(sys)("homeassistant.components"))
    ws_api = type(sys)("homeassistant.components.websocket_api")
    ws_api.ActiveConnection = object
    ws_api.async_register_command = lambda *a, **k: None
    ws_api.websocket_command = lambda *a, **k: (lambda func: func)
    ws_api.async_response = lambda func: func
    sys.modules["homeassistant.components.websocket_api"] = ws_api
    components.websocket_api = ws_api
    sys.modules.pop("jc.websocket", None)
    ws = load("websocket")
    bio = load("biometrics")
    jarvis_config = load("jarvis_config")
    made = {}

    async def fake_add_executor_job(func, *args):
        # Home Assistant rejects keyword args for async_add_executor_job; the
        # worker must receive a callable and positional state snapshot.
        made["func"] = func
        made["args"] = args
        return func(*args)

    fake_hass.async_add_executor_job = fake_add_executor_job
    monkeypatch.setattr(jarvis_config, "get", lambda *a, **k: False)
    monkeypatch.setattr(bio, "discover", lambda hass=None, states=None: {"heart_rate": []})

    await ws.ws_biometrics(
        fake_hass,
        SimpleNamespace(
            send_result=lambda *_args, **_kwargs: None,
            send_error=lambda *_args, **_kwargs: None,
        ),
        {"id": 1, "action": "status"},
    )

    assert callable(made["func"])
    assert made["args"][0] is None
    assert made["args"][1] == []


@pytest.mark.asyncio
async def test_observer_activity_log_uses_zero_arg_executor_callable(load, fake_hass, monkeypatch):
    observer = load("observer")
    db = load("database")
    saved = []

    async def fake_add_executor_job(func, *args):
        # Reproduce HA's API contract: executor jobs accept only positional args,
        # so the worker must get a zero-argument callable.
        assert not args
        return func()

    async def fake_classify(*a, **k):
        return {"worth_considering": False}

    fake_hass.async_add_executor_job = fake_add_executor_job
    observer._STATE.hass = fake_hass
    observer._STATE.config = {}
    monkeypatch.setattr(observer.classifier, "classify", fake_classify)
    monkeypatch.setattr(observer.audio_routing, "entity_area", lambda *_a, **_k: "kitchen")
    monkeypatch.setattr(observer, "get_recent_context", lambda *_a, **_k: "quiet")
    monkeypatch.setattr(db, "save_activity", lambda **kwargs: saved.append(kwargs))

    event = SimpleNamespace(data={
        "entity_id": "binary_sensor.front_door",
        "old_state": SimpleNamespace(state="closed", attributes={}),
        "new_state": SimpleNamespace(
            state="open",
            attributes={"friendly_name": "Front Door", "device_class": "door"},
        ),
    })

    await observer._process_event(event)

    assert saved and saved[0]["entity_id"] == "binary_sensor.front_door"


# ── agent tool registration ──────────────────────────────────────────────────

def test_wellbeing_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "wellbeing_context" in names
    assert "wellbeing_context" in agent._TOOL_MAP
