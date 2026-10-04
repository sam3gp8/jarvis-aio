"""MCU Phase R (R3b): the kernel intrusion entry-gate is AUTHORITATIVE, and it is
stricter than the legacy precondition — it adds a residents-tracked-home veto.

This fixes a real-world false-alarm class: a resident moving on camera *at home*,
while confinement was held open by an unavailable alarm panel, was investigated as
an intruder. The veto: an awake, positively tracked-home resident never opens an
investigation (at night the veto lifts, so a genuine break-in is still monitored).
Fail-safe to the legacy decision on any kernel fault; kill-switch
INTRUSION_GATE_ENFORCE.
"""
import pytest


@pytest.fixture
def sit(load):
    return load("kernel.situation")


# ── the pure kernel gate veto ───────────────────────────────────────────────

def test_veto_awake_resident_home_blocks_even_with_corroboration(sit):
    # away + motion + corroboration (open entry) would open — but an awake
    # tracked-home resident vetoes it.
    assert sit.intrusion_gate(
        away=True, qualifying_motion=True, require_corroboration=True,
        alarm_armed=False, open_entry=True,
        residents_tracked_home=True, sleeping=False) is False


def test_veto_lifts_when_sleeping(sit):
    # Asleep: the veto lifts, so a corroborated entry still opens for monitoring.
    assert sit.intrusion_gate(
        away=True, qualifying_motion=True, require_corroboration=True,
        alarm_armed=False, open_entry=True,
        residents_tracked_home=True, sleeping=True) is True


def test_no_resident_home_still_opens_on_corroboration(sit):
    assert sit.intrusion_gate(
        away=True, qualifying_motion=True, require_corroboration=True,
        alarm_armed=False, open_entry=True,
        residents_tracked_home=False, sleeping=False) is True


# ── SafetyManager helpers + gate ────────────────────────────────────────────

@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(
        fake_hass, {"honorific": "sir", "intrusion_require_corroboration": True})


def test_resident_tracked_home_true_when_person_home(safety, fake_hass):
    fake_hass.states.set("person.sam", "home")
    assert safety._resident_tracked_home() is True


def test_resident_tracked_home_false_when_away(safety, fake_hass):
    fake_hass.states.set("person.sam", "not_home")
    assert safety._resident_tracked_home() is False


def test_entry_gate_vetoes_under_enforce_when_resident_home(safety, monkeypatch):
    monkeypatch.setattr(safety, "_resident_tracked_home", lambda: True)
    # legacy_open True (corroborated), but enforce + veto → do not open.
    assert safety._intrusion_entry_gate(
        away=True, require_corroboration=True, alarm_armed=False,
        open_entry=True, sleeping=False, legacy_open=True) is False


def test_entry_gate_kill_switch_off_uses_legacy(cognitive_core, safety, monkeypatch):
    monkeypatch.setattr(cognitive_core, "INTRUSION_GATE_ENFORCE", False)
    monkeypatch.setattr(safety, "_resident_tracked_home", lambda: True)
    # Kill-switch off → legacy_open wins (the veto is ignored).
    assert safety._intrusion_entry_gate(
        away=True, require_corroboration=True, alarm_armed=False,
        open_entry=True, sleeping=False, legacy_open=True) is True


def test_entry_gate_fails_safe_to_legacy_on_kernel_error(sit, safety, monkeypatch):
    monkeypatch.setattr(safety, "_resident_tracked_home", lambda: True)
    monkeypatch.setattr(sit, "intrusion_gate",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    # Kernel fault → fall back to the legacy decision (here, open).
    assert safety._intrusion_entry_gate(
        away=True, require_corroboration=True, alarm_armed=False,
        open_entry=True, sleeping=False, legacy_open=True) is True


# ── end-to-end regression: the user's false alarm ───────────────────────────

async def _wire(safety, monkeypatch, *, resident_home: bool):
    monkeypatch.setattr(safety, "_residents_away", lambda: not resident_home)
    monkeypatch.setattr(safety, "_resident_tracked_home", lambda: resident_home)
    monkeypatch.setattr(safety, "_resident_on_camera", lambda: False)
    monkeypatch.setattr(safety, "_qualifying_motion",
                        lambda sleeping: [("binary_sensor.kitchen_motion", "the kitchen")])
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)      # alarm unavailable → not armed
    monkeypatch.setattr(safety, "_open_entry", lambda: "binary_sensor.kitchen_window")  # window open at home
    safety._last_intrusion_alert = 0.0
    safety._investigation = None


async def test_resident_home_under_held_lockdown_does_not_alarm(safety, monkeypatch):
    # The reported scenario: confinement held open by an unavailable alarm, a
    # resident tracked home + awake, a window open — a resident on camera must NOT
    # be investigated as an intruder.
    await _wire(safety, monkeypatch, resident_home=True)
    action = await safety._check_intrusion(anyone_home=True, sleeping=False, confined=True)
    assert action is None
    assert safety._investigation is None


async def test_genuinely_away_still_opens_investigation(safety, monkeypatch):
    # Contrast: nobody tracked home, same corroborated entry → it DOES open.
    await _wire(safety, monkeypatch, resident_home=False)
    action = await safety._check_intrusion(anyone_home=False, sleeping=False, confined=True)
    # An investigation opened (the initial alert fired).
    assert safety._investigation is not None
    assert action is not None
