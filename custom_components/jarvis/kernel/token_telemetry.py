"""Cognitive token & cost telemetry (cross-cutting observability).

JARVIS routes work across model tiers (classifier / reasoning / review /
conversation / vision). Today cost and volume are *estimated*; this primitive
lets future releases report the **actual** per-call usage every provider returns
— input, output and cached (prompt-cache) tokens — attributed to a tier and
model, with an estimated cost derived from a small, explicit price book. It is
the measurement layer Phase Y (self-optimization) reads when it tunes model
spend, and what a cost panel renders instead of a guess.

This lands **pure**: a :class:`UsageRecord`, a :class:`PriceBook` that turns
token counts into an estimated USD cost, and pure aggregation
(:func:`summarize`, :func:`by_tier`, :func:`by_model`). Nothing live records
usage yet — the router / ``llm_provider`` emits a record per call in shadow,
parity against the current estimates, enforce when the panel and Phase Y read it.
No Home Assistant import, so it is deterministic and unit-testable, and nothing
here ever raises into a provider call path.

Cost convention: ``input_tokens`` is the count billed at the *input* rate and is
taken to be **non-overlapping** with ``cached_tokens`` (which is billed at the
reduced cached rate). A caller that only has a combined prompt count should pass
it all as ``input_tokens`` with ``cached_tokens=0``. Rates are USD per 1M tokens.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

# ── Model tiers (mirror llm_provider's roles + camera's vision tier) ─────────
TIER_CLASSIFIER = "classifier"
TIER_REASONING = "reasoning"
TIER_REVIEW = "review"
TIER_CONVERSATION = "conversation"
TIER_VISION = "vision"
TIER_UNKNOWN = "unknown"

TIERS: Tuple[str, ...] = (
    TIER_CLASSIFIER,
    TIER_REASONING,
    TIER_REVIEW,
    TIER_CONVERSATION,
    TIER_VISION,
    TIER_UNKNOWN,
)

_PER_MTOK = 1_000_000.0


def _nonneg_int(x) -> int:
    try:
        v = int(x)
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def _nonneg_float(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0.0 else 0.0


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for a model, split by token kind.

    ``cached_per_mtok`` defaults to the input rate when not given (i.e. no cache
    discount assumed), so a partially-specified price never *under*-counts cost.
    """

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cached_per_mtok: Optional[float] = None

    @property
    def cached_rate(self) -> float:
        return self.input_per_mtok if self.cached_per_mtok is None else self.cached_per_mtok

    def cost(self, *, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
        it = _nonneg_int(input_tokens)
        ot = _nonneg_int(output_tokens)
        ct = _nonneg_int(cached_tokens)
        return (
            it * _nonneg_float(self.input_per_mtok)
            + ot * _nonneg_float(self.output_per_mtok)
            + ct * _nonneg_float(self.cached_rate)
        ) / _PER_MTOK


class PriceBook:
    """Per-model prices with a fallback default; turns token counts into cost.

    Lookup is exact model id first, then a case-insensitive substring match (so
    ``"gpt-5-mini-2026"`` resolves against a ``"gpt-5-mini"`` entry), then the
    default. A :class:`PriceBook` with no default returns zero-cost for unknown
    models — telemetry still records the token counts; cost is simply ``0.0``
    until a price is known, never an error.
    """

    def __init__(
        self,
        prices: Optional[Mapping[str, ModelPrice]] = None,
        *,
        default: Optional[ModelPrice] = None,
    ) -> None:
        self._prices: Dict[str, ModelPrice] = dict(prices or {})
        self._default = default

    def price_for(self, model: str) -> Optional[ModelPrice]:
        if not model:
            return self._default
        if model in self._prices:
            return self._prices[model]
        low = model.lower()
        for key, price in self._prices.items():
            if key and key.lower() in low:
                return price
        return self._default

    def estimate_cost(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        price = self.price_for(model)
        if price is None:
            return 0.0
        return price.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
        )


@dataclass(frozen=True)
class UsageRecord:
    """One model call's measured token usage + attributed cost."""

    ts: float = 0.0
    tier: str = TIER_UNKNOWN
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: float = 0.0
    correlation_id: str = ""
    ok: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_tokens

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "correlation_id": self.correlation_id,
            "ok": self.ok,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "UsageRecord":
        return cls(
            ts=_nonneg_float(d.get("ts", 0.0)),
            tier=str(d.get("tier", TIER_UNKNOWN) or TIER_UNKNOWN),
            provider=str(d.get("provider", "") or ""),
            model=str(d.get("model", "") or ""),
            input_tokens=_nonneg_int(d.get("input_tokens", 0)),
            output_tokens=_nonneg_int(d.get("output_tokens", 0)),
            cached_tokens=_nonneg_int(d.get("cached_tokens", 0)),
            estimated_cost_usd=_nonneg_float(d.get("estimated_cost_usd", 0.0)),
            correlation_id=str(d.get("correlation_id", "") or ""),
            ok=bool(d.get("ok", True)),
        )


def record_usage(
    *,
    tier: str = TIER_UNKNOWN,
    provider: str = "",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    correlation_id: str = "",
    ok: bool = True,
    prices: Optional[PriceBook] = None,
    estimated_cost_usd: Optional[float] = None,
    now=None,
) -> UsageRecord:
    """Build a :class:`UsageRecord`, costing it from ``prices`` if not supplied.

    Token counts are coerced to non-negative ints. If ``estimated_cost_usd`` is
    given it is used as-is; otherwise a ``PriceBook`` (when provided) computes it,
    else cost is ``0.0``. ``tier`` is normalized to a known tier or ``unknown``.
    Pure: the caller extracts the provider's usage numbers; this just packages
    and prices them.
    """
    clock = now or time.time
    it = _nonneg_int(input_tokens)
    ot = _nonneg_int(output_tokens)
    ct = _nonneg_int(cached_tokens)
    t = tier if tier in TIERS else TIER_UNKNOWN
    if estimated_cost_usd is not None:
        cost = _nonneg_float(estimated_cost_usd)
    elif prices is not None:
        cost = prices.estimate_cost(model, input_tokens=it, output_tokens=ot, cached_tokens=ct)
    else:
        cost = 0.0
    return UsageRecord(
        ts=float(clock()),
        tier=t,
        provider=str(provider or ""),
        model=str(model or ""),
        input_tokens=it,
        output_tokens=ot,
        cached_tokens=ct,
        estimated_cost_usd=cost,
        correlation_id=str(correlation_id or ""),
        ok=bool(ok),
    )


@dataclass(frozen=True)
class UsageTotals:
    """Rolled-up usage across a set of records."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_tokens

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }


def summarize(records: Iterable[UsageRecord]) -> UsageTotals:
    """Totals across all records. Pure and total."""
    calls = it = ot = ct = 0
    cost = 0.0
    for r in records:
        if not isinstance(r, UsageRecord):
            continue
        calls += 1
        it += r.input_tokens
        ot += r.output_tokens
        ct += r.cached_tokens
        cost += r.estimated_cost_usd
    return UsageTotals(
        calls=calls,
        input_tokens=it,
        output_tokens=ot,
        cached_tokens=ct,
        estimated_cost_usd=cost,
    )


def _group(records: Iterable[UsageRecord], key: str) -> Dict[str, UsageTotals]:
    buckets: Dict[str, List[UsageRecord]] = {}
    for r in records:
        if not isinstance(r, UsageRecord):
            continue
        k = getattr(r, key, "") or ""
        buckets.setdefault(str(k), []).append(r)
    return {k: summarize(v) for k, v in buckets.items()}


def by_tier(records: Iterable[UsageRecord]) -> Dict[str, UsageTotals]:
    """Usage totals grouped by tier."""
    return _group(records, "tier")


def by_model(records: Iterable[UsageRecord]) -> Dict[str, UsageTotals]:
    """Usage totals grouped by model id."""
    return _group(records, "model")
