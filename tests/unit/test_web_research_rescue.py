"""#515: web_research rescue for small models.

Small local models (qwen3.5:4b, gemma4:12b) sometimes answer a current-events
question with a canned "training cutoff / that's in the future / no real-time
data" excuse instead of calling web_research — even when asked to search. The
rescue detects that excuse in a FINAL answer and runs the lookup the model
should have run, narrating from the results; a normal answer is never
second-guessed.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def agent(load):
    return load("agent")


class _Client:
    """Minimal provider: chat() returns a fixed narration, records its calls."""
    name = "ollama"
    model = "qwen3.5:4b"

    def __init__(self, narration="The latest, sir: it happened."):
        self.narration = narration
        self.chat_calls = []

    def chat(self, messages=None, tools=None, max_tokens=512, temperature=0.7, **kw):
        self.chat_calls.append(messages)
        return {"text": self.narration, "tool_calls": [], "raw": None}


def _patch_research(agent, monkeypatch, payload):
    calls = {"n": 0, "query": None, "name": None}

    async def _fake_exec(hass, name, args, hass_api=None, user_input=None):
        calls["n"] += 1
        calls["name"] = name
        calls["query"] = (args or {}).get("query")
        return payload

    monkeypatch.setattr(agent, "_execute_tool", _fake_exec)
    return calls


# ── detector ──────────────────────────────────────────────────────────────────

def test_detector_english(agent):
    assert agent._looks_like_cutoff_refusal("My training data only goes up to 2024.")
    assert agent._looks_like_cutoff_refusal("I don't have real-time access to the news.")
    assert agent._looks_like_cutoff_refusal("That event is in the future for me.")
    assert agent._looks_like_cutoff_refusal("It's beyond my knowledge, I'm afraid.")


def test_detector_german(agent):
    assert agent._looks_like_cutoff_refusal("2026 liegt in der Zukunft.")
    assert agent._looks_like_cutoff_refusal("Mein Wissensstand endet 2024.")
    assert agent._looks_like_cutoff_refusal("Ich habe keine Echtzeitdaten.")


def test_detector_negatives(agent):
    assert not agent._looks_like_cutoff_refusal("The kitchen light is on, sir.")
    assert not agent._looks_like_cutoff_refusal("Everything is locked and secured.")
    assert not agent._looks_like_cutoff_refusal("")


# ── rescue behaviour ────────────────────────────────────────────────────────────

async def test_rescue_searches_and_reanswers_on_cutoff_excuse(agent, monkeypatch):
    h = FakeHass()
    calls = _patch_research(agent, monkeypatch, '{"summary":"Latest: X happened."}')
    client = _Client("Here's the latest, sir: X happened.")
    out = await agent._maybe_web_rescue(
        h, "I'm sorry, that's beyond my training cutoff.",
        user_text="What's the latest news on the Ukraine?",
        client=client, hass_api=None, user_input=None, config={"honorific": "sir"})
    assert calls["n"] == 1 and calls["name"] == "web_research"
    assert "ukraine" in (calls["query"] or "").lower()
    assert out == "Here's the latest, sir: X happened."
    assert client.chat_calls                      # a narration pass occurred


async def test_normal_answer_is_untouched(agent, monkeypatch):
    h = FakeHass()
    calls = _patch_research(agent, monkeypatch, '{"summary":"x"}')
    client = _Client()
    out = await agent._maybe_web_rescue(
        h, "The front door is locked, sir.",
        user_text="Is the front door locked?",
        client=client, hass_api=None, user_input=None, config={})
    assert out == "The front door is locked, sir."
    assert calls["n"] == 0                         # no web search
    assert client.chat_calls == []                 # no extra model call


async def test_research_error_keeps_original(agent, monkeypatch):
    h = FakeHass()
    _patch_research(agent, monkeypatch, '{"error":"offline"}')
    client = _Client()
    original = "I don't have real-time data, sir."
    out = await agent._maybe_web_rescue(
        h, original, user_text="latest news?",
        client=client, hass_api=None, user_input=None, config={})
    assert out == original                         # degraded gracefully
    assert client.chat_calls == []                 # never narrated an error


async def test_narration_strips_think(agent, monkeypatch):
    h = FakeHass()
    _patch_research(agent, monkeypatch, '{"summary":"ok"}')
    client = _Client("<think>let me read the results</think>Here's the latest, sir.")
    out = await agent._maybe_web_rescue(
        h, "that is beyond my knowledge cutoff",
        user_text="news?", client=client, hass_api=None, user_input=None, config={})
    assert out == "Here's the latest, sir."
    assert "<think>" not in out


async def test_empty_user_text_no_rescue(agent, monkeypatch):
    h = FakeHass()
    calls = _patch_research(agent, monkeypatch, '{"summary":"x"}')
    client = _Client()
    out = await agent._maybe_web_rescue(
        h, "beyond my training cutoff", user_text="",
        client=client, hass_api=None, user_input=None, config={})
    assert out == "beyond my training cutoff"
    assert calls["n"] == 0
