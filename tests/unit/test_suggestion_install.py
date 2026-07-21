"""Tests for closing the pattern-engine loop (v6.52.0): a learned suggestion,
once approved, actually becomes a Home Assistant automation instead of just
flipping a DB flag. Covers the pure normalizer across every pattern shape the
analyzer emits, and the async installer wiring end-to-end."""
import json

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


# ── normalize_suggestion_automation (pure) ───────────────────────────────────

def test_time_routine_normalizes_and_modernizes(pa):
    stored = json.dumps({
        "alias": "JARVIS Learned: light.porch on at 18:00",
        "trigger": {"platform": "time", "at": "18:00:00"},
        "action": {"service": "light.turn_on", "entity_id": "light.porch"},
    })
    out = pa.normalize_suggestion_automation(stored)
    assert out["installable"] is True
    # platform → trigger, service → action (HA modernization)
    assert out["trigger"][0]["trigger"] == "time"
    assert "platform" not in out["trigger"][0]
    assert out["action"][0]["action"] == "light.turn_on"
    assert "service" not in out["action"][0]


def test_sequence_preserves_delay_action_item(pa):
    stored = json.dumps({
        "alias": "JARVIS Learned: light.hall after binary_sensor.door",
        "trigger": {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"},
        "action": [
            {"delay": "00:01:00"},
            {"service": "light.turn_on", "entity_id": "light.hall"},
        ],
    })
    out = pa.normalize_suggestion_automation(stored)
    assert out["installable"] is True
    assert out["action"][0] == {"delay": "00:01:00"}          # delay untouched
    assert out["action"][1]["action"] == "light.turn_on"      # service modernized


def test_presence_pattern_installable(pa):
    stored = json.dumps({
        "alias": "JARVIS Learned: light.entry when person.sam home",
        "trigger": {"platform": "state", "entity_id": "person.sam", "to": "home"},
        "action": {"service": "light.turn_on", "entity_id": "light.entry"},
    })
    out = pa.normalize_suggestion_automation(stored)
    assert out["installable"] is True
    assert out["alias"].startswith("JARVIS Learned")


def test_manual_review_is_not_installable(pa):
    stored = json.dumps({"note": "Consider automating: 'goodnight' at 23:00",
                         "type": "manual_review"})
    out = pa.normalize_suggestion_automation(stored)
    assert out["installable"] is False
    assert "advisory" in out["reason"]


def test_bare_note_not_installable(pa):
    out = pa.normalize_suggestion_automation(json.dumps({"note": "some description"}))
    assert out["installable"] is False


def test_malformed_payloads_rejected(pa):
    assert pa.normalize_suggestion_automation("")["installable"] is False
    assert pa.normalize_suggestion_automation("not json{")["installable"] is False
    assert pa.normalize_suggestion_automation("[1,2,3]")["installable"] is False
    assert pa.normalize_suggestion_automation(
        json.dumps({"alias": "x"}))["installable"] is False   # missing trigger/action


# ── service_for: domain-aware service resolution (v6.52.1) ───────────────────

def test_service_for_onoff_domains(pa):
    assert pa.service_for("light.porch", "on") == {"service": "light.turn_on", "entity_id": "light.porch"}
    assert pa.service_for("switch.pump", "off") == {"service": "switch.turn_off", "entity_id": "switch.pump"}
    assert pa.service_for("fan.attic", "on")["service"] == "fan.turn_on"


def test_service_for_lock_uses_lock_service_not_turn(pa):
    """The flagship bug: a learned door-lock routine must NOT become
    lock.turn_locked. It uses lock.lock / lock.unlock."""
    assert pa.service_for("lock.front", "locked") == {"service": "lock.lock", "entity_id": "lock.front"}
    assert pa.service_for("lock.front", "unlocked") == {"service": "lock.unlock", "entity_id": "lock.front"}


def test_service_for_cover_uses_open_close(pa):
    assert pa.service_for("cover.garage", "open")["service"] == "cover.open_cover"
    assert pa.service_for("cover.garage", "closed")["service"] == "cover.close_cover"
    # transient states settle to the intended end state
    assert pa.service_for("cover.garage", "opening")["service"] == "cover.open_cover"


def test_service_for_unmappable_returns_none(pa):
    assert pa.service_for("climate.hall", "heat") is None      # needs params
    assert pa.service_for("media_player.tv", "playing") is None
    assert pa.service_for("light.x", "weird_state") is None     # not on/off
    assert pa.service_for("notanentity", "on") is None
    assert pa.service_for("", "on") is None


def test_sequence_with_lock_installs_valid_service(pa):
    """End-to-end: a lock sequence generates YAML that normalizes to a real
    lock.lock action — the whole point of the fix."""
    # simulate the generator output shape for a lock sequence
    stored = json.dumps({
        "alias": "JARVIS Learned: lock.front after cover.garage",
        "trigger": {"platform": "state", "entity_id": "cover.garage", "to": "closed"},
        "action": [{"delay": "00:01:00"},
                   pa.service_for("lock.front", "locked")],
    })
    out = pa.normalize_suggestion_automation(stored)
    assert out["installable"] is True
    lock_action = out["action"][1]
    assert lock_action["action"] == "lock.lock"      # service→action modernized
    assert "turn_" not in lock_action["action"]      # the bug is gone


# ── install_approved_suggestion (async wiring) ───────────────────────────────

class _StubAnalyzer:
    def __init__(self, suggestion):
        self._sug = suggestion
        self.approved = None
        self.installed = None

    def get_suggestion(self, sid):
        return self._sug

    def approve_suggestion(self, sid):
        self.approved = sid
        return True

    def mark_installed(self, sid, auto_id):
        self.installed = (sid, auto_id)


async def test_installer_installs_concrete_suggestion(pa, fake_hass, monkeypatch):
    sug = {"id": 7, "description": "learned",
           "automation_yaml": json.dumps({
               "alias": "porch on at 18:00",
               "trigger": {"platform": "time", "at": "18:00:00"},
               "action": {"service": "light.turn_on", "entity_id": "light.porch"},
           })}
    stub = _StubAnalyzer(sug)
    monkeypatch.setattr(pa, "get_analyzer", lambda: stub)

    calls = {}
    async def _fake_create(hass, **kw):
        calls.update(kw)
        return {"success": True, "automation_id": "jarvis_auto_porch",
                "alias": "JARVIS · porch on at 18:00"}
    monkeypatch.setattr("jc.automation_creator.create_automation", _fake_create, raising=False)

    res = await pa.install_approved_suggestion(fake_hass, 7)
    assert res["ok"] is True and res["installed"] is True
    assert res["automation_id"] == "jarvis_auto_porch"
    assert stub.approved == 7                       # approval recorded
    assert stub.installed == (7, "jarvis_auto_porch")
    assert calls["alias"] == "porch on at 18:00"    # normalized args passed through


async def test_installer_advisory_approves_without_install(pa, fake_hass, monkeypatch):
    sug = {"id": 9, "description": "note",
           "automation_yaml": json.dumps({"note": "x", "type": "manual_review"})}
    stub = _StubAnalyzer(sug)
    monkeypatch.setattr(pa, "get_analyzer", lambda: stub)

    called = {"n": 0}
    async def _fake_create(hass, **kw):
        called["n"] += 1
        return {"success": True, "automation_id": "x", "alias": "x"}
    monkeypatch.setattr("jc.automation_creator.create_automation", _fake_create, raising=False)

    res = await pa.install_approved_suggestion(fake_hass, 9)
    assert res["ok"] is True and res["installed"] is False
    assert "advisory" in res["reason"]
    assert stub.approved == 9        # still acknowledged
    assert stub.installed is None    # nothing written
    assert called["n"] == 0          # create_automation never called


async def test_installer_missing_suggestion(pa, fake_hass, monkeypatch):
    stub = _StubAnalyzer(None)
    monkeypatch.setattr(pa, "get_analyzer", lambda: stub)
    res = await pa.install_approved_suggestion(fake_hass, 404)
    assert res["ok"] is False and "not found" in res["error"]


async def test_installer_reports_write_failure(pa, fake_hass, monkeypatch):
    sug = {"id": 3, "description": "d",
           "automation_yaml": json.dumps({
               "alias": "a", "trigger": {"platform": "time", "at": "18:00:00"},
               "action": {"service": "light.turn_on", "entity_id": "light.p"}})}
    stub = _StubAnalyzer(sug)
    monkeypatch.setattr(pa, "get_analyzer", lambda: stub)

    async def _fail_create(hass, **kw):
        return {"success": False, "error": "disk full"}
    monkeypatch.setattr("jc.automation_creator.create_automation", _fail_create, raising=False)

    res = await pa.install_approved_suggestion(fake_hass, 3)
    assert res["ok"] is True and res["installed"] is False
    assert "disk full" in res["reason"]
    assert stub.approved == 3        # approved, just not installed
    assert stub.installed is None
