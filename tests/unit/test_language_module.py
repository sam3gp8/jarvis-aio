"""Household-language directive — the single source of truth in `language.py`.

The directive follows Home Assistant's configured language so JARVIS's output
(status briefs, camera analysis, sentinel notices and chat replies alike) is
localized. English (and unknown/absent config) installs get nothing, so they
are unaffected. `build_system_prompt` must append the same block to task
prompts, which is what fixed non-English briefs/camera analysis.
"""
import importlib.util
import sys
import types

import pytest


@pytest.fixture
def language(load):
    return load("language")


def _hass(lang):
    return types.SimpleNamespace(config=types.SimpleNamespace(language=lang))


def test_german_gets_directive(language):
    d = language.language_directive(_hass("de"))
    assert "## Language" in d and "German" in d


def test_russian_gets_directive(language):
    d = language.language_directive(_hass("ru"))
    assert "## Language" in d and "Russian" in d


def test_region_suffix_is_stripped(language):
    assert "German" in language.language_directive(_hass("de-DE"))


def test_unmapped_code_falls_back_to_code(language):
    d = language.language_directive(_hass("xx"))
    assert "## Language" in d and "xx" in d


def test_english_gets_nothing(language):
    assert language.language_directive(_hass("en")) == ""
    assert language.language_directive(_hass("en-US")) == ""


def test_missing_language_is_safe(language):
    assert language.language_directive(_hass(None)) == ""
    assert language.language_directive(types.SimpleNamespace()) == ""


# ── forced task directive (#140 — camera analysis logs reverting to English) ──
def test_task_directive_forces_language_without_escape_clause(language):
    d = language.language_task_directive(_hass("fr"))
    assert "French" in d
    assert "regardless of the language" in d
    # No "reply in the user's language" escape — unlike language_directive.
    assert "another language" not in d


def test_task_directive_english_is_empty(language):
    assert language.language_task_directive(_hass("en")) == ""
    assert language.language_task_directive(_hass(None)) == ""


def test_task_directive_appends_with_leading_space(language):
    d = language.language_task_directive(_hass("de"))
    assert d.startswith(" ") and "German" in d


def test_request_language_overrides_global(language):
    # A German voice satellite in a Russian household must be answered in German,
    # not Russian (discussion #55: wrong-language, garbled voice replies).
    assert language.configured_language(_hass("ru"), "de-DE") == "de"
    d = language.language_directive(_hass("ru"), "de")
    assert "German" in d and "Russian" not in d


def test_request_language_falls_back_to_global(language):
    # No per-request language → the household's global language still applies.
    assert language.configured_language(_hass("ru"), None) == "ru"
    assert "Russian" in language.language_directive(_hass("ru"), None)


def test_english_request_on_nonenglish_home_gets_nothing(language):
    # A request that arrives in English is answered in English even when the
    # household's global language is not — the request language wins.
    assert language.language_directive(_hass("de"), "en") == ""


def _load_real_directive_helper():
    """Load the real directive_helper into jc.directive_helper, past conftest's
    lightweight stub, restoring the stub afterwards so other tests are
    unaffected."""
    from conftest import COMP
    stub = sys.modules.get("jc.directive_helper")
    sys.modules.pop("jc.directive_helper", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "jc.directive_helper", COMP / "directive_helper.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["jc.directive_helper"] = mod
        spec.loader.exec_module(mod)
        return mod, stub
    except BaseException:
        if stub is not None:
            sys.modules["jc.directive_helper"] = stub
        raise


def test_build_system_prompt_appends_language_for_non_english():
    mod, stub = _load_real_directive_helper()
    try:
        prompt = mod.build_system_prompt(_hass("de"), "sir", "Give a status brief.")
        # #307: task prompts carry the FORCED directive (no escape clause), so a
        # weak local model can't read the English instructions as "user wrote
        # English" and answer in English.
        assert "German" in prompt and "regardless of the language" in prompt
        assert "writes to you in another language" not in prompt
        # English installs are unaffected — no language block injected.
        en = mod.build_system_prompt(_hass("en"), "sir", "Give a status brief.")
        assert "regardless of the language" not in en and "German" not in en
    finally:
        if stub is not None:
            sys.modules["jc.directive_helper"] = stub
        else:
            sys.modules.pop("jc.directive_helper", None)


def test_build_system_prompt_includes_capability_honesty():
    # #247: every task prompt must carry the capability-honesty rule so proactive
    # messages never offer control JARVIS lacks — regardless of language.
    mod, stub = _load_real_directive_helper()
    try:
        for lang in ("en", "de"):
            prompt = mod.build_system_prompt(_hass(lang), "sir", "Give a status brief.")
            assert "CAPABILITY HONESTY" in prompt
            assert "read-only" in prompt
    finally:
        if stub is not None:
            sys.modules["jc.directive_helper"] = stub
        else:
            sys.modules.pop("jc.directive_helper", None)


def test_configured_language_strips_region_and_lowercases(language):
    assert language.configured_language(_hass("de-DE")) == "de"
    assert language.configured_language(_hass("EN")) == "en"


def test_configured_language_defaults_to_en(language):
    assert language.configured_language(_hass(None)) == "en"
    assert language.configured_language(types.SimpleNamespace(config=None)) == "en"


def test_language_name_empty_for_english(language):
    assert language.language_name(_hass("en")) == ""
    assert language.language_name(_hass(None)) == ""


def test_language_name_maps_and_falls_back(language):
    assert language.language_name(_hass("de")) == "German"
    assert language.language_name(_hass("xx")) == "xx"


# ── JARVIS-specific output_language override (#148) ──────────────────────────
# A household can run Home Assistant in one language but make JARVIS speak
# another — the case where briefings came out in the HA language because that
# was the only knob.

@pytest.fixture
def set_output_language(load, monkeypatch):
    """Patch jarvis_config.get so language.py reads a chosen output_language."""
    jc = load("jarvis_config")

    def _set(value):
        real = {"output_language": value}
        monkeypatch.setattr(jc, "get", lambda k, d=None: real.get(k, d))
    return _set


def test_jarvis_output_language_overrides_global(language, set_output_language):
    set_output_language("de")
    # HA is Russian, but JARVIS is set to German → German wins.
    assert language.configured_language(_hass("ru")) == "de"
    assert "German" in language.language_directive(_hass("ru"))


def test_request_language_beats_jarvis_output_language(language, set_output_language):
    set_output_language("de")
    # An explicit per-request (voice pipeline) language still wins over the
    # JARVIS setting, so a request spoken in Russian is answered in Russian.
    assert language.configured_language(_hass("en"), "ru") == "ru"


def test_jarvis_output_language_auto_follows_global(language, set_output_language):
    set_output_language("auto")
    assert language.configured_language(_hass("ru")) == "ru"


def test_jarvis_output_language_blank_follows_global(language, set_output_language):
    set_output_language("")
    assert language.configured_language(_hass("ru")) == "ru"


def test_jarvis_output_language_english_silences_directive(language, set_output_language):
    # Explicitly choosing English on a non-English home turns the directive off.
    set_output_language("en")
    assert language.language_directive(_hass("ru")) == ""


# ── ui_language fallback (#317) ──────────────────────────────────────────────
# When output_language is Auto, JARVIS's own panel UI language is a better
# default than HA's SYSTEM language: a user who set JARVIS's UI (and their HA
# *profile*) to Russian, but never the HA *system* language, had announces and
# camera analysis come out in English because that system language was the only
# remaining source. ui_language now fills that gap — below output_language, above
# the HA global.

@pytest.fixture
def set_langs(load, monkeypatch):
    """Patch jarvis_config.get with chosen output_language + ui_language."""
    jc = load("jarvis_config")

    def _set(output="", ui=""):
        real = {"output_language": output, "ui_language": ui}
        monkeypatch.setattr(jc, "get", lambda k, d=None: real.get(k, d))
    return _set


def test_ui_language_fills_in_when_output_is_auto(language, set_langs):
    # output_language Auto, JARVIS UI set to Russian, HA system English →
    # output should follow the UI language the user actually chose (#317).
    set_langs(output="auto", ui="ru")
    assert language.configured_language(_hass("en")) == "ru"
    assert "Russian" in language.language_task_directive(_hass("en"))


def test_output_language_still_beats_ui_language(language, set_langs):
    # An explicit output_language wins over ui_language (keeps #148 intact).
    set_langs(output="de", ui="ru")
    assert language.configured_language(_hass("en")) == "de"


def test_request_language_beats_ui_language(language, set_langs):
    set_langs(output="auto", ui="ru")
    assert language.configured_language(_hass("en"), "fr") == "fr"


def test_ui_language_auto_falls_through_to_global(language, set_langs):
    # Both JARVIS knobs on Auto → HA system language, exactly as before.
    set_langs(output="auto", ui="auto")
    assert language.configured_language(_hass("ru")) == "ru"


def test_ui_language_region_suffix_stripped(language, set_langs):
    set_langs(output="", ui="pt-br")
    assert language.configured_language(_hass("en")) == "pt"


# ── translate_directive — localizing static alert templates (#317) ───────────
# Sentinel's door/window lines are plain English format strings; this directive
# lets the caller translate them to the household language. English homes get ""
# (no translation, no extra model call).

def test_translate_directive_empty_for_english(language):
    assert language.translate_directive(_hass("en")) == ""
    assert language.translate_directive(_hass(None)) == ""


def test_translate_directive_names_target_language(language):
    d = language.translate_directive(_hass("ru"))
    assert d and "Russian" in d and "Translate" in d


def test_translate_directive_follows_output_language(language, set_langs):
    # HA system English, but JARVIS Output language = German → translate to German.
    set_langs(output="de", ui="auto")
    assert "German" in language.translate_directive(_hass("en"))


def test_translate_directive_follows_ui_language_fallback(language, set_langs):
    # Output Auto, JARVIS UI Russian (the #317 case) → translate to Russian.
    set_langs(output="auto", ui="ru")
    assert "Russian" in language.translate_directive(_hass("en"))
