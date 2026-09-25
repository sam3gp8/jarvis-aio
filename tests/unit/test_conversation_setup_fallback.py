"""The conversation platform must never do blocking config/secret I/O in the
entity constructor.

`JarvisAgent.__init__` runs on the event loop. An earlier version resolved the
fallback LLM client there via `jarvis_config.effective_config()` +
`ha_secrets.get_provider_key_sync()`, both of which read files — enough to trip
Home Assistant's blocking-I/O detector. The fallback now lives in
`async_setup_entry`, where the config read and provider construction go through
the executor and the key comes from the async secrets helper.

The entity needs a live HA conversation stack, so `async_setup_entry` is
extracted and exec'd in isolation (same technique as test_conversation_dispatch).
"""
import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "conversation.py"
_PKG = "conv_setup_stub_pkg"


def _source_segment(name: str, cls: str | None = None) -> str:
    src = SRC.read_text()
    tree = ast.parse(src)
    body = tree.body
    if cls is not None:
        body = next(
            n.body for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == cls
        )
    for n in body:
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name:
            return ast.get_source_segment(src, n)
    raise AssertionError(f"{cls or ''}.{name} not found in conversation.py")


def _build(monkeypatch, *, key="secret-key", effective=None):
    """Exec `async_setup_entry` against stub siblings; return (fn, namespace)."""
    calls = {"executor": [], "created": [], "async_key": []}

    jarvis_config = types.ModuleType(f"{_PKG}.jarvis_config")

    def effective_config(entry):
        # Blocking in reality — must only ever be reached via the executor.
        assert calls["executor"], "effective_config called outside the executor"
        return dict(effective or {"llm_provider": "openai", "model": "gpt-4o-mini"})

    jarvis_config.effective_config = effective_config

    ha_secrets = types.ModuleType(f"{_PKG}.ha_secrets")

    async def async_get_provider_key(hass, provider):
        calls["async_key"].append(provider)
        return key

    ha_secrets.async_get_provider_key = async_get_provider_key

    def _no_sync(*a, **k):
        raise AssertionError("get_provider_key_sync must not be used")

    ha_secrets.get_provider_key_sync = _no_sync

    const = types.ModuleType(f"{_PKG}.const")
    const.resolve_provider_base_url = lambda cfg, provider: f"https://{provider}.test"

    pkg = types.ModuleType(_PKG)
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, _PKG, pkg)
    for mod in (jarvis_config, ha_secrets, const):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
        setattr(pkg, mod.__name__.rsplit(".", 1)[1], mod)

    mod = types.ModuleType(f"{_PKG}.conversation")
    mod.__package__ = _PKG
    ns = mod.__dict__
    ns["DOMAIN"] = "jarvis"
    ns["CONF_MODEL"] = "model"
    ns["DEFAULT_MODEL"] = "default-model"

    def create_provider(provider, api_key, model, base_url):
        assert calls["executor"][-1] == "create_provider", (
            "create_provider called outside the executor"
        )
        client = types.SimpleNamespace(
            name=provider, api_key=api_key, model=model, base_url=base_url)
        calls["created"].append(client)
        return client

    ns["create_provider"] = create_provider

    class JarvisAgent:
        def __init__(self, hass, entry, client=None):
            self.hass, self.entry, self.client = hass, entry, client

    ns["JarvisAgent"] = JarvisAgent

    seg = "from __future__ import annotations\n" + _source_segment("async_setup_entry")
    exec(compile(seg, str(SRC), "exec"), ns)  # noqa: S102 — test harness
    return ns["async_setup_entry"], calls


def _hass(fake_hass, calls):
    original = fake_hass.async_add_executor_job

    async def tracked(func, *args):
        calls["executor"].append(getattr(func, "__name__", func))
        return await original(func, *args)

    fake_hass.async_add_executor_job = tracked
    return fake_hass


def _entry():
    return types.SimpleNamespace(entry_id="abc123", data={}, options={})


def test_fallback_resolves_client_without_blocking_constructor(fake_hass, monkeypatch):
    """No shared client: the provider is built in async_setup_entry, using the
    executor for blocking setup and the async helper for the key."""
    setup, calls = _build(
        monkeypatch,
        key="k-123",
        effective={"llm_provider": "anthropic", "model": "claude-x"},
    )
    hass = _hass(fake_hass, calls)
    hass.data["jarvis"] = {"abc123": {}}
    added = []

    asyncio.run(setup(hass, _entry(), added.append))

    assert calls["executor"] == ["effective_config", "create_provider"]
    assert calls["async_key"] == ["anthropic"]
    (entity,) = added[0]
    assert entity.client is calls["created"][0]
    assert entity.client.name == "anthropic"
    assert entity.client.api_key == "k-123"
    assert entity.client.model == "claude-x"
    assert entity.client.base_url == "https://anthropic.test"


def test_fallback_falls_back_to_default_model(fake_hass, monkeypatch):
    setup, calls = _build(monkeypatch, effective={"llm_provider": "groq"})
    hass = _hass(fake_hass, calls)
    added = []

    asyncio.run(setup(hass, _entry(), added.append))

    assert added[0][0].client.model == "default-model"


def test_shared_client_path_does_no_config_or_secret_work(fake_hass, monkeypatch):
    """Normal path: the client from hass.data is injected verbatim and neither
    the config merge nor the secrets lookup runs."""
    setup, calls = _build(monkeypatch)
    hass = _hass(fake_hass, calls)
    shared = types.SimpleNamespace(name="groq")
    hass.data["jarvis"] = {"abc123": {"client": shared}}
    added = []

    asyncio.run(setup(hass, _entry(), added.append))

    assert calls["executor"] == []
    assert calls["async_key"] == []
    assert calls["created"] == []
    assert added[0][0].client is shared


@pytest.mark.parametrize("forbidden", ["effective_config", "get_provider_key_sync"])
def test_constructor_has_no_blocking_calls(forbidden):
    """Source guard: the blocking resolvers must not reappear in __init__."""
    assert forbidden not in _source_segment("__init__", cls="JarvisAgent")
