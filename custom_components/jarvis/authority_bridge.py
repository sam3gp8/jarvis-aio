"""Bridge: live authorization gate ↔ kernel.authority (hardening H1 → Phase G/G4).

The actuator path runs every protected action through `policy.confirm_gate` (the
live, authoritative gate). This module layers two things over that gate:

1. **Parity recording (log-only).** `record_control_parity` computes what the
   kernel authority **engine** would decide for the same action and records
   whether the two agree — never changing behaviour. This is the evidence the
   enforce flip below was earned against.

2. **Authority ENFORCE (live — MCU Phase G/G4/G4b).** `enforced_decision` returns
   ``legacy_ok AND engine == ALLOW`` behind a **max-restriction belt**: the engine
   can only *tighten* the legacy gate (turn an allow into a hold-for-confirmation),
   never loosen it, and a kernel fault leaves the legacy outcome unchanged
   (fail-safe). It is **ON by default** (`AUTHORITY_ENFORCE` + `AUTHORITY_ENFORCE_ALL`);
   set `AUTHORITY_ENFORCE_ALL=False` to revert to the bounded capability allowlist,
   or `AUTHORITY_ENFORCE=False` to revert to pure parity — either on the next load.

3. **identity → authz parity (log-only — MCU Tranche 1).** The live gate builds its
   request with ``identity=None``, so the policy's identity/confidence rules never
   bite. `_emit_identity_authz_parity` resolves the live principal
   (`identity.quick_identify`) and logs what the decision WOULD be with that
   identity + confidence — a divergence from the identity-blind baseline is a real
   finding the enforce rung acts on. Observe-only; the recorded decision is
   unchanged. Kill-switch: ``IDENTITY_AUTHZ_PARITY``.

4. **identity → authz ENFORCE (live — MCU Tranche 1).** When authority enforce is
   on and the caller resolved no requester, `enforced_decision` resolves the live
   principal and, only for a KNOWN requester, re-runs the policy with that identity
   + confidence and folds the verdict into the SAME max-restriction belt: a
   known-but-low-confidence requester can turn a sensitive ALLOW into a
   hold-for-confirmation. It can only ever *tighten* — an unidentified requester
   (identity stays ``None``) or any identity/kernel fault leaves the identity-blind
   outcome unchanged (tighten-only / fail-safe). Kill-switch:
   ``IDENTITY_AUTHZ_ENFORCE`` (reverts to parity-only on the next load).

The running tally lives in `hass.data[DOMAIN]["_authority_parity"]` and is exposed
via `parity_summary()` for diagnostics. Best-effort: a parity/record failure is
swallowed and the actuator path is untouched.
"""
from __future__ import annotations

import json
import logging
import os

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_PARITY_KEY = "_authority_parity"
# The tally lives in hass.data (in-memory) and so resets on every restart, which
# wipes the engine-vs-gate agreement evidence the enforce-allowlist widening
# depends on. Persist it to a small JSON file so it accrues across restarts and
# updates (Step A.5). All of this is best-effort and off the authoritative path.
_PARITY_FILE_PARTS = ("jarvis", "authority_parity.json")


def _parity_path(hass):
    try:
        from .paths import config_path_str
        return config_path_str(*_PARITY_FILE_PARTS, hass=hass)
    except Exception:
        return None


def load_parity(hass) -> bool:
    """Load the persisted parity tally into hass.data (Step A.5). Sync — call from
    an executor at setup. Best-effort: a missing or corrupt file just leaves the
    tally empty. Returns True if a tally was restored."""
    path = _parity_path(hass)
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        from .kernel import AuthorityParity
        hass.data.setdefault(DOMAIN, {})[_PARITY_KEY] = AuthorityParity.from_dict(data)
        return True
    except Exception as exc:  # never raises into setup
        _LOGGER.debug("authority parity load failed: %s", exc)
        return False


def save_parity(hass) -> bool:
    """Persist the current parity tally to disk (Step A.5). Sync — call from an
    executor (a periodic scheduler tick and on unload). Best-effort; writes
    atomically via a temp file so a crash mid-write can't corrupt the store.
    Returns True on a successful write."""
    path = _parity_path(hass)
    if not path:
        return False
    try:
        p = (hass.data.get(DOMAIN) or {}).get(_PARITY_KEY)
        if p is None:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p.to_dict(), f)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # never raises into a scheduler tick / unload
        _LOGGER.debug("authority parity save failed: %s", exc)
        return False

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

# GENERAL ENFORCE (owner-approved, MCU Phase G4b): the kernel engine's verdict now
# gates EVERY actuation, not just the bounded allowlist below. The max-restriction
# belt still holds (the engine can only ADD a confirmation, never loosen the gate),
# and default_policy returns ALLOW for safe + in-policy sensitive actions at the
# default confidence, so ordinary actions are unaffected: only SECURITY-tier or
# low-confidence sensitive capabilities are held. Reversible in two steps:
# AUTHORITY_ENFORCE_ALL=False reverts to the bounded allowlist (previous
# behaviour); AUTHORITY_ENFORCE=False reverts to pure parity. Both on next load.
AUTHORITY_ENFORCE_ALL = True

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


# identity -> authz PARITY (MCU Tranche 1 — foundation trust). The live authority
# gate builds its AuthorityRequest with identity=None (callers don't resolve who
# is asking), so the policy's identity/confidence rules never bite: a SECURITY
# capability is held purely by the legacy gate, and a sensitive action never
# confirms just because WHO is present is a shaky guess. This consult resolves the
# live principal (identity.quick_identify) and recomputes the decision WITH that
# identity + its confidence, logging any divergence from the identity-blind
# baseline. Observe-only: the enforced outcome is unchanged; making the
# identity-aware decision authoritative is a later, owner-gated enforce rung.
# Kill-switch: IDENTITY_AUTHZ_PARITY.
IDENTITY_AUTHZ_PARITY = True


def _emit_identity_authz_parity(hass, cap, baseline, *, area_id=None):
    """Log what the authority decision WOULD be if it knew who is asking.

    Resolves the live principal and recomputes ``cap``'s decision with that
    identity + confidence, comparing to the identity-blind ``baseline`` the live
    gate actually used. A divergence (e.g. a SECURITY cap moving from "denied — no
    identified requester" to "confirm — identified", or a sensitive action moving
    to CONFIRM because who is present is a low-confidence guess) is a real finding
    the enforce rung would act on. Observe-only; never raises into the actuator
    path. Kill-switch: :data:`IDENTITY_AUTHZ_PARITY`."""
    if not IDENTITY_AUTHZ_PARITY or baseline is None:
        return
    try:
        from . import identity as _identity
        from .kernel import AuthorityRequest, authorize
        ident = _identity.quick_identify(hass, area_id=area_id)
        who = _identity.normalize(ident.person) if (ident and ident.known) else None
        conf = float(getattr(ident, "confidence", 0.0) or 0.0) if who else 1.0
        aware = authorize(AuthorityRequest(
            capability=cap, identity=who, confidence=conf))
        if aware.decision != baseline.decision:
            _LOGGER.warning(
                "identity->authz(parity): %s — identity-blind=%s, but with the "
                "resolved requester %s (conf %.2f) the engine would be %s: %s "
                "(observe-only)",
                cap, baseline.decision, who or "<unidentified>", conf,
                aware.decision, aware.reason)
        else:
            _LOGGER.debug(
                "identity->authz(parity): %s — %s with or without the resolved "
                "requester %s (conf %.2f) (observe-only)",
                cap, baseline.decision, who or "<unidentified>", conf)
    except Exception:   # pragma: no cover - observe-only, never disrupts actuation
        pass


# identity -> authz ENFORCE (MCU Tranche 1 — foundation trust). The parity rung
# above proved, log-only, that resolving WHO is asking changes the engine's verdict
# on real traffic. This rung makes that resolution bite — but only ever to TIGHTEN.
# When authority enforce is on and the caller passed no identity (the common case),
# enforced_decision resolves the live principal and, for a KNOWN requester, re-runs
# the policy with that identity + its confidence and ANDs the result into the same
# max-restriction belt. The only effect on an action the identity-blind gate would
# ALLOW is to hold a SENSITIVE one for confirmation when the requester is a
# low-confidence guess; an unidentified requester (identity stays None) or any
# fault leaves the identity-blind outcome unchanged. Reversible: flip
# IDENTITY_AUTHZ_ENFORCE to False to revert to parity-only (log-only) on next load.
IDENTITY_AUTHZ_ENFORCE = True


def _identity_aware_decision(hass, cap, *, situation=None, scope=None,
                             intent=None, token=None, context=None):
    """The policy's verdict on ``cap`` WITH the live principal resolved, for the
    enforce belt — or ``None`` to leave the identity-blind outcome untouched.

    Returns ``None`` (no tightening) when the rung is off, when no requester is
    identified (``ident.known`` is false — an unidentified requester must never
    change the decision), or on any fault (fail-safe: an identity/kernel error can
    never block a legitimate action). Otherwise re-runs :func:`authorize` with the
    SAME request the identity-blind baseline used, differing only in identity +
    confidence, so the belt compares like with like and can only tighten (a
    known-but-low-confidence requester turns a sensitive ALLOW into CONFIRM).
    Kill-switch: :data:`IDENTITY_AUTHZ_ENFORCE`."""
    if not IDENTITY_AUTHZ_ENFORCE:
        return None
    try:
        from . import identity as _identity
        from .kernel import AuthorityRequest, authorize
        ident = _identity.quick_identify(hass)
        if not (ident and ident.known):
            return None                      # unidentified → identity-blind stands
        who = _identity.normalize(ident.person)
        if not who:
            return None
        conf = float(getattr(ident, "confidence", 0.0) or 0.0)
        return authorize(AuthorityRequest(
            capability=cap, identity=who, confidence=conf, situation=situation,
            scope=scope, intent=intent, token=token, context=dict(context or {})))
    except Exception:   # pragma: no cover - fail-safe, never disrupts actuation
        return None


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
        # identity -> authz parity (Tranche 1): when the caller resolved no
        # requester (the common case today), log what the decision would be if it
        # knew who is asking. Observe-only; the recorded/enforced decision above is
        # unchanged.
        if identity is None:
            _emit_identity_authz_parity(hass, cap, decision)
        return decision
    except Exception as exc:  # never affect the actuator path
        _LOGGER.debug("authority parity record failed: %s", exc)
        return None


def enforced_decision(hass, domain: str, service: str, *, legacy_ok: bool,
                      identity=None, confidence: float = 1.0, situation=None,
                      scope=None, intent=None, token=None, context=None):
    """MCU Phase G/G4 — authority ENFORCE with a max-restriction belt.

    Records the engine-vs-gate parity (as ``record_control_parity`` does) and,
    when enforcement is on — generally (G4b), or for an allowlisted capability when
    general enforce is off — returns the effective outcome: ``legacy_ok AND engine
    == ALLOW``. So the engine can only *tighten* the gate (turn an allow into a
    held-for-confirmation), never loosen it. Returns ``(effective_ok, decision)``.

    FAIL-SAFE: a kernel fault (decision is None) leaves ``legacy_ok`` unchanged —
    an engine error can never block a legitimate action. KILL-SWITCH: outside
    enforcement the legacy outcome is returned verbatim (pure parity)."""
    decision = record_control_parity(
        hass, domain, service, allowed=legacy_ok, identity=identity,
        confidence=confidence, situation=situation, scope=scope, intent=intent,
        token=token, context=context)
    cap = f"{domain}.{service}"
    # General enforce (G4b): every capability is enforced; with AUTHORITY_ENFORCE_ALL
    # off, only the bounded allowlist is (the previous behaviour). Either way the
    # max-restriction belt below only ever tightens the legacy gate.
    enforce_this = AUTHORITY_ENFORCE and (
        AUTHORITY_ENFORCE_ALL or cap in AUTHORITY_ENFORCE_CAPABILITIES)
    if not enforce_this:
        return legacy_ok, decision
    if decision is None:
        return legacy_ok, decision          # fail-safe: never block on a fault
    try:
        from .kernel import authority as A
        engine_allows = (decision.decision == A.ALLOW)
    except Exception:   # pragma: no cover - defensive, fail-safe
        return legacy_ok, decision
    effective_ok = bool(legacy_ok) and engine_allows
    # identity -> authz ENFORCE (Tranche 1): when the caller resolved no requester,
    # also tighten by the live principal. Only runs when the identity-blind gate
    # would ALLOW (effective_ok) — a held action can't be tightened further — and
    # only ever turns that ALLOW into a hold (tighten-only / fail-safe; see
    # _identity_aware_decision). The identity-blind parity record above is unchanged.
    held_by_identity = False
    if effective_ok and identity is None:
        aware = _identity_aware_decision(
            hass, cap, situation=situation, scope=scope, intent=intent,
            token=token, context=context)
        if aware is not None and aware.decision != A.ALLOW:
            effective_ok = False
            held_by_identity = True
            _LOGGER.warning(
                "identity->authz ENFORCE: %s held for confirmation — the resolved "
                "requester makes the engine %s: %s (the identity-blind gate would "
                "have allowed it)", cap, aware.decision,
                getattr(aware, "reason", ""))
    if legacy_ok and not effective_ok and not held_by_identity:
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
