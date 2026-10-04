"""Log-only bridge: live authorization gate ↔ kernel.authority (hardening H1).

The actuator path already runs every protected action through `policy.confirm_gate`
(the live, authoritative gate). This bridge computes what the Phase 4 authority
**engine** would have decided for the same action and records whether the two
agree — **log-only, never changing behaviour** — so enforcement can be flipped
from parity to deny only once the engine matches the gate on real traffic.

The running tally lives in `hass.data[DOMAIN]["_authority_parity"]` and is
exposed via `parity_summary()` for diagnostics. Entirely best-effort: any failure
here is swallowed and the actuator path is untouched.
"""
from __future__ import annotations

import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_PARITY_KEY = "_authority_parity"

# ── authority ENFORCE (MCU Phase G/G4) ──────────────────────────────────────────
# Owner-approved flip of the kernel capability engine from parity to enforce, built
# with a MAX-RESTRICTION belt: for an allowlisted capability the action proceeds
# only if the legacy confirm-gate allows it AND the kernel engine returns ALLOW.
# This can only ever ADD a confirmation (when the engine is stricter than the
# gate); it can NEVER remove the legacy gate or let through something the gate
# would hold. FAIL-SAFE: on any engine error the legacy outcome stands unchanged
# (a kernel fault can never block a legitimate action). KILL-SWITCH: flip
# AUTHORITY_ENFORCE to False to revert to pure parity (log-only) on the next load.
AUTHORITY_ENFORCE = True

# Per-capability allowlist — enforcement applies ONLY to these, so the blast
# radius is bounded. Seeded with exactly the security-relevant capabilities the
# live policy.confirm_gate already protects (policy._CRITICAL/_HIGH/_MEDIUM), where
# an extra confirmation is the safe direction. Widen deliberately, never blindly.
AUTHORITY_ENFORCE_CAPABILITIES = frozenset({
    "alarm_control_panel.alarm_disarm",
    "lock.unlock",
    "lock.open",
    "cover.open_cover",
    "cover.open",
    "alarm_control_panel.alarm_arm_away",
    "alarm_control_panel.alarm_arm_home",
    "alarm_control_panel.alarm_arm_night",
})


def _parity(hass):
    from .kernel import AuthorityParity
    store = hass.data.setdefault(DOMAIN, {})
    p = store.get(_PARITY_KEY)
    if p is None:
        p = AuthorityParity()
        store[_PARITY_KEY] = p
    return p


def record_control_parity(hass, domain: str, service: str, *, allowed: bool,
                          identity=None, confidence: float = 1.0,
                          situation=None, scope=None, intent=None,
                          token=None, context=None):
    """Record the authority engine's decision for ``domain.service`` against the
    live gate's ``allowed`` outcome. LOG-ONLY. Returns the engine decision (or
    None on failure).

    The engine request is built with the *full* set of authoritative inputs the
    caller can supply — ``situation`` (the active home situation), ``scope`` (the
    target area/entity), ``intent`` (why), ``token`` (a capability token, which
    drives delegation/expiry/revocation), and free-form ``context`` — not just
    capability/identity/confidence (MCU audit A4). Feeding the real request now,
    while still log-only, is what makes the eventual enforce-flip trustworthy:
    parity is measured against the decision the engine would *actually* make.
    """
    try:
        from .kernel import AuthorityRequest, authority as A, authorize
        cap = f"{domain}.{service}"
        decision = authorize(AuthorityRequest(
            capability=cap, identity=identity, confidence=confidence,
            situation=situation, scope=scope, intent=intent, token=token,
            context=dict(context or {})))
        # Map the gate result into the engine's vocabulary: the gate either lets
        # the action proceed (ALLOW) or holds it for confirmation (CONFIRM).
        actual = A.ALLOW if allowed else A.CONFIRM
        if not _parity(hass).record(decision, actual):
            _LOGGER.debug(
                "authority parity mismatch: %s engine=%s actual=%s (%s)",
                cap, decision.decision, actual, decision.reason)
        return decision
    except Exception as exc:  # never affect the actuator path
        _LOGGER.debug("authority parity record failed: %s", exc)
        return None


def enforced_decision(hass, domain: str, service: str, *, legacy_ok: bool,
                      identity=None, confidence: float = 1.0, situation=None,
                      scope=None, intent=None, token=None, context=None):
    """MCU Phase G/G4 — authority ENFORCE with a max-restriction belt.

    Records the engine-vs-gate parity (as ``record_control_parity`` does) and,
    when enforcement is on and ``domain.service`` is allowlisted, returns the
    effective outcome: ``legacy_ok AND engine == ALLOW``. So the engine can only
    *tighten* the gate (turn an allow into a held-for-confirmation), never loosen
    it. Returns ``(effective_ok, decision)``.

    FAIL-SAFE: a kernel fault (decision is None) leaves ``legacy_ok`` unchanged —
    an engine error can never block a legitimate action. KILL-SWITCH / allowlist:
    outside enforcement the legacy outcome is returned verbatim (pure parity)."""
    decision = record_control_parity(
        hass, domain, service, allowed=legacy_ok, identity=identity,
        confidence=confidence, situation=situation, scope=scope, intent=intent,
        token=token, context=context)
    cap = f"{domain}.{service}"
    if not AUTHORITY_ENFORCE or cap not in AUTHORITY_ENFORCE_CAPABILITIES:
        return legacy_ok, decision
    if decision is None:
        return legacy_ok, decision          # fail-safe: never block on a fault
    try:
        from .kernel import authority as A
        engine_allows = (decision.decision == A.ALLOW)
    except Exception:   # pragma: no cover - defensive, fail-safe
        return legacy_ok, decision
    effective_ok = bool(legacy_ok) and engine_allows
    if legacy_ok and not effective_ok:
        _LOGGER.warning(
            "authority ENFORCE: %s held for confirmation by the kernel engine "
            "(decision=%s, reason=%s) — the legacy gate would have allowed it",
            cap, decision.decision, getattr(decision, "reason", ""))
    return effective_ok, decision


def parity_summary(hass) -> dict:
    """The current engine-vs-gate agreement tally (for diagnostics/panel)."""
    try:
        return _parity(hass).summary()
    except Exception:
        return {}
