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


def _load_real_directive_helper():
    """Load the real directive_helper into jc.directive_helper, past conftest's
    lightweight stub, restoring the stub afterwards so other tests are
    unaffected."""
    from tests.conftest import COMP  # type: ignore
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
        assert "## Language" in prompt and "German" in prompt
        # English installs are unaffected — no language block injected.
        en = mod.build_system_prompt(_hass("en"), "sir", "Give a status brief.")
        assert "## Language" not in en
    finally:
        if stub is not None:
            sys.modules["jc.directive_helper"] = stub
        else:
            sys.modules.pop("jc.directive_helper", None)
