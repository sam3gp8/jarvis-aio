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


def test_scheduler_skips_only_when_confidently_empty():
    # require_home is honored, but via the fail-open helper so uncertain
    # presence no longer silences scheduled briefings (v6.95.0).
    assert "briefing_require_home" in INIT
    assert "everyone_confidently_away" in INIT


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


# ── v6.78.1 regression guards ────────────────────────────────────────────────

def test_scheduler_uses_the_real_llm_client_name():
    """The scheduled briefing must reference llm_client (which exists in
    async_setup_entry), not groq_client (a parameter of _register_services).
    Referencing the wrong name raised NameError on every scheduled run and was
    swallowed by the handler's except."""
    assert "async_briefing(hass, call, llm_client," in INIT
    # and the wrong name must not appear inside the setup-scope scheduler
    sched = INIT.split("Scheduled briefings", 1)[1].split("def _register_services", 1)[0]
    assert "groq_client" not in sched


def test_briefing_failures_are_logged_visibly():
    """A scheduled briefing that fails must warn, not whisper at debug — the
    debug level is what hid the NameError."""
    sched = INIT.split("Scheduled briefings", 1)[1].split("def _register_services", 1)[0]
    assert "_LOGGER.warning" in sched
    assert 'briefing failed' in sched


def test_hazard_announce_argument_order():
    """async_announce is (hass, text, tts_entity, speakers) — the hazard monitor
    passed (hass, tts, speakers, text), which would speak the wrong thing."""
    hz = (COMP / "hazard_monitor.py").read_text()
    assert "async_announce(hass, message, tts, spk" in hz


def test_all_announce_callers_pass_text_second():
    """Guard the whole class: every async_announce call site must pass the text
    as the second positional argument."""
    import re
    bad = []
    for py in COMP.glob("*.py"):
        for m in re.finditer(r"async_announce\(\s*hass,\s*([A-Za-z_][\w\.\[\]\"']*)", py.read_text()):
            arg = m.group(1)
            # a TTS entity or speaker list in the text slot is the bug signature
            if arg in ("tts", "tts_entity", "spk", "speakers"):
                bad.append(f"{py.name}: async_announce(hass, {arg}, ...)")
    assert not bad, f"wrong async_announce argument order: {bad}"


def test_briefing_tts_and_speakers_use_effective_config():
    # Briefing TTS/speaker resolution must read the effective config
    # (jarvis_config wins), not the empty entry.options — otherwise the
    # announce silently bails before reaching TTS (v6.96.0).
    tts_body = INIT[INIT.index("def _get_tts"):INIT.index("def _get_speakers")]
    spk_start = INIT.index("def _get_speakers")
    spk_body = INIT[spk_start:spk_start + 1400]
    assert "effective_config" in tts_body, "_get_tts must use effective_config"
    assert "effective_config" in spk_body, "_get_speakers must use effective_config"
    # and must NOT resolve these keys from bare entry.options anymore
    assert "entry.options.get(CONF_TTS_ENGINE" not in tts_body


def test_briefing_falls_back_on_empty_model_output():
    # empty LLM output must NOT silently skip the announce — read the gathered
    # facts instead so the briefing still happens (v6.97.0).
    assert "_plain_briefing" in BRIEF
    assert "empty model output" in BRIEF
