"""German on/off verbs on the local fast path (issue #511).

Entity resolution already worked in German ("Bodenleuchte" resolves), but the
command verbs did not: "Schalte Bodenleuchte ein" matched no intent pattern and
fell through to the speculative bare-entity state query, so it was answered as a
status ("… is currently off") and the light never switched. These tests pin the
German separable verbs (schalte/mach … ein/an/aus) and the infinitive forms
(… einschalten/ausschalten), plus the guard that sends an unrecognised command
to the LLM instead of misreading it as a state query.
"""
from __future__ import annotations

import sys
import types

import pytest

from fakes import FakeHass


def _install_vc(monkeypatch):
    """voice_confirm stub: lights are never protected, so the fast path actuates."""
    fake = types.ModuleType("jc.voice_confirm")
    fake.action_is_protected = lambda hass, d, s, e="": False
    async def _confirm(hass, q, entity_id=""):
        return False
    fake.confirm = _confirm
    monkeypatch.setitem(sys.modules, "jc.voice_confirm", fake)
    monkeypatch.setattr(sys.modules["jc"], "voice_confirm", fake, raising=False)


def _hass():
    h = FakeHass()
    h.states.set("light.bodenleuchte_flur", "off", friendly_name="Bodenleuchte Flur")
    return h


# ── the reported case ─────────────────────────────────────────────────────────

async def test_schalte_ein_turns_on(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "Schalte Bodenleuchte ein.", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)


async def test_schalte_aus_turns_off(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    h.states.set("light.bodenleuchte_flur", "on", friendly_name="Bodenleuchte Flur")
    result = await le.try_local(h, "Schalte Bodenleuchte aus", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_off") for c in h.service_calls)


# ── other German forms ────────────────────────────────────────────────────────

async def test_mach_an_turns_on(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "mach die Bodenleuchte an", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)


async def test_mach_aus_turns_off(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    h.states.set("light.bodenleuchte_flur", "on", friendly_name="Bodenleuchte Flur")
    result = await le.try_local(h, "mach das Bodenleuchte aus", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_off") for c in h.service_calls)


async def test_infinitive_einschalten_turns_on(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "Bodenleuchte einschalten", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)


async def test_infinitive_ausschalten_turns_off(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    h.states.set("light.bodenleuchte_flur", "on", friendly_name="Bodenleuchte Flur")
    result = await le.try_local(h, "Bodenleuchte ausschalten", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_off") for c in h.service_calls)


# ── no bogus state query; unknown German command escalates ────────────────────

async def test_unresolved_german_command_escalates_not_state(load, monkeypatch):
    # A German command whose device can't be resolved must fall through to the
    # LLM (None), never return a speculative state on a mis-matched entity.
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "schalte den Flummiball ein", "sir")
    assert result is None
    assert h.service_calls == []


def test_looks_like_entity_name_rejects_commands(load):
    le = load("local_engine")
    # Leading action verbs → a command, not a bare device name.
    assert le._looks_like_entity_name("dimme die lampe") is False
    assert le._looks_like_entity_name("öffne das garagentor") is False
    assert le._looks_like_entity_name("turn on the lamp") is False
    # A genuine bare device name is still accepted (English state-query feature).
    assert le._looks_like_entity_name("kitchen lights") is True
    assert le._looks_like_entity_name("bodenleuchte flur") is True


# ── English regression ────────────────────────────────────────────────────────

async def test_english_turn_on_still_works(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "turn on Bodenleuchte", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)


# ── German replies honor output_language (#511, local_i18n) ───────────────────

async def test_german_command_reply_is_german(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "Schalte Bodenleuchte ein", "mein Herr", lang="de")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)
    # The spoken reply is German, not "… is on."
    assert "ist an" in result.text
    assert "is on" not in result.text


async def test_german_state_query_reply_is_german(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    h.states.set("light.bodenleuchte_flur", "off", friendly_name="Bodenleuchte Flur")
    # A bare device name → speculative state query, now rendered in German.
    result = await le.try_local(h, "Bodenleuchte Flur", "mein Herr", lang="de")
    assert result is not None
    assert "ist derzeit aus" in result.text        # "is currently off" → de, state translated


async def test_english_reply_unchanged_when_lang_en(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch)
    h = _hass()
    result = await le.try_local(h, "turn on Bodenleuchte", "sir", lang="en")
    assert result is not None and "is on" in result.text
