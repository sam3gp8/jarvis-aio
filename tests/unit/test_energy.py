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
    def __init__(self, state, unit="W", **attrs):
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


# ── agent tool registration ──────────────────────────────────────────────────

def test_energy_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "energy_status" in names
    assert "energy_status" in agent._TOOL_MAP
