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


def _areas(safety, monkeypatch, mapping, breach, adjacent, load):
    """Give sensors areas and set the breach + its adjacent rooms."""
    monkeypatch.setattr(safety, "_motion_key", lambda eid: mapping.get(eid, eid))
    monkeypatch.setattr(safety, "_breach_area", lambda e: breach)
    rg = load("residence_graph")
    monkeypatch.setattr(rg, "adjacent_areas", lambda h, c, a: set(adjacent))


async def test_route_from_breach_confirms_and_notifies_all(safety, fake_hass, clock, monkeypatch, load):
    # Breach in the living room; hall is adjacent. Motion at the entry then into
    # the adjacent room = a real intrusion route.
    _areas(safety, monkeypatch,
           {"binary_sensor.living_motion": "living", "binary_sensor.hall_motion": "hall"},
           breach="living", adjacent={"hall"}, load=load)
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")   # at the breach
    await _intr(safety, fake_hass)
    clock["now"] += 20
    _motion(fake_hass, "binary_sensor.hall_motion")      # moved to adjacent room
    conf = await _intr(safety, fake_hass)
    assert len(conf) == 1
    assert conf[0]["type"] == "intrusion_confirmed"
    assert conf[0]["notify_all"] is True


async def test_motion_far_from_breach_does_not_confirm(safety, fake_hass, clock, monkeypatch, load):
    # Two zones of motion, but neither is the breach room or adjacent to it —
    # exactly the pattern behind a false alarm. JARVIS keeps watching, doesn't
    # conclude an intrusion.
    _areas(safety, monkeypatch,
           {"binary_sensor.attic_motion": "attic", "binary_sensor.study_motion": "study"},
           breach="garage", adjacent={"kitchen"}, load=load)
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.attic_motion")
    await _intr(safety, fake_hass)
    clock["now"] += 20
    _motion(fake_hass, "binary_sensor.study_motion")     # 2 zones, both far from breach
    assert await _intr(safety, fake_hass) == []           # not concluded
    assert safety._investigation is not None               # still investigating


async def test_no_breach_location_requires_sustained(safety, fake_hass, clock, monkeypatch):
    # When the breach has no known room, escalation needs sustained movement, not
    # a momentary two-zone blip.
    monkeypatch.setattr(safety, "_breach_area", lambda e: None)
    monkeypatch.setattr(safety, "_motion_key", lambda eid: eid)
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)
    clock["now"] += 20
    _motion(fake_hass, "binary_sensor.hall_motion")
    assert await _intr(safety, fake_hass) == []            # 20s < sustained window
    clock["now"] += 50                                     # now sustained (>60s)
    _motion(fake_hass, "binary_sensor.hall_motion")
    conf = await _intr(safety, fake_hass)
    assert conf and conf[0]["type"] == "intrusion_confirmed"


async def test_status_exposes_breach_and_route(safety, fake_hass, clock, monkeypatch, load, cc):
    _areas(safety, monkeypatch,
           {"binary_sensor.living_motion": "living"}, breach="living",
           adjacent=set(), load=load)
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)
    cc._CORE.safety_mgr = safety
    status = cc.intrusion_status()
    assert status["active"] is True
    assert status["breach_area"] == "living"
    assert "living" in status["path"]


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
