# Kernel adoption / bypass matrix

> Generated signal, kept honest by `scripts/kernel_adoption.py`. Run
> `python3 scripts/kernel_adoption.py` for the live table, or
> `--check` in CI to fail on drift. **Do not hand-edit the table below to make
> it look better than the code** — fix the code or the declared stage instead.

The kernel migration is a [strangler fig](docs/KERNEL_PLAN.md): each primitive
lands pure and additive, then the live integration is wired onto it one caller at
a time. The danger the external gap-audit flagged is **silent drift** — a
primitive that exists but nothing live consults, so the kernel looks adopted on
paper while the legacy path still owns the decision.

This matrix is the antidote. It names, for every kernel primitive, how far
adoption has actually progressed and which live modules reference it.

## Stages

| Icon | Stage | Meaning |
| --- | --- | --- |
| `·` | **pure** | Primitive exists and is unit-tested; nothing live wired yet (kernel-internal or awaiting its first caller). |
| `◐` | **shadow** | A live module mirrors data into it but ignores its output. |
| `◑` | **parity** | A live module computes the kernel's answer alongside the legacy decision and logs agreement — still obeys legacy (log-only). |
| `●` | **enforce** | The kernel decision is authoritative for at least one live path. |

Promotion is one-directional and deliberate: a primitive only moves `pure →
shadow → parity → enforce` once the stage below it has held on real traffic.
**`world_model` is at `enforce`** (C4) — but note *what kind* of primitive it
is: a **read-only context facade**. Its enforce means the facade is the
authoritative *read* path on migrated surfaces (proven by
`test_c4_worldmodel_authoritative`); it changes nothing about what JARVIS *does*.
That is categorically different from enforcing an **authority / situation /
safety decision** — flipping one of those from log-only to authoritative on the
live home is an **owner-gated** step (see `docs/JARVIS_CONSTITUTION.md`, invariant
on authority).

The owner approved a **staged** roll-out of the decision-primitive flips, safest
first. Two have been taken, both on the *discretionary autonomous* path and both
degrading gracefully (worst case: one learned-convenience action is skipped and
re-surfaces next cycle — never an unsafe or missed-safety outcome), each with a
fail-open gate and a one-line kill-switch: **`budget` → enforce** (G1, a
self-imposed hourly ceiling) and **`loop_detect` → enforce** (G2, suppress a
thrashing action). The remaining flips are still un-taken: the `hazard`/`delivery`
situations (G3), and — behind an explicit owner pause — **`authority`** (G4) and
the **`intrusion`** situation (G5), the two highest-risk ones. In short: a read facade may reach enforce on its own;
a *non-safety, self-imposed* decision primitive may reach it with owner approval
and a kill-switch; an authority/situation/safety decision may not, except by an
explicit per-flip owner go/no-go.

## Current matrix

<!-- BEGIN kernel-adoption (python3 scripts/kernel_adoption.py --markdown) -->
| Primitive | Stage | Live callers |
| --- | --- | --- |
| `actor` | ◐ shadow | `actuation`, `agent` |
| `actuator` | ● enforce | `actuation`, `agent` |
| `agency_state` | ◐ shadow | `continuity` |
| `attention` | ◐ shadow | `output_gate` |
| `authority` | ● enforce | `authority_bridge` |
| `autonomy` | · pure | — |
| `beliefs` | ◐ shadow | `agent` |
| `budget` | ● enforce | `actuation` |
| `causal` | ◑ parity | `pattern_analyzer` |
| `conflict` | · pure | — |
| `correlation` | ◐ shadow | `actuation`, `decision_record`, `observer`, `proactive_audio` |
| `cycle` | ◑ parity | `cognitive_core` |
| `event` | ◑ parity | `actuation`, `camera`, `events`, `observer`, `proactive_audio` |
| `event_bus` | ◐ shadow | `__init__` |
| `graph` | ◐ shadow | `knowledge` |
| `identity_fabric` | ◑ parity | `identity` |
| `journal` | ◐ shadow | `continuity`, `goals` |
| `learning` | ◑ parity | `actuation` |
| `ledger` | ◐ shadow | `__init__` |
| `loop_detect` | ● enforce | `actuation` |
| `outcome` | ◐ shadow | `actuation` |
| `persistence` | · pure | — |
| `plan` | ◑ parity | `actuation`, `agent`, `goals` |
| `priority` | · pure | — |
| `provenance` | ◐ shadow | `knowledge` |
| `router` | ◐ shadow | `reasoning_loop` |
| `situation` | ● enforce | `cognitive_core`, `continuity`, `delivery_situation`, `events`, `hazard_situation`, `intrusion` |
| `space_time` | ◑ parity | `cognitive_core` |
| `token_telemetry` | ◐ shadow | `llm_provider` |
| `uncertainty` | · pure | — |
| `world_model` | ● enforce | `actuation`, `agent`, `cognitive_core`, `continuity`, `home_state`, `proactive_briefing` |
<!-- END kernel-adoption -->

## Notes on specific primitives

- **persistence** is an internal seam (connection + migrations) consumed by other
  kernel modules such as `ledger`, not by live callers directly — "pure" here
  means "no legacy bypass to retire", not "unused".
- **world_model** is at **enforce** through `actuation`, `agent`,
  `cognitive_core`, `home_state` and `proactive_briefing` (MCU Phase A/B/C). The
  `control_device` path reads its pre-action context snapshot through the facade
  — the canonical context authority — and uses the result (area, previous_state).
  Phase C widens the read side: **C1** routes cognition's presence context
  (`anyone_home`) through the facade; **C2** routes the intrusion-safety
  presence/alarm reads (`SafetyManager._residents_away`,
  `LockdownManager._anyone_home`, `_alarm_armed`) through it with a fail-safe
  raw-sweep fallback; **C3** routes the home-summary / briefing context builders
  (`home_state._build_summary`, the `get_home_summary` agent tool,
  `proactive_briefing`'s arrival detection) through it. **C4** promotes the stage
  to enforce: the facade is *authoritative* on these paths, not merely consulted.
  `home_state._build_summary` and `agent._exec_home_summary` read it with **no**
  raw fallback, and `test_c4_worldmodel_authoritative` proves the facade's value
  wins over raw HA state on every migrated read — a regression to a raw sweep
  fails those tests. (Enforce means authoritative on ≥1 live path; the raw
  sources survive underneath only as a failure fallback for the safety /
  best-effort callers, and context reads in still-legacy modules — `identity`,
  `presence`, `cognition`, `local_engine`, `observer`, … — remain to be migrated
  per-release, tracked by their own work, not by this stage.)
- **actuator** is at parity through the shared `actuation` envelope (8.26.0 → 8.31.0, B0): the
  `control_device` path builds a canonical `ActuatorRequest` (now carrying the
  expected end-state) and the verify step produces the matching `ActuatorOutcome`
  (requested → executed → observed → verified / mismatch / failed). Parity, not
  enforce: the contract records the actuation faithfully, but legacy code still
  performs the HA service call — routing *execution* through the actuator is a
  later step.
- **plan** is at shadow through `actuation` (8.33.0, MCU Phase A/B): `control_device`
  expresses each actuation as a canonical one-step `Plan` (preconditions → act →
  postconditions, with an idempotency key) and logs it. Shadow, not parity:
  `kernel.plan.execute_plan` is synchronous while HA actuation is `await`-ed, so
  the plan describes the actuation but legacy code still performs it — having the
  plan own execution needs an async driver and is a later step. As of Phase B
  (B4/B4b) `control_device` and `execute_plan` route execution *through* the async
  planner (`aexecute_plan`), and as of **F1** (MCU Phase F) `goals` expresses each
  goal's ordered steps as a kernel `Plan` in shadow — a goal is a plan pursued
  across time — so `plan`'s live callers are `actuation`, `agent` and `goals`.
- **event** is at parity across `agent`, `camera`, `observer`, `proactive_audio`.
  As of 8.32.0 (MCU Phase A) `control_device` publishes a canonical actuation
  `JarvisEvent` (`from_actuation`) on the bus, which the ledger records — so the
  actuation enters the event stream alongside perception. Parity, not enforce:
  the event is emitted and recorded, but no cognitive consumer reacts to it yet.
- **authority** is at **enforce** through `authority_bridge` (MCU Phase G/G4,
  owner-approved). Every control action still records engine-vs-gate parity, and
  now — for an allowlisted set of security capabilities
  (`AUTHORITY_ENFORCE_CAPABILITIES`: unlock, latch-open, garage/cover open, alarm
  disarm/arm) — `authority_bridge.enforced_decision` makes the engine
  authoritative via a **max-restriction belt**: the action proceeds only if the
  legacy gate allows it AND the engine returns ALLOW, so the engine can only
  *tighten* the gate (hold an otherwise-allowed action for confirmation), never
  loosen it. It **fails safe** to the legacy outcome on any engine fault, has a
  one-line **kill-switch** (`AUTHORITY_ENFORCE`), and applies at both protected
  actuation paths (`control_device` and the bulk/plan step gate). Safety-critical
  autonomous responses (lockdown, intrusion securing) call `hass.services`
  directly and never route through the gate, so they are unaffected.
- **situation** is at parity through `intrusion`, `hazard_situation` and
  `delivery_situation` (D1–D3): each mirrors a live lifecycle (intrusion verdict,
  freeze warning→critical→cleared, package delivered→removed) into a kernel
  `Situation` and logs agreement. A G-phase review established *why* it cannot
  simply be "flipped" to enforce: the situation store is a **recorder** of a
  verdict computed in a live module (a temperature threshold, a vision state
  machine, the SafetyManager intrusion score), not an independent decider — so
  making it "authoritative" would be either circular/hollow or, for the safety
  verdicts, an unsafe suppression of a safety alert. The honest path (MCU Phase R)
  moves the *verdict computation itself* into the kernel via a strangler, one
  domain at a time: **(a) decision-parity** — the kernel computes the verdict and
  the live path logs any divergence (no behaviour change) — then **(b) flip** the
  live path to consume the kernel verdict once real traffic shows zero divergence.
  **R1a (8.60.0)** was step (a) for delivery (decision-parity); **R1b (8.61.0)**
  is step (b) — the flip. The stage is now **enforce**, but read *which caller*:
  only **delivery** is authoritative. `package_monitor` keys the delivered/removed
  transition off the kernel store's per-camera "package present" verdict (an open
  `delivery` episode) instead of its in-memory `_STATE` flag, with a kill-switch
  (`package_monitor.DELIVERY_SITUATION_ENFORCE`) and a fail-safe fallback to the
  legacy flag on any store-read error. This meets the matrix's enforce bar
  (authoritative on ≥1 live path, exactly as `world_model`'s enforce does) **but
  `hazard` (freeze) and `intrusion` remain parity MIRRORS** — their verdicts are
  still computed in the live modules and only recorded into the situation store.
  Freeze (R2) and intrusion (R3) are their own flips, in that order: the freeze
  flip will fail *toward* alerting, and the intrusion re-architecture is
  safety-critical and behind an explicit owner go/no-go. So "situation = enforce"
  here means "delivery presence is kernel-owned", not "every situation is".
  **R2a (8.62.0)** was the freeze step (a) (decision-parity); **R2b (8.63.0)** is
  the flip: `cognitive_core._check_freeze` now acts on the kernel
  `freeze_verdict`, so freeze is a *second* authoritative situation caller. Because
  it is a safety alert, the flip uses a **max-severity rule** — it acts on the
  more severe of (kernel, legacy inline threshold), falls back to legacy on any
  kernel error, and has a kill-switch (`HAZARD_SITUATION_ENFORCE`) — so a freeze
  alert is **never suppressed** on kernel error or uncertainty (fail *toward*
  alerting). **Intrusion** is the last caller still driven by the legacy path.
  **R3a (8.64.0)** is its decision-parity step: `kernel.situation.intrusion_gate`
  computes the false-alarm-critical *entry gate* (away + qualifying motion +
  corroboration). **R3b (8.68.0)** makes that gate **authoritative** in
  `SafetyManager._check_intrusion`, and — unlike the behaviour-preserving flips —
  makes it *stricter* than the legacy precondition to fix a real-world false-alarm
  class: it adds a **residents-tracked-home veto** (an awake, positively
  tracked-home resident never opens an investigation, so a resident on camera at
  home under a degraded confinement hold — e.g. a lockdown held open because the
  alarm panel is `unavailable` — is no longer investigated as an intruder; at
  night the veto lifts). The flip **fails safe to the legacy decision** on any
  kernel fault and has a one-line kill-switch (`INTRUSION_GATE_ENFORCE`). Only the
  entry gate is kernel-owned; the stateful investigation/escalation and the
  vision-confirm step remain legacy.
- **priority** (emergency hierarchy) and **causal** (causal inference) remain
  **pure**: no live path consults them yet, and wiring one without a genuine
  consumer would be a hollow adoption — left honest at pure until a real caller
  needs them. **persistence** stays pure by design (an internal seam the other
  kernel modules build on, not a live-adoption surface).
- **journal** is at **shadow** through `goals` (MCU Phase F/F2): when a goal is
  created, its shadow `Plan` (F1) is recorded into the kernel execution journal
  (`record_plan`), so the goal → plan → step chain is durably reconstructable —
  the foundation for agency recovery after a restart. Shadow: the journal is
  written but never replayed in the live flow; the goal store stays authoritative.
- **budget** is at **enforce** through `actuation` (MCU Phase G/G1) — the first
  *decision* primitive promoted to enforce on the live home, taken as the safest
  first trial. `actuation.agency_budget_check` is authoritative for exactly one
  tightly-scoped path: a **discretionary autonomous** actuation — JARVIS acting
  on a learned/trusted pattern of its own accord
  (`cognitive_core._execute_action_data`). When the rolling-hour ceiling (kernel
  default 60 autonomous actions/hr) is reached, the actuation is actually
  **blocked**. The scope is deliberately narrow: **user-requested** actuations
  (the agent tool path / `control_device`) and **safety-critical** responses
  (nighttime lockdown, intrusion securing — which call `hass.services` directly)
  never route through the gate, so neither a user command nor a safety action
  can ever be budget-blocked. The gate **fails open** (a budget fault allows the
  action), and a one-line **kill-switch** (`actuation.AGENCY_BUDGET_ENFORCE =
  False`) reverts it to shadow on the next load. This is still a read-vs-decision
  distinction honoured: unlike the authority/situation/safety flips, blocking a
  *self-imposed, discretionary, non-safety* ceiling degrades gracefully (worst
  case: one learned-convenience action is skipped and re-surfaces next cycle),
  which is why it was chosen first.
- **loop_detect** is at **enforce** through `actuation` (MCU Phase G/G2) — the
  second staged flip. `actuation.loop_detect_check` is authoritative for the same
  path as `budget`: a thrashing discretionary autonomous actuation (the identical
  action re-firing in a tight window, or an A → event → A self-trigger) is
  actually **suppressed**, breaking the cycle. The threshold is deliberately high
  (5 identical firings within 60s, above any legitimate proactive cadence), it is
  checked before the budget gate (a thrash consumes no budget slot), the scope is
  the same narrow one as G1 (user-requested and safety-critical actuations never
  route through it), it **fails open**, and a one-line kill-switch
  (`LOOP_DETECT_ENFORCE`) reverts it to shadow. The envelope-wide E4 shadow lens
  in `actuation._agency_shadow` still logs thrash across the whole actuation
  stream, unchanged.
- **router** is at **shadow** through `reasoning_loop` (MCU Phase E/E3): the
  reasoning path computes the kernel local-first provider route
  (`router.route`: cloud when the connectivity breaker is closed, local Mind when
  it is OPEN) alongside its live decision and logs any divergence. Shadow — the
  kernel result is ignored and the breaker stays authoritative. The breaker's
  `allow_request()` is called exactly once and its value reused (it mutates the
  half-open probe counter), so routing behaviour is unchanged.
- **beliefs** is at **shadow** through `agent` (MCU Phase E/E1): the
  `cognitive_status` tool surfaces a read-only snapshot of JARVIS's beliefs —
  knowledge-store facts seeded into the kernel's probabilistic belief model
  (`WorldModel.beliefs` → `seed_from_confidence`), prefixed by a minimal identity
  self-assertion (`beliefs.identity_assertion`). Shadow, not parity: it is
  introspection only — no decision consumes it, and the knowledge store stays
  authoritative. Surfacing the identity assertion into the *live* conversation
  context (so the model is told who it is) is a user-visible self-model step that
  is **proposed to the owner, not enabled here**.
- **attention** is at **shadow** through `output_gate` (MCU Phase E/E2): the
  gate computes the kernel interruption arbitration (`attention.arbitrate` →
  ALLOW / DEFER / SUPPRESS) alongside its legacy announce decision and logs any
  divergence. Shadow — the kernel verdict is ignored and `output_gate` stays
  authoritative; it establishes the primitive as live-wired before any delegation.

## Keeping this current

`scripts/kernel_adoption.py` scans `custom_components/jarvis` (excluding the
`kernel/` package itself and tests) for references to each primitive — by module
name, dotted use, or any symbol imported `from .kernel`. The declared stage lives
in `_DECLARED` inside that script and is the single source of truth this document
renders from. When you wire a new caller or promote a stage:

1. Make the code change.
2. Update the matching `_DECLARED` entry.
3. Run `python3 scripts/kernel_adoption.py --check` (CI runs this) — it fails if a
   non-pure stage has no live caller, or a primitive is on disk but undeclared.
4. Regenerate the block above with `--markdown`.
