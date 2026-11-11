"""MCU Tranche 1 (foundation trust) — per-request correlation IDs on usage.

Every LLM call made in service of ONE request/cycle (a conversation turn, an
observer event) shares a correlation id, so token telemetry can attribute the
full spend of that one thing across every tier it touched. The id is pure
telemetry metadata: observe-only, it changes no response and no decision.

Covers the three moving parts:
  * llm_provider.correlation_scope / current_correlation_id (contextvar);
  * _record_usage_shadow stamping the live id onto each UsageRecord;
  * the kernel's pure by_correlation grouping + the bounded diagnostics surface.
"""
import types

import pytest


@pytest.fixture
def tt(load):
    return load("kernel.token_telemetry")


@pytest.fixture
def llm(load):
    m = load("llm_provider")
    m._USAGE_RECORDS.clear()
    yield m
    m._USAGE_RECORDS.clear()


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def _resp(p=0, c=0):
    return types.SimpleNamespace(usage=_Usage(p, c))


# ── the contextvar scope ─────────────────────────────────────────────────────

def test_current_correlation_id_default_empty(llm):
    assert llm.current_correlation_id() == ""


def test_scope_stamps_every_record_in_it(llm):
    with llm.correlation_scope("req-1"):
        llm._record_usage_shadow(_resp(1000, 500), "openai", "gpt-5-mini", tier="conversation")
        llm._record_usage_shadow(_resp(200, 50), "openai", "gpt-5-mini", tier="reasoning")
    recs = llm.usage_records()
    assert len(recs) == 2
    assert {r.correlation_id for r in recs} == {"req-1"}


def test_no_scope_leaves_correlation_empty(llm):
    llm._record_usage_shadow(_resp(1000, 500), "openai", "gpt-5-mini")
    assert llm.usage_records()[-1].correlation_id == ""


def test_nested_scope_restores_outer_id(llm):
    with llm.correlation_scope("outer"):
        assert llm.current_correlation_id() == "outer"
        with llm.correlation_scope("inner"):
            assert llm.current_correlation_id() == "inner"
        assert llm.current_correlation_id() == "outer"
    assert llm.current_correlation_id() == ""


def test_scope_resets_even_on_exception(llm):
    with pytest.raises(ValueError):
        with llm.correlation_scope("boom"):
            raise ValueError("x")
    assert llm.current_correlation_id() == ""   # restored despite the raise


def test_blank_scope_is_noop_tag(llm):
    with llm.correlation_scope(""):
        llm._record_usage_shadow(_resp(10, 10), "openai", "gpt-5-mini")
    assert llm.usage_records()[-1].correlation_id == ""


async def test_scope_propagates_across_await(llm):
    # contextvars flow across await within the same task, so an id set at a
    # request boundary reaches a provider call deep in the awaited chain.
    async def _deep():
        llm._record_usage_shadow(_resp(100, 10), "openai", "gpt-5-mini")

    with llm.correlation_scope("turn-42"):
        await _deep()
    assert llm.usage_records()[-1].correlation_id == "turn-42"


# ── kernel: pure by_correlation grouping ─────────────────────────────────────

def test_by_correlation_groups_records(tt):
    recs = [
        tt.record_usage(model="gpt-5-mini", input_tokens=100, correlation_id="a"),
        tt.record_usage(model="gpt-5-mini", input_tokens=200, correlation_id="a"),
        tt.record_usage(model="gpt-5-mini", input_tokens=50, correlation_id="b"),
    ]
    grouped = tt.by_correlation(recs)
    assert grouped["a"].calls == 2
    assert grouped["a"].input_tokens == 300
    assert grouped["b"].input_tokens == 50


def test_by_correlation_empty_id_bucket(tt):
    recs = [tt.record_usage(model="m", input_tokens=10)]   # no correlation id
    grouped = tt.by_correlation(recs)
    assert "" in grouped and grouped[""].input_tokens == 10


# ── diagnostics surface: bounded correlation split ───────────────────────────

def test_breakdown_counts_and_lists_correlations(llm):
    with llm.correlation_scope("req-A"):
        llm._record_usage_shadow(_resp(100_000, 0), "openai", "gpt-5-mini", tier="conversation")
    with llm.correlation_scope("req-B"):
        llm._record_usage_shadow(_resp(50_000, 0), "openai", "gpt-5-mini", tier="reasoning")
    b = llm.telemetry_breakdown()
    assert b["correlations"] == 2
    assert set(b["top_correlations"]) == {"req-A", "req-B"}
    assert b["top_correlations"]["req-A"]["input_tokens"] == 100_000


def test_breakdown_top_correlations_excludes_empty_id(llm):
    llm._record_usage_shadow(_resp(10_000, 0), "openai", "gpt-5-mini")   # empty id
    with llm.correlation_scope("req-A"):
        llm._record_usage_shadow(_resp(100_000, 0), "openai", "gpt-5-mini")
    b = llm.telemetry_breakdown()
    assert b["correlations"] == 1                       # empty id not counted
    assert set(b["top_correlations"]) == {"req-A"}      # and not listed


def test_breakdown_top_correlations_bounded_to_ten(llm):
    # 12 distinct requests → count reflects all 12, list is capped at 10.
    for i in range(12):
        with llm.correlation_scope(f"req-{i:02d}"):
            llm._record_usage_shadow(_resp(1000 * (i + 1), 0), "openai", "gpt-5-mini")
    b = llm.telemetry_breakdown()
    assert b["correlations"] == 12
    assert len(b["top_correlations"]) == 10
    # the costliest (most tokens) requests survive the cut; req-00 (smallest) drops
    assert "req-11" in b["top_correlations"]
    assert "req-00" not in b["top_correlations"]
