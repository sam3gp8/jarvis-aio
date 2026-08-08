"""Static guards on the agent system prompt (v6.69.1).

The prompt is assembled as an f-string in agent.py; these read the source and
pin the sections that must survive future prompt edits — most importantly the
reasoning-methodology block ("## How you reason"), which encodes the
investigate→verify→act discipline. If someone reworks the prompt and drops it,
this fails CI instead of silently degrading JARVIS's reasoning behavior."""
import pathlib

AGENT_SRC = (pathlib.Path(__file__).resolve().parents[2]
             / "custom_components" / "jarvis" / "agent.py").read_text()


def test_prompt_has_reasoning_methodology_section():
    assert "## How you reason" in AGENT_SRC


def test_reasoning_section_encodes_core_discipline():
    # the load-bearing tenets, pinned individually
    for tenet in (
        "INVESTIGATE before concluding",
        "OBSERVE",                      # observation vs inference distinction
        "INFER",
        "VERIFY",                       # verify before consequential action
        "CONFIRM the result",           # post-action verification
        "fail safe",                    # consequential uncertainty → safe side
        "say so plainly",               # honest gaps over invention
    ):
        assert tenet in AGENT_SRC, f"reasoning tenet missing from prompt: {tenet}"


def test_reasoning_section_precedes_critical_rules():
    # methodology frames the rules, so it must come first in the prompt
    assert AGENT_SRC.index("## How you reason") < AGENT_SRC.index("## Critical rules")


def test_prompt_keeps_entity_search_rule():
    # rule 1 (search, never guess entity_ids) predates the methodology section
    # and must survive alongside it
    assert "Never guess entity_ids" in AGENT_SRC


def test_prompt_has_questions_are_not_commands_rule():
    # v6.70.2: questions about the past must not trigger actions
    assert "Questions are not commands" in AGENT_SRC
    assert "A question about a device is NOT a request to change it" in AGENT_SRC
    # must point at the real mechanism for answering "when did X turn on"
    assert "last_changed" in AGENT_SRC


def test_get_entity_state_returns_last_changed():
    # the rule tells the model to read last_changed; the tool must actually
    # return it, or the instruction is hollow
    assert '"last_changed"' in AGENT_SRC
    assert "state.last_changed.isoformat()" in AGENT_SRC


def test_prompt_routes_weather_time_questions_to_forecast():
    # v6.75.0: "what time is it supposed to rain" must go to the forecast, not
    # the clock (the model was answering with the current time)
    assert "'What time' is not always the clock" in AGENT_SRC
    assert "weather_forecast" in AGENT_SRC
    assert "NEVER" in AGENT_SRC and "current clock time" in AGENT_SRC


def test_weather_forecast_tool_registered():
    assert '"name": "weather_forecast"' in AGENT_SRC
    assert '"weather_forecast":' in AGENT_SRC          # in _TOOL_MAP
    assert "_exec_weather_forecast" in AGENT_SRC
