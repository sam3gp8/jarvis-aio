"""Frontend <-> WebSocket command contract — v7.45.0.

The panel talks to the backend over a set of `jarvis/*` WebSocket commands. A
backend refactor that renames or drops a command, or forgets to register a
handler, would break the dashboard silently. These tests pin the contract by
static analysis (no Home Assistant / voluptuous needed):

  * every command the panel SENDS is registered and reachable in the backend,
  * every WS handler that declares a command type is actually registered,
  * the extraction itself found commands on both sides (guards vacuous passes).

They read the real source files, so they fail the moment the two sides drift.
"""
from __future__ import annotations

import ast
import pathlib
import re

_COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
_WS_PY = _COMP / "websocket.py"
_PANEL_JS = _COMP / "frontend" / "jarvis-panel.js"


def _type_from_decorator(dec: ast.AST):
    """Pull the "jarvis/…" type out of a @websocket_command({...type...: "…"})
    decorator, or None if this decorator isn't one."""
    if not (isinstance(dec, ast.Call) and dec.args):
        return None
    first = dec.args[0]
    if not isinstance(first, ast.Dict):
        return None
    for key, value in zip(first.keys, first.values):
        is_type_key = (
            (isinstance(key, ast.Call) and key.args
             and isinstance(key.args[0], ast.Constant) and key.args[0].value == "type")
            or (isinstance(key, ast.Constant) and key.value == "type")
        )
        if is_type_key and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _backend():
    """Return (func_to_type, registered_types)."""
    src = _WS_PY.read_text()
    tree = ast.parse(src)
    func_to_type: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                t = _type_from_decorator(dec)
                if t:
                    func_to_type[node.name] = t
    registered_funcs = set(
        re.findall(r"async_register_command\(\s*hass\s*,\s*([A-Za-z_]\w*)", src))
    registered_types = {t for f, t in func_to_type.items() if f in registered_funcs}
    return func_to_type, registered_funcs, registered_types


def _frontend() -> set:
    """The jarvis/* command types the panel actually SENDS (type: "jarvis/…")."""
    src = _PANEL_JS.read_text()
    return set(re.findall(r"""type:\s*["'](jarvis/[a-z_]+)["']""", src))


def test_every_panel_command_is_registered():
    _, _, registered_types = _backend()
    missing = _frontend() - registered_types
    assert not missing, (
        "panel sends WS commands the backend doesn't register (would break the "
        f"dashboard): {sorted(missing)}")


def test_no_declared_handler_left_unregistered():
    func_to_type, registered_funcs, _ = _backend()
    orphans = {t for f, t in func_to_type.items() if f not in registered_funcs}
    assert not orphans, (
        "WS handlers declare a command type but are never async_register_command'd: "
        f"{sorted(orphans)}")


def test_extraction_found_commands_on_both_sides():
    # If a regex/AST change silently extracts nothing, the parity checks above
    # would pass vacuously — this guards against that.
    _, _, registered_types = _backend()
    frontend = _frontend()
    assert len(registered_types) >= 20, f"backend extraction looks wrong: {len(registered_types)}"
    assert len(frontend) >= 20, f"frontend extraction looks wrong: {len(frontend)}"
