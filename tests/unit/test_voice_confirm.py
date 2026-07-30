"""Tests for voice confirmation (v6.67.0). The safety-critical property is the
fail-safe: a confirmation that isn't clearly affirmative must return False so the
protected action does NOT run. Also covers which actions are protected, mode
selection, and the yes/no sentence sets."""
import pytest


@pytest.fixture
def vc(load, monkeypatch):
    m = load("voice_confirm")
    return m


def _cfg_map(mapping):
    return lambda hass, k, d=None: mapping.get(k, d)


class _Hass:
    """Minimal hass; services.async_call is a recording no-op by default."""
    def __init__(self):
        self.calls = []
        self._states = {}
        outer = self
        class _Svc:
            async def async_call(self, dom, name, data, **kw):
                outer.calls.append((dom, name, data, kw))
                return outer._call_result
        class _States:
            def async_all(self, domain):
                return []
            def get(self, eid):
                return outer._states.get(eid)
        self.services = _Svc()
        self.states = _States()
        self._call_result = None


# ── enablement + protection ──────────────────────────────────────────────────

def test_disabled_by_default(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({}))
    assert vc.is_enabled(_Hass()) is False


def test_protected_requires_enabled(vc, monkeypatch):
    # even a lock/unlock isn't protected if the feature is off
    monkeypatch.setattr(vc, "_cfg", _cfg_map({}))
    assert vc.action_is_protected(_Hass(), "lock", "unlock", "lock.front") is False


def test_lock_unlock_is_protected_when_enabled(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    assert vc.action_is_protected(_Hass(), "lock", "unlock", "lock.front") is True


def test_light_toggle_is_not_protected(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    assert vc.action_is_protected(_Hass(), "light", "turn_on", "light.kitchen") is False


def test_garage_open_is_protected(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    assert vc.action_is_protected(_Hass(), "cover", "open", "cover.garage") is True


def test_alarm_disarm_is_protected(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    assert vc.action_is_protected(_Hass(), "alarm_control_panel", "alarm_disarm", "alarm_control_panel.home") is True


def test_switch_off_only_protected_for_security(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    assert vc.action_is_protected(_Hass(), "switch", "turn_off", "switch.alarm_siren") is True
    assert vc.action_is_protected(_Hass(), "switch", "turn_off", "switch.desk_lamp") is False


def test_entity_override_exempts(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({
        "voice_confirm_enabled": True,
        "voice_confirm_entities": ["!lock.test_deadbolt"]}))
    # explicit exemption wins even for a normally-protected action
    assert vc.action_is_protected(_Hass(), "lock", "unlock", "lock.test_deadbolt") is False


def test_entity_override_includes(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({
        "voice_confirm_enabled": True,
        "voice_confirm_entities": ["switch.pool_pump"]}))
    # a normally-unprotected switch can be opted in
    assert vc.action_is_protected(_Hass(), "switch", "turn_off", "switch.pool_pump") is True


# ── mode selection ───────────────────────────────────────────────────────────

def test_mode_defaults_to_auto(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({}))
    assert vc._mode(_Hass()) == "auto"


def test_mode_bad_value_falls_back_auto(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_mode": "nonsense"}))
    assert vc._mode(_Hass()) == "auto"


def test_mode_explicit_gated(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_mode": "gated"}))
    assert vc._mode(_Hass()) == "gated"


# ── fail-safe confirm behavior ───────────────────────────────────────────────

async def test_confirm_no_satellite_returns_false(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    monkeypatch.setattr(vc, "_satellite_for_entity", lambda h, e: None)
    # no satellite → cannot confirm → fail-safe False (action won't run)
    assert await vc.confirm(_Hass(), "sure?", entity_id="lock.front") is False


async def test_confirm_native_affirmative_returns_true(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({
        "voice_confirm_enabled": True, "voice_confirm_mode": "native"}))
    monkeypatch.setattr(vc, "_satellite_for_entity", lambda h, e: "assist_satellite.basement")
    async def _native_yes(h, s, q, t): return True
    monkeypatch.setattr(vc, "_confirm_native", _native_yes)
    assert await vc.confirm(_Hass(), "sure?", entity_id="lock.front") is True


async def test_confirm_native_denial_returns_false(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({
        "voice_confirm_enabled": True, "voice_confirm_mode": "native"}))
    monkeypatch.setattr(vc, "_satellite_for_entity", lambda h, e: "assist_satellite.basement")
    async def _native_no(h, s, q, t): return False
    monkeypatch.setattr(vc, "_confirm_native", _native_no)
    assert await vc.confirm(_Hass(), "sure?", entity_id="lock.front") is False


async def test_confirm_gated_never_auto_approves(vc, monkeypatch):
    # gated path voices the prompt + reopens mic but cannot itself approve →
    # always False (the follow-up turn completes the action). Fail-safe.
    monkeypatch.setattr(vc, "_cfg", _cfg_map({
        "voice_confirm_enabled": True, "voice_confirm_mode": "gated"}))
    monkeypatch.setattr(vc, "_satellite_for_entity", lambda h, e: "assist_satellite.basement")
    monkeypatch.setattr(vc, "_speaker_for_satellite", lambda h, s: ("tts.piper", ["media_player.nest"]))
    async def _noop_announce(*a, **k): return None
    async def _noop_wait(*a, **k): return None
    async def _noop_start(*a, **k): return True
    # patch the tts_helper import target + waits
    import sys, types
    th = types.ModuleType("jc.tts_helper")
    th.async_announce = _noop_announce
    sys.modules["jc.tts_helper"] = th
    monkeypatch.setattr(vc, "_wait_for_playback", _noop_wait)
    monkeypatch.setattr(vc, "_start_listening", _noop_start)
    try:
        assert await vc.confirm(_Hass(), "sure?", entity_id="lock.front") is False
    finally:
        sys.modules.pop("jc.tts_helper", None)


async def test_confirm_exception_is_failsafe_false(vc, monkeypatch):
    monkeypatch.setattr(vc, "_cfg", _cfg_map({"voice_confirm_enabled": True}))
    def _boom(h, e): raise RuntimeError("x")
    monkeypatch.setattr(vc, "_satellite_for_entity", _boom)
    assert await vc.confirm(_Hass(), "sure?", entity_id="lock.front") is False


# ── sentence sets ────────────────────────────────────────────────────────────

def test_yes_no_sentence_sets_are_disjoint(vc):
    assert not (set(vc._YES) & set(vc._NO))       # no overlap
    assert "yes" in vc._YES and "no" in vc._NO
    assert "unlock it" in vc._YES and "cancel" in vc._NO
