"""Tests for jarvis_config.effective_config (v6.82.0).

The single source of truth resolver: the panel config (config.json) wins over
stale entry data/options, but only on non-empty values — so a blank panel field
can't wipe a real credential carried on the entry. This is what keeps the boot
client, conversation, and agent from diverging on which LLM to run.
"""
import pytest


@pytest.fixture
def jc(load):
    return load("jarvis_config")


class _Entry:
    def __init__(self, data=None, options=None):
        self.data = data
        self.options = options


def test_panel_wins_over_entry(jc, monkeypatch):
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"llm_provider": "ollama", "model": "gemma4:26b"})
    e = _Entry(data={"llm_provider": "gemini", "api_key": "K"},
               options={"llm_provider": "gemini", "model": "gemini-2.5-pro"})
    eff = jc.effective_config(e)
    assert eff["llm_provider"] == "ollama"      # panel wins over both entry layers
    assert eff["model"] == "gemma4:26b"         # panel wins over entry.options
    assert eff["api_key"] == "K"                # entry-only value preserved


def test_blank_panel_value_does_not_clobber_entry(jc, monkeypatch):
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"api_key": "", "llm_base_url": None, "llm_provider": "ollama"})
    e = _Entry(data={"api_key": "REALKEY", "llm_base_url": "http://x:11434"})
    eff = jc.effective_config(e)
    assert eff["api_key"] == "REALKEY"           # blank panel string didn't wipe it
    assert eff["llm_base_url"] == "http://x:11434"  # None panel value didn't wipe it
    assert eff["llm_provider"] == "ollama"


def test_options_win_over_data_when_panel_silent(jc, monkeypatch):
    monkeypatch.setattr(jc, "get_all", lambda: {})
    e = _Entry(data={"x": "from_data"}, options={"x": "from_options"})
    assert jc.effective_config(e)["x"] == "from_options"


def test_entry_only_key_survives(jc, monkeypatch):
    monkeypatch.setattr(jc, "get_all", lambda: {"model": "gemma4:26b"})
    e = _Entry(data={"honorific": "Sir"})
    eff = jc.effective_config(e)
    assert eff["honorific"] == "Sir"
    assert eff["model"] == "gemma4:26b"


def test_no_entry_returns_panel_only(jc, monkeypatch):
    monkeypatch.setattr(jc, "get_all", lambda: {"a": "1", "b": ""})
    eff = jc.effective_config(None)
    assert eff["a"] == "1"
    assert "b" not in eff                        # blank panel value skipped


def test_none_entry_layers_tolerated(jc, monkeypatch):
    monkeypatch.setattr(jc, "get_all", lambda: {"a": "1"})
    assert jc.effective_config(_Entry(data=None, options=None)) == {"a": "1"}
