"""Reply-language directive — follows Home Assistant's configured language.

Non-English households get a directive to reply in their language; English (and
unknown/absent config) installs get nothing, so they're unaffected.
"""
import types
import pytest


@pytest.fixture
def agent(load):
    return load("agent")


def _hass(language):
    return types.SimpleNamespace(config=types.SimpleNamespace(language=language))


def test_french_gets_directive(agent):
    d = agent._language_directive(_hass("fr"))
    assert "French" in d and "## Language" in d


def test_region_suffix_is_stripped(agent):
    assert "German" in agent._language_directive(_hass("de-DE"))


def test_unmapped_code_falls_back_to_code(agent):
    # unknown code still produces a directive, using the raw code
    d = agent._language_directive(_hass("xx"))
    assert "## Language" in d and "xx" in d


def test_english_gets_nothing(agent):
    assert agent._language_directive(_hass("en")) == ""
    assert agent._language_directive(_hass("en-US")) == ""


def test_missing_language_is_safe(agent):
    assert agent._language_directive(_hass(None)) == ""
    assert agent._language_directive(types.SimpleNamespace()) == ""
