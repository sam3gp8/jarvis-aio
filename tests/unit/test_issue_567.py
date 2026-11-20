"""#567 — a model that emits a tool call as PLAIN TEXT (Gemma-4 / nemotron-bolt
`toolname{arg:<|"|>val<|"|>}`) must have it executed, not spoken to TTS verbatim.

`agent._salvage_inline_tool_call` recovers such a call into JARVIS's structured
`{"id","name","args"}` shape, conservatively — only a DECLARED tool name followed
by a JSON-object body triggers it; anything else is left exactly as the model
returned it.
"""
import pathlib

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def _tools(*names):
    """OpenAI-nested tool specs, as JARVIS_TOOLS / _scoped_tool_list produce."""
    return [{"type": "function", "function": {"name": n}} for n in names]


def test_salvages_gemma_delimited_call(agent):
    text = 'llm-intents__search_web{query:<|"|>beste Klasse World of Warcraft<|"|>}'
    calls, cleaned = agent._salvage_inline_tool_call(
        text, _tools("llm-intents__search_web"))
    assert calls == [{"id": "call_inline_0", "name": "llm-intents__search_web",
                      "args": {"query": "beste Klasse World of Warcraft"}}]
    assert cleaned == ""


def test_strips_the_raw_call_from_surrounding_text(agent):
    text = 'Ich suche das. web_research{query:<|"|>aktuelle Sportereignisse<|"|>}'
    calls, cleaned = agent._salvage_inline_tool_call(text, _tools("web_research"))
    assert calls and calls[0]["name"] == "web_research"
    assert calls[0]["args"] == {"query": "aktuelle Sportereignisse"}
    assert cleaned == "Ich suche das."


def test_normalises_unquoted_js_style_keys(agent):
    text = 'control_device{entity_id: "light.kitchen", action: "turn_on"}'
    calls, _ = agent._salvage_inline_tool_call(text, _tools("control_device"))
    assert calls[0]["args"] == {"entity_id": "light.kitchen", "action": "turn_on"}


def test_flat_tool_spec_shape_also_supported(agent):
    # Defensive: a flat {"name": ...} tool entry resolves too, not just nested.
    text = 'search_web{query:<|"|>x<|"|>}'
    calls, _ = agent._salvage_inline_tool_call(text, [{"name": "search_web"}])
    assert calls and calls[0]["name"] == "search_web"


def test_undeclared_name_is_not_salvaged(agent):
    text = 'definitely_not_a_tool{query:<|"|>x<|"|>}'
    assert agent._salvage_inline_tool_call(text, _tools("search_web")) == ([], text)


def test_plain_reply_with_braces_is_untouched(agent):
    text = "The set {1, 2, 3} is small, sir."
    assert agent._salvage_inline_tool_call(text, _tools("search_web")) == ([], text)


def test_no_tools_or_no_brace_is_untouched(agent):
    assert agent._salvage_inline_tool_call("hello there", _tools("x")) == ([], "hello there")
    assert agent._salvage_inline_tool_call("search_web{q:1}", None) == (
        [], "search_web{q:1}")
    assert agent._salvage_inline_tool_call("", _tools("x")) == ([], "")


def test_unparseable_body_is_not_salvaged(agent):
    # A declared name but a body that is not a JSON object → leave it alone rather
    # than guess (better spoken-but-wrong than executed-but-wrong).
    text = 'search_web{query: unterminated'
    assert agent._salvage_inline_tool_call(text, _tools("search_web")) == ([], text)
    text2 = 'search_web{<|"|>just a bare string<|"|>}'
    assert agent._salvage_inline_tool_call(text2, _tools("search_web")) == ([], text2)


def test_wiring_present_in_run_agent():
    src = pathlib.Path("custom_components/jarvis/agent.py").read_text()
    # Salvage runs on the no-structured-tool-calls path, before the empty-turn
    # fallback, and feeds its result back into tool_calls/text.
    assert "tool_calls, text = _salvage_inline_tool_call(text, tools)" in src
