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

def test_speech_no_engine_present_is_idle_not_down(sh):
    # v6.70.3: no engine registered is a setup gap shown calmly (IDLE), not a
    # scary red DOWN — only a real transcription failure marks DOWN.
    sh._USAGE.clear()
    hass = _Hass({"tts": []})
    out = sh._check_speech_entity(hass, "tts", "TTS", "auto")
    assert out["status"] == "idle"


def test_speech_auto_picks_available(sh):
    sh._USAGE.clear()
    hass = _Hass({"stt": [_State("stt.whisper", "idle")]})
    out = sh._check_speech_entity(hass, "stt", "STT", "auto")
    assert out["status"] == "ok"
    assert out["entity"] == "stt.whisper"


def test_speech_auto_all_unavailable_is_idle_not_down(sh):
    # engines present but idle/unavailable → IDLE (they come available on
    # demand), NOT down. This was the STT false-alarm bug.
    sh._USAGE.clear()
    hass = _Hass({"tts": [_State("tts.piper", "unavailable")]})
    out = sh._check_speech_entity(hass, "tts", "TTS", "auto")
    assert out["status"] == "idle"


def test_speech_recent_real_use_beats_unavailable(sh):
    # if a real transcription just succeeded, unavailable entities still read OK
    sh._USAGE.clear()
    sh.record_usage("stt", True)
    hass = _Hass({"stt": [_State("stt.whisper", "unavailable")]})
    out = sh._check_speech_entity(hass, "stt", "STT", "auto")
    assert out["status"] == "ok"


def test_speech_real_failure_marks_down(sh):
    # a REAL stt failure during use is authoritative → DOWN
    sh._USAGE.clear()
    sh.record_usage("stt", False, "transcription timed out")
    hass = _Hass({"stt": [_State("stt.whisper", "idle")]})
    out = sh._check_speech_entity(hass, "stt", "STT", "auto")
    assert out["status"] == "down"
    assert "transcription timed out" in out["detail"]


def test_speech_specific_engine_available(sh):
    sh._USAGE.clear()
    hass = _Hass({"tts": [_State("tts.piper", "idle"), _State("tts.cloud", "idle")]})
    out = sh._check_speech_entity(hass, "tts", "TTS", "tts.piper")
    assert out["status"] == "ok" and out["entity"] == "tts.piper"


def test_speech_specific_engine_missing_is_warn(sh):
    sh._USAGE.clear()
    hass = _Hass({"tts": [_State("tts.cloud", "idle")]})
    out = sh._check_speech_entity(hass, "tts", "TTS", "tts.piper")
    assert out["status"] == "warn"
    assert "not found" in out["detail"]


def test_speech_specific_engine_unavailable_is_idle_not_down(sh):
    # v6.70.3: a configured engine reading unavailable is IDLE (comes available
    # on demand), not DOWN — unless a real request failed.
    sh._USAGE.clear()
    hass = _Hass({"stt": [_State("stt.whisper", "unavailable")]})
    out = sh._check_speech_entity(hass, "stt", "STT", "stt.whisper")
    assert out["status"] == "idle"


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
    assert len(res["services"]) == 7
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
    assert "services" in res and len(res["services"]) == 7


# ── agent tool registration ──────────────────────────────────────────────────

def test_system_diagnostics_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "system_diagnostics" in names
    assert "system_diagnostics" in agent._TOOL_MAP


# ── real-usage outcome tracking (v6.70.3) ────────────────────────────────────

def test_record_usage_success_then_ok(sh):
    sh._USAGE.clear()
    sh.record_usage("embeddings", True)
    assert sh._recently_used_ok("embeddings") is True
    assert sh._recent_real_failure("embeddings") is None


def test_record_usage_failure_marks_recent_failure(sh):
    sh._USAGE.clear()
    sh.record_usage("embeddings", False, "boom")
    assert sh._recent_real_failure("embeddings") == "boom"


def test_success_after_failure_clears_it(sh):
    sh._USAGE.clear()
    sh.record_usage("embeddings", False, "boom")
    sh.record_usage("embeddings", True)          # a later success clears the fail
    assert sh._recent_real_failure("embeddings") is None


def test_real_failure_expires(sh, monkeypatch):
    sh._USAGE.clear()
    sh.record_usage("stt", False, "old failure")
    # jump past the DOWN window
    import time as _t
    future = _t.time() + sh._REAL_FAIL_TTL + 10
    monkeypatch.setattr(sh.time, "time", lambda: future)
    assert sh._recent_real_failure("stt") is None   # no longer alarming


def test_recent_use_window_expires(sh, monkeypatch):
    sh._USAGE.clear()
    sh.record_usage("llm", True)
    import time as _t
    future = _t.time() + sh._RECENT_USE_TTL + 10
    monkeypatch.setattr(sh.time, "time", lambda: future)
    assert sh._recently_used_ok("llm") is False


def test_unknown_key_is_neutral(sh):
    sh._USAGE.clear()
    assert sh._recent_real_failure("nope") is None
    assert sh._recently_used_ok("nope") is False


async def test_retry_async_tolerates_transient_miss(sh):
    # first call misses, second succeeds → returns the success (no false DOWN)
    calls = {"n": 0}
    async def _flaky():
        calls["n"] += 1
        return {"ok": calls["n"] >= 2}
    res = await sh._retry_async(_flaky, attempts=3, delay=0)
    assert res["ok"] is True
    assert calls["n"] == 2


async def test_retry_async_all_miss_returns_last(sh):
    async def _always_miss():
        return {"ok": False, "error": "still cold"}
    res = await sh._retry_async(_always_miss, attempts=2, delay=0)
    assert res["ok"] is False


# ── embeddings check: no more false DOWN (v6.70.3) ───────────────────────────

def _install_emb_stub(monkeypatch, probe_result):
    """Install a stub jc.embeddings that _check_embeddings will import. Sets BOTH
    sys.modules AND the jc package attribute, because `from .. import embeddings`
    reads the package attribute when present. monkeypatch restores both."""
    import sys, types
    emb = types.ModuleType("jc.embeddings")
    emb.is_enabled = lambda: True
    async def _probe(h): return probe_result
    emb.probe = _probe
    monkeypatch.setitem(sys.modules, "jc.embeddings", emb)
    if "jc" in sys.modules:
        monkeypatch.setattr(sys.modules["jc"], "embeddings", emb, raising=False)
    return emb


async def test_embeddings_probe_miss_is_idle_not_down(sh, monkeypatch):
    # semantic enabled, probe keeps missing, no real usage → IDLE (was DOWN)
    sh._USAGE.clear()
    _install_emb_stub(monkeypatch, {"ok": False, "error": "cold"})
    out = await sh._check_embeddings(_Hass({}))
    assert out["status"] == "idle"


async def test_embeddings_probe_miss_but_recent_use_is_ok(sh, monkeypatch):
    sh._USAGE.clear()
    sh.record_usage("embeddings", True)          # real embed worked recently
    _install_emb_stub(monkeypatch, {"ok": False, "error": "model idle"})
    out = await sh._check_embeddings(_Hass({}))
    assert out["status"] == "ok"               # recent real success wins


async def test_embeddings_real_failure_is_down(sh, monkeypatch):
    sh._USAGE.clear()
    sh.record_usage("embeddings", False, "ingest failed")
    _install_emb_stub(monkeypatch, {"ok": True, "model": "nomic", "dim": 768})
    out = await sh._check_embeddings(_Hass({}))
    # a real failure is authoritative even if the probe now succeeds
    assert out["status"] == "down"
    assert "ingest failed" in out["detail"]


async def test_overall_idle_services_not_alarming(sh, monkeypatch):
    # a mix of ok + idle should be overall OK (idle never alarms)
    async def _llm(h): return {"name": "LLM", "key": "llm", "status": "ok", "detail": ""}
    async def _emb(h): return {"name": "Embeddings", "key": "embeddings", "status": "idle", "detail": ""}
    monkeypatch.setattr(sh, "_check_llm", _llm)
    monkeypatch.setattr(sh, "_check_embeddings", _emb)
    monkeypatch.setattr(sh, "_check_tts", lambda h: {"name": "TTS", "key": "tts", "status": "idle", "detail": ""})
    monkeypatch.setattr(sh, "_check_stt", lambda h: {"name": "STT", "key": "stt", "status": "ok", "detail": ""})
    res = await sh.run_service_health(_Hass({}))
    assert res["overall"] == "ok"              # NOT down — idle is fine
    assert "idle" in res["summary"]


# ── camera health check (v6.93.0) ────────────────────────────────────────────

def test_cameras_none_is_off(sh):
    r = sh._check_cameras(_Hass({"camera": []}))
    assert r["status"] == "off" and "no cameras" in r["detail"]


def test_cameras_all_available_ok(sh):
    r = sh._check_cameras(_Hass({"camera": [
        _State("camera.front", "idle"), _State("camera.back", "streaming")]}))
    assert r["status"] == "ok" and "2 camera" in r["detail"]


def test_cameras_some_unavailable_warn(sh):
    r = sh._check_cameras(_Hass({"camera": [
        _State("camera.front", "idle"), _State("camera.back", "unavailable")]}))
    assert r["status"] == "warn" and "1/2" in r["detail"]


def test_cameras_all_unavailable_warn(sh):
    r = sh._check_cameras(_Hass({"camera": [_State("camera.front", "unavailable")]}))
    assert r["status"] == "warn" and "all 1" in r["detail"]


async def test_overall_includes_cameras(sh, monkeypatch):
    async def _fake_async(hass):
        return {"name": "x", "key": "x", "status": "off", "detail": ""}
    monkeypatch.setattr(sh, "_check_llm", _fake_async)
    monkeypatch.setattr(sh, "_check_embeddings", _fake_async)
    monkeypatch.setattr(sh, "_check_tts", lambda hass: {"name": "TTS", "key": "tts", "status": "off", "detail": ""})
    monkeypatch.setattr(sh, "_check_stt", lambda hass: {"name": "STT", "key": "stt", "status": "off", "detail": ""})
    res = await sh.run_service_health(_Hass({"camera": [_State("camera.front", "idle")]}))
    assert "cameras" in {s["key"] for s in res["services"]}


def test_routines_check_warns_when_identity_confidence_high(sh, monkeypatch):
    monkeypatch.setattr(sh, "_cfg", lambda k, d=None: 0.95 if k == "identity_min_confidence" else d)
    out = sh._check_routines(None)
    assert out["status"] == "warn"


def test_routines_check_ok_when_identity_confidence_normal(sh, monkeypatch):
    monkeypatch.setattr(sh, "_cfg", lambda k, d=None: 0.5 if k == "identity_min_confidence" else d)
    out = sh._check_routines(None)
    assert out["status"] == "ok"


# ── conversation-store health check (v7.42.0) ─────────────────────────────────
def test_database_check_off_when_not_created(sh):
    # In the sandbox the real DB path doesn't exist -> OFF, never a false DOWN.
    out = sh._check_database(_Hass({}))
    assert out["status"] == "off"


def test_database_check_down_on_health_failure(sh, monkeypatch):
    fake = types.ModuleType("jc.database")
    fake.DB_PATH = type("P", (), {"exists": staticmethod(lambda: True)})()
    fake.health = lambda: {"ok": False, "error": "disk I/O error"}
    monkeypatch.setitem(sys.modules, "jc.database", fake)
    monkeypatch.setattr(sys.modules["jc"], "database", fake, raising=False)
    out = sh._check_database(_Hass({}))
    assert out["status"] == "down"
    assert "disk I/O" in out["detail"]


def test_database_check_ok_when_healthy(sh, monkeypatch):
    fake = types.ModuleType("jc.database")
    fake.DB_PATH = type("P", (), {"exists": staticmethod(lambda: True)})()
    fake.health = lambda: {"ok": True, "error": ""}
    monkeypatch.setitem(sys.modules, "jc.database", fake)
    monkeypatch.setattr(sys.modules["jc"], "database", fake, raising=False)
    out = sh._check_database(_Hass({}))
    assert out["status"] == "ok"
