# Behavioral kernel-coverage matrix

> Generated signal, kept honest by `scripts/kernel_coverage.py`. Run it for the
> live table, `--markdown` for the block below, `--check` in CI. **Do not inflate
> the declared stages to make the number look better** — raise a cell only when
> the real execution path reaches that stage, and update its evidence with it.

`KERNEL_ADOPTION.md` answers *"does a live module reference a kernel primitive?"*
This answers the sharper question the external MCU audit raised:

> **What fraction of behaviour-bearing paths actually pass through the kernel
> contract** — Event → WorldModel → Situation → Authority → Plan → Verify →
> Outcome?

A module can `from .kernel import authorize` and still have actuators that bypass
Authority, so *reference* coverage overstates reality. This matrix tracks, per
behaviour-bearing path, how far each pipeline contract is actually wired
(`· none  ◐ shadow  ◑ parity  ● full`), and the script verifies every non-`none`
claim against evidence that must exist in the path's source — the matrix cannot
drift into fiction.

## Current matrix

<!-- BEGIN kernel-coverage (python3 scripts/kernel_coverage.py --markdown) -->
| Path | event | world_model | situation | authority | plan | verify | outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `control_device` | ◑ | ◑ | · | ◑ | ◐ | ● | ● |
| `bulk_control` | · | · | · | · | · | · | · |
| `execute_plan` | ◑ | · | · | · | ● | · | · |
| `run_scene_or_script` | ◑ | ◑ | · | · | ◐ | · | · |
| `set_mode` | ◑ | · | · | · | ◐ | · | · |
| `intrusion` | · | · | ◑ | · | · | · | · |
| `goals` | · | · | · | · | · | · | · |
| `proactive` | · | · | · | · | · | · | · |
| `friday` | · | · | · | · | · | · | · |
| `homer` | · | · | · | · | · | · | · |

**Kernel coverage: 13.3%** (· none ◐ shadow ◑ parity ● full)
<!-- END kernel-coverage -->

**13.3% is the honest number today** — most paths are still legacy, exactly the
state the audit flagged ("the kernel is not yet the operating system of JARVIS").
It *dropped* from 8.9% at 8.34.0 on purpose: the new governance gate (below)
surfaced two consequential tools that were acting on the home without being in
this matrix at all — `run_scene_or_script` and `set_mode` — so they are now
declared as the legacy paths they are. A lower, complete number beats a higher,
partial one.
This figure is the one to move: *"X% of behaviour-bearing paths are
kernel-authoritative"* is far more meaningful than *"Kernel Phase N completed."*

**Phase A (the third MCU audit's one actionable recommendation)** is complete
for the `control_device` path: its pre-action read (`world_model` ◑), outcome
(`outcome` ●), event emission (`event` ◑) and plan expression (`plan` ◐) are
wired, alongside the pre-existing authority parity (◑, log-only) and
verify-after-act (●). `situation` stays `·` by design — not every single
actuation is a situation, and forcing it would be the inflation this matrix
exists to prevent. It shipped one contract at a time (8.30.0–8.33.0), each its
own release, with authority staying **log-only / owner-gated** — the spine is
built structurally; the enforce flip is a separate, explicit decision. The next
move is to *template* this onto the other consequential paths (`bulk_control`,
`execute_plan`, `run_scene_or_script`, `set_mode`, …) and, eventually, route
execution itself through the plan/actuator contract. The async plan driver this
needs now exists — `kernel.plan.aexecute_plan` (B-async, 8.39.0).

**Phase B is underway.** B0 (8.36.0) extracted the golden-path wiring into a
shared **`actuation`** envelope (`actuation.py`: `context` / `request` /
`plan_shadow` / `emit_event` / `outcome`) so each remaining actuator routes
through the *same* kernel contract instead of re-implementing it. `control_device`
adopts it with no behaviour change (coverage unchanged); the other paths adopt it
one release at a time.

## What the cells mean today

- **`control_device`** — the Phase A golden path in progress. **WorldModel** at
  parity (8.30.0): its pre-action context snapshot is read through the
  `WorldModel` facade, the canonical context authority, rather than a bare
  `states.get` (parity, not full, because the post-action read-back still reads
  raw HA state). Authority at **parity** (`authority_bridge`, log-only) and a
  real verify-after-act (`_verify_control`, `●`). **Outcome** at full (8.31.0):
  the verify step produces the canonical `kernel.actuator.ActuatorOutcome`
  (requested → executed → observed → **verified / mismatch / failed**) as the
  path's real outcome record — the audit's point 18, *"the service returned
  success" is not "the world reached the expected state"*. **Event** at parity
  (8.32.0): the actuation is published as a canonical `JarvisEvent`
  (`from_actuation`) onto the kernel event bus, which the ledger records —
  parity, not full, because it enters the stream but no cognitive consumer
  reacts to it yet (the audit's item #8, the event bus as nervous system).
  **Plan** at shadow (8.33.0): the actuation is expressed as a canonical
  one-step `kernel.plan.Plan` (preconditions → act → postconditions, with an
  idempotency key) and logged — shadow, because `execute_plan` is synchronous
  while HA actuation is `await`-ed, so the plan does not yet *own* execution.
  The farthest-along path.
- **`intrusion`** — mirrors its lifecycle into the kernel **Situation** state
  machine at parity.
- **`run_scene_or_script`** — the first Phase B adoption (B1): routed through the
  shared `actuation` envelope for its WorldModel context (`world_model` ◑),
  actuation event (`event` ◑) and shadow plan (`plan` ◐). No deterministic
  end-state, so no verify/outcome; no confirm-gate, so no authority cell.
- **`set_mode`** (B2) — the mode directive records its change through the envelope
  (`event` ◑, `plan` ◐). It is not a single-entity actuation (its home effect is
  the applied mode scene), so there is no `world_model` entity context and no
  verify/outcome; the actuation event's target is the mode name.
- **`execute_plan`** (B4) — each step's execution routes through the kernel planner
  (`aexecute_plan`, `plan` ●) and publishes an actuation event (`event` ◑); steps
  are arbitrary services, so there is no single expected end-state (no
  verify/outcome). Behaviour preserved: continue-on-failure, per-step results.
- **`bulk_control`** — still legacy (only the `policy` confirmation gate).
- **`goals` / `proactive` / `friday` / `homer`** — not yet wired to any kernel
  contract.

> **Actuator contract (8.26.0 → 8.31.0):** `control_device` constructs a
> canonical `kernel.actuator.ActuatorRequest` (who/intent/target/correlation/
> idempotency/**expected_outcome**), and the verify step now produces the
> matching `ActuatorOutcome` (requested → executed → observed → verified). That
> is why the **outcome** cell above is `●` and `KERNEL_ADOPTION.md` lists
> `actuator` at **parity** (8.31.0), up from shadow. The actuation also now
> publishes a canonical `JarvisEvent` (8.32.0, `event` ◑) and is expressed as a
> one-step `kernel.plan.Plan` (8.33.0, `plan` ◐). Still ahead: routing
> *execution itself* through the plan/actuator contract (so the kernel, not the
> legacy branch, performs the `await`ed service call) — the step that raises
> `plan` from shadow to full, now unblocked by the async plan driver
> (`kernel.plan.aexecute_plan`, B-async).

## How to raise the number

The matrix is driven by `_PATHS` in `scripts/kernel_coverage.py`. To record real
progress:

1. Wire the path onto the kernel contract (e.g. route `control_device` through the
   `ActuatorRequest` contract, or have a path publish a `JarvisEvent`).
2. Raise that cell's `stage` in `_PATHS` and set `evidence` to a symbol that must
   appear in the path's source.
3. `python3 scripts/kernel_coverage.py --check` (CI runs it) — fails if a declared
   stage has no evidence.
4. Regenerate the block above with `--markdown`.

No primitive is at `enforce` and authority stays **log-only / owner-gated**; this
matrix measures wiring, not a licence to flip enforcement.

## Governance rule (8.34.0): new consequential actions enter through the kernel

> **Any new behaviour that can cause a consequential action on the home must
> enter through the kernel contract from the start** — and must be a declared
> path in `_PATHS` above.

This is the audit's "rule I would add now", and it is enforced, not just
written down. `scripts/kernel_coverage.py --check` (run in CI) now also fails if
a tool in agent's `_TOOL_MAP` is **neither** a declared coverage path **nor**
listed in the `_NON_ACTUATOR_TOOLS` allowlist (read-only / informational /
bookkeeping tools). So a newly-added actuator tool cannot land uncounted: the
author must either wire it as a path or explicitly classify it as a
non-actuator. The gate paid for itself immediately — it surfaced
`run_scene_or_script` and `set_mode`, two consequential tools that were acting on
the home without appearing in this matrix, now declared as the legacy paths they
are. When you add a tool:

- **It can change the home** (calls a service, runs a scene/script, applies a
  mode) → add it to `_PATHS`, ideally already wired onto the kernel contract
  (WorldModel read → ActuatorRequest/Outcome → actuation event → one-step Plan).
- **It only reads or does bookkeeping** → add it to `_NON_ACTUATOR_TOOLS` with a
  one-line rationale.
