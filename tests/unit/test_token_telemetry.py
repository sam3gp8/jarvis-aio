"""Tests for cognitive token & cost telemetry (pure).

No Home Assistant, no DB: plain records, a small price book, a fixed clock.
"""
import pytest


@pytest.fixture
def tt(load):
    return load("kernel.token_telemetry")


def test_model_price_costs_each_token_kind(tt):
    price = tt.ModelPrice(input_per_mtok=1.0, output_per_mtok=3.0, cached_per_mtok=0.25)
    # 1_000_000 input @ $1 + 1_000_000 output @ $3 + 1_000_000 cached @ $0.25
    cost = price.cost(input_tokens=1_000_000, output_tokens=1_000_000, cached_tokens=1_000_000)
    assert cost == pytest.approx(4.25)


def test_cached_rate_defaults_to_input_rate(tt):
    price = tt.ModelPrice(input_per_mtok=2.0, output_per_mtok=6.0)  # no cached rate given
    assert price.cached_rate == 2.0
    # cached billed at the input rate when no discount is specified (never under-counts)
    assert price.cost(input_tokens=0, output_tokens=0, cached_tokens=1_000_000) == pytest.approx(2.0)


def test_pricebook_exact_then_substring_then_default(tt):
    book = tt.PriceBook(
        {"gpt-5-mini": tt.ModelPrice(input_per_mtok=0.25, output_per_mtok=2.0)},
        default=tt.ModelPrice(input_per_mtok=10.0, output_per_mtok=30.0),
    )
    # substring match: a dated model id resolves against the family entry
    c = book.estimate_cost("gpt-5-mini-2026-01", input_tokens=1_000_000, output_tokens=0)
    assert c == pytest.approx(0.25)
    # unknown model → default price
    d = book.estimate_cost("some-other-model", input_tokens=1_000_000, output_tokens=0)
    assert d == pytest.approx(10.0)


def test_pricebook_without_default_is_zero_cost_not_error(tt):
    book = tt.PriceBook()
    assert book.estimate_cost("unknown", input_tokens=5000, output_tokens=5000) == 0.0


def test_record_usage_prices_and_normalizes(tt):
    book = tt.PriceBook({"flash": tt.ModelPrice(input_per_mtok=0.10, output_per_mtok=0.40)})
    rec = tt.record_usage(
        tier=tt.TIER_CLASSIFIER, provider="gemini", model="gemini-flash",
        input_tokens=2_000_000, output_tokens=1_000_000, cached_tokens=-5,
        correlation_id="abc", prices=book, now=lambda: 123.0,
    )
    assert rec.ts == 123.0
    assert rec.tier == tt.TIER_CLASSIFIER
    assert rec.cached_tokens == 0           # negative coerced to 0
    assert rec.total_tokens == 3_000_000
    # 2M @ $0.10 + 1M @ $0.40 = 0.20 + 0.40
    assert rec.estimated_cost_usd == pytest.approx(0.60)


def test_record_usage_unknown_tier_falls_back(tt):
    rec = tt.record_usage(tier="banana", model="m", input_tokens=1)
    assert rec.tier == tt.TIER_UNKNOWN


def test_record_usage_explicit_cost_wins_over_pricebook(tt):
    book = tt.PriceBook(default=tt.ModelPrice(input_per_mtok=99.0))
    rec = tt.record_usage(model="m", input_tokens=1_000_000, estimated_cost_usd=0.5, prices=book)
    assert rec.estimated_cost_usd == pytest.approx(0.5)


def test_summarize_totals(tt):
    recs = [
        tt.record_usage(tier=tt.TIER_CLASSIFIER, model="a", input_tokens=100, output_tokens=10,
                        estimated_cost_usd=0.01),
        tt.record_usage(tier=tt.TIER_REASONING, model="b", input_tokens=200, output_tokens=20,
                        cached_tokens=5, estimated_cost_usd=0.05),
    ]
    totals = tt.summarize(recs)
    assert totals.calls == 2
    assert totals.input_tokens == 300
    assert totals.output_tokens == 30
    assert totals.cached_tokens == 5
    assert totals.total_tokens == 335
    assert totals.estimated_cost_usd == pytest.approx(0.06)


def test_by_tier_and_by_model_group(tt):
    recs = [
        tt.record_usage(tier=tt.TIER_VISION, model="v1", input_tokens=100, estimated_cost_usd=0.02),
        tt.record_usage(tier=tt.TIER_VISION, model="v1", input_tokens=50, estimated_cost_usd=0.01),
        tt.record_usage(tier=tt.TIER_CLASSIFIER, model="c1", input_tokens=10, estimated_cost_usd=0.001),
    ]
    tiers = tt.by_tier(recs)
    assert set(tiers) == {tt.TIER_VISION, tt.TIER_CLASSIFIER}
    assert tiers[tt.TIER_VISION].calls == 2
    assert tiers[tt.TIER_VISION].input_tokens == 150
    assert tiers[tt.TIER_VISION].estimated_cost_usd == pytest.approx(0.03)
    models = tt.by_model(recs)
    assert models["v1"].calls == 2 and models["c1"].calls == 1


def test_summarize_empty_is_total(tt):
    totals = tt.summarize([])
    assert totals.calls == 0 and totals.total_tokens == 0 and totals.estimated_cost_usd == 0.0


def test_record_round_trips_through_dict(tt):
    rec = tt.record_usage(
        tier=tt.TIER_REVIEW, provider="openai", model="gpt-5", input_tokens=7,
        output_tokens=3, cached_tokens=2, estimated_cost_usd=0.009,
        correlation_id="cid", ok=False, now=lambda: 9.0,
    )
    back = tt.UsageRecord.from_dict(rec.to_dict())
    assert back == rec
    assert back.ok is False
