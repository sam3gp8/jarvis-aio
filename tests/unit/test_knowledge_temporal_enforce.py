"""Epistemic Fabric — Temporal validity (ENFORCE) on curated knowledge.

Owner-flipped (9.121.0). `knowledge._temporal_fact_marker` marks an AGING/EXPIRED
curated fact as "(may be out of date)" in the "What you know" recall block, so
JARVIS caveats a stale fact when it recalls it instead of stating it as current.
A fresh/durable fact is rendered exactly as before. This is the semantic
counterpart to the where-last-seen enforce (9.120.0); its parity rung (9.119.0)
measured the divergence first. Downward-only, fail-safe, kill-switchable.
"""
import time

import pytest


@pytest.fixture
def kn(load):
    return load("knowledge")


def _fact(key, value, *, updated_at, expires_at=None, subject="household",
          source="stated", confidence=1.0):
    return {"subject": subject, "key": key, "value": value, "source": source,
            "confidence": confidence, "updated_at": updated_at,
            "expires_at": expires_at}


def test_marker_on_aging_fact(kn):
    now = time.time()
    f = _fact("wifi", "pw", updated_at=now - 800, expires_at=now - 800 + 1000)  # frac 0.8
    assert kn._temporal_fact_marker(f, now) == " (may be out of date)"


def test_marker_on_expired_fact(kn):
    now = time.time()
    f = _fact("code", "1234", updated_at=now - 2000, expires_at=now - 1000)  # frac > 1
    assert kn._temporal_fact_marker(f, now) == " (may be out of date)"


def test_no_marker_on_fresh_fact(kn):
    now = time.time()
    f = _fact("wifi", "pw", updated_at=now, expires_at=now + 10_000)
    assert kn._temporal_fact_marker(f, now) == ""


def test_no_marker_on_durable_fact(kn):
    now = time.time()
    f = _fact("name", "Sam", updated_at=now - 10 ** 6)  # no expiry → durable
    assert kn._temporal_fact_marker(f, now) == ""


def test_kill_switch_reverts(kn, monkeypatch):
    monkeypatch.setattr(kn, "TEMPORAL_ENFORCE", False)
    now = time.time()
    f = _fact("wifi", "pw", updated_at=now - 800, expires_at=now)  # aging
    assert kn._temporal_fact_marker(f, now) == ""


def test_defensive_on_bad_fact(kn):
    now = time.time()
    assert kn._temporal_fact_marker({"updated_at": "junk"}, now) == ""
    assert kn._temporal_fact_marker({}, now) == ""   # no updated_at → durable → ""


def test_format_block_marks_only_the_stale_fact(kn):
    now = time.time()
    facts = [
        _fact("name", "Sam", updated_at=now),                                     # durable
        _fact("wifi", "pw", updated_at=now - 800, expires_at=now - 800 + 1000),   # aging
    ]
    block = kn._format_block(facts)
    assert "- wifi: pw (may be out of date)" in block
    # The fresh/durable fact is rendered flat, no staleness marker.
    name_line = [ln for ln in block.splitlines() if "name: Sam" in ln][0]
    assert "may be out of date" not in name_line
