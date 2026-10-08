"""cognitive_core space/time shadow emission (roadmap Phase Q — shadow).

`_emit_space_time_shadow` builds the WorldModel space/time views and logs a
one-line summary, observe-only and kill-switched. It must never raise and must
drive nothing — these tests pin the log line, the kill-switch, and defensiveness.
"""
import logging

import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


def test_emit_logs_summary(cc, fake_hass, monkeypatch, caplog):
    # Fake the WorldModel the emitter constructs so no real store/HA is needed.
    class _G:
        areas = ("kitchen", "hall", "living")
        edges = (("hall", "kitchen"), ("hall", "living"))

        def is_empty(self):
            return False

    class _Frame:
        daypart = "evening"
        hour = 19
        is_known = True

    class _WM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return _G()

        def temporal_frame(self):
            return _Frame()

    monkeypatch.setattr(cc, "SPACE_TIME_SHADOW", True)
    # WorldModel is imported inside the function from .kernel.world_model; patch there.
    from jc.kernel import world_model as wm_mod
    monkeypatch.setattr(wm_mod, "WorldModel", _WM)

    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_shadow(fake_hass, {})
    assert any("space_time(shadow):" in r.message and "daypart=evening" in r.message
               for r in caplog.records)


def test_kill_switch_silences(cc, fake_hass, monkeypatch, caplog):
    monkeypatch.setattr(cc, "SPACE_TIME_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_shadow(fake_hass, {})
    assert not any("space_time(shadow)" in r.message for r in caplog.records)


def test_emit_is_defensive(cc, fake_hass, monkeypatch, caplog):
    from jc.kernel import world_model as wm_mod

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("world model down")

    monkeypatch.setattr(cc, "SPACE_TIME_SHADOW", True)
    monkeypatch.setattr(wm_mod, "WorldModel", _Boom)
    # must not raise
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_shadow(fake_hass, {})
    assert not any("space_time(shadow)" in r.message for r in caplog.records)


# ── Phase Q parity ─────────────────────────────────────────────────────────────
def _fake_spatial_graph(adjacency):
    """A kernel SpatialGraph over the given adjacency, for the parity fake."""
    from jc.kernel import space_time as ST
    return ST.SpatialGraph.from_adjacency(adjacency)


def test_parity_agreement_logged(cc, fake_hass, monkeypatch, caplog):
    from jc.kernel import world_model as wm_mod
    from jc import residence_graph as rg

    # kernel graph: kitchen — hall — living (slugs, as room_adjacency emits)
    g = _fake_spatial_graph({"kitchen": {"hall"}, "hall": {"kitchen", "living"},
                             "living": {"hall"}})

    class _WM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return g

    monkeypatch.setattr(cc, "SPACE_TIME_PARITY", True)
    monkeypatch.setattr(wm_mod, "WorldModel", _WM)
    # breach area_id -> slug identity, so legacy area_ids are already slugs here
    monkeypatch.setattr(rg, "_area_slug", lambda hass, aid: aid)

    # legacy hops (area_id -> depth) that MATCH the kernel BFS from "kitchen"
    legacy = {"kitchen": 0, "hall": 1, "living": 2}
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_parity(fake_hass, {}, "kitchen", legacy)
    assert any("space_time(parity):" in r.message and "agree=True" in r.message
               for r in caplog.records)


def test_parity_divergence_logged(cc, fake_hass, monkeypatch, caplog):
    from jc.kernel import world_model as wm_mod
    from jc import residence_graph as rg

    g = _fake_spatial_graph({"kitchen": {"hall"}, "hall": {"kitchen", "living"},
                             "living": {"hall"}})

    class _WM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return g

    monkeypatch.setattr(cc, "SPACE_TIME_PARITY", True)
    monkeypatch.setattr(wm_mod, "WorldModel", _WM)
    monkeypatch.setattr(rg, "_area_slug", lambda hass, aid: aid)

    # legacy disagrees (living at depth 5)
    legacy = {"kitchen": 0, "hall": 1, "living": 5}
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_parity(fake_hass, {}, "kitchen", legacy)
    assert any("space_time(parity):" in r.message and "agree=False" in r.message
               for r in caplog.records)


def test_parity_kill_switch_and_no_breach(cc, fake_hass, monkeypatch, caplog):
    monkeypatch.setattr(cc, "SPACE_TIME_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_parity(fake_hass, {}, "kitchen", {"kitchen": 0})
    # switch off → silent
    assert not any("space_time(parity)" in r.message for r in caplog.records)
    # and with the switch on but no breach area → still silent
    monkeypatch.setattr(cc, "SPACE_TIME_PARITY", True)
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_parity(fake_hass, {}, None, {})
    assert not any("space_time(parity)" in r.message for r in caplog.records)


# ── Phase Q camera↔sensor coverage parity ──────────────────────────────────────
def test_coverage_parity_combines_cam_and_sensors(cc, fake_hass, monkeypatch, caplog):
    from jc.kernel import world_model as wm_mod
    from jc.kernel import space_time as ST
    from jc import camera_coverage as cov
    from jc import audio_routing as ar

    g = ST.SpatialGraph.from_adjacency(
        {"kitchen": ["hall"], "hall": ["kitchen", "living_room"],
         "living_room": ["hall"]})

    class _WM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return g

    # cameras cover kitchen + living room; hall has none
    def _cam(hass, name):
        return "camera.x" if name in ("kitchen", "living room") else None

    monkeypatch.setattr(cc, "SPACE_TIME_COVERAGE_PARITY", True)
    monkeypatch.setattr(wm_mod, "WorldModel", _WM)
    monkeypatch.setattr(cov, "camera_for_area", _cam)
    monkeypatch.setattr(ar, "currently_occupied_areas", lambda h: ["kitchen"])

    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_coverage_parity(fake_hass, {})
    msgs = [r.message for r in caplog.records if "space_time(parity):" in r.message]
    assert msgs, "expected a coverage parity log line"
    m = msgs[0]
    assert "model=3 area(s), cam-covered=2" in m
    assert "occupied-now=1 (in-model=1, cam-corroborated=1)" in m


def test_coverage_parity_kill_switch_and_empty(cc, fake_hass, monkeypatch, caplog):
    monkeypatch.setattr(cc, "SPACE_TIME_COVERAGE_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_coverage_parity(fake_hass, {})
    assert not any("space_time(parity)" in r.message for r in caplog.records)

    # switch on but an empty model → silent (nothing to corroborate)
    from jc.kernel import world_model as wm_mod
    from jc.kernel import space_time as ST

    class _EmptyWM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return ST.SpatialGraph()

    monkeypatch.setattr(cc, "SPACE_TIME_COVERAGE_PARITY", True)
    monkeypatch.setattr(wm_mod, "WorldModel", _EmptyWM)
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_coverage_parity(fake_hass, {})
    assert not any("space_time(parity)" in r.message for r in caplog.records)
