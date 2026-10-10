#!/usr/bin/env python3
"""Kernel adoption / bypass matrix (kernel hardening H2, docs/KERNEL_PLAN.md).

The migration is a strangler fig: each kernel primitive lands pure and additive,
then the live integration is wired onto it one caller at a time (shadow → parity
→ enforce). The risk the external audit flagged is *silent drift* — a primitive
that exists but nothing live consults, so the kernel looks adopted on paper while
the legacy path still owns the decision.

This script makes that visible. It scans the live integration (everything under
``custom_components/jarvis`` except ``kernel/`` itself and tests) for references
to each kernel primitive, and compares what it finds against the *declared*
adoption stage below. ``--check`` turns a regression into a non-zero exit: a
primitive we claim is wired that no live module imports.

    python3 scripts/kernel_adoption.py            # print the matrix
    python3 scripts/kernel_adoption.py --check     # + fail on drift
    python3 scripts/kernel_adoption.py --markdown  # emit the KERNEL_ADOPTION table

Keep ``_DECLARED`` honest: it is the single source of truth the doc renders from,
and the check only protects stages we have actually reached.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

# ── declared adoption stage per primitive ──────────────────────────────────────
# stage ∈ {"pure", "shadow", "parity", "enforce"}
#   pure    — primitive exists, nothing live wired yet (kernel-only / tests).
#   shadow  — a live module mirrors into it but ignores its output.
#   parity  — a live module computes it alongside the legacy decision, logs
#             agreement, still obeys legacy (log-only).
#   enforce — the kernel decision is authoritative for at least one live path.
# "owners" are the live modules (basename, no .py) expected to reference it once
# the stage is > pure. The check fails if a non-pure primitive has no live ref.
_DECLARED: dict[str, dict] = {
    "event":        {"stage": "parity",  "owners": ["actuation", "camera", "events", "observer", "proactive_audio"]},
    # environment: physical-world intelligence (roadmap Phase X). A pure read-only
    # model of environmental state (temperature / humidity / air-quality / power /
    # apertures as plain Readings) plus objective functions — comfort scoring
    # against ComfortBands and efficiency against a peak threshold — and a
    # recommender that is advisory only and yields to safety: every Recommendation
    # carries a priority tier (comfort=CONVENIENCE, efficiency=HOUSEHOLD) and is
    # flagged blocked_by_safety whenever an active safety concern outranks it
    # (via kernel.priority.may_override), so comfort/efficiency can never override
    # a safety concern. SHADOW (Phase X, 8.171.0): energy.evaluate_for_proactive
    # mirrors the live whole-home draw into environment.efficiency and logs the
    # kernel verdict against its own over_peak decision (environment(shadow),
    # kill-switch energy.ENVIRONMENT_SHADOW). PARITY (Phase X, 8.172.0): over real
    # proactive ticks energy._environment_parity tracks how often the kernel
    # recommender's ACTIONABLE efficiency verdict agrees with the incumbent
    # 'would surface an energy offer' predicate (over peak AND >=2 sheddable
    # loads) and logs the running rate (environment(parity), kill-switch
    # ENVIRONMENT_PARITY) — observe-only, the quantified bar for enforce. Comfort
    # readings + the safety guard wire in when a climate/hazard path adopts it.
    # ENFORCE (Phase X, owner-gated): energy._environment_over_peak makes the
    # kernel recommender's actionable efficiency verdict the authority for the
    # over-peak determination that gates a proactive energy offer, kill-switched
    # (energy.ENVIRONMENT_ENFORCE / `environment_enforce`, default OFF so shipping
    # is behaviour-preserving) and fail-safe (any error falls back to the legacy
    # over_peak threshold; the >=2-sheddable guard is unchanged).
    "environment":  {"stage": "enforce", "owners": ["energy"]},
    # optimize: self-optimization within owner bounds (roadmap Phase Y). A pure
    # measure-then-tune primitive whose core is a TIERED GUARDRAIL: every tunable
    # parameter classifies as SAFE (latency/cost/provider/cache/context/resource —
    # self-tunable within owner Bounds), SENSITIVE (autonomy/confidence/interrupt
    # thresholds, safety model selection — proposal-only, owner-gated) or FORBIDDEN
    # (authority ceiling, security policy, identity requirement, safety threshold,
    # human override, audit retention — never tunable). classify() defaults an
    # UNKNOWN parameter to FORBIDDEN (fail-safe = fixed config). A TuningProposal is
    # auto_applicable only when SAFE and within bounds, proposal_only when SENSITIVE
    # and within bounds, else rejected — so Y can never relax a safety threshold or
    # authority gate, structurally. PURE: no HA import, it proposes and never
    # applies; the live wiring (reading token_telemetry/host metrics, emitting
    # proposals) is the shadow rung.
    "optimize":     {"stage": "pure",    "owners": []},
    # persistence is an internal seam consumed by other kernel modules (ledger),
    # not by live callers directly — so "pure" from a live-adoption standpoint.
    "persistence":  {"stage": "pure",    "owners": []},
    # agency_state: continuity of self (roadmap Phase I). A durable, versioned
    # snapshot of JARVIS's ongoing commitments (active goals, open situations,
    # mode) through the persistence seam, with a continuity summary + reconcile so
    # a restart can know what it was in the middle of. SHADOW (I2, 8.87.0):
    # `continuity` captures a snapshot periodically and logs the continuity
    # summary on boot — it reads live state and writes its own DB + a log line,
    # driving nothing. Boot reconcile is I3 (parity). Phase I-B adds a cognitive
    # snapshot (pure) + capture (shadow) + cognitive reconcile (parity); I-B.4
    # (8.147.0) builds the resume ENFORCE path — `continuity.resume_summary`
    # sources the boot resume from the cognitive snapshot when
    # `CONTINUITY_RESUME_ENFORCE` is on — but that flag DEFAULTS OFF (owner-gated)
    # and no boot path consumes resume yet, so the live stage stays `shadow`.
    "agency_state": {"stage": "shadow",  "owners": ["continuity"]},
    # agency: hierarchical delegation (roadmap Phase O). A pure lifecycle for a
    # parent agency spawning a CHILD agency with a strictly NARROWER capability set
    # (reusing authority.CapabilityToken.derive — one source of truth for the
    # no-escalation rule), a delegation depth (mirroring budget.max_delegation_depth)
    # and a bounded pending→active→settled/failed lifecycle. can_spawn reports the
    # legality + how the request narrows against the parent. SHADOW (Phase O):
    # agent._run_delegated dry-runs the equivalent agency spawn for each delegated
    # sub-agent (FRIDAY/HOMER or a capability group) and logs whether the kernel
    # agrees it is legal — "agency(shadow): child=… depth=… caps=… granted=…
    # dropped=… ok=…" via agency.can_spawn (parent holds '*', child = the resolved
    # tool set, depth bound = MAX_DELEGATION_DEPTH) — observe-only (AGENCY_SHADOW
    # kill-switch), driving nothing; the real tool scoping / depth cap / attribution
    # are untouched. PARITY (Phase O): at each _run_delegated decision point agent
    # also logs whether the kernel spawn verdict AGREES with what the incumbent
    # actually does (proceed vs error) — "agency(parity): … kernel_ok=…
    # incumbent_proceeded=… agree=…". The expected divergence is a disabled FRIDAY
    # profile (kernel allows the declared tools, incumbent refuses via the
    # _profile_enabled opt-in gate) — the signal the enforce rung must close by
    # sitting behind those incumbent gates. ENFORCE (Phase O, owner-gated, default
    # OFF): with AGENCY_ORCHESTRATION_ENFORCE=True the kernel becomes the authority
    # source — agent._run_delegated derives the sub-agent's effective tool set from
    # agency.spawn (a strict narrowing of the incumbent-resolved set) and VETOES the
    # delegation if agency.can_spawn refuses, sitting BEHIND every incumbent gate so
    # it can only narrow. ENFORCE (owner-enabled 8.159.0, AGENCY_ORCHESTRATION_ENFORCE
    # =True): the kernel is the authority source for a delegated sub-agent — its
    # tool set is DERIVED from a kernel agency spawn (strict narrowing) and the
    # delegation is VETOED if the kernel refuses. Behaviour-identical today (JARVIS
    # holds all capabilities, so the derived set == the incumbent set and the veto
    # never fires); it bites once JARVIS's own token is scoped. Fail-safe = the
    # incumbent set (any kernel error passes the incumbent decision through).
    # Kill-switches: AGENCY_SHADOW / AGENCY_PARITY (observe) + AGENCY_ORCHESTRATION_ENFORCE
    # (Settings → Governance). (Peer coordination — kernel/coordination.py — is a
    # later increment of this phase.)
    "agency":       {"stage": "enforce", "owners": ["agent"]},
    # cycle: unified cognitive cycle (roadmap Phase J). A CognitiveCycle runs
    # named injected steps (perceive→interpret→decide→act→reflect) as one
    # instrumented pass with a per-tick correlation id and a CycleTrace. PARITY
    # (J3): cognitive_core._tick runs the cycle alongside its own loop and logs
    # an AGREEMENT flag — the cycle's DECIDE count vs the actions the loop
    # actually dispatched (ACT) — over real traffic; still drives nothing
    # (kill-switch COGNITIVE_CYCLE_SHADOW + config). ENFORCE (J4, owner-gated):
    # cognitive_core._tick's proactive dispatch loop IS the cycle —
    # _cognitive_cycle_plan runs the canonical cycle whose DECIDE phase produces
    # the ordered dispatch plan the loop then performs (ACT). Kill-switched
    # (COGNITIVE_CYCLE_ENFORCE / the `cognitive_cycle_enforce` key, default OFF so
    # the plan is just `actions` — behaviour-preserving); fail-safe — any cycle
    # error falls back to the legacy order, dispatch runs exactly once (no
    # double-fire); safety (intrusion/freeze/lockdown) never routes through it.
    # The J3 parity log is the real-traffic evidence the household watches before
    # flipping it on.
    "cycle":        {"stage": "enforce", "owners": ["cognitive_core"]},
    # graph: typed, queryable knowledge graph (roadmap Phase T, Deep World
    # Model). Entities (nodes with attributes) + typed directed relations, built
    # from plain knowledge.py fact/relation rows with a small query surface
    # (entity / relate / neighbors). SHADOW (Phase T): world_model exposes a
    # graph view (knowledge_graph / entities / relations / query) built from the
    # facts()+relationships() it already reads, and knowledge.all_facts() folds
    # the live facts + relations into a KnowledgeGraph and logs a one-line
    # summary — observe-only (kill-switch GRAPH_SHADOW); nothing reads the graph
    # authoritatively yet. The ENFORCE path is wired but gated OFF
    # (KNOWLEDGE_GRAPH_ENFORCE=False): when the household flips it, the curated-
    # knowledge prompt block becomes graph-authoritative with 1-hop relation
    # expansion and the current recall as the fail-safe; that flip advances this
    # row to "enforce". Until then the live state is shadow.
    # graph: typed, queryable knowledge graph (roadmap Phase T, Deep World Model).
    # Entities + typed relations built from curated knowledge rows. SHADOW:
    # knowledge.all_facts() folds facts + relation edges into a KnowledgeGraph and
    # logs a summary. PARITY: knowledge.prompt_block_async computes the would-be
    # 1-hop graph expansion of the recall seed and logs how many related facts it
    # WOULD add (kill-switch GRAPH_PARITY) — observe-only. ENFORCE (owner-enabled
    # 8.158.0, KNOWLEDGE_GRAPH_ENFORCE=True): the prompt block is graph-authoritative
    # — recall-seeded facts expanded one hop through the graph — fail-safe = flat
    # recall (any failure falls back, so it only ADDS context, never touches
    # actuation). Revert from Settings → Governance (knowledge_graph).
    "graph":        {"stage": "enforce", "owners": ["knowledge"]},
    # space_time: first-class space & time (roadmap Phase Q, Embodied JARVIS). A
    # pure SpatialGraph (areas + undirected adjacency, built from a floor-plan
    # adjacency map like residence_graph.room_adjacency, with neighbor / adjacent
    # / BFS hops / distance / within queries) and a TemporalFrame (an hour+weekday
    # resolved to a coarse daypart, is_weekend / is_daytime). PARITY (Phase Q):
    # world_model exposes a space/time view (spatial_graph() + temporal_frame());
    # the cognitive_core tick logs a one-line "space_time(shadow)" summary, and
    # the intrusion investigator now computes the breach-depth map from the kernel
    # SpatialGraph alongside the incumbent residence_graph.hops_from_breach and
    # logs AGREEMENT/DIVERGENCE ("space_time(parity)") — observe-only (kill-
    # switches SPACE_TIME_SHADOW / SPACE_TIME_PARITY), driving nothing. This
    # parity isolates the kernel BFS as a faithful re-implementation of the
    # incumbent; the camera↔sensor mapping (cf. #140) and the enforce flip
    # (SPACE_TIME_ENFORCE — presence/coverage/routing reading the model
    # authoritatively, fail-safe = present mapping) are later, owner-gated rungs.
    "space_time":   {"stage": "parity",  "owners": ["cognitive_core"]},
    # identity_fabric: Identity & Trust Fabric (roadmap Phase I½, audit-added).
    # An IdentityAssertion carries who / how-established / confidence / expiry /
    # evidence / scope, and resolve() folds many into one verdict that FAILS
    # TOWARD CONFIRMATION when two subjects are too close. Encodes identity ≠
    # presence ≠ authority ≠ trust. PARITY (#237, I½2): identity.resolve() emits
    # an IdentityAssertion for its verdict, runs the fabric resolver over it, and
    # logs AGREEMENT/DIVERGENCE vs the legacy "known person" decision — observe-
    # only, drives nothing. It is EXPECTED to diverge when the legacy resolver
    # calls a presence-only prior "known" (identity ≠ presence). ENFORCE (#237,
    # owner-gated): identity.resolve() downgrades a confident verdict resting only
    # on presence-class signals to UNKNOWN when IDENTITY_FABRIC_ENFORCE / the
    # `identity_fabric_enforce` key is on (default OFF, so behaviour-preserving as
    # shipped); fail-safe — any error leaves the legacy verdict untouched.
    "identity_fabric": {"stage": "enforce", "owners": ["identity"]},
    # token_telemetry: cognitive token & cost telemetry (cross-cutting
    # observability, feeds Phase Y). A UsageRecord captures a call's actual
    # input/output/cached tokens by tier + model, a PriceBook estimates cost,
    # and summarize/by_tier/by_model roll it up. SHADOW (TC2): llm_provider emits
    # a UsageRecord per call from each provider's own usage fields, observe-only
    # (kill-switch TOKEN_TELEMETRY_SHADOW); nothing consumes it yet. Parity vs
    # current estimates, enforce when the panel + Phase Y read it.
    "token_telemetry": {"stage": "shadow", "owners": ["llm_provider"]},
    # outcome: canonical outcome model (Epistemic Fabric, pre-Phase M). A
    # structured Outcome (intended/observed/success/confidence/deviation/cause/
    # side_effects/feedback) with a distilled learning_signal in [-1,1], so M
    # reads structure rather than re-reading logs. SHADOW (#237): actuation.
    # outcome() emits a kernel.outcome.Outcome (via from_verification) alongside
    # the canonical ActuatorOutcome on every verified actuation — log-only,
    # nothing reads it yet. Parity vs current feedback next, enforce when M reads.
    "outcome":      {"stage": "shadow",  "owners": ["actuation"]},
    # provenance: where a piece of state came from (Epistemic Fabric, audit-
    # added). A Provenance pairs a value with source / observed-at / confidence /
    # model / corroboration / expiry; select_authoritative picks the freshest
    # best record and corroborate() merges agreeing records (noisy-OR). Essential
    # for learning, debugging, explanations, security, trust and conflict
    # resolution. SHADOW (#237): knowledge.all_facts() packages each curated fact
    # as a Provenance (value/source/confidence/model), and recognition.who_is_where()
    # packages each recent face recognition it returns (value=name / source=camera /
    # confidence / observed-at / cache-window expiry), logging the authoritative
    # fresh sighting via select_authoritative — observe-only, nothing consumes it
    # yet (WorldModel.provenances() exposes the same view). Parity vs current reads
    # next, enforce when a consumer reads it authoritatively.
    "provenance":   {"stage": "shadow",  "owners": ["knowledge", "recognition"]},
    # uncertainty: first-class "what do I know vs think vs guess" (Epistemic
    # Fabric, audit-added). An Uncertain pairs a value with a confidence, a band
    # (known/believed/guessed/unknown), its basis and what evidence would
    # resolve it; update() folds new evidence (noisy-OR agree, discount
    # disagree). PARITY (#237): identity.resolve() packages its fused verdict as
    # an Uncertain (value=person / the computed confidence / basis=voting methods
    # / resolver for a weak read), logs its epistemic band, AND compares whether
    # gating on the band (is_actionable) agrees with the legacy min-confidence
    # "known" decision — observe-only, the Identification returned is unchanged,
    # nothing gates on the band yet (enforce when a decision gates on the band).
    # COVERAGE (Epistemic Fabric — "world-model wraps outputs too"):
    # knowledge.all_facts() logs the epistemic-band distribution of the curated
    # facts (UNCERTAINTY_SHADOW), broadening the primitive from perception to
    # curated knowledge (observe-only). The kernel world_model.uncertainties()
    # facade exposes the same facts as Uncertain values (kernel-internal, so not a
    # live-adoption owner). The declared stage tracks the furthest anchor
    # (identity, parity).
    "uncertainty":  {"stage": "parity",  "owners": ["identity", "knowledge"]},
    # conflict: formal conflict resolution between contradictory evidence
    # (Epistemic Fabric, audit-added). Consumes Provenance records; resolve()
    # scores each (confidence × source-reliability × recency × corroboration),
    # sums per value, returns the winner, flagging CONTESTED when the runner-up
    # is within a margin so an unclear conflict defers. Must exist before
    # advanced world-model reasoning. SHADOW (#237): presence.get_presence_summary()
    # adjudicates each person's backing device_trackers through conflict.resolve
    # (a Provenance per tracker, reliability by source_type) and logs the winner,
    # whether CONTESTED, and agreement with HA's own person.state — observe-only,
    # HA's resolution still stands. PARITY (Phase K cycle): over a rolling window
    # of those per-person adjudications, presence._emit_presence_conflict_parity
    # logs how often the kernel winner AGREES with HA's native person.state and how
    # often it would DEFER (CONTESTED) — the quantified bar the enforce rung needs
    # (kill-switch CONFLICT_PARITY). Observe-only, drives nothing. MULTI-SOURCE
    # FUSION: WorldModel.fuse_presence adjudicates a person's whereabouts across
    # independent source TYPES (HA person.state vs a recent camera recognition),
    # not just device_trackers, and presence._emit_presence_fusion_shadow logs it
    # (kill-switch PRESENCE_FUSION_SHADOW) — the broader multi-source verdict the
    # enforce rung will read. Enforce (a consumer reads the adjudicated value) is
    # owner-gated.
    "conflict":     {"stage": "parity",  "owners": ["presence"]},
    # temporal: valid-as-of / expires-at (Epistemic Fabric — Time, the fifth
    # primitive). A Validity pairs an observed-at with an optional ttl and derives
    # age / remaining / is_valid / fraction-elapsed and a coarse freshness band
    # (fresh / aging / expired / durable); summarize() rolls a set up by band.
    # Makes time-boundedness first-class alongside uncertainty and provenance —
    # "true 30 min ago / probably still true / expired, re-check". SHADOW spans two
    # sources now: (1) knowledge.all_facts() packages each curated fact's updated_at
    # (valid-as-of) + expires_at (→ ttl; absent ⇒ durable) as a temporal.Validity
    # and logs the freshness-band distribution (kill-switch TEMPORAL_SHADOW); and
    # (2) agent._exec_where_last_seen logs the freshness band of the scene sighting
    # it surfaces for a "where did I last see X" answer, against WHERE_LAST_SEEN_TTL
    # (kill-switch WHERE_LAST_SEEN_TEMPORAL_SHADOW) — the episodic-memory source
    # where staleness is exactly what a future hedge needs. Both observe-only; the
    # fabric now covers semantic + episodic time-boundedness. Ladder from here:
    # shadow → parity → enforce (a decision defers on / hedges a stale value,
    # owner-gated).
    "temporal":     {"stage": "shadow",  "owners": ["agent", "knowledge"]},
    # self_model: JARVIS's honest, inspectable picture of itself (roadmap Phase S).
    # A read-only projection — capabilities (each with a status), current
    # commitments (goals / open situations), overall confidence, known limits —
    # so "what can you do / what are you doing / are you sure?" can be model-backed
    # instead of confabulated. SHADOW: agent._exec_cognitive_status (the
    # cognitive-status introspection read) builds a SelfModel from live inputs —
    # governed capabilities from the enforcement registry (each available when its
    # switch is on), commitments from active goals + open situations, confidence =
    # share of capabilities active, inactive ones as limits — and logs it (and
    # surfaces it in the status JSON, beside the E1 beliefs snapshot), kill-switch
    # SELF_MODEL_SHADOW. Observe-only; no decision consumes it. Hard rule by
    # construction: it only DESCRIBES — it carries no authority and cannot grant,
    # activate or widen anything (authority lives in the authority primitive).
    # Ladder: pure → shadow → parity (self-report vs ground truth) → enforce
    # (self-answers sourced from the model, no confabulation), owner-gated behind
    # SELF_MODEL_ENFORCE (fail-safe = static capability list). PARITY (8.186.0):
    # `agent._emit_self_model_parity` compares the self-model's reported capability
    # availability against an independent ground truth (switch live-enabled AND its
    # backing module resolves) and logs agreement/divergence — isolating the
    # confabulation (reported-available but not actually performable) the enforce
    # rung must forbid. Observe-only; drives nothing.
    "self_model":   {"stage": "parity",  "owners": ["agent"]},
    # long_horizon: durable, resumable, progress-tracked goals spanning days/weeks
    # (roadmap Phase V) — the direct payoff of Phase I (continuity). Models a goal
    # as an ordered set of milestones with a stable id, pure progress derivations
    # (resolved fraction / next milestone / stalled? / complete?) and total
    # transitions (advance returns a new goal), plus a roll-up. PURE: a record +
    # deterministic derivations; nothing persists or consumes it live yet. Ladder:
    # pure → shadow (a live binder persists these and resumes them from
    # agency_state) → parity (resume-after-restart proven vs the journal) → enforce
    # (a multi-day goal survives restarts and drives suggestions), owner-gated
    # behind LONG_HORIZON_ENFORCE (fail-safe = session-scoped goals). SHADOW:
    # continuity._long_horizon_shadow models the live active goals (their steps →
    # milestones) as long_horizon goals on each capture tick and logs a progress
    # roll-up (count / complete / avg progress), kill-switch LONG_HORIZON_SHADOW —
    # observe-only, nothing persists or resumes durably yet.
    "long_horizon": {"stage": "shadow",  "owners": ["continuity"]},
    # learning: closed-loop learning & adaptation (roadmap Phase M). Turns
    # structured kernel.outcome.Outcome records into bounded, reversible
    # WeightAdjustments — a prior nudged toward the mean learning_signal, capped
    # per step and clamped to [floor, ceil], with the prior retained so apply/undo
    # are lossless. Governance invariant (enforced in the math): a protected
    # weight (authority gate / safety threshold) can only tighten, never be
    # relaxed. SHADOW (Phase M): actuation keeps a bounded window of recent
    # kernel.outcome.Outcomes and, per verified actuation, logs the would-be
    # per-capability trust WeightAdjustment learning.adjust() would compute from
    # that capability's track record (a neutral 0.5 prior). PARITY (Phase M):
    # actuation also logs a "learning(parity)" line comparing that learned trust
    # against the capability's realized success_rate (outcome.summarize) and
    # whether they agree on direction — observe-only (kill-switches
    # LEARNING_SHADOW / LEARNING_PARITY), driving nothing. ENFORCE (Phase M,
    # owner-gated): actuation._emit_learning_enforce CLOSES THE LOOP — the learned
    # per-capability trust is persisted (<config>/jarvis/capability_trust.json) and
    # fed back as the prior for the next adjustment, so trust accumulates across
    # restarts instead of resetting to 0.5; actuation.learned_trust(cap) exposes
    # it. Behind LEARNING_ENFORCE / the `learning_enforce` key (default OFF, so
    # behaviour-preserving — prior stays 0.5, store untouched); fail-safe — any
    # error falls back to the neutral prior and persists nothing; the governance
    # clamp (protected weights only tighten) is unchanged.
    "learning":     {"stage": "enforce", "owners": ["actuation"]},
    # autonomy: graduated per-capability trust (roadmap Phase N). Replaces the
    # single autonomy flag with an EARNED autonomy level — a pure function of a
    # capability's verified track record (kernel.outcome.OutcomeStats) and its
    # risk class (authority.sensitivity). Ladder suggest -> confirm -> act; SAFE
    # reads are pinned at act, SENSITIVE actuation earns up from suggest, and
    # SECURITY actuation is pinned at confirm and NEVER auto-promotes. Transitions
    # are bounded (one rung per evaluation) and reversible. SHADOW (Phase N):
    # actuation rolls up the same bounded outcome window it already keeps and, per
    # verified actuation, logs the would-be autonomy level kernel.autonomy.grant()
    # computes for that capability ("autonomy(shadow): …"). PARITY (Phase N):
    # actuation also logs whether that earned level would auto-execute
    # (AutonomyGrant.may_act) AGREES with the single blanket incumbent it refines —
    # the active mode's auto-actions flag (modes.mode_allows_auto_actions) —
    # "autonomy(parity): capability=… earned=… earned_auto=… mode_auto_flag=…
    # agree=…". Per-proactive-pattern trust stays with cognitive_core's
    # AutonomyManager; this axis is the blanket flag only. Observe-only
    # (AUTONOMY_SHADOW / AUTONOMY_PARITY kill-switches), driving nothing; the flag
    # is untouched. ENFORCE (Phase N, owner-gated + user opt-in): cognitive_core's
    # AutonomyManager.is_autonomous consults kernel.autonomy so a granted proactive
    # pattern whose capability is SECURITY-class (pinned at CONFIRM — may_act False
    # regardless of record) NEVER auto-acts; it always falls back to asking. Behind
    # GRADUATED_AUTONOMY_ENFORCE / the `autonomy_enforce` key — default OFF and
    # written only after the panel's full-explanation confirmation screen — so
    # shipping is behaviour-preserving; fail-safe — any error leaves the legacy
    # gate (grant + mode flag) untouched. Safety never routes through this gate.
    "autonomy":     {"stage": "enforce", "owners": ["actuation", "cognitive_core"]},
    "correlation":  {"stage": "shadow",  "owners": ["actuation", "decision_record", "observer", "proactive_audio"]},
    # actor: ambient acting-agent attribution (MCU Phase H, H6). agent._run_delegated
    # brackets a named sub-agent's run in an actor scope; actuation.request /
    # emit_event read it so every actuation the sub-agent performs through the
    # universal seam is recorded as actor="friday"/"homer" (correlated to JARVIS's
    # delegating turn), not falsely as "jarvis". Metadata-only, like correlation —
    # no decision consumes it for control flow, so shadow.
    "actor":        {"stage": "shadow",  "owners": ["actuation", "agent"]},
    "event_bus":    {"stage": "shadow",  "owners": ["__init__"]},
    "ledger":       {"stage": "shadow",  "owners": ["__init__"]},
    # world_model: control_device reads its pre-action context snapshot through
    # the facade (8.30.0, MCU Phase A); cognition (C1), the intrusion-safety
    # presence reads (C2), and the home-summary / briefing context builders (C3)
    # read the world through the facade too — the canonical context authority.
    # ENFORCE (C4, 8.46.0): the facade is *authoritative* on migrated paths, not
    # merely consulted — home_state._build_summary and agent._exec_home_summary
    # read it with no raw fallback, and test_c4_worldmodel_authoritative proves
    # the facade value wins over raw HA state on every migrated read (a regression
    # to a raw sweep fails those tests). Enforce = authoritative on ≥1 live path;
    # the raw sources remain underneath only as a failure fallback for the
    # safety/best-effort callers, and un-migrated context reads elsewhere are
    # still legacy (tracked by the per-release migration, not this stage).
    "world_model":  {"stage": "enforce", "owners": ["actuation", "cognitive_core",
                                                     "home_state", "agent",
                                                     "proactive_briefing"]},
    # situation: ENFORCE (R1b, 8.61.0) — authoritative on >=1 live path (same
    # bar as world_model). package_monitor now keys the delivered/removed
    # transition off the kernel store's per-camera package-present verdict, not
    # the in-memory flag. HONESTY: only the DELIVERY caller is authoritative;
    # hazard (freeze) and intrusion remain parity MIRRORS (their re-architecture
    # is R2 and R3) — see KERNEL_ADOPTION note. Fail-safe + kill-switch.
    "situation":    {"stage": "enforce", "owners": ["intrusion", "hazard_situation",
                                                     "delivery_situation"]},
    # authority: ENFORCE (G4, owner-approved). authority_bridge.enforced_decision
    # makes the kernel capability engine authoritative over the live confirm-gate
    # for an allowlisted set of security capabilities, via a MAX-RESTRICTION belt
    # (can only add a confirmation, never remove a gate), fail-safe to legacy on a
    # kernel fault, with a kill-switch. Authoritative on >=1 live path (the agent
    # control + bulk/plan gates).
    "authority":    {"stage": "enforce", "owners": ["authority_bridge"]},
    # control_device expresses each actuation as a one-step Plan in shadow
    # (actuation.plan_shadow); execute_plan routes each step's execution THROUGH
    # the planner (agent, aexecute_plan) — B4, so parity, not shadow.
    "plan":         {"stage": "parity",  "owners": ["actuation", "agent", "goals"]},
    # beliefs: the cognitive_status tool surfaces a belief snapshot seeded from
    # knowledge confidences via WorldModel.beliefs() + the kernel identity
    # assertion (E1) — shadow: read-only introspection, no decision consumes it.
    "beliefs":      {"stage": "shadow",  "owners": ["agent"]},
    # attention: output_gate computes the kernel interruption arbitration
    # alongside its legacy announce decision and logs divergence (E2) — shadow.
    "attention":    {"stage": "shadow",  "owners": ["output_gate"]},
    # working_memory: a bounded, decay-scored working set feeding attention
    # arbitration (roadmap Phase K). kernel/working_memory.py holds a
    # capacity-bounded set of typed cognitive-context items (situation / objective
    # / intent / people / devices / observations / questions / pending actions &
    # verification / memories / predictions / constraints), scores each by base
    # salience × recency decay, evicts the weakest deterministically, and renders a
    # WorkingSnapshot — the canonical bounded cognitive context a decision engine
    # reads instead of assembling its own. SHADOW (Phase K): cognitive_core._tick
    # folds each tick's cognitive context (home occupancy / sleep situation,
    # people present, actions decided) into the ONE shared kernel working set
    # (working_memory.shared()), a decaying WorkingMemory, and logs the bounded
    # snapshot (kill-switch WORKING_MEMORY_SHADOW + the working_memory_shadow
    # config key). PARITY (Phase K): output_gate's attention arbitration CONSULTS
    # that shared set — it derives a signal the current gate ignores (household
    # asleep, from the situation item) and logs whether consulting the canonical
    # context would CHANGE the arbitration vs the working-memory-blind baseline
    # (kill-switch WORKING_MEMORY_PARITY). The owner chose the shared-singleton
    # architecture. Observe-only, drives nothing. Ladder: pure → shadow → parity →
    # enforce (arbitration reads it authoritatively, behind WORKING_MEMORY_ENFORCE,
    # fail-safe = current attention inputs) — owner-gated.
    "working_memory": {"stage": "parity",  "owners": ["cognitive_core", "output_gate"]},
    # router: reasoning_loop computes the kernel local-first provider route
    # alongside its live cloud/local-Mind breaker decision and logs divergence
    # (E3) — shadow.
    "router":       {"stage": "shadow",  "owners": ["reasoning_loop"]},
    # causal: causal learning + prediction (roadmap Phase L). ΔP contingency
    # hypotheses (cause → effect) with a prediction surface — predict(context) /
    # explain(effect). SHADOW (Phase L): pattern_analyzer.analyze() folds the
    # sequence patterns it detects ("<trigger> then <action>") into a CausalModel
    # and logs a one-line "causal(shadow)" summary. PARITY (Phase L): on each
    # analyze() pass, _emit_causal_parity reconstructs the REAL cause/effect
    # contingency for the top sequence patterns from state history (Option A —
    # event-window: each trigger firing a cause-present trial, effect present if
    # the action follows within the pairing window; cause-absent trials are
    # window-bins with no firing), builds a kernel CausalHypothesis and logs a
    # "causal(parity)" line comparing the ΔP verdict against pattern_analyzer's
    # co-occurrence confidence (how many patterns survive the base-rate correction
    # vs are explained away) — observe-only (kill-switches CAUSAL_PREDICT_SHADOW /
    # CAUSAL_PREDICT_PARITY), driving nothing. Enforce behind CAUSAL_PREDICT_ENFORCE
    # (gating a proactive path, fail-safe = reactive only) is the owner-gated rung.
    "causal":       {"stage": "parity",  "owners": ["pattern_analyzer"]},
    "priority":     {"stage": "pure",    "owners": []},
    # resilience: PURE (Phase Z, Resilient Compute Federation). A local-first,
    # offline-safe fallback policy across compute tiers (LOCAL/EDGE/CLOUD × health).
    # Encodes the HAOS boundary structurally — a canonical-state tier can never be
    # an offload target (the brain stays under the HA integration; external compute
    # is disposable) and the chooser fails safe to the local/canonical tier. No HA
    # import, no I/O, no clock; nothing live consumes it yet (a health poller +
    # the conservative breaker wire onto it at the shadow rung). Ladder from here:
    # pure → shadow (would-be tier choice) → parity (vs current breaker) → enforce
    # (RESILIENCE_ENFORCE, fail-safe = current breaker), owner-gated.
    "resilience":   {"stage": "pure",    "owners": []},
    # surfaces: PURE (Phase AA, Omnipresent Multimodal — presence continuity). A
    # surface registry + a single cross-surface arbiter: one utterance is emitted
    # on exactly ONE surface (no double-announce), mutes honored everywhere, and an
    # Interaction survives a handoff between surfaces (voice → mobile → HUD) keeping
    # its id — presence continuity, not a UI layer. No HA import, no I/O, no clock;
    # nothing live consumes it yet (the registry + announcement path wire on at the
    # shadow rung). Ladder: pure → shadow → parity (which satellite speaks) →
    # enforce (SURFACES_ENFORCE, fail-safe = per-surface current logic), owner-gated.
    "surfaces":     {"stage": "pure",    "owners": []},
    # inquiry: PURE (Phase AD, Research & Discovery — investigative agency).
    # Bounded, cited inquiry with evidence provenance: a Finding is a TYPED claim
    # (fact/observation/inference/prediction/hypothesis/recommendation) with source,
    # confidence and an injected valid-as-of; record() enforces a HARD spend cap
    # (a finding that would overrun the budget is refused); synthesize() picks the
    # best-grounded claim and CAPS confidence when a contradiction is present (never
    # false certainty), surfacing contradictions rather than hiding them. No HA
    # import, no I/O, no clock, no network; nothing live consumes it yet. Ladder:
    # pure → shadow (dry investigations) → parity (vs direct answer) → enforce
    # (INQUIRY_ENFORCE, fail-safe = direct answer / no investigation), owner-gated.
    "inquiry":      {"stage": "pure",    "owners": []},
    # household: PURE (Phase P, Proactive Household Intelligence). An occupancy
    # rhythm (per-daypart occupancy likelihood) + a routine graph (recurring
    # activity transitions) derived from plain observation rows. The invariant is
    # structural: anticipate() emits advisory Suggestions that carry NO actuator —
    # the model may only propose; acting stays the authority/actuation seam's job
    # (suggest, never silent actuation). No HA import, no I/O, no clock; nothing
    # live consumes it yet. Ladder: pure → shadow (infer routines) → parity
    # (suggestions vs heuristics) → enforce (HOUSEHOLD_PROACTIVE_ENFORCE, fail-safe
    # = current heuristics), owner-gated.
    "household":    {"stage": "pure",    "owners": []},
    # privacy: PURE (Phase W prerequisite — information-flow / privacy boundary).
    # The audit requires an explicit information-flow policy to land BEFORE the
    # social model: a pure decision over a labelled DataItem (classification /
    # subject / purpose / consent / audience / source). The load-bearing rule is
    # structural and fail-closed: a PERSONAL/SENSITIVE item about person A never
    # flows to a different person B without A's explicit consent (SENSITIVE also
    # needs a matching purpose), and an unknown classification denies. No HA import,
    # no I/O, no clock, no storage; nothing live consumes it yet. Ladder: pure →
    # shadow → parity → enforce (SOCIAL_MODEL_ENFORCE gates W on top of it),
    # owner-gated.
    "privacy":      {"stage": "pure",    "owners": []},
    # loop_detect: ENFORCE (G2, owner-approved staged roll-out). actuation.loop_detect_check
    # is authoritative for the discretionary autonomous proactive path
    # (cognitive_core._execute_action_data): a thrashing/self-triggering action
    # is actually SUPPRESSED. Same tight scope as budget (G1) — user-requested and
    # safety-critical actuations never route through it — high threshold, fails
    # open, one-line kill-switch LOOP_DETECT_ENFORCE. (The envelope-wide E4 shadow
    # lens in _agency_shadow still logs thrash across the whole actuation stream.)
    "loop_detect":  {"stage": "enforce", "owners": ["actuation"]},
    # journal: goals records each goal's shadow Plan into the execution journal
    # (F2), making the goal->plan->step chain durably reconstructable — shadow.
    "journal":      {"stage": "shadow",  "owners": ["goals"]},
    # budget: ENFORCE (G1, owner-approved staged roll-out). actuation.agency_budget_check
    # is authoritative for the discretionary autonomous proactive path
    # (cognitive_core._execute_action_data): when the self-imposed hourly ceiling
    # is reached, the autonomous actuation is actually BLOCKED. Scope is tight —
    # user-requested and safety-critical actuations never route through the gate,
    # it fails open, and a one-line kill-switch (AGENCY_BUDGET_ENFORCE) reverts it
    # to shadow. Enforce = authoritative on >=1 live path.
    "budget":       {"stage": "enforce", "owners": ["actuation"]},
    # actuator: ENFORCE (MCU Phase H, H1). actuation.execute_actuator is the
    # universal actuator SEAM — one authoritative Execution (through the kernel
    # planner) -> Event -> Verification/Outcome composition. control_device,
    # bulk_control and scenes (agent) route execution through it directly; the
    # discretionary proactive path (H5), the SAFETY securing path (H8, via
    # actuation.execute_safety_actuator) and the offline local fast-path (H9,
    # local_engine via actuation._seam_execute) do too, as seam *consumers*
    # through actuation (so the direct kernel-primitive referencers stay
    # actuation/agent). H8 closed the last SAFETY direct bypass; H9–H11 migrated
    # the remaining alternate execution paths (local_engine, intent_router,
    # mode_scene, scenes), and scripts/kernel_bypass_check.py now proves the
    # Phase H exit criterion: every world-mutating service call converges on the
    # seam or is classified (communications / read-only / meta / the generic
    # user-authored routine runner).
    "actuator":     {"stage": "enforce", "owners": ["actuation", "agent"]},
}

_STAGE_ICON = {"pure": "·", "shadow": "◐", "parity": "◑", "enforce": "●"}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def _component_dir() -> pathlib.Path:
    return _repo_root() / "custom_components" / "jarvis"


def _kernel_primitives(kernel_dir: pathlib.Path) -> list[str]:
    return sorted(
        p.stem
        for p in kernel_dir.glob("*.py")
        if p.stem != "__init__" and "__pycache__" not in str(p)
    )


def _live_modules(comp: pathlib.Path) -> list[pathlib.Path]:
    out = []
    for p in comp.rglob("*.py"):
        s = str(p)
        if "__pycache__" in s:
            continue
        # Skip the kernel package itself and any test trees.
        rel = p.relative_to(comp)
        if rel.parts and rel.parts[0] == "kernel":
            continue
        if "tests" in rel.parts:
            continue
        out.append(p)
    return out


def _module_symbols(kernel_dir: pathlib.Path) -> dict[str, str]:
    """Map every public symbol a kernel module exports → its module name.

    So that ``from .kernel import EventLedger`` in a live file counts as a
    reference to the ``ledger`` primitive, not just a bare ``kernel.ledger``.
    """
    index: dict[str, str] = {}
    for p in kernel_dir.glob("*.py"):
        if p.stem == "__init__" or "__pycache__" in str(p):
            continue
        tree = ast.parse(p.read_text())
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    index[node.name] = p.stem
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        index[tgt.id] = p.stem
    return index


def _referenced_primitives(text: str, sym_index: dict[str, str],
                           primitives: list[str]) -> set[str]:
    """Which kernel primitives a live module references, by module name,
    dotted use, or any exported symbol imported from ``.kernel``."""
    found: set[str] = set()
    for prim in primitives:
        pats = (
            rf"\bfrom\s+\.kernel\s+import\b[^\n]*\b{prim}\b",
            rf"\bfrom\s+\.kernel\.{prim}\b",
            rf"\bimport\s+[^\n]*\bkernel\.{prim}\b",
            rf"\bkernel\.{prim}\b",
        )
        if any(re.search(p, text) for p in pats):
            found.add(prim)
    # Symbols imported from the kernel package map back to their module.
    for m in re.finditer(r"from\s+\.kernel\s+import\s+\(?([^)\n]+)\)?", text):
        for name in re.split(r"[,\s]+", m.group(1)):
            name = name.strip().split(" as ")[0].strip()
            if name in sym_index:
                found.add(sym_index[name])
    return found


def scan() -> dict[str, dict]:
    comp = _component_dir()
    kernel_dir = comp / "kernel"
    primitives = _kernel_primitives(kernel_dir)
    sym_index = _module_symbols(kernel_dir)
    live = _live_modules(comp)
    live_refs = {
        p: _referenced_primitives(p.read_text(), sym_index, primitives)
        for p in live
    }

    def _label(path: pathlib.Path) -> str:
        rel = path.relative_to(comp)
        # Disambiguate package __init__ files by their directory.
        if path.stem == "__init__":
            return "__init__" if rel.parent == pathlib.Path(".") else f"{rel.parent}/__init__"
        return path.stem

    rows: dict[str, dict] = {}
    for prim in primitives:
        users = sorted(
            _label(p) for p, refs in live_refs.items() if prim in refs
        )
        decl = _DECLARED.get(prim, {"stage": "pure", "owners": []})
        rows[prim] = {
            "stage": decl["stage"],
            "declared_owners": decl.get("owners", []),
            "live_users": users,
        }
    return rows


def drift(rows: dict[str, dict]) -> list[str]:
    """Problems: a non-pure primitive with no live reference, or an undeclared
    primitive (one on disk missing from ``_DECLARED``)."""
    problems = []
    for prim, row in rows.items():
        if prim not in _DECLARED:
            problems.append(f"{prim}: on disk but missing from _DECLARED")
            continue
        if row["stage"] != "pure" and not row["live_users"]:
            problems.append(
                f"{prim}: declared '{row['stage']}' but no live module references it"
            )
    for prim in _DECLARED:
        if prim not in rows:
            problems.append(f"{prim}: declared but no kernel/{prim}.py on disk")
    return problems


def render_markdown(rows: dict[str, dict]) -> str:
    lines = [
        "| Primitive | Stage | Live callers |",
        "| --- | --- | --- |",
    ]
    for prim in sorted(rows):
        row = rows[prim]
        icon = _STAGE_ICON.get(row["stage"], "?")
        users = ", ".join(f"`{u}`" for u in row["live_users"]) or "—"
        lines.append(f"| `{prim}` | {icon} {row['stage']} | {users} |")
    return "\n".join(lines)


def render_table(rows: dict[str, dict]) -> str:
    out = []
    width = max(len(p) for p in rows)
    for prim in sorted(rows):
        row = rows[prim]
        icon = _STAGE_ICON.get(row["stage"], "?")
        users = ", ".join(row["live_users"]) or "—"
        out.append(f"  {icon} {prim.ljust(width)}  {row['stage']:<7}  {users}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    rows = scan()
    if "--markdown" in argv:
        print(render_markdown(rows))
    else:
        print("Kernel adoption matrix "
              "(· pure  ◐ shadow  ◑ parity  ● enforce)\n")
        print(render_table(rows))

    if "--check" in argv:
        problems = drift(rows)
        if problems:
            print("\nDRIFT:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("\nadoption OK — every declared stage has a live caller")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
