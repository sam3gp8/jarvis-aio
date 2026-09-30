"""Tests for vision/scene_memory.py — persistent scene/object recall across time."""
import pytest


@pytest.fixture
def sm(load):
    # scene_memory lives under the vision subpackage; load it directly by path.
    import importlib.util, sys, pathlib
    comp = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
    key = "jc_scene_memory"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, comp / "vision" / "scene_memory.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(sm, tmp_path, monkeypatch):
    p = str(tmp_path / "scene.db")
    monkeypatch.setattr(sm, "DB_PATH", p)
    return p


def test_record_and_where_last_seen(sm, db):
    sm.record_scene("camera.garage", "a red bicycle and some keys on the workbench", ts=100)
    sm.record_scene("camera.garage", "an empty workbench", ts=200)
    hit = sm.where_last_seen("keys")
    assert hit and hit["camera"] == "camera.garage" and hit["ts"] == 100
    assert "keys" in hit["description"]
    assert sm.where_last_seen("nonexistent") is None


def test_where_last_seen_prefers_most_recent(sm, db):
    sm.record_scene("camera.porch", "a package by the door", ts=100)
    sm.record_scene("camera.driveway", "a package on the driveway", ts=300)
    hit = sm.where_last_seen("package")
    assert hit["camera"] == "camera.driveway" and hit["ts"] == 300


def test_inventory_returns_latest(sm, db):
    sm.record_scene("camera.porch", "chair and a plant", ts=100)
    sm.record_scene("camera.porch", "chair, plant and a delivery box", ts=200)
    inv = sm.inventory("camera.porch")
    assert inv["ts"] == 200 and "box" in inv["objects"]


def test_changed_since_diffs_objects(sm, db):
    sm.record_scene("camera.yard", "a bicycle and a hose", ts=1000)
    sm.record_scene("camera.yard", "a hose and a ladder", ts=2000)
    ch = sm.changed_since("camera.yard", since=1500)
    assert "ladder" in ch["added"]
    assert "bicycle" in ch["removed"]
    assert ch["latest_ts"] == 2000 and ch["baseline_ts"] == 1000


def test_changed_since_no_baseline(sm, db):
    sm.record_scene("camera.yard", "a bicycle", ts=2000)
    ch = sm.changed_since("camera.yard", since=1000)   # nothing at/before 1000
    assert ch["added"] == [] and ch["removed"] == []


def test_history_bounded_per_camera(sm, db, monkeypatch):
    monkeypatch.setattr(sm, "MAX_ROWS_PER_CAMERA", 3)
    for i in range(6):
        sm.record_scene("camera.x", f"scene number {i}", ts=float(i))
    rows = sm.recent("camera.x", limit=50)
    assert len(rows) == 3
    assert rows[0]["description"] == "scene number 5"   # newest kept


def test_blank_inputs_are_ignored(sm, db):
    assert sm.record_scene("", "something") is None
    assert sm.record_scene("camera.x", "") is None
    assert sm.where_last_seen("") is None
