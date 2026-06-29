"""Every config key the panel tries to save must be accepted by the backend.

This guards the bug class where the panel calls jarvis/update_config with a key
that isn't in PANEL_WRITABLE_KEYS, so the write is rejected with `invalid_key`
and the user sees a save failure (e.g. the Residence tab's HOME STYLE control).

Both sides are parsed statically from source — websocket.py can't be imported
(heavy HA module-level imports), and the panel is JS.
"""
import ast
import re
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
_PANEL_JS = _COMPONENT / "frontend" / "jarvis-panel.js"
_WEBSOCKET = _COMPONENT / "websocket.py"


def _allowlist() -> set[str]:
    """The PANEL_WRITABLE_KEYS set, read from websocket.py via AST."""
    tree = ast.parse(_WEBSOCKET.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Set):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "PANEL_WRITABLE_KEYS":
                    return {e.value for e in node.value.elts
                            if isinstance(e, ast.Constant)}
    raise AssertionError("PANEL_WRITABLE_KEYS set not found in websocket.py")


def _panel_saved_keys() -> set[str]:
    """Every literal key the panel persists: _saveConfig('k', …) calls and the
    data-cfg-key='k' attributes consumed by the generic save handlers."""
    js = _PANEL_JS.read_text()
    keys = set(re.findall(r"_saveConfig\(\s*['\"]([a-z0-9_]+)['\"]", js))
    keys |= set(re.findall(r"data-cfg-key=['\"]([a-z0-9_]+)['\"]", js))
    return keys


def test_panel_writable_keys_set_exists():
    assert len(_allowlist()) > 0


def test_every_panel_saved_key_is_writable():
    allow = _allowlist()
    saved = _panel_saved_keys()
    assert saved, "expected to find keys the panel saves"
    missing = sorted(k for k in saved if k not in allow)
    assert not missing, (
        "panel saves config keys the backend rejects as not-writable "
        f"(jarvis/update_config -> invalid_key): {missing}"
    )


def test_residence_keys_specifically_writable():
    # The exact keys behind the Residence-tab save failure — pinned so this can't
    # regress silently.
    allow = _allowlist()
    for key in ("residence_style", "floor_plan_sqft", "home_stories",
                "has_basement", "dormers_front", "dormers_rear",
                "garage_bays", "chimney_side"):
        assert key in allow, f"{key} missing from PANEL_WRITABLE_KEYS"
