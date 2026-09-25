"""Tests for the native Gemini Interactions API adapter."""
from __future__ import annotations

import sys
import types


class _Interactions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _RejectsThinkingLevel:
    """Simulates a model that 400s on thinking_level (pre-Gemini-3), so chat()
    must retry with thinking_budget only."""
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["generation_config"].get("thinking_config", {}).get("thinking_level"):
            raise RuntimeError("400 Invalid argument: thinking_level is not supported for this model")
        return self.response


def _provider(llm, monkeypatch, response):
    interactions = _Interactions(response)
    client = types.SimpleNamespace(interactions=interactions)
    genai = types.SimpleNamespace(Client=lambda **kwargs: client)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return llm.GeminiProvider("AIza-key", "gemini-2.5-flash"), interactions


def _provider_rejecting_thinking_level(llm, monkeypatch, response):
    interactions = _RejectsThinkingLevel(response)
    client = types.SimpleNamespace(interactions=interactions)
    genai = types.SimpleNamespace(Client=lambda **kwargs: client)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return llm.GeminiProvider("AIza-key", "gemma-4-26b-a4b-it"), interactions


def test_gemini_uses_interactions_api_and_normalizes_function_calls(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(
        id="interaction-1",
        output_text="Done.",
        steps=[types.SimpleNamespace(
            type="function_call", id="call-1", name="turn_on", arguments={"area": "kitchen"},
        )],
    )
    provider, interactions = _provider(llm, monkeypatch, response)

    result = provider.chat(
        [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "Turn on the kitchen."}],
        tools=[{"type": "function", "function": {"name": "turn_on", "description": "Turn on a light", "parameters": {"type": "object"}}}],
        max_tokens=120,
        temperature=0.2,
    )

    assert result["text"] == "Done."
    assert result["tool_calls"] == [{"id": "call-1", "name": "turn_on", "args": {"area": "kitchen"}}]
    assert interactions.calls == [{
        "model": "gemini-2.5-flash",
        "input": [{"type": "user_input", "content": [{"type": "text", "text": "Turn on the kitchen."}]}],
        "generation_config": {
            "max_output_tokens": 120,
            "temperature": 0.2,
            "thinking_config": {"thinking_level": "minimal", "thinking_budget": 0},
        },
        "system_instruction": "Be concise.",
        "tools": [{"type": "function", "name": "turn_on", "description": "Turn on a light", "parameters": {"type": "object"}}],
    }]


def test_gemini_continues_function_results_with_prior_interaction(load, monkeypatch):
    llm = load("llm_provider")
    first = types.SimpleNamespace(
        id="interaction-1", output_text="",
        steps=[types.SimpleNamespace(type="function_call", id="call-1", name="get_temp", arguments={})],
    )
    provider, interactions = _provider(llm, monkeypatch, first)
    provider.chat([{"role": "user", "content": "What is the temperature?"}])
    interactions.response = types.SimpleNamespace(id="interaction-2", output_text="It is 22 C.", steps=[])

    result = provider.chat([
        {"role": "user", "content": "What is the temperature?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "function": {"name": "get_temp", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "{\"temperature\": 22}"},
    ])

    assert result["text"] == "It is 22 C."
    assert interactions.calls[1]["previous_interaction_id"] == "interaction-1"
    assert interactions.calls[1]["input"] == [{
        "type": "function_result", "name": "get_temp", "call_id": "call-1",
        "result": [{"type": "text", "text": "{\"temperature\": 22}"}],
    }]


def test_gemini_new_interaction_uses_only_the_latest_user_turn(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Tomorrow.", steps=[])
    provider, interactions = _provider(llm, monkeypatch, response)

    provider.chat([
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Who are you?"},
        {"role": "assistant", "content": "JARVIS."},
        {"role": "user", "content": "What is the weather now?"},
        {"role": "user", "content": "And tomorrow?"},
    ])

    assert interactions.calls[0]["input"] == [{
        "type": "user_input", "content": [{"type": "text", "text": "And tomorrow?"}],
    }]


def test_gemini_falls_back_to_thinking_budget_when_level_rejected(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="A man in a green shirt.", steps=[])
    provider, interactions = _provider_rejecting_thinking_level(llm, monkeypatch, response)

    result = provider.chat(
        [{"role": "user", "content": "Describe the scene."}],
        max_tokens=300, thinking=False,
    )

    assert result["text"] == "A man in a green shirt."
    assert len(interactions.calls) == 2
    assert interactions.calls[0]["generation_config"]["thinking_config"] == {
        "thinking_level": "minimal", "thinking_budget": 0,
    }
    assert interactions.calls[1]["generation_config"]["thinking_config"] == {"thinking_budget": 0}


def test_gemini_thinking_enabled_uses_high_level(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Reasoned answer.", steps=[])
    provider, interactions = _provider(llm, monkeypatch, response)

    provider.chat([{"role": "user", "content": "Decide."}], max_tokens=200, thinking=True)

    assert interactions.calls[0]["generation_config"]["thinking_config"] == {"thinking_level": "high"}


def test_gemini_thinking_enabled_falls_back_when_level_rejected(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Reasoned answer.", steps=[])
    provider, interactions = _provider_rejecting_thinking_level(llm, monkeypatch, response)

    result = provider.chat([{"role": "user", "content": "Decide."}], max_tokens=200, thinking=True)

    assert result["text"] == "Reasoned answer."
    assert len(interactions.calls) == 2
    assert interactions.calls[0]["generation_config"]["thinking_config"] == {"thinking_level": "high"}
    assert interactions.calls[1]["generation_config"]["thinking_config"] == {}