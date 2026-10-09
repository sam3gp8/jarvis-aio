"""Phase S (shadow) — self-model projection on the cognitive-status read.

`agent._project_self_model` turns live-read rows (enforcement switches, active
goals, open situations) into a `kernel.self_model.SelfModel`, which
`_exec_cognitive_status` logs and surfaces for introspection. Observe-only; the
model only *describes* — naming a capability never grants it."""
import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def test_projection_maps_switches_to_capabilities(agent):
    sm = agent._project_self_model(
        switches=[
            {"key": "authority", "enabled": True, "category": "safety"},
            {"key": "knowledge_graph", "enabled": True, "category": "capability"},
            {"key": "continuity_resume", "enabled": False, "category": "capability"},
        ],
        goals_rows=[],
        situation_labels=[],
    )
    # 2 of 3 active → confidence 2/3; the disabled one is a stated limit.
    assert sm.can("authority") is True
    assert sm.can("knowledge_graph") is True
    assert sm.can("continuity_resume") is False      # disabled → unavailable
    assert "continuity_resume" in sm.limits
    assert abs(sm.confidence - (2 / 3)) < 1e-9


def test_projection_collects_commitments(agent):
    sm = agent._project_self_model(
        switches=[],
        goals_rows=[{"title": "dim lights at sunset"}, {"outcome": "restock coffee"}],
        situation_labels=["hazard · freezer", ""],   # blank dropped
    )
    assert "goal: dim lights at sunset" in sm.commitments
    assert "goal: restock coffee" in sm.commitments
    assert "situation: hazard · freezer" in sm.commitments
    assert len(sm.commitments) == 3


def test_projection_is_defensive_on_junk_rows(agent):
    sm = agent._project_self_model(
        switches=["not-a-dict", {"enabled": True}],   # 2nd has no key → dropped
        goals_rows=["nope", {"title": ""}],           # both dropped
        situation_labels=None,
    )
    assert sm.capabilities == ()
    assert sm.commitments == ()
    assert sm.confidence == 1.0                        # no capabilities → 1.0


def test_projection_never_grants_describing_is_not_having(agent):
    # A present-but-disabled capability is described but never reported usable.
    sm = agent._project_self_model(
        switches=[{"key": "arm_alarm", "enabled": False, "category": "safety"}],
        goals_rows=[], situation_labels=[])
    assert sm.capability("arm_alarm") is not None      # described
    assert sm.can("arm_alarm") is False                # but not usable
    assert sm.confidence == 0.0                         # 0 of 1 active


def test_shadow_flag_enabled_by_default(agent):
    assert agent.SELF_MODEL_SHADOW is True
