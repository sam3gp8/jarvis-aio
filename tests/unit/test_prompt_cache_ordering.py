"""KV-cache-friendly prompt ordering (#561).

The per-turn dynamic context (clock, presence, home state, memory, knowledge)
must NOT live in the system prompt: a changing minute timestamp near the top of
a ~12k-token prompt invalidates the whole KV-cache on local backends every 60s.
run_agent now keeps the system prompt (persona + guidance + tool schemas) a
byte-stable prefix and delivers the live context at the TAIL of the request,
merged into the final user turn.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def agent(load):
    return load("agent")


# ── _append_live_context (pure helper) ───────────────────────────────────────

def test_append_live_context_merges_into_last_user_turn(agent):
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]
    out = agent._append_live_context(msgs, "LIVE")
    # merged into the LAST user turn only
    assert out[-1]["content"] == "second\n\nLIVE"
    assert out[1]["content"] == "first"          # earlier user turn untouched
    assert out[0]["content"] == "SYS"            # system untouched


def test_append_live_context_does_not_mutate_input(agent):
    user = {"role": "user", "content": "hello"}
    msgs = [{"role": "system", "content": "SYS"}, user]
    out = agent._append_live_context(msgs, "LIVE")
    # the caller's own dicts/list are never mutated (history is shared state)
    assert user["content"] == "hello"
    assert msgs[1]["content"] == "hello"
    assert out is not msgs
    assert out[-1]["content"] == "hello\n\nLIVE"


def test_append_live_context_multimodal_user_gets_text_part(agent):
    parts = [{"type": "text", "text": "look"},
             {"type": "image_url", "image_url": {"url": "x"}}]
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": parts}]
    out = agent._append_live_context(msgs, "LIVE")
    assert out[-1]["content"][-1] == {"type": "text", "text": "LIVE"}
    assert len(out[-1]["content"]) == 3
    assert parts == [{"type": "text", "text": "look"},
                     {"type": "image_url", "image_url": {"url": "x"}}]  # unmutated


def test_append_live_context_empty_block_is_noop(agent):
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    assert agent._append_live_context(msgs, "") is msgs


def test_append_live_context_no_user_falls_back_to_system(agent):
    msgs = [{"role": "system", "content": "SYS"}]
    out = agent._append_live_context(msgs, "LIVE")
    assert out[0]["content"] == "SYS\n\nLIVE"


# ── run_agent: static prefix stable, live context in the user turn ────────────

class _CapModel:
    """Captures the messages passed to chat(), then finals immediately."""
    name = "groq"
    model = "llama"

    def __init__(self):
        self.seen = []

    def supports_tools(self):
        return True

    def chat(self, messages=None, tools=None, max_tokens=512, temperature=0.7, **kw):
        self.seen.append([dict(m) for m in (messages or [])])
        return {"text": "Done, sir.", "tool_calls": [], "raw": None}

    def supports_vision(self):
        return False


def _patch_provider(agent, monkeypatch, model):
    async def _fake(*a, **k):
        return model
    monkeypatch.setattr(agent, "_create_provider_with_fallback", _fake)


async def _run_once(agent, h, model, clock):
    return await agent.run_agent(
        h, messages=[{"role": "user", "content": "what's the plan"}],
        persona="PERSONA-STATIC-PREFIX", provider_name="groq", api_key="k",
        model="llama", hass_api=None, config={},
        turn_context=f"## Current context (live data)\nCurrent time: {clock}.")


@pytest.mark.asyncio
async def test_system_prompt_is_byte_stable_across_a_minute_change(agent, monkeypatch):
    """The whole point of #561: the clock changes, the system prompt does NOT."""
    h = FakeHass()
    h.states.set("light.den", "off", friendly_name="Den light")
    model = _CapModel()
    _patch_provider(agent, monkeypatch, model)

    await _run_once(agent, h, model, "Monday November 17, 10:00 AM")
    await _run_once(agent, h, model, "Monday November 17, 10:01 AM")

    sys1, sys2 = model.seen[0][0], model.seen[1][0]
    assert sys1["role"] == "system" and sys2["role"] == "system"
    # byte-identical system prompt → KV-cache for the whole prefix is reused
    assert sys1["content"] == sys2["content"]
    # the persona prefix leads, and no per-turn dynamic context leaked into it
    assert sys1["content"].startswith("PERSONA-STATIC-PREFIX")
    assert "Current time" not in sys1["content"]
    assert "Current home state" not in sys1["content"]


@pytest.mark.asyncio
async def test_live_context_rides_in_the_final_user_turn(agent, monkeypatch):
    h = FakeHass()
    h.states.set("light.den", "off", friendly_name="Den light")
    model = _CapModel()
    _patch_provider(agent, monkeypatch, model)

    await _run_once(agent, h, model, "Monday November 17, 10:00 AM")

    convo = model.seen[0]
    user = convo[-1]
    assert user["role"] == "user"
    # the user's words are preserved, with the live context appended after them
    assert user["content"].startswith("what's the plan")
    assert "Current time: Monday November 17, 10:00 AM." in user["content"]
    # home state moved out of the system prompt and into the tail as well
    assert "## Current home state" in user["content"]
