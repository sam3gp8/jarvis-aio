"""Tests for lockdown engage behaviour (v6.31.0):

  • motorized/closeable openings (garage doors) are CLOSED on engage;
  • bare contacts (windows) can't be closed — alerted once, then left;
  • locks are locked;
  • startup adoption is silent (no announcement) so reboots don't re-notify.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, cc):
    monkeypatch.setattr(cc, "LOCKDOWN_STATE_PATH", str(tmp_path / "lockdown.json"))
    yield


def _mgr(cc, fake_hass):
    return cc.LockdownManager(fake_hass, {"honorific": "sir"})


def _calls(fake_hass, domain, service):
    return [c for c in fake_hass.service_calls if c[0] == domain and c[1] == service]


async def test_engage_locks_and_closes_garage(cc, fake_hass):
    fake_hass.states.set("lock.front", "unlocked", friendly_name="Front Lock")
    fake_hass.states.set("cover.garage_door", "open", device_class="garage",
                         friendly_name="Garage Door")
    fake_hass.states.set("binary_sensor.sams_window_1", "on", device_class="window",
                         friendly_name="Sam's Window 1")
    mgr = _mgr(cc, fake_hass)

    action = await mgr.engage("test")
    fake_hass.close_pending()

    assert _calls(fake_hass, "lock", "lock"), "should lock the unlocked lock"
    closes = _calls(fake_hass, "cover", "close_cover")
    assert closes and closes[0][2].get("entity_id") == "cover.garage_door"

    # the window can't be closed → left open and alerted, never auto-closed
    assert "binary_sensor.sams_window_1" in mgr.exempt_windows
    assert action and "Sam's Window 1 is open" in action["message"]
    assert "closed Garage Door" in action["message"]


async def test_bare_window_is_not_closed(cc, fake_hass):
    fake_hass.states.set("binary_sensor.window", "on", device_class="window")
    mgr = _mgr(cc, fake_hass)
    await mgr.engage("test")
    fake_hass.close_pending()
    assert not _calls(fake_hass, "cover", "close_cover")
    assert "binary_sensor.window" in mgr.exempt_windows


async def test_closed_garage_not_treated_as_intentional_open(cc, fake_hass):
    fake_hass.states.set("cover.garage_door", "open", device_class="garage")
    mgr = _mgr(cc, fake_hass)
    await mgr.engage("test")
    fake_hass.close_pending()
    # we closed it, so it's tracked as secured-by-us, NOT exempted as left-open
    assert "cover.garage_door" not in mgr.exempt_windows
    assert "cover.garage_door" in mgr._secured_by_us


async def test_silent_engage_secures_without_announcing(cc, fake_hass):
    fake_hass.states.set("lock.front", "unlocked")
    mgr = _mgr(cc, fake_hass)
    action = await mgr.engage("startup", auto=True, announce=False)
    fake_hass.close_pending()
    assert action is None                       # no notification on silent adopt
    assert mgr.active is True                     # but lockdown is active
    assert _calls(fake_hass, "lock", "lock")      # and it still locked up


async def test_nothing_to_do_is_fully_secured_message(cc, fake_hass):
    fake_hass.states.set("lock.front", "locked")   # already locked, nothing open
    mgr = _mgr(cc, fake_hass)
    action = await mgr.engage("test")
    fake_hass.close_pending()
    assert action["message"].endswith("the home was already fully secured.")
