"""Durable (cross-restart) token-telemetry store (MCU Tranche 1 — token cost accounting).

The session ring in ``llm_provider`` resets on every restart, so measured LLM
spend was only ever known for the current session. This folds drained
``UsageRecord``s into a small on-disk JSON of **lifetime** and **per-UTC-day**
roll-ups (plus per-tier / per-model lifetime splits), so the cost accounting
survives reboots and a cost panel can show "spend this week", not just "this
session".

Entirely observe-only and best-effort, mirroring ``authority_bridge``'s
persistence: it reads records the providers already measured, never changes a
call, and never raises into the scheduler tick or the unload that drive it. The
file is written atomically (temp + ``os.replace``) so a crash mid-write can't
corrupt it, and the per-day map is bounded so it can't grow without limit.
Kill-switched via ``llm_provider.TOKEN_TELEMETRY_PERSIST`` / the
``token_telemetry_persist`` config key (honored by the flush caller).
"""
from __future__ import annotations

import json
import logging
import os
import time

_LOGGER = logging.getLogger(__name__)

_STORE_FILE_PARTS = ("jarvis", "token_telemetry.json")
_SCHEMA_VERSION = 1
# Hard cap on the per-day map so the file stays bounded even when retention is
# disabled (db_retention_days = 0). ~13 months of days is plenty for a ledger.
_MAX_DAYS = 400
_DEFAULT_RETENTION_DAYS = 90


def _store_path(hass):
    try:
        from .paths import config_path_str
        return config_path_str(*_STORE_FILE_PARTS, hass=hass)
    except Exception:
        return None


def _retention_days() -> int:
    """Reuse the DB-retention window (issue #491) so the cost ledger ages out on
    the same schedule as the other observation stores. 0 = keep (still hard-capped
    at _MAX_DAYS); a bad value falls back to the default."""
    try:
        from . import jarvis_config
        v = int(jarvis_config.get("db_retention_days", _DEFAULT_RETENTION_DAYS))
        return v if v >= 0 else _DEFAULT_RETENTION_DAYS
    except Exception:
        return _DEFAULT_RETENTION_DAYS


def load(hass) -> dict:
    """Read the persisted store, or ``{}`` when missing/corrupt. Sync, fail-safe."""
    path = _store_path(hass)
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # a corrupt file must never break a read
        _LOGGER.debug("token telemetry store load failed: %s", exc)
        return {}


def _empty_totals() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cached_tokens": 0, "estimated_cost_usd": 0.0}


def _merge_into(acc, totals) -> dict:
    """Field-wise add a kernel ``UsageTotals`` into a plain totals dict, returning
    a new dict. Tolerates a malformed ``acc`` by treating it as zero."""
    acc = acc if isinstance(acc, dict) else {}

    def _i(key):
        try:
            return int(acc.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    try:
        cost = float(acc.get("estimated_cost_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return {
        "calls": _i("calls") + int(totals.calls),
        "input_tokens": _i("input_tokens") + int(totals.input_tokens),
        "output_tokens": _i("output_tokens") + int(totals.output_tokens),
        "cached_tokens": _i("cached_tokens") + int(totals.cached_tokens),
        "estimated_cost_usd": round(cost + float(totals.estimated_cost_usd), 6),
    }


def _prune_days(days: dict) -> dict:
    """Drop days outside the retention window and hard-cap the map size."""
    if not isinstance(days, dict):
        return {}
    keep = _retention_days()
    if keep > 0:
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - keep * 86400))
        days = {d: t for d, t in days.items() if str(d) >= cutoff}
    if len(days) > _MAX_DAYS:                       # bound even with retention off
        for d in sorted(days)[:len(days) - _MAX_DAYS]:
            days.pop(d, None)
    return days


def flush(hass, records) -> bool:
    """Fold ``records`` into the on-disk lifetime + per-day roll-ups and write
    atomically. Sync — call from an executor (a scheduler tick and on unload).
    Best-effort: a missing/corrupt store starts fresh and nothing here raises.
    Returns True on a successful write, False when there was nothing to do or it
    could not persist."""
    path = _store_path(hass)
    if not path or not records:
        return False
    try:
        from .kernel import token_telemetry as _tt

        store = load(hass)
        now = time.time()
        if not isinstance(store, dict):
            store = {}
        store["version"] = _SCHEMA_VERSION
        store.setdefault("first_ts", now)
        store["updated_ts"] = now

        store["lifetime"] = _merge_into(store.get("lifetime"), _tt.summarize(records))

        tiers = store.get("by_tier") if isinstance(store.get("by_tier"), dict) else {}
        for k, v in _tt.by_tier(records).items():
            tiers[str(k)] = _merge_into(tiers.get(str(k)), v)
        store["by_tier"] = tiers

        models = store.get("by_model") if isinstance(store.get("by_model"), dict) else {}
        for k, v in _tt.by_model(records).items():
            models[str(k)] = _merge_into(models.get(str(k)), v)
        store["by_model"] = models

        days = store.get("days") if isinstance(store.get("days"), dict) else {}
        for k, v in _tt.by_day(records).items():
            if not k:                               # skip an undatable record
                continue
            days[str(k)] = _merge_into(days.get(str(k)), v)
        store["days"] = _prune_days(days)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # never raises into a scheduler tick / unload
        _LOGGER.debug("token telemetry store flush failed: %s", exc)
        return False


def persisted_breakdown(hass, *, recent_days: int = 14) -> dict:
    """The durable store for the diagnostics dump: lifetime + per-tier/-model
    splits and the most recent days. Fail-safe ``{}`` when empty or on any fault;
    bounded to ``recent_days`` so a long-running home's dump stays small."""
    try:
        store = load(hass)
        if not store:
            return {}
        days = store.get("days") if isinstance(store.get("days"), dict) else {}
        recent = ({d: days[d] for d in sorted(days)[-max(1, recent_days):]}
                  if days else {})
        out = {
            "lifetime": store.get("lifetime") or {},
            "by_tier": store.get("by_tier") or {},
            "by_model": store.get("by_model") or {},
            "days": recent,
            "day_count": len(days),
        }
        for k in ("first_ts", "updated_ts"):
            if store.get(k):
                out[k] = store[k]
        return out
    except Exception as exc:  # diagnostics read, never fatal
        _LOGGER.debug("token telemetry persisted_breakdown failed: %s", exc)
        return {}
