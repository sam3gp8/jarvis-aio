"""Camera vision recognition hint — per-camera match + home-wide resident fallback.

Per-camera face recognition is incomplete by nature: a subject whose back is to
the kitchen camera still shows a face to the dining-room camera. Without a
home-wide fallback, a recognised resident whose face a given camera can't see
gets narrated as an "unidentified person" and, at the 'important' alert level,
announced as one — even though Frigate recognised them elsewhere seconds ago.
"""
import sys
import types

import pytest


@pytest.fixture
def cam(load, monkeypatch):
    # Stub the heavy third-party/sibling imports camera.py pulls at module load.
    monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    hc = types.ModuleType("homeassistant.components.camera")
    hc.async_get_image = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "homeassistant.components.camera", hc)
    net = types.ModuleType("homeassistant.helpers.network")
    net.get_url = lambda *a, **k: "http://localhost:8123"
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.network", net)
    for sib, fns in {
        "jc.database": {"save_message": lambda *a, **k: None},
        "jc.tts_helper": {"async_announce": lambda *a, **k: None},
        "jc.camera_backends": {"find_backend": lambda *a, **k: None},
    }.items():
        m = types.ModuleType(sib)
        for n, f in fns.items():
            setattr(m, n, f)
        monkeypatch.setitem(sys.modules, sib, m)
    sys.modules.pop("jc.camera", None)
    mod = load("camera")
    monkeypatch.setattr(mod, "_RECOGNITION_AVAILABLE", True, raising=False)
    yield mod
    sys.modules.pop("jc.camera", None)


def _set(cam, monkeypatch, *, last, resident):
    monkeypatch.setattr(cam, "last_seen_at", lambda hass, eid: last, raising=False)
    monkeypatch.setattr(cam, "resident_present", lambda hass: resident, raising=False)


def test_per_camera_match_names_the_person(cam, monkeypatch):
    _set(cam, monkeypatch,
         last={"is_confident": True, "name": "Sam", "confidence": 94, "age_seconds": 5},
         resident="Sam")
    hint = cam._recognition_hint(None, "camera.dining_room")
    assert "identified the person on camera as 'Sam'" in hint
    assert "94%" in hint


def test_home_wide_fallback_when_this_camera_has_no_match(cam, monkeypatch):
    # No fresh match on THIS camera, but Frigate recognised a resident elsewhere.
    _set(cam, monkeypatch, last=None, resident="Sam")
    hint = cam._recognition_hint(None, "camera.kitchen")
    assert "household resident 'Sam'" in hint
    assert "another camera" in hint
    assert "unidentified" in hint        # explicit instruction not to call them that


def test_stale_per_camera_match_falls_back_to_home_wide(cam, monkeypatch):
    # A match on this camera but too old (age >= 60) → not named directly; the
    # home-wide resident signal carries it instead.
    _set(cam, monkeypatch,
         last={"is_confident": True, "name": "Sam", "confidence": 94, "age_seconds": 300},
         resident="Sam")
    assert "household resident 'Sam'" in cam._recognition_hint(None, "camera.kitchen")


def test_low_confidence_per_camera_falls_back_to_home_wide(cam, monkeypatch):
    # below the 60% per-camera threshold → not named directly; home-wide used.
    _set(cam, monkeypatch,
         last={"is_confident": True, "name": "Sam", "confidence": 52, "age_seconds": 5},
         resident="Sam")
    assert "household resident 'Sam'" in cam._recognition_hint(None, "camera.kitchen")


def test_no_recognition_anywhere_gives_empty_hint(cam, monkeypatch):
    _set(cam, monkeypatch, last=None, resident=None)
    assert cam._recognition_hint(None, "camera.kitchen") == ""
