"""#556: a control command confirmed but not executed.

A tool-CAPABLE local model (passes supports_tools → native loop) can still reply
to "turn on the TV and the LED" with a confirmation and NO tool call — so nothing
happens, yet JARVIS says it's done. run_agent now forces ONE retry to actually
call the tool, and if the model still won't act, returns an honest line instead
of relaying a false confirmation. A plain Q&A (no control verb) is untouched, and
a reply that already admits it couldn't act is left as-is.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def agent(load):
    load("actuation")   # same module instance agent.py imports / we monkeypatch
    return load("agent")


# ── pure helpers ─────────────────────────────────────────────────────────────
def test_control_request_positives(agent):
    for t in ["turn on the LED and TV", "turn off everything",
              "switch on the lamp", "lock the door", "dim the lights",
              "mach das Licht an", "schalte die Lampe ein", "einschalten",
              "allume la lumière", "éteins la télé"]:
        assert agent._looks_like_control_request(t), t


def test_control_request_negatives(agent):
    for t in ["what's the weather", "is the TV on?", "when are the opening hours",
              "who founded the soviet union", "give me a home status report",
              "wie spät ist es", ""]:
        assert not agent._looks_like_control_request(t), t


def test_admits_no_action(agent):
    for t in ["I couldn't find a device called TV, sir.",
              "Ich konnte das Gerät nicht finden.",
              "Je n'ai pas trouvé l'appareil.", "Which device did you mean?"]:
        assert agent._admits_no_action(t), t
    for t in ["The TV and LED are now on, sir.", "Done.", ""]:
        assert not agent._admits_no_action(t), t


def test_honest_reply_is_honorific(agent):
    out = agent._honest_no_action_reply({"honorific": "boss"})
    assert "boss" in out and "wasn't able" in out


# ── run_agent loop (native, tool-capable model) ──────────────────────────────
class _ScriptedNative:
    """A tool-capable model that returns a scripted sequence of turns; each turn
    is {"text": str, "tool_calls": list}."""
    name = "groq"
    model = "nemotron"

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0
        self.seen = []

    def supports_tools(self):
        return True

    def supports_vision(self):
        return False

    def chat(self, messages=None, tools=None, max_tokens=512, temperature=0.7, **kw):
        self.calls += 1
        self.seen.append({"messages": messages, "tools": tools})
        t = self._turns.pop(0) if self._turns else {"text": "", "tool_calls": []}
        return {"text": t.get("text", ""),
                "tool_calls": t.get("tool_calls", []), "raw": None}


def _device_responds(h):
    async def _acall(domain, service, data=None, blocking=True, **kw):
        h.service_calls.append((domain, service, dict(data or {})))
        reached = {"turn_on": "on", "turn_off": "off"}.get(service)
        if reached is not None and (data or {}).get("entity_id"):
            h.states.set((data or {})["entity_id"], reached)
    h.services.async_call = _acall


def _no_verify_sleep(agent, monkeypatch):
    async def _nosleep(_secs):
        return None
    monkeypatch.setattr(agent, "_VERIFY_SLEEP", _nosleep)


def _patch_provider(agent, monkeypatch, model):
    async def _fake(*a, **k):
        return model
    monkeypatch.setattr(agent, "_create_provider_with_fallback", _fake)


def _ctl(entity_id, call_id):
    return {"id": call_id, "name": "control_device",
            "args": {"action": "turn_on", "entity_id": entity_id}}


async def _run(agent, h, model, text):
    return await agent.run_agent(
        h, messages=[{"role": "user", "content": text}],
        persona="", provider_name="groq", api_key="k", model="nemotron",
        hass_api=None, config={})


@pytest.mark.asyncio
async def test_false_confirmation_retries_and_acts(agent, monkeypatch):
    """Turn 1: confirms with NO tool call. The forced retry makes the model call
    control_device for both devices — they actually turn on, and the reply is
    truthful."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("light.led", "off", friendly_name="LED")
    h.states.set("switch.tv", "off", friendly_name="TV")
    _device_responds(h)
    model = _ScriptedNative([
        {"text": "The TV and the LED are now on, sir.", "tool_calls": []},   # lie
        {"text": "", "tool_calls": [_ctl("light.led", "c1"),
                                    _ctl("switch.tv", "c2")]},               # retry acts
        {"text": "Done — the LED and TV are on, sir.", "tool_calls": []},
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await _run(agent, h, model, "turn on the LED and the TV")
    await h.drain()

    done = [(d, s) for d, s, _ in h.service_calls]
    assert ("light", "turn_on") in done and ("switch", "turn_on") in done
    assert h.states.get("light.led").state == "on"
    assert h.states.get("switch.tv").state == "on"
    assert model.calls == 3
    # the forced-retry nudge reached the model on the second turn
    assert any(m.get("content") == agent._CONTROL_NUDGE
               for m in model.seen[1]["messages"])
    assert "on" in reply.lower()


@pytest.mark.asyncio
async def test_persistent_false_confirmation_becomes_honest(agent, monkeypatch):
    """Model confirms with no tool call on BOTH the first turn and the forced
    retry: JARVIS returns an honest line, never the false 'done', and nothing is
    actuated."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("switch.tv", "off", friendly_name="TV")
    _device_responds(h)
    model = _ScriptedNative([
        {"text": "The TV is now on, sir.", "tool_calls": []},
        {"text": "Yes, the TV is on, sir.", "tool_calls": []},
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await _run(agent, h, model, "turn on the TV")
    await h.drain()

    assert h.service_calls == []                      # nothing actuated
    assert model.calls == 2                           # one forced retry, no more
    assert "wasn't able" in reply.lower()             # honest, not "the TV is on"
    assert "is now on" not in reply.lower()


@pytest.mark.asyncio
async def test_plain_question_untouched(agent, monkeypatch):
    """A question with no control verb makes one call and the answer is returned
    verbatim — no retry, no override."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    _device_responds(h)
    model = _ScriptedNative([
        {"text": "Paris is the capital of France, sir.", "tool_calls": []},
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await _run(agent, h, model, "what is the capital of France?")
    await h.drain()

    assert reply == "Paris is the capital of France, sir."
    assert model.calls == 1
    assert h.service_calls == []


@pytest.mark.asyncio
async def test_admitted_failure_is_kept(agent, monkeypatch):
    """A control request where the model honestly says it couldn't find the device
    is left as-is — no retry, no override."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    _device_responds(h)
    model = _ScriptedNative([
        {"text": "I couldn't find a device called TV, sir.", "tool_calls": []},
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await _run(agent, h, model, "turn on the TV")
    await h.drain()

    assert reply == "I couldn't find a device called TV, sir."
    assert model.calls == 1
    assert h.service_calls == []


@pytest.mark.asyncio
async def test_normal_control_unaffected(agent, monkeypatch):
    """A model that acts on the first turn is unaffected — no extra retry."""
    _no_verify_sleep(agent, monkeypatch)
    h = FakeHass()
    h.states.set("light.led", "off", friendly_name="LED")
    _device_responds(h)
    model = _ScriptedNative([
        {"text": "", "tool_calls": [_ctl("light.led", "c1")]},
        {"text": "The LED is on, sir.", "tool_calls": []},
    ])
    _patch_provider(agent, monkeypatch, model)

    reply = await _run(agent, h, model, "turn on the LED")
    await h.drain()

    assert ("light", "turn_on") in [(d, s) for d, s, _ in h.service_calls]
    assert model.calls == 2                           # no forced retry needed
    assert "led is on" in reply.lower()
