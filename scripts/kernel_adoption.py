#!/usr/bin/env python3
"""Kernel adoption / bypass matrix (kernel hardening H2, docs/KERNEL_PLAN.md).

The migration is a strangler fig: each kernel primitive lands pure and additive,
then the live integration is wired onto it one caller at a time (shadow → parity
→ enforce). The risk the external audit flagged is *silent drift* — a primitive
that exists but nothing live consults, so the kernel looks adopted on paper while
the legacy path still owns the decision.

This script makes that visible. It scans the live integration (everything under
``custom_components/jarvis`` except ``kernel/`` itself and tests) for references
to each kernel primitive, and compares what it finds against the *declared*
adoption stage below. ``--check`` turns a regression into a non-zero exit: a
primitive we claim is wired that no live module imports.

    python3 scripts/kernel_adoption.py            # print the matrix
    python3 scripts/kernel_adoption.py --check     # + fail on drift
    python3 scripts/kernel_adoption.py --markdown  # emit the KERNEL_ADOPTION table

Keep ``_DECLARED`` honest: it is the single source of truth the doc renders from,
and the check only protects stages we have actually reached.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

# ── declared adoption stage per primitive ──────────────────────────────────────
# stage ∈ {"pure", "shadow", "parity", "enforce"}
#   pure    — primitive exists, nothing live wired yet (kernel-only / tests).
#   shadow  — a live module mirrors into it but ignores its output.
#   parity  — a live module computes it alongside the legacy decision, logs
#             agreement, still obeys legacy (log-only).
#   enforce — the kernel decision is authoritative for at least one live path.
# "owners" are the live modules (basename, no .py) expected to reference it once
# the stage is > pure. The check fails if a non-pure primitive has no live ref.
_DECLARED: dict[str, dict] = {
    "event":        {"stage": "parity",  "owners": ["actuation", "camera", "events", "observer", "proactive_audio"]},
    # persistence is an internal seam consumed by other kernel modules (ledger),
    # not by live callers directly — so "pure" from a live-adoption standpoint.
    "persistence":  {"stage": "pure",    "owners": []},
    "correlation":  {"stage": "shadow",  "owners": ["actuation", "decision_record", "observer", "proactive_audio"]},
    "event_bus":    {"stage": "shadow",  "owners": ["__init__"]},
    "ledger":       {"stage": "shadow",  "owners": ["__init__"]},
    # world_model: control_device reads its pre-action context snapshot through
    # the facade (8.30.0, MCU Phase A); cognition (C1), the intrusion-safety
    # presence reads (C2), and the home-summary / briefing context builders (C3)
    # read the world through the facade too — the canonical context authority.
    # ENFORCE (C4, 8.46.0): the facade is *authoritative* on migrated paths, not
    # merely consulted — home_state._build_summary and agent._exec_home_summary
    # read it with no raw fallback, and test_c4_worldmodel_authoritative proves
    # the facade value wins over raw HA state on every migrated read (a regression
    # to a raw sweep fails those tests). Enforce = authoritative on ≥1 live path;
    # the raw sources remain underneath only as a failure fallback for the
    # safety/best-effort callers, and un-migrated context reads elsewhere are
    # still legacy (tracked by the per-release migration, not this stage).
    "world_model":  {"stage": "enforce", "owners": ["actuation", "cognitive_core",
                                                     "home_state", "agent",
                                                     "proactive_briefing"]},
    "situation":    {"stage": "parity",  "owners": ["intrusion", "hazard_situation",
                                                     "delivery_situation"]},
    "authority":    {"stage": "parity",  "owners": ["authority_bridge"]},
    # control_device expresses each actuation as a one-step Plan in shadow
    # (actuation.plan_shadow); execute_plan routes each step's execution THROUGH
    # the planner (agent, aexecute_plan) — B4, so parity, not shadow.
    "plan":         {"stage": "parity",  "owners": ["actuation", "agent"]},
    # beliefs: the cognitive_status tool surfaces a belief snapshot seeded from
    # knowledge confidences via WorldModel.beliefs() + the kernel identity
    # assertion (E1) — shadow: read-only introspection, no decision consumes it.
    "beliefs":      {"stage": "shadow",  "owners": ["agent"]},
    "attention":    {"stage": "pure",    "owners": []},
    "router":       {"stage": "pure",    "owners": []},
    "causal":       {"stage": "pure",    "owners": []},
    "priority":     {"stage": "pure",    "owners": []},
    "loop_detect":  {"stage": "pure",    "owners": []},
    "journal":      {"stage": "pure",    "owners": []},
    "budget":       {"stage": "pure",    "owners": []},
    # control_device builds an ActuatorRequest (with expected_outcome) and the
    # verify step produces the canonical ActuatorOutcome — 8.31.0, MCU Phase A.
    "actuator":     {"stage": "parity",  "owners": ["actuation", "agent"]},
}

_STAGE_ICON = {"pure": "·", "shadow": "◐", "parity": "◑", "enforce": "●"}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def _component_dir() -> pathlib.Path:
    return _repo_root() / "custom_components" / "jarvis"


def _kernel_primitives(kernel_dir: pathlib.Path) -> list[str]:
    return sorted(
        p.stem
        for p in kernel_dir.glob("*.py")
        if p.stem != "__init__" and "__pycache__" not in str(p)
    )


def _live_modules(comp: pathlib.Path) -> list[pathlib.Path]:
    out = []
    for p in comp.rglob("*.py"):
        s = str(p)
        if "__pycache__" in s:
            continue
        # Skip the kernel package itself and any test trees.
        rel = p.relative_to(comp)
        if rel.parts and rel.parts[0] == "kernel":
            continue
        if "tests" in rel.parts:
            continue
        out.append(p)
    return out


def _module_symbols(kernel_dir: pathlib.Path) -> dict[str, str]:
    """Map every public symbol a kernel module exports → its module name.

    So that ``from .kernel import EventLedger`` in a live file counts as a
    reference to the ``ledger`` primitive, not just a bare ``kernel.ledger``.
    """
    index: dict[str, str] = {}
    for p in kernel_dir.glob("*.py"):
        if p.stem == "__init__" or "__pycache__" in str(p):
            continue
        tree = ast.parse(p.read_text())
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    index[node.name] = p.stem
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        index[tgt.id] = p.stem
    return index


def _referenced_primitives(text: str, sym_index: dict[str, str],
                           primitives: list[str]) -> set[str]:
    """Which kernel primitives a live module references, by module name,
    dotted use, or any exported symbol imported from ``.kernel``."""
    found: set[str] = set()
    for prim in primitives:
        pats = (
            rf"\bfrom\s+\.kernel\s+import\b[^\n]*\b{prim}\b",
            rf"\bfrom\s+\.kernel\.{prim}\b",
            rf"\bimport\s+[^\n]*\bkernel\.{prim}\b",
            rf"\bkernel\.{prim}\b",
        )
        if any(re.search(p, text) for p in pats):
            found.add(prim)
    # Symbols imported from the kernel package map back to their module.
    for m in re.finditer(r"from\s+\.kernel\s+import\s+\(?([^)\n]+)\)?", text):
        for name in re.split(r"[,\s]+", m.group(1)):
            name = name.strip().split(" as ")[0].strip()
            if name in sym_index:
                found.add(sym_index[name])
    return found


def scan() -> dict[str, dict]:
    comp = _component_dir()
    kernel_dir = comp / "kernel"
    primitives = _kernel_primitives(kernel_dir)
    sym_index = _module_symbols(kernel_dir)
    live = _live_modules(comp)
    live_refs = {
        p: _referenced_primitives(p.read_text(), sym_index, primitives)
        for p in live
    }

    def _label(path: pathlib.Path) -> str:
        rel = path.relative_to(comp)
        # Disambiguate package __init__ files by their directory.
        if path.stem == "__init__":
            return "__init__" if rel.parent == pathlib.Path(".") else f"{rel.parent}/__init__"
        return path.stem

    rows: dict[str, dict] = {}
    for prim in primitives:
        users = sorted(
            _label(p) for p, refs in live_refs.items() if prim in refs
        )
        decl = _DECLARED.get(prim, {"stage": "pure", "owners": []})
        rows[prim] = {
            "stage": decl["stage"],
            "declared_owners": decl.get("owners", []),
            "live_users": users,
        }
    return rows


def drift(rows: dict[str, dict]) -> list[str]:
    """Problems: a non-pure primitive with no live reference, or an undeclared
    primitive (one on disk missing from ``_DECLARED``)."""
    problems = []
    for prim, row in rows.items():
        if prim not in _DECLARED:
            problems.append(f"{prim}: on disk but missing from _DECLARED")
            continue
        if row["stage"] != "pure" and not row["live_users"]:
            problems.append(
                f"{prim}: declared '{row['stage']}' but no live module references it"
            )
    for prim in _DECLARED:
        if prim not in rows:
            problems.append(f"{prim}: declared but no kernel/{prim}.py on disk")
    return problems


def render_markdown(rows: dict[str, dict]) -> str:
    lines = [
        "| Primitive | Stage | Live callers |",
        "| --- | --- | --- |",
    ]
    for prim in sorted(rows):
        row = rows[prim]
        icon = _STAGE_ICON.get(row["stage"], "?")
        users = ", ".join(f"`{u}`" for u in row["live_users"]) or "—"
        lines.append(f"| `{prim}` | {icon} {row['stage']} | {users} |")
    return "\n".join(lines)


def render_table(rows: dict[str, dict]) -> str:
    out = []
    width = max(len(p) for p in rows)
    for prim in sorted(rows):
        row = rows[prim]
        icon = _STAGE_ICON.get(row["stage"], "?")
        users = ", ".join(row["live_users"]) or "—"
        out.append(f"  {icon} {prim.ljust(width)}  {row['stage']:<7}  {users}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    rows = scan()
    if "--markdown" in argv:
        print(render_markdown(rows))
    else:
        print("Kernel adoption matrix "
              "(· pure  ◐ shadow  ◑ parity  ● enforce)\n")
        print(render_table(rows))

    if "--check" in argv:
        problems = drift(rows)
        if problems:
            print("\nDRIFT:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("\nadoption OK — every declared stage has a live caller")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
