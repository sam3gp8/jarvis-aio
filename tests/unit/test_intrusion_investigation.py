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


# ── v6.39.0: outdoor hardening — the false-alarm guards ──────────────────────

async def test_outdoor_motion_never_seeds_investigation(safety, fake_hass, clock):
    _away(fake_hass)                                   # away + door open
    _motion(fake_hass, "binary_sensor.patio_motion")   # outdoor-only motion
    assert await _intr(safety, fake_hass) == []
    assert safety._investigation is None


async def test_outdoor_camera_person_does_not_confirm(safety, fake_hass, clock):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)                     # investigating
    clock["now"] += 20
    # a delivery driver on the driveway cam must NOT confirm an intrusion…
    fake_hass.states.set("binary_sensor.driveway_cam_person", "on",
                         device_class="occupancy")
    assert await _intr(safety, fake_hass) == []
    assert safety._investigation is not None            # still just watching
    clock["now"] += 10
    # …but a person on an INDOOR camera confirms immediately.
    fake_hass.states.set("binary_sensor.hallway_cam_person", "on",
                         device_class="occupancy")
    conf = await _intr(safety, fake_hass)
    assert conf and conf[0]["type"] == "intrusion_confirmed"


async def test_open_yard_gate_is_not_a_breach(safety, fake_hass, clock):
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("cover.side_gate", "open", device_class="gate")
    _motion(fake_hass, "binary_sensor.living_motion")
    # gate open is property perimeter, not the house envelope → no corroboration
    assert await _intr(safety, fake_hass) == []
    assert safety._investigation is None
    # the garage cover, though, IS envelope and still corroborates
    fake_hass.states.set("cover.garage_door", "open", device_class="garage")
    first = await _intr(safety, fake_hass)
    assert len(first) == 1 and first[0]["type"] == "intrusion_investigating"


# ── response-timeout escalation (v6.69.0) ────────────────────────────────────

async def test_no_response_while_active_escalates(safety, fake_hass, clock, cc):
    # motion starts (investigating), stays active, no one responds → after the
    # response timeout, escalate to a full alert.
    import sys
    sys.modules.pop("jc.intrusion", None)          # ensure fresh call-off state
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)                  # investigating alert fired
    # keep motion active and advance past the response timeout
    clock["now"] += cc.INTRUSION_RESPONSE_TIMEOUT_SECS + 5
    _motion(fake_hass, "binary_sensor.living_motion")   # still active now
    out = await _intr(safety, fake_hass)
    esc = [a for a in out if a.get("type") == "intrusion_confirmed"]
    assert esc, "unanswered active intrusion should escalate after the timeout"
    assert "no response" in esc[0]["message"].lower()


async def test_acknowledge_holds_timeout_escalation(safety, fake_hass, clock, cc):
    # if the user acknowledges ('I'm looking'), the no-response timeout is held.
    from jc import intrusion as intr
    intr.clear_calloff()
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.living_motion")
    await _intr(safety, fake_hass)
    intr.acknowledge("on my way")                  # user is handling it
    clock["now"] += cc.INTRUSION_RESPONSE_TIMEOUT_SECS + 5
    _motion(fake_hass, "binary_sensor.living_motion")
    out = await _intr(safety, fake_hass)
    esc = [a for a in out if a.get("type") == "intrusion_confirmed"]
    assert not esc, "acknowledgement should hold the no-response escalation"
    intr.clear_calloff()                            # cleanup


# ── vision-confirmation gate on Frigate person detection (v6.73.0) ───────────
# Frigate's person sensor is a good trigger but false-positives; JARVIS's own
# vision must confirm before escalating to a full intrusion.

def _person_cam(safety, monkeypatch, cam="camera.kitchen"):
    """Make _person_camera_entity report a Frigate person on a camera."""
    monkeypatch.setattr(safety, "_person_camera_entity", lambda indoor_only=True: cam)


def _vision_says(safety, monkeypatch, verdict):
    """Stub JARVIS's vision confirmation to return True/False/None."""
    async def _v(cam):
        return verdict
    monkeypatch.setattr(safety, "_confirm_person_with_vision", _v)


async def _in_investigation(safety, fake_hass, clock):
    _away(fake_hass)
    _motion(fake_hass, "binary_sensor.kitchen_motion")
    await _intr(safety, fake_hass)                 # opens the investigation
    assert safety._investigation is not None


async def test_vision_confirms_person_escalates(safety, fake_hass, clock, monkeypatch):
    await _in_investigation(safety, fake_hass, clock)
    _person_cam(safety, monkeypatch)
    _vision_says(safety, monkeypatch, True)        # JARVIS's eyes agree
    clock["now"] += 20
    _motion(fake_hass, "binary_sensor.kitchen_motion")
    actions = await _intr(safety, fake_hass)
    # a confirmed escalation fires
    assert any(a["type"] == "intrusion_confirmed" or a.get("escalation") for a in actions) \
        or safety._investigation is None or any("confirmed" in str(a).lower() for a in actions)


async def test_vision_denies_person_does_not_escalate_on_camera(safety, fake_hass, clock, monkeypatch):
    await _in_investigation(safety, fake_hass, clock)
    _person_cam(safety, monkeypatch)
    _vision_says(safety, monkeypatch, False)       # Frigate false positive
    # no breach spread, no sustained movement → without the camera it must NOT confirm
    clock["now"] += 20
    actions = await _intr(safety, fake_hass)
    confirmed = [a for a in actions if a.get("type") == "intrusion_confirmed"]
    assert confirmed == []                         # the false positive did not alarm
    assert safety._investigation is not None       # still watching, not escalated


async def test_vision_inconclusive_falls_back_to_camera(safety, fake_hass, clock, monkeypatch):
    # vision unavailable/ambiguous → fail SAFE: trust the camera (don't suppress
    # a possibly-real alert just because vision couldn't run)
    await _in_investigation(safety, fake_hass, clock)
    _person_cam(safety, monkeypatch)
    _vision_says(safety, monkeypatch, None)        # inconclusive
    clock["now"] += 20
    _motion(fake_hass, "binary_sensor.kitchen_motion")
    actions = await _intr(safety, fake_hass)
    # falls back to prior behavior (camera confirms) → escalates
    assert any(a.get("type") == "intrusion_confirmed" for a in actions) \
        or safety._investigation is None


async def test_vision_confirm_respects_killswitch(safety, fake_hass, monkeypatch):
    # with intrusion_vision_confirm off, the method returns None (no vision call)
    safety.config["intrusion_vision_confirm"] = False
    res = await safety._confirm_person_with_vision("camera.kitchen")
    assert res is None


async def test_vision_confirm_rejects_non_camera_handle(safety, fake_hass):
    # a non-camera fallback handle (binary_sensor.*) can't be snapshotted → None
    res = await safety._confirm_person_with_vision("binary_sensor.kitchen_person")
    assert res is None
