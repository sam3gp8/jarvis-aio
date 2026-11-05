"""Tests for whole-house energy management (v6.62.0). The safety-relevant parts
get the most coverage: the agency ladder (advisory/opt_in/autonomous), the
never-shed guarantee for critical loads, over-peak detection, how the proactive
offer is shaped per agency level, and the opt-in mode bump."""
import pytest


@pytest.fixture
def energy(load, monkeypatch):
    m = load("energy")
    # default config accessor returns provided defaults unless overridden per-test
    return m


@pytest.fixture(autouse=True)
def _clean_modes_stub():
    """Ensure no jc.modes stub leaks into or out of a test — several tests
    inject one, and energy._mode_agency_bonus does `from . import modes`, so a
    stray stub would pollute neighbours in either direction."""
    import sys
    sys.modules.pop("jc.modes", None)
    yield
    sys.modules.pop("jc.modes", None)


class _State:
    def __init__(self, state, unit="W", entity_id=None, **attrs):
        self.entity_id = entity_id or "sensor.mock"
        self.state = state
        self.attributes = {"unit_of_measurement": unit, **attrs}


class _Hass:
    def __init__(self, states=None, by_domain=None):
        self._states = states or {}
        self._by = by_domain or {}
    @property
    def states(self):
        outer = self
        class _States:
            def get(self, eid):
                return outer._states.get(eid)
            def async_all(self, domain):
                return outer._by.get(domain, [])
        return _States()


def _cfg_map(energy, mapping):
    """Override energy._cfg to read from a dict."""
    return lambda k, d=None: mapping.get(k, d)


# ── agency ladder ────────────────────────────────────────────────────────────

def test_default_agency_advisory(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {}))
    assert energy.effective_agency() == "advisory"


def test_configured_agency_in_force(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_agency": "autonomous"}))
    assert energy.effective_agency() == "autonomous"


def test_bad_agency_falls_back_advisory(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_agency": "nonsense"}))
    assert energy.effective_agency() == "advisory"


def test_mode_bump_empty_by_default_no_change(energy, monkeypatch):
    # advisory user, a mode wants a bump, but bump list empty → bonus is 0
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_agency": "advisory"}))
    monkeypatch.setattr(energy, "_mode_agency_bonus", lambda: 0)
    assert energy.effective_agency() == "advisory"      # no surprise bump


def test_mode_bump_raises_one_step_when_opted_in(energy, monkeypatch):
    # advisory ceiling + an active bump mode (bonus 1) → opt_in
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_agency": "advisory"}))
    monkeypatch.setattr(energy, "_mode_agency_bonus", lambda: 1)
    assert energy.effective_agency() == "opt_in"        # advisory + 1 step


def test_mode_bump_capped_at_autonomous(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_agency": "autonomous"}))
    monkeypatch.setattr(energy, "_mode_agency_bonus", lambda: 1)
    assert energy.effective_agency() == "autonomous"    # can't exceed top


def test_mode_bonus_reads_configured_bump_list(energy, monkeypatch):
    # _mode_agency_bonus itself: returns 1 only when active mode is in the list
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_mode_bump": ["away"]}))
    import sys, types
    modes_stub = types.ModuleType("jc.modes")
    modes_stub.active_mode = lambda: "away"
    monkeypatch.setitem(sys.modules, "jc.modes", modes_stub)
    monkeypatch.setattr(sys.modules["jc"], "modes", modes_stub, raising=False)
    assert energy._mode_agency_bonus() == 1
    modes_stub.active_mode = lambda: "normal"
    assert energy._mode_agency_bonus() == 0             # not in bump list


# ── never-shed guarantee ─────────────────────────────────────────────────────

def test_never_shed_critical_loads(energy):
    assert energy._never_shed("switch.cpap_machine", "CPAP") is True
    assert energy._never_shed("sensor.fridge_power", "Refrigerator") is True
    assert energy._never_shed("switch.network_rack", "Server") is True
    assert energy._never_shed("switch.sump_pump", "Sump Pump") is True


def test_ordinary_loads_are_sheddable(energy):
    assert energy._never_shed("switch.dryer", "Clothes Dryer") is False
    assert energy._never_shed("switch.dishwasher", "Dishwasher") is False


# ── peak threshold ───────────────────────────────────────────────────────────

def test_peak_threshold_default(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {}))
    assert energy._peak_threshold() == 8000.0


def test_peak_threshold_configured(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_peak_watts": 5000}))
    assert energy._peak_threshold() == 5000.0


# ── proactive offer shaping per agency ───────────────────────────────────────

def _over_peak_status(energy, monkeypatch, agency):
    """Force a power_status that is over peak with two sheddable loads."""
    monkeypatch.setattr(energy, "effective_agency", lambda: agency)
    monkeypatch.setattr(energy, "power_status", lambda h: {
        "watts": 9000, "kw": 9.0, "over_peak": True, "agency": agency,
        "running": [
            {"name": "Dryer", "entity": "switch.dryer", "watts": 4000, "shed_ok": True},
            {"name": "Oven", "entity": "switch.oven", "watts": 5000, "shed_ok": True},
        ],
    })


def test_advisory_only_informs(energy, monkeypatch):
    _over_peak_status(energy, monkeypatch, "advisory")
    offer = energy.evaluate_for_proactive(_Hass())
    assert offer is not None
    assert offer["type"] == "energy_advice"
    assert offer["auto_act"] is False


def test_opt_in_proposes_action(energy, monkeypatch):
    _over_peak_status(energy, monkeypatch, "opt_in")
    offer = energy.evaluate_for_proactive(_Hass())
    assert offer["type"] == "energy_shed_offer"
    assert offer["auto_act"] is False
    assert offer.get("entity_id")            # names a specific load to hold


def test_autonomous_auto_acts(energy, monkeypatch):
    _over_peak_status(energy, monkeypatch, "autonomous")
    offer = energy.evaluate_for_proactive(_Hass())
    assert offer["type"] == "energy_shed"
    assert offer["auto_act"] is True


def test_no_offer_when_under_peak(energy, monkeypatch):
    monkeypatch.setattr(energy, "power_status", lambda h: {"over_peak": False, "running": []})
    assert energy.evaluate_for_proactive(_Hass()) is None


def test_no_offer_single_load(energy, monkeypatch):
    # over peak but only ONE sheddable load — nothing to stagger
    monkeypatch.setattr(energy, "power_status", lambda h: {
        "over_peak": True, "agency": "autonomous",
        "running": [{"name": "Oven", "entity": "switch.oven", "watts": 9000, "shed_ok": True}],
    })
    assert energy.evaluate_for_proactive(_Hass()) is None


def test_autonomous_never_sheds_critical(energy, monkeypatch):
    # two loads over peak, but both are never-shed → no auto action
    monkeypatch.setattr(energy, "effective_agency", lambda: "autonomous")
    monkeypatch.setattr(energy, "power_status", lambda h: {
        "over_peak": True, "agency": "autonomous",
        "running": [
            {"name": "Fridge", "entity": "sensor.fridge", "watts": 4000, "shed_ok": False},
            {"name": "CPAP", "entity": "switch.cpap", "watts": 5000, "shed_ok": False},
        ],
    })
    # no sheddable loads → no offer at all
    assert energy.evaluate_for_proactive(_Hass()) is None


def test_find_whole_home_meter_prefers_suffix_free_sensor(energy):
    states = {
        "sensor.electric_consumption_1": _State("4200", "W", entity_id="sensor.electric_consumption_1", friendly_name="Electric Consumption (1)", device_class="power"),
        "sensor.electric_consumption": _State("4000", "W", entity_id="sensor.electric_consumption", friendly_name="Electric Consumption", device_class="power"),
    }
    assert energy._find_whole_home_meter(states) == "sensor.electric_consumption"


def test_fallback_meter_ignores_non_sensor_snapshot_entries(energy):
    states = {
        "light.main_meter": _State("4100", "W", entity_id="light.main_meter", friendly_name="Main meter", device_class="power"),
        "sensor.main_meter": _State("3900", "W", entity_id="sensor.main_meter", friendly_name="Main meter", device_class="power"),
    }
    assert energy._fallback_meter(_Hass(), states) == "sensor.main_meter"


# ── agent tool registration ──────────────────────────────────────────────────

def test_energy_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "energy_status" in names
    assert "energy_status" in agent._TOOL_MAP


# ── #476: configurable whole-home power sensor + signed import/export ─────────
def test_configured_power_sensor_overrides_discovery(energy, monkeypatch):
    # An explicit override must win over auto-discovery (which could latch onto a
    # Powerwall max-discharge entity and cry "over peak").
    monkeypatch.setattr(energy, "_cfg",
                        _cfg_map(energy, {"energy_power_sensor": "sensor.whole_home_power"}))
    states = {
        "sensor.whole_home_power": _State("2800", unit="W", entity_id="sensor.whole_home_power"),
        "sensor.powerwall_max_discharge": _State(
            "9000", unit="W", entity_id="sensor.powerwall_max_discharge",
            device_class="power", friendly_name="Powerwall max discharge capability"),
    }
    val, eid = energy._read_whole_home_watts(None, states)
    assert eid == "sensor.whole_home_power"
    assert val == 2800.0


def test_configured_sensor_kw_normalised(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_power_sensor": "sensor.grid"}))
    val, eid = energy._read_whole_home_watts(None, {"sensor.grid": _State("2.8", unit="kW", entity_id="sensor.grid")})
    assert val == 2800.0 and eid == "sensor.grid"


def test_misconfigured_override_shows_no_reading(energy, monkeypatch):
    # A non-sensor override is honoured (no reading) rather than silently reverting
    # to the mis-detected auto sensor.
    monkeypatch.setattr(energy, "_cfg", _cfg_map(energy, {"energy_power_sensor": "not_a_sensor"}))
    val, eid = energy._read_whole_home_watts(None, {})
    assert val is None and eid == "not_a_sensor"


def test_report_scenario_2800w_under_8kw_is_not_over_peak(energy, monkeypatch):
    # The exact issue: 2.8 kW on the correct sensor must NOT alert against an 8 kW peak.
    monkeypatch.setattr(energy, "_cfg", _cfg_map(
        energy, {"energy_power_sensor": "sensor.grid", "energy_peak_watts": 8000}))
    monkeypatch.setattr(energy, "_running_appliances", lambda h, s=None: [])
    st = energy.power_status(None, {"sensor.grid": _State("2800", unit="W", entity_id="sensor.grid")})
    assert st["over_peak"] is False
    assert st["watts"] == 2800
    assert st["exporting"] is False
    assert st["configured_meter"] == "sensor.grid"


def test_export_never_triggers_over_peak(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(
        energy, {"energy_power_sensor": "sensor.grid", "energy_peak_watts": 8000}))
    monkeypatch.setattr(energy, "_running_appliances", lambda h, s=None: [])
    st = energy.power_status(None, {"sensor.grid": _State("-3000", unit="W", entity_id="sensor.grid")})
    assert st["over_peak"] is False
    assert st["exporting"] is True
    assert st["watts"] == 0                 # import clamped to 0
    assert st["signed_watts"] == -3000


def test_import_over_peak_still_alerts(energy, monkeypatch):
    monkeypatch.setattr(energy, "_cfg", _cfg_map(
        energy, {"energy_power_sensor": "sensor.grid", "energy_peak_watts": 8000}))
    monkeypatch.setattr(energy, "_running_appliances", lambda h, s=None: [])
    st = energy.power_status(None, {"sensor.grid": _State("9000", unit="W", entity_id="sensor.grid")})
    assert st["over_peak"] is True
    assert st["watts"] == 9000


def test_power_sensor_candidates_lists_power_only(energy):
    states = {
        "sensor.grid": _State("2800", unit="W", entity_id="sensor.grid",
                              device_class="power", friendly_name="Grid Power"),
        "sensor.solar": _State("1.5", unit="kW", entity_id="sensor.solar", friendly_name="Solar"),
        "sensor.temp": _State("70", unit="°F", entity_id="sensor.temp",
                              device_class="temperature", friendly_name="Temp"),
    }
    cands = energy.power_sensor_candidates(None, states)
    ents = [c["entity"] for c in cands]
    assert "sensor.grid" in ents and "sensor.solar" in ents
    assert "sensor.temp" not in ents
    grid = next(c for c in cands if c["entity"] == "sensor.grid")
    assert grid["watts"] == 2800
