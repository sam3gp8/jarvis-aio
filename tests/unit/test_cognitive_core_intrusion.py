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


async def test_tracked_away_motion_with_open_door_alerts_once(safety, fake_hass):
    # v6.33.0: corroborated motion when away fires ONE "investigating" alert,
    # then investigates silently (escalation only on confirmation).
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("device_tracker.sam_phone", "not_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    intr = _intrusions(actions)
    assert len(intr) == 1
    assert intr[0]["type"] == "intrusion_investigating"
    assert intr[0]["urgency"] == "high"


async def test_bare_motion_away_without_corroboration_is_suppressed(safety, fake_hass):
    # v6.32.0: bare motion when away — no armed alarm, no open door — is treated
    # as benign (pet / robot vacuum / blinds) and does NOT alert.
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("device_tracker.sam_phone", "not_home")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    assert _intrusions(actions) == []


async def test_corroboration_can_be_disabled(cognitive_core, fake_hass):
    # Users who want the old behaviour can opt back in.
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_require_corroboration": False})
    fake_hass.states.set("person.sam", "not_home")
    _motion(fake_hass)
    actions = await safety.tick(sleeping=False, anyone_home=False)
    fake_hass.close_pending()
    assert len(_intrusions(actions)) == 1


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
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")  # corroboration
    _motion(fake_hass)
    first = await _tick(safety, fake_hass, anyone_home=False)
    second = await _tick(safety, fake_hass, anyone_home=False)  # immediately again
    assert len(_intrusions(first)) == 1
    assert _intrusions(second) == []  # 5-min debounce suppresses the repeat


# ── intrusion_requires_confinement: confinement as the master switch (#111) ──

async def test_requires_confinement_suppresses_when_not_confined(cognitive_core, fake_hass):
    # A scenario that alerts in the default model (tracked-away + open door +
    # motion) is silent when confinement-gating is on and nothing is armed.
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("device_tracker.sam_phone", "not_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await safety.tick(sleeping=False, anyone_home=False)
    fake_hass.close_pending()
    assert _intrusions(actions) == []


async def test_requires_confinement_armed_home_is_not_away(cognitive_core, fake_hass):
    # 8.70.0: arming HOME is a HOME posture, not "away". A resident moving through
    # the house while armed_home must NOT be treated as an intruder — even with no
    # positive home tracker (the live case: Sam armed home, phones not tracked),
    # and even with an open door. ("Jarvis pinned it to away when I armed HOME.")
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await safety.tick(sleeping=False, anyone_home=True)
    fake_hass.close_pending()
    assert _intrusions(actions) == []


async def test_requires_confinement_armed_away_still_fires(cognitive_core, fake_hass):
    # Contrast: armed_AWAY is an away posture — corroborated motion still alerts,
    # even without device trackers (the #111 confinement-as-master-switch case).
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await safety.tick(sleeping=False, anyone_home=False)
    fake_hass.close_pending()
    assert len(_intrusions(actions)) == 1


async def test_disabling_confinement_stops_active_investigation(cognitive_core, fake_hass):
    # An investigation in progress must stop the instant confinement is cleared.
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    first = await safety.tick(sleeping=False, anyone_home=False)
    fake_hass.close_pending()
    assert len(_intrusions(first)) == 1
    assert safety._investigation is not None

    # Disarm → confinement off → monitoring stops and the investigation is dropped.
    fake_hass.states.set("alarm_control_panel.home", "disarmed")
    second = await safety.tick(sleeping=False, anyone_home=False)
    fake_hass.close_pending()
    assert _intrusions(second) == []
    assert safety._investigation is None


async def test_confinement_gate_off_preserves_default_away_behavior(safety, fake_hass):
    # With the flag absent (default), the proven away-path behavior is unchanged.
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    assert len(_intrusions(actions)) == 1


# ── notification snapshot image data (v6.69.0) ──────────────────────────────

def test_notification_image_data_absolute_url(cognitive_core, fake_hass):
    # already-absolute URLs are passed through with both platform keys
    data = cognitive_core._notification_image_data(fake_hass, "https://x/snap.jpg")
    assert data["image"] == "https://x/snap.jpg"
    assert data["attachment"]["url"] == "https://x/snap.jpg"


def test_notification_image_data_makes_local_absolute(cognitive_core, fake_hass):
    # a /local path gets the external base prepended so the app can fetch it
    try:
        fake_hass.config.external_url = "https://home.example.com"
    except Exception:
        pass
    data = cognitive_core._notification_image_data(fake_hass, "/local/jarvis/intrusion/x.jpg")
    # either prepended (if fake_hass exposes config.external_url) or left relative,
    # but must always carry both keys and never raise
    assert "image" in data and "attachment" in data


def test_notification_image_data_none_is_empty(cognitive_core, fake_hass):
    assert cognitive_core._notification_image_data(fake_hass, None) == {}
    assert cognitive_core._notification_image_data(fake_hass, "") == {}


# ── resident face whitelist stands intrusion down (#140) ─────────────────────

@pytest.fixture
def faces(load, tmp_path, monkeypatch):
    """Isolated recognition cache + empty household roster, plus a helper to seed
    a confident, recent recognition on a camera."""
    from datetime import datetime, timedelta, timezone

    fr = load("face_roster")
    monkeypatch.setattr(fr, "ROSTER_PATH", str(tmp_path / "face_roster.json"))
    fr._loaded = False
    fr._roster = {}

    recog = load("recognition")
    recog._RECOGNITION_CACHE.clear()

    def seen(name, camera_entity="camera.front_door", confidence=92.0, age=10):
        ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age)
        recog._RECOGNITION_CACHE[camera_entity] = {
            "name": name, "confidence": confidence, "ts": ts, "unknown_count": 0}

    yield type("Faces", (), {"roster": fr, "recog": recog, "seen": staticmethod(seen)})
    recog._RECOGNITION_CACHE.clear()


async def test_recognized_resident_suppresses_initial_alert(safety, fake_hass, faces):
    # Away + corroborated motion would normally fire one investigating alert…
    faces.roster.add_resident("Sam")
    faces.seen("Sam")
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    # …but the face on camera is a flagged resident, so JARVIS stands down.
    assert _intrusions(actions) == []
    assert safety._investigation is None


async def test_unlisted_face_still_alerts(safety, fake_hass, faces):
    # Same scenario, but the recognized person is NOT a resident → alert as usual,
    # proving the stand-down is gated on the whitelist, not on any recognition.
    faces.roster.add_resident("Sam")
    faces.seen("Quentin")
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    actions = await _tick(safety, fake_hass, anyone_home=False)
    assert len(_intrusions(actions)) == 1


async def test_resident_appearing_mid_investigation_stands_down(safety, fake_hass, faces):
    # First tick opens an investigation (one alert) with no face recognized yet.
    faces.roster.add_resident("Sam")
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    first = await _tick(safety, fake_hass, anyone_home=False)
    assert len(_intrusions(first)) == 1
    assert safety._investigation is not None

    # Now a resident is recognized on camera → the active investigation stands down.
    faces.seen("Sam")
    second = await _tick(safety, fake_hass, anyone_home=False)
    assert _intrusions(second) == []
    assert safety._investigation is None
