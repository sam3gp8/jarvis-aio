"""
JARVIS Connectivity Circuit Breaker (v5.9.06).

Tracks whether the cloud LLM (Groq/Gemini) is reachable so JARVIS can:
  1. Avoid wasting 5-10s on doomed network calls when already known-offline.
  2. Degrade gracefully to local-only handling during outages.
  3. Recover automatically once connectivity returns.

Classic circuit-breaker semantics:
  - CLOSED   → LLM believed healthy; calls allowed.
  - OPEN     → recent failures; calls skipped, straight to local fallback.
  - HALF_OPEN→ cooldown elapsed; allow ONE probe call to test recovery.

State is process-local (resets on HA restart), which is the correct scope:
connectivity is a runtime condition, not persistent config.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock

_LOGGER = logging.getLogger(__name__)

# Tuning
_FAILURE_THRESHOLD = 2       # consecutive failures before opening the breaker
_OPEN_COOLDOWN = 60.0        # seconds to stay OPEN before allowing a probe
_HALF_OPEN_MAX_PROBES = 1    # probe calls allowed in HALF_OPEN

# States
_CLOSED = "closed"
_OPEN = "open"
_HALF_OPEN = "half_open"


@dataclass
class _BreakerState:
    state: str = _CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_probes: int = 0
    last_success: float = 0.0
    last_failure: float = 0.0
    total_failures: int = 0
    total_successes: int = 0
    lock: Lock = field(default_factory=Lock)


_BREAKER = _BreakerState()


def allow_request() -> bool:
    """
    Return True if an LLM call should be attempted.

    - CLOSED    → always allow.
    - OPEN      → allow only if cooldown elapsed (transitions to HALF_OPEN).
    - HALF_OPEN → allow up to _HALF_OPEN_MAX_PROBES probe(s).
    """
    with _BREAKER.lock:
        now = time.time()
        st = _BREAKER.state

        if st == _CLOSED:
            return True

        if st == _OPEN:
            if (now - _BREAKER.opened_at) >= _OPEN_COOLDOWN:
                _BREAKER.state = _HALF_OPEN
                _BREAKER.half_open_probes = 0
                _LOGGER.info("Connectivity: cooldown elapsed, probing LLM (half-open)")
                # fall through to half-open handling
            else:
                return False

        # HALF_OPEN (either just transitioned or already there)
        if _BREAKER.half_open_probes < _HALF_OPEN_MAX_PROBES:
            _BREAKER.half_open_probes += 1
            return True
        return False


def record_success() -> None:
    """Call after a successful LLM response. Closes the breaker."""
    with _BREAKER.lock:
        now = time.time()
        was_down = _BREAKER.state != _CLOSED
        _BREAKER.state = _CLOSED
        _BREAKER.consecutive_failures = 0
        _BREAKER.half_open_probes = 0
        _BREAKER.last_success = now
        _BREAKER.total_successes += 1
        if was_down:
            _LOGGER.info("Connectivity: LLM reachable again — breaker closed")


def record_failure() -> None:
    """Call after a failed LLM call. May open the breaker."""
    with _BREAKER.lock:
        now = time.time()
        _BREAKER.consecutive_failures += 1
        _BREAKER.last_failure = now
        _BREAKER.total_failures += 1

        if _BREAKER.state == _HALF_OPEN:
            # Probe failed — re-open immediately
            _BREAKER.state = _OPEN
            _BREAKER.opened_at = now
            _BREAKER.half_open_probes = 0
            _LOGGER.warning("Connectivity: probe failed — breaker re-opened")
        elif _BREAKER.consecutive_failures >= _FAILURE_THRESHOLD:
            if _BREAKER.state != _OPEN:
                _LOGGER.warning(
                    "Connectivity: %d consecutive LLM failures — breaker OPEN "
                    "(local-only mode for %ds)",
                    _BREAKER.consecutive_failures, int(_OPEN_COOLDOWN),
                )
            _BREAKER.state = _OPEN
            _BREAKER.opened_at = now


def is_online() -> bool:
    """Best-effort: True if the LLM is believed reachable (breaker closed)."""
    with _BREAKER.lock:
        return _BREAKER.state == _CLOSED


def is_offline() -> bool:
    """True if the breaker is OPEN (known degraded)."""
    with _BREAKER.lock:
        return _BREAKER.state == _OPEN


def status() -> dict:
    """Snapshot for diagnostics / agent introspection."""
    with _BREAKER.lock:
        now = time.time()
        cooldown_remaining = 0
        if _BREAKER.state == _OPEN:
            cooldown_remaining = max(0, int(_OPEN_COOLDOWN - (now - _BREAKER.opened_at)))
        return {
            "state": _BREAKER.state,
            "online": _BREAKER.state == _CLOSED,
            "consecutive_failures": _BREAKER.consecutive_failures,
            "cooldown_remaining_s": cooldown_remaining,
            "total_failures": _BREAKER.total_failures,
            "total_successes": _BREAKER.total_successes,
            "last_success_ago_s": int(now - _BREAKER.last_success) if _BREAKER.last_success else None,
            "last_failure_ago_s": int(now - _BREAKER.last_failure) if _BREAKER.last_failure else None,
        }


def reset() -> None:
    """Force-close the breaker (e.g. manual override or test)."""
    with _BREAKER.lock:
        _BREAKER.state = _CLOSED
        _BREAKER.consecutive_failures = 0
        _BREAKER.half_open_probes = 0
        _LOGGER.info("Connectivity: breaker manually reset to closed")
