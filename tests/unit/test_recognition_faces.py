"""Tests for the Household Faces data layer (#140): recognition.recent_faces,
which feeds the panel, and recognition.resident_present, which stands intrusion
monitoring down when a known resident is the face on camera.

Both read the in-memory recognition cache (populated by the MQTT/DoubleTake path)
and the household roster. The cache stores naive-UTC timestamps, so the tests
drive it directly with controlled ages and isolate module-level state."""
from datetime import datetime, timedelta, timezone

import pytest


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def recog(load, tmp_path, monkeypatch):
    """recognition module with an empty cache and an isolated, empty roster."""
    fr = load("face_roster")
    monkeypatch.setattr(fr, "ROSTER_PATH", str(tmp_path / "face_roster.json"))
    fr._loaded = False
    fr._roster = {}

    mod = load("recognition")
    monkeypatch.setattr(mod, "FACE_REF_DIR", str(tmp_path / "faces_ref"))
    mod._RECOGNITION_CACHE.clear()
    mod._FACE_SNAPSHOTS.clear()
    mod._LLM_GUESS_CACHE.clear()
    yield mod, fr
    mod._RECOGNITION_CACHE.clear()
    mod._FACE_SNAPSHOTS.clear()
    mod._LLM_GUESS_CACHE.clear()


def _cache(mod, camera_entity, name, confidence, age_seconds):
    mod._RECOGNITION_CACHE[camera_entity] = {
        "name": name,
        "confidence": confidence,
        "ts": _now() - timedelta(seconds=age_seconds),
        "unknown_count": 0,
    }


# ── recent_faces ──────────────────────────────────────────────────────────────

def test_recent_faces_marks_resident_and_unknown(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=10)
    _cache(mod, "camera.driveway", "Unknown", 0.0, age_seconds=30)

    rows = mod.recent_faces(fake_hass)
    by_name = {r["name"]: r for r in rows}

    assert by_name["Sam"]["is_resident"] is True
    assert by_name["Sam"]["is_unknown"] is False
    assert by_name["Unknown"]["is_unknown"] is True
    assert by_name["Unknown"]["is_resident"] is False


def test_recent_faces_non_resident_known_face_not_flagged(recog, fake_hass):
    mod, fr = recog
    # Roster has Sam; a recognized-but-unlisted person must not be a resident.
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Quentin", 88.0, age_seconds=5)

    rows = mod.recent_faces(fake_hass)
    assert rows[0]["name"] == "Quentin"
    assert rows[0]["is_resident"] is False
    assert rows[0]["is_unknown"] is False


def test_recent_faces_sorted_newest_first_and_limited(recog, fake_hass):
    mod, _ = recog
    _cache(mod, "camera.a", "Old", 70.0, age_seconds=600)
    _cache(mod, "camera.b", "Mid", 70.0, age_seconds=120)
    _cache(mod, "camera.c", "New", 70.0, age_seconds=5)

    rows = mod.recent_faces(fake_hass, limit=2)
    assert [r["name"] for r in rows] == ["New", "Mid"]
    assert rows[0]["age_seconds"] <= rows[1]["age_seconds"]


def test_recent_faces_drops_stale_cache_entries(recog, fake_hass):
    mod, _ = recog
    # Older than CACHE_MAX_AGE (2h) → excluded.
    _cache(mod, "camera.front_door", "Sam", 90.0,
           age_seconds=int(mod.CACHE_MAX_AGE.total_seconds()) + 60)
    assert mod.recent_faces(fake_hass) == []


def test_recent_faces_empty_when_nothing_seen(recog, fake_hass):
    mod, _ = recog
    assert mod.recent_faces(fake_hass) == []


# ── resident_present ────────────────────────────────────────────────────────────

def test_resident_present_recent_and_confident(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Sam", 90.0, age_seconds=30)
    assert mod.resident_present(fake_hass) == "Sam"


def test_resident_present_ignores_stale(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Sam", 90.0, age_seconds=10_000)
    assert mod.resident_present(fake_hass) is None


def test_resident_present_ignores_low_confidence(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Sam",
           mod.CONFIDENCE_THRESHOLD - 1, age_seconds=10)
    assert mod.resident_present(fake_hass) is None


def test_resident_present_ignores_non_resident(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Quentin", 95.0, age_seconds=10)
    assert mod.resident_present(fake_hass) is None


def test_resident_present_none_when_whitelist_empty(recog, fake_hass):
    mod, _ = recog
    # No residents flagged → presence gate is a no-op even with a confident face.
    _cache(mod, "camera.front_door", "Sam", 95.0, age_seconds=10)
    assert mod.resident_present(fake_hass) is None


# ── Phase 2: pinned recognition-time snapshots (#140) ───────────────────────────

def test_should_capture_face_gate_and_throttle(recog):
    mod, _ = recog
    # Unknowns and blanks are never captured.
    assert mod._should_capture_face("Unknown") is False
    assert mod._should_capture_face("") is False
    # A fresh, known name is eligible…
    assert mod._should_capture_face("Sam") is True
    # …but once pinned recently it's throttled.
    mod._FACE_SNAPSHOTS[mod._face_norm("Sam")] = {"url": "/local/x.jpg", "ts": __import__("time").time()}
    assert mod._should_capture_face("Sam") is False
    # An old pin is eligible again.
    mod._FACE_SNAPSHOTS[mod._face_norm("Sam")]["ts"] = 0
    assert mod._should_capture_face("Sam") is True


def test_recent_faces_includes_pinned_snapshot_url(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    mod._FACE_SNAPSHOTS[mod._face_norm("Sam")] = {
        "url": "/local/jarvis/faces/sam.jpg", "ts": _now().timestamp()}
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=10)
    _cache(mod, "camera.driveway", "Unknown", 0.0, age_seconds=30)

    rows = {r["name"]: r for r in mod.recent_faces(fake_hass)}
    assert rows["Sam"]["snapshot_url"] == "/local/jarvis/faces/sam.jpg"
    # Unknown faces never carry a pinned snapshot.
    assert rows["Unknown"]["snapshot_url"] is None


def test_recent_faces_snapshot_url_none_without_pin(recog, fake_hass):
    mod, _ = recog
    _cache(mod, "camera.front_door", "Quentin", 88.0, age_seconds=5)
    assert mod.recent_faces(fake_hass)[0]["snapshot_url"] is None


def _install_fake_camera(monkeypatch, content=b"\xff\xd8\xff\xe0JPEGDATA"):
    """Guarantee a `homeassistant.components.camera` with a fake async_get_image,
    regardless of whether the real-HA integration tests have disturbed the
    module in sys.modules. setitem restores the prior state after the test."""
    import sys
    import types as _t

    async def _fake_image(hass, entity_id, timeout=10):
        return _t.SimpleNamespace(content=content)
    stub = _t.ModuleType("homeassistant.components.camera")
    stub.async_get_image = _fake_image
    monkeypatch.setitem(sys.modules, "homeassistant.components.camera", stub)


async def test_capture_face_snapshot_pins_and_caches(load, recog, fake_hass, tmp_path, monkeypatch):
    import os
    mod, _ = recog
    # Point the servable dir at a tmp path and feed a fake camera frame.
    paths = load("paths")
    monkeypatch.setattr(paths, "config_path_str", lambda *a, **k: str(tmp_path))
    _install_fake_camera(monkeypatch)

    rec = await mod.capture_face_snapshot(fake_hass, "front_door", "Sam")
    assert rec is not None
    assert rec["url"] == "/local/jarvis/faces/sam.jpg"
    assert mod.face_snapshot_url("sam") == "/local/jarvis/faces/sam.jpg"
    # The file was actually written to the servable dir.
    assert os.path.exists(os.path.join(str(tmp_path), "sam.jpg"))


async def test_capture_face_snapshot_skips_unknown(recog, fake_hass):
    mod, _ = recog
    assert await mod.capture_face_snapshot(fake_hass, "front_door", "Unknown") is None
    assert mod._FACE_SNAPSHOTS == {}


def test_face_norm_sanitizes_path_traversal(recog):
    # The name comes from an external MQTT backend; it must never yield a path
    # separator or escape the snapshot directory.
    mod, _ = recog
    key = mod._face_norm("../../config/secret")
    assert "/" not in key and ".." not in key
    assert mod._face_norm("Sam Smith") == "sam_smith"
    assert mod._face_norm("/// ") == ""   # nothing usable → empty, capture bails


async def test_capture_face_snapshot_contains_traversal(load, recog, fake_hass, tmp_path, monkeypatch):
    import os
    mod, _ = recog
    paths = load("paths")
    monkeypatch.setattr(paths, "config_path_str", lambda *a, **k: str(tmp_path))
    _install_fake_camera(monkeypatch, content=b"\xff\xd8\xff\xe0JPEG")

    rec = await mod.capture_face_snapshot(fake_hass, "front_door", "../../etc/passwd")
    # The write must land inside the snapshot dir, never above it.
    if rec is not None:
        assert os.path.realpath(rec["path"]).startswith(os.path.realpath(str(tmp_path)))
    assert not os.path.exists(os.path.join(str(tmp_path), "..", "etc"))


# ── Phase 3: best-effort LLM guesses stay out of the trusted path (#140) ────────

def test_resident_present_ignores_llm_guess(recog, fake_hass):
    # SAFETY: a best-effort LLM guess must NEVER stand intrusion monitoring down.
    mod, fr = recog
    fr.add_resident("Sam")
    # A recent, "confident" LLM guess for a flagged resident — but it lives in the
    # separate guess cache, which resident_present must not read.
    mod._LLM_GUESS_CACHE["camera.front_door"] = {
        "name": "Sam", "confidence": 99.0, "ts": _now()}
    assert mod.resident_present(fake_hass) is None
    # The trusted cache, in contrast, does stand down.
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=10)
    assert mod.resident_present(fake_hass) == "Sam"


def test_recent_faces_surfaces_llm_guess_flagged(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    mod._LLM_GUESS_CACHE["camera.front_door"] = {
        "name": "Sam", "confidence": 70.0, "ts": _now()}
    row = {r["name"]: r for r in mod.recent_faces(fake_hass)}["Sam"]
    assert row["source"] == "llm_guess"
    assert row["is_low_confidence"] is True


def test_recent_faces_trusted_recognition_outranks_llm_guess(recog, fake_hass):
    mod, fr = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.front_door", "Sam", 93.0, age_seconds=5)
    mod._LLM_GUESS_CACHE["camera.front_door"] = {
        "name": "Sam", "confidence": 99.0, "ts": _now()}
    rows = [r for r in mod.recent_faces(fake_hass)
            if r["name"] == "Sam" and r["camera_entity"] == "camera.front_door"]
    assert len(rows) == 1
    assert rows[0]["is_low_confidence"] is False   # the trusted sighting wins


def test_remember_llm_guess_keys_by_camera_entity(recog):
    mod, _ = recog
    mod.remember_llm_guess("front_door", "Sam", 80.0)
    assert "camera.front_door" in mod._LLM_GUESS_CACHE
    assert mod._LLM_GUESS_CACHE["camera.front_door"]["name"] == "Sam"


# ── Phase 3: resident reference-photo store ─────────────────────────────────────

def test_face_reference_store_roundtrip(recog):
    mod, _ = recog
    assert mod.has_face_reference("Sam") is False
    assert mod.set_face_reference("Sam", b"\xff\xd8\xff\xe0JPEG") is True
    assert mod.has_face_reference("Sam") is True
    assert "sam" in mod.list_face_references()
    assert mod.face_reference_path("SAM") is not None       # case-insensitive key
    assert mod.remove_face_reference("sam") is True
    assert mod.has_face_reference("Sam") is False


def test_face_reference_rejects_blank_and_empty(recog):
    mod, _ = recog
    assert mod.set_face_reference("", b"data") is False
    assert mod.set_face_reference("Sam", b"") is False


def test_face_reference_name_is_traversal_safe(recog):
    mod, _ = recog
    import os
    assert mod.set_face_reference("../../etc/passwd", b"\xff\xd8data") is True
    # The stored file must live inside FACE_REF_DIR, not above it.
    for n in mod.list_face_references():
        assert "/" not in n and ".." not in n
    assert not os.path.exists(os.path.join(mod.FACE_REF_DIR, "..", "etc"))
