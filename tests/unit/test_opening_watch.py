"""#530 core — vision open/closed watch for sensor-less openings.

The pure helpers carry the logic (which openings qualify, how a vision reply maps
to open/closed, the throttle, which one to check next) and are tested without
Home Assistant. The async driver is tested through its seams: it ships OFF, and
when the owner enables it, it classifies one due opening per tick and logs a real
open↔closed change.
"""
import pytest


@pytest.fixture
def ow(load):
    return load("opening_watch")


@pytest.fixture(autouse=True)
def _clear_state(ow):
    ow.clear()
    yield
    ow.clear()


# ── sensorless_openings ─────────────────────────────────────────────────────
def test_sensorless_openings_selects_only_sensorless_doors_windows(ow):
    elems = {
        "1st": [
            {"id": "e1", "type": "door", "kind": "exterior", "wall": "front",
             "entity": "binary_sensor.front_door"},            # has sensor → excluded
            {"id": "e2", "type": "window", "room": "Shed"},    # sensor-less window → in
            {"id": "e3", "type": "door", "room": "L Living", "entity": ""},  # blank sensor → in
            {"id": "e4", "type": "wall", "room": "Kitchen"},   # not an opening → excluded
        ],
    }
    got = ow.sensorless_openings(elems)
    ids = {o["id"] for o in got}
    assert ids == {"e2", "e3"}
    by_id = {o["id"]: o for o in got}
    assert by_id["e2"]["type"] == "window"
    assert by_id["e2"]["room"] == "Shed"
    assert by_id["e2"]["label"] == "Shed window"
    assert by_id["e3"]["label"] == "L Living door"


def test_sensorless_openings_is_robust_to_junk(ow):
    assert ow.sensorless_openings(None) == []
    assert ow.sensorless_openings("nope") == []
    assert ow.sensorless_openings({"1st": "notalist"}) == []
    # Element with no id is skipped (can't key a state on it).
    assert ow.sensorless_openings({"1st": [{"type": "window", "room": "A"}]}) == []


# ── parse_open_closed ───────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expect", [
    ("OPEN", "open"),
    ("CLOSED", "closed"),
    ("The window is closed.", "closed"),
    ("It appears to be wide open.", "open"),
    ("The door is slightly ajar.", "open"),
    ("the door is not open", "closed"),
    ("it is not closed", "open"),
    ("the shutter is secured", "closed"),
    ("There is an opening in the wall of the closet.", "unknown"),  # word-boundary guard
    ("I can't tell from this angle.", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_parse_open_closed(ow, text, expect):
    assert ow.parse_open_closed(text) == expect


# ── due / pick_next ─────────────────────────────────────────────────────────
def test_due(ow):
    assert ow.due(0, 1000, 600) is True          # never checked
    assert ow.due(None, 1000, 600) is True
    assert ow.due(900, 1000, 600) is False        # 100s < 600s
    assert ow.due(400, 1000, 600) is True         # 600s elapsed
    assert ow.due("bad", 1000, 600) is True       # defensive → due


def test_pick_next_prefers_least_recently_checked(ow):
    openings = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    states = {"a": {"ts": 900}, "b": {"ts": 300}, "c": {"ts": 950}}
    # interval 600, now 1000 → a(100 ago) not due, b(700 ago) due, c(50 ago) not due
    pick = ow.pick_next(openings, states, now=1000, interval_s=600)
    assert pick["id"] == "b"
    # Nothing due → None
    assert ow.pick_next(openings, {"a": {"ts": 999}, "b": {"ts": 999}, "c": {"ts": 999}},
                        now=1000, interval_s=600) is None
    # Never-checked beats an old one
    pick2 = ow.pick_next(openings, {"a": {"ts": 1}, "b": {}, "c": {"ts": 1}},
                         now=10_000, interval_s=600)
    assert pick2["id"] == "b"


# ── _record / change detection ──────────────────────────────────────────────
def test_record_flags_only_real_state_changes(ow):
    op = {"id": "e2", "type": "window", "room": "Shed", "floor": "1st", "label": "Shed window"}
    assert ow._record(op, "open", "camera.shed", now=100) is False      # first observation
    assert ow._record(op, "open", "camera.shed", now=200) is False      # unchanged
    assert ow._record(op, "closed", "camera.shed", now=300) is True     # open → closed
    assert ow._record(op, "unknown", "camera.shed", now=400) is False   # unknown never a "change"
    st = ow.get_states()["e2"]
    assert st["state"] == "unknown" and st["camera"] == "camera.shed" and st["ts"] == 400


# ── public gate ─────────────────────────────────────────────────────────────
def test_is_enabled_default_off(ow):
    assert ow.is_enabled(object()) is False


def test_is_enabled_tracks_watch_on(ow, monkeypatch):
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    assert ow.is_enabled(object()) is True


# ── driver: ships OFF ───────────────────────────────────────────────────────
async def test_check_openings_disabled_by_default(ow):
    # No config → _watch_on False → the tick does nothing.
    res = await ow.check_openings(hass=object())
    assert res == {"enabled": False, "checked": 0}


# ── driver: enabled path classifies and logs a change ───────────────────────
async def test_check_openings_classifies_and_logs_change(ow, load, monkeypatch):
    cc = load("camera_coverage")
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    monkeypatch.setattr(ow, "_interval_s", lambda hass: 600.0)
    monkeypatch.setattr(ow, "_elements", lambda hass: {
        "1st": [{"id": "e2", "type": "window", "room": "Shed"}]})
    monkeypatch.setattr(cc, "camera_for_area", lambda hass, area: "camera.shed")

    verdicts = iter(["open", "closed"])

    async def _fake_classify(hass, op):
        return next(verdicts)

    monkeypatch.setattr(ow, "_classify_opening", _fake_classify)
    logged = []
    monkeypatch.setattr(ow, "_log_change", lambda op, state: logged.append((op["id"], state)))

    # Tick 1 at t=1000 → first observation "open", no change logged.
    r1 = await ow.check_openings(hass=object(), now=1000)
    assert r1["checked"] == 1
    assert ow.get_states()["e2"]["state"] == "open"
    assert logged == []

    # Tick 2 at t=2000 (past the 600s throttle) → "closed" is a real change → logged.
    r2 = await ow.check_openings(hass=object(), now=2000)
    assert r2["checked"] == 1
    assert ow.get_states()["e2"]["state"] == "closed"
    assert logged == [("e2", "closed")]


async def test_check_openings_throttles_fresh_openings(ow, load, monkeypatch):
    cc = load("camera_coverage")
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    monkeypatch.setattr(ow, "_interval_s", lambda hass: 600.0)
    monkeypatch.setattr(ow, "_elements", lambda hass: {
        "1st": [{"id": "e2", "type": "window", "room": "Shed"}]})
    monkeypatch.setattr(cc, "camera_for_area", lambda hass, area: "camera.shed")

    calls = {"n": 0}

    async def _fake_classify(hass, op):
        calls["n"] += 1
        return "open"

    monkeypatch.setattr(ow, "_classify_opening", _fake_classify)

    await ow.check_openings(hass=object(), now=1000)
    # Second tick 100s later — opening is fresh (< 600s) → nothing due, no classify.
    r = await ow.check_openings(hass=object(), now=1100)
    assert calls["n"] == 1
    assert r["checked"] == 0


async def test_check_openings_skips_when_no_camera_sightline(ow, load, monkeypatch):
    cc = load("camera_coverage")
    monkeypatch.setattr(ow, "_watch_on", lambda hass: True)
    monkeypatch.setattr(ow, "_elements", lambda hass: {
        "1st": [{"id": "e2", "type": "window", "room": "Shed"}]})
    monkeypatch.setattr(cc, "camera_for_area", lambda hass, area: None)

    async def _boom(hass, op):  # must never be called
        raise AssertionError("classify should not run without a camera")

    monkeypatch.setattr(ow, "_classify_opening", _boom)
    res = await ow.check_openings(hass=object(), now=1000)
    assert res == {"enabled": True, "checked": 0, "reason": "no camera sightline"}
