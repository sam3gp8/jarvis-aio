"""Tests for timely delivery/mail triggers (v8.0.0): discovery of the porch /
mailbox sensors that should prompt an immediate check, and the direct mailbox
announcement path (no vision)."""
import pytest


@pytest.fixture
def pm(load):
    return load("package_monitor")


def test_delivery_motion_sensors_discovery(pm, fake_hass):
    h = fake_hass
    h.states.set("binary_sensor.front_porch_motion", "off", device_class="motion")
    h.states.set("binary_sensor.doorbell_motion", "off", device_class="motion",
                 friendly_name="Doorbell Motion")
    h.states.set("binary_sensor.driveway", "off", device_class="occupancy")
    h.states.set("binary_sensor.mailbox", "off", device_class="opening",
                 friendly_name="Mailbox")
    h.states.set("binary_sensor.kitchen_motion", "off", device_class="motion")   # unrelated
    h.states.set("binary_sensor.bedroom_temp", "off", device_class="temperature")  # wrong class
    ids = pm.delivery_motion_sensors(h)
    assert "binary_sensor.front_porch_motion" in ids
    assert "binary_sensor.doorbell_motion" in ids
    assert "binary_sensor.driveway" in ids
    assert "binary_sensor.mailbox" in ids
    assert "binary_sensor.kitchen_motion" not in ids
    assert "binary_sensor.bedroom_temp" not in ids


def test_discovery_rejects_substring_false_positives(pm, fake_hass):
    # Whole-word matching: an indoor "front room", Tesla "sentry mode", and a
    # "voicemail" flag must never trigger porch sweeps or mail announcements.
    h = fake_hass
    h.states.set("binary_sensor.front_room_motion", "off", device_class="motion")
    h.states.set("binary_sensor.model_y_sentry_mode", "off", device_class="motion")
    h.states.set("binary_sensor.voicemail_waiting", "off")
    ids = pm.delivery_motion_sensors(h)
    assert "binary_sensor.front_room_motion" not in ids
    assert "binary_sensor.model_y_sentry_mode" not in ids
    assert "binary_sensor.voicemail_waiting" not in pm.mailbox_sensors(h)


def test_camera_package_detection_sensor_is_a_trigger(pm, fake_hass):
    # UniFi/Reolink-style package-detection sensors often have no device_class.
    h = fake_hass
    h.states.set("binary_sensor.front_doorbell_package_detected", "off")
    h.states.set("binary_sensor.doorbell_battery_problem", "off", device_class="problem")
    ids = pm.delivery_motion_sensors(h)
    assert "binary_sensor.front_doorbell_package_detected" in ids
    assert "binary_sensor.doorbell_battery_problem" not in ids


async def test_concurrent_evaluations_announce_once(pm, load, fake_hass, monkeypatch):
    # Doorbell press + doorbell motion can reach evaluate() at the same moment;
    # the lock must make the second one see the first's state, not re-announce.
    import asyncio
    pm._STATE.clear()
    spoke = []
    tts_helper = load("tts_helper")

    async def _slow_announce(hass, msg, tts, spk, context=None):
        await asyncio.sleep(0.01)          # yield mid-announce, like real TTS
        spoke.append(msg)

    monkeypatch.setattr(tts_helper, "async_announce", _slow_announce)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_log", lambda *a, **k: None)
    det = {"package": True, "mail": False, "count": 1}
    await asyncio.gather(
        pm.evaluate(fake_hass, None, "Sir", "tts.x", [], "camera.front_door", det, "doorbell"),
        pm.evaluate(fake_hass, None, "Sir", "tts.x", [], "camera.front_door", det, "motion"),
    )
    assert len([m for m in spoke if "package" in m]) == 1


def test_mailbox_sensors_discovery(pm, fake_hass):
    h = fake_hass
    h.states.set("binary_sensor.mailbox", "off", device_class="opening",
                 friendly_name="Mailbox")
    h.states.set("binary_sensor.mail_slot", "off", device_class="door")
    h.states.set("binary_sensor.email_flag", "off")               # 'email' excluded
    h.states.set("binary_sensor.front_door", "off", device_class="door")   # not mail
    ids = pm.mailbox_sensors(h)
    assert "binary_sensor.mailbox" in ids
    assert "binary_sensor.mail_slot" in ids
    assert "binary_sensor.email_flag" not in ids
    assert "binary_sensor.front_door" not in ids


async def test_announce_mail_speaks_when_allowed(pm, load, fake_hass, monkeypatch):
    pm._MAILBOX_CD.clear()
    spoke = []
    tts_helper = load("tts_helper")

    async def _fake(hass, msg, tts, spk, context=None):
        spoke.append(msg)

    monkeypatch.setattr(tts_helper, "async_announce", _fake)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    ok = await pm.announce_mail(fake_hass, "Sir", "tts.x", ["media_player.k"],
                                "binary_sensor.mailbox")
    assert ok is True
    assert spoke and "mail has arrived" in spoke[0].lower()


async def test_announce_mail_silent_in_quiet_hours(pm, fake_hass, monkeypatch):
    pm._MAILBOX_CD.clear()
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: True)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    assert await pm.announce_mail(fake_hass, "Sir", "tts.x", [],
                                  "binary_sensor.mailbox2") is False


async def test_announce_mail_silent_when_announcements_off(pm, fake_hass, monkeypatch):
    pm._MAILBOX_CD.clear()
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: False)
    assert await pm.announce_mail(fake_hass, "Sir", "tts.x", [],
                                  "binary_sensor.mailbox3") is False


async def test_announce_mail_dedups_within_cooldown(pm, load, fake_hass, monkeypatch):
    pm._MAILBOX_CD.clear()
    spoke = []
    tts_helper = load("tts_helper")

    async def _fake(hass, msg, tts, spk, context=None):
        spoke.append(msg)

    monkeypatch.setattr(tts_helper, "async_announce", _fake)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    a = await pm.announce_mail(fake_hass, "Sir", "tts.x", [], "binary_sensor.mb")
    b = await pm.announce_mail(fake_hass, "Sir", "tts.x", [], "binary_sensor.mb")
    assert a is True and b is False and len(spoke) == 1


# ── Visual confirmation of a real delivery (v8.2.0) ──────────────────────────
# A mailbox opening / motion trigger is only a hint; JARVIS should confirm an
# actual mail carrier or delivery vehicle on a camera before announcing, and
# only fall back to the raw sensor when no camera can see the spot.

def test_parse_detection_reads_carrier_flag(pm):
    det = pm._parse_detection('{"package": false, "mail": false, "carrier": true}')
    assert det["carrier"] is True
    assert det["package"] is False and det["mail"] is False


def test_detection_from_text_flags_a_carrier(pm):
    assert pm.detection_from_text("a USPS mail truck is at the curb")["carrier"] is True
    assert pm.detection_from_text("a FedEx delivery driver dropped a box")["carrier"] is True
    assert pm.detection_from_text("an empty quiet porch")["carrier"] is False


async def test_carrier_present_confirms_from_camera(pm, fake_hass, monkeypatch):
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_mail_view_cameras", lambda h, c=None: ["camera.mailbox"])

    async def _detect(hass, client, eid):
        return {"package": False, "mail": False, "carrier": True, "count": 0}
    monkeypatch.setattr(pm, "detect_on_camera", _detect)
    assert await pm.carrier_present(fake_hass, None) is True


async def test_carrier_present_suppresses_when_camera_sees_nothing(pm, fake_hass, monkeypatch):
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_mail_view_cameras", lambda h, c=None: ["camera.mailbox"])

    async def _detect(hass, client, eid):
        return {"package": False, "mail": False, "carrier": False, "count": 0}
    monkeypatch.setattr(pm, "detect_on_camera", _detect)
    # A camera looked and saw no delivery → False (do NOT announce), not None.
    assert await pm.carrier_present(fake_hass, None) is False


async def test_carrier_present_falls_back_when_no_camera(pm, fake_hass, monkeypatch):
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_mail_view_cameras", lambda h, c=None: [])
    # No camera to look at → None, so the caller trusts the raw sensor.
    assert await pm.carrier_present(fake_hass, None) is None


async def test_carrier_present_falls_back_when_frames_unreadable(pm, fake_hass, monkeypatch):
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_mail_view_cameras", lambda h, c=None: ["camera.mailbox"])

    async def _detect(hass, client, eid):
        return None      # blank/unreadable frame — never actually looked
    monkeypatch.setattr(pm, "detect_on_camera", _detect)
    assert await pm.carrier_present(fake_hass, None) is None


async def test_carrier_present_silent_in_quiet_hours(pm, fake_hass, monkeypatch):
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: True)

    async def _boom(*a, **k):
        raise AssertionError("must not run vision during quiet hours")
    monkeypatch.setattr(pm, "detect_on_camera", _boom)
    assert await pm.carrier_present(fake_hass, None) is False


def test_mail_view_cameras_includes_mailbox_and_driveway(pm, fake_hass, monkeypatch):
    monkeypatch.setattr(pm, "watched_cameras", lambda h, c=None: ["camera.front_door"])

    class _S:
        def __init__(self, e): self.entity_id = e
    import sys
    import types
    # pm is loaded as jc.package_monitor (__package__ == "jc"), so its
    # `from .camera import ...` resolves to jc.camera.
    fake_cam = types.SimpleNamespace(
        active_camera_states=lambda h: [
            _S("camera.front_door"), _S("camera.mailbox"),
            _S("camera.driveway"), _S("camera.backyard")])
    monkeypatch.setitem(sys.modules, "jc.camera", fake_cam)
    cams = pm._mail_view_cameras(fake_hass)
    assert "camera.mailbox" in cams and "camera.driveway" in cams
    assert "camera.backyard" not in cams          # not a delivery-view camera
    assert "camera.front_door" in cams            # porch set preserved
