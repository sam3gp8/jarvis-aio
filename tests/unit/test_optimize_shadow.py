"""Phase Y (shadow) — optimize tuning proposals from the interruption budget.

`decision_record._optimize_proposal_from_budget` maps the live over-interruption
assessment into a kernel.optimize proposal for the SENSITIVE `interrupt_threshold`.
It is observe-only and, crucially, proposal_only (owner-gated) — never
auto-applied, demonstrating the tiered guardrail.
"""
import pytest


@pytest.fixture
def dr(load):
    load("kernel.optimize")
    return load("decision_record")


def test_shadow_flag_default(dr):
    assert dr.OPTIMIZE_SHADOW is True


def test_no_data_yields_no_proposal(dr):
    assert dr._optimize_proposal_from_budget({"judged": 0, "multiplier": 1.0}) is None
    assert dr._optimize_proposal_from_budget({}) is None


def test_over_interrupting_proposes_raising_threshold(dr):
    budget = {"judged": 20, "multiplier": 0.4, "assessment": "over-interrupting",
              "unwelcome_rate": 0.6}
    report = dr._optimize_proposal_from_budget(budget)
    assert report is not None and len(report.proposals) == 1
    p = report.proposals[0]
    assert p.param == "interrupt_threshold"
    assert p.proposed > p.current            # interrupt less
    # SENSITIVE → owner-gated proposal, NEVER auto-applied (the Y invariant).
    assert p.proposal_only is True
    assert p.auto_applicable is False


def test_healthy_budget_proposes_no_meaningful_change(dr):
    budget = {"judged": 20, "multiplier": 1.0, "assessment": "healthy"}
    report = dr._optimize_proposal_from_budget(budget)
    p = report.proposals[0]
    assert p.proposed == p.current           # multiplier 1.0 → no change proposed
    assert p.proposal_only is True           # still only a proposal, never auto


def test_proposal_stays_within_bounds(dr):
    # An extreme multiplier is clamped by the 0.9 cap and the [0,1] bound.
    budget = {"judged": 5, "multiplier": 0.0, "assessment": "over-interrupting"}
    p = dr._optimize_proposal_from_budget(budget).proposals[0]
    assert 0.0 <= p.proposed <= 1.0 and p.within_bounds is True


def test_emit_never_raises(dr):
    # Defensive: a junk budget must not raise through the observe-only emitter.
    dr._emit_optimize_shadow({"judged": 3, "multiplier": "oops"})
    dr._emit_optimize_shadow(None)
