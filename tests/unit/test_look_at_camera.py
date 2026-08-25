"""Tests for the look_at_camera vision tool (v6.58.0).

The underlying camera pipeline needs Home Assistant + a vision provider, so the
tool's *behavior* is split into two testable halves:

  * argument validation (_exec_look_at_camera) — pure, no vision stack, and
  * result shaping (_shape_look_at_camera_result) — a pure function turning a
    vision result into the tool's JSON contract.

Earlier revisions patched the analyze path by swapping module internals; that
proved fragile across CPython builds (a persistent CI-only failure that could
not be reproduced locally). Testing the pure shaping function instead exercises
the same success/failure contract with nothing that can flake.
"""
import json

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


async def test_look_at_camera_requires_args(agent, fake_hass):
    out = json.loads(await agent._exec_look_at_camera(fake_hass, {}))
    assert "error" in out
    out2 = json.loads(await agent._exec_look_at_camera(
        fake_hass, {"entity_id": "camera.x"}))          # no question
    assert "error" in out2
    out3 = json.loads(await agent._exec_look_at_camera(
        fake_hass, {"question": "anything?"}))           # no entity
    assert "error" in out3


def test_shape_success_returns_answer(agent):
    out = json.loads(agent._shape_look_at_camera_result({
        "success": True, "analysis": "Yes — a soldering iron is on the bench.",
        "camera": "Workshop", "source": "frigate"}, "camera.workshop"))
    assert out["success"] is True
    assert "soldering iron" in out["answer"]
    assert out["camera"] == "Workshop"
    assert out["source"] == "frigate"


def test_shape_failure_gives_hint(agent):
    out = json.loads(agent._shape_look_at_camera_result({
        "success": False, "error": "no_image", "camera": "Backyard"},
        "camera.backyard"))
    assert out["success"] is False
    assert out["camera"] == "Backyard"
    assert out["error"] == "no_image"
    assert out["hint"]                                    # actionable hint present


def test_shape_failure_defaults_camera_to_entity_and_error(agent):
    out = json.loads(agent._shape_look_at_camera_result({"success": False}, "camera.x"))
    assert out["success"] is False
    assert out["camera"] == "camera.x"                   # falls back to the entity_id
    assert out["error"] == "vision analysis failed"      # default message


def test_shape_success_omits_hint(agent):
    out = json.loads(agent._shape_look_at_camera_result({
        "success": True, "analysis": "clear", "camera": "Shop", "source": "nest"},
        "camera.shop"))
    assert "hint" not in out
