"""Sibling-burst coalescing in the observer's classify path.

An alarm panel toggling binary_sensor.home_cove_alarm_zone_49..63 at once used to
trigger one classifier call per zone — flooding the LLM and evicting the activity
log. _group_debounced collapses a burst of numbered siblings to one escalation per
window. These load just the pure helpers to avoid the observer's heavy imports.
"""
import ast
import time
from pathlib import Path

OBS = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "observer.py"
WANTED = {"_GROUP_LAST", "_SEQ_SUFFIX_RE", "_group_key", "_group_debounced"}


def _load():
    import re
    from typing import Optional
    src = OBS.read_text()
    ns = {"re": re, "time": time, "Optional": Optional}
    for node in ast.parse(src).body:
        name = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in WANTED:
                    name = t.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id in WANTED:
            name = node.target.id
        elif isinstance(node, ast.FunctionDef) and node.name in WANTED:
            name = node.name
        if name:
            exec(compile(ast.get_source_segment(src, node), "<obs>", "exec"), ns)
    return ns


def test_group_key():
    ns = _load()
    gk = ns["_group_key"]
    assert gk("binary_sensor.home_cove_alarm_zone_49") == "binary_sensor.home_cove_alarm_zone"
    assert gk("binary_sensor.home_cove_alarm_zone_63") == "binary_sensor.home_cove_alarm_zone"
    assert gk("light.kitchen_2") == "light.kitchen"
    assert gk("binary_sensor.front_door") is None          # no numeric suffix
    assert gk("sensor.x_1") is None                          # base too short
    assert gk("not_an_entity") is None


def test_burst_of_siblings_collapses_to_one():
    ns = _load()
    gd = ns["_group_debounced"]
    ns["_GROUP_LAST"].clear()
    # First zone passes; the rest of the bank within the window are suppressed.
    assert gd("binary_sensor.home_cove_alarm_zone_49", 90) is False
    assert gd("binary_sensor.home_cove_alarm_zone_50", 90) is True
    assert gd("binary_sensor.home_cove_alarm_zone_63", 90) is True
    # A different group is independent.
    assert gd("cover.garage_door_1", 90) is False
    # Non-grouped entities are never suppressed.
    assert gd("binary_sensor.front_door", 90) is False
    assert gd("binary_sensor.back_door", 90) is False
    # interval <= 0 disables coalescing.
    assert gd("binary_sensor.home_cove_alarm_zone_49", 0) is False


def test_window_expiry_lets_next_burst_through():
    ns = _load()
    gd = ns["_group_debounced"]
    ns["_GROUP_LAST"].clear()
    assert gd("binary_sensor.zone_bank_1", 0.05) is False
    assert gd("binary_sensor.zone_bank_2", 0.05) is True
    time.sleep(0.06)
    assert gd("binary_sensor.zone_bank_3", 0.05) is False   # window cleared
