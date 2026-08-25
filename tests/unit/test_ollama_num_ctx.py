"""Ollama num_ctx is configurable (ollama_num_ctx) with an 8192 default — v7.46.0."""
from __future__ import annotations

import sys
import types


def _install_jarvis_config(monkeypatch, value):
    """Fake jc.jarvis_config.get returning `value` for ollama_num_ctx."""
    fake = types.ModuleType("jc.jarvis_config")
    def get(key, default=None):
        return value if key == "ollama_num_ctx" else default
    fake.get = get
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", fake)
    monkeypatch.setattr(sys.modules["jc"], "jarvis_config", fake, raising=False)


def _ollama(load):
    lp = load("llm_provider")
    # _extra_body reads only module config, no instance state — build via __new__
    # so we don't construct a real OpenAI client (openai isn't in the sandbox).
    return lp, lp.OllamaProvider.__new__(lp.OllamaProvider)


def test_default_num_ctx_when_unset(load, monkeypatch):
    lp, prov = _ollama(load)
    _install_jarvis_config(monkeypatch, None)          # not configured
    body = prov._extra_body()
    assert body["options"]["num_ctx"] == lp.OLLAMA_NUM_CTX
    assert body["think"] is False                      # reasoning-model guard intact


def test_configured_num_ctx_is_used(load, monkeypatch):
    lp, prov = _ollama(load)
    _install_jarvis_config(monkeypatch, 32768)
    assert prov._extra_body()["options"]["num_ctx"] == 32768


def test_absurd_num_ctx_falls_back_to_default(load, monkeypatch):
    lp, prov = _ollama(load)
    _install_jarvis_config(monkeypatch, 16)            # too small to be real
    assert prov._extra_body()["options"]["num_ctx"] == lp.OLLAMA_NUM_CTX


def test_bad_num_ctx_value_falls_back(load, monkeypatch):
    lp, prov = _ollama(load)
    _install_jarvis_config(monkeypatch, "not-a-number")
    assert prov._extra_body()["options"]["num_ctx"] == lp.OLLAMA_NUM_CTX
