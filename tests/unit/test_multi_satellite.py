"""Multi-satellite continuity: the follow-up mic follows the person to another
room's satellite ONLY when the starting room is completely empty (opt-in).

The decision logic (_pick_reopen_target) is tested directly with audio_routing
monkeypatched, so we exercise the branch behavior without an area registry.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("continued_conversation")


def _wire(cc, monkeypatch, *, enabled, area_of, occupied_areas, sats_by_area,
          area_occupied):
    """Point continued_conversation at controlled config + routing."""
    pkg = cc.__name__.rsplit(".", 1)[0]
    import sys, types
    jc = types.SimpleNamespace(
        get=lambda k, d=None: enabled if k == "continued_conversation_multi_satellite" else d)
    ar = types.SimpleNamespace(
        entity_area=lambda hass, ent: area_of.get(ent),
        is_area_occupied=lambda hass, area: area_occupied.get(area, False),
        currently_occupied_areas=lambda hass: list(occupied_areas),
        satellites_in_area=lambda hass, area: list(sats_by_area.get(area, [])),
    )
    monkeypatch.setitem(sys.modules, f"{pkg}.jarvis_config", jc)
    monkeypatch.setitem(sys.modules, f"{pkg}.audio_routing", ar)


def test_disabled_always_returns_original(cc, monkeypatch):
    _wire(cc, monkeypatch, enabled=False,
          area_of={"assist_satellite.kitchen": "kitchen"},
          occupied_areas=["living_room"], sats_by_area={"living_room": ["assist_satellite.living"]},
          area_occupied={"kitchen": False})
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.kitchen"


def test_handoff_when_start_empty_and_other_room_occupied(cc, monkeypatch):
    _wire(cc, monkeypatch, enabled=True,
          area_of={"assist_satellite.kitchen": "kitchen"},
          occupied_areas=["living_room"],
          sats_by_area={"living_room": ["assist_satellite.living"]},
          area_occupied={"kitchen": False, "living_room": True})
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.living"


def test_no_handoff_while_start_room_still_occupied(cc, monkeypatch):
    # start room NOT empty → stay put even though another room is also occupied
    _wire(cc, monkeypatch, enabled=True,
          area_of={"assist_satellite.kitchen": "kitchen"},
          occupied_areas=["kitchen", "living_room"],
          sats_by_area={"living_room": ["assist_satellite.living"]},
          area_occupied={"kitchen": True, "living_room": True})
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.kitchen"


def test_no_handoff_when_no_other_satellite_room(cc, monkeypatch):
    # start room empty, but the occupied room has no satellite → stay put
    _wire(cc, monkeypatch, enabled=True,
          area_of={"assist_satellite.kitchen": "kitchen"},
          occupied_areas=["bathroom"],
          sats_by_area={},                       # bathroom has no satellite
          area_occupied={"kitchen": False, "bathroom": True})
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.kitchen"


def test_no_handoff_when_house_empty(cc, monkeypatch):
    # start room empty and nobody anywhere → stay put (reopening elsewhere is moot)
    _wire(cc, monkeypatch, enabled=True,
          area_of={"assist_satellite.kitchen": "kitchen"},
          occupied_areas=[],
          sats_by_area={"living_room": ["assist_satellite.living"]},
          area_occupied={"kitchen": False})
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.kitchen"


def test_start_area_unresolved_stays_put(cc, monkeypatch):
    _wire(cc, monkeypatch, enabled=True,
          area_of={},                            # no area for the satellite
          occupied_areas=["living_room"],
          sats_by_area={"living_room": ["assist_satellite.living"]},
          area_occupied={})
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.kitchen"


def test_skips_start_area_even_if_listed_occupied_elsewhere(cc, monkeypatch):
    # the start area must never be chosen as its own handoff target
    _wire(cc, monkeypatch, enabled=True,
          area_of={"assist_satellite.kitchen": "kitchen"},
          occupied_areas=["kitchen"],            # only the (empty-by-flag) start area is "occupied" in list
          sats_by_area={"kitchen": ["assist_satellite.kitchen"]},
          area_occupied={"kitchen": False})
    # is_area_occupied says kitchen empty; the loop skips kitchen → original
    assert cc._pick_reopen_target(None, "assist_satellite.kitchen") == "assist_satellite.kitchen"
