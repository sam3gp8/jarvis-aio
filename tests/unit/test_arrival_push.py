"""Issue #265 — opt-in 'heading home' awareness push.

The routing decision lives in the pure `cognitive_core._awareness_push_wanted`
so the behaviour-preserving guarantee (default OFF, never double-notify) is
testable without the HA-coupled `_emit_action`.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


_ARRIVING = {"type": "anticipation_arriving", "push": True}


def test_pushes_when_enabled_and_not_already_pushed(cc):
    assert cc._awareness_push_wanted(_ARRIVING, pushed=False, routed=False,
                                     enabled=True) is True


def test_default_off_preserves_behaviour(cc):
    # enabled=False (the default) → never pushes, so existing behaviour is kept
    assert cc._awareness_push_wanted(_ARRIVING, pushed=False, routed=False,
                                     enabled=False) is False


def test_no_double_push_when_already_pushed(cc):
    assert cc._awareness_push_wanted(_ARRIVING, pushed=True, routed=False,
                                     enabled=True) is False


def test_no_push_when_routed_to_car(cc):
    assert cc._awareness_push_wanted(_ARRIVING, pushed=False, routed=True,
                                     enabled=True) is False


def test_non_push_eligible_action_never_pushes(cc):
    plain = {"type": "camera_event"}          # no push flag
    assert cc._awareness_push_wanted(plain, pushed=False, routed=False,
                                     enabled=True) is False
    assert cc._awareness_push_wanted({"push": False}, pushed=False, routed=False,
                                     enabled=True) is False
