"""Config-flow options steps must render fields — regression guard for the
empty "Step 1 of N" dialog (schemas had been left as stubs in an earlier build).
Stubs the minimal HA config-flow/selector surface at import."""
import sys
import types

import pytest

pytest.importorskip("voluptuous")  # core HA dep; skip cleanly where absent


def _install_stubs():
    # homeassistant.core.callback
    core = sys.modules.get("homeassistant.core") or types.ModuleType("homeassistant.core")
    if not hasattr(core, "callback"):
        core.callback = lambda f: f
    sys.modules["homeassistant.core"] = core

    # homeassistant.config_entries
    ce = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, **kw):
            pass

        async def async_set_unique_id(self, unique_id):
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self, **kw):
            pass

        def async_show_form(self, **kw):
            return {"type": "form", **kw}

        def async_show_menu(self, **kw):
            return {"type": "menu", **kw}

        def async_create_entry(self, **kw):
            return {"type": "create_entry", **kw}

        def async_abort(self, **kw):
            return {"type": "abort", **kw}

    class OptionsFlow:
        def async_show_form(self, **kw):
            return {"type": "form", **kw}

        def async_show_menu(self, **kw):
            return {"type": "menu", **kw}

        def async_create_entry(self, **kw):
            return {"type": "create_entry", **kw}

        def async_abort(self, **kw):
            return {"type": "abort", **kw}

    class ConfigEntry:
        pass

    ce.ConfigFlow = ConfigFlow
    ce.OptionsFlow = OptionsFlow
    ce.ConfigEntry = ConfigEntry
    sys.modules["homeassistant.config_entries"] = ce

    # homeassistant.helpers.selector — every selector used, as no-op callables.
    sel = types.ModuleType("homeassistant.helpers.selector")
    for name in ("TextSelector", "TextSelectorConfig", "SelectSelector",
                 "SelectSelectorConfig", "BooleanSelector", "AreaSelector",
                 "AreaSelectorConfig", "EntitySelector", "EntitySelectorConfig",
                 "NumberSelector", "NumberSelectorConfig"):
        setattr(sel, name, lambda *a, **k: object())

    class _Modes:
        DROPDOWN = "dropdown"
        SLIDER = "slider"

    class _Types:
        PASSWORD = "password"

    sel.SelectSelectorMode = _Modes
    sel.NumberSelectorMode = _Modes
    sel.TextSelectorType = _Types
    helpers = sys.modules.get("homeassistant.helpers")
    if helpers is not None:
        helpers.selector = sel
    sys.modules["homeassistant.helpers.selector"] = sel


_install_stubs()


class _Entry:
    options: dict = {}
    data: dict = {}


@pytest.fixture
def config_flow(load):
    return load("config_flow")


# ── v6.45.0: legacy add-on split removed ─────────────────────────────────────

def test_find_config_reads_runtime_path_only(config_flow, tmp_path, monkeypatch, load):
    """Auto-import reads /config/jarvis/config.json (the panel's runtime
    store, for zero-touch re-installs). The legacy add-on path is gone.
    Credentials live in secrets.yaml now, so "usable" is judged from there."""
    assert not hasattr(config_flow, "_CONFIG_PATHS")   # legacy list removed
    ha_secrets = load("ha_secrets")
    monkeypatch.setattr(ha_secrets, "get_secret_sync",
                        lambda key, default="", *a, **k: "gsk_test" if key == "jarvis_api_key" else default)
    runtime = tmp_path / "config.json"
    runtime.write_text('{"model": "llama"}')
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH", str(runtime))
    cfg = config_flow._find_config()
    assert cfg and cfg["model"] == "llama"


def test_find_config_requires_usable_llm(config_flow, tmp_path, monkeypatch):
    runtime = tmp_path / "config.json"
    runtime.write_text('{"honorific": "sir"}')        # no key, no local URL
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH", str(runtime))
    assert config_flow._find_config() is None


def test_find_config_accepts_selected_provider_legacy_key(config_flow, tmp_path, monkeypatch):
    runtime = tmp_path / "config.json"
    runtime.write_text('{"llm_provider": "gemini", "gemini_api_key": "gk"}')
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH", str(runtime))
    cfg = config_flow._find_config()
    assert cfg and cfg["llm_provider"] == "gemini"


def test_find_config_missing_file_is_none(config_flow, tmp_path, monkeypatch):
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH",
                        str(tmp_path / "nope.json"))
    assert config_flow._find_config() is None


def _user_flow(config_flow, fake_hass, monkeypatch, tmp_path):
    # No pre-existing runtime config, so async_step_user takes the manual path.
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH",
                        str(tmp_path / "nope.json"))
    flow = config_flow.JarvisConfigFlow()
    flow.hass = fake_hass
    return flow


async def test_user_step_shows_provider_menu(config_flow, fake_hass, monkeypatch, tmp_path):
    res = await _user_flow(config_flow, fake_hass, monkeypatch, tmp_path).async_step_user(None)
    assert res["type"] == "menu" and res["step_id"] == "provider_menu"
    assert set(res["menu_options"]) == {
        "groq", "openai", "anthropic", "gemini", "custom", "ollama", "finish",
    }


async def test_groq_step_saves_key_and_loops_back_to_menu(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    async def _ok(hass, provider, api_key):
        return None
    monkeypatch.setattr(config_flow, "_validate_provider_key", _ok)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_groq({"api_key": "gsk_new"})
    assert res["type"] == "menu" and res["step_id"] == "provider_menu"
    assert flow._provider_keys["groq"]["api_key"] == "gsk_new"


async def test_gemini_step_validates_key_without_chat_completion(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    seen = {}

    async def _fake_fetch_models(hass, provider, api_key, base_url):
        seen.update(provider=provider, api_key=api_key, base_url=base_url)
        return ["gemini-2.5-flash"]
    websocket = load("websocket")
    monkeypatch.setattr(websocket, "_fetch_models", _fake_fetch_models, raising=False)

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("fresh setup should not POST a chat completion")
    llm_provider = load("llm_provider")
    monkeypatch.setattr(llm_provider, "test_connection", _fail_if_called)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_gemini({"api_key": "AIza-key"})
    assert res["type"] == "menu" and res["step_id"] == "provider_menu"
    assert seen == {"provider": "gemini", "api_key": "AIza-key", "base_url": ""}


async def test_openai_step_uses_models_endpoint_instead_of_chat_completion(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    seen = {}

    async def _fake_fetch_models(hass, provider, api_key, base_url):
        seen.update(provider=provider, api_key=api_key, base_url=base_url)
        return ["gpt-4o-mini"]
    websocket = load("websocket")
    monkeypatch.setattr(websocket, "_fetch_models", _fake_fetch_models, raising=False)

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("fresh setup should not POST a chat completion")
    llm_provider = load("llm_provider")
    monkeypatch.setattr(llm_provider, "test_connection", _fail_if_called)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_openai({"api_key": "sk-key"})
    assert res["type"] == "menu" and res["step_id"] == "provider_menu"
    assert seen == {"provider": "openai", "api_key": "sk-key", "base_url": ""}


async def test_provider_key_step_shows_error_when_model_fetch_fails(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    async def _fail(hass, provider, api_key, base_url):
        raise Exception("401 Unauthorized")
    websocket = load("websocket")
    monkeypatch.setattr(websocket, "_fetch_models", _fail, raising=False)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_openai({"api_key": "bad"})
    assert res["type"] == "form" and res["errors"]["base"] == "invalid_auth"


async def test_provider_key_step_shows_connect_error_on_timeout(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    async def _timeout(hass, provider, api_key, base_url):
        raise TimeoutError()
    websocket = load("websocket")
    monkeypatch.setattr(websocket, "_fetch_models", _timeout, raising=False)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_openai({"api_key": "bad"})
    assert res["type"] == "form" and res["errors"]["base"] == "cannot_connect"


async def test_provider_key_step_shows_unknown_when_models_empty(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    async def _empty(hass, provider, api_key, base_url):
        return []
    websocket = load("websocket")
    monkeypatch.setattr(websocket, "_fetch_models", _empty, raising=False)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_openai({"api_key": "sk-key"})
    assert res["type"] == "form" and res["errors"]["base"] == "unknown"


async def test_groq_step_shows_error_on_bad_key(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    async def _bad(hass, provider, api_key):
        return "invalid_auth"
    monkeypatch.setattr(config_flow, "_validate_provider_key", _bad)

    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_groq({"api_key": "wrong"})
    assert res["type"] == "form" and res["errors"]["base"] == "invalid_auth"
    assert "groq" not in flow._provider_keys


async def test_custom_step_requires_base_url(config_flow, fake_hass, monkeypatch, tmp_path):
    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_custom({"api_key": "", "llm_base_url": ""})
    assert res["type"] == "form" and res["errors"]["base"] == "need_llm"


async def test_finish_redirects_to_menu_when_nothing_configured(
    config_flow, fake_hass, monkeypatch, tmp_path,
):
    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    res = await flow.async_step_finish(None)
    assert res["type"] == "menu" and res["step_id"] == "provider_menu"


async def test_finish_creates_entry_from_configured_provider(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    ha_secrets = load("ha_secrets")
    set_calls = []

    async def _fake_set(hass, provider, value):
        set_calls.append((provider, value))
        return True
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fake_set)
    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    flow._provider_keys["groq"] = {"api_key": "gsk_x"}
    monkeypatch.setattr(config_flow, "_fetch_available_models", _models)
    res = await flow.async_step_finish({
        "llm_provider": "groq", "honorific": "sir",
    })
    assert res["step_id"] == "finish_model"
    res = await flow.async_step_finish_model({"model": "m"})
    assert res["type"] == "create_entry"
    assert res["data"]["llm_provider"] == "groq"
    # the key goes to secrets.yaml only — never into the entry itself
    assert res["data"]["api_key"] == ""
    assert set_calls == [("groq", "gsk_x")]


async def test_finish_stays_on_form_when_secret_write_fails(
    config_flow, fake_hass, monkeypatch, tmp_path, load,
):
    ha_secrets = load("ha_secrets")

    async def _fake_set(hass, provider, value):
        return False
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fake_set)
    flow = _user_flow(config_flow, fake_hass, monkeypatch, tmp_path)
    flow._provider_keys["groq"] = {"api_key": "gsk_x"}
    monkeypatch.setattr(config_flow, "_fetch_available_models", _models)
    await flow.async_step_finish({
        "llm_provider": "groq", "honorific": "sir",
    })
    res = await flow.async_step_finish_model({"model": "m"})
    assert res["type"] == "form"
    assert res["errors"]["base"] == "unknown"


async def _models(*args, **kwargs):
    return ["m", "n"]


def _flow(config_flow, fake_hass):
    flow = config_flow.JarvisOptionsFlow(_Entry())
    flow.hass = fake_hass
    return flow


async def test_step_init_renders_menu(config_flow, fake_hass):
    # init is now a landing menu (not a form) — jump to any section directly
    res = await _flow(config_flow, fake_hass).async_step_init(None)
    assert res["type"] == "menu" and res["step_id"] == "init"
    assert set(res["menu_options"]) == {
        "llm", "core", "routing", "observer", "identity", "email", "agents"}


async def test_step_agents_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_agents(None)
    assert res["type"] == "form" and res["step_id"] == "agents"
    # friday toggle, friday confirm, proximity, host telemetry
    assert len(res["data_schema"].schema) == 4


async def test_step_agents_enabling_friday_without_confirm_is_rejected(config_flow, fake_hass):
    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_agents({"friday_automator": True, "friday_confirm": False})
    assert res["type"] == "form" and res["step_id"] == "agents"
    assert res["errors"]["base"] == "friday_confirm_required"


async def test_step_agents_enabling_friday_with_confirm_saves(config_flow, fake_hass):
    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_agents({"friday_automator": True, "friday_confirm": True})
    assert res["type"] == "create_entry"
    # the acknowledgement checkbox is not persisted as config
    assert "friday_confirm" not in flow._data


async def test_step_agents_disabling_friday_needs_no_confirm(config_flow, fake_hass):
    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_agents({"friday_automator": False, "proximity_volume": True})
    assert res["type"] == "create_entry"


async def test_step_core_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_core(None)
    assert res["type"] == "form" and res["step_id"] == "core"
    assert len(res["data_schema"].schema) == 6   # persona, preset, directive, model, hass-api, web-research-fallback


async def test_step_routing_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_routing(None)
    assert len(res["data_schema"].schema) == 3


async def test_step_observer_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_observer(None)
    assert res["type"] == "menu"
    assert set(res["menu_options"]) == {
        "classifier", "reasoning", "review", "observer_settings", "back",
    }


async def test_step_identity_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_identity(None)
    assert len(res["data_schema"].schema) == 5   # enabled, voice-fp, source, auto-enroll, min-confidence


async def test_no_section_step_is_an_empty_stub(config_flow, fake_hass):
    # init is a menu now; the four section steps must each render real fields
    flow = _flow(config_flow, fake_hass)
    for step in (flow.async_step_core, flow.async_step_routing,
                 flow.async_step_identity):
        res = await step(None)
        assert res["type"] == "form"
        assert len(res["data_schema"].schema) > 0   # never an empty form


async def test_section_saves_independently(config_flow, fake_hass):
    # submitting a section creates the entry immediately (menu flow — each
    # section saves on its own rather than chaining to the next step)
    flow = _flow(config_flow, fake_hass)
    flow._available_models = _models
    res = await flow.async_step_core({"honorific": "boss", "llm_provider": "groq"})
    assert res["step_id"] == "core_model"
    res = await flow.async_step_core_model({"model": "x"})
    assert res["type"] == "create_entry"
    # only the submitted keys are carried in _data (other sections untouched)
    assert flow._data == {"honorific": "boss", "llm_provider": "groq", "model": "x"}


async def test_llm_menu_lists_providers_and_main(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_llm(None)
    assert res["type"] == "menu" and res["step_id"] == "llm"
    assert set(res["menu_options"]) == {
        "groq", "openai", "anthropic", "gemini", "custom", "ollama", "back",
    }


async def test_llm_menu_back_returns_to_main_configure_menu(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_back(None)
    assert res["type"] == "menu" and res["step_id"] == "init"


async def test_llm_groq_step_saves_and_loops_back_to_menu(
    config_flow, fake_hass, monkeypatch, load,
):
    llm_provider = load("llm_provider")
    jarvis_config = load("jarvis_config")

    async def _ok(hass, provider, api_key, model, base_url):
        return None
    ha_secrets = load("ha_secrets")
    async def _fake_set(hass, provider, value):
        return True
    monkeypatch.setattr(llm_provider, "test_connection", _ok)
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fake_set)
    monkeypatch.setattr(jarvis_config, "set_many", lambda updates: None)

    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_groq({"api_key": "gsk_new"})
    assert res["type"] == "menu" and res["step_id"] == "llm"


async def test_llm_groq_step_shows_error_when_secret_write_fails(
    config_flow, fake_hass, monkeypatch, load,
):
    llm_provider = load("llm_provider")
    ha_secrets = load("ha_secrets")

    async def _ok(hass, provider, api_key, model, base_url):
        return None

    async def _fail_set(hass, provider, value):
        return False

    monkeypatch.setattr(llm_provider, "test_connection", _ok)
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fail_set)

    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_groq({"api_key": "gsk_new"})
    assert res["type"] == "form" and res["errors"]["base"] == "unknown"


async def test_llm_groq_step_shows_error_on_bad_key(config_flow, fake_hass, monkeypatch, load):
    llm_provider = load("llm_provider")

    async def _bad(hass, provider, api_key, model, base_url):
        return "invalid_auth"
    monkeypatch.setattr(llm_provider, "test_connection", _bad)

    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_groq({"api_key": "wrong"})
    assert res["type"] == "form" and res["errors"]["base"] == "invalid_auth"


async def test_observer_tier_uses_provider_then_live_model(config_flow, fake_hass):
    flow = _flow(config_flow, fake_hass)
    flow._available_models = _models
    res = await flow.async_step_classifier(None)
    assert res["type"] == "form"
    res = await flow.async_step_classifier({"classifier_provider": "groq"})
    assert res["step_id"] == "classifier_model"
    res = await flow.async_step_classifier_model({"classifier_model": "m"})
    assert res["type"] == "menu" and res["step_id"] == "observer"


async def test_import_allows_blank_ollama_url(config_flow, fake_hass):
    flow = config_flow.JarvisConfigFlow()
    flow.hass = fake_hass
    res = await flow.async_step_import({"llm_provider": "ollama"})
    assert res["type"] == "create_entry"
    assert res["data"]["llm_provider"] == "ollama"


async def test_observer_tier_save_updates_live_runtime_config(config_flow, fake_hass):
    entry = _Entry()
    fake_hass.data.setdefault(config_flow.DOMAIN, {})["entry-1"] = {
        "runtime_config": {
            "classifier_provider": "groq",
            "classifier_model": "old-model",
        },
    }
    entry.entry_id = "entry-1"
    flow = config_flow.JarvisOptionsFlow(entry)
    flow.hass = fake_hass
    flow._available_models = _models

    await flow.async_step_classifier({"classifier_provider": "openai"})
    await flow.async_step_classifier_model({"classifier_model": "new-model"})

    live = fake_hass.data[config_flow.DOMAIN]["entry-1"]["runtime_config"]
    assert live["classifier_provider"] == "openai"
    assert live["classifier_model"] == "new-model"


async def test_observer_menu_back_returns_to_main_configure_menu(config_flow, fake_hass):
    flow = _flow(config_flow, fake_hass)
    res = await flow.async_step_observer(None)
    assert res["type"] == "menu"
    assert "back" in res["menu_options"]
    res = await flow.async_step_back(None)
    assert res["type"] == "menu" and res["step_id"] == "init"


async def test_import_migrates_selected_provider_legacy_key(config_flow, fake_hass, monkeypatch, load):
    ha_secrets = load("ha_secrets")
    calls = []

    async def _fake_set(hass, provider, value):
        calls.append((provider, value))
        return True

    monkeypatch.setattr(ha_secrets, "get_stored_provider_key_sync", lambda provider: "")
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fake_set)

    flow = config_flow.JarvisConfigFlow()
    flow.hass = fake_hass
    res = await flow.async_step_import({"llm_provider": "gemini", "gemini_api_key": "gk"})
    assert res["type"] == "create_entry"
    assert calls == [("gemini", "gk")]


async def test_import_aborts_when_cloud_key_cannot_be_persisted(config_flow, fake_hass, monkeypatch, load):
    ha_secrets = load("ha_secrets")

    monkeypatch.setattr(ha_secrets, "get_stored_provider_key_sync", lambda provider: "")
    async def _fail_set(hass, provider, value):
        return False
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fail_set)

    flow = config_flow.JarvisConfigFlow()
    flow.hass = fake_hass
    res = await flow.async_step_import({"llm_provider": "openai", "api_key": "sk-openai"})
    assert res["type"] == "abort"
    assert res["reason"] == "import_failed"


async def test_import_aborts_when_custom_legacy_key_cannot_be_persisted(
    config_flow, fake_hass, monkeypatch, load,
):
    ha_secrets = load("ha_secrets")

    monkeypatch.setattr(ha_secrets, "get_stored_provider_key_sync", lambda provider: "")
    async def _fail_set(hass, provider, value):
        return False
    monkeypatch.setattr(ha_secrets, "async_set_provider_key", _fail_set)

    flow = config_flow.JarvisConfigFlow()
    flow.hass = fake_hass
    res = await flow.async_step_import({
        "llm_provider": "custom",
        "custom_base_url": "http://local/v1",
        "api_key": "legacy-custom",
    })
    assert res["type"] == "abort"
    assert res["reason"] == "import_failed"
