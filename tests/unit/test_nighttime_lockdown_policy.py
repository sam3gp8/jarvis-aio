"""Issue #495 — nighttime lockdown must honor operator exclusions and must not
roll egress covers down over sleeping, occupied rooms.

The crude nighttime securing path (SafetyManager._nighttime_lockdown) used to:

  * close EVERY open cover of ANY device_class — including motorized window
    shutters/blinds that double as a fire-egress route — while residents were
    asleep INSIDE; and
  * actuate locks and covers the operator had explicitly EXCLUDED, because the
    safety seam fails open and never consults the exclusion list.

Both violate the integration's own operator-sovereignty philosophy (exclusions
are absolute; the intrusion path is already class/feature-restricted). This
suite locks in the fix: exclusions are honored, the default policy ("secure")
closes only perimeter openings (door/garage/gate), and the behaviour is
operator-configurable ("all" restores legacy, "off" closes no covers).
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def cc(load):
    # actuation is a sibling import of cognitive_core's safety seam; load it so
    # execute_safety_actuator is importable and un-gated.
    act = load("actuation")
    act._autonomous_budget = None
    act._autonomous_loop = None
    act._loop_detector = None
    act._agency_budget = None
    return load("cognitive_core")


def _exclude(hass, *, entities=None, domains=None):
    """Seed the runtime_config exclusion list the way the panel/setup does."""
    hass.data["jarvis"] = {
        "entry1": {
            "runtime_config": {
                "excluded_entities": list(entities or []),
                "excluded_domains": list(domains or []),
            }
        }
    }


def _closed(hass):
    return {c[2].get("entity_id") for c in hass.service_calls
            if c[0] == "cover" and c[1] == "close_cover"}


def _locked(hass):
    return {c[2].get("entity_id") for c in hass.service_calls
            if c[0] == "lock" and c[1] == "lock"}


# ── operator exclusions are absolute (LOTO) ──────────────────────────────────

async def test_excluded_lock_is_never_locked(cc):
    hass = FakeHass()
    _exclude(hass, entities=["lock.front"])
    hass.states.set("lock.front", "unlocked", friendly_name="Front Door")
    hass.states.set("lock.back", "unlocked", friendly_name="Back Door")
    mgr = cc.SafetyManager(hass, {"honorific": "sir"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _locked(hass) == {"lock.back"}          # excluded lock untouched


async def test_excluded_cover_is_never_closed(cc):
    hass = FakeHass()
    _exclude(hass, entities=["cover.patio"])
    # Both are perimeter "door" covers that the default policy WOULD close.
    hass.states.set("cover.patio", "open", device_class="door", friendly_name="Patio")
    hass.states.set("cover.side", "open", device_class="door", friendly_name="Side")
    mgr = cc.SafetyManager(hass, {"honorific": "sir"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == {"cover.side"}          # excluded cover untouched


async def test_excluded_domain_blocks_all_covers(cc):
    hass = FakeHass()
    _exclude(hass, domains=["cover"])
    hass.states.set("cover.garage", "open", device_class="garage")
    mgr = cc.SafetyManager(hass, {"honorific": "sir"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == set()


# ── default "secure" policy: perimeter openings only, never egress shutters ───

async def test_secure_mode_leaves_window_shutters_open(cc):
    hass = FakeHass()
    hass.states.set("cover.garage", "open", device_class="garage")
    hass.states.set("cover.front", "open", device_class="door")
    hass.states.set("cover.gate", "open", device_class="gate")
    # These must NOT be auto-closed over sleeping residents by default.
    hass.states.set("cover.bedroom", "open", device_class="shutter")
    hass.states.set("cover.kitchen", "open", device_class="blind")
    hass.states.set("cover.lounge", "open", device_class="shade")
    mgr = cc.SafetyManager(hass, {"honorific": "sir"})   # no key → default secure
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == {"cover.garage", "cover.front", "cover.gate"}


async def test_secure_mode_skips_unclassified_cover(cc):
    # A cover with no device_class isn't a known perimeter opening, so the safe
    # default leaves it alone rather than guessing it's a door.
    hass = FakeHass()
    hass.states.set("cover.mystery", "open")           # no device_class
    hass.states.set("cover.garage", "open", device_class="garage")
    mgr = cc.SafetyManager(hass, {"honorific": "sir"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == {"cover.garage"}


# ── "all" restores legacy behaviour; "off" disables cover-closing ────────────

async def test_all_mode_closes_every_open_cover(cc):
    hass = FakeHass()
    hass.states.set("cover.bedroom", "open", device_class="shutter")
    hass.states.set("cover.garage", "open", device_class="garage")
    hass.states.set("cover.mystery", "open")
    mgr = cc.SafetyManager(hass, {"nighttime_lockdown_covers": "all"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == {"cover.bedroom", "cover.garage", "cover.mystery"}


async def test_off_mode_closes_no_covers_but_still_locks(cc):
    hass = FakeHass()
    hass.states.set("cover.garage", "open", device_class="garage")
    hass.states.set("lock.front", "unlocked")
    mgr = cc.SafetyManager(hass, {"nighttime_lockdown_covers": "off"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == set()                   # covers left alone …
    assert _locked(hass) == {"lock.front"}          # … but locking still happens


async def test_unknown_mode_falls_back_to_secure(cc):
    hass = FakeHass()
    hass.states.set("cover.bedroom", "open", device_class="shutter")
    hass.states.set("cover.garage", "open", device_class="garage")
    mgr = cc.SafetyManager(hass, {"nighttime_lockdown_covers": "banana"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == {"cover.garage"}         # bad value → safe default


# ── only act on covers that actually advertise a CLOSE service ───────────────

async def test_cover_without_close_feature_is_skipped(cc):
    hass = FakeHass()
    # supported_features reported but CLOSE bit (2) absent → status-only opener.
    hass.states.set("cover.oneway", "open", device_class="garage", supported_features=1)
    hass.states.set("cover.garage", "open", device_class="garage", supported_features=3)
    mgr = cc.SafetyManager(hass, {"nighttime_lockdown_covers": "all"})
    await mgr._nighttime_lockdown()
    await hass.drain()
    assert _closed(hass) == {"cover.garage"}
