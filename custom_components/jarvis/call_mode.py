"""JARVIS — VoIP/SIP call-mode detection (#554).

JARVIS should not provision or drive an ESP32 intercom's wake-word mic. Instead,
for the intercom use case it rides the SIP bridge that already exists outside
JARVIS — Home Assistant's built-in ``voip`` integration (a SIP↔Assist bridge), or
a third-party SIP component such as an esphome-intercom HA add-on — and acts as
the conversation brain over a full-duplex call.

A turn that arrives over such a call is, by definition, a caller speaking **to**
JARVIS over an open line. The ambient filters built for an always-listening
wake-word satellite (the presence gate and the relevance/ambient gate) and the
wake-word mic-reopen dance are satellite concerns: over a call they only gatekeep
the caller and stall the conversation (issue #554 — the intercom going "stuck
blue", the mic not reopening on a follow-up). So JARVIS runs a call full-duplex.

This module answers exactly one question: *did this turn come in over a call?*
It is deliberately conservative — it returns True only when the originating
device positively resolves to a configured call integration, so a wake-word
satellite, a typed panel message, or anything unknown keeps the existing
(ambient-filtered) behaviour untouched.
"""
from __future__ import annotations

# Integration domains whose devices are VoIP/SIP call endpoints. Home Assistant's
# built-in SIP↔Assist bridge is ``voip``; a third-party SIP HA component can be
# added through the ``call_integration_domains`` config key (comma-separated).
_DEFAULT_CALL_DOMAINS = ("voip",)


def _call_domains(hass) -> set:
    """The set of integration domains treated as VoIP/SIP calls. The built-in
    ``voip`` plus any extra domains the owner configured (comma/semicolon
    separated). Never raises."""
    domains = set(_DEFAULT_CALL_DOMAINS)
    try:
        from . import jarvis_config
        extra = jarvis_config.get("call_integration_domains", "")
        for part in str(extra or "").replace(";", ",").split(","):
            part = part.strip().lower()
            if part:
                domains.add(part)
    except Exception:
        pass
    return domains


def is_call(hass, device_id) -> bool:
    """True when this turn originates from a VoIP/SIP call device rather than a
    wake-word satellite or typed input.

    Conservative by design: returns True only when ``device_id`` resolves to a
    device owned by a configured call integration (:func:`_call_domains`). A
    missing device, a device from another integration, a missing registry, or any
    error all yield False, so the caller keeps its existing ambient-filtered
    behaviour. Never raises.
    """
    if not hass or not device_id:
        return False
    try:
        from homeassistant.helpers import device_registry as dr
        dev = dr.async_get(hass).async_get(device_id)
    except Exception:
        return False
    if dev is None:
        return False
    domains = _call_domains(hass)
    try:
        entries = hass.config_entries
    except Exception:
        return False
    for entry_id in getattr(dev, "config_entries", None) or ():
        try:
            entry = entries.async_get_entry(entry_id)
        except Exception:
            entry = None
        if entry is not None and getattr(entry, "domain", None) in domains:
            return True
    return False
