"""MCU Phase D (D3): mirror the package-delivery lifecycle into kernel.situation.

`package_monitor` tracks a per-camera delivery episode with a genuine lifecycle:
a package is **delivered** (arrival), sits, then is **removed** (pickup). This
module mirrors that episode into a kernel `Situation` (``kind="delivery"``,
``subject=<camera entity_id>``) — one open episode per camera — and, like the
intrusion (D1) and freeze (D2) mirrors, *verifies* the kernel situation agrees
with the delivery verdict (``_record_parity``).

PARITY, log-only: `package_monitor`'s announcements and state machine stay
authoritative and unchanged. Everything here is best-effort — a mirror failure is
swallowed and never affects delivery handling. Nothing is flipped to
authoritative.

Only the *package* episode is mirrored (it has an arrival→pickup lifecycle). Mail
arrival is a one-shot with no removal tracking, so there is no sustained episode
to mirror and none is invented.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

_mgr = None                                      # lazily-built SituationManager
_delivery_situation_ids: Dict[str, str] = {}     # camera entity_id -> open sit id
_last_parity: Optional[dict] = None              # most recent parity (tests)
_last_presence_parity: Optional[dict] = None     # R1a decision-parity (tests)

_STAGE_RANK = {"possible": 1, "investigating": 2, "confirmed": 3,
               "response": 4, "benign": 4, "resolved": 5}
_EXPECTED_RANK = {"delivered": 2, "removed": 5}


def _get_manager(hass):
    global _mgr
    if _mgr is None:
        from .kernel import SituationManager
        _mgr = SituationManager(
            config_path_str("jarvis", "situations.db", hass=hass))
    return _mgr


def _record_parity(mgr, action: str, episode_id: Optional[str]) -> None:
    """Compare the kernel situation's resulting state against the delivery verdict
    and record/log agreement. Log-only: never affects delivery handling."""
    global _last_parity
    expected = _EXPECTED_RANK.get(action)
    if expected is None or episode_id is None:
        return
    try:
        sit = mgr.get(episode_id)
    except Exception:
        sit = None
    actual_state = sit.state if sit is not None else None
    actual_rank = _STAGE_RANK.get(actual_state, 0)
    agree = actual_rank >= expected
    _last_parity = {
        "action": action, "episode_id": episode_id,
        "expected_rank": expected, "actual_state": actual_state,
        "actual_rank": actual_rank, "agree": agree,
    }
    if agree:
        _LOGGER.debug("delivery situation parity OK: verdict %r → kernel %r",
                      action, actual_state)
    else:
        _LOGGER.warning(
            "delivery situation parity DIVERGENCE: verdict %r but kernel "
            "situation is %r (episode %s)", action, actual_state, episode_id)


def kernel_package_present_sync(hass, entity_id: str) -> Optional[bool]:
    """The kernel situation store's view of per-camera package presence: is there
    an open (non-terminal) ``delivery`` episode for this camera?

    This is the verdict the re-architecture (MCU Phase R, R1) moves into the
    kernel — today the live in-memory ``_STATE[entity_id]["package"]`` owns it.
    Returns True/False, or ``None`` when the store can't be read (best-effort —
    the caller treats None as "no kernel opinion", never as a divergence). SYNC —
    SQLite read, run via the executor."""
    try:
        mgr = _get_manager(hass)
        for sit in mgr.open_situations("delivery"):
            if sit.subject == entity_id:
                return True
        return False
    except Exception:   # pragma: no cover - defensive
        return None


def record_presence_parity(hass, entity_id: str, legacy_present: bool) -> None:
    """R1a decision-parity: compare the kernel store's per-camera package-present
    view against the legacy in-memory verdict (``prev["package"]``) and log any
    divergence. LOG-ONLY — nothing is gated, no behaviour changes. This earns the
    kernel the right to *own* this verdict (the enforce flip, R1b) only once the
    logs show the two agree on real traffic."""
    global _last_presence_parity
    kernel_present = kernel_package_present_sync(hass, entity_id)
    if kernel_present is None:
        return                        # no kernel opinion → not a divergence
    agree = (kernel_present == bool(legacy_present))
    _last_presence_parity = {
        "entity_id": entity_id, "legacy": bool(legacy_present),
        "kernel": kernel_present, "agree": agree,
    }
    if agree:
        _LOGGER.debug("delivery presence parity OK: %s (camera %s)",
                      legacy_present, entity_id)
    else:
        _LOGGER.warning(
            "delivery presence parity DIVERGENCE: legacy=%s kernel=%s (camera %s)",
            bool(legacy_present), kernel_present, entity_id)


def mirror_delivery_sync(hass, entity_id: str, action: str,
                         count: Optional[int] = None) -> None:
    """Mirror one delivery lifecycle event for ``entity_id`` into kernel.situation.

    ``action`` ∈ {``delivered``, ``removed``}. SYNC — run via the executor
    (SQLite I/O). Best-effort: never raises into the caller."""
    try:
        from .kernel import situation as S
        mgr = _get_manager(hass)
        cur_id = _delivery_situation_ids.get(entity_id)
        cur = mgr.get(cur_id) if cur_id else None
        episode_id: Optional[str] = None

        if action == "delivered":
            if cur is None or cur.terminal:
                sit = mgr.open("delivery", subject=entity_id,
                               data={"count": count})
                mgr.transition(sit.id, S.INVESTIGATING, reason="package delivered")
                _delivery_situation_ids[entity_id] = sit.id
                episode_id = sit.id
            else:
                if cur.state == S.POSSIBLE:
                    mgr.transition(cur.id, S.INVESTIGATING, reason="package delivered")
                episode_id = cur.id

        elif action == "removed":
            if cur is not None and not cur.terminal:
                episode_id = cur.id
                mgr.transition(cur.id, S.RESOLVED, reason="package removed")
            _delivery_situation_ids.pop(entity_id, None)

        _record_parity(mgr, action, episode_id)
        # D4: publish the transition as a canonical JarvisEvent (parity — it
        # enters the stream + ledger; no consumer reacts yet). Best-effort.
        if episode_id:
            sit_after = mgr.get(episode_id)
            if sit_after is not None:
                from . import events
                events.publish_situation(hass, sit_after, action=action)
    except Exception as exc:
        _LOGGER.debug("delivery: situation mirror failed (%s/%s): %s",
                      entity_id, action, exc)
