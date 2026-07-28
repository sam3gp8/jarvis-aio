"""Tests for core service-health diagnostics (v6.60.0). The module lives in the
diagnostics package and uses `from .. import` for config; we load it with a
minimal package context and exercise the checks that take hass/state directly
(TTS/STT entity health, redaction, the overall aggregate) plus tool wiring."""
import importlib.util
import pathlib
import sys
import types

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def sh():
    """Load diagnostics.service_health with a stub parent package so its
    `from .. import jarvis_config` resolves without pulling the whole tree."""
    # stub parent 'jc' with a jarvis_config that reads an overridable dict
    if "jc" not in sys.modules:
        pkg = types.ModuleType("jc")
        pkg.__path__ = [str(COMP)]
        sys.modules["jc"] = pkg
    cfg_store = {}
    jc_cfg = types.ModuleType("jc.jarvis_config")
    jc_cfg.get = lambda k, d=None: cfg_store.get(k, d)
    sys.modules["jc.jarvis_config"] = jc_cfg
    # diagnostics subpackage
    if "jc.diagnostics" not in sys.modules:
        dpkg = types.ModuleType("jc.diagnostics")
        dpkg.__path__ = [str(COMP / "diagnostics")]
        sys.modules["jc.diagnostics"] = dpkg
    key = "jc.diagnostics.service_health"
    if key in sys.modules:
        del sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        key, COMP / "diagnostics" / "service_health.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    mod._cfg_store = cfg_store
    return mod


class _State:
    def __init__(self, entity_id, state):
        self.entity_id = entity_id
        self.state = state


class _States:
    def __init__(self, by_domain):
        self._by = by_domain
    def async_all(self, domain):
        return self._by.get(domain, [])


class _Hass:
    def __init__(self, by_domain):
        self.states = _States(by_domain)


# ── redaction ────────────────────────────────────────────────────────────────

def test_redact_strips_credentials(sh):
    assert sh._redact("http://user:pass@host:11434") == "http://host:11434"


def test_redact_leaves_plain_url(sh):
    assert sh._redact("http://gpu.local:11434") == "http://gpu.local:11434"


# ── TTS/STT entity checks ────────────────────────────────────────────────────

def test_speech_no_engine_present_is_down(sh):
    hass = _Hass({"tts": []})
    out = sh._check_speech_entity(hass, "tts", "TTS", "auto")
    assert out["status"] == "down"
    assert "no tts engine" in out["detail"]


def test_speech_auto_picks_available(sh):
    hass = _Hass({"stt": [_State("stt.whisper", "idle")]})
    out = sh._check_speech_entity(hass, "stt", "STT", "auto")
    assert out["status"] == "ok"
    assert out["entity"] == "stt.whisper"


def test_speech_auto_all_unavailable_is_down(sh):
    hass = _Hass({"tts": [_State("tts.piper", "unavailable")]})
    out = sh._check_speech_entity(hass, "tts", "TTS", "auto")
    assert out["status"] == "down"


def test_speech_specific_engine_available(sh):
    hass = _Hass({"tts": [_State("tts.piper", "idle"), _State("tts.cloud", "idle")]})
    out = sh._check_speech_entity(hass, "tts", "TTS", "tts.piper")
    assert out["status"] == "ok" and out["entity"] == "tts.piper"


def test_speech_specific_engine_missing_is_warn(sh):
    hass = _Hass({"tts": [_State("tts.cloud", "idle")]})
    out = sh._check_speech_entity(hass, "tts", "TTS", "tts.piper")
    assert out["status"] == "warn"
    assert "not found" in out["detail"]


def test_speech_specific_engine_unavailable_is_down(sh):
    hass = _Hass({"stt": [_State("stt.whisper", "unavailable")]})
    out = sh._check_speech_entity(hass, "stt", "STT", "stt.whisper")
    assert out["status"] == "down"


# ── aggregate overall status ─────────────────────────────────────────────────

async def test_aggregate_overall_down_when_any_active_down(sh, monkeypatch):
    async def _llm(h): return {"name": "LLM", "key": "llm", "status": "ok", "detail": ""}
    async def _emb(h): return {"name": "Embeddings", "key": "embeddings", "status": "off", "detail": ""}
    monkeypatch.setattr(sh, "_check_llm", _llm)
    monkeypatch.setattr(sh, "_check_embeddings", _emb)
    monkeypatch.setattr(sh, "_check_tts", lambda h: {"name": "TTS", "key": "tts", "status": "down", "detail": "x"})
    monkeypatch.setattr(sh, "_check_stt", lambda h: {"name": "STT", "key": "stt", "status": "ok", "detail": ""})
    res = await sh.run_service_health(_Hass({}))
    assert res["overall"] == "down"
    assert len(res["services"]) == 4
    # 'off' services excluded from the healthy count
    assert "healthy" in res["summary"]


async def test_aggregate_overall_ok_when_all_active_ok(sh, monkeypatch):
    async def _llm(h): return {"name": "LLM", "key": "llm", "status": "ok", "detail": ""}
    async def _emb(h): return {"name": "Embeddings", "key": "embeddings", "status": "off", "detail": ""}
    monkeypatch.setattr(sh, "_check_llm", _llm)
    monkeypatch.setattr(sh, "_check_embeddings", _emb)
    monkeypatch.setattr(sh, "_check_tts", lambda h: {"name": "TTS", "key": "tts", "status": "ok", "detail": ""})
    monkeypatch.setattr(sh, "_check_stt", lambda h: {"name": "STT", "key": "stt", "status": "ok", "detail": ""})
    res = await sh.run_service_health(_Hass({}))
    assert res["overall"] == "ok"


async def test_aggregate_never_raises_on_check_error(sh, monkeypatch):
    async def _boom(h): raise RuntimeError("kaboom")
    monkeypatch.setattr(sh, "_check_llm", _boom)
    monkeypatch.setattr(sh, "_check_embeddings", _boom)
    monkeypatch.setattr(sh, "_check_tts", lambda h: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(sh, "_check_stt", lambda h: {"name": "STT", "key": "stt", "status": "ok", "detail": ""})
    res = await sh.run_service_health(_Hass({}))       # must not raise
    assert "services" in res and len(res["services"]) == 4


# ── agent tool registration ──────────────────────────────────────────────────

def test_system_diagnostics_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "system_diagnostics" in names
    assert "system_diagnostics" in agent._TOOL_MAP
