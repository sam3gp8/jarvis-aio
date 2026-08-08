"""Tests for residence graph — room adjacency from the floor plan (v6.35.0)."""
import json
import pytest


@pytest.fixture
def rg(load):
    return load("residence_graph")


def test_adjacency_of_touching_rooms(rg):
    config = {"floor_plan_rooms": {"1F": {"rooms": [
        {"name": "Kitchen", "x": 0, "y": 0, "w": 50, "h": 50},
        {"name": "Living Room", "x": 50, "y": 0, "w": 50, "h": 50},  # shares kitchen's edge
        {"name": "Attic", "x": 500, "y": 500, "w": 30, "h": 30},      # far away
    ]}}}
    adj = rg.room_adjacency(config)
    assert "living_room" in adj["kitchen"]
    assert "kitchen" in adj["living_room"]
    assert "attic" not in adj.get("kitchen", set())


def test_adjacency_empty_without_plan(rg):
    assert rg.room_adjacency({}) == {}
    assert rg.room_adjacency({"floor_plan_rooms": {}}) == {}


def test_adjacency_from_json_string(rg):
    config = {"floor_plan_rooms": json.dumps({"1F": {"rooms": [
        {"name": "A", "x": 0, "y": 0, "w": 10, "h": 10},
        {"name": "B", "x": 10, "y": 0, "w": 10, "h": 10},
    ]}})}
    assert "b" in rg.room_adjacency(config)["a"]


@pytest.mark.parametrize("b,expected", [
    (("B", 15, 0, 10, 10), True),    # 5-unit gap < threshold → adjacent
    (("C", 100, 0, 10, 10), False),  # far → not adjacent
])
def test_touch(rg, b, expected):
    assert rg._touch(("A", 0, 0, 10, 10), b) is expected


def test_slug(rg):
    assert rg.slug("Master Bedroom") == "master_bedroom"


def test_adjacent_areas_no_breach(rg, fake_hass):
    assert rg.adjacent_areas(fake_hass, {}, None) == set()


# ── hops_from_breach: inward-distance BFS (v6.74.0) ──────────────────────────

def test_hops_from_breach_empty_without_breach(rg):
    assert rg.hops_from_breach(None, {}, None) == {}


def test_room_adjacency_and_bfs_depth(rg, monkeypatch):
    # a simple linear house: kitchen — hall — living — bedroom
    adj = {"kitchen": {"hall"}, "hall": {"kitchen", "living"},
           "living": {"hall", "bedroom"}, "bedroom": {"living"}}
    monkeypatch.setattr(rg, "room_adjacency", lambda cfg: adj)
    monkeypatch.setattr(rg, "_area_slug", lambda h, a: a)

    # fake area registry: area_id == slug for simplicity
    class _Area:
        def __init__(self, name): self.id = name; self.name = name
    import sys, types
    ar = types.SimpleNamespace(
        async_get=lambda h: types.SimpleNamespace(
            async_list_areas=lambda: [_Area(n) for n in adj]))
    helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.area_registry", ar)
    monkeypatch.setattr(helpers, "area_registry", ar, raising=False)

    hops = rg.hops_from_breach(None, {}, "kitchen")
    assert hops.get("kitchen") == 0
    assert hops.get("hall") == 1
    assert hops.get("living") == 2
    assert hops.get("bedroom") == 3
