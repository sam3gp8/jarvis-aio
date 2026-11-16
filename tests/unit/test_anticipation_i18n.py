"""Localized anticipation / learned-routine alert templates (#539).

These proactive mentions are built without the LLM, so before #539 a non-English
household got them in hard-coded English. This is a static per-language catalog
(no API call); English is the base and the fallback. Six of the seven English
templates are byte-identical to the originals they replaced, so English installs
are unchanged — the tests freeze that wording. The routine-start message is the
exception: it became type-aware (a spoken command vs. a device state change)
and is tested on the new shape.
"""
import pytest


@pytest.fixture
def ai(load):
    return load("anticipation_i18n")


# ── coverage: no half-translated message ─────────────────────────────────────
def test_every_language_in_every_message_table(ai):
    langs = set(ai.LANGS)
    for key, table in ai.MESSAGES.items():
        assert langs <= set(table), f"MESSAGES[{key}] missing {langs - set(table)}"


def test_every_language_in_states_and_an_event(ai):
    langs = set(ai.LANGS)
    assert langs <= set(ai.AN_EVENT)
    assert langs <= set(ai.STATES)
    base = set(ai.STATES["en"])
    for lang in ai.LANGS:
        assert set(ai.STATES[lang]) == base, f"STATES[{lang}] key set differs"


# ── English freeze: the six unchanged templates read exactly as before ───────
def test_english_unusual_state_unchanged(ai):
    assert ai.message("unusual_state", "en", name="Garage", state="open",
                      usual="closed") == (
        "Garage is open. Around this time it's usually closed, so I thought it "
        "worth mentioning.")


def test_english_overdue_unchanged(ai):
    assert ai.message("overdue_activity", "en", name="Kitchen", time="09:00") == (
        "Kitchen usually has activity by around 09:00, but there's been none yet "
        "today — thought it worth flagging.")


def test_english_departure_unchanged(ai):
    assert ai.message("departure", "en", event="Dentist", minutes=20) == (
        "Heads up — Dentist begins in about 20 minutes; you'll want to head out.")
    assert ai.message("departure_at", "en", event="Dentist", location="Clinic",
                      minutes=20) == (
        "Heads up — Dentist at Clinic begins in about 20 minutes; you'll want to "
        "head out.")


def test_english_still_home_and_not_back_unchanged(ai):
    assert ai.message("still_home", "en", name="Sam", time="08:30") == (
        "Sam is usually out by around 08:30, but is still home — thought you'd "
        "want to know.")
    assert ai.message("not_back", "en", name="Sam", time="18:00") == (
        "Sam is usually home by around 18:00, but isn't back yet.")


def test_english_arriving_unchanged(ai):
    assert ai.message("arriving", "en", name="Sam",
                      distance=ai.number(3.4, "en", 1)) == (
        "Sam is heading home — about 3.4 km out.")


# ── localization renders filled, in-language ─────────────────────────────────
def test_german_unusual_state(ai):
    out = ai.message("unusual_state", "de", name="Garage",
                     state=ai.state_word("open", "de", "door"),
                     usual=ai.state_word("closed", "de", "door"))
    assert "Garage" in out and "offen" in out and "geschlossen" in out
    assert "usually" not in out


def test_italian_arriving_uses_decimal_comma(ai):
    out = ai.message("arriving", "it", name="Sam",
                     distance=ai.number(3.4, "it", 1))
    assert "3,4 km" in out and "casa" in out


def test_region_suffix_normalized(ai):
    out = ai.message("not_back", "de-DE", name="Alex", time="18:00")
    assert "Alex" in out and "zu Hause" in out


def test_unknown_language_falls_back_to_english(ai):
    out = ai.message("not_back", "xx", name="Alex", time="18:00")
    assert out == "Alex is usually home by around 18:00, but isn't back yet."


def test_unknown_key_is_empty_not_raising(ai):
    assert ai.message("no_such_key", "de", name="x") == ""


def test_missing_param_degrades_to_english_or_safe(ai):
    # A formatting problem never propagates.
    out = ai.message("arriving", "de", name="Sam")   # no 'distance'
    assert isinstance(out, str)


# ── routine-start: the new type-aware shape ──────────────────────────────────
def test_routine_command_passes_command_verbatim(ai):
    out = ai.message("routine_command", "it", command="accendi le luci")
    assert "accendi le luci" in out      # a person's own words, untranslated
    assert out.startswith("A quest'ora")


def test_routine_device_localizes_state(ai):
    out = ai.message("routine_device", "de", name="Flurlicht",
                     state=ai.state_word("on", "de"))
    assert "Flurlicht" in out and "an" in out


def test_routine_other_localizes_leadin_only(ai):
    out = ai.message("routine_other", "fr", routine="turn on the lights")
    # lead-in localized; the stored description is shown as-is
    assert out.startswith("À cette heure-ci") and "turn on the lights" in out


# ── state_word ───────────────────────────────────────────────────────────────
def test_state_word_opening_classes(ai):
    assert ai.state_word("on", "en", "door") == "open"
    assert ai.state_word("off", "en", "garage_door") == "closed"
    assert ai.state_word("on", "de", "window") == "offen"


def test_state_word_unknown_passthrough(ai):
    # An unlisted state is shown with underscores as spaces — never blank, never
    # an English word swapped in.
    assert ai.state_word("buffering_fast", "de") == "buffering fast"


def test_state_word_localized(ai):
    assert ai.state_word("locked", "fr") == "verrouillé"


# ── number ───────────────────────────────────────────────────────────────────
def test_number_decimal_mark(ai):
    assert ai.number(3.4, "en", 1) == "3.4"
    assert ai.number(3.4, "de", 1) == "3,4"
    assert ai.number("not-a-number", "de") == "not-a-number"


def test_an_event_localized(ai):
    assert ai.an_event("it") == "un evento"
    assert ai.an_event("xx") == "an event"
