"""Tests for scheduled briefings (v6.78.0). The scheduler lives in __init__ and
can't run headless, so these cover the pieces that carry the logic: time
parsing, the hazard section added to the briefing content, and the panel
read-back/writable registration that makes the toggles work."""
import json
import pathlib
import re

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
INIT = (COMP / "__init__.py").read_text()
WS = (COMP / "websocket.py").read_text()
BRIEF = (COMP / "briefing.py").read_text()
PANEL = (COMP / "frontend" / "jarvis-panel.js").read_text()


def test_scheduler_uses_clock_time_not_interval():
    # a briefing must fire at a wall-clock time, not every N minutes
    assert "async_track_time_change" in INIT
    assert "_morning_briefing" in INIT and "_evening_briefing" in INIT


def test_scheduler_defaults_are_off():
    # opt-in: JARVIS must not start talking on a schedule unannounced
    assert 'briefing_{kind}_enabled", False' in INIT or \
           'f"briefing_{kind}_enabled", False' in INIT


def test_scheduler_skips_empty_house_by_default():
    assert "briefing_require_home" in INIT
    assert "nobody home" in INIT


def test_time_parser_present_with_sane_defaults():
    assert "_parse_hhmm" in INIT
    assert '"07:30"' in INIT and '"19:30"' in INIT


def test_hazards_included_in_briefing_content():
    assert "include_hazards" in BRIEF
    assert "Active hazards nearby" in BRIEF
    assert "hazard_monitor" in BRIEF


def test_briefing_keys_are_writable():
    block = WS.split("PANEL_WRITABLE_KEYS", 1)[1][:9000]
    for k in ("briefing_morning_enabled", "briefing_evening_enabled",
              "briefing_morning_time", "briefing_evening_time",
              "briefing_require_home", "briefing_include_hazards"):
        assert f'"{k}"' in block, k


def test_briefing_keys_are_surfaced_for_read_back():
    # the read-back bug class: a control the panel reads must be returned by
    # get_panel_data or it resets on re-render
    for k in ("briefing_morning_enabled", "briefing_evening_enabled",
              "briefing_morning_time", "briefing_evening_time"):
        assert f'"{k}": ' in WS, k


def test_panel_briefing_toggles_are_wired():
    # every briefing toggle must carry data-cfg-val or it's inert
    for k in ("briefing_morning_enabled", "briefing_evening_enabled",
              "briefing_require_home", "briefing_include_hazards"):
        m = re.search(r'data-cfg-key="' + k + r'"[^>]*data-cfg-val', PANEL)
        assert m, f"{k} toggle is not wired"


def test_panel_has_manual_trigger():
    assert "brief-now" in PANEL and "_wireBriefings" in PANEL
