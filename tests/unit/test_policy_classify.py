"""Capability-based risk classification in policy.classify().

Known high-signal (domain, service) pairs keep their exact risk; novel services
on inherently security domains are escalated so nothing unenumerated defaults to
LOW on a lock/alarm — while safe directions (lock, arm) and benign devices keep
their low friction.
"""
import pytest


@pytest.fixture
def pol(load):
    return load("policy")


def test_known_pairs_unchanged(pol):
    assert pol.classify("alarm_control_panel", "alarm_disarm")[0] == "critical"
    assert pol.classify("lock", "unlock")[0] == "high"
    assert pol.classify("cover", "open_cover")[0] == "medium"
    assert pol.classify("lock", "open")[0] == "medium"
    assert pol.classify("alarm_control_panel", "alarm_arm_away")[0] == "medium"


def test_convenience_stays_low(pol):
    assert pol.classify("light", "turn_on", "light.kitchen")[0] == "low"
    assert pol.classify("media_player", "media_play")[0] == "low"
    assert pol.classify("climate", "set_temperature")[0] == "low"
    assert pol.classify("scene", "turn_on")[0] == "low"


def test_safe_security_direction_stays_low(pol):
    # locking a lock is the safe direction — must not gain confirmation friction
    assert pol.classify("lock", "lock")[0] == "low"


def test_novel_guard_dropping_service_escalates_high(pol):
    # services we never enumerated, on a security domain, that drop a guard
    assert pol.classify("lock", "unlatch")[0] == "high"
    assert pol.classify("lock", "unbolt")[0] == "high"


def test_novel_unknown_security_service_needs_review(pol):
    # unrecognized actuating service on a security domain → medium, not low
    assert pol.classify("lock", "grant_access")[0] == "medium"
    assert pol.classify("alarm_control_panel", "alarm_trigger")[0] == "medium"


def test_read_only_service_on_security_domain_is_low(pol):
    assert pol.classify("lock", "update")[0] == "low"


def test_security_named_switch_off_still_high(pol):
    assert pol.classify("switch", "turn_off", "switch.garage_door")[0] == "high"
    assert pol.classify("switch", "turn_off", "switch.front_lock")[0] == "high"


def test_security_named_switch_on_not_over_frictioned(pol):
    # turning ON a switch that merely contains 'garage' (e.g. garage lights)
    # must stay LOW — only turn_off was ever escalated
    assert pol.classify("switch", "turn_on", "switch.garage_lights")[0] == "low"
