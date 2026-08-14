"""Tests for ephemeral sub-agents (delegate_task, v6.83.0).

Reentrancy of run_agent is established by inspection — locals-based message
accumulation, stateless _TOOL_MAP dispatch, no mutable module globals. These
tests cover the delegation machinery around it: capability resolution + the
denylist, tool-subset scoping, the recursion depth cap, and correct nested
invocation wiring (via a spy on run_agent).
"""
import json

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


# ── capability resolution + denylist ─────────────────────────────────────────

def test_resolve_capability_known(agent):
    tools = agent._resolve_capability("scheduling")
    assert "calendar_agenda" in tools and "read_email" in tools


def test_resolve_capability_unknown_is_empty(agent):
    assert agent._resolve_capability("nonsense") == set()
    assert agent._resolve_capability("") == set()


def test_no_group_grants_a_denied_tool(agent):
    for name in agent.CAPABILITY_GROUPS:
        resolved = agent._resolve_capability(name)
        assert not (resolved & agent._SUBAGENT_DENY), f"{name} leaks a denied tool"


def test_denylist_subtracted_even_if_group_lists_it(agent, monkeypatch):
    monkeypatch.setitem(agent.CAPABILITY_GROUPS, "rogue",
                        {"calendar_agenda", "control_device"})
    resolved = agent._resolve_capability("rogue")
    assert "calendar_agenda" in resolved
    assert "control_device" not in resolved          # denylist wins


# ── tool-subset scoping ──────────────────────────────────────────────────────

def test_scoped_tool_list_none_is_full(agent):
    assert len(agent._scoped_tool_list(None)) == len(agent.JARVIS_TOOLS)


def test_scoped_tool_list_filters_to_subset(agent):
    scoped = agent._scoped_tool_list({"read_email", "calendar_agenda"})
    names = {t["function"]["name"] for t in scoped}
    assert names == {"read_email", "calendar_agenda"}


def test_subagent_never_gets_delegate_task(agent):
    # delegate_task is in no capability group and on the denylist → a sub-agent
    # can never re-delegate (belt-and-suspenders with the depth cap).
    for name in agent.CAPABILITY_GROUPS:
        scoped = agent._scoped_tool_list(agent._resolve_capability(name))
        assert "delegate_task" not in {t["function"]["name"] for t in scoped}


# ── _run_delegated wiring ────────────────────────────────────────────────────

@pytest.fixture
def spy_run_agent(agent, monkeypatch):
    calls = []

    async def _fake(hass, **kw):
        calls.append(kw)
        return "sub-agent answer"

    monkeypatch.setattr(agent, "run_agent", _fake)
    return calls


async def _delegate(agent, args, depth=0):
    return await agent._run_delegated(
        None, args, persona="p", provider_name="ollama", api_key="",
        model="gemma4:26b", base_url=None, config={}, depth=depth,
    )


async def test_delegate_happy_path(agent, spy_run_agent):
    out = json.loads(await _delegate(
        agent, {"objective": "check my week", "capability": "scheduling"}))
    assert out["result"] == "sub-agent answer"
    assert len(spy_run_agent) == 1
    kw = spy_run_agent[0]
    assert kw["depth"] == 1                                    # depth incremented
    assert kw["allowed_tools"] == agent._resolve_capability("scheduling")
    assert kw["max_iterations"] <= agent._DELEGATION_MAX_TURNS


async def test_delegate_depth_cap_blocks(agent, spy_run_agent):
    out = json.loads(await _delegate(
        agent, {"objective": "x", "capability": "scheduling"},
        depth=agent.MAX_DELEGATION_DEPTH))
    assert "error" in out and "depth" in out["error"]
    assert spy_run_agent == []                                 # run_agent NOT called


async def test_delegate_requires_objective(agent, spy_run_agent):
    out = json.loads(await _delegate(
        agent, {"objective": "  ", "capability": "scheduling"}))
    assert "error" in out
    assert spy_run_agent == []


async def test_delegate_unknown_capability(agent, spy_run_agent):
    out = json.loads(await _delegate(
        agent, {"objective": "x", "capability": "bogus"}))
    assert "error" in out and "unknown capability" in out["error"]
    assert spy_run_agent == []


async def test_delegate_caps_max_turns(agent, spy_run_agent):
    await _delegate(
        agent, {"objective": "x", "capability": "research", "max_turns": 999})
    assert spy_run_agent[0]["max_iterations"] == agent._DELEGATION_MAX_TURNS


def test_delegate_task_tool_registered_but_not_in_tool_map(agent):
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "delegate_task" in names
    assert "delegate_task" not in agent._TOOL_MAP     # handled inline by design
