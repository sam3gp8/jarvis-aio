"""run_agent on a model WITHOUT function-calling (local-capability parity).

A model that can't call functions (e.g. Gemma via Ollama) would run the native
tool loop, emit no tool call, and narrate without acting (issue #413). run_agent
now routes such a model — detected by supports_tools() == False — through the
shared tool_driver's JSON-action protocol, so it drives JARVIS's tools over
plain JSON. A tool-capable model (supports_tools True) and an ad-hoc fake with no
capability signal (None) keep the native loop untouched.
"""
import json

import pytest

from fakes import FakeHass


@pytest.fixture
def agent(load):
    load("actuation")  # same module instance agent.py imports / we monkeypatch
    return load("agent")


def _device_responds(h):
    async def _acall(domain, service, data=None, blocking=True, **kw):
        h.service_calls.append((domain, service, dict(data or {})))
        reached = {"turn_on": "on", "turn_off": "off"}.get(service)
        if reached is not None:
            eid = (data or {}).get("entity_id")
            if eid:
                h.states.set(eid, reached)
    h.services.async_call = _acall


def _no_verify_sleep(agent, monkeypatch):
    async def _nosleep(_secs):
        return None
    monkeypatch.setattr(agent, "_VERIFY_SLEEP", _nosleep)


class _JsonModel:
    """A local model without function-calling: supports_tools() is False and
    chat() returns plain text (JSON actions), never tool_calls."""
    name = "ollama"
    model = "gemma4:26b"

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = 0
        self.seen = []

    def supports_tools(self):
        return False

    def chat(self, messages=None, tools=None, max_tokens=512, temperature=0.7, **kw):
        self.calls += 1
        self.seen.append({"messages": messages, "tools": tools})
        text = self._texts.pop(0) if self._texts else '{"tool":"final","text":"done"}'
        return {"text": text, "tool_calls": [], "raw": None}

    def supports_vision(self):
        return False


class _NativeModel:
    """A tool-capable model: supports_tools() True, emits real tool_calls."""
    name = "groq"
    model = "llama"

    def __init__(self):
        self.turns = 0
        self.seen = []

    def supports_tools(self):
        return True

    def chat(self, messages=None, tools=None, max_tokens=512, temperature=0.7, **kw):
        self.turns += 1
        self.seen.append(tools)
        if self.turns == 1:
            return {"text": "", "raw": None, "tool_calls": [
                {"id": "c1", "name": "control_device",
                 "args": {"action": "turn_on", "entity_id": "light.den"}}]}
        return {"text": "Done, sir.", "tool_calls": [], "raw": None}

    def supports_vision(self):
        return False


def _patch_provider(agent, monkeypatch, model):
    async def _fake(*a, **k):
        return model
    monkeypatch.setattr(agent, "_create_provider_with_fallback", _fake)


@pytest.mark.asyncio
async def test_non_tool_model_acts_through_json(agent, monkeypatch):
    """The core win: a non-function-calling model actually ACTS — it drives a
    control_device through the real seam over the JSON protocol."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("light.den", "off", friendly_name="Den light")
    _device_responds(h)
    model = _JsonModel([
        json.dumps({"tool": "control_device",
                    "args": {"action": "turn_on", "entity_id": "light.den"}}),
        json.dumps({"tool": "final", "text": "The den light is on, sir."}),
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await agent.run_agent(
        h, messages=[{"role": "user", "content": "turn on the den light"}],
        persona="", provider_name="ollama", api_key="", model="gemma4:26b",
        hass_api=None, config={})
    await h.drain()

    assert ("light", "turn_on") in [(d, s) for d, s, _ in h.service_calls]
    assert h.states.get("light.den").state == "on"
    assert "den light is on" in reply.lower()
    # JSON mode never offers native tools, and no capability probe was spent
    # (an explicit False needs none): exactly the two scripted turns.
    assert model.calls == 2
    assert all(c["tools"] is None for c in model.seen)


@pytest.mark.asyncio
async def test_non_tool_model_pure_qa(agent, monkeypatch):
    """A question needing no tool: the model finals immediately and the answer is
    returned verbatim — no service calls."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("light.den", "off", friendly_name="Den light")
    _device_responds(h)
    model = _JsonModel([
        json.dumps({"tool": "final", "text": "The den light is off, sir."}),
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await agent.run_agent(
        h, messages=[{"role": "user", "content": "is the den light on?"}],
        persona="", provider_name="ollama", api_key="", model="gemma4:26b",
        hass_api=None, config={})
    await h.drain()

    assert reply == "The den light is off, sir."
    assert h.service_calls == []
    assert model.calls == 1


@pytest.mark.asyncio
async def test_non_tool_model_empty_reply_is_graceful(agent, monkeypatch):
    """If the model never produces a usable final, run_agent returns the graceful
    fallback line, never a blank turn."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    _device_responds(h)
    model = _JsonModel(["", "", "", "", "", "", "", "", "", "", "", ""])
    _patch_provider(agent, monkeypatch, model)

    reply = await agent.run_agent(
        h, messages=[{"role": "user", "content": "..."}],
        persona="", provider_name="ollama", api_key="", model="gemma4:26b",
        hass_api=None, config={})
    await h.drain()

    assert isinstance(reply, str) and reply.strip()
    assert h.service_calls == []


@pytest.mark.asyncio
async def test_scoped_non_tool_subagent_acts_over_json(agent, monkeypatch):
    """A scoped sub-agent (allowed_tools set — the path _run_delegated uses) on a
    non-tool model drives only its scoped tools over JSON. This is how delegation
    inherits local parity: _run_delegated calls run_agent with allowed_tools, and
    run_agent routes a non-tool model through the JSON driver with that set."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("light.den", "off", friendly_name="Den light")
    _device_responds(h)
    model = _JsonModel([
        json.dumps({"tool": "control_device",
                    "args": {"action": "turn_on", "entity_id": "light.den"}}),
        json.dumps({"tool": "final", "text": "Done, sir."}),
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await agent.run_agent(
        h, messages=[{"role": "user", "content": "turn on the den light"}],
        persona="", provider_name="ollama", api_key="", model="gemma4:26b",
        hass_api=None, config={},
        allowed_tools={"control_device", "get_entity_state"}, max_iterations=4)
    await h.drain()

    assert ("light", "turn_on") in [(d, s) for d, s, _ in h.service_calls]
    assert "done" in reply.lower()


@pytest.mark.asyncio
async def test_tool_capable_model_stays_on_native(agent, monkeypatch):
    """A provider that reports function-calling support is NOT diverted — the
    native loop runs and offers real tools."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("light.den", "off", friendly_name="Den light")
    _device_responds(h)
    model = _NativeModel()
    _patch_provider(agent, monkeypatch, model)

    reply = await agent.run_agent(
        h, messages=[{"role": "user", "content": "turn on the den light"}],
        persona="", provider_name="groq", api_key="k", model="llama",
        hass_api=None, config={})
    await h.drain()

    assert model.seen[0] is not None            # native tools were offered
    assert ("light", "turn_on") in [(d, s) for d, s, _ in h.service_calls]
    assert isinstance(reply, str)
