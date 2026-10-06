"""Tests for the canonical outcome model (Epistemic Fabric — pure).

No Home Assistant, no DB: plain outcomes and a fixed clock. The key properties
are that the distilled learning_signal is bounded and sign-correct, and that a
failed verification auto-records the deviation.
"""
import pytest


@pytest.fixture
def oc(load):
    return load("kernel.outcome")


def test_learning_signal_is_bounded_and_sign_correct(oc):
    assert oc.learning_signal_for(True, 0.9) == pytest.approx(0.9)
    assert oc.learning_signal_for(False, 0.9) == pytest.approx(-0.9)
    assert oc.learning_signal_for(True, 2.0) == pytest.approx(1.0)   # clamped
    assert oc.learning_signal_for(False, 5.0) == pytest.approx(-1.0)  # clamped
    assert oc.learning_signal_for(True, -1.0) == pytest.approx(0.0)   # confidence floor


def test_record_outcome_distills_signal_when_omitted(oc):
    o = oc.record_outcome(success=True, confidence=0.8, capability="lights.on",
                          now=lambda: 10.0)
    assert o.ts == 10.0
    assert o.learning_signal == pytest.approx(0.8)
    bad = oc.record_outcome(success=False, confidence=0.7)
    assert bad.learning_signal == pytest.approx(-0.7)


def test_record_outcome_explicit_signal_wins_and_clamps(oc):
    o = oc.record_outcome(success=True, confidence=0.5, learning_signal=-3.0)
    assert o.learning_signal == pytest.approx(-1.0)


def test_from_verification_records_deviation_on_failure(oc):
    ok = oc.from_verification(intended_result="light on", observed_result="light on",
                              verified=True, confidence=1.0, now=lambda: 1.0)
    assert ok.success is True and ok.deviation == "" and ok.learning_signal == pytest.approx(1.0)
    bad = oc.from_verification(intended_result="light on", observed_result="light off",
                               verified=False, confidence=1.0, now=lambda: 1.0)
    assert bad.success is False
    assert "light on" in bad.deviation and "light off" in bad.deviation
    assert bad.learning_signal == pytest.approx(-1.0)


def test_confidence_clamped_on_the_record(oc):
    o = oc.record_outcome(success=True, confidence=1.7)
    assert o.confidence == 1.0


def test_summarize_rolls_up(oc):
    outs = [
        oc.record_outcome(success=True, confidence=0.9, capability="a"),
        oc.record_outcome(success=False, confidence=0.5, capability="a"),
        oc.record_outcome(success=True, confidence=1.0, capability="b"),
    ]
    stats = oc.summarize(outs)
    assert stats.count == 3 and stats.successes == 2
    assert stats.success_rate == pytest.approx(2 / 3)
    assert stats.mean_confidence == pytest.approx((0.9 + 0.5 + 1.0) / 3)
    # signals: +0.9, -0.5, +1.0 → mean (1.4/3)
    assert stats.mean_learning_signal == pytest.approx((0.9 - 0.5 + 1.0) / 3)


def test_by_capability_groups(oc):
    outs = [
        oc.record_outcome(success=True, confidence=0.9, capability="lights"),
        oc.record_outcome(success=False, confidence=0.9, capability="lights"),
        oc.record_outcome(success=True, confidence=0.9, capability="lock"),
    ]
    per = oc.by_capability(outs)
    assert set(per) == {"lights", "lock"}
    assert per["lights"].count == 2 and per["lights"].successes == 1
    assert per["lights"].success_rate == pytest.approx(0.5)
    assert per["lock"].success_rate == pytest.approx(1.0)


def test_summarize_empty_is_total(oc):
    stats = oc.summarize([])
    assert stats.count == 0 and stats.success_rate == 0.0 and stats.mean_learning_signal == 0.0


def test_outcome_round_trips_through_dict(oc):
    o = oc.record_outcome(
        intended_result="door locked", observed_result="door locked", success=True,
        confidence=0.95, side_effects=["chime played"], user_feedback="thanks",
        actor="jarvis", capability="lock.lock", correlation_id="cid", now=lambda: 7.0,
    )
    back = oc.Outcome.from_dict(o.to_dict())
    assert back == o
