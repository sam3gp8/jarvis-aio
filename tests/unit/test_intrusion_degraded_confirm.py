"""Away false-alarm fix (8.69.0): harden intrusion CONFIRMATION under a degraded
alarm-panel hold.

When the home is treated as "away" ONLY because an auto (alarm-engaged) lockdown
is being held through an unreadable (``unavailable``) alarm panel, a resident is
very likely still inside. In that degraded state an inconclusive / unavailable
vision check must NOT fail open to a confirmed critical intrusion alarm — it must
require real corroboration (a positive vision person-confirm, or a genuine inward
route). Genuine away (tracked-away / armed-away / a user-requested lockdown) is
unchanged: vision-inconclusive still fails toward alerting there.
"""
import types

import pytest


@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_require_corroboration": True})


def _stub_lockdown(monkeypatch, cognitive_core, *, active, auto):
    monkeypatch.setattr(
        cognitive_core._CORE, "lockdown_mgr",
        types.SimpleNamespace(active=active, auto=auto))


# ── _confinement_degraded() matrix ──────────────────────────────────────────

def test_degraded_true_held_auto_lockdown_panel_unavailable(
        safety, cognitive_core, monkeypatch, fake_hass):
    # No alarm panel readable (none present → indeterminate), no residents tracked
    # away, an auto-lockdown held → degraded.
    monkeypatch.setattr(safety, "_residents_away", lambda: False)
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)
    _stub_lockdown(monkeypatch, cognitive_core, active=True, auto=True)
    assert safety._confinement_degraded() is True


def test_degraded_false_when_residents_tracked_away(
        safety, cognitive_core, monkeypatch):
    # Genuinely away → never degraded (keep failing toward alerting).
    monkeypatch.setattr(safety, "_residents_away", lambda: True)
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)
    _stub_lockdown(monkeypatch, cognitive_core, active=True, auto=True)
    assert safety._confinement_degraded() is False


def test_degraded_false_when_panel_actually_armed(
        safety, cognitive_core, monkeypatch):
    # A readable armed panel is genuine confinement, not a degraded hold.
    monkeypatch.setattr(safety, "_residents_away", lambda: False)
    monkeypatch.setattr(safety, "_alarm_armed", lambda: True)
    _stub_lockdown(monkeypatch, cognitive_core, active=True, auto=True)
    assert safety._confinement_degraded() is False


def test_degraded_false_for_manual_lockdown(
        safety, cognitive_core, monkeypatch):
    # A user-REQUESTED (manual, auto=False) lockdown is a deliberate away-posture
    # and keeps full confirmation strength.
    monkeypatch.setattr(safety, "_residents_away", lambda: False)
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)
    _stub_lockdown(monkeypatch, cognitive_core, active=False, auto=False)
    # even active manual:
    _stub_lockdown(monkeypatch, cognitive_core, active=True, auto=False)
    assert safety._confinement_degraded() is False


def test_degraded_false_when_no_lockdown(safety, cognitive_core, monkeypatch):
    monkeypatch.setattr(safety, "_residents_away", lambda: False)
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)
    monkeypatch.setattr(cognitive_core._CORE, "lockdown_mgr", None)
    assert safety._confinement_degraded() is False


def test_degraded_false_when_panel_readable_disarmed(
        safety, cognitive_core, monkeypatch, fake_hass):
    # Panel readable + disarmed → not indeterminate → not degraded. (In practice
    # the auto-lockdown would already have lifted; this guards the predicate.)
    monkeypatch.setattr(safety, "_residents_away", lambda: False)
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)
    fake_hass.states.set("alarm_control_panel.home", "disarmed")
    _stub_lockdown(monkeypatch, cognitive_core, active=True, auto=True)
    assert safety._confinement_degraded() is False


# ── confirmation-step hardening in _investigate_step ────────────────────────

def _investigation(now):
    return {
        "start": now, "last_motion": now,
        "zones": {"kitchen"}, "path": ["kitchen"], "escalated": False,
        "breach_area": None, "breach_name": None,
        "connected": set(), "hops": {}, "max_depth": 0,
    }


def _wire_investigation(safety, monkeypatch, *, degraded):
    monkeypatch.setattr(safety, "_resident_on_camera", lambda: False)
    monkeypatch.setattr(safety, "_qualifying_motion",
                        lambda sleeping: [("binary_sensor.kitchen_motion", "kitchen")])
    monkeypatch.setattr(safety, "_motion_key", lambda eid: "kitchen")
    monkeypatch.setattr(safety, "_person_camera_entity",
                        lambda indoor_only=True: "camera.kitchen")

    async def _vision_none(cam):
        return None  # inconclusive / unavailable
    monkeypatch.setattr(safety, "_confirm_person_with_vision", _vision_none)
    monkeypatch.setattr(safety, "_confinement_degraded", lambda: degraded)


async def test_degraded_vision_inconclusive_does_not_confirm(safety, monkeypatch):
    import time
    now = time.time()
    _wire_investigation(safety, monkeypatch, degraded=True)
    safety._investigation = _investigation(now)
    # Degraded hold + camera present + vision inconclusive + no inward route →
    # must NOT escalate to a confirmed critical intrusion.
    action = await safety._investigate_step(now, away=True, sleeping=False)
    assert action is None
    assert safety._investigation is not None            # still investigating
    assert safety._investigation["escalated"] is False


async def test_genuine_away_vision_inconclusive_still_confirms(safety, monkeypatch):
    import time
    now = time.time()
    _wire_investigation(safety, monkeypatch, degraded=False)
    safety._investigation = _investigation(now)
    # Genuine away + vision inconclusive → fail toward safety (trust the camera),
    # so a broken vision path never suppresses a real alert.
    action = await safety._investigate_step(now, away=True, sleeping=False)
    assert action is not None
    assert action["type"] == "intrusion_confirmed"
    assert safety._investigation["escalated"] is True


async def test_kill_switch_off_restores_fail_open(safety, cognitive_core, monkeypatch):
    import time
    now = time.time()
    monkeypatch.setattr(cognitive_core, "INTRUSION_DEGRADED_CONFIRM_HARDEN", False)
    _wire_investigation(safety, monkeypatch, degraded=True)
    safety._investigation = _investigation(now)
    # Kill-switch off → prior always-fail-open behavior even under a degraded hold.
    action = await safety._investigate_step(now, away=True, sleeping=False)
    assert action is not None
    assert action["type"] == "intrusion_confirmed"


async def test_degraded_positive_vision_still_escalates(safety, monkeypatch):
    import time
    now = time.time()
    _wire_investigation(safety, monkeypatch, degraded=True)

    async def _vision_yes(cam):
        return True
    monkeypatch.setattr(safety, "_confirm_person_with_vision", _vision_yes)
    safety._investigation = _investigation(now)
    # A POSITIVE vision person-confirm escalates immediately, even under a degraded
    # hold — the hardening only removes the blind fail-open, never a real sighting.
    action = await safety._investigate_step(now, away=True, sleeping=False)
    assert action is not None
    assert action["type"] == "intrusion_confirmed"
