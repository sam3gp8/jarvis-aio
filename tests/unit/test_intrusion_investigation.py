"""Tests for the one-alert-then-investigate intrusion flow (v6.33.0).

Corroborated motion when away fires exactly one alert, then JARVIS investigates
silently until it confirms an intrusion (escalate to the whole house + every
device) or clears it as benign — never a stream of repeat alerts.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def clock(cc, monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(cc.time, "time", lambda: t["now"])
    return t


@pytest.fixture
def safety(cc, fake_hass):
    return cc.SafetyManager(fake_hass, {"honorific": "sir"})


def _away(hass):
    hass.states.set("person.sam", "not_home")
    hass.states.set("binary_sensor.front_door", "on", device_class="door")  # corroboration


def _motion(hass, eid, on=True):
    hass.states.set(eid, "on" if on else "off", device_class="motion")


async def _intr(safety, hass):
    actions = await safety.tick(sleeping=False, anyone_home=False)
    hass.close_pending()
    return [a for a in actions if str(a.get("type", "")).startswith("intrusion")]


async def test_one_alert_then_silent_investigation(safety, fake_hass, clock):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    first = await _intr(safety, fake_hass)
    assert len(first) == 1 and first[0]["type"] == "intrusion_investigating"
    assert first[0]["urgency"] == "high"
    # same single sensor keeps firing → no more alerts
    clock["now"] += 30
    assert await _intr(safety, fake_hass) == []
    assert safety._investigation is not None      # still watching


async def test_spread_confirms_intrusion_and_notifies_all(safety, fake_hass, clock):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)                 # investigating
    clock["now"] += 20
    _motion(fake_hass, "binary_sensor.hall_motion")   # a SECOND zone
    conf = await _intr(safety, fake_hass)
    assert len(conf) == 1
    assert conf[0]["type"] == "intrusion_confirmed"
    assert conf[0]["urgency"] == "critical"
    assert conf[0]["notify_all"] is True
    clock["now"] += 20
    assert await _intr(safety, fake_hass) == []    # does not re-escalate


async def test_camera_person_leads_to_confirmation(safety, fake_hass, clock):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)
    clock["now"] += 20
    fake_hass.states.set("binary_sensor.front_cam_person", "on", device_class="occupancy")
    conf = await _intr(safety, fake_hass)
    assert conf and conf[0]["type"] == "intrusion_confirmed"


async def test_benign_motion_clears_without_escalation(safety, fake_hass, clock, cc):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)                 # investigating
    _motion(fake_hass, "binary_sensor.living_motion", on=False)  # it stops
    clock["now"] += cc.INTRUSION_CLEAR_QUIET_SECS + 5
    assert await _intr(safety, fake_hass) == []
    assert safety._investigation is None           # nothing of note


async def test_residents_return_stops_investigation(safety, fake_hass, clock):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)
    assert safety._investigation is not None
    fake_hass.states.set("person.sam", "home")     # residents come home
    clock["now"] += 10
    await safety.tick(sleeping=False, anyone_home=True)
    fake_hass.close_pending()
    assert safety._investigation is None


async def test_notify_all_devices_hits_every_mobile_app(cc, fake_hass):
    fake_hass.services.register("notify", "mobile_app_sam")
    fake_hass.services.register("notify", "mobile_app_alex")
    fake_hass.services.register("notify", "slack")   # not a device target
    await cc._notify_all_devices(fake_hass, {}, "intrusion!", "intrusion_confirmed")
    names = {c[1] for c in fake_hass.service_calls if c[0] == "notify"}
    assert names == {"mobile_app_sam", "mobile_app_alex"}
    assert any(c[0] == "persistent_notification" for c in fake_hass.service_calls)


async def test_notify_all_falls_back_when_no_devices(cc, fake_hass):
    # no mobile_app services registered → fall back to the configured single one
    await cc._notify_all_devices(
        fake_hass, {"notify_service": "notify.fallback"}, "hi", "intrusion_investigating")
    assert ("notify", "fallback", {"message": "hi", "title": "JARVIS — Security Alert"}) \
        in fake_hass.service_calls
