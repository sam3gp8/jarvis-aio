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
(`· none  ◐ shadow  ◑ parity  ● full  – n/a`), and the script verifies every
non-`none` claim against evidence that must exist in the path's source — the
matrix cannot drift into fiction.

**The headline is the honest *applicable-cell* figure.** Not every contract can
exist for every path: a one-shot device toggle is not a situation, a
fire-and-forget bulk write has no single end-state to verify, a detection path
observes and never actuates. Those cells are declared **N/A-by-design** (`–`),
excluded from the denominator, and listed one-by-one with their reason below, so
the percentage measures progress against *what can actually be wired* instead of
being permanently diluted. N/A is held to a conservative bar — when in doubt a
cell stays an un-wired gap (`·`), which lowers the number — and because every
exclusion is spelled out, a real gap cannot be quietly reclassified as N/A to
flatter the figure. The all-84-cell figures (weighted and honest over the full
grid) are kept underneath for continuity.

## Current matrix

<!-- BEGIN kernel-coverage (python3 scripts/kernel_coverage.py --markdown) -->
| Path | event | world_model | situation | authority | plan | verify | outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `control_device` | ◑ | ◑ | – | ◑ | ● | ● | ● |
| `bulk_control` | ◑ | ◑ | – | ◑ | ◐ | – | – |
| `execute_plan` | ◑ | · | – | ◑ | ● | – | – |
| `run_scene_or_script` | ◑ | ◑ | – | ◑ | ◐ | – | – |
| `set_mode` | ◑ | – | – | ◑ | ◐ | – | – |
| `intrusion` | ◑ | · | ◑ | – | – | – | – |
| `hazard` | ◑ | · | ◑ | – | – | – | – |
| `delivery` | ◑ | · | ◑ | – | – | – | – |
| `goals` | ◑ | · | – | · | ◐ | · | · |
| `proactive` | ◑ | · | – | ◑ | ● | · | · |
| `friday` | · | · | – | ◑ | · | · | · |
| `homer` | – | – | – | – | – | – | – |

**Kernel coverage (applicable): 42.4%** — honest credit (observe-only shadow earns 0; only parity + full count) over the 48 cells that can actually be wired. The 36 N/A-by-design cells (`–`, listed below) are excluded, not scored as gaps. **This is the number to move.**

Across all 84 cells (N/A counted as un-wired): 25.8% weighted / 24.2% honest. Legend: `·` none  `◐` shadow  `◑` parity  `●` full  `–` n/a.

**N/A-by-design cells** — excluded from the applicable denominator because the contract is structurally meaningless for the path. Every exclusion is listed with its reason so a real gap can't hide here:

- `control_device` · **situation** — a single imperative device actuation is not a situation lifecycle (no open/close state to model)
- `bulk_control` · **situation** — imperative multi-device actuation, not a situation lifecycle
- `bulk_control` · **verify** — fire-and-forget across many targets — no single expected end-state to read back
- `bulk_control` · **outcome** — fire-and-forget — no per-device actuator outcome record
- `execute_plan` · **situation** — an imperative multi-step plan is not a situation lifecycle
- `execute_plan` · **verify** — arbitrary services per step — no single expected end-state to read back
- `execute_plan` · **outcome** — arbitrary services — no single actuator outcome record
- `run_scene_or_script` · **situation** — scene/script activation is imperative, not a situation lifecycle
- `run_scene_or_script` · **verify** — a scene has no single deterministic end-state to read back
- `run_scene_or_script` · **outcome** — no single expected end-state — no actuator outcome record
- `set_mode` · **world_model** — the target is an abstract mode, not a single entity with pre/post state to snapshot
- `set_mode` · **situation** — a mode directive is imperative, not a situation lifecycle
- `set_mode` · **verify** — the mode's home effect is a scene — no single deterministic end-state to read back
- `set_mode` · **outcome** — no single expected end-state — no actuator outcome record
- `intrusion` · **authority** — detection path — it observes and raises a situation, it does not actuate, so there is no authority decision
- `intrusion` · **plan** — detection path — produces no actuation plan
- `intrusion` · **verify** — nothing is actuated by this path, so there is nothing to verify
- `intrusion` · **outcome** — nothing is actuated — no actuator outcome record
- `hazard` · **authority** — detection path — observes a hazard, does not actuate
- `hazard` · **plan** — detection path — produces no actuation plan
- `hazard` · **verify** — nothing is actuated by this path — nothing to verify
- `hazard` · **outcome** — nothing is actuated — no actuator outcome record
- `delivery` · **authority** — detection path — observes a delivery, does not actuate
- `delivery` · **plan** — detection path — produces no actuation plan
- `delivery` · **verify** — nothing is actuated by this path — nothing to verify
- `delivery` · **outcome** — nothing is actuated — no actuator outcome record
- `goals` · **situation** — goal-driven actuation is imperative, not a situation lifecycle
- `proactive` · **situation** — discretionary autonomous actuation is imperative, not a situation lifecycle
- `friday` · **situation** — a persona actuation path is imperative, not a situation lifecycle
- `homer` · **event** — read-only diagnostic sub-agent (actuating: False) — it raises no lifecycle and publishes no actuation event
- `homer` · **world_model** — read-only — it takes no action, so there is no pre-action context to snapshot
- `homer` · **situation** — read-only diagnostic sub-agent — not a situation lifecycle
- `homer` · **authority** — read-only diagnostic sub-agent — it observes and reports, it never actuates, so there is no authority decision
- `homer` · **plan** — read-only — produces no actuation plan
- `homer` · **verify** — nothing is actuated by this path — nothing to verify
- `homer` · **outcome** — nothing is actuated — no actuator outcome record
<!-- END kernel-coverage -->

**The headline figure above is the one to move** — most paths are still legacy,
exactly the state the audit flagged ("the kernel is not yet the operating system
of JARVIS"). Every percentage is generated by `scripts/kernel_coverage.py` from
the one matrix (the headline is honest credit — observe-only shadow earns 0 — over
only the *applicable* cells; the all-84 figures give shadow ⅓ / 0 credit over the
full grid), and `--check` fails CI if this file's embedded block, figures or N/A
ledger drift from the generator — so no figure can be hand-edited or silently
diverge again. The all-84 honest number *dropped* from 8.9% at 8.34.0 on purpose:
the governance gate (below) surfaced two consequential tools that were acting on
the home without being in this matrix at all — `run_scene_or_script` and
`set_mode` — so they are now declared as the legacy paths they are. A lower,
complete number beats a higher, partial one.
This figure is the one to move: *"X% of behaviour-bearing paths are
kernel-authoritative"* is far more meaningful than *"Kernel Phase N completed."*
The agreed target is **80% of applicable cells** — the strangler-fig all but
complete — reached one path at a time, each enforce flip a separate owner-gated
decision.

**Phase A (the third MCU audit's one actionable recommendation)** is complete
for the `control_device` path: its pre-action read (`world_model` ◑), outcome
(`outcome` ●), event emission (`event` ◑) and plan execution (`plan` ●) are
wired, alongside authority (◑ — log-only on the spine for most capabilities,
with the scoped security-capability allowlist now **enforced**, G4) and
verify-after-act (●). `situation` stays `·` by design — not every single
actuation is a situation, and forcing it would be the inflation this matrix
exists to prevent. It shipped one contract at a time (8.30.0–8.33.0), each its
own release, with authority kept **log-only** at the time — the spine built
structurally first, each enforce flip a separate, explicit decision (the scoped
G4 flip has since been taken). The next move is to *template* this onto the other
consequential paths (`bulk_control`, `execute_plan`, `run_scene_or_script`,
`set_mode`, …); execution itself already routes through the plan/actuator
contract on `control_device` via the async plan driver `kernel.plan.aexecute_plan`
(B-async, 8.39.0 → B4b), which is why its `plan` cell is `●`.

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
  **Plan** at full (B4b): the actuation runs *through* the kernel planner
  (`aexecute_plan`) — a one-step plan whose run_step performs the awaited
  service call — so the planner owns execution. The farthest-along path: only
  `situation` (n/a `–` by design — a one-shot toggle is not a situation) is
  excluded; the scoped `authority` enforce flip (G4) is live, and the remaining
  move is the broader, unscoped flip — owner-gated.
- **`intrusion`** — mirrors its lifecycle into the kernel **Situation** state
  machine at parity, and (D1) **verifies** the mirrored state agrees with the
  legacy intrusion verdict after every episode event (`_record_parity`,
  log-only, WARNING on divergence) — genuine parity, not a blind shadow copy.
  Still owner-gated and non-authoritative: the legacy SafetyManager path owns
  the intrusion decision; flipping the situation to authoritative is a separate
  owner-approved step, deliberately not taken. Its presence *context* (the away/home/armed signals that
  decide whether motion is a possible intruder) reads through the `WorldModel`
  facade as of C2 (8.44.0), behaviour-identical with a fail-safe raw-sweep
  fallback — but that read lives in `cognitive_core.py` (`SafetyManager` /
  `LockdownManager`), credited on the already-parity `cognitive_core`
  `world_model` adoption, not as a new cell on this spine path (whose module,
  `intrusion.py`, does no presence reads).
- **`hazard`** (D2) — the in-home **freeze** hazard lifecycle
  (`SafetyManager._check_freeze`: warning → critical → cleared) is mirrored into a
  kernel Situation (`kind="hazard"`) and its agreement verified
  (`hazard_situation._record_parity`), log-only parity. Adding this path honestly
  surfaces hazards as a tracked behaviour path — only the `situation` cell is
  wired, so the overall number *dips* slightly (that is the point: the matrix
  counts the un-wired cells too). Smoke / CO / water-leak are not detected by
  JARVIS today, so there is nothing to mirror for them and no detection is
  invented. The freeze alerts themselves are unchanged — the mirror is additive,
  off-loop and best-effort.
- **`delivery`** (D3) — the per-camera package delivery lifecycle
  (`package_monitor`: delivered → removed) is mirrored into a kernel Situation
  (`kind="delivery"`, one open episode per camera) and its agreement verified
  (`delivery_situation._record_parity`), log-only parity. Mail arrival is a
  one-shot with no pickup tracking, so there is no sustained episode to mirror
  and none is invented. The delivery announcements and per-camera state machine
  are unchanged — the mirror is additive, off-loop and best-effort.
- **Situation events (D4)** — `intrusion`, `hazard` and `delivery` each now
  **publish** a canonical `situation.transition` `JarvisEvent`
  (`events.publish_situation` → `kernel.from_situation`) on every lifecycle
  change, so their `event` cell is `◑` parity: the event enters the bus stream and
  the ledger records it, but **no consumer reacts yet**. Publishing is additive
  and best-effort — it never affects the underlying intrusion/freeze/delivery
  handling. This is the step that lifts coverage back up (15.5% → 17.9%).
- **`run_scene_or_script`** — the first Phase B adoption (B1): routed through the
  shared `actuation` envelope for its WorldModel context (`world_model` ◑),
  actuation event (`event` ◑) and shadow plan (`plan` ◐). verify/outcome are
  **n/a** (`–`, no deterministic end-state). **Authority** at **parity** (◑,
  authority-breadth): activation now records the kernel authority engine's verdict
  (`record_control_parity`, log-only) against the path's no-gate allow — a
  scene/script can do anything a confirm-gate would guard, so its authority
  contract is measured, not blank. Log-only; it never gates the activation.
- **`set_mode`** (B2) — the mode directive records its change through the envelope
  (`event` ◑, `plan` ◐). `world_model` and verify/outcome are **n/a** (`–`): the
  target is an abstract mode, not a single entity with pre/post state. **Authority**
  at **parity** (◑, authority-breadth): the mode change now records the authority
  engine's verdict (`record_control_parity`, log-only) against the path's no-gate
  allow — a mode change applies scenes that can be consequential, so its authority
  contract is measured, not blank. Log-only; it never gates the directive.
- **`execute_plan`** (B4) — each step's execution routes through the kernel planner
  (`aexecute_plan`, `plan` ●) and publishes an actuation event (`event` ◑), each
  run through the authority engine (`authority` ◑). Steps are arbitrary services,
  so verify/outcome are **n/a** (`–`). `world_model` is an un-wired **gap** (`·`).
  Behaviour preserved: continue-on-failure, per-step results.
- **`bulk_control`** (B3) — recorded as one explicit N-step kernel `Plan` (shadow,
  `plan` ◐) with each executed target routed through the envelope (`world_model`
  ◑, `event` ◑, `authority` ◑). Fire-and-forget, so per-device verify/outcome are
  **n/a** (`–`); protected devices are still skipped.
- **`goals`** — an *orchestration* path: when a goal comes due it runs the
  headless agent, whose tool-calls actuate through the already-wired paths. What
  goals owns on the spine so far: its steps are expressed as a kernel `Plan` and
  durably journaled (`_shadow_plan`, F1/F2, `plan` ◐ shadow — built and recorded
  but never executed or consulted; the goal store stays authoritative), and each
  engage/close publishes a canonical `goal.transition` `JarvisEvent`
  (`publish_goal`, `event` ◑ parity — enters the stream + ledger, no consumer
  reacts yet). `world_model`/`authority`/`verify`/`outcome` stay honest **gaps**
  (`·`): goals doesn't read state or actuate directly (the agent does), so they
  are not claimed here; `situation` is n/a (`–`).
- **`proactive`** (9.34.0) — JARVIS acting on a learned/trusted pattern *of its
  own accord*, the discretionary autonomous device path. This row now names the
  real path, `cognitive_core._execute_action_data` — **not** the
  `proactive_audio.py` TTS/announcement path it used to point at (that is
  communication, not actuation on the home, and so was crediting nothing). The
  path routes each target through the universal actuation seam
  (`actuation.execute_actuator`), so the kernel planner owns execution (`plan` ●,
  via `aexecute_plan` inside the seam) and the canonical actuation `JarvisEvent`
  is published (`event` ◑) — wiring that already existed but went uncredited
  while the row was mis-pointed. **Authority** at **parity** (◑, log-only,
  *new* this release): the engine verdict is recorded (`record_control_parity`)
  against the path's proceed decision. `world_model`/`verify`/`outcome` stay
  honest **gaps** (`·`): the path takes no pre-action WorldModel snapshot and
  passes `verify=None` (no deterministic read-back, so no outcome record);
  `situation` is n/a (`–`). The G1/G2 agency-budget + loop-detect **enforce**
  gates that decide *whether* a proactive action runs are a separate rate/loop
  governor, not the authority permission engine measured here.
- **`friday`** (9.34.0) — the FRIDAY background-automator sub-agent, the one
  profile that may actuate. It acts **only** through its grant set
  (`control_device` / `bulk_control` / `run_scene_or_script`), each of which
  records the kernel authority engine's verdict; the H6 delegation scope
  (`_DELEGATION_ATTRIBUTION`) brackets friday's run so every verdict is recorded
  against `actor="friday"`. So **authority** is at **parity** (◑), attributed to
  the sub-agent — measured, not blank. The other actuation contracts
  (`event`/`world_model`/`plan`/`verify`/`outcome`) are performed inside the
  grant-set handlers and counted on **their** rows; friday's own path adds no
  wiring there, so they stay conservative **gaps** (`·`) rather than
  double-count. `situation` is n/a (`–`).
- **`homer`** (9.34.0) — the HOMER diagnostic sub-agent is **read-only**
  (`actuating: False`): its tool set is entirely observational, it investigates
  and reports a root cause to its parent, and it never actuates, raises no
  lifecycle, and publishes no event. Every contract is therefore **n/a-by-design**
  (`–`) — the same shape as a detection path's actuation cells, extended across
  the whole row because homer isn't even a situation path. It was previously
  mis-counted as six applicable actuation **gaps**; those were never wireable
  (homer must never actuate), so reclassifying them N/A — with every reason
  listed in the ledger — corrects a false penalty rather than flattering the
  figure. The row is kept, all-N/A, so the census records this profile as
  non-actuating by design.

> **Actuator contract (8.26.0 → 8.31.0):** `control_device` constructs a
> canonical `kernel.actuator.ActuatorRequest` (who/intent/target/correlation/
> idempotency/**expected_outcome**), and the verify step now produces the
> matching `ActuatorOutcome` (requested → executed → observed → verified). That
> is why the **outcome** cell above is `●` and `KERNEL_ADOPTION.md` lists
> `actuator` at **parity** (8.31.0), up from shadow. The actuation also
> publishes a canonical `JarvisEvent` (8.32.0, `event` ◑) and runs *execution
> itself* through a one-step `kernel.plan.Plan` executed by the async plan driver
> (`kernel.plan.aexecute_plan`, B-async, 8.39.0 → B4b): the kernel, not the legacy
> branch, performs the `await`ed service call, which is why the **plan** cell
> above is `●` (full), up from the shadow expression it started as (8.33.0).

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

This matrix measures **wiring depth** on the pipeline spine — a separate axis from
the **enforce ladder** tracked in `KERNEL_ADOPTION.md`. A `●` cell means the
contract is fully *wired* on the path, **not** that its decision binds. Several
primitives are at enforce there (agency budget G1, loop-detect G2, authority G4 —
scoped to a security-capability allowlist), each its own owner-gated flip; raising
a cell here is wiring, not a licence to flip enforcement.

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
