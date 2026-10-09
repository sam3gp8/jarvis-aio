"""Kernel Phase O (shadow) — agency orchestration dry-run in the delegation path.

When JARVIS delegates to a sub-agent, `agent._emit_agency_shadow` dry-runs the
equivalent `kernel.agency` spawn and logs whether the kernel agrees the spawn is
legal (capabilities within the parent, depth within MAX_DELEGATION_DEPTH).
Observe-only; the real delegation path is untouched."""
import logging

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def test_agency_shadow_logs_legal_spawn(agent, caplog):
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_shadow("FRIDAY", {"control_device", "bulk_control"}, 0)
    msgs = [r.message for r in caplog.records if "agency(shadow):" in r.message]
    assert msgs, "expected an agency(shadow) log line"
    last = msgs[-1]
    # a depth-0 delegation of a non-empty tool set is legal: child depth 1,
    # everything granted (parent holds '*'), nothing dropped.
    assert "child=friday" in last and "depth=1" in last
    assert "caps=2" in last and "granted=2" in last and "dropped=0" in last
    assert "ok=True" in last


def test_agency_shadow_flags_depth_overflow(agent, caplog):
    # a sub-agent (depth == MAX_DELEGATION_DEPTH) can't delegate further; the
    # kernel reports the spawn illegal, mirroring the incumbent's depth guard.
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_shadow("sub", {"get_entity_state"},
                                  agent.MAX_DELEGATION_DEPTH)
    last = [r.message for r in caplog.records if "agency(shadow):" in r.message][-1]
    assert "ok=False" in last and "depth" in last


def test_agency_shadow_kill_switch(agent, monkeypatch, caplog):
    monkeypatch.setattr(agent, "AGENCY_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_shadow("FRIDAY", {"control_device"}, 0)
    assert not any("agency(shadow)" in r.message for r in caplog.records)


# ── Phase O (parity): kernel verdict vs the incumbent delegation decision ─────

def test_agency_parity_agrees_on_proceed(agent, caplog):
    # incumbent proceeds; kernel would also allow → agree.
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_parity("FRIDAY", {"control_device"}, 0,
                                  incumbent_proceeded=True, note="proceed")
    last = [r.message for r in caplog.records if "agency(parity):" in r.message][-1]
    assert "kernel_ok=True" in last and "incumbent_proceeded=True" in last
    assert "agree=True" in last


def test_agency_parity_agrees_on_depth_refuse(agent, caplog):
    # incumbent refuses on depth; the kernel refuses on depth too → agree.
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_parity("sub", ("*",), agent.MAX_DELEGATION_DEPTH,
                                  incumbent_proceeded=False, note="depth-guard")
    last = [r.message for r in caplog.records if "agency(parity):" in r.message][-1]
    assert "kernel_ok=False" in last and "agree=True" in last


def test_agency_parity_diverges_on_disabled_profile(agent, caplog):
    # a disabled FRIDAY: incumbent refuses, but the kernel (knowing only the
    # declared tool set) would allow it → the expected DIVERGENCE the enforce
    # rung must close by sitting behind the _profile_enabled gate.
    tools = agent.AGENT_PROFILES["FRIDAY"]["tools"]
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_parity("FRIDAY", tools, 0,
                                  incumbent_proceeded=False, note="profile-refused")
    last = [r.message for r in caplog.records if "agency(parity):" in r.message][-1]
    assert "kernel_ok=True" in last and "incumbent_proceeded=False" in last
    assert "agree=False" in last and "profile-refused" in last


def test_agency_parity_kill_switch(agent, monkeypatch, caplog):
    monkeypatch.setattr(agent, "AGENCY_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        agent._emit_agency_parity("FRIDAY", {"control_device"}, 0,
                                  incumbent_proceeded=True)
    assert not any("agency(parity)" in r.message for r in caplog.records)
