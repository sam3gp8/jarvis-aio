"""Regression tests for the HeartbeatMonitor failover state machine.

Stdlib-only; loads standalone. Pins the 3-miss unavailability threshold, recovery,
and the route-to-adjacent / route-to-any / no-route failover ladder. The UDP probe
is injected so routing tests need no network.
"""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "jarvis_assistant" / "jarvis_component"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hb = _load_standalone("jarvis_heartbeat", "diagnostics/heartbeat.py")

NODES = {
    "office": {"ip": "10.0.0.1", "speaker": "media_player.office",
               "adjacent": ["hallway", "living_room"]},
    "hallway": {"ip": "10.0.0.2", "speaker": "media_player.hallway",
                "adjacent": ["office"]},
    "living_room": {"ip": "10.0.0.3", "speaker": "media_player.living_room",
                    "adjacent": ["office"]},
    "basement": {"ip": "10.0.0.4", "speaker": "media_player.basement",
                 "adjacent": []},
}


def _down(monitor, node, times=3):
    for _ in range(times):
        monitor.record_result(node, False)


def test_unavailable_after_three_misses():
    m = hb.HeartbeatMonitor(NODES)
    m.record_result("office", False)
    m.record_result("office", False)
    assert m.is_available("office") is True   # 2 misses — still up
    m.record_result("office", False)
    assert m.is_available("office") is False   # 3rd miss — down


def test_recovers_on_alive():
    m = hb.HeartbeatMonitor(NODES)
    _down(m, "office")
    assert m.is_available("office") is False
    m.record_result("office", True)
    assert m.is_available("office") is True


def test_route_self_when_up():
    m = hb.HeartbeatMonitor(NODES)
    assert m.route_for("office") == "office"


def test_route_to_first_available_adjacent():
    m = hb.HeartbeatMonitor(NODES)
    _down(m, "office")
    assert m.route_for("office") == "hallway"


def test_route_skips_down_adjacent():
    m = hb.HeartbeatMonitor(NODES)
    _down(m, "office")
    _down(m, "hallway")
    assert m.route_for("office") == "living_room"


def test_route_falls_back_to_any_when_adjacents_down():
    m = hb.HeartbeatMonitor(NODES)
    _down(m, "office")
    _down(m, "hallway")
    _down(m, "living_room")
    assert m.route_for("office") == "basement"  # only non-adjacent node still up


def test_route_none_when_all_down():
    m = hb.HeartbeatMonitor(NODES)
    for node in NODES:
        _down(m, node)
    assert m.route_for("office") is None


def test_speaker_for_maps_failover_to_entity():
    m = hb.HeartbeatMonitor(NODES)
    _down(m, "office")
    assert m.speaker_for("office") == "media_player.hallway"


@pytest.mark.asyncio
async def test_run_once_with_injected_ping():
    async def fake_ping(ip):
        return ip != "10.0.0.1"  # office unreachable, others fine

    m = hb.HeartbeatMonitor(NODES, ping_fn=fake_ping, max_misses=1)
    await m.run_once()
    assert m.is_available("office") is False
    assert m.is_available("hallway") is True
    assert m.down_nodes() == ["office"]
