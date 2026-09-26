"""Tests for package_monitor false-positive fixes (v6.46.0):
negation-aware text detection, blank-frame guard, and the two-frame
confirmation required before a new arrival can announce."""
import pytest


@pytest.fixture
def pm(load):
    return load("package_monitor")


# ── detection_from_text: negation handling ───────────────────────────────────

def test_text_positive_package(pm):
    det = pm.detection_from_text("A UPS driver left a package by the door")
    assert det["package"] is True and det["count"] == 1


def test_text_negated_package_is_false(pm):
    for text in (
        "A person at the door, no package visible",
        "Someone rang the bell but is not carrying a delivery",
        "Visitor without any boxes at the door",
        "No sign of packages or mail on the porch",
        "There are no parcels left at the doorway",
    ):
        det = pm.detection_from_text(text)
        assert det["package"] is False, text
        assert det["mail"] is False, text


def test_text_negation_does_not_mask_real_positive(pm):
    # A negation about one thing must not erase a genuine other sighting.
    det = pm.detection_from_text("No mail today, but a package sits on the step")
    assert det["package"] is True
    assert det["mail"] is False


def test_text_mail_positive(pm):
    det = pm.detection_from_text("The mail carrier left two envelopes")
    assert det["mail"] is True


# ── detect_on_camera: blank-frame guard ──────────────────────────────────────

async def test_blank_frame_returns_none(pm, load, fake_hass, monkeypatch):
    cam = load("camera")

    async def _fake_img(hass, eid):
        return b"\xff\xd8" + b"\x00" * 5000  # nominal JPEG bytes

    monkeypatch.setattr(cam, "_get_best_image", _fake_img)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: True)
    called = {"vision": False}
    monkeypatch.setattr(cam, "_make_client",
                        lambda *a, **k: called.__setitem__("vision", True))
    det = await pm.detect_on_camera(fake_hass, None, "camera.front_door")
    assert det is None
    assert called["vision"] is False  # never reached the vision model


# ── periodic_check: two-frame confirmation ───────────────────────────────────

def test_confirm_transitions_drops_unconfirmed_new_positive(pm):
    prev = {"package": False, "mail": False}
    det = {"package": True, "mail": False, "count": 1, "description": "box"}
    out = pm._confirm_transitions(prev, det, {"package": False, "mail": False})
    assert out["package"] is False and out["count"] == 0


def test_confirm_transitions_keeps_confirmed_positive(pm):
    prev = {"package": False, "mail": False}
    det = {"package": True, "mail": False, "count": 1, "description": "box"}
    out = pm._confirm_transitions(prev, det, {"package": True, "mail": False, "count": 2})
    assert out["package"] is True and out["count"] == 2


def test_confirm_transitions_second_look_failure_drops_positive(pm):
    prev = {"package": False, "mail": False}
    det = {"package": True, "mail": False, "count": 1, "description": "box"}
    out = pm._confirm_transitions(prev, det, None)   # re-capture failed
    assert out["package"] is False


def test_confirm_transitions_established_state_untouched(pm):
    # Already-known package: no confirmation needed, no announcement at stake.
    prev = {"package": True, "mail": False}
    det = {"package": True, "mail": False, "count": 1, "description": "box"}
    out = pm._confirm_transitions(prev, det, None)
    assert out["package"] is True


def test_confirm_transitions_pickup_single_frame(pm):
    # Negative transition (pickup) intentionally needs no second frame.
    prev = {"package": True, "mail": False}
    det = {"package": False, "mail": False, "count": 0, "description": ""}
    out = pm._confirm_transitions(prev, det, None)
    assert out["package"] is False


async def test_periodic_announces_once_on_confirmed_arrival(pm, load, fake_hass, monkeypatch):
    tts = load("tts_helper")
    spoken = []

    async def _announce(hass, msg, *a, **k):
        spoken.append(msg)
    monkeypatch.setattr(tts, "async_announce", _announce)

    pm._STATE.clear()
    pm._ANNOUNCE_CD.clear()
    fake_hass.states.set("camera.front_door_test", "idle")

    seq = [
        {"package": True, "mail": False, "count": 1, "description": "box"},
        {"package": True, "mail": False, "count": 1, "description": "box"},
    ]

    async def _det(hass, client, eid):
        return seq.pop(0) if seq else {"package": True, "mail": False,
                                       "count": 1, "description": "box"}
    monkeypatch.setattr(pm, "detect_on_camera", _det)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)

    report = await pm.periodic_check(fake_hass, None, "Sir", "tts.x", ["media_player.y"],
                                     configured_camera="camera.front_door_test")
    assert report["checked"] == 1
    assert len(spoken) == 1 and "package has been delivered" in spoken[0]


async def test_periodic_hallucination_single_frame_stays_quiet(pm, load, fake_hass, monkeypatch):
    tts = load("tts_helper")
    spoken = []

    async def _announce(hass, msg, *a, **k):
        spoken.append(msg)
    monkeypatch.setattr(tts, "async_announce", _announce)

    pm._STATE.clear()
    fake_hass.states.set("camera.front_door_test", "idle")

    seq = [
        {"package": True, "mail": False, "count": 1, "description": "ghost box"},
        {"package": False, "mail": False, "count": 0, "description": "empty porch"},
    ]

    async def _det(hass, client, eid):
        return seq.pop(0) if seq else {"package": False, "mail": False,
                                       "count": 0, "description": ""}
    monkeypatch.setattr(pm, "detect_on_camera", _det)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)

    await pm.periodic_check(fake_hass, None, "Sir", "tts.x", ["media_player.y"],
                            configured_camera="camera.front_door_test")
    assert spoken == []                                     # nothing announced
    assert pm._STATE["camera.front_door_test"]["package"] is False  # no phantom state


# ── watched_cameras: only door/porch views, never wide panoramas ─────────────

def test_watched_cameras_excludes_wide_panoramas(pm, fake_hass, monkeypatch):
    # A wide yard/street/driveway view (or an indoor garage) is not a porch
    # package camera, even when its name contains "front" — a parked car or a
    # neighbour's mailbox there reads as a delivery and false-announces.
    import sys
    import types

    class _S:
        def __init__(self, e, fn=""):
            self.entity_id = e
            self.attributes = {"friendly_name": fn}

    cams = [
        _S("camera.front_door", "Front Door"),
        _S("camera.porch"),
        _S("camera.doorbell"),
        _S("camera.side_door"),
        _S("camera.front_yard", "Front Yard"),
        _S("camera.driveway"),
        _S("camera.backyard"),
        _S("camera.garage"),
        _S("camera.kitchen"),
    ]
    monkeypatch.setitem(sys.modules, "jc.camera",
                        types.SimpleNamespace(active_camera_states=lambda h: cams))
    out = pm.watched_cameras(fake_hass)
    assert "camera.front_door" in out and "camera.porch" in out
    assert "camera.doorbell" in out and "camera.side_door" in out
    for wide in ("camera.front_yard", "camera.driveway", "camera.backyard",
                 "camera.garage", "camera.kitchen"):
        assert wide not in out, wide


def test_watched_cameras_configured_list_wins(pm, fake_hass):
    # An explicit configuration bypasses the name heuristic entirely.
    fake_hass.states.set("camera.front_yard", "idle")
    assert pm.watched_cameras(fake_hass, "camera.front_yard") == ["camera.front_yard"]


# ── announcement cooldown: an oscillating flag announces once ────────────────

async def test_flickering_detection_announces_once(pm, load, fake_hass, monkeypatch):
    tts = load("tts_helper")
    spoken = []

    async def _announce(hass, msg, *a, **k):
        spoken.append(msg)

    monkeypatch.setattr(tts, "async_announce", _announce)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_log", lambda *a, **k: None)
    pm._STATE.clear()
    pm._ANNOUNCE_CD.clear()

    eid = "camera.porch"
    pkg = {"package": True, "mail": False, "count": 1}
    empty = {"package": False, "mail": False, "count": 0}
    # Arrival announces; then the flag drops and rises again within the window —
    # the same package must not be announced a second time.
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", [], eid, pkg, "periodic")
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", [], eid, empty, "periodic")
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", [], eid, pkg, "periodic")
    assert len([m for m in spoken if "package has been delivered" in m]) == 1

    # A genuinely different camera is independent — it still announces.
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", [], "camera.front_door",
                      pkg, "periodic")
    assert len([m for m in spoken if "package has been delivered" in m]) == 2


async def test_cooldown_is_per_kind(pm, load, fake_hass, monkeypatch):
    # Package and mail are separate kinds: a package announcement must not
    # suppress a mail announcement on the same camera.
    tts = load("tts_helper")
    spoken = []

    async def _announce(hass, msg, *a, **k):
        spoken.append(msg)

    monkeypatch.setattr(tts, "async_announce", _announce)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    monkeypatch.setattr(pm, "_log", lambda *a, **k: None)
    pm._STATE.clear()
    pm._ANNOUNCE_CD.clear()

    eid = "camera.porch"
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", [], eid,
                      {"package": True, "mail": False, "count": 1}, "periodic")
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", [], eid,
                      {"package": True, "mail": True, "count": 1}, "periodic")
    assert any("package has been delivered" in m for m in spoken)
    assert any("mail has arrived" in m for m in spoken)
