"""Phase S (enforce) — the self-report is sourced from the model with its
confabulations removed.

When the ground truth is supplied (enforce on), `agent._project_self_model`
demotes a capability whose switch is enabled but whose backing module does not
resolve to *unavailable* — so `cognitive_status` cannot report a capability JARVIS
cannot actually perform. Read-only and strictly tightening; carries no authority.
These tests pin the demotion, the fail-safe (no ground truth → legacy), and the
kill-switches. The parity comparison itself stays covered by test_self_model_parity.
"""
import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def _caps(agent, switches, ground_truth=None):
    sm = agent._project_self_model(
        switches=switches, goals_rows=[], situation_labels=[],
        ground_truth=ground_truth)
    return sm.to_dict()["capabilities"], sm.to_dict().get("limits", [])


def _usable(caps, name):
    for c in caps:
        if c.get("name") == name:
            return bool(c.get("usable"))
    raise AssertionError(f"{name} not in caps")


def test_enforce_demotes_confabulated_capability(agent):
    # switch is on but ground truth says the module is not really performable
    caps, limits = _caps(
        agent, [{"key": "knowledge_graph", "enabled": True, "category": "capability"}],
        ground_truth={"knowledge_graph": False})
    assert _usable(caps, "knowledge_graph") is False
    assert "knowledge_graph" in limits


def test_enforce_keeps_genuinely_available_capability(agent):
    caps, limits = _caps(
        agent, [{"key": "authority", "enabled": True, "category": "safety"}],
        ground_truth={"authority": True})
    assert _usable(caps, "authority") is True
    assert "authority" not in limits


def test_enforce_absent_from_ground_truth_is_not_demoted(agent):
    # a name we could not check is left to the switch alone (nothing to demote on)
    caps, _ = _caps(
        agent, [{"key": "a", "enabled": True, "category": "x"}],
        ground_truth={"b": False})
    assert _usable(caps, "a") is True


def test_no_ground_truth_is_legacy_behaviour(agent):
    # ground_truth=None → switch-only projection (today's parity behaviour)
    caps, _ = _caps(
        agent, [{"key": "a", "enabled": True, "category": "x"}], ground_truth=None)
    assert _usable(caps, "a") is True


def test_disabled_switch_stays_unavailable(agent):
    # an off switch is unavailable regardless of ground truth
    caps, limits = _caps(
        agent, [{"key": "a", "enabled": False, "category": "x"}],
        ground_truth={"a": True})
    assert _usable(caps, "a") is False
    assert "a" in limits


# ── kill-switches ────────────────────────────────────────────────────────────
def test_enforce_flag_default_on(agent):
    assert agent.SELF_MODEL_ENFORCE is True
    assert agent._self_model_enforce_enabled() is True


def test_enforce_kill_switch_module_flag(agent, monkeypatch):
    monkeypatch.setattr(agent, "SELF_MODEL_ENFORCE", False)
    assert agent._self_model_enforce_enabled() is False


def test_enforce_kill_switch_config_key(agent, monkeypatch):
    import jc.jarvis_config as cfg
    monkeypatch.setattr(
        cfg, "get",
        lambda key, default=None: False if key == "self_model_enforce" else default)
    assert agent._self_model_enforce_enabled() is False


def test_ground_truth_never_raises(agent):
    # the helper is defensive — it returns a dict (possibly empty), never raises
    assert isinstance(agent._self_model_ground_truth(), dict)
