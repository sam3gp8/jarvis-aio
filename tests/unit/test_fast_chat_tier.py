"""Fast-chat tier routing (latency work): a small/fast model answers simple
conversational turns, the big Main Agent model handles complex ones — split by
the complexity score local_engine already computes.

These exercise the pure policy (``fast_chat.fast_tier_target`` /
``fast_model_name``); the conversation wiring just swaps the provider/model this
returns into the ``run_agent`` call.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def fc(load):
    return load("fast_chat")


def _cfg(**over):
    base = {
        "fast_chat_enabled": True,
        "fast_model": "gemma3n:e4b",
        "fast_provider": "",
        "fast_chat_max_complexity": 70,
        "llm_provider": "ollama",
    }
    base.update(over)
    return base


# ── the route: a simple conversational turn goes to the small model ──────────

def test_simple_turn_routes_to_fast_model(fc):
    t = fc.fast_tier_target(_cfg(), 40)
    assert t == {"provider": "ollama", "model": "gemma3n:e4b"}


def test_complex_turn_stays_on_main_agent(fc):
    assert fc.fast_tier_target(_cfg(), 85) is None          # >= threshold → big model


def test_threshold_is_exclusive_at_boundary(fc):
    assert fc.fast_tier_target(_cfg(), 70) is None           # exactly at threshold → big
    assert fc.fast_tier_target(_cfg(), 69) is not None       # just below → fast


def test_custom_threshold_respected(fc):
    assert fc.fast_tier_target(_cfg(fast_chat_max_complexity=50), 60) is None
    assert fc.fast_tier_target(_cfg(fast_chat_max_complexity=50), 40) is not None


# ── direct-reasoning sentinel and odd complexity values never divert ─────────

def test_direct_reasoning_sentinel_uses_big_model(fc):
    # complexity == -1 means "skip local triage, go straight to the reasoning
    # agent" — must never be stolen by the fast tier.
    assert fc.fast_tier_target(_cfg(), -1) is None


def test_nonint_complexity_is_safe(fc):
    assert fc.fast_tier_target(_cfg(), None) is None
    assert fc.fast_tier_target(_cfg(), "nope") is None


def test_bad_threshold_falls_back_to_default(fc):
    # garbage threshold → default 70, so complexity 40 still routes to fast
    assert fc.fast_tier_target(_cfg(fast_chat_max_complexity="x"), 40) is not None
    assert fc.fast_tier_target(_cfg(fast_chat_max_complexity="x"), 75) is None


# ── enable gate and model presence ───────────────────────────────────────────

def test_disabled_never_routes(fc):
    assert fc.fast_tier_target(_cfg(fast_chat_enabled=False), 10) is None


def test_enabled_as_truthy_string(fc):
    assert fc.fast_tier_target(_cfg(fast_chat_enabled="true"), 10) is not None
    assert fc.fast_tier_target(_cfg(fast_chat_enabled="off"), 10) is None


def test_blank_model_never_routes(fc):
    assert fc.fast_tier_target(_cfg(fast_model=""), 10) is None
    assert fc.fast_tier_target(_cfg(fast_model="   "), 10) is None


def test_non_dict_config_is_safe(fc):
    assert fc.fast_tier_target(None, 10) is None
    assert fc.fast_tier_target("x", 10) is None


# ── provider resolution ──────────────────────────────────────────────────────

def test_blank_fast_provider_falls_back_to_main(fc):
    t = fc.fast_tier_target(_cfg(fast_provider="", llm_provider="ollama"), 10)
    assert t["provider"] == "ollama"


def test_explicit_fast_provider_wins(fc):
    t = fc.fast_tier_target(_cfg(fast_provider="custom"), 10)
    assert t["provider"] == "custom"


def test_provider_defaults_to_ollama_when_unset(fc):
    t = fc.fast_tier_target(_cfg(fast_provider="", llm_provider=""), 10)
    assert t["provider"] == "ollama"


# ── fast_model_name (keep-warm helper) ───────────────────────────────────────

def test_fast_model_name_when_enabled(fc):
    assert fc.fast_model_name(_cfg()) == "gemma3n:e4b"


def test_fast_model_name_none_when_disabled(fc):
    assert fc.fast_model_name(_cfg(fast_chat_enabled=False)) is None


def test_fast_model_name_none_when_blank(fc):
    assert fc.fast_model_name(_cfg(fast_model="")) is None
