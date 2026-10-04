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
**No primitive is at `enforce` yet** — that is an owner-gated step (see
`docs/JARVIS_CONSTITUTION.md`, invariant on authority), taken only after parity
holds on the live home system.

## Current matrix

<!-- BEGIN kernel-adoption (python3 scripts/kernel_adoption.py --markdown) -->
| Primitive | Stage | Live callers |
| --- | --- | --- |
| `actuator` | ◑ parity | `actuation`, `agent` |
| `attention` | · pure | — |
| `authority` | ◑ parity | `authority_bridge` |
| `beliefs` | · pure | — |
| `budget` | · pure | — |
| `causal` | · pure | — |
| `correlation` | ◐ shadow | `actuation`, `decision_record`, `observer`, `proactive_audio` |
| `event` | ◑ parity | `actuation`, `camera`, `observer`, `proactive_audio` |
| `event_bus` | ◐ shadow | `__init__` |
| `journal` | · pure | — |
| `ledger` | ◐ shadow | `__init__` |
| `loop_detect` | · pure | — |
| `persistence` | · pure | — |
| `plan` | ◑ parity | `actuation`, `agent` |
| `priority` | · pure | — |
| `router` | · pure | — |
| `situation` | ◑ parity | `intrusion` |
| `world_model` | ◑ parity | `actuation`, `cognitive_core` |
<!-- END kernel-adoption -->

## Notes on specific primitives

- **persistence** is an internal seam (connection + migrations) consumed by other
  kernel modules such as `ledger`, not by live callers directly — "pure" here
  means "no legacy bypass to retire", not "unused".
- **world_model** is at parity through `actuation` and `cognitive_core` (MCU
  Phase A/B/C): the
  `control_device` path reads its pre-action context snapshot through the facade
  — the canonical context authority — and uses the result (area, previous_state),
  falling back to raw HA state. Parity, not enforce: the facade informs the path
  but the raw sources stay authoritative underneath. C1 (Phase C) routes
  cognition's presence context (`anyone_home`) through the facade too,
  reading the same person entities — behaviour-identical, context via the
  authority.
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
  plan own execution needs an async driver and is a later step.
- **event** is at parity across `agent`, `camera`, `observer`, `proactive_audio`.
  As of 8.32.0 (MCU Phase A) `control_device` publishes a canonical actuation
  `JarvisEvent` (`from_actuation`) on the bus, which the ledger records — so the
  actuation enters the event stream alongside perception. Parity, not enforce:
  the event is emitted and recorded, but no cognitive consumer reacts to it yet.
- **authority** is at parity through `authority_bridge` (log-only): every control
  action records what the capability engine *would* have decided against what the
  legacy confirm-gate actually did. Flipping it to `enforce` on the live system is
  owner-gated and must not happen without explicit approval.
- **priority** (emergency hierarchy) is a pure comparison primitive; `attention`,
  `authority` and the planner will consult it as they reach parity.

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
