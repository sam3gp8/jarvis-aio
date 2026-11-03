"""Restart persistence for the Faces panel (issue #331).

The recognition cache and the pinned-snapshot index used to live only in memory,
so a Home Assistant / JARVIS restart emptied the Faces tab. These tests pin that
the cache + snapshot index round-trip through disk, that stale entries age out on
restore, and — critically — that a recognition restored from a previous run never
stands intrusion monitoring down (`resident_present`); only a fresh sighting can.
"""
from datetime import datetime, timedelta, timezone

import pytest


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def recog(load, tmp_path, monkeypatch):
    """recognition module with isolated, empty caches, roster, and persistence."""
    fr = load("face_roster")
    monkeypatch.setattr(fr, "ROSTER_PATH", str(tmp_path / "face_roster.json"))
    fr._loaded = False
    fr._roster = {}

    mod = load("recognition")
    monkeypatch.setattr(mod, "RECOG_CACHE_PATH", str(tmp_path / "recognition_cache.json"))
    monkeypatch.setattr(mod, "FACE_REF_DIR", str(tmp_path / "faces_ref"))

    def _reset():
        mod._RECOGNITION_CACHE.clear()
        mod._FACE_SNAPSHOTS.clear()
        mod._LLM_GUESS_CACHE.clear()
        mod._UNKNOWN_FACE_CACHE.clear()
        mod._persist_loaded = False
        mod._persist_last_save = 0.0

    _reset()
    yield mod, fr, tmp_path, _reset
    _reset()


def _cache(mod, cam, name, confidence, age_seconds, restored=False):
    rec = {
        "name": name,
        "confidence": confidence,
        "ts": _now() - timedelta(seconds=age_seconds),
        "unknown_count": 0,
    }
    if restored:
        rec["restored"] = True
    mod._RECOGNITION_CACHE[cam] = rec


# ── save → restore round-trip ───────────────────────────────────────────────────
def test_recognition_cache_round_trips(recog):
    mod, _fr, _tmp, _reset = recog
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=30)
    mod._persist_save(force=True)

    _reset()                      # simulate a restart: memory gone, disk remains
    mod._persist_load()

    rec = mod._RECOGNITION_CACHE.get("camera.front_door")
    assert rec is not None
    assert rec["name"] == "Sam"
    assert rec["confidence"] == 92.0
    assert rec["restored"] is True   # restored entries are tagged


def test_snapshot_index_restored_only_if_frame_exists(recog):
    mod, _fr, tmp_path, _reset = recog
    live = tmp_path / "faces" / "sam.jpg"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"\xff\xd8\xff")      # a frame that still exists on disk
    mod._FACE_SNAPSHOTS["sam"] = {
        "url": "/local/jarvis/faces/sam.jpg", "path": str(live),
        "ts": 1.0, "camera_entity": "camera.front_door"}
    mod._FACE_SNAPSHOTS["ghost"] = {
        "url": "/local/jarvis/faces/ghost.jpg",
        "path": str(tmp_path / "faces" / "ghost.jpg"),   # no such file
        "ts": 1.0, "camera_entity": "camera.driveway"}
    mod._persist_save(force=True)

    _reset()
    mod._persist_load()

    assert "sam" in mod._FACE_SNAPSHOTS          # frame present → restored
    assert "ghost" not in mod._FACE_SNAPSHOTS    # frame gone → dropped


def test_stale_entries_age_out_on_restore(recog):
    mod, _fr, _tmp, _reset = recog
    _cache(mod, "camera.fresh", "Sam", 90.0, age_seconds=60)                 # recent
    _cache(mod, "camera.stale", "Max", 90.0, age_seconds=3 * 60 * 60)        # 3h > 2h
    mod._persist_save(force=True)

    _reset()
    mod._persist_load()

    assert "camera.fresh" in mod._RECOGNITION_CACHE
    assert "camera.stale" not in mod._RECOGNITION_CACHE   # older than CACHE_MAX_AGE


def test_load_is_idempotent(recog):
    mod, _fr, _tmp, _reset = recog
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=30)
    mod._persist_save(force=True)
    _reset()
    mod._persist_load()
    mod._persist_load()                      # second call is a guarded no-op
    assert len(mod._RECOGNITION_CACHE) == 1


# ── SAFETY: restored sightings never stand intrusion down ───────────────────────
def test_restored_recognition_never_stands_intrusion_down(recog, fake_hass):
    mod, fr, _tmp, _reset = recog
    fr.add_resident("Sam")
    # A fresh, recent recognition of a resident → stands monitoring down.
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=10)
    assert mod.resident_present(fake_hass) == "Sam"

    # The same recognition restored from a previous run → must NOT stand down.
    _reset()
    _cache(mod, "camera.front_door", "Sam", 92.0, age_seconds=10, restored=True)
    assert mod.resident_present(fake_hass) is None

    # A fresh sighting overwrites the restored entry → normal behaviour resumes.
    mod.remember_recognition("front_door", "Sam", 92.0)
    assert mod.resident_present(fake_hass) == "Sam"


# ── resident_standdown_reason: WHY an intrusion alert didn't stand down ──────
# Diagnostic mirror of resident_present(); explains a false "unidentified person"
# alert so the cause shows in the LOGS tab instead of being guessed at.

def test_standdown_reason_no_fresh_recognition(recog, fake_hass):
    mod, _fr, _tmp, _reset = recog
    assert "no fresh face recognition" in mod.resident_standdown_reason(fake_hass)


def test_standdown_reason_below_confidence_threshold(recog, fake_hass):
    mod, fr, _tmp, _reset = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.kitchen", "Sam", 52.0, age_seconds=10)   # < 60 threshold
    r = mod.resident_standdown_reason(fake_hass)
    assert "Sam" in r and "52%" in r and "below" in r
    assert mod.resident_present(fake_hass) is None               # really no stand-down


def test_standdown_reason_not_flagged_resident(recog, fake_hass):
    mod, _fr, _tmp, _reset = recog
    _cache(mod, "camera.kitchen", "Guest", 88.0, age_seconds=10)  # confident, not a resident
    r = mod.resident_standdown_reason(fake_hass)
    assert "Guest" in r and "not flagged as a resident" in r
    assert mod.resident_present(fake_hass) is None


def test_standdown_reason_only_unknown_faces(recog, fake_hass):
    mod, _fr, _tmp, _reset = recog
    _cache(mod, "camera.kitchen", "unknown", 70.0, age_seconds=10)
    assert "none identified" in mod.resident_standdown_reason(fake_hass)


def test_standdown_reason_qualifying_resident(recog, fake_hass):
    mod, fr, _tmp, _reset = recog
    fr.add_resident("Sam")
    _cache(mod, "camera.kitchen", "Sam", 92.0, age_seconds=10)
    assert "qualifies" in mod.resident_standdown_reason(fake_hass)
    assert mod.resident_present(fake_hass) == "Sam"              # and it really would


def test_remember_recognition_persists(recog):
    mod, _fr, _tmp, _reset = recog
    mod.remember_recognition("front_door", "Sam", 92.0)
    _reset()
    mod._persist_load()
    assert mod._RECOGNITION_CACHE.get("camera.front_door", {}).get("name") == "Sam"


# ── loop safety: the live MQTT path must not block the event loop on disk I/O ───
def test_remember_recognition_offloads_write_when_hass_given(recog):
    """HA flagged a blocking ``open`` on the event loop from the MQTT face path
    (``remember_recognition`` → persist, seen in a user log). With ``hass`` the
    disk write must be handed to the executor, not run inline on the loop — while
    the data still round-trips."""
    mod, _fr, _tmp, _reset = recog

    ran = []

    class _LoopHass:
        # Mirrors real HA: submits the job to the executor and returns immediately.
        # Here we record the hand-off and run it inline so the round-trip holds.
        def async_add_executor_job(self, func, *args):
            ran.append(func.__name__)
            func(*args)

    mod.remember_recognition("front_door", "Sam", 92.0, hass=_LoopHass())
    assert ran == ["_persist_write"]   # offloaded, never an inline open() on the loop

    _reset()
    mod._persist_load()
    assert mod._RECOGNITION_CACHE.get("camera.front_door", {}).get("name") == "Sam"


def test_persist_without_hass_writes_inline(recog):
    """Sync / test callers with no ``hass`` fall back to an inline write (the tests
    above rely on this, and there is no loop to offload onto)."""
    mod, _fr, _tmp, _reset = recog
    mod._RECOGNITION_CACHE["camera.front_door"] = {
        "name": "Sam", "confidence": 90.0, "ts": _now(), "unknown_count": 0}
    mod._persist(force=True)          # hass=None → inline
    _reset()
    mod._persist_load()
    assert mod._RECOGNITION_CACHE.get("camera.front_door", {}).get("name") == "Sam"
