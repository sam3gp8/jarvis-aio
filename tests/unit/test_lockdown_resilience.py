"""Regression tests for lockdown resilience (v6.24.2).

Lockdown must work even when the cognitive-core start path didn't initialise the
manager (it lives deep inside the observer's start(), wrapped in a non-fatal
catch). And, per the security requirement, it must auto-engage when any alarm is
armed and lift when disarmed — event-driven, independent of the loop tick.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_lockdown(tmp_path, monkeypatch, cognitive_core):
    """Each test gets a private lockdown-state file and a clean _CORE, so
    persisted state never bleeds between tests (or between runs)."""
    monkeypatch.setattr(cognitive_core, "LOCKDOWN_STATE_PATH", str(tmp_path / "lockdown_state.json"))
    cognitive_core._CORE.lockdown_mgr = None
    cognitive_core._CORE.hass = None
    cognitive_core._CORE.config = {"honorific": "sir", "lockdown_auto_on_arm": True}
    cognitive_core._CORE.alarm_unsub = None
    yield


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


# ── Open-entity policy (v6.24.3): snapshot-and-ignore at engage, secure-or-ignore on change ──
from fakes import FakeState  # noqa: E402


async def _engage(cc, hass):
    await cc.request_lockdown(True, reason="test", hass=hass)
    hass.close_pending()
    return cc._CORE.lockdown_mgr


async def test_new_open_controllable_cover_is_closed(cognitive_core, fake_hass):
    mgr = await _engage(cognitive_core, fake_hass)
    fake_hass.service_calls.clear()
    old = FakeState("cover.garage_door", "closed", {"device_class": "garage"})
    new = FakeState("cover.garage_door", "open", {"device_class": "garage"})
    fake_hass.states.set("cover.garage_door", "open", device_class="garage")
    action = await mgr.handle_state_change("cover.garage_door", old, new)
    fake_hass.close_pending()  # discard the delayed verify task
    assert ("cover", "close_cover", {"entity_id": "cover.garage_door"}) in fake_hass.service_calls
    assert action is None
    assert "cover.garage_door" in mgr._secured_by_us


async def test_new_open_contact_sensor_is_assumed_intentional(cognitive_core, fake_hass):
    mgr = await _engage(cognitive_core, fake_hass)
    fake_hass.service_calls.clear()
    old = FakeState("binary_sensor.kitchen_window", "off", {"device_class": "window"})
    new = FakeState("binary_sensor.kitchen_window", "on", {"device_class": "window"})
    action = await mgr.handle_state_change("binary_sensor.kitchen_window", old, new)
    fake_hass.close_pending()
    assert fake_hass.service_calls == []          # nothing JARVIS could close
    assert action is None                          # ignored, no alert
    assert "binary_sensor.kitchen_window" in mgr.exempt_windows


async def test_uncloseable_open_at_engage_is_ignored(cognitive_core, fake_hass):
    # A bare window contact open at engage can't be closed remotely, so it's left
    # as-is and not fought afterwards. (Closeable covers like garage doors ARE
    # closed on engage — see test_lockdown_engage.)
    _reset(cognitive_core)
    fake_hass.states.set("binary_sensor.window", "on", device_class="window")  # already open
    mgr = await _engage(cognitive_core, fake_hass)
    fake_hass.close_pending()
    assert "binary_sensor.window" in mgr.exempt_windows
    fake_hass.service_calls.clear()
    old = FakeState("binary_sensor.window", "on", {"device_class": "window"})
    new = FakeState("binary_sensor.window", "on", {"device_class": "window"})
    action = await mgr.handle_state_change("binary_sensor.window", old, new)
    assert action is None
    assert fake_hass.service_calls == []           # never fought


async def test_reopen_after_secure_is_adopted_and_alerts(cognitive_core, fake_hass):
    mgr = await _engage(cognitive_core, fake_hass)
    mgr._secured_by_us.add("cover.garage_door")    # JARVIS already closed it once
    old = FakeState("cover.garage_door", "closed", {"device_class": "garage"})
    new = FakeState("cover.garage_door", "open", {"device_class": "garage"})
    action = await mgr.handle_state_change("cover.garage_door", old, new)
    fake_hass.close_pending()
    assert action is not None and action["type"] == "lockdown_breach"
    assert "cover.garage_door" in mgr.exempt_windows
    assert "cover.garage_door" not in mgr._secured_by_us
