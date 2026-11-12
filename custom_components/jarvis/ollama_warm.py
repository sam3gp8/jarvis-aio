"""Keep JARVIS's local Ollama model warm (MCU latency work).

The symptom (owner-reported): the first voice turn after an idle gap takes
minutes while Ollama cold-loads the local model (e.g. Gemma 4 26B) into the GPU,
then every later turn is fast — the classic cold-start signature. Two things make
it bite:

  * ``keep_alive`` only holds a model AFTER a request, so the FIRST request of an
    idle period still pays the full load.
  * JARVIS runs the model at a larger ``num_ctx`` than a hand-run CLI/Alpaca
    session, and Ollama keys a *resident* model instance by its options — so
    JARVIS's instance is a SEPARATE copy that the other client never keeps warm.

This module pins JARVIS's exact model (+ its num_ctx) resident: a tiny no-op
completion with an indefinite keep_alive, sent once at startup and then on a
periodic scheduler tick (interval well inside any unload window), so the model is
already in VRAM when the user speaks. Local-only — it no-ops for cloud providers.
All best-effort and off-loop: a warm failure never touches a request path.

Kill-switch: :data:`OLLAMA_WARM_ENABLED` (module) or the ``ollama_keep_warm``
config key (default on).
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# ON by default. The periodic re-pin runs every OLLAMA_WARM_INTERVAL_S seconds —
# short enough to stay inside the model's unload window and to reload promptly
# after an Ollama restart or eviction, so the user's turn is never the thing that
# triggers the load.
OLLAMA_WARM_ENABLED = True
OLLAMA_WARM_INTERVAL_S = 240


def _config_enabled() -> bool:
    """Module flag AND the optional ``ollama_keep_warm`` config override (default
    on). Either one off disables warming."""
    if not OLLAMA_WARM_ENABLED:
        return False
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("ollama_keep_warm", True))
    except Exception:
        return True


def warm_sync(client, model=None) -> dict:
    """Blocking warm of one model on the live client — run this in an executor,
    never on the loop. ``model`` defaults to the client's own model; pass one to
    warm a second resident model (e.g. the fast-chat tier's small model). No-ops
    (returns a ``skipped`` reason) when warming is disabled or the active provider
    isn't Ollama; otherwise delegates to the provider's own ``warm()`` and returns
    its diagnostics dict. Never raises."""
    if not _config_enabled():
        return {"skipped": "disabled"}
    warm = getattr(client, "warm", None)
    if getattr(client, "name", None) != "ollama" or not callable(warm):
        return {"skipped": "not-ollama"}
    try:
        res = warm(model) if model is not None else warm()
        return res or {"skipped": "no-result"}
    except Exception as exc:   # provider.warm() shouldn't raise, but a tick must survive
        return {"ok": False, "error": str(exc)[:200]}


def _log_warm(res: dict) -> None:
    if res.get("skipped"):
        _LOGGER.debug("ollama warm: skipped (%s)", res["skipped"])
    elif res.get("ok"):
        _LOGGER.info("ollama warm: %s pinned warm in %d ms",
                     res.get("model", "?"), int(res.get("ms", 0) or 0))
    else:
        _LOGGER.warning("ollama warm: failed for %s: %s",
                        res.get("model", "?"), res.get("error", "?"))


async def async_warm(hass, client, extra_models=None) -> dict:
    """Warm the Main Agent model off-loop and log the outcome, then warm each of
    ``extra_models`` (e.g. the fast-chat small model) so every model a turn might
    hit is already resident. Safe for any client/provider and any failure; returns
    the Main Agent model's diagnostics dict for callers/tests."""
    try:
        res = await hass.async_add_executor_job(warm_sync, client)
    except Exception as exc:
        _LOGGER.debug("ollama warm: executor error: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
    _log_warm(res)
    # Only chase extra models when the main warm actually ran (Ollama + enabled);
    # a skip reason means there's nothing to warm here.
    if not res.get("skipped"):
        seen = {res.get("model")}
        for m in (extra_models or []):
            m = (str(m).strip() if m is not None else "")
            if not m or m in seen:
                continue
            seen.add(m)
            try:
                _log_warm(await hass.async_add_executor_job(warm_sync, client, m))
            except Exception as exc:
                _LOGGER.debug("ollama warm: extra model '%s' error: %s", m, exc)
    return res
