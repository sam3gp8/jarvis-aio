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
