"""Canonical JARVIS event type.

Phase 0 of the kernel plan (docs/KERNEL_PLAN.md): a single, pure, serialisable
record for "something happened" — a state change, a camera analysis, a voice turn
— so every subsystem can speak one vocabulary instead of passing around ad-hoc
dicts. This module deliberately has **no Home Assistant import** and no side
effects: it is just the dataclass plus adapters that normalise common sources
into it, which keeps it trivially unit-testable and safe to import anywhere.

Nothing is wired to this yet. Phase 1's event bus will publish ``JarvisEvent``s
in shadow mode and thread ``correlation_id`` through the decision record; until
then this is purely additive.

The adapters are intentionally duck-typed (they read attributes defensively
rather than importing HA types) so they work equally against a real HA ``Event``
and a test fake.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Iterable, Mapping, Optional

# Canonical event-type strings. Kept as module constants so publishers and
# subscribers agree on spelling rather than scattering string literals.
EVENT_STATE_CHANGED = "state_changed"
EVENT_CAMERA_ANALYSIS = "camera.analysis"
EVENT_VOICE_TURN = "voice.turn"
EVENT_ACTUATION = "control.actuation"
EVENT_SITUATION = "situation.transition"


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class JarvisEvent:
    """One thing that happened, in canonical form.

    Immutable by construction (``frozen=True``) because an event is a historical
    fact: derive a changed copy with :meth:`evolve` rather than mutating. ``data``
    is a plain dict for ergonomics and is treated as read-only by convention.

    Fields:
        type:           canonical event type (see the ``EVENT_*`` constants).
        source:         emitting subsystem — "ha", "camera", "voice", "observer".
        subject:        what the event is about (entity_id, person, camera id).
        location:       area / room, when known.
        data:           source-specific payload.
        confidence:     0..1 — how sure we are the event is real/correct.
        importance:     0..1 — salience hint for attention/prioritisation.
        causality:      ids of events that directly led to this one.
        correlation_id: groups one event→decision→outcome chain (set by the bus
                        / ledger in Phase 1; ``None`` until then).
        id:             unique event id (auto-generated).
        ts:             creation time, epoch seconds UTC (auto-generated).
    """

    type: str
    source: str
    subject: Optional[str] = None
    location: Optional[str] = None
    data: dict = field(default_factory=dict)
    confidence: float = 1.0
    importance: float = 0.5
    causality: tuple[str, ...] = ()
    correlation_id: Optional[str] = None
    id: str = field(default_factory=_new_id)
    ts: float = field(default_factory=_now)

    # ── Serialisation ────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """A plain, JSON-friendly dict (``causality`` becomes a list)."""
        d = asdict(self)
        d["causality"] = list(self.causality)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JarvisEvent":
        """Rebuild from :meth:`to_dict` output, ignoring unknown keys."""
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in allowed}
        if kwargs.get("causality") is not None:
            kwargs["causality"] = tuple(kwargs["causality"])
        return cls(**kwargs)

    # ── Derivation (frozen-safe) ─────────────────────────────────────────────
    def evolve(self, **changes: Any) -> "JarvisEvent":
        """Return a copy with ``changes`` applied."""
        return replace(self, **changes)

    def caused_by(self, *parents: "JarvisEvent | str") -> "JarvisEvent":
        """Return a copy whose causality records these parent events/ids."""
        ids = tuple(p.id if isinstance(p, JarvisEvent) else str(p) for p in parents)
        return replace(self, causality=self.causality + ids)

    def with_correlation(self, correlation_id: str) -> "JarvisEvent":
        """Return a copy tagged with a correlation id (chain grouping)."""
        return replace(self, correlation_id=correlation_id)


# ── Source adapters ───────────────────────────────────────────────────────────
# Each returns a JarvisEvent from a common source. Duck-typed on purpose.

def _event_ts(event: Any) -> float:
    """Epoch seconds for an HA Event's ``time_fired`` (datetime), else now."""
    fired = getattr(event, "time_fired", None)
    try:
        return fired.timestamp()  # type: ignore[union-attr]
    except Exception:
        return _now()


def _context_id(event: Any) -> Optional[str]:
    """HA links related events via Event.context.id — reuse it for correlation."""
    ctx = getattr(event, "context", None)
    cid = getattr(ctx, "id", None)
    return str(cid) if cid else None


def _state_value(state: Any) -> Optional[str]:
    return getattr(state, "state", None) if state is not None else None


def from_state_changed(event: Any, *, importance: float = 0.3) -> JarvisEvent:
    """Normalise a Home Assistant ``state_changed`` Event into a JarvisEvent.

    Reads ``event.data`` (entity_id / old_state / new_state), the new state's
    ``area_id`` attribute as location, and ``event.context.id`` as the correlation
    id so HA-caused chains stay linked. Tolerant of partial/fake events.
    """
    data = getattr(event, "data", None) or {}
    entity_id = data.get("entity_id")
    new_state = data.get("new_state")
    old_state = data.get("old_state")
    attrs = getattr(new_state, "attributes", None) or {}
    location = attrs.get("area_id") or attrs.get("area")
    payload = {
        "entity_id": entity_id,
        "old": _state_value(old_state),
        "new": _state_value(new_state),
        "attributes": dict(attrs),
    }
    return JarvisEvent(
        type=EVENT_STATE_CHANGED,
        source="ha",
        subject=entity_id,
        location=location,
        data=payload,
        importance=importance,
        correlation_id=_context_id(event),
        ts=_event_ts(event),
    )


def from_camera_analysis(
    camera_id: str,
    description: str,
    *,
    objects: Optional[Iterable[Any]] = None,
    confidence: float = 1.0,
    importance: float = 0.5,
    location: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> JarvisEvent:
    """A completed camera scene analysis as a JarvisEvent."""
    return JarvisEvent(
        type=EVENT_CAMERA_ANALYSIS,
        source="camera",
        subject=camera_id,
        location=location,
        data={"description": description, "objects": list(objects or [])},
        confidence=confidence,
        importance=importance,
        correlation_id=correlation_id,
    )


def from_voice_turn(
    text: str,
    *,
    speaker: Optional[str] = None,
    intent: Optional[str] = None,
    confidence: float = 1.0,
    importance: float = 0.4,
    location: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> JarvisEvent:
    """A single voice/conversation turn as a JarvisEvent."""
    return JarvisEvent(
        type=EVENT_VOICE_TURN,
        source="voice",
        subject=speaker,
        location=location,
        data={"text": text, "intent": intent},
        confidence=confidence,
        importance=importance,
        correlation_id=correlation_id,
    )


def from_actuation(
    capability: str,
    target: str,
    *,
    intent: Optional[str] = None,
    actor: str = "jarvis",
    location: Optional[str] = None,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    importance: float = 0.5,
) -> JarvisEvent:
    """A control action JARVIS performed on the home, as a JarvisEvent.

    The nervous-system record that *JARVIS acted* — who, what capability, on
    what target, why — distinct from the ``state_changed`` the action causes.
    Emitted by ``control_device`` so an actuation enters the event stream the
    same way perception does (MCU audit item #8, the event bus as the nervous
    system). ``request_id`` links it back to the correlated ``ActuatorRequest``.
    """
    return JarvisEvent(
        type=EVENT_ACTUATION,
        source="actuator",
        subject=target,
        location=location,
        data={"capability": capability, "intent": intent, "actor": actor,
              "request_id": request_id},
        importance=importance,
        correlation_id=correlation_id,
    )


def from_situation(
    kind: str,
    *,
    state: Optional[str] = None,
    action: Optional[str] = None,
    subject: Optional[str] = None,
    location: Optional[str] = None,
    situation_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    importance: float = 0.6,
) -> JarvisEvent:
    """A situation lifecycle transition as a JarvisEvent (MCU Phase D/D4).

    The nervous-system record that a tracked situation (intrusion / hazard /
    delivery / …) opened or changed state — ``kind`` is the situation family,
    ``state`` its resulting lifecycle state, ``action`` the verdict that drove it.
    Emitted by the situation mirrors so situations enter the event stream the same
    way perception and actuation do (MCU audit item #8). Parity: the event is
    published and the ledger records it, but no consumer reacts to it yet.
    """
    return JarvisEvent(
        type=EVENT_SITUATION,
        source="situation",
        subject=subject,
        location=location,
        data={"kind": kind, "state": state, "action": action,
              "situation_id": situation_id},
        importance=importance,
        correlation_id=correlation_id,
    )
