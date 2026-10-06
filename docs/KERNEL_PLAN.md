# JARVIS Kernel — phased plan of attack

Status: **in progress.** This is the plan of record for consolidating JARVIS's
subsystems into one coherent operating loop ("the kernel"), in response to the
v8.4.0 architecture audit. It is written as a **strangler-fig migration** — wrap
and absorb the working subsystems incrementally — **not** a big-bang rewrite. Each
phase ships as its own release with green CI and a parity/regression guard, so
JARVIS stays shippable throughout.

### Progress

| Phase | Status | Release(s) |
| --- | --- | --- |
| 0 — Foundations & guardrails | ✅ Shipped | 8.4.3 (HA lifecycle tests + CI gate), 8.5.0 (`JarvisEvent` + persistence seam) |
| 1 — Event bus + correlated ledger | ✅ Shipped | 8.6.0 |
| 2 — World-model facade | ✅ Shipped | 8.7.0 |
| 3 — Situation manager | ✅ Shipped | 8.8.0 (machine), 8.8.1 (intrusion shadow) |
| 4 — Authority / capability engine | 🚧 In progress | 8.9.0 |
| 5 — Planner → Executor → Verifier | 🚧 In progress | 8.10.0 |
| 6 — Beliefs · Attention · Model Router | 🚧 In progress | 8.11.0 |
| 7 — Causal learning | 🚧 In progress | 8.12.0 |

_Kept current as each phase merges._ **All phase primitives (0–7) are now shipped additively** (shadow / parity / opt-in); 🚧 marks phases whose remaining work is wiring the existing consumers to enforce the new primitive (tracked in each phase's section).

The audit's own conclusion is the premise here: the gap is **consolidation, not
features**. Most of the "missing" pieces already exist as strong but parallel
subsystems; the work is giving them a common spine, not rebuilding them.

A second external audit (2026-10) reviewed the post-H roadmap itself and is now
folded into "Roadmap — post-H maturity tiers" below: measure progress by
*authoritative dependence* not existence, add **Identity & Trust** and an
**Epistemic Fabric** (Outcome · Provenance · Uncertainty · Conflict · Time) as
first-class primitives, move world-model/space-time ahead of prediction/learning,
and split the single definition of done into three bars (H, R, AE).

## Guiding principles

1. **No big-bang.** The kernel is introduced as seams that existing modules opt
   into one at a time — never a flag-day rewrite of `cognitive_core` / `agent` /
   `observer`.
2. **Shadow → parity → enforce.** Every behavior-bearing kernel piece first runs
   alongside the current path, recording what it *would* do; we prove parity on
   real/recorded traffic; then we flip it to authoritative.
3. **One release per phase.** Full unit suite plus the HA lifecycle integration
   tests must be green before each phase merges.
4. **The invariants are the contract** (enforced progressively):
   - No actuator bypasses authority.
   - Sub-agents cannot expand their own capabilities.
   - Security-sensitive capabilities require explicit authority.
   - Every actuator has postcondition verification.
   - Every autonomous operation carries correlation and idempotency identifiers.
   - Credentials never enter prompts or LLM-visible context.
   - Local-first routing is preferred when capability and quality permit.
   - Blocking event-loop I/O is prohibited.
   - Supervisor interaction is optional, bounded, and diagnosable.

## What already exists (so we wrap, not rebuild)

| Kernel concept (audit) | Existing substrate to absorb |
| --- | --- |
| Audit ledger (event → decision → outcome) | `decision_record.py` (immutable observation/interpretation/decision/outcome) + `feedback.py` |
| Planner → Executor → Verifier | `goals.py`, `agent.execute_plan`, verify-after-act |
| World model | `knowledge.py` (facts + relations graph + semantic recall), `identity.py`, `vision/scene_memory.py` |
| Situation machine | `intrusion.py` (normal → investigating → confirmed) |
| Authority | `voice_confirm.py`, `output_gate.py`, autonomy / `manage_autonomy` |
| Attention arbitration | `output_gate.py`, adaptive interruption budget |
| Model router | `llm_provider.py` tiers (classifier / reasoning / review / vision) |
| Fallback chain | `local_mind.py`, `local_engine.py`, connectivity breaker |
| Causal-learning seed | `rca.py`, `pattern_analyzer.py`, `feedback.py` |
| Multi-agent | `agent.py` FRIDAY/HOMER delegation (`_run_delegated`) |

---

## Phase 0 — Foundations & guardrails (prerequisite)

Target release: **8.5.0** — **✅ Shipped** (HA lifecycle tests + CI gate in 8.4.3; `JarvisEvent` + persistence seam in 8.5.0).

- **HA lifecycle integration tests in CI.** A hard prerequisite: without a real
  setup / reload / unload test exercised in CI, later kernel refactors can
  silently break the integration. (Blocked on a local-PHACC run to land the
  loader-discovery fix; see "Open blocker" below.)
- **Canonical `JarvisEvent`.** A pure, tested dataclass in `kernel/event.py`:
  `id, ts, source, type, subject, location, data, confidence, importance,
  causality, correlation_id`. No wiring yet — just the type plus adapters that
  convert an HA `state_changed`, a camera analysis, or a voice turn into one.
- **Unified DB access seam.** A thin `kernel/persistence.py` wrapping
  `sqlite_utils.ClosingConnection` plus a migrations hook, so every store opens
  connections one way. **No schema merge** (high churn, low gain); just
  consistent access and a home for migrations.

Risk: low (all additive). Guard: full suite unchanged.

## Phase 1 — Event bus + correlated ledger

Target release: **8.6.0** — highest leverage, lowest risk. **✅ Shipped** (shadow-mode bus + buffered ledger; `correlation_id` through `decision_record`).

- `kernel/event_bus.py`: an in-process pub/sub. `observer`, `camera`,
  `proactive_audio`, and the voice path publish `JarvisEvent`s **in shadow mode**
  — existing code paths remain authoritative; the bus only records.
- Thread `correlation_id` through `decision_record` so every
  event → decision → outcome links into one chain (the audit's single
  explainable chain).

Deliverable: a queryable, correlated decision trail — which also makes every
later phase debuggable. Risk: low (shadow-only). Guard: no behavior change.

## Phase 2 — World-model facade

Target release: **8.7.0** — **✅ Shipped** (`kernel/world_model.py` read facade + parity tests).

- `kernel/world_model.py`: a **read facade** over HA state + the knowledge graph +
  identity + scene memory, answering in canonical terms (people / rooms / devices
  / activities / relationships). Reasoning paths migrate to it one caller at a
  time; raw HA entity access remains underneath.

Risk: low (read-only). Guard: per-caller opt-in; parity test vs. direct HA reads.

## Phase 3 — Situation manager

Target release: **8.8.0** — **✅ Shipped** (durable `kernel/situation.py` machine in 8.8.0; `intrusion` adopted it in shadow mode in 8.8.1, running parallel to the authoritative path until verdicts match).

- `kernel/situation.py`: generalize `intrusion.py`'s state machine into durable,
  correlated situations (normal → possible → investigating → confirmed/benign →
  response → resolved) fed by the event bus. `intrusion` is the first consumer;
  delivery and hazard flows migrate later.

Risk: medium. Guard: `intrusion` runs new + old in parallel; flip only once
verdicts match on recorded history.

## Phase 4 — Authority / capability engine (the safety keystone)

Target release: **8.9.0** — **🚧 In progress** (pure `kernel/authority.py` engine + capability tokens shipped in 8.9.0; `voice_confirm`/`output_gate`/autonomy delegating in log-only parity mode, then enforce, to follow).

- `kernel/authority.py`: one capability check (capability, identity, context,
  situation, confidence, time, intent, scope). `voice_confirm` / `output_gate` /
  autonomy delegate to it. Capability **tokens** for FRIDAY/HOMER so delegation
  cannot escalate.
- Every actuator routes through it.

Risk: high — so ship in **log-only / allow-as-before** mode first, prove it
reaches the same allow/deny as today on real traffic, then enforce. Guard:
parity log + the invariant tests.

## Phase 5 — Planner → Executor → Verifier

Target release: **8.10.0** — **🚧 In progress** (pure `kernel/plan.py` plan/step objects + executor/verifier with idempotency shipped in 8.10.0; `goals`/agent adoption to follow).

- Formalize `goals.py` + agent execution into explicit plan objects with
  preconditions, postcondition verification, and `idempotency_key`.
  Verify-after-act becomes first-class rather than ad hoc.

Risk: medium. Guard: existing goals/followups regression suite + new verifier
tests.

## Phase 6 — Beliefs · Attention · Model Router

Target release: **8.11.0** — **🚧 In progress** (pure `kernel/beliefs.py`, `kernel/attention.py`, `kernel/router.py` shipped in 8.11.0; callers delegate to them, parity-checked, to follow. Router lives in `kernel/` to share the kernel test harness.)

- `kernel/beliefs.py`: probabilistic beliefs (evidence, source, decay,
  contradiction) seeded from knowledge confidence.
- `kernel/attention.py`: centralized interruption arbitration (absorbs
  `output_gate` + the adaptive interruption budget).
- `providers/router.py`: formalized tier routing (local-first; capability /
  privacy / latency / cost / availability).

Risk: medium. Guard: attention parity vs. current gate decisions.

## Phase 7 — Causal learning

Target release: **8.12.0** — **🚧 In progress** (pure `kernel/causal.py` ΔP-based causal model shipped in 8.12.0; `pattern_analyzer`/`rca`/`feedback` adoption to follow).

- Extend `pattern_analyzer` + `rca` + `feedback` toward
  observation → hypothesis → action → outcome → causal confidence.

Risk: medium; isolated to the learning layer.

---

## Post-migration hardening (external audit follow-up)

After Phases 0–7 shipped the kernel primitives, an external architecture audit
(ChatGPT; `docs` upload) was reviewed against the codebase. ~40% of it was
already shipped and a few items were over-engineered for a single-home
deployment; the usable, novel work is tracked here, each as its own release.

| # | Hardening item | Status | Release |
| --- | --- | --- | --- |
| H1 | Authority: capability expiry/revocation + log-only parity tracker | ✅ Shipped | 8.13.0 |
| H2 | Kernel-adoption / bypass matrix + JARVIS Constitution + emergency hierarchy | ✅ Shipped | 8.14.0 |
| H3 | Loop detection (action → event → action) | ✅ Shipped | 8.15.0 |
| H4 | Execution journal + crash recovery | ✅ Shipped | 8.16.0 |

Deferred as over-engineered for this deployment (not planned): a full 7-type
memory-lifecycle taxonomy, a broad logical-persistence API, saga-style
compensation beyond verify+idempotency, and a full event replay/simulation
subsystem. Hard authority *enforcement* (flipping parity → deny) is a separate,
owner-gated step once parity holds on real traffic.

## MCU migration (second external audit follow-up)

The second audit's thesis: *"JARVIS needs to become the kernel, not just have
one."* Most kernel primitives exist but aren't yet authoritative (the honest
`KERNEL_ADOPTION.md` / `KERNEL_COVERAGE.md` state). The net-new, right-sized items
from that audit — each a feature release, each additive, authority still
owner-gated:

| # | MCU item | Status | Release |
| --- | --- | --- | --- |
| A1 | Behavioral coverage matrix (path × contract) + CI gate | 🚧 In progress | 8.22.0 |
| A2 | Executable Constitution (invariant-violation tests) | ✅ Shipped | 8.23.0 |
| A3 | Agency Budget (rate / retry / delegation-depth caps) | ✅ Shipped | 8.24.0 |
| A4 | Richer authority **parity** inputs (situation/scope/intent/token) — still log-only | ✅ Shipped | 8.25.0 |
| A5 | Universal `ActuatorRequest` contract + route `control_device` through it (shadow/parity) | ✅ Shipped | 8.26.0 |

Held as north-star at the time of that audit (not near-term then): persistent
agency / continuity-of-self, a graduated-autonomy state machine, a full
IdentityAssertion subsystem, closing the full learning loop, and a single
physical persistence store. Continuity-of-self is now in active, incremental work
as **Phase I** (see "Roadmap — post-H maturity tiers" below); it lands shadow →
parity → enforce like everything else, so a lived-in system stays stable. Hard
authority enforcement remains owner-gated.

### A1 — Behavioral coverage matrix (8.22.0)

- `scripts/kernel_coverage.py` + `KERNEL_COVERAGE.md`: measures what fraction of
  behaviour-bearing paths (`control_device`, `bulk_control`, `execute_plan`,
  `goals`, `intrusion`, `proactive`, `friday`, `homer`) actually pass through each
  kernel contract (Event → WorldModel → Situation → Authority → Plan → Verify →
  Outcome), as `none/shadow/parity/full`. Every non-`none` cell is verified
  against evidence in the path's source, and `--check` is a CI gate. Honest
  starting number: **4.2%** — the figure to move as paths migrate.

### H1 — Authority hardening (8.13.0)

- `CapabilityToken` gains `expires_at` + `token_id`; `authorize` denies an
  expired or revoked token; `derive(..., ttl=)` clamps a child's expiry to its
  parent's (a child never outlives its issuer).
- `AuthorityParity`: a log-only tracker comparing the engine's decision to the
  actual behaviour, so enforcement is flipped on only once parity holds.

### H2 — Adoption visibility + invariants (8.14.0)

- `kernel/priority.py`: the emergency/priority ladder (life safety → security →
  property → household → convenience → personality) as one pure comparison, with
  the invariant *personality never overrides a safety concern* encoded in
  `may_override`.
- `docs/JARVIS_CONSTITUTION.md`: the short, stable set of inviolable invariants
  (precedence, authority is log-only + owner-gated, fail-safe defaults, additive
  change discipline).
- `KERNEL_ADOPTION.md` + `scripts/kernel_adoption.py`: a living adoption/bypass
  matrix that scans live code for kernel references and compares against the
  declared stage (pure → shadow → parity → enforce). `--check` runs in CI and
  fails on drift — a primitive claimed adopted that nothing live consults.

### H3 — Feedback-loop detection (8.15.0)

- `kernel/loop_detect.py`: a pure `LoopDetector` that spots a thrash loop —
  either **repetition** (the same action fires too many times inside a sliding
  window) or a **self-trigger** (A → event → A traced through a cause chain) —
  and applies a **cooldown** after flagging so the caller can break the cycle
  instead of re-detecting it. No HA import, no wall clock (`now` is passed in),
  so it is deterministic and testable; it reports, it never acts.

### H4 — Execution journal + crash recovery (8.16.0)

- `kernel/journal.py`: an `ExecutionJournal` that writes each plan step's
  lifecycle (`record_plan` → `start_step` RUNNING → `finish_step` DONE/FAILED/…)
  durably through `kernel.persistence`, so after a restart `in_flight()` returns
  exactly the steps that were started but never resolved.
- `recover(journal, verify=, act=)`: settles each in-flight step by asking live
  state (injected `verify`) whether it actually completed — verified → DONE,
  otherwise re-run via the injected `act` or flagged `NEEDS_REPLAY`, honouring
  idempotency so recovery never double-acts. Injected checks keep it
  deterministic and HA-free; it runs on a real temp DB in tests.

**This completes the post-migration hardening shortlist (H1–H4).**

---

## Roadmap — post-H maturity tiers (Phase I onward)

With the MCU "universal agency spine" complete (every consequential actuation
converges on the kernel, journal-reconstructable, zero bypasses), the roadmap
turns to the maturity tiers that were previously held as north-star. They follow
the same discipline as everything above: **one release per increment, additive,
shadow → parity → enforce, authority/safety owner-gated, every gate green.**
More capability never means less governance.

### External audit (2026-10) — five structural corrections

A second-opinion gap audit reviewed this roadmap against the current code. Its
verdict: *the roadmap is pointed in the right direction and should not be thrown
away, but it describes **what capabilities** JARVIS should acquire, when the
master plan needs to describe **what information and authority must flow
between** them.* That distinction decides whether this becomes one coherent
JARVIS or an impressive collection of AI subsystems. Five corrections are now
folded in below; the phase letters are kept stable so the backlog stays legible.

1. **Progress is measured by authoritative dependence, not existence.**
   `kernel/foo.py` existing is not JARVIS *using* `foo` as its cognitive
   substrate. The real metric is already the adoption matrix's own axis: a
   primitive counts only at the stage its authoritative runtime actually depends
   on it (`pure` < `shadow` < `parity` < `enforce`). Every phase's "Done" below
   is an **enforce** claim about a live path, never "the primitive exists."
2. **Identity & Trust becomes a first-class phase** (new **Phase I½**), before
   serious autonomy — `IdentityAssertion` with subject, source, method,
   confidence, expiry, evidence, scope; and the rule *identity ≠ presence ≠
   authority ≠ trust*.
3. **Outcome, Provenance, Uncertainty, Conflict and Time become kernel
   primitives** — a cross-cutting **Epistemic Fabric** (new tier below) that
   several later phases depend on, rather than things each phase re-invents.
4. **World-model / space-time foundations move ahead of serious prediction and
   learning** (T and Q before L and M), so prediction is not built on fragmented
   legacy representations and rebuilt later.
5. **R becomes an integration gate and AE the MCU certification gate** — two
   different bars (see the three definitions of done at the end of this file),
   and **O/AB** and **N/AC** are merged as the backlog already recommended.

The audit also expands **Phase H's** definition of done: not merely "no
consequential actuator bypass," but every object on the
intent → situation → plan → authority → budget → actuator → HA → observation →
verification → outcome → journal chain sharing one agency identity
(`agency_id`, `correlation_id`, `causation_id`, `actor`, `identity`, `intent`,
`authority`, `idempotency_key`). A universal actuator without that shared chain
is still not a reconstructable agency. **P0-10** from the audit — *make the
roadmap itself CI-verifiable* — is the standing meta-task: the adoption matrix,
Constitution ledger and bypass checker already enforce parts of it; the gap is a
check that each phase's claimed stage matches the code.

### Phase I — Continuity of self (AgencyState + restart recovery) 🚧

The journal (H4) recovers in-flight *plan steps*; Phase I makes JARVIS's wider
*agency* — the goals it's pursuing, the situations it has open, the mode it's in —
survive a restart, so it resumes rather than waking up blank.

**Audit split (2026-10):** what ships here is *continuity of active agency
state*, not yet *continuity of self*. The audit separates the two, and so does
the roadmap now:

- **I-A — Commitment continuity** (I1–I4): active goals, open situations, mode,
  restart snapshot, reconciliation. This is the work below.
- **I-B — Cognitive continuity** (net-new, see after I½): the rest of the self
  that must survive a restart — identity context, beliefs, attention / working
  memory, intent, chosen plan, authority context, autonomy state, delegations,
  execution state, pending verification, unresolved uncertainty, recent
  causality, memory and learning state. Only when I-B lands can JARVIS resume
  with *"I believed Y, had chosen plan Z, delegated part to FRIDAY, held
  authority A, was awaiting verification B, with uncertainty C"* — not just *"I
  was working on objective X."* I-B depends on I½ (identity), the Epistemic
  Fabric (uncertainty/provenance) and J (a cognitive context to snapshot), so it
  is sequenced after them.

#### I-A — Commitment continuity (I1–I4)

| # | Increment | Stage | Status | Release |
| --- | --- | --- | --- | --- |
| I1 | `kernel/agency_state.py`: durable versioned snapshot + continuity summary + reconcile | pure | ✅ Shipped | 8.85.0 |
| I2 | `continuity.py`: bootstrap captures the snapshot; boot logs the continuity summary | shadow | ✅ Shipped | 8.87.0 |
| I3 | `continuity.boot_reconcile`: reloaded vs. live commitments, log agreement | parity | ✅ Shipped | 8.88.0 |
| I4 | JARVIS resumes / announces continuity through the output seam (kill-switched) | enforce | ⏳ Planned (PAUSE for owner) | — |

#### I1 — AgencyState primitive (8.85.0)

- `kernel/agency_state.py`: `capture(mode, goals, situations, …)` builds an
  `AgencyState` from plain extracted data (no HA import — pure); `AgencyStore`
  persists snapshots through `kernel.persistence` (own DB file, newest-wins,
  pruned to the newest N) and reloads the latest, skipping any written by a newer
  schema rather than mis-parsing it.
- `continuity_summary(state)` renders a one-line "what I was in the middle of";
  `reconcile(state, live_goal_ids, live_situation_ids)` splits a reloaded snapshot
  into commitments still live vs. ones that vanished while JARVIS was down.
- Landed **pure** (declared in the adoption matrix): the primitive exists and is
  unit-tested, nothing live wired yet, so the release is behaviour-preserving.

#### I2 — bootstrap capture + boot summary (8.87.0)

- `continuity.py`: a live binder over the pure primitive. On boot
  `boot_summary()` reloads the last snapshot and logs what JARVIS was in the
  middle of; on a 5-minute tick (and once at boot) `capture_now()` reads the
  live goals (`goals.active`), open situations (kernel `SituationManager`) and mode
  (`modes.active_mode`) and writes a fresh snapshot.
- **Shadow** (`agency_state` → shadow, owner `continuity`): reads live state and
  writes its own snapshot DB + a log line; drives nothing. Every read is
  defensive, so a failure can never reach the boot path. Kill-switch:
  `continuity.AGENCY_CAPTURE_ENABLED`.

#### I3 — boot-time reconcile (8.88.0)

- `continuity.boot_reconcile()`: on boot (before the seed capture, so it reads
  the *pre-restart* snapshot) it reconciles each remembered goal / situation
  against the ids still live now (`agency_state.reconcile`), and logs how many
  JARVIS could resume versus how many vanished while it was down.
- **Parity / log-only:** it computes and logs the reconciliation and drives
  nothing, so the adoption stage stays `shadow` (observe-only) until I4 makes a
  resumption authoritative (enforce). Same kill-switch and fail-safe as I2.

### Phase I½ — Identity & Trust Fabric (new, audit-added) 🚧

| # | Increment | Stage | Status | Release |
| --- | --- | --- | --- | --- |
| I½1 | `kernel/identity_fabric.py`: `IdentityAssertion` + confidence algebra + `resolve()` (fails toward confirmation) | pure | ✅ Shipped | 8.94.0 |
| I½2 | recognizers (voice/camera/mobile) emit assertions alongside current identity reads | shadow | ⏳ Planned | — |
| I½3 | assertion-derived identity compared to current `identity.py` reads | parity | ⏳ Planned | — |
| I½4 | ≥1 identity-sensitive path consumes an assertion with confidence + expiry | enforce | ⏳ Planned | — |

The single largest omission in the pre-audit roadmap. Authority already carries
*actor*, *token*, *scope* and *confidence*, but identity deserves a dedicated
primitive before autonomy grows — because as JARVIS acts more on its own it must
keep straight *who is speaking, who is present, who owns this request, who
delegated it, who authorized it, how identity was established, how confident we
are, how long that holds, and what evidence supports it.*

New: `kernel/identity_fabric.py` — a pure `IdentityAssertion`:

```
IdentityAssertion
├── subject              # whose identity this asserts
├── source               # voice / camera / mobile / delegated-agent / token
├── authentication_method
├── confidence           # 0..1
├── timestamp
├── expiration           # assertions decay; none is permanent
├── evidence             # what backs it
└── scope                # what it is good for
```

**Hard rule:** `identity ≠ presence ≠ authority ≠ trust`. An assertion that
someone is present is not an assertion of who they are; knowing who they are is
not authorization; authorization is not earned trust. The fabric keeps these
four distinct and lets authority, autonomy and the social model consume
*assertions with confidence and expiry* rather than booleans. Ladder: pure
(assertion + confidence algebra, unit-tested) → shadow (voice/camera/mobile
recognizers emit assertions alongside current identity reads) → parity
(assertion-derived identity vs. current `identity.py` reads) → enforce (≥1
identity-sensitive path — e.g. a restricted intent — consumes an assertion with
confidence + expiry). Kill: `IDENTITY_FABRIC_ENFORCE`; fail-safe = current
identity reads, and **low confidence or an expired assertion fails toward *more*
confirmation, never less.** **PAUSE for owner before enforce** (it gates who may
command JARVIS). Done: one identity-sensitive decision is assertion-backed, with
confidence and expiry honored.

#### I-B — Cognitive continuity (after I½ + Epistemic Fabric + J)

Extends `kernel/agency_state.py` from a *commitment* snapshot to a *cognitive*
one: alongside goals/situations/mode, capture (defensively, each independently
best-effort) the live identity context, belief set, attention / working-memory
focus, current intent, chosen plan, authority context, autonomy state, open
delegations, execution state, pending verifications, unresolved uncertainty,
recent causality and learning state — whatever those subsystems expose once they
exist. Ladder: pure (extend the snapshot schema, newer-schema-skip preserved) →
shadow (capture the richer snapshot, log the fuller continuity summary) → parity
(reloaded cognitive state vs. live, agreement logged) → enforce (boot resume is
sourced from the cognitive snapshot). Kill: reuse `AGENCY_CAPTURE_ENABLED` +
`CONTINUITY_RESUME_ENFORCE`; fail-safe = I-A commitment-only continuity. **PAUSE
for owner before enforce.** Done: after a restart JARVIS can state belief, chosen
plan, delegation, authority and pending verification — not just the objective.

### Epistemic Fabric — cross-cutting kernel primitives (new, audit-added) ⏳

The audit's third correction: five concepts that *every* cognitive phase leans
on should be kernel primitives, not re-invented per phase. They land on the same
ladder as everything else, each pure first, and are consumed by J–M, N and R.
Several already exist in pieces (correlation/causation ids on events, confidences
in beliefs); this tier makes them canonical and first-class.

- **Outcome Model** (`kernel/outcome.py`) — ✅ **pure shipped (8.96.0)**. A
  canonical `Outcome` (`intended_result`, `observed_result`, `success`,
  `confidence`, `deviation`, `cause`, `side_effects`, `user_feedback`,
  `environmental_feedback`, `learning_signal`), sitting between verification and
  learning, with a distilled `learning_signal` in `[-1, 1]` (`+confidence` on
  success, `-confidence` on failure) and per-capability roll-ups
  (`summarize`/`by_capability`). Without it, "learning" degrades to *the LLM
  reads logs and decides what it learned.* **Required before Phase M.** Ladder:
  pure ✅ → shadow (every verified actuation produces a structured outcome,
  logged) → parity → enforce (M reads `Outcome`, never raw logs).
- **Provenance** (`kernel/provenance.py`) — ✅ **pure shipped (8.99.0)**. Every
  important piece of state can answer *where did this come from*: source,
  observed-at, confidence, model, corroboration, expiry. `select_authoritative`
  picks the freshest high-confidence record; `corroborate` merges agreeing
  records (noisy-OR). Essential for learning, debugging, explanations, security,
  trust and conflict resolution. Ladder: pure ✅ → shadow (world-model /
  situation / recognition attach provenance) → parity → enforce (a consumer
  reads the provenanced value).
- **Uncertainty Fabric** (`kernel/uncertainty.py`) — ✅ **pure shipped
  (8.100.0)**. First-class *"I believe the garage is empty: 0.92"* vs. *"the
  garage is empty."* An `Uncertain` pairs a value with a confidence, a band
  (known / believed / guessed / unknown), its basis and *what evidence would
  resolve it*; `update` folds new evidence (noisy-OR on agreement, discount on
  disagreement). Spans perception, identity, world-model, beliefs, prediction,
  planning, authority and outcomes. Ladder: pure ✅ → shadow (perception /
  world-model / prediction wrap outputs) → parity → enforce (a decision gates on
  the band).
- **Conflict Resolution** (`kernel/conflict.py`) — a formal rule for
  contradictions between sources (camera says empty, phone says present, motion
  fires): evidence ranking, source reliability, recency, confidence,
  corroboration, identity, situation. Must exist **before** advanced world-model
  reasoning so contradictions resolve by policy, not by whichever subsystem ran
  last.
- **Time / Temporal Validity** — more fundamental than Phase Q's space/time
  model: world-model facts and beliefs carry *valid-as-of* and *expires-at*, so
  JARVIS can reason *"this was true 30 min ago / is probably still true / expires
  in 60 s."* Folded into world-model + belief semantics (and reused by Q).

These are the **Governance / Epistemic Fabric** that wraps the whole cognitive
loop — alongside authority, privacy, safety, budgets, rate limits, audit and
human override. *More capability never means less governance* is enforced here.

### Cognitive token & cost telemetry (cross-cutting observability) 🚧

Measurement is its own cross-cutting concern: JARVIS routes work across model
tiers (classifier / reasoning / review / conversation / vision) and today
reports cost and volume as *estimates*. This primitive records the **actual**
per-call usage every provider returns — input, output and **cached** tokens —
attributed to a tier and model, with an estimated cost from an explicit price
book. It is what Phase Y (self-optimization) reads to tune *model spend* safely,
and what a cost panel renders instead of a guess.

| # | Increment | Stage | Status | Release |
| --- | --- | --- | --- | --- |
| TC1 | `kernel/token_telemetry.py`: `UsageRecord` + `PriceBook` + `summarize`/`by_tier`/`by_model` | pure | ✅ Shipped | 8.95.0 |
| TC2 | the router / `llm_provider` emits a `UsageRecord` per call from the provider's own usage fields | shadow | ⏳ Planned | — |
| TC3 | telemetry totals compared to the current estimates | parity | ⏳ Planned | — |
| TC4 | the cost panel + Phase Y read telemetry instead of estimates | enforce | ⏳ Planned | — |

### Phase J — Cognitive OS (unified cognitive cycle) 🚧

| # | Increment | Stage | Status | Release |
| --- | --- | --- | --- | --- |
| J1 | `kernel/cycle.py`: `CognitiveCycle` + `CycleTrace` + `standard_cycle` | pure | ✅ Shipped | 8.89.0 |
| J2 | `cognitive_core._tick` runs a cycle alongside its loop, logs the trace (observe-only, kill-switched) | shadow | ✅ Shipped | 8.97.0 |
| J3 | per-tick cycle decision compared to the loop's dispatch — agreement logged | parity | ✅ Shipped | 8.98.0 |
| J4 | one subsystem's loop *is* the cycle; legacy path retired | enforce | ⏳ Planned | — |

#### J1 — cognitive-cycle primitive (8.89.0)

- `kernel/cycle.py`: `CognitiveCycle` runs ordered, named `CycleStep`s over a
  shared context as one pass, stamping a per-tick `cycle_id` and returning a
  `CycleTrace` (per-step ok / detail / duration); a failing step is recorded,
  never raised. `standard_cycle({phase: fn})` builds a pass over the canonical
  perceive → interpret → decide → act → reflect phases, skipping omitted ones.
- Landed **pure** (declared in the adoption matrix): nothing live runs through it
  yet, so the release is behaviour-preserving.

### Phases J–Ω — forward spec

Each phase lands as its own sequence of releases on the **shadow → parity →
enforce** ladder, with a per-phase kill-switch (`*_ENFORCE`) and a fail-safe
default to the legacy path. Every phase extends the existing kernel primitives
(`event · world_model · situation · authority · beliefs · attention · router ·
causal · plan · journal · persistence · actuator · budget · loop_detect ·
priority · agency_state · cycle`) and the audit-added ones (`identity_fabric ·
outcome · provenance · uncertainty · conflict`) rather than adding a parallel
system. The cross-cutting invariant below binds all of them.

**Phase J — Cognitive OS (unified cognitive cycle).** One explicit
perceive→interpret→decide→act→reflect loop every subsystem ticks through, vs. N
ad-hoc loops. New: `kernel/cycle.py` (pure step sequencer + `CycleTrace`,
correlation id per tick). Ladder: pure → shadow (`cognitive_core` runs a cycle
alongside its loop) → parity (per-tick decision compare) → enforce (one
subsystem driven by the cycle). Kill: `COGNITIVE_CYCLE_ENFORCE`; fail-safe =
legacy loop. **Audit sharpening:** a cycle alone doesn't create cognition, and J
must not become a mere scheduler around existing subsystems. The exit criterion
is **the cognitive cycle owns the decision context** — it holds the per-tick
world-model read, beliefs, situation, attention focus, intent, plan, authority
and outcome, and subsystems read *from* it — not merely that it executes
callbacks in order. The fuller runtime it grows toward is event → perception →
world-model update → belief update → situation → attention → working memory →
intent → prediction → plan → authority → budget → act → observe → verify →
outcome → learn → world-model. Done: ≥1 loop *is* the cycle and the cycle owns
that tick's decision context, journal-reconstructable.

**Phase K — Attention & Working Memory.** A bounded, decay-scored working set
feeding attention arbitration. New: `kernel/working_memory.py` (capacity-bounded,
deterministic eviction). Ladder: pure → shadow (populate from the bus) → parity
(attention consults it) → enforce (arbitration reads it authoritatively). Kill:
`WORKING_MEMORY_ENFORCE`; fail-safe = current attention inputs. **Audit
sharpening:** working memory must represent the *current cognitive context*, not
just salient events — current situation, active objective and intent, relevant
people and devices, recent observations, unresolved questions, pending actions
and verification, important memories, predictions and constraints — otherwise the
context assembler keeps building its own hidden working memory. Done: the
decision engine receives a **canonical bounded cognitive context** from
`WorkingMemory`, not merely that `WorkingMemory` exists.

**Phase L — Prediction & Causal Reasoning.** Upgrade `causal.py` from seed to
live predictor: `predict(context)` + `explain(effect)`. **Reordering (audit):**
L now follows the world-model / space-time foundations (**T** knowledge graph +
**Q** space/time + Epistemic Fabric), not precedes them — prediction needs a
stable representation of entities, state, relationships, events, time, context
and causality, or it gets built on fragmented legacy state and rebuilt later.
Ladder: pure → shadow
(predict next state, log hit/miss) → parity (accuracy over real traffic) →
enforce (proactive gated on prediction confidence). Kill:
`CAUSAL_PREDICT_ENFORCE`; fail-safe = reactive only. Done: a measured accuracy
number gates ≥1 proactive path.

**Phase M — Learning & Adaptation (closed loop).** Journaled outcomes feed back
into beliefs/weights. New: `kernel/learning.py` (bounded, reversible updates).
**Prerequisite (audit):** the Epistemic Fabric's **Outcome Model** must land
first — M reads structured `Outcome` objects (intended vs. observed, success,
deviation, cause, side-effects, feedback, learning signal), never raw logs, or
"learning" becomes an unconstrained LLM log-read. Ladder: pure → shadow (compute
would-be adjustments) → parity (offline compare) → enforce (belief confidences
update from outcomes, clamped + audited). Kill: `LEARNING_ENFORCE`; fail-safe =
frozen weights. **Governance:** learned weights may never relax an authority gate
or safety threshold. Done: a decision's inputs shift from a prior structured
outcome, reversibly.

**Phase N — Graduated Autonomy (per-capability trust).** Replace the single
autonomy flag with per-capability trust that earns up (suggest → confirm → act)
on verified track record. **Prerequisite (audit):** autonomy is a trust engine,
and trust needs reliable evidence — so N depends on **I½ identity**, the
**Outcome Model** (verified track record), capability risk class, verification
history, user feedback and current context. The dependency is
`identity → outcome → trust → autonomy`; building autonomy on incomplete evidence
is building a trust engine on sand. New: `kernel/autonomy.py` (trust ledger;
level = pure fn of success history × risk class). Ladder: pure → shadow → parity
→ enforce
(**owner-gated** promotion, max-restriction belt within an owner ceiling; safety
classes never auto-promote). Kill: `GRADUATED_AUTONOMY_ENFORCE`; fail-safe =
current single setting. **PAUSE for owner before enforce.** Done: ≥1 low-risk
capability self-promotes within a ceiling; safety classes never do.

**Phase O — Agency Orchestration (merged O + AB).** *(Audit: O and AB are both
agency coordination with no architectural reason to be separate — merged into one
framework.)* One agency framework covering **hierarchical delegation**
(parent → child: scoped, budgeted child agents beyond FRIDAY/HOMER, each
inheriting a strictly *narrower* capability set) **and peer coordination**
(peer ↔ peer: bid/claim/settle over the event bus for a shared objective). New:
`kernel/agency.py` (lifecycle + capability derivation that can only narrow) +
`kernel/coordination.py` (budgeted peer protocol). Ladder: pure → shadow (dry-run
spawn / simulate) → parity (child plan vs parent-direct; coordinated vs
single-agent) → enforce (one real budgeted sub-task; one task split across peers,
conflicts resolved by the priority ladder). Kill: `AGENCY_ORCHESTRATION_ENFORCE`;
fail-safe = parent/single-agent acts directly. Done: a child completes a scoped
task (attributed, budgeted, provably un-escalatable) **and** two peer agencies
complete a shared task without escalation or double-actuation.

**Phase P — Proactive Household Intelligence.** Household-level anticipation
(routines, comfort) — suggest, don't act. **Reordering (audit): delayed.**
Proactivity is an *application* of the cognitive architecture, not another
cognitive subsystem; running it early makes an old heuristic, an LLM inference, a
routine detector and a household model all compete to decide what's useful. It
now follows world-model + situation + attention + prediction + objective +
autonomy, so a proactive candidate flows model → authority → act/suggest/ignore.
Builds on L + K; adds a
`household_model` view (occupancy rhythm, routine graph). Ladder: pure → shadow
(infer routines) → parity (suggestions vs heuristics) → enforce (suggestions
sourced from the model, never silent actuation). Kill:
`HOUSEHOLD_PROACTIVE_ENFORCE`; fail-safe = current heuristics. Done: suggestions
are model-driven and measurably more relevant.

**Phase Q — Embodied JARVIS (spatial/temporal model).** First-class space (areas,
adjacency, floor-plan) and time (dayparts, cadence). New: `kernel/space_time.py`
(spatial graph + temporal frame, pure queries). Ladder: pure → shadow → parity
(camera↔sensor mapping, cf. #140) → enforce (presence/coverage/routing read the
model). Kill: `SPACE_TIME_ENFORCE`; fail-safe = current per-feature mapping.
Done: ≥1 live presence/coverage path is model-authoritative.

**Phase R — Integration Gate (one closed loop).** *(Audit: R is a GATE, not a
capability phase.)* It proves the architecture *can operate as one* — cognition
(J–M) + agency (N–V) run as a single closed loop on **one** real scenario — and
is the **Cognitive/Agency Architecture Done** bar (see definitions of done). This
removes the old naming confusion where R read as both "MCU integration" and the
start of the post-R roadmap. No new primitive; wiring + a coverage lift + an
exit-criteria gate. Ladder: shadow (trace a full
perceive→predict→decide→act→learn pass) → parity (representative set) → enforce
(loop owns one end-to-end scenario) + CI gate forbidding open-loop regressions.
Fail-safe = open-loop legacy. Done: one real scenario is fully closed-loop and
journal-reconstructable — *"can the architecture operate as one?"*

**Phase S — Self Model & Self Awareness.** An explicit, inspectable model of
JARVIS's own capabilities, commitments (agency_state), confidence and limits,
reported honestly. New: `kernel/self_model.py` (read-only projection; no new
authority). Ladder: pure → shadow (diagnostics read) → parity (self-report vs
ground truth) → enforce (self-answers sourced from the model, no confabulation).
Kill: `SELF_MODEL_ENFORCE`; fail-safe = static capability list. **Hard rule:**
describing a capability never grants it. Done: "what can/are you doing" answers
are provably model-backed.

**Phase T — Deep World Model (knowledge graph).** Upgrade `knowledge.py`
facts+relations to a typed, queryable graph. New: graph view on
`kernel/world_model.py` (`entities`, `relations`, `query`). Ladder: pure → shadow
(answer context queries) → parity (graph vs current recall) → enforce (≥1 context
builder reads the graph authoritatively). Kill: `KNOWLEDGE_GRAPH_ENFORCE`;
fail-safe = current semantic recall. Done: a live context read is
graph-authoritative with no raw fallback.

**Phase U — Advanced Reasoning & Planning.** Multi-step, constraint-aware plans
(preconditions, alternatives, compensation). New: extend `kernel/plan.py`. Ladder:
pure → shadow (richer plans alongside linear) → parity (outcome compare) → enforce
(goals/execute_plan use it; every step still authority+verify gated). Kill:
`ADVANCED_PLANNER_ENFORCE`; fail-safe = linear planner. Done: a multi-constraint
task plans+verifies through it, journal-reconstructable.

**Phase V — Long-Horizon Agency.** Durable, resumable, progress-tracked goals
spanning days/weeks — the direct payoff of Phase I. New: `kernel/long_horizon.py`
(durable goal/milestone ledger, resumed from agency_state). Ladder: pure → shadow
(track progress) → parity (resume-after-restart proven vs journal) → enforce
(a multi-day goal survives restarts and drives suggestions). Kill:
`LONG_HORIZON_ENFORCE`; fail-safe = session-scoped goals. Done: a goal persists +
resumes across a restart with correct progress.

**Phase W — Social & Relationship Intelligence.** Per-person preference/pattern
models that personalize — within strict consent/privacy limits. New:
`kernel/social.py` (consent-flagged, owner-inspectable, purgeable). Ladder: pure
→ shadow (infer preferences) → parity (personalized vs default) → enforce
(opt-in personalization on ≥1 path). Kill: `SOCIAL_MODEL_ENFORCE`; fail-safe =
non-personalized default. **Privacy (audit):** consent-gated + purgeable is not
enough — W needs an explicit **information-flow policy** (a cross-cutting
Privacy / Information Boundary subsystem that lands *before* W): data
classification, purpose, subject, consent, retention, audience, source,
provenance. Person A's preference is not automatically disclosable to Person B.
Never drives a security/intrusion decision; owner can purge any person-model.
Done: one interaction is measurably personalized, consent-gated, purgeable, and
governed by an explicit information-flow policy.

**Phase X — Physical World Intelligence.** *(Renamed from "Physical /
Environmental" per audit — climate/energy is only one subset.)* Reason about the
physical world: environment, objects, spatial state, activity, occupancy,
equipment health, maintenance, energy, comfort, anomalies and physical
constraints — including climate/air/light and comfort/efficiency trade-offs. New:
`kernel/environment.py` (state + objective functions). Ladder: pure → shadow
(recommendations) → parity (vs sentinel/energy heuristics) → enforce
(suggest-don't-act; any actuation stays on the safety seam). Kill:
`ENVIRONMENT_ENFORCE`; fail-safe = current heuristics. Done: climate/energy
suggestions come from the model and respect the safety seam + priority ladder.

**Phase Y — Self-Optimization.** Measure own performance (latency, accuracy,
interruption cost, model spend) and tune *within owner bounds*. New:
`kernel/optimize.py` (metrics + bounded tuning proposals). **Tiered guardrails
(audit) — encoded explicitly in the primitive:** *safe to self-optimize* —
latency, cost, provider selection, cache, context size, resource allocation;
*sensitive, owner-gated, shadow-only until approved* — autonomy levels,
confidence thresholds, interrupt thresholds, model selection for *safety*
decisions; *forbidden, never tunable by Y* — authority ceiling, security policy,
identity requirements, safety thresholds, human override, audit retention.
Ladder: pure → shadow (propose tunings) → parity (tuned vs baseline) → enforce
(self-tunes **safe-tier** params within owner bounds; sensitive tier proposes
only; forbidden tier immutable). Kill: `SELF_OPTIMIZE_ENFORCE`; fail-safe = fixed
config. Done: one safe-tier parameter self-tunes within bounds with a measured
win, and the forbidden tier is provably untouched.

**Phase Z — Resilient Compute Federation.** *(Renamed from "Resilient /
Distributed Compute" per audit.)* Graceful degradation and optional *federation*
of compute — local-first, cloud-optional, offline-safe. **HAOS boundary (audit):
this is explicitly NOT "distributed JARVIS."** The canonical identity, state,
authority, agency, world-model and journal **must remain under the HA
integration's control**; external compute is disposable. Framing it as
federation (not distribution) removes the architectural temptation to move the
brain outside HAOS. New: `kernel/resilience.py` (health/fallback policy across
compute tiers). Ladder: pure → shadow (would-be tier choice) → parity (vs current
breaker) → enforce (degradation path owns routing when a tier is down). Kill:
`RESILIENCE_ENFORCE`; fail-safe = current conservative breaker. Done: a simulated
tier outage degrades gracefully with no loss of safety behavior and the canonical
state never leaves the HA integration.

**Phase AA — Omnipresent Multimodal JARVIS (presence continuity).** Unify
voice/vision/text/panel/satellites into one coherent presence. **Audit: the deep
concept is presence continuity, not a UI layer** — if a conversation starts on
voice and the user walks to another room (voice → mobile → HUD), JARVIS must know
*this is the same interaction.* So the surface registry carries `interaction_id`,
`surface_id`, `presence_context`, attention ownership, handoff state and
conversation continuity. New: `kernel/surfaces.py` (surface registry + one
cross-surface arbiter). Ladder: pure → shadow → parity (which satellite speaks) →
enforce (cross-surface arbitration authoritative, mutes honored everywhere,
handoff preserves interaction identity). Kill: `SURFACES_ENFORCE`; fail-safe =
per-surface current logic. Done: one utterance/notification arbitrated once
across all surfaces with no double-announce, and one interaction survives a
surface handoff.

**Phase AB — (merged into Phase O — Agency Orchestration).** Retained as a letter
for backlog stability; the peer-coordination work described here now lives in the
merged **Phase O** above (hierarchical delegation + peer coordination in one
agency framework).

**Phase AC — Advanced Autonomy (dynamic/contextual).** Autonomy flexes with
context (time, presence, risk, confidence) atop per-capability trust. New: extend
`kernel/autonomy.py` with a contextual modifier (context → level, only *downward*
from the N ceiling by default). Ladder: pure → shadow → parity → enforce (context
tightens autonomy — e.g., guests present → more confirmation — owner-gated; only
loosens within the N ceiling). Kill: `CONTEXTUAL_AUTONOMY_ENFORCE`; fail-safe =
Phase N static levels. **PAUSE for owner.** Done: context demonstrably tightens
autonomy on ≥1 capability, never silently loosens past the ceiling.
*(Consolidation applied (audit): **N + AC are one autonomy engine, two stages** —
N earns the per-capability ceiling, AC flexes downward within it by context.
Treat as a single phase for sequencing. Both are the most safety-sensitive work
in the roadmap and both PAUSE for owner before any enforce step.)*

**Phase AD — Research & Discovery (investigative agency).** Bounded, cited,
tool-using inquiry (diagnose an anomaly, research an answer). **Audit: evidence
provenance, not merely citations** — MCU JARVIS must distinguish *fact vs.
observation vs. inference vs. hypothesis vs. prediction vs. recommendation*, and
every result preserves source, timestamp, confidence, claim, evidence, inference
and contradictions (reusing the Epistemic Fabric's Provenance + Uncertainty), or
the "research agent" is just another LLM answer generator. New:
`kernel/inquiry.py` (investigate→gather→synthesize with provenance + spend cap).
Ladder: pure → shadow (dry investigations) → parity (vs direct answer) → enforce
(a real diagnostic question answered with provenanced evidence, budget-capped).
Kill: `INQUIRY_ENFORCE`; fail-safe = direct answer / no investigation. Done: one
investigation returns a provenanced, claim-typed, bounded conclusion within
budget.

**Phase AE — MCU Certification (systems certification).** *(Audit: AE is the
certification phase — "does the complete JARVIS system actually behave as one?" —
and the **MCU System Done** bar.)* Not merely a synthesis gate + suite: a
systems-certification phase with explicit **scenario classes**, each run
closed-loop and journal-reconstructable:
- **Conversational** — request → reasoning → action → verification.
- **Proactive** — observation → prediction → suggestion → response.
- **Long-horizon** — objective → days → restart → resume → completion.
- **Delegation** — JARVIS → child → result → synthesis.
- **Failure** — action → HA failure → recovery.
- **Security** — ambiguous identity → deny/confirm.
- **Conflicting priorities** — convenience vs. safety.
- **Provider failure** — cloud unavailable → local degradation.
- **Cognitive error** — bad belief → observation → correction.
- **Restart** — mid-agency restart → reconciliation → resume.

Ladder: scenario-class traces (shadow) → parity dashboard (coverage %,
closed-loop %, autonomy ceilings, invariants green) → enforce (every scenario
class runs closed-loop) → a certification CI gate (no phase regressed below its
enforced stage). Done: the full scenario-class suite passes closed-loop and
`KERNEL_COVERAGE` + governance invariants hold at target.

**Phase Ω — Continuous Evolution (evergreen).** A standing process: new HA
features, issues and audits fold in under the same discipline forever. No new
primitive; a recurring cadence + a "new capability intake" checklist (ladder,
gates, kill-switch, Constitution update). The governance gates prevent drift.
Exit criterion: never — the invariants keep holding as the system grows.

### Cross-cutting invariant (binds every phase above)

Each phase's enforce step must leave intact (and the four gates enforce): no
actuator bypasses authority; sub-agents / children / peers can only *narrow*
capability; security-sensitive capability needs explicit authority; every
actuator verifies its postcondition; everything carries correlation +
idempotency; credentials never enter prompts; local-first when quality permits;
no blocking I/O on the loop. **Learning (M), autonomy (N/AC) and optimization (Y)
may never relax a safety threshold or authority gate** — only tighten, or loosen
strictly within an owner-set ceiling. *More capability never means less
governance.*

### Recommended sequencing (revised by the 2026-10 audit)

The audit's biggest single correction was sequencing: world-model / space-time
foundations and the Epistemic Fabric move *ahead* of serious prediction and
learning, identity precedes autonomy, and proactivity/social/physical move later
as *applications* of the architecture. The revised dependency order:

```
H  (universal agency spine)  ✅
│
├── I-A  Commitment continuity              (I1–I3 ✅, I4 ⏳ owner-gated)
├── I½   Identity & Trust Fabric            (new — before autonomy)
├── J    Cognitive Runtime (owns context)
├── K    Attention + Working Memory (canonical cognitive context)
├── T    Deep World Model / knowledge graph
├── Q    Space / Time
├──      Epistemic Fabric: Conflict · Uncertainty · Provenance · Time
├── L    Prediction / Causality
├──      Outcome Model
├── M    Learning
├── I-B  Cognitive continuity               (needs I½ + Fabric + J)
├── N(+AC) Graduated + Contextual Autonomy  (owner-gated — PAUSE)
├── U    Advanced Planning
├── V    Long-Horizon Agency                (pays off Phase I)
├── S    Self Model
├── O(+AB) Agency Orchestration             (hierarchical + peer)
├── P    Proactive Intelligence             (application, delayed)
├──      Privacy / Information Boundary      (before W)
├── W    Social Intelligence
├── X    Physical World Intelligence
├── Y    Self-Optimization                  (tiered guardrails)
├── Z    Resilient Compute Federation       (brain stays in HAOS)
├── AA   Multimodal Presence Continuity
├── AD   Research / Discovery (provenanced)
├── R    Integration Gate                   ← Cognitive/Agency Architecture Done
└── AE   MCU Certification                  ← MCU System Done
          → Ω  Continuous Evolution (evergreen)
```

**Consolidations applied (audit):** **O + AB** are one agency framework
(hierarchical + peer); **N + AC** are one autonomy engine (ceiling + contextual
flex). Letters are retained in the backlog for stability, but they sequence as
single phases. Both autonomy stages PAUSE for owner before any enforce step.

---

## Sequencing rationale

Observability (Phase 1) comes before everything else, because you cannot safely
refactor what you cannot trace. The world model (2) and situations (3) are mostly
additive read/state layers. Authority (4) is the keystone but the riskiest, so it
follows the ledger that lets us prove parity. Planner/verifier (5) and the quality
layers (6–7) build on all of it. **Phase 0's integration tests gate the whole
program.**

## Definitions of done (three bars, per the 2026-10 audit)

The pre-audit plan had a single "definition of done" reached at Phases 6–7, which
contradicted the much larger J→Ω roadmap bolted on later. The audit resolves the
drift by naming **three** distinct bars:

1. **Kernel Foundation Done — at H.** The universal agency spine: every
   consequential actuation converges on the kernel, authority-gated,
   verify-after-act, journal-reconstructable, zero bypasses — *and* every object
   on the intent→…→journal chain shares one agency identity (`agency_id`,
   `correlation_id`, `causation_id`, `actor`, `identity`, `intent`, `authority`,
   `idempotency_key`). **This is the H exit gate.**
2. **Cognitive/Agency Architecture Done — at R.** The architecture can operate as
   one: cognition (J–M) + agency (I½, N–V) run as a single closed loop on one
   real scenario, journal-reconstructable. R is an **integration gate**, not a
   capability phase.
3. **MCU System Done — at AE.** The complete system behaves as one: every
   AE scenario class (conversational, proactive, long-horizon, delegation,
   failure, security, conflicting priorities, provider failure, cognitive error,
   restart) passes closed-loop, with coverage + governance invariants at target.
   AE is **systems certification**.

The older "perceive → … → learn through one coherent loop" sentence still
describes the *shape* of the target loop — it is just not *done* at Phases 6–7;
it is proven at R (one scenario) and certified at AE (all scenario classes).

## Governance / Epistemic Fabric (the cross-cutting contract)

The audit's deepest point: the roadmap must describe *what information and
authority flow between capabilities*, not just the capabilities. Wrapping the
whole cognitive loop (event → world-model → beliefs/uncertainty → situation →
attention → working memory → intent → prediction → plan → authority/autonomy →
delegation → actuator → HA → observation → verification → outcome → learning →
world-model) is a standing governance fabric that every phase's enforce step must
preserve: **authority · identity · privacy · provenance · uncertainty · safety ·
budgets · rate limits · audit · human override · the HAOS boundary.** This is the
operational form of *more capability never means less governance*, and the four
static CI gates plus the Epistemic Fabric primitives are how it is enforced rather
than merely documented.

## P0 gaps the audit would fix before new MCU features

Ranked above adding new capability: **(1)** finish H completely (no consequential
bypass + full shared agency chain); **(2)** make AgencyState authoritative, not
just a snapshot (I-B resume); **(3)** `IdentityAssertion` before serious autonomy
(I½); **(4)** J owns the decision context, not a callback scheduler; **(5)**
canonical event semantics (correlation + causation + provenance + sequence +
dedupe); **(6)** canonical `Outcome` for every action; **(7)** world-model
authority — end cognitive dependence on fragmented subsystem state; **(8)** a
restart *scenario* suite, not just unit tests; **(9)** end-to-end agency traces
(why → what → under what belief → under what authority → what happened → observed
→ learned); **(10)** make the roadmap itself CI-verifiable (adoption-stage vs.
code). These map onto I½, I-B, J, K, T and the Epistemic Fabric above.

## Open blocker

Phase 0's integration-tests-in-CI sub-item is blocked on a local
`pytest-homeassistant-custom-component` (PHACC) run. PHACC pulls in all of Home
Assistant and cannot be installed in the cloud dev sandbox, so the HA
loader-discovery failure (`IntegrationNotFound: jarvis`) needs one local run to
capture the exact error and land the fix. Everything else in Phase 0 can proceed
without it.

## Proposed package structure (target state, introduced incrementally)

```
kernel/        kernel.py, event.py, event_bus.py, world_model.py, situation.py,
               beliefs.py, attention.py, planner.py, authority.py, execution.py,
               outcomes.py, persistence.py
providers/     router.py and provider implementations
```

Existing top-level modules remain and are absorbed behind these seams phase by
phase; none are deleted until their behavior is fully carried by the kernel and
proven at parity.
