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
