"""Persistent announcement mutes (#181).

shush()/unshush() must survive a restart: the entity/category/blanket mute set
is written to disk and reloaded. current_mutes() reports it for the panel.
"""
import pytest


@pytest.fixture
def og(load, tmp_path, monkeypatch):
    mod = load("output_gate")
    monkeypatch.setattr(mod, "MUTES_PATH", str(tmp_path / "output_mutes.json"))
    # Start from a clean, unloaded state for each test.
    mod._STATE.muted_entities.clear()
    mod._STATE.muted_categories.clear()
    mod._STATE.mute_all = False
    mod._mutes_loaded = False
    return mod


def _simulate_restart(og):
    """Drop in-memory mutes and force a reload from disk."""
    og._STATE.muted_entities.clear()
    og._STATE.muted_categories.clear()
    og._STATE.mute_all = False
    og._mutes_loaded = False


def test_entity_mute_persists_across_restart(og):
    og.shush(entity_id="binary_sensor.lounge_window")
    _simulate_restart(og)
    assert "binary_sensor.lounge_window" in og.current_mutes()["entities"]


def test_category_mute_persists(og):
    og.shush(category="appliance")
    _simulate_restart(og)
    assert "appliance" in og.current_mutes()["categories"]


def test_blanket_mute_persists(og):
    og.shush(all=True)
    _simulate_restart(og)
    assert og.current_mutes()["all"] is True


def test_unshush_entity_persists_removal(og):
    og.shush(entity_id="light.wled")
    assert "light.wled" in og.current_mutes()["entities"]
    og.unshush(entity_id="light.wled")
    _simulate_restart(og)
    assert "light.wled" not in og.current_mutes()["entities"]


def test_clear_all_persists(og):
    og.shush(entity_id="light.wled")
    og.shush(category="appliance")
    og.shush(all=True)
    og.unshush()   # no args → clear everything
    _simulate_restart(og)
    m = og.current_mutes()
    assert m["entities"] == [] and m["categories"] == [] and m["all"] is False


def test_muted_entity_is_gated(og):
    # A muted entity's announcement is suppressed by the gate decision.
    og.shush(entity_id="binary_sensor.lounge_window")
    allowed, _reason = og._gate_decision(
        1.0, entity_id="binary_sensor.lounge_window", category="observation",
        urgency="low", message="window open")
    assert allowed is False
