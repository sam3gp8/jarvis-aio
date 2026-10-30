"""MCU Phase R (R2c / R3c): a live consumer ACTS on the kernel Situation lifecycle.

The intrusion/hazard/delivery situations were long mirrored into a durable kernel
``Situation`` but (for two of them) only *recorded* — nothing read the lifecycle
back. These tests pin the flips that make the kernel situation DRIVE behaviour:

  * ``SituationManager.recent`` surfaces a just-resolved episode (the seam the
    intrusion call-off read needs — ``open_situations`` would already drop it);
  * ``intrusion.kernel_calloff_active_sync`` reports a recent false-alarm (BENIGN)
    dismissal so a reload still honours the call-off, and fails safe to ``None``;
  * ``hazard_situation.kernel_freeze_active_sync`` reports an open freeze episode;
  * ``SafetyManager._check_freeze`` suppresses a DUPLICATE warning under an open
    episode but NEVER a critical (fail toward alerting);
  * ``SafetyManager._check_intrusion`` honours a durable call-off, and the
    kill-switch / an unreadable store revert it (additive, never over-suppresses).

The integration tests (through ``_check_freeze`` / ``_check_intrusion``) exercise
the REAL per-test-isolated kernel situation store rather than a monkeypatched
manager instance: the methods look the seam up lazily (``from . import …``) and
read/write through the config-path db *file*, so sharing the file — which the
autouse ``_reset_situation_store`` fixture wipes between tests — is what makes the
write and the read meet, independent of any module instance.
"""
import pytest

from fakes import FakeHass


# ── SituationManager.recent: includes just-resolved episodes ─────────────────────

def test_recent_includes_resolved_episode(load, tmp_path):
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "s.db"))
    sit = mgr.open("intrusion", subject="garage")
    mgr.transition(sit.id, S.BENIGN, reason="false alarm")
    mgr.transition(sit.id, S.RESOLVED, reason="false alarm")
    # open_situations drops it (terminal); recent() still surfaces it.
    assert mgr.open_situations("intrusion") == []
    recent = mgr.recent("intrusion", since_ts=0.0)
    assert len(recent) == 1 and recent[0].id == sit.id
    # history carries the BENIGN step the call-off read keys on.
    assert any(step["to"] == S.BENIGN for step in recent[0].history)


def test_recent_respects_since_ts(load, tmp_path):
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "s.db"))
    sit = mgr.open("intrusion")
    # A cutoff in the future excludes the just-written episode.
    assert mgr.recent("intrusion", since_ts=sit.updated_ts + 1000) == []
    assert len(mgr.recent("intrusion", since_ts=sit.updated_ts - 1000)) == 1


# ── intrusion: durable false-alarm (call-off) read ───────────────────────────────

@pytest.fixture
def intr(load, tmp_path, monkeypatch):
    mod = load("intrusion")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(mod, "_situation_mgr", mgr)
    monkeypatch.setattr(mod, "_situation_id", None)
    return mod, S, mgr


def _dismiss(intr_mod, hass):
    """Drive an intrusion episode to a BENIGN false-alarm dismissal via the mirror."""
    intr_mod._mirror_situation_sync(hass, "investigating", reason="motion", breach_area="garage")
    intr_mod._mirror_situation_sync(hass, "dismissed", reason="false alarm")


def test_calloff_active_true_after_recent_dismissal(intr):
    mod, S, mgr = intr
    hass = FakeHass()
    _dismiss(mod, hass)
    assert mod.kernel_calloff_active_sync(hass) is True


def test_calloff_inactive_after_window(intr):
    mod, S, mgr = intr
    hass = FakeHass()
    _dismiss(mod, hass)
    # A "now" well past the call-off cooldown → the dismissal no longer suppresses.
    import time
    later = time.time() + mod._CALLOFF_COOLDOWN + 10
    assert mod.kernel_calloff_active_sync(hass, now=later) is False


def test_calloff_inactive_with_no_episode(intr):
    mod, S, mgr = intr
    assert mod.kernel_calloff_active_sync(FakeHass()) is False


def test_calloff_fails_safe_to_none_on_unreadable_store(intr, monkeypatch):
    mod, S, mgr = intr

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(mgr, "recent", _boom)
    # A store error → None (caller treats it as "no durable opinion", never a
    # reason to over-suppress a real intrusion).
    assert mod.kernel_calloff_active_sync(FakeHass()) is None


# ── hazard: durable open-freeze read ─────────────────────────────────────────────

@pytest.fixture
def hz(load, tmp_path, monkeypatch):
    mod = load("hazard_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(mod, "_mgr", mgr)
    monkeypatch.setattr(mod, "_freeze_situation_id", None)
    return mod, S, mgr


def test_freeze_active_true_when_open(hz):
    mod, S, mgr = hz
    mod.mirror_freeze_sync(FakeHass(), "warning", "34F")
    assert mod.kernel_freeze_active_sync(FakeHass()) is True


def test_freeze_active_false_when_cleared(hz):
    mod, S, mgr = hz
    hass = FakeHass()
    mod.mirror_freeze_sync(hass, "warning", "34F")
    mod.mirror_freeze_sync(hass, "cleared", "45F")
    assert mod.kernel_freeze_active_sync(hass) is False


def test_freeze_active_false_with_no_episode(hz):
    mod, S, mgr = hz
    assert mod.kernel_freeze_active_sync(FakeHass()) is False


def test_freeze_active_none_on_unreadable_store(hz, monkeypatch):
    mod, S, mgr = hz

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(mgr, "open_situations", _boom)
    assert mod.kernel_freeze_active_sync(FakeHass()) is None


# ── SafetyManager._check_freeze: durable WARNING de-dup, CRITICAL never ───────────

@pytest.fixture
def freeze_safety(cognitive_core, fake_hass, load):
    """A SafetyManager whose freeze episodes live in the real (per-test isolated)
    config-path situation store. Both the mirror write and _check_freeze's lazy
    `from . import hazard_situation` read resolve to the same db FILE, so the test
    is robust to the module being re-imported mid-call."""
    hazard = load("hazard_situation")
    safety = cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})
    return cognitive_core, safety, fake_hass, hazard


async def test_warning_suppressed_when_freeze_episode_already_open(freeze_safety):
    cc, safety, hass, hazard = freeze_safety
    # A freeze episode is already open (announced before a restart; _freeze_warned
    # was lost). The durable read adopts "already warned" → no duplicate WARNING.
    hazard.mirror_freeze_sync(hass, "warning", "34F")
    hass.states.set("weather.home", "cloudy", temperature=30)   # legacy=warning
    assert safety._freeze_warned is False
    action = await safety._check_freeze()
    assert action is None                 # duplicate warning suppressed
    assert safety._freeze_warned is True  # adopted from the durable episode


async def test_critical_never_suppressed_even_with_open_episode(freeze_safety):
    cc, safety, hass, hazard = freeze_safety
    hazard.mirror_freeze_sync(hass, "critical", "15F")   # open episode present
    hass.states.set("weather.home", "snowy", temperature=15)  # legacy=critical
    action = await safety._check_freeze()
    # CRITICAL fails toward alerting — the durable de-dup only touches WARNING.
    assert action is not None and action["type"] == "freeze_critical"


async def test_warning_fires_when_no_open_episode(freeze_safety):
    cc, safety, hass, hazard = freeze_safety
    hass.states.set("weather.home", "cloudy", temperature=30)
    action = await safety._check_freeze()
    assert action is not None and action["type"] == "freeze_warning"


# ── SafetyManager._check_intrusion: durable call-off ─────────────────────────────

@pytest.fixture
def intr_safety(cognitive_core, fake_hass, load, monkeypatch):
    """A SafetyManager wired to the away + corroborated-entry scenario that WOULD
    open an investigation. Like freeze_safety, the intrusion episodes live in the
    real per-test-isolated config-path store, so the write (_dismiss) and the read
    (_check_intrusion's lazy import) meet on the same db file."""
    intrusion = load("intrusion")
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_require_corroboration": True})
    monkeypatch.setattr(safety, "_residents_away", lambda: True)
    monkeypatch.setattr(safety, "_resident_tracked_home", lambda: False)
    monkeypatch.setattr(safety, "_resident_on_camera", lambda: False)
    monkeypatch.setattr(safety, "_qualifying_motion",
                        lambda sleeping: [("binary_sensor.kitchen_motion", "the kitchen")])
    monkeypatch.setattr(safety, "_alarm_armed", lambda: True)
    monkeypatch.setattr(safety, "_open_entry", lambda: "binary_sensor.kitchen_window")
    safety._last_intrusion_alert = 0.0
    safety._investigation = None
    return cognitive_core, safety, fake_hass, intrusion


async def test_intrusion_suppressed_by_durable_calloff(intr_safety):
    cc, safety, hass, intrusion = intr_safety
    _dismiss(intrusion, hass)                      # a recent BENIGN episode
    assert intrusion.is_called_off() is False      # the in-memory window is NOT what suppresses
    action = await safety._check_intrusion(anyone_home=False, sleeping=False)
    assert action is None                          # durable call-off suppressed it
    assert safety._investigation is None


async def test_intrusion_opens_without_durable_calloff(intr_safety):
    cc, safety, hass, intrusion = intr_safety
    # No prior dismissal → nothing durable to honour → the investigation opens.
    action = await safety._check_intrusion(anyone_home=False, sleeping=False)
    assert action is not None
    assert safety._investigation is not None


async def test_intrusion_kill_switch_off_ignores_durable_calloff(intr_safety, monkeypatch):
    cc, safety, hass, intrusion = intr_safety
    monkeypatch.setattr(cc, "INTRUSION_SITUATION_ENFORCE", False)
    _dismiss(intrusion, hass)                      # a recent BENIGN episode exists
    action = await safety._check_intrusion(anyone_home=False, sleeping=False)
    # Kill-switch off → the durable call-off is not consulted → it opens.
    assert action is not None
    assert safety._investigation is not None


def test_default_kill_switch_is_enforce(cognitive_core):
    assert cognitive_core.INTRUSION_SITUATION_ENFORCE is True
