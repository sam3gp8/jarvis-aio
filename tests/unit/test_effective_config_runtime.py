"""effective_config_with_runtime — the accessor subsystem restarts now use.

The point of Option A: a subsystem (re)started from config must see config.json
settings even on a panel install (empty entry.data/options), with live panel
runtime_config on top. The old `{**entry.data, **entry.options}` dropped every
config.json value — this pins that it no longer does.
"""
import pytest


@pytest.fixture
def jc(load):
    return load("jarvis_config")


class _Entry:
    def __init__(self, data=None, options=None):
        self.data = data or {}
        self.options = options or {}


def _passthrough_secrets(jc, load, monkeypatch):
    hs = load("ha_secrets")
    monkeypatch.setattr(hs, "overlay_credentials", lambda d: d)


def test_includes_configjson_and_runtime_precedence(jc, load, monkeypatch):
    _passthrough_secrets(jc, load, monkeypatch)
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"llm_provider": "groq", "model": "x", "blank": ""})
    entry = _Entry(data={"honorific": "sir"}, options={"model": "opt-model"})
    cfg = jc.effective_config_with_runtime(entry, {"model": "rt-model", "empty": ""})
    assert cfg["honorific"] == "sir"          # entry.data
    assert cfg["llm_provider"] == "groq"      # config.json — the value the old merge dropped
    assert cfg["model"] == "rt-model"         # runtime_config wins
    assert "blank" not in cfg                 # blank config.json value is skipped
    assert "empty" not in cfg                 # blank runtime value doesn't add/clobber


def test_no_runtime_equals_effective_config(jc, load, monkeypatch):
    _passthrough_secrets(jc, load, monkeypatch)
    monkeypatch.setattr(jc, "get_all", lambda: {"a": "1"})
    entry = _Entry(options={"b": "2"})
    assert jc.effective_config_with_runtime(entry, None) == {"a": "1", "b": "2"}
