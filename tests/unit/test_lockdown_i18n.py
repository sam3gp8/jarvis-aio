"""Localized composed lockdown messages (issue: French safety notifications).

The device-list lockdown messages are now stitched from localized verb phrases,
a localized list join, and per-language wrappers — so a French (or other) house
gets the whole sentence, device list included, in their language. Device names
pass through untranslated.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def i18n(load):
    return load("notify_i18n")


# ── localized join ───────────────────────────────────────────────────────────

def test_join_names_french_no_oxford(i18n):
    assert i18n.join_names(["a", "b"], "fr") == "a et b"
    assert i18n.join_names(["a", "b", "c"], "fr") == "a, b et c"   # no comma before 'et'


def test_join_names_other_languages(i18n):
    assert i18n.join_names(["a", "b"], "de") == "a und b"
    assert i18n.join_names(["a", "b"], "es") == "a y b"
    assert i18n.join_names(["a", "b"], "it") == "a e b"
    assert i18n.join_names(["a", "b"], "nl") == "a en b"
    assert i18n.join_names(["a", "b"], "pt") == "a e b"


def test_join_names_english_keeps_oxford(i18n):
    assert i18n.join_names(["a", "b", "c"], "en") == "a, b, and c"


# ── build_lockdown_message in French ─────────────────────────────────────────

def test_lockdown_french_locked_and_closed(cc):
    msg = cc.build_lockdown_message(
        "monsieur", ["Porte d'entrée"], ["Garage"], [], "fr")
    assert msg == ("Monsieur, confinement activé — j'ai verrouillé Porte d'entrée "
                   "et fermé Garage. La maison est sécurisée.")


def test_lockdown_french_locked_only(cc):
    msg = cc.build_lockdown_message("monsieur", ["Serrure avant"], [], [], "fr")
    assert "j'ai verrouillé Serrure avant" in msg
    assert "La maison est sécurisée." in msg


def test_lockdown_french_gap(cc):
    # a window that can't be secured remotely — gender-safe phrasing, no
    # adjective/pronoun agreeing with the device
    msg = cc.build_lockdown_message("monsieur", [], ["Garage"], ["Fenêtre 1"], "fr")
    assert "j'ai fermé Garage" in msg
    assert "Fenêtre 1 : impossible à verrouiller à distance" in msg
    assert "mais" in msg


def test_lockdown_french_already_secured(cc):
    msg = cc.build_lockdown_message("monsieur", [], [], [], "fr")
    assert msg == ("Monsieur, confinement activé — la maison était déjà "
                   "entièrement sécurisée.")


def test_lockdown_french_many_open(cc):
    msg = cc.build_lockdown_message(
        "monsieur", [], [], ["f1", "f2", "f3", "f4"], "fr")
    assert "4 ouvertures ne peuvent pas être verrouillées à distance" in msg


# ── English output is unchanged (regression guard) ───────────────────────────

def test_lockdown_english_unchanged(cc):
    msg = cc.build_lockdown_message("sir", ["Front Lock"], ["the Garage Door"], [])
    assert msg == ("Sir, lockdown engaged — I locked Front Lock and closed the "
                   "Garage Door. The home is secure.")


# ── nighttime wrapper renders in French ──────────────────────────────────────

def test_nighttime_wrapper_french(i18n):
    out = i18n.message("lockdown_nighttime", "fr",
                       honorific="Monsieur", body="verrouillé Garage")
    assert out == "Monsieur, confinement nocturne : verrouillé Garage. La maison est sécurisée."


def test_unknown_language_falls_back_to_english(i18n):
    out = i18n.message("lockdown_did", "xx", honorific="Sir", did="locked X")
    assert out == "Sir, lockdown engaged — I locked X. The home is secure."
