"""Adaptive interruption budget in the output gate (opt-in).

When `adaptive_interruption_budget` is off (default), the hourly announcement cap
is unchanged. When on, the multiplier from decision_record.interruption_budget()
scales the cap down, so JARVIS interrupts less after a run of dismissed alerts.
"""
import asyncio

import pytest


@pytest.fixture
def og(load):
    return load("output_gate")


class _Hass:
    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


def _fill(og, n):
    now = og._now()
    og._STATE.history.clear()
    for i in range(n):
        og._STATE.history.append(og.Announcement(
            timestamp=now, entity_id=f"e{i}", category="x",
            urgency="low", message=f"m{i}", was_spoken=True))


def test_multiplier_off_by_default(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)   # flag unset -> False
    og._BUDGET_CACHE["ts"] = 0.0
    assert og._budget_multiplier() == 1.0


def test_multiplier_applied_when_enabled(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: True if k == "adaptive_interruption_budget" else d)
    dr = load("decision_record")
    monkeypatch.setattr(dr, "interruption_budget", lambda *a, **k: {"multiplier": 0.5})
    og._BUDGET_CACHE["ts"] = 0.0
    assert og._budget_multiplier() == 0.5


def test_cap_unchanged_when_off(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)   # adaptive off
    og._BUDGET_CACHE["ts"] = 0.0
    _fill(og, og.DEFAULT_MAX_PER_HOUR)                     # exactly at base cap (6)
    allowed, reason = og.can_announce(
        entity_id="e", category="x", urgency="low", message="new")
    assert allowed is False
    assert f"/{og.DEFAULT_MAX_PER_HOUR}/hour" in reason    # base cap enforced


def test_cap_tightened_when_adaptive_on(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: True if k == "adaptive_interruption_budget" else d)
    dr = load("decision_record")
    monkeypatch.setattr(dr, "interruption_budget", lambda *a, **k: {"multiplier": 0.5})
    og._BUDGET_CACHE["ts"] = 0.0
    half = max(1, round(og.DEFAULT_MAX_PER_HOUR * 0.5))    # 3
    _fill(og, half)                                        # only 3 in the last hour
    allowed, reason = og.can_announce(
        entity_id="e", category="x", urgency="low", message="new")
    assert allowed is False                                # blocked at the tightened cap
    assert f"/{half}/hour" in reason


def test_async_reservation_tracks_owner_only(og):
    hass = _Hass()
    og._STATE.reservations.clear()
    og._STATE.history.clear()
    og._STATE.recent_messages.clear()

    async def _run():
        allowed1, reason1, reservation1 = await og.async_reserve_announcement(
            hass, entity_id="e", category="x", urgency="low", message="alpha"
        )
        allowed2, reason2, reservation2 = await og.async_reserve_announcement(
            hass, entity_id="f", category="y", urgency="low", message="beta"
        )
        assert allowed1 and allowed2 and reason1 == "ok" and reason2 == "ok"
        assert reservation1 != reservation2
        await og.async_record_announcement(
            hass, reservation_id=reservation1,
            entity_id="e", category="x", urgency="low", message="alpha", was_spoken=True,
        )
        assert len(og._STATE.reservations) == 1
        await og.async_record_announcement(
            hass, reservation_id=reservation2,
            entity_id="f", category="y", urgency="low", message="beta", was_spoken=True,
        )
        assert len(og._STATE.reservations) == 0

    asyncio.run(_run())
