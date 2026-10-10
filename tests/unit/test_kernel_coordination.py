"""Phase O (pure) — budgeted peer coordination (bid / claim / settle).

`kernel/coordination.py` awards an objective to exactly one agency (no
double-actuation), rejects bids over budget, and resolves conflicts by the
kernel.priority ladder — never by bid order.
"""
import pytest


@pytest.fixture
def C(load):
    load("kernel.priority")
    return load("kernel.coordination")


def _bid(C, aid, obj="task", cost=1.0, tier="household", conf=0.5):
    return C.Bid(agency_id=aid, objective=obj, cost=cost, tier=tier, confidence=conf)


def test_arbitrate_awards_exactly_one(C):
    claim = C.arbitrate([_bid(C, "a"), _bid(C, "b"), _bid(C, "c")], budget=5)
    assert claim.awarded and claim.winner in {"a", "b", "c"}
    assert claim.status == C.CLAIMED


def test_budget_rejects_overrunning_bids(C):
    claim = C.arbitrate([_bid(C, "pricey", cost=10)], budget=5)
    assert claim.awarded is False and claim.status == C.OPEN   # nothing eligible


def test_priority_ladder_resolves_conflict_not_order(C):
    # 'conv' is first and cheaper, but 'sec' bids at a higher priority tier → wins.
    bids = [_bid(C, "conv", tier="convenience", cost=1),
            _bid(C, "sec", tier="security", cost=3)]
    assert C.arbitrate(bids, budget=5).winner == "sec"


def test_tie_broken_by_cost_then_confidence(C):
    bids = [_bid(C, "hi", tier="household", cost=2, conf=0.9),
            _bid(C, "lo", tier="household", cost=1, conf=0.1)]
    assert C.arbitrate(bids, budget=5).winner == "lo"   # lower cost wins the tier tie


def test_settle_success_and_failure(C):
    claim = C.arbitrate([_bid(C, "a")], budget=5)
    assert C.settle(claim, success=True).status == C.SETTLED
    assert C.settle(claim, success=False).status == C.FAILED


def test_settle_unawarded_is_noop(C):
    open_claim = C.arbitrate([], budget=5)
    assert C.settle(open_claim, success=True).status == C.OPEN


def test_would_double_actuate_guard(C):
    c1 = C.arbitrate([_bid(C, "a", obj="x")], budget=5)
    c2 = C.arbitrate([_bid(C, "b", obj="x")], budget=5)   # same objective, 2nd award
    assert C.would_double_actuate([c1, c2]) is True
    c3 = C.arbitrate([_bid(C, "c", obj="y")], budget=5)
    assert C.would_double_actuate([c1, c3]) is False       # different objectives
