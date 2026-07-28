"""Tests for the Directive Layer / operational modes (v6.61.0). Covers mode
resolution and override fallback, set/clear + validation, custom-mode merging,
the persona/proactive/autonomy hook values, and the invariant that modes never
purport to gate safety. State is redirected to a temp file per test."""
import importlib
import pytest


@pytest.fixture
def modes(load, tmp_path, monkeypatch):
    m = load("modes")
    # isolate persistence + reset in-memory state each test
    monkeypatch.setattr(m, "MODE_STATE_PATH", str(tmp_path / "mode_state.json"))
    m._state = {"mode": m.DEFAULT_MODE, "since": 0.0, "reason": ""}
    m._loaded = False
    return m


# ── defaults & resolution ────────────────────────────────────────────────────

def test_default_is_normal(modes):
    assert modes.active_mode() == "normal"
    assert modes.mode_allows_proactive() is True
    assert modes.mode_allows_auto_actions() is True


def test_set_known_mode(modes):
    res = modes.set_mode("party")
    assert res["ok"] is True and res["mode"] == "party"
    assert modes.active_mode() == "party"


def test_set_unknown_mode_rejected(modes):
    res = modes.set_mode("nonsense")
    assert res["ok"] is False
    assert "available" in res and "party" in res["available"]
    assert modes.active_mode() == "normal"          # unchanged


def test_set_mode_is_case_insensitive(modes):
    modes.set_mode("PARTY")
    assert modes.active_mode() == "party"


def test_clear_returns_to_normal(modes):
    modes.set_mode("movie")
    modes.clear_mode()
    assert modes.active_mode() == "normal"


# ── override profiles per mode ───────────────────────────────────────────────

def test_party_relaxes_proactive_and_maxes_banter(modes):
    modes.set_mode("party")
    assert modes.mode_allows_proactive() is False
    assert modes.mode_banter_level() == 2
    assert modes.mode_announce_scope() == "critical"


def test_movie_is_near_silent(modes):
    modes.set_mode("movie")
    assert modes.mode_allows_proactive() is False
    assert modes.mode_banter_level() == 0
    assert modes.mode_allows_auto_actions() is False


def test_away_keeps_auto_actions_but_cuts_convenience(modes):
    modes.set_mode("away")
    assert modes.mode_allows_proactive() is False
    assert modes.mode_allows_auto_actions() is True   # security automations still run


def test_normal_banter_is_none_so_config_wins(modes):
    # normal mode doesn't force a banter level → persona keeps user's config
    assert modes.mode_banter_level() is None


def test_override_falls_back_for_unspecified_fields(modes):
    # a mode that omits a field inherits normal's value
    modes.set_mode("guest")
    ov = modes.mode_overrides()
    # guest specifies proactive True + banter 1 but relies on defaults elsewhere
    assert ov["proactive"] is True
    assert ov["announce_scope"] == "notable"


# ── custom modes via config ──────────────────────────────────────────────────

def test_custom_mode_from_config(modes, monkeypatch):
    monkeypatch.setattr(modes, "_cfg", lambda k, d=None: (
        {"cleaning": {"description": "vacuum running", "proactive": False,
                      "banter": 0, "announce_scope": "critical",
                      "auto_actions": False}}
        if k == "custom_modes" else d))
    res = modes.set_mode("cleaning")
    assert res["ok"] is True
    assert modes.active_mode() == "cleaning"
    assert modes.mode_allows_proactive() is False


def test_custom_mode_can_override_builtin(modes, monkeypatch):
    # user redefines 'party' to keep proactive on
    monkeypatch.setattr(modes, "_cfg", lambda k, d=None: (
        {"party": {"proactive": True}} if k == "custom_modes" else d))
    modes.set_mode("party")
    assert modes.mode_allows_proactive() is True     # user override wins


# ── persistence across reload ────────────────────────────────────────────────

def test_mode_persists_across_reload(modes, load):
    modes.set_mode("lab", reason="soldering")
    # simulate a fresh process: reset in-memory, force reload from disk
    modes._loaded = False
    modes._state = {"mode": modes.DEFAULT_MODE, "since": 0.0, "reason": ""}
    assert modes.active_mode() == "lab"              # restored from file
    assert modes.mode_info()["reason"] == "soldering"


# ── mode_info shape (for panel/agent) ────────────────────────────────────────

def test_mode_info_lists_available(modes):
    info = modes.mode_info()
    names = {m["name"] for m in info["available"]}
    assert {"normal", "party", "lab", "movie", "guest", "away", "focus"} <= names
    assert "overrides" in info


# ── agent tool registration ──────────────────────────────────────────────────

def test_set_mode_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "set_mode" in names
    assert "set_mode" in agent._TOOL_MAP
