"""Ollama keep-warm: pin JARVIS's exact local model resident so the first turn
isn't a multi-minute cold load (MCU latency work).

Two layers are covered:
  * ``ollama_warm.warm_sync`` — the provider-agnostic dispatcher: no-ops for
    cloud providers and when the kill-switch / config toggle is off, else calls
    the provider's ``warm()``.
  * ``OllamaProvider.warm()`` — pins the model with an indefinite keep_alive at
    the SAME num_ctx real turns use, and is fully best-effort.
"""
from __future__ import annotations

import sys
import types

import pytest


# ── fakes ────────────────────────────────────────────────────────────────────

class _Completions:
    def __init__(self, recorder, raise_exc=None):
        self._rec = recorder
        self._raise = raise_exc

    def create(self, **kwargs):
        self._rec.append(kwargs)
        if self._raise:
            raise self._raise
        return object()               # warm() ignores the response body


class _FakeOpenAIClient:
    def __init__(self, recorder, raise_exc=None, with_options=True):
        self.chat = types.SimpleNamespace(completions=_Completions(recorder, raise_exc))
        self._with_options = with_options

    def with_options(self, **kw):
        if not self._with_options:
            raise RuntimeError("no with_options on this client")
        return self                    # pretend to apply the longer timeout


class _FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _StubProvider:
    """Minimal stand-in for a provider in warm_sync dispatch tests."""
    def __init__(self, name, warm_result=None, warm_raises=None, has_warm=True):
        self.name = name
        self._warm_result = warm_result
        self._warm_raises = warm_raises
        if has_warm:
            self.warm = self._warm

    def _warm(self):
        if self._warm_raises:
            raise self._warm_raises
        return self._warm_result


# ── warm_sync dispatch ───────────────────────────────────────────────────────

@pytest.fixture
def ow(load):
    return load("ollama_warm")


def test_warm_sync_calls_ollama_provider(ow):
    out = ow.warm_sync(_StubProvider("ollama", warm_result={"ok": True, "model": "gemma"}))
    assert out == {"ok": True, "model": "gemma"}


def test_warm_sync_skips_cloud_provider(ow):
    out = ow.warm_sync(_StubProvider("groq", warm_result={"ok": True}))
    assert out == {"skipped": "not-ollama"}


def test_warm_sync_skips_ollama_without_warm_method(ow):
    out = ow.warm_sync(_StubProvider("ollama", has_warm=False))
    assert out == {"skipped": "not-ollama"}


def test_warm_sync_module_kill_switch(ow, monkeypatch):
    monkeypatch.setattr(ow, "OLLAMA_WARM_ENABLED", False)
    out = ow.warm_sync(_StubProvider("ollama", warm_result={"ok": True}))
    assert out == {"skipped": "disabled"}


def test_warm_sync_config_toggle_off(ow, monkeypatch):
    fake = types.ModuleType("jc.jarvis_config")
    fake.get = lambda key, default=None: False if key == "ollama_keep_warm" else default
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", fake)
    monkeypatch.setattr(sys.modules["jc"], "jarvis_config", fake, raising=False)
    out = ow.warm_sync(_StubProvider("ollama", warm_result={"ok": True}))
    assert out == {"skipped": "disabled"}


def test_warm_sync_survives_provider_fault(ow):
    out = ow.warm_sync(_StubProvider("ollama", warm_raises=RuntimeError("boom")))
    assert out["ok"] is False and "boom" in out["error"]


def test_warm_sync_tolerates_warm_returning_none(ow):
    out = ow.warm_sync(_StubProvider("ollama", warm_result=None))
    assert out == {"skipped": "no-result"}


# ── async_warm wrapper ───────────────────────────────────────────────────────

async def test_async_warm_returns_result(ow):
    out = await ow.async_warm(_FakeHass(), _StubProvider("ollama", warm_result={"ok": True, "model": "m", "ms": 5}))
    assert out["ok"] is True


async def test_async_warm_skips_cloud(ow):
    out = await ow.async_warm(_FakeHass(), _StubProvider("openai"))
    assert out == {"skipped": "not-ollama"}


# ── async_warm also warms the fast-chat tier's extra model ───────────────────

class _RecordingProvider:
    """Ollama-like provider recording every model it was asked to warm."""
    name = "ollama"

    def __init__(self):
        self.warmed = []

    def warm(self, model=None):
        self.warmed.append(model)                 # None = this provider's own model
        return {"ok": True, "model": model or "main", "ms": 1}


async def test_async_warm_warms_extra_models(ow):
    prov = _RecordingProvider()
    await ow.async_warm(_FakeHass(), prov, extra_models=["gemma3n:e4b"])
    # the main model (None → own model) plus the fast model
    assert prov.warmed == [None, "gemma3n:e4b"]


async def test_async_warm_dedups_and_skips_blank_extras(ow):
    prov = _RecordingProvider()
    await ow.async_warm(_FakeHass(), prov, extra_models=["", "  ", None])
    assert prov.warmed == [None]                   # only the main warm ran


async def test_async_warm_no_extras_when_main_skipped(ow, monkeypatch):
    # provider isn't Ollama → main warm is skipped → extras must not be chased
    prov = _StubProvider("openai")
    out = await ow.async_warm(_FakeHass(), prov, extra_models=["gemma3n:e4b"])
    assert out == {"skipped": "not-ollama"}


# ── OllamaProvider.warm() ────────────────────────────────────────────────────

def _ollama_provider(load, recorder, **client_kw):
    lp = load("llm_provider")
    prov = lp.OllamaProvider.__new__(lp.OllamaProvider)   # no real OpenAI client
    prov.model = "gemma4:26b"
    prov._client = _FakeOpenAIClient(recorder, **client_kw)
    return lp, prov


def test_provider_warm_pins_indefinitely_at_real_num_ctx(load):
    rec: list = []
    lp, prov = _ollama_provider(load, rec)
    out = prov.warm()
    assert out["ok"] is True and out["model"] == "gemma4:26b" and out["ms"] >= 0
    assert len(rec) == 1
    call = rec[0]
    # a 1-token no-op on the right model
    assert call["model"] == "gemma4:26b"
    assert call["max_tokens"] == 1
    body = call["extra_body"]
    assert body["keep_alive"] == lp.OLLAMA_WARM_KEEP_ALIVE == -1   # pinned forever
    # SAME context window real turns use — otherwise Ollama warms a different
    # resident instance than the one a turn would hit.
    assert body["options"]["num_ctx"] == lp.OLLAMA_NUM_CTX


def test_provider_warm_is_fail_safe_on_error(load):
    rec: list = []
    lp, prov = _ollama_provider(load, rec, raise_exc=RuntimeError("ollama down"))
    out = prov.warm()
    assert out["ok"] is False and "ollama down" in out["error"] and out["model"] == "gemma4:26b"


def test_provider_warm_without_with_options(load):
    # A client that can't take a per-call timeout must still warm (falls back to
    # the base client rather than failing).
    rec: list = []
    lp, prov = _ollama_provider(load, rec, with_options=False)
    out = prov.warm()
    assert out["ok"] is True and len(rec) == 1
