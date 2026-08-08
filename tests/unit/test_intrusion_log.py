"""Tests for the intrusion event log, labeling, and learned damping (v6.76.0).

The safety-critical property: learning may only silence LOW-CONFIDENCE alerts,
and a single 'real' label must cancel damping outright — JARVIS must never learn
its way into ignoring a place where a genuine intrusion happened."""
import pathlib
import time

import pytest


@pytest.fixture
def intr(load, tmp_path, monkeypatch):
    m = load("intrusion")
    monkeypatch.setattr(m, "SNAPSHOT_DIR", str(tmp_path / "snaps"))
    monkeypatch.setattr(m, "LOG_PATH", pathlib.Path(tmp_path / "intrusion_log.json"))
    m._log = []
    m._log_loaded = True          # skip disk load; we control state
    m._called_off_until = 0.0
    m._false_alarms = []
    return m


# ── event log ────────────────────────────────────────────────────────────────

def test_record_event_appends(intr):
    ev = intr.record_event("investigating", reason="motion", breach="kitchen window",
                           breach_area="kitchen")
    assert ev["kind"] == "investigating"
    assert ev["breach"] == "kitchen window"
    assert ev["label"] is None
    assert len(intr.get_log()) == 1


def test_get_log_newest_first(intr):
    intr.record_event("investigating", reason="first")
    intr.record_event("confirmed", reason="second")
    log = intr.get_log()
    assert log[0]["reason"] == "second"


def test_log_persists_to_disk(intr):
    intr.record_event("confirmed", reason="real one")
    assert intr.LOG_PATH.exists()
    import json
    data = json.loads(intr.LOG_PATH.read_text())
    assert data[0]["reason"] == "real one"


def test_log_capped(intr):
    for i in range(intr._MAX_LOG + 25):
        intr.record_event("investigating", reason=str(i))
    assert len(intr._log) <= intr._MAX_LOG


def test_event_carries_snapshot(intr):
    snap = {"url": "/local/x.jpg", "path": "/config/www/x.jpg", "camera": "camera.kitchen"}
    ev = intr.record_event("confirmed", snapshot=snap)
    assert ev["snapshot_url"] == "/local/x.jpg"
    assert ev["camera"] == "camera.kitchen"


# ── labeling ─────────────────────────────────────────────────────────────────

def test_label_event_marks(intr):
    ev = intr.record_event("investigating", breach_area="kitchen")
    res = intr.label_event(ev["id"], "false")
    assert res["ok"] is True
    assert intr.get_log()[0]["label"] == "false"


def test_label_rejects_bad_value(intr):
    ev = intr.record_event("investigating")
    assert intr.label_event(ev["id"], "maybe")["ok"] is False


def test_label_unknown_event(intr):
    assert intr.label_event("nope", "false")["ok"] is False


def test_label_can_be_cleared(intr):
    ev = intr.record_event("investigating")
    intr.label_event(ev["id"], "false")
    intr.label_event(ev["id"], "")
    assert intr.get_log()[0]["label"] is None


# ── learning: damping the weak path ──────────────────────────────────────────

def _n_false(intr, n, area="kitchen"):
    for _ in range(n):
        ev = intr.record_event("investigating", breach_area=area)
        intr.label_event(ev["id"], "false")


def test_damp_only_after_threshold(intr):
    _n_false(intr, intr._LEARN_MIN_FALSE - 1)
    assert intr.should_damp_weak_alert("kitchen", None) is False
    _n_false(intr, 1)
    assert intr.should_damp_weak_alert("kitchen", None) is True


def test_single_real_label_cancels_damping(intr):
    # SAFETY: even with many false labels, one confirmed-real event means this
    # pattern must never be damped again.
    _n_false(intr, intr._LEARN_MIN_FALSE + 3)
    assert intr.should_damp_weak_alert("kitchen", None) is True
    ev = intr.record_event("confirmed", breach_area="kitchen")
    intr.label_event(ev["id"], "real")
    assert intr.should_damp_weak_alert("kitchen", None) is False, \
        "a real intrusion must cancel learned damping for that pattern"


def test_damping_is_pattern_scoped(intr):
    _n_false(intr, intr._LEARN_MIN_FALSE + 1, area="kitchen")
    # a different room is unaffected
    assert intr.should_damp_weak_alert("basement", None) is False


def test_old_labels_expire(intr, monkeypatch):
    _n_false(intr, intr._LEARN_MIN_FALSE + 1)
    assert intr.should_damp_weak_alert("kitchen", None) is True
    future = time.time() + intr._LEARN_WINDOW + 100
    monkeypatch.setattr(intr.time, "time", lambda: future)
    assert intr.should_damp_weak_alert("kitchen", None) is False


def test_unlabeled_events_do_not_damp(intr):
    for _ in range(10):
        intr.record_event("investigating", breach_area="kitchen")
    assert intr.should_damp_weak_alert("kitchen", None) is False


def test_learning_summary_shape(intr):
    _n_false(intr, intr._LEARN_MIN_FALSE)
    s = intr.learning_summary()
    assert s["labeled"] == intr._LEARN_MIN_FALSE
    assert s["damped_patterns"]


def test_should_damp_never_raises(intr):
    assert intr.should_damp_weak_alert(None, None) in (True, False)
