#!/usr/bin/env python3
"""Documentation ↔ implementation consistency gate (MCU Phase H, H0).

The external gap-audit flagged a governance hazard: the *executable* architecture
(``scripts/kernel_adoption.py`` — the single source of truth for each primitive's
adoption stage) can advance while the *specification* (``docs/JARVIS_CONSTITUTION.md``)
and the rendered matrix (``KERNEL_ADOPTION.md``) still describe an older reality.
That is exactly the drift the kernel governance is meant to prevent — and it had
already happened once (the Constitution said "authority is log-only … no primitive
at enforce" after authority had been flipped to enforce).

This script makes the docs *generated* from ``kernel_adoption`` and fails CI when
they disagree:

  * the ``KERNEL_ADOPTION.md`` matrix block must equal ``kernel_adoption --markdown``;
  * the ``JARVIS_CONSTITUTION.md`` stage-ledger block must equal the stage table
    generated here from ``kernel_adoption._DECLARED``.

    python3 scripts/kernel_docs_sync.py            # show status
    python3 scripts/kernel_docs_sync.py --check     # + exit non-zero on drift
    python3 scripts/kernel_docs_sync.py --write      # rewrite both blocks in place

Keep ``_DECLARED`` honest; these docs render from it, so a stage change updates
both docs in one place and CI refuses a mismatch.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kernel_adoption as KA  # noqa: E402

_ADOPTION_BEGIN = "<!-- BEGIN kernel-adoption (python3 scripts/kernel_adoption.py --markdown) -->"
_ADOPTION_END = "<!-- END kernel-adoption -->"
_LEDGER_BEGIN = "<!-- BEGIN kernel-stage-ledger (python3 scripts/kernel_docs_sync.py --write) -->"
_LEDGER_END = "<!-- END kernel-stage-ledger -->"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def _adoption_md_path() -> pathlib.Path:
    return _repo_root() / "KERNEL_ADOPTION.md"


def _constitution_path() -> pathlib.Path:
    return _repo_root() / "docs" / "JARVIS_CONSTITUTION.md"


def generate_adoption_block() -> str:
    """The canonical KERNEL_ADOPTION matrix, rendered from the live scan."""
    return KA.render_markdown(KA.scan())


def generate_ledger_block() -> str:
    """The Constitution's enforcement ledger — every primitive and its declared
    stage, generated from ``kernel_adoption._DECLARED`` so the constitution can
    never again claim a stage the implementation has moved past."""
    rows = KA.scan()
    lines = ["| Primitive | Stage |", "| --- | --- |"]
    for prim in sorted(rows):
        stage = rows[prim]["stage"]
        icon = KA._STAGE_ICON.get(stage, "?")
        lines.append(f"| `{prim}` | {icon} {stage} |")
    return "\n".join(lines)


def _extract_block(text: str, begin: str, end: str) -> str | None:
    try:
        i = text.index(begin) + len(begin)
        j = text.index(end, i)
    except ValueError:
        return None
    return text[i:j].strip("\n")


def _replace_block(text: str, begin: str, end: str, body: str) -> str:
    i = text.index(begin) + len(begin)
    j = text.index(end, i)
    return text[:i] + "\n" + body + "\n" + text[j:]


def _check_file(path: pathlib.Path, begin: str, end: str, expected: str,
                label: str) -> list[str]:
    problems: list[str] = []
    if not path.exists():
        return [f"{label}: {path} does not exist"]
    text = path.read_text()
    found = _extract_block(text, begin, end)
    if found is None:
        problems.append(f"{label}: missing generated block markers in {path.name}")
    elif found.strip() != expected.strip():
        problems.append(
            f"{label}: {path.name} block is STALE — regenerate with "
            f"`python3 scripts/kernel_docs_sync.py --write`")
    return problems


def check() -> list[str]:
    return (
        _check_file(_adoption_md_path(), _ADOPTION_BEGIN, _ADOPTION_END,
                    generate_adoption_block(), "adoption-matrix")
        + _check_file(_constitution_path(), _LEDGER_BEGIN, _LEDGER_END,
                      generate_ledger_block(), "constitution-ledger")
    )


def write() -> None:
    ap = _adoption_md_path()
    ap.write_text(_replace_block(ap.read_text(), _ADOPTION_BEGIN, _ADOPTION_END,
                                 generate_adoption_block()))
    cp = _constitution_path()
    cp.write_text(_replace_block(cp.read_text(), _LEDGER_BEGIN, _LEDGER_END,
                                 generate_ledger_block()))
    print("wrote adoption matrix + constitution ledger from kernel_adoption._DECLARED")


def main(argv: list[str]) -> int:
    if "--write" in argv:
        write()
        return 0
    print("Doc ↔ implementation consistency (constitution ledger + adoption matrix)\n")
    print(generate_ledger_block())
    if "--check" in argv:
        problems = check()
        if problems:
            print("\nDOC DRIFT:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("\ndocs OK — constitution + adoption matrix match kernel_adoption")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
