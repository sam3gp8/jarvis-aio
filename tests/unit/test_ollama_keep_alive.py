"""Configurable Ollama keep_alive (#567).

Ollama unloads an idle model after its keep_alive window. JARVIS hardcoded "30m",
so a user couldn't stop the model unloading between turns (losing the warm model
/ paying the cold-load cost). ollama_keep_alive now drives the value sent on every
real turn — a duration ("30m", "1h"), seconds, 0 (unload now) or -1 (keep forever).
"""
from __future__ import annotations

import sys
import types

import pytest


def _stub_config(monkeypatch, values: dict):
    """Install a jc.jarvis_config whose .get() reads `values` (mirrors the
    pattern in test_ollama_keep_warm). The autouse conftest fixture unloads it
    again after the test."""
    fake = types.ModuleType("jc.jarvis_config")
    fake.get = lambda key, default=None: values.get(key, default)
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", fake)
    monkeypatch.setattr(sys.modules["jc"], "jarvis_config", fake, raising=False)


def _provider(load):
    lp = load("llm_provider")
    prov = lp.OllamaProvider.__new__(lp.OllamaProvider)   # no real OpenAI client
    prov.model = "gemma4:26b"
    return lp, prov


# ── _normalize_keep_alive (pure) ─────────────────────────────────────────────

def test_default_constant_is_30m(load):
    # the parametrized defaults below assume this
    assert load("llm_provider").OLLAMA_KEEP_ALIVE == "30m"


@pytest.mark.parametrize("raw,expected", [
    # unset / blank → default
    (None, "30m"), ("", "30m"), ("   ", "30m"),
    # duration strings pass through
    ("30m", "30m"), ("1h", "1h"), ("90m", "90m"),
    # bare integers (incl. the -1 "forever" and 0 "unload now" sentinels) → int
    ("-1", -1), ("0", 0), ("600", 600),
    (-1, -1), (0, 0), (300, 300),
    # bool is an int subclass but never a valid keep_alive → default
    (True, "30m"), (False, "30m"),
])
def test_normalize_keep_alive(load, raw, expected):
    assert load("llm_provider")._normalize_keep_alive(raw) == expected


# ── _extra_body wiring ───────────────────────────────────────────────────────

def test_extra_body_defaults_to_30m_when_unset(load, monkeypatch):
    lp, prov = _provider(load)
    _stub_config(monkeypatch, {})
    assert prov._extra_body()["keep_alive"] == "30m"


def test_extra_body_honours_minus_one(load, monkeypatch):
    lp, prov = _provider(load)
    _stub_config(monkeypatch, {"ollama_keep_alive": "-1"})
    body = prov._extra_body()
    assert body["keep_alive"] == -1                       # pinned forever
    assert body["options"]["num_ctx"] == lp.OLLAMA_NUM_CTX  # num_ctx default intact


def test_extra_body_honours_duration_string(load, monkeypatch):
    lp, prov = _provider(load)
    _stub_config(monkeypatch, {"ollama_keep_alive": "2h"})
    assert prov._extra_body()["keep_alive"] == "2h"


def test_extra_body_keep_alive_independent_of_num_ctx(load, monkeypatch):
    lp, prov = _provider(load)
    _stub_config(monkeypatch, {"ollama_keep_alive": "0", "ollama_num_ctx": 16384})
    body = prov._extra_body()
    assert body["keep_alive"] == 0                        # unload immediately
    assert body["options"]["num_ctx"] == 16384
