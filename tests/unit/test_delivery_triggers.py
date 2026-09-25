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
