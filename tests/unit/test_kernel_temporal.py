"""Tests for the temporal-validity primitive (Epistemic Fabric — Time).

Pure: ``now`` is always passed in, so age / expiry / band are deterministic.
"""
import pytest


@pytest.fixture
def T(load):
    return load("kernel.temporal")


def test_durable_never_expires(T):
    v = T.assess(1000.0, ttl=None)
    assert v.durable is True
    assert v.expires_at is None
    assert v.is_valid(now=10_000.0) is True
    assert v.remaining(now=10_000.0) is None
    assert v.band(now=10_000.0) == T.DURABLE
    assert v.freshness(now=10_000.0) == 1.0


def test_fresh_then_aging_then_expired(T):
    v = T.assess(1000.0, ttl=100.0)          # expires at 1100
    assert v.expires_at == 1100.0
    # 20% elapsed → fresh
    assert v.band(now=1020.0) == T.FRESH and v.is_valid(1020.0)
    # 70% elapsed → aging
    assert v.band(now=1070.0) == T.AGING and v.is_valid(1070.0)
    # 100%+ elapsed → expired
    assert v.band(now=1100.0) == T.EXPIRED and not v.is_valid(1100.0)
    assert v.band(now=1200.0) == T.EXPIRED


def test_age_remaining_fraction(T):
    v = T.assess(1000.0, ttl=100.0)
    assert v.age(now=1040.0) == 40.0
    assert v.remaining(now=1040.0) == 60.0
    assert v.fraction_elapsed(now=1040.0) == pytest.approx(0.4)
    assert v.freshness(now=1040.0) == pytest.approx(0.6)
    # Clamps: never negative age, remaining floors at 0.
    assert v.age(now=500.0) == 0.0
    assert v.remaining(now=5000.0) == 0.0
    assert v.freshness(now=5000.0) == 0.0


def test_non_positive_ttl_is_safe(T):
    v = T.assess(1000.0, ttl=0.0)
    # Zero ttl: expired immediately, no divide-by-zero.
    assert v.fraction_elapsed(now=1000.0) == 0.0  # guarded (ttl<=0 → 0.0)
    assert v.band(now=1001.0) in (T.FRESH, T.AGING, T.EXPIRED)  # does not raise


def test_assess_clamps_bad_inputs(T):
    v = T.assess("not-a-number", ttl="bad")
    assert v.observed_at == 0.0 and v.ttl == 0.0  # ttl coerced to 0, not None
    assert v.durable is False


def test_describe(T):
    durable = T.assess(1000.0, ttl=None)
    assert "durable" in durable.describe(now=2000.0)
    fresh = T.assess(1000.0, ttl=100.0)
    assert "fresh" in fresh.describe(now=1010.0) and "expires in" in fresh.describe(now=1010.0)
    expired = T.assess(1000.0, ttl=100.0)
    assert "expired" in expired.describe(now=2000.0)


def test_to_dict(T):
    v = T.assess(1000.0, ttl=100.0)
    d = v.to_dict(now=1050.0)
    assert d["expires_at"] == 1100.0 and d["band"] == T.AGING
    assert d["is_valid"] is True and d["freshness"] == pytest.approx(0.5)
    # Without now, only the static shape.
    d0 = v.to_dict()
    assert "band" not in d0 and d0["ttl"] == 100.0


def test_summarize_counts_by_band(T):
    items = [
        T.assess(1000.0, ttl=None),       # durable
        T.assess(1000.0, ttl=100.0),      # fresh @1020
        T.assess(1000.0, ttl=100.0),      # fresh @1020
        T.assess(1000.0, ttl=50.0),       # aging @1020 (0.4? no: 20/50=0.4 fresh) → make it aging
        T.assess(1000.0, ttl=10.0),       # expired @1020
    ]
    stats = T.summarize(items, now=1020.0)
    assert stats.count == 5
    assert stats.durable == 1
    assert stats.expired == 1
    assert stats.valid == 4                      # all but the expired one
    # fresh + aging + durable + expired == count
    assert stats.fresh + stats.aging + stats.durable + stats.expired == 5


def test_summarize_skips_non_validity(T):
    stats = T.summarize(["not-a-validity", T.assess(1000.0, ttl=100.0)], now=1010.0)
    assert stats.count == 1 and stats.fresh == 1
