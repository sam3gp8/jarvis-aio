"""Feedback-loop detection (kernel hardening H3, docs/KERNEL_PLAN.md).

An automation system that both *reacts to* and *produces* events can chase its
own tail: an action changes a state, the state change is an event, the event
triggers the same action again — a thrash loop that burns the actuator and the
logs. The audit called out that JARVIS has no single place that notices this.

This is that place. The caller feeds it each action as it fires, optionally with
the id of the event that *caused* it; the detector returns a verdict:

    * **repetition** — the same action fired too many times inside a window
      (the classic "flapping" loop), or
    * **self_trigger** — the action was caused, through the event chain, by an
      earlier firing of the *same* action (a true A → event → A cycle), or
    * nothing, and a short **cooldown** after a loop is flagged so the caller can
      break the cycle rather than re-detect it every tick.

Pure: no Home Assistant import, no I/O, no wall clock — ``now`` is passed in, so
the detector is deterministic and unit-testable. It never acts; it only reports.
The caller decides whether to suppress, back off, or alert (and, once wired, how
this feeds `attention` / `priority`).
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple

# verdict reasons
NONE = "none"
REPETITION = "repetition"
SELF_TRIGGER = "self_trigger"
COOLDOWN = "cooldown"


@dataclass(frozen=True)
class LoopVerdict:
    """The detector's answer for one observed action."""

    looping: bool
    reason: str = NONE
    count: int = 0            # firings of this key currently in the window
    cooling_down: bool = False  # a loop was already flagged; caller should hold
    key: str = ""

    def __bool__(self) -> bool:  # truthy iff a loop (active or cooling) is in effect
        return self.looping or self.cooling_down


@dataclass
class LoopDetector:
    """Sliding-window repetition + causal self-trigger detector.

    Parameters:
        window_s     — how far back repetitions are counted.
        max_repeats  — firings of one key within the window that counts as a loop.
        cooldown_s   — after a loop is flagged, how long to report ``cooling_down``
                       so the caller can break the cycle instead of re-detecting.
        chain_depth  — how many cause→action hops to walk when tracing a
                       self-trigger (bounds the lookup; a real loop is short).
    """

    window_s: float = 10.0
    max_repeats: int = 3
    cooldown_s: float = 30.0
    chain_depth: int = 8

    # key → timestamps of recent firings (pruned to the window)
    _fires: Dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))
    # key → time the current cooldown expires
    _cooldown_until: Dict[str, float] = field(default_factory=dict)
    # emitted event id → (action key that produced it, the cause id it reacted to)
    _emitted: Dict[str, Tuple[str, Optional[str]]] = field(default_factory=dict)
    # bound on the causal map so a long-lived detector doesn't grow without limit
    _max_emitted: int = 4096

    def _prune(self, key: str, now: float) -> None:
        dq = self._fires[key]
        cutoff = now - self.window_s
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _self_triggered(self, key: str, cause: Optional[str]) -> bool:
        """Walk the cause chain: did an earlier firing of ``key`` lead here?"""
        seen = 0
        cur = cause
        while cur is not None and seen < self.chain_depth:
            entry = self._emitted.get(cur)
            if entry is None:
                return False
            producer, prev_cause = entry
            if producer == key:
                return True
            cur = prev_cause
            seen += 1
        return False

    def peek(self, key: str, *, now: float) -> bool:
        """True if ``key`` is currently flagged as thrashing (in loop cooldown),
        WITHOUT recording a firing. For a cross-path consult that reads a detector
        another caller (e.g. the event-bus reactor) has populated — so the reader
        doesn't double-count the firing the bus already recorded."""
        until = self._cooldown_until.get(key)
        return until is not None and now < until

    def record(
        self,
        key: str,
        *,
        now: float,
        cause: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> LoopVerdict:
        """Observe one action firing and return a :class:`LoopVerdict`.

        ``key``      — a stable identifier for the action (e.g. "light.turn_on:bed").
        ``now``      — monotonic/seconds timestamp supplied by the caller.
        ``cause``    — id of the event that triggered this action, if known.
        ``event_id`` — id of the event this action *emits* (what downstream
                       actions will pass as their ``cause``), so the chain links up.
        """
        self._prune(key, now)

        # Register this firing's emitted event so a later action can trace back.
        if event_id is not None:
            if len(self._emitted) >= self._max_emitted:
                self._emitted.clear()  # cheap bound; chains are short-lived
            self._emitted[event_id] = (key, cause)

        # Still cooling down from a prior loop on this key → hold, don't re-flag.
        until = self._cooldown_until.get(key)
        if until is not None:
            if now < until:
                return LoopVerdict(
                    looping=False, reason=COOLDOWN, count=len(self._fires[key]),
                    cooling_down=True, key=key,
                )
            del self._cooldown_until[key]

        self._fires[key].append(now)
        count = len(self._fires[key])

        # A → event → A traced through the cause chain: an immediate loop.
        if self._self_triggered(key, cause):
            self._cooldown_until[key] = now + self.cooldown_s
            return LoopVerdict(looping=True, reason=SELF_TRIGGER, count=count,
                               cooling_down=False, key=key)

        # Flapping: too many firings of the same key inside the window.
        if count >= self.max_repeats:
            self._cooldown_until[key] = now + self.cooldown_s
            return LoopVerdict(looping=True, reason=REPETITION, count=count,
                               cooling_down=False, key=key)

        return LoopVerdict(looping=False, reason=NONE, count=count,
                           cooling_down=False, key=key)

    def cooling_down(self, key: str, now: float) -> bool:
        """True if ``key`` is within an active cooldown (does not record a firing)."""
        until = self._cooldown_until.get(key)
        return until is not None and now < until

    def reset(self, key: Optional[str] = None) -> None:
        """Clear state for one key, or everything when ``key`` is None."""
        if key is None:
            self._fires.clear()
            self._cooldown_until.clear()
            self._emitted.clear()
            return
        self._fires.pop(key, None)
        self._cooldown_until.pop(key, None)
