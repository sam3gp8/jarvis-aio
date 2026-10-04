"""Away false-alarm root fix (8.70.0): a HOME arming posture is not "away".

Arming the panel in a HOME mode (armed_home / armed_night) — or a lockdown held
through a Cove dropout that began from a home-mode arm — must NOT flip intrusion
into the "away" branch that treats a resident moving through the house as an
intruder. Only genuine tracked-away, an armed_away/vacation panel, or a
user-requested lockdown is an away posture.
"""
import types

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, cognitive_core):
    monkeypatch.setattr(cognitive_core, "LOCKDOWN_STATE_PATH",
                        str(tmp_path / "lockdown_state.json"))
    cognitive_core._CORE.lockdown_mgr = None
    cognitive_core._CORE.hass = None
    cognitive_core._CORE.config = {"honorific": "sir", "lockdown_auto_on_arm": True}
    cognitive_core._CORE.alarm_unsub = None
    yield


@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})


def _motion(hass, eid="binary_sensor.hall_motion"):
    hass.states.set(eid, "on", device_class="motion")


def _intrusions(actions):
    return [a for a in actions if str(a.get("type", "")).startswith("intrusion")]


# ── _confinement_is_home_posture() matrix (readable panel) ──────────────────

def test_readable_armed_home_is_home_posture(safety, fake_hass):
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    assert safety._confinement_is_home_posture() is True


def test_readable_armed_night_is_home_posture(safety, fake_hass):
    fake_hass.states.set("alarm_control_panel.home", "armed_night")
    assert safety._confinement_is_home_posture() is True


def test_readable_armed_away_is_not_home_posture(safety, fake_hass):
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    assert safety._confinement_is_home_posture() is False


def test_tracked_away_overrides_even_if_panel_home(safety, fake_hass):
    # A resident tracked away is genuinely away regardless of a stray armed_home.
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("device_tracker.sam_phone", "not_home")
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    assert safety._residents_away() is True
    assert safety._confinement_is_home_posture() is False


def test_manual_lockdown_is_not_home_posture(safety, cognitive_core, fake_hass):
    # A user-requested (manual, auto=False) lockdown is a deliberate away posture.
    cognitive_core._CORE.lockdown_mgr = types.SimpleNamespace(
        active=True, auto=False, arm_mode="")
    assert safety._confinement_is_home_posture() is False


def test_no_confinement_is_not_home_posture(safety, fake_hass):
    assert safety._confinement_is_home_posture() is False


# ── held through a Cove dropout: arm_mode carries the home/away distinction ──

async def test_arm_mode_captured_and_held_home(cognitive_core, fake_hass):
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    await cognitive_core.ensure_lockdown(fake_hass, cognitive_core._CORE.config)
    fake_hass.close_pending()
    mgr = cognitive_core._CORE.lockdown_mgr
    assert mgr.active and mgr.auto
    assert mgr.arm_mode == "armed_home"

    # Panel drops out → lockdown HELD, arm_mode preserved → still a home posture.
    fake_hass.states.set("alarm_control_panel.home", "unavailable")
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    assert cognitive_core.is_lockdown() is True
    assert safety._confinement_is_home_posture() is True


async def test_held_away_mode_stays_away(cognitive_core, fake_hass):
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    await cognitive_core.ensure_lockdown(fake_hass, cognitive_core._CORE.config)
    fake_hass.close_pending()
    assert cognitive_core._CORE.lockdown_mgr.arm_mode == "armed_away"

    fake_hass.states.set("alarm_control_panel.home", "unavailable")
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    # Held from an AWAY arm → NOT a home posture → still infers away.
    assert safety._confinement_is_home_posture() is False


# ── end-to-end: the live false alarm ────────────────────────────────────────

async def test_armed_home_held_through_dropout_does_not_alarm(cognitive_core, fake_hass):
    # armed_home engaged lockdown, the Cove then went unavailable, a resident moves
    # through the house with an open door — must NOT raise an intrusion.
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    await cognitive_core.ensure_lockdown(fake_hass, cognitive_core._CORE.config)
    fake_hass.close_pending()
    fake_hass.states.set("alarm_control_panel.home", "unavailable")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    safety._last_intrusion_alert = 0.0
    actions = await safety.tick(sleeping=False, anyone_home=True)
    fake_hass.close_pending()
    assert _intrusions(actions) == []


async def test_kill_switch_off_restores_away_on_armed_home(cognitive_core, fake_hass, monkeypatch):
    monkeypatch.setattr(cognitive_core, "INTRUSION_HOME_ARM_NOT_AWAY", False)
    fake_hass.states.set("alarm_control_panel.home", "armed_home")
    fake_hass.states.set("binary_sensor.front_door", "on", device_class="door")
    _motion(fake_hass)
    safety = cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_requires_confinement": True})
    safety._last_intrusion_alert = 0.0
    actions = await safety.tick(sleeping=False, anyone_home=True)
    fake_hass.close_pending()
    # Kill-switch off → prior behavior (armed_home treated as away) → fires.
    assert len(_intrusions(actions)) == 1
