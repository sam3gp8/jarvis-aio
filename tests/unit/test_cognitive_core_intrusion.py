"""Regression tests for SafetyManager intrusion detection.

These pin the away-vs-home decision that drives "motion … while no one is home"
alerts — the false-positive class fixed in v6.7.1. Intrusion must fire only when
residents are CONFIDENTLY away (tracked away / armed-away), never on the mere
absence of occupancy.

tick() is called with sleeping=False so the nighttime-lockdown branch (which
would touch the filesystem) is skipped; we assert purely on the returned actions.
"""
import pytest


@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})


def _motion(hass, eid="binary_sensor.hall_motion"):
    hass.states.set(eid, "on", device_class="motion")


async def _tick(safety, hass, anyone_home):
    actions = await safety.tick(sleeping=False, anyone_home=anyone_home)
    hass.close_pending()  # discard any announce coroutine; we assert on actions
    return actions


def _intrusions(actions):
    return [a for a in actions if str(a.get("type", "")).startswith("intrusion")]


async def test_untracked_resident_motion_is_not_intrusion(safety, fake_hass):
    # The bug: someone home & moving, but NO person/device_tracker/alarm at all.
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=True)
    assert _intrusions(actions) == []


async def test_tracked_away_motion_fires_critical_intrusion(safety, fake_hass):
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("device_tracker.sam_phone", "not_home")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    intr = _intrusions(actions)
    assert len(intr) == 1
    assert intr[0]["type"] == "intrusion_away"
    assert intr[0]["urgency"] == "critical"


async def test_person_home_suppresses_intrusion(safety, fake_hass):
    # Phone/person home wins outright even if motion is firing.
    fake_hass.states.set("person.sam", "home")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=True)
    assert _intrusions(actions) == []


async def test_armed_away_alarm_enables_intrusion_without_trackers(safety, fake_hass):
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    assert len(_intrusions(actions)) == 1


async def test_no_motion_no_intrusion_even_when_away(safety, fake_hass):
    fake_hass.states.set("person.sam", "not_home")  # away, but nothing moving
    actions = await _tick(safety, fake_hass, anyone_home=False)
    assert _intrusions(actions) == []


async def test_intrusion_debounced_within_window(safety, fake_hass):
    fake_hass.states.set("person.sam", "not_home")
    _motion(fake_hass)
    first = await _tick(safety, fake_hass, anyone_home=False)
    second = await _tick(safety, fake_hass, anyone_home=False)  # immediately again
    assert len(_intrusions(first)) == 1
    assert _intrusions(second) == []  # 5-min debounce suppresses the repeat
