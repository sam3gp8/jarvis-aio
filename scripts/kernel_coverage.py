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
fiction). It prints a single honest coverage percentage.

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

# How much each stage counts toward coverage.
_WEIGHT = {"none": 0.0, "shadow": 1 / 3, "parity": 2 / 3, "full": 1.0}
_ICON = {"none": "·", "shadow": "◐", "parity": "◑", "full": "●"}


# Behaviour-bearing paths → the module file that carries the path, and the
# declared stage + evidence per contract. A contract absent from "contracts"
# is "none" (the path does not pass through that kernel contract yet).
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
    },
    # Recorded as one explicit N-step kernel Plan (shadow) and each executed
    # target routed through the actuation envelope (B3): WorldModel context +
    # actuation event. Fire-and-forget, so no per-device verify/outcome.
    "bulk_control": {
        "module": "agent.py",
        "contracts": {
            "world_model": {"stage": "parity", "evidence": "actuation.context"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            "plan": {"stage": "shadow", "evidence": "bulk_plan"},
        },
    },
    # Each step's execution routes through the kernel planner
    # (aexecute_plan) and publishes an actuation event (B4). Full plan
    # ownership of execution; steps are arbitrary services, so no single
    # expected end-state (no verify/outcome cell).
    "execute_plan": {
        "module": "agent.py",
        "contracts": {
            "plan": {"stage": "full", "evidence": "aexecute_plan"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
        },
    },
    # Activates scenes/scripts/automations. Routed through the shared actuation
    # envelope (B1): WorldModel context, actuation event, shadow plan. No
    # deterministic end-state, so no verify/outcome; no confirm-gate, so no
    # authority cell.
    "run_scene_or_script": {
        "module": "agent.py",
        "contracts": {
            "world_model": {"stage": "parity", "evidence": "actuation.context"},
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            "plan": {"stage": "shadow", "evidence": "actuation.plan_shadow"},
        },
    },
    # Mode directive whose entry applies a mode scene (mode_scene). Routed
    # through the actuation envelope (B2): actuation event + shadow plan for the
    # mode change. No single-entity context (so no world_model) and no
    # deterministic end-state (so no verify/outcome); the target is the mode.
    "set_mode": {
        "module": "agent.py",
        "contracts": {
            "event": {"stage": "parity", "evidence": "actuation.emit_event"},
            "plan": {"stage": "shadow", "evidence": "actuation.plan_shadow"},
        },
    },
    # Intrusion mirrors its lifecycle into the kernel Situation state machine AND
    # (D1) verifies the mirrored state agrees with the legacy verdict, log-only —
    # genuine parity, not a blind shadow copy. Evidence is the parity check.
    "intrusion": {
        "module": "intrusion.py",
        "contracts": {
            "situation": {"stage": "parity", "evidence": "_record_parity"},
            # D4: each lifecycle transition publishes a canonical situation
            # JarvisEvent on the bus (parity — enters the stream + ledger, no
            # consumer reacts yet).
            "event": {"stage": "parity", "evidence": "publish_situation"},
        },
    },
    # The in-home freeze hazard lifecycle (SafetyManager._check_freeze:
    # warning → critical → cleared) is mirrored into a kernel Situation
    # (kind="hazard") and its agreement verified (D2), log-only parity. Adding
    # this path honestly surfaces hazards as a tracked behaviour path — the only
    # cell wired so far is situation, so it (slightly) dilutes the coverage %.
    "hazard": {
        "module": "hazard_situation.py",
        "contracts": {
            "situation": {"stage": "parity", "evidence": "_record_parity"},
            "event": {"stage": "parity", "evidence": "publish_situation"},
        },
    },
    # The per-camera package delivery lifecycle (package_monitor: delivered →
    # removed) is mirrored into a kernel Situation (kind="delivery") and its
    # agreement verified (D3), log-only parity. Another tracked behaviour path.
    "delivery": {
        "module": "delivery_situation.py",
        "contracts": {
            "situation": {"stage": "parity", "evidence": "_record_parity"},
            "event": {"stage": "parity", "evidence": "publish_situation"},
        },
    },
    "goals": {"module": "goals.py", "contracts": {}},
    "proactive": {"module": "proactive_audio.py", "contracts": {}},
    "friday": {"module": "agent.py", "contracts": {}},
    "homer": {"module": "agent.py", "contracts": {}},
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
    "energy_status", "hazard_report", "activity_history", "weather_forecast",
    "wellbeing_context", "root_cause", "web_research", "calendar_agenda",
    "read_email", "look_at_camera", "who_do_you_see", "where_last_seen",
    "search_documents", "ingest_documents",
    # memory / preferences
    "remember", "ignore_entity", "unignore_entity",
    # scheduling / goals / suggestions (bookkeeping, not home actuation)
    "schedule_followup", "manage_followups", "create_goal", "update_goal",
    "manage_goals", "review_suggestions", "approve_suggestion",
    "dismiss_suggestion", "manage_autonomy",
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


def coverage_pct(rows: dict[str, dict] | None = None) -> float:
    rows = rows or scan()
    total = len(_PATHS) * len(_CONTRACTS)
    got = sum(_WEIGHT.get(stage, 0.0)
              for contracts in rows.values() for stage in contracts.values())
    return round(100.0 * got / total, 1) if total else 0.0


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
    return problems


def render_markdown(rows: dict[str, dict] | None = None) -> str:
    rows = rows or scan()
    header = "| Path | " + " | ".join(_CONTRACTS) + " |"
    sep = "| --- " * (len(_CONTRACTS) + 1) + "|"
    lines = [header, sep]
    for path in _PATHS:
        cells = " | ".join(f"{_ICON[rows[path][c]]}" for c in _CONTRACTS)
        lines.append(f"| `{path}` | {cells} |")
    lines.append("")
    lines.append(f"**Kernel coverage: {coverage_pct(rows)}%** "
                 "(· none ◐ shadow ◑ parity ● full)")
    return "\n".join(lines)


def render_table(rows: dict[str, dict] | None = None) -> str:
    rows = rows or scan()
    width = max(len(p) for p in _PATHS)
    out = ["  " + "path".ljust(width) + "  " + " ".join(c[:4] for c in _CONTRACTS)]
    for path in _PATHS:
        cells = " ".join(_ICON[rows[path][c]].center(4) for c in _CONTRACTS)
        out.append("  " + path.ljust(width) + "  " + cells)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    rows = scan()
    if "--markdown" in argv:
        print(render_markdown(rows))
    else:
        print("Behavioral kernel-coverage "
              "(· none  ◐ shadow  ◑ parity  ● full)\n")
        print(render_table(rows))
        print(f"\n  coverage: {coverage_pct(rows)}% of behaviour-bearing "
              "path × contract cells are kernel-wired")

    if "--check" in argv:
        problems = drift()
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
