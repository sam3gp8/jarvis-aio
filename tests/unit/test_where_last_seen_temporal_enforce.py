"""Epistemic Fabric — Temporal validity (ENFORCE) on the where-last-seen answer.

Owner-flipped (9.120.0). `agent._apply_where_last_seen_temporal_enforce` adds the
staleness caveat to the tool result when the surfaced sighting is aging/expired,
so JARVIS's answer HEDGES a stale sighting ("…though that was 3h ago, so it may
have changed") instead of stating it flat. Fresh/durable sightings are unchanged.
Downward-only honesty, fail-safe to the flat result, kill-switchable. The caveat
wording is the same pure `_temporal_hedge_suffix` the parity rung measured, so the
"would hedge" and the real hedge read identically.
"""
import time

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def test_expired_sighting_gets_caveat(agent):
    result = {"found": True, "camera": "camera.garage", "description": "bike"}
    hit = {"camera": "camera.garage",
           "ts": time.time() - 2 * agent.WHERE_LAST_SEEN_TTL}
    agent._apply_where_last_seen_temporal_enforce(result, hit)
    assert result.get("is_stale") is True
    assert "out of date" in result.get("staleness_note", "")
    # The caveat is a clean clause, not the sentence-appending suffix form.
    assert not result["staleness_note"].startswith("—")


def test_aging_sighting_gets_caveat(agent):
    result = {"found": True, "camera": "camera.hall"}
    hit = {"ts": time.time() - 0.75 * agent.WHERE_LAST_SEEN_TTL}
    agent._apply_where_last_seen_temporal_enforce(result, hit)
    assert result.get("is_stale") is True
    assert "may have changed" in result.get("staleness_note", "")


def test_fresh_sighting_unchanged(agent):
    result = {"found": True, "camera": "camera.kitchen", "description": "keys"}
    hit = {"ts": time.time()}
    agent._apply_where_last_seen_temporal_enforce(result, hit)
    assert "is_stale" not in result and "staleness_note" not in result


def test_kill_switch_reverts_to_flat(agent, monkeypatch):
    monkeypatch.setattr(agent, "WHERE_LAST_SEEN_TEMPORAL_ENFORCE", False)
    result = {"found": True}
    hit = {"ts": time.time() - 2 * agent.WHERE_LAST_SEEN_TTL}
    agent._apply_where_last_seen_temporal_enforce(result, hit)
    assert "is_stale" not in result and "staleness_note" not in result


def test_defensive_on_missing_ts_or_bad_hit(agent):
    result = {"found": True}
    agent._apply_where_last_seen_temporal_enforce(result, {"camera": "c"})
    agent._apply_where_last_seen_temporal_enforce(result, None)
    assert "is_stale" not in result and "staleness_note" not in result


async def test_exec_where_last_seen_hedges_stale_end_to_end(agent, monkeypatch):
    """The tool itself carries the caveat for a stale hit, flat for a fresh one."""
    import json
    import sys
    import types

    from fakes import FakeHass

    stale_hit = {"camera": "camera.garage", "description": "the bike",
                 "ts": time.time() - 2 * agent.WHERE_LAST_SEEN_TTL}

    sm = types.ModuleType("jc.vision.scene_memory")
    sm.where_last_seen = lambda term: stale_hit
    vision_pkg = sys.modules.get("jc.vision") or types.ModuleType("jc.vision")
    vision_pkg.scene_memory = sm
    monkeypatch.setitem(sys.modules, "jc.vision", vision_pkg)
    monkeypatch.setitem(sys.modules, "jc.vision.scene_memory", sm)

    out = json.loads(await agent._exec_where_last_seen(FakeHass(), {"term": "bike"}))
    assert out["found"] is True and out["is_stale"] is True
    assert "out of date" in out["staleness_note"]
