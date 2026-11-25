"""Panel settings audit (9.166.0): the 38 previously UI-less keys are now fully
surfaced — writable from the panel, echoed in ws_get_panel_data (so controls
reflect the stored value instead of resetting), and backed by a real control in
jarvis-panel.js. Also pins the arrival_greeting_enabled de-duplication and the
three diagnostics service buttons.

Both sides are parsed statically from source — websocket.py can't be imported
(heavy HA module-level imports) and the panel is JS.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JARVIS = ROOT / "custom_components" / "jarvis"
WEBSOCKET = JARVIS / "websocket.py"
PANEL_JS = JARVIS / "frontend" / "jarvis-panel.js"

# The 38 keys the audit surfaces (Sections A + B of jarvis_panel_ui_audit.md).
AUDIT_KEYS = [
    # A — already writable, were missing UI + (mostly) read-back
    "opening_watch_enabled", "opening_watch_interval_min", "briefing_include_presence",
    "arrival_min_away_minutes", "arrival_push_enabled", "proactive_questions_interactive",
    "quiet_hours_push_critical_only", "cognition_threshold", "pattern_min_occurrences",
    "pattern_confidence", "presence_only_entities", "hazard_disaster_radius_km",
    "intrusion_inward_depth", "outdoor_areas", "outdoor_entities", "indoor_entities",
    "energy_mode_bump", "call_integration_domains", "embed_model", "embed_base_url",
    "voice_confirm_entities", "satellite_audio_out", "satellite_start_action",
    "voice_local_trust_threshold", "voice_local_guess_threshold", "voice_local_min_samples",
    "goals_kernel_authoritative", "biometric_entities", "custom_modes",
    # B — were in neither PANEL_WRITABLE_KEYS nor get_panel_data
    "ollama_keep_warm", "tts_engine", "tts_premium_engine", "tts_premium_contexts",
    "observer_proactive", "lockdown_auto_on_arm", "intrusion_require_corroboration",
    "intrusion_spread_zones", "appliance_sensors",
]

SECTION_B_KEYS = [
    "ollama_keep_warm", "tts_engine", "tts_premium_engine", "tts_premium_contexts",
    "observer_proactive", "lockdown_auto_on_arm", "intrusion_require_corroboration",
    "intrusion_spread_zones", "appliance_sensors",
]


def _ws() -> str:
    return WEBSOCKET.read_text(encoding="utf-8")


def _js() -> str:
    return PANEL_JS.read_text(encoding="utf-8")


def _writable_keys() -> set[str]:
    blk = re.search(r"PANEL_WRITABLE_KEYS\s*=\s*\{(.*?)\n\}", _ws(), re.S).group(1)
    return set(re.findall(r'"([a-z0-9_]+)"', blk))


def _config_block() -> str:
    # Same delimiter the panel-data parity test uses: the ws_get_panel_data
    # "config": { ... } dict, up to its 12-space closing brace.
    m = re.search(r'"config":\s*\{(.*?)\n            \},', _ws(), re.DOTALL)
    assert m, "could not locate the ws_get_panel_data config block"
    return m.group(1)


def _config_keys() -> list[str]:
    return re.findall(r'"([a-z0-9_]+)":', _config_block())


def _panel_cfg_keys() -> set[str]:
    return set(re.findall(r"data-cfg-key=['\"]([a-z0-9_]+)['\"]", _js()))


def test_all_audit_keys_are_writable():
    missing = sorted(k for k in AUDIT_KEYS if k not in _writable_keys())
    assert not missing, f"audit keys missing from PANEL_WRITABLE_KEYS: {missing}"


def test_section_b_keys_added_to_writable():
    # The 9 Section-B keys specifically were NOT writable before the audit.
    allow = _writable_keys()
    for k in SECTION_B_KEYS:
        assert k in allow, f"Section-B key not writable: {k}"


def test_all_audit_keys_surfaced_in_panel_data():
    surfaced = set(_config_keys())
    missing = sorted(k for k in AUDIT_KEYS if k not in surfaced)
    assert not missing, (
        f"audit keys not echoed in ws_get_panel_data config (controls reset "
        f"on re-render): {missing}")


def test_all_audit_keys_have_a_panel_control():
    saved = _panel_cfg_keys()
    missing = sorted(k for k in AUDIT_KEYS if k not in saved)
    assert not missing, f"audit keys with no data-cfg-key control in the panel: {missing}"


def test_arrival_greeting_enabled_surfaced_exactly_once():
    # The duplicate-key bug: it was emitted twice in get_panel_data with conflicting
    # defaults (True then False). Only the canonical opt-in default (False) remains.
    occurrences = [k for k in _config_keys() if k == "arrival_greeting_enabled"]
    assert len(occurrences) == 1, (
        f"arrival_greeting_enabled appears {len(occurrences)}x in the config block "
        f"(the conflicting-default duplicate must be removed)")
    block = _config_block()
    assert '"arrival_greeting_enabled": bool(_runtime_opt(hass, entry, "arrival_greeting_enabled", False))' in block, \
        "the surviving arrival_greeting_enabled must keep its opt-in default (False)"


def test_no_duplicate_keys_in_config_block():
    keys = _config_keys()
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert not dups, f"duplicate keys in the get_panel_data config block: {dups}"


def test_diagnostics_service_buttons_present():
    js = _js()
    for svc in ("jarvis.test_routing", "jarvis.unshush", "jarvis.backup"):
        assert f'data-svc="{svc}"' in js, f"missing diagnostics button for {svc}"


def test_csv_json_parse_helpers_and_handler():
    js = _js()
    assert "_cfgCsv(" in js and "_cfgJson(" in js, "missing CSV/JSON parse helpers"
    # the generic cfg-field handler must honour the parse mode
    assert 'data-cfg-parse' in js
    assert 'getAttribute("data-cfg-parse")' in js
    # list fields use csv, map fields use json
    assert 'data-cfg-parse="csv"' in js
    assert 'data-cfg-parse="json"' in js
