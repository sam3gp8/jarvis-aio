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


def _patch_camera(monkeypatch, load, result):
    """Patch camera.async_analyze_camera IN PLACE on the real module.

    The tool does `from .camera import async_analyze_camera, _FakeCall`. Patching
    the loaded module's attribute (rather than swapping sys.modules for a fake)
    keeps this independent of how that import resolves — 3.13/3.14 can bind it
    from the parent-package attribute rather than sys.modules, so a sys.modules-
    only fake was silently bypassed there. The real module already provides
    _FakeCall (with .data), so agent's call shape is unchanged."""
    captured = {}
    async def _fake_analyze(hass, call, client, honorific, tts, spk,
                            gate_announce=False, force_images=None):
        captured["prompt"] = call.data.get("prompt")
        captured["entity_id"] = call.data.get("entity_id")
        captured["announce"] = call.data.get("announce")
        return result
    # camera.py imports aiohttp at module level; stub it so the real module loads
    # where aiohttp isn't installed (sandbox/CI).
    sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
    cam = load("camera")
    monkeypatch.setattr(cam, "async_analyze_camera", _fake_analyze)
    return captured


async def test_look_at_camera_success_returns_answer(agent, fake_hass, monkeypatch, load):
    captured = _patch_camera(monkeypatch, load, {
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
    captured = _patch_camera(monkeypatch, load, {
        "success": True, "analysis": "clear", "camera": "Shop", "source": "nest"})
    await agent._exec_look_at_camera(fake_hass, {
        "entity_id": "camera.shop", "question": "anything out?"})
    assert captured["announce"] is False          # quiet by default (monitor-safe)


async def test_look_at_camera_announce_passthrough(agent, fake_hass, monkeypatch, load):
    captured = _patch_camera(monkeypatch, load, {
        "success": True, "analysis": "x", "camera": "C", "source": "s"})
    await agent._exec_look_at_camera(fake_hass, {
        "entity_id": "camera.c", "question": "q", "announce": True})
    assert captured["announce"] is True


async def test_look_at_camera_failure_gives_hint(agent, fake_hass, monkeypatch, load):
    _patch_camera(monkeypatch, load, {
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
