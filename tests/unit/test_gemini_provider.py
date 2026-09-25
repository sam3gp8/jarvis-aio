"""Tests for the native Gemini Interactions API adapter."""
from __future__ import annotations

import sys
import types

import pytest


class _Interactions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _SequentialInteractions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class _RejectsThinkingLevel:
    """Simulates a model that 400s on thinking_level: "minimal" (not every
    model accepts it — ai.google.dev/gemini-api/docs/thinking), so chat()
    must retry with "low" instead. Uses the exact wording Gemini returns
    ("thinking level", with a space, not the field name)."""
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["generation_config"].get("thinking_level") == "minimal":
            raise RuntimeError(
                "Error code: 400 - {'error': {'message': \"'minimal' is not a "
                "supported thinking level for this model. Allowed values are: "
                "medium, low, high.\", 'code': 'invalid_request'}}"
            )
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
    return llm.GeminiProvider("AIza-key", "gemini-2.5-flash-lite"), interactions


def _provider_with_responses(llm, monkeypatch, responses):
    interactions = _SequentialInteractions(responses)
    client = types.SimpleNamespace(interactions=interactions)
    genai = types.SimpleNamespace(Client=lambda **kwargs: client)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return llm.GeminiProvider("AIza-key", "gemini-flash-latest"), interactions


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
    run_state = {}
    provider.chat([{"role": "user", "content": "What is the temperature?"}], run_state=run_state)
    interactions.response = types.SimpleNamespace(id="interaction-2", output_text="It is 22 C.", steps=[])

    result = provider.chat([
        {"role": "user", "content": "What is the temperature?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "function": {"name": "get_temp", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "{\"temperature\": 22}"},
    ], run_state=run_state)

    assert result["text"] == "It is 22 C."
    assert interactions.calls[1]["previous_interaction_id"] == "interaction-1"
    assert interactions.calls[1]["input"] == [{
        "type": "function_result", "name": "get_temp", "call_id": "call-1",
        "result": [{"type": "text", "text": "{\"temperature\": 22}"}],
    }]


def test_gemini_continuation_state_is_scoped_per_run(load, monkeypatch):
    llm = load("llm_provider")
    run_a_call = types.SimpleNamespace(
        id="interaction-a", output_text="",
        steps=[types.SimpleNamespace(type="function_call", id="call-a", name="get_temp", arguments={})],
    )
    run_b_call = types.SimpleNamespace(
        id="interaction-b", output_text="",
        steps=[types.SimpleNamespace(type="function_call", id="call-b", name="turn_on", arguments={})],
    )
    run_a_answer = types.SimpleNamespace(id="interaction-a2", output_text="It is 22 C.", steps=[])
    provider, interactions = _provider_with_responses(
        llm, monkeypatch, [run_a_call, run_b_call, run_a_answer]
    )
    state_a = {}
    state_b = {}

    provider.chat([{"role": "user", "content": "What is the temperature?"}], run_state=state_a)
    provider.chat([{"role": "user", "content": "Turn on the kitchen."}], run_state=state_b)
    result = provider.chat([
        {"role": "user", "content": "What is the temperature?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-a", "function": {"name": "get_temp", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-a", "content": "{\"temperature\": 22}"},
    ], run_state=state_a)

    assert result["text"] == "It is 22 C."
    assert interactions.calls[2]["previous_interaction_id"] == "interaction-a"
    assert interactions.calls[2]["input"] == [{
        "type": "function_result", "name": "get_temp", "call_id": "call-a",
        "result": [{"type": "text", "text": "{\"temperature\": 22}"}],
    }]


def test_gemini_new_interaction_preserves_plain_transcript(load, monkeypatch):
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

    assert interactions.calls[0]["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "Who are you?"}]},
        {"type": "model_output", "content": [{"type": "text", "text": "JARVIS."}]},
        {"type": "user_input", "content": [{"type": "text", "text": "What is the weather now?"}]},
        {"type": "user_input", "content": [{"type": "text", "text": "And tomorrow?"}]},
    ]


def test_gemini_new_interaction_does_not_replay_stale_tool_calls(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Tomorrow.", steps=[])
    provider, interactions = _provider(llm, monkeypatch, response)

    provider.chat([
        {"role": "user", "content": "What is the weather now?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "function": {"name": "weather_forecast", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Rain today."},
        {"role": "assistant", "content": "It will rain today."},
        {"role": "user", "content": "And tomorrow?"},
    ])

    assert interactions.calls[0]["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "What is the weather now?"}]},
        {"type": "model_output", "content": [{"type": "text", "text": "It will rain today."}]},
        {"type": "user_input", "content": [{"type": "text", "text": "And tomorrow?"}]},
    ]


def test_gemini_falls_back_to_low_level_when_minimal_rejected(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="A man in a green shirt.", steps=[])
    provider, interactions = _provider_rejecting_thinking_level(llm, monkeypatch, response)

    result = provider.chat(
        [{"role": "user", "content": "Describe the scene."}],
        max_tokens=300, thinking=False,
    )

    assert result["text"] == "A man in a green shirt."
    assert len(interactions.calls) == 2

    assert interactions.calls[0]["generation_config"]["thinking_level"] == "minimal"
    assert interactions.calls[1]["generation_config"]["thinking_level"] == "low"


def test_gemini_thinking_enabled_uses_high_level(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Reasoned answer.", steps=[])
    provider, interactions = _provider(llm, monkeypatch, response)

    provider.chat([{"role": "user", "content": "Decide."}], max_tokens=200, thinking=True)

    assert interactions.calls[0]["generation_config"]["thinking_level"] == "high"


def test_gemini_thinking_enabled_is_unaffected_by_minimal_rejection(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Reasoned answer.", steps=[])
    provider, interactions = _provider_rejecting_thinking_level(llm, monkeypatch, response)

    # "high" is universally supported, so the disable-path fallback (for
    # "minimal") never triggers here — no retry needed.
    result = provider.chat([{"role": "user", "content": "Decide."}], max_tokens=200, thinking=True)

    assert result["text"] == "Reasoned answer."
    assert len(interactions.calls) == 1
    assert interactions.calls[0]["generation_config"]["thinking_level"] == "high"


def test_gemini_thinking_unset_leaves_level_unset(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Routine.", steps=[])
    provider, interactions = _provider(llm, monkeypatch, response)

    # Callers with no opinion on thinking (e.g. the camera-reasoning model,
    # which isn't the vision toggle's model) must not send thinking_level at
    # all — some models 400 on "minimal", so the model's own default applies.
    provider.chat([{"role": "user", "content": "Judge this scene."}], max_tokens=220)

    assert "thinking_level" not in interactions.calls[0]["generation_config"]


def test_gemini_thinking_unset_never_retries_on_rejecting_model(load, monkeypatch):
    llm = load("llm_provider")
    response = types.SimpleNamespace(id="interaction-1", output_text="Routine.", steps=[])
    provider, interactions = _provider_rejecting_thinking_level(llm, monkeypatch, response)

    # No thinking_level sent → the fake never raises → single call, no retry.
    result = provider.chat([{"role": "user", "content": "Judge this scene."}], max_tokens=220)

    assert result["text"] == "Routine."
    assert len(interactions.calls) == 1
    assert "thinking_level" not in interactions.calls[0]["generation_config"]


def test_gemini_retries_incomplete_response_with_larger_budget(load, monkeypatch):
    llm = load("llm_provider")
    incomplete = types.SimpleNamespace(
        id="interaction-incomplete", status="incomplete",
        output_text='{"speak": false, "reason":', steps=[],
    )
    complete = types.SimpleNamespace(
        id="interaction-complete", status="completed",
        output_text='{"speak": false, "reason": "routine"}', steps=[],
    )
    provider, interactions = _provider_with_responses(
        llm, monkeypatch, [incomplete, complete]
    )

    result = provider.chat(
        [{"role": "user", "content": "Decide."}], max_tokens=200
    )

    assert result["text"] == '{"speak": false, "reason": "routine"}'
    assert interactions.calls[0]["generation_config"]["max_output_tokens"] == 200
    assert interactions.calls[1]["generation_config"]["max_output_tokens"] == 2048
    assert "previous_interaction_id" not in interactions.calls[1]


def test_gemini_rejects_response_that_remains_incomplete(load, monkeypatch):
    llm = load("llm_provider")
    incomplete = types.SimpleNamespace(
        id="interaction-incomplete", status="incomplete",
        output_text='{"speak": false, "reason":', steps=[],
    )
    provider, interactions = _provider_with_responses(
        llm, monkeypatch, [incomplete, incomplete]
    )

    with pytest.raises(RuntimeError, match="remained incomplete"):
        provider.chat(
            [{"role": "user", "content": "Decide."}], max_tokens=200
        )

    assert len(interactions.calls) == 2


@pytest.mark.parametrize("status", ["failed", "cancelled", "budget_exceeded"])
def test_gemini_terminal_failure_does_not_save_continuation_state(load, monkeypatch, status):
    llm = load("llm_provider")
    response = types.SimpleNamespace(
        id="failed-interaction", status=status, output_text="", steps=[]
    )
    provider, _ = _provider(llm, monkeypatch, response)
    run_state = {}

    with pytest.raises(RuntimeError, match=f"status: {status}"):
        provider.chat([{"role": "user", "content": "Hello."}], run_state=run_state)

    assert run_state == {}
