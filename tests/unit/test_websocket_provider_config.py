from __future__ import annotations

import importlib
import importlib.util
import datetime
import pathlib
import sys
import types

import pytest


def _load_websocket_module():
    comp = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
    sys.modules.pop("jc.websocket", None)

    ws_api = types.ModuleType("homeassistant.components.websocket_api")
    ws_api.websocket_command = lambda schema: (lambda func: func)
    ws_api.async_response = lambda func: func
    ws_api.async_register_command = lambda hass, func: None
    sys.modules["homeassistant.components.websocket_api"] = ws_api
    import homeassistant.components as ha_components

    ha_components.websocket_api = ws_api

    spec = importlib.util.spec_from_file_location("jc.websocket", comp / "websocket.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jc.websocket"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Conn:
    def __init__(self):
        self.result = None
        self.error = None

    def send_result(self, msg_id, payload):
        self.result = (msg_id, payload)

    def send_error(self, msg_id, code, message):
        self.error = (msg_id, code, message)


def _install_jc_module(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, f"jc.{name}", module)
    monkeypatch.setattr(sys.modules["jc"], name, module, raising=False)
    return module


async def test_configured_providers_require_custom_endpoint(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    ha_secrets = importlib.import_module("jc.ha_secrets")
    values = {
        "llm_provider": "custom",
        "custom_base_url": "",
        "ollama_base_url": "",
        "llm_base_url": "",
    }
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))

    async def _no_key(hass, provider):
        return ""

    monkeypatch.setattr(ha_secrets, "async_get_provider_key", _no_key)

    configured = await websocket._configured_providers(fake_hass, object())
    assert "custom" not in configured


async def test_configured_providers_allow_selected_ollama_default_endpoint(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    ha_secrets = importlib.import_module("jc.ha_secrets")
    values = {
        "llm_provider": "ollama",
        "ollama_base_url": "",
        "llm_base_url": "",
    }
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))

    async def _no_key(hass, provider):
        return ""

    monkeypatch.setattr(ha_secrets, "async_get_provider_key", _no_key)

    configured = await websocket._configured_providers(fake_hass, object())
    assert "ollama" in configured


async def test_configured_providers_do_not_leak_legacy_url_to_unselected_ollama(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    ha_secrets = importlib.import_module("jc.ha_secrets")
    values = {
        "llm_provider": "custom",
        "custom_base_url": "http://gpu.local:11434/v1",
        "ollama_base_url": "",
        "llm_base_url": "http://gpu.local:11434/v1",
    }
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))

    async def _no_key(hass, provider):
        return ""

    monkeypatch.setattr(ha_secrets, "async_get_provider_key", _no_key)

    configured = await websocket._configured_providers(fake_hass, object())
    assert "ollama" not in configured


async def test_configured_providers_do_not_leak_legacy_url_to_unselected_custom(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    ha_secrets = importlib.import_module("jc.ha_secrets")
    values = {
        "llm_provider": "ollama",
        "custom_base_url": "",
        "ollama_base_url": "http://gpu.local:11434/v1",
        "llm_base_url": "http://gpu.local:11434/v1",
    }
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))

    async def _no_key(hass, provider):
        return ""

    monkeypatch.setattr(ha_secrets, "async_get_provider_key", _no_key)

    configured = await websocket._configured_providers(fake_hass, object())
    assert "custom" not in configured


async def test_fetch_models_uses_resolved_api_key_in_auth_header(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    seen = {}

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return ""

        async def json(self):
            return {"data": [{"id": "gpt-4o-mini"}]}

    class _Session:
        def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            return _Response()

    class _Timeout:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.aiohttp_client"],
        "async_get_clientsession",
        lambda hass: _Session(),
    )
    sys.modules["async_timeout"] = types.SimpleNamespace(timeout=lambda seconds: _Timeout())

    models = await websocket._fetch_models(fake_hass, "openai", "sk-openai", "")

    assert models == ["gpt-4o-mini"]
    assert seen["url"] == "https://api.openai.com/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer " + "sk-openai"


async def test_fetch_models_gemini_uses_genai_sdk(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    captured = {}

    class _Models:
        def list(self):
            return [
                types.SimpleNamespace(
                    name="models/gemini-3.1-flash-lite",
                    supported_actions=["generateContent"],
                ),
                types.SimpleNamespace(
                    name="models/gemini-embedding-001",
                    supported_actions=["embedContent"],
                ),
            ]

    def _client(**kwargs):
        captured["client"] = kwargs
        return types.SimpleNamespace(models=_Models())

    genai = types.SimpleNamespace(Client=_client)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    models = await websocket._fetch_models(fake_hass, "gemini", "AIza-key", "")

    assert captured["client"] == {"api_key": "AIza-key"}
    assert models == ["gemini-3.1-flash-lite"]


async def test_fetch_models_custom_uses_models_endpoint_without_v1_suffix(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    seen = {}

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return ""

        async def json(self):
            return {"data": [{"id": "custom-model"}]}

    class _Session:
        def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            return _Response()

    class _Timeout:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.aiohttp_client"],
        "async_get_clientsession",
        lambda hass: _Session(),
    )
    sys.modules["async_timeout"] = types.SimpleNamespace(timeout=lambda seconds: _Timeout())

    models = await websocket._fetch_models(fake_hass, "custom", "sk-custom", "https://host/openai")

    assert models == ["custom-model"]
    assert seen["url"] == "https://host/openai/models"
    assert seen["headers"]["Authorization"] == "Bearer " + "sk-custom"


async def test_ws_update_config_refreshes_live_clients(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    jarvis_config = importlib.import_module("jc.jarvis_config")
    observer = importlib.import_module("jc.observer")
    llm_provider = importlib.import_module("jc.llm_provider")
    entry = type("Entry", (), {"entry_id": "entry-1", "data": {}, "options": {}})()
    fake_hass.config_entries.async_entries = lambda domain: [entry]
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: {}}}
    conn = _Conn()
    persisted = []
    refreshed = {"main": 0, "observer": 0}

    monkeypatch.setattr(jarvis_config, "set_many", lambda updates: persisted.append(dict(updates)))
    async def _refresh_main(hass, entry):
        refreshed["main"] += 1
    async def _refresh_observer(hass, updates=None):
        refreshed["observer"] += 1
    monkeypatch.setattr(llm_provider, "async_refresh_main_client", _refresh_main)
    monkeypatch.setattr(observer, "is_running", lambda: True)
    monkeypatch.setattr(observer, "refresh_tier_providers", _refresh_observer)

    await websocket.ws_update_config(fake_hass, conn, {"id": 7, "key": "llm_provider", "value": "openai"})

    assert conn.error is None
    assert conn.result == (7, {"key": "llm_provider", "value": "openai"})
    assert fake_hass.data[websocket.DOMAIN][entry.entry_id]["runtime_config"]["llm_provider"] == "openai"
    assert persisted == [{"llm_provider": "openai"}]
    assert refreshed == {"main": 1, "observer": 1}


async def test_ws_update_config_marks_review_provider_as_explicit_opt_in(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    jarvis_config = importlib.import_module("jc.jarvis_config")
    observer = importlib.import_module("jc.observer")
    entry = type("Entry", (), {"entry_id": "entry-1", "data": {}, "options": {}})()
    fake_hass.config_entries.async_entries = lambda domain: [entry]
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: {}}}
    conn = _Conn()
    persisted = []
    refreshes = []

    monkeypatch.setattr(jarvis_config, "set_many", lambda updates: persisted.append(dict(updates)))
    monkeypatch.setattr(observer, "is_running", lambda: True)
    async def _refresh_observer(hass, updates=None):
        refreshes.append(dict(updates or {}))
    monkeypatch.setattr(observer, "refresh_tier_providers", _refresh_observer)

    await websocket.ws_update_config(fake_hass, conn, {"id": 8, "key": "review_provider", "value": "gemini"})

    runtime = fake_hass.data[websocket.DOMAIN][entry.entry_id]["runtime_config"]
    assert conn.error is None
    assert conn.result == (8, {"key": "review_provider", "value": "gemini"})
    assert runtime["review_provider"] == "gemini"
    assert runtime["review_enabled"] is True
    assert persisted == [{"review_provider": "gemini", "review_enabled": True}]
    assert refreshes == [{"review_provider": "gemini", "review_enabled": True}]


def test_get_observer_stats_aggregates_activity_and_cognition(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    import time

    state = types.SimpleNamespace(
        running=True,
        classifier_timestamps=[time.time() - 10, time.time() - 4000],
        hass=fake_hass,
    )
    observer = types.SimpleNamespace(
        _STATE=state,
        _effective_rate_limit=lambda: 12,
        _cognition_enabled=lambda: False,
        _cognition_threshold=lambda: 0.75,
    )
    cognition = types.SimpleNamespace(
        stats=lambda: {
            "entities_tracked": 4,
            "events_seen": 9,
            "anomalies_escalated": 2,
            "predictable": 3,
            "routines": 5,
            "presence_routines": 1,
        },
        presence_status=lambda hass: ["person.alex"],
    )
    monkeypatch.setitem(sys.modules, "jc.observer", observer)
    monkeypatch.setattr(sys.modules["jc"], "observer", observer, raising=False)
    monkeypatch.setitem(sys.modules, "jc.cognition", cognition)
    monkeypatch.setattr(sys.modules["jc"], "cognition", cognition, raising=False)

    recent = [
        {"was_spoken": True, "message": "event flagged", "source": "observer"},
        {"was_spoken": False, "message": "not worth announcing"},
        {"was_spoken": True, "message": "normal event"},
    ]
    result = websocket._get_observer_stats(
        recent,
        {"learned_patterns": 7, "cloud_calls": 2},
    )

    assert result["running"] is True
    assert result["calls_last_hour"] == 1
    assert result["rate_limit"] == 12
    assert (result["events_24h"], result["flagged_24h"]) == (3, 1)
    assert (result["dropped_24h"], result["spoken_24h"]) == (1, 2)
    assert result["cognition_enabled"] is False
    assert result["cognition_threshold"] == 0.75
    assert result["cog_entities"] == 4
    assert result["cog_presence"] == 1
    assert result["presence"] == ["person.alex"]
    assert result["learned_patterns"] == 7
    assert result["cloud_calls"] == 2


def test_get_observer_stats_loads_recent_activity_when_not_supplied(monkeypatch):
    websocket = _load_websocket_module()
    calls = []
    recent = [{"was_spoken": True, "message": "flagged event"}]
    database = types.ModuleType("jc.database")
    database.get_recent_activity = lambda **kwargs: calls.append(kwargs) or recent
    observer = types.SimpleNamespace(
        _STATE=types.SimpleNamespace(running=False, hass=None),
        _effective_rate_limit=lambda: 30,
        _cognition_enabled=lambda: True,
        _cognition_threshold=lambda: 0.6,
    )
    cognition = types.SimpleNamespace(
        stats=lambda: {},
        presence_status=lambda hass: [],
    )
    monkeypatch.setitem(sys.modules, "jc.database", database)
    monkeypatch.setitem(sys.modules, "jc.observer", observer)
    monkeypatch.setattr(sys.modules["jc"], "observer", observer, raising=False)
    monkeypatch.setitem(sys.modules, "jc.cognition", cognition)
    monkeypatch.setattr(sys.modules["jc"], "cognition", cognition, raising=False)

    result = websocket._get_observer_stats()

    assert calls == [{"hours": 24, "limit": 500}]
    assert result["events_24h"] == 1
    assert result["flagged_24h"] == 1
    assert result["spoken_24h"] == 1


async def test_async_get_observer_stats_fetches_activity_and_passes_reasoning(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    activity = [{"message": "recent event"}]
    reasoning = {"learned_patterns": 3}
    calls = []

    def _get_recent_activity(hours, limit):
        calls.append((hours, limit))
        return activity

    database = types.ModuleType("jc.database")
    database.get_recent_activity = _get_recent_activity
    monkeypatch.setitem(sys.modules, "jc.database", database)
    monkeypatch.setattr(websocket, "_get_reasoning_stats", lambda: reasoning)
    monkeypatch.setattr(
        websocket,
        "_get_observer_stats",
        lambda recent, reasoning_stats: (recent, reasoning_stats),
    )

    result = await websocket._async_get_observer_stats(fake_hass)

    assert calls == [(24, 500)]
    assert result == (activity, reasoning)


async def test_get_area_sparklines_parses_and_downsamples_recorder_states(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    calls = {}
    raw = {
        "sensor.room_temperature": [
            {"state": "18.5"},
            types.SimpleNamespace(state="19.0"),
            {"state": "unknown"},
            {"state": "21.5"},
            {"state": "22.0"},
        ],
        "sensor.room_humidity": [
            {"state": "45"},
            {"state": "46.5"},
        ],
    }

    class _RecorderInstance:
        async def async_add_executor_job(self, func):
            return func()

    def _get_significant_states(hass, start, end, entity_ids, **kwargs):
        calls.update({
            "start": start,
            "end": end,
            "entity_ids": entity_ids,
            "kwargs": kwargs,
        })
        return raw

    recorder = types.ModuleType("homeassistant.components.recorder")
    recorder.get_instance = lambda hass: _RecorderInstance()
    recorder.history = types.SimpleNamespace(get_significant_states=_get_significant_states)
    components = sys.modules["homeassistant.components"]
    monkeypatch.setattr(components, "__path__", [], raising=False)
    monkeypatch.setattr(components, "recorder", recorder, raising=False)
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", recorder)

    result = await websocket._get_area_sparklines(
        fake_hass,
        {
            "living": {
                "temp": "sensor.room_temperature",
                "humidity": "sensor.room_humidity",
            },
            "empty": {"temp": "sensor.no_history", "humidity": None},
        },
        hours=3,
        points=2,
    )

    assert result == {
        "living": {"temp": [18.5, 21.5], "humidity": [45.0, 46.5]},
    }
    assert calls["entity_ids"] == [
        "sensor.no_history",
        "sensor.room_humidity",
        "sensor.room_temperature",
    ]
    assert calls["end"] - calls["start"] == datetime.timedelta(hours=3)
    assert calls["kwargs"] == {"minimal_response": True, "no_attributes": True}


async def test_get_area_sparklines_returns_empty_without_entities(fake_hass):
    websocket = _load_websocket_module()

    result = await websocket._get_area_sparklines(
        fake_hass, {"living": {"temp": None, "humidity": None}}
    )

    assert result == {}


async def test_get_area_sparklines_handles_recorder_failure(fake_hass, monkeypatch):
    websocket = _load_websocket_module()

    class _RecorderInstance:
        async def async_add_executor_job(self, func):
            raise RuntimeError("recorder unavailable")

    recorder = types.ModuleType("homeassistant.components.recorder")
    recorder.get_instance = lambda hass: _RecorderInstance()
    recorder.history = types.SimpleNamespace(get_significant_states=lambda *a, **k: {})
    components = sys.modules["homeassistant.components"]
    monkeypatch.setattr(components, "__path__", [], raising=False)
    monkeypatch.setattr(components, "recorder", recorder, raising=False)
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", recorder)

    result = await websocket._get_area_sparklines(
        fake_hass, {"living": {"temp": "sensor.room_temperature"}}
    )

    assert result == {}


async def test_ws_get_activity_log_formats_entries_and_falls_back_on_bad_timestamp(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    calls = []
    entries = [
        {
            "timestamp": "2026-04-23T05:30:00",
            "urgency": "high",
            "entity_id": "binary_sensor.front_door",
            "message": "Front door opened",
            "source": "observer",
        },
        {
            "timestamp": "bad timestamp",
            "source": "briefing",
            "message": "Morning summary",
        },
    ]
    database = types.ModuleType("jc.database")

    def _get_recent_activity(*, hours, limit):
        calls.append((hours, limit))
        return entries

    database.get_recent_activity = _get_recent_activity
    monkeypatch.setitem(sys.modules, "jc.database", database)
    connection = _Conn()

    await websocket.ws_get_activity_log(
        fake_hass, connection, {"id": 12, "hours": 6, "limit": 8}
    )

    assert calls == [(6, 8)]
    assert connection.error is None
    assert connection.result == (12, {
        "entries": [
            {
                "ts": "05:30",
                "urgency": "high",
                "tag": "FRONT_DOOR",
                "msg": "Front door opened",
                "source": "observer",
            },
            {
                "ts": "bad t",
                "urgency": "low",
                "tag": "BRIEFING",
                "msg": "Morning summary",
                "source": "briefing",
            },
        ],
    })


async def test_ws_get_activity_log_reports_database_error(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    database = types.ModuleType("jc.database")

    def _raise(*args, **kwargs):
        raise RuntimeError("activity store unavailable")

    database.get_recent_activity = _raise
    monkeypatch.setitem(sys.modules, "jc.database", database)
    connection = _Conn()

    await websocket.ws_get_activity_log(
        fake_hass, connection, {"id": 13, "hours": 24, "limit": 50}
    )

    assert connection.result is None
    assert connection.error == (
        13, "activity_log_failed", "activity store unavailable"
    )


def test_get_runtime_json_obeys_config_precedence_and_json_fallback(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    entry = types.SimpleNamespace(entry_id="entry-1")
    fake_hass.data = {
        websocket.DOMAIN: {
            entry.entry_id: {
                "runtime_config": {
                    "runtime_json": '{"source": "runtime"}',
                    "runtime_dict": {"source": "runtime"},
                    "invalid_runtime": "{",
                },
            },
        },
    }
    persistent = {
        "persistent_json": '{"source": "persistent"}',
        "invalid_persistent": "not-json",
    }
    options = {"options_json": '["entry option"]', "invalid_options": "["}
    jarvis_config = types.ModuleType("jc.jarvis_config")
    jarvis_config.get = lambda key: persistent.get(key)
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", jarvis_config)
    monkeypatch.setattr(sys.modules["jc"], "jarvis_config", jarvis_config, raising=False)
    monkeypatch.setattr(
        websocket,
        "_entry_opt",
        lambda entry, key, default=None: options.get(key, default),
    )

    assert websocket._get_runtime_json(fake_hass, entry, "runtime_json", None) == {
        "source": "runtime",
    }
    assert websocket._get_runtime_json(fake_hass, entry, "runtime_dict", None) == {
        "source": "runtime",
    }
    assert websocket._get_runtime_json(fake_hass, entry, "persistent_json", None) == {
        "source": "persistent",
    }
    assert websocket._get_runtime_json(fake_hass, entry, "options_json", None) == [
        "entry option",
    ]
    assert websocket._get_runtime_json(fake_hass, entry, "invalid_persistent", None) == "not-json"
    assert websocket._get_runtime_json(fake_hass, entry, "invalid_options", "fallback") == "fallback"
    assert websocket._get_runtime_json(fake_hass, entry, "invalid_runtime", "fallback") == "fallback"
    assert websocket._get_runtime_json(fake_hass, None, "missing", "fallback") == "fallback"


def test_get_runtime_str_obeys_runtime_persistent_and_options_precedence(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    entry = types.SimpleNamespace(entry_id="entry-2")
    fake_hass.data = {
        websocket.DOMAIN: {
            entry.entry_id: {"runtime_config": {"value": 0}},
        },
    }
    persistent = {"persistent_value": 12}
    options = {"option_value": False}
    jarvis_config = types.ModuleType("jc.jarvis_config")
    jarvis_config.get = lambda key: persistent.get(key)
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", jarvis_config)
    monkeypatch.setattr(sys.modules["jc"], "jarvis_config", jarvis_config, raising=False)
    monkeypatch.setattr(
        websocket,
        "_entry_opt",
        lambda entry, key, default=None: options.get(key, default),
    )

    assert websocket._get_runtime_str(fake_hass, entry, "value", "default") == "0"
    assert websocket._get_runtime_str(fake_hass, entry, "persistent_value", "default") == "12"
    assert websocket._get_runtime_str(fake_hass, entry, "option_value", "default") == "False"
    assert websocket._get_runtime_str(fake_hass, None, "missing", "default") == "default"


def test_format_uptime_handles_seconds_minutes_hours_and_days():
    websocket = _load_websocket_module()

    assert websocket._format_uptime(59.9) == "59s"
    assert websocket._format_uptime(60) == "1m 0s"
    assert websocket._format_uptime(3600) == "1h 0m"
    assert websocket._format_uptime(86400) == "1d 0h"


def test_downsample_preserves_small_inputs_and_evenly_selects_large_inputs():
    websocket = _load_websocket_module()
    values = [float(value) for value in range(10)]

    assert websocket._downsample(values[:2], 2) == values[:2]
    assert websocket._downsample(values, 0) == values
    assert websocket._downsample(values, 3) == [0.0, 3.0, 6.0]


async def test_ws_get_panel_data_returns_status_and_full_config_payload(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    entry = types.SimpleNamespace(entry_id="panel-entry", data={}, options={})
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: {"observer_running": False}}}
    monkeypatch.setattr(websocket, "_get_entry", lambda hass: entry)
    monkeypatch.setattr(websocket, "_entry_opt", lambda entry, key, default=None: {
        websocket.CONF_BEDROOM_AREAS: ["bedroom"],
        websocket.CONF_BROADCAST_GROUP: "media_player.house",
        websocket.CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
    }.get(key, default))
    runtime = {
        websocket.CONF_OBSERVER_ENABLED: True,
        "announcements_enabled": True,
        "sentinel_enabled": False,
        "cognition_enabled": False,
        "home_context_max_entities": 0,
    }
    monkeypatch.setattr(
        websocket,
        "_runtime_opt",
        lambda hass, entry, key, default=None: runtime.get(key, default),
    )
    monkeypatch.setattr(websocket, "_get_runtime_json", lambda hass, entry, key, default: default)
    monkeypatch.setattr(websocket, "_get_onboarding_state", lambda hass, entry, notify: {"show": False})
    monkeypatch.setattr(websocket, "_get_disabled_rules", lambda hass, entry: [])
    monkeypatch.setattr(websocket, "_available_labels", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_sentinel_rules", lambda: [])
    monkeypatch.setattr(websocket, "_get_door_states", lambda hass: {})
    monkeypatch.setattr(websocket, "_get_lockdown_status", lambda: {"active": False})
    monkeypatch.setattr(websocket, "_get_intrusion_status", lambda: {"active": False})
    monkeypatch.setattr(websocket, "_get_appliance_status", lambda: {"running": False})
    monkeypatch.setattr(websocket, "_get_satellites", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_cast_devices", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_cameras", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_camera_overrides", lambda: {})
    monkeypatch.setattr(websocket, "_get_camera_names", lambda: {})
    monkeypatch.setattr(websocket, "_get_announcements_today", lambda: 4)
    monkeypatch.setattr(websocket, "_get_doorbell_training", lambda: {"stats": {"total": 2}})
    monkeypatch.setattr(websocket, "_get_knowledge_stats", lambda: {"total": 3})
    monkeypatch.setattr(websocket, "_get_suggestions", lambda: [{"id": 1}])
    monkeypatch.setattr(websocket, "_get_goals", lambda: [{"id": 2}])
    monkeypatch.setattr(websocket, "_async_get_observer_stats", lambda hass: _resolved({"events_24h": 5}))
    monkeypatch.setattr(websocket, "_get_memory_stats", lambda: {"total_memories": 6})
    monkeypatch.setattr(websocket, "_configured_providers", lambda hass, entry: _resolved(["custom"]))
    monkeypatch.setattr(websocket.sleep_detection, "is_sleeping", lambda *a, **k: (True, "quiet hours"))
    monkeypatch.setattr(websocket, "_dominant_area", lambda hass: None)
    monkeypatch.setattr(websocket.audio_routing, "anyone_home", lambda hass: False)

    class _Services:
        @staticmethod
        def async_services():
            return {"notify": {"mobile_app_phone": None}}

    fake_hass._services = _Services()
    connection = _Conn()

    await websocket.ws_get_panel_data(fake_hass, connection, {"id": 21})

    assert connection.error is None
    assert connection.result[0] == 21
    payload = connection.result[1]
    assert payload["status"] == {
        "observer": {"state": "READY", "level": "warn"},
        "sleep": {"state": "ASLEEP", "level": "warn"},
        "llm_providers": {"state": "1 READY", "level": "live"},
        "broadcast": {"state": "ONLINE", "level": "live"},
        "notify": {"state": "READY", "level": "live"},
        "satellites": {"state": "NONE", "level": "off"},
    }
    assert payload["dominant"]["name"] == "AWAY"
    assert payload["sleep_reason"] == "quiet hours"
    assert payload["meta"]["bedrooms"] == 1
    assert payload["meta"]["announcements_today"] == 4
    assert payload["knowledge"] == {"total": 3}
    assert payload["suggestions"] == [{"id": 1}]
    assert payload["goals"] == [{"id": 2}]
    assert payload["config"]["observer_enabled"] is True
    assert payload["config"]["announcements_enabled"] is True
    assert payload["config"]["sentinel_enabled"] is False
    assert payload["config"]["home_context_max_entities"] == 0
    assert payload["config"]["notify_services_available"] == ["notify.mobile_app_phone"]


async def test_ws_get_panel_data_populates_area_and_dominant_room(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    area_id = "living-room"
    monkeypatch.setattr(websocket, "_get_entry", lambda hass: None)
    monkeypatch.setattr(websocket, "_entry_opt", lambda entry, key, default=None: default)
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: default)
    monkeypatch.setattr(websocket, "_get_runtime_json", lambda hass, entry, key, default: default)
    monkeypatch.setattr(websocket, "_get_onboarding_state", lambda *args: {"show": False})
    monkeypatch.setattr(websocket, "_get_disabled_rules", lambda *args: [])
    monkeypatch.setattr(websocket, "_available_labels", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_sentinel_rules", lambda: [])
    monkeypatch.setattr(websocket, "_get_door_states", lambda hass: {})
    monkeypatch.setattr(websocket, "_get_lockdown_status", lambda: {"active": False})
    monkeypatch.setattr(websocket, "_get_intrusion_status", lambda: {"active": False})
    monkeypatch.setattr(websocket, "_get_appliance_status", lambda: {"running": False})
    monkeypatch.setattr(websocket, "_get_satellites", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_cast_devices", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_cameras", lambda hass: [])
    monkeypatch.setattr(websocket, "_get_camera_overrides", lambda: {})
    monkeypatch.setattr(websocket, "_get_camera_names", lambda: {})
    monkeypatch.setattr(websocket, "_get_announcements_today", lambda: 0)
    monkeypatch.setattr(websocket, "_get_doorbell_training", lambda: {})
    monkeypatch.setattr(websocket, "_get_knowledge_stats", lambda: {})
    monkeypatch.setattr(websocket, "_get_suggestions", lambda: [])
    monkeypatch.setattr(websocket, "_get_goals", lambda: [])
    monkeypatch.setattr(websocket, "_async_get_observer_stats", lambda hass: _resolved({}))
    monkeypatch.setattr(websocket, "_get_memory_stats", lambda: {})
    monkeypatch.setattr(websocket, "_configured_providers", lambda hass, entry: _resolved(["groq"]))
    monkeypatch.setattr(websocket.sleep_detection, "is_sleeping", lambda *args, **kwargs: (False, ""))
    monkeypatch.setattr(websocket, "_all_areas_with_anything", lambda hass: [area_id])
    monkeypatch.setattr(websocket, "_area_capabilities", lambda hass, aid: ["light", "mmwave"])
    monkeypatch.setattr(websocket.audio_routing, "is_area_occupied", lambda hass, aid: True)
    monkeypatch.setattr(websocket, "_area_light_state", lambda hass, aid: (2, 3))
    readings = {"temp": "21°C", "humidity": "50%", "last_motion_seconds": 90, "lights": "2/3"}
    monkeypatch.setattr(websocket, "_area_live_readings", lambda hass, aid: readings)
    monkeypatch.setattr(websocket, "_area_temp_humidity_entities", lambda hass, aid: ("sensor.t", "sensor.h"))
    monkeypatch.setattr(websocket, "_area_name", lambda hass, aid: "Living Room")
    monkeypatch.setattr(websocket, "_dominant_area", lambda hass: area_id)
    monkeypatch.setattr(websocket.audio_routing, "satellites_in_area", lambda hass, aid: ["assist_satellite.living"])
    fake_hass.states.set("assist_satellite.ready", "idle")
    connection = _Conn()

    await websocket.ws_get_panel_data(fake_hass, connection, {"id": 22})

    assert connection.error is None
    payload = connection.result[1]
    assert payload["areas"] == [{
        "id": area_id, "name": "Living Room", "caps": ["light", "mmwave"],
        "active": True, "bedroom": False, "lights_on": 2, "lights_total": 3,
        "temp": "21°C", "humidity": "50%", "temp_entity": "sensor.t",
        "humidity_entity": "sensor.h", "last_motion": "1m",
    }]
    assert payload["dominant"] == {
        "area_id": area_id, "name": "Living Room", "subtitle": "Occupied · 1m",
        "coord": "#living-r", "temp": "21°C", "humidity": "50%", "lights": "2/3",
        "satellite": "living", "last_motion": "1m",
    }
    assert payload["status"]["satellites"] == {"state": "1 / 1", "level": "live"}


async def _resolved(value):
    return value


def test_area_reading_and_capability_helpers_cover_domains(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    entity_ids = [
        "assist_satellite.kitchen",
        "media_player.kitchen",
        "camera.kitchen",
        "binary_sensor.motion",
        "binary_sensor.door",
        "binary_sensor.leak",
        "binary_sensor.smoke",
        "light.ceiling",
        "switch.fan",
        "lock.front_door",
        "climate.kitchen",
        "sensor.temperature",
        "sensor.humidity",
    ]
    monkeypatch.setattr(websocket, "_entities_in_area", lambda hass, area: entity_ids)
    for entity_id in entity_ids:
        attributes = {}
        state = "off"
        if entity_id == "binary_sensor.motion":
            attributes["device_class"] = "motion"
        elif entity_id == "binary_sensor.door":
            attributes["device_class"] = "door"
        elif entity_id == "binary_sensor.leak":
            attributes["device_class"] = "moisture"
        elif entity_id == "binary_sensor.smoke":
            attributes["device_class"] = "smoke"
        elif entity_id == "sensor.temperature":
            attributes.update(device_class="temperature", unit_of_measurement="°C")
            state = "21.6"
        elif entity_id == "sensor.humidity":
            attributes["device_class"] = "humidity"
            state = "48.2"
        elif entity_id == "light.ceiling":
            state = "on"
        fake_hass.states.set(entity_id, state, **attributes)
    states = {
        entity_id: types.SimpleNamespace(
            state=state.state,
            attributes=state.attributes,
            last_changed=datetime.datetime.now(datetime.timezone.utc),
        )
        for entity_id, state in fake_hass.states._d.items()
    }
    monkeypatch.setattr(fake_hass.states, "get", states.get)

    assert websocket._area_capabilities(fake_hass, "kitchen") == [
        "sat", "spkr", "mmwave", "cam", "light", "switch", "lock",
        "climate", "door", "leak", "alarm",
    ]
    readings = websocket._area_live_readings(fake_hass, "kitchen")
    assert readings["temp"] == "22°C"
    assert readings["humidity"] == "48%"
    assert readings["lights"] == "ON"
    assert readings["last_motion_seconds"] is not None
    assert websocket._area_light_state(fake_hass, "kitchen") == (1, 1)
    assert websocket._area_temp_humidity_entities(fake_hass, "kitchen") == (
        "sensor.temperature", "sensor.humidity",
    )


def test_simple_websocket_helpers_cover_satellites_cast_devices_and_rules(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    fake_hass.states.set("assist_satellite.available", "idle")
    fake_hass.states.set("assist_satellite.missing", "unavailable")
    fake_hass.states.set("media_player.google_speaker", "playing", friendly_name="Google speaker")
    fake_hass.states.set("media_player.cast", "idle", friendly_name="Speaker", platform="cast")
    fake_hass.states.set("media_player.nest", "idle", friendly_name="Nest Hub")
    fake_hass.states.set("media_player.feature", "idle", supported_features=16384)
    fake_hass.states.set("media_player.hidden", "idle", friendly_name="TV")
    fake_hass.states.set("media_player.unavailable_sonos", "unavailable", friendly_name="Sonos")

    assert websocket._satellite_count(fake_hass) == (1, 2)
    assert websocket._get_cast_devices(fake_hass) == [
        {"entity_id": "media_player.google_speaker", "name": "Google speaker"},
        {"entity_id": "media_player.cast", "name": "Speaker"},
        {"entity_id": "media_player.nest", "name": "Nest Hub"},
        {"entity_id": "media_player.feature", "name": "media_player.feature"},
    ]

    entry = types.SimpleNamespace(entry_id="entry-rules", options={})
    fake_hass.data = {
        websocket.DOMAIN: {
            entry.entry_id: {"runtime_config": {"disabled_sentinel_rules": ["rule_a"]}},
        },
    }
    assert websocket._get_disabled_rules(fake_hass, entry) == ["rule_a"]
    fake_hass.data[websocket.DOMAIN][entry.entry_id]["runtime_config"]["disabled_sentinel_rules"] = "["
    monkeypatch.setattr(websocket, "_entry_opt", lambda entry, key, default=None: "[\"rule_b\"]")
    assert websocket._get_disabled_rules(fake_hass, entry) == []
    assert websocket._get_disabled_rules(fake_hass, None) == []


def test_area_dominance_and_format_helpers(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    assert websocket._format_duration(None) == "—"
    assert websocket._format_duration(59.9) == "59s"
    assert websocket._format_duration(60) == "1m"
    assert websocket._format_duration(3600) == "1h"

    monkeypatch.setattr(websocket, "_area_name", lambda hass, area: {
        "yard": "Backyard", "room_a": "Living room", "room_b": "Bedroom",
    }[area])
    monkeypatch.setattr(
        websocket.audio_routing,
        "currently_occupied_areas",
        lambda hass: ["yard", "room_a", "room_b"],
    )
    monkeypatch.setattr(
        websocket.audio_routing,
        "presence_entities_in_area",
        lambda hass, area: [f"binary_sensor.{area}"],
    )
    for index, area in enumerate(("yard", "room_a", "room_b")):
        fake_hass.states.set(f"binary_sensor.{area}", "on")
    state_data = {
        f"binary_sensor.{area}": types.SimpleNamespace(
            state="on",
            attributes={},
            last_changed=datetime.datetime(
                2026, 1, 1, 0, index, tzinfo=datetime.timezone.utc,
            ),
        )
        for index, area in enumerate(("yard", "room_a", "room_b"))
    }
    monkeypatch.setattr(fake_hass.states, "get", state_data.get)

    assert websocket._is_outdoor_area(fake_hass, "yard") is True
    assert websocket._is_outdoor_area(fake_hass, "room_a") is False
    assert websocket._dominant_area(fake_hass) == "room_b"
    monkeypatch.setattr(websocket.audio_routing, "currently_occupied_areas", lambda hass: [])
    assert websocket._dominant_area(fake_hass) is None


async def test_simple_websocket_commands_return_payloads(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    websocket._DEBUG_LOG.clear()
    websocket._DEBUG_LOG.append({"cat": "TEST", "msg": "entry"})

    await websocket.ws_get_debug_log(fake_hass, connection, {"id": 31})
    assert connection.result == (31, {"entries": [{"cat": "TEST", "msg": "entry"}]})

    monkeypatch.setattr(websocket, "_get_person_routines", lambda: {"alex": [{"id": 1}]})
    await websocket.ws_get_person_routines(fake_hass, connection, {"id": 32})
    assert connection.result == (32, {"routines": {"alex": [{"id": 1}]}})

    monkeypatch.setattr(websocket, "_all_areas_with_anything", lambda hass: ["kitchen"])
    monkeypatch.setattr(websocket, "_area_temp_humidity_entities", lambda hass, area: ("sensor.temp", None))
    monkeypatch.setattr(
        websocket,
        "_get_area_sparklines",
        lambda hass, entity_map: _resolved({"kitchen": {"temp": [21.0]}}),
    )
    await websocket.ws_get_area_sparklines(fake_hass, connection, {"id": 33})
    assert connection.result == (33, {"sparklines": {"kitchen": {"temp": [21.0]}}})


async def test_knowledge_and_cognitive_commands_return_results(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    knowledge = _install_jc_module(monkeypatch, "knowledge", types.SimpleNamespace(
        DEFAULT_SUBJECT="home",
        all_facts=lambda subject=None: [{"subject": subject}],
        stats=lambda: {"total": 1},
        remember=lambda *args, **kwargs: True,
        forget=lambda **kwargs: True,
    ))
    del knowledge

    await websocket.ws_get_knowledge(
        fake_hass, connection, {"id": 41, "subject": "user"}
    )
    assert connection.result == (41, {
        "facts": [{"subject": "user"}], "stats": {"total": 1},
    })
    await websocket.ws_add_knowledge(
        fake_hass, connection, {"id": 42, "key": "pet", "value": "Milo"}
    )
    assert connection.result == (42, {"ok": True, "facts": [{"subject": None}]})
    await websocket.ws_forget_knowledge(
        fake_hass, connection, {"id": 43, "fact_id": 7}
    )
    assert connection.result == (43, {"removed": True, "facts": [{"subject": None}]})

    core = _install_jc_module(monkeypatch, "cognitive_core", types.SimpleNamespace(
        request_lockdown=lambda *args, **kwargs: _resolved(True),
        lockdown_status=lambda: {"active": True},
        status=lambda: {"running": True},
        run_analysis_now=lambda hass: _resolved({"ran": True}),
    ))
    await websocket.ws_set_lockdown(fake_hass, connection, {"id": 44, "on": True})
    assert connection.result == (44, {"ok": True, "lockdown": {"active": True}})
    await websocket.ws_get_cognitive_status(fake_hass, connection, {"id": 45})
    assert connection.result == (45, {"running": True})
    await websocket.ws_run_analysis(fake_hass, connection, {"id": 46})
    assert connection.result == (46, {"ran": True})


async def test_root_cause_and_camera_coverage_commands(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    rca = _install_jc_module(monkeypatch, "rca", types.SimpleNamespace(
        DEFAULT_WINDOW_SECS=3600,
        analyze=lambda entity_id, event_time, window: {
            "entity_id": entity_id, "event_time": event_time, "window": window,
        },
    ))
    del rca
    await websocket.ws_root_cause(
        fake_hass, connection,
        {"id": 51, "entity_id": "light.office", "window_secs": 0},
    )
    assert connection.result == (51, {
        "entity_id": "light.office", "event_time": None, "window": 3600,
    })

    entry = types.SimpleNamespace(entry_id="coverage-entry", data={}, options={})
    monkeypatch.setattr(websocket, "_get_entry", lambda hass: entry)
    config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        effective_config=lambda entry: {"model": "test"},
    ))
    del config
    coverage = _install_jc_module(monkeypatch, "camera_coverage", types.SimpleNamespace(
        infer_coverage=lambda hass, cfg, camera: _resolved({
            "config": cfg, "camera": camera,
        }),
    ))
    del coverage
    await websocket.ws_compute_camera_coverage(
        fake_hass, connection, {"id": 52, "camera": {"entity_id": "camera.front"}}
    )
    assert connection.result == (52, {
        "config": {"model": "test"}, "camera": {"entity_id": "camera.front"},
    })


async def test_document_commands_cover_all_actions(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    calls = []
    documents = _install_jc_module(monkeypatch, "documents", types.SimpleNamespace(
        library_status=lambda: {"files": 2},
        ingest_directory_async=lambda hass: _resolved({
            "files_ingested": 1, "total_chunks": 4, "semantic": True,
            "embedded_chunks": 3,
        }),
        save_and_ingest_upload=lambda hass, filename, content: _resolved({
            "ok": True, "filename": filename, "chunks": 2,
        }),
        scan_watch_folders=lambda hass: _resolved({"new_files": 1}),
        delete_source=lambda filename: {"ok": True},
        search_documents_async=lambda hass, query, limit: _resolved([query, limit]),
    ))
    del documents
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: calls.append(args))
    connection = _Conn()

    await websocket.ws_documents(fake_hass, connection, {"id": 61, "action": "status"})
    assert connection.result == (61, {"files": 2})
    await websocket.ws_documents(fake_hass, connection, {"id": 62, "action": "ingest"})
    assert connection.result == (62, {
        "files_ingested": 1, "total_chunks": 4, "semantic": True,
        "embedded_chunks": 3,
    })
    await websocket.ws_documents(fake_hass, connection, {
        "id": 63, "action": "upload", "filename": "notes.txt", "content": "abc",
    })
    assert connection.result == (63, {"ok": True, "filename": "notes.txt", "chunks": 2})
    await websocket.ws_documents(fake_hass, connection, {"id": 64, "action": "scan_watch"})
    assert connection.result == (64, {"new_files": 1})
    await websocket.ws_documents(fake_hass, connection, {
        "id": 65, "action": "delete", "filename": "old.txt",
    })
    assert connection.result == (65, {"ok": True})
    await websocket.ws_documents(fake_hass, connection, {
        "id": 66, "action": "search", "query": "solar",
    })
    assert connection.result == (66, {"results": ["solar", 5]})
    assert len(calls) == 4


async def test_semantic_search_commands_cover_action_paths(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    persisted = []
    probes = []
    config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        set=lambda key, value: persisted.append((key, value)),
        get=lambda key, default=None: True if key == "semantic_search" else default,
    ))
    del config
    embeddings = _install_jc_module(monkeypatch, "embeddings", types.SimpleNamespace(
        init_store=lambda: None,
        probe=lambda hass: _resolved(probes.append("probe") or {"ok": True, "model": "embed", "dim": 4}),
        _ollama_base=lambda: "http://ollama",
        vector_count=lambda: 9,
        _model=lambda: "embed-model",
    ))
    del embeddings
    connection = _Conn()

    await websocket.ws_semantic_search(fake_hass, connection, {"id": 71, "action": "enable"})
    assert connection.result == (71, {"ok": True, "model": "embed", "dim": 4, "enabled": True})
    await websocket.ws_semantic_search(fake_hass, connection, {"id": 72, "action": "disable"})
    assert connection.result == (72, {"enabled": False, "ok": True})
    await websocket.ws_semantic_search(fake_hass, connection, {"id": 73, "action": "test"})
    assert connection.result[1]["ok"] is True
    await websocket.ws_semantic_search(fake_hass, connection, {"id": 74, "action": "status"})
    assert connection.result == (74, {
        "enabled": True, "ollama_configured": True, "base": "http://ollama",
        "model": "embed-model", "vector_count": 9,
    })
    assert persisted == [("semantic_search", True), ("semantic_search", False)]
    assert probes == ["probe", "probe"]


async def test_intrusion_command_covers_status_learning_and_label_actions(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    calls = []
    intrusion = _install_jc_module(monkeypatch, "intrusion", types.SimpleNamespace(
        async_dismiss_intrusion=lambda hass, reason: _resolved({"dismissed": reason}),
        acknowledge=lambda reason: {"acknowledged": reason},
        async_load=lambda hass: _resolved(calls.append("load")),
        get_log=lambda limit: [{"limit": limit}],
        learning_summary=lambda: {"labels": 2},
        async_label_event=lambda hass, event_id, label: _resolved({
            "event_id": event_id, "label": label,
        }),
        status=lambda: {"active": True},
    ))
    del intrusion
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: None)
    monkeypatch.setattr(sys.modules["jc"], "cognitive_core", types.SimpleNamespace(
        _CORE=types.SimpleNamespace(safety_mgr=types.SimpleNamespace(_investigation="active")),
    ), raising=False)
    connection = _Conn()

    await websocket.ws_intrusion(fake_hass, connection, {"id": 81, "action": "status"})
    assert connection.result == (81, {"active": True})
    await websocket.ws_intrusion(fake_hass, connection, {"id": 82, "action": "acknowledge"})
    assert connection.result[1]["acknowledged"] == "panel"
    await websocket.ws_intrusion(fake_hass, connection, {"id": 83, "action": "log", "limit": 4})
    assert connection.result == (83, {"events": [{"limit": 4}], "learning": {"labels": 2}})
    await websocket.ws_intrusion(fake_hass, connection, {
        "id": 84, "action": "label", "event_id": "event-1", "label": "real",
    })
    assert connection.result == (84, {
        "event_id": "event-1", "label": "real", "learning": {"labels": 2},
    })
    await websocket.ws_intrusion(fake_hass, connection, {"id": 85, "action": "learning"})
    assert connection.result == (85, {"labels": 2})
    await websocket.ws_intrusion(fake_hass, connection, {"id": 86, "action": "dismiss"})
    assert connection.result[1]["dismissed"] == "panel"
    assert calls == ["load", "load"]


async def test_biometrics_energy_hazard_mode_and_health_commands(
    fake_hass, monkeypatch,
):
    websocket = _load_websocket_module()
    connection = _Conn()
    persisted = []
    config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        set=lambda key, value: persisted.append((key, value)),
        get=lambda key, default=None: True,
    ))
    del config
    biometrics = _install_jc_module(monkeypatch, "biometrics", types.SimpleNamespace(
        discover=lambda config, states: {"sleep": [{"entity_id": "sensor.sleep"}]},
    ))
    del biometrics
    fake_hass.states.set("sensor.sleep", "good")
    fake_hass.states.set("binary_sensor.wearable", "on")

    await websocket.ws_biometrics(fake_hass, connection, {"id": 91, "action": "enable"})
    assert connection.result == (91, {
        "enabled": True, "found": 1,
        "entities": [{"kind": "sleep", "entity_id": "sensor.sleep"}],
    })
    await websocket.ws_biometrics(fake_hass, connection, {"id": 92, "action": "disable"})
    assert persisted == [("biometrics_enabled", True), ("biometrics_enabled", False)]

    energy = _install_jc_module(monkeypatch, "energy", types.SimpleNamespace(
        AGENCY_ADVISORY="advisory", AGENCY_OPT_IN="opt_in", AGENCY_AUTONOMOUS="autonomous",
        power_status=lambda config, states: {"entities": len(states)},
    ))
    del energy
    await websocket.ws_energy(fake_hass, connection, {"id": 93, "action": "set_agency", "agency": "invalid"})
    assert connection.error == (93, "bad_agency", "unknown agency 'invalid'")
    await websocket.ws_energy(fake_hass, connection, {"id": 94, "action": "set_agency", "agency": "OPT_IN"})
    assert connection.result == (94, {"entities": 2})
    assert persisted[-1] == ("energy_agency", "opt_in")

    hazard = _install_jc_module(monkeypatch, "hazard_monitor", types.SimpleNamespace(
        scan_now=lambda hass: _resolved({"scan": True}),
        status=lambda hass: _resolved({"enabled": True}),
    ))
    del hazard
    await websocket.ws_hazard(fake_hass, connection, {"id": 95, "action": "scan"})
    assert connection.result == (95, {"scan": True})
    await websocket.ws_hazard(fake_hass, connection, {"id": 96, "action": "status"})
    assert connection.result == (96, {"enabled": True})

    modes = _install_jc_module(monkeypatch, "modes", types.SimpleNamespace(
        set_mode=lambda mode, reason: {"ok": True, "mode": mode, "reason": reason},
        mode_info=lambda: {"active": "focus"},
    ))
    del modes
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: None)
    scene_calls = []
    _install_jc_module(monkeypatch, "mode_scene", types.SimpleNamespace(
        apply_mode_entry=lambda hass, mode: _resolved(scene_calls.append(mode)),
    ))
    await websocket.ws_mode(fake_hass, connection, {
        "id": 97, "action": "set", "mode": "focus", "reason": "test",
    })
    assert connection.result == (97, {"ok": True, "mode": "focus", "reason": "test", "active": "focus"})
    assert scene_calls == ["focus"]
    await websocket.ws_mode(fake_hass, connection, {"id": 98, "action": "status"})
    assert connection.result == (98, {"active": "focus"})

    _install_jc_module(monkeypatch, "diagnostics", types.SimpleNamespace(
        run_service_health=lambda hass: _resolved({"healthy": True}),
    ))
    _install_jc_module(monkeypatch, "voice_confirm", types.SimpleNamespace(
        announce_test=lambda hass: _resolved({"played": True}),
    ))
    await websocket.ws_diagnostics(fake_hass, connection, {"id": 99})
    assert connection.result == (99, {"healthy": True})
    await websocket.ws_voice_confirm_test(fake_hass, connection, {"id": 100})
    assert connection.result == (100, {"played": True})


async def test_suggestion_goal_memory_and_appliance_commands(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    logs = []
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: logs.append(args))

    analyzer = types.SimpleNamespace(dismiss_suggestion=lambda sid: sid == 5)
    pattern_analyzer = _install_jc_module(monkeypatch, "pattern_analyzer", types.SimpleNamespace(
        get_analyzer=lambda: analyzer,
        install_approved_suggestion=lambda hass, sid: _resolved({
            "ok": True, "installed": True, "alias": "Auto lights",
        }),
    ))
    del pattern_analyzer
    await websocket.ws_suggestion_action(fake_hass, connection, {
        "id": 111, "suggestion_id": 4, "action": "approve",
    })
    assert connection.result == (111, {
        "ok": True, "installed": True, "reason": None, "alias": "Auto lights",
    })
    await websocket.ws_suggestion_action(fake_hass, connection, {
        "id": 112, "suggestion_id": 5, "action": "dismiss",
    })
    assert connection.result == (112, {"ok": True})

    goals_calls = []
    goals = _install_jc_module(monkeypatch, "goals", types.SimpleNamespace(
        create=lambda title, outcome, **kwargs: goals_calls.append((title, outcome, kwargs)) or {"id": 7},
        delete=lambda goal_id: goal_id == 8,
        cancel=lambda goal_id: goal_id == 9,
    ))
    del goals
    monkeypatch.setattr(websocket, "_get_goals", lambda: [{"id": 7}])
    await websocket.ws_goal_action(fake_hass, connection, {"id": 113, "action": "create", "outcome": "  save energy ", "interval_min": 30})
    assert connection.result == (113, {"ok": True, "goal": {"id": 7}, "goals": [{"id": 7}]})
    assert goals_calls == [("", "save energy", {"check_interval_min": 30.0})]
    await websocket.ws_goal_action(fake_hass, connection, {"id": 114, "action": "create", "outcome": "  "})
    assert connection.error == (114, "empty_outcome", "a goal needs an outcome to work toward")
    await websocket.ws_goal_action(fake_hass, connection, {"id": 115, "action": "delete", "goal_id": 8})
    assert connection.result == (115, {"ok": True, "goals": [{"id": 7}]})
    await websocket.ws_goal_action(fake_hass, connection, {"id": 116, "action": "cancel"})
    assert connection.error == (116, "missing_goal_id", "cancel needs a goal_id")
    await websocket.ws_goal_action(fake_hass, connection, {"id": 117, "action": "cancel", "goal_id": 9})
    assert connection.result == (117, {"ok": True, "goals": [{"id": 7}]})

    memory = _install_jc_module(monkeypatch, "memory", types.SimpleNamespace(
        search_memory=lambda query, k: [{"query": query, "k": k}],
    ))
    del memory
    await websocket.ws_search_memory(fake_hass, connection, {"id": 118, "query": "past" , "k": 2})
    assert connection.result == (118, {"results": [{"query": "past", "k": 2}]})

    entry = types.SimpleNamespace(entry_id="appliance-entry", data={}, options={})
    monkeypatch.setattr(websocket, "_get_entry", lambda hass: entry)
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: {"runtime_config": {"watts": 5}}}}
    config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        effective_config_with_runtime=lambda entry, runtime: {"runtime": runtime},
    ))
    del config
    start_args = []
    appliance = _install_jc_module(monkeypatch, "appliance_monitor", types.SimpleNamespace(
        start=lambda hass, cfg: _resolved(start_args.append(cfg)),
    ))
    del appliance
    monkeypatch.setattr(websocket, "_get_appliance_status", lambda: {"running": True})
    await websocket.ws_reload_appliances(fake_hass, connection, {"id": 119})
    assert connection.result == (119, {"ok": True, "appliances": {"running": True}})
    assert start_args == [{"runtime": {"watts": 5}}]


def test_websocket_data_adapters_shape_and_filter_rows(monkeypatch):
    websocket = _load_websocket_module()
    analyzer = types.SimpleNamespace(
        get_pending_suggestions=lambda: [
            {
                "id": 3, "pattern_type": "motion", "details": '{"room": "hall"}',
                "entity_ids": '["binary_sensor.hall"]', "pattern_count": 4,
                "confidence": 0.876, "description": "Hall activity",
                "automation_yaml": "alias: Hall",
            },
        ],
        get_person_patterns=lambda: [
            {"person": "Alex", "id": 1, "confidence": 0.456, "occurrences": 2},
            {"person": "Alex", "id": 2, "confidence": None},
            {"person": "", "id": 3},
        ],
    )
    _install_jc_module(monkeypatch, "pattern_analyzer", types.SimpleNamespace(
        get_analyzer=lambda: analyzer,
        explain_suggestion=lambda kind, details, count: {
            "headline": f"{kind}:{details.get('room', '')}", "evidence": [count],
        },
    ))
    assert websocket._get_suggestions() == [
        {
            "id": 3, "created": "", "description": "Hall activity",
            "yaml": "alias: Hall", "confidence": 0.88, "count": 4,
            "pattern_type": "motion", "entities": ["binary_sensor.hall"],
            "why_headline": "motion:hall", "evidence": [4],
        },
    ]
    analyzer.get_pending_suggestions = lambda: [{
        "id": 4, "details": "bad", "entity_ids": "bad", "confidence": "bad",
    }]
    assert websocket._get_suggestions() == []
    assert websocket._get_person_routines() == {
        "Alex": [
            {"id": 1, "pattern_type": "", "description": "", "confidence": 0.46, "occurrences": 2, "last_seen": ""},
            {"id": 2, "pattern_type": "", "description": "", "confidence": 0.0, "occurrences": 0, "last_seen": ""},
        ],
    }

    goals = _install_jc_module(monkeypatch, "goals", types.SimpleNamespace(recent=lambda limit: [
        {"id": 8, "title": "Goal", "steps": [{"status": "done"}, {"status": "active"}]},
    ]))
    del goals
    assert websocket._get_goals() == [{
        "id": 8, "title": "Goal", "outcome": "", "status": "active",
        "steps_done": 1, "steps_total": 2,
        "steps": [{"status": "done"}, {"status": "active"}],
        "next_check_ts": "", "deadline_ts": None, "last_result": "", "updated_ts": "",
    }]


def test_camera_onboarding_and_provider_status_helpers(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    jarvis_config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        get=lambda key, default=None: {
            "camera_overrides": {"camera.old": "camera.new", "n": 3},
            "camera_names": {"camera.front": "Front entry"},
            "onboarding_dismissed": False,
            "banter_level": 2,
            "briefing_morning_enabled": True,
        }.get(key, default),
    ))
    del jarvis_config
    assert websocket._get_camera_overrides() == {"camera.old": "camera.new", "n": "3"}
    assert websocket._get_camera_names() == {"camera.front": "Front entry"}
    monkeypatch.setattr(websocket, "_get_cameras", lambda hass: [{"entity_id": "camera.front"}])
    state = websocket._get_onboarding_state(fake_hass, None, "")
    assert state["show"] is True
    assert state["done_count"] == 3
    assert state["total"] == 5
    dismissed_config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        get=lambda key, default=None: True if key == "onboarding_dismissed" else default,
    ))
    del dismissed_config
    assert websocket._get_onboarding_state(fake_hass, None, "notify.mobile") ["show"] is False


def test_appliance_reasoning_and_registry_fallback_helpers(monkeypatch, fake_hass):
    websocket = _load_websocket_module()
    appliance = _install_jc_module(monkeypatch, "appliance_monitor", types.SimpleNamespace(
        status=lambda: {
            "running": True, "profile": ["washer"],
            "sensors": {"sensor.watts": {"power_w": 12.6, "friendly_name": "Watts"}},
            "native_appliances": {"sensor.washer": {"current_state": "on"}},
            "whole_home_delta": True,
        },
    ))
    del appliance
    reasoning = _install_jc_module(monkeypatch, "reasoning_cache", types.SimpleNamespace(
        stats=lambda: {"learned_patterns": 4},
    ))
    del reasoning
    _install_jc_module(monkeypatch, "connectivity", types.SimpleNamespace(
        status=lambda: {"state": "open"},
    ))
    assert websocket._get_appliance_status() == {
        "running": True, "profile": ["washer"],
        "tracked_sensors": [{
            "entity": "sensor.watts", "name": "Watts", "appliance": None,
            "phase": None, "power_w": 13, "discovery": None,
        }],
        "native": [{
            "entity": "sensor.washer", "name": "sensor.washer",
            "appliance": None, "state": "on",
        }],
        "whole_home": True,
    }
    assert websocket._get_reasoning_stats()["learned_patterns"] == 4
    assert websocket._get_reasoning_stats()["llm_breaker"] == "open"

    monkeypatch.setattr(websocket, "_area_capabilities", lambda hass, area: ["light"] if area == "a" else [])
    area_registry = sys.modules["homeassistant.helpers.area_registry"]
    monkeypatch.setattr(area_registry, "async_get", lambda hass: types.SimpleNamespace(
        async_list_areas=lambda: [types.SimpleNamespace(id="a"), types.SimpleNamespace(id="b")],
    ))
    assert websocket._all_areas_with_anything(fake_hass) == ["a"]


def test_area_entity_registry_and_satellite_resolution(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    entities = {
        "light.direct": types.SimpleNamespace(
            entity_id="light.direct", area_id="living", device_id=None,
        ),
        "sensor.device_area": types.SimpleNamespace(
            entity_id="sensor.device_area", area_id=None, device_id="device-1",
        ),
        "switch.other": types.SimpleNamespace(
            entity_id="switch.other", area_id="other", device_id=None,
        ),
    }
    entity_registry = sys.modules["homeassistant.helpers.entity_registry"]
    device_registry = sys.modules["homeassistant.helpers.device_registry"]
    area_registry = sys.modules["homeassistant.helpers.area_registry"]
    helpers = sys.modules["homeassistant.helpers"]
    monkeypatch.setattr(helpers, "entity_registry", entity_registry)
    monkeypatch.setattr(helpers, "device_registry", device_registry)
    monkeypatch.setattr(helpers, "area_registry", area_registry)
    monkeypatch.setattr(entity_registry, "async_get", lambda hass: types.SimpleNamespace(entities=entities))
    monkeypatch.setattr(device_registry, "async_get", lambda hass: types.SimpleNamespace(
        async_get=lambda device_id: types.SimpleNamespace(area_id="living"),
    ))
    _install_jc_module(monkeypatch, "entity_filter", types.SimpleNamespace(
        is_excluded=lambda hass, entity_id: entity_id == "light.direct",
    ))
    assert websocket._entities_in_area(fake_hass, "living") == ["sensor.device_area"]

    areas = {"area-1": types.SimpleNamespace(name="Kitchen")}
    monkeypatch.setattr(area_registry, "async_get", lambda hass: types.SimpleNamespace(
        async_get_area=lambda area_id: areas.get(area_id),
    ))
    assert websocket._area_name(fake_hass, "area-1") == "Kitchen"
    assert websocket._area_name(fake_hass, "missing") == "missing"

    fake_hass.states.set("assist_satellite.kitchen", "idle", friendly_name="Kitchen Voice")
    monkeypatch.setattr(entity_registry, "async_get", lambda hass: types.SimpleNamespace(
        async_get=lambda entity_id: types.SimpleNamespace(device_id="voice-device"),
    ))
    monkeypatch.setattr(device_registry, "async_get", lambda hass: types.SimpleNamespace(
        async_get=lambda device_id: types.SimpleNamespace(area_id="area-1"),
    ))
    assert websocket._get_satellites(fake_hass) == [{
        "entity_id": "assist_satellite.kitchen", "name": "Kitchen Voice", "area": "Kitchen",
    }]


def test_camera_adapter_builds_designation_and_fallbacks(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    fake_hass.states.set("camera.patio", "idle", friendly_name="Patio")
    fake_hass.states.set("camera.front", "idle")
    _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        get=lambda key, default=None: {"camera_names": {"camera.patio": "Deck"}}.get(key, default),
    ))
    outdoor = _install_jc_module(monkeypatch, "outdoor", types.SimpleNamespace(
        _cfg_list=lambda key: ["camera.patio"] if key == "outdoor_entities" else [],
        is_outdoor=lambda hass, entity_id, name: entity_id == "camera.patio",
        location_mode=lambda entity_id, indoors, outdoors: "outdoor" if entity_id in outdoors else "auto",
    ))
    del outdoor
    _install_jc_module(monkeypatch, "camera", types.SimpleNamespace(
        display_name=lambda entity_id, friendly, names: names.get(entity_id, friendly),
        _disabled_cameras=lambda: ["camera.front"],
    ))
    assert websocket._get_cameras(fake_hass) == [
        {
            "entity_id": "camera.patio", "name": "Deck", "raw_name": "Patio",
            "enabled": True, "outdoor": True, "location_mode": "outdoor",
        },
        {
            "entity_id": "camera.front", "name": "camera.front", "raw_name": "camera.front",
            "enabled": False, "outdoor": False, "location_mode": "auto",
        },
    ]
    assert websocket._get_camera_overrides() == {}


def test_observer_and_reasoning_stats_return_defensive_defaults(monkeypatch):
    websocket = _load_websocket_module()
    _install_jc_module(monkeypatch, "observer", types.SimpleNamespace(_STATE=None))
    assert websocket._get_observer_stats() == {
        "running": False, "calls_last_hour": 0, "rate_limit": 30,
        "events_24h": 0, "flagged_24h": 0, "dropped_24h": 0, "spoken_24h": 0,
        "cognition_enabled": True, "cognition_threshold": 0.6,
        "cog_entities": 0, "cog_events_seen": 0, "cog_escalated": 0,
        "cog_predictable": 0, "cog_routines": 0, "cog_presence": 0,
        "presence": [], "learned_patterns": 0, "cloud_calls": 0,
        "local_decisions": 0, "local_rate": 0, "llm_breaker": "closed",
    }
    _install_jc_module(monkeypatch, "reasoning_cache", types.SimpleNamespace(
        stats=lambda: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    ))
    _install_jc_module(monkeypatch, "connectivity", types.SimpleNamespace(
        status=lambda: "not-a-dict",
    ))
    assert websocket._get_reasoning_stats() == {
        "learned_patterns": 0, "cloud_calls": 0, "local_decisions": 0,
        "local_rate": 0, "llm_breaker": "closed",
    }


async def test_ws_update_config_validation_and_observer_lifecycle(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    await websocket.ws_update_config(fake_hass, connection, {
        "id": 141, "key": "not_writable", "value": True,
    })
    assert connection.error == (
        141, "invalid_key", "Key 'not_writable' is not writable from the panel",
    )

    fake_hass.config_entries.async_entries = lambda domain: []
    await websocket.ws_update_config(fake_hass, connection, {
        "id": 142, "key": "observer_enabled", "value": True,
    })
    assert connection.error == (142, "no_entry", "No JARVIS config entry found")

    entry = types.SimpleNamespace(entry_id="observer-entry", data={}, options={})
    fake_hass.config_entries.async_entries = lambda domain: [entry]
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: None}}
    await websocket.ws_update_config(fake_hass, connection, {
        "id": 143, "key": "observer_enabled", "value": True,
    })
    assert connection.error == (143, "no_data", "JARVIS runtime data not found")

    persisted = []
    monkeypatch.setattr(fake_hass, "data", {websocket.DOMAIN: {entry.entry_id: {}}})
    _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        set_many=lambda updates: persisted.append(dict(updates)),
        effective_config_with_runtime=lambda entry, runtime: {"enabled": runtime["observer_enabled"]},
    ))
    lifecycle = []
    _install_jc_module(monkeypatch, "observer", types.SimpleNamespace(
        start=lambda hass, config: _resolved(lifecycle.append(("start", config))),
        stop=lambda: _resolved(lifecycle.append(("stop", None))),
    ))
    await websocket.ws_update_config(fake_hass, connection, {
        "id": 144, "key": "observer_enabled", "value": True,
    })
    assert connection.result == (144, {"key": "observer_enabled", "value": True})
    assert fake_hass.data[websocket.DOMAIN][entry.entry_id]["observer_running"] is True
    assert lifecycle == [("start", {"enabled": True})]
    await websocket.ws_update_config(fake_hass, connection, {
        "id": 145, "key": "observer_enabled", "value": False,
    })
    assert fake_hass.data[websocket.DOMAIN][entry.entry_id]["observer_running"] is False
    assert lifecycle[-1] == ("stop", None)
    assert persisted == [
        {"observer_enabled": True}, {"observer_enabled": False},
    ]


def test_debug_log_loads_valid_persisted_rows_and_filters_conversation(monkeypatch, tmp_path):
    websocket = _load_websocket_module()
    log_file = tmp_path / "jarvis.log"
    log_file.write_text(
        "2026-09-25 10:11:12 [CONV] hello\n"
        "invalid line\n"
        "2026-09-25 10:12:13 [NOISE] motion\n"
    )
    monkeypatch.setattr(websocket, "_LOG_FILE", log_file)
    websocket._DEBUG_LOG.clear()
    websocket._CONV_LOG.clear()

    websocket._load_persisted_log()

    assert websocket.recent_debug_log() == [
        {"date": "2026-09-25", "ts": "10:11:12", "cat": "CONV", "msg": "hello"},
        {"date": "2026-09-25", "ts": "10:12:13", "cat": "NOISE", "msg": "motion"},
    ]
    assert websocket.recent_conversation_log() == [websocket._DEBUG_LOG[0]]
    assert websocket.recent_debug_log(1) == [websocket._DEBUG_LOG[-1]]
    assert websocket.recent_debug_log(0) == list(websocket._DEBUG_LOG)
    assert websocket.recent_conversation_log(-1) == list(websocket._CONV_LOG)


def test_jarvis_log_truncates_and_queues_without_starting_writer(monkeypatch):
    websocket = _load_websocket_module()
    persisted = []
    monkeypatch.setattr(websocket, "_persist_log_entry", persisted.append)
    websocket._DEBUG_LOG.clear()
    websocket._CONV_LOG.clear()

    websocket.jarvis_log("ROUTE", "x" * 600)
    websocket.jarvis_log("SENSOR", "quiet")

    assert websocket._DEBUG_LOG[-1]["msg"] == "quiet"
    assert len(websocket._DEBUG_LOG[0]["msg"]) == 500
    assert len(websocket._CONV_LOG) == 1
    assert websocket._CONV_LOG[0]["cat"] == "ROUTE"
    assert persisted == list(websocket._DEBUG_LOG)


def test_debug_log_writer_and_queue_saturation(monkeypatch, tmp_path):
    websocket = _load_websocket_module()
    log_file = tmp_path / "writer" / "jarvis.log"
    monkeypatch.setattr(websocket, "_LOG_FILE", log_file)

    class _OneEntryQueue:
        def __init__(self):
            self.calls = 0
            self.completed = 0

        def get(self):
            self.calls += 1
            if self.calls == 1:
                return {"date": "2026-09-25", "ts": "10:00:00", "cat": "TEST", "msg": "written"}
            raise SystemExit

        def task_done(self):
            self.completed += 1

    writer_queue = _OneEntryQueue()
    monkeypatch.setattr(websocket, "_LOG_QUEUE", writer_queue)
    with pytest.raises(SystemExit):
        websocket._log_writer_loop()
    assert log_file.read_text() == "2026-09-25 10:00:00 [TEST] written\n"
    assert writer_queue.completed == 1

    class _FullQueue:
        def put_nowait(self, entry):
            raise websocket._queue.Full

    monkeypatch.setattr(websocket, "_ensure_writer", lambda: None)
    monkeypatch.setattr(websocket, "_LOG_QUEUE", _FullQueue())
    websocket._persist_log_entry({"msg": "drop persisted copy"})


async def test_fetch_models_normalizes_provider_responses_and_errors(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    requests = []

    class _Response:
        def __init__(self, status, payload):
            self.status = status
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return "provider error body"

        async def json(self):
            return self.payload

    class _Session:
        def get(self, url, headers=None):
            requests.append((url, headers or {}))
            if url.endswith("/api/tags"):
                payload = {"models": [{"name": "z-model"}, {}, {"name": "a-model"}]}
            else:
                payload = {"data": [{"id": "z-model"}, {}, {"id": "a-model"}, {"id": "z-model"}]}
            status = 503 if url.endswith("/v1/models") and len(requests) > 3 else 200
            return _Response(status, payload)

    class _Timeout:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.aiohttp_client"],
        "async_get_clientsession",
        lambda hass: _Session(),
    )
    monkeypatch.setitem(sys.modules, "async_timeout", types.SimpleNamespace(timeout=lambda seconds: _Timeout()))

    assert await websocket._fetch_models(fake_hass, "groq", "key", "") == ["a-model", "z-model"]
    assert requests[-1][0] == "https://api.groq.com/openai/v1/models"
    assert requests[-1][1] == {"Authorization": "Bearer key"}
    assert await websocket._fetch_models(fake_hass, "anthropic", "key", "") == ["a-model", "z-model"]
    assert requests[-1][1] == {"x-api-key": "key", "anthropic-version": "2023-06-01"}
    assert await websocket._fetch_models(fake_hass, "ollama", "", "http://ollama/") == ["a-model", "z-model"]
    assert requests[-1][0] == "http://ollama/api/tags"
    with pytest.raises(ValueError, match="base URL required"):
        await websocket._fetch_models(fake_hass, "custom", "", "")
    with pytest.raises(ValueError, match="unknown provider"):
        await websocket._fetch_models(fake_hass, "unknown", "", "")


async def test_ws_list_models_returns_models_and_provider_errors(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    _install_jc_module(monkeypatch, "ha_secrets", types.SimpleNamespace(
        async_get_provider_key=lambda hass, provider: _resolved("secret"),
    ))
    monkeypatch.setattr(websocket, "_get_entry", lambda hass: None)
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: "https://models.local")
    monkeypatch.setattr(websocket, "_fetch_models", lambda hass, provider, key, base: _resolved([provider, base, key]))
    connection = _Conn()

    await websocket.ws_list_models(fake_hass, connection, {"id": 121, "provider": "CUSTOM"})
    assert connection.result == (121, {
        "provider": "custom", "models": ["custom", "https://models.local", "secret"],
    })
    monkeypatch.setattr(websocket, "_fetch_models", lambda *args: _raise_async("offline"))
    await websocket.ws_list_models(fake_hass, connection, {"id": 122, "provider": "openai"})
    assert connection.result == (122, {
        "provider": "openai", "models": [], "error": "offline",
    })


async def _raise_async(message):
    raise RuntimeError(message)


async def test_camera_snapshot_rename_location_and_diagnostics(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    fake_hass.states.set("camera.front", "idle", friendly_name="Front")
    fake_hass.states.set("camera.side", "streaming", friendly_name="Side")
    fake_hass.states.set("light.entry", "on")
    saved = []
    jarvis_config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        set=lambda key, value: saved.append((key, value)),
        set_many=lambda values: saved.append(values),
    ))
    del jarvis_config
    camera = _install_jc_module(monkeypatch, "camera", types.SimpleNamespace(
        _get_best_image=lambda hass, entity_id: _resolved(b"jpeg-data"),
        _downscale_jpeg=lambda image, width: image + b"-small",
        merge_camera_name=lambda names, entity_id, name: {entity_id: name} if name else {},
        probe_camera=lambda hass, entity_id: _resolved({"entity_id": entity_id, "verdict": "ok"}),
    ))
    del camera
    monkeypatch.setattr(websocket, "_get_camera_names", lambda: {})
    monkeypatch.setattr(websocket, "_get_cameras", lambda hass: [{"entity_id": "camera.front"}])
    monkeypatch.setattr(websocket, "_snap_log", lambda *args: None)
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: None)

    await websocket.ws_camera_snapshot(fake_hass, connection, {"id": 131, "entity_id": "camera.front"})
    assert connection.result == (131, {"image": "anBlZy1kYXRhLXNtYWxs"})
    await websocket.ws_camera_snapshot(fake_hass, connection, {"id": 132, "entity_id": "light.entry"})
    assert connection.error == (132, "unknown_camera", "light.entry")

    await websocket.ws_rename_camera(fake_hass, connection, {
        "id": 133, "entity_id": "camera.front", "name": "Entry",
    })
    assert connection.result == (133, {
        "ok": True, "camera_names": {"camera.front": "Entry"},
        "cameras": [{"entity_id": "camera.front"}],
    })
    assert saved[-1] == ("camera_names", {"camera.front": "Entry"})
    await websocket.ws_rename_camera(fake_hass, connection, {
        "id": 134, "entity_id": "sensor.unknown", "name": "Nope",
    })
    assert connection.error == (134, "unknown_camera", "sensor.unknown")

    outdoor = _install_jc_module(monkeypatch, "outdoor", types.SimpleNamespace(
        _cfg_list=lambda key: [],
        set_entity_location=lambda indoors, outdoors, entity, mode: (
            [entity] if mode == "indoor" else [], [entity] if mode == "outdoor" else [],
        ),
    ))
    del outdoor
    await websocket.ws_camera_location(fake_hass, connection, {
        "id": 135, "entity_id": "camera.front", "mode": "indoor",
    })
    assert connection.result == (135, {
        "ok": True, "cameras": [{"entity_id": "camera.front"}],
    })
    assert saved[-1] == {
        "indoor_entities": ["camera.front"], "outdoor_entities": [],
    }

    entity_registry = sys.modules["homeassistant.helpers.entity_registry"]
    monkeypatch.setattr(entity_registry, "async_get", lambda hass: types.SimpleNamespace(
        async_get=lambda entity_id: types.SimpleNamespace(platform="demo"),
    ))
    await websocket.ws_camera_diagnostics(fake_hass, connection, {
        "id": 136, "entity_id": "camera.front",
    })
    assert connection.result == (136, {
        "summary": [
            {"entity_id": "camera.front", "state": "idle", "platform": "demo"},
            {"entity_id": "camera.side", "state": "streaming", "platform": "demo"},
        ],
        "platforms": {"demo": 2},
        "probe": {"entity_id": "camera.front", "verdict": "ok"},
    })


async def test_mmwave_overview_reports_room_and_sensor_states(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    monkeypatch.setattr(websocket, "_all_areas_with_anything", lambda hass: ["living", "yard", "empty"])
    monkeypatch.setattr(websocket, "_area_name", lambda hass, area: {
        "living": "Living Room", "yard": "Backyard", "empty": "Empty",
    }[area])
    monkeypatch.setattr(websocket, "_is_outdoor_area", lambda hass, area: area == "yard")
    monkeypatch.setattr(websocket.audio_routing, "presence_entities_in_area", lambda hass, area: {
        "living": ["binary_sensor.motion_on", "binary_sensor.motion_off", "binary_sensor.missing"],
        "yard": ["binary_sensor.yard"],
        "empty": [],
    }[area])
    states = {
        "binary_sensor.motion_on": types.SimpleNamespace(
            state="on", attributes={"friendly_name": "Motion"},
            last_changed=datetime.datetime.now(datetime.timezone.utc),
        ),
        "binary_sensor.motion_off": types.SimpleNamespace(
            state="off", attributes={},
            last_changed=datetime.datetime.now(datetime.timezone.utc),
        ),
        "binary_sensor.yard": types.SimpleNamespace(
            state="on", attributes={},
            last_changed=datetime.datetime.now(datetime.timezone.utc),
        ),
    }
    monkeypatch.setattr(fake_hass.states, "get", states.get)
    connection = _Conn()

    await websocket.ws_mmwave_overview(fake_hass, connection, {"id": 151})

    assert connection.error is None
    payload = connection.result[1]
    assert [room["area_id"] for room in payload["rooms"]] == ["yard", "living"]
    assert payload["summary"] == {
        "rooms_with_mmwave": 2, "rooms_detecting": 2, "total_sensors": 3,
    }
    living = payload["rooms"][1]
    assert living["state"] == "detecting"
    assert living["sensor_count"] == 2
    assert living["sensors"][0]["name"] == "Motion"
    assert living["sensors"][1]["name"] == "binary_sensor.motion_off"


async def test_calibration_command_success_and_fallbacks(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    record = _install_jc_module(monkeypatch, "decision_record", types.SimpleNamespace(
        calibration=lambda: {"n": 4},
        interruption_budget=lambda: {"judged": 3},
        stats=lambda: {"total": 4},
        outcome_rate=lambda action: {"action": action},
    ))
    del record
    _install_jc_module(monkeypatch, "pattern_analyzer", types.SimpleNamespace(
        CONFIDENCE_THRESHOLD=0.7,
        _effective_threshold=lambda: 0.72,
        _learned_threshold_delta=lambda: 0.02,
    ))
    await websocket.ws_get_calibration(fake_hass, connection, {"id": 152})
    assert connection.result == (152, {
        "calibration": {"n": 4}, "interruption_budget": {"judged": 3},
        "stats": {"total": 4}, "suggestion": {"action": "suggestion"},
        "suggestion_threshold": {"base": 0.7, "effective": 0.72, "learned_delta": 0.02},
    })

    _install_jc_module(monkeypatch, "pattern_analyzer", types.SimpleNamespace(
        CONFIDENCE_THRESHOLD=0.7,
        _effective_threshold=lambda: (_ for _ in ()).throw(RuntimeError("not ready")),
        _learned_threshold_delta=lambda: 0.0,
    ))
    await websocket.ws_get_calibration(fake_hass, connection, {"id": 153})
    assert "suggestion_threshold" not in connection.result[1]

    _install_jc_module(monkeypatch, "decision_record", types.SimpleNamespace(
        calibration=lambda: (_ for _ in ()).throw(RuntimeError("store down")),
    ))
    await websocket.ws_get_calibration(fake_hass, connection, {"id": 154})
    assert connection.result == (154, {
        "calibration": {"n": 0}, "interruption_budget": {"judged": 0},
        "error": "store down",
    })


def test_registration_helpers_and_defensive_data_fallbacks(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    registered = []
    monkeypatch.setattr(
        websocket.websocket_api,
        "async_register_command",
        lambda hass, command: registered.append(command.__name__),
    )
    websocket.async_register(fake_hass)
    assert "ws_get_panel_data" in registered
    assert "ws_get_activity_log" in registered
    assert "ws_biometrics" in registered
    assert len(registered) > 30

    monkeypatch.setattr(
        websocket.websocket_api,
        "async_register_command",
        lambda hass, command: (_ for _ in ()).throw(RuntimeError("already registered")),
    )
    websocket.async_register(fake_hass)

    _install_jc_module(monkeypatch, "knowledge", types.SimpleNamespace(
        stats=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    ))
    assert websocket._get_knowledge_stats() == {"total": 0, "by_kind": {}, "by_subject": {}}
    _install_jc_module(monkeypatch, "memory", types.SimpleNamespace(
        get_memory_stats=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    ))
    assert websocket._get_memory_stats() == {"backend": "unavailable", "total_memories": 0}
    _install_jc_module(monkeypatch, "cognitive_core", types.SimpleNamespace(
        lockdown_status=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        intrusion_status=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    ))
    assert websocket._get_lockdown_status()["active"] is False
    assert websocket._get_intrusion_status() == {"active": False, "confirmed": False}


async def test_websocket_handler_error_paths(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()

    monkeypatch.setattr(websocket, "_get_entry", lambda hass: (_ for _ in ()).throw(RuntimeError("entry failed")))
    await websocket.ws_get_panel_data(fake_hass, connection, {"id": 161})
    assert connection.error == (161, "panel_data_failed", "entry failed")

    cognitive = _install_jc_module(monkeypatch, "cognitive_core", types.SimpleNamespace(
        request_lockdown=lambda *args, **kwargs: _raise_async("lockdown failed"),
    ))
    del cognitive
    await websocket.ws_set_lockdown(fake_hass, connection, {"id": 162, "on": True})
    assert connection.error == (162, "lockdown_failed", "lockdown failed")

    _install_jc_module(monkeypatch, "knowledge", types.SimpleNamespace(
        all_facts=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("knowledge failed")),
        stats=lambda: {},
    ))
    await websocket.ws_get_knowledge(fake_hass, connection, {"id": 163})
    assert connection.error == (163, "knowledge_failed", "knowledge failed")

    _install_jc_module(monkeypatch, "rca", types.SimpleNamespace(
        DEFAULT_WINDOW_SECS=600,
        analyze=lambda *args: (_ for _ in ()).throw(RuntimeError("rca failed")),
    ))
    await websocket.ws_root_cause(fake_hass, connection, {"id": 164, "entity_id": "light.a"})
    assert connection.error == (164, "root_cause_failed", "rca failed")

    async def _fail_retrieve(*args, **kwargs):
        raise RuntimeError("history unavailable")
    monkeypatch.setattr(websocket, "_get_area_sparklines", _fail_retrieve)
    await websocket.ws_get_area_sparklines(fake_hass, connection, {"id": 165})
    assert connection.error == (165, "sparklines_failed", "history unavailable")


async def test_camera_diagnostic_and_snapshot_failure_branches(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    fake_hass.states.set("camera.broken", "idle")
    connection = _Conn()
    camera = _install_jc_module(monkeypatch, "camera", types.SimpleNamespace(
        probe_camera=lambda hass, entity_id: _raise_async("probe failed"),
        _get_best_image=lambda hass, entity_id: _raise_async("snapshot failed"),
    ))
    del camera
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: None)
    monkeypatch.setattr(websocket, "_snap_log", lambda *args: None)

    await websocket.ws_camera_diagnostics(fake_hass, connection, {
        "id": 166, "entity_id": "camera.broken",
    })
    assert connection.error == (166, "camera_diag_failed", "probe failed")
    await websocket.ws_camera_snapshot(fake_hass, connection, {
        "id": 167, "entity_id": "camera.broken",
    })
    assert connection.error == (167, "snapshot_failed", "snapshot failed")


def test_remaining_config_area_and_registry_utility_branches(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: {
        "valid": "0", "invalid": "not-an-integer",
    }.get(key, default))
    assert websocket._int_opt(fake_hass, None, "valid", 5) == 0
    assert websocket._int_opt(fake_hass, None, "invalid", 5) == 5

    area_registry = sys.modules["homeassistant.helpers.area_registry"]
    monkeypatch.setattr(area_registry, "async_get", lambda hass: (_ for _ in ()).throw(RuntimeError("unavailable")))
    assert websocket._area_name(fake_hass, "office") == "office"
    assert websocket._available_labels(fake_hass) == []
    label_registry = types.ModuleType("homeassistant.helpers.label_registry")
    label_registry.async_get = lambda hass: types.SimpleNamespace(labels={
        "b": types.SimpleNamespace(label_id="b", name="zebra"),
        "a": types.SimpleNamespace(label_id="a", name="Alpha"),
    })
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.label_registry", label_registry)
    monkeypatch.setattr(sys.modules["homeassistant.helpers"], "label_registry", label_registry, raising=False)
    assert websocket._available_labels(fake_hass) == [
        {"id": "a", "name": "Alpha"}, {"id": "b", "name": "zebra"},
    ]

    entry = types.SimpleNamespace(entry_id="options-entry", options={})
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: {"runtime_config": {"bad_json": "{"}}}}
    config = _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        get=lambda key: (_ for _ in ()).throw(RuntimeError("config unreadable")),
    ))
    del config
    monkeypatch.setattr(websocket, "_entry_opt", lambda entry, key, default=None: {
        "options_json": '["from options"]', "options_text": 0,
    }.get(key, default))
    assert websocket._get_runtime_json(fake_hass, entry, "options_json", None) == ["from options"]
    assert websocket._get_runtime_json(fake_hass, entry, "bad_json", "fallback") == "fallback"
    assert websocket._get_runtime_str(fake_hass, entry, "options_text", "fallback") == "0"
    assert websocket._get_runtime_str(fake_hass, entry, "missing", "fallback") == "fallback"

    monkeypatch.setattr(websocket, "_entities_in_area", lambda hass, area: [
        "sensor.bad_temp", "sensor.bad_humidity", "light.off", "binary_sensor.no_timestamp",
    ])
    states = {
        "sensor.bad_temp": types.SimpleNamespace(
            state="unknown", attributes={"device_class": "temperature"},
        ),
        "sensor.bad_humidity": types.SimpleNamespace(
            state="bad", attributes={"device_class": "humidity"},
        ),
        "light.off": types.SimpleNamespace(state="off", attributes={}),
        "binary_sensor.no_timestamp": types.SimpleNamespace(
            state="on", attributes={"device_class": "motion"},
        ),
    }
    monkeypatch.setattr(fake_hass.states, "get", states.get)
    assert websocket._area_live_readings(fake_hass, "office") == {
        "temp": None, "humidity": None, "lights": "OFF", "last_motion_seconds": None,
    }

    monkeypatch.setattr(websocket, "_get_camera_names", lambda: {})
    _install_jc_module(monkeypatch, "outdoor", types.SimpleNamespace(
        _cfg_list=lambda key: [],
    ))
    _install_jc_module(monkeypatch, "camera", types.SimpleNamespace(
        display_name=lambda *args: (_ for _ in ()).throw(RuntimeError("camera helper failed")),
        _disabled_cameras=lambda: [],
    ))
    fake_hass.states.set("camera.fail", "idle")
    assert websocket._get_cameras(fake_hass) == []


async def test_more_endpoint_alternatives_and_http_errors(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    fake_hass.states.set("camera.empty", "idle")
    image_logs = []
    _install_jc_module(monkeypatch, "camera", types.SimpleNamespace(
        _get_best_image=lambda hass, entity_id: _resolved(None),
    ))
    monkeypatch.setattr(websocket, "_snap_log", lambda *args: image_logs.append(args))
    await websocket.ws_camera_snapshot(fake_hass, connection, {
        "id": 171, "entity_id": "camera.empty",
    })
    assert connection.result == (171, {"image": None})
    assert image_logs

    class _Response:
        status = 502
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def text(self):
            return "bad gateway"

    class _Session:
        def get(self, url, headers=None):
            return _Response()

    class _Timeout:
        async def __aenter__(self):
            return None
        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.aiohttp_client"],
        "async_get_clientsession", lambda hass: _Session(),
    )
    monkeypatch.setitem(sys.modules, "async_timeout", types.SimpleNamespace(timeout=lambda seconds: _Timeout()))
    with pytest.raises(RuntimeError, match="HTTP 502: bad gateway"):
        await websocket._fetch_models(fake_hass, "openai", "", "")

    analyzer = types.SimpleNamespace(dismiss_suggestion=lambda sid: True)
    _install_jc_module(monkeypatch, "pattern_analyzer", types.SimpleNamespace(
        get_analyzer=lambda: analyzer,
        install_approved_suggestion=lambda hass, sid: _resolved({
            "ok": True, "installed": False, "reason": "advisory",
        }),
    ))
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: None)
    await websocket.ws_suggestion_action(fake_hass, connection, {
        "id": 172, "suggestion_id": 9, "action": "approve",
    })
    assert connection.result == (172, {
        "ok": True, "installed": False, "reason": "advisory", "alias": None,
    })


def test_config_resolver_area_and_satellite_fallback_branches(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    resolved = []
    _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        runtime_get=lambda hass, entry, key, default: resolved.append((hass, entry, key, default)) or "17",
    ))
    assert websocket._entry_opt("entry", "value", 0) == "17"
    assert websocket._runtime_opt(fake_hass, "entry", "value", 0) == "17"
    assert resolved == [
        (None, "entry", "value", 0),
        (fake_hass, "entry", "value", 0),
    ]

    entities = {"light.living": types.SimpleNamespace(
        entity_id="light.living", area_id="living", device_id=None,
    )}
    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.entity_registry"],
        "async_get", lambda hass: types.SimpleNamespace(entities=entities),
    )
    helpers = sys.modules["homeassistant.helpers"]
    monkeypatch.setattr(
        helpers,
        "entity_registry",
        sys.modules["homeassistant.helpers.entity_registry"],
    )
    monkeypatch.setattr(
        helpers,
        "device_registry",
        sys.modules["homeassistant.helpers.device_registry"],
    )
    monkeypatch.setitem(sys.modules, "jc.entity_filter", None)
    assert websocket._entities_in_area(fake_hass, "living") == ["light.living"]

    monkeypatch.setattr(websocket, "_entities_in_area", lambda hass, aid: [
        "binary_sensor.problem", "binary_sensor.unknown",
    ])
    fake_hass.states.set("binary_sensor.problem", "on", device_class="problem")
    fake_hass.states.set("binary_sensor.unknown", "on", device_class="unknown")
    assert websocket._area_capabilities(fake_hass, "living") == ["alarm"]

    monkeypatch.setattr(websocket, "_area_name", lambda hass, area: area)
    monkeypatch.setattr(websocket.audio_routing, "currently_occupied_areas", lambda hass: ["yard"])
    monkeypatch.setattr(websocket.audio_routing, "presence_entities_in_area", lambda hass, area: ["binary_sensor.no_time"])
    monkeypatch.setattr(fake_hass.states, "get", lambda entity_id: types.SimpleNamespace(last_changed=None))
    assert websocket._dominant_area(fake_hass) == "yard"


def test_satellite_registry_fallback_and_onboarding_config_errors(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    fake_hass.states.set("assist_satellite.voice", "idle", friendly_name="Voice")
    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.entity_registry"],
        "async_get", lambda hass: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    assert websocket._get_satellites(fake_hass) == [{
        "entity_id": "assist_satellite.voice", "name": "Voice", "area": "",
    }]

    _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        get=lambda key, default=None: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    ))
    assert websocket._get_camera_overrides() == {}
    assert websocket._get_camera_names() == {}
    monkeypatch.setattr(websocket, "_get_cameras", lambda hass: (_ for _ in ()).throw(RuntimeError("camera unavailable")))
    state = websocket._get_onboarding_state(fake_hass, None, "")
    assert state["dismissed"] is False
    assert state["show"] is True
    assert state["done_count"] == 1


async def test_remaining_command_alternatives_cross_coverage_threshold(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    connection = _Conn()
    persisted = []
    energy = _install_jc_module(monkeypatch, "energy", types.SimpleNamespace(
        AGENCY_ADVISORY="advisory", AGENCY_OPT_IN="opt_in", AGENCY_AUTONOMOUS="autonomous",
        power_status=lambda config, states: {"count": len(states)},
    ))
    del energy
    _install_jc_module(monkeypatch, "jarvis_config", types.SimpleNamespace(
        set=lambda key, value: persisted.append((key, value)),
        set_many=lambda updates: (_ for _ in ()).throw(RuntimeError("disk full")),
        get=lambda key, default=None: False,
    ))
    fake_hass.states.set("light.office", "on")
    await websocket.ws_energy(fake_hass, connection, {"id": 181, "action": "status"})
    assert connection.result == (181, {"count": 1})

    _install_jc_module(monkeypatch, "modes", types.SimpleNamespace(
        set_mode=lambda mode, reason: {"ok": False, "reason": "rejected"},
        mode_info=lambda: {"active": "normal"},
    ))
    await websocket.ws_mode(fake_hass, connection, {
        "id": 182, "action": "set", "mode": "unknown",
    })
    assert connection.result == (182, {"ok": False, "reason": "rejected", "active": "normal"})

    entry = types.SimpleNamespace(entry_id="persist-failure", data={}, options={})
    fake_hass.config_entries.async_entries = lambda domain: [entry]
    fake_hass.data = {websocket.DOMAIN: {entry.entry_id: {}}}
    await websocket.ws_update_config(fake_hass, connection, {
        "id": 183, "key": "ui_language", "value": "fr",
    })
    assert connection.result == (183, {"key": "ui_language", "value": "fr"})
    assert fake_hass.data[websocket.DOMAIN][entry.entry_id]["runtime_config"]["ui_language"] == "fr"

    embeddings = _install_jc_module(monkeypatch, "embeddings", types.SimpleNamespace(
        init_store=lambda: None,
        probe=lambda hass: _resolved({"ok": False, "error": "server offline"}),
        _ollama_base=lambda: "", vector_count=lambda: 0, _model=lambda: "model",
    ))
    del embeddings
    monkeypatch.setattr(websocket, "jarvis_log", lambda *args: None)
    await websocket.ws_semantic_search(fake_hass, connection, {
        "id": 184, "action": "enable",
    })
    assert connection.result == (184, {
        "ok": False, "error": "server offline", "enabled": True,
    })


def test_missing_state_and_recent_motion_helper_branches(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    now = datetime.datetime.now(datetime.timezone.utc)
    states = {
        "binary_sensor.recent": types.SimpleNamespace(
            state="on", attributes={}, last_changed=now,
        ),
        "sensor.temperature": types.SimpleNamespace(
            state="20", attributes={"device_class": "temperature"},
        ),
        "sensor.humidity": types.SimpleNamespace(
            state="40", attributes={"device_class": "humidity"},
        ),
        "sensor.after": types.SimpleNamespace(state="1", attributes={}),
    }
    monkeypatch.setattr(fake_hass.states, "get", states.get)
    monkeypatch.setattr(websocket, "_area_name", lambda hass, area: "Living room")
    monkeypatch.setattr(websocket.audio_routing, "currently_occupied_areas", lambda hass: ["living"])
    monkeypatch.setattr(websocket.audio_routing, "presence_entities_in_area", lambda hass, area: [
        "binary_sensor.missing", "binary_sensor.recent",
    ])
    assert websocket._dominant_area(fake_hass) == "living"

    monkeypatch.setattr(websocket, "_entities_in_area", lambda hass, area: ["light.missing"])
    assert websocket._area_light_state(fake_hass, "living") == (0, 0)
    assert websocket._area_live_readings(fake_hass, "living")["lights"] is None

    monkeypatch.setattr(websocket, "_entities_in_area", lambda hass, area: [
        "sensor.temperature", "sensor.humidity", "sensor.after",
    ])
    assert websocket._area_temp_humidity_entities(fake_hass, "living") == (
        "sensor.temperature", "sensor.humidity",
    )
