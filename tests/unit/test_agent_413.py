"""413 'request too large' detection + slim-retry prompt shrinking."""
import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def test_detects_groq_413(agent):
    exc = Exception("Error code: 413 - {'error': {'message': 'Request too large "
                    "for model `openai/gpt-oss-120b` in organization `org_x` "
                    "service tier `on_demand`'}}")
    assert agent._is_too_large(exc) is True


def test_detects_context_length_variants(agent):
    assert agent._is_too_large(Exception("maximum context length exceeded"))
    assert agent._is_too_large(Exception("This prompt is too long"))
    assert agent._is_too_large(Exception("HTTP 413 Payload Too Large"))


def test_does_not_false_positive(agent):
    # a bare token count containing 413 is NOT a size error
    assert agent._is_too_large(Exception("completed using 413 tokens")) is False
    # connectivity / model errors are handled elsewhere
    assert agent._is_too_large(Exception("Connection timed out")) is False
    assert agent._is_too_large(Exception("model not found")) is False


def test_strip_home_state_removes_block_keeps_rest(agent):
    sp = ("You are JARVIS.\n\n"
          "## Current home state\n"
          "Areas: Kitchen, Office\nLights: a, b, c\nSwitches: x, y\n\n"
          "## Tools\nUse control_device.\n")
    out = agent._strip_home_state(sp)
    assert "Areas: Kitchen" not in out and "Lights: a, b, c" not in out
    assert "## Tools\nUse control_device." in out          # later section intact
    assert "You are JARVIS." in out                         # earlier section intact
    assert "search_entities" in out                         # pointer added


def test_strip_home_state_no_marker_is_unchanged(agent):
    sp = "You are JARVIS.\n\n## Tools\nUse control_device.\n"
    assert agent._strip_home_state(sp) == sp


def test_strip_home_state_as_last_section(agent):
    sp = "You are JARVIS.\n\n## Current home state\nLights: a, b, c\n"
    out = agent._strip_home_state(sp)
    assert "Lights: a, b, c" not in out and "You are JARVIS." in out


def test_gemini_thought_signature_salvaged_as_tool_error(agent):
    # routed through the 'answer without tools' salvage, NOT the offline path
    exc = Exception("400 - Function call is missing a thought_signature in "
                    "functionCall parts. This is required for tools to work correctly.")
    assert agent._is_tool_format_error(exc) is True
    # and it must not be misread as connectivity (which would trip the breaker)
    assert agent._is_connectivity_error(exc) is False


def test_slim_tools_are_a_small_valid_subset(agent):
    import json
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert agent._SLIM_TOOLS <= names                      # all exist
    full = agent._scoped_tool_list(None)
    slim = agent._scoped_tool_list(agent._SLIM_TOOLS)
    assert len(slim) == len(agent._SLIM_TOOLS)
    # the slim schema must be dramatically smaller — the whole point of the
    # 413 retry is a request that actually fits a size-limited tier
    assert len(json.dumps(slim)) * 3 < len(json.dumps(full))
