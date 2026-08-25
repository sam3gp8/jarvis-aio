"""Tests for the look_at_camera vision tool (v6.58.0) — on-demand and monitor
visual queries. The underlying camera pipeline needs HA + a vision provider, so
we test the tool's validation, its wrapping of async_analyze_camera, and how it
shapes success/failure results, with the analyze path monkeypatched."""
import json
import sys
import types

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


def _patch_camera(monkeypatch, agent, result):
    """Patch agent._analyze_camera directly — a name in the agent module's own
    namespace. `_exec_look_at_camera` resolves that name from the module dict at
    call time, so this works regardless of how `from .camera import …` resolves
    (which varied across CPython builds and caused a persistent CI-only failure).
    No fake module, no sys.modules juggling, no aiohttp stub needed."""
    captured = {}

    async def _fake_analyze(hass, entity_id, prompt, announce, honorific):
        captured["entity_id"] = entity_id
        captured["prompt"] = prompt
        captured["announce"] = announce
        return result

    monkeypatch.setattr(agent, "_analyze_camera", _fake_analyze)
    return captured


async def test_look_at_camera_success_returns_answer(agent, fake_hass, monkeypatch, load):
    captured = _patch_camera(monkeypatch, agent, {
        "success": True, "analysis": "Yes — a soldering iron is on the bench.",
        "camera": "Workshop", "source": "frigate"})
    out = json.loads(await agent._exec_look_at_camera(fake_hass, {
        "entity_id": "camera.workshop",
        "question": "is a tool left on the bench?"}))
    assert out["success"] is True
    assert "soldering iron" in out["answer"]
    assert out["camera"] == "Workshop"
    # the user's question is embedded in the vision prompt
    assert "is a tool left on the bench?" in captured["prompt"]
    assert captured["entity_id"] == "camera.workshop"


async def test_look_at_camera_defaults_announce_false(agent, fake_hass, monkeypatch, load):
    captured = _patch_camera(monkeypatch, agent, {
        "success": True, "analysis": "clear", "camera": "Shop", "source": "nest"})
    await agent._exec_look_at_camera(fake_hass, {
        "entity_id": "camera.shop", "question": "anything out?"})
    assert captured["announce"] is False          # quiet by default (monitor-safe)


async def test_look_at_camera_announce_passthrough(agent, fake_hass, monkeypatch, load):
    captured = _patch_camera(monkeypatch, agent, {
        "success": True, "analysis": "x", "camera": "C", "source": "s"})
    await agent._exec_look_at_camera(fake_hass, {
        "entity_id": "camera.c", "question": "q", "announce": True})
    assert captured["announce"] is True


async def test_look_at_camera_failure_gives_hint(agent, fake_hass, monkeypatch, load):
    _patch_camera(monkeypatch, agent, {
        "success": False, "error": "no_image", "camera": "Backyard"})
    out = json.loads(await agent._exec_look_at_camera(fake_hass, {
        "entity_id": "camera.backyard", "question": "anyone there?"}))
    assert out["success"] is False
    assert out["error"] == "no_image"
    assert "hint" in out                          # actionable guidance on failure


def test_look_at_camera_registered(agent):
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "look_at_camera" in names
    assert "look_at_camera" in agent._TOOL_MAP
