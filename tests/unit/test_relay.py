"""Relay a spoken message to a household member (#540).

JARVIS as household intermediary: "tell dad dinner's ready" → announce it on the
home's speakers, framed as a message for that person. The recipient matching and
text shaping are pure; one async path does the announce (stubbed here).
"""
import sys
import types

import pytest


@pytest.fixture
def relay(load):
    return load("relay")


_PEOPLE = [
    {"name": "Sam", "state": "home"},
    {"name": "Alex Smith", "state": "not_home"},
]


# ── resolve_recipient (pure) ─────────────────────────────────────────────────
def test_resolve_exact_name(relay):
    assert relay.resolve_recipient("Sam", _PEOPLE)["name"] == "Sam"


def test_resolve_first_name(relay):
    assert relay.resolve_recipient("alex", _PEOPLE)["name"] == "Alex Smith"


def test_resolve_strips_filler(relay):
    assert relay.resolve_recipient("tell Sam", _PEOPLE)["name"] == "Sam"


def test_resolve_unknown_relationship_is_none(relay):
    # "dad" rarely maps to an HA person — None is fine; the relay still speaks it.
    assert relay.resolve_recipient("dad", _PEOPLE) is None


def test_resolve_bad_input(relay):
    assert relay.resolve_recipient("", _PEOPLE) is None
    assert relay.resolve_recipient("Sam", None) is None
    assert relay.resolve_recipient("Sam", []) is None
    assert relay.resolve_recipient("tell", _PEOPLE) is None   # all filler


# ── compose_announcement (pure) ──────────────────────────────────────────────
def test_compose_with_sender(relay):
    assert relay.compose_announcement("Dad", "dinner's ready", "Sam") \
        == "Sam asked me to tell Dad: dinner's ready."


def test_compose_without_sender(relay):
    assert relay.compose_announcement("Dad", "dinner's ready") \
        == "Message for Dad: dinner's ready."


def test_compose_no_recipient(relay):
    assert relay.compose_announcement("", "come eat") == "Announcement: come eat."


def test_compose_keeps_existing_punctuation(relay):
    assert relay.compose_announcement("Dad", "are you there?") \
        == "Message for Dad: are you there?"


def test_compose_empty_message_is_empty(relay):
    assert relay.compose_announcement("Dad", "   ") == ""


# ── presence_note (pure) ─────────────────────────────────────────────────────
def test_presence_note_away(relay):
    note = relay.presence_note({"name": "Alex", "state": "not_home"})
    assert "away" in note and "Alex" in note


def test_presence_note_home_is_empty(relay):
    assert relay.presence_note({"name": "Sam", "state": "home"}) == ""


def test_presence_note_unknown_is_empty(relay):
    assert relay.presence_note(None) == ""


# ── async_relay (announce path, siblings stubbed) ────────────────────────────
def _stub_sibling(monkeypatch, name: str, **attrs):
    """Replace ``jc.<name>`` with a stub — both in sys.modules AND as an
    attribute on the synthetic ``jc`` package. relay.py reaches its siblings via
    ``from . import <name>``, which binds the PACKAGE ATTRIBUTE; when the real
    module has already been imported by an earlier test (full-suite ordering),
    replacing only sys.modules is ignored and the real module leaks through. Set
    both so the stub holds in every ordering. monkeypatch restores both."""
    mod = types.ModuleType(f"jc.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"jc.{name}", mod)
    jc_pkg = sys.modules.get("jc")
    if jc_pkg is not None:
        monkeypatch.setattr(jc_pkg, name, mod, raising=False)
    return mod


@pytest.fixture
def stubbed(monkeypatch):
    """Stub relay.py's lazily-imported siblings; capture the announce call."""
    captured = {}

    async def _announce(hass, text, tts_entity, speakers, **k):
        captured["text"] = text
        captured["speakers"] = list(speakers)

    _stub_sibling(monkeypatch, "presence",
                  get_presence_summary=lambda hass: {"people": _PEOPLE})
    _stub_sibling(monkeypatch, "audio_routing",
                  broadcast_target=lambda hass, **k: ["media_player.kitchen",
                                                      "assist_satellite.office"])
    _stub_sibling(monkeypatch, "tts_helper", async_announce=_announce)
    _stub_sibling(monkeypatch, "websocket", jarvis_log=lambda *a, **k: None)
    return captured


async def test_async_relay_announces_and_drops_satellites(relay, fake_hass, stubbed):
    res = await relay.async_relay(fake_hass, "dad", "dinner's ready", "Sam")
    assert res["success"] is True
    assert res["spoken"] == "Sam asked me to tell dad: dinner's ready."
    # listen-only satellite is excluded from the announce targets
    assert stubbed["speakers"] == ["media_player.kitchen"]
    assert stubbed["text"] == res["spoken"]


async def test_async_relay_notes_away_recipient(relay, fake_hass, stubbed):
    res = await relay.async_relay(fake_hass, "Alex", "call me back")
    assert res["success"] is True
    assert res["recipient_state"] == "not_home"
    assert "away" in res["note"]


async def test_async_relay_requires_message(relay, fake_hass, stubbed):
    res = await relay.async_relay(fake_hass, "Sam", "   ")
    assert res["success"] is False
    assert "message is required" in res["error"]


async def test_async_relay_errors_with_no_speakers(relay, fake_hass, monkeypatch):
    _stub_sibling(monkeypatch, "presence",
                  get_presence_summary=lambda hass: {"people": _PEOPLE})
    _stub_sibling(monkeypatch, "audio_routing",
                  broadcast_target=lambda hass, **k: [])   # none configured
    _stub_sibling(monkeypatch, "websocket", jarvis_log=lambda *a, **k: None)
    res = await relay.async_relay(fake_hass, "Sam", "hi")
    assert res["success"] is False
    assert "speaker" in res["error"].lower()
