"""JARVIS kernel — canonical types and seams for the staged architecture.

This package is introduced by the kernel migration plan (docs/KERNEL_PLAN.md) as
a quiet foundation layer: pure, well-tested building blocks that the rest of the
integration migrates onto one caller at a time. Nothing here changes behaviour on
its own — Phase 0 is purely additive.

Phase 0 contents:
    * event.py       — the canonical JarvisEvent record + source adapters.
    * persistence.py — a single SQLite access seam (connection + migrations).
"""
from __future__ import annotations

from . import actor, correlation, persistence
from .event import (
    EVENT_ACTUATION,
    EVENT_CAMERA_ANALYSIS,
    EVENT_SITUATION,
    EVENT_STATE_CHANGED,
    EVENT_VOICE_TURN,
    JarvisEvent,
    from_actuation,
    from_camera_analysis,
    from_situation,
    from_state_changed,
    from_voice_turn,
)
from . import (
    actuator,
    agency_state,
    attention,
    authority,
    beliefs,
    budget,
    causal,
    conflict,
    cycle,
    graph,
    identity_fabric,
    journal,
    loop_detect,
    outcome,
    plan,
    provenance,
    uncertainty,
    priority,
    router,
    situation,
    token_telemetry,
)
from .actuator import ActuatorOutcome, ActuatorRequest, build_actuator_request
from .loop_detect import LoopDetector, LoopVerdict
from .journal import ExecutionJournal, JournaledStep, RecoveryReport, recover
from .budget import AgencyBudget, BudgetLimits, BudgetVerdict
from .causal import CausalHypothesis, CausalModel
from .attention import AttentionContext, AttentionDecision, AttentionRequest, arbitrate
from .authority import (
    AuthorityDecision,
    AuthorityParity,
    AuthorityRequest,
    CapabilityToken,
    authorize,
)
from .beliefs import Belief, Evidence
from .graph import Attribute, Entity, KnowledgeGraph, Relation
from .identity_fabric import (
    IdentityAssertion,
    IdentityResolution,
    assert_identity,
    resolve,
)
from .token_telemetry import (
    ModelPrice,
    PriceBook,
    UsageRecord,
    UsageTotals,
    record_usage,
)
from .outcome import Outcome, OutcomeStats, record_outcome
from .provenance import Provenance
from .uncertainty import Uncertain
from .conflict import Resolution as ConflictResolution
from .event_bus import JarvisEventBus
from .plan import Plan, PlanReport, Step, StepOutcome, execute_plan
from .router import Provider, RouteResult, TaskRequirements, route
from .ledger import EventLedger
from .situation import InvalidTransition, Situation, SituationManager
from .world_model import WorldModel

__all__ = [
    "JarvisEvent",
    "EVENT_STATE_CHANGED",
    "EVENT_CAMERA_ANALYSIS",
    "EVENT_VOICE_TURN",
    "EVENT_ACTUATION",
    "EVENT_SITUATION",
    "from_state_changed",
    "from_camera_analysis",
    "from_voice_turn",
    "from_actuation",
    "from_situation",
    "persistence",
    "correlation",
    "actor",
    "agency_state",
    "JarvisEventBus",
    "EventLedger",
    "WorldModel",
    "situation",
    "Situation",
    "SituationManager",
    "InvalidTransition",
    "authority",
    "authorize",
    "AuthorityRequest",
    "AuthorityDecision",
    "AuthorityParity",
    "CapabilityToken",
    "plan",
    "Plan",
    "Step",
    "StepOutcome",
    "PlanReport",
    "execute_plan",
    "beliefs",
    "Belief",
    "Evidence",
    "graph",
    "KnowledgeGraph",
    "Entity",
    "Relation",
    "Attribute",
    "attention",
    "arbitrate",
    "AttentionRequest",
    "AttentionContext",
    "AttentionDecision",
    "router",
    "route",
    "Provider",
    "TaskRequirements",
    "RouteResult",
    "causal",
    "CausalModel",
    "CausalHypothesis",
    "identity_fabric",
    "IdentityAssertion",
    "IdentityResolution",
    "assert_identity",
    "resolve",
    "token_telemetry",
    "ModelPrice",
    "PriceBook",
    "UsageRecord",
    "UsageTotals",
    "record_usage",
    "outcome",
    "Outcome",
    "OutcomeStats",
    "record_outcome",
    "provenance",
    "Provenance",
    "uncertainty",
    "Uncertain",
    "conflict",
    "ConflictResolution",
    "cycle",
    "priority",
    "loop_detect",
    "LoopDetector",
    "LoopVerdict",
    "journal",
    "ExecutionJournal",
    "JournaledStep",
    "RecoveryReport",
    "recover",
    "budget",
    "AgencyBudget",
    "BudgetLimits",
    "BudgetVerdict",
    "actuator",
    "ActuatorRequest",
    "ActuatorOutcome",
    "build_actuator_request",
]
