# JARVIS Kernel — phased plan of attack

Status: **draft / proposed.** This is the plan of record for consolidating JARVIS's
subsystems into one coherent operating loop ("the kernel"), in response to the
v8.4.0 architecture audit. It is written as a **strangler-fig migration** — wrap
and absorb the working subsystems incrementally — **not** a big-bang rewrite. Each
phase ships as its own release with green CI and a parity/regression guard, so
JARVIS stays shippable throughout.

The audit's own conclusion is the premise here: the gap is **consolidation, not
features**. Most of the "missing" pieces already exist as strong but parallel
subsystems; the work is giving them a common spine, not rebuilding them.

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

Target release: **8.5.0**

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

Target release: **8.6.0** — highest leverage, lowest risk.

- `kernel/event_bus.py`: an in-process pub/sub. `observer`, `camera`,
  `proactive_audio`, and the voice path publish `JarvisEvent`s **in shadow mode**
  — existing code paths remain authoritative; the bus only records.
- Thread `correlation_id` through `decision_record` so every
  event → decision → outcome links into one chain (the audit's single
  explainable chain).

Deliverable: a queryable, correlated decision trail — which also makes every
later phase debuggable. Risk: low (shadow-only). Guard: no behavior change.

## Phase 2 — World-model facade

Target release: **8.7.0**

- `kernel/world_model.py`: a **read facade** over HA state + the knowledge graph +
  identity + scene memory, answering in canonical terms (people / rooms / devices
  / activities / relationships). Reasoning paths migrate to it one caller at a
  time; raw HA entity access remains underneath.

Risk: low (read-only). Guard: per-caller opt-in; parity test vs. direct HA reads.

## Phase 3 — Situation manager

Target release: **8.8.0**

- `kernel/situation.py`: generalize `intrusion.py`'s state machine into durable,
  correlated situations (normal → possible → investigating → confirmed/benign →
  response → resolved) fed by the event bus. `intrusion` is the first consumer;
  delivery and hazard flows migrate later.

Risk: medium. Guard: `intrusion` runs new + old in parallel; flip only once
verdicts match on recorded history.

## Phase 4 — Authority / capability engine (the safety keystone)

Target release: **8.9.0**

- `kernel/authority.py`: one capability check (capability, identity, context,
  situation, confidence, time, intent, scope). `voice_confirm` / `output_gate` /
  autonomy delegate to it. Capability **tokens** for FRIDAY/HOMER so delegation
  cannot escalate.
- Every actuator routes through it.

Risk: high — so ship in **log-only / allow-as-before** mode first, prove it
reaches the same allow/deny as today on real traffic, then enforce. Guard:
parity log + the invariant tests.

## Phase 5 — Planner → Executor → Verifier

Target release: **8.10.0**

- Formalize `goals.py` + agent execution into explicit plan objects with
  preconditions, postcondition verification, and `idempotency_key`.
  Verify-after-act becomes first-class rather than ad hoc.

Risk: medium. Guard: existing goals/followups regression suite + new verifier
tests.

## Phase 6 — Beliefs · Attention · Model Router

Target release: **8.11.0**

- `kernel/beliefs.py`: probabilistic beliefs (evidence, source, decay,
  contradiction) seeded from knowledge confidence.
- `kernel/attention.py`: centralized interruption arbitration (absorbs
  `output_gate` + the adaptive interruption budget).
- `providers/router.py`: formalized tier routing (local-first; capability /
  privacy / latency / cost / availability).

Risk: medium. Guard: attention parity vs. current gate decisions.

## Phase 7 — Causal learning

Target release: **8.12.0**

- Extend `pattern_analyzer` + `rca` + `feedback` toward
  observation → hypothesis → action → outcome → causal confidence.

Risk: medium; isolated to the learning layer.

---

## Sequencing rationale

Observability (Phase 1) comes before everything else, because you cannot safely
refactor what you cannot trace. The world model (2) and situations (3) are mostly
additive read/state layers. Authority (4) is the keystone but the riskiest, so it
follows the ledger that lets us prove parity. Planner/verifier (5) and the quality
layers (6–7) build on all of it. **Phase 0's integration tests gate the whole
program.**

## Definition of done (the audit's bar)

JARVIS is architecturally mature when it can consistently perceive → world-model →
form beliefs → track situations → reason → plan → authorize → act → verify →
record → learn → update state, through one coherent loop rather than disconnected
subsystems — reached at the end of Phases 6–7, with every invariant enforced and
green in CI.

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
