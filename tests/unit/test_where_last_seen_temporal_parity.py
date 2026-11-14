"""Epistemic Fabric — Temporal validity (PARITY) on the "where did I last see X"
answer path.

The shadow rung (`_emit_where_last_seen_temporal_shadow`) logs how stale the
surfaced sighting is. The parity rung (`_emit_where_last_seen_temporal_parity`)
logs what a temporal-AWARE answer WOULD do about it — whether it would hedge the
sighting as stale — against the current behaviour, which always answers flat and
never hedges. Observe-only: the tool's JSON answer is unchanged; this only
quantifies the divergence rate the owner-gated enforce step reads before a real
hedge turns on. The hedge wording lives in a pure helper reused by that enforce
rung so the parity log and the eventual real caveat agree exactly.
"""
import logging
import time

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def _parity_lines(caplog):
    return [r.message for r in caplog.records
            if "temporal(parity): where-last-seen" in r.message]


def test_fresh_sighting_would_not_hedge(agent, caplog):
    hit = {"camera": "camera.kitchen", "ts": time.time(), "description": "keys"}
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_parity(hit)
    lines = _parity_lines(caplog)
    assert lines and "band=fresh" in lines[-1]
    assert "would_hedge=False" in lines[-1] and "diverge=False" in lines[-1]
    assert "camera.kitchen" in lines[-1]


def test_aging_sighting_would_hedge(agent, caplog):
    # Past the fresh window but not yet expired → AGING → the temporal answer hedges.
    hit = {"camera": "camera.hall",
           "ts": time.time() - 0.75 * agent.WHERE_LAST_SEEN_TTL}
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_parity(hit)
    lines = _parity_lines(caplog)
    assert lines and "band=aging" in lines[-1]
    assert "would_hedge=True" in lines[-1] and "diverge=True" in lines[-1]


def test_expired_sighting_would_hedge(agent, caplog):
    hit = {"camera": "camera.garage",
           "ts": time.time() - 2 * agent.WHERE_LAST_SEEN_TTL}
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_parity(hit)
    lines = _parity_lines(caplog)
    assert lines and "band=expired" in lines[-1]
    assert "would_hedge=True" in lines[-1] and "diverge=True" in lines[-1]


def test_hedge_suffix_is_pure_and_bounded(agent):
    # Fresh / durable → no caveat; stale → a caveat with a coarse human age.
    assert agent._temporal_hedge_suffix("fresh", 100) == ""
    assert agent._temporal_hedge_suffix("durable", 10 ** 9) == ""
    aging = agent._temporal_hedge_suffix("aging", 4 * 3600)
    assert "may have changed" in aging and "4h ago" in aging
    expired = agent._temporal_hedge_suffix("expired", 36 * 3600)
    assert "out of date" in expired and "re-check" in expired
    # Minutes for a sub-hour age; never raises on junk.
    assert "m ago" in agent._temporal_hedge_suffix("aging", 300)
    assert agent._temporal_hedge_suffix("aging", "junk") == ""


def test_kill_switch_silences_parity(agent, monkeypatch, caplog):
    monkeypatch.setattr(agent, "WHERE_LAST_SEEN_TEMPORAL_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_parity(
            {"camera": "c", "ts": time.time()})
    assert not _parity_lines(caplog)


def test_defensive_on_missing_ts_or_bad_hit(agent, caplog):
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_parity({"camera": "c"})
        agent._emit_where_last_seen_temporal_parity(None)
    assert not _parity_lines(caplog)
