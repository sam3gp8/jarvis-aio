from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
import types


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
