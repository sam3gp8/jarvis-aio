# JARVIS Constitution

> The invariants JARVIS must never violate, and the ordering it resolves conflicts
> by. This is a short, deliberately stable document: the kernel migration
> (`docs/KERNEL_PLAN.md`) and the adoption matrix (`KERNEL_ADOPTION.md`) describe
> *how* the system is being built; this describes *what must stay true* no matter
> how it is built. If an implementation ever contradicts a rule here, the
> implementation is wrong.

## 1. Precedence ladder

When two concerns compete, the higher tier wins. This ladder is encoded once, in
`custom_components/jarvis/kernel/priority.py`, so attention, authority and the
planner resolve conflicts the same way instead of with ad-hoc `if urgent` checks.

| Rank | Tier | Examples |
| --- | --- | --- |
| 1 (highest) | **life safety** | smoke/CO, medical, fire, a person in danger |
| 2 | **security** | intrusion, unexpected unlock, perimeter breach |
| 3 | **property** | water leak, freeze, appliance fault, left-on hazard |
| 4 | **household** | routines, schedules, comfort automations |
| 5 | **convenience** | nice-to-have automations, proactive suggestions |
| 6 (lowest) | **personality** | tone, banter, flavour, voice persona |

**Invariant P1 — personality never overrides safety.** No stylistic,
conversational, or persona concern may suppress, delay, or soften a life-safety or
security action. `priority.may_override(PERSONALITY, <safety tier>)` is `False` by
construction, and no code path may route around it.

**Invariant P2 — a lower tier never overrides a strictly higher one.** Equal tiers
may arbitrate among themselves; a lower tier may not win against a higher one.

## 2. Authority

**Invariant A1 — authority is log-only until deliberately promoted.** The capability
engine (`kernel/authority.py`) begins in parity through `authority_bridge`: for every
control action it records what it *would* have decided versus what the legacy
confirm-gate actually did, and while in parity for a given capability it must not
deny or alter the live action. Promotion past parity is the owner-gated step in A2;
a primitive never jumps straight to authoritative (C1).

**Invariant A2 — enforcement is owner-gated, and may only tighten.** Flipping a
decision primitive from parity to `enforce` on the live home is a deliberate,
human-approved step, taken only after parity has held on real traffic. No automated
process, prompt, scheduled trigger, or external input may flip it. Where authority
*is* enforced, it runs under a **max-restriction belt**: the action proceeds only if
the legacy gate allows it **and** the engine allows it, so enforcement can only ever
*add* a confirmation, never remove the legacy gate or loosen a decision. Every
enforced path fails safe to the legacy outcome on an engine fault and carries a
one-line kill-switch back to parity. The live stage of each primitive is recorded in
the generated ledger below (§ *Enforcement ledger*), which CI keeps in agreement
with the implementation.

**Invariant A3 — capabilities expire and can be revoked.** Tokens carry an optional
expiry and a revocation set; an expired or revoked token is denied even if its scope
would otherwise allow the action. Derived (child) tokens never outlive their parent.

### Enforcement ledger

> **Generated** from `scripts/kernel_adoption.py` (`_DECLARED`) by
> `scripts/kernel_docs_sync.py`; CI (`--check`) fails if this block or the
> `KERNEL_ADOPTION.md` matrix disagrees with the implementation. Do not hand-edit —
> change the declared stage in code and run `--write`. A primitive at `enforce` is
> authoritative on at least one live path; several (authority, the freeze/delivery
> situations) are owner-approved and governed by the A2 max-restriction/fail-safe
> rule, and intrusion deliberately remains at parity pending a real-traffic burn-in.

<!-- BEGIN kernel-stage-ledger (python3 scripts/kernel_docs_sync.py --write) -->
| Primitive | Stage |
| --- | --- |
| `actor` | ◐ shadow |
| `actuator` | ● enforce |
| `agency` | ● enforce |
| `agency_state` | ◐ shadow |
| `attention` | ◐ shadow |
| `authority` | ● enforce |
| `autonomy` | ◑ parity |
| `beliefs` | ◐ shadow |
| `budget` | ● enforce |
| `causal` | ◑ parity |
| `conflict` | ◑ parity |
| `correlation` | ◐ shadow |
| `cycle` | ◑ parity |
| `event` | ◑ parity |
| `event_bus` | ◐ shadow |
| `graph` | ● enforce |
| `identity_fabric` | ◑ parity |
| `journal` | ◐ shadow |
| `learning` | ◑ parity |
| `ledger` | ◐ shadow |
| `loop_detect` | ● enforce |
| `outcome` | ◐ shadow |
| `persistence` | · pure |
| `plan` | ◑ parity |
| `priority` | · pure |
| `provenance` | ◐ shadow |
| `router` | ◐ shadow |
| `self_model` | ◐ shadow |
| `situation` | ● enforce |
| `space_time` | ◑ parity |
| `temporal` | ◐ shadow |
| `token_telemetry` | ◐ shadow |
| `uncertainty` | ◑ parity |
| `working_memory` | ◑ parity |
| `world_model` | ● enforce |
<!-- END kernel-stage-ledger -->



## 3. Safety defaults

**Invariant S1 — fail safe, not open.** When a safety-relevant input is unavailable
or unknown (e.g. the alarm panel is `unavailable` at startup), JARVIS holds the
safe state rather than assuming all-clear. Only a positive, confirmed signal lifts a
protective hold.

**Invariant S2 — no silent control.** Every control action JARVIS takes is
attributable: it records who/what requested it, the decision, and the outcome, so
the ledger can reconstruct what happened. A control path that cannot be recorded is
a bug.

**Invariant S3 — degrade, don't crash.** A non-critical subsystem failing (a
monitor, an optional sensor, a model provider) must degrade to a logged non-fatal
warning, never take down the integration or a safety path.

## 4. Change discipline

**Invariant C1 — additive first.** New kernel capability lands pure and
unit-tested, then shadow, then parity, before it can influence a live decision. No
primitive jumps straight to authoritative. The adoption matrix
(`KERNEL_ADOPTION.md`) must reflect reality; `scripts/kernel_adoption.py --check`
guards against a declared stage having no live caller, and
`scripts/kernel_docs_sync.py --check` guards against this document and the matrix
describing a different stage than the implementation declares.

**Invariant C2 — the invariants above outrank convenience.** If a feature can only
ship by weakening a rule in this document, it does not ship until the rule is
deliberately, explicitly revised here first.

---

## Executable

These invariants are not just prose — the ones that can be mechanically checked are
encoded as tests that *attempt the violation* and assert the responsible kernel
primitive blocks it, in `tests/unit/test_constitution.py` (run in CI):

| Invariant | Enforced by | Test asserts |
| --- | --- | --- |
| Personality never overrides safety (P1/P2) | `kernel.priority.may_override` | personality can't override a safety tier; no lower tier overrides a higher one |
| No delegation escalation (A3) | `CapabilityToken.derive` | a derived token can't gain a capability its parent lacked; a child never outlives its parent |
| Autonomy is revocable (A3) | `kernel.authority.authorize` | an **expired** or **revoked** token is denied |
| Security requires authority (invariant S / A) | `kernel.authority.authorize` | a security capability is never silently allowed — DENY without identity, CONFIRM with |
| Fail closed | `kernel.authority.authorize` | a policy that raises resolves to DENY |
| Verify after act | `kernel.plan.execute_plan` | a step whose postcondition never holds is not DONE (VERIFY_FAILED) |
| Idempotency required | `kernel.plan.execute_plan` | a completed idempotency key is skipped, never re-executed |
| Correlation propagates | `kernel.correlation.scope` | a correlation id is carried through a scope and restored on exit |

If one of those tests fails, an invariant has been broken — treat it as a release
blocker. The owner-gated promotion stance (A2) and each primitive's live stage are
operational facts verified by the adoption/coverage matrices and the doc-sync gate
(`scripts/kernel_docs_sync.py --check`), which keeps this document's enforcement
ledger in agreement with the implementation, rather than by unit tests.

---

*These invariants are intentionally few. Add one only when it is genuinely
inviolable — the value of this document is that every line is load-bearing.*
