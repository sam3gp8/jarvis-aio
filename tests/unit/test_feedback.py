"""Tests for feedback.py — the adaptive-threshold engine that closes the LEARN
loop from Decision Record outcomes.

v8.4.1: the hot path (threshold_delta / effective_threshold) is cache-only and
non-blocking so it is safe on the event loop; the SQLite read happens off-loop in
async_refresh. These tests prove both the ranking logic and that the read is
dispatched through the executor, not run inline."""
import pytest


@pytest.fixture
def fb(load):
    m = load("feedback")
    m.reset_cache()
    return m


@pytest.fixture
def dr(load, tmp_path):
    return load("decision_record"), str(tmp_path / "dr.db")


class _RecHass:
    """Records what ran in the executor, proving the DB read is off-loop."""

    def __init__(self):
        self.executor_calls = []

    async def async_add_executor_job(self, func, *args):
        self.executor_calls.append(getattr(func, "__name__", repr(func)))
        return func(*args)


def _seed(dr_mod, db, kind, good=0, unnecessary=0, wrong=0):
    for verdict, n in (("good", good), ("unnecessary", unnecessary), ("wrong", wrong)):
        for _ in range(n):
            rid = dr_mod.record(kind, decision="x", db_path=db)
            dr_mod.set_outcome(rid, verdict, db_path=db)


# ── cache-only hot path ───────────────────────────────────────────────────────

def test_threshold_delta_is_zero_before_any_refresh(fb):
    assert fb.threshold_delta("anticipation") == 0.0


def test_threshold_delta_never_reads_disk(fb, dr, monkeypatch):
    # Even with judged records on disk, the sync hot path must not read them.
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", wrong=10)
    import sys
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("hot path hit the DB"))
    monkeypatch.setattr(sys.modules["jc.decision_record"], "outcome_rate", boom)
    assert fb.threshold_delta("anticipation") == 0.0   # cache empty → 0.0, no read


# ── async refresh does the off-loop read ──────────────────────────────────────

async def test_async_refresh_reads_off_loop_and_caches(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=1, unnecessary=5, wrong=4)   # 90% unwelcome
    rhass = _RecHass()
    d = await fb.async_refresh(rhass, "anticipation", db_path=db)
    assert d == 0.15
    assert "outcome_rate" in rhass.executor_calls      # read went through executor
    assert fb.threshold_delta("anticipation") == 0.15  # now served from cache


async def test_async_refresh_mostly_welcome_relaxes(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=10)
    assert await fb.async_refresh(_RecHass(), "anticipation", db_path=db) == -0.07


async def test_async_refresh_too_little_evidence_is_zero(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", wrong=2)          # below min_judged (5)
    assert await fb.async_refresh(_RecHass(), "anticipation", db_path=db) == 0.0


async def test_async_refresh_optin_off_caches_zero_without_read(fb, dr, monkeypatch):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", wrong=10)
    import sys, types
    monkeypatch.setitem(sys.modules, "jc.jarvis_config",
                        types.SimpleNamespace(get=lambda k, d=None: False))
    rhass = _RecHass()
    assert await fb.async_refresh(rhass, "anticipation", opt_in_key="x", db_path=db) == 0.0
    assert rhass.executor_calls == []                   # opt-in off → no read at all


async def test_async_refresh_throttles_to_cache_ttl(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=10)
    rhass = _RecHass()
    await fb.async_refresh(rhass, "anticipation", db_path=db)
    await fb.async_refresh(rhass, "anticipation", db_path=db)   # within TTL → no 2nd read
    assert rhass.executor_calls.count("outcome_rate") == 1


# ── effective_threshold clamps around the cached delta ────────────────────────

async def test_effective_threshold_clamps(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=1, unnecessary=5, wrong=4)   # +0.15
    await fb.async_refresh(_RecHass(), "anticipation", db_path=db)
    assert fb.effective_threshold(0.9, "anticipation", hi=0.95) == pytest.approx(0.95)
    assert fb.effective_threshold(0.6, "anticipation") == pytest.approx(0.75)
