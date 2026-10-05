"""Unified cognitive cycle (roadmap Phase J — Cognitive OS).

Today JARVIS runs several parallel, ad-hoc loops (observer, cognitive_core,
proactive, goals), each perceiving / deciding / acting on its own cadence. Phase
J introduces one explicit, instrumented cognitive cycle —
perceive → interpret → decide → act → reflect — that a subsystem ticks through,
so there is a single named sequence with one correlation id per pass and a
reconstructable trace of what each step did.

Phase J lands this primitive **pure**: a :class:`CognitiveCycle` sequences
injected, named steps over a shared mutable context and records a
:class:`CycleTrace` of each step's outcome. Nothing live runs through it yet
(a subsystem runs a cycle alongside its own loop in J2/shadow, parity in J3,
enforce in J4). No Home Assistant import — steps are injected callables — so it
is deterministic and unit-testable, and a failing step is recorded, never raised
into the caller.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

# The canonical phases of one cognitive pass, in order.
PERCEIVE = "perceive"
INTERPRET = "interpret"
DECIDE = "decide"
ACT = "act"
REFLECT = "reflect"
PHASES: Tuple[str, ...] = (PERCEIVE, INTERPRET, DECIDE, ACT, REFLECT)

# What to do when a step raises: record it and keep going, or halt the pass.
ON_ERROR_CONTINUE = "continue"
ON_ERROR_STOP = "stop"


@dataclass(frozen=True)
class StepResult:
    """Outcome of one step within a cycle pass."""

    name: str
    ok: bool
    detail: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class CycleTrace:
    """The reconstructable record of one cognitive pass."""

    cycle_id: str
    started_ts: float
    steps: Tuple[StepResult, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every step that ran succeeded."""
        return all(s.ok for s in self.steps)

    @property
    def failed(self) -> List[StepResult]:
        return [s for s in self.steps if not s.ok]

    @property
    def names(self) -> List[str]:
        return [s.name for s in self.steps]

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "started_ts": self.started_ts,
            "ok": self.ok,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass(frozen=True)
class CycleStep:
    """A named step: ``fn(context)`` runs the step, may read/mutate the context."""

    name: str
    fn: Callable[[Dict[str, Any]], Any]


class CognitiveCycle:
    """An ordered sequence of named steps run as one instrumented pass.

    ``tick(context)`` runs each step in order over a shared mutable context dict,
    stamping a fresh ``cycle_id`` (the pass's correlation id) into it, and returns
    a :class:`CycleTrace`. A step that raises is recorded as a failed
    :class:`StepResult`; with ``on_error="continue"`` (the default) the pass goes
    on, with ``"stop"`` it halts after the failure. ``tick`` never raises.
    """

    def __init__(
        self,
        steps: Iterable[CycleStep],
        *,
        on_error: str = ON_ERROR_CONTINUE,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._steps: List[CycleStep] = [s for s in steps if isinstance(s, CycleStep)]
        self._on_error = on_error if on_error in (ON_ERROR_CONTINUE, ON_ERROR_STOP) else ON_ERROR_CONTINUE
        self._now = now or time.time

    @property
    def step_names(self) -> List[str]:
        return [s.name for s in self._steps]

    def tick(self, context: Optional[Mapping[str, Any]] = None) -> CycleTrace:
        ctx: Dict[str, Any] = dict(context or {})
        cycle_id = str(ctx.get("cycle_id") or uuid.uuid4().hex)
        ctx["cycle_id"] = cycle_id
        started = float(self._now())
        results: List[StepResult] = []
        for step in self._steps:
            t0 = self._now()
            try:
                step.fn(ctx)
                ok, detail = True, ""
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
                _LOGGER.debug("cognitive cycle: step %r failed: %s", step.name, exc)
            duration_ms = round((float(self._now()) - float(t0)) * 1000.0, 3)
            results.append(StepResult(step.name, ok, detail, duration_ms))
            if not ok and self._on_error == ON_ERROR_STOP:
                break
        return CycleTrace(cycle_id=cycle_id, started_ts=started, steps=tuple(results))


def standard_cycle(
    handlers: Mapping[str, Optional[Callable[[Dict[str, Any]], Any]]],
    **kwargs: Any,
) -> CognitiveCycle:
    """Build a cycle over the canonical :data:`PHASES` from a ``{phase: fn}`` map.

    Phases absent from the map (or mapped to a falsy handler) are skipped, so a
    caller can provide only the stages it implements while keeping canonical
    order. Extra ``kwargs`` pass through to :class:`CognitiveCycle`.
    """
    steps = [CycleStep(p, handlers[p]) for p in PHASES if handlers.get(p)]
    return CognitiveCycle(steps, **kwargs)
