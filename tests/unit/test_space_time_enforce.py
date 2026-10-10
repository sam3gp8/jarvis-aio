"""cognitive_core space/time enforce (roadmap Phase Q — enforce, default OFF).

`_space_time_breach_hops` lets the kernel SpatialGraph own the intrusion
investigation's breach-depth map AUTHORITATIVELY — but only when the kernel
reproduces the incumbent residence_graph.hops_from_breach map exactly at
room-slug level, keyed back to area-ids. Any divergence / empty graph /
unmappable key / error keeps the legacy map (fail-safe). Default OFF is
behaviour-preserving; the switch is the module flag OR the `space_time_enforce`
config key. Safety path — adoption is behaviour-identical by construction.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


def _graph():
    # kitchen — hall — living_room (slugs, as room_adjacency emits)
    from jc.kernel import space_time as ST
    return ST.SpatialGraph.from_adjacency(
        {"kitchen": ["hall"], "hall": ["kitchen", "living_room"],
         "living_room": ["hall"]})


def _wire(cc, monkeypatch, *, graph=None, slug=lambda hass, aid: aid):
    """Point the enforce helper at a kernel graph and a slug resolver."""
    from jc.kernel import world_model as wm_mod
    from jc import residence_graph as rg
    g = _graph() if graph is None else graph

    class _WM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return g

    monkeypatch.setattr(wm_mod, "WorldModel", _WM)
    monkeypatch.setattr(rg, "_area_slug", slug)


# ── the switch ────────────────────────────────────────────────────────────────
def test_enforce_off_by_default(cc, cfg):
    assert cc._space_time_enforce_on() is False


def test_enforce_on_via_config(cc, cfg):
    cfg["space_time_enforce"] = True
    assert cc._space_time_enforce_on() is True


def test_enforce_on_via_flag(cc, monkeypatch):
    monkeypatch.setattr(cc, "SPACE_TIME_ENFORCE", True)
    assert cc._space_time_enforce_on() is True


# ── the breach-depth adoption ───────────────────────────────────────────────
def test_off_returns_legacy_unchanged(cc, cfg, fake_hass, monkeypatch):
    _wire(cc, monkeypatch)
    legacy = {"kitchen": 0, "hall": 1, "living_room": 2}
    out = cc._space_time_breach_hops(fake_hass, {}, "kitchen", legacy)
    assert out is legacy  # untouched


def test_on_adopts_kernel_map_on_agreement(cc, cfg, fake_hass, monkeypatch):
    cfg["space_time_enforce"] = True
    _wire(cc, monkeypatch)
    legacy = {"kitchen": 0, "hall": 1, "living_room": 2}
    out = cc._space_time_breach_hops(fake_hass, {}, "kitchen", legacy)
    # kernel-derived, but behaviour-identical (same area-id keys, same depths)
    assert out == legacy


def test_on_keys_kernel_map_back_to_area_ids(cc, cfg, fake_hass, monkeypatch):
    cfg["space_time_enforce"] = True
    # area_ids differ from slugs: strip an "area_" prefix to get the slug.
    _wire(cc, monkeypatch,
          slug=lambda hass, aid: aid.replace("area_", ""))
    legacy = {"area_kitchen": 0, "area_hall": 1, "area_living_room": 2}
    out = cc._space_time_breach_hops(fake_hass, {}, "area_kitchen", legacy)
    # the returned map is keyed by the original area-ids, not slugs
    assert out == {"area_kitchen": 0, "area_hall": 1, "area_living_room": 2}
    assert set(out) == set(legacy)


def test_on_divergence_keeps_legacy(cc, cfg, fake_hass, monkeypatch):
    cfg["space_time_enforce"] = True
    _wire(cc, monkeypatch)
    # legacy disagrees with the kernel BFS (living_room at the wrong depth)
    legacy = {"kitchen": 0, "hall": 1, "living_room": 9}
    out = cc._space_time_breach_hops(fake_hass, {}, "kitchen", legacy)
    assert out is legacy  # fail-safe: incumbent stays authoritative


def test_on_empty_graph_keeps_legacy(cc, cfg, fake_hass, monkeypatch):
    cfg["space_time_enforce"] = True
    from jc.kernel import space_time as ST
    _wire(cc, monkeypatch, graph=ST.SpatialGraph())
    legacy = {"kitchen": 0}
    out = cc._space_time_breach_hops(fake_hass, {}, "kitchen", legacy)
    assert out is legacy


def test_on_no_breach_area_keeps_legacy(cc, cfg, fake_hass, monkeypatch):
    cfg["space_time_enforce"] = True
    _wire(cc, monkeypatch)
    legacy = {"kitchen": 0}
    assert cc._space_time_breach_hops(fake_hass, {}, None, legacy) is legacy


def test_on_worldmodel_fault_keeps_legacy(cc, cfg, fake_hass, monkeypatch):
    cfg["space_time_enforce"] = True
    from jc.kernel import world_model as wm_mod

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("world model down")

    monkeypatch.setattr(wm_mod, "WorldModel", _Boom)
    legacy = {"kitchen": 0, "hall": 1}
    out = cc._space_time_breach_hops(fake_hass, {}, "kitchen", legacy)
    assert out is legacy
