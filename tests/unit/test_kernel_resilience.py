"""Phase Z (pure) — resilient compute federation policy.

`kernel/resilience.py` is a pure, local-first, offline-safe fallback policy over
compute tiers. The load-bearing invariant is the HAOS boundary: a canonical-state
tier can never be an offload target, and the chooser fails safe to the
local/canonical tier rather than raising.
"""
import pytest


@pytest.fixture
def R(load):
    return load("kernel.resilience")


def _tier(R, name, kind="local", health="healthy", latency=0.0, quality=1.0, canonical=False):
    return R.Tier(name=name, kind=kind, health=health, latency_ms=latency,
                  quality=quality, canonical=canonical)


def test_tier_coerces_bad_values(R):
    t = R.Tier(name="  x ", kind="nonsense", health="weird", latency_ms=-5, quality=9)
    assert t.name == "x" and t.kind == R.LOCAL and t.health == R.HEALTHY
    assert t.latency_ms == 0.0 and t.quality == 1.0


def test_choose_is_local_first(R):
    tiers = [_tier(R, "cloud", "cloud", latency=10),
             _tier(R, "local", "local", latency=50),
             _tier(R, "edge", "edge", latency=5)]
    # Even though cloud/edge are faster, LOCAL wins the local-first preference.
    assert R.choose(tiers).name == "local"


def test_choose_skips_down_tiers(R):
    tiers = [_tier(R, "local", "local", health="down"),
             _tier(R, "edge", "edge", health="healthy")]
    assert R.choose(tiers).name == "edge"


def test_choose_respects_quality_floor(R):
    tiers = [_tier(R, "local", "local", quality=0.2),
             _tier(R, "cloud", "cloud", quality=0.9)]
    # LOCAL can't meet the quality need → fall through to the capable cloud tier.
    assert R.choose(tiers, need_quality=0.5).name == "cloud"


def test_choose_fail_safe_keeps_brain_home_when_all_down(R):
    tiers = [_tier(R, "cloud", "cloud", health="down"),
             _tier(R, "home", "local", health="down", canonical=True)]
    # Nothing usable → fall back to the canonical tier, never None/raise.
    assert R.choose(tiers).name == "home"


def test_choose_empty_is_none(R):
    assert R.choose([]) is None
    assert R.choose(None) is None


def test_canonical_tier_is_never_offloadable(R):
    canonical_local = _tier(R, "home", "local", canonical=True)
    canonical_cloud = _tier(R, "mirror", "cloud", canonical=True)   # even a cloud canonical
    assert R.can_offload(canonical_local) is False
    assert R.can_offload(canonical_cloud) is False
    assert R.can_offload(_tier(R, "helper", "edge")) is True
    assert R.can_offload(_tier(R, "box", "local")) is False         # local is home, not a target
    assert R.can_offload(None) is False


def test_offload_candidates_excludes_canonical_and_down(R):
    tiers = [_tier(R, "home", "local", canonical=True),
             _tier(R, "edge", "edge"),
             _tier(R, "cloud", "cloud", health="down"),
             _tier(R, "cloud2", "cloud", quality=0.9)]
    names = [t.name for t in R.offload_candidates(tiers)]
    assert "home" not in names and "cloud" not in names
    assert set(names) == {"edge", "cloud2"}


def test_is_offline_safe(R):
    assert R.is_offline_safe([_tier(R, "local", "local")]) is True
    assert R.is_offline_safe([_tier(R, "home", "cloud", canonical=True)]) is True
    assert R.is_offline_safe([_tier(R, "cloud", "cloud")]) is False      # no local/canonical
    assert R.is_offline_safe([_tier(R, "local", "local", health="down")]) is False


def test_degrade_order_local_first_then_latency(R):
    tiers = [_tier(R, "cloud", "cloud", latency=5),
             _tier(R, "edge", "edge", latency=20),
             _tier(R, "local", "local", latency=100),
             _tier(R, "dead", "edge", health="down")]
    order = [t.name for t in R.degrade_order(tiers)]
    assert order == ["local", "edge", "cloud"]   # 'dead' excluded (not usable)


def test_summarize(R):
    tiers = [_tier(R, "home", "local", canonical=True),
             _tier(R, "edge", "edge"),
             _tier(R, "cloud", "cloud", health="down")]
    s = R.summarize(tiers)
    assert s["tiers"] == 3 and s["usable"] == 2 and s["down"] == 1
    assert s["offline_safe"] is True and s["chosen"] == "home"
    assert s["offload_targets"] == ["edge"]
