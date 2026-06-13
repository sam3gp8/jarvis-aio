"""
JARVIS — Learned Reasoning Cache (v5.9.38).

Memoizes the cloud LLM's announce/stay-silent decisions by a COARSE event
signature, so repeated event patterns are decided locally without a cloud call.
Over time the cache covers the common patterns and cloud usage drops — and when
the connectivity breaker is OPEN (cloud rate-limited / down), the cache provides
the decision so local speech stays robust.

Design:
  - Signature generalizes across rooms/entities: domain | device_class |
    category | from->to (numeric values collapsed) | home/away. Most routine
    events ("a motion sensor cleared", "a humidity reading drifted") map to the
    same signature regardless of which entity fired.
  - The cache primarily learns SILENCE for routine patterns — exactly the calls
    that were wastefully going to the cloud to be told "stay quiet".
  - Entries are re-validated against the cloud after REFRESH_AGE so the learned
    behaviour can adapt.

This is a dependency-free leaf module (no package imports) — observer and
reasoning_loop import it lazily. Persists to /config/jarvis/reasoning_cache.json.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from threading import Lock

_LOGGER = logging.getLogger(__name__)

CACHE_PATH = Path("/config/jarvis/reasoning_cache.json")
REFRESH_AGE = 14 * 86400        # re-validate a learned decision via cloud after 2 weeks
MAX_ENTRIES = 2000              # bound the cache
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")

_lock = Lock()
_cache: dict = {}               # sig -> {speak, urgency, hits, created, refreshed}
_loaded = False

# Session counters (reset on restart) — show the "calling cloud less" trend.
_CLOUD_CALLS = 0
_CACHE_HITS = 0


def _norm_state(s) -> str:
    if s is None:
        return "?"
    v = str(s).strip().lower()
    if v in ("unknown", "unavailable", "none", ""):
        return "?"
    if _NUMERIC_RE.match(v):
        return "#"              # collapse numeric values so signatures generalize
    return v


def signature(domain, device_class, category, from_state, to_state, anyone_home,
              urgency="") -> str:
    return (
        f"{domain or '-'}|{device_class or '-'}|{category or '-'}|"
        f"{_norm_state(from_state)}->{_norm_state(to_state)}|"
        f"home={1 if anyone_home else 0}|u={urgency or '-'}"
    )


def load() -> int:
    global _cache, _loaded
    with _lock:
        try:
            if CACHE_PATH.exists():
                with open(CACHE_PATH) as f:
                    _cache = json.load(f)
            else:
                _cache = {}
            _loaded = True
            _LOGGER.info("Reasoning cache: loaded %d learned patterns", len(_cache))
        except Exception as exc:
            _LOGGER.warning("Reasoning cache load error: %s", exc)
            _cache = {}
            _loaded = True
    return len(_cache)


def save() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            # Evict oldest-refreshed entries if over the cap
            if len(_cache) > MAX_ENTRIES:
                items = sorted(_cache.items(), key=lambda kv: kv[1].get("refreshed", 0))
                for sig, _v in items[: len(_cache) - MAX_ENTRIES]:
                    _cache.pop(sig, None)
            tmp = CACHE_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(_cache, f)
            tmp.replace(CACHE_PATH)
    except Exception as exc:
        _LOGGER.debug("Reasoning cache save error: %s", exc)


def get(sig: str, ignore_age: bool = False):
    """Return a cached decision dict, or None if absent/stale."""
    global _loaded
    if not _loaded:
        load()
    with _lock:
        e = _cache.get(sig)
        if not e:
            return None
        if not ignore_age and (time.time() - e.get("refreshed", 0)) > REFRESH_AGE:
            return None             # stale → caller should refresh via cloud
        return dict(e)


def similar(domain, device_class, category, anyone_home) -> tuple:
    """
    Case-based memory for the Local Mind: tally past cloud decisions whose
    signature shares this event's domain/device_class/category and home state
    (any from→to transition, any urgency). Returns (speak_count, silent_count).
    Age is ignored deliberately — for offline judgment, an old real decision
    beats no decision.
    """
    global _loaded
    if not _loaded:
        load()
    prefix = f"{domain or '-'}|{device_class or '-'}|{category or '-'}|"
    home_tok = f"|home={1 if anyone_home else 0}|"
    speak_n = silent_n = 0
    with _lock:
        for sig, e in _cache.items():
            if sig.startswith(prefix) and home_tok in sig:
                if e.get("speak"):
                    speak_n += 1
                else:
                    silent_n += 1
    return (speak_n, silent_n)


def remember(sig: str, speak: bool, urgency: str) -> None:
    """Store/refresh a learned decision from the cloud."""
    global _loaded
    if not _loaded:
        load()
    now = time.time()
    with _lock:
        e = _cache.get(sig)
        if e:
            e["speak"] = bool(speak)
            e["urgency"] = urgency
            e["refreshed"] = now
        else:
            _cache[sig] = {
                "speak": bool(speak), "urgency": urgency,
                "hits": 0, "created": now, "refreshed": now,
            }
    save()


def note_hit(sig: str) -> None:
    """Record that a decision was served locally from the cache (no cloud call)."""
    global _CACHE_HITS
    with _lock:
        _CACHE_HITS += 1
        e = _cache.get(sig)
        if e:
            e["hits"] = e.get("hits", 0) + 1


def note_cloud_call() -> None:
    global _CLOUD_CALLS
    _CLOUD_CALLS += 1


def stats() -> dict:
    global _loaded
    if not _loaded:
        load()
    with _lock:
        total = _CLOUD_CALLS + _CACHE_HITS
        return {
            "learned_patterns": len(_cache),
            "cloud_calls": _CLOUD_CALLS,
            "local_decisions": _CACHE_HITS,
            "local_rate": round(_CACHE_HITS / total * 100) if total else 0,
        }


def reset() -> None:
    global _cache, _CLOUD_CALLS, _CACHE_HITS
    with _lock:
        _cache = {}
        _CLOUD_CALLS = 0
        _CACHE_HITS = 0
    save()
