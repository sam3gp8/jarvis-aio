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


def _panel_data_config_keys() -> set[str]:
    """Keys surfaced in ws_get_panel_data's `config` dict, read from the
    "key": _runtime_opt(...) / "key": _get_... lines via regex. This is what the
    panel reads back as d.config.KEY."""
    js = _WEBSOCKET.read_text()
    # crude but effective: any '"key":' inside the config block
    m = re.search(r'"config":\s*\{(.*?)\n            \},', js, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r'"([a-z0-9_]+)":', m.group(1)))


def _panel_read_cfg_keys() -> set[str]:
    """cfg-field keys the panel reads back via d.config?.KEY — a select/input
    that saves AND is expected to reflect its saved value on re-render."""
    js = _PANEL_JS.read_text()
    return set(re.findall(r"d\.config\?\.([a-z0-9_]+)", js))


def test_character_and_research_keys_surfaced_in_panel_data():
    """Regression: banter_level, search_backend, searxng_url,
    calendar_tight_gap_min, and recognition_source were SAVED but not returned
    by get_panel_data, so their selects snapped back to defaults on re-render.
    Pin them so this can't regress."""
    surfaced = _panel_data_config_keys()
    for key in ("banter_level", "search_backend", "searxng_url",
                "calendar_tight_gap_min", "recognition_source"):
        assert key in surfaced, (
            f"{key} is missing from ws_get_panel_data's config block — the panel "
            f"select will reset to its default on every re-render")


def test_read_back_cfg_fields_are_surfaced():
    """Every cfg-field the panel reads via d.config?.KEY must be surfaced in the
    panel-data config block, or its control silently resets after saving. Guards
    the whole bug class, not just the known keys."""
    read = _panel_read_cfg_keys()
    surfaced = _panel_data_config_keys()
    # only enforce for keys that are ALSO saved as cfg-fields (round-trip fields)
    saved = _panel_saved_keys()
    round_trip = read & saved
    missing = sorted(k for k in round_trip if k not in surfaced)
    assert not missing, (
        "these config keys are saved + read-back by the panel but not surfaced "
        f"in get_panel_data, so they reset on re-render: {missing}")
