"""Epistemic Fabric — Temporal validity (PARITY) on curated knowledge.

`knowledge._emit_temporal_parity` logs how many of the returned curated facts a
temporal-AWARE consumer WOULD caveat as approaching staleness (AGING, or EXPIRED
on a row that slipped the expiry filter) against the current behaviour, which
caveats none. The AGING share is the quantified bar the owner-gated enforce rung
reads before JARVIS starts hedging an aging fact. Observe-only: `all_facts`
returns the same rows whether this runs or not. This mirrors the where-last-seen
temporal parity rung (9.118.0), extending the fabric's parity coverage from the
episodic "last seen" answer to semantic curated knowledge.
"""
import logging
import time

import pytest


@pytest.fixture
def kn(load):
    return load("knowledge")


def _parity_lines(caplog):
    return [r.message for r in caplog.records
            if "temporal(parity):" in r.message and "fact(s)" in r.message]


def test_aging_fact_counts_as_would_caveat(kn, caplog):
    now = time.time()
    facts = [
        {"updated_at": now, "expires_at": now + 10_000},            # fresh
        {"updated_at": now - 800, "expires_at": now - 800 + 1000},  # aging (frac 0.8)
        {"updated_at": now - 100},                                  # durable (no expiry)
    ]
    with caplog.at_level(logging.DEBUG):
        kn._emit_temporal_parity(facts, now)
    lines = _parity_lines(caplog)
    assert lines and "3 fact(s)" in lines[-1]
    assert "would_caveat=1" in lines[-1] and "diverge=True" in lines[-1]


def test_all_fresh_or_durable_no_divergence(kn, caplog):
    now = time.time()
    facts = [
        {"updated_at": now, "expires_at": now + 10_000},  # fresh
        {"updated_at": now - 5},                          # durable
    ]
    with caplog.at_level(logging.DEBUG):
        kn._emit_temporal_parity(facts, now)
    lines = _parity_lines(caplog)
    assert lines and "would_caveat=0" in lines[-1] and "diverge=False" in lines[-1]


def test_expired_fact_also_counts(kn, caplog):
    now = time.time()
    # A row past its ttl (frac >= 1.0) — EXPIRED — still counts toward would_caveat
    # for the rare case one slips the all_facts expiry filter.
    facts = [{"updated_at": now - 2000, "expires_at": now - 1000}]
    with caplog.at_level(logging.DEBUG):
        kn._emit_temporal_parity(facts, now)
    lines = _parity_lines(caplog)
    assert lines and "would_caveat=1" in lines[-1] and "diverge=True" in lines[-1]


def test_kill_switch_silences_parity(kn, monkeypatch, caplog):
    monkeypatch.setattr(kn, "TEMPORAL_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        kn._emit_temporal_parity(
            [{"updated_at": time.time() - 800, "expires_at": time.time()}],
            time.time())
    assert not _parity_lines(caplog)


def test_defensive_on_empty_or_bad_facts(kn, caplog):
    now = time.time()
    with caplog.at_level(logging.DEBUG):
        kn._emit_temporal_parity([], now)
        kn._emit_temporal_parity([None, "nope", 3], now)
        kn._emit_temporal_parity(None, now)
    assert not _parity_lines(caplog)
