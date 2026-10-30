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
| `control_device` | ● | ◑ | – | ● | ● | ● | ● |
| `bulk_control` | ● | ◑ | – | ● | ● | – | – |
| `execute_plan` | ● | ◑ | – | ● | ● | – | – |
| `run_scene_or_script` | ● | ◑ | – | ● | ● | – | – |
| `set_mode` | ◑ | – | – | ● | ● | – | – |
| `intrusion` | ◑ | · | ● | – | – | – | – |
| `hazard` | ◑ | · | ● | – | – | – | – |
| `delivery` | ◑ | · | ● | – | – | – | – |
| `goals` | ◑ | · | – | · | ● | · | · |
| `proactive` | ● | ◑ | – | ● | ● | ● | ● |
| `friday` | · | · | – | ◑ | · | · | · |
| `homer` | – | – | – | – | – | – | – |

**Kernel coverage (applicable): 67.4%** — honest credit (observe-only shadow earns 0; only parity + full count) over the 48 cells that can actually be wired. The 36 N/A-by-design cells (`–`, listed below) are excluded, not scored as gaps. **This is the number to move.**

Across all 84 cells (N/A counted as un-wired): 38.5% weighted / 38.5% honest. Legend: `·` none  `◐` shadow  `◑` parity  `●` full  `–` n/a.

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
(`outcome` ●), event emission (`event` ●, consumed by the thrash reactor) and plan execution (`plan` ●) are
wired, alongside authority (● — **general enforce**, G4b: the kernel engine's
verdict now gates the action under the max-restriction belt, not just the scoped
security allowlist) and verify-after-act (●). `situation` stays `·` by design —
not every single actuation is a situation, and forcing it would be the inflation
this matrix exists to prevent. It shipped one contract at a time (8.30.0–8.33.0),
each its own release, with authority kept log-only at first — the spine built
structurally first, each enforce flip a separate, explicit decision (log-only →
scoped allowlist G4 → general G4b, all now taken). That authority enforce was
*templated* across the other consequential paths (`bulk_control`, `execute_plan`,
`run_scene_or_script`, `set_mode`, `proactive`), so every one of them now gates on
the engine's verdict (authority ●); execution itself already routes through the
plan/actuator contract on `control_device` via the async plan driver `kernel.plan.aexecute_plan`
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
  raw HA state). Authority at **full** (`enforced_decision`, G4b — the engine's
  verdict gates the action under the max-restriction belt) and a real
  verify-after-act (`_verify_control`, `●`). **Outcome** at full (8.31.0):
  the verify step produces the canonical `kernel.actuator.ActuatorOutcome`
  (requested → executed → observed → **verified / mismatch / failed**) as the
  path's real outcome record — the audit's point 18, *"the service returned
  success" is not "the world reached the expected state"*. **Event** at **full**
  (H): the actuation is published as a canonical `JarvisEvent` (`from_actuation`)
  onto the kernel event bus, which the ledger records **and** the cross-path
  thrash reactor now *consumes* to drive the autonomous back-off — the audit's
  item #8, the event bus as a live nervous system rather than a passive ledger.
  **Plan** at full (B4b): the actuation runs *through* the kernel planner
  (`aexecute_plan`) — a one-step plan whose run_step performs the awaited
  service call — so the planner owns execution. The farthest-along path: only
  `situation` (n/a `–` by design — a one-shot toggle is not a situation) is
  excluded; the `authority` enforce flip is complete — log-only → scoped
  allowlist (G4) → general (G4b) — so the engine now gates every capability.
- **`intrusion`** — mirrors its lifecycle into the kernel **Situation** state
  machine and now **acts on it** (R3c): a user "false alarm" dismissal drives the
  episode to `BENIGN`, and `cognitive_core._check_intrusion` reads that lifecycle
  state back (`intrusion.kernel_calloff_active_sync`) to suppress a re-alarm on
  the same benign motion **even across a reload** that lost the in-memory call-off
  window — a durable false-alarm memory, so `situation` is **`●` full** (the
  kernel situation *drives* the live suppression, not just records it). ADDITIVE
  and fail-safe: a `None`/error read suppresses nothing, so it can only reduce
  false alarms, never miss a real intrusion; reversible via
  `INTRUSION_SITUATION_ENFORCE`. The pure entry-gate verdict (open an
  investigation at all) is separately kernel-owned (R3b, `intrusion_gate`,
  `INTRUSION_GATE_ENFORCE`). Its presence *context* (the away/home/armed signals)
  reads through the `WorldModel` facade (C2, 8.44.0), credited on the
  `cognitive_core` `world_model` adoption, not as a new cell here.
- **`hazard`** (R2c) — the in-home **freeze** hazard lifecycle
  (`SafetyManager._check_freeze`: warning → critical → cleared) is mirrored into a
  kernel Situation (`kind="hazard"`) and now **drives** a de-dup: `_check_freeze`
  reads the open-episode view (`hazard_situation.kernel_freeze_active_sync`) and,
  when a freeze was already announced before a restart (the in-memory
  `_freeze_warned` is lost), suppresses a duplicate **WARNING** — so `situation`
  is **`●` full**. `CRITICAL` is **never** suppressed (it fails toward alerting);
  fail-safe (a `None`/error read lets the warning fire); reversible via
  `HAZARD_SITUATION_ENFORCE` (which also owns the fail-toward-alerting freeze
  verdict, R2b). Smoke / CO / water-leak are not detected by JARVIS today, so
  there is nothing to mirror for them and no detection is invented.
- **`delivery`** (R1b) — the per-camera package delivery lifecycle
  (`package_monitor`: delivered → removed) is mirrored into a kernel Situation
  (`kind="delivery"`, one open episode per camera), and the kernel store is
  **authoritative** for the per-camera "package present" verdict:
  `package_monitor._evaluate_locked` keys the delivered/removed transition off the
  open-episode view (`delivery_situation.kernel_package_present_sync` →
  `open_situations("delivery")`), **not** the in-memory `_STATE` flag — so
  `situation` is **`●` full** (proven by `test_delivery_enforce`; this also fixed
  the #404 restart bug where in-memory state was lost and deliveries were never
  re-announced). Fail-safe to the legacy flag on a `None`/error read, reversible
  via `DELIVERY_SITUATION_ENFORCE`. The earlier parity cell under-credited this
  live enforce — the 9.10.0 metric reform carried over the D3 "log-only mirror"
  description. Mail arrival is a one-shot with no pickup tracking, so there is no
  sustained episode to mirror and none is invented.
- **Situation events (D4)** — `intrusion`, `hazard` and `delivery` each now
  **publish** a canonical `situation.transition` `JarvisEvent`
  (`events.publish_situation` → `kernel.from_situation`) on every lifecycle
  change, so their `event` cell is `◑` parity: the event enters the bus stream and
  the ledger records it, but **no consumer reacts yet**. Publishing is additive
  and best-effort — it never affects the underlying intrusion/freeze/delivery
  handling. This is the step that lifts coverage back up (15.5% → 17.9%).
- **`run_scene_or_script`** — the first Phase B adoption (B1): routed through the
  shared `actuation` envelope for its WorldModel context (`world_model` ◑) and
  actuation event (`event` ●, consumed by the thrash reactor). verify/outcome are **n/a** (`–`, no deterministic
  end-state). **Plan** at **full** (`●`): the activation runs *through* the kernel
  planner — the universal seam (`execute_actuator`) executes it as a one-step
  `aexecute_plan` whose run_step performs the awaited service call, exactly as
  `control_device` does. B1/H3 replaced the old hand-logged shadow plan with this
  seam execution, so the planner owns execution here; the cell was corrected from
  `◐` to `●` to reflect the wiring already in place. **Authority** at **full**
  (●, G4b): the activation is gated on the kernel engine's verdict
  (`enforced_decision`, max-restriction) — a scene/script can do anything a
  confirm-gate would guard, so it runs only if the engine returns ALLOW,
  fail-safe to the path's allow on an engine fault.
- **`set_mode`** (B2) — the mode directive routes its change through the envelope
  (`event` ◑, `plan` ●). `world_model` and verify/outcome are **n/a** (`–`): the
  target is an abstract mode, not a single entity with pre/post state. **Plan at
  full (`●`):** the directive's *application* — set the mode **and** apply its
  on-activation mood (`mode_scene.apply_mode_entry`) — now runs **through the
  kernel planner** as a one-step `aexecute_plan` (precondition → act → record),
  the same planner-owned execution `control_device`/`run_scene_or_script` use.
  This is the real change that lifts the cell off its old descriptive shadow: it
  was shadow because the directive mostly flipped a variable and only `movie`
  mode's dim routed through the planner (in `mode_scene`), so crediting `●` would
  have over-credited every other mode — now *every* `set_mode`, mood-bearing or
  not, is itself a planner-executed, journaled step, fail-safe so a planner fault
  never blocks the mode change. **Authority** at
  **full** (●, G4b): the mode directive is gated on the kernel engine's verdict
  (`enforced_decision`, max-restriction) *before* it is applied — a mode change
  applies scenes that can be consequential, so it proceeds only if the engine
  returns ALLOW, fail-safe to allow on an engine fault.
- **`execute_plan`** (B4) — each step's execution routes through the kernel planner
  (`aexecute_plan`, `plan` ●) and publishes an actuation event (`event` ●,
  consumed by the thrash reactor), each
  run through the authority engine (`authority` ●, G4b — general enforce). **`world_model`** at **parity**
  (`◑`): each step target's pre-action context is read through the facade
  (`actuation.context`) and the event is area-tagged, the same read
  `control_device`/`bulk_control` do, rather than acting on raw state alone. Steps
  are arbitrary services, so verify/outcome stay **n/a** (`–`). Behaviour
  preserved: best-effort read, continue-on-failure, per-step results.
- **`bulk_control`** (B3) — each executed target routed through the envelope
  (`world_model` ◑, `event` ● (consumed by the thrash reactor), `authority` ● —
  G4b: each target is gated on the engine's verdict, held targets skipped and
  reported like protected ones).
  **Plan** at **full** (`●`): every
  target's execution runs *through* the kernel planner — the universal seam
  (`execute_actuator`) drives each as a one-step `aexecute_plan`, the same
  mechanism `control_device` and `proactive` use, so the planner owns execution
  per target (an additional descriptive N-step `bulk_plan` is also recorded, but
  it is incidental to the per-target planner execution the cell credits). The cell
  was corrected from `◐` to `●` to reflect this. Fire-and-forget, so
  per-device verify/outcome are **n/a** (`–`); protected devices are still
  skipped.
- **`goals`** — an *orchestration* path: when a goal comes due it runs the
  headless agent, whose tool-calls actuate through the already-wired paths. What
  goals owns on the spine so far: its steps are expressed as a kernel `Plan`,
  durably journaled (`_shadow_plan`, F1/F2) and kept in step-by-step sync with
  the goal store (H4a). **`plan` at full (H4b)** (●): the kernel journal is now
  the **authoritative** source of a goal's step state — writes commit to it
  first (`_journal_sync_steps`) and reads overlay it onto the store
  (`_apply_journal_authority`), with the goal store kept as its current mirror
  and durable fallback (when the journal has no rows for a goal — pre-H4b, or
  `journal.db` lost — the store stands, so goal state is never lost). The kernel
  Plan now *drives* the goal's operative step state, not just shadows or consults
  it; `_plan_parity` remains as the live agreement/invariant check. Reversible
  via the `goals_kernel_authoritative` flag (default on), which falls the path
  back to store-authoritative parity. Each engage/close also publishes a canonical
  `goal.transition` `JarvisEvent` (`publish_goal`, `event` ◑ parity — enters the
  stream + ledger, no consumer reacts yet). `world_model`/`authority`/`verify`/
  `outcome` stay honest **gaps** (`·`): goals doesn't read state or actuate
  directly (the agent does), so they are not claimed here; `situation` is n/a
  (`–`).
- **`proactive`** (9.34.0) — JARVIS acting on a learned/trusted pattern *of its
  own accord*, the discretionary autonomous device path. This row now names the
  real path, `cognitive_core._execute_action_data` — **not** the
  `proactive_audio.py` TTS/announcement path it used to point at (that is
  communication, not actuation on the home, and so was crediting nothing). The
  path routes each target through the universal actuation seam
  (`actuation.execute_actuator`), so the kernel planner owns execution (`plan` ●,
  via `aexecute_plan` inside the seam) and the canonical actuation `JarvisEvent`
  is published (`event` ●, consumed by the cross-path thrash reactor, which the
  autonomous path itself then consults to back off). **Authority** at **full** (●, G4b): each target is
  gated on the engine's verdict (`enforced_decision`, max-restriction) — a held
  verdict means JARVIS does **not** act of its own accord on that target (no one
  to confirm an autonomous action, so it fails closed and skips; the engine never
  lets an autonomous security action through), fail-safe to the path's proceed
  decision on an engine fault. **Verify** (`●`) + **outcome** (`●`): for the deterministic services
  JARVIS actuates of its own accord (turn_on/off, lock/unlock, open/close) a
  verify-after-act (`_proactive_verify_factory`) reads the target back and
  records the canonical `ActuatorOutcome` (`actuation.outcome`) — VERIFIED when
  the world reached the expected state, MISMATCH when it did not. This is the
  audit's point 18 ("the service returned success" is not "the world reached the
  expected state") applied where it matters most: no human is watching an
  autonomous action, so an unlanded one must be *noticed*. A mismatch is recorded
  and surfaced, never silently re-fired (that would re-actuate the home off-loop
  without re-checking the agency gates). **`world_model`** at **parity** (◑): a
  pre-action context snapshot is read through the WorldModel facade
  (`actuation.context`) and the actuation is area-tagged — the same read
  `control_device`/`run_scene_or_script` do. `situation` is n/a (`–`). The G1/G2
  agency-budget + loop-detect **enforce** gates that decide
  *whether* a proactive action runs are a separate rate/loop governor, not the
  authority permission engine measured here.
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
> publishes a canonical `JarvisEvent` (8.32.0, `event` ● — consumed by the
> cross-path thrash reactor) and runs *execution
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
