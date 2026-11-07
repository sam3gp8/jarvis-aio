"""TC4 enforce — the status panel reads measured token telemetry.

There is no independent legacy cost estimate to run a TC3 agreement-parity
against (the panel showed a literal "—"), and a measured usage record *is* the
ground truth for a pure measurement primitive, so this advances shadow →
enforce directly. These tests cover the three moving parts:

  * the kernel's default (operator-overridable) price book — pure;
  * llm_provider's priced retention ring + the enforce kill-switch;
  * websocket's fail-safe ``est_cost`` helper (never regresses the panel).
"""
import types

import pytest


# ── kernel: default price book (pure) ────────────────────────────────────────
@pytest.fixture
def tt(load):
    return load("kernel.token_telemetry")


def test_default_price_book_prices_known_families(tt):
    book = tt.default_price_book()
    # output billed higher than input; a known family costs > 0
    assert book.estimate_cost(
        "gpt-5-mini-2026", input_tokens=1_000_000, output_tokens=1_000_000
    ) == pytest.approx(0.25 + 2.0)
    # most-specific key wins (gpt-5-mini is listed before gpt-5)
    assert book.estimate_cost(
        "gpt-5-mini", input_tokens=1_000_000, output_tokens=0
    ) == pytest.approx(0.25)
    assert book.estimate_cost(
        "gpt-5", input_tokens=1_000_000, output_tokens=0
    ) == pytest.approx(1.25)
    # anthropic / gemini / groq families resolve by substring
    assert book.estimate_cost(
        "claude-sonnet-4-5", input_tokens=1_000_000, output_tokens=0
    ) == pytest.approx(3.0)
    assert book.estimate_cost(
        "gemini-2.5-flash", input_tokens=1_000_000, output_tokens=0
    ) == pytest.approx(0.10)
    assert book.estimate_cost(
        "llama-3.3-70b-versatile", input_tokens=1_000_000, output_tokens=0
    ) == pytest.approx(0.10)


def test_default_price_book_unknown_model_is_zero_not_error(tt):
    book = tt.default_price_book()
    assert book.estimate_cost(
        "nonesuch-model", input_tokens=1_000_000, output_tokens=1_000_000
    ) == 0.0


def test_default_price_book_is_fresh_each_call(tt):
    # pure: no shared mutable state handed out
    assert tt.default_price_book() is not tt.default_price_book()


# ── llm_provider: priced retention + enforce gate ────────────────────────────
class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Resp:
    def __init__(self, usage):
        self.usage = usage


@pytest.fixture
def llm(load):
    m = load("llm_provider")
    m._USAGE_RECORDS.clear()
    yield m
    m._USAGE_RECORDS.clear()


def test_record_usage_retains_priced_record(llm):
    llm._record_usage_shadow(_Resp(_Usage(1000, 500)), "openai", "gpt-5-mini")
    recs = llm.usage_records()
    assert len(recs) == 1
    r = recs[0]
    assert r.input_tokens == 1000
    assert r.output_tokens == 500
    assert r.estimated_cost_usd > 0  # priced via the default book


def test_record_usage_shadow_killswitch_skips(llm, monkeypatch):
    monkeypatch.setattr(llm, "TOKEN_TELEMETRY_SHADOW", False)
    llm._record_usage_shadow(_Resp(_Usage(1000, 500)), "openai", "gpt-5-mini")
    assert llm.usage_records() == ()


def test_record_usage_never_raises_on_bad_resp(llm):
    # no usage attribute at all → silently skipped, no record, no raise
    llm._record_usage_shadow(object(), "openai", "gpt-5-mini")
    assert llm.usage_records() == ()


def test_record_usage_ring_is_bounded(llm):
    assert llm._USAGE_RECORDS.maxlen and llm._USAGE_RECORDS.maxlen > 0


def test_enforce_enabled_default_on(llm):
    assert llm.telemetry_enforce_enabled() is True


def test_enforce_enabled_module_flag(llm, monkeypatch):
    monkeypatch.setattr(llm, "TOKEN_TELEMETRY_ENFORCE", False)
    assert llm.telemetry_enforce_enabled() is False


def test_enforce_enabled_config_key(llm, monkeypatch):
    import jc.jarvis_config as cfg
    monkeypatch.setattr(
        cfg, "get",
        lambda key, default=None: False if key == "token_telemetry_enforce" else default)
    assert llm.telemetry_enforce_enabled() is False


# ── llm_provider.telemetry_est_cost — the panel figure (fail-safe) ───────────
# (websocket's _telemetry_est_cost is a one-line delegate to this; the test
# harness pre-stubs jc.websocket, so the real logic is exercised here.)
def test_est_cost_placeholder_when_no_records(llm):
    assert llm.telemetry_est_cost() == "—"


def test_est_cost_reads_measured_cost(llm):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 50_000)), "openai", "gpt-5-mini")
    out = llm.telemetry_est_cost()
    assert out.startswith("$")
    assert out != "—"
    # (100000*0.25 + 50000*2.0) / 1e6 = $0.1250
    assert out == "$0.1250"


def test_est_cost_placeholder_when_enforce_off(llm, monkeypatch):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 50_000)), "openai", "gpt-5-mini")
    monkeypatch.setattr(llm, "TOKEN_TELEMETRY_ENFORCE", False)
    assert llm.telemetry_est_cost() == "—"


def test_est_cost_placeholder_when_only_unpriced(llm):
    # a recorded call whose model has no price → cost 0 → the panel keeps the
    # placeholder rather than implying a misleading "$0.0000" (free).
    llm._record_usage_shadow(_Resp(_Usage(100_000, 50_000)), "local", "nonesuch-model")
    assert llm.telemetry_est_cost() == "—"


# ── honesty: a mixed priced/unpriced total is flagged as a lower bound ────────
def test_est_cost_flags_unpriced_mix_as_lower_bound(llm):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 50_000)), "openai", "gpt-5-mini")   # priced
    llm._record_usage_shadow(_Resp(_Usage(100_000, 50_000)), "local", "nonesuch")      # unpriced
    out = llm.telemetry_est_cost()
    assert out.startswith("$") and out.endswith("+")   # real spend is higher


def test_est_cost_no_flag_when_all_priced(llm):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 50_000)), "openai", "gpt-5-mini")
    out = llm.telemetry_est_cost()
    assert out.startswith("$") and not out.endswith("+")


# ── provider-specific usage shapes + cached-token pricing ────────────────────
def test_cached_tokens_priced_at_input_rate_by_default(tt):
    book = tt.default_price_book()
    # default-book entries leave cached_per_mtok=None → cached billed at the input
    # rate (never under-counts a cache hit). gpt-5-mini input = 0.25 / Mtok.
    assert book.estimate_cost(
        "gpt-5-mini", input_tokens=0, output_tokens=0, cached_tokens=1_000_000
    ) == pytest.approx(0.25)


def test_record_usage_anthropic_shape(llm):
    # Anthropic: input_tokens / output_tokens / cache_read_input_tokens on .usage
    u = types.SimpleNamespace(input_tokens=1000, output_tokens=200,
                              cache_read_input_tokens=300)
    llm._record_usage_shadow(types.SimpleNamespace(usage=u), "anthropic",
                             "claude-sonnet-4-5")
    r = llm.usage_records()[-1]
    assert r.cached_tokens == 300
    assert r.input_tokens == 700          # 1000 prompt − 300 cached (non-overlapping)
    assert r.output_tokens == 200
    assert r.estimated_cost_usd > 0


def test_record_usage_gemini_shape(llm):
    # Gemini: *_token_count on .usage_metadata (no .usage)
    u = types.SimpleNamespace(prompt_token_count=800, candidates_token_count=150,
                              cached_content_token_count=200)
    llm._record_usage_shadow(types.SimpleNamespace(usage_metadata=u), "gemini",
                             "gemini-2.5-flash")
    r = llm.usage_records()[-1]
    assert r.cached_tokens == 200
    assert r.input_tokens == 600          # 800 prompt − 200 cached
    assert r.output_tokens == 150


def test_record_usage_openai_cached_details_shape(llm):
    # OpenAI: prompt_tokens_details.cached_tokens
    u = types.SimpleNamespace(
        prompt_tokens=1200, completion_tokens=300,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=200))
    llm._record_usage_shadow(types.SimpleNamespace(usage=u), "openai", "gpt-5-mini")
    r = llm.usage_records()[-1]
    assert r.cached_tokens == 200
    assert r.input_tokens == 1000         # 1200 − 200 cached
    assert r.output_tokens == 300


# ── tier attribution (MCU Tranche 1 — token tier accounting) ─────────────────
def test_record_usage_threads_tier(llm):
    llm._record_usage_shadow(_Resp(_Usage(1000, 500)), "groq", "gpt-oss-120b",
                             tier="reasoning")
    assert llm.usage_records()[-1].tier == "reasoning"


def test_record_usage_defaults_tier_unknown(llm):
    # no tier passed → honestly "unknown", never a wrong label
    llm._record_usage_shadow(_Resp(_Usage(1000, 500)), "groq", "gpt-oss-120b")
    assert llm.usage_records()[-1].tier == "unknown"


def test_record_usage_bogus_tier_normalized_unknown(llm):
    llm._record_usage_shadow(_Resp(_Usage(10, 10)), "groq", "x", tier="not-a-tier")
    assert llm.usage_records()[-1].tier == "unknown"


def test_by_tier_groups_recorded_usage(llm, tt):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 0)), "groq", "gpt-5-mini", tier="reasoning")
    llm._record_usage_shadow(_Resp(_Usage(200_000, 0)), "groq", "gpt-5-mini", tier="classifier")
    grouped = tt.by_tier(llm.usage_records())
    assert grouped["reasoning"].input_tokens == 100_000
    assert grouped["classifier"].input_tokens == 200_000


def test_provider_tier_defaults_unknown(llm):
    # every provider exposes a tier (class default) so an untagged one is honestly
    # unattributed rather than mislabelled.
    assert llm.LLMProvider.tier == "unknown"


def test_create_tier_provider_tags_its_tier(llm, monkeypatch):
    # a provider built for a tier carries it, so chat() attributes its usage.
    # (create_provider needs provider SDKs; stub it to test only the tagging.)
    fake = types.SimpleNamespace(tier="unknown")
    monkeypatch.setattr(llm, "create_provider", lambda **kw: fake)
    p = llm.create_tier_provider(
        {"reasoning_provider": "groq", "reasoning_model": "x"}, "reasoning")
    assert p is fake and p.tier == "reasoning"


# ── telemetry_breakdown — the per-tier/per-model dump (diagnostics) ───────────
def test_telemetry_breakdown_splits_by_tier_and_model(llm):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 0)), "groq", "gpt-5-mini", tier="reasoning")
    llm._record_usage_shadow(_Resp(_Usage(50_000, 0)), "groq", "gpt-5-mini", tier="classifier")
    b = llm.telemetry_breakdown()
    assert b["records"] == 2
    assert b["total"]["input_tokens"] == 150_000
    assert b["by_tier"]["reasoning"]["input_tokens"] == 100_000
    assert b["by_tier"]["classifier"]["input_tokens"] == 50_000
    assert "gpt-5-mini" in b["by_model"]


def test_telemetry_breakdown_empty_when_no_records(llm):
    assert llm.telemetry_breakdown() == {}


def test_telemetry_breakdown_empty_when_enforce_off(llm, monkeypatch):
    llm._record_usage_shadow(_Resp(_Usage(100_000, 0)), "groq", "gpt-5-mini", tier="reasoning")
    monkeypatch.setattr(llm, "TOKEN_TELEMETRY_ENFORCE", False)
    assert llm.telemetry_breakdown() == {}
