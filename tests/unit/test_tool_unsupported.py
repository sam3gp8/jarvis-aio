"""A model that can't tool-call still answers, instead of failing to "connectivity" (#557).

When the configured model doesn't support function calling, the provider rejects
the tools-bearing request with a 400 ("`tool calling` is not supported with this
model"). That's not malformed tool SYNTAX and not a connectivity failure — the
model is reachable and can answer in prose. Before the fix it fell through to the
reasoning-tier fallback and ended at the "connectivity issues" sentinel; now the
agent drops tools and retries the same model.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def agent(load):
    return load("agent")


# ── _is_tool_unsupported_error (pure detector) ───────────────────────────────

# The exact error the reporter pasted (Groq, via the OpenAI-compatible surface).
_REPORTER_ERR = (
    "Error code: 400 - {'error': {'message': '`tool calling` is not supported "
    "with this model', 'type': 'invalid_request_error', 'param': 'tool calling'}}"
)

_UNSUPPORTED = [
    _REPORTER_ERR,
    "This model does not support tools.",
    "function calling is not supported for this model",
    "Error: tool use is not supported with the selected model",
    "400 unsupported: tools",
]

_NOT_UNSUPPORTED = [
    # malformed tool SYNTAX — handled by _is_tool_format_error, a different path
    "400 tool_use_failed: failed to call a function",
    "tool call validation failed",
    # genuine connectivity / rate-limit / server errors
    "Connection timed out",
    "429 rate limit exceeded, too many requests",
    "502 Bad Gateway",
    # model-not-found — must switch models, not drop tools
    "The model 'gpt-x' does not exist",
    # unrelated 400 with no tool mention
    "400 invalid_request_error: missing required parameter 'model'",
    "",
]


@pytest.mark.parametrize("msg", _UNSUPPORTED)
def test_detects_tool_unsupported(agent, msg):
    assert agent._is_tool_unsupported_error(RuntimeError(msg)) is True


@pytest.mark.parametrize("msg", _NOT_UNSUPPORTED)
def test_ignores_unrelated_errors(agent, msg):
    assert agent._is_tool_unsupported_error(RuntimeError(msg)) is False


def test_reporter_error_is_not_a_tool_format_error(agent):
    # it must NOT be mistaken for malformed-syntax (that path retries WITH tools
    # first, which would just fail again on a tool-less model)
    assert agent._is_tool_format_error(RuntimeError(_REPORTER_ERR)) is False


# ── run_agent end-to-end: drops tools and answers ────────────────────────────

class _ToollessModel:
    """Raises the 'not supported' 400 whenever tools are passed; answers in prose
    when they aren't. Records each call's tools-present flag."""
    name = "groq"
    model = "tool-less-model"

    def __init__(self):
        self.tools_seen = []

    def supports_tools(self):
        return True          # claims support → native loop, no pre-probe call

    def supports_vision(self):
        return False

    def chat(self, messages=None, tools=None, max_tokens=512, temperature=0.7, **kw):
        self.tools_seen.append(bool(tools))
        if tools:
            raise RuntimeError(_REPORTER_ERR)
        return {"text": "The living room is off, sir.", "tool_calls": [], "raw": None}


def _patch_provider(agent, monkeypatch, model):
    async def _fake(*a, **k):
        return model
    monkeypatch.setattr(agent, "_create_provider_with_fallback", _fake)


@pytest.mark.asyncio
async def test_toolless_model_answers_without_tools(agent, monkeypatch):
    h = FakeHass()
    model = _ToollessModel()
    _patch_provider(agent, monkeypatch, model)

    out = await agent.run_agent(
        h, messages=[{"role": "user", "content": "is the living room on?"}],
        persona="PERSONA", provider_name="groq", api_key="k",
        model="tool-less-model", hass_api=None, config={},
        turn_context="Current time: now.")

    # the model answered in prose rather than erroring to the connectivity sentinel
    assert out == "The living room is off, sir."
    assert "connectivity issues" not in out
    # first call carried tools (and raised), the retry dropped them
    assert model.tools_seen[0] is True
    assert model.tools_seen[-1] is False
