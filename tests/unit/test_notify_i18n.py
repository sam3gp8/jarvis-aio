"""Localized safety-notification templates."""
import pytest


@pytest.fixture
def i18n(load):
    return load("notify_i18n")


def test_english_freeze_matches_original_wording(i18n):
    msg = i18n.message("freeze_critical", "en", honorific="Sir",
                       reading="18.0°F", set_to="55°F")
    assert msg.startswith("Sir, outdoor temperature has dropped to 18.0°F")
    assert "55°F" in msg


def test_french_freeze_is_french_and_filled(i18n):
    msg = i18n.message("freeze_critical", "fr", honorific="Monsieur",
                       reading="-10,0°C", set_to="13°C")
    assert "température extérieure" in msg and "-10,0°C" in msg and "13°C" in msg


def test_region_suffix_normalized(i18n):
    assert "Außentemperatur" in i18n.message("freeze_warning", "de-DE",
                                             honorific="Sir", reading="2°C")


def test_unknown_language_falls_back_to_english(i18n):
    assert i18n.message("freeze_warning", "xx", honorific="Sir",
                        reading="2°C").startswith("Sir, outdoor temperature")


def test_unknown_key_is_safe(i18n):
    assert i18n.message("nope", "fr", honorific="X") == ""


def test_titles_localized_with_fallback(i18n):
    assert i18n.title("intrusion_confirmed", "fr") == "JARVIS — INTRUSION"
    assert i18n.title("lockdown", "de") == "JARVIS — Haus gesichert"
    assert i18n.title("lockdown", "xx") == "JARVIS — House Secured"   # fallback
    assert i18n.title("unknown_type", "fr") == "JARVIS"               # default


def test_intrusion_ctx_fragments(i18n):
    assert i18n.message("intrusion_ctx_open", "fr", name="Front Door") == " (Front Door ouvert)"
    assert i18n.message("intrusion_ctx_armed", "es") == " (alarma armada)"


def test_all_message_keys_cover_all_title_languages(i18n):
    # every message/title present in English must at least exist; spot-check the
    # 7-language coverage is symmetric so nothing silently misses a language
    langs = {"en", "fr", "de", "es", "it", "nl", "pt"}
    for key, table in i18n.MESSAGES.items():
        assert set(table.keys()) == langs, f"{key} missing languages"
    for key, table in i18n.TITLES.items():
        assert set(table.keys()) == langs, f"title {key} missing languages"
