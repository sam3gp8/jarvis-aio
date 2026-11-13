"""Zero-API localization catalogue for the local engine's replies (#511).

English is the canonical fallback: a missing language or key must degrade to the
English template (never a blank), so English homes are unchanged and a partial
translation never shows a hole.
"""
import datetime

import pytest


@pytest.fixture
def i18n(load):
    return load("local_i18n")


# ── t(): render + fallback ────────────────────────────────────────────────────

def test_english_template(i18n):
    assert i18n.t("turn_on", "en", ack="Done", sec="", h="sir",
                  name="Lamp", bp="", temp="", vol="", action="turn_on") \
        == "Done, sir. Lamp is on."


def test_german_template(i18n):
    out = i18n.t("turn_on", "de", ack="Erledigt", sec="", h="mein Herr",
                 name="Lampe", bp="", temp="", vol="", action="turn_on")
    assert out == "Erledigt, mein Herr. Lampe ist an."


def test_unknown_language_falls_back_to_english(i18n):
    out = i18n.t("turn_off", "xx", ack="Done", sec="", h="sir",
                 name="Lamp", bp="", temp="", vol="", action="turn_off")
    assert out == "Done, sir. Lamp is off."


def test_unknown_key_falls_back_to_blank_not_raise(i18n):
    assert i18n.t("no_such_key", "de", h="sir") == ""


def test_missing_field_falls_back_to_english_then_safe(i18n):
    # German template references {h}; omitting it must not raise.
    out = i18n.t("thanks_1", "de")       # no h= provided
    assert isinstance(out, str)


def test_primary_subtag_normalisation(i18n):
    # "de-DE" resolves to the German catalogue.
    out = i18n.t("media_pause", "de-DE", h="mein Herr")
    assert out == "Pausiert, mein Herr."


# ── vocabulary helpers ────────────────────────────────────────────────────────

def test_leadins_per_language(i18n):
    assert "Erledigt" in i18n.leadins("ack", "de")
    assert "Done" in i18n.leadins("ack", "en")
    assert i18n.leadins("ack", "xx") == i18n.leadins("ack", "en")   # fallback


def test_state_word_translates_de_passes_en(i18n):
    assert i18n.state_word("off", "de") == "aus"
    assert i18n.state_word("on", "de") == "an"
    assert i18n.state_word("off", "en") == "off"            # english untouched
    assert i18n.state_word("42", "de") == "42"              # unknown passes through


def test_noun_plural_forms(i18n):
    assert i18n.noun("light", 1, "en") == "light"
    assert i18n.noun("light", 3, "en") == "lights"
    assert i18n.noun("light", 1, "de") == "Licht"
    assert i18n.noun("light", 3, "de") == "Lichter"
    assert i18n.noun("person", 2, "en") == "persons"        # preserves legacy literal


def test_bulk_verb(i18n):
    assert i18n.bulk_verb("turn_off", "en") == "turn off"
    assert i18n.bulk_verb("turn_off", "de") == "ausgeschaltet"
    assert i18n.bulk_verb("turn_on", "de") == "eingeschaltet"


def test_greet_word_by_hour(i18n):
    assert i18n.greet_word(9, "de") == "Guten Morgen"
    assert i18n.greet_word(14, "de") == "Guten Tag"
    assert i18n.greet_word(20, "de") == "Guten Abend"
    assert i18n.greet_word(9, "en") == "Good morning"


def test_format_date_german_names(i18n):
    d = datetime.datetime(2026, 11, 13, 9, 0)      # a Friday
    out = i18n.format_date(d, "de")
    assert out == "Freitag, 13. November 2026"


def test_format_time_24h_for_non_english(i18n):
    d = datetime.datetime(2026, 11, 13, 15, 5)
    assert i18n.format_time(d, "de") == "15:05"
    assert "PM" in i18n.format_time(d, "en")       # english keeps 12h AM/PM
