"""Test harness for the JARVIS integration.

The linchpin is sequencing: the cores do `from homeassistant.core import ...`
at import time, so faithful stubs must be installed into sys.modules BEFORE any
test imports the integration. conftest.py is imported before collection, so the
module-level work here runs first.

The integration modules are loaded under a SYNTHETIC package `jc` whose __path__
points at the component directory — never the real `jarvis_component` package,
whose __init__.py would drag in the whole integration (config flow, setup, …).
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import os
import pathlib
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))  # make fakes.py importable here


# ── 1. Install faithful-but-minimal Home Assistant stubs ──────────────────────
def _install_ha_stubs() -> None:
    if "homeassistant.core" in sys.modules:
        return

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # only a type reference for annotations
        pass

    class Event:
        def __init__(self, event_type="", data=None):
            self.event_type = event_type
            self.data = data or {}

    class State:
        def __init__(self, entity_id, state, attributes=None):
            self.entity_id = entity_id
            self.state = state
            self.attributes = attributes or {}

    class ServiceCall:  # type reference only (camera.py annotations)
        def __init__(self, domain="", service="", data=None):
            self.domain = domain
            self.service = service
            self.data = data or {}

    def callback(func):
        return func

    core.HomeAssistant = HomeAssistant
    core.Event = Event
    core.State = State
    core.ServiceCall = ServiceCall
    core.callback = callback

    dt = types.ModuleType("homeassistant.util.dt")
    dt.utcnow = lambda: _dt.datetime.now(_dt.timezone.utc)
    dt.now = lambda tz=None: _dt.datetime.now(tz)
    dt.as_local = lambda v: v
    dt.parse_datetime = lambda s: None
    util = types.ModuleType("homeassistant.util")
    util.dt = dt

    er = types.ModuleType("homeassistant.helpers.entity_registry")
    dr = types.ModuleType("homeassistant.helpers.device_registry")
    er.async_get = lambda hass: types.SimpleNamespace(entities={})
    dr.async_get = lambda hass: types.SimpleNamespace(devices={})
    ac = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ac.async_get_clientsession = lambda hass: None
    net = types.ModuleType("homeassistant.helpers.network")
    net.get_url = lambda hass, **kw: "http://127.0.0.1:8123"
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.entity_registry = er
    helpers.device_registry = dr
    helpers.aiohttp_client = ac
    helpers.network = net

    cfg = types.ModuleType("homeassistant.config_entries")
    cfg.ConfigEntry = type("ConfigEntry", (), {})

    components = types.ModuleType("homeassistant.components")
    comp_camera = types.ModuleType("homeassistant.components.camera")

    async def _stub_get_image(hass, entity_id, timeout=10):
        return None
    comp_camera.async_get_image = _stub_get_image
    components.camera = comp_camera

    const = types.ModuleType("homeassistant.const")
    const.__getattr__ = lambda name: name  # any HA const → its own name

    ha = types.ModuleType("homeassistant")
    ha.core, ha.util, ha.helpers, ha.config_entries, ha.const = (
        core, util, helpers, cfg, const)

    for name, mod in {
        "homeassistant": ha,
        "homeassistant.core": core,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_registry": er,
        "homeassistant.helpers.device_registry": dr,
        "homeassistant.helpers.aiohttp_client": ac,
        "homeassistant.helpers.network": net,
        "homeassistant.config_entries": cfg,
        "homeassistant.const": const,
        "homeassistant.components": components,
        "homeassistant.components.camera": comp_camera,
    }.items():
        sys.modules[name] = mod


_install_ha_stubs()


# ── 2. Synthetic package + stubs for the I/O siblings ─────────────────────────
ROOT = pathlib.Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "jarvis"

if "jc" not in sys.modules:
    _pkg = types.ModuleType("jc")
    _pkg.__path__ = [str(COMP)]
    sys.modules["jc"] = _pkg

    # websocket.jarvis_log is called lazily for logging — no-op it.
    _ws = types.ModuleType("jc.websocket")
    _ws.jarvis_log = lambda *a, **k: None
    sys.modules["jc.websocket"] = _ws

    # directive_helper pulls in const/config_entries to build prompts; stub the
    # one function reasoning_loop imports so we don't load that whole chain.
    _dh = types.ModuleType("jc.directive_helper")
    _dh.build_system_prompt = lambda *a, **k: "You are JARVIS. Respond with JSON only."
    sys.modules["jc.directive_helper"] = _dh


def _load(modname: str):
    """Import a component module under the synthetic `jc` package, so its
    relative imports (`from .websocket import …`, `from . import reasoning_cache`)
    resolve to our `jc.*` stubs and to single shared instances.

    Note: we deliberately do NOT pass submodule_search_locations. Doing so would
    make each module a *package* whose relative imports resolve under its own
    name (jc.reasoning_loop.reasoning_cache), creating duplicate module copies
    that defeat monkeypatching. As plain modules their __package__ is "jc", so
    `from . import X` resolves to jc.X via the jc package __path__."""
    key = f"jc.{modname}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, COMP / f"{modname}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 3. Fixtures ───────────────────────────────────────────────────────────────
import pytest  # noqa: E402
from fakes import FakeHass, FakeProvider  # noqa: E402


@pytest.fixture
def load():
    """Return the component-module loader (call as load('cognitive_core'))."""
    return _load


@pytest.fixture
def fake_hass():
    """A fresh fake Home Assistant core (named `fake_hass` to avoid colliding
    with pytest-homeassistant-custom-component's real `hass` fixture used in
    tests/integration/)."""
    return FakeHass()


@pytest.fixture
def provider_factory():
    return FakeProvider


@pytest.fixture
def cognitive_core():
    return _load("cognitive_core")


@pytest.fixture
def reasoning_loop():
    return _load("reasoning_loop")


@pytest.fixture
def connectivity():
    return _load("connectivity")
