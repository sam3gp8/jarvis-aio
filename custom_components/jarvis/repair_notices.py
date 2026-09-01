"""Home Assistant Repair issues for JARVIS operational problems.

These surface an actionable condition — the reasoning backend being unreachable
— in Settings → Repairs with a clear description, complementing the in-panel
diagnostics and the failure reason already logged on the LLM path. The issue is
a *non-fixable notice*: it never blocks the integration from loading (JARVIS
stays up in limited mode) and it clears itself the moment reasoning recovers.

Deliberately named ``repair_notices`` rather than ``repairs`` so Home Assistant
does not treat it as the repairs *platform* (which would expect a fix-flow).
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_LLM_ISSUE = "llm_unavailable"

# Track our own issue so we neither churn the registry during an outage nor
# leave a stale issue behind after a restart. ``cleared_once`` lets the first
# recovery after startup delete an issue left over from a previous run.
_state = {"active": False, "detail": None, "cleared_once": False}


def note_llm_problem(hass: HomeAssistant, detail: str) -> None:
    """Raise (or update) the 'reasoning backend unavailable' Repair issue.

    ``detail`` is the specific cause already computed on the LLM failure path
    (e.g. a decommissioned model, or an auth/connectivity error). Safe to call
    from the event loop; never raises.
    """
    text = (detail or "The reasoning model could not be reached.").strip()[:300]
    if _state["active"] and _state["detail"] == text:
        return
    try:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _LLM_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=_LLM_ISSUE,
            translation_placeholders={"detail": text},
        )
        _state.update(active=True, detail=text, cleared_once=False)
    except Exception as exc:  # registry unavailable / mid-teardown
        _LOGGER.debug("could not create LLM repair issue: %s", exc)


def clear_llm_problem(hass: HomeAssistant) -> None:
    """Clear the LLM Repair issue once reasoning works again.

    Clears once per process even if we didn't raise the issue this run, so a
    notice left over from a previous run is removed on the next success. Safe to
    call from the event loop; never raises.
    """
    if not _state["active"] and _state["cleared_once"]:
        return
    try:
        ir.async_delete_issue(hass, DOMAIN, _LLM_ISSUE)
    except Exception as exc:
        _LOGGER.debug("could not clear LLM repair issue: %s", exc)
    _state.update(active=False, detail=None, cleared_once=True)
