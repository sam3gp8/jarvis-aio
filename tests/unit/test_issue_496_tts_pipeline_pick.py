"""Issue #496 — TTS auto-pick must follow the user's preferred Assist pipeline.

A site with an OpenAI (or any cloud) TTS pipeline still got Piper on every
JARVIS-routed reply / announcement / jarvis.speak, because the auto-pick
(find_best_tts_entity) was hardcoded to prefer Piper and never consulted the
preferred pipeline's engine. Voice/language resolution already followed the
pipeline, so only the ENGINE selection was wrong.

Fix: when the engine is left on "auto", honor the preferred pipeline's tts.*
engine first, then fall back to the local default (piper > edge > any). An
explicit engine pin still wins, and premium-context routing is unaffected.
"""
import types

import pytest

from fakes import FakeHass


@pytest.fixture
def tts(load):
    return load("tts_helper")


def _fake_pipeline(engine):
    return types.SimpleNamespace(tts_engine=engine, tts_voice="onyx", tts_language="de-DE")


# ── find_best_tts_entity: pipeline engine wins over the piper default ────────

def test_auto_pick_prefers_preferred_pipeline_engine(tts, monkeypatch):
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    hass.states.set("tts.openai_onyx", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))
    assert tts.find_best_tts_entity(hass) == "tts.openai_onyx"


def test_auto_pick_falls_back_to_piper_without_pipeline(tts, monkeypatch):
    hass = FakeHass()
    hass.states.set("tts.openai_onyx", "idle")
    hass.states.set("tts.piper", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline", lambda h: None)
    assert tts.find_best_tts_entity(hass) == "tts.piper"


def test_auto_pick_falls_back_when_pipeline_engine_unavailable(tts, monkeypatch):
    # Pipeline names an engine, but its entity isn't live (e.g. disabled) →
    # don't pick a dead entity; fall through to the local default.
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))  # no state set
    assert tts.find_best_tts_entity(hass) == "tts.piper"


# ── preferred_pipeline_tts_entity: validation guards ─────────────────────────

def test_preferred_entity_requires_live_state(tts, monkeypatch):
    hass = FakeHass()
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))
    assert tts.preferred_pipeline_tts_entity(hass) is None     # state missing
    hass.states.set("tts.openai_onyx", "idle")
    assert tts.preferred_pipeline_tts_entity(hass) == "tts.openai_onyx"


def test_preferred_entity_rejects_legacy_platform_name(tts, monkeypatch):
    # A legacy pipeline may store a platform name ("piper"), not an entity id.
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("piper"))
    assert tts.preferred_pipeline_tts_entity(hass) is None


def test_preferred_entity_none_when_no_pipeline(tts, monkeypatch):
    hass = FakeHass()
    monkeypatch.setattr(tts, "_get_preferred_pipeline", lambda h: None)
    assert tts.preferred_pipeline_tts_entity(hass) is None


# ── resolve_tts_entity: auto follows pipeline; explicit pin wins ─────────────

def test_resolve_auto_follows_pipeline(tts, monkeypatch):
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    hass.states.set("tts.openai_onyx", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))
    assert tts.resolve_tts_entity(hass, "auto") == "tts.openai_onyx"


def test_explicit_pin_wins_over_pipeline(tts, monkeypatch):
    # The user pinned Piper deliberately; the OpenAI pipeline must NOT override.
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    hass.states.set("tts.openai_onyx", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))
    assert tts.resolve_tts_entity(hass, "tts.piper") == "tts.piper"


# ── resolve_tts_for_context: regular path follows pipeline, premium unaffected ─

def test_regular_context_uses_pipeline_engine(tts, monkeypatch):
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    hass.states.set("tts.openai_onyx", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))
    got = tts.resolve_tts_for_context(hass, "chat", "auto", None, [])
    assert got == "tts.openai_onyx"


def test_premium_context_still_prefers_premium(tts, monkeypatch):
    # Premium routing is independent of the pipeline auto-pick.
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    hass.states.set("tts.openai_onyx", "idle")
    hass.states.set("tts.elevenlabs", "idle")
    monkeypatch.setattr(tts, "_get_preferred_pipeline",
                        lambda h: _fake_pipeline("tts.openai_onyx"))
    got = tts.resolve_tts_for_context(
        hass, "briefing", "auto", "tts.elevenlabs", ["briefing"])
    assert got == "tts.elevenlabs"


def test_get_preferred_pipeline_is_safe_without_ha(tts):
    # In the unit environment homeassistant isn't importable; the helper must
    # degrade to None rather than raise, so the piper default still works.
    hass = FakeHass()
    hass.states.set("tts.piper", "idle")
    assert tts._get_preferred_pipeline(hass) is None
    assert tts.find_best_tts_entity(hass) == "tts.piper"
