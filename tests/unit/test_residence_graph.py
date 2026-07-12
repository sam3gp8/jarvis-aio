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
