"""MCU Phase D (D2): freeze hazard mirrored into kernel.situation (parity, log-only).

Two layers:
  * the mirror/parity logic in `hazard_situation` exercised directly against a
    real SituationManager on a tmp db (like the intrusion mirror tests), and
  * an integration check that `SafetyManager._check_freeze` still returns the
    same freeze actions AND drives the mirror — proving the mirror is additive
    and the safety behaviour is unchanged.
"""
import pytest

from fakes import FakeHass


# ── direct mirror / parity logic ────────────────────────────────────────────────

@pytest.fixture
def hz(load, tmp_path, monkeypatch):
    mod = load("hazard_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(mod, "_mgr", mgr)
    monkeypatch.setattr(mod, "_freeze_situation_id", None)
    monkeypatch.setattr(mod, "_last_parity", None)
    return mod, S, mgr


def test_warning_opens_hazard_situation(hz):
    mod, S, mgr = hz
    mod.mirror_freeze_sync(FakeHass(), "warning", "34°F")
    opens = mgr.open_situations("hazard")
    assert len(opens) == 1
    sit = opens[0]
    assert sit.state == S.INVESTIGATING and sit.subject == "freeze"
    assert mod._last_parity["agree"] is True


def test_warning_then_critical_confirms(hz):
    mod, S, mgr = hz
    h = FakeHass()
    mod.mirror_freeze_sync(h, "warning", "34°F")
    mod.mirror_freeze_sync(h, "critical", "18°F")
    opens = mgr.open_situations("hazard")
    assert len(opens) == 1 and opens[0].state == S.CONFIRMED
    assert [s["to"] for s in opens[0].history] == [
        S.POSSIBLE, S.INVESTIGATING, S.CONFIRMED]
    assert mod._last_parity["actual_state"] == S.CONFIRMED
    assert mod._last_parity["agree"] is True


def test_critical_without_prior_warning(hz):
    mod, S, mgr = hz
    mod.mirror_freeze_sync(FakeHass(), "critical", "15°F")
    opens = mgr.open_situations("hazard")
    assert len(opens) == 1 and opens[0].state == S.CONFIRMED


def test_cleared_resolves_episode(hz):
    mod, S, mgr = hz
    h = FakeHass()
    mod.mirror_freeze_sync(h, "warning", "34°F")
    sid = mod._freeze_situation_id
    mod.mirror_freeze_sync(h, "cleared", "42°F")
    assert mgr.open_situations("hazard") == []
    assert mgr.get(sid).state == S.RESOLVED
    assert mod._freeze_situation_id is None
    assert mod._last_parity["action"] == "cleared"
    assert mod._last_parity["agree"] is True


def test_cleared_with_no_episode_is_not_scored(hz):
    mod, S, mgr = hz
    mod.mirror_freeze_sync(FakeHass(), "cleared", "42°F")
    assert mod._last_parity is None


def test_parity_detects_divergence(hz, monkeypatch, caplog):
    import logging
    mod, S, mgr = hz

    class _StubSit:
        state = "possible"

    class _StubMgr:
        def get(self, _id):
            return _StubSit()

    with caplog.at_level(logging.WARNING):
        mod._record_parity(_StubMgr(), "critical", "sit_lagging")
    assert mod._last_parity["agree"] is False
    assert any("DIVERGENCE" in r.message for r in caplog.records)


def test_mirror_never_raises_on_bad_manager(load, monkeypatch):
    mod = load("hazard_situation")

    class _Boom:
        def get(self, *_a, **_k):
            raise RuntimeError("db down")
    monkeypatch.setattr(mod, "_mgr", _Boom())
    monkeypatch.setattr(mod, "_freeze_situation_id", "sit_x")
    mod.mirror_freeze_sync(FakeHass(), "critical", "15°F")   # must not raise


# ── integration: freeze alerting unchanged AND the mirror is driven ─────────────

@pytest.fixture
def safety_wired(cognitive_core, load, tmp_path, monkeypatch):
    """A SafetyManager plus an isolated hazard SituationManager."""
    hazard_situation = load("hazard_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(hazard_situation, "_mgr", mgr)
    monkeypatch.setattr(hazard_situation, "_freeze_situation_id", None)
    monkeypatch.setattr(hazard_situation, "_last_parity", None)
    return cognitive_core, hazard_situation, S, mgr


async def test_check_freeze_critical_preserved_and_mirrored(safety_wired, fake_hass):
    cc, hz, S, mgr = safety_wired
    fake_hass.states.set("weather.home", "snowy",
                         temperature=15, temperature_unit="°F")
    safety = cc.SafetyManager(fake_hass, {"honorific": "sir"})
    action = await safety._check_freeze()
    # Behaviour preserved: the critical freeze action is unchanged.
    assert action is not None
    assert action["type"] == "freeze_critical" and action["urgency"] == "critical"
    assert action["auto_act"] is True
    # And the hazard situation was mirrored to CONFIRMED (log-only, additive).
    opens = mgr.open_situations("hazard")
    assert len(opens) == 1 and opens[0].state == S.CONFIRMED
    assert hz._last_parity["agree"] is True


async def test_check_freeze_warning_preserved_and_mirrored(safety_wired, fake_hass):
    cc, hz, S, mgr = safety_wired
    fake_hass.states.set("weather.home", "cloudy",
                         temperature=33, temperature_unit="°F")
    safety = cc.SafetyManager(fake_hass, {"honorific": "sir"})
    action = await safety._check_freeze()
    assert action is not None
    assert action["type"] == "freeze_warning" and action["urgency"] == "high"
    opens = mgr.open_situations("hazard")
    assert len(opens) == 1 and opens[0].state == S.INVESTIGATING


async def test_check_freeze_mild_no_action_no_situation(safety_wired, fake_hass):
    cc, hz, S, mgr = safety_wired
    fake_hass.states.set("weather.home", "clear",
                         temperature=60, temperature_unit="°F")
    safety = cc.SafetyManager(fake_hass, {"honorific": "sir"})
    action = await safety._check_freeze()
    assert action is None
    assert mgr.open_situations("hazard") == []
