#!/usr/bin/env python3
"""MCU Phase H exit-criteria gate — zero consequential actuator bypasses.

Phase H's exit criterion is that **every consequential (world-mutating) actuation
converges on the universal actuator seam** (``actuation.execute_actuator`` /
``execute_safety_actuator``), so each one is planned, event-published and
journaled. This gate is the honest, mechanical proof of that: it AST-scans the
integration for ``*.services.async_call(...)`` and fails CI if any call that can
change the physical state of the home is made **outside** the seam and is not
explicitly classified here.

It is the runtime sibling of the 8.34.0 governance gate (which classifies agent
*tools*): same "classify or wire" philosophy, applied to raw service calls. A new
direct ``light``/``lock``/``cover``/``climate``/… call therefore cannot land
uncounted — the author must either route it through the seam or classify it here
with a reason.

Classification (a call is allowed iff one holds):
  * it lives in ``actuation.py`` — that module *is* the seam;
  * its literal domain is a **communication** channel (notify / tts / …), which
    does not mutate the home;
  * it is a literal **read-only** or **meta** service (weather.get_forecasts,
    automation.reload);
  * it is a literal ``media_player`` **playback** service (TTS / announcements);
  * its (file, function) is in ``_ALLOWED_SITES`` with a documented reason
    (dynamic-domain notify, the execute_plan kernel-planner step, the seam's
    verify-after-act retry, the generic user-authored routine runner).

Pure stdlib; no Home Assistant import. Run ``--check`` in CI; bare run lists every
call and its classification.
"""
from __future__ import annotations

import ast
import pathlib
import sys

# ── communication / non-world-mutating domains (literal first arg) ───────────
_COMM_DOMAINS = frozenset({
    "notify", "persistent_notification", "tts", "assist_satellite",
    "conversation", "logbook",
})

# literal (domain, service) that read or reload config rather than mutate devices
_READONLY_META = frozenset({
    ("weather", "get_forecasts"),
    ("automation", "reload"),
})

# media_player services that are playback/announcement (TTS), not device control
_MEDIA_PLAYBACK = frozenset({"play_media", "media_play", "media_stop"})

# ── sites that are not literal-communication yet are legitimately not a bypass ─
# Keyed (filename, enclosing-function) → reason. Each is either the seam's own
# machinery or a dynamic-domain communication/macro path that cannot change a
# device on its own. A NEW direct world-mutating call in a new location is NOT
# here, so it fails the gate until seam-routed or classified.
_ALLOWED_SITES: dict[tuple[str, str], str] = {
    # execute_plan expresses each step as a kernel Plan and runs it via
    # aexecute_plan; this closure is that plan's execution step (Phase B4) — the
    # seam's planner contract, not a bypass.
    ("agent.py", "_run"): "execute_plan kernel-planner execution step (B4)",
    # verify-after-act: _verify_control re-issues the SAME canonical action once
    # as part of the seam's verification step (control_device/H1).
    ("agent.py", "_verify_control"): "seam verify-after-act retry (H1)",
    # routines: a user-AUTHORED macro of arbitrary service/target/data steps,
    # executed verbatim — not a JARVIS device decision. Generic dispatcher; the
    # per-entity seam does not fit arbitrary service+target+data steps.
    ("routines.py", "async_run_routine"): "generic user-authored macro runner",
    # config-driven phone/notify pushes (domain/service from notify_service) —
    # communications, resolve to notify.*.
    ("sentinel.py", "_announce_rule"): "config-driven notify (communication)",
    ("appliance_monitor.py", "_announce_done"): "config-driven notify (communication)",
    ("proactive_briefing.py", "_push_to_phone"): "config-driven notify (communication)",
    ("cognitive_core.py", "_push_notification"): "config-driven push notify (communication)",
    ("observer.py", "_send_notification"): "config-driven notify (communication)",
    ("__init__.py", "_test_notify"): "config-driven test notify (communication)",
    # tts / announcement playback through dynamic service names (jarvis.speak).
    ("proactive_audio.py", "_run_audit"): "jarvis.speak announcement (communication)",
    # the TTS announcement pipeline sets the target speaker's volume before
    # speaking — a playback-pipeline side effect, not a standalone device command.
    ("proactive_audio.py", "_set_volume"): "announcement-pipeline TTS volume (communication)",
    # ESPHome fallback that starts a voice satellite listening — a voice-pipeline
    # trigger, not a home device state change.
    ("voice_confirm.py", "_start_listening"): "voice-satellite start action (communication)",
}


def _component_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "jarvis"


class _Scanner(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.func_stack: list[str] = []
        self.calls: list[dict] = []

    def visit_FunctionDef(self, node):
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        func = node.func
        # match X.services.async_call(...)
        if (isinstance(func, ast.Attribute) and func.attr == "async_call"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "services"):
            domain = self._literal(node.args[0]) if node.args else None
            service = self._literal(node.args[1]) if len(node.args) > 1 else None
            self.calls.append({
                "line": node.lineno,
                "func": self.func_stack[-1] if self.func_stack else "<module>",
                "domain": domain, "service": service,
            })
        self.generic_visit(node)

    @staticmethod
    def _literal(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _classify(filename: str, call: dict) -> tuple[bool, str]:
    if filename == "actuation.py":
        return True, "seam (actuation.execute_actuator / execute_safety_actuator)"
    dom, svc = call["domain"], call["service"]
    if dom in _COMM_DOMAINS:
        return True, f"communication domain '{dom}'"
    if (dom, svc) in _READONLY_META:
        return True, f"read-only/meta {dom}.{svc}"
    if dom == "media_player" and svc in _MEDIA_PLAYBACK:
        return True, f"media playback {dom}.{svc}"
    key = (filename, call["func"])
    if key in _ALLOWED_SITES:
        return True, _ALLOWED_SITES[key]
    where = f"{dom}.{svc}" if dom else "<dynamic domain>"
    return False, f"unclassified world-mutating call {where} in {call['func']}()"


def scan() -> list[dict]:
    comp = _component_dir()
    rows: list[dict] = []
    for path in sorted(comp.rglob("*.py")):
        if "/kernel/" in str(path):          # pure types, no hass; never actuates
            continue
        rel = path.name
        try:
            tree = ast.parse(path.read_text())
        except Exception as exc:   # pragma: no cover - defensive
            rows.append({"file": rel, "line": 0, "func": "<parse>",
                         "ok": False, "reason": f"parse error: {exc}"})
            continue
        sc = _Scanner(rel)
        sc.visit(tree)
        for call in sc.calls:
            ok, reason = _classify(rel, call)
            rows.append({"file": rel, "line": call["line"], "func": call["func"],
                         "ok": ok, "reason": reason})
    return rows


def main(argv: list[str]) -> int:
    rows = scan()
    violations = [r for r in rows if not r["ok"]]
    if "--check" not in argv:
        for r in sorted(rows, key=lambda r: (r["file"], r["line"])):
            mark = "ok " if r["ok"] else "XX "
            print(f"  {mark}{r['file']}:{r['line']} {r['func']}() — {r['reason']}")
        print(f"\n{len(rows)} service calls, {len(violations)} unclassified")
    if "--check" in argv:
        if violations:
            print("ACTUATOR BYPASS — world-mutating service calls outside the "
                  "seam and unclassified:", file=sys.stderr)
            for r in violations:
                print(f"  - {r['file']}:{r['line']} {r['func']}() — {r['reason']}",
                      file=sys.stderr)
            print("\nRoute it through actuation.execute_actuator (or "
                  "execute_safety_actuator for securing), or classify it in "
                  "scripts/kernel_bypass_check.py with a reason.", file=sys.stderr)
            return 1
        print(f"bypass gate OK — all {len(rows)} service calls converge on the "
              f"seam or are classified (Phase H exit criterion)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
