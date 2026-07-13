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

    class OptionsFlow:
        def async_show_form(self, **kw):
            return {"type": "form", **kw}

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

def test_find_config_reads_runtime_path_only(config_flow, tmp_path, monkeypatch):
    """Auto-import reads /config/jarvis/config.json (the panel's runtime
    store, for zero-touch re-installs). The legacy add-on path is gone."""
    assert not hasattr(config_flow, "_CONFIG_PATHS")   # legacy list removed
    runtime = tmp_path / "config.json"
    runtime.write_text('{"api_key": "gsk_test", "model": "llama"}')
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH", str(runtime))
    cfg = config_flow._find_config()
    assert cfg and cfg["api_key"] == "gsk_test"


def test_find_config_requires_usable_llm(config_flow, tmp_path, monkeypatch):
    runtime = tmp_path / "config.json"
    runtime.write_text('{"honorific": "sir"}')        # no key, no local URL
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH", str(runtime))
    assert config_flow._find_config() is None


def test_find_config_missing_file_is_none(config_flow, tmp_path, monkeypatch):
    monkeypatch.setattr(config_flow, "_RUNTIME_CONFIG_PATH",
                        str(tmp_path / "nope.json"))
    assert config_flow._find_config() is None


def _flow(config_flow, fake_hass):
    flow = config_flow.JarvisOptionsFlow(_Entry())
    flow.hass = fake_hass
    return flow


async def test_step_init_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_init(None)
    assert res["type"] == "form" and res["step_id"] == "init"
    assert len(res["data_schema"].schema) == 5   # persona, preset, directive, model, hass-api


async def test_step_routing_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_routing(None)
    assert len(res["data_schema"].schema) == 3


async def test_step_observer_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_observer(None)
    assert len(res["data_schema"].schema) == 7


async def test_step_identity_renders_fields(config_flow, fake_hass):
    res = await _flow(config_flow, fake_hass).async_step_identity(None)
    assert len(res["data_schema"].schema) == 5   # enabled, voice-fp, source, auto-enroll, min-confidence


async def test_no_step_is_an_empty_stub(config_flow, fake_hass):
    flow = _flow(config_flow, fake_hass)
    for step in (flow.async_step_init, flow.async_step_routing,
                 flow.async_step_observer, flow.async_step_identity):
        res = await step(None)
        assert len(res["data_schema"].schema) > 0   # never an empty form
