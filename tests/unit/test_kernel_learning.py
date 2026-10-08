"""Pure learning & adaptation primitive (kernel.learning — roadmap Phase M).

Bounded, reversible weight adjustments distilled from structured Outcomes. PURE:
nothing live consumes it yet, so these tests pin the math, the bounds, the
governance never-relax guard, and reversibility directly.
"""
import pytest


@pytest.fixture
def L(load):
    return load("kernel.learning")


@pytest.fixture
def O(load):
    return load("kernel.outcome")


def _outcomes(O, signals):
    """Outcomes carrying the given learning signals (success irrelevant here)."""
    return [O.record_outcome(capability="c", intended_result="x",
                             observed_result="x", success=s >= 0,
                             confidence=1.0, learning_signal=s)
            for s in signals]


# ── no-op / totality ───────────────────────────────────────────────────────────
def test_no_outcomes_is_noop(L):
    a = L.adjust("w", 0.5, [])
    assert a.prior == 0.5 and a.proposed == 0.5 and a.delta == 0.0
    assert a.samples == 0 and not a.changed
    assert a.applied() == 0.5 and a.reverted() == 0.5


def test_prior_is_clamped_into_range(L, O):
    a = L.adjust("w", 5.0, _outcomes(O, [0.0]), floor=0.0, ceil=1.0)
    assert a.prior == 1.0            # 5.0 clamped to ceil before adjusting


# ── bounded nudge ──────────────────────────────────────────────────────────────
def test_positive_signal_nudges_up_capped(L, O):
    # mean signal 1.0, step_cap 0.1 → +0.1
    a = L.adjust("w", 0.5, _outcomes(O, [1.0, 1.0]), step_cap=0.1)
    assert a.signal == pytest.approx(1.0)
    assert a.proposed == pytest.approx(0.6) and a.delta == pytest.approx(0.1)
    assert a.samples == 2 and a.changed


def test_negative_signal_nudges_down(L, O):
    a = L.adjust("w", 0.5, _outcomes(O, [-1.0]), step_cap=0.1)
    assert a.proposed == pytest.approx(0.4) and a.delta == pytest.approx(-0.1)


def test_partial_signal_scales_step(L, O):
    # mean signal 0.5 → half the cap
    a = L.adjust("w", 0.5, _outcomes(O, [1.0, 0.0]), step_cap=0.2)
    assert a.signal == pytest.approx(0.5)
    assert a.delta == pytest.approx(0.1)


def test_floor_and_ceil_clamp(L, O):
    hi = L.adjust("w", 0.95, _outcomes(O, [1.0]), step_cap=0.1, ceil=1.0)
    assert hi.proposed == 1.0 and hi.clamped
    lo = L.adjust("w", 0.05, _outcomes(O, [-1.0]), step_cap=0.1, floor=0.0)
    assert lo.proposed == 0.0 and lo.clamped


# ── governance: protected weights never relax ───────────────────────────────────
def test_protected_weight_never_lowered(L, O):
    a = L.adjust("safety", 0.7, _outcomes(O, [-1.0]), step_cap=0.1, protected=True)
    assert a.proposed == 0.7 and a.delta == 0.0 and a.clamped
    # but a protected weight may still TIGHTEN (move up)
    up = L.adjust("safety", 0.7, _outcomes(O, [1.0]), step_cap=0.1, protected=True)
    assert up.proposed == pytest.approx(0.8) and up.delta == pytest.approx(0.1)


# ── reversibility ──────────────────────────────────────────────────────────────
def test_reversible(L, O):
    a = L.adjust("w", 0.5, _outcomes(O, [1.0]), step_cap=0.1)
    assert a.reverted() == 0.5 and a.applied() == pytest.approx(0.6)


# ── batch planning ─────────────────────────────────────────────────────────────
def test_plan_adjustments_ranks_and_protects(L, O):
    priors = {"a": 0.5, "b": 0.5, "safety": 0.5, "idle": 0.5}
    by_key = {
        "a": _outcomes(O, [1.0]),           # +0.1
        "b": _outcomes(O, [0.5]),           # +0.05
        "safety": _outcomes(O, [-1.0]),     # would be -0.1, but protected → no-op
        "idle": [],                         # no outcomes → excluded
    }
    plan = L.plan_adjustments(priors, by_key, step_cap=0.1,
                              protected_keys={"safety"})
    keys = [a.key for a in plan]
    assert keys == ["a", "b"]               # safety no-op + idle excluded; ranked
    assert all(a.key != "safety" for a in plan)


def test_frozen(L, O):
    a = L.adjust("w", 0.5, _outcomes(O, [1.0]))
    with pytest.raises(Exception):
        a.proposed = 0.9
    assert hash(a) == hash(a)
