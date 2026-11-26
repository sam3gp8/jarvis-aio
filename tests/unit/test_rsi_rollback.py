"""RSI engine — Phase 3a: probation watchdog & auto-rollback (kernel/rsi.py).

The safety net that ends an applied Tier-1 mutation's probation: auto-rollback
(restore the prior value + blacklist the mutation) on a boot failure, fault-rate
spike, or override spike — rollback always winning over graduation — and
graduation to CERTIFIED once the window closes clean. Pure decision logic; the
restore goes through the same injected applier, and a failed restore is loud.
"""
import pytest


@pytest.fixture
def rsi(load):
    return load("kernel/rsi")


NOW = 1_800_000_000.0


def _probation(rsi, *, applied_ts=NOW, window_s=3600.0, checkpoint="0.6"):
    return rsi.ProbationRecord(
        proposal_id="p1", mutation_hash="h1", applied_ts=applied_ts,
        window_s=window_s, checkpoint_ref=checkpoint, status=rsi.PROBATION)


def _proposal(rsi):
    p = rsi.MutationProposal("p1", rsi.TIER_PARAMETRIC, "observer",
                             "cognition_threshold", 0.6, 0.5)
    p.status = rsi.PROMOTED
    p.mutation_hash = "h1"
    return p


# ── evaluate_probation (pure) ────────────────────────────────────────────────

def test_boot_failure_rolls_back(rsi):
    v = rsi.evaluate_probation(_probation(rsi), now=NOW + 10, boot_ok=False)
    assert v.action == rsi.PROBATION_ROLLBACK
    assert "boot" in v.reason


def test_fault_spike_rolls_back(rsi):
    v = rsi.evaluate_probation(_probation(rsi), now=NOW + 10,
                               fault_rate=1.0, baseline_fault_rate=0.2)
    assert v.action == rsi.PROBATION_ROLLBACK
    assert "fault" in v.reason


def test_override_spike_rolls_back(rsi):
    v = rsi.evaluate_probation(_probation(rsi), now=NOW + 10, override_rate=0.5)
    assert v.action == rsi.PROBATION_ROLLBACK
    assert "override" in v.reason


def test_clean_window_graduates(rsi):
    v = rsi.evaluate_probation(_probation(rsi, window_s=3600), now=NOW + 3601)
    assert v.action == rsi.PROBATION_GRADUATE


def test_within_window_holds(rsi):
    v = rsi.evaluate_probation(_probation(rsi, window_s=3600), now=NOW + 1800)
    assert v.action == rsi.PROBATION_HOLD


def test_rollback_wins_over_graduation(rsi):
    # window elapsed AND a fault spike -> rollback, never graduate
    v = rsi.evaluate_probation(_probation(rsi, applied_ts=NOW - 7200, window_s=3600),
                               now=NOW, fault_rate=1.0, baseline_fault_rate=0.1)
    assert v.action == rsi.PROBATION_ROLLBACK


def test_low_fault_within_tolerance_holds(rsi):
    # a small fault rate under baseline×factor is not a spike
    v = rsi.evaluate_probation(_probation(rsi, window_s=3600), now=NOW + 100,
                               fault_rate=0.25, baseline_fault_rate=0.2)
    assert v.action == rsi.PROBATION_HOLD


# ── rollback_tier1 ───────────────────────────────────────────────────────────

def test_rollback_restores_and_blacklists(rsi):
    p = _proposal(rsi)
    pr = _probation(rsi, checkpoint="0.6")
    guard = rsi.OscillationGuard()
    restored = []
    ledger = []
    out = rsi.rollback_tier1(p, pr, applier=lambda k, v: restored.append((k, v)),
                             now=NOW, reason="fault spike", oscillation=guard,
                             ledger_sink=ledger.append)
    assert restored == [("cognition_threshold", 0.6)]   # prior value restored
    assert p.status == rsi.ROLLED_BACK
    assert out.status == rsi.ROLLED_BACK
    assert guard.is_blacklisted("h1")                    # can't be re-proposed
    assert ledger and ledger[0]["event"] == "rsi_rollback"
    assert ledger[0]["restored"] == 0.6


def test_rollback_restore_failure_is_loud(rsi):
    p = _proposal(rsi)
    pr = _probation(rsi)

    def _boom(k, v):
        raise RuntimeError("restore failed")

    with pytest.raises(RuntimeError):
        rsi.rollback_tier1(p, pr, applier=_boom, now=NOW)


# ── graduate ─────────────────────────────────────────────────────────────────

def test_graduate_certifies(rsi):
    p = _proposal(rsi)
    pr = _probation(rsi)
    nudged = []
    out = rsi.rsi_graduate(p, pr, now=NOW, autonomy_sink=nudged.append) \
        if hasattr(rsi, "rsi_graduate") else rsi.graduate(p, pr, now=NOW, autonomy_sink=nudged.append)
    assert p.status == rsi.CERTIFIED
    assert out.status == rsi.CERTIFIED
    assert nudged == [p]


# ── coordinator.watch_probation (dispatch) ───────────────────────────────────

def test_watch_dispatches_rollback(rsi):
    coord = rsi.RSICoordinator()
    p = _proposal(rsi)
    pr = _probation(rsi, checkpoint="0.6")
    restored = []
    out = coord.watch_probation(p, pr, applier=lambda k, v: restored.append((k, v)),
                                now=NOW + 10, boot_ok=False)
    assert out.status == rsi.ROLLED_BACK
    assert restored == [("cognition_threshold", 0.6)]
    assert coord.oscillation.is_blacklisted("h1")


def test_watch_dispatches_graduation(rsi):
    coord = rsi.RSICoordinator()
    p = _proposal(rsi)
    pr = _probation(rsi, applied_ts=NOW - 7200, window_s=3600)
    out = coord.watch_probation(p, pr, applier=lambda k, v: None, now=NOW)
    assert out.status == rsi.CERTIFIED
    assert p.status == rsi.CERTIFIED


def test_watch_holds_within_window(rsi):
    coord = rsi.RSICoordinator()
    p = _proposal(rsi)
    pr = _probation(rsi, applied_ts=NOW, window_s=3600)
    applied = []
    out = coord.watch_probation(p, pr, applier=lambda k, v: applied.append((k, v)), now=NOW + 60)
    assert out.status == rsi.PROBATION      # unchanged
    assert applied == []                    # nothing touched during a hold


def test_rollback_blacklist_persists(rsi, tmp_path):
    # a rolled-back mutation stays locked across a store reload (anti-oscillation)
    db = str(tmp_path / "rsi.db")
    store = rsi.RSIStore(db)
    coord = rsi.RSICoordinator(store=store)
    p = _proposal(rsi)
    pr = _probation(rsi)
    coord.watch_probation(p, pr, applier=lambda k, v: None, now=NOW + 10, boot_ok=False)
    # fresh coordinator loads the blacklist from disk
    coord2 = rsi.RSICoordinator(store=rsi.RSIStore(db))
    assert coord2.oscillation.is_blacklisted("h1")
