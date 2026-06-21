#!/usr/bin/env python3
"""
CRAP (Change Risk Anti-Patterns) score calculator.

    CRAP(m) = comp(m)**2 * (1 - cov(m))**3 + comp(m)

where comp(m) is the cyclomatic complexity of method m and cov(m) is its line
coverage as a fraction in [0, 1]. High complexity combined with low coverage
yields a high CRAP score — code that is both convoluted and untested, i.e. risky
to change. Note that CRAP(m) >= comp(m) always, so a method can only score below
a threshold T if its complexity is below T regardless of coverage.

Complexity comes from `radon`; coverage from a `coverage.py` JSON report. Per
function, lines within [lineno, endline] are bucketed into executed vs missing to
derive that function's coverage.

Usage:
    python scripts/crap.py --coverage coverage.json [--threshold 4.0] FILE [FILE ...]

Exit code is non-zero if the average CRAP exceeds the threshold, so this doubles
as a CI gate.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def radon_functions(files: list[str]) -> dict[str, list[dict]]:
    """{abspath: [ {name, lineno, endline, complexity}, ... ]} for functions/methods."""
    proc = subprocess.run(
        ["radon", "cc", "-j", *files],
        capture_output=True, text=True, check=False,
    )
    data = json.loads(proc.stdout or "{}")
    out: dict[str, list[dict]] = {}
    for fname, blocks in data.items():
        if isinstance(blocks, dict) and blocks.get("error"):
            continue
        funcs = [b for b in blocks if b.get("type") in ("function", "method")]
        out[os.path.abspath(fname)] = funcs
    return out


def load_coverage(path: str) -> dict[str, tuple[set, set]]:
    """{abspath: (executed_lines, missing_lines)} from a coverage.py JSON report."""
    with open(path) as fh:
        data = json.load(fh)
    cov: dict[str, tuple[set, set]] = {}
    for fname, info in data.get("files", {}).items():
        cov[os.path.abspath(fname)] = (
            set(info.get("executed_lines", [])),
            set(info.get("missing_lines", [])),
        )
    return cov


def function_coverage(lo: int, hi: int, executed: set, missing: set) -> float:
    """Fraction of executable lines in [lo, hi] that were executed."""
    ex = sum(1 for ln in range(lo, hi + 1) if ln in executed)
    mi = sum(1 for ln in range(lo, hi + 1) if ln in missing)
    total = ex + mi
    if total == 0:
        return 1.0  # no executable lines (e.g. pure docstring/signature)
    return ex / total


def crap_score(complexity: int, coverage: float) -> float:
    return complexity ** 2 * (1.0 - coverage) ** 3 + complexity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute CRAP scores.")
    parser.add_argument("--coverage", default="coverage.json",
                        help="Path to coverage.py JSON report")
    parser.add_argument("--threshold", type=float, default=4.0,
                        help="Fail if average CRAP exceeds this")
    parser.add_argument("--crappy", type=float, default=30.0,
                        help="Per-function 'crappy' line (industry default 30)")
    parser.add_argument("files", nargs="+", help="Python files to score")
    args = parser.parse_args(argv)

    funcs = radon_functions(args.files)
    cov = load_coverage(args.coverage)

    rows = []
    for path, blocks in funcs.items():
        executed, missing = cov.get(path, (set(), set()))
        for b in blocks:
            c = function_coverage(b["lineno"], b["endline"], executed, missing)
            rows.append({
                "crap": crap_score(b["complexity"], c),
                "comp": b["complexity"],
                "cov": c,
                "name": b["name"],
                "file": os.path.basename(path),
                "line": b["lineno"],
            })

    if not rows:
        print("No functions found to score (check file paths and coverage report).")
        return 1

    rows.sort(key=lambda r: r["crap"], reverse=True)
    avg = sum(r["crap"] for r in rows) / len(rows)
    worst = rows[0]
    crappy = [r for r in rows if r["crap"] > args.crappy]

    name_w = max(len(f"{r['file']}:{r['name']}") for r in rows)
    print(f"{'FUNCTION':<{name_w}}  {'CX':>3}  {'COV':>6}  {'CRAP':>7}")
    print("-" * (name_w + 22))
    for r in rows:
        flag = "  <-- crappy" if r["crap"] > args.crappy else ""
        print(f"{r['file']}:{r['name']:<{name_w - len(r['file']) - 1}}  "
              f"{r['comp']:>3}  {r['cov'] * 100:>5.0f}%  {r['crap']:>7.2f}{flag}")
    print("-" * (name_w + 22))
    print(f"functions: {len(rows)}   average CRAP: {avg:.2f}   "
          f"worst: {worst['file']}:{worst['name']} ({worst['crap']:.2f})")
    print(f"functions over crappy line ({args.crappy:.0f}): {len(crappy)}")

    ok = avg <= args.threshold
    print(f"\n{'PASS' if ok else 'FAIL'}: average CRAP {avg:.2f} "
          f"{'<=' if ok else '>'} threshold {args.threshold:.1f}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
