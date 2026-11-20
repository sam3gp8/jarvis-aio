"""#554 — VoIP/SIP call-mode detection (`call_mode.is_call`).

`is_call` decides whether a conversation turn arrived over a VoIP/SIP call (so
JARVIS runs it full-duplex, bypassing the always-listening-satellite ambient
gates) versus a wake-word satellite or typed input. It is deliberately
conservative: True only when the originating device resolves to a configured call
integration; anything missing/unknown/erroring is False so existing behaviour is
untouched.
"""
import types

import pytest


@pytest.fixture
def cm(load):
    return load("call_mode")


def _hass_with(entries):
    """A fake hass whose config_entries.async_get_entry resolves `entries`."""
    return types.SimpleNamespace(
        config_entries=types.SimpleNamespace(
            async_get_entry=lambda eid: entries.get(eid)))


def _point_registry_at(monkeypatch, device):
    """Make the stubbed device_registry.async_get return a registry whose
    async_get(device_id) yields `device`."""
    import homeassistant.helpers.device_registry as drmod
    monkeypatch.setattr(
        drmod, "async_get",
        lambda hass: types.SimpleNamespace(async_get=lambda did: device),
        raising=False)


# ── no device / typed input ─────────────────────────────────────────────────
def test_no_device_id_is_never_a_call(cm):
    assert cm.is_call(object(), None) is False
    assert cm.is_call(object(), "") is False


def test_no_hass_is_never_a_call(cm):
    assert cm.is_call(None, "phone-1") is False


# ── positive: a VoIP device ─────────────────────────────────────────────────
def test_voip_integration_device_is_a_call(cm, monkeypatch):
    dev = types.SimpleNamespace(config_entries={"e1"})
    _point_registry_at(monkeypatch, dev)
    hass = _hass_with({"e1": types.SimpleNamespace(domain="voip")})
    assert cm.is_call(hass, "phone-1") is True


# ── negative: a wake-word satellite / other integration ─────────────────────
def test_satellite_device_is_not_a_call(cm, monkeypatch):
    dev = types.SimpleNamespace(config_entries={"e2"})
    _point_registry_at(monkeypatch, dev)
    hass = _hass_with({"e2": types.SimpleNamespace(domain="esphome")})
    assert cm.is_call(hass, "sat-1") is False


def test_unknown_device_is_not_a_call(cm, monkeypatch):
    _point_registry_at(monkeypatch, None)   # registry has no such device
    hass = _hass_with({})
    assert cm.is_call(hass, "ghost") is False


def test_registry_error_degrades_to_not_a_call(cm, monkeypatch):
    import homeassistant.helpers.device_registry as drmod

    def _boom(hass):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(drmod, "async_get", _boom, raising=False)
    assert cm.is_call(_hass_with({}), "x") is False


# ── configurable extra call integrations ────────────────────────────────────
def test_extra_call_integration_domain_from_config(cm, load, monkeypatch):
    """A third-party SIP HA component is recognised when added via the
    `call_integration_domains` config key (comma/semicolon separated)."""
    jc = load("jarvis_config")
    monkeypatch.setattr(
        jc, "get",
        lambda key, default=None: ("mysip, other_sip"
                                   if key == "call_integration_domains" else default),
        raising=False)
    dev = types.SimpleNamespace(config_entries={"e3"})
    _point_registry_at(monkeypatch, dev)
    hass = _hass_with({"e3": types.SimpleNamespace(domain="mysip")})
    assert cm.is_call(hass, "sip-1") is True
    # voip is still a call alongside the configured extras.
    assert "voip" in cm._call_domains(hass)


def test_first_matching_entry_wins_across_multiple(cm, monkeypatch):
    # A device can carry several config entries; any one in a call domain counts.
    dev = types.SimpleNamespace(config_entries=["other", "the_voip_one"])
    _point_registry_at(monkeypatch, dev)
    hass = _hass_with({
        "other": types.SimpleNamespace(domain="sensor"),
        "the_voip_one": types.SimpleNamespace(domain="voip"),
    })
    assert cm.is_call(hass, "multi") is True
