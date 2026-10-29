#!/usr/bin/env python3
"""Behavioral kernel-coverage matrix (MCU audit follow-up P0.5).

`kernel_adoption.py` answers *"does a live module reference a kernel primitive?"*
That is necessary but not sufficient: a module can `from .kernel import authorize`
and still have actuators that bypass Authority. The external MCU audit asked the
sharper question —

    "what fraction of behaviour-bearing paths actually pass through the kernel
     contract (Event → WorldModel → Situation → Authority → Plan → Verify →
     Outcome)?"

This script makes that measurable. For each behaviour-bearing *path* (the ways
JARVIS acts on the home) it declares, per pipeline contract, how far that path is
wired onto the kernel — ``none / shadow / parity / full`` — and the script
verifies every non-``none`` claim against a piece of **evidence** that must be
present in the path's source (so the matrix cannot drift into optimistic
fiction).

**The headline is the honest *applicable-cell* figure.** Not every contract can
exist for every path — a one-shot toggle is not a situation; a fire-and-forget
bulk write has no single end-state to verify; a detection path observes and never
actuates. Those cells are declared **N/A-by-design** (each with a reason, in a
rendered ledger) and excluded from the denominator, so the percentage measures
progress against what can actually be wired rather than being permanently diluted
by cells that are structurally meaningless. The all-cells figures (weighted and
honest over the full grid) are still printed underneath for continuity. N/A is
held to a conservative bar — when in doubt a cell stays an un-wired *gap*, which
lowers the number — and every N/A reason is listed so a real gap can't hide as one.

    python3 scripts/kernel_coverage.py             # the matrix + coverage %
    python3 scripts/kernel_coverage.py --check      # + fail on evidence drift
    python3 scripts/kernel_coverage.py --markdown    # the KERNEL_COVERAGE table

The number is deliberately low today — most paths are still legacy. That is the
truth the audit wanted surfaced, and the gate keeps it honest as paths migrate.
Keep ``_PATHS`` the single source of truth; raise a cell's stage only when the
real execution path reaches that stage, and update the evidence with it.
"""
from __future__ import annotations

import pathlib
import sys

# Pipeline contracts, in order (the MCU "spine").
_CONTRACTS = ["event", "world_model", "situation", "authority", "plan",
              "verify", "outcome"]

# How much each stage counts toward coverage. Two scales, both generated so
# neither can drift from a hand-written figure (the earlier doc hard-coded an
# "honest" number in prose that silently diverged from the generated one):
#   _WEIGHT        — weighted: observe-only shadow earns 1/3 partial credit.
#   _STRICT_WEIGHT — honest: shadow earns 0 (it governs nothing — it only logs),
#                    so only parity (logs agreement) and full (drives) count.
_WEIGHT = {"none": 0.0, "shadow": 1 / 3, "parity": 2 / 3, "full": 1.0}
_STRICT_WEIGHT = {"none": 0.0, "shadow": 0.0, "parity": 2 / 3, "full": 1.0}
_ICON = {"none": "·", "shadow": "◐", "parity": "◑", "full": "●"}
# A cell marked N/A-by-design (see each path's ``na`` map): the contract is
# structurally meaningless for that path, so it is excluded from the honest
# *applicable* denominator rather than counted as an un-wired gap. Rendered with
# its own glyph and listed, with its reason, in the N/A ledger — the mechanism
# that keeps "N/A" from becoming a hiding place for real gaps.
_NA_ICON = "–"


# Behaviour-bearing paths → the module file that carries the path, the declared
# stage + evidence per contract, and a per-path ``na`` map declaring which
# contracts are **N/A-by-design** (structurally meaningless for that path), each
# with a concrete reason.
#
# Three states per cell, not two:
#   * declared in ``contracts``  → wired at that stage (evidence verified);
#   * listed in ``na``           → N/A-by-design (excluded from the applicable
#                                   denominator, shown with its reason);
#   * neither                    → an un-wired **gap** ("none"): applicable but
#                                   not yet wired, so it counts *against* us.
#
# N/A is held to a conservative bar on purpose: a contract is N/A only when it
# cannot meaningfully exist for the path (a one-shot toggle is not a situation;
# a fire-and-forget bulk write has no single end-state to verify; a detection
# path observes and never actuates, so it has no authority/plan/outcome). When
# in doubt a cell is left a gap, which lowers the number — the honest failure
# mode. Every ``na`` reason is rendered into the ledger so a real gap can never
# be quietly reclassified as N/A to flatter the percentage.
_PATHS: dict[str, dict] = {
    "control_device": {
        "module": "agent.py",
        "contracts": {
            # Pre-action context snapshot via the WorldModel facade (8.30.0),
            # routed through the shared actuation envelope (B0). Parity, not full
            # — the post-action verify/read-back still reads raw HA state.
            "world_model": {"stage": "parity", "evidence": "actuation.context"},
            # H1: logs the Phase-4 engine decision vs the live confirm-gate.
            "authority": {"stage": "parity", "evidence": "authority_bridge"},
            # v6.38 verify-after-act for deterministic targets.
            "verify": {"stage": "full", "evidence": "_verify_control"},
            # The verify step produces the canonical ActuatorOutcome (8.31.0) via
            # the envelope — requested→executed→observed→verified (audit #18).
            "outcome": {"stage": "full", "evidence": "actuation.outcome"},
            # The actuation is published as a canonical JarvisEvent on the bus
            # (8.32.0). Parity — it enters the stream, no consumer reacts yet.
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            # Execution routes through the kernel planner (B4b): a one-step plan
            # whose run_step performs the awaited service call (aexecute_plan).
            # Full — the planner owns execution, not just a shadow description.
            "plan": {"stage": "full", "evidence": "aexecute_plan"},
        },
        "na": {
            "situation": "a single imperative device actuation is not a "
                         "situation lifecycle (no open/close state to model)",
        },
    },
    # Each executed target is routed through the actuation envelope (B3):
    # WorldModel context + actuation event. Authority at parity (#4b): per target
    # the kernel authority engine's verdict is recorded against the legacy skip
    # decision (log-only, covering the skipped protected targets too).
    # Plan FULL: each target's execution runs *through* the kernel planner — the
    # universal seam (execute_actuator) drives every target as a one-step
    # aexecute_plan, the same mechanism control_device and proactive use, so the
    # planner owns execution per target. (An additional descriptive N-step
    # `bulk_plan` is also recorded for the whole op; it is incidental to the
    # per-target planner execution the plan cell credits.) Fire-and-forget, so
    # no per-device verify/outcome by design.
    "bulk_control": {
        "module": "agent.py",
        "contracts": {
            "world_model": {"stage": "parity", "evidence": "actuation.context"},
            "authority": {"stage": "parity", "evidence": "record_control_parity"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            "plan": {"stage": "full", "evidence": "execute_actuator"},
        },
        "na": {
            "situation": "imperative multi-device actuation, not a situation "
                         "lifecycle",
            "verify": "fire-and-forget across many targets — no single expected "
                      "end-state to read back",
            "outcome": "fire-and-forget — no per-device actuator outcome record",
        },
    },
    # Each step's execution routes through the kernel planner
    # (aexecute_plan) and publishes an actuation event (B4). Authority at parity
    # (#4b): each step runs through the confirm-gate + kernel authority engine
    # (authority_bridge.enforced_decision) — the same max-restriction belt as
    # control_device, log-only parity except where it tightens an allowlisted
    # security capability. Full plan ownership of execution; steps are arbitrary
    # services, so no single expected end-state (no verify/outcome cell).
    "execute_plan": {
        "module": "agent.py",
        "contracts": {
            "authority": {"stage": "parity", "evidence": "enforced_decision"},
            "plan": {"stage": "full", "evidence": "aexecute_plan"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
        },
        "na": {
            "situation": "an imperative multi-step plan is not a situation "
                         "lifecycle",
            "verify": "arbitrary services per step — no single expected "
                      "end-state to read back",
            "outcome": "arbitrary services — no single actuator outcome record",
        },
    },
    # Activates scenes/scripts/automations. Routed through the shared actuation
    # envelope (B1): WorldModel context, actuation event. No deterministic
    # end-state, so no verify/outcome. Authority at parity (authority-breadth):
    # the activation records the kernel authority engine's verdict
    # (record_control_parity, log-only) against the path's no-gate allow — a
    # scene/script can do anything a confirm-gate would guard, so its authority
    # contract is measured, not blank. Log-only; never gates.
    # Plan FULL: the activation runs *through* the kernel planner — the universal
    # seam (execute_actuator) executes it as a one-step aexecute_plan whose
    # run_step performs the awaited service call, exactly as control_device does
    # (B1/H3 replaced the old hand-logged shadow plan with this seam execution).
    "run_scene_or_script": {
        "module": "agent.py",
        "contracts": {
            "world_model": {"stage": "parity", "evidence": "actuation.context"},
            "authority": {"stage": "parity", "evidence": "record_control_parity"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            "plan": {"stage": "full", "evidence": "execute_actuator"},
        },
        "na": {
            "situation": "scene/script activation is imperative, not a "
                         "situation lifecycle",
            "verify": "a scene has no single deterministic end-state to read "
                      "back",
            "outcome": "no single expected end-state — no actuator outcome "
                       "record",
        },
    },
    # Mode directive whose entry applies a mode scene (mode_scene). Routed
    # through the actuation envelope (B2): actuation event + shadow plan for the
    # mode change. No single-entity context (so no world_model) and no
    # deterministic end-state (so no verify/outcome); the target is the mode.
    # Authority at parity (authority-breadth): the mode change now records the
    # kernel authority engine's verdict (record_control_parity, log-only)
    # against the path's no-gate allow — a mode change applies scenes that can
    # be consequential, so its authority contract is measured, not blank.
    # Log-only; never gates the directive.
    "set_mode": {
        "module": "agent.py",
        "contracts": {
            "authority": {"stage": "parity", "evidence": "record_control_parity"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            "plan": {"stage": "shadow", "evidence": "actuation.plan_shadow"},
        },
        "na": {
            "world_model": "the target is an abstract mode, not a single entity "
                           "with pre/post state to snapshot",
            "situation": "a mode directive is imperative, not a situation "
                         "lifecycle",
            "verify": "the mode's home effect is a scene — no single "
                      "deterministic end-state to read back",
            "outcome": "no single expected end-state — no actuator outcome "
                       "record",
        },
    },
    # Intrusion mirrors its lifecycle into the kernel Situation state machine AND
    # (D1) verifies the mirrored state agrees with the legacy verdict, log-only —
    # genuine parity, not a blind shadow copy. Evidence is the parity check.
    # A detection path: it observes and raises a situation; it does not itself
    # actuate the home (responses flow through control_device etc.), so
    # authority/plan/verify/outcome are N/A for this path.
    "intrusion": {
        "module": "intrusion.py",
        "contracts": {
            "situation": {"stage": "parity", "evidence": "_record_parity"},
            # D4: each lifecycle transition publishes a canonical situation
            # JarvisEvent on the bus (parity — enters the stream + ledger, no
            # consumer reacts yet).
            "event": {"stage": "parity", "evidence": "publish_situation"},
        },
        "na": {
            "authority": "detection path — it observes and raises a situation, "
                         "it does not actuate, so there is no authority decision",
            "plan": "detection path — produces no actuation plan",
            "verify": "nothing is actuated by this path, so there is nothing to "
                      "verify",
            "outcome": "nothing is actuated — no actuator outcome record",
        },
    },
    # The in-home freeze hazard lifecycle (SafetyManager._check_freeze:
    # warning → critical → cleared) is mirrored into a kernel Situation
    # (kind="hazard") and its agreement verified (D2), log-only parity. A
    # detection path, same N/A shape as intrusion.
    "hazard": {
        "module": "hazard_situation.py",
        "contracts": {
            "situation": {"stage": "parity", "evidence": "_record_parity"},
            "event": {"stage": "parity", "evidence": "publish_situation"},
        },
        "na": {
            "authority": "detection path — observes a hazard, does not actuate",
            "plan": "detection path — produces no actuation plan",
            "verify": "nothing is actuated by this path — nothing to verify",
            "outcome": "nothing is actuated — no actuator outcome record",
        },
    },
    # The per-camera package delivery lifecycle (package_monitor: delivered →
    # removed) is mirrored into a kernel Situation (kind="delivery") and its
    # agreement verified (D3), log-only parity. A detection path, same N/A shape.
    "delivery": {
        "module": "delivery_situation.py",
        "contracts": {
            "situation": {"stage": "parity", "evidence": "_record_parity"},
            "event": {"stage": "parity", "evidence": "publish_situation"},
        },
        "na": {
            "authority": "detection path — observes a delivery, does not actuate",
            "plan": "detection path — produces no actuation plan",
            "verify": "nothing is actuated by this path — nothing to verify",
            "outcome": "nothing is actuated — no actuator outcome record",
        },
    },
    # goals is an orchestration path: when a goal comes due it runs the headless
    # agent, whose tool-calls actuate through the already-wired paths. What goals
    # itself owns on the spine: the goal-as-Plan (F1/F2, shadow) and the lifecycle
    # events it publishes. world_model/authority/verify/outcome stay honest gaps —
    # goals doesn't read state or actuate directly (the agent does), so they are
    # not yet claimed here.
    "goals": {
        "module": "goals.py",
        "contracts": {
            # F1/F2: a goal's ordered steps are expressed as a kernel Plan and
            # durably journaled (record_plan via _shadow_plan). Shadow — the Plan
            # is built and recorded but never executed or consulted; the goal
            # store stays authoritative (the enforce flip is the owner-gated H4b).
            "plan": {"stage": "shadow", "evidence": "_shadow_plan"},
            # Each goal engage/close publishes a canonical goal.transition
            # JarvisEvent on the bus (parity — enters the stream + ledger, no
            # consumer reacts yet), the same nervous-system wiring as situations.
            "event": {"stage": "parity", "evidence": "publish_goal"},
        },
        "na": {"situation": "goal-driven actuation is imperative, not a "
                            "situation lifecycle"},
    },
    # proactive — JARVIS acting on a learned/trusted pattern of its own accord
    # (the *discretionary autonomous* device path). The live path is
    # `cognitive_core._execute_action_data`, NOT the `proactive_audio.py`
    # announcement/TTS path this row used to name (that is communication, not
    # actuation on the home); re-pointed here so the row measures the real
    # device autonomy. It routes each target through the universal actuation
    # seam (`actuation.execute_actuator`): the kernel planner owns execution
    # (plan ●, via aexecute_plan inside the seam) and the canonical actuation
    # JarvisEvent is published (event ◑). Authority at parity (log-only): the
    # engine verdict is recorded (`record_control_parity`) against the path's
    # proceed decision. world_model/verify/outcome stay honest gaps — the path
    # takes no pre-action WorldModel snapshot and passes verify=None (no
    # read-back), so there is no outcome record either; conservative, counts
    # against us. (The G1/G2 agency-budget and loop-detect ENFORCE gates that
    # decide *whether* a proactive action runs are a separate rate/loop
    # governor, not the authority permission engine measured here.)
    "proactive": {
        "module": "cognitive_core.py",
        "contracts": {
            "plan": {"stage": "full", "evidence": "execute_actuator"},
            "event": {"stage": "parity", "evidence": "execute_actuator"},
            "authority": {"stage": "parity", "evidence": "record_control_parity"},
        },
        "na": {"situation": "discretionary autonomous actuation is imperative, "
                            "not a situation lifecycle"},
    },
    # friday — the FRIDAY background-automator sub-agent, the one profile that
    # may actuate. It acts ONLY through its grant set
    # (control_device / bulk_control / run_scene_or_script), each of which
    # records the kernel authority engine's verdict (parity). The H6 delegation
    # scope (`_DELEGATION_ATTRIBUTION`) brackets friday's run so every such
    # verdict is recorded against actor="friday" — so friday's authority
    # contract is measured (at parity), attributed to the sub-agent, not blank.
    # The remaining actuation contracts (event/world_model/plan/verify/outcome)
    # are performed inside those grant-set handlers and counted on *their* rows;
    # friday's own path adds no wiring there, so they stay conservative gaps
    # here rather than double-count.
    "friday": {
        "module": "agent.py",
        "contracts": {
            "authority": {"stage": "parity", "evidence": "_DELEGATION_ATTRIBUTION"},
        },
        "na": {"situation": "a persona actuation path is imperative, not a "
                            "situation lifecycle"},
    },
    # homer — the HOMER diagnostic sub-agent: read-only (`actuating: False`),
    # its tool set is entirely observational. It investigates and reports a root
    # cause to its parent; it never actuates, raises no lifecycle and publishes
    # no actuation event. Every contract is therefore N/A-by-design — the same
    # shape as a detection path's actuation cells, extended to the whole row
    # because homer isn't even a situation path. Kept as an explicit all-N/A row
    # so the census records this profile as non-actuating by design.
    "homer": {
        "module": "agent.py",
        "contracts": {},
        "na": {
            "event": "read-only diagnostic sub-agent (actuating: False) — it "
                     "raises no lifecycle and publishes no actuation event",
            "world_model": "read-only — it takes no action, so there is no "
                           "pre-action context to snapshot",
            "situation": "read-only diagnostic sub-agent — not a situation "
                         "lifecycle",
            "authority": "read-only diagnostic sub-agent — it observes and "
                         "reports, it never actuates, so there is no authority "
                         "decision",
            "plan": "read-only — produces no actuation plan",
            "verify": "nothing is actuated by this path — nothing to verify",
            "outcome": "nothing is actuated — no actuator outcome record",
        },
    },
}


# ── governance gate (8.34.0, the audit's "rule I would add now") ─────────────
# Every agent tool that can cause a consequential action on the home must be a
# declared path above, so it cannot silently bypass the kernel coverage matrix.
# This is the allowlist of tools that are NOT home actuators — read-only,
# informational, memory/preferences, scheduling, goal/suggestion bookkeeping,
# alert acknowledgement. A tool in `_TOOL_MAP` (agent.py) that is neither here
# nor in `_PATHS` fails the gate: classify it (add it here) or wire it (add it
# as a path). A NEW actuator tool therefore cannot land uncounted.
_NON_ACTUATOR_TOOLS: frozenset[str] = frozenset({
    # read / informational
    "get_entity_state", "search_entities", "get_area_devices", "get_home_summary",
    "cognitive_status", "connectivity_status", "system_diagnostics",
    "security_audit",
    "energy_status", "hazard_report", "activity_history", "weather_forecast",
    "wellbeing_context", "root_cause", "web_research", "calendar_agenda",
    "read_email", "look_at_camera", "who_do_you_see", "where_last_seen",
    "usual_times", "search_documents", "ingest_documents",
    # memory / preferences
    "remember", "ignore_entity", "unignore_entity",
    # scheduling / goals / suggestions (bookkeeping, not home actuation).
    # create_automation INSTALLS an HA automation (config write + reload), the
    # same class of action as approve_suggestion — it does not itself command a
    # device; the automation it writes runs through Home Assistant's own engine
    # and is visible/editable there. It is, if anything, more governed than
    # approve_suggestion: every entity_id and service is validated against the
    # live home before install.
    "schedule_followup", "manage_followups", "create_goal", "update_goal",
    "manage_goals", "review_suggestions", "approve_suggestion",
    "create_automation", "dismiss_suggestion", "manage_autonomy",
    # alert / intrusion acknowledgement (state of an alert, not a device)
    "dismiss_intrusion", "acknowledge_alert",
})


def _tool_map_names(agent_src: str) -> list[str]:
    """The string keys of ``_TOOL_MAP`` in agent.py, parsed from source (no
    import, so the gate stays stdlib-only like the rest of this script)."""
    import ast
    tree = ast.parse(agent_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_TOOL_MAP" \
                        and isinstance(node.value, ast.Dict):
                    return [k.value for k in node.value.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def undeclared_actuator_tools() -> list[str]:
    """Problems: an agent tool that is neither declared as a kernel-coverage path
    nor classified as a non-actuator — i.e. a consequential path that could
    bypass the matrix. Empty when the gate is satisfied."""
    comp = _component_dir()
    agent = comp / "agent.py"
    if not agent.exists():
        return ["agent.py not found — cannot check actuator-path coverage"]
    names = _tool_map_names(agent.read_text())
    if not names:
        return ["could not parse _TOOL_MAP from agent.py"]
    problems = []
    for name in names:
        if name in _PATHS or name in _NON_ACTUATOR_TOOLS:
            continue
        problems.append(
            f"tool '{name}' is neither a declared coverage path nor an "
            f"allowlisted non-actuator — classify it in _NON_ACTUATOR_TOOLS "
            f"or declare it in _PATHS (governance rule, 8.34.0)")
    return problems


def _component_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "jarvis"


def _stage(path: str, contract: str) -> str:
    return _PATHS[path]["contracts"].get(contract, {}).get("stage", "none")


def scan() -> dict[str, dict]:
    """Return per-path per-contract stages (the declared matrix)."""
    return {
        path: {c: _stage(path, c) for c in _CONTRACTS}
        for path in _PATHS
    }


def coverage_pct(rows: dict[str, dict] | None = None,
                 weights: dict[str, float] | None = None) -> float:
    rows = rows or scan()
    w = weights or _WEIGHT
    total = len(_PATHS) * len(_CONTRACTS)
    got = sum(w.get(stage, 0.0)
              for contracts in rows.values() for stage in contracts.values())
    return round(100.0 * got / total, 1) if total else 0.0


def strict_coverage_pct(rows: dict[str, dict] | None = None) -> float:
    """The honest figure: observe-only shadow cells earn no governance credit."""
    return coverage_pct(rows, _STRICT_WEIGHT)


def _is_na(path: str, contract: str) -> bool:
    """True if ``contract`` is declared N/A-by-design for ``path``."""
    return contract in _PATHS[path].get("na", {})


def na_ledger() -> list[tuple[str, str, str]]:
    """``(path, contract, reason)`` for every N/A-by-design cell, in matrix order.

    Rendered into the doc so every exclusion from the applicable denominator is
    visible and reviewable — the mechanism that stops "N/A" from becoming a place
    to hide a real gap.
    """
    out: list[tuple[str, str, str]] = []
    for path, spec in _PATHS.items():
        na = spec.get("na", {})
        for contract in _CONTRACTS:
            if contract in na:
                out.append((path, contract, na[contract]))
    return out


def applicable_cell_count() -> int:
    """The honest denominator: cells that *can* be wired (total minus N/A)."""
    na = sum(len(spec.get("na", {})) for spec in _PATHS.values())
    return len(_PATHS) * len(_CONTRACTS) - na


def applicable_coverage_pct(rows: dict[str, dict] | None = None) -> float:
    """The headline figure: honest credit (shadow earns 0) over only the
    *applicable* cells. N/A-by-design cells are excluded from both the numerator
    and the denominator, so the number measures progress against what can
    actually be wired — not diluted by cells that are structurally meaningless
    (every one of which is listed, with its reason, in :func:`na_ledger`).
    """
    rows = rows or scan()
    denom = applicable_cell_count()
    got = sum(_STRICT_WEIGHT.get(stage, 0.0)
              for path, contracts in rows.items()
              for contract, stage in contracts.items()
              if not _is_na(path, contract))
    return round(100.0 * got / denom, 1) if denom else 0.0


def _cell_glyph(path: str, contract: str, rows: dict[str, dict]) -> str:
    """The matrix glyph for one cell: the N/A marker, else the stage icon."""
    if _is_na(path, contract):
        return _NA_ICON
    return _ICON[rows[path][contract]]


def drift() -> list[str]:
    """Problems: a declared non-``none`` cell whose evidence is absent from the
    path's source, an unknown contract name, or a missing module file."""
    comp = _component_dir()
    problems: list[str] = []
    for path, spec in _PATHS.items():
        src_path = comp / spec["module"]
        text = src_path.read_text() if src_path.exists() else None
        if text is None:
            problems.append(f"{path}: module {spec['module']} not found")
            continue
        for contract, cell in spec["contracts"].items():
            if contract not in _CONTRACTS:
                problems.append(f"{path}: unknown contract '{contract}'")
                continue
            if cell["stage"] != "none":
                ev = cell.get("evidence")
                if not ev or ev not in text:
                    problems.append(
                        f"{path}.{contract}: declared '{cell['stage']}' but "
                        f"evidence {ev!r} not found in {spec['module']}")
        # N/A declarations: a cell is wired or N/A, never both; every N/A needs a
        # real reason — so "N/A" can't silently absorb an un-wired gap.
        for contract, reason in spec.get("na", {}).items():
            if contract not in _CONTRACTS:
                problems.append(f"{path}: unknown N/A contract '{contract}'")
            if contract in spec["contracts"]:
                problems.append(
                    f"{path}.{contract}: declared both wired and N/A — a cell is "
                    "one or the other")
            if not (isinstance(reason, str) and reason.strip()):
                problems.append(
                    f"{path}.{contract}: N/A needs a non-empty reason")
    return problems


def render_markdown(rows: dict[str, dict] | None = None) -> str:
    rows = rows or scan()
    header = "| Path | " + " | ".join(_CONTRACTS) + " |"
    sep = "| --- " * (len(_CONTRACTS) + 1) + "|"
    lines = [header, sep]
    for path in _PATHS:
        cells = " | ".join(_cell_glyph(path, c, rows) for c in _CONTRACTS)
        lines.append(f"| `{path}` | {cells} |")
    lines.append("")
    total = len(_PATHS) * len(_CONTRACTS)
    applicable = applicable_cell_count()
    lines.append(
        f"**Kernel coverage (applicable): {applicable_coverage_pct(rows)}%** — "
        f"honest credit (observe-only shadow earns 0; only parity + full count) "
        f"over the {applicable} cells that can actually be wired. The "
        f"{total - applicable} N/A-by-design cells (`{_NA_ICON}`, listed below) "
        "are excluded, not scored as gaps. **This is the number to move.**")
    lines.append("")
    lines.append(
        f"Across all {total} cells (N/A counted as un-wired): "
        f"{coverage_pct(rows)}% weighted / {strict_coverage_pct(rows)}% honest. "
        f"Legend: `·` none  `◐` shadow  `◑` parity  `●` full  `{_NA_ICON}` n/a.")
    lines.append("")
    lines.append(
        "**N/A-by-design cells** — excluded from the applicable denominator "
        "because the contract is structurally meaningless for the path. Every "
        "exclusion is listed with its reason so a real gap can't hide here:")
    lines.append("")
    for path, contract, reason in na_ledger():
        lines.append(f"- `{path}` · **{contract}** — {reason}")
    return "\n".join(lines)


_DOC_BEGIN = "<!-- BEGIN kernel-coverage"
_DOC_END = "<!-- END kernel-coverage -->"


def _coverage_doc_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "KERNEL_COVERAGE.md"


def doc_block_drift(rows: dict[str, dict] | None = None) -> list[str]:
    """Problem if KERNEL_COVERAGE.md's embedded matrix + figures diverge from the
    generator — so a stale hand-edit, or a figure left un-regenerated after the
    matrix moved, fails CI instead of silently disagreeing with the doc."""
    rows = rows or scan()
    path = _coverage_doc_path()
    try:
        text = path.read_text()
    except OSError:
        return [f"{path.name} not found"]
    if _DOC_BEGIN not in text or _DOC_END not in text:
        return [f"{path.name}: kernel-coverage markers missing"]
    after_begin = text.split(_DOC_BEGIN, 1)[1].split("-->", 1)[1]
    embedded = after_begin.split(_DOC_END, 1)[0].strip()
    if embedded != render_markdown(rows).strip():
        return [f"{path.name}: embedded coverage block is stale — regenerate it "
                "with `python3 scripts/kernel_coverage.py --markdown`"]
    return []


def render_table(rows: dict[str, dict] | None = None) -> str:
    rows = rows or scan()
    width = max(len(p) for p in _PATHS)
    out = ["  " + "path".ljust(width) + "  " + " ".join(c[:4] for c in _CONTRACTS)]
    for path in _PATHS:
        cells = " ".join(_cell_glyph(path, c, rows).center(4) for c in _CONTRACTS)
        out.append("  " + path.ljust(width) + "  " + cells)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    rows = scan()
    if "--markdown" in argv:
        print(render_markdown(rows))
    else:
        print("Behavioral kernel-coverage "
              f"(· none  ◐ shadow  ◑ parity  ● full  {_NA_ICON} n/a)\n")
        print(render_table(rows))
        total = len(_PATHS) * len(_CONTRACTS)
        print(f"\n  headline: {applicable_coverage_pct(rows)}% honest over the "
              f"{applicable_cell_count()} applicable cells "
              f"({total - applicable_cell_count()} of {total} are N/A by design)")
        print(f"  all cells: {coverage_pct(rows)}% weighted / "
              f"{strict_coverage_pct(rows)}% honest")

    if "--check" in argv:
        problems = drift() + doc_block_drift(rows)
        gate = undeclared_actuator_tools()
        if problems or gate:
            if problems:
                print("\nDRIFT:", file=sys.stderr)
                for p in problems:
                    print(f"  - {p}", file=sys.stderr)
            if gate:
                print("\nUNDECLARED ACTUATOR PATH:", file=sys.stderr)
                for p in gate:
                    print(f"  - {p}", file=sys.stderr)
            return 1
        print("\ncoverage matrix OK — every declared stage has live evidence")
        print("governance gate OK — every consequential agent tool is a "
              "declared path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
