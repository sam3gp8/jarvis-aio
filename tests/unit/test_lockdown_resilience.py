"""Regression tests for lockdown resilience (v6.24.2).

Lockdown must work even when the cognitive-core start path didn't initialise the
manager (it lives deep inside the observer's start(), wrapped in a non-fatal
catch). And, per the security requirement, it must auto-engage when any alarm is
armed and lift when disarmed — event-driven, independent of the loop tick.
"""
import pytest


def _reset(cc):
    cc._CORE.lockdown_mgr = None
    cc._CORE.hass = None
    cc._CORE.config = {"honorific": "sir", "lockdown_auto_on_arm": True}
    cc._CORE.alarm_unsub = None


async def test_manual_toggle_lazily_creates_manager(cognitive_core, fake_hass):
    # Boot where start() never set lockdown_mgr — the manual toggle must still work.
    _reset(cognitive_core)
    ok = await cognitive_core.request_lockdown(True, reason="test", hass=fake_hass)
    fake_hass.close_pending()
    assert ok is True
    assert cognitive_core._CORE.lockdown_mgr is not None
    assert cognitive_core.is_lockdown() is True

    ok2 = await cognitive_core.request_lockdown(False, reason="test", hass=fake_hass)
    fake_hass.close_pending()
    assert ok2 is True
    assert cognitive_core.is_lockdown() is False


async def test_request_without_hass_or_core_is_safe(cognitive_core):
    # No hass anywhere → returns False rather than raising.
    _reset(cognitive_core)
    ok = await cognitive_core.request_lockdown(True, reason="test")
    assert ok is False
    assert cognitive_core.is_lockdown() is False


async def test_alarm_armed_engages_on_ensure(cognitive_core, fake_hass):
    # A reboot while the alarm is armed must re-engage lockdown at setup time.
    _reset(cognitive_core)
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    await cognitive_core.ensure_lockdown(fake_hass, cognitive_core._CORE.config)
    fake_hass.close_pending()
    assert cognitive_core.is_lockdown() is True
    assert cognitive_core._CORE.lockdown_mgr.auto is True


async def test_alarm_disarm_lifts_auto_lockdown(cognitive_core, fake_hass):
    _reset(cognitive_core)
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    await cognitive_core.ensure_lockdown(fake_hass, cognitive_core._CORE.config)
    fake_hass.close_pending()
    assert cognitive_core.is_lockdown() is True

    fake_hass.states.set("alarm_control_panel.home", "disarmed")
    await cognitive_core._sync_lockdown_to_alarm("test disarm")
    fake_hass.close_pending()
    assert cognitive_core.is_lockdown() is False


async def test_auto_on_arm_can_be_disabled(cognitive_core, fake_hass):
    _reset(cognitive_core)
    cognitive_core._CORE.config["lockdown_auto_on_arm"] = False
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    await cognitive_core.ensure_lockdown(fake_hass, cognitive_core._CORE.config)
    fake_hass.close_pending()
    # Opt-out respected: armed alarm does NOT force lockdown.
    assert cognitive_core.is_lockdown() is False
