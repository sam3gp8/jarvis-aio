#!/usr/bin/env python3
"""Phase R integration gate — the closed cognitive/agency loop stays wired.

Phase R (the Integration Gate) proves the architecture can **operate as one**:
one real pass runs the whole way round **perceive → predict → decide → act →
learn** as a single correlated, journal-reconstructable chain, rather than five
subsystems that merely share a process. This gate is the *enforce* rung's teeth —
a CI check that forbids **open-loop regressions**. It fails the build if the
integration loop primitive is removed, or stops being assembled from the live
tick, so the loop can never be silently un-wired while the rest of CI stays green.

Invariants (all must hold):
  1. ``kernel/integration.py`` defines the loop record + rolling tally: the five
     canonical ``STAGES``, ``LoopTrace``, ``from_flags`` and ``LoopAccumulator``.
  2. ``cognitive_core`` assembles a ``LoopTrace`` each tick from the five REAL
     stage signals — ``_emit_integration_loop_shadow`` is defined AND called with
     ``perceived=`` / ``predicted=`` / ``decided=`` / ``acted=`` / ``learned=``.
  3. ``kernel_adoption`` declares ``integration`` at stage ``enforce``.

Pure stdlib; no Home Assistant import. Run ``--check`` in CI; a bare run prints
each invariant's status.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_KERNEL = ROOT / "custom_components" / "jarvis" / "kernel" / "integration.py"
_CORE = ROOT / "custom_components" / "jarvis" / "cognitive_core.py"
_ADOPTION = ROOT / "scripts" / "kernel_adoption.py"

# The canonical loop and the keyword signals the live tick must supply.
_CANONICAL = ("perceive", "predict", "decide", "act", "learn")
_SIGNALS = ("perceived=", "predicted=", "decided=", "acted=", "learned=")


def _checks() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    # 1. the integration primitive
    try:
        itext = _KERNEL.read_text(encoding="utf-8")
    except Exception as exc:
        return [("integration module present", False,
                 f"cannot read {_KERNEL.name}: {exc}")]
    for sym in ("class LoopTrace", "class LoopAccumulator", "def from_flags",
                "STAGES"):
        out.append((f"integration defines {sym}", sym in itext, ""))
    out.append((
        "integration canonical stages",
        all((f'"{s}"' in itext or f"'{s}'" in itext) for s in _CANONICAL),
        "perceive→predict→decide→act→learn",
    ))

    # 2. cognitive_core assembles the loop each tick
    try:
        ctext = _CORE.read_text(encoding="utf-8")
    except Exception as exc:
        out.append(("cognitive_core readable", False, str(exc)))
        return out
    out.append(("tick defines the loop emitter",
                "def _emit_integration_loop_shadow(" in ctext, ""))
    # defined (def ...) AND called at least once more → wired into the tick.
    out.append(("tick calls the loop emitter",
                ctext.count("_emit_integration_loop_shadow(") >= 2, ""))
    out.append(("tick supplies all five stage signals",
                all(sig in ctext for sig in _SIGNALS),
                "perceived/predicted/decided/acted/learned"))

    # 3. the declared adoption stage
    try:
        spec = importlib.util.spec_from_file_location("_ka_check", _ADOPTION)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)   # type: ignore[union-attr]
        stage = mod._DECLARED.get("integration", {}).get("stage")
        out.append(("integration declared enforce", stage == "enforce",
                    f"stage={stage}"))
    except Exception as exc:
        out.append(("integration declared enforce", False, f"read failed: {exc}"))

    return out


def main(argv: list[str]) -> int:
    checks = _checks()
    failed = [c for c in checks if not c[1]]
    if "--check" not in argv:
        for name, ok, detail in checks:
            print(f"  {'ok ' if ok else 'XX '}{name}"
                  + (f" — {detail}" if detail else ""))
        print(f"\n{len(checks)} invariants, {len(failed)} failing")
    if "--check" in argv:
        if failed:
            print("OPEN-LOOP REGRESSION — the integration loop is no longer fully "
                  "wired:", file=sys.stderr)
            for name, _ok, detail in failed:
                print(f"  - {name}" + (f" ({detail})" if detail else ""),
                      file=sys.stderr)
            print("\nThe perceive→predict→decide→act→learn loop must stay assembled "
                  "in cognitive_core._tick via kernel.integration (Phase R "
                  "enforce). Re-wire it or, if the loop is being retired, update "
                  "this gate deliberately.", file=sys.stderr)
            return 1
        print(f"integration gate OK — the closed cognitive/agency loop is wired "
              f"end-to-end ({len(checks)} invariants, Phase R enforce)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
