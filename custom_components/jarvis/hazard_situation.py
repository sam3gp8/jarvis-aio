"""MCU Phase D (D2): mirror the in-home freeze hazard into kernel.situation.

The `SafetyManager._check_freeze` path is the only in-home hazard JARVIS tracks
with a genuine lifecycle (warning → critical → cleared). This module mirrors that
lifecycle into a kernel `Situation` (``kind="hazard"``, ``subject="freeze"``) so
the generalised state machine tracks hazards alongside intrusion — and, like the
intrusion mirror (D1), *verifies* the kernel situation agrees with the freeze
verdict (``_record_parity``).

PARITY, log-only: the SafetyManager freeze path stays authoritative and its
alerts are unchanged. Everything here is best-effort — a mirror failure is
swallowed and never affects freeze alerting. Nothing here is flipped to
authoritative; that would be an owner-gated step and is deliberately not taken.

(Smoke / CO / water-leak hazards are *not* detected by JARVIS today, so there is
nothing to mirror for them — this module covers the freeze lifecycle that exists,
and no new detection is invented.)
"""
from __future__ import annotations

import logging
from typing import Optional

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

_mgr = None                               # lazily-built kernel.SituationManager
_freeze_situation_id: Optional[str] = None   # current open freeze episode, if any
_last_parity: Optional[dict] = None          # most recent parity comparison (tests)

# Lifecycle rank: the kernel situation must have reached AT LEAST the stage the
# freeze verdict implies. (Values mirror kernel.situation's state constants.)
_STAGE_RANK = {"possible": 1, "investigating": 2, "confirmed": 3,
               "response": 4, "benign": 4, "resolved": 5}
# Minimum kernel-situation rank expected for each freeze verdict.
_EXPECTED_RANK = {"warning": 2, "critical": 3, "cleared": 5}


def _get_manager(hass):
    global _mgr
    if _mgr is None:
        from .kernel import SituationManager
        _mgr = SituationManager(
            config_path_str("jarvis", "situations.db", hass=hass))
    return _mgr


def _record_parity(mgr, action: str, episode_id: Optional[str]) -> None:
    """Compare the kernel situation's resulting state against the freeze verdict
    and record/log agreement. Log-only: never affects freeze handling."""
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
        _LOGGER.debug("freeze situation parity OK: verdict %r → kernel %r",
                      action, actual_state)
    else:
        _LOGGER.warning(
            "freeze situation parity DIVERGENCE: verdict %r but kernel "
            "situation is %r (episode %s)", action, actual_state, episode_id)


def mirror_freeze_sync(hass, action: str, reading: Optional[str] = None) -> None:
    """Mirror one freeze lifecycle event into kernel.situation.

    ``action`` ∈ {``warning``, ``critical``, ``cleared``}. SYNC — run via the
    executor (SQLite I/O). Best-effort: never raises into the caller."""
    global _freeze_situation_id
    try:
        from .kernel import situation as S
        mgr = _get_manager(hass)
        cur = mgr.get(_freeze_situation_id) if _freeze_situation_id else None
        episode_id: Optional[str] = None

        if action == "warning":
            if cur is None or cur.terminal:
                sit = mgr.open("hazard", subject="freeze",
                               data={"reading": reading})
                _freeze_situation_id = sit.id
                mgr.transition(sit.id, S.INVESTIGATING, reason="freeze warning")
            elif cur.state == S.POSSIBLE:
                mgr.transition(cur.id, S.INVESTIGATING, reason="freeze warning")
            episode_id = _freeze_situation_id

        elif action == "critical":
            if cur is None or cur.terminal:
                sit = mgr.open("hazard", subject="freeze",
                               data={"reading": reading})
                _freeze_situation_id = sit.id
                cur = mgr.transition(sit.id, S.INVESTIGATING, reason="freeze critical")
            if cur.state == S.POSSIBLE:
                cur = mgr.transition(cur.id, S.INVESTIGATING, reason="freeze critical")
            if cur.state == S.INVESTIGATING:
                mgr.transition(cur.id, S.CONFIRMED, reason="freeze critical")
            episode_id = _freeze_situation_id

        elif action == "cleared":
            if cur is not None and not cur.terminal:
                episode_id = cur.id
                mgr.transition(cur.id, S.RESOLVED, reason="temperature recovered")
            _freeze_situation_id = None

        # Parity (log-only): verify the kernel situation agrees with the verdict.
        _record_parity(mgr, action, episode_id)
        # D4: publish the transition as a canonical JarvisEvent (parity — it
        # enters the stream + ledger; no consumer reacts yet). Best-effort.
        if episode_id:
            sit_after = mgr.get(episode_id)
            if sit_after is not None:
                from . import events
                events.publish_situation(hass, sit_after, action=action)
    except Exception as exc:
        _LOGGER.debug("hazard: freeze situation mirror failed (%s): %s", action, exc)
