#!/usr/bin/env python3
"""Phase AE certification gate — the observable scenario classes stay wired.

Phase AE (MCU Certification) is the systems-certification bar: *"does the complete
JARVIS behave as one?"*, expressed as a suite of **scenario classes** each meant to
run closed-loop and journal-reconstructable. Eight of the ten classes are observed
from their own live surfaces and fold into one shared ledger; two
(``conflicting_priorities``, ``cognitive_error``) are deliberately NOT observed yet,
because no honest live surface exists.

This gate is the *enforce* rung's teeth — a CI check that forbids a silent
**un-wiring** of the certification suite. It fails the build if the certification
primitive is removed, if any of the eight observable classes loses its live
observer, or if the dashboard accessor disappears — so the "behaves as one"
evidence can never quietly rot while the rest of CI stays green. It also keeps the
suite HONEST: it asserts the two unobservable classes are NOT falsely wired, so a
hollow observation cannot be slipped in to fake certification.

Invariants (all must hold):
  1. ``kernel/certification.py`` defines the record + tally: the ten ``CLASSES``,
     ``CertificationReport``, ``CertificationLedger``, and ``result``/``report``/
     ``from_map``.
  2. Each of the eight observable classes has its live observer wired — the owning
     module emits ``CERT.<CLASS>`` into the shared ledger.
  3. ``cognitive_core`` owns the shared seam: ``certification_observe`` +
     ``certification_report`` defined, and ``status()`` surfaces the dashboard.
  4. ``kernel_adoption`` declares ``certification`` at stage ``enforce``.
  5. HONESTY: the two not-yet-observable classes (``conflicting_priorities``,
     ``cognitive_error``) have NO emit call in any owner module — they are tracked
     as open work, never faked.

Pure stdlib; no Home Assistant import. Run ``--check`` in CI; a bare run prints
each invariant's status.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_COMP = ROOT / "custom_components" / "jarvis"
_CERT = _COMP / "kernel" / "certification.py"
_CORE = _COMP / "cognitive_core.py"
_ADOPTION = ROOT / "scripts" / "kernel_adoption.py"

# Each observable scenario class → the module whose live surface observes it.
_OBSERVED = {
    "PROACTIVE": "cognitive_core.py",
    "CONVERSATIONAL": "agent.py",
    "DELEGATION": "agent.py",
    "PROVIDER_FAILURE": "agent.py",
    "FAILURE": "actuation.py",
    "RESTART": "continuity.py",
    "LONG_HORIZON": "continuity.py",
    "SECURITY": "identity.py",
}
# Deliberately NOT observed yet (no honest live surface): must stay unwired.
_UNOBSERVED = ("CONFLICTING_PRIORITIES", "COGNITIVE_ERROR")

_OWNER_FILES = (
    "cognitive_core.py", "agent.py", "actuation.py", "continuity.py", "identity.py",
)


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _checks() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    # 1. the certification primitive
    try:
        ctext = _read(_CERT)
    except Exception as exc:
        return [("certification module present", False,
                 f"cannot read {_CERT.name}: {exc}")]
    for sym in ("CLASSES", "class CertificationReport", "class CertificationLedger",
                "def result(", "def report(", "def from_map("):
        out.append((f"certification defines {sym}", sym in ctext, ""))

    # 2. each observable class has its live observer wired
    owner_src: dict[str, str] = {}
    for fname in _OWNER_FILES:
        try:
            owner_src[fname] = _read(_COMP / fname)
        except Exception as exc:
            out.append((f"owner {fname} readable", False, str(exc)))
            owner_src[fname] = ""
    for cls, fname in _OBSERVED.items():
        marker = f"CERT.{cls}"
        out.append((f"{cls.lower()} observed in {fname}",
                    marker in owner_src.get(fname, ""), marker))

    # 3. cognitive_core owns the shared seam + dashboard
    core = owner_src.get("cognitive_core.py", "")
    out.append(("core defines certification_observe",
                "def certification_observe(" in core, ""))
    out.append(("core defines certification_report",
                "def certification_report(" in core, ""))
    out.append(("status() surfaces the dashboard",
                '"certification": certification_report(' in core, ""))

    # 4. the declared adoption stage
    try:
        spec = importlib.util.spec_from_file_location("_ka_cert_check", _ADOPTION)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)   # type: ignore[union-attr]
        stage = mod._DECLARED.get("certification", {}).get("stage")
        out.append(("certification declared enforce", stage == "enforce",
                    f"stage={stage}"))
    except Exception as exc:
        out.append(("certification declared enforce", False, f"read failed: {exc}"))

    # 5. HONESTY: the two unobservable classes must NOT be emitted anywhere
    for cls in _UNOBSERVED:
        marker = f"CERT.{cls}"
        emitted_in = [f for f, src in owner_src.items() if marker in src]
        out.append((f"{cls.lower()} stays unobserved (honest)",
                    not emitted_in,
                    (f"falsely wired in {', '.join(emitted_in)}" if emitted_in
                     else "no emit — tracked as open work")))

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
            print("CERTIFICATION REGRESSION — the MCU certification suite is no "
                  "longer honestly wired:", file=sys.stderr)
            for name, _ok, detail in failed:
                print(f"  - {name}" + (f" ({detail})" if detail else ""),
                      file=sys.stderr)
            print("\nEach observable scenario class must keep its live observer "
                  "(emitting CERT.<CLASS> into the shared ledger via "
                  "cognitive_core.certification_observe), the dashboard must stay "
                  "surfaced in status(), and the two not-yet-observable classes "
                  "must stay unwired (Phase AE enforce). Re-wire it, or update this "
                  "gate deliberately when a class gains or loses an honest surface.",
                  file=sys.stderr)
            return 1
        print(f"certification gate OK — {len(_OBSERVED)} observable scenario classes "
              f"wired, {len(_UNOBSERVED)} tracked as open work "
              f"({len(checks)} invariants, Phase AE enforce)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
