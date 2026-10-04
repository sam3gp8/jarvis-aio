"""MCU Phase C (C2): parity tests for the intrusion/safety presence reads that
now route through the kernel WorldModel facade.

``SafetyManager._residents_away`` is the signal that decides whether motion is a
possible intruder, so a regression here reintroduces the false "motion … while no
one is home" class of bug. These tests lock the migration to *behaviour-identical*:
the WorldModel path and the legacy ``hass.states.async_all`` sweep must return the
same answer for every presence/alarm/occupancy combination, and a facade failure
must fall back to the exact legacy decision.
"""
import pytest


# ── legacy reference implementations (verbatim pre-C2 logic) ─────────────────────
# These mirror the code as it read BEFORE routing through WorldModel. The test
# asserts the migrated methods agree with them across a state matrix, so the
# reference is the oracle the live decision can never silently drift from.

def _legacy_residents_away(hass) -> bool:
    for st in hass.states.async_all("person"):
        if str(st.state).lower() == "home":
            return False
    for st in hass.states.async_all("device_tracker"):
        if str(st.state).lower() == "home":
            return False
    for st in hass.states.async_all("alarm_control_panel"):
        if str(st.state).lower() in ("armed_away", "armed_vacation"):
            return True
    tracked = False
    for st in hass.states.async_all("person"):
        tracked = True
    for st in hass.states.async_all("device_tracker"):
        if str(st.state).lower() in ("home", "not_home", "away"):
            tracked = True
    return tracked


def _legacy_anyone_home(hass) -> bool:
    for st in hass.states.async_all("person"):
        if str(st.state).lower() == "home":
            return True
    for st in hass.states.async_all("device_tracker"):
        if str(st.state).lower() == "home":
            return True
    occ_on = ("on", "detected", "occupied", "home", "true")
    for st in hass.states.async_all("binary_sensor"):
        if (st.attributes.get("device_class") in ("occupancy", "motion", "presence")
                and str(st.state).lower() in occ_on):
            return True
    return False


def _legacy_alarm_armed(hass, armed_states) -> bool:
    for st in hass.states.async_all("alarm_control_panel"):
        if st.state in armed_states:
            return True
    return False


# ── a matrix of realistic presence/alarm/occupancy worlds ───────────────────────

def _seed(hass, spec):
    for eid, (state, attrs) in spec.items():
        hass.states.set(eid, state, **attrs)


_WORLDS = [
    # empty world — no trackers at all (the untracked-resident case)
    {},
    # someone home by person
    {"person.sam": ("home", {})},
    # person away, phone away (tracked away)
    {"person.sam": ("not_home", {}), "device_tracker.sam_phone": ("not_home", {})},
    # device_tracker home but person away
    {"person.sam": ("not_home", {}), "device_tracker.sam_phone": ("home", {})},
    # armed-away alarm, no trackers
    {"alarm_control_panel.home": ("armed_away", {})},
    # armed-vacation alarm
    {"alarm_control_panel.home": ("armed_vacation", {})},
    # armed-home alarm (NOT an away state)
    {"alarm_control_panel.home": ("armed_home", {})},
    # disarmed alarm, person away
    {"alarm_control_panel.home": ("disarmed", {}), "person.sam": ("not_home", {})},
    # device_tracker 'away' literal (tracked, not home)
    {"device_tracker.sam_phone": ("away", {})},
    # mixed case states exercise the .lower() normalisation
    {"person.sam": ("Home", {}), "device_tracker.x": ("NOT_HOME", {})},
    # occupancy sensor on (matters only for _anyone_home)
    {"binary_sensor.hall": ("on", {"device_class": "occupancy"})},
    # motion sensor on, nobody tracked
    {"binary_sensor.hall": ("on", {"device_class": "motion"}),
     "person.sam": ("not_home", {})},
    # a non-presence binary_sensor must not count as home
    {"binary_sensor.door": ("on", {"device_class": "door"})},
    # everything at once
    {"person.sam": ("not_home", {}), "device_tracker.sam_phone": ("not_home", {}),
     "alarm_control_panel.home": ("armed_away", {}),
     "binary_sensor.hall": ("on", {"device_class": "motion"})},
]


@pytest.mark.parametrize("world", _WORLDS)
def test_residents_away_worldmodel_parity(cognitive_core, fake_hass, world):
    _seed(fake_hass, world)
    safety = cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})
    assert safety._residents_away() == _legacy_residents_away(fake_hass)


@pytest.mark.parametrize("world", _WORLDS)
def test_anyone_home_worldmodel_parity(cognitive_core, fake_hass, world):
    _seed(fake_hass, world)
    lockdown = cognitive_core.LockdownManager(fake_hass, {"honorific": "sir"})
    assert lockdown._anyone_home() == _legacy_anyone_home(fake_hass)


@pytest.mark.parametrize("world", _WORLDS)
def test_alarm_armed_worldmodel_parity(cognitive_core, fake_hass, world):
    _seed(fake_hass, world)
    safety = cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})
    assert safety._alarm_armed() == _legacy_alarm_armed(
        fake_hass, cognitive_core.ALARM_ARMED_STATES)


# ── fallback: a facade failure must yield the identical legacy decision ──────────

def test_residents_away_falls_back_to_raw_sweep(cognitive_core, fake_hass, monkeypatch):
    # person away + armed-away alarm → legacy decision is "away" (True).
    fake_hass.states.set("person.sam", "not_home")
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    safety = cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})

    # Break the facade: WorldModel.devices blows up for every call.
    def _boom(self, **kw):
        raise RuntimeError("facade down")
    monkeypatch.setattr(cognitive_core.WorldModel, "devices", _boom)

    # The method must NOT raise and must match the legacy sweep exactly.
    assert safety._residents_away() == _legacy_residents_away(fake_hass) is True


def test_anyone_home_falls_back_to_raw_sweep(cognitive_core, fake_hass, monkeypatch):
    fake_hass.states.set("person.sam", "home")
    lockdown = cognitive_core.LockdownManager(fake_hass, {"honorific": "sir"})

    def _boom(self, **kw):
        raise RuntimeError("facade down")
    monkeypatch.setattr(cognitive_core.WorldModel, "devices", _boom)

    assert lockdown._anyone_home() == _legacy_anyone_home(fake_hass) is True


def test_alarm_armed_falls_back_to_raw_sweep(cognitive_core, fake_hass, monkeypatch):
    fake_hass.states.set("alarm_control_panel.home", "armed_away")
    safety = cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})

    def _boom(self, **kw):
        raise RuntimeError("facade down")
    monkeypatch.setattr(cognitive_core.WorldModel, "devices", _boom)

    assert safety._alarm_armed() is True
