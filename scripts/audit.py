#!/usr/bin/env python3
"""JARVIS pre-release audit.

Two checks that the old ast.parse gate missed:

  1. COMPILE  — py_compile (real bytecode compile) of every module. Unlike
     ast.parse, this enforces __future__ positioning and other compile-stage
     rules, matching how Home Assistant actually imports the integration.

  2. IMPORTS  — resolves every relative import (top-level AND lazy/nested)
     across the package and verifies each imported name actually exists in the
     target module (or its __all__). Catches wrong relative levels
     (e.g. `from .automation` inside a subpackage that needed `..automation`)
     and stale exports — failures that are invisible to per-file syntax checks.

Run from anywhere:  python3 scripts/audit.py [component_dir]
Default component dir: jarvis_assistant/jarvis_component
Exit code 0 = clean, 1 = problems found.
"""
from __future__ import annotations

import ast
import pathlib
import py_compile
import sys


def _component_dir() -> pathlib.Path:
    if len(sys.argv) > 1:
        return pathlib.Path(sys.argv[1])
    here = pathlib.Path(__file__).resolve().parent
    return here.parent / "jarvis_assistant" / "jarvis_component"


def _modules(root: pathlib.Path) -> list[pathlib.Path]:
    return [f for f in root.rglob("*.py") if "__pycache__" not in str(f)]


def compile_gate(files: list[pathlib.Path]) -> list[str]:
    problems = []
    for f in files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as exc:
            problems.append(f"{f}: {str(exc).splitlines()[0]}")
    return problems


# ── relative-import resolution ────────────────────────────────────────────────
_PARSE_CACHE: dict[pathlib.Path, ast.Module] = {}


def _parse(p: pathlib.Path) -> ast.Module:
    if p not in _PARSE_CACHE:
        _PARSE_CACHE[p] = ast.parse(p.read_text())
    return _PARSE_CACHE[p]


def _toplevel_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()

    def add_target(tg: ast.AST) -> None:
        if isinstance(tg, ast.Name):
            names.add(tg.id)
        elif isinstance(tg, (ast.Tuple, ast.List)):
            for e in tg.elts:
                add_target(e)

    def scan(body: list[ast.stmt]) -> None:
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    add_target(t)
            elif isinstance(n, ast.AnnAssign):
                if isinstance(n.target, ast.Name):
                    names.add(n.target.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    names.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.Try):
                scan(n.body)
                for h in n.handlers:
                    scan(h.body)
                scan(n.orelse)
                scan(n.finalbody)
            elif isinstance(n, ast.If):
                scan(n.body)
                scan(n.orelse)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                scan(n.body)

    scan(tree.body)
    return names


def _dunder_all(tree: ast.Module) -> set[str] | None:
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    try:
                        return set(ast.literal_eval(n.value))
                    except Exception:
                        return None
    return None


def _resolve(curfile: pathlib.Path, level: int, module: str | None) -> pathlib.Path | None:
    base = curfile.parent
    for _ in range(level - 1):
        base = base.parent
    if module:
        parts = module.split(".")
        as_mod = base.joinpath(*parts).with_suffix(".py")
        as_pkg = base.joinpath(*parts, "__init__.py")
        if as_mod.exists():
            return as_mod
        if as_pkg.exists():
            return as_pkg
        return None
    return base / "__init__.py"


def import_gate(files: list[pathlib.Path]) -> list[str]:
    problems = []
    for f in files:
        for node in ast.walk(_parse(f)):
            if not (isinstance(node, ast.ImportFrom) and node.level and node.level >= 1):
                continue
            if node.module is None:  # from . import x, y
                base = f.parent
                for _ in range(node.level - 1):
                    base = base.parent
                for a in node.names:
                    nm = a.name
                    if (base / f"{nm}.py").exists() or (base / nm / "__init__.py").exists():
                        continue
                    ini = base / "__init__.py"
                    if ini.exists() and nm in _toplevel_names(_parse(ini)):
                        continue
                    problems.append(f"{f}: from {'.' * node.level} import {nm} → unresolved")
            else:
                target = _resolve(f, node.level, node.module)
                if target is None:
                    problems.append(f"{f}: from {'.' * node.level}{node.module} → module not found")
                    continue
                tt = _parse(target)
                allset = _dunder_all(tt)
                valid = (allset if allset is not None else set()) | _toplevel_names(tt)
                for a in node.names:
                    if a.name == "*":
                        continue
                    if a.name not in valid:
                        problems.append(
                            f"{f}: from {'.' * node.level}{node.module} import {a.name} → not found in {target.name}"
                        )
    return problems


def main() -> int:
    root = _component_dir()
    if not root.is_dir():
        print(f"audit: component dir not found: {root}")
        return 1
    files = _modules(root)

    compile_problems = compile_gate(files)
    import_problems = import_gate(files)

    print(f"COMPILE  : {'OK (' + str(len(files)) + ' modules)' if not compile_problems else 'FAIL'}")
    for p in compile_problems:
        print(f"  ✗ {p}")
    print(f"IMPORTS  : {'OK' if not import_problems else 'FAIL'}")
    for p in import_problems:
        print(f"  ✗ {p}")

    if compile_problems or import_problems:
        print("\nAUDIT FAILED")
        return 1
    print("\nAUDIT CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
