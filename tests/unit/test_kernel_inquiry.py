"""Phase AD (pure) — research & discovery with evidence provenance + spend cap.

`kernel/inquiry.py`: findings are TYPED claims with provenance; the budget is a
hard cap (overrunning findings are refused); synthesis prefers the best-grounded
claim and caps confidence when a contradiction is present — never false certainty.
"""
import pytest


@pytest.fixture
def Q(load):
    return load("kernel.inquiry")


def _f(Q, claim, ctype="observation", conf=0.8, source="s", ev=()):
    return Q.Finding(claim=claim, claim_type=ctype, confidence=conf, source=source, evidence=ev)


def test_finding_coerces_unknown_type_to_weakest(Q):
    f = Q.Finding(claim="x", claim_type="nonsense", confidence=5)
    assert f.claim_type == Q.HYPOTHESIS and f.confidence == 1.0


def test_record_enforces_hard_spend_cap(Q):
    inq = Q.start("why is the hallway light on?", budget=2.0)
    inq = Q.record(inq, _f(Q, "motion seen at 2am"), cost=1.0)
    inq = Q.record(inq, _f(Q, "schedule rule fired"), cost=1.0)
    assert inq.spent == 2.0 and len(inq.findings) == 2 and inq.exhausted
    # A third finding would overrun the budget → refused, inquiry unchanged.
    inq2 = Q.record(inq, _f(Q, "ignored — over budget"), cost=1.0)
    assert inq2 is inq and len(inq2.findings) == 2


def test_zero_cost_allowed_positive_cost_refused_at_zero_budget(Q):
    inq = Q.start("q", budget=0.0)
    inq2 = Q.record(inq, _f(Q, "anything"), cost=0.0)   # zero cost → always allowed
    assert len(inq2.findings) == 1
    inq3 = Q.record(inq2, _f(Q, "more"), cost=0.1)       # positive cost → overruns 0 budget
    assert inq3 is inq2


def test_synthesize_prefers_best_grounded(Q):
    inq = Q.start("q", budget=10)
    inq = Q.record(inq, _f(Q, "the door is open", ctype="hypothesis", conf=0.95), cost=1)
    inq = Q.record(inq, _f(Q, "sensor reads open", ctype="observation", conf=0.7), cost=1)
    c = Q.synthesize(inq)
    # observation out-grounds hypothesis even at lower confidence.
    assert c.answer == "sensor reads open" and c.claim_type == "observation"
    assert c.contradicted is False


def test_synthesize_caps_confidence_on_contradiction(Q):
    inq = Q.start("is anyone home?", budget=10)
    inq = Q.record(inq, _f(Q, "someone is home", ctype="fact", conf=0.9), cost=1)
    inq = Q.record(inq, _f(Q, "no someone is home", ctype="fact", conf=0.9), cost=1)
    c = Q.synthesize(inq)
    assert c.contradicted is True
    assert c.confidence <= 0.5          # unresolved contradiction caps certainty


def test_synthesize_none_when_empty(Q):
    assert Q.synthesize(Q.start("q", budget=5)) is None


def test_contradictions_detected(Q):
    fs = [_f(Q, "the garage is closed"), _f(Q, "no the garage is closed")]
    pairs = Q.contradictions(fs)
    assert len(pairs) == 1


def test_within_budget_and_remaining(Q):
    inq = Q.start("q", budget=3.0)
    inq = Q.record(inq, _f(Q, "a"), cost=2.0)
    assert inq.within_budget and inq.remaining == 1.0 and not inq.exhausted
