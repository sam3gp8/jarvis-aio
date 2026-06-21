"""Regression tests for reasoning_loop.decide()'s resilience cascade.

decide() is engineered so the household keeps a coherent voice as capabilities
drop away: local templates -> learned cache -> cloud LLM, with a connectivity
breaker, falling back to stale cache then the Local Mind. These tests isolate the
cascade ROUTING by stubbing the collaborator layers (templates, cache, Local
Mind), so we assert on the control flow — not on those modules' internals.

The single network seam is the injected provider; the only shared mutable state
is connectivity._BREAKER, reset before each test via the public reset().
"""
import pytest


@pytest.fixture(autouse=True)
def reset_breaker(connectivity):
    connectivity.reset()
    yield
    connectivity.reset()


@pytest.fixture
def isolate_cascade(reasoning_loop, connectivity, load, monkeypatch):
    """Force decide() down to the cloud/breaker branch deterministically and
    capture the Local-Mind floor without loading patterns.db or the cache file."""
    rl = reasoning_loop
    cache = load("reasoning_cache")
    local_mind = load("local_mind")

    monkeypatch.setattr(rl, "_try_local_reasoning", lambda *a, **k: None)
    monkeypatch.setattr(rl, "_rich_mode", lambda hass: False)
    monkeypatch.setattr(cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(cache, "remember", lambda *a, **k: None)
    monkeypatch.setattr(cache, "note_hit", lambda *a, **k: None)

    async def fake_assess(*a, **k):
        return {"speak": True, "message": "Local Mind handled it.", "urgency": "medium"}

    monkeypatch.setattr(local_mind, "assess", fake_assess)
    return rl


def _decide(rl, hass, provider, **over):
    kwargs = dict(
        honorific="sir",
        event_summary="binary_sensor.cellar_window changed to on",
        home_state_summary="away",
        classifier_urgency="medium",
        classifier_category="general",
        recent_announcements=[],
        anyone_home=False,
        entity_id="binary_sensor.cellar_window",
        device_class="window",
        from_state="off",
        to_state="on",
        friendly_name="Cellar Window",
    )
    kwargs.update(over)
    return rl.decide(hass, provider, **kwargs)


async def test_decide_never_raises_on_provider_failure(
        isolate_cascade, fake_hass, provider_factory):
    rl = isolate_cascade
    provider = provider_factory(exc=RuntimeError("connection refused"))  # non-transient
    out = await _decide(rl, fake_hass, provider)
    assert isinstance(out, dict) and "speak" in out      # resilient: returns a decision
    assert provider.calls == 1                            # tried the cloud once
    assert out["message"] == "Local Mind handled it."     # fell through to the floor


async def test_two_failures_open_breaker_then_skip_network(
        isolate_cascade, fake_hass, provider_factory, connectivity):
    rl = isolate_cascade
    provider = provider_factory(exc=RuntimeError("connection refused"))
    for _ in range(2):                                   # _FAILURE_THRESHOLD = 2
        await _decide(rl, fake_hass, provider)
    assert connectivity.is_offline() is True             # breaker OPEN

    calls_before = provider.calls
    out = await _decide(rl, fake_hass, provider)
    assert provider.calls == calls_before                # cloud skipped while OPEN
    assert out["message"] == "Local Mind handled it."    # served by the floor


async def test_successful_cloud_decision_is_returned(
        isolate_cascade, fake_hass, provider_factory):
    rl = isolate_cascade
    provider = provider_factory(
        replies=['{"speak": true, "message": "Sir, the cellar window opened.", "urgency": "high"}'])
    out = await _decide(rl, fake_hass, provider)
    assert provider.calls == 1
    assert out["speak"] is True
    assert out["urgency"] == "high"
    assert "cellar window" in out["message"].lower()


async def test_cloud_silence_decision_is_returned(
        isolate_cascade, fake_hass, provider_factory):
    rl = isolate_cascade
    provider = provider_factory(replies=['{"speak": false, "reason": "routine"}'])
    out = await _decide(rl, fake_hass, provider)
    assert out["speak"] is False


async def test_recovery_after_cooldown(
        isolate_cascade, fake_hass, provider_factory, connectivity, monkeypatch):
    rl = isolate_cascade
    # Trip the breaker.
    bad = provider_factory(exc=RuntimeError("connection refused"))
    for _ in range(2):
        await _decide(rl, fake_hass, bad)
    assert connectivity.is_offline()

    # Advance time past the 60s cooldown so the breaker allows a probe again.
    import time as _t
    base = _t.time()
    monkeypatch.setattr(_t, "time", lambda: base + 120)

    good = provider_factory(
        replies=['{"speak": true, "message": "Recovered, sir.", "urgency": "low"}'])
    out = await _decide(rl, fake_hass, good)
    assert good.calls == 1                 # probe was allowed through
    assert out["speak"] is True
