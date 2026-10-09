"""Epistemic Fabric — Temporal validity (shadow) on the "where did I last see X"
answer path.

`agent._emit_where_last_seen_temporal_shadow` logs the freshness band (fresh /
aging / expired) of the scene sighting the tool surfaces, via `kernel.temporal`,
against `WHERE_LAST_SEEN_TTL`. Observe-only: the tool's JSON answer is unchanged;
this only quantifies how stale the "last seen" answer is — the signal a future
hedge would use."""
import logging
import time

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def test_fresh_sighting_logs_fresh_band(agent, caplog):
    hit = {"camera": "camera.kitchen", "ts": time.time(), "description": "keys"}
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_shadow(hit)
    line = [r.message for r in caplog.records
            if "temporal(shadow): where-last-seen" in r.message]
    assert line and "fresh" in line[-1] and "camera.kitchen" in line[-1]


def test_stale_sighting_logs_expired_band(agent, caplog):
    # Observed well past 2× the ttl → EXPIRED (should be re-confirmed).
    hit = {"camera": "camera.garage",
           "ts": time.time() - 2 * agent.WHERE_LAST_SEEN_TTL,
           "description": "bike"}
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_shadow(hit)
    line = [r.message for r in caplog.records
            if "temporal(shadow): where-last-seen" in r.message]
    assert line and "expired" in line[-1]


def test_kill_switch_silences_shadow(agent, monkeypatch, caplog):
    monkeypatch.setattr(agent, "WHERE_LAST_SEEN_TEMPORAL_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_shadow(
            {"camera": "c", "ts": time.time()})
    assert not [r for r in caplog.records
                if "temporal(shadow): where-last-seen" in r.message]


def test_defensive_on_missing_ts_or_bad_hit(agent, caplog):
    # No ts, and a non-dict, must never raise and must emit nothing.
    with caplog.at_level(logging.DEBUG):
        agent._emit_where_last_seen_temporal_shadow({"camera": "c"})
        agent._emit_where_last_seen_temporal_shadow(None)
    assert not [r for r in caplog.records
                if "temporal(shadow): where-last-seen" in r.message]
