"""Tests for HA config-entry diagnostics (v6.70.2). The safety-critical property
is redaction: a diagnostics dump must never leak API keys, tokens, or the
address. Also verifies the function never raises so the download always yields a
file."""
import importlib.util
import pathlib
import sys

import pytest

_COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def diag():
    """Load the diagnostics package __init__.py directly (the shared _load helper
    only handles single .py modules, but diagnostics is a package)."""
    key = "jc.diagnostics"
    if key in sys.modules:
        return sys.modules[key]
    init = _COMP / "diagnostics" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        key, init, submodule_search_locations=[str(_COMP / "diagnostics")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def test_redact_strips_api_keys(diag):
    src = {
        "groq_api_key": "gsk_secret123",
        "gemini_api_key": "AItotallysecret",
        "honorific": "sir",
        "nested": {"anthropic_api_key": "sk-ant-xxx", "banter_level": 2},
    }
    out = diag._redact(src)
    assert out["groq_api_key"] == "**REDACTED**"
    assert out["gemini_api_key"] == "**REDACTED**"
    assert out["nested"]["anthropic_api_key"] == "**REDACTED**"
    # non-sensitive values pass through untouched
    assert out["honorific"] == "sir"
    assert out["nested"]["banter_level"] == 2


def test_redact_strips_tokens_and_address(diag):
    src = {"refresh_token": "1//xxx", "floor_plan_address": "123 Real St",
           "client_secret": "shh", "password": "hunter2"}
    out = diag._redact(src)
    for k in src:
        assert out[k] == "**REDACTED**", f"{k} was not redacted"


def test_redact_leaves_empty_values_alone(diag):
    # an unset key shouldn't become "**REDACTED**" (nothing to hide)
    out = diag._redact({"api_key": "", "token": None})
    assert out["api_key"] == ""
    assert out["token"] is None


def test_redact_handles_lists(diag):
    out = diag._redact({"items": [{"api_key": "x"}, {"name": "ok"}]})
    assert out["items"][0]["api_key"] == "**REDACTED**"
    assert out["items"][1]["name"] == "ok"


def test_redact_never_raises_on_weird_input(diag):
    # should degrade gracefully, not crash
    class Weird:
        def __iter__(self):
            raise RuntimeError("nope")
    assert diag._redact(Weird()) is not None    # returns something, no raise


async def test_diagnostics_entry_point_redacts_and_returns_dict(diag):
    # end-to-end: the HA entry point returns a dict with keys redacted, no raise
    class _Entry:
        version = 1
        data = {"groq_api_key": "gsk_leak", "honorific": "sir"}
        options = {"gemini_api_key": "leak2", "banter_level": 2}
    class _States:
        def async_all(self):
            return []
    class _Hass:
        states = _States()
        async def async_add_executor_job(self, fn, *a):
            return fn(*a)
    out = await diag.async_get_config_entry_diagnostics(_Hass(), _Entry())
    assert isinstance(out, dict)
    assert out["integration"] == "jarvis"
    # the API keys must not appear anywhere in the serialized dump
    import json
    dumped = json.dumps(out)
    assert "gsk_leak" not in dumped
    assert "leak2" not in dumped


def test_diagnostics_exports_entry_point(diag):
    assert hasattr(diag, "async_get_config_entry_diagnostics")
    assert "async_get_config_entry_diagnostics" in diag.__all__
