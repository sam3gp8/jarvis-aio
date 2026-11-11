"""MCU Tranche 1 (foundation trust) — durable (cross-restart) token cost store.

The session usage ring resets on every restart, so measured LLM spend was only
ever known for the current session. The durable store folds drained records into
an on-disk lifetime + per-UTC-day ledger that survives reboots. Observe-only and
fail-safe: it reads records the providers already measured and never raises into
the flush tick / unload that drive it.

Covers the three moving parts:
  * kernel pure helpers (by_day, merge_totals, UsageTotals.from_dict);
  * llm_provider's pending-persist buffer + drain + kill-switch;
  * token_store's atomic flush / load / retention / diagnostics surface.
"""
import json

import pytest

from fakes import FakeHass


@pytest.fixture
def tt(load):
    return load("kernel.token_telemetry")


@pytest.fixture
def store(load, tmp_path, monkeypatch):
    m = load("token_store")
    path = tmp_path / "token_telemetry.json"
    monkeypatch.setattr(m, "_store_path", lambda hass: str(path))
    # default: retention keeps everything in tests unless a test overrides it
    monkeypatch.setattr(m, "_retention_days", lambda: 0)
    m._test_path = path
    return m


@pytest.fixture
def llm(load):
    m = load("llm_provider")
    m._USAGE_RECORDS.clear()
    m._PENDING_PERSIST.clear()
    yield m
    m._USAGE_RECORDS.clear()
    m._PENDING_PERSIST.clear()


# one day in seconds; a couple of fixed UTC timestamps on known days
_DAY = 86400
_T_JAN1 = 1_735_689_600.0   # 2025-01-01T00:00:00Z
_T_JAN2 = _T_JAN1 + _DAY    # 2025-01-02


def _rec(tt, *, ts, tier="conversation", model="gpt-5-mini", inp=0, out=0, cost=0.0):
    return tt.record_usage(tier=tier, model=model, input_tokens=inp, output_tokens=out,
                           estimated_cost_usd=cost, now=lambda: ts)


# ── kernel pure helpers ──────────────────────────────────────────────────────

def test_by_day_groups_by_utc_date(tt):
    recs = [_rec(tt, ts=_T_JAN1, inp=100), _rec(tt, ts=_T_JAN1 + 3600, inp=50),
            _rec(tt, ts=_T_JAN2, inp=10)]
    grouped = tt.by_day(recs)
    assert set(grouped) == {"2025-01-01", "2025-01-02"}
    assert grouped["2025-01-01"].input_tokens == 150
    assert grouped["2025-01-02"].input_tokens == 10


def test_merge_totals_adds_fields(tt):
    a = tt.summarize([_rec(tt, ts=_T_JAN1, inp=100, out=10, cost=0.01)])
    b = tt.summarize([_rec(tt, ts=_T_JAN1, inp=200, out=20, cost=0.02)])
    m = tt.merge_totals(a, b)
    assert m.calls == 2
    assert m.input_tokens == 300 and m.output_tokens == 30
    assert m.estimated_cost_usd == pytest.approx(0.03)


def test_merge_totals_tolerates_non_total(tt):
    a = tt.summarize([_rec(tt, ts=_T_JAN1, inp=5)])
    assert tt.merge_totals(a, None).input_tokens == 5
    assert tt.merge_totals("x", a).input_tokens == 5


def test_usage_totals_from_dict_roundtrips(tt):
    t = tt.summarize([_rec(tt, ts=_T_JAN1, inp=100, out=50, cost=0.5)])
    back = tt.UsageTotals.from_dict(t.to_dict())
    assert back.calls == t.calls and back.input_tokens == 100
    assert back.estimated_cost_usd == pytest.approx(0.5)


def test_usage_totals_from_dict_tolerates_garbage(tt):
    z = tt.UsageTotals.from_dict({"calls": "nope", "input_tokens": -5})
    assert z.calls == 0 and z.input_tokens == 0


# ── llm_provider: pending-persist buffer ─────────────────────────────────────

class _Usage:
    def __init__(self, p=0, c=0):
        self.prompt_tokens = p
        self.completion_tokens = c


def _resp(p=0, c=0):
    import types
    return types.SimpleNamespace(usage=_Usage(p, c))


def test_record_feeds_pending_buffer(llm):
    llm._record_usage_shadow(_resp(1000, 500), "openai", "gpt-5-mini", tier="conversation")
    assert len(llm._PENDING_PERSIST) == 1


def test_drain_returns_and_clears(llm):
    llm._record_usage_shadow(_resp(100, 10), "openai", "gpt-5-mini")
    llm._record_usage_shadow(_resp(200, 20), "openai", "gpt-5-mini")
    drained = llm.drain_pending_records()
    assert len(drained) == 2
    assert llm.drain_pending_records() == ()      # emptied


def test_persist_module_killswitch_skips_buffer(llm, monkeypatch):
    monkeypatch.setattr(llm, "TOKEN_TELEMETRY_PERSIST", False)
    llm._record_usage_shadow(_resp(100, 10), "openai", "gpt-5-mini")
    assert len(llm._PENDING_PERSIST) == 0


def test_persist_enabled_config_key(llm, monkeypatch):
    import jc.jarvis_config as cfg
    monkeypatch.setattr(cfg, "get",
                        lambda key, default=None: False if key == "token_telemetry_persist" else default)
    assert llm.telemetry_persist_enabled() is False


def test_persist_enabled_default_on(llm):
    assert llm.telemetry_persist_enabled() is True


# ── token_store: flush / load / accumulate / retention ───────────────────────

def test_flush_creates_and_folds(store, tt):
    hass = FakeHass()
    recs = [_rec(tt, ts=_T_JAN1, tier="conversation", model="gpt-5-mini", inp=100, cost=0.01),
            _rec(tt, ts=_T_JAN1, tier="vision", model="gpt-4o", inp=200, cost=0.05)]
    assert store.flush(hass, recs) is True
    data = json.loads(store._test_path.read_text())
    assert data["lifetime"]["input_tokens"] == 300
    assert data["lifetime"]["estimated_cost_usd"] == pytest.approx(0.06)
    assert data["by_tier"]["vision"]["input_tokens"] == 200
    assert data["by_model"]["gpt-5-mini"]["input_tokens"] == 100
    assert data["days"]["2025-01-01"]["input_tokens"] == 300


def test_flush_accumulates_across_restarts(store, tt):
    hass = FakeHass()
    store.flush(hass, [_rec(tt, ts=_T_JAN1, inp=100, cost=0.01)])
    # second "session" after a restart: the in-memory ring would be empty, but the
    # on-disk ledger must accrue.
    store.flush(hass, [_rec(tt, ts=_T_JAN2, inp=50, cost=0.02)])
    data = json.loads(store._test_path.read_text())
    assert data["lifetime"]["calls"] == 2
    assert data["lifetime"]["input_tokens"] == 150
    assert data["lifetime"]["estimated_cost_usd"] == pytest.approx(0.03)
    assert set(data["days"]) == {"2025-01-01", "2025-01-02"}


def test_flush_empty_is_noop(store):
    hass = FakeHass()
    assert store.flush(hass, []) is False
    assert not store._test_path.exists()


def test_load_missing_returns_empty(store):
    assert store.load(FakeHass()) == {}


def test_load_corrupt_returns_empty(store):
    store._test_path.write_text("{not json")
    assert store.load(FakeHass()) == {}


def test_retention_prunes_old_days(store, tt, monkeypatch):
    import time as _time
    hass = FakeHass()
    monkeypatch.setattr(store, "_retention_days", lambda: 7)
    now = _time.time()
    old = now - 30 * _DAY
    store.flush(hass, [_rec(tt, ts=old, inp=10), _rec(tt, ts=now, inp=20)])
    data = json.loads(store._test_path.read_text())
    # the 30-day-old day is outside the 7-day window; today's survives
    assert len(data["days"]) == 1
    # lifetime still counts both (it's never pruned)
    assert data["lifetime"]["input_tokens"] == 30


def test_persisted_breakdown_bounds_recent_days(store, tt):
    hass = FakeHass()
    recs = [_rec(tt, ts=_T_JAN1 + i * _DAY, inp=10) for i in range(20)]
    store.flush(hass, recs)
    pb = store.persisted_breakdown(hass, recent_days=5)
    assert pb["day_count"] == 20
    assert len(pb["days"]) == 5
    assert pb["lifetime"]["input_tokens"] == 200


def test_persisted_breakdown_empty_without_store(store):
    assert store.persisted_breakdown(FakeHass()) == {}
