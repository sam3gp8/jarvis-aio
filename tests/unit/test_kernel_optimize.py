"""Phase Y — self-optimization within owner bounds (kernel/optimize.py).

Pure classification + bounds + guard logic. The point of these tests is the
tiered guardrail: Y may auto-apply SAFE params within bounds, propose SENSITIVE
ones for the owner, and may NEVER touch FORBIDDEN (or unrecognised) ones.
"""
import pytest


@pytest.fixture
def O(load):
    return load("kernel.optimize")


# ── classify / tier guardrail ─────────────────────────────────────────────────

def test_classify_known_tiers(O):
    assert O.classify("latency") == O.SAFE
    assert O.classify("cost") == O.SAFE
    assert O.classify("autonomy_level") == O.SENSITIVE
    assert O.classify("confidence_threshold") == O.SENSITIVE
    assert O.classify("authority_ceiling") == O.FORBIDDEN
    assert O.classify("safety_threshold") == O.FORBIDDEN
    assert O.classify("human_override") == O.FORBIDDEN


def test_unknown_param_is_forbidden_failsafe(O):
    assert O.classify("some_new_knob") == O.FORBIDDEN
    assert O.classify("") == O.FORBIDDEN


def test_may_autotune_only_safe(O):
    assert O.may_autotune("latency") is True
    assert O.may_autotune("autonomy_level") is False
    assert O.may_autotune("safety_threshold") is False
    assert O.may_autotune("unknown") is False


# ── Bound ─────────────────────────────────────────────────────────────────────

def test_bound_contains_and_clamp(O):
    b = O.Bound("latency", 100.0, 500.0)
    assert b.contains(100.0) and b.contains(500.0) and b.contains(300.0)
    assert not b.contains(99.0) and not b.contains(501.0)
    assert b.clamp(50.0) == 100.0
    assert b.clamp(600.0) == 500.0
    assert b.clamp(300.0) == 300.0


# ── Metric direction ─────────────────────────────────────────────────────────

def test_metric_improves_on_direction(O):
    fast = O.Metric("latency", 100.0, lower_is_better=True)
    slow = O.Metric("latency", 300.0, lower_is_better=True)
    assert fast.improves_on(slow) and not slow.improves_on(fast)
    hi = O.Metric("cache_hit", 0.9, lower_is_better=False)
    lo = O.Metric("cache_hit", 0.5, lower_is_better=False)
    assert hi.improves_on(lo) and not lo.improves_on(hi)


# ── propose: the guardrail in action ─────────────────────────────────────────

def test_safe_within_bounds_is_auto_applicable(O):
    p = O.propose("latency", current=400.0, proposed=300.0,
                  bound=O.Bound("latency", 100.0, 500.0))
    assert p.tier == O.SAFE and p.within_bounds
    assert p.auto_applicable and not p.proposal_only and not p.rejected


def test_safe_out_of_bounds_is_rejected_not_auto(O):
    p = O.propose("latency", current=400.0, proposed=50.0,
                  bound=O.Bound("latency", 100.0, 500.0))
    assert p.tier == O.SAFE and not p.within_bounds
    assert not p.auto_applicable and p.rejected


def test_sensitive_is_proposal_only_never_auto(O):
    p = O.propose("confidence_threshold", current=0.6, proposed=0.7,
                  bound=O.Bound("confidence_threshold", 0.5, 0.9))
    assert p.tier == O.SENSITIVE and p.within_bounds
    assert p.proposal_only and not p.auto_applicable and not p.rejected


def test_forbidden_is_always_rejected_even_within_bounds(O):
    p = O.propose("safety_threshold", current=1.0, proposed=1.0,
                  bound=O.Bound("safety_threshold", 0.0, 10.0))
    assert p.tier == O.FORBIDDEN
    assert p.rejected and not p.auto_applicable and not p.proposal_only


def test_unknown_param_proposal_is_rejected(O):
    p = O.propose("mystery_knob", current=1.0, proposed=2.0)
    assert p.tier == O.FORBIDDEN and p.rejected


def test_propose_without_bound_safe_is_within_but_forbidden_is_not(O):
    safe = O.propose("cost", current=10.0, proposed=8.0)
    assert safe.within_bounds and safe.auto_applicable
    forb = O.propose("authority_ceiling", current=1.0, proposed=2.0)
    assert not forb.within_bounds and forb.rejected


# ── report roll-up ───────────────────────────────────────────────────────────

def test_report_buckets_proposals(O):
    proposals = [
        O.propose("latency", 400.0, 300.0, bound=O.Bound("latency", 100.0, 500.0)),
        O.propose("cost", 10.0, 9.0),                                    # safe auto
        O.propose("autonomy_level", 1.0, 2.0,
                  bound=O.Bound("autonomy_level", 0.0, 3.0)),            # owner
        O.propose("safety_threshold", 1.0, 1.0),                        # rejected
        O.propose("latency", 400.0, 50.0,
                  bound=O.Bound("latency", 100.0, 500.0)),              # rejected (oob)
    ]
    rep = O.report(proposals)
    assert len(rep.auto_applicable) == 2
    assert len(rep.for_owner) == 1
    assert len(rep.rejected) == 2
    d = rep.to_dict()
    assert d["auto_applicable"] == 2 and d["for_owner"] == 1 and d["rejected"] == 2
    assert isinstance(d["proposals"], list) and len(d["proposals"]) == 5


def test_proposal_to_dict_exposes_guard_flags(O):
    p = O.propose("latency", 400.0, 300.0, bound=O.Bound("latency", 100.0, 500.0),
                  metric_before=O.Metric("latency", 400.0), rationale="p95 high")
    d = p.to_dict()
    assert d["auto_applicable"] is True
    assert d["rejected"] is False and d["proposal_only"] is False
    assert d["rationale"] == "p95 high"
