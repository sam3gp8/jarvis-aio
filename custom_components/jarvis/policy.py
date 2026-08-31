"""Action authorization policy — the single gate every actuation routes through.

Before JARVIS calls a Home Assistant service that changes state, the request
passes through here. Two questions are answered:

  * how risky is the action (low / medium / high / critical), and
  * may it proceed right now, or must it be confirmed first?

Confirmation itself is delegated to :mod:`voice_confirm` (unchanged mechanism
and unchanged opt-in: it only ever asks when ``voice_confirm_enabled`` is on).
The guarantee this layer adds is **fail-closed for authority**: if a protected
action needs confirmation and the confirmation path errors, the action is
DENIED rather than allowed through. Convenience actions (lights, climate,
media) are classified LOW and never gain friction.

This also closes a gap where ``bulk_control`` and ``execute_plan`` could
actuate locks / covers / alarms with no confirmation at all — they now route
through the same gate.
"""
from __future__ import annotations

import logging
from typing import Tuple

_LOGGER = logging.getLogger(__name__)

# Precise, high-signal (domain, service) pairs. The capability net below catches
# novel/unenumerated services so a new one can't default to LOW just because it
# isn't listed here.
_CRITICAL = {
    ("alarm_control_panel", "alarm_disarm"),
}
_HIGH = {
    ("lock", "unlock"),
}
_MEDIUM = {
    ("cover", "open_cover"),                # garage doors are covers
    ("cover", "open"),
    ("alarm_control_panel", "alarm_arm_away"),
    ("alarm_control_panel", "alarm_arm_home"),
    ("alarm_control_panel", "alarm_arm_night"),
    ("lock", "open"),                       # some locks support latch/open
}

# Domains that are security-relevant by their very nature: an actuating service
# on them is escalated even when we haven't enumerated it, so a future service
# (e.g. lock.unlatch) can't slip through as LOW convenience.
_SECURITY_DOMAINS = {"lock", "alarm_control_panel"}
# Actuating verbs (as substrings) that DROP a guard → the high end.
_OPENING = ("unlock", "disarm", "open", "unlatch", "unbolt")
# Known-safe actuating services on a security domain (raising a guard) stay
# frictionless — locking/closing is safe; arming is already MEDIUM above.
_SECURITY_SAFE = {"lock", "close", "close_cover"}
# Non-actuating services carry no risk regardless of entity.
_READ_ONLY = {"", "update", "reload"}

# Substrings that make an otherwise-neutral switch security-relevant.
_SECURITY_HINTS = ("alarm", "security", "camera", "lock", "garage", "gate", "door")


def classify(domain: str, service: str, entity_id: str = "") -> Tuple[str, str]:
    """Return ``(risk, reason)`` for an action.

    Pure — takes no ``hass`` and performs no I/O, so it is safe to call from
    anywhere (including logging / telemetry paths). ``risk`` is one of
    ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.

    Classification is capability-based rather than a bare service allowlist:
    known high-signal pairs match exactly, then any actuating service on an
    inherently security domain is escalated — a guard-dropping verb (unlock,
    disarm, open, unlatch) to HIGH, any other unrecognized actuating service to
    MEDIUM — so a service that isn't enumerated here cannot silently be LOW on a
    lock or alarm. Safe directions (lock, arm, close) keep their low friction.
    """
    dom = domain or ""
    svc = service or ""
    key = (dom, svc)
    hay = (entity_id or "").lower()

    if key in _CRITICAL:
        return "critical", f"{dom}.{svc} disarms security"
    if key in _HIGH:
        return "high", f"{dom}.{svc} unlocks a door"
    if key in _MEDIUM:
        return "medium", f"{dom}.{svc} opens a physical barrier"

    svc_l = svc.lower()
    # Capability net for inherently security domains (lock, alarm).
    if dom in _SECURITY_DOMAINS and svc_l not in _READ_ONLY:
        if any(w in svc_l for w in _OPENING):
            return "high", f"{dom}.{svc} opens a security device"
        if svc_l not in _SECURITY_SAFE:
            return "medium", f"unrecognized {dom} action on a security device"

    if key == ("switch", "turn_off"):
        if any(t in hay for t in _SECURITY_HINTS):
            return "high", "turning off a security switch"
    return "low", ""


def requires_confirmation(hass, domain: str, service: str, entity_id: str = "") -> bool:
    """Whether this action must be confirmed before it runs.

    Delegates to ``voice_confirm.action_is_protected`` (which honours the
    ``voice_confirm_enabled`` opt-in and the per-entity override list). If the
    confirmation module can't be consulted, fail closed for anything above LOW
    risk and allow LOW-risk convenience through.
    """
    try:
        from . import voice_confirm
        return bool(voice_confirm.action_is_protected(hass, domain, service, entity_id))
    except Exception as exc:  # module unavailable / errored
        risk, _ = classify(domain, service, entity_id)
        if risk == "low":
            return False
        _LOGGER.warning(
            "policy: could not consult voice_confirm for %s.%s (%s); "
            "requiring confirmation for %s-risk action", domain, service, exc, risk)
        return True


async def confirm_gate(
    hass,
    domain: str,
    service: str,
    entity_id: str = "",
    action_label: str = "",
) -> Tuple[bool, str]:
    """May this action proceed now? Returns ``(allowed, note)``.

    Outcomes:
      * not a protected action              -> ``(True, "")``   (no friction)
      * protected and confirmed             -> ``(True, "")``
      * protected and declined              -> ``(False, "<awaiting confirmation>")``
      * protected but the confirm path errored -> ``(False, "<denied for safety>")``

    The final case is the fail-closed guarantee: an error anywhere in the
    confirmation subsystem can never let a protected action through.
    """
    # Resolve the confirmation module. If it's missing, LOW-risk proceeds and
    # anything higher is denied (fail closed for authority).
    try:
        from . import voice_confirm
    except Exception as exc:
        risk, _ = classify(domain, service, entity_id)
        if risk == "low":
            return True, ""
        _LOGGER.warning("policy: voice_confirm import failed for %s.%s (%s); denying",
                        domain, service, exc)
        return False, "confirmation unavailable — action denied for safety"

    # Does this action need confirmation at all?
    try:
        protected = bool(voice_confirm.action_is_protected(hass, domain, service, entity_id))
    except Exception as exc:
        risk, _ = classify(domain, service, entity_id)
        if risk == "low":
            return True, ""
        _LOGGER.warning("policy: protection check failed for %s.%s (%s); denying",
                        domain, service, exc)
        return False, "confirmation check failed — action denied for safety"

    if not protected:
        return True, ""

    # Confirmation required — ask. ANY failure here denies (fail closed).
    label = (action_label or service.replace("_", " ")).strip()
    ent = entity_id.split(".")[-1].replace("_", " ").strip() if entity_id else ""
    question = (f"{label} {ent} — are you sure?").strip()
    try:
        confirmed = await voice_confirm.confirm(hass, question, entity_id=entity_id)
    except Exception as exc:
        _LOGGER.warning("policy: confirmation errored for %s.%s (%s); denying",
                        domain, service, exc)
        return False, "confirmation unavailable — action denied for safety"

    if confirmed:
        return True, ""
    return False, (f"asked for spoken confirmation before {label} "
                   f"on {entity_id}; not yet confirmed")
