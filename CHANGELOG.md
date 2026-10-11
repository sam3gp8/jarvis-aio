## [8.223.0] — Phase Y (Self-Optimization) — parity

Advances the `optimize` primitive shadow → parity, and records an important honesty
boundary about its enforce rung.

- **`decision_record.py`**: alongside the Phase Y shadow (which maps the
  interruption-budget assessment into a `kernel.optimize` tuning proposal for the
  SENSITIVE `interrupt_threshold`), `_emit_optimize_shadow` now logs a **parity**
  check (`OPTIMIZE_PARITY`): the optimizer proposes *raising* the threshold exactly
  when the live output gate is already damping its announcement cap
  (budget `multiplier < 1.0`), so parity logs whether the two agree on "interrupt
  less" — proving the SENSITIVE proposal tracks the live mechanism. Observe-only.
- **Honesty note on enforce**: `SELF_OPTIMIZE_ENFORCE` auto-applies **SAFE-tier**
  tunings only (SENSITIVE stays proposal-only, FORBIDDEN immutable, UNKNOWN →
  FORBIDDEN by `classify()`). The *only* live tuning surface today proposes a
  SENSITIVE parameter, which is structurally never auto-applied — so enforce has **no
  honest live apply point yet**. Rather than fabricate a SAFE-tier tuning surface
  the system doesn't have, **parity is `optimize`'s honest ceiling** until a genuine
  SAFE-tier live tuning surface exists; that remains evergreen work (Phase Ω). The
  tiered guardrail (safety thresholds / authority / identity can never be tuned)
  holds at every rung.
- Adoption: `optimize` advances **shadow → parity** (`_DECLARED`). Regenerated the
  adoption matrix + Constitution ledger.
- Tests: `test_optimize_shadow.py` adds the parity cases (agreement logged when
  over-interrupting and when healthy).

Six kernel gates + audit + docs-sync + changelog-extract green. Version
8.222.0 → 8.223.0.

## [8.222.0] — Phase V (Long-Horizon Agency) — enforce (owner-authorized)

Third owner-authorized enforce flip. The durable long-horizon ledger is promoted to
the **authoritative boot continuity view**: on boot JARVIS surfaces the multi-day
goals it is resuming.

- **`continuity.py`**: when `LONG_HORIZON_ENFORCE` is on, `boot_summary` surfaces a
  `JARVIS (long-horizon): resuming N multi-day goal(s): …` line built by
  `_long_horizon_resume_line` from the durable ledger (the pre-restart snapshot) —
  each in-flight goal's title, progress %, and next milestone. Nothing surfaced
  long-horizon goals at boot before (the parity only logged a match count at debug).
- **Non-redundant, non-actuating, fail-safe**: this is *not* a redundant
  "restore progress" step — the goals store is already durable sqlite, so progress
  already survives restart; the enforce adds the thing that was missing, a boot
  continuity *view* of what JARVIS is picking back up. It only surfaces a line
  (never drives an action). Kill-switch back to parity: `LONG_HORIZON_ENFORCE` /
  the `long_horizon_enforce` config key; fail-safe = session-scoped (no resume line,
  today's silent boot) on no-ledger / nothing-in-flight / any error.
- Adoption: `long_horizon` advances **parity → enforce** (`_DECLARED`). Regenerated
  the adoption matrix + Constitution ledger.
- Tests: `test_long_horizon_enforce.py` pins the resume line (in-flight goals with
  progress + next milestone), that complete goals are omitted, the boot-summary
  surfacing, both kill-switches, and never-raises.

Owner-authorized per the owner-gated enforce policy (Constitution A2); kill-switched,
non-actuating, fail-safe. Six kernel gates + audit + docs-sync + changelog-extract
green. Version 8.221.0 → 8.222.0.

## [8.221.0] — Phase P (Proactive Household Intelligence) — enforce (owner-authorized)

Second owner-authorized enforce flip. The `kernel.household` model is promoted from
observe-only to an **authoritative proactive advisory** — but as an *augment*, so
it can only ever add a proposal, never change what JARVIS does.

- **`proactive_audio.py`**: when `HOUSEHOLD_PROACTIVE_ENFORCE` is on, the household
  model's `anticipate()` suggestions are surfaced at INFO as a live
  `household(proactive)` advisory (a real proactive voice), not merely the
  "would-suggest" shadow line. It **augments, never replaces** the
  `PredictiveHabitMatrix`: the predictor is left exactly as-is as the fail-safe
  heuristic floor, so a household fault or an empty model leaves today's behaviour
  untouched.
- **Advisory-only / non-actuating**: a household `Suggestion` carries **no
  actuator** (the primitive's structural invariant), so enforce changes only what
  JARVIS *proposes*, never what it does — proactive *execution* stays gated behind
  `PREDICTOR_AUTOEXECUTE` (off). Kill-switch back to parity:
  `HOUSEHOLD_PROACTIVE_ENFORCE` / the `household_proactive_enforce` config key;
  fail-safe = current heuristics (the predictor alone).
- Adoption: `household` advances **parity → enforce** (`_DECLARED`). Regenerated the
  adoption matrix + Constitution ledger.
- Tests: `test_household_shadow.py` adds the enforce cases (an authoritative advisory
  surfaces once the model warms up, silence when there's no suggestion, and both
  kill-switches).

Owner-authorized per the owner-gated enforce policy (Constitution A2); kill-switched,
augment-only, fail-safe. Six kernel gates + audit + docs-sync + changelog-extract
green. Version 8.220.0 → 8.221.0.

## [8.220.0] — Phase S (Self Model) — enforce (owner-authorized)

First of the owner-authorized enforce flips. The self-report is now **sourced from
the self-model with its confabulations removed** — the safest flip to make live: it
only changes how JARVIS *answers* "what can you do / are you sure?", carries no
authority, and actuates nothing.

- **`agent.py`**: `_build_self_model_snapshot` now, when `SELF_MODEL_ENFORCE` is on,
  projects the self-model against an independent **ground truth** (a capability is
  really performable only when its enforcement switch is live-enabled AND its
  backing module resolves). A switch-on-but-unresolvable capability is **demoted to
  unavailable** (and stated as a limit) instead of being reported available — so
  `cognitive_status` can no longer confabulate a capability JARVIS cannot perform.
  The ground-truth helper (`_self_model_ground_truth`) is factored out and shared
  with the existing parity check; `_project_self_model` gains an optional
  `ground_truth` arg for the demotion.
- **Strictly tightening / fail-safe**: enforce can only *remove* a capability claim,
  never add one — it cannot grant, activate, or widen anything (authority lives in
  the authority primitive). Kill-switch back to parity: `SELF_MODEL_ENFORCE` /
  the `self_model_enforce` config key. If the ground-truth read fails or is empty,
  the projection falls back to the unfiltered (parity) self-model, so a fault can
  only ever restore today's behaviour.
- Adoption: `self_model` advances **parity → enforce** (`_DECLARED`). Regenerated the
  adoption matrix + Constitution ledger.
- Tests: `test_self_model_enforce.py` pins the demotion of a confabulated capability,
  that a genuinely-available one is kept, the "absent from ground truth → not
  demoted" and "no ground truth → legacy" fail-safes, an off switch staying
  unavailable, and both kill-switches.

Owner-authorized per the roadmap's owner-gated enforce policy (Constitution A2);
kill-switched and fail-safe. Six kernel gates + audit + docs-sync + changelog-extract
green. Version 8.219.0 → 8.220.0.

## [8.219.0] — Phase Z (Resilient Compute Federation) — shadow rung

Opens Phase Z: the pure `kernel.resilience` policy (local-first, offline-safe
compute-tier fallback) gets its live binder on the connectivity circuit breaker,
observe-only.

- **`connectivity.py`**: the breaker already tracks whether the cloud LLM is
  reachable (CLOSED ↔ OPEN). At each real transition — cloud reachable again
  (`record_success` closes the breaker) or cloud lost (`record_failure` opens it) —
  `_emit_resilience_shadow` folds the real health into `kernel.resilience` as two
  tiers (a canonical `haos-local` tier, always healthy; a disposable `cloud-llm`
  tier, healthy iff the breaker is closed) and logs the would-be tier policy:
  `chosen` (always `haos-local` — local-first), `offline_safe` (always true — the
  canonical brain stays under the HA integration), and `offload_targets` (cloud,
  only while healthy). The emit fires strictly on a state *transition* (computed
  inside the lock, emitted after releasing it), so a steady breaker and
  sub-threshold failures stay quiet.
- **Why this surface**: the breaker is the "conservative breaker" the resilience
  primitive was written against, and a cloud outage is a real, observable event.
  The shadow makes the **HAOS boundary** visible live — when cloud drops, the
  offload set empties and JARVIS keeps running locally; the canonical tier is never
  an offload target.
- **Observe-only / fail-safe**: the breaker's own CLOSED/OPEN verdict is unchanged;
  this only adds a `resilience(shadow)` log line. Kill-switched by `RESILIENCE_SHADOW`
  and the `resilience_shadow` config key (both default on); any failure is swallowed,
  never propagated into the breaker path.
- **`kernel/__init__.py`**: re-export the `resilience` submodule (audit IMPORTS
  convention). **`kernel/resilience.py`** docstring names `connectivity` as the live
  binder.
- Adoption: `resilience` advances **pure → shadow** (`_DECLARED`), with `connectivity`
  as its live caller; `staged_ahead` dropped. Regenerated the adoption matrix +
  Constitution ledger.
- Tests: `test_resilience_shadow.py` pins the binder — open/recovery transitions
  fire it (and non-transitions / duplicate-open do not), the local-first /
  offline-safe / cloud-as-disposable-offload policy it logs, and both kill-switches.

Ladder from here: shadow → parity (vs the breaker's own online/offline call) →
enforce (`RESILIENCE_ENFORCE`, fail-safe = current breaker), owner-gated. Six kernel
gates + audit + docs-sync + changelog-extract green. Version 8.218.0 → 8.219.0.

## [8.218.0] — Phase P (Proactive Household Intelligence) — parity rung

Second rung of Phase P: a log-only agreement check between the kernel `household`
model and the live heuristic (`PredictiveHabitMatrix`), still observe-only.

- **`proactive_audio.py`**: both models answer the same question each audit tick —
  *is proactivity warranted right now?* The predictor fires when it flags any due
  pre-emption; the household model fires when `anticipate()` would surface any
  suggestion. `_emit_household_shadow` now takes the predictor's due list and folds
  the two verdicts into a rolling agreement tally (`_household_parity_record`):
  `both_fire` / `both_quiet` (agreement) vs `hh_only` / `pred_only` (divergence),
  logged periodically with the agreement rate. `_run_predictor` now returns
  `(occupied, due)` so the parity check reuses the predictor's own verdict without
  recomputing it.
- **Why a boolean parity**: the two models have different native output shapes
  (the predictor ranks `{area}_entry` recurrences; the household model emits
  presence + routine suggestions), so the honest comparison is at the decision that
  the eventual enforce actually gates — *when does proactivity fire*. Keeping the
  `hh_only` / `pred_only` breakdown makes any divergence visible **before** the
  owner is asked to consider the enforce flip (household may surface more than the
  predictor — that broadening is exactly the owner's call, not an automatic one).
- **Still observe-only / fail-safe**: no actuator, no behaviour change — one
  enriched `household(shadow)` log line. Same `HOUSEHOLD_SHADOW` / `household_shadow`
  kill-switches; any failure swallowed, never raised into the tick.
- Adoption: `household` advances **shadow → parity** (`_DECLARED`). Regenerated the
  adoption matrix + Constitution ledger.
- Tests: `test_household_shadow.py` adds the parity cases (no tally without the
  predictor verdict, both-quiet / pred-only / both-fire agreement, and the pure
  `_household_parity_record` bookkeeping).

Ladder from here: parity → enforce (`HOUSEHOLD_PROACTIVE_ENFORCE`, fail-safe =
current heuristics) — **owner-gated, paused for review** before any flip. Six
kernel gates + audit + docs-sync + changelog-extract green. Version
8.217.0 → 8.218.0.

## [8.217.0] — Phase P (Proactive Household Intelligence) — shadow rung

Resumes the roadmap after the audit cleanup. First rung of Phase P: the pure
`kernel.household` model (occupancy rhythm + routine graph) gets its live binder,
observe-only.

- **`proactive_audio.py`**: the infrastructure-audit tick already samples current
  occupancy for the `PredictiveHabitMatrix`. `_emit_household_shadow` now folds the
  *same* occupancy sample into `kernel.household` — appending one `Observation`
  (daypart from `kernel.space_time`, weekday, occupied, and the primary occupied
  area as the routine-mining activity label) to a bounded rolling window, then
  logging what the model *would* anticipate (occupancy rhythm + the most-supported
  next routine) beside the live predictor. `_run_predictor` now returns the sampled
  occupancy so the shadow reuses it without re-reading presence.
- **Observe-only / fail-safe**: the household model's `anticipate()` emits advisory
  `Suggestion` objects that carry **no actuator**, so this drives nothing and
  changes no live behaviour — it only adds a `household(shadow)` log line.
  Kill-switched by the `HOUSEHOLD_SHADOW` module flag and the `household_shadow`
  config key (both default on); any failure is swallowed, never propagated into the
  audit tick. The rolling window is bounded (240 samples) and lives in runtime
  `entry_data` only (not persisted).
- **`kernel/household.py`**: docstring updated to name `proactive_audio` as the
  live shadow binder.
- Adoption: `household` advances **pure → shadow** (`_DECLARED`), with
  `proactive_audio` as its live caller; the `staged_ahead` marker is dropped now
  that it has one. Regenerated the adoption matrix + Constitution ledger via
  `kernel_docs_sync.py --write`.
- Tests: `test_household_shadow.py` pins the binder (observation shape, routine
  label, bounded window, last-activity tracking, both kill-switches, never-raises);
  the model's own derivations stay covered by `test_kernel_household.py`.

Ladder from here: shadow → parity (household suggestions vs. the predictor's) →
enforce (`HOUSEHOLD_PROACTIVE_ENFORCE`, fail-safe = current heuristics), owner-gated.
Six kernel gates + audit + docs-sync + changelog-extract green. Version
8.216.0 → 8.217.0.

## [8.216.0] — Audit follow-up: docs hygiene (stale progress table, staged-ahead marker, kill-switch prose)

Clears the audit's documentation findings (§3, §2a, §5) so the written record
matches the implementation. Docs + the adoption generator only — no behaviour
changes, no primitive stage changes.

- **`docs/KERNEL_PLAN.md` (§3)**: retired the stale, hand-kept Phase 0–7 Progress
  table that still marked phases 4–7 "🚧 In progress" long after they shipped and
  enforced. Replaced it with a pointer to **`KERNEL_ADOPTION.md`** as the generated,
  CI-guarded source of truth for every primitive's live stage, so no hand-kept
  stage table can drift again.
- **`scripts/kernel_adoption.py` (§2a)**: the eight pure primitives built *ahead*
  of the roadmap phase that will consume them (`priority`, `resilience`, `surfaces`,
  `inquiry`, `household`, `privacy`, `social`, `coordination`) now carry a
  `staged_ahead` flag in `_DECLARED`. `render_markdown` renders them as
  `— *(staged ahead)*` with a legend so the matrix isn't misread as adoption drift —
  zero live callers is the *intended* state for these, not a gap. `persistence`
  (an internal kernel seam, pure for a different reason) is deliberately not marked.
  The annotation lives at the generator, so `KERNEL_ADOPTION.md` regenerates cleanly
  and the `kernel_docs_sync` gate stays green.
- **`docs/JARVIS_CONSTITUTION.md` (§5)**: added one sentence to Invariant A2 making
  explicit that an `enforce` flag landing in code is a capability made *available*,
  not a behaviour switched *on* — it defaults to its fail-safe until the owner flips
  it, and the flip back is always one switch away. Prose only (the generated ledger
  block is untouched).

Regenerated via `kernel_docs_sync.py --write`. Six kernel gates + audit +
docs-sync + changelog-extract green. Version 8.215.0 → 8.216.0.

## [8.215.0] — Audit follow-up: the `security` certification signal closes only on a real decision

Acts on the audit's §B2 finding: `identity._emit_security_shadow` closed the loop on
*every* `resolve()` with signal (both confirm and deny), so the certification
dashboard's `security`-closed was near-constant and weak evidence.

- **`identity.py`**: closure is now reserved for a REAL identity decision. The loop
  closes only on (a) a confident CONFIRM backed by an identity-establishing method
  (face/voice — `_fabric_establishes_identity`), or (b) an ENFORCED deny (presence
  located a body but the fabric refused to treat it as identity). A plain
  low-confidence UNKNOWN, and a confident verdict resting only on presence-class
  votes, are now observed as **exercised-but-open** — a located body is not an
  established *who*. The helper takes `(*, closed, note)` so the call sites express
  the real outcome.
- Tests updated: confirm closes, enforced-deny closes, low-confidence is open.

Observe-only, kill-switched, behaviour-preserving (the `Identification` returned by
`resolve()` is unchanged; only the certification observation's `closed_loop` flag
changed). Six kernel gates + audit + certification suite green. Version
8.214.0 → 8.215.0.

## [8.214.0] — Audit follow-up: hermetic test config (fixes live-home contamination)

Acts on the audit's #1 recommendation (the one real bug): unit tests read the
host's live `/config/jarvis` instead of defaults, so in a dev container with the
owner's Home Assistant config they see enforce flags ON and `output_mutes.json`
`all:true` — eight "off by default" / shadow tests fail spuriously and the
in-container suite is permanently red, masking real regressions.

- **`paths.py`**: the sync-only config-dir fallback now honours a
  `JARVIS_CONFIG_DIR` env override (`DEFAULT_CONFIG_DIR`). Production is unchanged —
  the variable is unset there and HA's live `hass.config.path` always wins over the
  fallback anyway; the override only redirects the no-hass fallback.
- **`tests/conftest.py`**: points `JARVIS_CONFIG_DIR` at a fresh empty temp dir
  before any `jc.*` module loads, so unit tests that import `jarvis_config` /
  `output_gate` for real see DEFAULTS — reproducing clean-CI behaviour regardless
  of the host. Integration tests pass a real hass and are unaffected.
- Result: the 8 previously-contaminated tests
  (`test_actuation_learning_enforce`, `test_autonomy_enforce`,
  `test_energy_environment_enforce`, `test_identity_fabric_enforce`,
  `test_attention_shadow`, `test_delivery_triggers`) now pass in-container; the
  unit suite is hermetic.

No behaviour change. Six kernel gates + audit green. Version 8.213.0 → 8.214.0.

## [8.213.0] — Bug & gap audit (roadmap pause)

Docs-only release. With Phase AE complete (shadow → parity → enforce), the roadmap
is **paused** for a point-in-time **bug & gap audit** before any Phase Ω work or the
v9.0.0 major bump.

- **`docs/AUDIT_2026-10.md`** (new): an evidence-based review of the kernel/MCU
  migration at 8.212.0 against its own roadmap and Constitution — the adoption-ladder
  state (18 enforce / 6 parity / 14 shadow / 9 pure), roadmap-vs-reality gaps,
  correctness spot-checks, governance/invariant review, and the live-home unit-test
  contamination — with ranked recommendations.
- **Headline findings** (none block Phase Ω; all are honesty-of-signal / hygiene,
  not correctness): (1) eight `pure` kernel primitives are staged ahead of any
  consumer; (2) the Phase 0–7 Progress table in `KERNEL_PLAN.md` is ~200 releases
  stale vs. the live adoption matrix; (3) two AE scenario classes
  (`conflicting_priorities`, `cognitive_error`) are deliberately uncertifiable until
  honest surfaces exist, so "MCU System Done" is 8/10 observable; (4) the `security`
  certification signal closes trivially; (5) 8 unit tests fail in-container because
  they read the owner's live `/config/jarvis` enforce flags — a test-isolation bug.
- No behaviour change, no primitive stage change. Six kernel gates + audit green.
  Version 8.212.0 → 8.213.0.

## [8.212.0] — Phase AE (MCU Certification) — enforce: the certification CI gate (Phase AE COMPLETE)

Advances Phase AE **parity → enforce** — the final AE rung. Like Phase R, the
enforce teeth are a **CI gate**, not a runtime behaviour change.

- **`scripts/kernel_certification_check.py --check`** (new gate): fails the build if
  the certification suite is silently un-wired or faked. It asserts (1)
  `kernel/certification.py` still defines the ten `CLASSES`, `CertificationReport`,
  `CertificationLedger`, and the builders; (2) each of the **8 observable** scenario
  classes keeps its live observer — the owning module emits `CERT.<CLASS>` into the
  shared ledger (`proactive`→cognitive_core, `conversational`/`delegation`/
  `provider_failure`→agent, `failure`→actuation, `restart`/`long_horizon`→
  continuity, `security`→identity); (3) `cognitive_core` still owns the shared seam
  (`certification_observe` + `certification_report`) and surfaces the dashboard in
  `status()`; (4) `certification` is declared `enforce`; and — the HONESTY invariant
  — (5) the **2 not-yet-observable** classes (`conflicting_priorities`,
  `cognitive_error`) have NO emit anywhere, so a hollow closure can never be slipped
  in to fake certification. Wired into `.github/workflows/validate.yml` as the sixth
  kernel gate.
- Gate tests (`tests/unit/test_kernel_certification_check.py`): passes on the real
  tree, and catches a removed observer, a faked closure, and a downgraded stage.
- Adoption matrix now `certification ● enforce`; Constitution regenerated.

**Phase AE (MCU Certification) is complete** — shadow (8/10 classes observed from
live surfaces) → parity (the dashboard) → enforce (this gate). The suite behaves
observe-only and kill-switched; the CI gate is the enforce mechanism. The two
unobservable classes remain tracked as open work (they need a live safety-vs-
convenience arbitration and a belief-corrected-by-observation surface,
respectively). Six kernel gates + audit green. Version 8.211.0 → 8.212.0.

## [8.211.0] — Phase AE (MCU Certification) — parity: the certification dashboard

Advances Phase AE **shadow → parity**. The rolling certification tally is now
**surfaced read-only** as a dashboard, so the observable set's coverage and
closed-loop rate are visible rather than only logged.

- **`cognitive_core.certification_report()`** (new, read-only): returns the shared
  `CertificationLedger`'s rolling report — `coverage` (classes exercised / 10),
  `closed_loop_rate` (of those exercised, how many closed), the `closed` /
  `missing` class lists, `is_certified`, and the `observations` count. Returns a
  zero-observation report before anything is observed; never raises.
- **`cognitive_core.status()`** now carries a `certification` key with that
  dashboard, so diagnostics / the panel can read the quantified "does the whole
  JARVIS behave as one?" picture.
- Adoption matrix now `certification ◑ parity`; Constitution regenerated. New
  tests for the report accessor (empty shape, reflects observations) and the
  `status()` surfacing.

Still observe-only and kill-switched (`CERTIFICATION_SHADOW` / the
`certification_shadow` config key); nothing gates on the tally. With 8 of 10
classes observed from live surfaces, the dashboard reads `incomplete` until the
two deliberately-unobserved classes (`conflicting_priorities`, `cognitive_error`)
gain honest surfaces. Next and final AE rung: enforce — a certification CI gate
asserting the observable classes stay wired. Five kernel gates + audit green.
Version 8.210.0 → 8.211.0.

## [8.210.0] — Phase AE (MCU Certification) — shadow: security + long_horizon (8 of 10 observed)

Advances Phase AE shadow to **eight of the ten** scenario classes observed from
live surfaces, and settles the remaining two honestly.

- **`identity.resolve`**: observes the **`security`** scenario (ambiguous identity →
  deny / confirm). Both a confident confirm (a known person established) and a
  deliberate deny (low confidence, or presence-is-not-identity under the fabric
  enforce flip) close the loop — each is the identity decision resolving correctly.
  A no-signal / disabled resolve makes no decision and is not observed.
- **`continuity._long_horizon_parity`**: observes the **`long_horizon`** scenario
  (objective → days → restart → resume → completion). Observed only when goals
  actually spanned a restart (ledger ∩ live non-empty); closes iff at least one
  resumed with identical progress — the resume-after-restart proof.
- Adoption matrix now `certification ◐ shadow` with live callers `actuation`,
  `agent`, `cognitive_core`, `continuity`, `identity`; Constitution regenerated.

**The last two classes are deliberately left unobserved**, not by omission:
`conflicting_priorities` needs a live safety-vs-convenience arbitration
(`kernel.priority` is still pure — nothing arbitrates on it yet) and
`cognitive_error` needs a belief actually corrected by observation. Wiring either
today would be a hollow observation, so the parity and enforce rungs will certify
the **observable set (8/10)** and name these two as open work rather than fabricate
closures for them.

Behaviour unchanged — identity resolution and long-horizon parity run exactly as
before; this only watches them. Observe-only, kill-switched (`CERTIFICATION_SHADOW`
/ the `certification_shadow` config key), fail-safe. Next: the parity dashboard
(surface coverage % / closed-loop %), then the enforce gate. Five kernel gates +
audit green. Version 8.209.0 → 8.210.0.

## [8.209.0] — Phase AE (MCU Certification) — shadow: failure + restart

Advances Phase AE shadow by observing **two more** scenario classes from their own
honest live surfaces, folding into the same shared certification ledger. **Six of
the ten** classes are now observed from live traffic.

- **`actuation.execute_safety_actuator`**: observes the **`failure`** scenario
  (action → HA failure → recovery). Reaching the fail-open backstop means the
  universal seam already failed; the class closes iff the direct fail-open call
  then secures the home, and is exercised-but-open when even that fails. A clean
  seam success is *not* a failure scenario, so it is not observed.
- **`continuity.boot_reconcile`**: observes the **`restart`** scenario (mid-agency
  restart → reconciliation → resume). It closes iff reconciliation found live
  agency to resume (`still_live` non-empty), and is exercised-but-open when a
  snapshot existed but nothing was still live. A fresh boot with no snapshot is not
  observed.
- Adoption matrix now `certification ◐ shadow` with live callers `actuation`,
  `agent`, `cognitive_core`, `continuity`; Constitution regenerated. New tests for
  both emitters and a six-class shared-ledger test.

Behaviour unchanged — the safety fail-open path and boot reconciliation run exactly
as before; this only watches them. Observe-only, kill-switched (`CERTIFICATION_SHADOW`
/ the `certification_shadow` config key), fail-safe. Ladder from here: any remaining
cleanly-observable classes (security, long_horizon; conflicting_priorities /
cognitive_error if a non-hollow surface exists) → parity dashboard → enforce + a
certification CI gate. Five kernel gates + audit green. Version 8.208.0 → 8.209.0.

## [8.208.0] — Phase AE (MCU Certification) — shadow: delegation + provider_failure

Advances Phase AE shadow by observing **two more** scenario classes from their own
honest live surfaces, folding into the same shared certification ledger. Four of
the ten classes are now observed from live traffic.

- **`agent._run_delegated`**: observes the **`delegation`** scenario (JARVIS →
  child → result → synthesis). A sub-agent that returned a usable result for the
  parent to synthesise closes it; a sub-agent failure is exercised but open.
- **`agent.run_agent` fallback path**: observes the **`provider_failure`** scenario
  (cloud unavailable → local degradation). A primary-provider failure the fallback
  reasoning tier recovers from closes it; both providers failing is exercised but
  open.
- The agent-side emitters are refactored behind one `_emit_cert()` helper that
  routes every observation through `cognitive_core.certification_observe` (the one
  shared, kill-switched seam).
- Adoption matrix unchanged (`certification ◐ shadow`, callers `agent`,
  `cognitive_core`); the declared comment now lists all four observed classes. New
  tests for both emitters (closes on success/recovery, open on failure, kill-switch).

Behaviour unchanged — delegation and the provider fallback run exactly as before;
this only watches them. Observe-only, kill-switched (`CERTIFICATION_SHADOW` / the
`certification_shadow` config key), fail-safe. Ladder from here: the remaining
classes (failure, restart, security, long_horizon, conflicting_priorities,
cognitive_error) → parity dashboard → enforce + a certification CI gate. Five
kernel gates + audit green. Version 8.207.0 → 8.208.0.

## [8.207.0] — Phase AE (MCU Certification) — shadow: the conversational scenario + one shared ledger

Advances Phase AE shadow by adding the **second** scenario class and routing every
observer through **one shared certification ledger**, so the dashboard reflects the
whole system rather than a single loop.

- **`cognitive_core`**: the certification tally becomes a shared seam —
  `certification_ledger()` (the one lazily-created `CertificationLedger`) and
  `certification_observe(scenario, *, closed_loop, note)` (the single entry point
  any live surface calls; kill-switched, defensive, logs the rolling dashboard
  every 20 observations). The proactive emitter now routes through it.
- **`agent.run_agent`**: observes each turn as a **`conversational`** scenario
  (request → reasoning → tool action → verified reply). A turn that executed a tool
  action **and** produced a model-generated (non-empty) reply closes the class; a
  tool-less Q&A is exercised but open. Folds into the same shared ledger via
  `cognitive_core.certification_observe`.
- Adoption matrix now `certification ◐ shadow` with live callers `agent`,
  `cognitive_core`; Constitution regenerated. New tests for the shared observer,
  the conversational wiring, and that agent + core share one ledger.

Behaviour unchanged — both the proactive loop and the agent turn run exactly as
before; this only watches them. Observe-only, kill-switched (`CERTIFICATION_SHADOW`
/ the `certification_shadow` config key), fail-safe (any error swallowed). Ladder
from here: the remaining classes from their own live surfaces → parity dashboard →
enforce + a certification CI gate. Five kernel gates + audit green. Version
8.206.0 → 8.207.0.

## [8.206.0] — Phase AE (MCU Certification) — shadow: the first live scenario observation

Advances Phase AE **pure → shadow**. The certification suite stops being a static
record and starts **observing live cognitive passes** — the first real data for
the eventual certification dashboard.

- **`kernel/certification.py`**: new `CertificationLedger` (pure) — accumulates
  scenario-class observations into a rolling `CertificationReport`, **sticky-best
  per class** (a class that has ever closed the loop stays closed for the window,
  because certification asks whether each class has been *demonstrated to close at
  least once*, not whether its latest run closed). `observe()` / `report()` /
  `summary()` / `to_dict()` (carries the observation count) / `reset()`; ignores
  non-canonical names. Exported from `kernel/__init__.py`.
- **`cognitive_core`**: `_tick` now folds each pass into the ledger as a
  **`proactive`** scenario observation — the integration loop's
  perceive→predict→decide→act→learn **is** that scenario's shape (observation →
  prediction → suggestion → response), so a pass that predicted an anticipation
  exercises the class and one that ran end-to-end closes it. Logs
  `certification(shadow)` every 20 observations. Observe-only; nothing reads the
  tally. Kill-switched (`CERTIFICATION_SHADOW` / the `certification_shadow` config
  key); never raises into the tick.
- Adoption matrix now `certification ◐ shadow` (owner `cognitive_core`);
  Constitution regenerated. New focused tests for the ledger and the live emitter
  (gate, closed mapping, sticky-best, kill-switches, defensive).

Behaviour unchanged — the proactive loop runs exactly as before; this only
watches it. Ladder from here: shadow → parity (all ten classes observed from
their own live surfaces, a coverage/closed-loop dashboard) → enforce (every class
closes + a certification CI gate). Five kernel gates + audit green. Version
8.205.0 → 8.206.0.

## [8.205.0] — Phase AE (MCU Certification) — pure: the scenario-class suite as a kernel record

Opens **Phase AE**, the systems-certification phase — *"does the complete JARVIS
behave as one?"* and the MCU System Done bar. AE is not a single gate but a suite
of explicit **scenario classes**, each meant to run closed-loop and
journal-reconstructable. This first release lands the **pure record** of that
suite, additively; nothing live produces results yet.

- **`kernel/certification.py`** (new, pure — no HA import, no I/O, no clock):
  the ten canonical scenario classes (`conversational`, `proactive`,
  `long_horizon`, `delegation`, `failure`, `security`, `conflicting_priorities`,
  `provider_failure`, `cognitive_error`, `restart`); `ScenarioResult`
  (exercised? closed-loop? a note); and `CertificationReport` with pure
  derivations — `coverage` (classes exercised / total), `closed_loop_rate` (of
  those exercised, how many closed), `missing_classes`, and `is_certified` (True
  only when EVERY class is exercised AND closed). Builders `result()` / `report()`
  (canonical order, last-wins per class, drops non-canonical, defensive on junk)
  / `from_map()`.
- Exported from `kernel/__init__.py` (`CertificationReport`, `ScenarioResult`,
  `certification_report` / `certification_result` / `certification_from_map`,
  `CERTIFICATION_CLASSES`).
- Declared `certification ○ pure` in the adoption matrix; Constitution
  regenerated. Full unit coverage of the derivations and builders.

Ladder from here: pure → shadow (traces from live passes) → parity (a
certification dashboard: coverage %, closed-loop %, invariants green) → enforce
(every class closes + a certification CI gate). Behaviour unchanged. Five kernel
gates + audit green. Version 8.204.0 → 8.205.0.

## [8.204.0] — Phase R (Integration Gate) — enforce: a CI gate against open-loop regressions

Advances Phase R **parity → enforce**. R is an integration *gate*, not a
behaviour flip, so its enforce teeth are a **CI gate** — not a runtime change.

- **`scripts/kernel_integration_check.py --check`** (new gate): fails the build if
  the perceive→predict→decide→act→learn loop stops being wired — i.e. if
  `kernel/integration.py` loses `LoopTrace` / `LoopAccumulator` / `from_flags` /
  the five `STAGES`, if `cognitive_core._tick` stops assembling a `LoopTrace` from
  the five real stage signals (`perceived=`/`predicted=`/`decided=`/`acted=`/
  `learned=`), or if `integration` is declared below `enforce`. So the loop can
  never be silently un-wired while the rest of CI is green. Wired into
  `.github/workflows/validate.yml` alongside the other kernel gates.
- A proven closed-loop scenario test (a full proactive pass closes the loop and is
  journal-reconstructable from its correlation id), plus gate tests that confirm
  it catches a missing stage signal and a downgraded stage.

The loop's own behaviour is unchanged (still observe-only, kill-switched);
fail-safe is the current open-loop legacy. Adoption matrix now `integration ●
enforce`; Constitution regenerated. Five kernel gates + audit green. Version
8.203.0 → 8.204.0. Part of #236.

## [8.203.0] — Phase R (Integration Gate) — parity: the closed-loop rate, quantified

Advances Phase R **shadow → parity**. Beyond tracing each pass, cognitive_core
now folds every perceive→predict→decide→act→learn `LoopTrace` into a rolling
`kernel.integration.LoopAccumulator` and, every N passes, logs the closed-loop
**rate** plus where open passes stall (`integration(parity): closed X/Y (Z%);
reached [none:… perceive:… …]`). That's the quantified bar R needs over real
traffic before anything gates on closure (enforce).

- `kernel/integration.py` (pure): new `LoopAccumulator` — `record(trace)`, `rate`,
  `reached_histogram()` (canonical order, `none` first), `summary()`, `to_dict()`,
  `reset()`; ignores non-`LoopTrace` input.
- `cognitive_core`: accumulates per pass and emits the rolling dashboard every 20
  passes, alongside the existing per-pass line. Observe-only; nothing reads the
  rate yet. Still kill-switched (`INTEGRATION_LOOP_SHADOW` / `integration_loop_shadow`);
  never raises into the tick.

Adoption matrix now `integration ◑ parity`; Constitution regenerated. Four kernel
gates + audit green; `test_kernel_integration.py` extended. Version 8.202.0 →
8.203.0. Part of #236.

## [8.202.0] — chat: answer "what time do I usually get home?", and never return a blank turn

Two chat-quality fixes prompted by a report where JARVIS answered a routine
question with live presence ("Sam is currently home") and then returned empty.

- **New `usual_times` tool.** JARVIS already learns each person's daily
  leave/arrive routine (cognition presence patterns) but the chat brain had no
  way to read it, so it fell back to a live presence check. `cognition.presence_schedule`
  now surfaces the learned usual leave/arrive time per presence entity (person /
  device_tracker) — only when a consistent near-daily routine exists — and the new
  `usual_times` tool exposes it. System-prompt guidance routes "what time do I
  usually get home / when do I leave / what's my routine" there, with an explicit
  "I haven't learned that yet" answer when there isn't enough history (never
  substitutes live presence, never invents a time).
- **No more "(JARVIS returned no text.)".** `run_agent` could return an empty
  string when a model (notably a small local one) synthesised no text and no tool
  call; the panel then showed a blank turn. Both return paths now fall back to a
  graceful line ("I'm not sure I caught that, sir — could you put it another
  way?"), honouring the household honorific.

No actuation change; read-only tool. New `test_usual_times.py`. Four kernel gates
+ audit green. Version 8.201.0 → 8.202.0.

## [8.201.0] — Phase R (Integration Gate) — shadow: the closed cognitive/agency loop, traced

Opens the **R capstone** (the "can the architecture operate as one?" bar). R is
not a new capability — it proves a single real pass runs the whole way round
**perceive → predict → decide → act → learn**, tying cognition (J–M) and agency
(N–V) into one correlated, journal-reconstructable chain.

- `kernel/integration.py` (pure, new): a `LoopTrace` of the five canonical stages
  under one correlation id, each present/absent with a summary + owning primitive,
  and pure derivations — `is_closed` (all five ran), `reached`/`depth` (how far a
  partial pass got), `summary`/`to_dict`. Deterministic, no HA import.
- `cognitive_core._tick`: assembles a `LoopTrace` from each pass's **real**
  signals — world_model read (perceive), `cognition.predict*` (predict), decided
  actions (decide), dispatched actions (act), learned-model update / fresh
  autonomous outcome (learn) — and logs whether the loop CLOSED or how far it got
  (`integration(shadow): …`). Observe-only; kill-switched (`INTEGRATION_LOOP_SHADOW`
  + the `integration_loop_shadow` config key); never raises into the tick.

Most passes are *open* (perceive + decide, no act) — which is exactly what the
trace shows; R's job is to make closure observable before measuring it (parity)
and owning one end-to-end scenario (enforce + a CI gate against open-loop
regressions). Adoption matrix adds `integration ◐ shadow` (owner `cognitive_core`);
Constitution regenerated. Four kernel gates + audit green. Version 8.200.0 →
8.201.0. Part of #236.

## [8.200.0] — chat: recalled camera observations no longer read as live, and in the reply's language (#341)

Fixes two issues in the chat window reported in #341, where a German question
got an answer whose camera lines were in Russian and described scenes that were
not analysed live (no vision model ran).

**Why it happened.** Camera scene summaries from the periodic observer analysis
loop are stored (conversation memory, observer buffer, scene memory) and later
recalled into chat via the "What I've noticed lately" block (`awareness.reflect`).
The chat model composed its answer from that *recollection* — not a live frame —
so no vision/vLLM call occurred, and it recited the stored lines as if current.
The lines were in the household/analysis language while the reply followed the
message language, so a different-language question surfaced verbatim foreign text.

**Fixes:**
- `agent.py`: the recollection block is now explicitly labelled a PAST, non-live
  memory. The model is told never to present it as the current scene, to use the
  `look_at_camera` tool for "what's on camera now" questions, and to render a
  recalled line in the reply's language rather than quoting a stored foreign-
  language line verbatim.
- `camera.py`: the camera *reasoning summary* (what gets stored and later
  recalled) is now pinned to the household language, like the vision analysis
  already was (#140/#307) — so stored observations stop drifting into another
  language. JSON keys/enums stay English so parsing is unaffected.

No actuation or vision-pipeline change; behaviour-preserving for English
installs (the language clause is empty there). Version 8.199.0 → 8.200.0.
Fixes #341.

## [8.199.0] — kernel.long_horizon → parity: durable goal ledger + resume-after-restart proof (Phase V)

Advances `long_horizon` **shadow → parity** with a real durable build. `continuity`
persists the live goals — modeled as `kernel.long_horizon` goals, carrying the
milestone progress the `agency_state` commitment deliberately does **not** — to a
durable ledger (`long_horizon_ledger.json`) on each capture, and on boot RESUMES
them from the ledger and compares their progress against the goals re-derived from
the live store.

- `continuity.py`: `_build_long_horizon_goals` (the single builder the shadow
  roll-up and the ledger read share), `_long_horizon_persist` (atomic temp+replace,
  capped, best-effort), `_long_horizon_load` (round-trips via
  `long_horizon.plan_goal`), `_long_horizon_parity` (logs how many goals resumed
  with identical progress + new/dropped counts). `capture_now` runs parity against
  the previous ledger then rewrites it; `boot_summary` runs parity against the
  pre-restart ledger. Kill-switch `LONG_HORIZON_PARITY`.

Not hollow: the ledger is an independent durable path written in a prior process,
compared against `goals.py`'s own persistence after a restart. **Observe-only** —
nothing resumes FROM the ledger yet (that is the owner-gated `LONG_HORIZON_ENFORCE`
rung, fail-safe = session-scoped goals); the parity read never raises into boot.
Adoption matrix + Constitution regenerated (`long_horizon ◑ parity`). Tests:
`test_long_horizon_parity.py`. Four kernel gates + audit green. Version 8.198.0 →
8.199.0. Part of #236.

## [8.198.0] — Phase AC: contextual autonomy — hold convenience auto-actions while a guest is present (default OFF)

Advanced Autonomy (dynamic / contextual), layered **on top of** the earned
per-capability level (Phase N) and its hard ceiling: a capability's autonomy at any
instant becomes `min(earned_level, contextual_cap)` — the N gate runs first and is
never exceeded, so contextual autonomy can tighten JARVIS's hand but never free it.

- `kernel/autonomy.py` (pure): `AutonomyContext` (guests / asleep / low-confidence)
  + `contextual_cap(level, context)` (DOWNWARD-ONLY clamp — always `min(level, cap)`
  on the ladder) + `context_blocks_autonomy()` (boolean adapter; None/malformed →
  never blocks).
- `cognitive_core.py`: behind `CONTEXTUAL_AUTONOMY_ENFORCE` / the owner-gated
  `contextual_autonomy_enforce` key (default OFF), `AutonomyManager.is_autonomous`
  consults the context atop the N ceiling, so a granted convenience pattern is held
  to ask-first while a guest is in the home. Context built once per proactive tick
  from `_guests_present()` (a recently-recognised non-resident face); only when
  enforce is on; no context passed = no-op.

Proactive offers are already suppressed while asleep upstream, so the live wiring
uses the **guests** signal (non-hollow); the asleep / low-confidence dimensions
ship in the pure primitive for a future clean signal. **Safe-directional** (can
only withhold, never grant), never loosens the N gate, default OFF /
behaviour-preserving, kill-switched, fail-safe to acting as today. Safety events
never route through this gate. Tests: `test_kernel_autonomy_contextual.py`,
`test_contextual_autonomy_enforce.py`. Four kernel gates + audit green. Version
8.197.0 → 8.198.0. Part of #236.

## [8.197.0] — kernel.space_time → enforce: intrusion breach-depth owned by the kernel SpatialGraph (Phase Q, default OFF)

Completes the Phase Q **spatial** ladder (pure → shadow → parity → enforce). The
intrusion investigation's breach-depth map (which tells inward motion from motion
lingering at the point of entry) can be owned authoritatively by the kernel
`SpatialGraph` instead of `residence_graph`.

- `cognitive_core.py`: behind `SPACE_TIME_ENFORCE` / the owner-gated
  `space_time_enforce` key (default OFF), `_space_time_breach_hops` adopts the
  kernel-derived depth map — but **only** when the kernel reproduces the incumbent
  `residence_graph.hops_from_breach` map exactly at room-slug level (the parity
  agreement test), keyed back to area-ids via the legacy map.

Safety path, safe by construction: exact-agreement adoption makes the kernel the
authoritative producer **without changing the safety behaviour**; any divergence /
empty graph / unmappable key / error keeps the incumbent map (fail-safe). Default
OFF, kill-switched. (The non-safety temporal/daypart path was considered first per
the roadmap caution, but the kernel's 6-bucket dayparts deliberately differ from the
live 4-bucket greeting buckets, so it would not be behaviour-preserving.) Adoption
matrix + Constitution regenerated (`space_time ● enforce`). Tests:
`test_space_time_enforce.py`. Four kernel gates + audit green. Version 8.196.0 →
8.197.0. Part of #236.

## [8.196.0] — kernel.working_memory → enforce: attention reads the canonical working set (Phase K, default OFF)

Completes the Phase K ladder (pure → shadow → parity → enforce). The output gate's
announce arbitration reads the one shared kernel `WorkingMemory`
(`working_memory.shared()`) **authoritatively**.

- `output_gate.py`: behind `WORKING_MEMORY_ENFORCE` / the owner-gated
  `working_memory_enforce` key (default OFF), the gate consults the canonical
  cognitive context and may **withhold** a non-critical announcement the legacy
  gate allowed, when the working set's knowledge (household asleep, from the
  situation item) is exactly what tips the kernel attention arbitration from ALLOW
  to DEFER/SUPPRESS. A shared `_working_memory_signal()` is the single source the
  parity log and the enforce gate both read; the verdict is the pure, unit-tested
  `_working_memory_gates_out()`.

**Tighten-only** (can only withhold, never surface a withheld announcement);
**critical urgency always bypasses**; **fail-safe** = the legacy decision (current
attention inputs) on any error or an unpopulated working set; behaviour-preserving
while OFF; kill-switched. Non-safety (announce surface only). Adoption matrix +
Constitution regenerated (`working_memory ● enforce`). Tests:
`test_working_memory_enforce.py`. Four kernel gates + audit green. Version 8.195.0 →
8.196.0. Part of #236.

## [8.195.0] — kernel.causal → enforce: proactive suggestions gated on prediction (Phase L, default OFF)

Advances the `causal` primitive **parity → enforce**. When the flip is on, a
detected **sequence** pattern is surfaced as a proactive suggestion only if the
kernel causal model confirms it a *genuine cause* — the real cause/effect
contingency (ΔP → direction "causes"), not mere co-occurrence. Proactivity is now
gated on prediction confidence, not just how often two things were seen together.

- `pattern_analyzer.py`: `_emit_causal_parity` tags each re-scored sequence
  pattern with `details["causal_confirms"]` (runs when parity **or** enforce is
  on); the suggestion-storing loop calls the pure `_causal_gates_out(pattern)` to
  withhold a sequence suggestion the causal contrast explicitly refuted. New
  `CAUSAL_PREDICT_ENFORCE` + `_causal_predict_enforce_on()` (module flag or the
  `causal_predict_enforce` config key).

**Kill-switched, default OFF** → behaviour-identical as shipped. **Fail-safe =
current behaviour**: an untagged pattern (verdict missing / any error) counts as
confirmed, so a failure can only fall back to suggesting as today — it can never
silently drop a suggestion it didn't evaluate. **Non-safety**: gates only the
learned-suggestion surface, never a safety actuation.

Adoption matrix + Constitution regenerated (`causal ● enforce`). Tests:
`test_causal_enforce.py` (default-off / flag / config; off=no-gating;
refuted-sequence gated; confirmed not gated; untagged fail-safe; non-sequence
never gated; defensive). Audit + four kernel gates green. Version 8.194.0 →
8.195.0. Part of #236.

## [8.194.0] — kernel.optimize → shadow: tuning proposals from the interruption budget (Phase Y)

Advances the `optimize` primitive **pure → shadow** with its first live producer.
`decision_record.interruption_budget` now maps its live over-interruption
assessment into a `kernel.optimize` tuning **proposal** for the SENSITIVE
`interrupt_threshold` and logs it (`optimize(shadow): …`) — the more JARVIS has
been interrupting without payoff (multiplier below 1.0), the more it would propose
*raising* the threshold to interrupt less.

- `decision_record.py`: pure `_optimize_proposal_from_budget(budget)` +
  `_emit_optimize_shadow(budget)` (called at the end of `interruption_budget`).
  Kill-switch `OPTIMIZE_SHADOW`.

Observe-only and, crucially, **proposal_only** — because `interrupt_threshold` is
SENSITIVE, the proposal is owner-gated and **never auto-applied**, demonstrating
the tiered guardrail (SAFE self-tunes, SENSITIVE proposes, FORBIDDEN is immutable;
the enforce rung owns self-application). Adoption matrix now `optimize ◐ shadow`
(owner `decision_record`); Constitution regenerated. Tests:
`test_optimize_shadow.py` (no-data; over-interrupting raises + proposal_only, never
auto; healthy = no change; within-bounds clamp; defensive emit). Four kernel gates
+ audit green. Version 8.193.0 → 8.194.0. Part of #236.

## [8.193.0] — kernel.coordination (pure): budgeted peer coordination (Phase O)

Lands the peer-coordination half of Phase O (merged O+AB) **pure** — hierarchical
delegation already lives in `kernel.agency` at enforce. `kernel/coordination.py`
is a budgeted bid/claim/settle protocol:

- `arbitrate(bids, budget=…)` awards an objective to **exactly one** agency (no
  double-actuation); bids whose cost would overrun the budget are rejected; and
  **conflicts resolve by the `kernel.priority` ladder** (a safety-tier bid
  outranks a convenience one), tie-broken by lower cost then higher confidence —
  never by bid order.
- `settle(claim, success=…)` closes an awarded claim (SETTLED/FAILED); an
  unawarded claim can't be settled. `would_double_actuate(claims)` guards the
  one-award invariant.

Pure: no HA import, no I/O, no clock, no bus; nothing live consumes it yet (the
event-bus loop wires on at the shadow rung). Declared `coordination · pure`;
Constitution regenerated. Tests: `test_kernel_coordination.py` (single award;
budget rejection; priority-not-order conflict; tie-breaks; settle success/failure;
unawarded no-op; double-actuation guard). Audit + four kernel gates green. Version
8.192.0 → 8.193.0. Part of #236.

## [8.192.0] — kernel.social (pure): consent-bounded personalization (Phase W)

Lands the Phase W primitive **pure**, built directly on the `kernel.privacy`
boundary from 8.191.0. `kernel/social.py` holds per-person `Preference`s and
personalizes **within strict consent limits**:

- `personalize_for(model, key, audience=…, default=…)` routes a cross-person read
  through `privacy.can_disclose`, so person A's PERSONAL preference is returned
  only to A (or a consented audience) — serving person B falls back to the
  non-personalized `default`. A HOUSEHOLD-classified preference is shared.
- `preference` (self-read), `remember` (newest-wins, ignores foreign subjects),
  `purge` (owner drops a person's whole model), `summarize` (owner inspection).

Structurally it **holds no actuator and exposes only preference values**, so it
can never drive a security/intrusion decision; consent-gated, owner-inspectable,
purgeable. Pure: no HA import, no I/O, no clock, no storage; nothing live consumes
it yet. Declared `social · pure`; Constitution regenerated. Tests:
`test_kernel_social.py` (newest-wins; foreign-subject ignored; self-read;
cross-person block without consent; consented audience; household shared; purge;
summary). Audit + four kernel gates green. Version 8.191.0 → 8.192.0. Part of #236.

## [8.191.0] — kernel.privacy (pure): information-flow boundary (Phase W prerequisite)

Lands the cross-cutting **information-flow / privacy boundary** the audit requires
*before* Phase W (Social & Relationship Intelligence). `kernel/privacy.py` is a
pure, **fail-closed** decision over a labelled `DataItem` (classification / subject
/ purpose / consent / audience / source):

- `can_disclose(item, audience=…, purpose=…, owner=…)` — PUBLIC/HOUSEHOLD flow
  freely; the subject always sees their own; the owner may see PERSONAL; **any
  other person needs the subject's explicit consent**, and SENSITIVE additionally
  needs a matching purpose. An unknown classification is coerced to SENSITIVE and
  denied.
- `cross_subject_leak(...)` flags the exact boundary W must not cross — person A's
  PERSONAL/SENSITIVE data flowing to a different person B without consent;
  `redact(...)` returns only the disclosable subset.

Pure: no HA import, no I/O, no clock, no storage; nothing live consumes it yet (the
social model + any cross-person surfacing consult it at the shadow rung). Declared
`privacy · pure`; Constitution regenerated. Tests: `test_kernel_privacy.py`
(fail-closed unknown; public/household; subject-sees-own; personal cross-person
block + owner/consent; sensitive consent+purpose; cross-subject-leak; redact;
defensive). Audit + four kernel gates green. Version 8.190.0 → 8.191.0. Part of #236.

## [8.190.0] — kernel.household (pure): proactive household intelligence (Phase P)

Lands the Phase P primitive **pure**. `kernel/household.py` derives an occupancy
**rhythm** (per-daypart occupancy likelihood) and a **routine graph** (recurring
activity transitions) from plain observation rows. The invariant is structural:
`anticipate()` emits advisory `Suggestion` objects that **carry no actuator** — the
household model may only *propose*; acting on a suggestion stays the
authority/actuation seam's job (suggest, never silent actuation).

- `occupancy_rhythm` / `routines(min_support=…)` / `anticipate(daypart=…,
  last_activity=…)` / `summarize` — all total, deterministic.

Pure: no HA import, no I/O, no clock; nothing live consumes it yet (an observation
feed + the proactive loop wire on at the shadow rung). Declared `household · pure`;
Constitution regenerated. Tests: `test_kernel_household.py` (rhythm; routine
support threshold; presence + routine suggestions; suggestions always advisory /
never actuators; empty; summary). Audit + four kernel gates green. Version
8.189.0 → 8.190.0. Part of #236.

## [8.189.0] — kernel.inquiry (pure): investigative agency (Phase AD)

Lands the Phase AD primitive **pure**. `kernel/inquiry.py` is bounded, cited
inquiry with **evidence provenance, not merely citations**:

- A `Finding` is a **typed** claim (fact / observation / inference / prediction /
  hypothesis / recommendation) with source, confidence and an injected
  valid-as-of timestamp.
- `record()` enforces a **hard spend cap** — a finding whose cost would overrun
  the budget is refused (zero-cost always allowed), so a bounded investigation
  can never silently overspend.
- `synthesize()` prefers the best-*grounded*, highest-confidence claim and
  **caps confidence when a contradiction is present** (never false certainty),
  surfacing contradictions rather than hiding them; `contradictions()` detects
  opposing claims.

Pure: no HA import, no I/O, no clock, no network; nothing live consumes it yet
(the tool-using gather loop wires on at the shadow rung). Declared `inquiry ·
pure`; Constitution regenerated. Tests: `test_kernel_inquiry.py` (type coercion;
hard cap; zero-vs-positive cost at zero budget; best-grounded synthesis;
contradiction confidence cap; contradiction detection; budget accounting). Audit
+ four kernel gates green. Version 8.188.0 → 8.189.0. Part of #236.

## [8.188.0] — kernel.surfaces (pure): presence continuity (Phase AA)

Lands the Phase AA primitive **pure**. `kernel/surfaces.py` is a surface registry
+ a single cross-surface arbiter, built around **presence continuity, not a UI
layer**:

- `arbitrate(surfaces)` picks **exactly one** surface to emit on — a focused
  surface wins over mere presence, else the lowest effective priority — so a
  notification is announced **once**, never N times across N surfaces. A muted or
  absent surface never wins; `None` means stay silent.
- Mutes are honored everywhere (`muted_surfaces`), and `would_double_announce`
  shows when arbitration is doing real work.
- An `Interaction` survives a `handoff` between surfaces (voice → mobile → HUD)
  **keeping its id** — `is_same_interaction` is the continuity test; a same-surface
  handoff is a no-op.

Pure: no Home Assistant import, no I/O, no clock; nothing live consumes it yet
(the registry + announcement path wire on at the shadow rung). Declared
`surfaces · pure`; Constitution regenerated. Tests: `test_kernel_surfaces.py`
(focused/priority/override arbitration; mute+absent exclusion; single-emit;
handoff identity continuity; same-surface no-op; summary). Audit + four kernel
gates green. Version 8.187.0 → 8.188.0. Part of #236.

## [8.187.0] — kernel.resilience (pure): resilient compute federation (Phase Z)

Lands the Phase Z primitive **pure**. `kernel/resilience.py` is a local-first,
offline-safe fallback policy over compute tiers (LOCAL / EDGE / CLOUD × health).
The load-bearing invariant is the **HAOS boundary**, encoded structurally — this
is explicitly *not* "distributed JARVIS":

- `can_offload(tier)` is **False for any canonical-state tier** (and for LOCAL),
  so the canonical brain can never be offloaded off the HA integration; only
  disposable EDGE/CLOUD tiers are offload targets.
- `choose(tiers, need_quality=…)` is **local-first** and **fail-safe** — if no
  tier is usable it falls back to the canonical/local tier (the brain keeps
  running at home) rather than raising or returning None.
- `is_offline_safe`, `degrade_order`, `offload_candidates`, `summarize` round it
  out; every function is total and deterministic.

Pure: no Home Assistant import, no I/O, no clock — tiers + health are injected;
nothing live consumes it yet (a health poller + the existing conservative breaker
wire onto it at the shadow rung). Declared `resilience · pure` in the adoption
matrix; Constitution regenerated. Tests: `test_kernel_resilience.py` (coercion;
local-first choice; down-skip; quality floor; fail-safe-to-home; canonical never
offloadable; offline-safety; degrade order; summary). Audit + four kernel gates
green. Version 8.186.0 → 8.187.0. Part of #236.

## [8.186.0] — kernel.self_model → parity: self-report vs ground truth (Phase S)

Advances the `self_model` primitive **shadow → parity**. Alongside the shadow
projection on the cognitive-status read, `agent._emit_self_model_parity` now
compares the self-model's reported capability availability against an
**independent ground truth** — a capability is really available only when its
enforcement switch is live-enabled **and** its backing module actually resolves —
and logs agreement/divergence (`self(parity): n=… agree=… report_only=…
truth_only=…`). `report_only` isolates exactly what the enforce rung must forbid:
the model reporting a capability *available* that JARVIS cannot actually perform
(confabulation).

- `agent.py`: pure `_self_model_parity(capabilities, ground_truth)` comparator +
  `_emit_self_model_parity(sm)` (builds ground truth from the enforcement
  registry), called from `_exec_cognitive_status` after the shadow log.
  Kill-switch `SELF_MODEL_PARITY`.

Observe-only — no decision consumes it; the hard rule holds structurally (the
model only *describes*, it grants nothing). Adoption matrix + Constitution
regenerated (`self_model ◑ parity`). Tests: `test_self_model_parity.py` (agree /
confabulation / under-report / unknown-key skip / defensive-empty). Four kernel
gates + audit green. Version 8.185.0 → 8.186.0. Part of #236.

## [8.185.0] — fix: Faces panel resets to 0 on every restart (#331)

The Household Faces tab emptied on every Home Assistant / JARVIS restart — the
faces JARVIS had recognized "disappeared and set to 0" until new sightings
arrived. The recognition cache and the pinned recognition-time snapshot index
were in-memory module globals, so a restart wiped them; households on
MQTT/DoubleTake (no live Frigate `last_recognized_face` sensor to repopulate
from) saw a permanently empty tab after every restart.

- `recognition.py`: persist the trusted recognition cache + the snapshot index to
  `<config>/jarvis/recognition_cache.json` (atomic, throttled) on each
  `remember_recognition` / `capture_face_snapshot`, and restore them on startup —
  **age-filtered to `CACHE_MAX_AGE` (2h)** so nothing stale is resurrected and a
  pinned frame is only restored if its file still exists.
- `__init__.py`: a `recognition.warm_start()` executor warm-load at setup, plus a
  lazy guarded restore inside `recent_faces` as a fallback.

**Safety preserved.** `resident_present()` — which stands intrusion monitoring
down when a recognized resident is on camera within 180s — now **ignores
restored entries** (tagged `restored`): only a *fresh* recognition made in the
running process can stand monitoring down, so a pre-restart sighting can never
disable an alert. A real sighting overwrites the restored entry, resuming normal
behaviour immediately. Best-effort throughout: every path never raises.

Tests: `test_recognition_persistence.py` (save → restore round-trips the cache
and snapshot index; stale entries age out; a missing pinned frame is skipped;
restored recognitions never satisfy `resident_present` while a fresh one does).
Audit + four kernel gates green. Version 8.184.0 → 8.185.0.

## [8.184.0] — kernel.cycle → enforce: the proactive dispatch loop IS the CognitiveCycle (Phase J / J4, default OFF)

Advances the `cycle` primitive **parity → enforce** — the J4 full refactor.
`cognitive_core._tick`'s proactive-action dispatch loop no longer just runs a
cycle *alongside* itself (J2/J3 shadow+parity): when the flip is on, the loop
genuinely **becomes** the kernel `CognitiveCycle`. The canonical
PERCEIVE → INTERPRET → DECIDE → ACT → REFLECT pass runs, and its **DECIDE phase
owns the ordered dispatch plan** that `_tick` then performs — one (non-safety)
subsystem's loop is now literally the cycle, not a parallel observer.

- `cognitive_core.py`: the dispatch loop (`for action in actions: await
  _emit_action(...)`) now iterates a `plan` produced by the new
  `_cognitive_cycle_plan(actions, *, people, anyone_home, sleeping)` — which runs
  `kernel.cycle.standard_cycle({...}).tick()` and returns the DECIDE-owned plan.
  New `COGNITIVE_CYCLE_ENFORCE` constant + `_cognitive_cycle_enforce_on()` gate
  (module flag OR the `cognitive_cycle_enforce` config key).

**Kill-switched, default OFF** (`COGNITIVE_CYCLE_ENFORCE` / `cognitive_cycle_enforce`)
→ shipping is **behaviour-preserving**: with the flip off the plan is exactly
`actions`, in the same order, and the loop dispatches it once — byte-for-byte the
old path. **Fail-safe**: any cycle failure (`trace.ok` false, or the import/call
raises) is caught and the loop falls back to the legacy `actions` order; the plan
is chosen once and dispatched exactly once, so there is **no double-fire**.
**Safety never routes through this loop** — intrusion/freeze/lockdown actuate on
their own enforced paths, not the proactive dispatch.

Activation is owner-gated on the **J3 parity log**: the household watches the
real-traffic DECIDE-vs-ACT agreement flag (unchanged, still emitted) and flips
`cognitive_cycle_enforce` only once it reads clean. Pre-authorized by the owner
("j4: full refactor").

Adoption matrix + Constitution regenerated (`cycle ● enforce`). Tests:
`test_cognitive_cycle_enforce.py` (kill-switch default-off / flag / config /
never-raises; plan preserves order + contents; empty actions; input not mutated;
raises on cycle failure so `_tick` falls back). Audit + four kernel gates green.
Version 8.183.0 → 8.184.0. Part of #236 / #239.

## [8.183.0] — kernel.autonomy → enforce: opt-in kernel-governed autonomy (Phase N, default OFF)

Advances the `autonomy` primitive **parity → enforce**. When the household
explicitly opts in — via a **full-explanation confirmation screen** in the panel
— JARVIS's graduated-autonomy auto-execute gate is governed by the kernel
autonomy ladder's hard ceiling: a granted proactive pattern whose capability is
**SECURITY-class (unlock, disarm, garage/lockdown off) NEVER auto-acts** — it
always falls back to asking, no matter how many times it was approved.

- `cognitive_core.py`: `AutonomyManager.is_autonomous` consults
  `kernel.autonomy` (a SECURITY capability is pinned at CONFIRM — `may_act` False
  regardless of record); new `_graduated_autonomy_enforce_on()` +
  `_pattern_is_security()` helpers.
- `jarvis-panel.js`: a new **"Act without asking (kernel-governed)"** toggle in
  Settings → Sub-Agents & Automation, carrying a `data-confirm` that spells out
  exactly what's being enabled (which automations auto-run, that security/safety
  never auto-act, how to turn it off) — the write only persists after the user
  confirms, reusing the existing confirm flow.
- `websocket.py`: `autonomy_enforce` registered writable + echoed in panel data.

**Kill-switched, default OFF** (`GRADUATED_AUTONOMY_ENFORCE` / `autonomy_enforce`)
→ shipping is behaviour-preserving. **Fail-safe**: any error leaves the legacy
gate (grant + mode flag) untouched. Safety (intrusion/freeze/lockdown) never
routes through this gate. The security ceiling is forward-looking — today's
proactive patterns are all SENSITIVE (lights/hvac), so nothing changes until the
household opts in.

Adoption matrix + Constitution regenerated (`autonomy ● enforce`). Tests:
`test_autonomy_enforce.py` (ceiling helper; default-off; security blocked only
under opt-in; SENSITIVE unaffected; ungranted never autonomous) +
`smoke_panel.js` pins the confirmation toggle. Audit + four kernel gates green.
Version 8.182.0 → 8.183.0. Part of #236.

## [8.182.0] — kernel.learning → enforce: closed-loop trust (Phase M, owner-gated, default OFF)

Advances the `learning` primitive **parity → enforce** — closes the learning
loop. The per-capability trust the shadow/parity rungs only *logged* is now
**persisted and fed back as the prior** for the next adjustment, so trust
accumulates across restarts instead of resetting to the neutral 0.5 each tick.

- `actuation.py`: new persistent per-capability trust store
  (`<config>/jarvis/capability_trust.json`, house JSON-snapshot pattern —
  loaded once, saved throttled), `learned_trust(cap)` accessor (the consumable
  for graduated autonomy), and `_emit_learning_enforce(oc, hass)` wired into the
  verified-outcome path.
- **Kill-switched, default OFF** (`LEARNING_ENFORCE` / the `learning_enforce`
  config key) → shipping is behaviour-preserving (prior stays 0.5, the store is
  never written). **Fail-safe**: any error — including a hass without a usable
  config path — falls back to the neutral prior and persists nothing. The
  governance clamp (protected weights only tighten, never relax) is unchanged.

Adoption matrix + Constitution regenerated (`learning ● enforce`). Tests:
`test_actuation_learning_enforce.py` (gate + accessor; default-off persists
nothing; on → persists and reloads across a restart; stored prior fed back;
fail-safe on a path-less hass). Audit + four kernel gates green.
Version 8.181.0 → 8.182.0. Part of #236.

## [8.181.0] — kernel.identity_fabric → enforce (Phase I½, owner-gated, default OFF)

Advances the `identity_fabric` primitive **parity → enforce** (#237). When
enabled, `identity.resolve()` applies the fabric's **identity ≠ presence** rule to
a live path: a confident verdict resting ONLY on presence-class signals
(sole-occupant / home-prior / room / proximity) is downgraded to `UNKNOWN`, with
method `presence_not_identity`. Presence locates a body; only a face or voiceprint
establishes *who*.

- `identity.py`: new `_identity_fabric_enforce_on()` + `_fabric_establishes_identity(methods)`, consulted in `resolve()` after the legacy confidence gate.
- **Kill-switched, default OFF** (`IDENTITY_FABRIC_ENFORCE` / the
  `identity_fabric_enforce` config key) → shipping is behaviour-preserving (the
  parity log is expected to show frequent presence→identity divergence in a
  single-occupant home, which is exactly why activation is the household's call,
  after watching that log). **Fail-safe**: any error leaves the legacy verdict
  untouched — enforce never downgrades a verdict it couldn't classify.

Adoption matrix + Constitution regenerated (`identity_fabric ● enforce`). Tests:
`test_identity_fabric_enforce.py` (helper rule; default-off keeps presence known;
enforce downgrades presence-only; a face verdict is never downgraded). Audit +
four kernel gates green. Version 8.180.0 → 8.181.0. Part of #236 / #237.

## [8.180.0] — kernel.environment → enforce (Phase X, owner-gated, default OFF)

Advances the `environment` primitive **parity → enforce**, the first of the
owner-authorized discretionary enforce flips. When enabled, the kernel
environment recommender's actionable efficiency verdict becomes the authority
for the "home is over peak" determination that gates a proactive energy offer —
the exact step the `environment(parity)` log has been measuring.

- `energy.py`: new `_environment_over_peak(st)` (+ `_environment_enforce_on`) and
  `evaluate_for_proactive` consults it instead of the raw `over_peak` flag.
- **Kill-switched, default OFF** (`energy.ENVIRONMENT_ENFORCE` / the
  `environment_enforce` config key), so shipping is byte-for-byte
  behaviour-preserving; the household flips it after watching the parity
  agreement log. **Fail-safe**: any error falls back to the legacy `over_peak`
  threshold, and the ≥2-sheddable guard is unchanged, so an over-peak verdict
  with nothing to stagger still surfaces nothing. Comfort/efficiency can never
  override a live safety concern (the kernel priority guard is unchanged).

Adoption matrix + Constitution regenerated (`environment ● enforce`). Tests:
`test_energy_environment_enforce.py` (default-off echoes legacy; kernel verdict
wins when on; module-flag + config-key toggles; fail-safe on bad meter). Audit +
four kernel gates green. Version 8.179.0 → 8.180.0. Part of #236.

## [8.179.0] — interior windows + balcony/French doors (issue #324, part 2)

Second part of #324, and the one the reporter asked for most: openings were
bound to the four outer walls, so you couldn't glaze a wall between two rooms or
the wall facing a courtyard/balcony between two building masses. Now you can:

- **Interior window** (`window` / `interior`) — glazing on a chosen **room's**
  wall, for a window between rooms or facing an interior courtyard.
- **Balcony / French door** (`door` / `french`) — a full-height glazed door on a
  chosen room's wall; lit when its open/closed sensor says open.

Both reuse the existing room-anchored placement the interior doors and cased
openings already use (pick the room, then the wall + position), so they're no
longer tied to the building's outer bounding box. New palette buttons
(**+ Interior Window**, **+ Balcony Door**), per-element room pickers, 2D-editor
markers, and 3D rendering on the selected floor.

Purely additive — no existing opening changes, and a home with none of these
renders exactly as before. Frontend-only. Tests: `scripts/smoke_panel.js` gains
four part-2 assertions (palette offers both; interior-window and balcony-door
rows attach to a room; the openings editor renders). Audit + four kernel gates
green. Version 8.178.0 → 8.179.0. Completes #324.

## [8.178.0] — European house options: center/multiple chimneys + interior cellar (issue #324, part 1)

First part of #324 — the 3D house map assumed an American layout. This adds the
two structural knobs a European/German house needs most:

- **Chimney: center + multiple.** `chimney_side` gains a **Center / ridge** option
  (a free-standing stack rising through the roof ridge, via the new
  `chimneyStack` primitive, vs the existing edge stack), and a new **Chimneys**
  count (1–4) places several spaced along the ridge — the common German case of a
  central chimney, often two or more.
- **Interior cellar entrance.** New **Cellar entrance** selector
  (`basement_entrance`): *Exterior bulkhead* (current default) or *Interior stairs
  (inside)*. Interior suppresses the auto exterior bulkhead, which is plain wrong
  for the European norm (dig the hole, build on it, reach the cellar from inside).

Defaults are unchanged (`chimney_side` right, count 1, `basement_entrance`
bulkhead), so existing homes render exactly as before. Frontend-only; no backend
or kernel change. Tests: `scripts/smoke_panel.js` gains four #324 assertions
(spec carries the new fields, the settings UI exposes the controls, a center
chimney renders without error). Audit + four kernel gates green.
Version 8.177.0 → 8.178.0.

Still to come for #324: interior windows and window-doors (balcony / French doors)
that aren't bound to the four outer walls — a deeper editor/renderer change,
coming as its own release.

## [8.177.0] — type-to-JARVIS chat tab in the panel (issue #322)

Adds a **Chat** tab to the JARVIS dashboard so you can *type* to the assistant
instead of speaking — for people who'd rather write at the computer, or who are
up late while the house is asleep and don't want a voice reply. No more switching
to Home Assistant's Assist box to test something.

- **Frontend-only, zero backend change.** The chat routes through JARVIS's own
  HA **conversation entity** via the native `conversation/process` WebSocket
  command, so it's the exact same brain as voice and the HA Assist box —
  questions *and* home control both work. No new backend command, no kernel
  surface touched.
- Agent discovery is a state-scan (the panel isn't admin-gated, so the entity
  registry WS isn't available to every user); falls back to HA's default
  conversation agent if no JARVIS entity is found.
- Multi-turn: the returned `conversation_id` is carried across messages for
  follow-up context. The request passes the viewer's HA language, so replies
  follow the user's language (consistent with #317).
- Session-local history (clears on reload), Enter-to-send / Shift+Enter for a
  newline, a typing indicator, inline error rows if JARVIS can't be reached, and
  a global-search entry so the tab is findable.

Behaviour-preserving: a new, opt-in-by-clicking tab; nothing else in the panel
changes. Tests: `scripts/smoke_panel.js` gains six chat assertions (tab + compose
render, agent discovery, routing through `conversation/process`, user/JARVIS
message rendering, conversation_id retention). Audit + four kernel gates green.
Version 8.176.0 → 8.177.0.

## [8.176.0] — localize Sentinel's static door/window alerts (issue #317)

Follow-up to 8.174.0. That fix localized JARVIS's **LLM-generated** output; this
one closes the remaining gap the reporter hit — proactive **Sentinel** alerts
("{honorific}, {friendly_name} has been open for {minutes} minutes") are plain
English **format strings** spoken verbatim, so on a non-English home they came
out in English (and, once TTS was set to Russian, as English words in a Russian
voice). The LLM-generated Sentinel fallback already localized; the static
default rules did not.

- `language.py`: new `translate_directive(hass, lang=None)` — a translation
  system prompt naming the household output language (keep names/numbers), or
  `""` for English / unset so the caller speaks the original text and makes **no
  extra model call**. Follows the same resolution as the rest of the module
  (per-request → `output_language` → `ui_language` → HA system → en).
- `sentinel.py`: new `_localize(text)` translates a filled static template into
  the output language via the sentinel LLM client before speaking/pushing it;
  wired into the `"message"`-rule branch. No-op for English; best-effort — any
  failure falls back to the original English line and never raises into the
  alert path.

Behaviour-preserving for English homes (no translation, no extra call, identical
text). Tests: `test_language_module.py` gains the `translate_directive` cases
(empty for English; names the target; follows `output_language` and the
`ui_language` fallback). Audit + four kernel gates green. Version
8.175.0 → 8.176.0.

## [8.175.0] — boot-continuity announce seam (Phase I4, default OFF)

Builds the last rung of commitment continuity (Phase I-A.4): on restart JARVIS
can **say aloud** what it was in the middle of — the goals, open situations and
mode it held before the restart — instead of only logging it. Ships **owner-gated
and default OFF**, so startup is behaviour-identical until the owner flips it.

- `continuity.py`: new `CONTINUITY_RESUME_ANNOUNCE` kill-switch (default **False**)
  + async `announce_resume(hass, entry)`. When enabled it speaks
  `resume_summary(hass)` through the output seam, resolving the TTS engine and
  announcement speakers exactly as Sentinel does (`tts_helper.resolve_tts_entity`
  + `audio_routing.broadcast_target` from the live runtime config) and speaking
  once via `tts_helper.async_announce`. Fail-safe: no snapshot / no speakers / any
  error → says nothing and returns False; it can never affect the boot path.
- `__init__.py`: calls `await continuity.announce_resume(hass, entry)` on boot,
  right after `boot_summary` / `boot_reconcile`.
- `docs/KERNEL_PLAN.md`: Phase I4 row → seam shipped, default OFF (owner flips
  `CONTINUITY_RESUME_ANNOUNCE`).

Behaviour-preserving: with the switch off (default) nothing new is spoken, and
`agency_state`'s adoption stage stays `shadow` (no live consumer while both
`CONTINUITY_RESUME_ANNOUNCE` and `CONTINUITY_RESUME_ENFORCE` are off). The owner
still flips the switch to actually enable the spoken resume. Tests: new
`test_continuity_announce.py` (default-off is silent; enabled-but-empty says
nothing; enabled speaks the resume line through stubbed TTS/speakers; enabled
with no speakers is safe). Audit + four kernel gates green. Version
8.174.0 → 8.175.0.

## [8.174.0] — output language falls back to the JARVIS UI language (issue #317)

The reporter had JARVIS's own UI (and their HA **profile**) set to Russian, yet
announcements and proactive camera analysis came out in **English** (and were
logged in English), while live satellite voice answered correctly. Root cause:
with the dedicated `output_language` on **Auto**, `configured_language` fell
straight through to Home Assistant's **system** language — which a user who
changed only their *profile* language never set — so machine-authored output
(no per-request language) defaulted to English. (The 500 error they also hit was
a separate upstream ollama 0.32–0.40.2 regression, fixed by downgrading to
0.31.2 — not a JARVIS bug.)

- `language.py`: `configured_language` gains a step — when `output_language` is
  Auto it now falls back to JARVIS's panel **`ui_language`** before HA's global
  language. New resolution order: per-request `lang` → `output_language` →
  `ui_language` → HA system `language` → `en`. If you set JARVIS's own UI to a
  language, its spoken/written output now defaults to it too. Added
  `_jarvis_ui_language()` + a shared `_clean_lang_setting()` helper (Auto /
  default / blank all normalise to "follow the next source").

Additive and behaviour-preserving everywhere else: an explicit `output_language`
still wins (issue #148 intact), a per-request voice language still wins, and with
both JARVIS knobs on Auto the HA system language is used exactly as before. Tests:
`test_language_module.py` gains the #317 cases (UI language fills in when output
is Auto; output_language and per-request language still beat it; both-Auto falls
through to the global; region suffix stripped). Audit + four kernel gates green.
Version 8.173.0 → 8.174.0.

## [8.173.0] — opt-in "heading home" push notification (issue #265)

The reporter asked to be **notified on their phone** when someone is heading
home — not just hear it spoken at the (possibly empty) house. The
`anticipation_arriving` alert was already produced (deduped per person in
8.166.0) but, being low-urgency, was spoken only. This adds an opt-in push.

- `cognition.py`: the `anticipation_arriving` action is now flagged
  `"push": True` (push-eligible).
- `cognitive_core.py`: new pure `_awareness_push_wanted(action, pushed, routed,
  enabled)` — a push-eligible low-urgency action also reaches the phone **only**
  when the household opts in and no earlier branch already pushed/routed it (so
  it is default-off and never double-notifies). `_emit_action` tracks a `_pushed`
  flag across its existing push sites (driving-mode / quiet-hours / critical-high)
  and consults the helper, pushing via the existing `_push_notification` /
  `_notify_all_devices` when enabled.
- `websocket.py`: `arrival_push_enabled` (default **False**) added to the
  settings get-block and the settable-keys allowlist, so it is toggleable from
  the panel like the other alert switches.

Behaviour-preserving by default: with `arrival_push_enabled` off (the default),
the arriving alert is spoken exactly as before and nothing new is pushed. Tests:
new `test_arrival_push.py` (the pure helper — pushes when enabled, default-off,
no double-push when already pushed, not when routed to the car, and never for a
non-push-eligible action) + `test_cognition_proximity_dedup.py` asserts the
arriving action is push-eligible. Audit + four kernel gates green. Version
8.172.0 → 8.173.0.

## [8.172.0] — environment parity over the live energy picture (Phase X)

Advances `kernel.environment` from **shadow → parity**: over real proactive
ticks, JARVIS now quantifies how often the kernel environment recommender agrees
with the incumbent energy decision — the bar the owner-gated enforce must clear.

- `energy.py`: new `ENVIRONMENT_PARITY` (default **True**) + `_environment_parity`.
  On each `evaluate_for_proactive` tick it compares the kernel recommender's
  **actionable** efficiency verdict (`environment.recommend` on the live draw)
  against the incumbent "would surface an energy offer" predicate (over peak AND
  ≥2 sheddable loads), accumulates agreement, and logs the running rate
  (`environment(parity): n=… agree=… kernel_rec=… incumbent_offer=…
  kernel_only=… incumbent_only=…`). The expected divergence is the kernel
  recommending on "over peak" alone while the incumbent also requires something
  to stagger — the signal enforce reconciles. **Observe-only:** hooked beside the
  shadow call before the proactive early-returns; never changes the offer;
  kill-switched and defensive (missing meter / zero peak / malformed input all
  no-op).
- `scripts/kernel_adoption.py`: `environment` **shadow → parity** (owner
  `energy`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Phase X status → pure + shadow + parity shipped.

No behaviour, actuation, or safety path changes. Tests: new
`test_energy_environment_parity.py` (agreement when over-peak-with-sheddable and
when under-peak, the kernel-only divergence, running-rate log, accumulation,
kill-switch, defensive no-ops). Audit + four kernel gates green. Version
8.171.0 → 8.172.0.

## [8.171.0] — environment shadow over the live energy picture (Phase X)

Advances `kernel.environment` from **pure → shadow**: the physical-world model's
efficiency objective is now exercised against JARVIS's real whole-home power
draw, observe-only.

- `energy.py`: new `ENVIRONMENT_SHADOW` (default **True**) + `_environment_shadow`.
  On each `evaluate_for_proactive` tick, the live power picture (`watts`,
  `peak_watts`) is mirrored into `kernel.environment.efficiency` and the kernel
  verdict + would-be recommendation is logged against the module's own
  `over_peak` decision (`environment(shadow): draw=… peak=… util=… kernel_over=…
  incumbent_over_peak=… agree=… recs=…`). **Observe-only:** hooked before the
  proactive early-returns, it never changes the offer; defensive (missing meter /
  zero peak / malformed input all no-op) and wrapped so it can never reach the
  energy path. Flip `ENVIRONMENT_SHADOW` off to silence it.
- Efficiency objective only here (energy has no comfort readings); comfort
  (temperature/humidity) and the recommender's safety guard wire in when a
  climate/hazard path adopts the model.
- `scripts/kernel_adoption.py`: `environment` **pure → shadow** (owner `energy`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Phase X status → pure + shadow shipped.

No behaviour, actuation, or safety path changes. Tests: new
`test_energy_environment_shadow.py` (logs the efficiency verdict + agreement
flag, kill-switch, and defensive no-ops on missing meter / zero peak / garbage).
Audit + four kernel gates green. Version 8.170.0 → 8.171.0.

## [8.170.0] — Self-optimization with a tiered guardrail (Phase Y, pure)

New pure kernel primitive `kernel/optimize.py` — measure-then-tune, with the
governance invariant ("more capability never means less governance") encoded
structurally so a self-optimizer can never relax a safety threshold or an
authority gate.

- **Tiered guardrail:** `classify(param)` returns SAFE (latency / cost /
  provider selection / cache / context size / resource allocation — self-tunable
  within owner bounds), SENSITIVE (autonomy levels / confidence thresholds /
  interrupt thresholds / safety model selection — proposal-only, owner-gated), or
  FORBIDDEN (authority ceiling / security policy / identity requirements / safety
  thresholds / human override / audit retention — never tunable). An **unknown
  parameter defaults to FORBIDDEN** (fail-safe = fixed config).
- **Guarded proposals:** `propose(param, current, proposed, bound=…)` builds a
  `TuningProposal` with a `Metric` (direction via `lower_is_better`) and a `Bound`
  (owner floor/ceiling). It is `auto_applicable` only when SAFE **and** within
  bounds, `proposal_only` when SENSITIVE and within bounds, else `rejected` — a
  SAFE value out of bounds and any FORBIDDEN/unknown param are rejected.
  `report(...)` rolls proposals into auto-applicable / owner / rejected buckets.
- `kernel/__init__.py`: exports `optimize`, `TuningProposal`, `TuningBound`,
  `OptimizeMetric`, `OptimizationReport`, `classify_tunable`, `propose_tuning`.
- `scripts/kernel_adoption.py`: declares `optimize` **pure**; `KERNEL_ADOPTION.md`
  + `docs/JARVIS_CONSTITUTION.md` regenerated (one new row).
- `docs/KERNEL_PLAN.md`: Phase Y status → pure shipped.

Pure: no HA import, it proposes and never applies; nothing live consumes it yet,
so the release is behaviour-preserving. Tests: new `test_kernel_optimize.py`
(tier classification incl. the unknown→FORBIDDEN fail-safe, bounds/clamp, metric
direction, and the full guard matrix — SAFE-in-bounds auto, SAFE-out-of-bounds
rejected, SENSITIVE proposal-only, FORBIDDEN always rejected — plus the report
roll-up). Audit + four kernel gates green. Version 8.169.0 → 8.170.0.

## [8.169.0] — Physical-world intelligence: state + objective functions (Phase X, pure)

New pure kernel primitive `kernel/environment.py` — a read-only model of the
physical world plus objective functions that score comfort and efficiency and,
crucially, a recommender that structurally obeys the safety seam and priority
ladder. JARVIS already senses the physical world through scattered heuristics
(`energy` draw-vs-peak with a never-shed list, `sentinel` apertures, the freeze
threshold); Phase X gives them a common, inspectable substrate.

- `kernel/environment.py`: `Reading` (temperature / humidity / air-quality /
  power / apertures) + `EnvironmentState` query helpers (`by_kind` / `by_area` /
  `latest`). Objective functions: `comfort(state, bands)` scores each reading
  against a `ComfortBand` (1.0 at the ideal, linearly to 0 one band-width away,
  with `classify` below/comfortable/above) and rolls up overall + worst;
  `efficiency(power_w, peak_w)` assesses whole-home draw against a peak
  (over / headroom / utilization).
- **Safety guard (the invariant):** `recommend(...)` / `assess(...)` emit
  advisory `Recommendation`s only — comfort at the CONVENIENCE tier, efficiency
  at HOUSEHOLD — and flag each `blocked_by_safety` whenever an active safety
  concern outranks it, reusing `kernel.priority.may_override`. So comfort and
  efficiency can never override a safety concern, and `actionable` is never True
  while one is active. The primitive holds no actuator and grants nothing.
- `kernel/__init__.py`: exports `environment`, `EnvironmentState`,
  `EnvironmentAssessment`, `ComfortBand`, `assess_environment`, etc.
- `scripts/kernel_adoption.py`: declares `environment` **pure**;
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated (one new row).
- `docs/KERNEL_PLAN.md`: Phase X status → pure shipped.

Nothing live consumes it yet, so the release is behaviour-preserving — no
actuation or safety path changes. Tests: new `test_kernel_environment.py`
(comfort-band classify/score/clamp, state queries, comfort + efficiency
objectives, the safety-ladder guard for LIFE_SAFETY/SECURITY/HOUSEHOLD, and the
`assess` one-shot). Audit + four kernel gates green. Version 8.168.0 → 8.169.0.

## [8.168.0] — Constraint-aware planning: alternatives + compensation (Phase U, pure)

Extends `kernel.plan` with the two net-new constraint-aware primitives Phase U
calls for over the linear executor — preconditions/postconditions/idempotency
already existed. Both are additive and **behaviour-preserving**: a step that
declares neither behaves exactly as before.

- `kernel/plan.py`: **alternatives** — a `Step` may carry ordered fallback
  `Step`s. If the primary attempt fails (precondition unmet / action fails /
  postconditions never verify), the executor tries each alternative in order and
  the first to reach DONE satisfies the step (the outcome notes which one). Wired
  into both `execute_plan` and the async `aexecute_plan`; a step with no
  alternatives collapses to the old act/verify path exactly.
- `kernel/plan.py`: **compensation** — a `Step` may declare an undo `Step`. The
  executor **never** runs it automatically (a saga/rollback *engine* stays
  deliberately out of scope for this deployment); instead the pure
  `plan_compensation(report, plan)` *derives* the rollback plan — the
  compensations of the steps that actually took hold, newest-first — so a caller
  that chooses to unwind a partially-applied plan routes that plan back through
  the same authority + verify executor. A step completed via an alternative still
  compensates (its effect took hold); a completed step with no compensation is
  skipped.
- `kernel/__init__.py`: exports `plan_compensation`.
- `docs/KERNEL_PLAN.md`: Phase U status → pure shipped.

Nothing live consumes alternatives/compensation yet, so `plan` stays at its
current adoption stage (parity). No behaviour, actuation, or safety path changes.
Tests: new `test_kernel_plan_constraints.py` (alternatives ordering + fallthrough
+ idempotency-via-alternative + async; compensation reverse-undo derivation,
done-without-compensation skip, executor-never-auto-runs-it, correlation carry).
Audit + four kernel gates green. Version 8.167.0 → 8.168.0.

## [8.167.0] — Long-horizon agency shadow over live goals (Phase V)

Advances `kernel.long_horizon` from **pure → shadow**: JARVIS's active goals are
now observed through the long-horizon primitive, observe-only.

- `continuity.py`: new `LONG_HORIZON_SHADOW` (default **True**) +
  `_long_horizon_shadow`. On each capture tick, each live active goal is modelled
  as a `long_horizon.LongHorizonGoal` (its steps → milestones, goal step statuses
  mapped to the milestone lifecycle) and a progress roll-up is logged (count /
  complete / avg progress). **Observe-only:** nothing persists or resumes the goals
  durably yet (that is the parity/enforce rung — a goal surviving a restart with
  correct progress). Fail-safe: wrapped and never raises into the capture path;
  flip `LONG_HORIZON_SHADOW` off to silence it.
- `scripts/kernel_adoption.py`: `long_horizon` **pure → shadow** (owner
  `continuity`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Phase V status → pure + shadow shipped.

No behaviour, actuation, or safety path changes. Tests: `test_continuity.py`
gains the long_horizon-shadow cases (progress roll-up, kill-switch, no-goals
defensive). Audit + four kernel gates green. Version 8.166.0 → 8.167.0.

## [8.166.0] — Arrival anticipation deduped per person (issue #265)

A person entity and the `device_tracker`s it owns (its "Track these devices"
list) are all GPS presence sources, so `cognition.predict_proximity` emitted a
separate "heading home" per entity — e.g. `brewston` **and** `brewston_S26` — for
one person on one trip.

- `cognition.py`: `predict_proximity` now keys the approach alert (and its
  per-trip `_APPROACH_ALERTED` state, reset on arriving home) by the **owning
  person** rather than the entity. Whichever of a person's sources crosses
  `APPROACH_OUTER_KM` while closing fires first; the rest are suppressed for that
  trip — so no alert is lost even if the person entity itself lacks live GPS, and
  the message names the **person**, not the phone. A tracker not attached to any
  person is still its own mover and alerts on its own. Ownership comes from each
  person entity's `device_trackers` attribute (the same source HA uses).

No config change. Tests: `test_cognition_proximity_dedup.py` (one alert per
person; standalone tracker still alerts; reset-on-home lets the next trip alert).
Version 8.165.0 → 8.166.0.

## [8.165.0] — Force the household language on all task prompts (issue #307)

Non-English households were still getting English on proactive/announce output
(briefings, sentinel notices, status, camera notes) because every machine-authored
task prompt went through `build_system_prompt`, which appended the **conversational**
language directive — the one with a *"if the user writes in another language, reply
in that"* escape clause. A weak local model reads the English instruction block +
that clause as *"the user wrote English"* and answers in English. This is exactly
the issue #140 failure that `language_task_directive` (forced, no escape clause)
already fixed for camera analysis — now applied to **all** task prompts.

- `directive_helper.py`: `build_system_prompt` now appends the **forced**
  `language_task_directive` instead of `language_directive`. Every task prompt
  (briefings, sentinel, summary, proactive briefing, reasoning loop, camera) now
  carries a no-escape-clause instruction to write in the configured language. The
  conversation path (`agent.run_agent`) keeps the escape-clause directive, which is
  correct there. Behaviour-preserving for English installs (both are empty for
  English); honours JARVIS's `output_language` setting and HA's global language.
- `camera.py`: drop the now-redundant manual `language_task_directive` append —
  `build_system_prompt` forces it for every caller uniformly.
- Tests: `test_language_module.py` updated to assert the forced directive (no
  escape clause) on task prompts.

**Note:** small quantized local models (sub-3B) often still ignore a "respond in
Russian/German" instruction regardless — a more capable model is needed for
reliable non-English spoken output. This change removes the prompt-side loophole;
it cannot make a weak model follow instructions. Version 8.164.0 → 8.165.0.

## [8.164.0] — Long-horizon agency primitive (pure) — opens Phase V

Lands the foundation of Phase V (Long-Horizon Agency): a durable, resumable,
progress-tracked goal model — the direct payoff of Phase I (continuity of self).

- `kernel/long_horizon.py` (NEW, **pure**): a `LongHorizonGoal` is an ordered set
  of `Milestone`s (lifecycle `pending` / `active` / `done` / `blocked` /
  `skipped`) with a stable id. Pure derivations: `progress` (resolved fraction),
  `next_milestone` (active preferred, else first unresolved), `is_stalled(now, *,
  max_idle)`, `is_complete`, `resolved` / `achieved` counts. Total transitions:
  `advance(milestone_id, status, now)` returns a **new** goal (the receiver is
  never mutated; an unknown id or invalid status is a no-op). Builder `plan_goal`
  coerces `Milestone` objects / `{label,status,id}` mappings / bare label strings,
  drops malformed entries and makes ids unique; `summarize` rolls a set up by
  state. No persistence, no clock, no Home Assistant import, no raises.
- Exported from `kernel/__init__.py` (`LongHorizonGoal`, `LongHorizonStats`,
  `Milestone`, `plan_long_horizon_goal`).
- `scripts/kernel_adoption.py`: new `long_horizon` primitive declared **pure**
  (no live caller yet); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md`
  regenerated. `docs/KERNEL_PLAN.md`: Phase V status → pure shipped.

No behaviour, actuation, or safety path changes — nothing consumes it yet. The
durable ledger + restart-resume wiring land at shadow (a live binder persists
these, resumed from `agency_state`, a natural fit for the existing `continuity`
binder). Ladder from here: pure → shadow → parity → enforce (owner-gated,
`LONG_HORIZON_ENFORCE`, fail-safe = session-scoped goals). Tests:
`test_kernel_long_horizon.py`. Audit + four kernel gates green. Version 8.163.0 →
8.164.0.

## [8.163.0] — Self-model shadow on the cognitive-status read (Phase S)

Advances `kernel.self_model` from **pure → shadow**: JARVIS's self-picture is now
*projected from live state* on the introspection path, observe-only.

- `agent.py`: new `SELF_MODEL_SHADOW` (default **True**) + `_project_self_model`
  (pure) + `_build_self_model_snapshot` (live reads). `_exec_cognitive_status`
  now builds a `SelfModel` from live inputs — governed **capabilities** from the
  enforcement registry (each `available` when its switch is on, `unavailable`
  when off), **commitments** from active goals + open kernel situations,
  **confidence** = share of capabilities active, and inactive ones as **limits** —
  then logs `self(shadow): …` and surfaces it in the status JSON (beside the E1
  beliefs snapshot). **Observe-only:** no decision consumes it; it only
  *describes* (naming a capability never grants it — authority stays in
  `kernel.authority`). Fail-safe: every read is guarded and never fails the status
  call; flip `SELF_MODEL_SHADOW` off to silence it.
- `scripts/kernel_adoption.py`: `self_model` **pure → shadow** (owner `agent`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Phase S status → pure + shadow shipped.

No behaviour, actuation, or safety path changes — the status tool gains an
introspection field, nothing more. Tests: `test_self_model_shadow.py` (switch →
capability mapping, commitments, confidence, defensive, describing-is-not-having,
kill-switch default). Audit + four kernel gates green. Version 8.162.0 → 8.163.0.

## [8.162.0] — Self-model primitive (pure) — opens Phase S (Self Model & Self Awareness)

Lands the foundation of Phase S: an explicit, inspectable model of what JARVIS is
and can do, so "what can you do / what are you doing / are you sure?" can be
model-backed instead of confabulated.

- `kernel/self_model.py` (NEW, **pure**): a read-only `SelfModel` projection —
  `capabilities` (each a `Capability` with an `available` / `degraded` /
  `unavailable` status), `commitments` (active goals / open situations), overall
  `confidence` (clamped [0,1]), known `limits`, and `identity`. Pure derivations:
  `can(name)` (case-insensitive, usability-aware), `available`, `report()` (an
  honest one-liner that never over-claims), `to_dict()`; plus a total `project()`
  builder that coerces Capability objects / `{name,status,note}` mappings / bare
  name strings and drops malformed entries. No Home Assistant import, no clock, no
  raises.
- **Hard rule, by construction:** the model only *describes* — it carries no
  authority and has no method that grants, activates or widens anything (authority
  lives in `kernel.authority`). An unavailable capability is never reported usable.
- Exported from `kernel/__init__.py` (`SelfModel`, `Capability`, `self_project`).
- `scripts/kernel_adoption.py`: new `self_model` primitive declared **pure**
  (no live caller yet); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md`
  regenerated. `docs/KERNEL_PLAN.md`: Phase S status → pure shipped.

No behaviour, actuation, or safety path changes — nothing consumes the model yet.
Ladder from here: pure → shadow → parity → enforce (owner-gated,
`SELF_MODEL_ENFORCE`, fail-safe = static capability list). Tests:
`test_kernel_self_model.py` (coercion, usability-aware `can`, never-over-claim
`report`, confidence clamping, empty/defensive). Audit + four kernel gates green.
Version 8.161.0 → 8.162.0.

## [8.161.0] — Temporal validity shadow on the "where last seen" answer (Epistemic Fabric)

Broadens the `kernel.temporal` shadow from **semantic** memory (curated knowledge,
8.160.0) to **episodic** memory (scene memory), where staleness is most decision-
relevant: a last-seen location trusted blindly can be hours out of date.

- `agent.py`: new `WHERE_LAST_SEEN_TEMPORAL_SHADOW` (default **True**) +
  `WHERE_LAST_SEEN_TTL` (6 h) + `_emit_where_last_seen_temporal_shadow`.
  `_exec_where_last_seen` now logs the freshness band (fresh / aging / expired) of
  the scene sighting it surfaces, via `temporal.Validity`. **Observe-only:** the
  tool's JSON answer is unchanged; nothing hedges on staleness yet (that is the
  parity/enforce rung). Fail-safe: the emitter never raises; flip the switch off
  to silence it.
- `scripts/kernel_adoption.py`: `temporal` owners now `agent, knowledge` (still
  shadow); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Time / Temporal Validity note → semantic + episodic shadow.

No behaviour, actuation, or safety path changes. Tests:
`test_where_last_seen_temporal_shadow.py` (fresh / expired band, kill-switch,
defensive). Audit + four kernel gates green. Version 8.160.0 → 8.161.0.

## [8.160.0] — Temporal validity shadow over curated knowledge (Epistemic Fabric)

Advances the fifth Epistemic-Fabric primitive `kernel.temporal` from **pure →
shadow**: curated knowledge is now observed as *time-bound* (sourced + uncertain
+ now valid-as-of / expires-at), observe-only.

- `knowledge.py`: new `TEMPORAL_SHADOW` (default **True**) + `_emit_temporal_shadow`.
  `all_facts()` packages each curated fact's `updated_at` (valid-as-of) and
  `expires_at` (→ ttl; absent ⇒ DURABLE) as a `temporal.Validity` and logs the
  freshness-band distribution (fresh / aging / expired / durable). **Observe-only:**
  `all_facts()` returns exactly the same rows whether this runs or not; nothing
  defers on staleness yet (that is the parity/enforce rung). Fail-safe: the
  emitter is wrapped and never raises into the store; flip `TEMPORAL_SHADOW` off
  to silence it.
- `scripts/kernel_adoption.py`: `temporal` **pure → shadow** (owner `knowledge`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Time / Temporal Validity status → shadow shipped.

No behaviour, actuation, or safety path changes. Tests: `test_knowledge.py` gains
the temporal-shadow cases (band distribution, aging as lifetime elapses,
kill-switch, defensive). Audit + four kernel gates green. Version 8.159.0 →
8.160.0.

## [8.159.0] — Enforce: multi-agent orchestration (Phase O)

Second owner-authorized enforce flip. The kernel is now the authority source for
delegated sub-agents — **behaviour-identical today**, fail-safe to the incumbent
tool set.

- `agent.py`: `AGENCY_ORCHESTRATION_ENFORCE` **False → True**. A delegated
  sub-agent's effective tool set is now DERIVED from a kernel `agency.spawn`
  (strict narrowing of the incumbent-resolved set), and a delegation the kernel
  refuses is VETOED. It sits **behind** every incumbent gate (depth guard, FRIDAY
  `_profile_enabled` opt-in, capability validity), so it can only narrow, never
  widen. **No-op today:** JARVIS's root token holds `("*",)`, so the derived set
  equals the incumbent set and the veto never fires — the kernel simply becomes
  authoritative so the narrowing bites once its token is scoped. **Fail-safe:**
  any kernel error passes the incumbent `allowed` set through unchanged.
- `enforcement.py`: `agency_orchestration` Governance switch now reports `default`
  **True** with an updated explanation; still a capability, reversible OFF.
- `scripts/kernel_adoption.py`: `agency` **parity → enforce** (owner `agent`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Phase O status → enforce ON.

The 7 safety kill-switches remain ON. Tests: `test_enforcement.py` +
`test_agency_delegation_shadow.py` updated for the new default (the
pass-through / never-widen / fail-safe properties already pinned). Audit + four
kernel gates green. Version 8.158.0 → 8.159.0.

## [8.158.0] — Enforce: knowledge-graph recall (Phase T) + Ω→README note

First owner-authorized **enforce flip** (per "full permissions for enforcements").
Lowest-risk capability rung, fail-safe to current behaviour.

- `knowledge.py`: `KNOWLEDGE_GRAPH_ENFORCE` **False → True**. The curated-knowledge
  prompt block is now **graph-authoritative** — recall-seeded facts are expanded
  one hop through the typed knowledge graph (directly-related facts pulled in,
  capped at 6). **Fail-safe:** any failure or empty graph result falls back to
  exactly the flat recall block, so this can only *add* related context — it never
  removes context or touches any actuation/safety path. Earned parity first
  (`GRAPH_PARITY` measured the would-be expansion in 8.154.0).
- `enforcement.py`: the `knowledge_graph` Governance switch now reports `default`
  **True** with an updated explanation; still a capability (reversible OFF →
  flat recall from Settings → Governance). `overridden` semantics unchanged.
- `scripts/kernel_adoption.py`: `graph` **parity → enforce** (owner `knowledge`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Phase T status → enforce ON; **and a durable note that
  Phase Ω's definition of done includes refreshing the README** (owner-requested).

The 7 safety kill-switches are untouched (still ON). Tests:
`tests/unit/test_knowledge.py` (enforce-on-by-default expansion; enforce-off path
pinned explicitly) and `test_enforcement.py` (knowledge_graph default True).
Audit + four kernel gates green. Version 8.157.0 → 8.158.0.

## [8.157.0] — World-model multi-source presence fusion (shadow)

Phase-T world-model multi-source fusion: adjudicate a person's whereabouts across
independent **source types**, not just device-trackers. This is the piece that
unblocks the Conflict enforce rung — a consumer can now read one fused, provenance
-scored verdict instead of whichever subsystem ran last.

- `kernel/world_model.py`:
  - `WorldModel.fuse_presence(name, *, now=None)` → a `kernel.conflict.Resolution`
    over `kernel.provenance` records, fusing HA's own aggregate `person.state`
    (source `person`, reliability 0.7) against a recent **camera recognition**
    (source `camera`, 0.8, 5-min ttl). Returns `home`/`away`, **contested** when
    the independent sources disagree within the margin (e.g. a camera sees Sam but
    their phone says away). Reads HA state directly (no re-entry into the presence
    read), best-effort → empty `Resolution` on any failure (consumer falls back to
    HA's own resolution). Only a genuine ≥2-source case is fused.
  - helper `_person_home_state`, seam `_who_is_where` (recognition).
- `presence.py`: `_emit_presence_fusion_shadow(hass)` logs the fused verdict per
  person (value / resolved-or-CONTESTED / ranked scores), wired into
  `get_presence_summary` alongside the existing device-tracker conflict shadow.
  **Observe-only** — the summary dict is unchanged; kill-switch
  `PRESENCE_FUSION_SHADOW`.
- `scripts/kernel_adoption.py`: `conflict` declaration comment notes the
  multi-source fusion (stage unchanged, **parity**, owner `presence`);
  `KERNEL_ADOPTION.md` regenerated (adds `presence` to `world_model`'s live-users,
  since it now references the facade).

Behaviour-preserving: observe-only, kill-switched, HA's resolution still stands;
the enforce rung (a consumer reads the fused value) stays owner-gated. Tests:
`tests/unit/test_kernel_world_model.py` (+6) and `test_presence_fusion_shadow.py`
(5). Audit + four kernel gates green. Version 8.156.0 → 8.157.0.

## [8.156.0] — Temporal-validity primitive (Epistemic Fabric — Time, pure)

The fifth and final Epistemic-Fabric primitive: **Time / Temporal Validity**.
Perception and knowledge aren't just uncertain (`kernel.uncertainty`) and sourced
(`kernel.provenance`) — they're time-bound. This makes that first-class.

- `kernel/temporal.py` (NEW, pure — no Home Assistant import, deterministic `now`
  passed in):
  - `Validity` — pairs an `observed_at` (valid-as-of) with an optional `ttl`
    (→ `expires_at`), deriving `age` / `remaining` / `is_valid` / `fraction_elapsed`
    / `freshness` (bounded [0,1], linear decay to expiry) and a coarse **band**:
    `fresh` (<50% of lifetime), `aging` (50–100%), `expired` (≥100%), `durable`
    (no ttl — never expires). `describe()` gives an honest one-liner
    ("expired (observed 900s ago) — re-confirm"); `to_dict()` serialises.
  - `summarize(items, now)` → `ValidityStats` rolls a set up by band (for shadow
    logging), skipping non-`Validity` items.
- `kernel/__init__.py` exports `temporal`, `Validity`, `ValidityStats`.
- `scripts/kernel_adoption.py` declares `temporal` at stage **pure** (owners
  `[]`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Time / Temporal-Validity note → pure shipped (completes
  the five Epistemic-Fabric primitives).

Behaviour-preserving: pure, nothing consumes it live yet. Ladder from here: shadow
(a live source logs its validity band distribution) → parity → enforce (a decision
defers on a stale/expired value), owner-gated. Tests:
`tests/unit/test_kernel_temporal.py` (9). Audit + four kernel gates green. Version
8.155.0 → 8.156.0.

## [8.155.0] — Uncertainty coverage: the world-model wraps outputs too

Epistemic Fabric (Uncertainty) broadened past perception, per the plan's
"world-model / prediction wrap outputs too." Until now only `identity.resolve`
packaged a kernel `Uncertain`; this extends the primitive to curated knowledge —
observe-only, nothing gates on the band (identity remains the parity anchor).

- `kernel/world_model.py`: new `WorldModel.uncertainties(subject)` facade — each
  curated fact's flat `confidence` becomes a kernel `Uncertain` (value + band
  known/believed/guessed/unknown + basis `source:subject.key`), mirroring the
  existing `beliefs()` / `provenances()` views. SHADOW: available and tested,
  nothing consumes it.
- `knowledge.py`: `_emit_uncertainty_shadow(facts)` logs the epistemic-band
  distribution of the curated facts (`N known / M believed / K guessed / W
  unknown`), wired into `all_facts()` alongside the provenance/graph shadows.
  Kill-switch `UNCERTAINTY_SHADOW`; `all_facts()` returns the same rows either way.
- `scripts/kernel_adoption.py`: `uncertainty` owners `["identity"]` →
  `["identity", "knowledge"]` (stage stays **parity** — identity is the anchor);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.
- `docs/KERNEL_PLAN.md`: Uncertainty ladder note updated with the broadened
  coverage.

Behaviour-preserving: both additions are observe-only and kill-switched; no
decision gates on the band (enforce is still future, owner-gated). Tests:
`tests/unit/test_kernel_world_model.py` (+3) and `test_knowledge.py` (+3). Audit +
four kernel gates green. Version 8.154.0 → 8.155.0.

## [8.154.0] — Phase T knowledge-graph shadow → parity (recall expansion)

Roadmap **Phase T** (Deep World Model) graph primitive promoted shadow → parity.
The knowledge-graph 1-hop recall expansion already ships as an owner-gated enforce
path (`KNOWLEDGE_GRAPH_ENFORCE`, default OFF); this adds the quantified parity bar
between shadow and enforce.

- `knowledge.py`:
  - `_log_graph_parity(seed, expanded)` — logs how many related facts the graph's
    1-hop expansion would add to the current flat recall.
  - `prompt_block_async`: when enforce is OFF (the live state) and `GRAPH_PARITY`
    is on, it computes the would-be expansion via `_graph_expand_facts` and logs
    the delta **without changing the block the model sees** — observe-only. When
    enforce is ON, it expands as before (no double parity log).
  - kill-switch `GRAPH_PARITY`, independent of `KNOWLEDGE_GRAPH_ENFORCE`.
- `scripts/kernel_adoption.py`: `graph` **shadow → parity** (owners
  `["knowledge"]`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md`
  regenerated from it.
- `docs/KERNEL_PLAN.md`: Phase T status → pure + shadow + parity shipped, enforce
  path built (owner-gated, OFF).

Behaviour-preserving: the prompt block is unchanged while enforce stays OFF; the
parity read only measures and logs. The enforce rung (`KNOWLEDGE_GRAPH_ENFORCE`,
fail-safe = flat recall) remains owner-gated and already appears in the Settings →
Governance registry (`knowledge_graph`). Tests: `tests/unit/test_knowledge.py`
(+3 — parity logs would-add without expanding, kill-switch, delta count). Audit +
four kernel gates green. Version 8.153.0 → 8.154.0.

## [8.153.0] — Phase K working-memory parity (attention consults the shared set)

Roadmap **Phase K** parity rung, on the owner-chosen architecture: a **shared
kernel-level `WorkingMemory` singleton** as the one canonical cognitive context.
`cognitive_core` populates it; `output_gate`'s attention arbitration now consults
it.

- `kernel/working_memory.py`: `shared()` returns the process-wide canonical
  `WorkingMemory` (lazily created); `reset_shared()` drops it (reload/tests).
  Exported from `kernel/__init__.py` as `working_memory_shared`.
- `cognitive_core.py`: `_populate_working_memory_shadow` now writes into
  `working_memory.shared()` (was a module-local instance) — so producer and
  consumers share one set. Behaviour unchanged (still shadow, kill-switched).
- `output_gate.py`: `_attention_working_memory_parity()` reads the shared set,
  derives a signal the current gate ignores — whether the household is asleep
  (from the `situation` item) — and logs whether consulting the canonical
  context would **change** the attention arbitration vs the working-memory-blind
  baseline. Wired into `_can_announce_with_multiplier` after the existing
  attention shadow. **Observe-only** — the gate stays authoritative; kill-switch
  `WORKING_MEMORY_PARITY`.
- `scripts/kernel_adoption.py`: `working_memory` **shadow → parity** (owners
  `["cognitive_core", "output_gate"]`); `KERNEL_ADOPTION.md` +
  `docs/JARVIS_CONSTITUTION.md` regenerated from it.
- `docs/KERNEL_PLAN.md`: Phase K status → pure + shadow + parity shipped.

Behaviour-preserving: nothing gates on the working set yet. Ladder from here:
enforce (`WORKING_MEMORY_ENFORCE`, fail-safe = current attention inputs) —
owner-gated, and will join the Governance registry when that constant ships.
Tests: `tests/unit/test_working_memory_parity.py` (7); `test_working_memory_shadow.py`
updated to the shared singleton. Audit + four kernel gates green. Version
8.152.0 → 8.153.0.

## [8.152.0] — Conflict resolution shadow → parity (presence)

Epistemic Fabric **Conflict** primitive promoted shadow → parity. The presence
read already adjudicates each person's backing `device_tracker`s through
`kernel.conflict` and logs the winner vs HA's native `person.state`; this adds
the quantified parity bar over real reads.

- `presence.py`:
  - `_emit_presence_conflict_parity()` — over a rolling window
    (`_CONFLICT_PARITY_WINDOW` = 200) of the per-person adjudications the shadow
    computes, logs how often `kernel.conflict`'s reliability-weighted winner
    **AGREES** with HA's native `person.state` (`agree_rate` over non-contested
    resolutions) and how often it would **DEFER** (`contested_rate`). That is the
    bar the owner-gated enforce rung needs: a consumer should read the adjudicated
    value only once it tracks HA closely and rarely defers.
  - verdicts accumulate inside the existing shadow loop (so parity shares the same
    real reads); the parity summary is emitted once per presence read. Observe-only
    — the summary dict is unchanged and HA's resolution still stands. Kill-switch
    `CONFLICT_PARITY` (independent of `CONFLICT_SHADOW`).
- `scripts/kernel_adoption.py`: `conflict` **shadow → parity** (owners
  `["presence"]`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md`
  regenerated from it.
- `docs/KERNEL_PLAN.md`: Conflict ladder note → pure + shadow + parity shipped.

Behaviour-preserving: observe-only, kill-switched, drives nothing; enforce (a
consumer reads the adjudicated value) stays owner-gated. Tests:
`tests/unit/test_presence_conflict_parity.py` (7 — agreement rate, contested
accounting, divergence, rolling-window arithmetic, kill-switch, empty-window,
summary-unchanged). Audit + four kernel gates green. Version 8.151.0 → 8.152.0.

## [8.151.0] — Phase K working-memory shadow (cognitive_core populate)

Roadmap **Phase K** shadow rung: `cognitive_core` now populates the
`kernel/working_memory.py` primitive (shipped pure in 8.150.0) from each
cognitive tick — the first live producer of the canonical working set.

- `cognitive_core.py`:
  - `_populate_working_memory_shadow(...)` — folds this tick's cognitive context
    into a module-level, **decaying** `WorkingMemory`: the current situation
    (home occupancy / household-asleep, high base salience), the people present
    (observation, salience scaled by count), and the actions decided this tick
    (only when non-zero, so a quiet tick lets the entry decay rather than
    refreshing noise). Logs the bounded `WorkingSnapshot.summary()` under the
    `WORKING_MEMORY` tag.
  - called from `_tick` after the cognitive-cycle shadow, in its own fail-safe
    try/except. **Observe-only** — nothing reads the set back; it drives no
    decision. Kill-switched two ways: the module constant `WORKING_MEMORY_SHADOW`
    and the `working_memory_shadow` config key (either off disables it).
- `scripts/kernel_adoption.py`: `working_memory` **pure → shadow**
  (owners `["cognitive_core"]`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md`
  regenerated from it (doc-sync gate).
- `docs/KERNEL_PLAN.md`: Phase K status → pure + shadow shipped.

Behaviour-preserving: the working set accumulates and decays across ticks but
nothing consumes it. Ladder from here: parity (attention consults it) → enforce
(`WORKING_MEMORY_ENFORCE`, fail-safe = current attention inputs). Tests:
`tests/unit/test_working_memory_shadow.py` (6 — populate, quiet-tick decay,
cross-tick dedup/refresh, both kill-switches, never-raises). Audit + four kernel
gates green. Version 8.150.0 → 8.151.0.

## [8.150.0] — Phase K working-memory primitive (pure)

Roadmap **Phase K — Attention & Working Memory** (docs/KERNEL_PLAN.md): the new
`kernel/working_memory.py` primitive — a bounded, decay-scored working set that
is the one canonical answer to "what is JARVIS holding in mind right now?",
replacing the hidden working memory each path assembles ad-hoc today.

- `kernel/working_memory.py` (NEW, pure — no Home Assistant import, deterministic
  `now` passed in so scoring/eviction are reproducible and unit-testable):
  - `WorkingItem` — a typed cognitive-context item (`kind`, `subject`, `content`,
    base `salience` 0..1, `pinned`, `ts`, `correlation_id`, `source`). `kind` is
    one of the canonical cognitive-context kinds the audit names — **situation,
    objective, intent, people, devices, observations, questions, pending actions
    & verification, memories, predictions, constraints** — so working memory
    represents the *current cognitive context*, not just salient events.
  - `WorkingMemory` — capacity-bounded (default 32), decay-scored
    (`salience × 0.5**(age/half_life)`), with **deterministic eviction** (lowest
    current score; ties → oldest `ts` → earliest insertion). Re-adding the same
    `(kind, subject)` *refreshes* rather than duplicates; pinned items neither
    decay nor get evicted (an active objective, a live constraint). `remember` /
    `touch` / `forget` / `clear`, `top(k)`, `by_kind`, `get`, `__len__`,
    `__contains__`.
  - `WorkingSnapshot` — the bounded cognitive-context view a decision engine
    reads: items grouped by kind (most salient first) plus a flat top-N, with a
    deterministic one-line `summary()` and `to_dict()`.
- `kernel/__init__.py` exports `working_memory`, `WorkingMemory`, `WorkingItem`,
  `WorkingSnapshot`.
- `scripts/kernel_adoption.py` declares `working_memory` at stage **pure**
  (owners `[]`); `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated
  from it (doc-sync gate).

Behaviour-preserving: nothing populates or reads the working set live yet. Ladder
from here is shadow (populate from the bus / cycle) → parity (attention consults
it) → enforce (`WORKING_MEMORY_ENFORCE`, fail-safe = current attention inputs).
Tests: `tests/unit/test_kernel_working_memory.py` (17). Audit + four kernel gates
green. Version 8.149.0 → 8.150.0.

## [8.149.0] — Governance settings tab in the panel (owner control surface, UI)

Second half of surfacing the kernel enforce kill-switches: a **Governance**
sub-section under Settings, wired to the `jarvis/get_enforcement` /
`jarvis/set_enforcement` commands shipped in 8.148.0. The owner can now see and
flip every switch from the panel, with an explanation and fail-safe for each — no
code edits.

- `frontend/jarvis-panel.js`:
  - new **Settings · Governance** sub-nav entry + a full-width governance panel
    (`settings-extra-panel[data-section="governance"]`) and a matching global-
    search entry.
  - `_fetchEnforcement()` calls `jarvis/get_enforcement` and renders a card per
    switch — name, roadmap phase, an "overridden" marker, the explanation, and an
    ON/OFF toggle — grouped into **Owner opt-in capabilities** (ship OFF) and
    **Safety & governance kill-switches (keep ON)**.
  - `_wireEnforcementToggles()` sends `jarvis/set_enforcement {key, enabled}` and
    refreshes. Both consequential directions are confirm-gated: turning a safety
    kill-switch OFF, or turning an opt-in capability ON.
  - populated whenever the Settings tab renders and when the Governance
    sub-section is opened.

No Python change (frontend + version + CHANGELOG only), so the kernel gates and
adoption matrix are untouched. Validated: `node --check` + the jsdom
`scripts/smoke_panel.js` smoke test both clean (the two CI Frontend checks).
Version 8.148.0 → 8.149.0.

## [8.148.0] — Governance enforcement registry + panel API (owner control surface, backend)

First half of surfacing every kernel *enforce* kill-switch to the owner (a
Governance tab in the panel follows). **Backend only; behaviour-preserving** —
with no override stored (the shipped state) nothing changes.

- New `enforcement.py`: a registry over the **ten live** `*_ENFORCE` flags —
  the three owner-opt-in capabilities that ship OFF (`CONTINUITY_RESUME_ENFORCE`,
  `AGENCY_ORCHESTRATION_ENFORCE`, `KNOWLEDGE_GRAPH_ENFORCE`) and the seven live
  safety/governance kill-switches that ship ON (`AUTHORITY_ENFORCE`,
  `AGENCY_BUDGET_ENFORCE`, `LOOP_DETECT_ENFORCE`, `SAFETY_SEAM_ENFORCE`,
  `HAZARD_SITUATION_ENFORCE`, `INTRUSION_GATE_ENFORCE`,
  `DELIVERY_SITUATION_ENFORCE`). Each carries a human name, an explanation of
  what flipping it does and the fail-safe, a category (safety vs capability), and
  its roadmap phase.
  - `apply_overrides()` runs at boot: applies only *stored* owner overrides to
    the live flags; with none stored, every flag keeps its code default.
  - `set_switch(key, enabled)` persists an override (survives restart) and flips
    the live flag immediately.
  - `current()` reports each switch's live value / default / override state.
  The design is minimally invasive — consumers keep reading their own module
  flag; the registry just sets it from config. Defensive throughout; never raises.
- `__init__.py`: calls `enforcement.apply_overrides()` once at setup (no-op until
  the owner stores an override).
- `websocket.py`: new `jarvis/get_enforcement` and `jarvis/set_enforcement` panel
  commands.
- Tests: `tests/unit/test_enforcement.py` (registry well-formedness, known
  defaults, current/override reporting, set persists + flips live, safety
  kill-switch can be toggled, unknown key rejected, apply-only-stored, defensive
  fallbacks).

No kernel adoption change (operability surface, not a ladder rung); `audit.py` +
all four gates green. The owner never has to edit code to flip a switch now, and
I never flip one — this just builds the control. Version 8.147.0 → 8.148.0.

## [8.147.0] — Cognitive continuity resume path (Phase I-B.4, enforce — default OFF)

Builds the **enforce** rung of Phase I-B, completing the ladder (I-B.1 pure /
I-B.2 shadow / I-B.3 parity / **I-B.4 enforce**). The resume capability ships
**ready to flip and default OFF** — nothing in a live home changes until the
owner turns it on, and even then it is behaviour-preserving today (no consumer
yet).

- `continuity.py`: new `CONTINUITY_RESUME_ENFORCE` (default **False**) and
  `resume_summary(hass)` — the continuity line a boot *resume* would announce.
  Sourced from the cognitive snapshot (intent / chosen plan / …) when the switch
  is on; the I-A **commitment-only** summary when off (the fail-safe). No boot
  path consumes it yet (the announce seam is I-A's own I4, also owner-gated), so
  with the switch OFF it is behaviour-identical to today — merely ready to flip.
- `kernel/agency_state.py`: `continuity_summary` gains `include_cognitive`
  (default True); the resume gate passes it through so the fail-safe branch
  renders a commitment-only line (no cognitive headline). Backward-compatible.
- Adoption: `agency_state` live stage stays **`shadow`** (the switch is OFF and
  nothing consumes resume), with the enforce path documented in
  `kernel_adoption` — so the Constitution / matrix are unchanged.
- Tests: `resume_summary` is commitment-only with the switch off (default),
  cognitive-sourced when on, empty under the capture kill-switch, and handles no
  prior snapshot; `continuity_summary(include_cognitive=False)` omits the
  cognitive headline while keeping commitments.

Owner action to enable: set `continuity.CONTINUITY_RESUME_ENFORCE = True` (and,
when a resume-announce consumer exists, wire it to `resume_summary`). Left for
the owner — never flipped autonomously.

Validation: `scripts/audit.py` + all four kernel gates green; agency_state /
continuity unit modules green. Version 8.146.0 → 8.147.0.

## [8.146.0] — Cognitive continuity parity (Phase I-B.3)

Adds the **parity** rung of Phase I-B (Cognitive Continuity): on boot, the
reloaded *cognitive* snapshot (what JARVIS was thinking before a restart) is
reconciled against live cognitive state, and the agreement is logged. Still
observe-only — drives nothing, enforce (`CONTINUITY_RESUME_ENFORCE`) remains a
future owner-gated rung. (I-B.1 pure schema and I-B.2 shadow capture already
shipped earlier.)

- `kernel/agency_state.py`: new pure `reconcile_cognitive(prev, live)` →
  `CognitiveReconcileReport` — field-by-field agreement between two
  `CognitiveContext`s (only fields populated on either side are judged; scalars
  compared directly, list fields as ordered tuples). Pure and total; either side
  may be None.
- `continuity.py`: `boot_reconcile` now also reconciles the cognitive snapshot
  against a freshly-built live cognitive context and logs how many fields
  persisted vs. changed since the restart (naming the changed ones). Best-effort,
  never raises into startup; the commitment reconcile is unaffected.
- Adoption stage unchanged (`agency_state` stays `shadow` — observe-only until a
  resume is made authoritative), so the Constitution / matrix are untouched.
- Tests: `test_agency_state.py` covers `reconcile_cognitive` (agreement, changes,
  empty-field skipping, one-sided populate, both-None); `test_continuity.py`
  covers the boot reconcile logging and its defensiveness.

Validation: `scripts/audit.py` + all four kernel gates green; agency_state /
continuity unit modules green. Version 8.145.0 → 8.146.0.

## [8.145.0] — Uncertainty parity on the identity read (Epistemic Fabric)

Promotes the **Uncertainty** adoption from shadow to **parity** (first of the
Epistemic-Fabric parity tier). Still observe-only, kill-switched, no enforce.

- `identity.resolve()`'s uncertainty emission now also compares whether gating on
  the epistemic band (`Uncertain.is_actionable`, ≥ the believed threshold 0.60)
  would **agree** with the legacy min-confidence "known" decision
  (`confidence ≥ identity_min_confidence`, default 0.45), logging AGREEMENT /
  DIVERGENCE alongside the band. The two disagree in the
  `[min_confidence, actionable)` band — where the legacy gate acts on a verdict
  the fabric would still call a *guess* — so the divergence is expected and
  instructive (the fabric is the more conservative gate). The returned
  `Identification` is unchanged; nothing gates on the band yet.
- `scripts/kernel_adoption.py`: `uncertainty` promoted `shadow → parity`;
  `KERNEL_ADOPTION.md` + Constitution ledger regenerated.
- Tests: `tests/unit/test_identity_uncertainty_shadow.py` gains parity cases — a
  confident read logs AGREEMENT, and a confidence in the in-between band logs
  DIVERGENCE (legacy acts, band withholds).

Validation: `scripts/audit.py` + all four kernel gates green; identity unit
module green. Version 8.144.0 → 8.145.0.

## [8.144.0] — Conflict shadow on the presence read (Epistemic Fabric)

Advances the audit-added **Conflict** primitive (`kernel/conflict.py`, pure
8.101.0) to its first **shadow** rung, at the primitive's canonical case —
contradictory presence sources (the phone says home, the watch says away).
Observe-only, kill-switched, behaviour-preserving.

- `presence.get_presence_summary()` now also adjudicates each person's own
  backing `device_tracker` entities through `kernel.conflict.resolve`: one
  `kernel.provenance.Provenance` per tracker (value = its home/away/zone state,
  source = the tracker entity, confidence 1.0, observed-at from `last_updated`),
  with a reliability prior per `source_type` (gps > router > bluetooth > ble).
  It logs the resolved winner, whether the result is **CONTESTED** (trackers
  disagree within the margin, so the conflict defers), and whether it **AGREES**
  with HA's own `person.state`. HA's resolution still stands — nothing reads the
  adjudicated value. Only runs for a person with ≥2 trackers. Gated by
  `presence.CONFLICT_SHADOW` (default on); the summary dict is identical whether
  the shadow runs or not, and the log-only path never raises.
- `scripts/kernel_adoption.py`: `conflict` promoted `pure → shadow`, owner
  `["presence"]`; `KERNEL_ADOPTION.md` + Constitution ledger regenerated.
- Tests: `tests/unit/test_presence_conflict_shadow.py` — summary identical with
  the shadow on vs. off, agreeing trackers resolve + AGREE, equal-reliability
  disagreeing trackers are CONTESTED, a single-tracker person emits nothing, and
  the emit path is defensive.

Validation: `scripts/audit.py` + all four kernel gates green; presence /
identity / recognition / knowledge / provenance / uncertainty / conflict unit
modules green. Version 8.143.0 → 8.144.0.

## [8.143.0] — Roadmap: Phase Ω opens the v9.0.0 line (owner directive)

Docs-only. Records an owner directive in `docs/KERNEL_PLAN.md`: the v8.x series is
the kernel-migration arc (standing up the spine and walking every subsystem up
the shadow → parity → enforce ladder), and **Phase Ω — Continuous Evolution**
marks the shift from *building* the spine to *evolving* on one that is already
authoritative, landing as **v9.0.0**. No code, primitive, or governance change.

## [8.142.0] — Uncertainty shadow on the identity fusion read (Epistemic Fabric)

Advances the audit-added **Uncertainty** primitive (`kernel/uncertainty.py`, pure
8.100.0) from pure to its first **shadow** rung: identity fusion — a perception
output that already produces a graded belief — now wraps its verdict as a
first-class `Uncertain`. Observe-only, kill-switched, behaviour-preserving.

- `identity.resolve()` now also packages its fused verdict as a
  `kernel.uncertainty.Uncertain` (value = resolved person or `unknown`,
  `confidence` = resolve()'s own fused score, `basis` = the methods that voted,
  `resolver` = what evidence would settle a weak read — a face recognition or
  voiceprint) and logs its epistemic band (`known` / `believed` / `guessed` /
  `unknown`) — an honest *"I believe 'sam' (0.60)"* instead of a bare name. This
  is the primitive's "perception wraps its output" rung. Gated by
  `identity.UNCERTAINTY_SHADOW` (default on); the returned `Identification` is
  identical whether the shadow runs or not, and the log-only path never raises.
- `scripts/kernel_adoption.py`: `uncertainty` promoted `pure → shadow`, owner
  `["identity"]`; `KERNEL_ADOPTION.md` + Constitution ledger regenerated.
- Tests: `tests/unit/test_identity_uncertainty_shadow.py` — verdict identical
  with the shadow on vs. off, a confident read logs the expected band, the
  no-signal path emits nothing, and the emit path is defensive.

Validation: `scripts/audit.py` + all four kernel gates green; identity /
recognition / knowledge / provenance / uncertainty unit modules green. Version
8.141.0 → 8.142.0.

## [8.141.0] — Provenance shadow extends to the recognition read (Epistemic Fabric)

Advances the audit-added **Provenance** primitive (`kernel/provenance.py`, pure
8.99.0; shadow already live for curated facts via `knowledge.all_facts`) one rung
by attaching provenance at a second, cleaner source: the face-recognition read.
Observe-only, kill-switched, behaviour-preserving — no live consumer reads it.

- `recognition.who_is_where()` now also packages each recent recognition it
  returns as a `kernel.provenance.Provenance` (value=name, source=camera entity,
  confidence scaled from the cache's 0..100 percent, observed-at = the sighting's
  own timestamp, a 2 h expiry matching the recognition-cache window) and logs the
  most authoritative *fresh* record via `provenance.select_authoritative`.
  Recognition is the cleanest provenance source: a face read natively carries
  *who*, *which camera*, *when*, and *how confident*. Gated by
  `recognition.PROVENANCE_SHADOW` (default on); the returned
  `{camera_entity: name}` mapping is identical whether the shadow runs or not,
  and the log-only path never raises into the identity read.
- `scripts/kernel_adoption.py`: `provenance` shadow owners now `["knowledge",
  "recognition"]`. Stage unchanged (still shadow), so the Constitution / adoption
  matrix rows are unaffected.
- Tests: `tests/unit/test_recognition_provenance_shadow.py` — read is identical
  with the shadow on vs. off, provenance covers exactly the sightings the read
  surfaces (stale / sub-threshold entries excluded), and the emit path is
  defensive on empty / malformed input.

Validation: `scripts/audit.py` + all four kernel gates green; related recognition
/ identity / knowledge / provenance unit modules green. Version 8.140.0 →
8.141.0.

## [8.140.0] — Integration test: options-update triggers a clean reload

Continues the v8.136.0 external audit's P1 end-to-end coverage (the owner-chosen
priority): one new **real-Home-Assistant** integration test (PHACC) covering a
HA-lifecycle contract not previously exercised. Test-only — no production code
changes.

- `tests/integration/test_wiring_smoke.py`:
  - **`test_options_update_triggers_clean_reload`** — updating the config
    entry's *options* must fire JARVIS's registered update listener, which
    reloads the entry (a full unload+setup cycle), so a settings change takes
    effect without a Home Assistant restart. Unlike
    `test_reload_leaves_integration_loaded` (which calls `async_reload`
    directly), this drives the reload through the real options-changed path, so
    it guards the listener *wiring* itself (`entry.add_update_listener` in
    `async_setup_entry`): drop that line and a settings change would silently
    never apply, and only this test would catch it. After the reload JARVIS is
    asserted fully re-wired (data store repopulated, services re-registered) with
    setup genuinely re-run (provider resolved afresh via a call-count check), and
    nothing leaks across the cycle (PHACC `verify_cleanup`).

Validation: full integration suite (6 tests) green under
`pytest-homeassistant-custom-component` on py3.13; `scripts/audit.py` + all four
kernel gates green (unaffected — test-only). Version 8.139.0 → 8.140.0.

## [8.139.0] — Integration tests: provider-outage safety + restart re-setup

Addresses the v8.136.0 external audit's P1 "measurable governance" / end-to-end
coverage: two new **real-Home-Assistant** integration tests (PHACC), so JARVIS's
HA-lifecycle resilience is backed by executable evidence rather than asserted.
Test-only — no production code changes.

- `tests/integration/test_wiring_smoke.py`:
  - **`test_setup_fails_safely_on_provider_outage`** — when the LLM provider
    cannot be constructed (outage / bad or missing key), `async_setup_entry`
    fails *cleanly*: setup returns False, nothing is left in `hass.data`, no
    services register, and PHACC's `verify_cleanup` confirms no lingering timers.
    JARVIS must never crash HA or partially load when its provider is down.
  - **`test_cold_resetup_after_unload`** — a full unload followed by a fresh
    setup (a restart proxy, distinct from reload which never fully tears down)
    re-initialises cleanly: the data store repopulates, services re-register, and
    nothing leaks across the cycle.

Validation: full integration suite (5 tests) green under
`pytest-homeassistant-custom-component` on py3.13; `scripts/audit.py` + all four
kernel gates green. Real-state verification of actuation remains covered at the
unit layer (`test_actuation.py`). Version 8.138.0 → 8.139.0.

## [8.138.0] — Agency orchestration enforce path (kernel Phase O, owner-gated, default OFF)

Builds the Phase O **enforce** path — the kernel becoming the authority source
for delegated sub-agents — behind `AGENCY_ORCHESTRATION_ENFORCE` **defaulted
OFF**. Per the v8.136.0 external audit (and the owner's "parity, then prep
enforce OFF" choice), this ships the capability *ready to flip*; **nothing in a
live home changes until the owner turns it on**, and even then it is
behaviour-preserving today.

- `agent._agency_enforce(label, allowed, depth) → (ok, effective_allowed, reason)`:
  derives a delegated sub-agent's effective tool set from a kernel
  `agency.spawn` (a strict narrowing of the incumbent-resolved set) and vetoes
  the delegation when `agency.can_spawn` refuses. `_run_delegated` consults it
  only when the switch is on, **after** every incumbent gate (depth, the FRIDAY
  `_profile_enabled` opt-in, capability validity), so it can only ever narrow
  authority — never widen. A kernel veto returns an error to the parent; any
  kernel error fails safe to the incumbent set.
- **Why ON is safe today:** JARVIS holds all capabilities, so the kernel-derived
  set equals the incumbent set and the veto never fires — ON is identical to
  today. The switch exists so the owner can make the kernel authoritative (and
  later scope JARVIS's own token / move gates into the kernel). The owner alone
  flips it.
- Adoption: `agency` stays **parity** as its *live* stage (enforce is OFF);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated with the
  enforce path documented. `docs/KERNEL_PLAN.md` Phase O status updated.

tests: `tests/unit/test_agency_delegation_shadow.py` gains enforce cases — the
switch defaults OFF, the kernel-derived set passes through (identity narrowing)
and never widens, and an over-depth spawn is vetoed. audit + all four kernel
gates green.

## [8.137.0] — Agency orchestration parity (kernel Phase O, parity)

Advances Phase O to the **parity** rung: at each delegation decision point,
JARVIS now logs whether the kernel's `agency.can_spawn` verdict **agrees** with
what the incumbent `_run_delegated` actually does (proceed vs. return an error).
Observe-only, so this release changes nothing JARVIS does.

- `agent._emit_agency_parity`: emitted at the depth guard, the profile-resolve
  refusal, the unknown-capability refusal, and the proceed path, logging
  `agency(parity): child=<holder> depth=<d> kernel_ok=<bool>
  incumbent_proceeded=<bool> agree=<bool> [note]`.
- The kernel models capability-narrowing + depth; the incumbent also enforces
  gates the kernel does **not** yet model — chiefly the FRIDAY opt-in
  (`_profile_enabled`). So a **disabled FRIDAY** is the expected **divergence**
  (kernel would allow the declared tool set, incumbent refuses). That divergence
  is exactly the signal the owner-gated enforce rung must close by sitting behind
  (or encoding) those incumbent gates — captured now as data rather than guessed
  at enforce time. Behind the `AGENCY_PARITY` kill-switch (default on);
  best-effort, never raises; drives nothing.
- Adoption: `agency` advances **shadow → parity** (owner `agent`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Enforce stays
  **owner-gated** (`AGENCY_ORCHESTRATION_ENFORCE`); per the v8.136.0 external
  audit, the enforce path will be built next behind that switch **defaulted off**
  for the owner to flip.

tests: `tests/unit/test_agency_delegation_shadow.py` gains parity cases — proceed
agrees, depth-refuse agrees, a disabled profile diverges (`agree=False`), and the
kill-switch silences it. audit + all four kernel gates green;
`docs/KERNEL_PLAN.md` Phase O status updated.

## [8.136.0] — Agency orchestration shadow (kernel Phase O, shadow)

Advances Phase O to the **shadow** rung: when JARVIS delegates to a sub-agent,
it now dry-runs the equivalent `kernel.agency` spawn and logs whether the kernel
agrees the delegation is legal. Observe-only, so this release changes nothing
JARVIS does — the real delegation path (tool scoping, depth cap, attribution) is
untouched.

- `agent._emit_agency_shadow`: called from `_run_delegated` once the sub-agent's
  resolved tool set, label, and depth are known. It builds JARVIS as a root agency holding all capabilities (at the
  current chain depth) and asks `kernel.agency.can_spawn` whether a child with
  that tool set spawns legally, logging
  `agency(shadow): child=<holder> depth=<d> caps=<n> granted=<n> dropped=<n>
  ok=<bool>`. A FRIDAY/HOMER or capability-group delegation that the incumbent
  runs reads `ok=True`; a depth over `MAX_DELEGATION_DEPTH` reads `ok=False`,
  mirroring the incumbent's depth guard. Behind the `AGENCY_SHADOW` kill-switch
  (default on); best-effort, never raises.
- `kernel.agency.root` gains an optional `depth=` so the shadow can model JARVIS
  at the real chain depth (keeps the logged depth and the legality verdict
  consistent). Backward-compatible (defaults to 0).
- Adoption: `agency` advances **pure → shadow** (owner `agent`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Enforce
  (deriving the sub-agent's token FROM the kernel, one real budgeted sub-task)
  stays **owner-gated** (`AGENCY_ORCHESTRATION_ENFORCE`).

tests: `tests/unit/test_agency_shadow.py` — a legal delegation logs
`ok=True granted=N dropped=0`, a depth-overflow logs `ok=False`, and the
kill-switch silences it. audit + all four kernel gates green;
`docs/KERNEL_PLAN.md` Phase O status updated.

## [8.135.0] — Agency orchestration primitive (kernel Phase O, pure)

Opens roadmap **Phase O — Agency Orchestration** at its **pure** rung: a new pure
primitive for **hierarchical delegation** — a parent agency spawning a child that
carries a strictly narrower capability set, a bounded delegation depth, and a
small lifecycle. Nothing live consumes it yet, so this release changes nothing
JARVIS does.

- `kernel/agency.py`: an immutable `Agency` (holder, capability token, depth,
  parent, scope, objective, lifecycle state).
  - `root(holder, capabilities)` / `spawn(parent, holder, capabilities, …)`: a
    child's token is the parent's `CapabilityToken.derive`d token — capabilities
    **intersected** with the parent's, so **delegation narrows, never widens**
    (the authority engine's no-escalation rule, reused as the single source of
    truth). Depth is `parent.depth + 1`.
  - `can_spawn(parent, capabilities, *, max_depth=3)`: pure verdict — refuses a
    terminal parent, a depth over the bound (mirrors
    `budget.max_delegation_depth`), or a request granting nothing; reports which
    requested capabilities were narrowed away (`dropped`).
  - `transition` / `can_transition`: a bounded, irreversible lifecycle
    `pending → active → settled|failed` — a settled agency can't be revived.
- Adoption: `agency` declared **pure** (no live owner); `KERNEL_ADOPTION.md` +
  `docs/JARVIS_CONSTITUTION.md` regenerated. Shadow dry-runs a spawn alongside
  FRIDAY/HOMER delegation next; enforce (one real budgeted sub-task) is
  **owner-gated** (`AGENCY_ORCHESTRATION_ENFORCE`, fail-safe = parent acts
  directly). Peer coordination (`kernel/coordination.py`) is a later increment.

tests: `tests/unit/test_kernel_agency.py` pins the no-escalation narrowing, the
depth bound, the `can_spawn` verdicts, and the bounded/irreversible lifecycle.
audit + all four kernel gates green.

## [8.134.0] — Graduated autonomy parity (kernel Phase N, parity)

Advances Phase N to the **parity** rung: alongside the would-be per-capability
autonomy level (shadow, 8.133.0), JARVIS now logs whether that level's
auto-execute verdict agrees with the single blanket incumbent it refines — the
active mode's auto-actions flag. Observe-only, so this release changes nothing
JARVIS does.

- `actuation._emit_autonomy_parity`: for the just-acted capability it computes the
  earned level (`kernel.autonomy.grant`) and compares its auto-execute verdict
  (`AutonomyGrant.may_act`) against `modes.mode_allows_auto_actions()` — *"whether
  autonomy graduations may auto-execute under the active mode"* — logging
  `autonomy(parity): capability=<cap> earned=<level> earned_auto=<bool>
  mode_auto_flag=<bool> agree=<bool> (n=…)`. A **divergence** flags a capability
  whose per-capability earned trust disagrees with the one-size-fits-all flag —
  precisely the resolution the enforce rung buys by replacing the blanket flag
  with the earned level. Behind the `AUTONOMY_PARITY` kill-switch (default on);
  best-effort, never raises; drives nothing.
- Per-proactive-pattern trust stays with `cognitive_core`'s `AutonomyManager`
  (acceptance-earned, per pattern); this axis is the blanket mode flag only.
- Adoption: `autonomy` advances **shadow → parity** (owner `actuation`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Enforce
  (`GRADUATED_AUTONOMY_ENFORCE` replacing the blanket flag with the earned level,
  fail-safe = the current single setting) stays **owner-gated**.

tests: `tests/unit/test_actuation.py` gains parity cases — flag on + unearned logs
`agree=False` (divergence), flag off + unearned logs `agree=True`, and the
kill-switch silences it. audit + all four kernel gates green.

## [8.133.0] — Graduated autonomy shadow (kernel Phase N, shadow)

Advances Phase N to the **shadow** rung: on each verified actuation JARVIS now
logs the *would-be* per-capability autonomy level the primitive grants from that
capability's track record — observe-only, so this release changes nothing JARVIS
does and the global autonomy flag is untouched.

- `actuation._emit_autonomy_shadow`: rolls up the same bounded outcome window the
  learning shadow already keeps (`_recent_outcomes`), summarizes the just-acted
  capability's record (`kernel.outcome.summarize`), and logs
  `autonomy(shadow): capability=<cap> risk=<class> level=<suggest|confirm|act>
  (n=…, rate=…)` via `kernel.autonomy.grant`. A SENSITIVE capability reads
  `suggest` until it earns up; a SECURITY capability reads `confirm [pinned]`
  however clean its streak. Behind the `AUTONOMY_SHADOW` kill-switch (default on);
  best-effort, never raises; nothing consumes the level.
- Adoption: `autonomy` advances **pure → shadow** (owner `actuation`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Enforce
  (`GRADUATED_AUTONOMY_ENFORCE` replacing the global autonomy flag with the earned
  level, fail-safe = the current single setting) stays **owner-gated**.

tests: `tests/unit/test_actuation.py` gains shadow cases — a sensitive capability
logs `risk=sensitive level=suggest`, a security capability logs
`risk=security level=confirm [pinned]`, and the kill-switch silences it. audit +
all four kernel gates green.

## [8.132.0] — Graduated autonomy primitive (kernel Phase N, pure)

Opens roadmap **Phase N — Graduated Autonomy** at its **pure** rung: a new pure
primitive that replaces the single autonomy flag with a per-capability autonomy
*level* a capability **earns** on its verified track record. Nothing live consumes
it yet, so this release changes nothing JARVIS does.

- `kernel/autonomy.py`: the level is a pure function of a capability's success
  history (`kernel.outcome.OutcomeStats`) and its risk class
  (`kernel.authority.sensitivity`, reused so there is one risk taxonomy). Ladder
  least → most autonomous: **SUGGEST → CONFIRM → ACT**.
  - `earned_level(stats, risk)` / `grant(capability, stats, *, current=None)`:
    SAFE reads are pinned at ACT; SENSITIVE actuation earns up from SUGGEST on a
    verified record (≥5 outcomes ≥0.60 → CONFIRM; ≥20 ≥0.90 → ACT); **SECURITY
    actuation is pinned at CONFIRM and NEVER auto-promotes** — a flawless streak
    can't earn it the right to act unattended.
  - `step_toward(current, target)`: transitions are **bounded** (one rung per
    evaluation) and **reversible** (a lapse demotes the same way it promoted).
- Adoption: `autonomy` is declared **pure** (no live owner); `KERNEL_ADOPTION.md`
  + `docs/JARVIS_CONSTITUTION.md` regenerated. Shadow (log the would-be level per
  capability) is next; enforce (replace the global flag, fail-safe = the current
  single setting) is **owner-gated**.

tests: `tests/unit/test_kernel_autonomy.py` pins the ladder, the risk
floor/ceiling (safe→act, security→confirm-pinned), the earning thresholds, and the
bounded/reversible transition. audit + all four kernel gates green.

## [8.131.0] — Learned-trust vs success-rate parity (kernel Phase M, parity)

Advances Phase M to the **parity** rung: alongside the would-be per-capability
trust, JARVIS now logs whether that learned trust agrees with the capability's
realized success rate. Observe-only, so this release changes nothing JARVIS does.

- `actuation._emit_learning_parity`: for the just-acted capability it computes the
  learned trust (`kernel.learning.adjust`, 0.5 prior) and the realized success rate
  (`kernel.outcome.summarize`), and logs
  `learning(parity): capability=<cap> trust=X.XX success_rate=Y.YY agree=<bool>
  (n=…)`. Agreement = the learned trust and the raw success rate point the same way
  about the 0.5 midpoint; a divergence flags a capability whose confidence-weighted
  signal disagrees with its hit rate — the case the enforce rung must get right
  before trusting the learned value. Behind the `LEARNING_PARITY` kill-switch
  (default on); best-effort, never raises; drives nothing.
- Adoption: `learning` advances **shadow → parity** (owner `actuation`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Enforce
  (`LEARNING_ENFORCE` updating a real per-capability trust store — itself a Phase N
  concern) stays owner-gated.

tests: `tests/unit/test_actuation.py` gains parity cases — consistent successes log
`success_rate=1.00 agree=True`, and the kill-switch silences it. audit + all four
kernel gates green.

## [8.130.0] — Per-capability trust learning shadow (kernel Phase M, shadow)

Advances Phase M to the **shadow** rung (owner-chosen: per-capability trust from
actuation outcomes). On each verified actuation, JARVIS now logs the *would-be*
per-capability trust adjustment the learning primitive would compute — observe-only,
so this release changes nothing JARVIS does.

- `actuation` keeps a bounded window (`_LEARN_WINDOW`, 50) of the recent
  `kernel.outcome.Outcome` records it already emits (the #237 outcome shadow), and
  `_emit_learning_shadow` rolls up the just-acted capability's track record and logs
  `learning(shadow): capability=<cap> trust 0.50 -> X.XX (delta ±…, signal …, n=…)`
  via `kernel.learning.adjust` (neutral `0.5` prior — the real per-capability trust
  store arrives with Phase N). Behind the `LEARNING_SHADOW` kill-switch (default on);
  best-effort, never raises; nothing consumes the adjustment.
- The learning primitive's guards carry through: adjustments are bounded, reversible,
  and a protected weight could never be relaxed (trust weights here are unprotected).
- Adoption: `learning` advances **pure → shadow** (owner `actuation`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Parity (offline
  accuracy) next; enforce updates a real per-capability trust store behind
  `LEARNING_ENFORCE` (clamped + audited), fail-safe = frozen weights.

tests: `tests/unit/test_actuation.py` gains learning-shadow cases — three verified
actuations push the would-be trust above the neutral prior and log `n=3`, and the
kill-switch silences it. audit + all four kernel gates green.

## [8.129.0] — Learning & adaptation primitive (kernel Phase M, pure)

Opens roadmap **Phase M — Learning & Adaptation** at its first rung: a pure
kernel primitive that turns structured `Outcome` records into **bounded,
reversible** weight adjustments. Additive and observe-only — nothing live
consumes it yet, so this release changes nothing.

- New `kernel/learning.py`:
  - `adjust(key, prior, outcomes, *, step_cap, floor, ceil, protected)` nudges a
    prior weight toward the mean `learning_signal` of the outcomes, scaled so a
    single adjustment moves it by at most `step_cap` and never leaves
    `[floor, ceil]`. Returns a `WeightAdjustment(prior, proposed, delta, signal,
    samples, protected, clamped)` that always retains the prior, so `applied()` /
    `reverted()` are both pure and lossless (nothing is learned irreversibly).
  - `plan_adjustments(priors, outcomes_by_key, …)` does several at once,
    strongest-change first.
  - **Governance invariant, enforced in the math:** a `protected` weight — an
    authority gate or safety threshold — can only *tighten*, never be relaxed
    (clamped to `delta >= 0`), no matter how negative the outcomes' signal is.
- Reuses `kernel.outcome.summarize`; no I/O. **PURE** (Phase M, first rung):
  declared `pure` in `kernel_adoption`, registered in `kernel/__init__.py`
  (`WeightAdjustment`, `adjust_weight`, `plan_adjustments`). Later rungs compute
  would-be adjustments in shadow, compare offline (parity), then update belief
  confidences behind `LEARNING_ENFORCE` (clamped + audited), fail-safe = frozen
  weights.

tests: `tests/unit/test_kernel_learning.py` pins the no-op/totality, the capped
nudge and signal scaling, floor/ceil clamping, the protected never-relax guard
(and that protected weights may still tighten), reversibility, and batch ranking.
audit + all four kernel gates green.

## [8.128.0] — Causal prediction parity on real contingency (kernel Phase L, parity)

Advances Phase L to the **parity** rung (owner-chosen **Option A — event-window**):
the causal predictor is now scored against the **real** cause/effect contingency
reconstructed from state history, replacing the shadow's placeholder baseline.
Observe-only, so this release changes nothing JARVIS does.

- `pattern_analyzer._emit_causal_parity` (run on each `analyze()` pass, on the
  executor thread with the DB open): for the top detected sequence patterns, it
  reconstructs the 2×2 contingency from `state_changes` —
  - **cause-present trials** = each trigger firing; **effect-present** when the
    action fires within the pairing window (`_CAUSAL_PARITY_WINDOW_S`, 600 s);
  - **cause-absent trials** = window-sized bins with no trigger firing;
    **effect-present** when the action fires in that bin —
  builds a `kernel.causal.CausalHypothesis(n11,n10,n01,n00)`, and logs
  `causal(parity): N sequence pattern(s) re-scored on real contingency;
  causal-confirms=C, diverges=D … pattern-conf=… causal-ΔP-conf=…`. The divergence
  count is the signal: patterns that are strong co-occurrences but are explained
  away by the effect's base rate (ΔP ≈ 0) are the ones the predictor should *not*
  trust. Behind the `CAUSAL_PREDICT_PARITY` kill-switch (default on); best-effort,
  never raises.
- New pure helper `_causal_contingency(cause_epochs, effect_epochs, window)` does
  the event-window tally; `_causal_firing_epochs` reads a `(entity, state)`
  firing series from history.
- Adoption: `causal` advances **shadow → parity** (owner `pattern_analyzer`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Enforce
  (`CAUSAL_PREDICT_ENFORCE`, gating a proactive path, fail-safe = reactive only)
  stays owner-gated.

tests: `tests/unit/test_causal_shadow.py` gains parity cases — the event-window
contingency tally (incl. empty/total), and `_emit_causal_parity` re-scoring a
genuine cause from a seeded in-memory history with the kill-switch / no-sequence
paths silent. audit + all four kernel gates green.

## [8.127.0] — Camera + sensor coverage parity (kernel Phase Q, parity)

Broadens the Phase Q **parity** evidence to the **camera↔sensor mapping**, per the
owner decision to corroborate area coverage with **both cameras and sensors**.
Observe-only, so this release changes nothing JARVIS does.

- `cognitive_core._emit_space_time_coverage_parity`: each cognitive tick, for the
  areas the space/time model knows (the floor-plan `SpatialGraph`), counts how
  many a camera covers (static, `camera_coverage.camera_for_area`) and how many a
  presence sensor reports occupied right now (live,
  `audio_routing.currently_occupied_areas`), plus the overlap where a
  sensor-occupied area is also camera-corroborated. Logs a one-line
  `space_time(parity): model=N area(s), cam-covered=C; occupied-now=K (in-model=M,
  cam-corroborated=X)`. Behind the `SPACE_TIME_COVERAGE_PARITY` kill-switch
  (default on); best-effort, never raises.
- This is the camera↔sensor side of Phase Q parity (cf. #140): it surfaces how
  well the two observation sources corroborate the spatial model's areas, as
  evidence toward enforce. `space_time` stays at **parity** — nothing reads the
  model, the current per-feature mapping stays authoritative, and the
  `SPACE_TIME_ENFORCE` behaviour flip remains the household's call.

tests: `tests/unit/test_space_time_shadow.py` gains coverage-parity cases — the
combined cam+sensor summary is logged with the right counts, and the kill-switch /
empty-model paths stay silent. audit + all four kernel gates green (no adoption or
doc change — `space_time` was already parity).

## [8.126.0] — Causal prediction shadow via pattern_analyzer (kernel Phase L, shadow)

Advances Phase L to the **shadow** rung: the pattern analyzer now feeds the
causal model from the sequence patterns it already detects and logs a one-line
summary — observe-only, so this release changes nothing JARVIS does.

- `pattern_analyzer._emit_causal_shadow`: at the end of `analyze()`, each detected
  **sequence** pattern (`<trigger> then <action>`) is folded into a
  `kernel.causal.CausalModel` as a `cause → effect` hypothesis (cause = the
  trigger entity/state, effect = the action entity/state), and a one-line
  `causal(shadow): N hypothesis(es) from M sequence pattern(s); predict() surfaces
  K cause(s); strongest … -> … (conf …)` summary is logged. Behind the
  `CAUSAL_PREDICT_SHADOW` kill-switch (default on); best-effort, never raises.
- This exercises the Phase L `predict()` surface over real learned relationships.
  **First-rung caveat:** a sequence pattern carries only the co-occurrence count,
  not the full cause-present/absent × effect-present/absent contingency, so the
  shadow pairs each co-occurrence with an equal "effect does not occur without the
  cause" baseline purely so ΔP is defined. That baseline is a shadow-only
  placeholder and the confidence is provisional; the **parity** rung replaces it
  with the real per-trial contingency from state history. Nothing reads the model.
- Adoption: `causal` advances **pure → shadow** (owner `pattern_analyzer`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.

tests: new `tests/unit/test_causal_shadow.py` pins the summary line, the
kill-switch, and defensiveness (non-sequence patterns ignored, malformed/empty
input never raises); the existing `pattern_analyzer` suite still passes. audit +
all four kernel gates green.

## [8.125.0] — Causal prediction surface (kernel Phase L, pure)

Opens roadmap **Phase L — Prediction & Causal Reasoning** at its first rung: the
causal primitive (`kernel/causal.py`) gains a pure forward-looking query surface.
Additive and observe-only — nothing live consumes it yet, so this release changes
nothing.

- `kernel/causal.py` — two pure reads over the existing ΔP contingency tally:
  - `CausalModel.predict(context)` — the effects the causes present in `context`
    make likely. Each present, positively-causal hypothesis predicts its effect;
    when several present causes drive one effect the strongest confidence wins and
    every contributing cause is listed. Returns `Prediction(effect, confidence,
    causes)` strongest-first.
  - `CausalModel.explain(effect)` — the candidate causes that best account for an
    observed effect, as `Explanation(cause, confidence)` strongest-first.
  - Both filter by a `min_confidence` threshold (default 0.05, matching the
    causal-direction threshold) and never raise on messy/empty input.
- **PURE** (Phase L, first rung): `causal` stays `pure` in `kernel_adoption`
  (nothing consumes the predictor yet); `Prediction` / `Explanation` are exported
  from `kernel/__init__.py`. Later rungs log predicted-vs-actual (shadow), measure
  accuracy over real traffic (parity), then gate a proactive path on prediction
  confidence behind `CAUSAL_PREDICT_ENFORCE` with reactive-only as the fail-safe.

tests: `tests/unit/test_kernel_causal.py` gains predictor cases — `predict` ranks
present causes and excludes absent ones, merges multiple causes for one effect,
respects the threshold and empty context; `explain` ranks an effect's causes. audit
+ all four kernel gates green.

## [8.124.0] — Space/time breach-depth parity (kernel Phase Q, parity)

Advances Phase Q to the **parity** rung for the spatial model: the intrusion
investigator now computes the breach-depth map from the kernel `SpatialGraph`
alongside its incumbent `residence_graph.hops_from_breach` and logs whether they
agree — observe-only, so this release changes nothing JARVIS does.

- `cognitive_core._emit_space_time_parity`: for a breach area, builds the kernel
  `SpatialGraph` over the same floor-plan adjacency and compares its
  `hops_from(breach)` (room-slug → depth) against the incumbent's result, logging
  `space_time(parity): breach-depth agree=… (kernel=N room(s), legacy=M room(s))`.
  Behind the `SPACE_TIME_PARITY` kill-switch (default on); best-effort, never
  raises. Called from the intrusion path right after `hops_from_breach` — the
  investigation is unchanged whether it runs or not.
- This isolates the kernel BFS (`SpatialGraph.hops_from`) as a faithful
  re-implementation of the incumbent before any enforce rung reads the model. It
  deliberately does **not** touch the camera↔sensor mapping (cf. #140): that
  parity and the `SPACE_TIME_ENFORCE` flip (presence/coverage/routing reading the
  model authoritatively, fail-safe = present mapping) are later, owner-gated rungs.
- Adoption: `space_time` advances **shadow → parity** (owner `cognitive_core`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated.

tests: `test_space_time_shadow.py` gains parity cases — agreement logged,
divergence logged, and the kill-switch / no-breach paths stay silent. audit +
all four kernel gates green.

## [8.123.0] — Space/time view + shadow (kernel Phase Q, shadow)

Advances Phase Q to the **shadow** rung: the kernel space/time primitive now has
a live view on the world-model facade and is observed on every cognitive tick —
still observe-only, so this release changes nothing JARVIS does.

- `kernel/world_model.py` gains a space/time view:
  - `spatial_graph(config=None)` — the home's floor plan as a
    `kernel.space_time.SpatialGraph`, built from the same floor-plan adjacency the
    intrusion investigator already derives (`residence_graph.room_adjacency`).
  - `temporal_frame(now=None)` — the current moment as a
    `kernel.space_time.TemporalFrame` (hour + weekday → coarse daypart), from the
    local wall clock by default. Both best-effort: an empty graph / unknown frame
    on any failure.
- `cognitive_core._tick` builds both views each tick and logs a one-line
  `space_time(shadow): N area(s), M adjacency(ies); daypart=…` summary, behind the
  `SPACE_TIME_SHADOW` kill-switch (default on). Observe-only — nothing reads the
  model authoritatively; the current per-feature mapping (`residence_graph`,
  `camera_coverage`, the briefing schedule) stays the source of truth.
- Adoption: `space_time` advances **pure → shadow** (owner `cognitive_core`);
  `KERNEL_ADOPTION.md` + `docs/JARVIS_CONSTITUTION.md` regenerated. Parity next
  (camera↔sensor mapping, cf. #140), then enforce behind `SPACE_TIME_ENFORCE`.

tests: `test_kernel_world_model.py` gains the space/time view cases
(`spatial_graph`/`temporal_frame`, incl. best-effort-on-error); new
`test_space_time_shadow.py` pins the tick summary, the kill-switch, and that the
emitter never raises. audit + all four kernel gates green.

## [8.122.0] — Spatial & temporal model primitive (kernel Phase Q, pure)

Opens roadmap **Phase Q — Embodied JARVIS** at its first rung: a pure kernel
primitive for first-class **space** and **time**. Additive and observe-only —
nothing in the live integration consumes it yet, so this release changes nothing.

- New `kernel/space_time.py`:
  - `SpatialGraph` — an immutable floor-plan graph of areas and undirected
    adjacency, built from an adjacency map (`{area: iterable(neighbor)}`, the
    exact shape `residence_graph.room_adjacency` already emits), with a small
    pure, case-insensitive query surface: `neighbors`, `adjacent`, `hops_from`
    (BFS distance by area, the pure counterpart of
    `residence_graph.hops_from_breach`), `distance`, and `within(radius)`.
    `from_adjacency` is total — it drops blank names, self-loops and duplicate
    edges and never raises on messy input.
  - `TemporalFrame` — an hour+weekday resolved to a coarse `daypart` (night /
    morning / midday / afternoon / evening / late_night, boundaries matching the
    integration's existing daypart sense), with `is_weekend` / `is_daytime` and a
    standalone `daypart_of(hour)` helper. Unknown input yields an explicit
    "unknown" frame rather than a false value.
- **PURE** (Phase Q, first rung): declared `pure` in `kernel_adoption` and
  registered in `kernel/__init__.py`; exempt from the live-owner check because
  nothing consumes it yet. Later rungs wire a space/time view onto `world_model`
  in shadow, then parity (camera↔sensor mapping, cf. #140), then enforce behind
  `SPACE_TIME_ENFORCE` with the current per-feature mapping as the fail-safe.

tests: `tests/unit/test_kernel_space_time.py` pins the daypart boundaries, the
temporal frame (including total-on-garbage), undirected/de-duped graph
construction, the case-insensitive queries, BFS hops/distance/radius, and that
both structures are frozen and hashable. audit + all four kernel gates green.

## [8.121.0] — Graph-authoritative context, gated off (kernel Phase T, enforce wiring)

Wires the Phase T **enforce** path — the knowledge graph becoming authoritative
for the conversation's injected knowledge — but ships it **OFF**, so this
release changes nothing until the household flips the switch.

- New `knowledge._graph_expand_facts`: 1-hop relation expansion — for each
  subject in the recall-seeded facts, it pulls in facts about directly-related
  entities (in/out edges) through the knowledge graph, de-duped and capped
  (`_GRAPH_EXPAND_CAP`, 6). The seed facts always come first and are never
  dropped; any failure returns the seed unchanged.
- `knowledge.prompt_block_async` consults it **only when `KNOWLEDGE_GRAPH_ENFORCE`
  is True** (default **False**): then the curated-knowledge block the LLM sees
  is graph-authoritative with 1-hop expansion; otherwise it is byte-for-byte the
  current recall block. Fail-safe: an empty or failed expansion falls back to the
  recall block, so flipping the switch can only add context, never lose today's.
- **Owner-gated, behaviour-preserving as shipped.** The kill-switch is off, so
  the `graph` adoption stays `shadow`; flipping `KNOWLEDGE_GRAPH_ENFORCE` to True
  is what advances it to `enforce` (and is the household's call — it's a live
  change to what JARVIS knows per turn). No kernel-primitive/actuation path is
  touched; the bypass gate is unaffected.

tests: `tests/unit/test_knowledge.py` gains the enforce cases — `_graph_expand_facts`
pulls a related entity's facts one hop, respects the cap, and is defensive on a
failing relations read; and `prompt_block_async` is plain recall with the switch
off but expands one hop with it on. audit + all four kernel gates green.

## [8.120.0] — Knowledge-graph view + shadow (kernel Phase T, shadow)

Advances Phase T to the **shadow** rung: a graph view over the curated knowledge
is now available and exercised, but nothing reads it authoritatively yet.

- **`kernel/world_model.py`** gains the graph view the plan calls for:
  `knowledge_graph(subject)` folds the `facts()` + `relationships()` it already
  reads into a `kernel.graph.KnowledgeGraph`, and `entities()` / `relations()` /
  `query()` answer context queries over it. Best-effort (empty graph on any
  failure). SHADOW — available + unit-tested, consumed by nothing.
- **`knowledge.all_facts()`** now also folds the live facts + relation edges into
  a `KnowledgeGraph` and logs a one-line summary (`_emit_graph_shadow`,
  kill-switch `GRAPH_SHADOW`) — observe-only, exactly like the provenance shadow
  beside it. The returned fact rows are unchanged whether it runs or not, so
  behaviour is preserved.
- `graph` adoption flips `pure → shadow` (owner `knowledge`); `kernel/__init__.py`
  exports it (and `KnowledgeGraph`/`Entity`/`Relation`/`Attribute`);
  KERNEL_ADOPTION.md + the Constitution ledger regenerated.
- Still **no authoritative read** of the graph: parity (graph vs current
  semantic recall) is next, and the enforce flip is gated behind
  `KNOWLEDGE_GRAPH_ENFORCE` with present recall as the fail-safe.

tests: `tests/unit/test_kernel_world_model.py` gains the graph-view cases
(entities / relations / query, attribute lookup, best-effort on a failing
store); `tests/unit/test_knowledge.py` gains the shadow cases (all_facts
behaviour-preserving + logs the summary, and a failing relations read never
breaks all_facts). audit + all four kernel gates green.

## [8.119.0] — Knowledge-graph kernel primitive (kernel Phase T, pure)

Opens roadmap **Phase T — Deep World Model** at the first rung of the ladder
(pure): a typed, queryable knowledge graph as a kernel primitive.

- New `kernel/graph.py` — a pure, immutable `KnowledgeGraph` of **entities**
  (nodes with attributes) and typed directed **relations** (edges). Built from
  plain `knowledge.py`-shaped rows (`{subject,key,value,confidence,source}`
  facts, `{subject,predicate,object,confidence}` relations) via
  `KnowledgeGraph.from_rows`, with a small case-insensitive query surface:
  `entity()`, `relate(subject/predicate/object)`, `neighbors()`, `is_empty()`.
  Construction is total (messy rows skipped, duplicate edges de-duped, bad
  confidences defaulted) and does no I/O.
- **PURE — nothing live consumes it yet, so behaviour is entirely unchanged.**
  The later rungs wire a graph view onto `kernel/world_model.py`
  (`entities`/`relations`/`query`) in shadow, then parity against the current
  semantic recall, then enforce behind a `KNOWLEDGE_GRAPH_ENFORCE` kill-switch
  with the present recall as the fail-safe — none of which lands here.
- Declared `graph: pure` in `scripts/kernel_adoption.py`; KERNEL_ADOPTION.md
  regenerated.

tests: new `tests/unit/test_kernel_graph.py` — construction (entities, implied
relation endpoints, attribute sort), totality on messy input, de-dup, the
query surface (relate / neighbors / entity, all case-insensitive), empty graph,
and frozen/hashable invariants. audit + all four kernel gates green.

## [8.118.0] — Continuity records who JARVIS was serving (kernel Phase I-B)

Adds the `identity` cognitive field — the **last-identified principal** — so the
agency snapshot records who JARVIS was serving, not just what it was thinking.
This is the owner-chosen source of truth for `identity` (last identified, per the
decision to use "last identified" rather than "currently present"). With it,
Phase I-B's cognitive capture is complete for every field that has a defined
source; `attention` is deliberately deferred to Phase K (Attention & Working
Memory), which will build a durable focus model.

- New `continuity._live_identity`: the most recently recognised **known** person
  from `recognition.recent_faces` (newest first), skipping unknown sightings and
  best-effort low-confidence guesses (a guess must never stand in for who JARVIS
  is serving — the same safety posture used elsewhere). Rendered as the name
  (+ ` (resident)` for a flagged household resident) with a `(seen …)` recency
  suffix when a real timestamp exists; sensor rows with a sentinel age show the
  name alone. New `_ago` helper formats the recency.
- `_live_cognitive` now fills `CognitiveContext.identity`.
- **Shadow / observe-only, behaviour-preserving.** `agency_state` stays at the
  `shadow` adoption stage. The read is defensive (empty without a live `hass`
  and on any failure) and runs only on the executor thread via `capture_now`.
  `recognition` is a non-kernel module, so the adoption matrix is unchanged.

tests: `tests/unit/test_continuity.py` gains identity cases — newest known
picked, unknown / low-confidence skipped, sentinel age omitted, empty without
hass / when only unknown, defensive on failure, and a capture test attaching +
persisting the identity. audit + all four kernel gates green.

## [8.117.0] — Continuity records autonomy posture + pending learning (kernel Phase I-B)

Completes the cleanly-sourced Phase I-B cognitive fields: the snapshot now also
captures a *restricted* `autonomy` posture and the count of `learning`
suggestions waiting on review — the last two I-B fields with an unambiguous live
source. (The remaining fields — `identity`, `attention` — need an owner decision
on their source of truth and are intentionally left for later.)

- New `continuity._live_autonomy`: surfaces `auto-actions suppressed` **only**
  when the active mode forbids autonomy graduations (`modes.mode_allows_auto_actions`
  is False); empty in the default/permissive case, so an otherwise-bare snapshot
  stays byte-identical to before.
- New `continuity._live_learning`: `N suggestion(s) pending review` from
  `pattern_analyzer.get_analyzer().get_pending_suggestions()`; empty when none.
- `_live_cognitive` now fills `CognitiveContext.autonomy` and `.learning`.
- **Shadow / observe-only, behaviour-preserving.** `agency_state` stays at the
  `shadow` adoption stage. Both reads are defensive (empty on any failure); the
  learning read (sqlite) runs only on the executor thread via `capture_now`. No
  kernel-primitive owners changed (`modes` / `pattern_analyzer` are non-kernel),
  so the adoption matrix is unchanged.

tests: `tests/unit/test_continuity.py` gains autonomy (restricted / permissive /
defensive) and learning (count / singular / none / defensive) cases, plus a
capture test attaching + persisting both. audit + all four kernel gates green.

## [8.116.0] — Continuity remembers what was mid-execution (kernel Phase I-B)

Extends the Phase I-B cognitive snapshot with the `execution` field: what JARVIS
was *mid-doing* at snapshot time, read from the kernel execution journal (H4) —
the steps that were started but not finished, which is exactly what a restart
would need to resume.

- New `continuity._live_execution`: reads `ExecutionJournal.in_flight()` and
  renders a short line — `running: <action>` for a single step, or
  `N steps running; e.g. <action>` for several. `_live_cognitive` now fills
  `CognitiveContext.execution`.
- **Shadow / observe-only, behaviour-preserving.** `agency_state` stays at the
  `shadow` adoption stage. The journal read runs only on the executor thread
  (`capture_now` is dispatched via `async_add_executor_job`), so the sqlite read
  never touches the event loop; it is empty when nothing is in flight and on any
  failure. With no goal, beliefs *and* nothing in flight, the context is still
  dropped, so a bare snapshot stays byte-identical to before.
- `continuity` now appears as a `journal` reader in the adoption matrix
  (regenerated via `kernel_docs_sync --write`); no `_DECLARED` stage change.

tests: `tests/unit/test_continuity.py` gains the execution cases (single step,
multiple steps, empty when nothing in flight, defensive on a failing journal,
and the snapshot capturing + persisting execution). The `cont` fixture now
resolves each kernel DB (`agency.db` / `journal.db` / `situations.db`) to its
own tmp file, matching production — each `kernel.persistence` DB keeps its own
schema-version row, so they must not share one file. audit + all four kernel
gates green.

## [8.115.0] — Continuity remembers its salient beliefs too (kernel Phase I-B)

Extends the Phase I-B cognitive snapshot: alongside `intent` + `plan`, the
agency snapshot now also captures JARVIS's **salient beliefs** — the most
confident knowledge facts, from the WorldModel belief view (E1) — so after a
restart the boot continuity line can reflect not just what JARVIS intended but
what it believed about the home.

- New `continuity._live_beliefs`: reads `WorldModel(hass).beliefs()`, drops the
  generic identity self-belief, ranks the rest by confidence and caps the count
  (top 5), rendering each as `"<proposition> (p=0.87)"`. `_live_cognitive` now
  fills `CognitiveContext.beliefs` from it and builds the context whenever
  *either* a goal (intent/plan) *or* beliefs are present.
- **Shadow / observe-only, behaviour-preserving.** `agency_state` stays at the
  `shadow` adoption stage. The belief read runs only on the executor thread
  (`capture_now` is dispatched via `async_add_executor_job`), so the
  knowledge-store read never touches the event loop; it is empty without a live
  `hass` and on any failure. With neither a goal nor beliefs, the cognitive
  context is still dropped, so a bare snapshot stays byte-identical to before.
- `continuity` now appears as a `world_model` reader in the adoption matrix
  (regenerated via `kernel_docs_sync --write`); no `_DECLARED` stage change.

tests: `tests/unit/test_continuity.py` gains the belief cases — identity
dropped + ranked + rendered, count capped at `_MAX_BELIEFS`, empty without hass,
defensive on a failing belief view, and the snapshot capturing + persisting
beliefs with no active goal. audit + all four kernel gates green.

## [8.114.0] — Continuity captures what JARVIS was thinking (kernel Phase I-B)

After a restart, JARVIS's boot continuity line can now say what it was *thinking*,
not just what it had committed to. The periodic/boot snapshot
(`continuity.capture_now`) now builds the Phase I-B **cognitive context** — the
`intent` and chosen `plan` — from JARVIS's primary active goal and attaches it
to the agency snapshot, so `boot_summary` surfaces e.g.
`intent=house is warm by 7am · plan=1/2 done; next: close the blinds`.

- New `continuity._live_cognitive` (and `_plan_summary`): reads the soonest-due
  active goal and fills `CognitiveContext.intent` (its outcome, or title) and
  `.plan` (step progress + next pending step). Only those two fields are
  sourced; the rest of the context maps to subsystems that are still pure or
  not yet built (delegations → Phase O, uncertainty → pure, …) and stay empty.
- **Shadow / observe-only, behaviour-preserving:** `agency_state` stays at the
  `shadow` adoption stage (it drives nothing). When there is no active goal the
  cognitive context is dropped, so a commitment-only snapshot is byte-identical
  to before. Every read is defensive — a failing goal reader yields no context,
  never an exception into the capture or boot path. Kill-switch
  `AGENCY_CAPTURE_ENABLED` still disables the whole binder.

tests: `tests/unit/test_continuity.py` gains the I-B cases — intent/plan built
from the primary goal (outcome preferred, title fallback), `_plan_summary`
shapes, defensive-on-reader-failure, the snapshot attaching + persisting the
context through the store and reaching the boot line, and the no-goal snapshot
staying commitment-only. audit + all four kernel gates green.

## [8.113.0] — Presence requires a person, not a parked car (issue #254)

A parked car (or a passing animal, or a delivered package) seen by a Frigate /
ONVIF camera no longer marks an area as occupied by a *human*. Those cameras
expose a per-object-class `binary_sensor` for every label
(`binary_sensor.garage_car_occupancy`, `…_dog_motion`, `…_package_detected`)
alongside the per-person one; they turn `on` for the object, not for a person,
so counting them as presence made JARVIS treat the garage as "occupied"
whenever a car was parked there (reported in discussion #221 by
@televisorsaal-ai).

- New conservative classifier `entity_filter.is_nonperson_object_sensor` — it
  recognises a camera object-class sensor for a **non-person** object (vehicle /
  animal / package) only when a known label sits in the Frigate/ONVIF sensor
  shape (the label token right before an occupancy/motion/presence suffix, or as
  the final token). An ordinary room sensor (`binary_sensor.kitchen_presence`),
  an mmWave presence sensor, or anything naming `person` is never matched.
- The **human-presence** paths skip those sensors: `presence.get_presence_summary`
  room detection and `audio_routing` (`presence_entities_in_area`,
  `all_areas_with_presence`, `anyone_home`). The person case and real room
  occupancy are unchanged.
- Deliberately scoped: the pattern-learning / automation-suggestion engine still
  sees car sensors (learning "when the car is in the bay, …" is legitimate), and
  the safety/intrusion motion path is untouched (presence must fail toward
  alerting). The raw-camera-motion symptom (`binary_sensor.*_motion` tripping on
  a leaf) is a follow-up — it is genuinely config-policy (some homes use PIR
  motion as presence) and will land as its own opt-in toggle.

tests: new `tests/unit/test_presence_person_254.py` (classifier matrix + car
excluded / person kept in `get_presence_summary`, and `anyone_home` ignoring a
parked car while still true for real presence or a person entity). audit + all
four kernel gates green; the suggestion-engine tests that rely on car sensors
still pass unchanged.

## [8.112.0] — Don't offer control JARVIS doesn't have (issue #247)

JARVIS no longer offers to close a window or unlock a door it has no actuator
for (e.g. a window shown only by a `binary_sensor`, or an "unlock" when there is
no `lock.*` entity).

- Every task system prompt (briefings, arrival greetings, camera notes, …) now
  carries a **capability-honesty** directive: offer a physical action only when
  Home Assistant actually has a device/service for it; a door/window shown only
  by a sensor is read-only — report it, never offer to close/open/lock/unlock it.
- The briefing's open-things list is annotated truthfully: a `binary_sensor`
  door/window/garage is marked *"monitored only — no actuator to close it"*
  unless a real `cover`/`lock` of the same name backs it; locks stay actionable.
- Behaviour-preserving otherwise: the same items are still reported; only the
  framing/offers change.

tests: new `tests/unit/test_capability_offers_247.py` (4 cases — sensor-only
marked non-actionable, lock stays actionable, cover-backed sensor stays
actionable, closed/locked things absent) + a `build_system_prompt` capability
assertion; existing plain-briefing test updated to the annotated output. audit +
all four kernel gates green.

## [8.111.0] — Cognitive continuity schema (kernel Phase I-B, pure)

Opens Phase I-B (cognitive continuity): the agency snapshot can now carry what
JARVIS was *thinking*, not just what it committed to.

- New pure `kernel.agency_state.CognitiveContext` — optional, all-empty-default
  fields for identity / intent / plan / authority / autonomy / execution /
  learning (scalars) and beliefs / attention / delegations /
  pending-verifications / uncertainty / causality (lists). `AgencyState` gains an
  optional `cognitive` slot; `capture(cognitive=…)` attaches it and
  `continuity_summary()` surfaces intent + plan when present.
- **Backward-compatible / behaviour-preserving:** an empty or omitted context is
  dropped, so a commitment-only snapshot serializes **byte-identically** to I-A,
  pre-I-B snapshots load with `cognitive=None`, and `continuity.py` still captures
  commitment-only (nothing populates the cognitive half yet — that is the I-B
  shadow rung). No adoption-stage change.

tests: five new `tests/unit/test_agency_state.py` cases (round-trip; empty
dropped + byte-identical; old snapshot still loads; summary surfaces intent/plan;
`is_empty`). audit + all four kernel gates green.

## [8.110.0] — Identity Fabric earns parity (kernel #237, Phase I½2)

The Identity & Trust Fabric moves `shadow → parity`: the kernel verdict is now
computed alongside the legacy identity read and checked for agreement.

- `identity.resolve()` runs the fabric resolver over its emitted
  `IdentityAssertion` and logs an **AGREEMENT / DIVERGENCE** flag against the
  legacy "known person" decision — still **observe-only**, drives nothing,
  kill-switched (`IDENTITY_FABRIC_SHADOW`).
- The fabric is **expected to diverge** exactly where the legacy resolver calls
  a confident *presence* prior (sole-occupant / room / proximity) "known": the
  fabric withholds identity from presence (**identity ≠ presence**), and that
  divergence is logged explicitly. This is the pre-enforce evidence that the
  legacy path over-trusts presence as identity.
- Adoption stage flipped `shadow → parity` (owner `identity`); adoption matrix /
  Constitution ledger regenerated.
- Behaviour-preserving: `resolve()` returns exactly the same `Identification`.

tests: two new `tests/unit/test_identity.py` parity cases (face → AGREEMENT;
presence-only → DIVERGENCE with the identity≠presence note). audit + all four
kernel gates green.

Note on the other Fabric primitives: `outcome` and `provenance` shadow values
are derived identically to the legacy read, so a meaningful parity check needs
an independent consumer (Phase M for outcome; conflict-resolution for
provenance) — their parity rungs are deferred to those phases rather than
logging trivial always-agreement.

## [8.109.0] — Identity Fabric wired to shadow (kernel #237, Phase I½2)

The Identity & Trust Fabric primitive earns its **shadow** rung — the last of
the three #237 shadow-wirings. The identity resolver now packages its verdict as
a structured assertion.

- `identity.resolve()` emits a `kernel.identity_fabric.IdentityAssertion` for its
  best-guess person (subject, method, confidence, expiry, evidence) and logs it —
  **log-only**, nothing consumes it yet (parity against the current identity read
  next, then enforce on one identity-sensitive path, owner-gated). One-line
  kill-switch `IDENTITY_FABRIC_SHADOW`.
- **Encodes identity ≠ presence**: only a recent **face** or **voiceprint**
  maps to an identifying method; sole-occupant / room / proximity map to
  `METHOD_PRESENCE`, so they are emitted as *someone is here*, never *who* —
  such an assertion can never `establishes_identity`.
- Adoption stage flipped `pure → shadow` (owner `identity`); Constitution ledger
  / adoption matrix regenerated.
- Observe-only and behaviour-preserving: `resolve()` returns exactly the same
  `Identification` either way.

tests: four new `tests/unit/test_identity.py` cases (face → identifying +
establishes identity; presence-only → not identity; kill-switch silences;
resolution unchanged either way). audit + all four kernel gates green.

With this, all three #237 shadow-wirings (`outcome`, `provenance`,
`identity_fabric`) are live and observe-only.

## [8.108.0] — Provenance primitive wired to shadow (kernel #237)

The Epistemic-Fabric **provenance** primitive earns its **shadow** rung: the
world model now packages where each fact came from.

- `knowledge.all_facts()` builds a `kernel.provenance.Provenance` per curated
  fact (value + `source` + `confidence` + `model`) and logs a one-line summary —
  **log-only**, nothing consumes it yet (conflict resolution and authoritative
  reads attach once it earns parity). `WorldModel.provenances()` exposes the
  same view on demand. One-line kill-switch `PROVENANCE_SHADOW`.
- Adoption stage flipped `pure → shadow` (owner `knowledge`); Constitution
  ledger / adoption matrix regenerated.
- Observe-only and behaviour-preserving: `all_facts()` returns exactly the same
  rows as before, kill-switch on or off.

tests: four new `tests/unit/test_kernel_world_model.py` cases (facts wrap to
Provenance with value/source/confidence; bad rows skipped + default source;
best-effort empty on DB error; `facts()` rows unchanged either way). audit +
all four kernel gates green.

## [8.107.0] — Outcome primitive wired to shadow (kernel #237)

The Epistemic-Fabric **outcome** primitive earns its **shadow** rung: every
verified actuation now also emits a structured `kernel.outcome.Outcome`.

- `actuation.outcome()` builds an `Outcome` via `from_verification` alongside
  the canonical `ActuatorOutcome` — same verdict, with intended end-state
  (`expected_outcome`), observed read-back, actor/capability/correlation, and a
  distilled `learning_signal` in `[-1, 1]`. **Log-only**; nothing reads it yet
  (Phase M will). One-line kill-switch `OUTCOME_SHADOW` silences it.
- Adoption stage flipped `pure → shadow` (owner `actuation`); Constitution
  ledger regenerated.
- Observe-only and behaviour-preserving: the existing `ActuatorOutcome`
  recording and every actuation path are unchanged.

tests: four new `tests/unit/test_actuation.py` cases (success verdict emits;
failure verdict → non-positive learning signal; kill-switch silences; a `None`
request never raises). audit + all four kernel gates green.

## [8.106.0] — Room light pill lists the lights before toggling (issue #234)

Clicking a room's "N lights on" pill used to turn every light in the room off.
Now it opens a popover listing each light with its own toggle — and keeps a
"Turn all off" action.

- New `jarvis/area_lights` WS endpoint returns the individual light entities in
  an area with on/off state, resolved through the same `_entities_in_area` path
  the room light count uses (one source of truth; respects excluded entities).
- The pill now opens a **per-light popover**: each light is a row that toggles
  just that light (`light.toggle` via its own `entity_id`), with a **Turn all
  off** button at the foot that preserves the old one-tap behaviour.
- Fetched on demand at click time (not added to the 5s panel poll), so the list
  is fresh and the poll stays lean. Read-only endpoint; toggles are plain HA
  service calls from the frontend.

tests: ten `scripts/smoke_panel.js` cases (pill opens the popover instead of
turning all off; per-light rows render; per-light on/off state; "turn all off"
preserved; a row toggles only its light; closes on ✕). `node --check` + smoke
clean; audit + all four kernel gates green.

## [8.105.0] — Departure alerts only for events with a location (issue #233)

"Time to head out" nudges now fire only for calendar events you physically
travel to — online meetings and events with no location set are skipped.

- New **departure_require_location** config toggle (Proactive settings,
  "↳ Only for events with a location"), **ON by default**. A location counts as
  physical when it's non-empty and not a video-call link / "online" marker
  (Zoom, Google Meet, Teams, Webex, a URL, "online/virtual/video call/phone
  call/dial-in", …).
- Turn the toggle **OFF** to restore the previous behaviour (alert for every
  timed event, using travel time when a location is present else the default
  lead).
- Behaviour change on upgrade: location-less departure alerts stop by default,
  per the requested behaviour. Everything else about the departure path (lead
  computation, travel sensor/OSRM routing, per-event-per-day dedup) is
  unchanged.

tests: six new `tests/unit/test_departure.py` cases (physical/virtual
classification; online + location-less events skipped; default-ON; physical
event still fires; toggle-off restores) and two `scripts/smoke_panel.js` cases
(toggle present/wired; defaults ON). audit + all four kernel gates green.

## [8.104.0] — Briefings are retrievable in the panel (issue #232)

Long briefings get truncated in phone notifications; now the full text is always
readable in the UI.

- The **Memory** tab gains a **Recent Briefings** card showing the last 10
  briefings in full, newest first, with timestamps (the latest highlighted).
- New `jarvis/briefings` WS endpoint returns recent briefings (full text,
  `[Briefing] ` prefix stripped). Briefings are read from the existing
  conversation store tagged `device_id="briefing"` — `briefing.async_briefing`
  already persisted them, and **proactive briefings now persist too** (they
  previously only logged/pushed), so both scheduled and arrival/security
  briefings show up.
- Additive, behaviour-preserving: no change to how briefings are generated,
  spoken, or pushed.

tests: three new `scripts/smoke_panel.js` cases (the card renders; briefings
show full text newest-first; the latest is marked). `node --check` + smoke
clean; audit (IMPORTS) + all four kernel gates green.

## [8.103.0] — Camera Watch remembers the chosen camera (issue #231)

Selecting a camera in Camera Watch didn't stick: navigating away and back reset
the view to the first camera in the list (which for some setups is a screensaver
feed). The active camera lived only on the panel component instance, so it was
lost whenever Home Assistant re-created the panel on tab navigation.

- The explicit pick is now persisted to `localStorage` (`jarvis_active_cam`) on
  selection and restored on setup — but only if it's still an **enabled**
  camera; an unknown/removed entity falls back to the first, so a stale
  preference can't leave the feed blank. Per-browser preference, wrapped in
  try/catch so private-mode or blocked storage degrades silently to the old
  first-camera default.
- Frontend-only (`frontend/jarvis-panel.js`); no backend or safety change.

tests: three new `scripts/smoke_panel.js` cases (selection persists; a fresh
setup restores the remembered camera; an unknown stored camera falls back to the
first). `node --check` + smoke clean; audit + bypass gate green.

## [8.102.0] — Token & cost telemetry runs in shadow on the provider layer (TC2)

First wiring of the token/cost telemetry primitive (TC1, 8.95.0) into live code
— **observe-only**. Every LLM call through `llm_provider` (Groq, OpenAI, Ollama,
Gemini, Anthropic) now emits a kernel `UsageRecord` built from that provider's
**actual** usage fields — input / output / **cached** tokens — attributed to the
provider and model.

- **Records, drives nothing.** The record is logged (debug) and goes nowhere
  else yet; responses and behaviour are unchanged. This is the shadow rung
  before parity (telemetry totals vs. the current estimates) and enforce (the
  cost panel + Phase Y read it).
- **Defensive across provider shapes:** reads `prompt_tokens`/`completion_tokens`
  + `prompt_tokens_details.cached_tokens` (OpenAI/Groq), `input_tokens`/
  `output_tokens` + `cache_read_input_tokens` (Anthropic), and
  `usage_metadata.*_token_count` (Gemini); cached is subtracted from the input
  count so the cost estimate doesn't double-count. **Kill-switched**
  (`TOKEN_TELEMETRY_SHADOW`) and fully wrapped so it can never disrupt or raise
  into an LLM call.
- `token_telemetry` advances `pure → shadow` in the adoption matrix (owner
  `llm_provider`); Constitution ledger regenerated.

Audit (COMPILE/IMPORTS/NAMES) + all four kernel gates green. (Part of #237.)

## [8.101.0] — Conflict-resolution primitive (Epistemic Fabric, pure)

Completes the Epistemic Fabric's core: a formal rule for which source wins when
evidence contradicts (camera says empty, phone says present, motion fires) —
rather than "whichever subsystem ran last." Must exist before advanced
world-model reasoning.

- New `kernel/conflict.py` (pure, no HA import): `resolve` takes
  `kernel.provenance.Provenance` candidates, scores each by
  `confidence × source-reliability × recency × corroboration`, sums scores per
  distinct value (independent agreeing sources reinforce), and returns the
  winning value with the strongest supporting record. It flags **contested**
  when the runner-up value is within a margin — so an unclear conflict defers to
  confirmation instead of picking blindly — and drops stale candidates. Source
  reliability is caller-supplied (default 0.5); recency uses a configurable
  half-life.
- **Pure / behaviour-preserving:** declared `pure` in the adoption matrix and
  unit-tested (9 cases); nothing live routes decisions through it yet
  (world-model / situation consult it in shadow, parity, then enforce).
  Re-exported from `kernel/__init__` (as `ConflictResolution`); Constitution
  ledger + adoption matrix regenerated.

Audit + all four kernel gates green. (Completes #238.)

## [8.100.0] — Uncertainty primitive (Epistemic Fabric, pure)

Makes uncertainty first-class: JARVIS can distinguish *"I believe the garage is
empty: 0.92"* from *"the garage is empty."*

- New `kernel/uncertainty.py` (pure, no HA import): an `Uncertain` pairs a value
  with a `confidence` (clamped 0–1), a **band** (`known` ≥0.95 / `believed` ≥0.60
  / `guessed` ≥0.30 / `unknown`), its `basis`, and a `resolver` naming what
  evidence would settle it. `describe()` phrases each band honestly (a guess
  reads as a guess); `is_actionable()` is the bar to clear before acting;
  `update()` folds new evidence (noisy-OR when it agrees, discount when it
  disagrees); `most_certain()` picks the strongest of a set.
- **Pure / behaviour-preserving:** declared `pure` in the adoption matrix and
  unit-tested (9 cases); nothing live produces `Uncertain` values yet
  (perception / world-model / prediction wrap outputs in shadow, parity vs.
  current confidences, enforce when a decision gates on the band). Re-exported
  from `kernel/__init__`; Constitution ledger + adoption matrix regenerated.

Audit + all four kernel gates green. (Part of #238.)

## [8.99.0] — Provenance primitive (Epistemic Fabric, pure)

Another Epistemic Fabric primitive: every important piece of state can now be
wrapped so it answers *where did this come from*.

- New `kernel/provenance.py` (pure, no HA import): a `Provenance` pairs a value
  with `source`, `observed_ts`, `confidence` (clamped 0–1), `model`,
  `corroboration` and an optional `expires_ts`. `select_authoritative` returns
  the freshest high-confidence non-expired record; `corroborate` merges two
  records of the same value with noisy-OR (independent agreement reinforces) and
  folds the other source into `corroboration`, while conflicting values return
  the higher-confidence record unchanged (conflict resolution is separate).
  `summary` renders a one-line origin description.
- **Pure / behaviour-preserving:** declared `pure` in the adoption matrix and
  unit-tested (8 cases); nothing live attaches provenance yet (world-model /
  situation / recognition attach it in shadow, parity vs. current reads, enforce
  when a consumer reads the provenanced value). Re-exported from `kernel/__init__`;
  Constitution ledger + adoption matrix regenerated.

Audit + all four kernel gates green.

## [8.98.0] — Cognitive cycle parity check over the main loop (Phase J, J3)

Advances Phase J from shadow to **parity**. The cognitive cycle in
`cognitive_core._tick` now compares its own view of the pass to what the loop
actually did: the cycle's DECIDE count must equal the number of actions the loop
dispatched (ACT), and the agreement is logged (`parity … agree=…`), with a
`parity MISS` line if they ever diverge.

- Still **observe-only** — drives nothing, behaviour-identical, same
  `COGNITIVE_CYCLE_SHADOW` + `cognitive_cycle_shadow` kill-switch and the whole
  pass stays wrapped so it can never affect the loop or safety checks.
- The dispatch loop now counts emitted actions so the parity check reflects what
  truly fired, not just what was decided. `cycle` advances `shadow → parity` in
  the adoption matrix; Constitution ledger regenerated.

Audit (COMPILE/IMPORTS/NAMES) + all four kernel gates green; cognitive-core
suite green.

## [8.97.0] — Cognitive cycle runs in shadow over the main loop (Phase J, J2)

First wiring of the unified cognitive cycle (J1, 8.89.0) into a live subsystem —
**observe-only**. The main evaluation tick (`cognitive_core._tick`) now also runs
a kernel `CognitiveCycle` over the same pass, stamping one `cycle_id` across
perceive→interpret→decide→act→reflect and logging a `CycleTrace` of what each
phase saw (people, home/sleeping, decisions, autonomous total).

- **Drives nothing.** The real decisions in `_tick` stand unchanged; the cycle
  only records the pass so it is reconstructable as one named cycle before any
  subsystem is actually driven by it (parity J3, enforce J4). Behaviour-identical.
- **Kill-switched + fail-safe:** gated by the `COGNITIVE_CYCLE_SHADOW` module
  constant and the `cognitive_cycle_shadow` config key (either off disables it);
  the whole shadow pass is wrapped so any error is swallowed and can never affect
  the evaluation loop or safety checks.
- `cycle` advances `pure → shadow` in the adoption matrix (owner
  `cognitive_core`); Constitution ledger regenerated. The cycle primitive's own
  unit tests (9) plus the full cognitive-core suite (94) stay green.

Audit (COMPILE/IMPORTS/NAMES) + all four kernel gates green.

## [8.96.0] — Canonical outcome model primitive (Epistemic Fabric, pure)

The structured record that must exist **before** closed-loop learning (Phase M):
rather than letting "learning" become *an LLM re-reading logs*, every
consequential action will eventually produce a canonical, structured outcome.

- New `kernel/outcome.py` (pure, no HA import): an `Outcome` carrying
  `intended_result`, `observed_result`, `success`, `confidence`, `deviation`,
  `cause`, `side_effects`, `user_feedback`, `environmental_feedback`, and a
  distilled `learning_signal` in `[-1, 1]` (`+confidence` on success,
  `-confidence` on failure — a confident success reinforces, a confident failure
  penalizes, a shaky result barely moves anything). `from_verification(...)`
  derives one from a postcondition check and auto-records the deviation on
  failure; `summarize` / `by_capability` roll up success rate and mean signal —
  the per-capability track record Phase N autonomy and Phase M learning both
  read.
- **Pure / behaviour-preserving:** declared `pure` in the adoption matrix and
  unit-tested (9 cases); nothing live produces outcomes yet. Ladder: the
  actuation/verify path records one (shadow) → parity vs. current feedback →
  learning (M) reads structured `Outcome`s, never raw logs (enforce). Re-exported
  from `kernel/__init__`; Constitution ledger + adoption matrix regenerated.

Audit + all four kernel gates green.

## [8.95.0] — Cognitive token & cost telemetry primitive (pure)

Foundation for reporting **actual** model usage and cost instead of estimates.
JARVIS routes work across tiers (classifier / reasoning / review / conversation
/ vision); this primitive captures what each call really spent.

- New `kernel/token_telemetry.py` (pure, no HA import): a `UsageRecord`
  (timestamp, tier, provider/model, input / output / **cached** tokens,
  estimated cost, correlation id, ok); a `PriceBook` (per-model USD-per-1M rates
  for input/output/cached, exact→substring→default lookup) that turns token
  counts into an estimated cost; and pure roll-ups `summarize`, `by_tier`,
  `by_model`. Cached tokens are first-class and billed at their own (reduced)
  rate; an unknown model records tokens at zero cost rather than erroring.
- **Pure / behaviour-preserving:** declared `pure` in the adoption matrix and
  unit-tested (11 cases); nothing live records usage yet. Later releases wire it
  on the ladder — the router/`llm_provider` emits a record per call from each
  provider's own usage fields (shadow), telemetry totals compared to the current
  estimates (parity), the cost panel + Phase Y self-optimization read it instead
  of estimates (enforce). Re-exported from `kernel/__init__`; Constitution ledger
  + adoption matrix regenerated.

Audit + all four kernel gates green.

## [8.94.0] — Identity & Trust Fabric primitive (roadmap Phase I½, pure)

First increment of the audit-added **Phase I½**: a kernel primitive for identity
that treats *who someone is* as a claim with provenance, confidence and expiry —
not a boolean — before autonomy grows to depend on it.

- New `kernel/identity_fabric.py` (pure, no HA import): an `IdentityAssertion`
  carries `subject`, `source`, `authentication_method`, `confidence` (clamped
  0–1), `asserted_ts`, `expires_ts`, `evidence` and `scope`. `resolve()` folds
  many assertions into one verdict, corroborating same-subject evidence
  (noisy-OR) and **failing toward confirmation** — when two subjects are within a
  margin of each other the identity is `contested` and *not* established.
- Encodes the rule **identity ≠ presence ≠ authority ≠ trust**: a presence
  signal (`METHOD_PRESENCE`) can never establish *who* (only that *someone* is
  there), an expired or low-confidence assertion never establishes identity, and
  establishing identity is neither authorization nor earned trust.
- **Pure / behaviour-preserving:** the primitive is declared `pure` in the
  adoption matrix and unit-tested (10 cases); nothing live consumes it yet
  (recognizers emit assertions in shadow, parity against the current
  `identity.py` reads, enforce on one identity-sensitive path later). Re-exported
  from `kernel/__init__`; Constitution ledger + adoption matrix regenerated.

Audit + all four kernel gates green.

## [8.93.0] — Resident "📷 Snapshot now" from any camera (issue #140)

Enrolling a resident's reference photo for best-effort recognition previously
needed either a file upload or a recent sighting to label. A resident who had
never been detected yet — the common bootstrap case — had no way to seed their
reference from a live camera.

- Each resident card in the **Faces** tab now has a **camera picker + "📷
  Snapshot now"** button: pick any `camera.*` entity, grab a fresh frame via
  `jarvis/camera_snapshot`, and enroll it as that resident's reference
  (`jarvis/faces set_reference`) — no prior sighting, no file upload. This
  complements the existing file upload and the sighting-based "📷 From sighting"
  button (which only appears once the person has been seen).
- Frontend-only (`frontend/jarvis-panel.js`); the camera list comes from
  `hass.states`, so it works in the Faces tab regardless of the Cameras tab.
  No backend, safety, or recognition-path change — `resident_present` and
  intrusion monitoring are untouched.

tests: three new `scripts/smoke_panel.js` cases (the button + camera picker
render on a resident card; snapshot-now grabs a frame from the chosen camera;
that frame is enrolled as the reference). `node --check` + smoke clean; audit +
all four kernel gates green.

## [8.92.0] — Fold the 2026-10 external audit into the kernel roadmap (docs)

A second external gap audit reviewed the post-H roadmap itself (not just the
code). Its verdict: the roadmap is pointed the right way but describes *what
capabilities* JARVIS should acquire when the master plan needs to describe *what
information and authority flow between them.* `docs/KERNEL_PLAN.md` now folds in
its five structural corrections:

- **Progress is authoritative dependence, not existence.** A `kernel/foo.py`
  existing isn't JARVIS *using* `foo`; the real metric stays the adoption
  matrix's `pure < shadow < parity < enforce` axis, and every phase's "Done" is
  an enforce claim about a live path.
- **Identity & Trust becomes a first-class phase** (new **I½**) — an
  `IdentityAssertion` (subject, source, method, confidence, expiry, evidence,
  scope) before serious autonomy, with the rule *identity ≠ presence ≠ authority
  ≠ trust*.
- **Outcome · Provenance · Uncertainty · Conflict · Time become kernel
  primitives** — a cross-cutting **Epistemic Fabric** that several later phases
  depend on (Outcome Model required before learning; Conflict before advanced
  world-model reasoning).
- **World-model / space-time (T, Q) move ahead of prediction/learning (L, M)**
  so prediction isn't built on fragmented legacy state and rebuilt later.
- **R becomes an integration gate and AE the MCU certification gate** (with ten
  explicit scenario classes); **O/AB** merge into one agency-orchestration
  framework and **N/AC** into one autonomy engine, as the backlog recommended.

Also: Phase H's definition of done expands to a full shared agency chain
(`agency_id` / `correlation_id` / `causation_id` / `actor` / `identity` /
`intent` / `authority` / `idempotency_key`); Phase I is split into **I-A
commitment continuity** (shipped) and **I-B cognitive continuity** (net-new);
and the single "definition of done" becomes three bars (**H** kernel foundation,
**R** architecture, **AE** MCU system). Docs-only — no behaviour change; all four
kernel gates + audit green.

## [8.91.0] — Unknown faces surface in Recently Seen so they're labelable (issue #140)

On a backend-less, reference-less setup, walking past the camera left the Faces
tab's **Recently Seen** empty ("No non-resident faces seen recently") — so the
on-the-fly "📷 Label as…" feature (8.84.0) had nothing to label, and the dataset
couldn't be bootstrapped. Best-effort only ever recorded a *matched* resident
guess; an unknown person was never surfaced.

- When **best-effort recognition is ON** (`llm_face_recognition`, opt-in) and a
  camera analysis describes a person, JARVIS now pins that frame and records a
  **best-effort UNKNOWN sighting** (`recognition.capture_unknown_snapshot` /
  `remember_unknown_face`), which `recent_faces` surfaces as a labelable
  **Unknown** card carrying the real frame.
- **Safety unchanged:** the unknown cache is kept strictly separate and is
  **never** read by `resident_present` — an unnamed sighting can't stand
  intrusion monitoring down. An Unknown card is suppressed for any camera that
  already has a named recognition/guess (no duplicate), and the write is
  throttled per camera. Off by default (gated on the same opt-in flag), so
  installs without best-effort are unaffected.

tests: three new cases in `tests/unit/test_recognition_faces.py` (surfaces a
labelable row with the pinned frame; suppressed when a named recognition covers
the camera; never feeds `resident_present`). Audit + all four kernel gates green.

## [8.90.0] — Camera analysis respects the configured language (issue #140)

Camera/vision logs reverted to English ("A man detected in the Salon…") even on
a household set to French. The system prompt did carry the language directive,
but that directive has a "reply in the user's language" escape clause — and the
vision call's own instruction text is English, so the model read that as the
user writing in English and answered in English.

- New `language.language_task_directive(hass)` — a **forced** output-language
  instruction for internal, machine-authored task prompts (no escape clause),
  returning `""` for English so English installs are unaffected.
- `camera.async_analyze_camera` appends it to the analysis task, so the
  description itself is produced in the configured language. Best-effort
  (import-guarded) — it can never break analysis.

tests: three new cases in `tests/unit/test_language_module.py` (forces the
language without the escape clause, empty for English, appends with a leading
space). Audit + all four static kernel gates green.

## [8.89.0] — Cognitive OS: the unified cognitive cycle primitive (roadmap Phase J, J1)

Phase J begins. Today JARVIS runs several parallel ad-hoc loops; Phase J
introduces one explicit, instrumented cognitive cycle they can converge on.

- New kernel primitive **`kernel/cycle.py`**:
  - `CognitiveCycle` runs an ordered list of named `CycleStep`s over a shared
    mutable context as one pass, stamping a per-tick `cycle_id` (the pass's
    correlation id) and returning a `CycleTrace` (per-step ok / detail /
    duration). A step that raises is recorded as failed, never raised into the
    caller; `on_error="stop"` halts the pass, `"continue"` (default) carries on.
  - `standard_cycle({phase: fn})` builds a cycle over the canonical phases
    (perceive → interpret → decide → act → reflect), skipping any a caller omits.
- Landed **pure** (declared `cycle: pure` in the adoption matrix): the primitive
  exists and is unit-tested, nothing live runs through it yet, so the release is
  behaviour-preserving. A subsystem runs a cycle alongside its own loop in J2
  (shadow), parity in J3, enforce in J4.

tests: `tests/unit/test_cycle.py` (9 cases — order + trace, shared context +
cycle_id, failure recorded/continue, on_error stop, never-raises, standard_cycle
canonical order, trace serialization). Audit + all four static kernel gates green.

## [8.88.0] — Continuity of self: boot-time reconcile (roadmap Phase I, I3)

Phase I's third increment: on boot, JARVIS now checks what it was in the middle
of against what is still live.

- New `continuity.boot_reconcile()`: loads the pre-restart snapshot (before the
  boot seed overwrites it) and reconciles each remembered goal / open situation
  against the ids still live now (`agency_state.reconcile`), logging
  `continuity reconcile — N still live, M vanished while down`.
- **Parity / log-only / behaviour-preserving:** it computes and logs the
  reconciliation and drives nothing — the adoption stage stays `shadow`
  (observe-only) until I4 makes a resumption authoritative. Same kill-switch
  (`continuity.AGENCY_CAPTURE_ENABLED`) and defensive reads as I2.

tests: three new cases in `tests/unit/test_continuity.py` (live/vanished split,
no-prior-snapshot, kill-switch); audit + the four static kernel gates green.

## [8.87.0] — Continuity of self: boot summary + periodic capture (roadmap Phase I, I2)

Phase I's second increment wires the agency-state primitive (8.85.0) into the
live integration — shadow stage.

- New `continuity.py` binder:
  - on startup, logs what JARVIS was in the middle of before the restart
    (`continuity: mode=… · N goals · M open situations (captured …s ago)`);
  - on a 5-minute tick (and once at boot) captures a fresh snapshot of the live
    active goals (`goals.active`), open kernel situations (`SituationManager`) and
    mode (`modes.active_mode`).
- **Shadow / behaviour-preserving:** it reads live state and writes its own
  snapshot DB + a log line — it drives nothing. Every read is defensive, so a
  failure can never reach the boot path. Kill-switch:
  `continuity.AGENCY_CAPTURE_ENABLED`.
- `agency_state` advances pure → **shadow** (owner `continuity`) in the adoption
  matrix; Constitution ledger + `KERNEL_ADOPTION.md` regenerate from it; all four
  static kernel gates stay green.

tests: `tests/unit/test_continuity.py` (capture persistence, boot summary,
no-prior-snapshot, kill-switch, reader-failure-swallowed).

## [8.86.0] — Roadmap: forward spec for Phases J–Ω (plan of record)

Documentation only. With Phase I underway, `docs/KERNEL_PLAN.md` now carries a
concise spec for every remaining maturity tier — J (Cognitive OS), K (Attention
& Working Memory), L (Prediction & Causal), M (Learning), N (Graduated
Autonomy), O (Multi-Agent), P (Household Intelligence), Q (Embodied), R (MCU
integration), S (Self Model), T (Knowledge Graph), U (Advanced Planning), V
(Long-Horizon Agency), W (Social), X (Environmental), Y (Self-Optimization), Z
(Resilience), AA (Multimodal), AB (Collaborative), AC (Contextual Autonomy), AD
(Research), AE (Synthesis) and Ω (Continuous Evolution). Each is specced to the
same discipline — new kernel primitive, shadow→parity→enforce ladder, per-phase
`*_ENFORCE` kill-switch and fail-safe, done-when — plus a cross-cutting
governance invariant, a dependency-ordered sequencing, and two consolidation
recommendations (merge O into AB; N with AC). No code change.

## [8.85.0] — Continuity of self: durable agency state (roadmap Phase I, I1)

The execution journal (H4) already recovers in-flight *plan steps* after a
restart, but JARVIS's sense of what it is *in the middle of* — the goals it's
pursuing, the situations it has open, the mode it's in — lived only in memory and
was lost on every reboot. Phase I begins closing that gap.

- New kernel primitive **`kernel/agency_state.py`** — a durable, versioned
  snapshot of JARVIS's ongoing commitments, written through the one persistence
  seam (its own DB file; no schema merge):
  - `capture(mode, goals, situations, …)` builds an `AgencyState` from plain
    extracted data (no Home Assistant import — pure and deterministic);
  - `AgencyStore` persists snapshots (newest-wins, pruned to the newest N) and
    reloads the latest, skipping any written by a newer schema rather than
    mis-parsing it;
  - `continuity_summary(state)` renders a one-line "here's what I was doing"
    string; `reconcile(state, live_goal_ids, live_situation_ids)` splits a
    reloaded snapshot into commitments still live vs. ones that vanished while
    JARVIS was down.
- Landed **pure** (shadow→parity→enforce): the primitive exists and is
  unit-tested, but nothing live is wired to it yet, so this release is
  behaviour-preserving. Bootstrap capture (shadow), boot-time reconcile (parity),
  and resume/announce (enforce, kill-switched) are the following increments.
- Declared `agency_state: pure` in the kernel adoption matrix; the Constitution
  stage ledger and `KERNEL_ADOPTION.md` regenerate from it, and all four static
  kernel gates stay green.

tests: `tests/unit/test_agency_state.py` (15 cases — capture, JSON round-trip,
store persistence/prune/reopen, newer-schema skip, continuity summary, reconcile).

## [8.84.0] — On-the-fly face labeling from camera snapshots (issue #140)

Building a reference-photo dataset for best-effort recognition meant manually
uploading a file per resident. Now you can enroll references straight from
real-world detections in the Faces tab.

- **Recently Seen cards** get a **"📷 Label as…"** dropdown of your household
  residents. Pick one and the frame that card is showing is enrolled as that
  resident's reference photo — turning a live sighting (known, unknown, or an
  LLM guess) into training data in one click.
- **Resident cards** get a **"📷 From sighting"** button next to the file
  upload (shown when the resident was seen on a camera) that enrolls their
  reference from the frame that last saw them — no file picking.
- Both reuse the existing `jarvis/faces` `set_reference` command and capture the
  exact image on the card: a loaded live frame or the pinned recognition-time
  snapshot, falling back to a fresh frame from the card's camera.

Frontend-only and entirely manual — nothing changes until you pick a resident,
so it's behaviour-preserving, with no new config and no effect on intrusion
logic (reference photos only feed the opt-in best-effort matcher, which never
drives intrusion decisions). tests: three new panel smoke-test assertions (the
Label-as dropdown lists residents; the resident-card capture button renders;
labeling a sighting round-trips that frame through `set_reference`);
`node --check` + the full smoke suite stay green; the four static kernel gates
are unaffected.

## [8.83.0] — Global search box in the panel header (issue #209)

The panel had no way to jump straight to a place or find an entity — you had to
know which tab/sub-section a setting lived under and scroll for it.

- New search box in the masthead (right-hand cluster, beside the clock) that
  searches **both** panel destinations and Home Assistant entities at once:
  - **Sections** — the eight tabs plus every Settings sub-section and the
    notable cards (Excluded Entities, Floor Plan Editor, Muted Announcements,
    AI Models, …), matched on label or keyword. Picking one switches to that
    tab and, for Settings, opens the right sub-section.
  - **Entities** — matched by entity id or friendly name from the live HA
    states. Picking one lands on Settings → Learning and pre-fills the
    Excluded-Entities picker (the task the reporter was mid-way through).
- Results render in a grouped dropdown; Escape or an empty query clears it, and
  the box is hidden on phones where the masthead has no room.
- The masthead's right-hand items (search · clock · lockdown) are now grouped in
  one flex cell so the three-column header layout is unchanged.

Frontend-only. The masthead renders on every tab, so the box is always
available. tests: five new panel smoke-test assertions (box renders; section
match; entity match by id; empty-query clears/hides; navigation switches
tab + sub-section); `node --check` + the full smoke suite stay green.

## [8.82.0] — Lockdown pill is smaller / harder to toggle by accident (issue #208)

The header lockdown toggle reserved a fixed 34px for its state label so the pill
stayed the same width whether it read "OFF" or "ARMED". When OFF (the common
case) that left a block of empty, still-clickable space to the right of the word
— making it easy to engage lockdown by mistake.

- The state label (`.ld-state`) now sizes to its actual text (`min-width: 0`),
  and the pill's right padding is trimmed (12px → 10px), so the OFF pill is just
  as wide as its contents. The pill grows slightly when it flips to "ARMED"
  (a rare, deliberate action), which is fine.

Frontend-only; `node --check` + the panel smoke test stay green.

## [8.81.0] — Fix: panel home greeting honors the configured name (issue #210)

The panel's home-view greeting always said "Good morning, **sir**" even when a
different form of address was configured — the name was hard-coded in the
template while the honorific setting was never surfaced to the frontend.

- `get_panel_data` now includes `config.honorific` (from the same
  `honorific` option the voice/announcement paths use; defaults to `"sir"`).
- The panel greeting renders `this._honorific()` (HTML-escaped), falling back to
  `"sir"` when unset or before live data arrives.

tests: `test_ws_get_panel_data_returns_status_and_full_config_payload` asserts the
honorific is surfaced; `node --check` + the panel smoke test stay green.

## [8.80.0] — MCU Phase H (H11): last actuator paths onto the seam + the exit-criteria gate

Tenth Phase H increment, and the one that **closes Phase H's exit criterion:
zero consequential actuator bypasses**.

**Last device-decision paths migrated:**
- `mode_scene.apply_mode_entry` (Movie-mode mood dim) now dims the bound room's
  lights **per entity through the seam** instead of a direct multi-entity call —
  still fire-and-forget, same net effect, now event-published and journaled.
- `scenes` scene-suggestion activation now routes `scene.turn_on` through the
  seam, like `run_scene_or_script` (H3).

**The exit-criteria CI gate — `scripts/kernel_bypass_check.py`:**
An AST scan of the integration that **fails CI if any world-mutating
`*.services.async_call` is made outside the universal seam and is not classified**
— the runtime sibling of the 8.34.0 governance gate (which classifies agent
*tools*), same "classify or wire" philosophy applied to raw service calls. A call
is allowed iff it lives in `actuation.py` (the seam), its literal domain is a
communication channel (notify / persistent_notification / tts / assist_satellite),
it is a literal read-only/meta service (`weather.get_forecasts`,
`automation.reload`) or `media_player` playback, or its `(file, function)` is in a
documented allowlist (the `execute_plan` kernel-planner step, the seam's
verify-after-act retry, config-driven notify pushes, and the **generic
user-authored routine runner** — arbitrary service/target/data steps that are a
user macro, not a JARVIS device decision, so deliberately classified rather than
forced onto the per-entity seam). Wired into `validate.yml` alongside the
adoption / coverage / docs-sync gates.

With this, **every consequential actuation JARVIS makes — user control, bulk,
scenes, proactive, delegated sub-agents, safety securing, the offline local
fast-path, intent routing, and mode moods — converges on
`actuation.execute_actuator`**, and the gate mechanically prevents a new bypass
from landing. `actuator` stays ● enforce; coverage unchanged (17.9%).

tests: `test_bypass_gate.py` (the tree is clean + a new direct `light`/`lock`/… or
dynamic-domain call in an unclassified site fails the gate), `test_mode_scene_seam.py`
(per-entity dim + event, zero-pct turn-off, non-movie no-op). Existing suites
stay green.

## [8.79.0] — MCU Phase H (H10): the local intent router onto the universal seam

Ninth Phase H increment. `LocalIntentRouter` (the no-cloud command router behind
"turn off the lights", "secure the garage", "turn it off") actuated with bare
`hass.services.async_call` — a single **multi-entity** batch call for area intents
(lights on/off, secure-area locks/covers) and a single call for the pronoun
context action. H10 routes both through `actuation.execute_actuator`.

- **Per-entity canonical request:** an area intent becomes one seam call per
  resolved entity (the same per-target shape bulk_control and the proactive path
  already use) instead of one `{"entity_id": [...]}` batch — so each actuation is
  planned, publishes its own actuation `JarvisEvent`, and is journaled.
- **Mutex + write-ahead ledger preserved, now more precise:** the per-entity
  concurrency locks are unchanged, and the high-stakes recovery ledger is marked
  complete **per entity that actually actuated** (keyed `txn_by_eid`), rather than
  marking the whole batch complete on a single call's return — better recovery
  fidelity if one target fails.
- **Net effect on the home is unchanged** (same services on the same entities);
  the result dict now reports the entities that genuinely actuated. `intent_router`
  no longer contains a direct world-mutating `hass.services` call.
- `actuator` stays ● enforce; `intent_router` consumes the seam through
  `actuation`. **Coverage unchanged (17.9%).**

tests: new `test_intent_router_seam.py` — lights-off routes per-entity + emits an
actuation event each; secure-area marks the recovery ledger complete per entity;
the pronoun context action routes a single entity through the seam. Existing
`test_intent_router.py` (pure matching/resolution) stays green.

Phase H exit criterion ("zero consequential actuator bypasses"): `routines` and
`mode_scene` are the last alternate paths, migrated next, then the exit-criteria
CI gate lands.

## [8.78.0] — MCU Phase H (H9): the offline local fast-path onto the universal seam

Eighth Phase H increment. The **local intent engine** (`local_engine` — the
zero-API offline fast-path that handles the bulk of voice/text commands without
the cloud LLM) was a whole alternate execution architecture: it actuated devices
with bare `hass.services.async_call` for turn on/off/toggle, lock/unlock,
open/close covers, dim/brighten, media + volume, climate set-temperature, scene /
script activation, and the "goodnight" shortcut (all-lights-off + lock-all).

H9 routes **all** of those through a new `local_engine._seam_execute` →
`actuation.execute_actuator`, so a command handled offline is now planned,
event-published and journaled exactly like one handled by the cloud agent — the
offline path is no longer an unobservable side-door.

- **Behaviour-preserving:** each site performs the same service call with the same
  `blocking` semantics and returns success/failure exactly as its old try/except
  did; the fast-path's authorization gate is unchanged (protected actions still
  defer to the agent *before* execution, so nothing new is actuated).
- **What changes:** each local actuation now emits a canonical actuation
  `JarvisEvent` (the observability control_device has had since Phase A), and
  `local_engine` no longer contains a single direct world-mutating `hass.services`
  call.
- `actuator` stays ● enforce; `local_engine` consumes the seam through
  `actuation` (direct kernel-primitive referencers remain actuation/agent).
  **Coverage unchanged (17.9%).**

tests: `test_local_fastpath_gate.py` gains a seam-routing + actuation-event test
and a `_seam_execute` failure-contract test; the existing fast-path gate /
actuation suites stay green (the seam preserves the exact service-call shape).

Part of closing the Phase H exit criterion ("zero consequential actuator
bypasses"): `intent_router`, `routines` and `mode_scene` are the remaining
alternate paths, migrated next, then the exit-criteria CI gate lands.

## [8.77.0] — MCU Phase H (H8): safety securing onto the universal seam (fail-toward-protection)

Seventh Phase H increment, and the one that closes the **last direct-actuator
bypass**. Securing the home against a threat — intrusion lockdown (`_lock_all`,
`_secure_entity`) and nighttime lockdown (`_nighttime_lockdown`) — was the only
world-mutating actuation left that called `hass.services` directly, outside the
universal seam. H8 routes it through a new **`actuation.execute_safety_actuator`**
under a **SAFETY policy-mode** that is the deliberate inverse of the discretionary
path:

- **Never blocked.** A safety response must always run, so it does **not** pass
  the agency-budget / loop-detect gates at all (those live in the proactive path,
  not the seam). An exhausted budget or a thrash verdict can never withhold a
  lock.
- **Verification mandatory.** Unlike a proactive offer, a securing action always
  schedules a verify-after-act + records the terminal `ActuatorOutcome` — a lock
  that silently didn't engage is a safety failure.
- **Fail toward protection.** On *any* seam/kernel fault — or any non-success —
  it **falls open to a direct `hass.services` call**, so the home is still
  secured. `lock` / `close_cover` are idempotent, so the backstop can never leave
  the home *less* secure than the direct call did.
- **Kill-switched.** `actuation.SAFETY_SEAM_ENFORCE = False` reverts the whole
  path to the exact direct call it replaced.

With this, every consequential actuation — user control, bulk, scenes, proactive,
delegated sub-agents, and now safety securing — converges on `execute_actuator`;
the `actuator` primitive gains `cognitive_core` as a live consumer. Behaviour on
the live home is preserved (same services, same end-states, same announcements);
what changes is that a securing action is now journaled (plan → event → verified
outcome) like every other actuation. **Coverage unchanged (17.9%).**

tests: `test_safety_seam.py` — secures through the seam + publishes the actuation
event; never budget/loop-blocked even when both are exhausted; fails open to a
direct call on a seam exception and on a non-success result; returns False only
when even the direct call fails; kill-switch reverts to the direct call;
verification is always scheduled; and the `LockdownManager` `_lock_all` /
`_secure_entity` paths secure through the seam end-to-end. Existing lockdown /
intrusion suites stay green. Coverage/adoption/docs-sync gates OK.

## [8.76.0] — MCU Phase H (H6): delegation attribution — a sub-agent's actuations name the sub-agent

Sixth Phase H increment. When JARVIS delegates an objective to a named sub-agent
(FRIDAY the background automator), the actuations the sub-agent performs flow
through the very **same** universal seam JARVIS uses (control_device /
bulk_control / run_scene_or_script, H1–H3). Until now the journal recorded
*"jarvis did X"* for an action a sub-agent took under JARVIS's delegation — the
audit's canonical request shape wants *who* to be truthful.

- New ambient **`kernel.actor`** contextvar (mirroring `kernel.correlation`):
  `"jarvis"` by default, isolated per async task, never leaks between chains.
- `agent._run_delegated` brackets a **named profile's** run in `actor.scope("friday")`
  / `actor.scope("homer")`, so every `ActuatorRequest` the sub-agent builds and
  every actuation `JarvisEvent` it emits is attributed to the sub-agent — while
  the existing **correlation id** still links the chain back to JARVIS's delegating
  turn. Together: *"FRIDAY did X, correlated to JARVIS's delegation."*
- `actuation.request` / `emit_event` read the ambient actor (the request carries
  it; the event names it). A **generic capability-scoped** delegation is JARVIS
  with a reduced toolset, not a distinct agent, so it stays `"jarvis"`.
- **Metadata-only and behaviour-preserving**: no control flow consumes `actor`
  (authority has its own separate actor on `AuthorityRequest`); only the recorded
  attribution changes. **Kill-switched** (`agent._DELEGATION_ATTRIBUTION = False`
  reverts every delegated actuation to `actor="jarvis"`).
- `actor` primitive declared ◐ shadow (owners: actuation, agent), like
  correlation. Coverage unchanged (17.9%).

tests: `test_delegation_attribution.py` — the contextvar (default/scope/nesting),
`actuation.request`/`emit_event` reading it, and `_run_delegated` attributing
FRIDAY/HOMER (and leaving generic delegation and the kill-switch as "jarvis",
with no scope leak past the run). Full suite green.

## [8.75.0] — MCU Phase H (H5): proactive autonomy onto the universal actuator seam

Fifth Phase H increment. The discretionary **autonomous/proactive** actuation path
(`cognitive_core._execute_action_data` — JARVIS acting on a learned/trusted pattern
of its own accord) now executes **through the universal actuator seam** instead of
a direct multi-entity `hass.services` call.

- The **G1/G2 gates still decide WHETHER** a proactive action runs (agency-budget
  and loop-detect enforce, unchanged and still checked first); the **seam decides
  HOW** it executes and is recorded — each target is a canonical per-entity
  `ActuatorRequest` run through the kernel planner, publishing an actuation
  JarvisEvent, exactly like control_device / bulk_control / scenes.
- Per-target now (the audit's "canonical actuator request per target"): a
  multi-entity proactive action becomes one seam call per entity. Best-effort —
  returns success if at least one target actuated, so a wholly-failed action makes
  no false "I did it" claim. Adds an `exists:<entity>` precondition (the seam's),
  so a proactive offer never fires at an unknown entity.
- `actuator` primitive stays ● enforce with **proactive as a new live consumer**
  (cognitive_core). Coverage unchanged (17.9%).
- This removes one of the audit's flagged **legacy actuator bypasses**
  (`proactive action → legacy`).

tests: `test_budget_enforce.py` gains a seam-routing/event test; the G1/G2 enforce
suites are updated to the per-entity service-call shape (and set the target entity
so the seam precondition holds). Full suite green.

## [8.74.0] — MCU Phase H (H4a): the full goal lifecycle is journaled

Fourth Phase H increment, first half of goals→kernel. F1/F2 recorded a goal's
kernel Plan into the execution journal **at creation** only — so the journal
could show a goal was *planned* but not what *happened* to it. H4a records the
goal's **step transitions and closure** into the journal too, directly advancing
Phase H's exit criterion: *every consequential agency operation is reconstructable
from the journal.*

- A goal's kernel Plan now uses **deterministic ids** (`goalplan_<id>` /
  `goalstep_<id>_<n>`), so create → step advance → close all address the **same**
  journal rows (record_plan is `INSERT OR IGNORE`; finish_step updates by id).
- `goals.update(...)` journals each step's terminal status (done/failed/skipped)
  as it is marked; when the goal itself closes (done/failed/cancelled) any
  still-open steps are journaled as `skipped`. `goals.cancel(...)` does the same.
- **Behaviour-preserving & SHADOW:** the goals SQLite store stays authoritative;
  the journal is written alongside (best-effort, after the authoritative commit)
  and never replayed in the live flow. Promoting the kernel Plan/Journal to be
  *authoritative* for goals — making the store a derived view — is the separate,
  **owner-gated H4b** (it changes user-facing persisted state), deliberately not
  taken here.
- `journal` primitive stays ◐ shadow (owner: goals); no coverage change (17.9%).

tests: `test_goal_journal_shadow.py` gains lifecycle coverage — create/advance/
close land on the same rows; cancel journals the terminal state. The F1/F2
shadow-plan tests are updated for the new `_shadow_plan(goal_id, …)` signature.

## [8.73.0] — MCU Phase H (H3): run_scene_or_script onto the universal actuator seam

Third Phase H increment: `run_scene_or_script` now activates scenes / scripts /
automations **through** the `execute_actuator` seam, so its execution passes the
kernel planner (precondition → act) and publishes the actuation event via the
same path control_device and bulk_control use — rather than a direct
`hass.services.async_call` + a separately hand-logged shadow plan + emit_event.

- `scene.turn_on` / `script.turn_on` / `automation.trigger` routes through
  `actuation.execute_actuator` (`blocking=True`, `verify=None` — a scene/script
  has no single deterministic end-state, so no verify-after-act, as before).
- Behaviour-preserving: same service call, one actuation event per activation
  (capability / subject / area unchanged), an invalid target still errors without
  touching the envelope, and the authority posture is unchanged (this path has no
  confirm-gate).
- No kernel-primitive change — `actuator` stays ● enforce with a **third live
  consumer** (run_scene_or_script). Coverage unchanged (17.9%).
- Next: goals (H4) consuming kernel Plan/Journal.

tests: the `run_scene_or_script` suite (scene/script/automation event, service
call, invalid target) stays green, pinning parity.

## [8.72.0] — MCU Phase H (H2): bulk_control onto the universal actuator seam

Second Phase H increment: migrate `bulk_control` onto the `execute_actuator` seam
H1 introduced, so a batch is genuinely "syntactic sugar over multiple canonical
actuator requests" (the audit's recommendation) rather than its own
discover-targets → service-calls path.

- Each executed target now routes through **`actuation.execute_actuator`** — the
  same Execution (through the kernel planner) → Event path control_device uses —
  instead of a hand-assembled `hass.services.async_call` + `emit_event`.
- **Behaviour-preserving:** still fire-and-forget (`blocking=False`, new seam
  param, default True), so no per-device verify/outcome; the already-off/unlocked
  filters and the protected-device skip (no event for a skipped device) are
  unchanged; one actuation event per executed target.
- The seam gains a `blocking` parameter (default True keeps control_device's
  verify-after-act semantics; bulk passes False).
- No kernel-primitive change — `actuator` stays ● enforce, now with a second live
  consumer. Coverage unchanged (17.9%).
- Next: run_scene_or_script (H3) onto the same seam.

tests: `test_execute_actuator_seam.py` gains a `blocking`-passthrough test (6
total); the bulk_control suite (events-per-target, filters, protected-skip) stays
green, pinning parity.

## [8.71.0] — MCU Phase H (H1): the universal actuator seam

The start of **Phase H — Universal Agency Spine** (the MCU audit's next milestone):
eliminate JARVIS's alternate agency paths so every consequential action converges
on one kernel spine. H0 (8.66.0) closed the governance/doc drift; H1 builds the
audit's P0 **universal actuator** — one authoritative seam every actuation routes
through.

- **New `actuation.execute_actuator(...)`** — the single composition point for an
  actuation's **Execution → Event → Verification/Outcome**. Execution runs
  *through* the kernel planner (`aexecute_plan`), the canonical actuation
  JarvisEvent is published on success, and the verify-after-act (which records the
  terminal `ActuatorOutcome`) is scheduled. This is the control_device golden path
  turned into the reusable framework the audit asked for ("turn the control-device
  path into the universal actuation framework").
- **control_device now routes its execution through the seam** instead of
  hand-assembling plan-execute + emit_event + verify inline. Behaviour-preserving:
  the same service calls, the same single planner pass, the same event, the same
  verify/outcome conditions. Every branch (on/off/lock/open/close/media,
  set_brightness/temperature/volume) now also carries a canonical
  `ActuatorRequest`, so each actuation has one described request.
- **`actuator` primitive promoted parity → ● enforce** — the seam is authoritative
  on a live path (the same bar as `world_model`/`situation`), not a shadow
  contract. Constitution ledger + `KERNEL_ADOPTION.md` regenerated; doc-sync CI
  gate green. **Coverage unchanged (honest 17.9%).**
- Next H increments migrate the remaining paths onto the same seam, in the audit's
  order: bulk_control → run_scene_or_script → goals → proactive → FRIDAY → HOMER →
  safety direct actuators.

tests: `test_execute_actuator_seam.py` (5) — executes through the planner once,
publishes the event on success, schedules verify on success, returns an error with
no event/verify on failure, and no verify when none is supplied. The full
control_device/actuation/verify suites (49) stay green, pinning parity.

## [8.70.0] — Intrusion: arming HOME is no longer treated as "away" (the real root fix)

The actual root cause behind the resident-mistaken-for-intruder false alarms.
With `intrusion_requires_confinement` on, `confined = is_lockdown() or
_alarm_armed()` and then `away = _residents_away() or confined` — and
`_alarm_armed()` is **true for `armed_home`**. So **arming the panel in HOME mode
flipped intrusion straight into the "away" branch**, where a resident moving
through the house is evaluated as an intruder. (The earlier 8.68.0/8.69.0 fixes
addressed the *confirmation* side and the *unavailable-panel hold*; this is the
plain case the user hit: panel readably `armed_home`, logbook "Cove Alarm → Armed
home", a resident home, confirmed as a break-in.)

**The fix.** A HOME arming posture is no longer read as "away":
- New `_confinement_is_home_posture()` — true when the panel is readably armed in
  a home mode (`armed_home` / `armed_night` / `armed_custom_bypass`), OR a held
  auto-lockdown was engaged from one of those modes (the Cove dropped out after a
  home arm). It is false for genuine tracked-away, an `armed_away`/`armed_vacation`
  panel, or a user-requested (manual) lockdown.
- `_check_intrusion` now computes `away = _residents_away()`, and only adds the
  confinement-implies-away inference when it is **not** a home posture. So arming
  HOME (or a Cove dropout holding a home-mode lockdown) no longer treats residents
  as intruders.
- The arming mode that engaged an auto-lockdown is now captured on
  `LockdownManager.arm_mode` (kept fresh while the panel is readably armed,
  persisted across restarts, cleared on disengage), so a later dropout holds with
  the correct home/away distinction.

**What still fires (unchanged):** `armed_away`/`armed_vacation`, genuine
tracked-away, and a user-requested lockdown all still infer away and detect
intrusions. **Night interior monitoring is unaffected** — it runs via the asleep
path, not this away branch. Lockdown's securing behaviour (locking, re-securing a
breach) is untouched. Kill-switch `INTRUSION_HOME_ARM_NOT_AWAY` (default on)
reverts the policy in one line. No kernel-primitive change; coverage unchanged
(17.9%).

- tests: `test_intrusion_home_posture.py` (10) — the `_confinement_is_home_posture`
  matrix (readable armed_home/night/away, tracked-away override, manual lockdown,
  none), arm_mode capture + held-through-dropout for home vs away, the live
  end-to-end (armed_home held through a dropout → no alarm), and kill-switch
  revert. `test_cognitive_core_intrusion.py` updated: armed_home while home →
  no intrusion; armed_away confinement → still fires. Full suite green.

## [8.69.0] — Intrusion: stop false "intrusion confirmed" on a resident during a degraded alarm-panel hold

A second, deeper false-alarm fix, on the **confirmation** side this time. R3b
(8.68.0) stopped an investigation from *opening* on an awake resident positively
tracked home. This closes the residual case where the investigation does open —
because the resident is home but *not* positively tracked (phone off Wi-Fi, a
`not_home`/stale tracker) and not face-enrolled — and then gets **confirmed as a
critical break-in**.

**The mechanism.** With `intrusion_requires_confinement` on and the alarm panel
(`alarm_control_panel.home_cove_alarm`) `unavailable`, the lockdown is *held*
defensively (correct — a cloud drop must not *lift* a real armed-night lockdown),
but it is never lifted either, because only a confirmed `disarmed` lifts it and an
unavailable panel never reports one. So `is_lockdown()` stays true all day →
`confined` → the home is treated as "away" while the family is home. During the
investigation, if the covering camera's vision check comes back
**inconclusive/unavailable** (a flaky or slow local model), the confirm step used
to **fail open** — "trust the camera" → a critical `intrusion_confirmed` alarm on
the resident.

**The fix.** A new `_confinement_degraded()` predicate recognises the one
degraded state — the home is "away" *only* because an auto (alarm-engaged)
lockdown is held through a currently-indeterminate panel, with no tracked-away /
armed-away presence and no user-requested lockdown. In that state, an inconclusive
vision check no longer confirms on "trust the camera" alone; it requires real
corroboration — a positive vision person-confirm, or a genuine **inward route**
from the breach (the same evidence the no-camera path already demands). A positive
vision confirm still escalates instantly on any later tick, and a clear vision
negative still clears, both unchanged. **Genuine away** (tracked-away, armed-away,
or a user-requested lockdown) is untouched: vision-inconclusive still fails toward
alerting there, so a real break-in with a broken vision path is never suppressed.

- Entry behaviour is unchanged (the initial, soft "possible intrusion —
  investigating" ping still fires once, still subject to learned damping); this
  only hardens the escalation to a *confirmed critical* alarm.
- Kill-switch `INTRUSION_DEGRADED_CONFIRM_HARDEN` (default on) reverts to the prior
  always-fail-open behaviour in one line. `_confinement_degraded()` never raises —
  a fault leaves the prior fail-toward-safety behaviour intact.
- No kernel-primitive change; the stateful investigation/confirmation stays in the
  live path by design. Coverage unchanged (17.9%).

## [8.68.0] — MCU Phase R (R3b): intrusion entry-gate ENFORCE — and a real false-alarm fix

Completes the intrusion re-architecture, and does more than flip it: it **fixes a
live false-alarm class**. The kernel intrusion entry-gate is now authoritative in
`SafetyManager._check_intrusion`, and — unlike the behaviour-preserving flips —
it is deliberately **stricter** than the legacy precondition.

- **Root cause fixed.** A diagnostic config dump showed the real failure: when the
  alarm panel (`home_cove_alarm`) flaps to `unavailable`, lockdown is *held* open
  as a fail-safe, and with `intrusion_requires_confinement` on that held hold made
  `_check_intrusion` treat the home as "away" (`away = _residents_away() or
  confined`). A resident moving on camera *at home* (seen in the log: "a man …
  shirtless, wearing red shorts" in the kitchen) was then investigated as an
  intruder. The design intent was always "never fire when someone is home" — the
  `or confined` path violated it.
- **The fix: a residents-tracked-home veto in the kernel gate.** An awake resident
  positively tracked home (a `person`/`device_tracker` reading `home`) never opens
  an intrusion investigation, even under a degraded confinement hold. At night
  (`sleeping`) the veto lifts, so a genuine break-in while asleep is still
  monitored. A genuinely armed-away alarm still reads as away (no resident home),
  so real away-intrusion detection is unaffected.
- **Authoritative, fail-safe, kill-switched.** `_check_intrusion` consumes the
  kernel gate's verdict for whether to open an investigation; on any kernel fault
  it falls back to the legacy precondition, and `cognitive_core.INTRUSION_GATE_ENFORCE
  = False` reverts entirely. Only the entry gate is kernel-owned; the stateful
  investigation/escalation and the vision-confirm step remain legacy.
- **No coverage/stage inflation.** `situation` was already `●` enforce (delivery +
  freeze); intrusion joins as an authoritative caller. **Coverage unchanged (17.9%).**

10 new tests: the kernel veto blocks an awake tracked-home resident even with
corroboration and lifts when asleep; `_resident_tracked_home` reads person/tracker
home; the entry gate vetoes under enforce, obeys the kill-switch, and fails safe to
legacy on a kernel fault; and two end-to-end regressions — the reported scenario
(held lockdown + resident home + open window → **no** investigation) and its
contrast (genuinely away → investigation still opens). Full suite green; all four
gates clean. Intrusion enforce + false-alarm fix → **8.67.0 → 8.68.0**.

## [8.67.0] — MCU Phase H (H0): close the governance drift — generated docs + a CI sync gate

An external gap-audit of v8.65 flagged a **critical governance defect**: the
`docs/JARVIS_CONSTITUTION.md` still declared *"authority is log-only … no primitive
at enforce"* after authority (and four other primitives) had been flipped to
enforce. The specification had fallen behind the executable architecture — exactly
the drift the kernel governance is meant to prevent. This release closes it and
makes the drift impossible to reintroduce silently.

- **Constitution Authority section rewritten to the enforced reality.** Invariant A1
  now says authority *begins* log-only and is promoted deliberately; A2 is widened
  to state the owner-gated promotion rule **and** the enforcement contract that
  actually governs the live flips — a **max-restriction belt** (enforcement can only
  *add* a confirmation, never loosen a decision), fail-safe to legacy on a kernel
  fault, and a one-line kill-switch. The stale *"no primitive at enforce"*
  parenthetical is gone.
- **New generated enforcement ledger** in the Constitution, and the existing
  `KERNEL_ADOPTION.md` matrix, are both rendered from `kernel_adoption._DECLARED` by
  the new `scripts/kernel_docs_sync.py` (`--write`). The docs are no longer
  hand-maintained prose that can drift.
- **New CI gate** `scripts/kernel_docs_sync.py --check` (wired into `validate.yml`)
  fails the build when the Constitution ledger or the adoption matrix describes a
  different stage than the implementation declares. On first run it already caught a
  stale caller list (`situation` had gained `cognitive_core` as a live caller) and
  corrected it.
- **No behaviour change** — documentation + governance only. The enforcement ledger
  reads: `world_model`, `budget`, `loop_detect`, `situation`, `authority` at
  `● enforce`; `actuator`/`authority`-adjacent contracts at parity; intrusion
  deliberately still parity pending burn-in. **Coverage unchanged (17.9%).**

6 new tests: the committed docs are in sync; the ledger lists every primitive with a
stage and reads `authority`/`world_model` as enforce; extract/replace round-trips;
and the gate detects both a stale block and missing markers. Full suite green; all
four gates (audit + adoption + coverage + docs-sync) clean. Governance fix →
**8.66.0 → 8.67.0**.

## [8.66.0] — Best-effort face recognition for plain cameras (no NVR) (#140)

Phase-3 best-effort face recognition only ever ran when a `nest_event` or
`frigate_event` fired (plus doorbell presses and the periodic package sweep).
A user on **plain Home Assistant cameras + a local vision model, with no
Frigate/Nest**, therefore never had a camera frame analysed on ordinary motion
— so the vision pipeline never ran, nothing reached the local model, and the
recognition hook (which rides on top of that analysis) never got a frame. This
is exactly the gap @QuentinVape40 hit on #140 (zero requests reaching Ollama).

- **New opt-in trigger: "Analyze HA motion (no NVR)"** (Settings → Cameras,
  config key `camera_motion_vision`, **default off**). When on, a Home Assistant
  motion/occupancy/presence `binary_sensor` firing is mapped to the camera that
  covers its area (direct HA area assignment first, then the saved floor-plan
  camera coverage) and a vision analysis is run on that camera — which carries
  the best-effort recognition hook, so the Faces tab finally populates on a
  no-NVR setup.
- **Silent by design.** The motion-triggered analysis runs with announcements
  suppressed — its job is to feed scene learning and face recognition, not to
  add spoken alerts on every motion. The recognition hook inside
  `async_analyze_camera` fires either way. (Frigate/Nest/doorbell paths keep
  their existing notability-gated announcements.)
- **Bounded.** Throttled per-camera (one analysis per 120s) and gated entirely
  behind the new default-off flag, so nothing changes — and the local model is
  never touched on motion — unless the user turns it on. Sensors are discovered
  at setup; a reload picks up new ones.
- `async_auto_analyze_on_event` gained an `announce` parameter (default True,
  so every existing caller is unchanged) that threads through to suppress the
  tts target, speaker list, and the call's own announce flag.

2 new unit tests (silent run suppresses the announcement targets while still
analysing the right camera; the default announcing path is unchanged) + a panel
smoke check for the toggle. New feature → 8.65.1 → 8.66.0.

## [8.65.1] — Fix: long room name no longer pushes the light pill into the next card

A room card whose name is long (e.g. "Conservatory") shoved its light ON/OFF
pill past the card's right edge and into the neighbouring card on the Command
Center dashboard (#188). The reporter saw it next to a lightless room and
suspected the missing pill was the cause; the real culprit was the long name.

- **Root cause.** In the card footer the room name and the light pill share a
  flex row (`justify-content: space-between`). The name had the default
  `min-width: auto`, so it refused to shrink below its content width, overflowed
  the fixed-width card, and pushed the pill out the right side. Short names fit,
  so only long-named rooms were affected — the lightless neighbour was a
  coincidence of position, not the cause.
- **Fix.** The footer name now shrinks and ellipsizes within its flex cell
  (`min-width: 0; text-overflow: ellipsis`), keeping the light pill pinned inside
  the card's right edge at any card width. On wide cards the full name still
  shows; when a card is too narrow the name truncates and the full name is
  available via a `title` hover tooltip.
- Frontend-only; no behaviour change. Regression pinned by two panel smoke
  checks.
## [8.65.0] — MCU Phase G (G4): authority ENFORCE — the kernel capability engine gates actuation

The authority flip. The kernel capability engine is now authoritative over the
live confirm-gate for an allowlisted set of security capabilities — built so it
can only ever *tighten* the gate, never loosen it.

- **Max-restriction belt.** For an allowlisted capability the action proceeds only
  if the legacy `confirm_gate` allows it **AND** the kernel engine returns
  `ALLOW`. So the engine can only turn an otherwise-allowed action into one *held
  for confirmation* — it can never remove the legacy gate or let through something
  the gate would hold. `kernel_adoption` moves `authority` `◑` parity → `●` enforce.
- **Bounded blast radius.** Enforcement applies only to
  `authority_bridge.AUTHORITY_ENFORCE_CAPABILITIES` — seeded with exactly the
  security capabilities `policy.confirm_gate` already protects (`lock.unlock`,
  `lock.open`, `cover.open_cover`/`open`, `alarm_control_panel.alarm_disarm`/
  `arm_away`/`arm_home`/`arm_night`), where an extra confirmation is the safe
  direction. Every other capability stays pure log-only parity.
- **Fail-safe + kill-switch.** A kernel fault (no engine decision) leaves the
  legacy outcome unchanged — an engine error can never block a legitimate action.
  Flip `authority_bridge.AUTHORITY_ENFORCE` to `False` to revert to pure parity on
  the next load. Applied at both protected actuation paths (`control_device` and
  the bulk/plan step gate); safety-critical autonomous responses (nighttime
  lockdown, intrusion securing) call `hass.services` directly and never route
  through the gate, so they are unaffected.
- **Coverage unchanged (17.9%).** `authority` is not a behaviour-spine contract.

11 new tests: the belt proceeds on engine ALLOW, gates on CONFIRM/DENY, never
loosens a legacy denial, ignores non-allowlisted capabilities, reverts with the
kill-switch off, fails safe to legacy on a kernel fault, and logs when it tightens;
plus end-to-end against the real engine (an unlock with no identity is held; a
safe light action is untouched). Full suite green; audit + adoption + coverage
gates clean. Authority enforce flip → **8.64.0 → 8.65.0**.

## [8.64.0] — MCU Phase R (R3a): intrusion entry-gate decision-parity

Step (a) of the intrusion re-architecture — the highest-risk, most carefully
staged of them all. The kernel now computes the *entry gate* for intrusion (the
precondition that decides whether a possible-intrusion investigation opens), and
the live SafetyManager logs it against its inline decision. **Log-only; no
behaviour change.**

- **New pure kernel gate** `kernel.situation.intrusion_gate(away, qualifying_motion,
  require_corroboration, alarm_armed, open_entry)` → bool. It mirrors the
  false-alarm-critical precondition exactly: an investigation opens only when
  presence is **away**, there is **qualifying motion**, and — when corroboration
  is required — there is an **armed alarm or an open entry point**. (A lone
  curtain-flutter with no corroboration never opens — the exact class of the prior
  false-intrusion bug.) The stateful parts of the investigation (cooldown,
  call-off, resident-on-camera, zone-spread escalation) stay in the caller.
- **`SafetyManager._check_intrusion` logs the kernel gate against its inline
  decision** at the corroboration point (`intrusion.record_gate_parity`), both
  when it opens an investigation and when it declines. Log-only, best-effort,
  never affects the intrusion decision.
- **Why only step (a) ships now.** Intrusion has **no safe fail-toward
  direction** — a false positive re-creates the prior false-alarm bug, a false
  negative misses a real break-in. So unlike freeze, there is no max-severity
  belt that makes an immediate flip safe. The enforce flip (R3b) will make the
  kernel gate authoritative only with *legacy-wins-on-divergence* + a kill-switch,
  and must wait until these parity logs show **zero divergence on real traffic**.
  That burn-in is a calendar/data gate on the live system, not a code step.
- **No stage change.** `situation` stays `●` enforce (delivery + freeze); the
  intrusion caller remains a parity *mirror* (now with a decision-parity gate on
  top of the lifecycle mirror). **Coverage unchanged (17.9%).**

9 new tests: the kernel gate opens/closes correctly across away / motion /
corroboration combinations (incl. the no-corroboration false-alarm case and the
corroboration-not-required case); the parity recorder agrees on open and closed
and logs a constructed divergence; and the live `_check_intrusion` path records
`legacy_open=False` agreeing with the kernel gate while staying silent. Full
suite green; audit + adoption + coverage gates clean. Intrusion decision-parity →
**8.63.0 → 8.64.0**.

## [8.63.0] — MCU Phase R (R2b): freeze ENFORCE — the kernel owns the verdict, failing toward alerting

The freeze flip. The kernel's pipe-freeze verdict is now authoritative in
`SafetyManager._check_freeze` — and because this is a **safety alert**, it is
built to fail *toward* alerting, never away from it.

- **The branch is driven by the kernel verdict** (`kernel.situation.freeze_verdict`),
  chosen via a **max-severity rule**: the acted-on verdict is the *more severe* of
  (kernel, legacy inline threshold). So a kernel fault can never produce a
  *less*-severe outcome than the raw thresholds would — **a freeze alert is never
  missed**. On *any* kernel error the code falls back to the legacy category.
- **Behaviour-identical in normal operation.** R2a proved the kernel verdict
  equals the inline thresholds on every temperature, so the alert fires exactly
  when it did before; the max-severity rule is a safety belt, and the 1-hour
  cooldown, the `_freeze_warned` hysteresis, the messages and the hazard-situation
  mirror are all unchanged.
- **Kill-switch.** Flip `cognitive_core.HAZARD_SITUATION_ENFORCE` to `False` to
  revert to the pure legacy thresholds on the next load.
- **`situation` stays `●` enforce** (it was already, from delivery R1b); freeze is
  now a second authoritative caller. **Intrusion remains a parity mirror**, behind
  an explicit owner go/no-go. **Coverage unchanged (17.9%).**

6 new tests: the kill-switch off uses pure legacy (kernel ignored); a kernel error
fails toward the legacy alert; a *less*-severe kernel verdict still alerts (the
max-severity belt); a *more*-severe kernel verdict wins (alert-biased); and the
severity helper + default kill-switch. The existing freeze regression suite
(`test_cognitive_core_freeze.py`) still passes unchanged, pinning behaviour
identity. Full suite green; audit + adoption + coverage gates clean. Freeze
enforce flip → **8.62.0 → 8.63.0**.

## [8.62.0] — MCU Phase R (R2a): freeze decision-parity — the kernel earns the hazard verdict

Step (a) of the freeze re-architecture — the kernel computes the pipe-freeze
verdict so the hazard situation can eventually *own* it (today it only records a
lifecycle it is told). Log-only; no behaviour change.

- **New pure kernel verdict** `kernel.situation.freeze_verdict(temp_f, warn_f,
  critical_f)` → `critical` / `warning` / `clear` / `none`, mirroring the live
  `SafetyManager` thresholds exactly (critical ≤ 20°F, warning ≤ 35°F, clear
  above 35+5°F with hysteresis). Pure and unit-agnostic — the caller converts to
  °F first; the alert cooldown and the `_freeze_warned` hysteresis flag stay in
  the caller (orchestration, not the classification).
- **`cognitive_core._check_freeze` now logs the kernel verdict against its inline
  threshold category** on every evaluation (`hazard_situation.record_freeze_verdict_parity`).
  **Log-only** — the existing branches still drive the freeze alert, best-effort,
  never affecting alerting.
- **Safety note.** This step changes nothing about when a freeze alert fires. The
  *flip* (R2b) — making the kernel verdict authoritative — will **fail toward
  alerting**: on any kernel error or uncertainty the legacy threshold alert still
  fires. A freeze alert is never suppressed on doubt.
- **No stage change.** `situation` stays `●` enforce (delivery, from R1b); freeze
  remains a parity *mirror* until R2b. **Coverage unchanged (17.9%).**

8 new tests: the kernel verdict across boundary temps (critical/warning/dead-band/
clear/none incl. a `None` reading); a full sweep proving the kernel verdict equals
the live inline category exactly; and the parity recorder agreeing, logging a
divergence, and returning the kernel's true verdict. Full suite green; audit +
adoption + coverage gates clean. Freeze decision-parity → **8.61.0 → 8.62.0**.

## [8.61.0] — MCU Phase R (R1b): delivery ENFORCE — the kernel situation store owns the per-camera verdict

The flip that R1a's decision-parity earned. `situation` moves `◑` parity → `●`
enforce — but read *which caller*: only **delivery** becomes authoritative.

- **`package_monitor` now keys the delivered/removed transition off the kernel
  store's per-camera "package present" verdict** (an open `delivery` episode),
  not the in-memory `_STATE["package"]` flag. The kernel situation store genuinely
  owns this decision now — meeting the matrix's enforce bar (authoritative on ≥1
  live path, exactly as `world_model`'s enforce does).
- **Non-safety, kill-switched, fail-safe.** Flip
  `package_monitor.DELIVERY_SITUATION_ENFORCE` to `False` to revert to the legacy
  flag on the next load. On *any* kernel read error the transition falls back to
  the legacy flag, so announcements can never break; the worst case under enforce
  is a single mis-timed delivery announcement (the `_announce_gate` cooldown still
  collapses rapid repeats), never a safety miss.
- **Honesty — `hazard` and `intrusion` are NOT enforced.** They remain parity
  *mirrors*: their verdicts are still computed in the live modules and only
  recorded into the situation store. "situation = enforce" here means delivery
  presence is kernel-owned, not that every situation is. Freeze (R2) and intrusion
  (R3) are their own later flips — freeze will fail *toward* alerting, intrusion is
  behind an explicit owner go/no-go. **Coverage unchanged (17.9%).**

5 new tests: under enforce a delivery announces once and a second identical
detection does not re-fire; the kernel view is authoritative on divergence (a
"forgotten" kernel episode re-fires the transition and logs the override); the
kill-switch off reverts to the legacy flag; a `None` kernel view falls back to
legacy (fail-safe); and a removal still announces when away and resolves the
episode. Full suite green; audit + adoption + coverage gates clean. Delivery
enforce flip → **8.60.0 → 8.61.0**.

## [8.60.0] — MCU Phase R (R1a): delivery decision-parity — the kernel situation store earns the per-camera verdict

Groundwork for a genuine situation enforce. The G-phase review found the
`hazard`/`delivery`/`intrusion` situations were *recorders* of a verdict computed
elsewhere, not deciders — so "flip the situation to authoritative" had no sound
implementation. The honest path is to move the *verdict computation* into the
kernel via a strangler: **(a) decision-parity** (compute both, log divergence, no
behaviour change), then **(b) flip** the live path to consume the kernel verdict
once the logs show zero divergence. This is step (a) for the safest domain,
delivery (non-safety).

- **The kernel situation store now computes a per-camera "package present" view**
  — "is there an open (non-terminal) `delivery` episode for this camera?" — and
  `package_monitor._evaluate_locked` compares it against the legacy in-memory
  verdict (`prev["package"]`) on every evaluation, logging any divergence
  (`delivery_situation.record_presence_parity`).
- **Log-only, zero behaviour change.** The in-memory `_STATE` still owns the
  decision and drives every announcement exactly as before; the parity check is
  best-effort, runs off-loop (SQLite read via the executor), and never affects
  the state machine. A store-read failure is treated as "no kernel opinion", not
  a divergence.
- **No stage change in the matrix.** `situation` stays at **parity** — this earns
  the kernel the *right* to own the verdict (the enforce flip, R1b) only once real
  traffic shows agreement; it does not claim it yet. **Coverage unchanged (17.9%).**
- **Freeze (R2) and intrusion (R3) are deliberately not touched here.** Freeze is
  a safety alert (its flip will fail *toward* alerting); intrusion is
  safety-critical and behind an explicit owner go/no-go.

8 new tests: the kernel present-view is false with no episode, true after a
delivery, false after removal, and per-camera independent; parity agrees when
present and when absent; divergence is logged; a None kernel view is not counted
as a divergence; and the live `evaluate` path drives the parity check while
announcing unchanged. Full suite green; audit + adoption + coverage gates clean.
Re-architecture groundwork → **8.59.0 → 8.60.0**.

## [8.59.0] — MCU Phase G (G2): loop-detect ENFORCE — suppress a thrashing autonomous action

The second staged enforce flip, on the same tightly-scoped path as G1.

- **A thrashing discretionary autonomous actuation is now actually suppressed.**
  When JARVIS acts on a learned/trusted pattern of its own accord
  (`cognitive_core._execute_action_data`) and the identical action re-fires too
  many times in a tight window (flapping), or self-triggers through the event
  chain (A → event → A), the kernel loop detector's verdict is **authoritative**:
  the actuation is skipped and the cycle breaks. `kernel_adoption` moves
  `loop_detect` `◐` shadow → `●` enforce.
- **Same narrow scope as the budget flip (G1).** User-requested actuations (the
  agent tool path / `control_device`) and safety-critical responses (nighttime
  lockdown, intrusion securing — which call `hass.services` directly) **never
  route through the gate**, so neither a user command nor a safety action can
  ever be loop-suppressed.
- **Deliberately HIGH threshold.** Five firings of the *identical* action within
  60 seconds — far above any legitimate proactive cadence — so a genuine repeat
  is never mistaken for a loop. Checked before the budget gate, so a thrash
  doesn't even consume a budget slot.
- **Fails open + one-line kill-switch.** Any detector error allows the action;
  flip `actuation.LOOP_DETECT_ENFORCE` to `False` to revert to shadow on the next
  load. (The envelope-wide E4 shadow lens in `_agency_shadow` still logs thrash
  across the whole actuation stream, unchanged.)
- **Coverage unchanged (17.9%).** `loop_detect` is not a behaviour-spine contract.

11 new tests: the gate allows a non-looping action; suppresses a thrashing action
under enforce; keeps suppressing through the post-loop cooldown; never suppresses
with the kill-switch off (shadow); fails open on error; distinct actions don't
trip each other; and the live autonomous path executes when not looping, is
suppressed once thrashing (4 calls made, the 5th asserted *not* made), is
untouched with the kill-switch off, and a direct safety-style call is never
loop-suppressed. Full suite green; audit + adoption + coverage gates clean.
Second enforce flip → **8.58.0 → 8.59.0**.

## [8.58.0] — MCU Phase G (G1): agency budget ENFORCE — the first live enforce flip

The kernel migration's first **enforce** flip of a *decision* primitive on the
live home, taken deliberately as the safest possible first trial and staged
behind an owner go/no-go for the higher-risk flips that follow.

- **The self-imposed autonomous-action ceiling is now authoritative** on exactly
  one tightly-scoped path: a **discretionary autonomous** actuation — JARVIS
  acting on a learned/trusted pattern of its own accord
  (`cognitive_core._execute_action_data`). When the rolling-hour ceiling (kernel
  default 60 autonomous actions/hr) is reached, the actuation is actually
  **blocked**, not merely logged, so a feedback loop or an over-eager pattern
  can't flood the house. `kernel_adoption` moves `budget` `◐` shadow → `●` enforce.
- **Scope is deliberately narrow, and safety/user paths are untouched.**
  User-requested actuations (the agent tool path / `control_device`) and
  safety-critical responses (nighttime lockdown, intrusion securing — which call
  `hass.services` directly) **never route through the gate**, so neither a user
  command nor a safety action can ever be budget-blocked. The gate counts a
  dedicated autonomous-only population, not the whole actuation stream.
- **Fails open + one-line kill-switch.** Any internal budget error allows the
  action (a budget bug can never stop JARVIS acting). Flip
  `actuation.AGENCY_BUDGET_ENFORCE` to `False` to revert instantly to shadow
  (log-only, zero behaviour change) on the next load — no other edit needed.
- **Coverage unchanged (17.9%).** `budget` is not one of the seven behaviour-spine
  contracts, so the coverage number is honestly unaffected by this flip.

10 new tests: the gate allows under the ceiling and records; blocks at the ceiling
under enforce; never blocks with the kill-switch off (shadow); a blocked action
consumes no slot (the window recovers); fails open on error; the live autonomous
path executes under ceiling, is suppressed over ceiling (service call asserted
*not* made), and flows again with the kill-switch off; and a direct safety-style
service call is never budget-blocked. Full suite green; audit + adoption +
coverage gates clean. First enforce flip → **8.57.0 → 8.58.0**.

## [8.57.0] — Notifications-only speaker mute + persistent, visible announcement mutes (#181)

Two controls for people who don't want unsolicited speaker interruptions.

- **"Notifications only" speaker mute (new toggle, Settings → General).** When on, JARVIS **never speaks a proactive announcement on any speaker** — every urgency, *including critical*, routes to a phone/text notification instead. This closes the gap a user hit: the **Announcement Speakers** list only governs broadcasts, while lower-urgency proactive announcements speak on *whichever room you're in* (which is why an office-only setting didn't stop a kitchen briefing). The new toggle (`announce_notify_only`) short-circuits that at the single routing chokepoint (`observer_speak_target`), so there's no room-local bypass. Off by default — nothing changes unless you enable it.
- **Per-entity announcement mutes are now persistent and visible.** `jarvis.shush` (mute announcements about a specific entity or category without excluding it from observation) already existed, but mutes were memory-only and reset on restart, with no UI. They now persist to `<config>/jarvis/output_mutes.json` across restarts, and a new **Muted Announcements** card (Settings → Safety & Energy) lists them with one-click unmute and an add-by-entity box. This is the supported way to tell JARVIS "stop mentioning this often-open window / this decorative light" while it keeps tracking them.

New `jarvis/mutes` websocket (list/mute/unmute/clear) backs the card. 13 new tests (notify-only routing across every urgency + off-path, mute persistence across a simulated restart, blanket/category/entity, gate suppression) + panel smoke coverage. New feature → **8.56.0 → 8.57.0**. Full suite green; audit clean.

## [8.56.0] — MCU Phase F (F2): agency recovery — goals journal their plan

F2 makes the goal → plan → step chain **durably reconstructable** — the audit's point that JARVIS can act but can't pick up where it left off after a restart.

- **When a goal is created, its shadow `Plan` (F1) is now recorded into the kernel execution journal** (`ExecutionJournal.record_plan`), which persists every step as PENDING through the `kernel.persistence` seam. After a restart, the chain is reconstructable from the journal.
- **Shadow, log-only.** The journal is written but **never replayed in the live flow** — `recover()` is not invoked, nothing is re-run, the goal store stays authoritative, and goal creation is byte-for-byte unchanged. Best-effort: a journal failure never affects `goals.create`. `kernel_adoption` moves `journal` `·` pure → `◐` shadow (owner `goals`).
- **`causal`, `priority` left honestly pure.** No live path consults them yet, and wiring one without a genuine consumer would be a hollow adoption — so they stay at pure rather than being inflated. `persistence` stays pure by design (internal seam).
- **Coverage unchanged (17.9%).**

4 new tests: a goal's shadow plan is recorded into the journal with the right steps; no-step goals journal nothing; a real-journal roundtrip reads the step chain back (PENDING); and goal creation still works with the journal wired. Phase F structural work (F1 + F2) is complete; F3 (closed learning loop) is a north-star item surfaced to the owner, not built. Phase F → middle-digit bump **8.55.0 → 8.56.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.55.0] — MCU Phase F (F1): goals expressed as kernel Plans

F1 begins Phase F (agency). A goal is a plan pursued across time, so `goals` now expresses each goal's ordered steps as a kernel `Plan`.

- **`goals.create` mirrors the goal's steps into a `kernel.plan.Plan`** (`_shadow_plan`): one `Step` per goal step (the step text as the action, its number + status as params), under a `Plan` named for the goal. Built and logged alongside the goal.
- **Shadow, log-only.** The Plan is **never executed or consulted** — the goal store stays authoritative and goal creation is behaviour-identical. Best-effort: a failure never affects `goals.create`. `kernel_adoption` now lists `goals` as a live `plan` caller (alongside `actuation`, `agent`); `plan` stays at parity.
- **Coverage unchanged (17.9%)** — this deepens an existing primitive's adoption, not a behaviour-bearing spine path.

3 new tests: the goal-steps → Plan mapping (actions + params), the empty/None-steps safe paths, and `goals.create` still returning and persisting the goal with its steps intact. The goals suite stays green. Phase F → middle-digit bump **8.54.0 → 8.55.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.54.0] — MCU Phase E (E4): agency self-limits — loop detector + budget in shadow

E4 wires the two kernel primitives that watch JARVIS's *own* actuation rate. Both were **pure** (nothing consulted them); this brings them to **shadow** — not the "advisory→enforce" the roadmap sketched, because they were never advisory, and because *enforcing* them gates live actuation, which is owner-gated.

- **The actuation envelope now feeds every actuation into `kernel.loop_detect` and `kernel.budget`.** `actuation.emit_event` calls `_agency_shadow`, which records the action key into a module-level `LoopDetector` (thrash: A → event → A, or flapping within a window) and an `AgencyBudget` (a self-imposed autonomous-action ceiling, default 60/hour), and **logs** a loop or exhausted-budget verdict at WARNING.
- **Shadow / log-only.** The verdict is **never acted on** — no actuation is suppressed or deferred; the actuation path is byte-for-byte unchanged and the watchers are best-effort (a failure never touches actuation). `kernel_adoption` moves `loop_detect` and `budget` `·` pure → `◐` shadow (owner `actuation`).
- **Enforcing is owner-gated.** Promoting either to *enforce* — actually blocking a thrashing action or one that exceeds the self-imposed budget — changes what JARVIS does on the live home, so it is **not** taken here; it is on the owner decision list.
- **Coverage unchanged (17.9%)** — these are cognition/agency primitives on the *adoption* matrix, not behaviour-bearing spine paths.

4 new tests: `_agency_shadow` never raises; a rapid same-key actuation burst trips the thrash-loop warning; exceeding the 60/hour ceiling logs budget exhaustion; and `emit_event` still publishes the actuation event with the shadow active. The actuation + loop-detect + budget suites stay green. Phase E → middle-digit bump **8.53.0 → 8.54.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.53.0] — MCU Phase E (E3): router — local-first provider routing in shadow

E3 adopts `kernel.router`, which formalises JARVIS's local-first model/provider selection: given a task's requirements and the available providers, pick the best allowed one, preferring local.

- **`reasoning_loop` now computes the kernel route alongside its live decision.** When a sensor event has no fresh reasoning-cache hit, JARVIS routes to the cloud LLM if the connectivity breaker is closed, or to the local Mind if it is OPEN. `_router_shadow` builds the two providers (local always-available; cloud available iff the breaker allows a request) and runs `router.route` over them, logging any divergence at DEBUG.
- **Shadow, log-only, behaviour-preserving.** The kernel verdict is **ignored** — `connectivity`'s breaker stays authoritative. Crucially, `connectivity.allow_request()` is called **exactly once** and its value reused for both the shadow and the live branch: that call *mutates* the half-open probe counter, so calling it twice would waste the breaker's recovery probe. (A first pass that double-called it was caught by the reasoning-cascade recovery test and fixed before merge.) `kernel_adoption` moves `router` `·` pure → `◐` shadow (owner `reasoning_loop`).
- **Coverage unchanged (17.9%)** — router is a cognition primitive on the *adoption* matrix, not a behaviour-bearing spine path. Behaviour is identical.

4 new tests: the shadow never raises and agrees with the live decision online and offline (no divergence logged), plus the underlying kernel route mapping (cloud preferred when available, local fallback when the cloud is down). The reasoning-cascade + breaker-recovery suites stay green. Phase E → middle-digit bump **8.52.0 → 8.53.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.52.0] — MCU Phase E (E2): attention — the interruption gate runs in shadow

E2 adopts `kernel.attention`, which generalises the "may JARVIS interrupt right now?" decision (ALLOW / DEFER / SUPPRESS) that today lives in `output_gate` plus the adaptive interruption budget.

- **`output_gate` now computes the kernel arbitration alongside its own decision.** `_can_announce_with_multiplier` was split into the unchanged `_gate_decision` (the legacy verdict) plus a thin wrapper that runs `attention.arbitrate` over a context mapped from the gate's live state (priority from urgency, recent-interruption count, effective cap, blanket-shush, duplicate) and logs any divergence at DEBUG.
- **Shadow, log-only.** The kernel verdict is **ignored** — `output_gate` stays authoritative, every existing announce/suppress/dedup/rate-limit/shush decision is byte-for-byte unchanged, and the shadow is best-effort (a failure never touches the gate). `kernel_adoption` moves `attention` `·` pure → `◐` shadow (owner `output_gate`).
- **Coverage unchanged (17.9%)** — attention is a cognition primitive on the *adoption* matrix, not a behaviour-bearing spine path. Behaviour is identical.

7 new tests: the gate's decisions (normal allow, blanket-shush blocks even critical, critical bypass, dedup) are unchanged with the shadow active; the shadow helper runs across all priority mappings without raising and logs on divergence; and the duplicate→SUPPRESS mapping is pinned. The full output-gate + budget suites stay green. Phase E → middle-digit bump **8.51.0 → 8.52.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.51.0] — MCU Phase E (E1): beliefs — knowledge confidences as a kernel belief model

Phase E begins the cognition primitives. E1 adopts `kernel.beliefs`: the flat per-fact `confidence` numbers scattered across the knowledge store are now also expressible through the kernel's probabilistic belief model (log-odds pooling, evidence, decay, contradiction).

- **New `WorldModel.beliefs(subject=None)`** reads the curated knowledge facts and returns them as kernel `Belief` values (`seed_from_confidence`, probability = the fact's confidence), prefixed by JARVIS's minimal identity self-assertion. Best-effort — the identity belief always stands even if the knowledge store is unavailable.
- **New minimal `beliefs.identity_assertion()`** (north-star seed): one stable, high-confidence self-belief — *"JARVIS is the household's home assistant"*. Pure and additive.
- **The `cognitive_status` tool surfaces a read-only belief snapshot** (self-assertion + count + a small sample), making `agent` the live caller. **Shadow**: this is introspection only — no decision consumes beliefs, and the knowledge store stays authoritative. `kernel_adoption` moves `beliefs` `·` pure → `◐` shadow (owner `agent`).
- **North-star boundary, respected:** surfacing the identity assertion into the *live conversation context* (telling the model who it is) is a user-visible self-model change. Per the standing guardrail I did **not** enable that — it is **proposed** for your decision, not built. The self-belief currently lives only in the belief *view*, consumed by nothing.
- **Coverage unchanged (17.9%)** — beliefs is a cognition primitive tracked by the *adoption* matrix, not a behaviour-bearing spine path. Behaviour is identical; the snapshot only appears when the `cognitive_status` tool is explicitly called.

4 new tests: the identity assertion (minimal, customisable, high-confidence), `WorldModel.beliefs` mapping fact confidences to belief probabilities with the identity prefix, and the best-effort/empty-knowledge paths. Phase E → middle-digit bump **8.50.0 → 8.51.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.50.0] — MCU Phase D complete (D4): situations enter the event stream

The final Phase D step. Every tracked situation — intrusion (D1), freeze hazard (D2), package delivery (D3) — now **publishes a canonical `JarvisEvent`** on the kernel event bus when its lifecycle changes, so situations flow through the nervous system the same way perception and actuation already do (MCU audit item #8).

- **New `from_situation` event builder + `EVENT_SITUATION` ("situation.transition")** in `kernel.event`, exported from the kernel package. It carries the situation `kind`, resulting `state`, the `action` (verdict) that drove the transition, `subject`/`location`, and the `situation_id`.
- **New `events.publish_situation(hass, situation, action=...)`** — the HA-aware bridge that builds the event and publishes it best-effort (no bus wired → silent no-op; never raises).
- **The three situation mirrors publish on every transition.** Right after each mirror records parity, it fetches the resulting situation and publishes a `situation.transition` event. **Parity, not enforce:** the event enters the bus stream and the ledger records it, but **no consumer reacts to it yet**. Publishing is additive, off-loop and best-effort — it never affects intrusion/freeze/delivery handling.
- **Honest coverage rises 15.5% → 17.9%.** This is real wiring, not a relabel: the `event` cell flips to `◑` parity on the `intrusion`, `hazard` and `delivery` paths (evidence: `publish_situation` present in each). The adoption matrix adds `events` as a live `event` caller.
- **Still log-only / owner-gated.** Nothing consumes these events to drive a decision; that (and any situation→authoritative flip) remains an owner-approved step, deliberately not taken.

**This completes MCU Phase D** — Situations are now a first-class, event-publishing part of the kernel across intrusion, hazards and deliveries, all at parity. 6 new tests: the `from_situation` builder, `publish_situation` (reaches the bus; silent without one), and each mirror publishing a correctly-shaped `situation.transition` event on transition. Phase D → middle-digit bump **8.49.0 → 8.50.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.49.0] — MCU Phase D (D3): package deliveries modelled as kernel Situations

Phase D's third situation kind: **deliveries**. The per-camera package lifecycle `package_monitor` already tracks (delivered → sits → removed) is now mirrored into a kernel `Situation` (`kind="delivery"`, `subject=<camera entity_id>`) — parity, log-only — one open episode per camera.

- **New `delivery_situation` module** mirrors the delivery lifecycle into `kernel.situation` and (like D1/D2) **verifies** agreement: `delivered ≥ INVESTIGATING`, `removed` → terminal, with a **WARNING** on divergence. It tracks one open episode per camera, so two porches can have independent deliveries in flight.
- **`package_monitor._evaluate_locked` drives it best-effort, off-loop** via `_mirror_delivery` (swallow-all, `async_add_executor_job`), placed right after the existing `_log(...)` calls on the `delivered` and `removed` transitions. The **announcements and per-camera state machine are unchanged** — same once-on-arrival package/mail speech, same removed-while-away alert, same cooldowns. A mirror failure never touches delivery handling.
- **Only the package episode is mirrored.** Mail arrival is a one-shot with no pickup tracking, so there is no sustained episode to mirror and none is invented.
- **Honest coverage *dips* 16.0% → 15.5%.** A new `delivery` path joins the matrix with only its `situation` cell wired. The adoption matrix now lists `delivery_situation`, `hazard_situation`, `intrusion` as live `situation` callers.
- **Still log-only / owner-gated.** Nothing here is authoritative.

8 new tests: the per-camera mirror lifecycle (delivered → removed; two cameras independent; idempotent redundant-delivered; removed-with-no-episode; forced divergence; never-raises) plus an integration test proving `package_monitor.evaluate` still announces once on arrival while opening/resolving the delivery situation. The existing package-monitor suite stays green. Phase D → middle-digit bump **8.48.0 → 8.49.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.48.0] — MCU Phase D (D2): freeze hazard modelled as a kernel Situation

Phase D widens Situations past intrusion to the first **hazard**. The in-home freeze lifecycle (`SafetyManager._check_freeze`: warning → critical → cleared) is now mirrored into a kernel `Situation` (`kind="hazard"`, `subject="freeze"`) — parity, log-only — so the generalised state machine tracks hazards the same way it tracks intrusion.

- **New `hazard_situation` module** mirrors the freeze lifecycle into `kernel.situation` and (like D1) **verifies** agreement: after each verdict, the kernel situation's resulting state is compared against what the verdict implies (`warning ≥ INVESTIGATING`, `critical ≥ CONFIRMED`, `cleared` → terminal), logging a **WARNING** on any divergence.
- **`SafetyManager._check_freeze` drives it best-effort, off-loop.** The mirror runs via `async_add_executor_job` inside a swallow-all wrapper (`_mirror_freeze_hazard`) placed *after* the freeze decision is computed — the freeze **alerts are byte-for-byte unchanged** (same `freeze_critical` / `freeze_warning` actions, same thresholds, same cooldown). A mirror failure never touches freeze handling.
- **No invented detection.** Smoke / CO / water-leak are not sensed by JARVIS today, so there is nothing to mirror for them — D2 covers the one in-home hazard that has a real lifecycle, and adds no new detection.
- **Honest coverage *dips* 16.7% → 16.0%.** A new `hazard` path joins the matrix with only its `situation` cell wired; counting its six un-wired cells lowers the percentage. That is the matrix working as intended — surfacing a newly-tracked path rather than hiding it. The adoption matrix adds `hazard_situation` as a live `situation` caller.
- **Still log-only / owner-gated.** Nothing here is authoritative; flipping any hazard or intrusion situation to drive the live decision remains an owner-approved step, deliberately not taken.

10 new tests: the freeze mirror lifecycle (warning → critical → cleared; critical-without-warning; cleared-with-no-episode; forced divergence; never-raises), plus integration tests proving `_check_freeze` returns the **same** critical/warning/none actions while driving the mirror. The existing freeze regression suite stays green. Phase D → middle-digit bump **8.47.0 → 8.48.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.47.0] — MCU Phase D (D1): intrusion situation earns genuine parity

Phase D begins: making kernel **Situations** the authoritative lifecycle — structurally, at **parity / log-only**, never flipping the live decision. D1 starts with the first and most sensitive consumer, intrusion.

- **The intrusion → `kernel.situation` mirror now *verifies* agreement, not just copies.** Previously the mirror blindly translated each intrusion lifecycle event into a situation transition (a *shadow* copy, despite the coverage matrix already declaring it parity). D1 adds `_record_parity`: after each mirrored event, the kernel situation's resulting state is compared against what the legacy verdict implies (a monotonic lifecycle rank — `investigating ≥ INVESTIGATING`, `confirmed ≥ CONFIRMED`, `unresolved`/`dismissed` → terminal) and the agreement is recorded and logged. A **divergence** (verdict says confirmed but the kernel situation lags) is logged at **WARNING** so a mirror bug becomes visible. This earns the *parity* the matrix already claimed — the kernel situation is now provably tracking the legacy verdict, not silently drifting.
- **Still log-only and owner-gated.** Nothing here is authoritative: the legacy `SafetyManager` path owns the intrusion decision. The parity check never affects intrusion handling — it stays inside the existing best-effort, off-loop mirror (a failure is swallowed). **Flipping the intrusion situation to authoritative is a separate, owner-approved step and is deliberately NOT taken.**
- **Coverage unchanged (16.7%)** — `intrusion`'s `situation` cell was already declared parity; D1 makes that declaration *honest* (evidence retargeted from the bare `situation` reference to the `_record_parity` check) rather than moving the number. The v6.7.1 false-intrusion protections are untouched.

5 new tests: parity agreement across the full lifecycle (investigating → confirmed → unresolved; dismissed → benign → resolved), the "already further along" non-divergence case, the "no open episode → not scored" case, and a forced-divergence test asserting the WARNING fires. The existing mirror + intrusion regression suites stay green. Phase D → middle-digit bump **8.46.0 → 8.47.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.46.0] — MCU Phase C (C4): WorldModel is authoritative — adoption → enforce

C1–C3 routed presence / home-summary / briefing context through the kernel `WorldModel` facade. C4 makes that **enforce**: the facade is the *authoritative* read path on those surfaces, not merely consulted alongside raw state — and proves it with tests that have teeth.

- **The gap C4 closes:** because the facade preserves state verbatim, the C1–C3 tests (which seed HA state and check the rendered output) pass *whether the code reads the facade or the raw sweep* — they can't catch a silent regression back to `hass.states.async_all`.
- **`test_c4_worldmodel_authoritative`** closes it: each test makes `WorldModel.devices(...)` return something that **differs** from raw HA state and asserts the **facade's value wins** — on `SafetyManager._residents_away` (C2), `home_state._build_summary` (C3), the `get_home_summary` agent tool (C3), and `proactive_briefing._anyone_home` (C3). A revert to a raw read now fails CI.
- **`kernel_adoption` promotes `world_model` ◑ parity → ● enforce.** This is defensible for a specific, honest reason: `home_state._build_summary` and `agent._exec_home_summary` read the facade with **no** raw fallback, so the facade is unambiguously authoritative on ≥1 live path (the adoption definition of enforce). The raw sources survive underneath only as a *failure* fallback for the safety / best-effort callers.
- **This is a read-only enforce, not a decision enforce.** The doc now draws the line explicitly: a read-context facade may reach enforce on its own because it changes nothing about what JARVIS *does*; flipping an **authority / situation / safety decision** from log-only to authoritative on the live home stays **owner-gated** and un-taken. Authority remains **log-only / parity**.
- **Coverage unchanged (16.7%)** — the behavioural spine matrix is a different axis; `control_device`'s `world_model` cell stays parity (its post-action verify still reads raw HA state). C4 moves the *adoption* stage, not a spine cell. Context reads in still-legacy modules (`identity`, `presence`, `cognition`, `local_engine`, `observer`, …) remain to be migrated per-release.

4 new enforcement tests; full suite green. Read-side only. Phase C → middle-digit bump **8.45.0 → 8.46.0**. Audit + adoption + coverage gates clean.

## [8.45.0] — MCU Phase C (C3): home-summary & briefing context through WorldModel

Phase C's read side widens to the **summary / proactive context** surfaces. The builders that describe the home to JARVIS — the system-prompt snapshot, the `get_home_summary` tool, and the proactive-briefing arrival check — now read the world through the kernel `WorldModel` facade (the context authority, audit item #6) instead of scattered raw-state sweeps.

- **`home_state._build_summary`** (the 60-second system-prompt snapshot) now reads every domain — sensors, lights, locks, covers, door/window binary_sensors, the alarm panel, and media players — through `WorldModel.devices(domain=…)`.
- **The `get_home_summary` agent tool (`agent._exec_home_summary`)** now reads people, lights, locks, doors/covers, climate, and weather through the facade.
- **`proactive_briefing`** (`_anyone_home` and the in-briefing home check) reads presence through the facade.
- All of these read the **same** `hass.states.async_all(domain)` source and preserve each entity's `state` and `attributes` **verbatim**, so every rendered summary string and JSON field is **behaviour-identical** — the raw sources stay underneath and authoritative.
- **Coverage unchanged (16.7%)** — adoption *breadth*, not a new actuator-spine cell. `kernel_adoption` now lists `agent`, `home_state`, and `proactive_briefing` as live `world_model` callers (joining `actuation`, `cognitive_core`); the facade is now the read authority for every home-description surface.
- **`proactive_audio`'s area-filtered ambient telemetry is deliberately deferred** — it re-resolves each sensor's area via `audio_routing.entity_area` regardless, and the facade's own `area=` filter resolves areas differently, so routing it through the facade would add overhead without changing the read authority (and couldn't be proven behaviour-identical as cleanly). Left as a raw sweep rather than migrated imperfectly.

5 new tests: the two home-summary builders are pinned against a seeded world (light/lock/cover/door/alarm/media/temperature rendering; people/climate/weather JSON; the room-temperature filter and all-off phrasing). The existing `proactive_briefing._anyone_home` fail-closed test already covers that migration. Read-side only; authority stays **log-only / parity**. Phase C → middle-digit bump **8.44.0 → 8.45.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.44.0] — MCU Phase C (C2): intrusion-safety presence reads through WorldModel

The careful C2 release the previous one deferred. The signals that decide whether motion is a possible **intruder** — "are the residents away?", "is anyone home?", "is the alarm armed?" — now read the world through the kernel `WorldModel` facade instead of scattered raw-state sweeps. This is the safety-sensitive counterpart to C1, so it ships behaviour-identical and fail-safe.

- **`SafetyManager._residents_away`, `LockdownManager._anyone_home`, and `_alarm_armed` (both managers) now read presence/alarm/occupancy through `WorldModel.devices(domain=…)`.** The facade reads the **same** `hass.states.async_all(domain)` source and preserves every entity's `state` and `attributes` verbatim, so each away/home/armed decision is **provably behaviour-identical** — iteration is one-to-one with the legacy loop.
- **Fail-safe, not best-effort-empty.** Unlike C1, an *empty* person/tracker list is a **meaningful** "untracked" signal for the away-check (it is what prevents the false "motion … while no one is home" alerts), so it is never treated as a facade miss. Instead, any facade **exception** falls back to the exact legacy raw-state sweep — the safety decision can never regress, and these reads can never newly raise.
- **No intrusion decision logic changed** — only where the presence context is *read*. Authority / situation stay **log-only / parity**; nothing was flipped to enforce.
- **Coverage unchanged (16.7%)** — this is adoption *depth* on the already-parity `cognitive_core` world_model caller, not a new actuator-spine cell (the intrusion spine path tracks the Situation lifecycle in `intrusion.py`, which does no presence reads).

21 new parity tests: a 14-world presence/alarm/occupancy matrix asserts each migrated method equals a verbatim legacy-logic oracle, plus three fallback tests proving a broken facade yields the identical legacy decision without raising. The v6.7.1 false-intrusion regression suite stays green. Read-side only. Phase C → middle-digit bump **8.43.0 → 8.44.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.43.0] — MCU Phase C (C1): cognition reads presence through WorldModel

Phase C makes the kernel `WorldModel` the **context authority** — cognitive paths read the world through the facade rather than scattered raw-state sweeps (the audit's item #6). It starts read-side and low-risk; nothing about what JARVIS *does* changes, only where it *reads* its context.

- **The main cognitive tick's `anyone_home` check now reads through `WorldModel.devices("person")`** instead of a bare `hass.states.async_all("person")` sweep. Because the facade reads the **same** person entities, this is **behaviour-identical** — it just routes cognition's presence context through the canonical authority. Best-effort: falls back to the raw sweep if the facade yields nothing.
- **The intrusion-safety away-check is deliberately *not* touched here** — it migrates separately in C2, where safety context gets its own careful release.
- **Coverage unchanged (16.7%)** — this is adoption *breadth*, not a new actuator-path cell. `kernel_adoption` adds `cognitive_core` as a live `world_model` caller.

1 new parity test (WorldModel.devices("person") home-detection matches the raw person states). Read-side only; authority stays **log-only**. Phase C → middle-digit bump **8.42.0 → 8.43.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.42.0] — MCU Phase B complete (B4b): control_device execution runs through the kernel planner

The final Phase B step. `control_device` expressed its actuation as a kernel `Plan` only in *shadow* (logged, not executed through), because the planner was synchronous. With the async driver (B-async) in place, its execution now genuinely **routes through the kernel planner**.

- **`control_device`'s actuation runs through `kernel.plan.aexecute_plan`** — a one-step plan whose `run_step` performs the awaited `hass.services.async_call` (precondition: the entity exists). The planner owns execution; the redundant shadow-plan log is gone. Applies to every branch (the `action_map` set *and* the parametric brightness/temperature/volume actions).
- **Behaviour preserved**: the confirm-gate + authority parity still run *before* the plan (fail-closed, `awaiting_confirmation` unchanged); verify-after-act (`_verify_control`) is still the background verify *after*; `previous_state`/`area`/success JSON unchanged; a failed/blocked plan returns the same `{"error": "Failed: …"}` a raised service call did. Authority stays **log-only**.
- **Honest coverage: 15.7% → 16.7%.** `control_device` `plan` rises `◐` → **● full**. Its row is now `event ◑ · world_model ◑ · authority ◑ · plan ● · verify ● · outcome ●` — only `situation` is `·` (by design) and `authority` stays at parity pending the owner-gated enforce decision.

**This completes MCU Phase B** — every consequential actuator (`control_device`, `run_scene_or_script`, `set_mode`, `execute_plan`, `bulk_control`) is on the shared kernel actuation contract. 3 new/updated tests (control_device + parametric actions route through the planner; a failing service call returns an error, not a bogus success). Kernel wiring → middle-digit bump **8.41.0 → 8.42.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.41.0] — MCU Phase B (B3): bulk_control as an explicit plan

The audit flagged `bulk_control` as higher-risk than a single action — *"turn everything off" should produce a plan with explicit targets, not let a bulk helper become a privileged shortcut.* This migrates it onto the kernel contract.

- **The batch is now recorded as one explicit N-step kernel `Plan`** (shadow) — one `Step` per resolved target, with the goal and target count — so a bulk operation is an inspectable plan, not an opaque loop.
- **Each executed target routes through the shared `actuation` envelope**: WorldModel context (surfacing its area), a canonical `ActuatorRequest`, and a canonical actuation `JarvisEvent` on the bus.
- **Behaviour preserved**: same target resolution (by area+domain or all-in-domain), the same action filter (don't turn off already-off, don't lock already-locked), **fire-and-forget** (`blocking=False`), the same **protected-device skip** (protected devices are still not run in bulk), and the same `count/total/blocked` result JSON. Because it's fire-and-forget, there is no per-device verify/outcome. Authority stays **log-only**.
- **Honest coverage: 13.3% → 15.7%.** `bulk_control` rises from all-`·` to `world_model ◑`, `event ◑`, `plan ◐`.

3 new tests (bulk turn_on publishes an event per target; turn_off filters already-off devices; protected devices are skipped with no event). Kernel wiring → middle-digit bump **8.40.0 → 8.41.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.40.0] — MCU Phase B (B4): execute_plan on the kernel planner

The audit noted the project had a real `kernel.plan` the live agent's `execute_plan` never used — "a reference implementation, not the runtime planning authority." This migrates it: **every step of a multi-step plan now executes *through* the kernel planner** (`kernel.plan.aexecute_plan`, the async driver from B-async).

- **Each step runs as a one-step kernel plan** — precondition (entity exists) → act (the awaited `hass.services.async_call`, as the planner's `run_step`) → record — and publishes a canonical actuation `JarvisEvent`. The kernel planner, not an ad-hoc loop, now drives execution.
- **Behaviour is preserved.** Steps still run independently and the loop **continues on failure**, collecting every per-step result (that's why each step is its *own* one-step plan rather than one N-step plan — the planner stops at the first failure). Validation, the per-step confirm-gate (fail-closed), and the `goal/total/succeeded/failed/results` JSON are unchanged. Authority stays **log-only**.
- **Honest coverage: 11.0% → 13.3%.** `execute_plan` rises from all-`·` to `plan ●` (full — the planner owns execution) and `event ◑`. `kernel_adoption` `plan` `shadow` → **◑ parity** (it now drives real execution, not just shadow-logging).

5 new tests (multi-step success routes through the planner; continue-on-failure with a missing entity; missing-fields error; per-step confirm-gate blocks one step while others run; empty plan). Kernel wiring → middle-digit bump **8.39.0 → 8.40.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.39.0] — MCU Phase B (B-async): the async plan driver

A prerequisite, not a path migration. Real HA actuators `await` their service calls, but `kernel.plan.execute_plan` is **synchronous** — so no path could route an *awaited* action *through* the plan contract (which is why `control_device.plan` has been stuck at shadow). This adds the async sibling.

- **New `kernel.plan.aexecute_plan`** — mirrors `execute_plan` exactly (same stages, statuses and semantics: idempotency skip → preconditions → act → verify-with-retry, stopping at the first BLOCKED/FAILED/VERIFY_FAILED, updating `completed` in place), with **awaitable** `run_step` / `check` callables. **Still pure** — no Home Assistant import, no I/O of its own; the side effects are the injected awaitables, so it stays deterministic and unit-testable.
- No live path adopts it yet, so **the coverage matrix is unchanged (11.0%)**. This unblocks **B4** (migrate `execute_plan` onto the kernel planner) and lets `control_device.plan` go shadow → full once its actuation routes through an awaited plan.

7 new async tests (happy path, precondition block, idempotency skip, verify-retry-then-ok, verify-failed, action-raises-is-failed, default check). Kernel addition → middle-digit bump **8.38.0 → 8.39.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.38.0] — MCU Phase B (B2): set_mode on the kernel contract

Second Phase B adoption — `set_mode` (switch JARVIS's operational mode) records its change through the shared `actuation` envelope.

- **`set_mode` routes through the envelope**: a canonical `ActuatorRequest`, a one-step shadow `Plan`, and a canonical actuation `JarvisEvent` on the bus, emitted right after the mode scene is applied. **`set_mode` is a directive, not a single-entity actuation** — its home effect is the applied mode scene — so there is **no WorldModel entity context** (no `world_model` cell) and **no deterministic end-state** (no verify/outcome). The actuation event's target is the **mode name**. **No behaviour change** — same mode switch, same result JSON; the envelope only describes and records.
- **Honest coverage: 9.5% → 11.0%.** `set_mode` rises from all-`·` to `event ◑`, `plan ◐`.

2 new tests (a successful mode switch publishes the actuation event with capability `jarvis.set_mode` and the mode as target; a failed switch publishes nothing). Authority stays **log-only / owner-gated** — unchanged. Kernel wiring → middle-digit bump **8.37.0 → 8.38.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.37.0] — MCU Phase B (B1): run_scene_or_script on the kernel contract

First Phase B *adoption* — now that B0 extracted the shared `actuation` envelope, migrating a path is small. `run_scene_or_script` (activate a scene / script / automation) is the lowest-risk actuator, so it goes first.

- **`run_scene_or_script` routes through the envelope**: WorldModel pre-action context (and surfaces the resolved `area` in the result), a canonical `ActuatorRequest`, a one-step shadow `Plan`, and a canonical actuation `JarvisEvent` on the bus. A scene/script/automation has **no single expected end-state**, so there is deliberately no verify-after-act / outcome here; this path has no confirm-gate, so no authority cell. **No behaviour change** to the activation itself — same service call (`scene`/`script` → `turn_on`, `automation` → `trigger`), same success/error, plus `area`.
- **Honest coverage: 7.1% → 9.5%.** `run_scene_or_script` rises from all-`·` to `world_model ◑`, `event ◑`, `plan ◐`.

4 new tests (scene activation publishes the actuation event with capability/area; script→turn_on; automation→trigger; an invalid target still errors without touching the envelope). Authority stays **log-only / owner-gated** — unchanged. Kernel wiring → middle-digit bump **8.36.0 → 8.37.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.36.0] — MCU Phase B (B0): the shared actuation envelope

Phase B is *"migrate every actuator onto the golden path."* Phase A's kernel wiring lived inline in `control_device`; copying it into every other actuator would be a maintenance trap. So Phase B starts by **extracting it into one reusable envelope** — the template every other path will adopt.

- **New `custom_components/jarvis/actuation.py`** — the golden-path wiring as pure, best-effort glue over the kernel primitives: `context()` (WorldModel pre-action snapshot), `request()` (canonical `ActuatorRequest` with expected end-state), `plan_shadow()` (one-step `kernel.plan.Plan`), `emit_event()` (actuation `JarvisEvent` on the bus), and `outcome()` (`ActuatorOutcome`). Every function degrades to a null result and never raises into a caller's path, so wiring a path onto the envelope can't break it. Authority stays **log-only** — the envelope describes and records an actuation; it does not perform or gate the service call.
- **`control_device` refactored onto the envelope** with **no behaviour change**. Its context read, request build, plan-shadow, event emission and outcome recording now call `actuation.*`; the verify-after-act loop is unchanged.
- **Coverage unchanged at 7.1%** (this is a refactor, not new wiring). The `control_device` cells keep their stages; their evidence now points at the `actuation.*` call sites. `kernel_adoption` shows `actuation` as the live caller for `world_model`/`plan`/`event`/`actuator`/`correlation` (was `agent`).

7 new tests for the envelope directly + the existing control_device suite re-pointed at it; all green. This unblocks B1–B4 (each remaining actuator becomes a small adoption). Refactor/feature → middle-digit bump **8.35.0 → 8.36.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.35.0] — Faces: best-effort LLM resident recognition (#140 Phase 3, opt-in)

For households with **no dedicated face backend** (Frigate/DoubleTake/CompreFace) — only JARVIS's own local vision model — Phase 1's resident whitelist had nothing feeding it names, so the Faces tab stayed empty. This adds an **opt-in, best-effort** path: JARVIS asks its vision model whether a camera frame matches an enrolled resident reference photo.

It is deliberately a **guess, not a recognition**, and is built so it can never cause harm:

- **Hard safety boundary.** Guesses are written to a **separate cache** that the intrusion stand-down (`recognition.resident_present`) **never reads** — a mis-identified stranger can *never* disable an intrusion alert. (Pinned by a regression test.) Guesses only feed the Faces panel.
- **Clearly labelled.** Guessed faces show a distinct **LLM GUESS** badge, never the authoritative RESIDENT badge, and a trusted backend recognition always outranks a guess for the same person/camera.
- **Opt-in & off by default.** A new **Best-effort face recognition (experimental)** toggle in the Faces tab (`llm_face_recognition`, default off). When off, nothing changes.

How it works:
- **`llm_recognition.py`** (new) — on a camera analysis (reusing the frame already captured, fire-and-forget), it sends the enrolled resident reference photos + the current frame to the configured vision model and parses a conservative `{name, confidence}`; it names a resident only on a clear match, else `unknown`. Never raises, never delays the normal analysis.
- **Reference photos** — each resident card in the Faces tab gets a *Set photo* upload. References are stored privately at `<config>/jarvis/faces_ref/<name>.jpg` (filename hardened against path traversal, since the name can originate from an external source).
- **`jarvis/faces`** gains `set_reference` / `remove_reference`; removing a resident also drops their reference.

For dependable recognition, Frigate's native face recognition is still the recommended route — this is for users who can't run one yet. 26 new tests (incl. the resident_present-ignores-guesses safety test, reference store + traversal safety, and the matcher's parse/gate/reference logic) + panel smoke coverage. New feature → **8.34.1 → 8.35.0**. Full suite green; audit clean.

## [8.34.1] — Quick Actions tooltips; clarify what Nap does (#157)

A user saw blinds close and (via a guess from another tool) assumed JARVIS's **Nap** button did it. It didn't — Nap only mutes JARVIS's non-critical announcements for N minutes and never commands lights, covers/blinds, or locks. The confusion came from the dashboard's Quick Actions buttons carrying no explanation.

- Every **Quick Actions** button now has an explanatory `title` tooltip. The **Nap** buttons spell out that they only quiet proactive speech and do **not** touch blinds, lights, or locks (safety alerts still come through); Briefing, Unshush All, Status Dump and Analyze Now each describe what they do.

No behavior change — purely explanatory UI. The only JARVIS feature that closes window coverings remains the opt-in `goodnight` routine, which is fully overridable via `/config/jarvis_routines.yaml`. Panel `node --check` + smoke test (new Nap-tooltip regression) clean; audit clean.

## [8.34.0] — MCU Phase A (5/5): the governance rule, enforced in CI

The final Phase A step — the audit's **"rule I would add now"**: *any new behaviour that can cause a consequential action on the home must enter through the kernel contract from the start.* This release writes it down **and enforces it**, so the migration debt the audit warned about (features outpacing the kernel — proven by 8.27→8.29 moving coverage 0.0 points) cannot grow silently.

- **New governance gate in `scripts/kernel_coverage.py --check`** (already run by CI). It fails if a tool in agent's `_TOOL_MAP` is **neither** a declared coverage path in `_PATHS` **nor** in the new `_NON_ACTUATOR_TOOLS` allowlist (read-only / informational / bookkeeping tools). A newly-added actuator tool therefore can't land uncounted — the author must wire it as a path or explicitly classify it as a non-actuator. The `_TOOL_MAP` keys are parsed from source with `ast` (no import), keeping the script stdlib-only.
- **The gate paid for itself immediately.** It surfaced two consequential tools that were acting on the home without appearing in the coverage matrix at all — **`run_scene_or_script`** (activates scenes/scripts/automations) and **`set_mode`** (applies a mode scene via `mode_scene`). Both are now declared as the legacy (all-`none`) paths they are.
- **Honest coverage: 8.9% → 7.1%** — it *dropped on purpose*. Declaring the two previously-invisible consequential paths grew the denominator; a lower, complete number beats a higher, partial one. This is exactly the honesty the matrix exists to enforce.
- **The rule is documented** in `KERNEL_COVERAGE.md` (new "Governance rule" section) and `CONTRIBUTING.md` (new "Kernel contract rule for new actions" section, plus the adoption/coverage gates added to the local-checks list).

This completes **MCU Phase A**: `control_device` is the golden end-to-end kernel path — `world_model` ◑, `outcome` ●, `event` ◑, `plan` ◐, alongside `authority` ◑ (log-only) and `verify` ● — and the governance rule keeps every future consequential action on the kernel contract. 6 new tests (gate satisfied, known actuators declared, gate catches an undeclared actuator, allowlisted tool accepted, the `ast` parser). **Authority remains log-only / owner-gated — the enforce flip is a separate, explicit decision and was not taken.** Kernel/governance → middle-digit bump **8.33.0 → 8.34.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.33.0] — MCU Phase A (4/5): control_device expresses the actuation as a kernel Plan

Step 4 of 5 of the Phase A golden path — the **plan** contract on `control_device`. The audit noted the project has a real `kernel.plan` (preconditions → act → postconditions, idempotency, retries) that the live agent hadn't adopted. This release has `control_device` express each actuation as a canonical one-step kernel `Plan`.

- **`control_device` builds a canonical one-step `kernel.plan.Plan`** for every executed actuation — a `Step` with `preconditions=("exists:<entity>",)`, `postconditions=("state:<expected>",…)` for deterministic targets, and an `idempotency_key` of `"<entity>:<action>"` — and logs it (`_shadow_control_plan`). No behaviour change; the legacy path still performs the action.
- **Deliberately shadow, not full.** `kernel.plan.execute_plan` is **synchronous** while HA actuation is `await`-ed, so routing *execution itself* through the plan contract needs an async plan driver — a later step. Expressing the actuation as a `Plan` object on real traffic (exactly how `ActuatorRequest` began) exercises the contract's shape before anything depends on it. Journaling each actuation was considered and **deliberately not done**: a single fire-and-verify control action completes in-request and needs no crash recovery, so opening a SQLite journal per action would add live I/O cost and a failure surface for no benefit — the journal belongs with multi-step `execute_plan` adoption.
- **Honest coverage: 8.3% → 8.9%.** `control_device × plan` rises `·` → **◐ shadow**. `kernel_adoption` `plan` `pure` → **◐ shadow** (owner `agent`). Verified by CI gates against evidence in source.

3 new tests (the plan's step shape — action/params/preconditions/postconditions/idempotency; no postconditions for a non-deterministic action; and that `control_device` builds the plan with the right capability/entity/action/expected). Authority stays **log-only / owner-gated** — unchanged. Kernel wiring → middle-digit bump **8.32.0 → 8.33.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.32.0] — MCU Phase A (3/5): control_device publishes a canonical actuation event

Step 3 of 5 of the Phase A golden path — the **event** contract on `control_device`. The audit's item #8: the event bus should be JARVIS's *nervous system*, carrying not just what the home did (`state_changed`) but what **JARVIS did**. Until now only perception (state changes, camera analysis, voice turns) produced `JarvisEvent`s; actuations didn't enter the stream.

- **New kernel event builder `from_actuation()`** (`kernel.event`, type `control.actuation`, source `actuator`) — the canonical record that *JARVIS acted*: who (actor), what capability, on what target, why (intent), linked to the correlated `ActuatorRequest` via `request_id`. Exported from the kernel package alongside the other `from_*` builders.
- **`control_device` publishes one actuation event per executed action** onto the kernel event bus (via the existing `events.publish` bridge), which the ledger records. Covers every executed action — the `action_map` set *and* the parametric ones (brightness, temperature, volume). Carries the WorldModel-resolved area as `location`. Best-effort: publishing never affects the actuation, and an unknown action publishes nothing.
- **Honest coverage: 7.1% → 8.3%.** `control_device × event` rises `·` → **◑ parity** — parity, not full, because the actuation enters the event stream but no cognitive consumer reacts to it yet. `kernel_adoption` `event` adds `agent` as a live caller. Verified by CI gates against evidence in source.

4 new tests (canonical event published with capability/intent/location/request_id, parametric actions publish too, unknown actions publish nothing, and the `from_actuation` builder). Authority stays **log-only / owner-gated** — unchanged. Kernel wiring → middle-digit bump **8.31.0 → 8.32.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.31.0] — MCU Phase A (2/5): control_device produces a canonical ActuatorOutcome

Step 2 of 5 of the Phase A golden path — the **outcome** contract on `control_device`. The audit's point 18: *"the HA service returned success" is not "the world reached the expected state"* (HA says the light turned off; it's still on). The path already had verify-after-act that knew the difference and logged it honestly — this release makes that determination the canonical kernel outcome record.

- **The verify step now produces a `kernel.actuator.ActuatorOutcome`** — the complete `requested → executed → observed → verified` lifecycle. `_verify_control` records **VERIFIED** when the device reached its expected state (first try or on retry), **MISMATCH** when it never did even after the retry, and **FAILED** when the retry call itself errored. The outcome references the action's `ActuatorRequest`, which now carries `expected_outcome` (the expected end-state), so request and outcome are one correlated record. Non-deterministic actions (brightness, volume) have no expected end-state and so legitimately produce no outcome.
- **No behaviour change.** The verify/retry logic and the honest activity-log messages are exactly as before; the `ActuatorOutcome` is an additional structured record (best-effort, never raises).
- **Honest coverage: 5.4% → 7.1%.** `control_device × outcome` rises `·` → **● full**. `kernel_adoption` `actuator` `shadow` → **◑ parity** (the contract now spans request *and* outcome, not just a logged request). Both verified by CI gates against evidence in source. Also regenerated the adoption matrix block, which had drifted (missing the `actuator` and `budget` rows, and `agent` as a `correlation` caller).

4 new tests pin the outcome lifecycle (VERIFIED on success with the request/expected_outcome correlation, MISMATCH when the world never reaches expected, no outcome for non-deterministic actions, and that the helper logs a real `ActuatorOutcome` with the observed state). Kernel wiring → middle-digit bump **8.30.0 → 8.31.0**. Full suite green; audit + adoption + coverage gates clean.

## [8.30.0] — MCU Phase A (1/5): control_device reads world state through the kernel

The third MCU gap audit (v8.29.0) made one net-new, actionable recommendation: stop adding kernel *primitives* and instead make **one behaviour-bearing path genuinely end-to-end through the kernel contract** — then template every other consequential actuator onto it. Its own risk #20 was proven by the releases it audited: 8.27→8.29 shipped three feature releases and moved honest kernel coverage by **0.0 points** (it sat at 4.2%). Phase A moves that number for real, one contract at a time, each its own release, with **authority staying log-only / owner-gated** — the spine is built structurally first; flipping enforcement remains a separate, explicit decision.

This is step 1 of 5: the **WorldModel** read contract on `control_device`.

- **`control_device` now reads its pre-action context through the kernel `WorldModel` facade** — the canonical context authority (entity_id / domain / name / state / **area**) — instead of a bare `hass.states.get`. The result now carries the resolved `area`, and `previous_state` comes from the canonical snapshot. Read-only and best-effort (the facade never raises; a missing snapshot falls back to raw state, and the not-found error is preserved). This is the audit's item #6 ("WorldModel needs to become the context authority, not merely a convenience API") applied to the farthest-along path.
- **Honest coverage: 4.2% → 5.4%.** The `control_device` × `world_model` cell rises `·` → **◑ parity** — parity, *not* full, because the post-action verify/read-back still reads raw HA state. `scripts/kernel_coverage.py --check` verifies the claim against evidence in source (CI gate), so the number can't drift into fiction.

4 new tests pin the wiring (previous_state and area come from the snapshot; a patched facade's distinct snapshot shows up in the result, proving the path routes through `WorldModel`; missing-entity still errors). No behaviour change to what executes or whether it executes. Kernel wiring → middle-digit bump **8.29.0 → 8.30.0**. Full suite green; audit + adoption + coverage gates clean.
## [8.29.0] — Faces: pinned recognition-time snapshots (#140 Phase 2)

Phase 2 of the Faces tab. Previously each face card showed the *live* view from the camera that recognized the person — which is often empty by the time you look, since the person has moved on. Now JARVIS **pins the camera frame from the moment it recognized the face** and shows that, so a resident's card is the snapshot of them as they were last seen, not a stale empty hallway.

- **`recognition.capture_face_snapshot()`** — on a confident, known recognition (Double Take match, Frigate `sub_label`, or Frigate `tracked_object_update`), JARVIS grabs a frame from that camera, downscales it, and pins it as `/config/www/jarvis/faces/<name>.jpg` (served at `/local/jarvis/faces/<name>.jpg`). One stable file per person — the latest sighting overwrites — and captures are throttled to at most once every 5 minutes per person. Entirely best-effort: a capture failure never affects recognition, and unknown/low-confidence faces are never pinned.
- **`recent_faces()`** now carries a `snapshot_url` per row (the pinned frame, or `null`).
- **Faces panel** — each card shows the pinned recognition-time snapshot when there is one, falls back to the live camera view until the next sighting, and to an initial-letter avatar when neither is available.

Backend-agnostic: the frame is captured through JARVIS's existing camera path (Nest event media / Frigate / proxy), so it works for every recognition source. A pixel-tight face *crop* (vs. the full frame) is a possible later refinement — it needs per-backend bounding-box data that varies by Frigate version and isn't reliably present across Double Take / CompreFace / DeepStack.

5 new recognition tests (capture gate/throttle, `snapshot_url` surfacing, the capture happy-path and unknown-skip) + panel smoke coverage (pinned snapshot shown directly; un-pinned face falls back to the live frame). New feature → **8.28.0 → 8.29.0**. Full suite green; audit clean.

## [8.28.0] — JARVIS output language setting + fix the panel language picker (#148)

Two language improvements from the discussion in #148 (televisorsaal-ai).

- **"JARVIS speaks" setting (new).** Previously JARVIS's spoken/written output (briefings, camera analysis, sentinel notices) could only follow Home Assistant's global language. That breaks down when the HA install is pinned to one language for other reasons — a shared/family system, or a device-pairing quirk that forces the UI language — but you want JARVIS in another. **Settings → General** now has a **JARVIS speaks** dropdown (German, Russian, and 25+ languages, or *Auto* to follow Home Assistant). Resolution order: a per-request voice/satellite language still wins, then this setting, then Home Assistant's global language. Leaving it on *Auto* preserves the existing behavior exactly. English output installs are unaffected.
- **Fixed the panel language picker being one change behind (#148).** Changing the dashboard's **Panel language** did nothing the first time, then applied the *previous* choice on each subsequent change. The language selector had two `change` handlers — one saved + re-fetched asynchronously, the other reloaded the translation immediately off the not-yet-updated config, so it always read the prior value. The picker now resolves the language from the live `<select>` value, so a pick takes effect at once. The existing "Language" label is now **Panel language** to distinguish it from **JARVIS speaks**.

New feature → middle-digit bump **8.27.0 → 8.28.0**. 6 new language tests + panel smoke-test coverage for the new control and the off-by-one fix; full suite green; audit clean.

## [8.27.0] — Household Faces: resident whitelist + face-aware intrusion (#140)

A dedicated **Faces** tab and a JARVIS-native resident whitelist, built on top of the face recognition your vision backend already provides. JARVIS does **not** run its own face engine or enrollment — it reads recognized names from the backend (Frigate's native face recognition, or middleware like **Double Take** paired with external detectors such as **CompreFace** or **DeepStack**) and layers a "who lives here" flag on top. Requested by QuentinVape40 in #140.

- **`face_roster.py`** (new) — the resident whitelist: a small, stdlib-only, JSON-persisted (`<config>/jarvis/face_roster.json`), thread-safe list matched by the same normalized-name key `identity.normalize` uses, so "Sam", "sam" and "  Sam  " are one resident. Atomic writes, never raises, tolerates a legacy `{normalized: display}` file.
- **`recognition.recent_faces()`** — merges the live MQTT/Double Take recognition cache with Frigate's `last_recognized_face` sensors into one newest-first list (name, confidence, age, camera), de-duped per person/camera, each row marked known/unknown and whether the name is a flagged resident. Feeds the panel.
- **`recognition.resident_present()`** — returns a flagged resident recognized confidently and recently on any camera, else `None`. A **no-op when the whitelist is empty**, so default behavior is unchanged.
- **Face-aware intrusion (opt-in by curating the whitelist)** — when a flagged resident is the face on camera, JARVIS stands intrusion monitoring down: it won't raise the initial alert, and a resident appearing mid-investigation ends it. Non-residents and unknown faces alert exactly as before. Gated entirely on the whitelist, so homes that never flag a resident see no change.
- **`jarvis/faces` websocket** — lists recent faces + residents and adds/removes residents (roster file I/O on the executor; the recognition read on the loop). Also reports the active `recognition_source`.
- **Faces tab** (its own top-level panel) — a snapshot gallery where each subject shows the camera frame that recognized them with their **name under it** and a resident/unknown badge. **Household** residents are sectioned off from everyone else (**Recently Seen**), with one-click *mark resident* / *remove*, an add-by-name box, and a hint naming the supported backends. Snapshots are pulled per-camera through JARVIS's existing camera backend (Nest event media / Frigate / proxy) and fall back to an initial avatar when no frame is available.

This is Phase 1 of #140: viewing recognized/unknown faces, building the whitelist, and wiring it into intrusion. Enrollment stays with your existing backend. 19 new tests (roster, `recent_faces`/`resident_present`, intrusion stand-down) + a panel smoke-test regression. Full suite green (2336 passed); audit clean.

## [8.26.1] — fix: register the conversation agent earlier at startup

Reduces the transient ESP32 voice errors *"intent recognition engine conversation.jarvis is not found"* seen at boot/reconnect.

The `conversation.jarvis` entity is correct and available — the errors were a **startup race**: the voice satellites resolve the agent the moment they connect, but JARVIS forwarded its conversation platform *after* the disk-touching panel-settings restore (two `config.json` reads) and service registration, so the agent registered late and an early voice request found nothing.

`async_setup_entry` now forwards the conversation platform **as early as its dependencies allow** — right after the shared LLM client and `hass.data` entry are ready and services are registered, and *before* the panel-settings restore. The agent reads its runtime config lazily, so the brief window before the restore completes just falls back to defaults. No functional change beyond ordering; the restore, Sentinel/reminder start and observer all still run, just after the agent is live.

Full suite + real-HA lifecycle integration test green. (The errors are inherent to HA's load/reload lifecycle and can still appear briefly during a settings-change reload; this shrinks the common boot window.)

## [8.26.0] — universal actuator contract (MCU audit A5)

Final MCU-audit shortlist item (point 17: *"perhaps the most important implementation rule… every actuator should have one interface"*, + point 18's complete outcome model). **Additive, shadow** — `control_device` describes itself through the contract but execution is unchanged.

- **`kernel/actuator.py`** — the one canonical request shape every actuator path should converge on: `ActuatorRequest` (`capability`, `target`, `params`, `actor`, `identity`, `intent`, `situation`, `authority`, `correlation_id`, `causation_id`, `idempotency_key`, `expected_outcome`) + a `build_actuator_request()` helper. Plus `ActuatorOutcome` with the lifecycle **`requested → executed → observed → verified`** (and `mismatch` / `failed`) — the audit's point that *"HA service returned success"* is not *"the world reached the expected state."* Pure: no HA import, no execution.
- **`agent._exec_control_device`** now constructs an `ActuatorRequest` for each actuation (capability, target, intent, correlation id, idempotency key) and logs it in **shadow** — no behaviour change, no gating. The shape is exercised on real traffic before anything depends on it.
- Registered in `kernel/__init__`; adoption matrix records `actuator` = **shadow** (owner `agent`). The coverage matrix is **deliberately unchanged at 4.2%** — shadow-logging the request object isn't a pipeline-contract pass-through, and the number stays honest until execution actually routes through the contract.

6 new tests. Audit clean; adoption + coverage green; full suite green.

**This completes the MCU-audit shortlist (A1–A5, 8.22.0–8.26.0):** behavioral coverage matrix, executable Constitution, agency budget, richer authority parity inputs, and the universal actuator contract. Authority remains log-only/owner-gated; the north-star items (persistent agency, graduated autonomy, full IdentityAssertion, closed learning loop) are intentionally not undertaken.

## [8.25.0] — richer authority parity inputs (MCU audit A4)

Fourth MCU-audit item (point 3: the parity bridge was only feeding the engine capability/identity/confidence, not the full request). **Still log-only** — the parity bridge never changes the live gate's outcome.

- **`authority_bridge.record_control_parity`** now accepts and threads the full set of authoritative inputs into the engine request: **`situation`** (the active home situation), **`scope`** (the target area/entity), **`intent`** (why), **`token`** (a capability token — which drives delegation/expiry/revocation), and free-form **`context`** — not just capability/identity/confidence.
- **`agent._exec_control_device`** now passes `intent` (the human action, e.g. "turn on") and `scope` (the entity id) when recording parity.

Why it matters: feeding the engine the request it will *actually* decide on — while still log-only — is what makes the eventual enforce-flip trustworthy. Parity is now measured against the real decision, not a stripped-down one. (A capability token that doesn't grant the action makes the engine DENY even when the live gate allowed — proof the richer inputs are genuinely in the decision.)

2 new bridge tests (richer inputs accepted + still log-only; token actually affects the decision). No behaviour change — authority remains log-only/owner-gated. Full suite green.

## [8.24.0] — agency budget primitive (MCU audit A3)

Third MCU-audit item (point 21: "a correct system can still produce runaway behavior"). **Additive, pure** — a new kernel primitive; nothing consults it yet, so no runtime change.

- **`kernel/budget.py`** — `AgencyBudget` holds the ceilings JARVIS places on *itself* and answers *"am I allowed one more of this right now?"*:
  - **rate caps** — autonomous actions/hour and LLM calls/hour, via a sliding window;
  - **retry cap** — attempts allowed per action;
  - **delegation-depth cap** — how deep a chain of delegated agents may go;
  - **concurrency cap** — how many of a kind may be in flight at once.
- `allow()` is pure (doesn't consume), `record()` counts, `check_and_record()` is the atomic pair; `0` on any limit means unlimited. Pure: no HA import, no I/O, `now` injected — deterministic and advisory (it only reports; the caller decides to defer/drop/alert). This is the rail that pairs with the loop detector before any autonomy is enabled.

Registered in `kernel/__init__` and the adoption matrix (`budget` = pure). 9 new tests. Audit clean; adoption + coverage green; full suite green.

## [8.23.0] — executable JARVIS Constitution (MCU audit A2)

Second MCU-audit item (point 24: "turn the Constitution's invariants into tests"). **Additive** — tests + a doc section; no runtime change.

Documentation drifts; tests don't. `tests/unit/test_constitution.py` now encodes the Constitution's mechanically-checkable invariants as tests that **attempt the violation** and assert the responsible kernel primitive blocks it:

- **Personality never overrides safety** — `kernel.priority.may_override` refuses it, and no lower tier overrides a higher one.
- **No delegation escalation** — `CapabilityToken.derive` can't grant a capability the parent lacked; a child never outlives its parent's expiry.
- **Autonomy is revocable** — `authorize()` denies an expired or revoked token.
- **Security requires authority** — a security capability is never silently allowed (DENY without identity, CONFIRM with).
- **Fail closed** — a policy that raises resolves to DENY.
- **Verify after act** — a step whose postcondition never holds is not DONE (VERIFY_FAILED).
- **Idempotency required** — a completed idempotency key is skipped, never re-executed.
- **Correlation propagates** — a correlation id is carried through a scope and restored on exit.

11 tests; a failure means an invariant is broken (release blocker). `docs/JARVIS_CONSTITUTION.md` gains an "Executable" section mapping each invariant to its enforcing primitive and test. Full suite green.

## [8.22.0] — behavioral kernel-coverage matrix (MCU audit A1)

First item from the second external architecture audit ("JARVIS needs to *become* the kernel, not just *have* one"). **Additive** — a new script, doc, and CI gate; no runtime change.

`KERNEL_ADOPTION.md` answers *"does a live module reference a kernel primitive?"* — necessary but not sufficient, since a module can import `authorize` and still have actuators that bypass Authority. This adds the sharper measure the audit asked for:

- **`scripts/kernel_coverage.py` + `KERNEL_COVERAGE.md`** — for each behaviour-bearing path (`control_device`, `bulk_control`, `execute_plan`, `goals`, `intrusion`, `proactive`, `friday`, `homer`), declares how far each pipeline contract (**Event → WorldModel → Situation → Authority → Plan → Verify → Outcome**) is actually wired: `none / shadow / parity / full`. Every non-`none` cell is verified against evidence that must exist in the path's source, so the matrix can't drift into fiction. It prints one honest coverage percentage.
- **Honest starting number: 4.2%** — most paths are still legacy (`control_device` has Authority-parity + verify-after-act; `intrusion` has Situation-parity; the rest are unwired). This is the figure to move as paths migrate — far more meaningful than "Phase N completed."
- **CI gate** — `kernel_coverage.py --check` runs in the audit job and fails on evidence drift.

6 new tests. Audit clean; adoption + coverage checks green; full suite green. Authority stays log-only/owner-gated — this measures wiring, not a licence to enforce.

## [8.21.1] — fix: false "intrusion confirmed" when the covering camera shows no one

A window/motion trip while away escalated to a **confirmed intrusion** ("someone is moving inward through the house from the point of entry") even though JARVIS's own camera saw an **empty** room — a false alarm fired while the resident was sitting in the driveway, with camera-confirm enabled and the garage empty.

**Root cause.** In `cognitive_core` the intrusion investigator asks JARVIS's vision model for a second opinion on the camera that covers the breach. That helper's contract is explicit: `False` = *"vision says no person — don't escalate."* But the caller ignored it: on `vision is False` it fell through to `confirmed = inward`, letting a **sensor motion-propagation heuristic override a clear visual negative**. So a resident leaving, a garage-door motor, a pet, or sensor cross-talk could form an "inward route" and fire a critical alarm while the covering camera plainly showed no one.

**Fix.** When vision explicitly clears the breach (`vision is False`), the camera is authoritative for the area it covers: JARVIS no longer confirms on motion alone. It keeps **investigating** — it still escalates the instant vision sees a real person, and the existing no-response path still sends a soft "couldn't reach you, please check" notice for an unanswered, ongoing event (it does not masquerade as a confirmed break-in). The no-camera and vision-unavailable paths are unchanged (the latter still fails toward alerting, so a broken vision model never suppresses a real intrusion).

Regression test added (covered breach + vision = no person + full inward motion → **no** confirmation, keeps investigating). The inward-route confirmation **without** a camera still fires as before. Audit clean; full suite green.

## [8.21.0] — fix: JARVIS no longer nags about minor infrastructure health

Stops the continual spoken announcements of minor infrastructure issues every audit cycle.

Two things combined to make this loud: the 15-minute infrastructure audit marks **any warning-level finding** as alert-worthy — including "I can't read *X*" for probe sensors a given install doesn't have (`sensor.server_root_storage_usage`, the core-switch/freeze sensors, etc.) — and it re-announced the same verdict **every cycle**. 8.19.0 then (correctly) stopped these being silently dropped to a non-existent area, which made the nagging audible for the first time.

The audit's **spoken** policy is now:

- **Criticals only, by default.** Warning-level findings (degraded visibility, a sensor that isn't present, an elevated-but-not-critical reading) are still recorded in diagnostics and the fault log, but **not spoken**. Set the `infra_audit_speak_warnings` config key `true` to restore speaking warnings.
- **No repeats.** The same finding set is not re-announced within a 6-hour cooldown; it speaks again only when it **clears and returns**, **escalates** (warning→critical), or the set of findings changes. The fault log is appended only when something is actually announced, not every quiet cycle.

The decision is a pure, unit-tested helper (`_infra_announce_decision`); `InfrastructureTriage.evaluate()` semantics are unchanged, so diagnostics still see every finding. 7 new tests. Full suite green.

> On "**not in his voice**": the proactive TTS entity defaults to `tts.piper` and is overridable via the `proactive_tts_entity` config key — set it to your JARVIS/Piper voice entity. With this release the minor-health announcements stop regardless, so JARVIS stays quiet unless something is genuinely critical. (The config diagnostics also show the **LLM backend "off" — no base URL configured**; if you expect JARVIS to use an LLM, that's a credentials/setup item, not a code issue.)

## [8.20.0] — fix: "database is locked" on the conversation store

Repairs the log errors *"conversation DB connect/schema failed: database is locked"* (database.py:71) and *"JARVIS activity log read error: database is locked"* (database.py:224).

The conversation store (`conversations.db`, which also holds the activity log) was opened with **no busy-timeout and in rollback-journal mode**, so any concurrent access — the observer/agent writing while diagnostics or the activity feed read — failed *immediately* with "database is locked" instead of waiting. This clustered at startup, when several subsystems touch the store at once.

`_connect()` now:

- opens with a **15 s busy-timeout** (+ matching `PRAGMA busy_timeout`) so a contended connection waits for the lock instead of raising at once;
- switches the database to **WAL journal mode** (best-effort; skipped gracefully on a read-only FS), so a reader and a writer can hold the DB at the same time — the same concurrency model HA's own recorder uses.

New regression tests (4): WAL + busy-timeout are enabled; a read succeeds while a write transaction is open; repeated open/use/close stays lock-free; `health()` is OK. Full suite green. No schema or API change.

> Note: the ESPHome *"intent recognition engine conversation.jarvis is not found"* errors in the same boot are a **startup race**, not a JARVIS defect — the voice satellites reach the agent before JARVIS has finished registering its `conversation.jarvis` entity during boot. They clustered in ~1 second at startup and then stopped; the entity id is correct. Reducing startup lock contention (this release) should make the window smaller.

## [8.19.0] — fix: infrastructure audit no longer dropped to a hardcoded area

Repairs the log warning *"jarvis.speak: unknown area 'office' — ignoring"*. The 15-minute infrastructure-health audit spoke its alerts to a hardcoded placeholder area (`AUDIT_TARGET_AREA = "office"`). On any install without an "office" area — the common case — every audit alert was **silently dropped** at the speak gate.

- **Configurable audit area** — the audit target now comes from the `infra_audit_area` config key (an area id / name / alias, resolved through the existing tolerant matcher). The hardcoded `"office"` default is gone (now empty = "no fixed area").
- **Never drop an infra alert** — when no audit area is configured, or the configured one doesn't resolve, the audit now **broadcasts house-wide** instead of dropping. This goes through a new opt-in `broadcast: true` flag on `jarvis.speak`, which skips strict area resolution and uses the existing house-broadcast speaker set (`_resolve_targets`' broadcast fallback). Normal area-targeted `jarvis.speak` calls are unchanged — an unknown area without `broadcast` still logs and drops as before.

New tests (5): `_audit_speak_target` picks the configured area when it resolves and broadcasts when unset/unresolvable; `broadcast: true` dispatch skips area resolution; strict dispatch still drops an unknown area. Full suite green. No change to normal `jarvis.speak` behaviour.

## [8.18.0] — fix: appliance monitor AttributeError on native appliances

Repairs the log warning *"Appliance monitor start failed (non-fatal): '_NativeAppliance' object has no attribute 'trigger_state'"*.

`_NativeAppliance` carries `trigger_states` (a frozenset of the state values that mean "done"), but two call sites read the non-existent singular `trigger_state`:

- `appliance_monitor.start()` — the per-appliance profile log (line ~1348) raised `AttributeError` whenever a native smart appliance (Samsung/LG run-completed sensor, etc.) was present, aborting the rest of `start()` — so the native-appliance announcements and whole-home delta setup never came up.
- `appliance_monitor.status()` — the diagnostics dict (line ~1408) had the same broken key.

Both now use the frozenset: the log joins it (`"on|finished"`) and the diagnostics dict exposes `trigger_states` (sorted list). Regression test added (`status()` with a native appliance no longer raises and reports the set). Full suite green. No other behaviour change.

## [8.17.0] — fix: event-loop thread-safety for bus listeners

Repairs the dominant warning/error class in the Home Assistant logs (HA 2026.x): JARVIS was calling loop-only APIs from executor threads, which HA's thread-safety guard flags as *"calls `…` from a thread other than the event loop, which may cause Home Assistant to crash or data to corrupt"* — and which left the `_process_event` coroutine **never awaited**.

Root cause: a bus listener registered with `hass.bus.async_listen` that is **neither a `@callback` nor a coroutine** is dispatched by HA to an executor worker thread. Two such listeners then called loop-only APIs off-loop:

- **`observer._state_changed_handler`** was a plain nested function → ran off-loop → its `async_create_task(_process_event(event))` (observer.py:582) tripped the guard and dropped the coroutine (~60 occurrences/boot, plus the "coroutine never awaited" warnings). Fixed by making it a module-level **`@callback`**; its body is non-blocking, so loop execution is correct.
- **`camera` Frigate/Nest listeners** were registered as bare lambdas → ran off-loop → `hass.bus.async_fire("jarvis_camera_event", …)` (camera.py:1547) tripped the guard. Fixed by registering **`@callback`** wrappers. (`camera_learning.on_camera_event` stays an executor listener — it only buffers via the state logger and must not run on the loop.)

New regression tests (3): the camera Frigate/Nest listeners and the observer `state_changed` listener now assert the `@callback` marker. Audit clean; full suite green. No functional/behaviour change beyond running these handlers on the correct thread.

## [8.16.0] — kernel hardening H4: execution journal + crash recovery

Final post-migration hardening item (docs/KERNEL_PLAN.md → "Post-migration hardening"). **No behaviour change** — additive, built on the existing persistence seam.

- **`kernel/journal.py`** — an `ExecutionJournal` that writes each plan step's lifecycle durably through `kernel.persistence`: `record_plan` (steps as PENDING) → `start_step` (RUNNING) → `finish_step` (DONE / FAILED / VERIFY_FAILED / …). Because it's on disk, after a restart `in_flight()` returns exactly the steps that were started but never resolved — the ones whose real-world effect is unknown.
- **`recover(journal, verify=, act=)`** — settles each in-flight step after a restart. An injected `verify(step)` asks live state whether the action actually took hold → marked DONE; otherwise the step is re-run via an injected `act(step)` (idempotency in the actuator makes this safe) or, with no `act`, flagged **NEEDS_REPLAY** for the caller. A raising `verify`/`act` is treated as "not recovered", never fatal. Returns a `RecoveryReport` (checked / verified / needs_replay / replayed).
- Its own DB file and tables (no schema merge); no Home Assistant import, injected clock + checks, so it's deterministic and tested against a real temp SQLite DB.

New unit tests (12): pending/terminal queries, idempotent re-record, in-flight surviving a fresh process (simulated crash), and the full recovery matrix (verified / needs-replay / replayed / act-failure / raising-verify / no-op). Audit clean; adoption check green; full suite green.

**This completes the post-migration hardening shortlist (H1–H4).**

## [8.15.0] — kernel hardening H3: feedback-loop detection

Third post-migration hardening item (docs/KERNEL_PLAN.md → "Post-migration hardening"). **No behaviour change** — a pure primitive, additive.

- **`kernel/loop_detect.py`** — a `LoopDetector` that spots a system chasing its own tail. It flags two shapes: **repetition** (the same action key fires ≥ `max_repeats` inside a sliding `window_s`, the classic flap) and **self-trigger** (an action was caused, through the event cause-chain, by an earlier firing of the *same* action — a true A → event → A cycle). After flagging, it reports a **cooldown** for `cooldown_s` so the caller can break the cycle rather than re-detect it every tick. The causal map is bounded so a long-lived detector never grows without limit.
- Pure: no Home Assistant import, no I/O, no wall clock (`now` is passed in), so it is deterministic and unit-testable. It only reports (`LoopVerdict`); the caller decides whether to suppress, back off, or alert — and, once wired, how it feeds `attention` / `priority`.

New unit tests (11): single-fire, repetition within/outside window, cooldown hold + expiry, self-trigger via cause chain, unrelated-cause negative, chain-depth bound, reset (per-key + all), and the bounded causal map. Audit clean; adoption check green; full suite green.

## [8.14.0] — kernel hardening H2: adoption matrix + JARVIS Constitution + emergency hierarchy

Second post-migration hardening item (docs/KERNEL_PLAN.md → "Post-migration hardening"). **No behaviour change** — all additive: new docs, a pure primitive, and a CI guard.

- **Emergency / priority hierarchy** — `kernel/priority.py`: one explicit precedence ladder (**life safety → security → property → household → convenience → personality**) as a pure comparison, so attention/authority/planner resolve competing concerns the same way instead of ad-hoc `if urgent` checks. The audit's core invariant — **personality must never override a safety concern** — is encoded in `may_override()`.
- **JARVIS Constitution** — `docs/JARVIS_CONSTITUTION.md`: the short, stable set of inviolable invariants (precedence ladder; authority is log-only and enforcement is owner-gated; capabilities expire/revoke; fail-safe defaults; additive change discipline). Every line is load-bearing.
- **Kernel-adoption / bypass matrix** — `KERNEL_ADOPTION.md` + `scripts/kernel_adoption.py`: a living matrix that scans live code for kernel references (by module name, dotted use, or symbol imported `from .kernel`) and compares against each primitive's declared stage (**pure → shadow → parity → enforce**). `--check` runs in CI and **fails on drift** — a primitive claimed adopted that nothing live consults. The first run corrected the record: `world_model` and `persistence` have no live callers yet (pure), not shadow as previously assumed.

New unit tests (8, priority hierarchy). Audit clean; adoption check green; full suite green. No primitive is at `enforce` — that stays owner-gated once parity holds on real traffic.

## [8.13.0] — kernel hardening H1: authority enforcement (log-only) + token expiry/revocation

First of the post-migration hardening items from the architecture-audit review (docs/KERNEL_PLAN.md → "Post-migration hardening"). **No behaviour change** — authority runs in **log-only parity mode**; nothing is blocked that wasn't already.

- **Capability expiry + revocation** — `CapabilityToken` gains `expires_at` and a `token_id`; `authorize()` now denies an **expired** or **revoked** token (by id). `derive(..., ttl=)` clamps a child token's expiry to its parent's, so a delegated token never outlives its issuer.
- **`AuthorityParity`** — a log-only tracker that compares the authority engine's decision to what actually happened (ALLOW / DENY / CONFIRM), tallying agreement per capability with recent mismatches. This is the evidence needed before enforcement is ever flipped from parity to deny.
- **Actuator wiring (log-only)** — `agent._exec_control_device` now records engine-vs-gate parity through `authority_bridge` right where `policy.confirm_gate` already decides. It never changes the gate's outcome; the tally lives in `hass.data` and is exposed via `authority_bridge.parity_summary()`.

New unit tests (11: 6 authority expiry/revocation/parity + 5 bridge). Audit clean; full suite green. Flipping authority to actually enforce remains a separate, owner-gated step once parity holds on real traffic.

## [8.12.0] — kernel Phase 7: causal learning (final phase)

Phase 7 of the kernel plan (docs/KERNEL_PLAN.md) — the last one. Gives `pattern_analyzer` / `rca` / `feedback` a shared, principled measure of *whether a cause actually drives an effect*, closing the loop observation → hypothesis → action → outcome → **causal confidence**. **Additive and pure**: ships now; the learning modules adopt it later, so there is no behaviour change.

- **`kernel/causal.py`** — each `CausalHypothesis` "C → E" accumulates a 2×2 contingency (cause present/absent × effect present/absent) and scores causal strength with **ΔP**, the causal contrast `P(E|C) − P(E|¬C)` — which, unlike raw co-occurrence, discounts an effect that happens just as often without the cause (so `prevents` reads negative, no-relationship reads ~0). Confidence shrinks ΔP toward 0 on small samples (≈5 balanced trials → half strength) and is 0 until the cause has been seen both present and absent. `CausalModel` tallies hypotheses and `ranked()` surfaces the strongest.
- Pure (no HA import, no I/O; immutable hypotheses), so it's deterministic and testable, and seedable from the existing learning signals.

New unit tests (8): perfect/absent/preventive causes, small-sample shrinkage, undefined-until-both-sides, contingency accumulation, and ranking. Audit clean (126 modules); full suite green.

**This completes the staged kernel migration (Phases 0–7).** Each phase shipped its primitive additively (shadow / parity / opt-in); wiring the remaining consumers to enforce is tracked per-phase in the plan.

## [8.11.0] — kernel Phase 6: beliefs · attention · model router

Phase 6 of the kernel plan (docs/KERNEL_PLAN.md): three pure primitives that formalise judgement JARVIS makes ad hoc today. **Additive**: nothing is routed through them yet, so there is no behaviour or settings change.

- **`kernel/beliefs.py`** — probabilistic beliefs via log-odds pooling: a `Belief` holds a proposition's probability backed by `Evidence` (source, supports/refutes, weight), combines independent evidence sensibly (probability stays in (0,1)), applies optional **time decay** toward 0.5 without ever flipping past it, and surfaces **contradiction** (evidence on both sides) with a 0..1 strength. Seedable from the knowledge store's flat confidences.
- **`kernel/attention.py`** — centralised interruption arbitration (the job split across `output_gate` + the adaptive budget): `arbitrate(request, context)` → **ALLOW / DEFER / SUPPRESS**. CRITICAL overrides quiet-hours/shush/budget (but a genuine duplicate is still suppressed); shush/duplicate suppress non-critical; quiet-hours / exhausted budget / too-many-recent defer the lower tiers.
- **`kernel/router.py`** — local-first model/provider routing: `route(requirements, providers)` picks the best eligible provider by capability, privacy (any / prefer-local / local-only), latency cap, cost cap and min-quality, preferring local when it clears the bar.

All three are pure (no HA import, no I/O — live state is injected), so each can be parity-checked against the current gate/provider logic before anything delegates to it. New unit tests (26). Audit clean (125 modules); full suite green.

## [8.10.0] — kernel Phase 5: planner → executor → verifier

Phase 5 of the kernel plan (docs/KERNEL_PLAN.md): formalises what `goals.py` + the agent do ad hoc into explicit plan objects with preconditions, **postcondition verification as a first-class step** (verify-after-act, not a bolt-on), and an `idempotency_key` so a retried or replayed plan never double-acts. **Additive and pure**: ships now; `goals`/agent adoption follows, so there is no behaviour change.

- **`kernel/plan.py`** — `Plan` / `Step` objects and `execute_plan(plan, run_step=, check=, completed=)`. Per step it: skips any step whose `idempotency_key` is already completed; gates on **preconditions**; runs the action; then **verifies postconditions**, retrying the action once (mirroring the existing `_verify_control`). It stops at the first `BLOCKED` / `FAILED` / `VERIFY_FAILED` step and reports `ok=False`, and records each succeeded step's idempotency key so a re-run no-ops what already took hold.
- Pure by construction — the side-effecting parts (perform a step, evaluate a condition against live state, know which keys ran) are **injected** callables, so the orchestration is deterministic and unit-testable; a raising condition check counts as unmet (safe). `PlanReport.to_dict()` is serialisable for the Phase 1 trail.

New unit tests (9): happy path, precondition-blocks, action-failure, postcondition retry-then-succeed and never-holds, idempotency skip + key recording, raising-check safety, and report serialisation. Audit clean (122 modules); full suite green.

## [8.9.0] — kernel Phase 4: authority / capability engine (the safety keystone)

Phase 4 of the kernel plan (docs/KERNEL_PLAN.md): one place that answers "may this capability be exercised, by this actor, in this context?" — the judgement today spread across `voice_confirm`, `output_gate` and the autonomy grants. **Additive and pure**: the engine ships now; no actuator is routed through it yet, so there is no behaviour or settings change. Per the plan's high-risk guard, it is built to run **log-only / allow-as-before** first (prove it reaches the same allow/deny as today on real traffic) before anything enforces it.

- **`kernel/authority.py`** — `authorize(request)` resolves to **ALLOW / DENY / CONFIRM** from `(capability, identity, actor, token, context, situation, confidence, intent, scope)`. Capabilities are classified by sensitivity (safe / sensitive / security), with **unknown capabilities treated as sensitive** so an unrecognised actuation is never silently allowed.
- **Capability tokens** — a delegated sub-agent (FRIDAY / HOMER) carries a `CapabilityToken`; a token-bearing actor is **denied** any capability its token doesn't grant, and `derive()` can only **narrow** a parent token (intersection), so delegation can never escalate.
- **Security-sensitive capabilities require explicit authority** — they resolve to CONFIRM (or DENY without an identified requester), never a silent allow — and the engine **fails closed** (DENY) on any policy error.

New unit tests (15) across sensitivity classification, the default policy, token non-escalation/derivation, and fail-closed behaviour. Audit clean (121 modules); full suite green.

## [8.8.1] — kernel Phase 3: intrusion adopts the situation machine (shadow)

Completes Phase 3 by wiring `intrusion` as the first consumer of the `kernel.situation` machine (added in 8.8.0), running **in shadow mode** alongside the existing authoritative path. The intrusion lifecycle is mirrored into a durable situation so the generalised machine can be proven to track the same episodes before anything flips onto it. **No behaviour change** — the mirror is entirely best-effort and never affects intrusion handling.

- `intrusion.async_record_event` and `async_dismiss_intrusion` now mirror each lifecycle event into a `SituationManager`, off the event loop: `investigating → open + INVESTIGATING`, `confirmed → CONFIRMED`, `unresolved → RESOLVED`, dismissal → `BENIGN → RESOLVED`. A new episode opens on the next `investigating` after one resolves.
- The mirror is wired **after** the dismissal's shielded decision-record persist, so it can't change the critical path's cancellation semantics, and a mirror failure is swallowed.

New unit tests (8) covering the full episode mappings, idempotent re-entry, new-episode-after-resolution, and failure isolation. Audit clean (120 modules); full suite green.

## [8.8.0] — kernel Phase 3: durable situation state machine

Phase 3 of the kernel plan (docs/KERNEL_PLAN.md): generalises the ad-hoc intrusion state machine (scattered across `intrusion.py` and the SafetyManager) into one reusable, durable, correlated **situation** lifecycle that any flow — intrusion first, delivery and hazards later — can drive. **Additive and opt-in**: the machine ships now; no existing flow is migrated onto it yet, so there is no behaviour or settings change.

- **`kernel/situation.py`** — a pure, validated lifecycle: `normal → possible → investigating → confirmed/benign → response → resolved`, with every active state able to reach `resolved` so a situation is never stuck. `can_transition` / `is_terminal` and the transition table are pure and fully tested; an illegal move raises `InvalidTransition` rather than silently corrupting state.
- **`Situation`** is an immutable record whose `stepped()` returns a new value with an append-only transition history (`from`/`to`/`ts`/`reason`/`event_id`), carrying a `correlation_id` so a situation links into the Phase 1 event→decision trail.
- **`SituationManager`** persists each situation and its history through the `kernel.persistence` seam (its own SQLite file), so an open situation survives a restart; `open()` / `transition()` / `resolve()` / `open_situations()` drive and query it. Writes are synchronous (situations are low-frequency) — event-loop callers use an executor.

Next increment: `intrusion` adopts the machine as the first consumer, running alongside the existing path (shadow) until recorded verdicts match, then flips — kept out of this release so the SafetyManager authoritative path is migrated carefully.

New unit tests (24) across the transition table, `Situation` value semantics, and the durable manager. Audit clean (120 modules); full suite green.

## [8.7.1] — fix: stop using the deprecated DeviceRegistry.devices mapping

Home Assistant 2026.08 deprecated accessing `device_registry.devices` as a mapping (`.values()`, `.get()`, membership, subscription), with removal in **HA 2027.9**. `camera._nest_device_to_camera` iterated `dev_reg.devices.values()` to map a Nest device id to its camera entity, which would have broken on that release (flagged by the Home Assistant Breakage Radar).

It now iterates the registry directly (`for device in dev_reg.devices`), the supported replacement that yields `DeviceEntry` on current cores, with a defensive fallback for older cores whose iteration yields ids — so behaviour is identical today and future-proof for 2027.9. Added focused tests covering both iteration styles and the no-match paths.

## [8.7.0] — kernel Phase 2: world-model read facade

Phase 2 of the kernel plan (docs/KERNEL_PLAN.md): a single **read-only** view over the facts JARVIS already has, answered in canonical terms. **Additive and opt-in** — nothing is migrated onto it yet, so there is no behaviour or settings change; reasoning paths will adopt it one caller at a time in later work.

- **`kernel/world_model.py`** — `WorldModel(hass)` answers canonical questions over Home Assistant state, the knowledge graph, identity/presence and scene memory, instead of callers each reaching into raw entity ids and ad-hoc dict shapes:
  - `device(entity_id)` / `devices(domain=, area=)` / `rooms()` — canonical snapshots over `hass.states`, with each entity's area resolved best-effort.
  - `people()` / `person_in(area)` — the presence roster (home/away) and a best guess of who's in an area.
  - `facts(subject)` / `relationships(subject, obj, predicate)` — curated facts and typed edges from the knowledge graph.
  - `last_seen(term)` — where the cameras last saw something, from scene memory.
- **Read-only and best-effort by contract** — no method writes state, fires events, or mutates a store, and each degrades to an empty/None result rather than raising. Each non-HA source sits behind a small module-level seam, so the facade's import surface stays tiny and the raw sources remain authoritative underneath.

New unit tests (13), including parity checks that the HA-state reads match direct `hass.states`. Audit clean (119 modules); full suite green.

## [8.6.0] — kernel Phase 1: shadow event bus + correlated decision trail

Phase 1 of the kernel plan (docs/KERNEL_PLAN.md), running in **shadow mode**: the observer, camera and voice paths now publish `JarvisEvent`s alongside their existing logic, and a ledger records them. **Nothing consumes events to drive behaviour** — existing code paths stay authoritative — so there is no behaviour or settings change. The payoff is a queryable, correlated trail that later phases build on, and it is entirely best-effort (a bus or ledger failure can never affect the authoritative paths).

- **In-process event bus** (`kernel/event_bus.py`) — a pure, synchronous pub/sub for `JarvisEvent`s. A failing subscriber is isolated so it can't break a publisher or starve other subscribers; `publish()` never raises.
- **Event ledger** (`kernel/ledger.py`) — the durable sink: every published event is buffered in memory (cheap, loop-safe) and written to its own SQLite file in batches by an off-loop flush (a scheduler tick, and once on unload), via the `kernel.persistence` seam. A high-frequency source like `state_changed` never blocks the loop, and the buffer is capped so a storm can't grow it without bound. Exposes `query_recent` / `query_by_correlation` for the trail.
- **Correlated decisions** — `decision_record` gains a `correlation_id` (new column, migrated in place like `ref`) that defaults to an ambient id set per event via a `contextvars`-backed `kernel/correlation.py`. An event handler opens a correlation scope; any decision recorded while handling the event links to it automatically — so event → decision → outcome join into one chain without threading an argument through the ~9 `record()` call sites.
- **Shadow publishers** — `observer` (`state_changed`, wrapped so decisions during handling inherit the correlation id), `camera` (completed scene analysis), and the voice intent path (`jarvis.process_intent`).

New unit tests (37 across the bus, ledger, correlation, event, persistence and decision-record modules); audit clean (118 modules); full suite green.

## [8.5.0] — kernel foundation: canonical event type + unified DB access seam

The first additive layer of the staged kernel architecture (docs/KERNEL_PLAN.md, Phase 0). **Nothing is wired to this yet** — no behaviour, settings, or stored-data change — it is pure, tested scaffolding that the rest of the integration will migrate onto one caller at a time in later phases.

- **Canonical `JarvisEvent`** (`kernel/event.py`). One immutable, serialisable record for "something happened" — a state change, a camera analysis, a voice turn — with `type, source, subject, location, data, confidence, importance, causality, correlation_id, id, ts`. Ships with duck-typed adapters (`from_state_changed`, `from_camera_analysis`, `from_voice_turn`) that normalise common sources into it, plus `to_dict`/`from_dict` and frozen-safe derivation helpers (`evolve`, `caused_by`, `with_correlation`). No Home Assistant import, so it is trivially testable. This is the record Phase 1's event bus will publish in shadow mode.
- **Unified SQLite access seam** (`kernel/persistence.py`). One canonical connection opener (`ClosingConnection` factory + a sane busy-timeout), an explicit `transaction()` context manager whose rollback also undoes DDL (so a half-applied migration can't be left behind), and an idempotent `run_migrations()` / `schema_version()` pair that sequences and records migrations in a tiny version table. **No schema merge** — each store keeps its own database and tables; this only removes the drift in how connections are opened and gives migrations a home.

Both land with focused unit tests (16 new). The full suite and the real-HA integration tests remain green.

## [8.4.3] — reliability: importability under partial environments + no leaked log-writer thread

Two correctness fixes surfaced while standing up real Home Assistant lifecycle tests (setup / reload / unload against an actual `hass`), plus the test harness itself. No settings or behaviour change.

- **The camera platform imports `homeassistant.components.camera` lazily.** The module pulled it in at import time, which in turn imports TurboJPEG; on an install where that optional backend isn't present the whole integration failed to import (`IntegrationNotFound: jarvis`). The image fetch is now wrapped so the heavy import happens only when an image is actually requested — the integration loads cleanly whether or not the camera backend's extras are installed.
- **The persistent-log writer thread is stopped on unload.** `websocket.py` lazily starts a daemon thread (`jarvis-log-writer`) the first time a log line is persisted, but nothing ever stopped it, so each reload leaked a fresh thread. Unload now shuts it down (stop sentinel + join, off the event loop); a later setup restarts it on demand.

**New: real-HA lifecycle smoke tests + CI job.** `tests/integration/` exercises the integration under a genuine `hass` via `pytest-homeassistant-custom-component` — config flow, setup, reload, and clean unload (including a lingering-timer/thread cleanup check) — and a new `integration` CI job runs them on every push and PR. The unit suite is unchanged; the integration layer skips cleanly where PHACC isn't installed.

## [8.4.2] — fix: panel toggles on the Intrusion tab now respond to clicks

**Fixes the new "Require confinement for intrusion monitoring" toggle (and the Frigate vision-confirm toggle beside it) doing nothing when clicked** (reported on #111). The panel wires its on/off toggles in a single pass that runs before the Intrusion tab is populated, so the two toggles in the Intrusion / Security card rendered but never got their click handler — clicking them appeared to do nothing and the setting never turned ON.

The toggle-wiring is now a reusable step that the Intrusion card runs on itself after it renders, so both toggles save correctly. Added a panel smoke-test regression check that clicks the confinement toggle and asserts the config is persisted.

## [8.4.1] — reliability fixes from the architecture audit

Two correctness/consistency fixes surfaced by an event-loop and persistence review (no settings or behaviour change):

- **Adaptive awareness no longer reads the database on the event loop.** The 8.3.0 adaptive cognition threshold recomputed its delta by reading the Decision Record synchronously inside an on-loop state-change callback (every few minutes when its cache expired) — a blocking SQLite read in exactly the place Home Assistant warns about. The learned delta is now refreshed **off the loop** (via the executor, from the async observer pipeline) and the on-loop path only reads the cached value. Behaviour is unchanged; it simply no longer risks stalling the loop.
- **Scene memory and the knowledge store use the shared connection factory.** Both now open SQLite through `sqlite_utils.ClosingConnection` (as the conversation store and Local Mind already do), so a connection always closes even on an error path.

## [8.4.0] — make confinement the master switch for intrusion monitoring (opt-in)

**New setting: `intrusion_requires_confinement`** (Settings → Safety & Energy → *Require confinement for intrusion monitoring*), off by default so nothing changes unless you turn it on. Requested in #111.

By default JARVIS auto-arms intrusion detection whenever the residents are confidently **away** (tracked-away or an armed-away alarm) or **asleep** — you never have to remember to arm it. Some homes would rather it be a deliberate switch. With this setting **on**:

- **Confinement is the master switch.** Intrusion monitoring runs only while the home is *confined* — a formal **Lockdown** is engaged **or** an **alarm panel is armed** (any armed state) — regardless of presence or sleep. Arming it while you're home still watches for entry.
- **Disarming stops it immediately.** Clearing the Lockdown / disarming the alarm halts monitoring at once and drops any investigation already in progress.
- **Off → nothing.** With confinement off, door/window and motion activity never raises an intrusion alert.

Everyone who leaves the setting off keeps the existing automatic, presence-driven protection unchanged. Corroboration, vision confirmation, the investigate-then-escalate flow, and false-alarm call-off all work exactly as before within whichever mode is active.

**Also fixes installation on current Home Assistant.** HA core now bundles `google-genai==2.25.0`, which the old `<2.25.0` requirement ceiling excluded — so manifest validation (hassfest) failed and the integration wouldn't install. The Gemini provider's native Interactions API surface is unchanged across the 2.x line (verified against 2.24.0 and 2.25.0), so the ceiling is widened to `<3.0.0`.

## [8.3.0] — closing the executive-brain loop: self-tuning, a knowledge graph, and scene memory

Three additions that connect capabilities JARVIS already had into a fuller loop.

**Awareness that learns from how it's received.** The Decision Record already scored every proactive decision (welcome / unnecessary / wrong), but only the automation-suggestion bar self-tuned from it. A new `feedback.py` generalizes that so the **anticipation / anomaly** surface adapts too: when recent alerts were mostly dismissed, the salience bar rises (fewer, better alerts); when they were almost all welcome, it eases a little. Off by default — turn on **Adaptive awareness** under Settings. Bounded, hysteretic, and a no-op until there's enough judged history.

**A knowledge graph + meaning-based recall.** Curated knowledge was flat facts with keyword lookup. Now:
- **Relations** — typed edges between things (`Sam —owns→ the Jeep`, `kitchen —adjacent_to→ garage`), so JARVIS can traverse how things relate, not just list facts. User-removed edges aren't resurrected by later observation.
- **Semantic recall** — facts are embedded (reusing the same local embedding model as document search) and recalled by meaning, so "who runs cold at night" finds a fact keyed "sleep temperature." Falls back to keyword recall whenever embeddings are off or unavailable, so it's always safe.

**Scene memory — persistent object recall across time.** The cameras produce a rich scene description on every analysis; those are now kept (`vision/scene_memory.py`) instead of discarded. JARVIS can answer questions that need history: *"where did I last see my keys?"*, *"what's changed in the garage since yesterday?"* A new **where_last_seen** conversation ability searches that memory. No extra vision calls — it consumes descriptions the pipeline already generates — and history is bounded per camera.

## [8.2.5] — model lists load again, and every provider is selectable

**Fixes the "no models found — HTTP 404" model pickers, and surfaces all supported providers.**

- **Ollama model lists load again.** The Settings model dropdowns fetch each provider's live model list, but the Ollama fetch appended `/api/tags` to the `/v1` chat base — producing `…:11434/v1/api/tags`, which Ollama answers with a bare *404 page not found*, so no models appeared and every Ollama role fell back to "Custom…". Ollama's model list lives at the **native** `/api/tags` endpoint at the server root, not under the OpenAI-compatible `/v1` prefix; the fetch now drops a trailing `/v1` first, so the list loads whether your Ollama URL ends in `/v1` or not.
- **Every supported provider is now selectable in the panel.** The AI-Models role dropdowns previously listed only providers that already had a key, which hid that JARVIS speaks more than three. All of them — **Groq, OpenAI, Google Gemini, Anthropic, a local Ollama server, and any OpenAI-compatible endpoint (Custom)** — now appear; one without a key yet is marked *needs setup*, with a note pointing to Settings → Devices & Services → JARVIS → Configure to add its key, after which it's ready to use.

## [8.2.4] — stop false and repeated delivery announcements

**Fixes JARVIS announcing mail/packages that aren't there, and repeating the same one.** Two causes, both addressed:

- **Wide camera views are no longer treated as porch cameras.** The porch package sweep picked any camera whose name merely *contained* "front" — so a `camera.front_yard` (a wide yard/street view with parked cars and a neighbour's mailbox across the road) was swept for deliveries, and ordinary traffic read as a package or mail. Camera selection is now word-aware: it looks at true doorway/porch views (doorbell, porch, stoop, front door, side/back door) and **excludes wide panoramas** — yard, driveway, street, lawn, garden, garage, pool, and the like — even when they're named "front …". An explicitly configured camera still wins.
- **An oscillating detection is announced once, not every time.** A vision flag can flicker — a weak local model, a car passing through frame, one bad frame — and several paths (the 15-minute sweep, the instant motion trigger, its follow-up, the doorbell press) all feed the announcer. Each flip back to "package present" used to re-announce the same delivery. A per-camera, per-kind cooldown now collapses those repeats for 30 minutes, without blocking a genuinely separate delivery later or a different camera.

If deliveries still misfire after this, it's usually the **vision model**: a small local model (e.g. a 4B Ollama model) is unreliable at spotting a carrier or parcel. Pointing the vision role at a stronger model, or configuring a specific porch/doorbell camera under Package Watch, makes detection far more accurate.

## [8.2.3] — small correctness fixes from a coverage pass

A large unit-test expansion surfaced three genuine bugs, now fixed (no settings or behavior change beyond these):

- **Appliance type is matched most-specific-first.** Classifying an appliance from its entity/friendly name (and from a native run-state sensor) now checks the longest keyword first, so a "dishwasher" is no longer misclassified as a "washer" just because *washer* is a substring, and the generic LG `job_state` sensor no longer wins over the specific `washer_job_state` / `dryer_job_state` ones.
- **The Local Mind's offline database connection always closes.** It now opens through the same auto-closing connection wrapper the rest of JARVIS uses, so a failed read can't leak a SQLite handle.
- **The noise filter stops flagging innocent entities.** The `_w` power-sensor suffix is now matched only as a suffix, so an entity like `front_walk` is no longer treated as a noisy power reading and hidden from awareness.

## [8.2.2] — learned automations are suggested once, and named for humans

**Fixes duplicate suggestions and cryptic suggestion names.** The review list could fill with many nearly-identical "JARVIS Learned" suggestions for the same behavior, and each one's name showed raw entity ids (`close cover.smart_garage_door_2007…_garage_2 after binary_sensor.bay_2_car_occupancy confirms`) instead of the friendly names you see everywhere else.

- **Each learned automation is suggested once.** De-duplication used to key on the suggestion's *description*, which carries a running "*N times in 30 days*" count — so every analysis pass saw a "new" description and stored another near-identical row as the count ticked up. It now keys on a **stable structural signature** (the trigger and the device action, independent of that count, of measured delays/timeouts, and of the display name), so a re-detected pattern refreshes the existing suggestion in place instead of piling up copies. Genuinely different automations (a different time, threshold, or target) still stay separate.
- **Existing pile-ups are collapsed.** On the next analysis pass, duplicate *pending* suggestions that share a signature are reconciled down to the strongest one (highest confidence), so review lists that already grew large clean themselves up.
- **Suggestions are named for humans.** Learned-automation names and their descriptions now use each entity's **friendly name** — "*close Garage 2 after Car occupancy confirms*", "*Hall Light after Front Door*" — falling back to a tidied entity name only when no friendly name is set. Older entity-id-named suggestions are relabeled in place as they're re-detected.

No settings change; existing approved automations are untouched.

## [8.2.1] — close SQLite connections and file handles deterministically

**Fixes unclosed database connections and file handles** (#99, contributed by @PhoenixB). SQLite's context manager commits or rolls back a transaction on exit but does **not** close the connection, so `with sqlite3.connect(...) as conn:` left the handle open — noisy as `ResourceWarning`/`PytestUnraisableExceptionWarning` under Python 3.14, and holding database resources open longer than intended in production.

- **A closing connection type.** A new `ClosingConnection` subclass preserves sqlite3's commit-on-success / rollback-on-exception behavior and then closes the connection on context exit, so `with _connect() as conn:` now frees the handle deterministically.
- **Applied across the SQLite helpers** — `database`, `goals`, `followups`, `reminders`, and the `PatternAnalyzer` paths — including the early-return and exception paths that previously leaked (e.g. `should_analyze` returning before it read a row, and connections opened before a schema-setup failure).
- **Tests close what they open**, and the run is clean: `PYTHONTRACEMALLOC=1 pytest -W error::ResourceWarning` → 1720 passed, 3 skipped, **0 unclosed-resource warnings**.

No behavior or settings change.

## [8.2.0] — delivery announcements confirm a real delivery, not just a trigger

Faster mail/package announcements (8.0.0) reacted to porch motion and mailbox sensors. A mailbox contact sensor opening announced *"mail has arrived"* with **no camera check at all** — so wind, an animal, or you checking the mail could trip it. Now JARVIS confirms an actual delivery before it speaks:

- **The vision check looks for the delivery itself.** The porch/mailbox classifier now also reports a **carrier** — a uniformed mail or parcel carrier, a USPS / UPS / FedEx / Amazon / DHL truck or van at the curb, driveway, or mailbox, or a person carrying or setting down a parcel — not just an object sitting there.
- **A mailbox opening is confirmed, not trusted blindly.** When a mailbox sensor fires, JARVIS checks a mailbox/porch/driveway camera for a carrier or mail delivery and only announces if it sees one. If **no camera** covers that spot, it falls back to the sensor as before, so camera-less setups still get their instant mail alert.
- Porch **package** announcements are unchanged: a package visible on the porch is itself the proof it was delivered (still double-frame confirmed against single-frame hallucinations), so those never wait on catching the carrier in the act.

Quiet hours and the announcements switch are respected throughout, and no camera calls are made overnight.

## [8.1.1] — reply in the language the request came in on

**Fixes wrong-language voice replies in multi-language households** (from discussion #55). A home with both a German and a Russian voice satellite could ask JARVIS in German and get an answer in Russian — which the German TTS voice then spoke as garbled Cyrillic.

The reply-language directive was built from Home Assistant's single **global** `config.language`, ignoring the **per-request** conversation language that HA already carries per pipeline. Now the request's own language (`user_input.language`) wins over the global setting, so a German request is answered in German and a Russian request in Russian in the same household, and the pipeline-driven TTS voice matches. Non-conversation prompts (briefings, camera analysis) still use the global language, and English installs are unaffected. The directive was also reworded to switch away from the request language only when the user clearly writes in another one, reducing mis-switching on imperfect voice transcripts.

## [8.1.0] — native Gemini support via Google's GenAI Interactions API

Gemini now runs through Google's **native GenAI Interactions API** (the `google-genai` SDK) instead of the OpenAI-compatible shim. This is more reliable for Gemini's own features and fixes the stale-conversation replay that could confuse thinking-capable models.

- **Native conversation & tool continuation.** Tool calls chain server-side through the interaction ID, so multi-step tool use keeps the signatures Gemini expects — no more replaying old structured turns, which is what triggered the "missing thought signature" errors on gemini-3.x / 2.5 models.
- **Thinking controls.** Models that support extended reasoning (Gemini/Gemma, and Ollama reasoning models) can be told to think or not. A new **Thinking** toggle appears on the Vision model role in the panel, **off by default** with a configurable token budget when on — a thinking model spends part of its budget reasoning before answering, so the tight vision budgets need more room when it's enabled.
- **Automatic recovery when thinking exhausts the budget.** A response that comes back `incomplete` is retried once with a larger output budget; models that reject the `minimal` thinking level fall back to `low`.
- **Native Google Search grounding.** Web research uses Gemini's native `google_search` tool, which the OpenAI-compat surface didn't expose.
- **Model discovery through the SDK**, and every synchronous Google SDK call runs off Home Assistant's event loop.

Adds the `google-genai>=2.3.0,<2.25.0` dependency. No migration needed — existing provider configs stay valid, and vision thinking is off until you turn it on.

## [8.0.1] — audit: keep Home Assistant's event loop responsive

A full code audit found several places where JARVIS did blocking disk/database work directly on Home Assistant's event loop (which can stall the whole UI and trigger HA's "detected blocking call" warnings), and a few where it did the opposite — read HA state or registries from a worker thread, which HA does not allow. All fixed; no behavior or settings change.

- **Off the event loop now:** Sentinel alert logging (three SQLite writes per alert), the intrusion event log (load, record, and labels from the panel), the proactive briefing's overnight-event query, ignore/unignore list saves, the learned-preferences file read on every conversation turn, and the startup routine count.
- **Back on the event loop (thread-safety):** the home-context builder, "who do you see", appliance/energy-meter discovery, and the camera client's provider base-URL lookup no longer touch HA state or config entries from a worker thread.
- **Documentation accuracy:** the `jarvis.test_routing` diagnostic service (used by the panel's routing test) is now described in `services.yaml`, the README states the minimum Home Assistant version (2024.10) and covers the 8.0.0 delivery announcements and occupancy-aware suggestions.

## [8.0.0] — timely delivery & mail announcements; garage suggestions are opt-in

**Deliveries and mail are announced as they happen, not up to 15 minutes later.** Package and mail detection used to rely on a porch-camera sweep every 15 minutes (plus the doorbell press, which was already instant). A carrier who drops a package and leaves without ringing — or a mail delivery — could go unannounced for most of that window. Now:

- **Instant porch triggers.** When porch, doorbell, front-door, or driveway motion fires, JARVIS checks the porch camera **right away** instead of waiting for the next sweep — then looks **once more about a minute later**, because the package usually lands a few seconds after the motion that announced the carrier. The per-camera tracker still announces each delivery exactly once, so the extra look can never double-announce. Triggers are debounced per sensor so a busy porch can't spam vision calls.
- **Mailbox sensors.** If you have a mailbox sensor (a contact/opening sensor named like "mailbox"), opening it now announces *"mail has arrived"* immediately — no camera needed.
- The 15-minute sweep stays as a safety net, and delivery announcements still respect quiet hours and the announcements switch. They're never held back by the proactive-announcement rate cap.

**Garage/cover confirmation suggestions are now opt-in.** The safety-sensitive "arrive → open the garage → confirm the car is inside → close it" suggestions introduced in 7.100.0 are **off by default**. Turn them on under **Settings → Routine Learning → ⚠ Confirmed garage/cover sequences** — enabling asks you to confirm, and each suggestion is still flagged safety-sensitive and only installs after you approve it. (If you were relying on these in 7.100.0, switch the toggle on to keep seeing them.)

## [7.100.0] — occupancy-aware, IFTTT-style automation suggestions

The pattern engine now proposes automations that respect **who's actually in the room** and can chain **multiple steps with a confirmation** — not just "when X, do Y." Three new capabilities, all still routed through the Suggestions review list (nothing installs itself):

- **Room-occupancy conditions.** A learned action can now be gated on presence: *"when the illuminance drops below 40, turn the den lights on — only while the den is occupied."* The engine attaches a `while <room> is occupied` condition when the action's area was consistently occupied at the times it happened (using your motion/occupancy/presence sensors, mapped by area).
- **"Hold on until unoccupied."** When a light you turn on is normally turned back off once the room empties, JARVIS suggests the whole behavior as one automation: *"when the kitchen door opens, turn the garage lights on and keep them on until the garage is clear."* It builds a turn-on → wait-for-the-area-to-clear (with a short settle delay) → turn-off choreography.
- **Confirmed sequences (safety-sensitive).** JARVIS can learn a multi-step, confirmed routine — *"when the Jeep arrives home, open the garage, then close it once the car is confirmed inside."* These are clearly flagged ⚠ SAFETY-SENSITIVE in the review card, use a wait-for-confirmation with a timeout that **leaves the cover open** if the confirmation never arrives, and only ever install after you explicitly approve them.

Everything is learned from your own history, bounded and failure-tolerant, and English/other installs are unaffected until a matching pattern is actually observed.

## [7.99.10] — Suggestions only propose automations that actually *do* something

The Suggestions tab was proposing read-only entities as automations — a binary sensor, device tracker, or plain sensor that merely *changes* to on/off at a regular time was offered as a "learned automation" with no real action behind it (there's no such thing as `binary_sensor.turn_on`). The daily-routine suggestion path skipped the actionability check that the door→light, presence, and threshold paths already used.

Now every suggestion must resolve to a real device action — turn a light/switch/fan on or off, lock/unlock a door, open/close a cover, activate a scene, and so on. A regular-time *change* on an entity JARVIS can't actuate is no longer offered as an automation, and any such bogus suggestions already sitting in your review list are cleared automatically on the next analysis pass. An automation needs something to **do**, not just something that happens on a schedule.

## [7.99.9] — current-date awareness, cleaner camera output, and more languages

Follow-ups from the community language discussion (#55), plus two fixes.

**JARVIS knows today's date now (fixes stale web search).** The situational context fed to the agent carried only the weekday and clock, never the year — so for anything time-sensitive the model fell back to its training-cutoff date, and a web search for current events (an election result, "the latest…") could land on old 2024-era hits. The full local date is now in context, read from your Home Assistant timezone, so current-events questions are answered against *today*.

**Cleaner camera descriptions, in one language.** Multi-frame camera analysis used to leak its internal tile labels — "Frame 1", "Frames" — into the description, in English even on a non-English home. JARVIS now describes the scene naturally and never mentions the capture format, so localized descriptions read as a single language.

**Safety & security alerts speak more languages.** The deterministic freeze, intrusion and lockdown notifications — generated without the LLM so they stay reliable — now include **Russian, Ukrainian and Polish** alongside the existing seven, so those households stop receiving safety alerts in English.

**Easier to help translate.** A new `scripts/i18n_coverage.py` reports how complete each language is across both the panel UI and the notifications and lists which languages still need work; a rewritten translation guide makes contributing possible without touching code; and a Translation issue template gives new-language offers a home. The panel UI is fully translated across all 22 languages, and the safety notifications now cover 10 — the rest are very welcome as community contributions.

Thanks to @televisorsaal-ai for the language feedback in #55.

## [7.99.8] — briefing, area-targeting, and startup-noise fixes

Three fixes.

**Full briefings again on non-English homes (fixes #79).** A regression from 7.99.5's language work: welcome-home briefings still opened with a hard-coded English greeting, which fought the "respond in your language" directive. On the reasoning tier that conflict burned the token budget and the briefing truncated to just "Good evening, Sir." with no weather or personality (and sometimes stray English). The greeting is now asked for in the household's configured language, and the reasoning-tier budget was raised so there's room to produce the whole briefing.

**`jarvis.speak` finds rooms by alias or slug (fixes #77).** Targeting an area by an alias, or by a spacing/underscore variant (e.g. `home office` for "Home Office", `living_room` for "Living Room"), or by name when the area's id is a ULID, previously dropped the announcement as "unknown area." It now matches against each area's id, name, and aliases, and a genuine miss lists the known area names so it explains itself.

**Quieter startup (fixes #78).** On first boot / entry reload, JARVIS unconditionally removed its panels before registering them, which made Home Assistant log "Removing unknown panel …" noise. Panel removal now checks the panel is actually registered first.

## [7.99.7] — deleted memories stay deleted

A fix from **@PhoenixB** (#75, closes #71). When you deleted a curated knowledge fact from the panel — a household or per-person memory — it could quietly come back on its own after the next pattern-analysis run: the analyzer kept re-observing the same routine and re-inserting the identical fact. Deletions now stick.

Deleting a fact records a tombstone instead of a hard delete, so JARVIS won't resurrect something you removed from ongoing observation. If you later **explicitly re-teach** the same thing ("actually, trash day is Wednesday"), it comes back as you'd expect — only passive re-observation is blocked. Tombstones are cleaned up automatically after a year by a daily maintenance pass (and the existing database-purge service), so nothing accumulates. Existing installs migrate automatically the first time a fact is deleted; no action needed.

## [7.99.6] — no more blocking-call warnings; a smoother event loop

A large reliability contribution from **@PhoenixB** (#66). JARVIS was doing synchronous filesystem and SQLite work directly on Home Assistant's event loop — reasoning-cache reads/writes, document ingestion, snapshots, routines, alias lookups, doorbell logging, and the panel/calibration/decision-record/cognition/goals/follow-ups/output-gate database paths. That produced Home Assistant "blocking call" warnings and could momentarily delay other HA tasks.

All of those are now delegated to executor workers, with SQLite connections kept on the thread that opens them, and Home Assistant state reads snapshotted on the loop before any off-loop work. Behavior is unchanged — JARVIS just stops blocking the event loop, so the warnings go away and the system stays responsive. Update and restart the integration to see the warnings clear.

Along the way this also hardened a few concurrency paths: the intrusion decision-record generation is now cancellation-safe, and the announcement gate uses ownership-safe reservation tokens (released in `finally`) so the rate cap can't be bypassed or leaked. Whole-home energy-meter selection keeps preferring the true (suffix-free) meter.

## [7.99.5] — JARVIS speaks your household's language everywhere

If your Home Assistant is set to a non-English language, JARVIS's **status briefings, camera/vision analysis, and sentinel notices** now come back in that language too — not just chat replies. Previously only the *conversation* path steered to your configured language, so proactive and task output stayed in English on, say, a German or Russian install (the problem reported in Discussion #55).

The household-language directive is now the single source of truth wired into **every** task prompt, using the same 25+ languages already supported for chat. **English installs are byte-for-byte unaffected** — the directive is empty for English or unset config, so nothing changes there. Your own input language still wins: write to JARVIS in another language and it replies in that one. Fixes #55.

## [7.99.4] — cleaner setup, correct config paths, and a live area-light button

Two contributions from **@PhoenixB** (Pascal Jerney).

**Setup validation and Home Assistant config paths (#64).** Fresh installs are tidier and more correct. Cloud provider API keys are now validated during setup by calling each provider's *model-list* endpoint instead of firing a throwaway chat completion, so the log no longer fills with spurious setup-time `POST /chat/completions` errors — while still telling apart an invalid key, a connectivity problem, and an empty model list. And JARVIS now resolves its storage paths through Home Assistant's own config-directory API (`hass.config.path(...)`) rather than assuming a hardcoded `/config`, so config, secrets, databases, documents, memory, embeddings, reminders, routines, intrusion snapshots, diagnostics and learned state all land in the right place on non-standard installs. The only remaining `/config` fallback is centralized in one module for non-HA/test contexts.

**Live area-light toggle (#65).** Toggling an area light from **Dashboard → Command Center → Areas** already changed the light in Home Assistant, but the ON/OFF button stayed stale until you refreshed the dashboard. The live DOM patch now updates the button's state, label, styling and tooltip immediately — no full re-render needed.

## [7.99.3] — Floor Plan Editor gets its own Settings tab

The Floor Plan Editor now lives on its own **Settings → Floor Plan** sub-tab instead of showing up at the bottom of every Settings section. Doorbell Training, which had the same problem, now sits under **Cameras** where it belongs. No change to the editor itself — just where it appears.

## [7.99.2] — only real automations in Suggestions

The Suggestions tab now only shows patterns that can actually become an automation — a trigger **and** an action JARVIS can take. Correlations with no action to perform (two sensors or cameras that just happen to change around the same time, like a pair of alarm sensors going quiet together) are no longer surfaced as suggestions, and any that were already in your review list are cleared on the next analysis pass. An automation needs something to *do*, not just something to notice.

## [7.99.1] — clearer automation suggestions

The Suggestions tab now tells you what each learned automation would actually **do**, not just what JARVIS noticed. An actionable pattern reads like *"When the front door opens, JARVIS can turn on the hallway light"* instead of restating the trigger, and a pattern with no real device action to take — two cameras that just happen to go idle around the same time — now says so plainly rather than looking like something worth automating. Entity names in the review card are friendlier too (no more raw `{'entity': …, 'state': …}` text or doubled-up slugs like `eliana_s_room_eliana_s_room`).

## [7.99.0] — reliable web search on Gemini & Groq, plus stability fixes

Web research is much more dependable now. Asking JARVIS a current-events or "look it up" question with **Gemini** or **Groq** as the Main Agent previously could fail — a thought-signature error, an empty reply, or a "no clear answer" — instead of relaying results. Those provider-specific request quirks are handled now, so real web searches actually complete. There's also a new **opt-in** setting, *Escalate to LLM web search* (off by default), that lets JARVIS fall back to the configured model's own live web grounding (Gemini's Google Search today) when the default DuckDuckGo/SearXNG lookup comes back empty — useful for fast-moving facts the default backend can't answer.

Two stability fixes round it out:
- Fixed an intermittent crash in the pattern analyzer where its database connection was used across threads (`SQLite objects created in a thread can only be used in that same thread`), which showed up as a logged warning and lost pattern analysis.
- Fixed a blocking call and a race condition in Observer mode management, so manual/auto mode changes are handled with proper thread-safe, lock-free reads and can't interleave with an in-flight auto-evaluation.

Thanks to @PhoenixB (Pascal Jerney) for these contributions (#53, #54, #56).

## [7.98.1] — release notes for the community floor-plan & 3D-view fixes

Two contributions from **@PhoenixB** (#49, #50) landed just ahead of 7.98.0 and were swept into that tag without their own notes — this release documents them. No new code beyond what already merged; verified integrated (importer suite 27/27, audit clean, frontend syntax OK).

**Floor-plan importer: exact room polygons, a `type` tag, and a `--mirror` flag (#49).** `scripts/sweethome3d_to_floorplan.py` now emits every room with a `type` tag and, for any room that isn't a plain axis-aligned rectangle, its exact SweetHome3D polygon as `points` — while still emitting the `x`/`y`/`w`/`h` bounding box `residence_graph.py` needs for adjacency. Plain rectangles stay as `{name, x, y, w, h, type}` with no `points`, keeping the config identical to the frontend's normal room objects (which already render `points` and `type` where present). Rooms with missing point coordinates in the XML no longer crash the parse. A rectangle detector with a small tolerance absorbs SweetHome3D's point-snapping rounding noise so genuine rectangles aren't emitted as redundant polygons. And a new **`--mirror x|y`** flag reflects a plan that imports as a true mirror image of the house — something `--rotate` can never fix, since rotation preserves chirality; `points` polygons are carried correctly through rotate, mirror and re-origin.

**3D view: corrected rotation and projection (#50).** The Residence tab's 3D model had inverted signs in its rotation matrix and projection, so the model turned the wrong way and read mirrored under drag. The rotation matrix, the projected X axis, and both drag handlers (editor preview and house view) are now sign-consistent, so dragging turns the model the way you'd expect.

## [7.98.0] — camera learning gets *sharper* and JARVIS gains a memory of what it's noticed

Three things, all building on 7.97.0's "learn from what the cameras see."

**Per-resident attribution.** A learned `camera_event` for a *person* is now stamped with *who* — JARVIS consults the recognition cache for the most-recent, still-fresh face at that camera and records the resident's name and confidence onto the learnable row. So the pattern miner can move past "someone is at the front door around six" toward "*Sam* gets home around six on weekdays," and a proposed automation can key off the person, not just the presence of a person. Non-person detections (a vehicle, a package) are never attributed.

**A confidence floor.** Detections that carry a score are now gated before they're recorded: anything below a configurable floor (`camera_event_min_confidence`, default 40 on a 0–100 scale; 0 disables it) is dropped, so a weak or uncertain hit never teaches a routine. Detectors that carry *no* score — Nest motion, the vision-analysis path — are never filtered on this basis, so nothing meaningful is lost.

**A first-person recollection.** A new `awareness` module gives JARVIS a short memory of the household's recent rhythm, composed from the same learned `camera_event` rows. Injected into the agent's prompt as a **"What I've noticed lately"** block — "I've been seeing Sam at the front door around 6pm on weekdays," "I keep noticing a vehicle in the driveway in the evenings" — it is deliberately distinct from the present-tense *Situation now* block: that one says what's true this second; this one is remembered regularity over the last few days, drawn straight from the learning store so JARVIS's spoken awareness and its learned automations come from one memory, not two. The ranking and phrasing are pure, tested functions; the reader is fully guarded and never breaks a turn.

## [7.97.0] — JARVIS learns from what the cameras *see*

Cameras are now a first-class source of *learnable* signal, not just live perception. Previously the semantic camera stream — Frigate/Nest object detections and JARVIS's own vision-analysis verdicts ("a delivery", "a person at the door", "a vehicle in the driveway") — was spoken and logged but never fed into the pattern learner; the only camera-derived data reaching learning was the raw `image.*` snapshot churn, which is unlearnable noise that buries real routines.

A new `camera_learning` module bridges that gap. Each semantic detection is normalised (person / vehicle / animal / package / activity), collapsed to at most one event per location per 5-minute window so a camera firing "person" every two seconds becomes a single meaningful "someone was at the front door around then", and recorded into the **same** `state_changes` store the pattern miner already reads — as a synthetic `camera_event.<area>` entity, stamped with area, hour and day-of-week.

The payoff: the temporal miner can now learn routines like *"a package arrives around midday"* or *"the driveway sees a vehicle at 5:40pm on weekdays"*, and the sequence miner can propose automations **triggered by camera perception** — *"when the front door sees a person, the porch light comes on."* Camera events are only ever triggers, never actuator targets, which is exactly right. It's wired both to the `jarvis_camera_event` bus (Frigate/Nest) and to the vision-analysis result, and it's fully guarded — perception never breaks if learning hiccups. The correct companion setting is still to exclude the raw `image` domain from learning (Settings → Learning); this feature is what makes that safe *and* an upgrade, because the meaning is captured here instead.

## [7.96.3] — floor-plan importer: right floor key by default, plus rotation

Two follow-ups from #34, learned from a real import. The converter's default floor is now **`1f`** instead of `main` — JARVIS's Residence tab keys floors `1f`/`2f`/`bsmt`, so a plan keyed `main` loaded but appeared on no tab (it looked like nothing happened). And a new **`--rotate 90|180|270`** flag turns the whole plan clockwise for plans that import mirrored or rotated relative to the house model — `--rotate 180` swaps front/back and left/right in one go. Docs and tests updated. No change to the integration's runtime behaviour.

## [7.96.2] — floor-plan importer reads native SweetHome3D .sh3d files

`scripts/sweethome3d_to_floorplan.py` now accepts a native **`.sh3d`** save file and raw SweetHome3D **XML** in addition to the JSON export, auto-detected from the input. This addresses the crux of #34: some exporters (the HTML export, older ExportToHASS builds) emit an empty `room` array even when rooms are drawn and named, whereas the native `.sh3d` always carries the room polygons — so pointing the tool at the file you already have is the reliable route. A `.sh3d` saved in SweetHome3D's legacy binary format (no XML home entry) now fails with a clear instruction to enable "Save homes in XML format" rather than a traceback. Docs and the "no rooms found" guidance updated accordingly. No change to the integration's runtime behaviour.

## [7.96.1] — the 7.96.0 toggles now appear in the panel's Settings tab

The FRIDAY, proximity-TTS and host-telemetry controls shipped in 7.96.0 were only wired into Home Assistant's *Configure* dialog, not the JARVIS panel's **Settings** tab where everything else is managed — so FRIDAY, in particular, looked missing. A new **Sub-Agents & Automation** card under Settings → General now exposes all three:

- **FRIDAY automator** — off by default, and turning it on prompts a confirmation warning that it can control devices on its own (the panel equivalent of the Configure dialog's acknowledgement). Backend behaviour is unchanged: even enabled, FRIDAY only gets its three actuators.
- **Proximity TTS volume** and **Host hardware monitoring** — both on by default and switchable.

These keys are now on the panel's writable-config allowlist and echoed back in the panel state with their correct defaults, so the toggles save and reflect their state on re-render. No behaviour change to the features themselves.

## [7.96.0] — specialised sub-agents, proximity-aware speech, and host hardware telemetry

Four capabilities from the JARVIS architecture roadmap, built to fit the existing design rather than the roadmap's imagined file layout.

**Named sub-agent profiles.** `delegate_task` now accepts a `profile` alongside the read-only capability groups. **HOMER** is a read-only System Diagnostic Specialist with a deterministic constraint-core prompt and a tools set scoped to diagnostics/telemetry/state — it root-causes a fault and reports back, never actuating. **FRIDAY** is a terse background automator that *can* control devices, run scenes, and run scripts. Because that deliberately breaches the invariant that keeps every other sub-agent read-only, FRIDAY is **off by default** and can only be enabled from Settings → JARVIS → Configure → **Agents**, which requires ticking an explicit acknowledgement — enabling it without the acknowledgement is rejected. Even when enabled, FRIDAY is granted only its three actuators; every other write/management tool stays denied, and sub-agents still cannot re-delegate.

**Proximity-aware TTS volume.** Where a room has high-resolution mmWave distance arrays (`sensor.*_distance` / `sensor.*_presence_coordinates`), proactive announcements now dampen their volume when you're right next to the speaker instead of projecting at a fixed level, on a smooth near→quiet, far→full ramp. It's strictly additive — a no-op wherever no distance array is readable, never applied to a critical alert — and can be switched off in the Agents screen.

**Zorin/Linux host telemetry.** The infrastructure audit and the `system_diagnostics` tool now read the physical server's stress signals straight from the kernel — CPU package temperature, system memory pressure (where a runaway local model shows up first), and NVMe I/O saturation — with **zero new dependencies** (stdlib `/proc` and `/sys` reads, done off the event loop). Critical host stress is spoken through the same audit that already surfaces infrastructure faults, so a hot or thrashing host that would otherwise only show up as sluggish AI latency becomes an explicit alert. It's self-limiting: on a host that doesn't expose those kernel files, every metric simply reads as unavailable and nothing is graded.

## [7.95.1] — SweetHome3D floor-plan import helper

Importing a floor plan into the Residence tab's `floor_plan_rooms` config no longer means hand-escaping JSON. A new helper, `scripts/sweethome3d_to_floorplan.py`, converts a SweetHome3D JSON export into the exact `{floor: {rooms: [{name, x, y, w, h}], labels: []}}` shape JARVIS reads for room adjacency — turning each SweetHome3D `room` polygon into its bounding box, grouping by level, with options to rescale centimetres (`--scale`), shift the plan to a `(0,0)` origin (`--origin-zero`), and emit a paste-ready escaped string (`--as-config-string`). If an export contains only walls and furniture with no rooms drawn, the tool says so plainly instead of producing an empty plan.

The new `docs/floor-plan-import.md` documents the `floor_plan_rooms` format and clarifies that the config field already accepts either a JSON object or a stringified JSON string — no double-escaping needed. This addresses #34. No change to the integration's runtime behaviour.

## [7.95.0] — multiple LLM providers, each with its own key, and a clearer configuration flow

JARVIS now supports several LLM providers side by side — Groq, OpenAI, Anthropic, Gemini, a custom OpenAI-compatible endpoint, and Ollama — each with its own credential. Previously a single shared API-key field meant switching providers could send one provider's key to another; now every provider has its own key, stored only in Home Assistant's `secrets.yaml` (never written to the panel's `config.json`), and provider/model selection is independent of where the secret lives. Switching the Main Agent's provider no longer risks reusing a stale or wrong key.

The configuration experience is cleaner too. Setup and the options menu are provider-aware with live model discovery, the dashboard only offers providers you've actually configured, and the LLM and Observer submenus have Back navigation to the main Configure menu. Observer tiers are now labelled by role and cost — Classifier (Tier 1, cheap), Reasoning (Tier 2, main), and Review (Tier 3, periodic) — and saving an Observer provider/model updates the running configuration immediately, so the dashboard reflects the change without a restart. Stale Gemini-only wording is gone, localized translations were updated to match the new flow, and the multi-provider setup is documented in the README.

Thanks to @PhoenixB (Pascal Jerney) for this contribution (#32).

## [7.94.1] — modern OpenAI/Claude model compatibility and a web-research fix

JARVIS now works with the current generation of OpenAI and Anthropic models. Agent turns that call tools — the ones that power most of what JARVIS actually does — previously failed against newer OpenAI reasoning models (o1/o3/gpt-5.x) and modern Claude tool-calling models because of provider-specific request differences. JARVIS now translates tool calls and results into each provider's format, retries automatically when a model rejects the older `temperature` or `max_tokens` parameters, and gives reasoning models room to think before giving up on an empty response. Simple prompts worked before; real tool-using turns now work too.

Web research is also more reliable: a search backend (or a cache/CDN in front of it) that answers with HTTP 202 and a valid result body is now used instead of being discarded as an outage.

Under the hood, JARVIS service registration was consolidated around a single source of truth so the setup/unload lifecycle stays in sync as services are added — no user-facing change.

Thanks to @PhoenixB (Pascal Jerney) for these contributions (#28, #29, #30).



Automatic camera reviews now default to **urgent only** — JARVIS watches and logs everything but speaks up only for a genuine concern (an unrecognized person approaching or at a door, someone at an odd hour, an apparent attempt to enter). Routine footage — residents, empty scenes, parked or passing cars, pets, weather, normal indoor activity — is analyzed silently. A new **Camera alerts** control (General → by Camera Watch) lets you choose the threshold: Off (observe only), Urgent only (default), Important (adds deliveries and packages), or Everything notable. Manual camera analyses always report their result, and this doesn't change intrusion alerts, which are always spoken.

Also fixed a source of spurious camera chatter: when the vision model returned an empty or "thinking-only" response, JARVIS treated the event as notable and could announce it. Empty or unreadable analyses are now skipped, thinking-model traces are stripped, and a failed reasoning step stays silent instead of defaulting to notable.

## [7.93.1] — fix: JARVIS silent / VoiceNotFoundError when only the medium voice is installed

JARVIS now requests the JARVIS voice at the quality it actually installed — it follows the Voice Quality setting (medium by default) instead of always asking for the high-quality voice. Previously, on a default setup where only en_GB-jarvis-medium was present, JARVIS's speech asked for en_GB-jarvis-high, hit a VoiceNotFoundError, and fell back to the engine's plain default voice (or went silent during TTS streaming). If you want the high-quality voice, set Voice Quality to "high" and JARVIS will install and use it.

## [7.93.0] — quieter cameras: analyze everything, announce only what matters

JARVIS no longer narrates routine camera activity. Automatic camera reviews now speak up only for things worth your attention — a person approaching or at a door, a delivery or package, mail, someone at an odd hour — while parked or passing cars, pets, empty scenes, and recognized residents are still analyzed and logged but kept silent. The scene-reasoning step was also tightened to default to silence and only flag genuinely notable events.

A new "Announce Important Only" toggle (General → next to Camera Watch) controls this; it's on by default. Turn it off if you'd rather hear every notable review. Manual camera analyses always report their result, and this doesn't change intrusion alerts, which come through their own always-on path.

## [7.92.0] — automatic camera analysis on Frigate events, and fewer phantom people at night

Camera analysis can now run on its own when Frigate detects a person or object — not just on doorbell presses. Turn on the new "Analyze Motion Events" toggle (General → alongside Camera Watch) and JARVIS inspects Frigate detections as they happen, throttled per camera, with the spoken alert still gated to things worth mentioning. No Home Assistant automation required.

Night-time false alarms are cut down too. Camera analysis is now grounded against what Frigate's detector actually sees and told to be cautious with dark/infrared images, so shadows, furniture, garden objects, and reflections are far less likely to be reported as "a person." If Frigate says there's no person on a camera, JARVIS weighs that heavily before mentioning one.

## [7.91.0] — camera analysis now reports on every area a camera covers

Camera analysis now uses your floor plan's camera coverage. When a camera's view spans more than one room — for example a Dining Room camera that also sees the Living Room — JARVIS is told which areas are in frame and reports on each of them, attributing people and activity to the specific area, instead of describing everything as just the room the camera is named after. Set a camera's covered areas on the floor plan (JARVIS does this automatically when you place a camera, or you can adjust it). Cameras with no coverage recorded behave exactly as before.

## [7.90.0] — silence door/window/garage "open at an unusual time" announcements

Added a Door/window/garage alerts toggle under Settings → Anticipation & Memory. When off, JARVIS stops proactively announcing that a door, window, or garage is open at a time it's usually closed (the "around this time it's usually closed, so I thought I'd mention it" heads-up), while still keeping those sensors fully monitored — Sentinel rules, intrusion detection, lockdown, and "what's open right now" all continue to see them. This is separate from Sentinel's "left open too long" rules: those are governed in Sentinel Rules, and these time-of-day anticipations are the anticipation engine, so silencing the proactive chatter no longer means disabling awareness of your openings. On by default; lock-state anticipations are unaffected.

## [7.89.0] — Driving mode: alerts follow you to the car screen (Android Auto)

When your phone is connected to the car over Android Auto, JARVIS can send its proactive heads-ups to the car screen instead of speaking them to an empty house. This is especially useful for leave-now reminders: a departure alert that used to be announced to your living room while you were already in the driveway now shows up on the car display where you'll actually see it.

You choose which alerts follow you to the car — departure and travel-time reminders, proactive briefings, and security/hazard alerts — each with its own toggle, and you can optionally keep the home speakers silent for those alerts while you're driving. Set it all up under Settings → Anticipation & Memory. JARVIS auto-detects the Home Assistant companion app's Android Auto connection sensor, or you can point it at a specific sensor. Driving mode is off until you turn it on, and when it's off nothing about your existing alerts changes.

## [7.88.0] — Setup dialog localized in 14 more languages

The Home Assistant setup and configuration dialogs (config/options flow — the screens where you enter your LLM key, pick models, and configure routing, observer, identity, and email) are now translated into 14 additional languages, bringing the dialog to full parity with the in-panel HUD. Added: Czech, Danish, Finnish, Norwegian Bokmål, Polish, Romanian, Russian, Slovak, Swedish, Turkish, Ukrainian, Brazilian Portuguese, Simplified Chinese, and Traditional Chinese. That's 20 languages now covering both the panel and the setup dialog. Placeholders and technical tokens (entity IDs, model names, hosts, ports, secrets.yaml keys) are preserved, and anything still untranslated falls back to English.

## [7.87.1] — Traditional Chinese

Added Traditional Chinese (繁體中文) alongside Simplified — all 336 panel strings, converted with OpenCC using Taiwan phrasing (e.g. 設定, 網路, 載入中…). It's picked up automatically when Home Assistant is set to Traditional Chinese (zh-Hant), and is selectable manually under Settings → General → Language. Regional codes are covered too: zh-Hant/zh-TW use Taiwan-style Traditional, zh-HK uses Hong Kong Traditional, so Traditional users never fall back to Simplified. Simplified remains the default for zh-Hans / zh-CN.

## [7.87.0] — Simplified Chinese, and every language now selectable

Added a Simplified Chinese (中文) translation of the JARVIS panel — all 336 interface strings — the first non-Latin-script language. It's picked up automatically when Home Assistant is set to Chinese (zh-Hans / zh-CN), and can be chosen manually under Settings → General → Language.

Also fixed the manual Language dropdown, which only listed 7 of the shipped languages: it now offers all of them — English, Simplified Chinese, Czech, Danish, German, Spanish, French, Italian, Dutch, Norwegian, Polish, Portuguese, Brazilian Portuguese, Romanian, Russian, Slovak, Finnish, Swedish, Turkish, Ukrainian — plus Auto. Traditional Chinese can follow the same way if there's interest.

## [7.86.3] — fix missing settings panels on desktop + JARVIS voice not found

Two reported bugs fixed.

- **Settings panels not showing in desktop browsers (#27).** The settings card layout used CSS multi-column, which miscalculates and drops cards on desktop/WebKit when the sub-navigation hides the inactive section — so several panels vanished in a computer browser while showing fine on mobile (where the layout collapses to a single column). Switched to a CSS grid, which removes hidden cards from flow cleanly, so every panel shows and sub-nav switching is reliable on all screens.
- **JARVIS voice missing / VoiceNotFoundError (#26).** The Assist pipeline could name en_GB-jarvis-high while only the medium voice was on disk (high not downloaded, or a fallback), producing VoiceNotFoundError and no speech. The voice download now reports the quality it actually installed, the pipeline is pointed at that voice (and repairs an existing pipeline whose voice file is missing), and downloads now also try the canonical rhasspy/piper-voices repo so the high voice fetches reliably. Manual install if ever needed: download en_GB-jarvis-high.onnx and .onnx.json from huggingface.co/rhasspy/piper-voices (en/en_GB/jarvis/high) into /share/piper/ and restart Piper.

## [7.86.2] — surface silent failures in the Home Assistant log

Audited where JARVIS caught errors and continued without logging anything, and gave the ones that matter a visible log line so a real failure is no longer invisible in Settings → System → Logs. Now logged at warning: a failure in the TV/screen filter that keeps spoken replies off displays (the "talking through the TV" class), a goodnight scene/script that didn't run, a lock that didn't secure during goodnight, bulk device control that failed, and failed appliance/intrusion/config-corruption notifications. Best-effort internals (caches, embeddings, optional follow-up prompts) stay quiet by design so the log isn't flooded. As a reminder, JARVIS's own step-by-step breadcrumbs (conversation routing, gating, classification) live in the panel's Log tab, and `logger: logs: custom_components.jarvis: debug` in configuration.yaml surfaces everything in the HA log.

## [7.86.1] — stop answering TV/media audio as if it were commands

When media is playing, JARVIS no longer treats ambient dialogue as commands. If your designated TV/movie player is playing, or any media player in a satellite's own area is playing, the relevance gate tightens: only input carrying a clear signal (the wake word, a command verb, a question, or a device name) is acted on — stray TV/movie dialogue is dropped instead of being answered. Continued-conversation follow-ups are also paused while media plays, so the microphone isn't reopened into the soundtrack. In a quiet room, behaviour is unchanged. Note: the primary defence against this is still wake-word gating on the satellites themselves — if a satellite transcribes ambient audio at all, make sure it requires the wake word rather than listening continuously.

## [7.86.0] — place devices on the floor plan + use your real plan as the background

You can now pin Home Assistant devices onto the floor plan and control them there, and use an imported real floor-plan image as the visual background.

- **Devices on the plan.** In Settings → Floor Plan Editor there's a new "Devices on plan" section: add a device, then drag its pin to where it actually is. Each pin shows the device's live state — lights ON/OFF, doors/windows OPEN/SHUT, locks, motion/occupancy, a person's home/away, or a sensor's reading (e.g. a temperature). Tap a pin to open that entity's full Home Assistant controls. Right-click removes it. Pin positions save with the rest of the plan.
- **Your real floor plan as the background.** Importing a floor-plan image (PNG/SVG/JPG) as a per-floor background already existed; it now has an opacity control, so you can turn your Sweet Home 3D (or any) exported plan from a faint underlay into the prominent background, with the rooms and device pins on top.

Together this gives a real spatial view of the home — your actual layout with live devices placed on it — rather than a room list.

## [7.85.10] — localization catalog completed (Excluded Entities card, floor-plan labels, and more)

Closed the last localization gap: a set of interface strings that had never been added to the translation catalog — so they showed English in every language, French included. Now translated across all 18 languages: the Excluded Entities card and its help text, the floor-plan editor element labels (doors, windows, dormers, "Add Room", zones), the Suggestions tab, and several dropdown placeholders and hints. The catalog is now identical across every language.

## [7.85.9] — excluded entities are now dropped from voice output too

Excluding an entity now also stops JARVIS from speaking through it. Until now, exclusion covered presence, the observer and learning, but not speaker selection or the final voice-output step — so an excluded media player could still be picked as a room speaker and spoken to. Both now honor your exclusions.

This fixes a specific case of unwanted TTS on a TV: when a television is exposed by two integrations at once (e.g. the Samsung TV integration plus a DLNA/DMR entity for the same set), the second entity doesn't report itself as a TV, so JARVIS's automatic TV-skip didn't catch it and tried to speak through it. You can now silence it by adding that entity to Settings → Excluded Entities. (Alternatively, disabling the redundant DLNA/DMR entity in Home Assistant also resolves it, since the Samsung integration already covers the TV.)

## [7.85.8] — full UI translations for Finnish, Romanian, Turkish, Ukrainian (all languages complete)

The final four languages are now fully translated (302/302): Finnish, Romanian, Turkish, and Ukrainian. With these, all 18 interface languages are complete — Czech, Danish, Dutch, English, Finnish, French, German, Italian, Norwegian Bokmål, Polish, Portuguese, Brazilian Portuguese, Romanian, Russian, Slovak, Spanish, Swedish, Turkish, and Ukrainian all cover the full panel. Set your language in Home Assistant and the JARVIS panel follows it end to end.

## [7.85.7] — full UI translations for Danish, Norwegian, Czech, Slovak

Four more languages are now fully translated (302/302): Danish, Norwegian Bokmål, Czech, and Slovak. Fourteen of the interface languages are now complete; the last four (Finnish, Romanian, Turkish, Ukrainian) will follow.

## [7.85.6] — fix: LLM calls failing with "_Unsupported is not JSON serializable"

On newer Home Assistant (2026.9+), every request that needed the LLM could fail with `Object of type _Unsupported is not JSON serializable`, while simple/local replies still worked — affecting Groq and custom OpenAI-compatible providers alike. Cause: when JARVIS converts Home Assistant's tool definitions into the function-calling format for the model, the newer schema converter leaves an internal "unsupported" marker in the result for any schema element it can't represent, and that marker can't be turned into JSON — so the whole request was rejected before it reached the provider. JARVIS now strips those unserializable markers from the tool schema, so LLM-backed responses work again. No configuration change needed.

## [7.85.5] — excluding an entity now silences already-learnt rules immediately

Completes the exclusion behaviour: excluding an entity now takes effect for rules that were already active, not just new ones. A door/window/garage/lock that Sentinel had already picked up stops being reported the moment you exclude it (by entity, domain, or label) — e.g. an unlocked lock no longer repeats its reminder. Appliance cycle-complete announcements skip excluded entities the same way, even for a cycle already in progress. Nothing is erased or "unlearnt" — exclusion simply gates these at run time, so removing the exclusion brings the behaviour back.

## [7.85.4] — full UI translations for Russian, Polish, Brazilian Portuguese, Swedish

Four more languages are now fully translated (302/302), joining French, German, Spanish, Italian, Dutch and Portuguese: Russian, Polish, Brazilian Portuguese, and Swedish. Ten of the interface languages are now complete. The remaining partially-translated languages (Czech, Danish, Finnish, Norwegian, Romanian, Slovak, Turkish, Ukrainian) will follow in subsequent updates.

## [7.85.3] — full UI translations for German, Spanish, Italian, Dutch, Portuguese

Five languages that were roughly half-translated are now complete: German, Spanish, Italian, Dutch, and Portuguese each cover the full interface (302/302 strings), matching French. If your Home Assistant is set to one of these, the JARVIS panel is now fully localized rather than falling back to English for the newer controls. The remaining partially-translated languages will be filled in over subsequent updates.

## [7.85.2] — excluded entities now dropped from room cards and group commands

Following up on entity exclusion (7.85.0): excluded entities were still appearing on the room cards and in area commands. Now an excluded entity is left out of a room card's light count, its capabilities list, and its last-motion reading, and it no longer takes part in area or group commands (e.g. "turn on the living-room lights" skips it). Controlling an excluded entity by name still works, as before. This completes exclusion so an excluded item genuinely drops out of JARVIS's day-to-day behaviour, not just its background monitoring.

## [7.85.1] — fix: Settings page blank when a configured entity was removed

The Settings page could fail to open with a blank screen if a setting still pointed at an entity that no longer exists — most often the departure travel-time sensor, but the same flaw affected the presence-tracker and door/opening pickers. Building those dropdowns threw an error on the missing entity, which aborted the whole panel render. Those pickers now handle a missing entity gracefully (showing its id), and the panel as a whole no longer goes blank if any single part errors while drawing — it shows a short error notice with details in the browser console instead.

## [7.85.0] — Exclude entities from JARVIS

You can now tell JARVIS which entities to ignore. In Settings → Excluded Entities (under Learning) you can exclude:

- **Specific entities**, one at a time,
- **Whole domains** (e.g. every `light` or `switch`), and
- **By Home Assistant label**, to exclude a whole group at once.

Excluded entities are dropped everywhere JARVIS looks on its own — presence detection, room routing, the observer, and routine learning — so noise sources stop interfering. A common case: a virtual occupancy sensor from another integration that JARVIS was treating as a real room-presence sensor; exclude it and it no longer affects presence or where JARVIS speaks. Excluding an entity only removes it from JARVIS's awareness — Home Assistant still has it, and JARVIS can still control it if you ask for it by name.

## [7.84.2] — Settings sub-tabs on translated UIs; temperatures follow your unit system

Two fixes for non-English / metric households:

- **Settings sub-tabs work on a translated interface.** On a non-English UI, every Settings sub-tab except the first showed no cards — the section grouping was matching on the on-screen heading text, which the interface translates. Sections are now tracked independently of the displayed language, so every sub-tab shows its cards in any language.
- **Temperatures follow Home Assistant's unit system everywhere.** JARVIS already reported sensor temperatures in your configured unit; now its freeform spoken remarks do too, so a metric home hears Celsius throughout instead of an occasional Fahrenheit value. Set your unit in Home Assistant (Settings → System → General) and JARVIS follows it — there's no separate switch to keep in sync.

## [7.84.1] — hard backstop: JARVIS never speaks through a TV

A final safety net now sits at the point where any spoken output is sent: every announcement and reply has its target list stripped of televisions (any media player reported as a TV, and the media player you've designated for movies) before it plays — no matter which feature produced it. Earlier fixes handled the known routing paths; this closes any remaining one, so a TV can't be spoken to even through a path we haven't traced. When it has to drop a TV target, it logs which feature tried, so any unexpected attempt is now traceable. Your movie/TV player still plays movies; it is simply never a voice target.

## [7.84.0] — Settings organized into sections

The Settings tab now has a sub-navigation bar that groups everything into six sections — General, Voice & Audio, Learning, Safety & Energy, Cameras, and Home & Extras — so you see one focused group at a time instead of scrolling one long wall of cards. It opens on General, and switching sections is instant. Nothing was removed or renamed; the same settings are just organized, which should make it far easier for new users to find their way around.

## [7.83.1] — spoken announcements never route to a TV

JARVIS no longer speaks through televisions. Room-based speech — proactive observer comments and room-targeted announcements — now skips any media player reported as a TV, as well as the media player you've designated for movies. Previously, if a room had a TV alongside a speaker (or only a TV), proactive speech could play through the television; now it uses the room's actual speaker, or stays quiet if the room has none. Your movie/TV player still plays movies as before — it's simply never used as a voice output. This complements the previous change so neither broadcast announcements nor room speech can take over a TV.

## [7.83.0] — announcements no longer blast every device by default

JARVIS will no longer broadcast to every speaker (and every TV) when you haven't chosen where announcements should play. Until you pick your announcement speakers in Settings → Announcement Speakers — or set a broadcast group — briefings, sentinel alerts, and other announcements stay silent instead of playing on all devices at once. This fixes the first-launch experience where a test briefing came out of the whole house with no way to stop it, and where announcements took over TVs. "Brief me now" now tells you if no announcement speakers are set rather than doing nothing, and the sentinel and voice-test both use your chosen announcement speakers. If your announcements have gone quiet after updating, choose your speakers in Settings → Announcement Speakers and they'll return.

## [7.82.1] — two settings moved into the panel

Two options that previously needed a config-file edit are now toggles in Settings, so you can turn them on without touching files. "Learn button & remote presses" lives with the other pattern-learning switches and lets JARVIS suggest press-to-scene automations. "Follow me between rooms" lives in the Anticipation & Memory card and moves a continued conversation to the satellite in the room you walked to once the room you started in is empty. Both are off until you turn them on.

## [7.82.0] — conversations can follow you between rooms

When you're mid-conversation with JARVIS and walk to another room, the follow-up can now follow you. If the room you started in has gone completely empty by the time JARVIS is ready to listen again, and you've moved to another room that has a voice satellite, JARVIS reopens the microphone on that room's satellite instead — so you can keep talking where you are. It's deliberately cautious: it only moves the conversation when the starting room is clearly empty and the new room clearly has you in it, and it stays on the original satellite in any uncertain case, so a brief presence dropout never sends the follow-up to the wrong room. This is opt-in and off by default (config key `continued_conversation_multi_satellite`), and applies to homes with more than one satellite.

## [7.81.1] — Vision model picker guidance

The Vision entry under Settings → AI Models now shows a short hint that it needs an image-capable model — moondream on Ollama, or a Groq vision model — and that a text-only model (like gpt-oss) will fail on camera analysis. This heads off a confusing setup where camera analysis errors out only because the wrong kind of model was chosen for vision; the other model roles are unaffected.

## [7.81.0] — a dedicated Suggestions tab

The automations JARVIS learns from your routines now have their own tab in the panel, instead of being tucked into the Command Center. Each suggestion shows what JARVIS observed, how confident it is, the devices involved, and the exact automation it would create — so you can review it properly and either approve it (which creates it in Home Assistant) or dismiss it. When there's nothing to review yet, the tab explains that JARVIS is still watching for patterns. Sensor-threshold suggestions (the "when it drops below X" kind) are labelled clearly alongside the routine, sequence, and presence types.

## [7.80.1] — panel/config settings honored consistently

Settings you set through the JARVIS panel or config.json — the sentinel and announcement toggles, notification service, TTS engine, honorific, prime-directive preset, and the camera vision/reasoning options — are now read through a single resolver everywhere. Previously a few of these were read straight from the config entry, which is empty on a panel-configured install, so they could quietly fall back to defaults even though you'd set them. They now resolve in the correct order (live panel value, then saved config, then any entry value), so what you configure is what runs.

## [7.80.0] — lockdown notifications are fully localized

The lockdown messages that list the specific doors and locks JARVIS just secured — and name any openings it couldn't secure remotely — now come through in your language, not just English. This was the last English-only piece of the safety notifications: it's assembled from device names with per-language grammar, so it's built from localized verb phrases, a localized list join (with the right conjunction and comma rules per language), and per-language sentence wrappers. French, German, Spanish, Italian, Dutch, and Portuguese are covered, with English as the fallback. Your device and room names are always left exactly as you named them. The openings-still-open phrasing is worded to read correctly regardless of a device's grammatical gender.

## [7.79.1] — clearer vision errors + no blocking SSL warning

If a camera-vision model can't accept images (for example a text-only Groq model like gpt-oss), the analysis now fails with a plain message telling you to set a vision-capable model — such as moondream on Ollama, or a Llama/Qwen vision model on Groq — instead of a raw "content must be a string" API error. Separately, the vision and camera-reasoning providers are now built off the event loop and reused across analyses, so Home Assistant no longer logs a blocking-call warning about SSL setup when a camera is analyzed.

## [7.79.0] — learned button & remote automations ("press → scene")

JARVIS can now learn what your buttons and remotes do. When a press consistently precedes an action — the living-room remote's single-press, then the movie scene — it suggests an automation triggered by that press. Modern Home Assistant exposes button and remote presses as event entities, and the suggestion fires on the specific press (single, double, hold) by matching the press type, so one button's different presses stay distinct. Scenes are now learnable as the target too, so "press → scene" resolves to activating that scene rather than each light individually. Button learning is opt-in — turn on button/remote learning to start recording presses — and as always the resulting automation is a suggestion you approve.

## [7.78.0] — learned arrival / departure automations

When something you do consistently lines up with leaving or coming home — the garage closing shortly after you drive off, the entry lights coming on when you get back — the suggestion is now built as a proper arrival/departure trigger instead of a raw state change. Home Assistant treats "leaves home" and "arrives home" as zone events, which handle the edges of your home zone correctly, so these suggestions read as "when person.sam leaves home, close the garage" and behave the way presence automations are meant to. This applies to people and phone/device trackers crossing your home zone; other triggers are unchanged, and it only appears when the pattern is consistent.

## [7.77.0] — time routines can be gated on their owner being home

When a daily routine is one person's habit — "the porch light goes on around 7pm, and it's Sam who's home when it does" — the suggested automation now carries a presence condition, so it only runs when that person is actually home instead of firing on the clock regardless. A time trigger has no built-in sense of who's around, so this is a real guard: the evening routine won't run to an empty house. It only attaches when the routine clearly belongs to one person and that person maps to a Home Assistant person entity, and like every learned automation it's a suggestion you approve — so a routine you deliberately want to run while away (a security light, say) can simply be declined. Household-wide routines with no single owner are unchanged.

## [7.76.0] — learned automations can be gated on a sensor reading

A suggested automation can now carry a numeric condition alongside its trigger — "when motion in the hall, turn on the heater, and only while the temperature is below 62". When an action consistently happens while a temperature, humidity, or light-level sensor sits on one side of a value, that condition is attached, using the same guard as the sensor trigger: it only fires when the readings genuinely cluster on one side and the sensor really spends time on the other, so a sensor that just stays low won't add a bogus condition. Conditions accumulate — a pattern that happens after dark and while it's cold gets both an "after dark" and a "below 62" condition, which Home Assistant requires together. Time-only and unconditioned patterns are unchanged.

## [7.75.0] — JARVIS learns sensor-threshold automations

JARVIS can now suggest automations that fire when a sensor crosses a value, not just when something changes state or a time of day arrives. If a habit like "turn on the space heater once it drops below 65°" shows up consistently in your history, the suggestion is built as a numeric trigger — "when the temperature sensor goes below 65, turn on the heater" — so it acts on the reading itself. It looks at temperature, humidity, and light-level sensors, and only proposes a threshold when the action genuinely clusters on one side of it and the sensor really spends time on the other side too, so a sensor that simply stays low all the time won't produce a bogus rule. As with every learned automation, these are suggestions you approve, never changes JARVIS makes on its own.

## [7.74.0] — learned automations gain an "after dark" condition

Trigger-based suggestions can now be scoped to "after dark" using the sun's position, not just a fixed clock window. When a pattern like "motion in the hall, turn on the light" consistently happens while the sun is down, the suggested automation is conditioned on sunset-to-sunrise — so it tracks the seasons and won't fire the light in daylight, without you picking any times. When the sun position doesn't cleanly explain the pattern, JARVIS falls back to the time-of-day window from before; patterns that happen at all hours still get no condition.

## [7.73.0] — Intrusion is now its own tab

The intrusion snapshot and the intrusion log (with its real / false-alarm labeling) have moved out of Settings into a dedicated **Intrusion** tab, alongside Command Center, Residence, Settings, Logs, and Memory. Security review and labeling now have a home of their own instead of being buried in Settings — everything else about them works exactly as before.

## [7.72.0] — Analyze Now learns from your history, not just from now on

Previously, enabling something like motion/presence learning only started recording from that moment, so a real habit took days to build up before it could be suggested. Analyze Now now backfills from Home Assistant's own recorded history: for any relevant entity JARVIS wasn't already tracking (a motion or occupancy sensor you just opted in, for example), it imports the recent past so patterns can surface right away — e.g. "car enters the bay, then the garage door closes" can be recognized from history instead of waiting for it to happen again seven times. It's careful about this: it only imports entities it wasn't already logging (no double-counting), and chatty sensors are throttled exactly as live logging throttles them, so importing history can't bloat the store. The button tells you how much it imported.

## [7.71.0] — see patterns building toward suggestions

When you run Analyze Now, JARVIS now shows patterns it has detected but that haven't recurred enough times yet to become a suggestion — for example "when the garage bay senses a car, the door closes (3/7)". This makes the difference between "JARVIS sees the pattern, it just needs to happen a few more times" and "JARVIS isn't seeing it at all" obvious at a glance, instead of leaving you guessing why a real habit hasn't turned into a suggestion. A suggestion is created once a trigger-based pattern has recurred enough to be trustworthy.

## [7.70.0] — learned automations gain a "when" — time-of-day conditions

Trigger-based suggestions now include a time condition when the pattern clearly warrants one. If a "when motion, turn on the light" pattern only ever happens in the evening, the suggested automation is scoped to that window — so it won't fire the light at noon — and the window is read straight from when the behavior actually occurs (evenings, overnight, mornings; overnight windows wrap correctly). Patterns that happen throughout the day get no time condition, as they shouldn't. This is the first of the "And if" conditions; sun-position and presence conditions are still to come.

## [7.69.0] — learn "when motion, do X" automations (opt-in)

New setting: **Learn motion/presence triggers**. Turn it on and JARVIS starts learning from your motion and occupancy sensors, so it can suggest trigger-based automations like "when the hallway senses motion, turn on the light" — the follow-on to the cross-device trigger learning added last release. It's off by default, and motion sensors are rate-limited hard when learning (one marker every few minutes, not every pulse) so enabling it can't bloat the pattern store. Door and window sensors remain a separate opt-in; ordinary devices are unaffected.

## [7.68.0] — option to use Home Assistant's default TTS voice

New setting: **Use Home Assistant default voice**. JARVIS normally speaks with its own Piper voice (en_GB-jarvis-high). Turn this on and JARVIS stops requesting that specific voice, letting your TTS engine use whatever voice you've configured in Home Assistant — so a French install with a French Piper voice, for example, will simply be spoken in French, with no dependency on the JARVIS voice being downloaded. Off by default, so nothing changes unless you choose it. Find it under Settings, in the voice options.

## [7.67.0] — smarter "when this, do that" learning

JARVIS's detection of actions that follow one another got two upgrades. It now spots these across different device types — a switch triggering a light, a cover triggering a fan — where before it could only relate devices of the same type. And when it turns one into a suggested automation, it uses the real, observed delay between the two events (a switch flip followed by a light ~90 seconds later becomes a 90-second delay) instead of a fixed one-minute guess; near-instant reactions get no artificial wait at all. The result is trigger-based suggestions that match what actually happens in your home.

## [7.66.0] — an "Analyze Now" button, and a last-analysis readout

Pattern analysis normally runs on its own every six hours. There's now an **Analyze Now** button under Quick Actions to run a pass on demand — handy after changing settings or exposing new entities, instead of waiting or restarting. It bypasses only the six-hour wait, not the data requirement (still needs about a week of history), so it can't produce noise on a fresh install, and it tells you the outcome right away: how many patterns it found and how many new suggestions it stored.

The Cognitive Core readout now also shows a **last analysis** line — when the most recent pass ran and what it produced — so you can tell the difference between "hasn't run yet" and "ran, but nothing consistent enough to suggest," at a glance.

## [7.65.0] — routines and automation suggestions form again on large histories

If JARVIS had been running a while with a lot of activity but never produced any routines or automation suggestions, this is the fix. One part of the pattern analysis — detecting actions that reliably follow one another — was written in a way that slowed down dramatically as your history grew, to the point where on a large home it never finished a pass. Because suggestions are only saved after the full analysis completes, that one slow step quietly blocked everything, so nothing was ever suggested. It now runs in a single efficient pass and finishes quickly even on very large histories, so learned routines and suggestions come through as intended. A smaller inefficiency in the daily-routine detector was tidied up at the same time. No settings change; existing data is used as-is.

## [7.64.0] — the "request too large" recovery now actually fits small tiers

Follow-up to the previous release: the automatic retry after a "request too large" (413) now also trims JARVIS's own tool set down to the essentials for that one retry, not just the Home Assistant per-entity tools. The full tool definitions are several thousand tokens on their own, so the earlier retry could still be too big for a tight tier like Groq's free gpt-oss-120b. The slimmed retry is now a small fraction of the size — enough to answer and to control devices via the core tools and on-demand entity lookup — so simple voice queries get a reply instead of dropping to offline.

## [7.63.0] — recovers from "request too large", and no longer goes silent on Gemini tool quirks

If your provider rejects a request as too large (a 413 — common on Groq's on-demand tier when you expose a lot of entities, which bloats the tool definitions), JARVIS now automatically retries once with a slimmer request: it drops the per-entity Home Assistant tool schemas and the full home-state snapshot, keeping its own controls and on-demand entity lookup. A simple question like "what time is it?" gets answered instead of dropping to the offline reply, and JARVIS can still operate devices via its own tools. If it recurs, lowering "home context max entities" or exposing fewer entities keeps requests small.

Also: when a Gemini "thinking" model rejects a tool call over the OpenAI-compatible endpoint (a "missing thought_signature" error), JARVIS now answers without tools instead of falling back to offline — so you still get a reply.

## [7.62.0] — safety notifications now speak your language

The freeze, intrusion, and lockdown notifications — the ones generated without the AI so they stay reliable — now appear in your Home Assistant language, titles included, across French, German, Spanish, Italian, Dutch, and Portuguese (English elsewhere). So a metric French home gets a freeze alert written in French, not English. One remaining detail: the lockdown message that lists exactly which doors and locks JARVIS just secured is still English while it's translated properly — the plain "already secured" and "lockdown lifted" messages are localized. Everything JARVIS says through its reasoning was already following your language as of the last release.

## [7.61.0] — replies follow your language, and settings apply consistently

JARVIS now replies in your home's configured language. If Home Assistant is set to French, German, Spanish, and so on, spoken and written conversational replies come back in that language; if you address JARVIS in another language, it follows yours. English homes are unchanged. Note that a few fixed safety notifications (such as the freeze and lockdown alerts) are still English for now — translating those message templates is separate, tracked work.

Also fixed a class of configuration bug: when the appliance monitor, the observer, or the lockdown manager were (re)started after a settings change, they were rebuilt from an incomplete view of your configuration and could miss panel settings. They now read the same complete, unified configuration the rest of JARVIS uses, so a setting you changed is the setting that takes effect.

## [7.60.0] — freeze warnings now respect your unit system

On a metric install, JARVIS was reading the outdoor temperature in Celsius but comparing it against a Fahrenheit freeze threshold — so a mild 18°C day would trip a false "pipe freeze" alert and the message would mislabel it as °F. Freeze detection now converts correctly before comparing and reports the temperature in your own unit (°C or °F), including the suggested heat setting. A genuine freeze still triggers exactly as before; a mild day no longer does. Household temperature summaries also fall back to your configured unit instead of assuming Fahrenheit.

## [7.59.0] — the reasoning-backend problem now shows up in Home Assistant's Repairs

When JARVIS can't reach its reasoning model and can't recover on the fallback, it now raises a Home Assistant Repair notice — the same actionable card HA uses for other integrations — spelling out the specific cause (for example a model your provider retired, or an authentication problem) and where to fix it. It clears itself automatically the moment reasoning is working again. This never blocks JARVIS from loading: device control, status, and scenes keep working in the meantime. Available in all seven supported languages.

## [7.58.0] — JARVIS learns how selective to be, and locks down novel security actions

Suggestions now get better at knowing when to speak up. When you turn on the new Adaptive suggestions setting, JARVIS watches how often its past suggestions turned out useful versus unnecessary and quietly adjusts how confident it must be before offering a new one — more selective after a run of dismissals, a little more forthcoming when they're landing well. The adjustment is bounded and always visible on the diagnostics card, and it only ever learns from suggestions — never from security or safety decisions. Off by default.

Security actions are also harder to slip past confirmation. The action risk classifier now recognises a lock or alarm action by the kind of device it is, not just by an exact list of known commands — so a lock or alarm service JARVIS hasn't seen before is treated as sensitive (a guard-dropping action like unlock/disarm as high risk, anything else unrecognised as needing review) instead of quietly passing as routine. Safe directions like locking or arming keep their low-friction behaviour, and everyday devices (lights, media, climate) are unaffected.

## [7.57.0] — when the reasoning backend is unreachable, JARVIS now tells you why

If a voice command reaches JARVIS but comes back with the "offline / reasoning systems" reply, the System Diagnostics card now shows the actual cause on the LLM row — for example a model name your provider has retired, or an authentication problem — instead of only reporting that the connection is down. That turns a puzzling silence into a one-line fix.

Also fixed: on installs configured entirely through the panel (where the underlying config entry is empty), the reasoning backend's fallback provider couldn't resolve, so a single hiccup on the primary provider dropped straight to the offline reply. JARVIS now resolves the full configuration for the fallback, so it can ride out a momentary primary-provider failure instead of going quiet.

## [7.56.0] — see how well JARVIS's confidence matches reality, and interrupt less when it's wrong

The System Diagnostics card now shows a judgment-calibration readout: for the decisions JARVIS has made and seen the results of, it groups them by how confident it was and shows how often each group actually turned out right — so you can tell at a glance whether "90% sure" really means 90%. It also reports an overall accuracy score (Brier) and how far confidence drifts from reality.

New optional setting: Adaptive interruptions. When on, JARVIS quietly interrupts less often after a run of alerts you dismissed as unneeded, and eases back up once its alerts are landing again. Off by default — nothing changes unless you turn it on.

Two settings that were already working under the hood now have controls in the panel: speaker-aware follow-up mic reopen (waits for the reply to finish on your speaker before reopening a satellite's mic) and sibling-burst coalescing (how long to fold a flurry from a bank of numbered sensors into one look).

# Changelog

All notable changes to JARVIS are documented here. This project uses semantic-ish
versioning (`MAJOR.MINOR.PATCH`); UI reskins and capability expansions bump MINOR,
bug fixes bump PATCH.

## [7.55.0] — follow-ups reopen the mic only after the reply finishes on the speaker
When continued conversation is enabled and JARVIS asks a follow-up, a mic-only satellite whose reply
plays on a separate speaker now reopens its microphone only once that speaker goes idle — so the mic
no longer picks up JARVIS's own reply. Previously Home Assistant reopened the mic based on the
satellite's own (instant) playback, before the speaker had finished speaking. If the speaker never
reports playback (e.g. a speaker group), JARVIS falls back to a spoken-length estimate so the mic
still reopens. Tunable via continued_conversation_speaker_reopen (default on); continued conversation
itself remains off by default.

## [7.54.0] — quieter observer: coalesce bursts from banks of numbered sensors
Observer mode now collapses a burst of numbered sibling entities — for example an alarm panel toggling
a whole bank of zone sensors (zone_49, zone_50, … zone_63) at once — into a single classification per
window instead of one call per sensor. A chattering sensor bank was flooding the language model with
redundant work and crowding real events out of the activity log. Tunable with observer_group_debounce
(seconds; default 90, set 0 to disable). Intrusion detection and safety response are unaffected — they
run on a separate check, and only entities whose name ends in a number are ever coalesced.

## [7.53.5] — fix: device-control commands failed with "connectivity issues"
Fixes queries that use Home Assistant tools — turning on lights, running scenes, reporting how many
lights are on, and similar — failing and replying "I'm experiencing connectivity issues with my
reasoning systems." The Home Assistant tool definitions were sent to the language model without
converting their parameter schemas into a serializable form, so the request errored before it ever
reached the model. The schemas are now converted correctly, and a single unconvertible tool no longer
breaks the whole request. (This path only began running once 7.53.4 restored the conversation handler.)

## [7.53.4] — fix: conversation agent crashed (NotImplementedError) — handler was outside the entity
Fixes the JARVIS conversation agent failing every voice/text turn with "Unexpected error during intent
recognition." A helper function had drifted to module scope in the middle of the conversation entity,
which ended the class early and left the whole message handler defined outside it — so Home Assistant
never saw JARVIS's handler and fell back to its own, which raises an error. The handler is now properly
part of the conversation entity, verified structurally with a test so it can't be orphaned again. This
is the root cause behind spoken replies not reaching paired speakers once the pipeline used JARVIS.

## [7.53.3] — fix: intent queries crash ("NoneType can't be awaited"); proactive TTS options
Fixes voice and text queries that trigger intent recognition (e.g. "how many lights are on") failing
with "Unexpected error during intent recognition." Home Assistant awaits JARVIS's intent-setup hook,
which was a plain function and could not be awaited; it is now a coroutine. Also fixes a proactive
announcement error when the text-to-speech engine rejects an option (e.g. speaking rate): JARVIS now
retries the announcement without the unsupported option instead of failing. (Reported in issue #12.)

## [7.53.2] — fix: voice turns crash in JARVIS's handler on current Home Assistant
Fixes spoken/typed turns failing with "Unexpected error during intent recognition." JARVIS was
overriding a conversation method that current Home Assistant marks final and uses to set up each
turn, which broke handling once the turn reached JARVIS. JARVIS now implements only the supported
handler. As a safety net, an unexpected error in the handler is now spoken back and recorded in
diagnostics with its cause, instead of surfacing as Home Assistant's generic error with no detail.

## [7.53.1] — make the voice-reply fix apply reliably
Follow-up to 7.53.0. Pointing the voice pipeline at JARVIS's own conversation agent now runs on every
startup instead of once behind the add-on setup, and it matches your pipeline by name or by its JARVIS
voice, so the correction actually takes effect and isn't skipped or raced. After this, speaking to a
satellite is handled by JARVIS and the reply is delivered to the paired speaker.

## [7.53.0] — voice replies now run through JARVIS (fixes replies not reaching your speaker)
Fixes spoken replies being generated but never delivered to the paired room/Cast speaker. The
auto-created "JARVIS" voice pipeline was left using Home Assistant's default conversation agent
instead of JARVIS's own, so JARVIS's speaker routing never ran and the reply went to a mic-only
satellite that can't play it. The pipeline's conversation agent is now set to JARVIS, and an existing
JARVIS pipeline on the wrong agent is repaired automatically on startup. If it isn't corrected for
any reason, set the pipeline's Conversation agent to JARVIS under Settings → Voice assistants.
Also fixes two startup file/import operations that ran on the main loop.

## [7.52.2] — revert reply-delivery change that broke spoken replies
Reverts the reply-delivery behavior (introduced across 7.50–7.51) that awaited delivery, polled the
paired speaker for a 'playing' state, and fell back to broadcast speakers. That verification misfired
on idle Cast/Nest speakers that play a short announcement fine but don't report a 'playing' state
during it, which broke spoken replies that had been working. Spoken replies now route to the paired
speaker the original way. Also fixes the diagnostics conversation-log buffer so it survives restarts
(it previously reset on every reload, making the log look empty after a redeploy).

## [7.52.1] — reply-routing decisions survive log floods
Added a dedicated conversation and reply-routing log buffer, included in Download Diagnostics, that a
burst of observer or anomaly activity cannot evict. The reply-delivery decisions — which speaker each
spoken reply reached and whether it fell back to a broadcast speaker — now stay visible in the
diagnostics even when the main activity log is flooded.

## [7.52.0] — fix: conversation crash on Home Assistant 2026.8.x
Fixes 'Unexpected error during intent recognition' (AttributeError: module
'custom_components.jarvis.intent' has no attribute 'async_setup_intents') on HA 2026.8.x. Home
Assistant's intent-platform loader calls async_setup_intents on any integration that exposes an
intent module; JARVIS routes intents through its own conversation agent rather than HA's intent
registry, so it now provides the expected entry point (a no-op) instead of crashing. Also moves the
lockdown state-file read off the event loop at startup, resolving the 'Detected blocking call to
open /config/jarvis/lockdown_state.json' warning. Note: if the custom Piper voice en_GB-jarvis-high
isn't installed, spoken output already falls back to the engine's default voice rather than failing.

## [7.51.1] — spoken replies fall back to broadcast speakers, not a silent satellite
Building on 7.51.0: when the paired room speaker won't play a reply — an idle or disconnected Cast
device that accepts the request but produces no sound — JARVIS now sends the reply to the
broadcast/announcement speakers that briefings already deliver to successfully, rather than the
voice satellite. Many satellites are mic-only (audio output disabled to free resources), so they
can't speak the reply themselves; routing to a known-working speaker means the reply is heard
instead of lost.

## [7.51.0] — fix: spoken reply lost when the paired speaker is idle/off
When JARVIS routes a spoken reply to a paired room speaker (a Google/Nest/Cast device), it silences
the voice satellite so only that speaker talks. But an idle or disconnected Cast device accepts the
request and plays nothing — so the reply was lost: the satellite stayed silent and the speaker never
played. JARVIS now confirms the speaker actually starts playing before silencing the satellite; if
it doesn't, the satellite speaks the reply itself. The reply is heard either way.

## [7.50.2] — Download Diagnostics is now self-contained
The diagnostics file is now a complete diagnostic picture rather than a config-and-health snapshot.
It includes the recent activity log — with the reply-routing decisions (which speaker each spoken
reply targeted, whether it reached a Cast speaker or fell back to the satellite) — the local
subsystem stats (cognition, decision record, intrusion, reasoning cache), and a resolved
audio-routing snapshot: each configured satellite→speaker pairing with its live reachability, plus
which text-to-speech engines are selected for replies vs premium contexts. Spoken-reply and routing
issues are now diagnosable straight from the download. Viewing diagnostics on a fresh install no
longer creates the decision store as a side effect.

## [7.50.1] — reply-routing visibility in the log
The Logs tab now shows how each spoken reply was delivered — which speaker it targeted, whether it
reached a Cast/Google speaker, and whether it fell back to the satellite. This makes it clear at a
glance where a missing spoken response is being lost.

## [7.50.0] — fix: spoken replies lost when a Piper voice is missing
When JARVIS routes a spoken reply to a paired room speaker (a Google/Nest/Cast device), it silences
the voice satellite so only that speaker talks. If the reply's text-to-speech was rejected — most
often because a custom Piper voice was removed or renamed by a Piper update — the reply was lost
entirely: the satellite stayed silent and nothing played on the speaker. JARVIS now (1) falls back
to the engine's default voice when the custom voice fails, so the reply is still heard, and (2) only
silences the satellite once the reply has actually been handed to the speaker — otherwise the
satellite speaks the reply itself. Briefings and other announcements gain the same voice fallback.

## [7.49.1] — fix: blocking file reads on the event loop
Home Assistant flags integrations that read files on its main loop — it can cause stalls, and it
prompts users to file bug reports. JARVIS now reads its persisted state (the panel file, saved
secrets, mode state, intrusion log, and reasoning cache) off the loop or from an in-memory cache,
so those warnings are gone and the loop stays responsive.

## [7.49.0] — quieter cognition: stop reasoning about diagnostic sensor noise
JARVIS's local cognition no longer treats diagnostic/technical sensors — board and CPU
temperatures, RF signal strength, reactive power, link stats — as anomalies worth escalating.
Their values naturally swing, so they were flooding the activity log and spending model calls on
nothing. Real sensors (room temperature, presence, doors, energy) are unaffected. Separately, any
single noisy sensor now escalates at most once every 30 minutes, so one flapping value can't drown
out everything else. Safety and access events are never throttled.

## [7.48.1] — fix: no response from JARVIS on current Home Assistant
Restores conversation on Home Assistant versions that moved to the newer conversation-entity API.
JARVIS now implements Home Assistant's current message handler, so spoken and typed requests reach
JARVIS again instead of failing with "Unexpected error during intent recognition".

## [7.48.0] — replay: test a decision threshold against real history
New `jarvis.replay_policy` service. Give it a kind of decision (e.g. intrusion) and, using the
outcomes you've already labelled, it reports the confidence threshold that best separates JARVIS's
right calls from its wrong ones — how accurate each threshold would have been, how many mistakes it
would have avoided, and how many good calls it would have lost. It's read-only: it evaluates
history so you can choose a threshold change confidently before making it, and it stays quiet until
there are enough labelled decisions to give a trustworthy recommendation.

## [7.47.1] — test-suite reliability
Reworked the camera-tool tests so they no longer depend on Python-build-specific import behavior
that was failing intermittently in CI. No functional change to JARVIS.

## [7.47.0] — expanded French translation of the settings panel
Most of the settings, labels, options, and help text throughout the panel are now translated when
Home Assistant is set to French — previously only the tabs and section headers were. Set Home
Assistant's language to French to use it.

## [7.46.0] — tune recognition strictness and Ollama context from the panel
Two settings are now adjustable from the panel. Recognition confidence sets how sure JARVIS must
be about a face before it names a person — below the threshold it records "unknown" — so you can
raise it to cut false names or lower it to name people more readily. Ollama context window sets
the context size (num_ctx) for local models, so a larger local model can use more context at the
cost of more memory; leave it at the default if you're unsure.

## [7.45.1] — Observer enabled from the panel now survives a restart
Turning on Observer mode from the panel didn't persist across a Home Assistant restart — on boot
JARVIS read the older add-on/entry setting instead of your panel choice, so the observer stayed
off. It's now read from the same single source as the rest of your settings, so a panel-enabled
observer comes back up after a restart. A manual observer start also now uses your current panel
settings rather than stale ones.

## [7.45.0] — smarter routine suggestions, judged by consistency
When JARVIS proposes an automation from a routine it noticed, it now scores that routine by how
consistently it happens — how many days it actually occurred out of how many it could have —
instead of a raw count of times seen. A routine that fires on most days is trusted more than one
that fires only occasionally, even when both were seen the same number of times, so fewer flaky
suggestions surface. The "why" behind a suggestion now shows the honest picture too, for example
"happened on 42 of 60 days (missed 18)".

## [7.44.0] — quick "unlock" commands now unlock (not lock), and honor confirmation
Fixed a bug where a spoken or typed "unlock the front door" could be read as "lock" and lock it
instead — unlock now unlocks, for a single door and for "unlock all doors". Separately, when
spoken confirmation is turned on, protected quick commands (unlocking a door, opening a garage)
now go through the same confirmation step as the rest of JARVIS instead of acting immediately, so
the quick-command path is no longer a way around it. Everyday quick commands — lights, climate,
media — are unchanged and still instant.

## [7.43.0] — periodic sweeps now scheduled and visible, cleaner reloads
JARVIS's recurring background tasks — the package/mail sweep, the service-health check, the
hazard monitor, and document auto-ingest — now run through a single scheduler that tracks each
one (when it last ran, how long it took, whether it's failing). The System Diagnostics self-test
reports scheduler health, so a sweep that quietly starts failing now shows up instead of going
unnoticed. Reloading or removing JARVIS also tears everything down through one path, making
reloads cleaner and less likely to leave stray timers behind. No change to what the sweeps do.

## [7.42.0] — conversation store in the self-test, plus reliability fixes
The System Diagnostics self-test now covers the conversation store, so a storage problem shows
up as a clear warning instead of quietly causing missed history. Calling off a false alarm now
records the outcome against that exact intrusion, keeping the decision history accurate even
when alerts happen close together. Also clears an internal date-handling deprecation so JARVIS
stays reliable on newer Python versions. No other visible change.

## [7.41.0] — protected actions now fail safe, and can't slip through in bulk
Locking, unlocking, opening a garage, and disarming the alarm now pass through a single
authorization step before they run. When spoken confirmation is turned on, these actions
require a clear "yes" — and if the confirmation can't be delivered for any reason, the action
is held back instead of going ahead. The same check now also covers batch commands and
multi-step plans, so a protected device can no longer be changed as part of a group without
confirmation. Everyday actions like lights, climate, and media are unaffected.

## [7.40.0] — decision outcomes: which proactive calls were right
Wires JARVIS's existing feedback signals into the decision record so each proactive decision
can get an outcome: dismissing a suggestion marks it "unnecessary", installing one marks it
"good", and calling off an intrusion as a false alarm marks it "wrong". This closes the loop —
the record now links decisions to whether they were actually useful, groundwork for a future
cognition score. No visible change on its own.

## [7.39.0] — decision record now covers intrusion + suggestions
Extends the internal decision record to JARVIS's other two proactive decision types: each
intrusion alert and each new automation suggestion is now logged with the facts it saw, how it
read them, and why — the same immutable, outcome-ready format as the anticipation alerts. This
completes decision recording for proactive behavior; it has no visible effect on its own yet.

## [7.38.0] — more languages + README language guide
Adds core-UI translations for eleven more languages — Polish, Russian, Ukrainian, Czech,
Slovak, Swedish, Danish, Norwegian, Finnish, Turkish, Romanian — plus Brazilian Portuguese as
a regional variant. The README now lists supported languages and explains how to add or
correct a translation (they're plain JSON files, no code). Untranslated strings fall back to
English, and community contributions are welcome.

## [7.37.0] — language picker + more panel translations
Adds a Language selector in Settings → General: choose Auto (follow Home Assistant), English,
or one of the translated languages, and the panel switches immediately. Also translates the
remaining diagnostics, status, and section labels, bringing each language to about 176
strings. Anything not yet translated stays in English.

## [7.36.0] — setup dialog translations (French, German, Spanish, Italian, Portuguese, Dutch)
The JARVIS setup and configuration dialogs (config flow) are now translated through Home
Assistant's own language system — every step, field, description, and error message in French,
German, Spanish, Italian, Portuguese, and Dutch. Home Assistant shows them automatically in
your selected language.

## [7.35.0] — broader panel translations (floor plan, toggles, messages)
Extends panel localization further across French, German, Spanish, Italian, Portuguese, and
Dutch — the floor-plan editor, proactive and observer toggle descriptions, feature names, and
common status messages are now translated (about 128 strings per language). Untranslated
strings continue to fall back to English.

## [7.34.0] — more panel translations + regional language fallback
Expands panel localization to the settings screen — most field labels, toggles, and buttons
across French, German, Spanish, Italian, Portuguese, and Dutch, so configuring JARVIS is far
clearer in those languages. Also adds regional language support: a variant like Brazilian
Portuguese (pt-BR) uses its own file if present, otherwise falls back to the base language.
Anything not yet translated stays in English.

## [7.33.0] — panel localization (French, German, Spanish, Italian, Portuguese, Dutch)
The JARVIS panel now follows your Home Assistant language. Section headers, tabs, and labels
are translated when a matching language file exists — starting with French, German, Spanish,
Italian, Portuguese, and Dutch. Technical values (entity IDs, model names, numbers) stay
unchanged. Translations are plain JSON files keyed by the English text, so the community can
add or extend a language without touching code; anything not yet translated stays English.

## [7.32.0] — decision-record foundation for proactive anticipation
Begins an internal record of JARVIS's proactive decisions. Each anticipation heads-up —
departure, routine, overdue, or presence — is now logged as an immutable entry capturing the
facts it saw, how it read them, the decision, and why, with a slot for a later outcome. This
is groundwork for evaluating whether JARVIS's proactive nudges are actually useful; it has no
visible effect on its own yet.

## [7.31.0] — bay windows and bump-outs in the 3D roof (floor plan, phase 3b-3)
Completes the polygonal floor-plan work: a room reshaped to jut out (a bay window or a bump-
out) is now recognized as its own section of the house and gets its own roof, instead of
stretching the main roof forward to cover it. Rectangular rooms and homes are unchanged.

## [7.30.0] — roof follows irregular footprints with a garage too (floor plan, phase 3b-2b)
Extends the irregular-footprint roof to homes with an attached garage: if the main house is
an irregular (L- or T-shaped) mass, each section gets its own gable, and the garage roof now
spans the garage's actual depth rather than the full house depth (fixing an overhang when the
garage is shallower than the house). Rectangular houses are unchanged.

## [7.29.0] — roof follows irregular footprints without a garage (floor plan, phase 3b-2b)
For homes without an attached garage, the 3D roof now follows an irregular (L- or T-shaped)
footprint instead of covering the bounding box — each rectangular section of the house gets
its own gable, so the roof no longer overhangs a notch. Rectangular homes and homes with an
attached garage are unchanged.

## [7.28.2] — continuous exterior walls in 3D (no seams between rooms)
Fixes the persistent "breaks" in the 3D exterior walls. Each room was drawing its own wall
segment, so adjacent rooms' walls met at a seam line that looked like a break. Collinear
walls now merge into a single continuous run, so a wall spanning several rooms draws as one
seamless wall.

## [7.28.1] — seamless exterior walls (snap touching rooms in 3D)
Fixes small gaps ("breaks") in the 3D exterior walls where two rooms were placed nearly — but
not exactly — touching. The 3D view now aligns edges within about a foot of each other, so
adjacent rooms form a continuous wall. Your saved layout, the 2D editor, and coverage are
unchanged; this only affects how the 3D house is drawn.

## [7.28.0] — exterior walls follow the real footprint (floor plan, phase 3b-2a)
The 3D house's exterior walls now trace the actual footprint outline instead of a bounding
box, so an L-shaped or stepped floor plan gets walls that follow its real shape, with shared
interior walls correctly left out. The pitched roof still spans the bounding box for now (the
polygonal roof is the next step) — so a non-rectangular footprint may show the roof
overhanging a notch until then. Rectangular homes look the same as before.

## [7.27.0] — floor-below outline in the editor
When editing an upper floor, the floor directly below now shows as a dashed red outline
behind your rooms — a footprint reference so you can keep the upper floor within the lower
one. Editing the 2nd floor shows the 1st floor's outline; editing the 1st floor shows the
basement's, if you have one. Outdoor zones are excluded from the reference.

## [7.26.1] — fix Prompt size = 0 (counts only) reverting to 15
Fixes the Prompt size setting snapping back to 15 in the panel after you set it to 0. The
value was being read back with a check that treated 0 as "unset," so the display reverted —
though the setting was actually applied behind the scenes. Setting it to 0 now sticks and
shows counts only, as intended.

## [7.26.0] — polygonal rooms in the per-floor 3D (phase 3b-1)
Reshaped (non-rectangular) rooms now render with their true shape in the per-floor 3D views
(1st Floor / 2nd Floor / Basement) — walls trace each polygon edge instead of a bounding box.
The exterior "All" view still uses the bounding box for the house shell for now; the full
polygonal shell and roof are the next step.

## [7.25.1] — fix upstairs windows lighting for the whole floor
Fixes the remaining part of the upstairs occupancy bug: the 2nd-floor windows (gable-end and
front/back) lit whenever any upstairs room was occupied, while the dormers correctly lit only
for their own room. Both now use the same per-room occupancy, so an upstairs window lights
only when the room it belongs to actually has someone in it.

## [7.25.0] — non-rectangular rooms (floor plan, phase 3a)
Rooms can now be any shape. Select a room and click Reshape to turn it into an editable
polygon — drag corners, double-click an edge to add one, right-click a corner to remove —
for L-shaped rooms, angled walls, and the like. Plain rectangular rooms are unchanged, with
the familiar resize handle. Camera coverage and sightlines use the true room shape. In the
3D house, reshaped rooms currently render as their bounding box; true polygonal 3D is next.

## [7.24.1] — fix upstairs showing fully occupied in the 3D view
Fixes the residence 3D view lighting every upstairs dormer when only one room is occupied
(and appearing to occupy rooms with no presence sensor). Each dormer now lights only for the
room directly beneath it, so occupancy on the house matches which rooms actually have someone
in them.

## [7.24.0] — zoom & pan in the floor-plan editor
The floor-plan editor can now zoom and pan. Scroll to zoom in on the cursor, middle-drag
(or drag empty space) to pan, and hit Fit to frame the whole plan again. This makes editing
fine details practical — placing openings, nudging cameras, dragging zone corners — instead
of being stuck at a zoomed-out view of the whole property.

## [7.23.1] — reduce prompt size for tight LLM token limits
Adds a "Prompt size" setting (on the AI Models card) controlling how many entity names
JARVIS lists per type in the system prompt. Lower it — 0 shows counts only — to shrink each
request for providers with tight tokens-per-minute limits, such as Groq's free tier, which
rejects requests over 8000 tokens. The assistant still discovers entities on demand, so
nothing breaks. Fixes conversations failing with "request too large" on small-quota
providers.

## [7.23.0] — outdoor zones can be any shape (polygons)
Outdoor zones are no longer limited to rectangles. Drag a zone's corners to reshape it,
double-click an edge to add a corner, right-click a corner to remove one — so a zone can
follow an irregular yard, an L-shaped lot, or a stepped boundary. Drag the zone's body to
move the whole thing. Camera coverage samples the true polygon shape, and zones still stay
out of the 3D house.

## [7.22.3] — place outdoor zones anywhere, including left of and in front of the home
Fixes not being able to move an outdoor zone to the left of the garage or in front of the
home. The editor was clamping every object to positive coordinates, so nothing could be
placed past the garage-side or front edges of the house. Objects can now be placed anywhere
in the editing field, and the field keeps generous room on all sides to work in.

## [7.22.2] — floor-plan grid now covers the whole lot (front & side yards)
Fixes the editor grid stopping at the front and garage-side edges of the house, which made
it impossible to place a front-yard or side-yard zone there. The grid was only drawn from
the origin outward; it now spans the full canvas — including the area in front of and beside
the home — so outdoor zones can be placed anywhere on the property.

## [7.22.1] — property boundary frames the whole lot (place zones anywhere)
Fixes not being able to place an outdoor zone at the front or sides of the home. With a
property boundary set, the editor now frames the entire lot, so the whole property is the
workspace, and the default boundary is generous — giving room in front, back, and to the
sides to drop zones. Draw your property, position your home on it, and place front/back/side
yards where they belong.

## [7.22.0] — property boundary + land size (place your home anywhere on the lot)
Adds an optional property boundary to the floor plan. Click "+ Property Line" to draw your
lot, then drag the corners to your actual property lines — double-click an edge to add a
corner, right-click to remove one — so the boundary can be any shape, not just a rectangle.
The lot area shows in acres or square feet (hectares/m² in metric). With a boundary set, the
editor frames the whole lot, so you can position your home anywhere on it — front, back, or
corner — rather than being forced to the center. It's optional: apartments and interior-only
setups can skip it entirely.

## [7.21.2] — outdoor zone fixes: openings no longer snap to zones
Fixes exterior doors and windows snapping to an outdoor zone's edge instead of the house
(the house footprint now ignores outdoor zones). Also keeps outdoor zones out of the
interior-opening room picker, spawns new zones just below the house instead of on top of
it, and adds margin around the house in the editor so there's room to place zones.

## [7.21.1] — Home Assistant 2026.8 LLM API compatibility
Fixes JARVIS failing to use Home Assistant's built-in LLM tools on HA 2026.8, where the
ToolInput and LLMContext APIs changed (the request context moved out of ToolInput, and
user_prompt was dropped from LLMContext). JARVIS now adapts to whichever field set the
installed Home Assistant version expects, so it works on both older and newer HA. Thanks
to @QuentinVape40 for the detailed report.

## [7.21.0] — outdoor zones for exterior camera coverage
Adds outdoor zones to the floor plan. Draw areas like Front Yard, Driveway, or Backyard
(the new + Outdoor Zone button, or Add Room with type "outdoor") and your exterior cameras
now show what they cover ("sees: Driveway 90%") instead of "nothing in view." Zones are
kept out of the 3D house so they don't change its shape, and they give JARVIS a model of
the areas around the home — which also feeds intrusion, so a camera watching the driveway
can be chosen to confirm someone there.

## [7.20.0] — coverage wired into intrusion + clipped FOV cones (camera coverage, Phase 3)
Camera coverage now feeds intrusion confirmation: when JARVIS detects a breach it
prefers a camera whose saved coverage actually sees that area, so it can confirm a
person through a camera in an adjacent room with a sightline — the dining camera seeing
the living room through the open staircase — not only a camera physically in the room.
And the camera FOV cones in the floor-plan editor now clip to walls and bleed through
openings, so each cone shows what the camera really sees instead of passing through
walls into the yard.

## [7.19.1] — fix phantom exterior door from cased openings
Fixes a cased opening (an interior open doorway) being drawn as an exterior door on the
3D house — which could appear as a door on the rear wall that isn't in your plan. Cased
openings are interior and no longer show up on the exterior shell.

## [7.19.0] — camera coverage: AI judgment + description (camera coverage, Phase 2b)
The floor-plan editor can now describe camera coverage in plain language. Hit Compute
coverage and JARVIS judges which rooms each camera can actually confirm a person in and
writes a short summary — "the full dining room and most of the living room through the
open staircase, and a corner of the kitchen." It reasons from the sightline geometry, so
open plans and pass-throughs are accounted for. Results are saved with the layout. If
the model is unavailable it falls back to a geometry-only summary.

## [7.18.0] — camera coverage: sightline geometry (camera coverage, Phase 2a)
Each camera now shows which rooms it can actually see, computed from the floor plan.
Sightlines travel through open doorways and cased openings and are stopped by solid
walls — so a camera in an open-plan dining room picks up the kitchen and living room it
has a line into, while a camera in a closed room sees only that room. Coverage shows
live under each camera and recomputes as you aim or move it. A following phase adds an
AI pass to describe coverage in words and feed it into intrusion confirmation.

## [7.17.0] — camera placement + field of view (camera coverage, Phase 1)
Place cameras on the floor plan. Add a camera, bind its camera entity, and aim it —
set the facing direction and field-of-view width, and for outdoor cameras how far it
sees (indoor cameras are bounded by walls). Each camera shows as a dot with a
translucent FOV cone on the 2D editor; drag the dot to move it, right-click to delete.
This is the placement groundwork — automatic coverage inference (which rooms each
camera can actually confirm) comes in the next phase.

## [7.16.0] — cased openings (open doorways) in the floor plan
You can now place a cased opening — a doorway with no door, an open pass-through — in
the floor-plan editor, alongside interior doors. Like an interior door it attaches to a
room and a wall, but it has no sensor (it's always open). This models the open sightlines
and flow between rooms (e.g. a living room open to a dining room), and lays the groundwork
for the upcoming camera-coverage feature, which needs to know which spaces are visually
connected.

## [7.15.0] — Lab & Movie mode room binding + Movie mood
Lab and Movie can now be scoped to specific rooms from the Operational Mode card.
Choose which room(s) Lab applies to, so its minimal-interruptions quiet covers just
the workshop rather than the whole house — the rest of the home stays normal. Bind
Movie to a room (and optionally a media player) with a dim level, and JARVIS dims that
room's lights the moment Movie mode turns on. Both modes stay settable by hand or voice,
and safety is never affected.

## [7.14.0] — automatic operational mode + more openings sensors
JARVIS can now switch its operational mode on its own: when Auto is on (Operational
Mode card), it follows occupancy — Away when the home empties, back to Normal when
someone returns. Deliberately chosen modes (Party, Movie, Lab, Guest, Focus) stay put
while you're home and are only superseded by Away once the house is empty; they remain
fully hands-on via the mode buttons or voice. Turn Auto off for fully manual control.
The floor-plan opening → sensor picker now also lists window contact sensors, so every
window, door, and dormer can be mapped to its sensor.

## [7.13.1] — Cape Cod 2nd-floor window height + lower garage roof
Second-floor gable-end windows now ride high on the gable — clamped under the roofline
so they never poke through the slope — instead of sitting low near the eave, and the
attached garage roof drops further. On a Cape Cod, Cabin, or any 1.5-story gable, the
upstairs window beside the garage now sits where it should and clears the garage roof.

## [7.13.0] — Dutch Colonial home style (gambrel roof)
Adds a Dutch Colonial home style with a proper gambrel ("barn") roof — a shallow
upper slope over a steep lower slope, with second-floor windows sitting high in the
steep slope where they belong. Attached garage roofs now sit lower, well below the
second floor, so they no longer collide with upstairs windows. Pick it under Home
Style on the Residence tab.

## [7.12.0] — bigger floor-plan editor, rotatable 3D preview, window-height fix
The floor-plan editor is now larger and auto-fits to your rooms, so bigger
properties and edge elements — like a rear dormer or a far bathroom — are no longer
cut off. The 3D preview above the editor can be rotated by dragging, and both it and
the Residence 3D now have quick view buttons (front, rear, left, right, iso). Also
fixes second-floor windows that aren't in a dormer rendering too low (they now sit
in the second-floor band instead of at the garage-roof line).

## [7.11.1] — routine-learning entity picker fixes
The "add specific entities" picker in Routine Learning is now a searchable field
that fits the panel instead of an oversized dropdown, and entities you add now
reliably appear in the list. Also includes a small internal cleanup.

## [7.11.0] — opt in doors, windows and presence to routine learning
Routine learning still skips noisy door/window and presence signals by default, but
you can now opt them in — a new Routine Learning card in Settings has toggles for
learning door/window activity and presence/arrivals, plus a picker to add specific
entities (like a garage-bay occupancy sensor) as routine triggers. This lets JARVIS
build routines such as closing a garage door once a car is parked in its bay.

## [7.10.0] — state backup/restore + a routines guardrail
Two additions. JARVIS can now back up and restore its own state — memory, patterns,
knowledge, and config — via the jarvis.backup and jarvis.restore services, so a
device re-flash or migration doesn't lose it (back up, download the file, re-flash,
restore, restart). And the diagnostics self-test now flags when the identity
confidence bar is set so high that per-person routines can't attribute, with a
suggested range.

## [7.9.2] — current Groq models + self-healing model selection
Groq retired the models JARVIS shipped as defaults, so a fresh Groq setup failed to
validate and camera vision returned errors. Two changes fix it: the defaults now use
Groq's current lineup (openai/gpt-oss-120b for the agent, reasoning, and briefings;
the multimodal qwen/qwen3.6-27b for camera vision), and — so this doesn't recur as
providers rotate models — each model setting now checks the provider's live model
list and switches to an available model if the saved one is gone (keeping vision on
a multimodal model). Setups with a valid selected model are unaffected.

## [7.9.1] — disabled cameras no longer appear in Command Center
Cameras you've disabled are now hidden from the Command Center — both the live
camera selector and the "analyze now" dropdown — matching the rest of JARVIS.

## [7.9.0] — routines learn who, even before it's certain
Observed behaviour is now attributed to the most likely person even when JARVIS
isn't fully certain, instead of being dropped as "unknown" — so per-person routines
accumulate and their owner firms up as recognition improves. Certainty still gates
personalized actions; a genuine coin-flip between people stays unattributed.

## [7.8.3] — model list loads for Ollama without a base URL set
The model picker now loads Ollama models using the default local endpoint when the
LOCAL LLM URL field is blank, instead of failing with "base URL required." Setting
the URL is still recommended — it's what enables embeddings/semantic search.

## [7.8.2] — interior doors render in the floor view
Fixes interior doors (like a kitchen door) not appearing in the per-floor 3D view
when their room was matched — the room name is now compared case-insensitively.

## [7.8.1] — basement openings + upstairs window placement
Basement exterior doors and windows now render on the 3D home — as walkout doors
and grade-level windows — where before they didn't appear. Second-floor windows on
the side walls now sit on the actual gable end instead of floating just inside it.

## [7.8.0] — pick the travel sensor and origin from a list
The departure "Origin tracker" and "Travel sensor" settings are now dropdowns of
your available entities — people and device trackers for the origin, and
travel-time sensors for the travel sensor — instead of typing an entity id.

## [7.7.0] — live 3D preview in the floor plan editor
The floor plan editor now shows a live 3D view of your home above the canvas. It
updates as you edit — adding or moving rooms, placing windows and doors, and
positioning dormers — so you can see the model come together without leaving the
editor.

## [7.6.0] — interior doors in the floor views
Placed interior doors now render in the per-floor 3D views, on their room's wall,
and show open or closed based on their mapped sensor.

## [7.5.0] — place your dormers
Dormers can now be placed individually. On the 2nd-floor editor, add front or rear
dormers and slide each one along the roof; the number you add sets how many render.
Homes without placed dormers keep the automatic evenly-spaced dormers from the
home style.

## [7.4.0] — garage doors show open or closed per bay
Each garage door in the 3D home now reads its own mapped sensor and shows open
(rolled up) or closed independently, using the per-bay sensor slots.

## [7.3.2] — bulkhead angle + upstairs window placement
The bulkhead cellar door now sits at a shallower, more realistic angle and lower
profile, so it no longer covers nearby windows. Second-floor windows now render
over the upstairs footprint (the main house) instead of over the garage.

## [7.3.1] — bulkhead cellar door + upstairs windows
The cellar door now renders as a sloped bulkhead against the wall (Bilco-style)
instead of a flat panel. Second-floor placed windows now render too — side windows
on the gable ends and front/back windows on the roof.

## [7.3.0] — the 3D home shows your placed windows and doors
The whole-house 3D view now draws the windows, exterior doors, and cellar door you
placed in the editor, at their positions, and each door shows open or closed based
on its mapped sensor. Homes without any placed openings keep the automatic
per-room windows.

## [7.2.2] — highlight the opening you're mapping
When you hover an opening's row or open its sensor dropdown, its marker on the
floor plan lights up, so it's clear which window or door you're mapping.

## [7.2.1] — opening refinements
Placed openings now use a dropdown of your door and window sensors instead of a
text field. Interior doors are attached to a room and then to a wall of that room.
A Cellar Door option was added, and each garage door has its own sensor mapping —
one per bay.

## [7.2.0] — place windows and doors in the floor plan editor
The floor plan editor now lets you place openings on each floor — windows,
exterior doors, and interior doors. For each one, choose which wall it sits on and
where along that wall, set its width, and map it to a door or window sensor.
Placed openings show as markers on the plan.

## [7.1.2] — gabled dormers
Dormers on the 3D home now have proper gabled (peaked) roofs instead of a flat top.

## [7.1.1] — 3D home refinements
The whole-house 3D view now gives an attached garage its own lower, shallower
roofline, so a single-story garage reads correctly next to the taller main house.
Dormers sit properly on the roof instead of recessed and now also appear on the
back slope, and a cellar door shows on homes that have a basement.

## [7.1.0] — the whole-house 3D view is a clean exterior again
The "All" view of the 3D residence now shows the exterior of the home — walls, a
home-type roof, dormers, garage doors, a chimney, and windows — with occupancy
shown as lit windows, instead of a stack of interior room boxes. It is built from
your floor plan and home style, so it reflects your actual home. Individual floor
views still show that floor's rooms for editing.

## [7.0.0] — choose which cameras JARVIS uses
You can now pick which cameras JARVIS uses. Under Settings -> Cameras, every
camera has an on/off toggle, with Enable all and Disable all buttons — so you can
use all of them, only some, or none. A disabled camera is left out of everything
JARVIS does with cameras: event watching, doorbell and package detection, and the
presence scan.

## [6.102.0] — the roof follows your home type
The 3D residence now wears a roof shaped by the home style — gable, hip, or flat
— sized to your floor plan's footprint, with dormers by count. A Cape Cod keeps
its half-story with the upstairs tucked under the roof, a two-story wears the roof
on top, and modern and apartment styles get a flat roof.

## [6.101.1] — the 3D house updates the moment you save the floor plan
Saving (or importing and saving) the floor plan now refreshes the 3D residence
right away instead of waiting for the next background sync, so your layout shows
up in the 3D house immediately.

## [6.101.0] — the 3D house is built from your floor plan
The 3D residence model is now generated from the rooms in the floor plan editor
instead of a fixed built-in layout. Each room's real dimensions size and place it
in the 3D view, and the footprint and exterior walls follow your rooms — so
editing the floor plan changes the house.

## [6.100.0] — export and import your floor plan
The floor plan editor can now export the current layout to a file and import a
layout back in. Use Export to keep a backup before making changes, and Import to
restore a saved layout (review it on the canvas, then Save to apply). This makes
it safe to experiment with the layout and return to a known-good version.

## [6.99.1] — floor plan editor fixes; address comes from Home Assistant
Fixes the floor plan editor: adding a room now updates the canvas right away,
switching floors works every time instead of only once, and the controls stay
responsive after each change. The editor's separate address field and map
overlay have been removed — JARVIS now uses the home location already configured
in Home Assistant.

## [6.99.0] — real per-room dimensions in the floor plan editor
The floor plan editor now works in real dimensions. Each room shows its size on
the canvas, and selecting a room lets you set its exact width and length by
typing them. A units control switches the editor between imperial (feet) and
metric (metres).

## [6.98.0] — briefings work with reasoning models
Some local models — including gemma4, qwen3, and deepseek-r1 — are reasoning
models: they think in a separate channel and only produce their answer once the
thinking finishes. On the briefing's small token budget the model spent it all
thinking and returned an empty answer, so the briefing had nothing to say. JARVIS
now asks the local model to answer directly instead of thinking out loud, and
gives the briefing enough room to finish, so reasoning models produce a proper
spoken briefing.

## [6.97.0] — briefings always speak, even when the model returns nothing
Briefings were going silent because the language model kept returning an empty
response, and the briefing skipped the announcement entirely when that happened.
It now falls back to reading the facts it already gathered — time, weather, who
is home, overnight events, open doors, calendar, energy, and active hazards — so
you get a briefing instead of silence even if the model produces nothing.

## [6.96.0] — briefings reach the speakers again
Scheduled and manual briefings went silent because the briefing service resolved
its TTS engine and speakers from a config layer that is empty when all settings
live in the panel. It fell back to defaults, could not find a TTS entity or a
speaker, and stopped before playing anything. It now reads those settings from
the same effective configuration everything else uses, so the briefing finds your
engine and announcement speakers and plays. Voice-requested briefings, which
reply through the requesting satellite, were never affected.

## [6.95.0] — briefings fire on uncertain presence; tidier settings layout
Scheduled morning and evening briefings are meant to skip only when the house is
empty, but the check treated any non-"home" presence — including "unknown" or
unavailable — as empty, so a scheduled briefing would silently skip whenever
presence was not a clean "home". It now skips only when every tracked person is
explicitly away; if presence is uncertain, the briefing plays. Voice-requested
briefings were never affected.

Separately, the Settings tab laid its cards out in a fixed grid, so each row's
height was set by its tallest card and shorter cards left large empty gaps
beneath them. Cards now flow in a tighter column layout that packs them by
height, so the tab fills the space instead of leaving holes.

## [6.94.0] — activity feed gains category icons
Each event in the Activity Feed now shows a small icon for its kind — motion,
doors, security, packages, energy, weather, cameras, briefings, and departures —
so the feed reads at a glance instead of as a column of repeated text tags.

## [6.93.0] — the system self-test now covers cameras
The "Run check" self-test under Settings → System Diagnostics now includes your
cameras alongside the LLM, embeddings, and speech engines. It reports how many
cameras are available and flags any that are unavailable, so one check tells you
whether everything JARVIS relies on is up.

## [6.92.0] — setup checks the LLM connection before finishing
When you enter a cloud API key or a local LLM URL during setup, JARVIS makes a
quick test call before creating the integration. If the endpoint cannot be
reached or the key is rejected, setup shows the reason and lets you fix it,
instead of installing and failing only once you try to use it.

## [6.91.0] — onboarding steps jump to the right setting
Each step in the welcome checklist now has a jump button that opens the Settings
tab, scrolls to the exact card it refers to — alert destination, cameras,
personality, or daily briefings — and briefly highlights it, instead of leaving
you to find it.

## [6.90.0] — the new anticipation & memory settings, in the panel
Everything the last several releases added — departure and routine alerts,
cross-session memory, continued conversation — was configurable only by editing
config on disk. Now it has a home in the Settings tab: a new "Anticipation &
Memory" card with toggles for departure alerts, routine alerts, memory
threading, and continued conversation, plus the numeric knobs (departure lead
time, the memory window and turn cap) and the optional open-source-routing fields
(origin tracker, OSRM URL, travel sensor). Everything reads its current value and
writes back through the panel like the rest of the settings.

This is the first of the onboarding-and-UI polish pass. 4 new panel smoke
assertions cover the card's presence and wiring.

## [6.89.0] — departure travel time goes open-source
The "leave now, sir" anticipation no longer leans on Google Travel Time or Waze.
Both are a poor fit here: Google's is a paid API, and both require a fixed origin
and destination baked into the integration — so covering more than one
destination would mean standing up a separate instance per place. Departure now
works dynamically: it takes your live location from device tracking, geocodes the
event's location with OpenStreetMap's Nominatim, and gets the drive time from
OSRM — all keyless, with OSRM's endpoint configurable (`departure_osrm_url`) so
you can point it at a self-hosted server.

When there is no device fix, the event has no location, or a routing call fails,
it falls back to the configurable fixed lead (`departure_lead_minutes`) exactly
as before, so nothing regresses; an explicit travel-time sensor is still honored
if you have set one. All network calls are async with a short timeout, and
geocoding results are cached in-process. 20 tests cover the routing math, the
geocode/route orchestration with the network mocked, and the departure logic end
to end.

## [6.88.0] — continued conversation (turn-taking foundation)
JARVIS can now hold a conversation open. When a response ends with a question or
an offer to act — "which room did you mean?", "shall I schedule it?" — the
satellite keeps listening for your reply without a fresh wake word, so a
back-and-forth flows naturally instead of "Jarvis..." every turn. A response
that is just a statement ends the turn as before.

The trigger is deliberately conservative (a trailing question or a clear offer,
nothing more) and the whole behavior is off by default
(`continued_conversation_enabled`), since natural turn-taking depends on your
satellites' listen timing and is best switched on and tuned against real
hardware. The continue signal is set defensively so it degrades gracefully on any
Home Assistant core. 7 new tests cover the turn-taking heuristic and the switch.

This is the in-process foundation; no-wake ambient response, barge-in,
multi-satellite continuity, and reopen timing for external (Cast/Nest) speakers
layer on top and are validated on the satellites themselves.

## [6.87.0] — one situational picture, not a device list
JARVIS's reasoning now starts from what is actually happening, not just what it
can control. The context it reads before every complex request used to be a
static inventory — areas, entity counts, aliases. Now, alongside that, it
composites a live situational snapshot: the time, who is home and where, the
weather, the next couple of calendar events (and any conflict between them),
current power draw, and a line of recent activity. So "should I turn the heat
down?" is answered against "it is 9pm, nobody is in the living room, and you are
drawing 6 kW," not in a vacuum.

Each signal is gathered from what JARVIS already tracks — presence, calendar,
energy, weather, the observer's recent-events buffer — and each is independently
guarded, so a missing weather entity or an unconfigured power meter simply drops
out of the picture rather than breaking it. 12 new tests cover the composite and
each signal.

## [6.86.0] — memory that threads across sessions
JARVIS now picks up where you left off. Until now each conversation started
cold, remembering only the last twenty messages of the current session; anything
from yesterday, or from before the last restart, was gone. It had all been saved
to disk the whole time — it just was not read back. Now, when a fresh
conversation begins, JARVIS seeds it with a bounded slice of recent history (the
last day or two, capped) so "what did we decide about the thermostat?" or "finish
that list from earlier" lands with context instead of a blank stare.

The threading is deliberately conservative: it seeds once per conversation, only
when the in-session window is empty (so it never double-counts the turn you are
in the middle of), reaches back a configurable window (48 hours, twelve turns by
default), and truncates long turns to keep the context lean. Memory is unified
across the home rather than split per satellite, so continuity follows you from
room to room. 9 new tests cover the shaping, the bounded DB read, and the
configuration.

## [6.85.0] — person-level routines, unstarved
JARVIS learns per-person routines and now speaks them: "around this time you
usually start the coffee." But first it had to actually *have* them — and it
did not, for a subtle reason. Every state change is stamped with whoever is
likely responsible, and that attribution has a sole-occupant shortcut ("only one
person home, so it is them") — except the shortcut was only reached when the
event carried no room, and JARVIS always passes the room now. So on a
single-person home, room resolution came back inconclusive and the event was
filed as "unknown," which meant the routine detector — which needs several
occurrences attributed to a *named* person — never had anything to work with.
The store was fine; it was starved.

The fix lets a sole occupant be attributed even when the room is known: if room
resolution is inconclusive but exactly one person is home, it is them.
Multi-person room logic is unchanged. This is forward-looking — history already
filed as "unknown" cannot be re-attributed — so routines materialize after about
a week of newly-attributed behavior, then surface as gentle "you usually start X
now" prompts through the same gated announce path as the rest of JARVIS's
anticipation, and only when that person is actually home.

The per-person routine store also gets its own home: a dedicated
`person_patterns.py` module owns the table, the upsert, and the read, with the
pattern analyzer and the Memory panel delegating to it. 18 new tests cover the
store, the attribution fix, and the routine prompts.

## [6.84.0] — anticipation: "leave now, sir"
JARVIS now watches the clock against your calendar and tells you when it's time
to head out. For the nearest upcoming timed event it warns once — "heads up, the
dentist at Main St begins in about 20 minutes; you'll want to head out" — timed
so you are not late. Lead time comes from a travel-time sensor (Waze or Google
Travel Time, if you have pointed JARVIS at one via `departure_travel_sensor`)
plus a small buffer, or a configurable default (`departure_lead_minutes`, 30 by
default) when you have not. The alert rides the same gated announce path as the
rest of JARVIS's proactive awareness — it speaks when you are around and pushes
quietly when you are not — and only fires while proactive awareness and the
local cognition layer are on.

This slots into the existing anticipation engine, which already flags things
unusual for the time of day; departure was the one piece of the "leave now, sir"
instinct that was not there yet. 8 new tests cover the timing (alert only once
it is actually time to leave), the travel-sensor lead, all-day and out-of-horizon
events, the once-per-event-per-day guard, and the off switch.

## [6.83.0] — ephemeral sub-agents, and credentials that live in secrets.yaml
JARVIS can now spin up a focused sub-agent for a complex slice of a request. Ask
for something multi-step and self-contained — "gather this week's schedule and
the weather for it" — and it delegates that to an in-process sub-agent with a
minimal objective, a curated read-only tool set, and a small turn budget, then
folds the result back into the main answer. It is not a separate process and it
is not parallel — on one machine the point is a tight, focused context the model
reasons over cleanly, with less drift. Sub-agents are read-only and cannot
recurse: actuators, anything that writes a persistent store, and delegation
itself are denied, and depth is capped.

LLM credentials move out of plaintext. API keys previously sat in the panel's
config.json; they now belong in Home Assistant's secrets.yaml, resolved
everywhere JARVIS builds a model client — and secrets.yaml wins. On upgrade, any
plaintext key still in config.json is relocated automatically and safely: JARVIS
writes it to secrets.yaml, re-reads to confirm it is durable, and only then
removes the plaintext copy. If anything about that fails, config.json is left
exactly as it was, so a key can never be lost and auth can never break. The
writer backs up the file and preserves everything else in it.

The README now carries a full capability reference — every agent tool, grouped
by domain, alongside the features they back. 26 new tests cover the delegation
machinery (capability scoping, the denylist, the depth cap, nested-invocation
wiring) and the credential path (the safe writer, the secrets overlay, and
verify-before-strip relocation).

## [6.82.0] — one source of truth for which model runs
JARVIS now resolves its LLM provider, model, and credentials from a single
authoritative place, ending a class of drift where different parts of the
integration could each pick a different model. A new resolver treats the panel's
saved settings (config.json) as the source of truth, layering them over the Home
Assistant config entry so the panel always wins — but only where it holds a real
value, so a blank field can never wipe a key the entry is carrying.

Before this, the startup LLM client and the conversation fallback read the
provider and key straight from the config entry, which can hold stale values
from an earlier setup — enough to instantiate an impossible pairing like a cloud
provider with a local model name while the panel showed something else. Those two
paths now go through the resolver, matching the agent and observer, which already
honored the panel. Whatever the panel shows under AI Models is what every part of
JARVIS runs.

6 new tests cover the resolver: the panel winning over stale entry data and
options, blank panel values leaving entry credentials intact, entry options
outranking entry data when the panel is silent, and tolerance of a missing entry.

## [6.81.0] — read-only email, native to the integration, credentials in secrets.yaml
Ask JARVIS to check your email and it now can. A new `read_email` tool reads the
most recent messages from your inbox over IMAP — "anything important come in?",
"summarize the inbox", "any unread from the office?" — and answers in JARVIS's
voice. It runs entirely inside the integration: no separate mail server, no
add-on, no new dependency, just the Python standard library with the blocking
IMAP session handed to the executor so the event loop never stalls.

Reading is read-only, and provably so. The mailbox is opened with EXAMINE rather
than SELECT, bodies are fetched with BODY.PEEK so nothing is ever marked read,
and the module contains no delete, move, or flag code at all — a test asserts the
source stays free of those verbs so it can't regress. Fetched mail is treated as
untrusted: every subject and body is HTML-stripped, length-capped, has common
prompt-injection phrasing declawed, and is handed to the model wrapped as data to
summarize, never as instructions to follow.

Credentials move where they belong. The IMAP password is read from Home
Assistant's secrets.yaml (add it under `jarvis_imap_password`) through a new
read-only resolver — it is never written into the panel config. The non-secret
connection settings (host, port, username, folder, SSL) live under Settings →
Configure → Email like any other option. A missing or malformed secrets file
degrades to a clear error instead of taking setup down. 25 new tests cover the
resolver, the read-only guarantees, the sanitizer, and credential resolution.

## [6.80.1] — embeddings failures say why, and how to fix them
The embeddings health check could report "Ollama embed call returned no vectors"
— true, but not a diagnosis. That message covers a model that isn't pulled, an
unreachable host, and an HTTP error alike, and they need different fixes.

The failure reason is now specific and actionable. A missing model says which
model and gives the exact command to pull it; an unreachable host says so; an
HTTP error carries the status. The same specific reason shows in the System
Diagnostics status and in the Settings embed-test button, and it clears the
moment a real embedding succeeds. 4 new tests covering the model-not-pulled,
empty-200, sticky-error-clears, and probe paths.

## [6.80.0] — suggestions show their reasoning, not just their conclusion
When JARVIS proposed an automation, the panel showed a one-line description, a
confidence number, and approve/dismiss. You had to trust it. The evidence that
justified the suggestion — which entity, what time, how many days running, which
person — was computed and then thrown away before it reached you.

That evidence now carries all the way through. Each suggestion in the panel shows
what JARVIS actually observed: the routine it noticed, how many times over the
last month, how consistent it was, and who it was tied to — laid out as the
reasoning behind the proposal. The pattern type is labelled, the entities
involved are listed, confidence is a colour-graded bar, and the generated
automation is one click away under "see the automation." Approving becomes an
informed decision instead of a leap of faith.

Nothing about the detection changed — this surfaces reasoning the analyzer was
already doing. New pattern_type, entity_ids, and details columns on the
suggestions table (migrated automatically), a pure explainer that turns pattern
evidence into a human "why," and a rebuilt review card. 11 new tests.

## [6.79.0] — documents ingest themselves
Dropping a manual or receipt into the documents folder used to require pressing
Scan in the panel before JARVIS could answer questions about it. It now picks up
new files on its own. Every ten minutes JARVIS checks the documents folder — and
any watch folders you've configured — and ingests anything new.

The scan is incremental: it tracks each file's modification time and ingests a
file only when it's new or has changed, so it never re-embeds the whole library
on a timer. Drop a PDF in, and within a few minutes you can ask about it; edit
one, and the change is picked up on the next scan. Unsupported file types and
oversized files are skipped, and a failure to read one file never stops the
rest.

This was the one genuinely missing piece from several rounds of looking over the
codebase — the ingestion pipeline, mtime tracking, and watch-folder scanning all
existed already; what was missing was running them on a schedule. 6 new tests.

## [6.78.2] — one dead speaker no longer silences the whole house
Briefings requested from the panel produced nothing, while asking out loud in a
room worked. The difference was how many speakers each path targets. A spoken
reply goes to the one speaker in the room you're standing in; a briefing is a
broadcast, and broadcasts went to every media player in the house as a single
request. That request succeeds or fails as a unit — so one target that couldn't
accept it, an off television or a stale cast device, failed the whole thing and
nobody heard anything.

Two changes. Broadcasts now skip players that are unavailable, since they can
never render audio anyway. And if a broadcast still fails, JARVIS retries each
speaker individually, so the reachable ones hear the briefing and the log names
the ones that didn't. What used to be all-or-nothing now degrades to
whoever-can-hear-it.

7 new tests, including the exact case: one dead speaker alongside a working one,
and the working one still plays.

## [6.78.1] — scheduled briefings actually run
The morning and evening briefings fired on schedule but produced nothing. The
scheduler passed the wrong name for the language-model client — one that exists
in the service handlers but not where the scheduler lives — so every run raised
an error immediately, and the handler logged it at debug level, which meant the
failure never surfaced anywhere you'd see it. Fixed the reference, and raised
that logging to a warning so a briefing that fails says so.

Also corrected the hazard monitor's spoken alert, which passed its arguments in
the wrong order and would have announced the wrong thing.

Both were invisible to the existing checks, so the audit gained a third gate:
it now resolves names statically and fails on any reference to something that
doesn't exist in scope. A plain syntax check accepts that kind of error happily
— it only appears at runtime, and only if something is listening. The gate
catches the original bug exactly. 4 new tests, including one that guards every
spoken-alert call site against the argument-order mistake.

## [6.78.0] — briefings that arrive on their own
JARVIS could already deliver a spoken briefing, but only when something called
the service. It now runs them itself, morning and evening, at times you set.

A new Briefings panel gives each one an on/off switch and a time, plus toggles
for what goes in: weather and the day's forecast, your calendar, what happened
overnight, notable power draw, and — new to the briefing — any active hazards
near home, so a severe-weather warning or a nearby wildfire is part of the
morning summary rather than something you have to go looking for. There's a
"brief me now" button for a one-off.

Both briefings are off by default; JARVIS doesn't start talking on a schedule
until you ask it to. By default it also stays quiet when nobody is home, rather
than narrating the day to an empty house. The morning briefing looks back
overnight and the evening one across the day, and both reuse the same content
engine as the jarvis.briefing service, so a scheduled briefing says exactly what
a manual one would.

9 new tests.

## [6.77.0] — per-person routines that work with more than one person home
The pattern analyzer has been learning per-person routines for a while, but it
was starved: a state change was only attributed to someone when *exactly one
person was home*, and any event it couldn't pin down was discarded. In a house
with a family in it, that meant most of the day produced no usable data and
per-person routines stayed thin.

Attribution is now room-aware and probabilistic:

- **Room-scoped identity.** Sole occupancy of the *house* is rare; sole occupancy
  of a *room* is common. When the camera recognition for the room an event
  happened in shows one person, that event is attributed to them — even with
  several people home. Recognitions older than five minutes stop counting, and a
  room with two people in it contributes a weaker vote to each.
- **Proximity.** Device trackers and BLE room-presence that resolve to the
  event's area add a nearest-person vote.
- **Confidence instead of certainty.** Every attribution now stores how sure it
  was. A clear front-runner that didn't quite clear the confidence bar is
  recorded with low confidence rather than thrown away; a genuine tie still
  stays unknown, because inventing attribution would poison the routines.
- **The analyzer weighs by confidence.** Patterns are scored by summed
  confidence rather than raw counts, and a dominant "unknown" bucket no longer
  kills a pattern outright — it's skipped, and the named attributions are
  ranked among themselves. Commands keep full weight, since the conversation
  path already runs the full identity resolver.

New person_confidence column on state_changes (migrated automatically), and
identity gains room and proximity vote tiers plus a confidence-carrying
quick_identify. 11 new tests.

## [6.76.1] — the Hazard Monitor and vision-confirm switches actually toggle
The Hazard Monitor's on/off switch and its three feed switches, plus the
intrusion vision-confirmation switch, rendered correctly but did nothing when
clicked. They were built with a button class that has no click handler — the
panel's toggle handler only binds to buttons carrying the value to write, which
these were missing. Converted all five to the panel's standard toggle, so they
save and take effect.

Added a smoke check that fails if any config button is rendered without the
attribute that makes it clickable, since an inert control looks completely
normal and passed every previous check.

## [6.76.0] — intrusion log with snapshots, and training from your labels
Every intrusion event is now recorded to a reviewable log with its snapshot —
what JARVIS flagged, where, when, which rooms the motion touched, how far it
travelled inward, and the still it captured. The log survives restarts.

A new Intrusion Log panel shows the history and lets you mark each event **real**
or **false alarm**. Those labels are the training signal: when a
location-and-time pattern has been called a false alarm three times, JARVIS stops
firing the low-confidence alerts for it — the initial "investigating" ping and
the unanswered "unresolved" notice both go quiet for that pattern.

The safety limits on that learning are strict:

- **A confirmed intrusion is never damped.** A person confirmed on camera by
  JARVIS's own vision, or motion tracing a real inward route from the point of
  entry, always fires the full alert regardless of what has been learned.
- **The investigation always runs.** Damping silences the notification, not the
  watching — if a damped pattern turns into a real inward route, it escalates
  normally.
- **One "real" label cancels damping entirely** for that pattern. If a genuine
  intrusion ever happened somewhere, JARVIS will not learn to ignore it.
- **Labels expire** after 30 days, so a stale pattern stops suppressing.

New jarvis/intrusion log, label, and learning actions. 18 new tests, including
ones that pin the safety rules — a confirmed intrusion still alerts through
maximum damping, and a single real label restores full alerting.

## [6.75.0] — weather forecasts, so "what time is it supposed to rain?" works
Asking when it would rain returned the current clock time instead of a forecast.
The cause: JARVIS had no forecast capability at all — it could see current
conditions but nothing about what the weather would do later — so a question
phrased "what time…" matched Home Assistant's built-in current-time intent and
answered with the clock.

Added a weather_forecast tool that pulls the real hourly, daily, or twice-daily
outlook from your Home Assistant weather entity, including each period's time,
condition, temperature, and precipitation, so JARVIS can say *when* rain is
expected. Falls back to the daily forecast when an entity doesn't provide hourly
data, and reports a clear message when no weather entity is configured.

The agent prompt also now states plainly that a "what time" question about
weather is a forecast question and must never be answered with the clock — the
current time is only for when you actually ask for it. 9 new tests.

## [6.74.0] — intrusion detection traces the route inward from the entry point
The remaining source of false "intrusion confirmed" alerts. Confirmation fired
when motion appeared in two zones and one of them was near the breach — which is
not a route. Motion that simply lingered at an open window (an AC unit running in
it, a curtain moving) satisfied "near the breach," and an unanswered alert then
escalated it to a full house alarm.

Detection is now directional. A real intruder enters at the breach and moves
*inward* — entry, then deeper rooms. JARVIS now computes each room's distance
from the point of entry using the floor plan and tracks how far motion actually
propagates inward:

- Motion must reach a configurable depth of rooms **from** the breach (default 2)
  to confirm an intrusion. Motion that lingers at or beside the entry never
  confirms, no matter how long it continues.
- An unanswered alert with no confirming inward route no longer reports itself as
  a confirmed intrusion. It sends a softer notice instead — JARVIS says it
  flagged activity near the entry, couldn't reach you, and has *not* confirmed
  anyone moving through the house — with the snapshot attached.
- A person confirmed on camera by JARVIS's own vision still escalates
  immediately, independent of the motion route.
- Houses without a mapped floor plan fall back to the previous behavior, so
  nothing regresses for unmapped setups.

New: hops_from_breach room-distance mapping, intrusion_inward_depth config, and
the intrusion_unresolved alert type. 6 new tests, including one that pins the
core fix — motion pinging only the breach room can never confirm an intrusion.

## [6.73.1] — declare the logbook dependency (hassfest/HACS validation)
The 6.72.0 activity-history work imported Home Assistant's logbook component but
didn't declare it in the manifest, which failed hassfest's dependency check in
CI. Added logbook to after_dependencies (alongside recorder, which was already
there) — JARVIS uses the logbook when it's present but doesn't require it to
start. Also added a test that checks every imported HA component is declared in
the manifest, so this class of validation failure is caught locally by pytest
instead of after a push.

## [6.73.0] — intrusions confirmed by JARVIS's own eyes, not just Frigate
Fixes the intrusion false alarms. Frigate's person detection was being trusted
as sufficient proof of an intruder — the moment Frigate's person sensor went on,
JARVIS escalated to a full alert. But Frigate false-positives on shadows,
headlights, reflections, and the like, so those became false intrusion alarms.

Now Frigate's person sensor is treated as a *trigger to look*, not proof. Before
escalating, JARVIS snapshots the flagged camera and asks its own vision model
whether a person is actually there:

- Vision confirms a person → escalate, as before.
- Vision says no person (empty room, shadow, light, reflection) → treat Frigate's
  signal as a false positive and don't escalate on it. A real intruder still
  moving through the house is caught by the movement-based logic instead.
- Vision can't run or is unsure → fall back to the previous behavior (trust the
  camera), so a broken vision path never *suppresses* a real alert. It fails
  toward safety, never toward silence.

The check is on by default with a toggle in the Intrusion panel ("Confirm Frigate
person with JARVIS vision before alarming") for anyone who wants Frigate-only
behavior. Uses your configured vision provider/model. 5 new tests covering
confirm / deny / inconclusive / kill-switch.

## [6.72.0] — JARVIS can read Home Assistant's activity history
JARVIS can now answer "what's been happening in the house?" from Home
Assistant's own records — not just its last-known state or its private pattern
log. A new activity_history tool reads HA's native history and logbook through
two lenses:

- **Device history** — the recorder timeline for an entity or a whole area over
  a window: every state change with timestamps, plus a count. Ask "when did the
  front door open?", "how many times did the garage open today?", "what was the
  thermostat doing overnight?" and JARVIS reads the real record.
- **Logbook** — HA's readable activity narrative over a window, optionally for
  one entity. Ask "what happened while I was out?" or "what's been going on?"
  and JARVIS summarizes the actual logbook.

Recorder and logbook internals vary by HA version, so every query is wrapped
defensively and run through the recorder's own executor — a miss returns an
empty result with a note, never an error and never a fabricated event, in
keeping with the rest of JARVIS. Queries are bounded (entity breadth, row
counts, and look-back windows are capped) so a broad question can't drag the
recorder. Conversational only — no panel changes. 11 new tests.

## [6.71.0] — real-time hazard monitor: earthquakes, severe weather, disasters
JARVIS now watches for natural hazards near home and speaks up the same way it
does for anything else. Three free, no-key government/agency feeds, polled every
10 minutes, scoped to your location:

- **Earthquakes** — USGS, filtered to a radius around home and a minimum
  magnitude (default 300 km / M2.5), so you hear about a real nearby quake, not
  micro-tremors or events across the world.
- **Severe weather** — the National Weather Service's active alerts for your
  exact point, filtered to genuinely notable severities (Extreme / Severe by
  default) so a tornado or flash-flood warning alerts but a minor advisory
  doesn't.
- **Natural disasters** — NASA's Earth Observatory tracker for wildfires,
  volcanic activity, and severe storms within range of home.

Location defaults to the coordinates Home Assistant already knows, with an
optional lat/long override in the panel. Each feed dedups on stable event IDs so
a standing event never re-alerts, and a feed that can't be reached is skipped
quietly — a failed fetch is never turned into a false alarm. Alerts push to your
phone and speak through your speakers like every other JARVIS alert.

New: a hazard_report agent tool ("any earthquakes nearby?", "are there weather
warnings?"), a Hazard Monitor panel card (master switch, per-feed toggles,
location override, radius/magnitude tuning, and a Scan Now button that runs a
live read-only check), the jarvis/hazard WS command, and the config to drive it.
Off by default — enable it in Settings. Crime monitoring is intentionally left
as a future opt-in, since there's no clean location-specific crime source to
build on. 35 new tests.

## [6.70.3] — service health stops crying wolf
The System Diagnostics panel was reporting core services as DOWN when they were
actually working — a synthetic health poke that missed once (for example, an
embedding model unloaded from VRAM at idle, or a speech engine that only goes
available on demand) flipped the service to an alarming red, even though real
use succeeded moments before. Reworked the health model so it reflects reality:

- **Three states instead of two.** OK (verified working), IDLE (reachable but
  not recently exercised, or a synthetic poke missed — shown calmly, never red),
  and DOWN (reserved for a genuine failure during real use). A cold model or an
  idle engine now reads IDLE, not DOWN.
- **Only real use can mark something DOWN.** The actual call sites report their
  outcomes — a real document ingest/search for embeddings, a transcribed voice
  turn for STT, an agent call for the LLM, a spoken announcement for TTS — and
  only a genuine-use failure turns a service red. The synthetic probe can set OK
  or IDLE but never DOWN.
- **Probes tolerate a transient miss.** Health checks retry a couple times
  before concluding, so a momentary blip (like a model loading on demand)
  doesn't alarm.
- **Hourly background sweep.** The health check now re-runs itself hourly,
  gently — reachability only, never alarming on its own — so the panel stays
  current without opening it.

This directly fixes the false "Embeddings DOWN" and "STT DOWN" readings when both
were functioning. 20 new/updated tests covering the three-state logic, the
real-usage tracking, and probe retry.

## [6.70.2] — stop acting on questions; fix the diagnostics download
Two bug fixes.

**Questions no longer trigger actions.** Asking "when did you turn on the
nightstand and why?" could cause JARVIS to turn the light on — treating a
question about the past as a command in the present, then repeating it each time
you asked again. Two changes stop this: the agent prompt now carries an explicit,
prominent rule that a question about a device (when/why/whether/how) is never a
request to change it — it answers by reading state instead of acting — and
get_entity_state now returns each entity's last_changed / last_updated
timestamp, so "when did this turn on?" is a question JARVIS can actually answer
by looking rather than guessing. Real device commands ("turn on the lamp") work
exactly as before.

**The Download Diagnostics button works.** The integration page's diagnostics
download failed with "File wasn't available on site" because the integration
never exposed Home Assistant's diagnostics entry point. It now produces a useful,
credential-redacted dump — config, service health, cognitive/connectivity
status, and entity counts — with all API keys, tokens, and the address stripped
before anything is written.

9 new tests, heavy on the diagnostics redaction (nothing sensitive reaches the
file) and the questions-are-not-commands prompt guard.

## [6.70.1] — remove hardcoded sample address from the Residence tab
The Residence tab displayed a specific street address as its default when none
was configured — baked into the property banner, a fallback, an input
placeholder, and a development harness file. Replaced with neutral placeholders
("ADDRESS NOT SET", a generic "123 Main St" example) so a fresh install shows no
real address until you enter your own. The address you set still lives only in
your local config, as before.

## [6.70.0] — first-run welcome, and a friendlier front door
A new install now greets you instead of dropping you into a wall of settings. A
dismissible **welcome card** appears on the Command Center for fresh setups with
a short checklist of the high-value next steps — set an alert destination,
connect cameras (optional), set up voice (optional), pick a personality level —
each showing a live done/to-do state computed from your actual configuration,
plus a progress bar, an "Open Settings" jump, and a "try asking JARVIS…" prompt.
It hides itself once the essential step is done or you dismiss it (persisted, so
it stays gone). The LLM key is still collected during the normal Add-Integration
flow before the panel ever loads; this fills the "what now?" gap after that.

The README is restructured so a newcomer sees value and a low on-ramp first: a
"Quick start (5 minutes, no cameras required)" section up top, the deep
Nest/go2rtc camera setup moved below Installation into a clearly-optional
"Advanced setup" section, and Requirements split into the two things you
actually need to begin versus optional add-ons. Content is the same; the order
now front-loads getting started instead of advanced configuration.

New onboarding state in the panel data, an onboarding welcome card with dismiss
+ settings-jump, and the onboarding_dismissed flag. 7 new tests; no new agent
tools or LLM surface.

## [6.69.1] — encode the reasoning discipline in the agent prompt
The agent's system prompt gains a compact "How you reason" section ahead of the
tool-routing rules, encoding the investigate→verify→act discipline as explicit
methodology: read actual state before concluding (the house is the source of
truth, not expectations of it); separate observation from inference; run a
cheap verification before consequential actions; confirm results after acting
rather than assuming success; fail safe on thin evidence for anything
irreversible; and say "I don't know" plainly rather than inventing. Four new
static guard tests pin the section and its tenets so a future prompt rework
can't silently drop them.

## [6.69.0] — snapshots in notifications, and escalate if no one answers
Two safety additions building on the 6.68.0 intrusion work.

**Snapshots ride the notifications now.** When JARVIS confirms an intruder on
camera, the still it captures is attached to the push sent to every device —
not just shown in the panel. Android gets it via `image`, iOS via
`attachment.url`, so whichever phone you're on renders the snapshot inline; the
local snapshot path is made absolute with your external URL so the companion
app can fetch it off your network.

**Unanswered alerts escalate on their own.** The initial "investigating" alert
goes out to notifications and voice; if no one responds within a configurable
window (default 2 minutes, tunable 1–10 min in the Intrusion panel) and the
situation is still active, JARVIS escalates to the full alert automatically — an
unanswered possible break-in should fail toward alerting, not toward silently
waiting. There are now three ways to respond: **call off** (false alarm — stops
everything), the new **acknowledge** ("I'm looking" / new acknowledge_alert
tool and an "I'm looking (hold)" button — holds the auto-escalation because
you're handling it, but JARVIS still escalates if a person appears on camera),
and **no response** (the timeout fires).

Care taken on the timeout: motion that starts and then stops still clears as
benign — a curtain flutter or a pet that moved once won't escalate just because
no one answered. The timeout only bites while something is actively still
happening. New acknowledge_alert tool, jarvis/intrusion gains an acknowledge
action, intrusion_response_timeout config, and the panel's hold button +
timeout selector. 18 new tests (heavy on the still-active-vs-benign distinction
and the notification image data); tool surface now 35.

## [6.68.0] — intruder snapshots, and call off a false alarm
When JARVIS confirms an intrusion on camera, it now grabs a **snapshot** from
that camera and attaches it to the alert — so the notification and the new
Intrusion panel show who/what triggered it, not just "motion detected." The
still is saved to a servable path and rides along on a new
`jarvis_intrusion_confirmed` event (with `snapshot_url`), so your own
notification automations can attach the image too.

And you can **call off a false alarm.** Say "it's a false alarm," "that's me,"
or "stand down" (new `dismiss_intrusion` tool), or hit the call-off button in
the panel. This clears the active investigation, stops any further escalation,
and suppresses re-triggering for a cooldown so the same benign motion doesn't
immediately re-alarm — and it records the false alarm, so recurring harmless
triggers can inform future tuning. The suppression is time-boxed, then the
system re-arms on its own.

Safety stays intact: the call-off only suppresses escalation for its cooldown
window; a genuinely new, unrelated trigger after it expires alarms normally.
The intrusion investigation now captures the person-detecting camera's entity
(not just a yes/no), which is what makes the targeted snapshot possible. New
intrusion.py module, dismiss_intrusion agent tool, jarvis/intrusion WebSocket
command, and the Intrusion panel with the live snapshot and call-off. 12 new
tests (heavy on the call-off suppression window and snapshot capture never
raising); tool surface now 34.

## [6.67.0] — voice-confirm sensitive actions, and ask out loud
JARVIS can now hold a spoken back-and-forth for the moments that need it. Two
opt-in capabilities, both in Settings → Voice Confirmation:

**Voice-confirm sensitive actions.** Before JARVIS unlocks a door, opens the
garage, or disarms the alarm, it asks out loud and waits for a spoken yes/no —
the confirmation pattern HA built for exactly these. Fail-safe by design: if
the answer isn't clearly affirmative (or anything goes wrong), the protected
action does NOT run. Which actions count is sensible by default (lock/unlock,
cover open, alarm disarm, security switch-off) and tunable per entity — add one
to always confirm, or prefix with '!' to exempt it.

**Open-ended follow-up.** JARVIS can ask a question aloud and listen for a free
answer, passing conversation context so a bare "yes" or "the blue one" is
understood.

Two delivery paths, chosen by mode. **Native** uses HA's
assist_satellite.ask_question / start_conversation, which sequence
announce → wait → listen internally — this works when the satellite's own audio
output routes to a real speaker. **Gated** is the fallback that fits JARVIS's
ears-only satellites: it speaks the prompt through the room speaker (your Nest)
via the normal announce path, waits for playback to finish (no echo), then
reopens the mic on the satellite. **Auto** tries native and falls back. Because
this hinges on where your satellite audio routes, there's a **Test Satellite
Audio** button that fires a bare announce so you can hear which path your setup
supports and pick accordingly.

New voice_confirm.py module, jarvis/voice_confirm_test WebSocket command,
who_do_you_see stays, and the Voice Confirmation panel with toggle, mode, and
test. Protected-action gating is wired into control_device. 18 new tests
(heavy on the fail-safe guarantee); tool surface unchanged at 33.

## [6.66.0] — JARVIS can actually see you now (Frigate face recognition, fixed)
Ask JARVIS "can you recognize me?" and it can finally say yes. The problem: the
Frigate-native identity added in 6.59.0 read a recognized name off the
`sub_label` on the `frigate/events` topic — but modern Frigate (0.14+, HA
integration 5.9.2+) doesn't reliably put it there. It publishes recognized
faces to a dedicated MQTT topic, `frigate/tracked_object_update`, as
`{"type":"face","name":"Sam","score":0.93,...}`, and exposes a
`sensor.<camera>_last_recognized_face` per camera. JARVIS was listening in the
wrong place, so its recognition cache stayed empty and it truthfully reported
that it couldn't see anyone.

This wires up both correct channels. JARVIS now subscribes to
`frigate/tracked_object_update` and handles `type: "face"` payloads in
real time, AND reads the `last_recognized_face` sensors directly — so the
conversation context ("Recent faces: Sam recognized at dining room ~91%") is
populated from whichever source has data, and a new `who_do_you_see` agent
tool answers "who do you see / can you recognize me" on demand by checking the
sensors live. Person detection and the older sub_label path still work; this
adds the channels modern Frigate actually uses. Honors the recognition_source
setting (Frigate side). 12 new tests; tool surface now 33.

## [6.65.0] — fix settings that reset after saving; pick your recognition source
**Bug fix:** several Settings controls saved your choice but snapped back to
the default on the next render — most visibly the JARVIS Character banter level
(pick "Full — MCU JARVIS," watch it revert to "Dry"). The save was working
fine; the problem was that `get_panel_data` never sent these values back to the
panel, so every re-render re-read the default. Fixed for the whole affected
set: banter level, web-research backend, SearXNG URL, calendar tight-gap, and —
found by the same regression test — the Residence model detail controls
(stories, basement, dormers, garage bays, chimney, bedrooms, bathrooms), which
had the identical latent bug (masked because the 3D house rebuild used the
local value until a full refetch). Two new static guards now fail CI if any
saved-and-read-back config key is missing from the panel-data payload, so this
bug class can't return.

**New:** a **face recognition source** selector in Settings → Cameras — choose
Both (Double Take + Frigate), Frigate only (sub_label), or Double Take only.
Previously both were always active when configured, which double-fired
recognition events; now you pick. Person *detection* (which triggers camera
analysis) runs regardless of the choice — only identity firing honors it.

## [6.64.0] — write in and delete goals from the panel
The Goals panel is now fully manageable by hand. Previously you could only
cancel an active goal (and only JARVIS or a voice command could create one).
Now there's a text box to **write a goal in** directly — type an outcome, hit
Add, and JARVIS starts working toward it — and finished or cancelled goals get
a **delete** button to tidy the list. The panel also shows the Goals card even
when empty, with the new-goal input, so you can add the first one from
scratch.

Backend: a new `goals.delete()` that hard-removes a goal row (distinct from
`cancel()`, which keeps it in history), and the `jarvis/goal_action` WebSocket
command extended from cancel-only to create / cancel / delete. Voice goal
creation (the `create_goal` tool) is unchanged. 3 new goal tests and 2 panel
smoke checks.

## [6.63.2] — consolidated options flow (menu instead of forced steps)
The Settings → Configure options flow was four screens you had to click
through in sequence — change one setting and you walked all four. It's now a
menu: pick the section you want (Core, Routing, Observer, or Identity), edit
it, and you're done. Each section saves on its own, so touching the honorific
no longer means paging past routing, observer, and identity to reach the end.

Every option, default, selector, and the stored-value pre-fill behavior is
preserved exactly — this is purely how the flow is presented. The (already
removed in v6.45.0) integration/add-on split stays gone; setup remains
config-entry-only. Strings and translations updated to match; config-flow
tests updated for the menu structure with a new check that each section
saves independently.

## [6.63.1] — document the privacy & data story
Adds a "Privacy & your data" section to the README, spelling out JARVIS's
local-first posture: what it stores and where (all local SQLite under
`/config/jarvis/`), that there's no JARVIS cloud or telemetry, that document
ingestion is path-guarded to its own folder, and — now that biometrics exist
— that wellbeing context is opt-in, off by default, never stored or
transmitted, and explicitly not medical. Documentation only; no code change.

(Reviewed an external suggestion list against the codebase and found nearly
all of it already implemented — offline reasoning, self-diagnostics, the
protocol/mode engine, biometrics, environmental sensing, the HUD, CI, and
pattern automations all already ship. The data-privacy writeup was the one
genuinely missing piece worth adding.)

## [6.63.0] — biometric wellbeing context (Sensory Integration)
JARVIS can now "feel" the user's state by reading biometric entities a
wearable already surfaces to Home Assistant — heart rate, sleep, steps — so
it can be quieter when you're resting and factor wellbeing into how it
behaves. The most useful hook: a wearable's sleep entity now strengthens
JARVIS's sleep detection — a watch reporting "asleep" is stronger evidence
than bedroom occupancy alone, so quiet-hours suppression gets more accurate.

Discovery is heuristic over entity name / device class / unit, so it works
across wearables — Withings, Google Fit, Apple Health bridges, Oura, Fitbit —
without hard-coding any integration; an explicit `biometric_entities` mapping
can override. A new Wellbeing Context panel shows what's connected with an
enable toggle, and a `wellbeing_context` agent tool answers "how did I sleep?"
or "what's my heart rate showing?".

**This is not a medical device and never behaves like one.** It reads existing
entities as comfort/context only — it does not diagnose, does not raise health
alarms, and does not interpret vitals clinically. Readings are reported plainly
as what the device shows; anything concerning is deferred to the person's own
device or a real medical resource, never assessed by JARVIS. The output carries
a non-medical disclaimer, and it's strictly opt-in and off by default — health
data stays private until the user turns it on. New biometrics.py module,
wellbeing_context tool, jarvis/biometrics WebSocket command, sleep-detection
enrichment, and the panel. 19 new tests (including a guarantee the output
contains no diagnostic or alarm language) and 4 panel smoke checks; tool
surface now 32.

## [6.62.0] — whole-house energy management
JARVIS graduates from sensing power to managing it. The appliance monitor
already found the whole-home meter and fingerprinted appliances by wattage;
this adds the decision layer on top — reading current draw, understanding
what's running, and helping run the house efficiently. Ask "how much power
are we using?" or "what's running?" (new energy_status tool), or watch the
new Energy Management panel: live kW, a peak-threshold indicator, running
high-draw loads, and staggering advice.

The centerpiece is a **configurable agency ladder** you choose your comfort
level on: advisory (only surfaces insights), opt-in (proposes deferring a
load, acts only on approval), or autonomous (auto-defers high-draw loads over
the peak threshold). It ties into the Directive Layer — a mode listed in
`energy_mode_bump` (e.g. away) can raise the level one step while active,
letting the house save more aggressively when you're out. The bump is
opt-in and empty by default, so nothing surprises you: choose advisory and
JARVIS only ever advises.

Safety is absolute here: JARVIS never sheds a critical load — fridge/freezer,
medical (CPAP, oxygen), security, network, sump/well pump, or heat — matched
by a never-shed list, regardless of agency. The energy check runs in the
cognitive loop under the same gating as other proactive offers (kill-switch +
mode), and every entry point is defensive and never raises.

New energy.py module (reusing the appliance monitor's meter discovery rather
than duplicating it), energy_status agent tool, jarvis/energy WebSocket
command, and the Energy Management panel with an agency selector. 18 new
tests (agency ladder, never-shed guarantee, per-agency offer shaping, mode
bump) and 5 panel smoke checks; tool surface now 31.

## [6.61.0] — the Directive Layer: operational modes
JARVIS gains high-level operational modes — a single switch that shifts its
whole behavior profile at once, generalizing the proven Lockdown state
machine into a proper directive layer. Built-in modes: normal, party (relax
nagging, full wit, only critical alerts), lab (minimal interruptions), movie
(near-silent), guest (softer autonomy), away (convenience off, security
posture), and focus (hold non-critical interrupts). Say "party mode," "movie
time," "I'm heading out," or "back to normal" — or pick from the new
Operational Mode panel in Settings.

Each mode is declared as behavior *overrides* (proactivity, persona banter,
event-surfacing scope, whether graduated autonomy may auto-execute); a mode
only states what it changes and everything else falls through to normal
config. The active mode is consulted by three hooks — the proactive gate, the
persona banter resolver, and the autonomy auto-execute check — so switching
modes genuinely changes how chatty, how autonomous, and how selective JARVIS
is. State persists atomically across reboots, exactly like lockdown.

Crucially, **modes never disable safety.** Pipe-freeze, intrusion, and
lockdown run regardless of mode — mode gating only touches the
proactive/convenience layer, never SafetyManager. Users can define their own
modes via the `custom_modes` config key, overriding built-ins or adding new
ones. New `set_mode` agent tool and `jarvis/mode` WebSocket command; 15 new
tests and 3 panel smoke checks; tool surface now 30.

## [6.60.0] — system diagnostics: is everything JARVIS needs up?
A one-glance health check for the core services JARVIS actually calls — the
LLM backend, the embedding endpoint (when semantic search is on), the TTS
engine, and the STT/Whisper engine — surfaced both as a Settings panel with
per-service status lights and a re-check button, and as a `system_diagnostics`
agent tool ("JARVIS, is everything working?"). Anything down comes back with
a specific reason: "no LLM base URL configured," "stt.whisper is unavailable,"
"Ollama did not return an embedding — pull the embed model," rather than a
vague failure.

Signals are matched to each service: the LLM reuses the connectivity circuit
breaker plus a live /api/tags ping for Ollama hosts; embeddings reuse the
existing Ollama probe; TTS and STT are Home Assistant entities, so the check
confirms the configured engine exists and isn't unavailable. Services that
aren't configured report "off" (not a failure) and are excluded from the
healthy-count. Every check is defensive — the whole run never raises.

This lives inside the existing diagnostics package (alongside the
infrastructure-triage and fault-log subsystems) as a new service_health
module — deliberately narrow to the four services JARVIS depends on, not the
whole home. 12 new tests; 4 new panel smoke checks; tool surface now 29.

## [6.59.0] — Frigate-native face identity (Double Take optional)
JARVIS can now read a recognized name straight from Frigate. When Frigate's
own face recognition (or a plus model) attaches a `sub_label` to a person
event on `frigate/events`, JARVIS uses it directly — caching the match and
firing the same `jarvis_face_recognized` event that drives greetings and
recognition-aware behavior. Double Take is no longer required for identity;
it still works for setups that use it, but this is one fewer add-on in the
chain, with identity coming straight from the detection stream JARVIS
already watches.

The sub_label parser handles Frigate's version-to-version format drift — a
bare `"Name"` string or a `["Name", score]` pair, with 0..1 scores scaled to
percent and malformed values falling back safely. Both identity sources
(Frigate-native and Double Take) converge on the same cache and event, so
downstream persona logic is unchanged. 11 new tests covering every sub_label
shape and malformed input.

## [6.58.0] — visual questions and standing camera monitors
JARVIS can now look at a camera and answer a specific question about what's
there — "is a tool left on the workbench," "is the garage open," "did a
package come," "is anyone in the backyard." A new `look_at_camera` agent
tool captures a fresh snapshot and reasons over it with the vision model,
using your question as the prompt.

Two shapes, one mechanism. On demand: ask and it checks now. Standing
monitor: create a goal whose recurring action is a `look_at_camera` check —
the existing goal loop already handles the interval, re-arming, run budget,
and stays quiet unless the thing is found, so "keep an eye on the workshop
for tools left out" needs no new scheduler. The tool defaults to silent
(announce=false) so a background monitor doesn't narrate every clear check.

Built on the existing camera-analysis pipeline (`async_analyze_camera`), so
it inherits the tiered snapshot chain and graceful failure — a WebRTC-only
or offline camera, or a missing vision provider, returns a clear reason and
hint rather than failing silently. Honest about limits: vision LLMs are
reliable for presence/absence and coarse identification, not fine detail
like exact model numbers. 6 new tests; the tool surface is now 28 tools.

Note: gesture recognition (real-time hand-landmark detection) is
deliberately NOT added here — it needs continuous local CV at video frame
rate, which doesn't fit a snapshot-plus-cloud-LLM pipeline and would
reintroduce the native-ML dependency the project avoids. That belongs in a
dedicated detector (Frigate/MediaPipe) publishing events JARVIS can consume.

## [6.57.0] — semantic search via Ollama, no ChromaDB
The 6.56.0 approach hit a wall: ChromaDB's embedded mode depends on
onnxruntime, which has no wheel for the Python 3.14 that Home Assistant now
runs on, so the install could never succeed on this platform. Rather than
wait on an upstream wheel, this replaces it with something more in the
JARVIS-AIO spirit — reuse what's already here.

Semantic search now runs on the **Ollama server JARVIS already talks to**.
Embeddings come from Ollama's `/api/embed` (`nomic-embed-text` by default) —
no API key, no Python package, no native wheel to compile, works on any
Python version. Vectors are stored in JARVIS's own `jarvis.db` SQLite file
alongside the keyword index, and similarity is plain cosine computed in
stdlib Python (no numpy). No new service, no ChromaDB, no 300–500 MB
download.

Enable it in Settings → Document Library: it runs a live Ollama health
check, and once on, re-ingesting embeds your documents so retrieval matches
on meaning instead of keywords. It degrades to keyword (FTS5) search
automatically whenever Ollama or the embed model isn't reachable — the
document tools and panel keep working either way. Requires an Ollama host
(set `llm_base_url`) and a pulled embed model (`ollama pull
nomic-embed-text`).

New: `embeddings.py` (Ollama calls + SQLite vector store + cosine search),
a `jarvis/semantic_search` WS command (status/enable/disable/test), async
`ingest_directory_async` / `search_documents_async` in documents.py, and a
reworked search-engine banner. The obsolete ChromaDB `vector_backend.py` is
removed. 17 new embeddings tests (vector math, store, mocked Ollama
batch/legacy endpoints) and updated panel smoke checks.

## [6.56.1] — fix ChromaDB install; modernize CI actions
Two fixes surfaced by real deployment of 6.56.0.

**Semantic-search install failed** with "HA package helper unavailable:
cannot import name 'async_install_package'." There is no
`async_install_package` in `homeassistant.util.package` — the real helper
is the synchronous `install_package`. Now the enable button calls that
through an executor job (it shells out to pip/uv, so it must stay off the
event loop), and semantic search installs as intended. Keyword search was
never affected.

**CI Node 20 deprecation warning.** `actions/checkout@v4` and
`actions/setup-python@v5` run on Node 20, which GitHub is retiring in favor
of Node 24 — the Validate workflow was green but annotated with a warning.
Bumped to `actions/checkout@v5` and `actions/setup-python@v6` (both on Node
24), clearing it. No behavior change to the checks themselves.

## [6.56.0] — optional ChromaDB: semantic search, one button
JARVIS-AIO leans further into "all-in-one" without punishing small hosts.
Memory and document retrieval have always worked everywhere via the
built-in SQLite FTS5 keyword search; now you can upgrade both to true
semantic vector search by installing ChromaDB from a single button in
Settings → Document Library — no separate add-on, no manual pip, no HA
restart.

It's deliberately opt-in, not a hard requirement: ChromaDB pulls ~300–500 MB
(onnxruntime, tokenizers, etc.) that can be slow or fail to build on a Pi /
HA Green / Yellow. So JARVIS ships light and the panel tells you plainly
what enabling costs and where it's a good idea. The install runs through
Home Assistant's own package helper (lands in the env HA imports from) and
then re-initializes the memory and document stores in place, so vector
search activates immediately. If the host can't build it, the install
fails gracefully and keyword search keeps working — nothing breaks.

New: `vector_backend.py` (detect / install / re-init), a
`jarvis/vector_backend` WS command, and a search-engine banner in the
Document Library panel showing KEYWORD vs SEMANTIC with an enable button
and honest host guidance. 7 unit tests (detection, re-init resilience,
install flow with mocked HA helper, graceful failure) and 4 panel smoke
checks.

Also: the CI workflow gained a `workflow_dispatch` trigger (so Validate can
be re-run on demand from the Actions tab without a throwaway commit), plus
an explicit `permissions: {}` block and branch scoping on push. Note that
Actions simply hadn't run since 6.47.0 because nothing had been pushed since
that commit — the workflow was healthy and green, just idle. Verified the
repo is fully HACS-default-compatible: brand icons are correctly sized
(256×256 / 512×512) and satisfy the HACS brands check in-repo, manifest keys
and hassfest ordering are valid, and hacs.json carries the required name.

## [6.55.0] — Document RAG: JARVIS reads your manuals
The last un-built agent from the home-agent blueprint. JARVIS can now
answer from the household's own paperwork: drop appliance manuals and
receipts (PDF, .txt, .md) into `/config/jarvis/documents`, ingest them,
and ask "what's the furnace filter size?" or "when did we buy the
dishwasher?" — it retrieves the relevant excerpt and answers, citing the
source document, instead of guessing.

Built on the same ChromaDB the memory system already runs, but as a
*separate* collection — a manual isn't a conversation turn, and a furnace
query shouldn't surface old chats. Documents are chunked with overlap on
paragraph/sentence boundaries for good recall, embedded via Chroma's
default function (no extra model dependency), cosine-scored. When ChromaDB
isn't installed it falls back to FTS5 keyword search in the existing
jarvis.db, so retrieval works on a minimal install too. PDF text
extraction degrades honestly across pypdf / pdfplumber / PyPDF2 and, if
none is present, says so and skips the file rather than crashing — plain
text always works. `pypdf` is now a manifest requirement so PDFs work out
of the box.

Two agent tools (`search_documents`, `ingest_documents`), a
`jarvis/documents` WS command, and a **Document Library** panel in Settings
with an ingest button, live source list, and a test-search box. 17 unit
tests (the pure chunker, extraction routing, ingest/search through a
simulated collection, honest fallbacks) plus 4 panel smoke checks.

This completes every blueprint agent that belongs inside Home Assistant.

## [6.54.0] — the floor plan glows from live mmWave
The residence model now lights up room-by-room from genuine mmWave/presence
detection, not just the binary area-occupancy flag. A room whose presence
sensor is actively detecting gets a distinct, punchy aqua-green glow with a
brighter border — visibly the "hottest" room — while a room lit only by
generic area occupancy stays standard cyan, and the dominant room keeps its
mint. At a glance you can now tell *where a body actually is* versus where
HA merely thinks a zone is active.

Mechanically: `_house3dLit()` overlays the `jarvis/mmwave_overview` feed
onto the plan's lit map as a new `mmwave` state that flows through the
whole 3D builder — floor fill, walls, label, and pulse dot all render it
distinctly. The plan rebuilds when fresh mmWave data lands, so detection
appears live. Verified by rasterizing the plan and eyeballing that the
three presence states are actually distinguishable before shipping.

## [6.53.0] — mmWave presence overview
The residence tab gains a live **mmWave Presence** panel: every room with a
presence, motion, or occupancy sensor, showing whether it's occupied right
now, how many of its sensors are detecting, and — when clear — how long
since the last detection. Occupied rooms glow green and pulse; the header
summarizes at a glance ("2/5 OCCUPIED"). It's the ground truth behind the
floor-plan glow, surfaced directly instead of inferred.

This reads genuine sensor state, not the binary area-occupancy flag — a
room lit only by a door contact won't masquerade as mmWave presence here.
A new `jarvis/mmwave_overview` WS command assembles the per-room breakdown
from the occupancy sensors already mapped to HA areas; outdoor rooms are
tagged so yard sensors don't read as living space. Refreshes live on the
poll while the tab is open. 6 panel smoke checks; verified by rasterizing
the panel and eyeballing it before ship.

## [6.52.1] — learned automations for locks and covers are now valid
A latent bug in the pattern generator became reachable the moment 6.52.0
started installing suggestions. Every learned action was built as
`{domain}.turn_{state}` — fine for lights and switches, but nonsense for
other domains: a learned door-lock routine (the pattern module's own
flagship example, "front door locks after garage closes") would emit
`lock.turn_locked`, and a garage-cover routine `cover.turn_closed` —
invalid services that would write a broken automation to `automations.yaml`.

Actions now resolve through a domain-aware `service_for()`: locks get
`lock.lock`/`unlock`, covers get `open_cover`/`close_cover`, on/off domains
keep `turn_on`/`turn_off`, and cover transient states (`opening`/`closing`)
settle to their end state. Domains that need parameters we can't infer from
a bare state (climate, media_player) return no mapping, so those patterns
become advisory suggestions rather than broken automations. Time routines
were already gated to on/off and unaffected. 6 new tests, including the
end-to-end proof that a lock sequence installs `lock.lock`, not a
`turn_`-prefixed impossibility.

## [6.52.0] — the pattern engine closes the loop
The learning pipeline had a dead end: it observed behavior, detected
patterns, generated automation YAML, surfaced suggestions — and approving
one only flipped a database flag to `approved`. Nothing was ever installed.
The user saw "learned a routine," approved it, and… nothing happened.

Approval now **installs the automation into Home Assistant**. A new
`install_approved_suggestion` bridges the gap: it reads the suggestion's
stored automation, normalizes the generator's legacy shape into HA's
current format (translating `platform`→`trigger` and `service`→`action`),
and writes it live through the existing automation creator — then records
the suggestion as `installed`. Both approval paths use it: the panel's ✓
button and the voice tool ("JARVIS, approve that suggestion"). Concrete
suggestions (time routines, sequences, presence) install and go live
immediately; advisory-only ones (vague repeated-command notes) are still
acknowledged as approved but honestly reported as needing a human to
design — no fabricated automations. The panel toast and the agent both
relay which outcome occurred, and a `LEARN` line logs each install.

16 new tests: the normalizer across every pattern shape and malformed
input, plus the installer wiring end-to-end (installs, advisory skip,
missing suggestion, write-failure). Plus a panel smoke check for the
approve→install path.

## [6.51.2] — roadmap: local GPU inference shipped
Local GPU inference is done — Ollama on a dedicated GPU box (via a HAOS
GPU AI setup), so the reasoning chain runs templates → cache → local model
→ cloud on your own hardware. Moved it out of the roadmap's "on the
horizon" list into "shipped recently," and updated the Requirements note so
the local GPU server reads as supported now rather than a future item.

## [6.51.1] — README visuals, Nest streaming guide, roadmap trim
Documentation pass. The README gains faithful HUD visuals — a hero banner
and a two-up gallery of the Cognitive Core feed and the Camera Watch/DIAG
panel — rendered as SVG from the panel's actual design tokens (real cyan,
real fonts, real layout), so they represent the aesthetic without stale
screenshots to maintain. Added a full **"Continuous streaming for Google
Nest cameras"** guide: the go2rtc restream setup that defeats Google's
5-minute expiring streams, with the exact `nest:` source config, where to
find each credential, the optional Frigate hand-off, and the JARVIS
`camera_overrides` mapping that ties it together. Trimmed the roadmap —
UI Phase 3 (real-time WebSocket subscriptions, sparklines, entity cards,
log/feed search) has shipped, so it moved to a "shipped recently" note;
Document RAG is now called out as the last un-built blueprint agent.

## [6.51.0] — three new agents, and JARVIS finally sounds like JARVIS
The home-agent blueprint, reconciled against what already existed. Most of
its twelve agents were already here under other names — the Supervisor is
the agentic core, the Memory agent is the Chroma vector store, Vision is
the camera stack, Voice is the Wyoming pipeline. Three were genuinely
missing; two of the twelve (OS control, code execution) are deliberately
NOT built — arbitrary desktop automation and code execution inside the HA
process are security weight a home butler shouldn't carry, and there's no
display to drive in the sandbox anyway.

**Web Research agent.** A new `web_research` tool: ask about the outside
world and JARVIS looks it up, then relays the gist in its own voice.
DuckDuckGo's Instant Answer API by default (no key, no signup), switchable
to a self-hosted SearXNG. Results are summarized, capped, and sanitized —
never a raw page dump — and a failed lookup returns an honest "couldn't
find that," never an exception.

**Communication agent.** A new `calendar_agenda` tool reading the
`calendar.*` entities HA already exposes: upcoming events plus conflict
detection — overlaps, and back-to-back transitions tighter than a
configurable gap. Email is deliberately untouched; reading an inbox from
inside HA is privacy weight better handled by exposing specific mail as an
entity.

**MCU-JARVIS persona.** The voice now leans into Stark's JARVIS — dry,
clever, unflappable — with an engineered safety valve: full wit only at
light/neutral register, automatically silenced at urgent/grave. JARVIS
does not quip during a smoke alarm, and that's now structurally guaranteed
(the urgent/grave phrase pools can never gain banter lines — there's a test
that asserts exactly this). A **banter level** knob (plain / dry / full)
in Settings → JARVIS Character tunes it live, flowing into both the phrase
pools and the LLM's own prompt.

New: `web_research.py`, `comms.py`, banter valve in `persona.py`, two agent
tools, four panel-writable config keys, a JARVIS Character settings panel,
26 tests. The HTTP paths can't be exercised from the build sandbox (no
egress to the search endpoints), so they're covered by pure-shaper tests
and run live where HA has normal network access.

## [6.50.0] — camera management moves to Settings
The ✎ NAME button and its overlay are gone from Command Center — camera
renaming and indoor/outdoor designation now live in a **Cameras** panel on
the Settings tab, one row per camera: entity id (with the restream
override arrow when one is mapped), a name field (Enter or blur saves,
unchanged blur makes no call, blank reverts — placeholder shows the HA
name), and the AUTO/⌂ INDOOR/▲ OUTDOOR chips with instant save and the
resolved heuristic on AUTO. All cameras visible and editable at once
instead of one-at-a-time through an overlay, and Command Center's Camera
Watch head is back to just DIAG.

Two mechanics worth noting: the Settings tab already skips poll re-renders,
so typing a name is never wiped mid-edit; and the row refresh no longer
depends on `CSS.escape`, which isn't guaranteed in every embedding (found
by the smoke harness — the chip toggle silently died in its catch).

Same backend as 6.48/6.49 — `jarvis/rename_camera` and
`jarvis/camera_location` unchanged.

## [6.49.0] — tell JARVIS which side of the walls a camera lives on
The ✎ camera overlay gains a **LOCATION** row: AUTO / ⌂ INDOOR /
▲ OUTDOOR, saving instantly per click. AUTO shows what the heuristics
currently resolve — "AUTO (outdoor)" — so overriding is an informed choice,
not a guess.

This isn't cosmetic. `outdoor.py` is the single source of truth the whole
cognitive stack consults — the intrusion investigator (outdoor sensors must
not seed or confirm indoor investigations), the notable-outdoor-event
filter, and the motion scan. Its most-authoritative layer has always been
the user's word (`indoor_entities` / `outdoor_entities`); the new chips pin
the exact entity id into those lists via `jarvis/camera_location`, so a
designation immediately governs everything downstream. AUTO unpins and the
heuristics resume. Hand-written globs in those lists are preserved
untouched and still classify — they just read as AUTO in the picker, since
they aren't a per-camera pin.

Regression-tested end-to-end: an INDOOR pin beats an outdoor name keyword
(`camera.backyard_playroom` stays inside), an OUTDOOR pin makes a hallway
camera exterior for the whole stack, and unpinning restores heuristics.

## [6.48.0] — call your cameras what you actually call them
Cameras can now be renamed **inside JARVIS only** — HA entity names and
Frigate stream names stay untouched. Useful now that restream twins exist:
`eliana_restream` can just be "Eliana's Room" on the panel.

A **✎ NAME** button in the Camera Watch head opens an inline overlay for
the active camera: type a name, Enter saves, Esc cancels, blank reverts to
the HA name (shown as the placeholder so you always know what blank means).
The name applies everywhere the panel shows a camera — chips, the SRC
strip (including the override-mapping arrow), pickers — via a
`camera_names` runtime map, persisted through a new `jarvis/rename_camera`
WS command with a CONFIG line in the activity log.

Server logs and DIAG probes deliberately keep entity ids — display names
are for humans, entity ids are for debugging, and mixing them costs
precision exactly when it matters.

**Also in 6.48.0 — config.json can no longer take the panel down.** A
hand-edited `/config/jarvis/config.json` (adding `camera_overrides` by
hand) that parsed to something other than a JSON object crashed
`async_setup_entry` *before* panel registration — the integration never
loaded and the JARVIS tab died with "Unable to load custom panel."
`jarvis_config` is now self-healing: an unparseable or non-object file is
**sidelined** (preserved as `config.json.corrupt-<timestamp>`, never
deleted), defaults load, every accessor guards the cache type, and a
persistent notification explains exactly what happened and where your
edits went. A typo in that file now costs you a notification, not the
integration. Seven regression tests, including the exact live failure.

## [6.47.2] — a cloud blip is not a disarm
Live bug: lockdown was lifting itself overnight. Cause: when the
Cove/Alula integration lost its cloud connection, the alarm panel entity
went `unavailable` — and the alarm→lockdown sync only knew two states.
`_alarm_armed()` said "not armed," the sync read that as a disarm, and an
auto-engaged lockdown disengaged (with an announcement) because a cloud
API hiccupped.

The sync now sees three states: **armed** (any panel armed → engage, as
before), **disarm confirmed** (no panel armed AND at least one
affirmatively reporting `disarmed` → lift, as before), and
**indeterminate** (all panels unavailable/unknown, or none exist → HOLD
everything). During a dropout nothing engages, nothing lifts, the
manual-exit suppression isn't reset, and a throttled SAFETY line (once
per 10 min) records "alarm panel unavailable — holding lockdown" in the
activity feed so the outage itself is visible. Recovery to armed re-adopts
silently; a genuine disarm after recovery lifts exactly as it always did.

Six regression tests, including the precise live sequence:
armed_night → unavailable → held → disarmed → lifted.

## [6.47.1] — local model, cloud provider: auto-corrected
The GPU server's first contact produced a confusing error: Google's API
404ing on `models/gemma4:26b`. Root cause: `model` was pointed at the
local Ollama model but `llm_provider` still said a cloud provider, so
JARVIS faithfully forwarded an Ollama tag to Google. Two fixes:

  • **Routing correction** at the single provider choke point: a
    colon-tagged model (Ollama syntax — no cloud provider uses it)
    configured against groq/gemini/openai/anthropic now auto-routes to
    the ollama provider with the configured `llm_base_url` (or Ollama's
    default), with a clear correction logged. Explicit `ollama`/`custom`
    settings are never touched.
  • **Smarter fallback**: the agent's failure path used to replay the
    SAME model on gemini — a 404'd model 404s everywhere identically.
    Model-not-found is now detected as a settings problem (with an
    actionable ERROR log naming the fix), and the fallback goes through
    the reasoning tier's own provider+model instead.

Test-infra fix along the way: the `homeassistant.helpers.llm` stub moved
from a per-file guard into conftest — agent.py only loaded if a file that
happened to stub it ran first, and the loader caches half-executed
modules (the exact order-dependence the standing test lessons warn about).

## [6.47.0] — camera_overrides: let the restream do the work
The durable fix for Nest's expiring streams and placeholder snapshots
isn't more heuristics — it's not using Google's transport for frames at
all. The community-standard architecture is a go2rtc restream (Frigate
bundles one): go2rtc's native `nest:` source speaks SDM directly, handles
the 5-minute stream extension, and republishes solid RTSP that HA and
JARVIS consume like any local camera.

JARVIS now meets that halfway with one runtime key:

    camera_overrides: { "camera.eliana_s_camera": "camera.eliana_restream" }

The original entity keeps its identity everywhere — chips, names, doorbell
events, Nest event metadata — while every FRAME transparently comes from
the twin: the panel tile (stream URL, token, stills), the JARVIS snapshot
tier, the package monitor, vision analysis, all via one server-side
`resolve_camera_source()` mirrored client-side. The cam strip shows the
mapping (`SRC eliana_s_camera → eliana_restream`), DIAG probes and labels
the actual source, and a missing/typo'd target safely falls back to the
original. 3 new smoke checks, 3 new unit tests.

## [6.46.3] — the black-frame case, cracked by DIAG
First live DIAG run told the whole story in three lines: `nest×2` (the
integration is fine), `state=streaming`, and "snapshot: **OK 2KB (13ms)**"
— declared a success. A real camera frame is tens of KB and takes hundreds
of ms; a 2KB instant response is a placeholder thumbnail. Meanwhile the
tile sat in MJPEG mode because the stream *decodes* — a steady all-black
feed — so `naturalWidth > 0` stood the watchdog down. Every tier reported
victory while delivering garbage. Fixes on both ends:

**Server**: a first-pass snapshot under 12KB (`SMALL_SUSPECT_SIZE`) is now
treated as a placeholder even when it isn't literally black — the stream
gets woken and re-shot for a real frame, with the tiny one kept only as a
last resort. The DIAG probe reports luminance stats (`lum μ σ W×H`) on
every frame it sees and calls out SUSPECT sizes instead of declaring
victory, so the next screenshot self-diagnoses.

**Panel**: the watchdog and the load-listener are now content-aware — a
decoded frame only proves a tier works if it isn't near-black (mean
luminance sampled via a 32×32 canvas; unverifiable frames get the benefit
of the doubt). A black MJPEG stream now escalates exactly like a dead one.
DIAG gains a **TILE** line reporting the client half of the story: render
mode, decoded dimensions, and the current frame's luminance — including an
explicit "BLACK STREAM (decodes fine, shows nothing)" verdict.

## [6.46.2] — stop guessing: camera diagnostics
Three versions of fixing blank Nest tiles blind is enough. The Camera
Watch head gains a **DIAG** button that probes the active camera
end-to-end — the exact tiers `_get_best_image` walks, instrumented:
backend match and fetch (with *why* it was empty), standard snapshot
(including the blank-frame check), and the stream-wake retry, each with
its result and the whole thing timed. The verdict is actionable — a Nest
camera failing every tier gets told about event media and Pub/Sub
subscriptions, not just "no frame" — and is also written to the Logs tab.

The probe response always includes a platform histogram of every camera
entity HA has, which answers the question underneath all of this in one
glance: **if `nest×N` isn't in that list, the Google Nest integration
isn't delivering camera entities to HA at all**, and no amount of
JARVIS-side code can render what HA doesn't have.

New: `jarvis/camera_diagnostics` WS command, `camera.probe_camera()`
(kept tier-for-tier in sync with `_get_best_image`), 6 unit tests, 5
panel smoke checks.

## [6.46.1] — the fallback chain learns about hangs
6.46.0's camera escalation was driven entirely by `<img>` error events —
and the most common Nest failure mode fires none. HA's proxy endpoints
often HANG for a WebRTC camera (HTTP 200, connection held open, zero
frames ever sent) while it tries to start a stream that will never
produce one. No error event → no escalation → tile still blank.

A no-frame watchdog now backs up the error path: if no decoded pixels
arrive within the window (6s stream / 5s stills — checked via
`naturalWidth`), the tier escalates exactly as an error would have. A
frame arriving stands the watchdog down.

Also fixed a self-inflicted diagnosis gap: the JARVIS snapshot tier
swallowed WS errors silently. If the command doesn't exist — the classic
case being HA not restarted after updating — the tile now says
"restart Home Assistant" instead of showing nothing. Other WS errors
render their message. Server-side, empty or failed snapshot fetches now
write a CAMERA line to the activity log (throttled to one per entity per
5 min) so the Logs tab answers "why is there no frame" directly.

## [6.46.0] — Nest cameras visible, phantom packages gone
Two long-standing camera complaints, both traced to real bugs.

**Nest tiles were permanently blank.** The panel's fallback was
stream → stills, but WebRTC-only Nest cameras fail *both* — no MJPEG
stream exists, and an idle WebRTC camera can't produce stills through
`/api/camera_proxy` — leaving the tile in a silent error loop. The chain
now escalates a third time to a new `jarvis/camera_snapshot` WS command
that pulls frames through JARVIS's own backend registry (Nest event media,
stream-wake), polling gently at 6s. A camera that resolves to this tier is
remembered, so re-renders jump straight there instead of blank-flashing
through two 404s. If even JARVIS can't get a frame, the tile now *says so*
with a pointer at the Nest integration rather than showing nothing.

**"A package has been delivered" — when none was.** Three compounding
causes, all fixed:
  • The doorbell-press path matched keywords with no negation handling —
    an analysis reading "person at the door, **no package** visible"
    literally contains "package" and announced a delivery. Negated spans
    (including "no packages or mail" chains) are now stripped first.
  • Backend-sourced frames (Nest event media, Frigate snapshots) skipped
    the blank-frame check that guards the standard snapshot path — a black
    wake-up frame fed to a vision model is a hallucination machine. Blank
    frames are now dropped before classification.
  • A single frame could announce an arrival. A NEW positive now triggers
    one immediate re-capture + re-classify, and only two independent
    frames agreeing announce — one extra vision call, only when an
    announcement is on the line. Pickups still register from one frame.

Also: README gains a proper "Nest cameras (prerequisite)" section — the
Google Device Access / SDM / Application Credentials setup lives on the
official Nest integration, which JARVIS consumes; that's the only path
Google's licensing allows, now documented instead of tribal knowledge.

## [6.45.2] — hassfest gets its way
The 6.45.1 push tripped hassfest on four counts, all now fixed:

  • `assist_pipeline` (used by the voice bootstrap's pipeline creation) and
    `recorder` (used by the sparkline history fetch since 6.43) are now
    declared in `after_dependencies` — both are opportunistic uses that
    hassfest rightly wants on the record.
  • The `llm_base_url` field description in `strings.json` and
    `translations/en.json` contained a literal example URL, which the
    translations validator forbids. Reworded to convey the same Ollama
    default (host/port/path) without a URL.

Also reordered manifest.json keys to hassfest's canonical form (domain,
name, then alphabetical) — currently only a preference, but the Cove
project got bitten by it once and it costs nothing to be ahead of it.

## [6.45.1] — JARVIS gets its face back
The integration now ships its brand icon at
`custom_components/jarvis/brand/` (icon.png 256×256 + icon@2x.png 512×512,
web-optimized). Since Home Assistant 2026.3, custom integrations serve
brand images directly from this folder through the local brands proxy —
taking priority over the CDN, no home-assistant/brands submission needed.
This is also now HACS's required form for brand assets, so it checks a
default-store submission box at the same time. Users on HA older than
2026.3 still see no icon until/unless a brands-repo PR is made; that's
optional now, not blocking.

## [6.45.0] — the add-on era is officially over
The last roadmap item from the great cutover: removing the machinery that
existed to bridge the old add-on and the integration. None of it had a
living counterpart anymore — the add-on that wrote `jarvis_config.json` is
gone, its orchestration long since re-homed into the in-process voice
bootstrap.

Removed:
  • The ~95-line "addon-owned keys" reconcile block that ran on every
    setup, hashing a config file nothing writes. Worse than dead weight: a
    stale leftover file could have shadowed Configure-dialog choices after
    any future change to the key list.
  • The `jarvis_config.json` import triggers (`async_setup`,
    `async_setup_post_start`) — the latter had no callers at all.
  • The v5.8.03 old-path migration in `jarvis_config.py`.
  • The legacy path from the config flow's auto-import.

Kept, deliberately: the auto-import itself. `/config/jarvis/config.json`
is the panel's runtime store and survives integration removal, so deleting
and re-adding JARVIS picks all your settings back up with zero re-entry —
that was never an add-on feature, just a good one.

JARVIS is now cleanly config-entry-only: users who still have `jarvis:` in
configuration.yaml get a proper warning, the conversation agent registers
through the platform as it always did, and setup has exactly one path.
Also updated the README's voice-setup note, which still described the
in-process bootstrap as a future plan.

## [6.44.0] — activity feed search (and the feed is actually live now)
The Command Center's Activity Feed gets the same treatment the Logs tab got
in 6.43: a search box that filters events by message or tag as you type,
with a "1 of 30" count and a clear empty state.

Wiring it exposed a quiet bug worth its own line: the Activity Feed never
updated in place. The 5s/real-time refresh patched status rows, the
dominant room, and area tiles — but not the feed, which only redrew on a
full structural re-render (an area being added or removed). In practice the
feed silently went stale the moment you opened the dashboard. It now
rebuilds its rows on every refresh, respecting whatever search is active,
without stealing focus from the search box.

## [6.43.0] — UI Phase 3: real-time, sparklines, entity cards, log search
Four things, in build order.

**Real-time entity subscriptions.** The dashboard polled every 5s flat. It
now subscribes to HA's native `state_changed` events (the same pattern
Camera Watch already used for its own events) and refreshes within ~2s of
anything actually changing — throttled so a burst of activity coalesces
into one refresh, not one per entity. The 5s poll is now a 20s safety net,
since real-time now covers the common case.

**Area tile sparklines.** Every area tile with a temperature or humidity
sensor now shows a compact trend line, not just the instant reading. This
needed a new data path: `state_changes` (patterns.db) deliberately excludes
sensor/binary_sensor domains as noise for pattern learning — exactly the
domains a sparkline needs — so this is the integration's first use of HA's
`recorder` history API, polled separately and slowly (5 min) since history
queries are heavier than the rest of the panel payload. This is the one
piece I couldn't exercise against a live recorder from here — worth a close
look on first deploy.

**Entity cards.** Click an area tile → a drill-down detail card: full-size
temp/humidity readouts with their sparklines, lights with the same toggle
as the tile, last motion, capabilities. Area tiles picked up `temp`/
`humidity` for the first time too — previously only the dominant room ever
got that data.

**Log search.** A text box next to the category filters, debounced,
filtering message and category text together with whatever category's
selected. A count line ("12 of 340") and a real empty-state message when a
search or filter matches nothing, instead of a blank pane.
Two things v6.40 and v6.41 built now have somewhere to show up.

**Goals card** (Command Center): every active goal, with its step progress,
next-check or deadline countdown, and a cancel button — plus recently
finished ones for a moment of "oh, it got that done." This existed in the
backend since the goal planner shipped with no way to see it short of asking
JARVIS directly.

**Person Routines** (Memory tab): the per-person habits JARVIS has learned
with enough confidence to attribute to one person by name, grouped and
confidence-scored, sitting next to the household-wide facts it already showed.

Also fixed along the way: the Suggestions card — live since the pattern
engine shipped — was silently reading `undefined` for its data the whole
time. `_data()` normalizes the raw panel payload into a fixed shape and never
carried the `suggestions` field through, so the card only ever rendered
empty. Both suggestions and the new goals data go through the same fix.
Since v6.29, every voice command has been tagged with the resolved person —
but that signal went nowhere. The pattern analyzer only ever learned
household-wide habits, and a `person_patterns` table has sat in the schema
since the goal planner shipped, unused.

State changes now carry a person too — stamped cheaply, sole-occupant only,
by the same listener that logs them for pattern learning (the full face/voice
resolver is too costly to run on every light flip; that's reserved for the
much lower-volume conversation path). When one person accounts for the clear
majority of an entity's routine, or a repeated command, JARVIS now says so:
"turns on around 7:00 most days when Sam is home" instead of a blanket
household statement — and attributes the learned fact to *that person's*
knowledge subject, not the household's. Mixed or ambiguous patterns behave
exactly as before.

`person_patterns` finally has a writer: person-owned routines land there,
independent of the household suggestions/automations flow, ready for a
per-person Routines card whenever UI work resumes.

This is data-layer only — no new UI this round. Next up: surfacing it.
JARVIS now has a goal planner: hand it an *outcome* and it will keep working
toward it — across minutes, hours, or days — until it's achieved, impossible, or
you call it off.

  "Get the house ready for guests by Saturday afternoon."
  "Warm the living room to 72 and let me know when it's actually there."
  "Keep an eye on the basement humidity today and run the dehumidifier if it climbs."

When you ask for something like that, JARVIS breaks it into concrete steps and
opens a goal. From then on it re-engages on its own cadence with its full
toolset — checking states, acting (every action still self-verifies), marking
steps off, and deciding when to check back next. It works **quietly**: progress
lands in the activity log, not your ears. You hear from it when the goal
*finishes* — done or failed — with a plain-spoken result, through the normal
announcement routing (so quiet hours still apply). Ask "what are you working
on?" anytime for status, or tell it to drop one.

Deadlines are honored honestly: if time runs out, JARVIS wraps up what it can
and closes the goal rather than pretending. And it can't run away with itself —
active goals are capped, each goal has an engagement budget, and a hiccup
mid-run (say the LLM being briefly unreachable) just means it tries again at
the next check instead of giving up.

This completes the agency ladder: `execute_plan` does many things *now*,
follow-ups handle one thing *later*, and goals pursue an *outcome* until it's
real.

## [6.39.0] — JARVIS knows outside from inside
An audit of how JARVIS classifies the outside world found the biggest remaining
sources of intrusion false alarms — and one filter from the original design that
had been sketched but never actually connected. All fixed:

  • **A delivery driver can no longer "confirm" a break-in.** Person detections
    from OUTDOOR cameras (driveway, doorbell, backyard) no longer count as proof
    someone is inside the house. Only an indoor camera seeing a person confirms
    an intrusion; the courier at your door stays a doorstep event.
  • **Outdoor motion can't start an intrusion investigation.** Previously only a
    handful of hard-coded names ("backyard", "porch"…) were recognized as
    outdoor — a sensor called *patio*, *deck*, *shed*, *garden*, *pool*, or
    *doorbell* was treated as motion **inside your house**. JARVIS now uses a
    proper classifier: your Home Assistant areas, a much richer set of outdoor
    names, and — decisively — your own say-so.
  • **An open yard gate isn't an open house.** Property-perimeter openings (a
    driveway or side gate) no longer corroborate a break-in the way an open
    window does. The garage still counts — it's part of the house.
  • **You get the final word.** Three new settings — `outdoor_areas`,
    `outdoor_entities`, and `indoor_entities` (globs; indoor wins) — let you
    force-classify anything the auto-detection gets wrong, no renaming required.

The notable-event policy (a person, package, mail, or damage outdoors is worth
telling you about; wind, passing cars, and animals are not) is now wired into
the same classifier, ready for the vision layer to consult.

## [6.38.0] — JARVIS closes its own loops
Two upgrades that make JARVIS genuinely agentic — acting across time and
confirming its own work — instead of only reacting turn by turn:

**It schedules its own follow-ups.** JARVIS can now queue work for its future
self and run it autonomously: "close the garage" can become *close it, then
check in five minutes that it actually shut*; adjusting the thermostat can come
with *confirm the room reached temperature in half an hour*; "remind me the
oven's on in 45 minutes" just works. When a follow-up comes due, JARVIS runs it
with its full toolset — checking states, acting if needed — and reports the
outcome out loud through the normal announcement channel (so quiet hours still
apply). Ask "what do you have queued?" to review or cancel them.

**It verifies what it was asked to do.** Every deterministic action — on/off,
lock/unlock, open/close — is now checked a few seconds later. If the device
didn't reach the target, JARVIS retries once; if it *still* didn't, that shows
up honestly in the activity log ("the garage door did not respond to close even
after a retry — it may be jammed, obstructed, or offline") instead of the
command silently going nowhere. Successes stay silent; only trouble surfaces.

Both build on everything JARVIS already learned this cycle: follow-ups announce
through the same routing as its other proactive speech, failures land in the
same activity log the root-cause analyzer reads, and the graduated-trust
autonomy model keeps the user in charge of what runs silently.

## [6.37.0] — ask JARVIS *why* something happened
JARVIS can now perform root cause analysis. Ask it things like "why did the
kitchen lights turn off?", "what caused the heat to kick on at 3am?", or "who
unlocked the front door?" — and instead of just reporting the state, it
investigates: it pulls its own state history, recent voice/text commands, and
its own actions from around the event, builds a timeline, and ranks the likely
causes with confidence:

  • a **recorded trigger** — when the change was captured with its cause attached
  • an **upstream failure** — a related device or hub going unavailable moments
    before (the classic "everything on that hub dropped" cascade)
  • a **person's request** — someone asked for it by voice or text, and who
  • a **JARVIS action** — it did it itself (lockdown, a routine, an announcement)
  • a **recurring schedule** — the same change happens at this hour most days,
    pointing at a Home Assistant automation
  • **related room activity** — something else changed in the same room just
    before

When the evidence is thin it says so honestly rather than inventing a story.
Everything runs locally over history JARVIS already keeps — no cloud calls to
analyze — and the answer comes back as a spoken-style explanation with the
timeline behind it. It's also available to the dashboard for an entity-by-entity
"why" view.

## [6.36.2] — the Memory forget button works now
Removing a memory with the ✕ on the Memory tab did nothing. The panel was sending
the fact's id in a field named `id`, but Home Assistant's WebSocket layer reserves
`id` for its own message numbering and overwrites it — so the request arrived
asking to forget the wrong thing, and nothing was deleted. The id now travels in
its own field, so ✕ removes the memory as expected (and the list updates
immediately). Added a guard so no future panel action can trip over the same
reserved field.

## [6.36.1] — spoken replies fall back to your real speakers
Following on from 6.36.0: if your voice satellite can't play audio itself — a
mic-only board (Waveshare with the speaker DAC off), or one in a room with no
real speaker — a spoken reply had nowhere to go and was silently lost, even
though proactive announcements (the briefing) played fine on your chosen
speakers. Now, when a reply would land on a satellite that can't speak, JARVIS
routes it to the same reply/broadcast speakers the briefing already uses. So if
the briefing is audible, replies will be too.

(You can still pin a specific speaker per satellite under Settings → satellite
pairings for room-accurate replies; the fallback only kicks in when there's no
usable speaker otherwise.)

## [6.36.0] — voice replies come back reliably
If JARVIS answered typed questions but went silent over voice, this is the fix.
Two of JARVIS's own reply-routing safeguards could swallow a spoken reply while
leaving text untouched (text never goes through them):

  • **Room-presence gating is now off by default.** JARVIS used to check the
    satellite's room for occupancy and stay silent there if a sensor said the
    room was empty — meant to keep only the right room answering. But if the
    room's mmWave/occupancy sensor hadn't registered you yet (or was flaky), it
    silenced the very satellite you were talking to. The multi-satellite dedup
    already prevents several speakers answering at once, so this gate is now
    opt-in (`presence_gate: true`) for homes with rock-solid per-room presence.
  • **A dead reply speaker no longer eats the reply.** When you have a reply/Cast
    speaker configured, JARVIS silences the satellite and speaks through that
    speaker instead — but it was doing so even when the speaker was offline,
    losing the reply entirely. Now it only hands off to a reply speaker that's
    actually reachable; otherwise the satellite speaks.

Net effect: the satellite you spoke to answers, unless you've deliberately set up
room-targeted or Cast-speaker replies and those are healthy.

## [6.35.0] — intrusion checks start at the door and follow the route
JARVIS now reasons about *where* activity is before crying wolf. When motion
happens while no one's home, it anchors the search at the **point of entry** —
the room with the open window or door — and only concludes there's an intruder
when activity forms a plausible route from there: motion at the breach, then into
the room next to it, and onward, the way a person actually moving through a house
looks. A camera spotting a person still confirms immediately.

Crucially, motion that has *nothing* to do with the open entry — a blip in a far
room while the open window is elsewhere, with no activity near it — no longer
trips a full intrusion alert. JARVIS keeps watching it (as you asked — it still
investigates activity anywhere), but it won't conclude an intrusion from
unrelated motion. That's what eliminates the occasional false alarm.

To follow the route it uses your **Residence floor plan** to know which rooms are
next to which. If a breach room has no motion sensor, or the layout isn't mapped,
it falls back to requiring sustained movement through several rooms rather than a
momentary two-sensor blip. Either way the bar for "intrusion" is higher and
better-reasoned.

The investigation now also reports the breach point and the route it's tracking,
so the Residence view can show where an intruder is and where they've been.

## [6.34.0] — JARVIS knows your voice
JARVIS can now tell who it's talking to by **voice**, and learn people's voices
over time from ordinary conversation — the strongest signal yet for its
per-person features.

It works by consuming a dedicated speaker-recognition service rather than running
a voice model itself (that keeps Home Assistant light and your GPU free for the
LLM). Point it at a service like **VoiceBM** or **speaker-recognition** — anything
that publishes "who's speaking" to Home Assistant as an entity — and JARVIS folds
voice into its existing identity picture alongside who's home and who's on camera.
When the voice is certain it's used; when it isn't, JARVIS falls back gracefully.

**Learning over time is hands-free.** The service does the enrolling, but JARVIS
supplies the missing piece — the *name*. When it already knows who's speaking
(you're the only one home, or a camera just recognized your face) but the voice
service hasn't learned that voice yet, JARVIS flags it so the sample can be
enrolled under the right person automatically. Voice profiles build themselves
from normal conversation, no sit-down training session.

Set it up in Configure → Identity: enable the voice tier and give it your
service's speaker entity (e.g. `binary_sensor.*_voice`). A full setup guide,
including the auto-enrollment automation, ships alongside this release.

## [6.33.0] — one alert, then JARVIS investigates and escalates
Motion while no one's home no longer turns into a stream of repeat alerts. Now
JARVIS alerts **once** and then investigates quietly:

  • A window or door left open on purpose is still a valid way in, so motion near
    it gets the one alert — JARVIS doesn't ignore it, and doesn't nag about it.
  • After that single alert it watches silently, tracking whether the motion
    stays put (a pet, a blind, one sensor) or **spreads through the house** the
    way a person moving room to room would — and it also watches your cameras for
    a person. It keeps investigating for as long as it takes to decide.
  • If it's **nothing** — motion settles, stays in one spot — it quietly stands
    down. No second alert.
  • If it **confirms an intrusion** — motion across multiple rooms, or a person
    on camera — it escalates hard: it announces out loud to the whole house
    **regardless of the time of day**, and pushes to **every device** connected
    to your home, not just one phone. A persistent notification is left too.

If residents come home mid-investigation, JARVIS stands down on its own.

You can tune it: `intrusion_spread_zones` (how many rooms of motion means
"someone's moving through", default 2), or turn the whole corroboration
requirement off with `intrusion_require_corroboration: false`.

## [6.32.0] — doors show open, quieter motion alerts, tuned for Ollama
Three things:

**Doors now actually show open on the Residence tab.** The house model was
never receiving live door state — the panel was quietly dropping it before it
reached the 3D view, so garage doors (and every other door) always drew closed
no matter what. Fixed at the source; open doors now render open, and combined
with the door-mapping added earlier you can make them match your home exactly.

**Motion alerts only fire when something's actually wrong.** When no one's home,
plain motion — a pet, a robot vacuum, blinds moving in the airflow, sun on a
sensor — no longer sets off an intrusion alert. JARVIS now only alerts on motion
while away when it's corroborated: the alarm is armed, or a door/window is open
(a real entry). If you'd rather be alerted on any motion, set
`intrusion_require_corroboration: false`.

**Optimized for a local Ollama server.** If you point JARVIS at Ollama, it now
keeps the model loaded between requests (no reload lag), uses a much larger
context window than Ollama's small default (so long prompts aren't silently
truncated), and allows a generous timeout for cold-start model loads. Point it
at your Ollama endpoint and it'll run local without the first-token stalls.

## [6.31.0] — lockdown closes what it can, and stops repeating itself
Two things, both from real use:

**It stops nagging.** Lockdown was re-announcing "lockdown engaged" on every
restart and reload — so during a day of tinkering you'd get the same alert over
and over. Now an already-armed alarm is adopted silently on startup; the
announcement only fires when the alarm actually arms (or you engage it yourself).
And anything it can't secure is mentioned once, never on a loop.

**It actually secures what it can.** On engage, lockdown now:
  • locks every motorized lock that's unlocked;
  • **closes motorized openings** — garage doors and powered covers. These have
    safety sensors, so if something's in the way the close just fails (and you're
    told it couldn't close), rather than forcing shut on a car or person;
  • for openings it can't close remotely — a plain window contact with no motor —
    it alerts you once so you can close it by hand, then treats it as
    intentionally open and leaves it alone.

So a typical engage now reads like "Sir, lockdown engaged — I locked the front
door and closed the Garage Door, but Sam's Window 1 is open and I can't secure it
remotely — you'll want to close it," and you hear it once, not every few minutes.

## [6.30.1] — lockdown tells you what's actually open
The lockdown announcement could come out nonsensical — "everything already
locked. 1 opening already open will be left as-is" — which made it sound like
JARVIS did nothing and was shrugging off the one door that was actually open.
During a lockdown an open door is the thing that matters, so the message now
names it and tells you to deal with it: e.g. "Sir, lockdown engaged. Everything
was already locked, but the Garage Door is open and I can't secure it remotely —
you'll want to close it." When nothing needs locking and nothing is open, it
simply says the home was already fully secured, instead of announcing a non-event.

## [6.30.0] — the Residence doors reflect reality now
The 3D house shows your doors open and closed live, but it had to *guess* which
of your entities was the garage, the front door, the cellar, and so on — purely
from their names. If your garage door's entity didn't happen to contain the word
"garage", or was exposed without a device class (common), it never showed as
open. That guessing is why the door states felt unreliable.

Two fixes:

  • **You can now map doors explicitly.** A new section on the Residence tab lets
    you point each door slot — Front, Garage, Garage Side/Rear, Kitchen↔Garage,
    Cellar, Basement — at the exact entity in your home (a cover, a door/contact
    sensor, or a lock). Mapped doors are read directly, with no guessing, so they
    always match. Leave a slot blank to keep auto-detection.
  • **Auto-detection is smarter.** Garage doors exposed as a cover with no device
    class are now recognized by name, while window coverings (shades, blinds) are
    excluded so they're never mistaken for doors.

So your garage door — and the rest — will track correctly: map it once and it's
certain, or rely on the improved auto-detection.

## [6.29.2] — the Residence tab saves your settings now
Changing anything on the **Residence** tab — home style, number of floors,
whether there's a basement, dormers, garage bays, chimney side, square footage,
bed/bath counts — was silently failing with an error, because the backend was
rejecting those settings as "not writable from the panel." Only the room layout
and background-image editor actually saved. Every Residence control now persists
correctly, so you can describe your home and have the 3D model match it.

## [6.29.1] — the Configure dialog actually configures now
If you opened **Settings → Devices & Services → JARVIS → Configure** and got a
step that showed a heading but no fields — just a Submit button — that's fixed.
The Configure dialog is a proper four-step setup (Core, Routing, Observer,
Identity) with real controls, pre-filled with your current values:

  • **Core** — what JARVIS calls you, its directive/personality preset (or a
    custom directive), the conversation model, and whether it can control the home.
  • **Routing** — your bedroom areas, a broadcast speaker group, and a phone
    notify service.
  • **Observer** — turn proactive awareness on, with its Gemini vision key, the
    model tiers, and quiet hours.
  • **Identity** — per-person recognition: on/off, the confidence threshold, and
    the voice-fingerprint tier (the one that needs a GPU).

This is in addition to the in-app JARVIS panel, which still holds the full set of
settings. (The empty dialog was leftover skeleton steps from an earlier build;
the fields had never been wired in.)

## [6.29.0] — JARVIS knows who it's talking to
Until now JARVIS treated everyone the same — it remembered facts and learned
routines, but couldn't tell who was speaking. It can now figure out *who* it's
talking to and tailor itself to that person: your preferences surface for you,
your spoken "remember that I…" is filed under you (not shared), and the routines
it learns get attributed to the right person instead of a generic "someone."
One resident's private facts no longer leak into another's conversations.

**It works without any special hardware.** JARVIS figures out who you are from
signals your home already has, in tiers:

  • **Who's home** — if you're the only person home, that's almost certainly who
    it's talking to. (Just Home Assistant person tracking — nothing to set up.)
  • **Recent face** — if a camera recognized someone moments ago, that's a strong
    clue. (Uses your existing Frigate/DoubleTake setup, which runs on the camera
    side — no GPU on your Home Assistant box.)
  • **Voice** *(optional, needs a GPU)* — recognizing people by their voice is the
    most direct signal, but it needs local AI horsepower, so it's **off by
    default**. When your GPU server is online you can switch it on; until then,
    the two tiers above give a non-power-user a fully working setup with zero
    configuration.

JARVIS only commits to a person when it's reasonably sure — when the signals are
ambiguous (say, two people home and no camera match), it stays neutral rather
than guessing wrong. The whole feature can be turned off, and the confidence
threshold tuned, in config.

## [6.28.0] — zero-touch voice setup is back
The convenience the old add-on gave you — automatically installing the voice
stack and setting up JARVIS's voice — now lives inside the integration, so the
HACS install gets it too. After you add JARVIS, on Home Assistant OS / Supervised
it quietly does the legwork in the background: installs the Piper, Whisper, and
openWakeWord add-ons if they're missing, downloads the JARVIS voice, restarts
Piper to pick it up, reconnects Wyoming, and builds an Assist pipeline with
JARVIS as the conversation agent. You don't have to touch any of it.

It's careful about it: the setup runs once per version (not on every restart),
never re-installs things you already have, and if any step can't complete it
just tells you the one manual step to finish in Settings → Voice Assistants
rather than failing. On Home Assistant Container/Core (no Supervisor) it cleanly
does nothing — there are no add-ons to install there — and you set up voice the
normal way. Power users can turn the whole thing off with `auto_bootstrap: false`.

With this, the move to a HACS integration is complete: install JARVIS and
everything — conversation, vision, the cognitive core, memory, the dashboard,
and now voice — comes up on its own.

## [6.27.0] — JARVIS is now a HACS integration
JARVIS installs through **HACS** now, as an ordinary Home Assistant integration —
no separate add-on. It runs entirely inside Home Assistant, so there's no extra
container to manage, and updates come through HACS like any other integration.

To install: add this repository to HACS as a custom **Integration**, install
"JARVIS AI Assistant," restart, then add it under Settings → Devices & Services
and enter your API key (or a local LLM URL). Everything else is still configured
from the JARVIS panel.

If you were running the old add-on: your data is safe. Everything under
`/config/jarvis/` — learned patterns, the new knowledge store, your persona, and
your settings — stays on disk, and JARVIS automatically imports your existing
configuration on first start, so nothing is re-entered.

One thing is still in flight: the add-on used to auto-install the voice stack
(Piper, Whisper, openWakeWord), download the JARVIS voice, and build the Assist
pipeline for you. That convenience is being re-homed into the integration. Until
it lands, set the voice pipeline up once via Settings → Voice Assistants with
JARVIS as the conversation agent. Everything else — conversation, vision, the
cognitive core, the dashboard, memory — works immediately on install.

## [6.26.0] — JARVIS learns your routines on its own
The pattern engine that watches how you use the house now does two new things
with what it sees.

First, the strongest routines it spots become things JARVIS simply *knows* —
they show up in the Memory tab on their own, marked with a "~" so you can tell
what it figured out by watching versus what you told it directly. So after a
week or two you might open Memory and find "porch light turns on → around 18:00
most days," or "asks 'goodnight' → usually around 23:00," with no effort on your
part. Anything you've stated yourself always wins and won't be overwritten by a
guess, and you can forget any of these with the ✕ like any other fact.

Second, a fix: JARVIS can now actually notice when one thing reliably follows
another — "the kitchen light comes on right after the hallway light" — and offer
to automate it. That detection had been silently failing; it works now, so the
"shall I automate this?" suggestions will be richer.

As before, suggested automations still wait for your yes/no — nothing is created
behind your back.

## [6.25.0] — JARVIS remembers
JARVIS can now hold on to durable facts and preferences — the kind of thing a
real butler just knows about your household — and bring them up naturally in
conversation. Tell it "remember that trash is Tuesday," or "remember I run cold
at night," and it keeps that. Ask later and it answers from what it knows; it
also quietly factors these in whenever it talks to you.

There's a new **Memory** tab to see and curate everything JARVIS knows:

  • Each fact is listed plainly — "trash day → Tuesday" — grouped into things
    about the household and things about you.
  • Teach it something on the spot with the box at the top, no voice needed.
  • Forget anything with the ✕ — this is your control over what it retains.
  • Facts it picked up by observation rather than being told are marked with a
    small "~", so you can see at a glance what it's sure of versus inferring.
  • Facts can be made to expire on their own — handy for the ephemeral ("the
    sitter comes at 3 today") so they don't linger as stale knowledge.

This sits alongside the conversation memory JARVIS already had (which recalls the
gist of past chats); the new layer is curated knowledge you can read and edit
directly, and it's the foundation the per-person and goal-planning features to
come will build on.

## [6.24.3] — Lockdown holds, and handles open doors the way you'd expect
Lockdown now stays put. The earlier "it flips on then flips back" was the header
not being told the real lockdown state on its regular refresh — it is now, so the
switch reflects exactly what the house is doing and holds there.

Lockdown is also smarter about doors and windows. Anything already open when you
engage is treated as deliberate and left alone — no fighting you over a window you
opened on purpose. From then on it watches for things that were shut and then open:

  • if it's something JARVIS can close or lock (a smart garage door, a smart lock),
    it secures it — and if that doesn't actually take, it alerts you;
  • if it's something JARVIS can't operate (a plain open/closed sensor with no
    motor or lock behind it), it assumes you meant to open it and leaves it be;
  • if JARVIS closes something and you open it right back, it takes the hint, leaves
    it open, and tells you once.

Worth knowing: because un-closeable openings are now assumed intentional, a window
JARVIS can't physically close that opens mid-lockdown is left alone rather than
alerted — your call, as requested. Auto-arm-with-the-alarm and surviving reboots
from the last update are unchanged.


## [6.24.2] — Lockdown that actually engages (and follows your alarm)
Fixed the lockdown toggle for good and made it dependable. It now engages every time you flip
it, the header switch reflects it immediately, and lockdown follows your alarm on its own — it
arms whenever any alarm panel is armed and lifts when you disarm, and it holds that state across
reboots and updates. (Manually flipping it off while armed still wins until you next disarm.)

Why it was stuck: lockdown used to be set up deep inside the optional observer's startup, so any
hiccup there left it silently switched off — which is exactly why the toggle did nothing while
everything else worked. It's now its own always-on security feature, created on demand if needed,
watching your alarm directly so arming/disarming takes effect the instant it happens instead of
waiting on a background cycle. The add-on log also spells out each lockdown action now, so if
anything misbehaves it's clear what happened.


## [6.24.1] — Basement door
Added the basement door at the foot of the cellar stairs. It lines up directly under the
cellar bulkhead and appears on the basement floor view, opening and closing in step with its
door sensor like every other door.

## [6.24.0] — Your doors, live on the model
Your doors now appear on the 3D home and light up the moment they open. JARVIS watches your
door and garage-door sensors and shows each one's state on the model — an open door glows amber
and swings ajar, a closed one sits flush in cyan, refreshing within a few seconds.

The home you see is a Cape Cod — the developer's own house, included as a worked example you'd
reshape into your own (see Settings → Residence / Home). It models a front entry, three garage
bays, a garage rear/man-door, a cellar bulkhead under the kitchen window, and an interior
kitchen↔garage door. Exterior doors show on the main view; interior doors show on the matching
floor view.

JARVIS matches your sensors to these doors by name — e.g. a sensor with "cellar" or "bulkhead"
lands on the cellar, "garage" + "side"/"man" on the garage man-door, "front" on the entry. If a
door never lights up, its sensor name didn't line up with one of those doors.

## [6.23.2] — Lockdown shows its real state · smoother phone rotation
Lockdown now reflects what's actually happening. Engage it — or have your alarm arm and trigger
it automatically — and the header switch and status both flip to ARMED and stay there. And on a
phone, spinning the 3D home no longer drags the page with it: a sideways swipe rotates the
model, an up/down swipe scrolls the page.

## [6.23.1] — Lockdown is a real switch
The lockdown control in the header is now an unmistakable on/off switch instead of a vague
banner. On, it slides over and glows red ("ARMED"); off, it sits grey. One glance tells you
whether the house is locked down.

## [6.23.0] — Make the model your home
The residence model is no longer fixed to one house. From Settings → Residence / Home you can
set it up for your own place: choose a home type (Cape Cod, Colonial, Ranch, Two-Story,
Craftsman, Modern, Townhouse, Apartment, or Cabin) and your specs — garage bays, dormers,
chimney side, basement, bedrooms, bathrooms, square footage, and address — and the 3D model and
the property readout update to match. Out of the box it's a Cape Cod (the developer's own home)
as a starting point; change the type and specs to make it yours. The floor tabs follow along,
too — single-story homes drop the 2nd-floor tab, basement-less homes drop the basement.

## [6.22.0] — JARVIS on your phone
The whole panel now works on a phone, not just a desktop or tablet. The tab bar scrolls instead
of running off the edge, the 3D home shrinks to fit the screen, the header and controls stack,
and you can drag to spin the model without the page fighting you. Nothing changes on desktop.

## [6.21.0] — Rotatable 3D residence model (default)
The residence overview is now a real, drag-rotatable 3D model of the home, replacing the
fixed cabinet-projection drawing. It is a pure-geometry axonometric projection rendered to
SVG (no build step, no CDN), so the same code rotates in the browser and rasterizes for
release verification.
- **Drag to rotate** to any angle; the model re-projects live.
- **Floor isolation** (All / 1st / 2nd / Basement). The "All" view shows the exterior with
  presence as lit windows; each floor view drops the shell and shows that level's rooms as
  labeled translucent volumes with per-room occupancy (cyan occupied, green dominant).
- **Built to the real house** — dimensions from the architectural plan (63′×24′ footprint,
  garage 30×24, house 33×24, dormered ~400 sf second floor); room layout and labels from the
  floor-plan editor. Correct gable roof, two front dormers, the round-window rear dormer
  (upstairs bath), three-car garage, and the exterior chimney on the east gable.
- **Occupancy is data-driven** from live HA areas matched by name, so rooms light up as people
  move through the house.
- **Not hard-wired to one home:** the house spec (dimensions, room list, garage doors, dormers)
  is a single labeled default-config block at the top of the inlined `JARVIS3D` module — edit
  it for a different house. Address still comes from config.
- Retired the leader-line presence callouts (a rotating model can't anchor fixed leaders);
  presence now reads directly off the lit windows and labeled rooms. Smoke test updated to
  assert the rotatable model, the three garage doors, and floor-isolation labels.

## [6.20.3] — Real-home geometry: 3-car garage + corrected room windows
Calibrated the cabinet-projection house against the actual property photos.
- **Three garage doors.** The left wing now renders three evenly-spaced single doors
  (was two), matching the real garage. All three light together from the `cover.*garage*`
  state.
- **Front facade corrected.** The wide left window is now a single Living Room picture
  window (two sections, no longer a stray "Kitchen" pane); front door and Dining window
  to the right are unchanged.
- **Corner rooms on the right gable.** The two windows flanking the end chimney now map to
  the rooms that actually sit at that corner — Dining Room (front of the stack) and
  **Kitchen** (behind the stack, rear-right corner side window).
- **Projection note.** The Guest Bedroom (rear-left) and the upstairs Bath (the round
  rear-dormer window) face the two elevations this fixed front-right angle can't show, so
  they appear in the presence callouts rather than as lit windows. Keeping the front-right
  view is deliberate — it's the only angle that shows the garage doors.
- Smoke test extended to assert the garage renders exactly three doors (21 checks).

## [6.20.2] — Flanking-window rooms
The two windows either side of the end chimney now map to distinct rooms (Guest Room in
front of the stack, a bath window behind) instead of both showing the living room.

## [6.20.1] — Home corrections (chimney, garage doors, windows)
From marked-up feedback on the render:
- **Chimney** moved to the right gable end as a tall exterior stack (was floating mid-roof).
- **Garage doors** redrawn so they clearly read as doors — bolder frame, panel courses, and
  vertical seams.
- **Windows added flanking the end chimney** (living-room windows either side of the
  fireplace), plus the front-facade window set adjusted (Living / Kitchen / door / Dining).

## [6.20.0] — Residence is now a solid home, not a diagram
Replaced the isometric room-plate cutaway with a real, solid-massed house drawn in cabinet
projection — walls, a gabled roof with dormers, the attached garage, a chimney, a front
door. It reads as a *home*, and it is still a fixed SVG that cannot rotate or zoom.
- **Presence shows as lit windows.** Occupied rooms glow cyan, the dominant room glows
  green with a brighter halo, idle rooms stay dark — like a house at dusk with lights on
  where people are. Garage doors light when the garage is active; basement windows light
  for the basement.
- **Window-to-room map:** dormers = Master Bedroom / Eliana's Room; first-floor windows =
  Living Room / Kitchen / Guest Room; garage doors = Garage; base windows = Basement.
- **Floor tabs focus a level** by dimming the other floors' windows.
- Property banner, sq-ft / bed-bath / style / occupied stats, and the systems callouts
  stay as the HUD surround. Audit clean, smoke 20/20, 170 tests passing.

## [6.19.1] — Lock down the iso view
Confirmed and hardened that the residence drawing cannot rotate or zoom: the 3D
transform/drag/wheel methods are empty no-ops, no pointer listeners are attached, and the
SVG is a fixed viewBox with no transform. Also removed the leftover grab cursor so the
drawing no longer even looks draggable.

## [6.19.0] — Residence is now a 2D isometric cutaway (no more fragile 3D)
Replaced the CSS-3D house with a fixed 2D isometric SVG drawing. It renders identically
every time — there is no rotation or zoom, so nothing can collapse to flat lines or blow
up and scatter the way the 3D model kept doing. This is the isometric look from earlier in
the project, re-themed to the panel's cyan and wired to live data.
- **Always-correct cutaway.** Basement, first floor (garage with doors, kitchen, dining,
  living room, guest room, hallway), and the dormered second floor (master bed, Eliana's
  room) drawn as a clean Iron-Man-HUD isometric.
- **Live presence.** Occupied rooms light up; the dominant room is brightest with a
  pulsing node and a "◉ DOMINANT" tag; idle rooms stay dim — same data that drove the old
  view.
- **Floor tabs emphasise a level.** All / 1st / 2nd / Basement dim the other floors so you
  can focus one. The property banner, sq-ft / bed-bath / style stats, and the OCCUPIED
  count (now replacing the old ANGLE readout) sit over the drawing, with the systems
  callouts down the sides.
- Removed the 3D drag/zoom/angle controls and machinery entirely. Smoke test updated to
  assert the SVG renders, the rooms draw, and the occupied count wires up. Audit clean,
  170 tests passing.


6.18.0 restored the right renderer but presented it badly: the auto-fit zoom blew the
house up to its ceiling on the wide Residence tab, and the default tilt was too top-down,
so the massing looked exploded and scattered instead of compact like the approved view.
- **Tamed the zoom.** Auto-fit is now capped at 1.5× (was 2.4×) and targets a compact,
  focal object — the house no longer fills the tab and overlaps itself.
- **Near-front hero angle.** Default and Fit now sit at ~22° rotation / -18° tilt — a
  gentle near-front view (matching the angle the approved preview was shown at) where the
  gable roof and dormers read as a solid mass instead of a foreshortened aerial.
- Scroll-zoom, drag-rotate, the 45/135/225/315 presets, and Fit are unchanged; Fit returns
  you to the hero view. Audit clean, smoke 20/20, 170 tests passing.


The 3D residence now uses the solid-walled Cape Cod renderer that was approved earlier
in this project (the one with a real gable roof, dormers, and chimney) — not the flat
floor-plate version that had crept in and collapsed to lines at low view angles.
- **Real house, real roof.** Walls render as solid volumes; the roof is an actual gable
  (front/back slopes + ridge + gable ends) with three dormers and a chimney. Garage
  doors sit at ground level and read open/closed from any `cover.*garage*` entity.
- **Live + dominant aware.** Occupied rooms glow cyan, the dominant room brightest with a
  pulsing node, idle rooms dim — driven by real presence.
- **Style selector drives the roof.** Cape Cod / Colonial / etc. keep the gabled roof;
  Modern / Apartment switch to a flat parapet cap. Rooms stay the same underneath.
- **Angle presets + Fit.** New 45° / 135° / 225° / 315° buttons and a Fit reset on the
  Residence tab, so a stray drag to a flat angle is one click to recover (the flat view
  was why the house looked broken). Scroll still zooms; drag still rotates; the house
  auto-fits the full-width tab.
- Smoke test (20 checks) now asserts the solid house actually builds (30+ faces, not flat
  plates) and the angle presets render. Audit clean, 170 tests passing.

> The renderer is the CSS-3D version pulled from this chat's history. It's a faithful
> stylized model of the actual house, not a Three.js/satellite reconstruction — that
> remains the separate, larger track if you want true engine-grade 3D.


The residence overview moves out of the cramped dashboard column into a dedicated
full-width tab, which is what unlocks the annotated-house treatment.
- **New "Residence" tab** between Command Center and Settings. Command Center keeps
  System Status / Camera Watch / Activity — the camera now owns the full center column
  (more room for the feed).
- **Full-width residence + callouts restored.** With the width back, the leader-line
  callouts return in the margins like the concept render: live presence on the left
  (dominant red, occupied cyan, idle dim) and system layers on the right, around a
  larger 3D house that auto-fits the wider canvas.
- **Home-style templates.** A HOME STYLE selector picks the massing/roof shell that the
  floor-plan rooms populate — Cape Cod, Colonial, Ranch, Two-Story, Craftsman, Modern,
  Townhouse, Apartment, Cabin. Peaked styles render a gable (end-walls + ridge), flat
  styles a parapet cap; the choice persists via `residence_style` config and tags the
  banner. This is the foundation for the template-driven 3D you described.
- Smoke test now exercises both tabs (18 checks): Command Center camera at native
  aspect with the residence moved out, and the Residence tab's scene, style selector,
  banner stats, restored callouts, and live dominant-room flag. Audit clean, 170 tests.

> Honest scope: the roof massing is a first pass built in CSS, and I can't visually
> verify 3D in my environment — the gable/flat shells differentiate styles but may need
> a tuning pass from a screenshot. True per-style accuracy, solid sloped/hip roofs, and
> satellite-imagery-derived geometry are the WebGL/Three.js build, which I'd take on as
> its own track on your go-ahead.


Corrections to the 6.16.0 dashboard from live feedback.
- **Camera shows its native aspect ratio.** The feed was a tall flex box with
  `object-fit: cover`, which cropped a 16:9 stream into an ultra-wide strip. The feed
  now sizes to the image's own ratio (`width:100%; height:auto`, no crop), so the
  picture is whole and correctly proportioned.
- **3D house auto-fits its column.** The house was scaled for the full-width centre it
  had before the camera split; in the narrower shared column it oversized and clipped.
  It now computes a fit-zoom from the scene width on every build (honoring a manual
  wheel-zoom once set), so it stays whole whatever the column width. Reminder: drag
  rotates — the default ~45° isometric is the intended view; a near-0° drag flattens
  the floor plates to lines.
- **Sq-ft estimate sane.** The estimate used a wrong factor and printed ~14,450. It's
  now clamped to a believable range (and still overridable via `floor_plan_sqft`).
- **Perimeter callouts pulled back.** They need generous side margins like the concept
  render; in the narrow secondary column they overlapped the house. The property banner
  + stats stay; the callouts return only when the residence has the width (see note).
- Smoke test updated (12 checks): native-aspect feed, banner + sane stats, callouts
  cleared. Python audit clean, 170 tests passing.

> Note: the residence can't carry the annotated-house concept *and* be a narrow panel
> beside a primary camera — that composition needs width. Make the residence the wide/
> primary element and the full callout treatment fits; keep the camera primary and the
> residence stays a clean house + banner.


The 3D Residence Overview now carries the identity of the "satellite + architectural
data-merge" concept — rendered in the panel's own medium (CSS/SVG, no engine, no
build), not a photoreal CGI reproduction.
- **Property banner.** Top-left header — `PROPERTY · <address> · SATELLITE +
  ARCHITECTURAL DATA MERGE` — with the address pulled from `floor_plan_address`.
- **Live stat block.** Top-right: EST SQ FT (from `floor_plan_sqft`, else estimated
  from the floor-plan geometry and labelled `~`), BED / BATH (counted from real
  area metadata), and the live rotation angle.
- **Leader-line callouts.** Annotation labels pinned to the scene perimeter with
  connector lines + nodes, the way the concept image annotates rooms. The left column
  is fed by **real presence** — dominant room flagged red, occupied rooms cyan, the
  rest dim — and refreshes every poll. The right column annotates the system layers
  (HVAC / electrical / plumbing / network mesh). Callouts are pinned to the frame, not
  projected onto the geometry, so they stay correct while you drag-rotate the house.
- **Wireframe glow** on the house, and the prior `house3d-hud` corner labels are
  replaced by the banner/stat overlay. The 3D isometric house, drag-rotate, floor
  tabs, presence glow and per-room light toggles are all unchanged underneath.
- Smoke test extended (now 13 checks) to assert the banner, populated stats, callout
  rendering, and dominant-room flagging. Python audit clean, 170 tests passing.

Not photoreal: this is a stylized HUD interpretation, not the ray-traced render. A true
volumetric version would need a WebGL/Three.js scene and a real satellite-image asset —
a separate, much larger build if you ever want to go there.


The separate Command Center panel is retired; its capability now lives in the main
JARVIS panel, which keeps its 3D isometric floor plan (the 2D top-down experiment is
dropped).
- **Camera Watch in the dashboard.** The live/selectable camera feed with event
  auto-focus — ported from the standalone panel into `jarvis-panel.js` — now sits in
  the Command Center tab. The dashboard centre is a 2-up: **Camera Watch (primary,
  wider) beside the 3D Residence Overview**. Cameras read from `config.cameras`, stream
  via HA's MJPEG proxy with the entity's access token, switch on chip click, and
  auto-focus the relevant feed on a `jarvis_camera_event` / `jarvis_face_recognized`
  (banner + 25s revert), with a still-image fallback for Nest/WebRTC. Subscriptions and
  timers are torn down in `_stopIntervals`.
- **Single panel.** `panel_register.py` registers only the JARVIS panel now and removes
  the stale `/jarvis-command` sidebar entry on upgrade. `jarvis-command.js` deleted.
- **JS behavioural test.** `scripts/smoke_panel.js` retargeted to the combined panel:
  renders it under jsdom with a realistic payload and asserts the dashboard draws —
  styles, the 3D scene, and the folded-in Camera Watch (chips from `config.cameras`,
  auto-selected stream wired with the token). 9/9 pass. Python audit clean, 170 tests
  passing.


First real-deployment look at the new panel surfaced two frontend bugs (data was
flowing — areas, presence, live log all correct — but the panel was broken):
- **Orphaned stylesheet.** The component's CSS (`JC_STYLES`) was defined but never
  injected into the shadow DOM — the `innerHTML` started at `<div class="app">` with
  no `<style>`. Result: a plain unstyled text stack, no grid/borders/colours. Now
  injected as `<style>${JC_STYLES}</style>…`.
- **Data-contract mismatches.** The panel read `d.cameras`, `d.presence`, and
  `d.lockdown`, but `get_panel_data` returns presence under `dominant` and nests
  `cameras` + `lockdown` inside `config`. So the camera picker was always empty
  ("NO CAMERA SELECTED") and the dominant-area temp showed "—". Now reads
  `d.config.cameras`, `d.dominant`, and `d.config.lockdown` (with fallbacks).
- **Why the audit missed it + the fix.** The release gates were Python-only
  (`scripts/audit.py`, pytest) plus `node --check`, which validates JS *syntax* but
  not behaviour — an orphaned const and a wrong object path are both valid syntax.
  Added `scripts/smoke_panel.js`: renders the component under jsdom with a realistic
  `get_panel_data` payload and asserts it actually draws (styles injected, grid +
  modules present, camera chips populated from `config.cameras`, dominant area/temp
  shown, live MJPEG `src` wired with the access token). 10/10 pass. Python audit
  clean, 170 tests passing.


Proactive audit (not wait-and-see) of the code paths that only began loading after
6.14.1 surfaced two real bugs in `intent/intent_router.py`:
- **`from . import audio_routing` → `from ..`.** `intent_router` lives in the
  `intent/` subpackage, so the single-dot form resolved to the non-existent
  `intent.audio_routing` instead of the top-level `audio_routing`. It's a lazy
  import inside `_area_of`, and `_call_domain_in_area` calls `_area_of` inside a
  `try/except` that swallows the `ImportError` — so `secure_area`/`lights_off`
  would have silently matched zero entities and done nothing. Now `..audio_routing`.
- **`from .automation.mutex import Priority` → `from ..automation.mutex`.** Same
  class of bug (added in 6.13.0): `.automation` resolved to `intent.automation`
  (doesn't exist) rather than the top-level `automation` package; would have thrown
  on any guarded intent execution. Now `..automation.mutex`.
- **New `scripts/audit.py`.** A real compile gate plus a cross-file resolver that
  verifies every relative import (top-level and lazy) points at a name that actually
  exists — the check that catches wrong levels and stale exports. Both bugs above
  compiled clean and passed the unit tests (which exercise pure functions, not these
  paths), which is exactly why this gate is now part of the release process. Audit
  reports clean; 170 tests passing.


- **Bug.** `audio/__init__.py` carried two module docstrings (a v6.13.0 edit
  prepended a second without removing the original), which pushed
  `from __future__ import annotations` to line 3 → `SyntaxError: from __future__
  imports must occur at the beginning of the file`. This aborted the whole
  integration import on HA startup (`Unable to import component: jarvis`). It was
  latent through 6.13.0–6.14.0 and only surfaced on the first HA restart after
  deploying. Fixed by collapsing to a single docstring.
- **Why the release audit missed it.** The pre-release syntax gate used
  `ast.parse`, which does **not** enforce `__future__` positioning — it parsed the
  broken file clean. Switched the gate to a real `compile()` / `py_compile`
  (bytecode compile), which catches `__future__` placement and matches how HA
  actually imports. Re-audited the full tree: all 62 modules compile clean. 170
  tests passing.


The tactical HUD ships as a real panel — a second sidebar entry, "Command Center"
(`/jarvis-command`), alongside the existing detailed JARVIS panel.
- **New panel (`frontend/jarvis-command.js`, `jarvis-command`).** The operational
  HUD: monospace/terminal styling, ASCII rules, the three-over-two layout. All data
  is live off `jarvis/get_panel_data` + `jarvis/get_activity_log` (polled): system
  status (observer/cognition/satellite count/LLM link), a 2D top-down occupancy plan
  whose nodes are driven by real per-area presence (dominant area labelled with live
  temp), and a live activity log. Quick actions are wired to real services —
  `jarvis.briefing`, `jarvis/set_lockdown` (toggles, reflects live state),
  `jarvis.diagnose_doorbell`.
- **Live, selectable camera feeds.** The camera panel streams the selected camera via
  HA's MJPEG proxy using the entity's rotating `access_token`, with a chip selector
  built from the live camera list and a still-image fallback for cameras that don't
  serve MJPEG (e.g. Nest/WebRTC). 
- **Event auto-focus.** `camera.py` now fires a `jarvis_camera_event` when a
  Frigate/Nest detection lands (entity, label, confidence); the panel subscribes to
  it (and to `jarvis_face_recognized`) and automatically switches the main feed to
  the camera of the event, banners "EVENT FOCUS · <label> <conf>%", flags the area on
  the plan, then reverts to the user's selection after ~25s.
- **Registration.** `panel_register.py` refactored to a shared `_register_one` helper
  registering both panels from the same static dir, each with independent content-hash
  cache-busting. The installer already copies `frontend/*.js`, so the new component
  ships with no run.sh change. No regressions — 170 passing.


Two additions from the resilience blueprint. (§7's `LocalSemanticMemory` was again
**not** re-added — `memory/` would shadow `memory.py`; the recovery ledger remains
top-level.)
- **Entity concurrency mutex (`automation/mutex.py`).** `EntityLockRegistry` enforces
  per-entity mutual exclusion on a priority ladder (PREDICTIVE < ROUTINE < INTENT <
  VISUAL < SAFETY): an equal-or-lower priority request is discarded while a lock is
  held, and a strictly-higher one preempts the holder — so a real-time presence
  command beats a stochastic predictive one rather than colliding. The intent router
  now acquires per-entity locks (at INTENT priority) before acting and releases after,
  skipping any entity already held at higher priority. Lock ops are synchronous dict
  mutations (safe on HA's single-threaded loop); the registry is stdlib-only and
  unit-tested, with an async `guard` context manager.
- **Differential noise gating (`audio/noise_gate.py`).** `NoiseGate` checks appliance
  power signatures (`sensor.<appliance>_power`) and subtracts the dominant running
  appliance's known dB contribution from the raw `ambient_db` before it reaches
  prosody — so a running dishwasher or microwave doesn't push JARVIS to project
  louder. Wired into `jarvis.speak`'s telemetry build; dB is floored at 0 and None
  passes through.
- **Tests:** +18 (mutex acquire/discard/preempt/release/guard, noise-gate threshold/
  dominant-attenuation/floor/passthrough) — 170 passing.


Two additions from the resilience blueprint. (§5's `LocalSemanticMemory` was again
**not** re-added, and the recovery ledger was placed at the top level rather than
the spec's `memory/ledger.py` — a `memory/` package would shadow `memory.py`.)
- **Heartbeat + failover (`diagnostics/heartbeat.py`).** `HeartbeatMonitor` probes
  fixed-IP audio satellites, flags a node unavailable after 3 missed cycles, and
  reroutes audio to the first available adjacent speaker (then any available node,
  then None). The probe defaults to a short TCP connect to the ESPHome API port but
  is injectable; the failover state machine is pure and fully unit-tested. This is a
  configured capability — construct it with your satellites' IPs/adjacency and drive
  `run_once` from an interval; it is not auto-started.
- **Write-ahead state ledger (`state_ledger.py`).** `StateLedger` durably appends a
  device intent (fsync'd JSON-lines) before a high-stakes action fires and a
  completion record after. On boot the integration reconciles any intent that never
  completed against the device's current state, logging actions a crash or power loss
  interrupted, then compacts the log. The intent router now records intent before
  `secure_area` (cover/lock) via an injected, duck-typed ledger — the router stays
  import-free and standalone-testable.
- **Tests:** +20 (heartbeat miss-threshold/recovery/failover ladder/injected probe,
  ledger record/complete/pending/reconcile/compact/torn-line tolerance) — 152 passing.


Three additions from the resilience blueprint (the spec's `LocalSemanticMemory`
was deliberately **not** re-added — it's the package that collided with `memory.py`;
its fault-history role already lives in `diagnostics/fault_log.py`).
- **Boot guard + alert queue (`boot_guard.py`).** `jarvis.speak` calls that arrive
  before the integration finishes initialising — or during a config-entry reload —
  are now buffered in a bounded in-memory queue and replayed in arrival order once
  setup reports ready, instead of being dropped or fired into a half-built system.
  Reload-safe (re-gates on each setup), drop-oldest overflow at 25, and the
  15-minute audit holds until ready. The buffer logic is a stdlib-only `AlertBuffer`
  so it's unit-tested directly.
- **Root-cause diagnostic trees (`diagnostics/monitor.py`).** When the core network
  switch drops offline, the triage engine now inspects its upstream power monitor
  (`sensor.core_switch_power_watts`) and folds the deduction into the spoken verdict:
  near-zero or unreachable power ⇒ "an upstream power loss on its utility circuit";
  power still present ⇒ "a network or uplink fault rather than a power loss."
- **Air-gapped fallback templates (`intent/templates.py`).** A curated set of
  hardcoded status phrases (grounded in this property's entities — switch, freeze
  sensor, sump pump, garage, storage, …) with keyword matching, for instant
  informational responses when every model link is unreachable. A starting
  scaffold, not 50 invented strings; extend `STATUS_TEMPLATES` as needed.
- **Tests:** +15 (root-cause branches, boot-queue buffer/replay/reload/overflow,
  template lookup/matching) — 132 passing.


- **Follow-up to 6.10.2.** Removing `memory/` from the repo isn't enough if the
  package still lingers in a deployed copy. Extracting a new release over an old
  add-on folder adds files but never deletes ones that were removed, so a stale
  `memory/` can survive in the source tree, ride into the rebuilt image, and get
  copied to `/config/custom_components/jarvis/memory/` on every start — where it
  again shadows `memory.py` and the Memory card reads "unavailable." `run.sh` now
  defensively deletes any `memory/` directory at the destination whenever the
  canonical `memory.py` is present, so a stale package cannot survive a deploy
  regardless of what the source tree carried. No Python change (117 passing).


- **Regression fix.** The `memory/` package added in 6.9.0 shadowed the existing
  top-level `memory.py` (ChromaDB / FTS5 semantic memory). Because Python resolves
  a package before a same-named module, `from .memory import get_memory_stats`
  (and `search_memory`, `store_memory`, `get_conversation_context`) silently
  imported the package — which only exported the fault store — so those calls hit
  their `except` paths: the panel's Memory card read **Backend: unavailable /
  Stored Memories: 0** and the conversation agent lost long-term recall. The
  collision was latent until 6.10.1 (which first actually deployed the
  subpackages) unmasked it.
- **Fix:** removed the `memory/` package and relocated its rolling fault-history
  store to **`diagnostics/fault_log.py`** as `FaultLog` (it was never semantic
  memory — it's an infrastructure fault ledger, and belongs with diagnostics).
  `proactive_audio` now imports `FaultLog` from `.diagnostics`; the audit's
  "this has occurred before" recall is unchanged. The on-disk file moves from
  `/config/jarvis/semantic_memory.json` to `/config/jarvis/fault_history.json`.
  `from .memory import …` once again resolves to the real `memory.py`, restoring
  `get_memory_stats`, `search_memory`, `store_memory`, and conversation context.
  Tests relocated accordingly (117 passing).


- **Critical install fix.** `run.sh` copied only the component's top-level
  `*.py`/`*.json`/`*.yaml` (plus the `frontend/`, `translations/`, `blueprints/`
  asset dirs) and never the Python subpackages. With `audio/`, `diagnostics/`,
  `vision/`, `memory/`, `intent/`, and `automation/` absent from
  `/config/custom_components/jarvis/`, `proactive_audio.py`'s top-level
  `from .audio import ProsodyController` raised `ModuleNotFoundError` and the
  whole integration failed to set up. The installer now copies every source
  subdirectory that is a Python package (selected by `__init__.py`, so future
  subpackages are picked up automatically), clearing previously-installed
  packages first so renamed/removed modules don't linger. Asset dirs have no
  `__init__.py` and are untouched. No Python changed (suite still 117 passing);
  this is purely the install step.


- **`intent/intent_router.py` — `LocalIntentRouter`:** local, cloud-free command
  matching with ordered regex patterns (`secure the garage`, `turn off the
  lights`, pronoun forms like `turn it off` / `close it`). Pronoun context
  resolves to the active entity in the target area — a playing `media_player`
  first, then an `on` `light` — and executes locally. Pure helpers
  `match_intent()` / `is_affirmative()` are stdlib-only and unit-tested; hass-
  touching code is lazily imported so the module loads standalone.
- **Interactive feedback loop:** `jarvis.speak` gains `expect_response` and
  `confirm_intent`; an actionable announcement opens a 10-second, wake-word-free
  confirmation window (fires `jarvis_feedback_window` for the voice satellite
  layer). New **`jarvis.process_intent`** service delivers a captured phrase — an
  affirmative completes the pending action, otherwise the phrase routes as a
  fresh command. One shared router per HA instance preserves the window between
  the two calls.
- **`automation/predictor.py` — `PredictiveHabitMatrix`:** time-bucketed habit
  model over a bounded on-disk log (5000 events). `probability(key, at)` is the
  share of distinct observed days the action recurred in that time bucket;
  `due_preemptions(now, lead_minutes)` surfaces actions whose probability clears
  90% in the window 5–10 minutes ahead. Wired into the 15-minute loop to sample
  occupancy and log candidates — pre-emptive **execution is OFF by default**
  (`PREDICTOR_AUTOEXECUTE`), in keeping with JARVIS earning autonomy.
- **`jarvis.speak` `user_id`:** accepted and threaded through (logged), reserved
  for per-user biometric/profile filtering.
- **Tests:** new `test_intent_router.py` (intent matching, affirmatives, area-
  scoped pronoun resolution) and `test_predictor.py` (probability moving average,
  90% threshold, bucketing, lookahead, persistence, rolling cap, corrupt-file
  tolerance), plus prosody/triage updates — **117 passing, 1 skipped**.


- **`vision/spatial.py` — `SpatialContextEngine`:** fuses three per-area presence
  signals into an occupancy-confidence score (`sensor.{area}_frigate_person_count`
  >0 → +0.60, `binary_sensor.{area}_camera_gaze_detected` → +0.20,
  `binary_sensor.{area}_mmwave_presence` → +0.35, clamped to [0,1]). When gaze AND
  mmWave presence are both established it sets `skip_preamble`, and `jarvis.speak`
  now feeds that into prosody.
- **Prosody `skip_preamble`:** when the listener is demonstrably present and
  attending, the speech rate eases by 0.05 so the terse, preamble-free status
  reads clearly. The telemetry key `media_active` is now accepted (alongside the
  legacy `media_playing`).
- **`memory/vector_store.py` — `LocalSemanticMemory`:** a rolling on-disk JSON
  buffer (last 1000 events) under `/config/jarvis/semantic_memory.json`, with
  `commit_event(text, tags)` and `query_related_faults(keywords)`. The 15-minute
  infrastructure audit now recalls prior occurrences of a fault (matched on the
  triage finding tags), folds a short history clause into the spoken warning, and
  commits each occurrence — all file I/O off the event loop. `InfrastructureTriage`
  verdicts now carry a `tags` list for this recall.
- **Tests:** 22 new unit tests for spatial fusion, the memory store (incl. rolling
  cap, persistence, corrupt-file tolerance), and the new prosody behaviour
  (81 passing total).


- **Target resolution now goes through `audio_routing`** instead of a private
  media_player enumeration. `jarvis.speak` resolves announcement speakers with
  `audio_routing.speakers_in_area` (the same area→speaker routing, including
  device-inherited areas and listen-only-satellite exclusion, used by briefings,
  the sentinel, and doorbell announcements), falling back to the house broadcast
  set (`announcement_speakers` panel override → configured `broadcast_group` →
  all non-satellite speakers) so an announcement is never silently dropped.
- Ambient light/noise telemetry now resolves area membership via the same
  `audio_routing.entity_area` helper, and media-playing state is read from the
  resolved target speakers — one area-resolution path instead of two.
- Ducking now applies to the resolved targets (the speakers actually used),
  restored in a `finally` block as before. Behaviour-equivalent for the common
  case (speakers in the area), but now consistent with the rest of JARVIS and
  correct for Cast-group and broadcast targets.


- **New service `jarvis.speak`** (`message`, `target_area`, `critical`): a
  context-aware spoken announcement. It resolves the target area's entities
  (direct *and* device-inherited), measures ambient light, noise, and media
  activity, and shapes delivery via a new `ProsodyController` — volume, speech
  rate, and a named style (authoritative / whisper / subdued / projected /
  neutral). Active media is ducked for the announcement and restored afterward
  in a `finally` block, so a TTS error never leaves your music turned down.
- **Infrastructure audit** (`InfrastructureTriage`): every 15 minutes JARVIS
  checks root storage (warn >90%, critical >96%), RAM (>92%), and the
  connectivity of the core network switch and basement freeze sensor, then
  synthesises a single natural-language verdict and announces failures to the
  office. Confirmed-offline is critical; unreadable/unavailable is a softer
  visibility warning. A startup probe runs ~60s after load so issues surface
  without waiting a full interval.
- New package layout: `audio/prosody.py` and `diagnostics/monitor.py` (both
  stdlib-only, no Home Assistant dependency), wired in through
  `proactive_audio.py` via two hooks in `async_setup_entry`/`async_unload_entry`.
- **Tests:** 23 new unit tests pin the prosody rule matrix and triage thresholds
  (59 passing total).
- **Config:** set your TTS entity (`proactive_tts_entity` in panel runtime config,
  else `tts.piper`) and the audit's target area (`office` by default).

## [6.7.3] — Safety-loop fix + regression test harness
- **Fix (critical):** since v6.7.1 the cognitive safety tick had been throwing
  `AttributeError` every cycle. `SafetyManager.tick` and `_check_intrusion` call
  `self._residents_away()`, but that method was defined on `LockdownManager`, not
  `SafetyManager` — so freeze, intrusion, and nighttime-lockdown checks were
  silently dying inside the loop's exception handler. `_residents_away` has been
  moved to `SafetyManager` where it is used; `LockdownManager` keeps the
  `_anyone_home` predicate it actually calls. Behaviour of both is unchanged from
  the v6.7.1 intent — they are now simply on the right classes.
- **Tooling:** introduced a Home-Assistant-free **pytest harness** under `tests/`.
  A `conftest.py` installs minimal `homeassistant.*` stubs into `sys.modules`
  before collection and loads integration modules under a synthetic `jc` package;
  hand-rolled fakes (`FakeHass`, `FakeProvider`) exercise `cognitive_core` and
  `reasoning_loop` as near-pure functions. A thin `pytest-homeassistant-custom-
  component` integration layer is scaffolded (skips cleanly until that dep is
  installed). 36 tests now cover the safety predicates, freeze thresholds, and the
  reasoning resilience cascade (cloud failure → breaker open → local floor). This
  is the harness that caught the bug above.

## [6.3.2] — Startup no longer blocked
- **Fix:** the cognitive loop was created with `async_create_task`, which Home
  Assistant tracks as part of config-entry setup — so HA's bootstrap waited the
  full startup timeout on a loop that never returns, logging "Something is
  blocking Home Assistant from wrapping up the start up phase." It now runs as a
  proper **background task** (exempt from the startup wait), with a guarded
  fallback for cores predating the helper.
- The loop also yields before its first tick so startup settles before any
  state-scanning work begins.

## [6.3.1] — Two root-cause fixes
- **Cognition:** `binary_sensor.backups_stale` was escalating as a
  "safety/security trigger" because device_class `problem` was lumped with
  smoke/CO/gas. `problem` now has its own moderate tier, and system-maintenance
  entities (backup, update, snapshot, certificate, HACS, supervisor, firmware…)
  are damped to informational so housekeeping never masquerades as a security
  event. Life-safety classes remain at top salience.
- **Doorbell backlog scanner:** device discovery now mirrors Home Assistant
  core's own Nest enumeration (`async_loaded_entries → runtime_data.device_manager`),
  fetches transcoded thumbnails for clip-preview events and image media for still
  events, and rejects raw MP4 bytes that a vision model can't read. Failure
  reporting is now stage-precise.

## [6.3.0] — Local speech parity
- All local speech now flows through one voice: the Local Mind's composer.
  The learned-cache replay, the legacy templates, and the fallback path no longer
  speak in three different vintages of phrasing.
- Device-aware language (a window "is open," not "is on"; motion "has detected
  motion"), safety-class phrasing ("is detecting smoke", "has cleared"), numeric
  readings (battery "is at 18%"), and named safety alerts ("a smoke alert from
  Kitchen Smoke Detector").

## [6.2.0] — The Local Mind
- An offline reasoning brain that replaces the crude fallback when the cloud is
  unreachable. It replicates a frontier model's decision *procedure*:
  self-awareness (duplicate + flap detection), historical grounding against
  `patterns.db`, case-based memory from past cloud decisions, situational
  judgment (urgency × novelty × presence × security), and persona verbalization.
- Every decision logs its reasoning chain to the dashboard's `LOCAL` log filter.

## [6.1.0] — Loosened reins (capability expansion)
- **Automation suggestions surfaced** in the dashboard with confidence bars,
  YAML reveal, and approve/dismiss — the pattern engine's intelligence is finally
  visible. Thresholds loosened and made runtime-tunable.
- **Visitor learning** — person events feed silent vision learning (training data
  only, never spoken).
- **Rich Reasoning** — optional cloud-first judgment for medium/high events.
- **Ollama groundwork** — a Local LLM URL field flows through every provider path
  for the upcoming GPU server.

## [6.0.0] — Glassmorphism UI
- A deep visual reskin: dark-cyan glassmorphism, Space Grotesk + JetBrains Mono,
  a perspective-grid 3D house with a rotating radar sweep, glass panels, and a
  status badge wired to lockdown state. No structural changes — pure aesthetics.

## [5.9.50] — Doorbell Training UI
- A Settings panel showing the analyzed doorbell dataset, with a backlog-scan
  button and source-tagged event rows (live / event-media / backlog).

## [5.9.49] — Package & mail detection
- Porch-camera watching for packages and mail with a per-camera state machine,
  15-minute sweeps, quiet-hours gating, and an on-demand `jarvis.check_packages`
  service.

## [5.9.48] — Doorbell-only analysis + backlog training
- Camera auto-analysis narrowed to intentional doorbell presses, with a two-pass
  live-clip / recorded-event approach and a JSONL training log.

## [5.9.47] — Automatic camera event analysis
- Doorbell and (optionally) motion events are now auto-analyzed as they happen,
  not just cached.

## [5.9.46] — Appliance profile loading fix
- The appliance monitor now reads its saved profile directly from live runtime
  config instead of falling back to legacy guessing.

## [5.9.45] — Appliance row UI fix
- Restructured appliance cards so delete buttons are no longer covered.

## [5.9.44] — Per-room 3D occupancy glow
- The isometric house lights rooms by occupancy: idle wireframe, occupied cyan
  glow, dominant room pulsing.

## [5.9.43] — Iron Man HUD radial gauges
- Temperature, humidity, and lighting for the dominant room rendered as SVG donut
  gauges.

---

Earlier releases (v5.7–v5.9.42) introduced the observer pipeline, the connectivity
breaker, lockdown management, multi-frame camera vision, constrained appliance
disaggregation, quiet-hours gating, and the reasoning cache.
