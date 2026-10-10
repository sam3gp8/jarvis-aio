"""Phase S (parity) — self-report vs ground truth.

`agent._self_model_parity` compares the self-model's reported capability
availability (each capability's `usable` flag) against an independent ground
truth (the switch is live-enabled AND its backing module resolves). It isolates
the one thing the enforce rung must forbid — the model reporting a capability
*available* that JARVIS cannot actually perform (confabulation). Observe-only.
"""
import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def _caps(agent, switches):
    """Project switches into the self-model, return its capability dicts."""
    return agent._project_self_model(
        switches=switches, goals_rows=[], situation_labels=[]).to_dict()["capabilities"]


def test_parity_agrees_when_report_matches_truth(agent):
    caps = _caps(agent, [
        {"key": "authority", "enabled": True, "category": "safety"},
        {"key": "continuity_resume", "enabled": False, "category": "capability"},
    ])
    gt = {"authority": True, "continuity_resume": False}
    assert agent._self_model_parity(caps, gt) == (2, 2, 0, 0)


def test_parity_flags_confabulation(agent):
    # Model reports it available, but ground truth says it isn't really performable.
    caps = _caps(agent, [{"key": "knowledge_graph", "enabled": True, "category": "capability"}])
    assert agent._self_model_parity(caps, {"knowledge_graph": False}) == (1, 0, 1, 0)


def test_parity_flags_underreport(agent):
    # Model reports unavailable, but ground truth says it is available.
    caps = _caps(agent, [{"key": "foo", "enabled": False, "category": "capability"}])
    assert agent._self_model_parity(caps, {"foo": True}) == (1, 0, 0, 1)


def test_parity_ignores_keys_absent_from_ground_truth(agent):
    caps = _caps(agent, [{"key": "a", "enabled": True, "category": "x"}])
    checked, agree, report_only, truth_only = agent._self_model_parity(caps, {"b": True})
    assert checked == 0 and (agree, report_only, truth_only) == (0, 0, 0)


def test_parity_defensive_on_empty(agent):
    assert agent._self_model_parity(None, None) == (0, 0, 0, 0)
    assert agent._self_model_parity([], {"x": True}) == (0, 0, 0, 0)


def test_parity_flag_enabled_by_default(agent):
    assert agent.SELF_MODEL_PARITY is True
