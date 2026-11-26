"""RSI engine — Phase 2: Tier-1 parametric loop (kernel/rsi.py).

Shadow-replay fitness scoring (promote only on a real improvement; reject
regressions and safety-domain regressions), the atomic apply → probation path
(checkpoint + lineage + injected applier, nothing half-mutated), the coordinator
evaluate→act cycle, and the observe/recommend bridges. Deterministic (`now`
passed in); the replay substrate is the real pure `replay.evaluate_threshold`
over synthetic judged records.
"""
import calendar

import pytest


@pytest.fixture
def rsi(load):
    return load("kernel/rsi")


NOW = calendar.timegm((2026, 1, 1, 3, 0, 0, 0, 0, 0))


def _recs(n_hi_right=20, n_lo_wrong=20, hi=0.9, lo=0.3):
    """High-confidence right + low-confidence wrong — a clean separation a higher
    threshold classifies better."""
    return ([{"confidence": hi, "outcome": "right"}] * n_hi_right
            + [{"confidence": lo, "outcome": "wrong"}] * n_lo_wrong)


def _prop(rsi, *, subsystem="observer", key="cognition_threshold",
          current=0.1, proposed=0.5, status=None):
    p = rsi.MutationProposal(
        proposal_id="p1", tier=rsi.TIER_PARAMETRIC, target_subsystem=subsystem,
        target_key_or_path=key, current_value=current, proposed_value=proposed,
        created_ts=NOW,
    )
    p.status = rsi.PROPOSED if status is None else status
    return p


# ── evaluate_parametric: fitness scoring ─────────────────────────────────────

def test_promotes_on_clear_improvement(rsi):
    # baseline 0.1 (acts on all) acc 0.5; candidate 0.5 separates perfectly acc 1.0
    ev = rsi.evaluate_parametric(_prop(rsi, current=0.1, proposed=0.5), _recs(), now=NOW)
    assert ev.passed is True
    assert ev.delta_outcome > 0.4
    assert ev.delta_fitness >= 0.01
    assert ev.safety_violations == 0


def test_rejects_regression(rsi):
    # candidate LOWERS the threshold → worse accuracy → negative ΔF → rejected
    ev = rsi.evaluate_parametric(_prop(rsi, current=0.5, proposed=0.1), _recs(), now=NOW)
    assert ev.passed is False
    assert ev.delta_outcome < 0
    assert "below floor" in ev.detail


def test_rejects_insufficient_samples(rsi):
    ev = rsi.evaluate_parametric(_prop(rsi, current=0.1, proposed=0.5),
                                 _recs(5, 5), now=NOW)
    assert ev.passed is False
    assert "insufficient" in ev.detail


def test_rejects_non_numeric_value(rsi):
    p = _prop(rsi, current="auto", proposed="manual")
    ev = rsi.evaluate_parametric(p, _recs(), now=NOW)
    assert ev.passed is False
    assert "non-numeric" in ev.detail


def test_below_noise_floor_rejected(rsi):
    # identical thresholds → ΔF == 0 < floor → not a promotion
    ev = rsi.evaluate_parametric(_prop(rsi, current=0.5, proposed=0.5), _recs(), now=NOW)
    assert ev.passed is False


def _safety_recs():
    # Lowering 0.8→0.5 raises accuracy (catches the 0.6-right calls) but also acts
    # on 5 wrong calls — a kept_wrong regression that only matters in a safety sub.
    return ([{"confidence": 0.9, "outcome": "right"}] * 30
            + [{"confidence": 0.6, "outcome": "right"}] * 10
            + [{"confidence": 0.6, "outcome": "wrong"}] * 5)


def test_positive_df_still_blocked_on_safety_regression(rsi):
    p = _prop(rsi, subsystem="intrusion", current=0.8, proposed=0.5)
    ev = rsi.evaluate_parametric(p, _safety_recs(), now=NOW)
    assert ev.delta_fitness >= 0.01          # fitness alone would promote…
    assert ev.passed is False                # …but the safety gate blocks it
    assert ev.safety_violations == 1
    assert "safety regression" in ev.detail


def test_same_candidate_passes_in_non_safety_subsystem(rsi):
    # identical change in a non-safety subsystem promotes (no safety gate)
    p = _prop(rsi, subsystem="observer", current=0.8, proposed=0.5)
    ev = rsi.evaluate_parametric(p, _safety_recs(), now=NOW)
    assert ev.passed is True
    assert ev.safety_violations == 0


# ── apply_tier1: atomic application + probation ──────────────────────────────

def test_apply_checkpoints_and_applies(rsi):
    p = _prop(rsi, current=0.1, proposed=0.5)
    ev = rsi.evaluate_parametric(p, _recs(), now=NOW)
    assert ev.passed
    applied = []
    ledgered = []
    pr = rsi.apply_tier1(p, ev, applier=lambda k, v: applied.append((k, v)),
                         now=NOW, ledger_sink=ledgered.append)
    assert applied == [("cognition_threshold", 0.5)]
    assert p.status == rsi.PROMOTED
    assert pr.status == rsi.PROBATION
    assert pr.applied_ts == NOW
    assert "0.1" in pr.checkpoint_ref        # the rollback value is preserved
    assert ledgered and ledgered[0]["event"] == "rsi_tier1_apply"


def test_apply_failure_does_not_promote(rsi):
    p = _prop(rsi, current=0.1, proposed=0.5)
    ev = rsi.evaluate_parametric(p, _recs(), now=NOW)

    def _boom(k, v):
        raise RuntimeError("config write failed")

    with pytest.raises(RuntimeError):
        rsi.apply_tier1(p, ev, applier=_boom, now=NOW)
    assert p.status != rsi.PROMOTED          # never half-mutated


# ── coordinator: evaluate → act cycle ────────────────────────────────────────

def test_coordinator_promotes_and_consumes_budget(rsi):
    coord = rsi.RSICoordinator()
    p = _prop(rsi, current=0.1, proposed=0.5)
    applied = []
    ev, pr = coord.promote_parametric(p, _recs(), applier=lambda k, v: applied.append((k, v)), now=NOW)
    assert ev.passed and pr is not None and pr.status == rsi.PROBATION
    assert applied == [("cognition_threshold", 0.5)]
    assert coord.budget.snapshot()["mutations"] == 1   # budget consumed on apply


def test_coordinator_rejects_regression_without_applying(rsi):
    coord = rsi.RSICoordinator()
    p = _prop(rsi, current=0.5, proposed=0.1)
    applied = []
    ev, pr = coord.promote_parametric(p, _recs(), applier=lambda k, v: applied.append((k, v)), now=NOW)
    assert ev.passed is False and pr is None
    assert p.status == rsi.REJECTED
    assert applied == []                      # nothing applied on a failed evaluation
    assert coord.budget.snapshot()["mutations"] == 0


def test_coordinator_refuses_non_proposed(rsi):
    coord = rsi.RSICoordinator()
    p = _prop(rsi, status=rsi.LOCKED)         # never cleared the guardrails
    applied = []
    ev, pr = coord.promote_parametric(p, _recs(), applier=lambda k, v: applied.append((k, v)), now=NOW)
    assert ev.passed is False and pr is None
    assert applied == []


def test_coordinator_rejects_non_tier1(rsi):
    coord = rsi.RSICoordinator()
    p = _prop(rsi, status=rsi.PROPOSED)
    p.tier = rsi.TIER_STRUCTURAL
    with pytest.raises(ValueError):
        coord.promote_parametric(p, _recs(), applier=lambda k, v: None, now=NOW)


# ── observe + recommend bridges ──────────────────────────────────────────────

def test_observe_flags_drifted_subsystem(rsi):
    signals = ([{"subsystem": "observer", "outcome": "wrong"}] * 6
               + [{"subsystem": "observer", "outcome": "right"}] * 4
               + [{"subsystem": "camera", "outcome": "right"}] * 10)
    anomalies = rsi.rsi_observe(signals, now=NOW) if hasattr(rsi, "rsi_observe") else rsi.observe(signals, now=NOW)
    subs = [a.subsystem for a in anomalies]
    assert "observer" in subs            # 60% wrong rate > 0.15 drift
    assert "camera" not in subs          # 0% wrong rate


def test_observe_respects_cooldown(rsi):
    signals = [{"subsystem": "observer", "outcome": "wrong"}] * 10
    assert rsi.observe(signals, now=NOW, last_cycle_ts=NOW - 60, cooldown_s=3600) == []
    assert rsi.observe(signals, now=NOW, last_cycle_ts=NOW - 7200, cooldown_s=3600)  # elapsed


def test_observe_counts_overrides_and_verified_false(rsi):
    signals = ([{"kind": "sentinel", "overridden": True}] * 5
               + [{"kind": "sentinel", "verified": False}] * 3
               + [{"kind": "sentinel", "outcome": "right"}] * 2)
    anomalies = rsi.observe(signals, now=NOW)
    assert anomalies and anomalies[0].subsystem == "sentinel"
    assert anomalies[0].overrides == 5 and anomalies[0].wrong == 3


def test_recommend_parametric(rsi):
    rec = rsi.recommend_parametric(_recs(), min_samples=10)
    assert rec is not None and "recommended" in rec
    assert rsi.recommend_parametric(_recs(2, 2), min_samples=25) is None
