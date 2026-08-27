"""JARVIS diagnostics layer: infrastructure health triage + fault history.

Also provides Home Assistant's config-entry diagnostics entry point
(async_get_config_entry_diagnostics) so the "Download diagnostics" button on the
integration page produces a useful, credential-redacted dump (v6.70.2). Without
this function HA has nothing to call and the download fails with "File wasn't
available on site"."""
from __future__ import annotations

import logging

from .fault_log import FaultLog
from .heartbeat import HeartbeatMonitor
from .monitor import Finding, InfrastructureTriage
from .service_health import run_service_health

__all__ = ["InfrastructureTriage", "Finding", "FaultLog", "HeartbeatMonitor",
           "run_service_health", "async_get_config_entry_diagnostics"]

_LOGGER = logging.getLogger(__name__)

# Config keys whose values must be stripped from any diagnostics dump.
_REDACT_KEYS = {
    "api_key", "groq_api_key", "gemini_api_key", "anthropic_api_key",
    "openai_api_key", "llm_api_key", "token", "access_token", "refresh_token",
    "client_secret", "client_id", "password", "secret", "notify_service",
    "floor_plan_address", "imap_user",
}


def _redact(obj):
    """Recursively redact sensitive values by key name. Never raises."""
    try:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = str(k).lower()
                if any(r in lk for r in _REDACT_KEYS):
                    out[k] = "**REDACTED**" if v not in (None, "") else v
                else:
                    out[k] = _redact(v)
            return out
        if isinstance(obj, (list, tuple)):
            return [_redact(v) for v in obj]
        return obj
    except Exception:
        return "**redaction-error**"


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    """HA calls this for the integration's "Download diagnostics" button. Returns
    a credential-redacted snapshot: config, cognitive/connectivity status,
    service health, and rough entity counts. Defensive — never raises; on any
    failure it returns what it has plus an error note, so the download always
    produces a file."""
    diag: dict = {"integration": "jarvis"}

    # Version
    try:
        diag["version"] = getattr(entry, "version", None)
        from ..const import DOMAIN  # noqa
        diag["domain"] = DOMAIN
    except Exception:
        pass

    # Config (redacted)
    try:
        data = dict(getattr(entry, "data", {}) or {})
        options = dict(getattr(entry, "options", {}) or {})
        diag["entry_data"] = _redact(data)
        diag["entry_options"] = _redact(options)
    except Exception as exc:
        diag["config_error"] = str(exc)

    # Live JARVIS config (redacted)
    try:
        from .. import jarvis_config
        diag["jarvis_config"] = _redact(jarvis_config.get_all())
    except Exception as exc:
        diag["jarvis_config_error"] = str(exc)

    # Service health (LLM/embeddings/TTS/STT)
    try:
        diag["service_health"] = await run_service_health(hass)
    except Exception as exc:
        diag["service_health_error"] = str(exc)

    # Cognitive core + connectivity status
    try:
        from .. import cognitive_core
        core = getattr(cognitive_core, "_CORE", None)
        if core and hasattr(core, "status"):
            diag["cognitive"] = _redact(core.status())
    except Exception as exc:
        diag["cognitive_error"] = str(exc)
    try:
        from .. import connectivity
        if hasattr(connectivity, "snapshot"):
            diag["connectivity"] = connectivity.snapshot()
    except Exception as exc:
        diag["connectivity_error"] = str(exc)

    # Rough entity/domain counts (no entity_ids — could reveal layout)
    try:
        from collections import Counter
        counts: Counter = Counter()
        for st in hass.states.async_all():
            counts[st.entity_id.split(".", 1)[0]] += 1
        diag["entity_domain_counts"] = dict(counts)
        diag["entity_total"] = sum(counts.values())
    except Exception as exc:
        diag["entity_count_error"] = str(exc)

    # Subsystem stats — the local loops and stores, so their volume/health is
    # visible without a live session. (Database health is already covered by
    # service_health above; it's omitted here because probing it applies the
    # schema, a side effect diagnostics must not cause.)
    def _collect_subsystems() -> dict:
        import importlib
        out: dict = {}
        for mod_name, fn_name in (
            ("cognition", "stats"),
            ("decision_record", "stats"),
            ("intrusion", "status"),
            ("reasoning_cache", "stats"),
        ):
            try:
                mod = importlib.import_module(f"..{mod_name}", __package__)
                out[mod_name] = _redact(getattr(mod, fn_name)())
            except Exception as exc:
                out[mod_name] = {"error": str(exc)[:200]}
        return out
    diag["subsystems"] = await hass.async_add_executor_job(_collect_subsystems)

    # Audio-routing snapshot — resolve each configured satellite→speaker pairing
    # and which TTS engines are selected, so "no audible reply" issues are
    # diagnosable directly (the pairings themselves are already in the config
    # above; this adds their live reachability and the resolved engines).
    try:
        from .. import jarvis_config as _jc, tts_helper as _tts
        import json as _json
        cfg = _jc.get_all()
        raw = cfg.get("satellite_pairings")
        pairings = (_json.loads(raw) if isinstance(raw, str) else raw) or {}
        routing: dict = {"pairings": {}, "tts": {}}
        for sat, spk in pairings.items():
            st = hass.states.get(spk) if spk else None
            routing["pairings"][sat] = {
                "speaker": spk,
                "state": st.state if st else "missing",
                "reachable": bool(st and st.state not in ("unavailable", "unknown")),
            }
        routing["tts"] = {
            "reply_engine": _tts.find_best_tts_entity(hass),
            "premium_engine": _tts.find_premium_tts_entity(hass),
            "tts_engine_option": cfg.get("tts_engine"),
            "premium_contexts": cfg.get("tts_premium_contexts"),
        }
        diag["audio_routing"] = routing
    except Exception as exc:
        diag["audio_routing_error"] = str(exc)[:200]

    # Recent activity log tail — includes the reply-routing decisions (which
    # speaker each spoken reply targeted, whether it reached a Cast speaker or
    # fell back to the satellite), so a "no audible reply" issue is diagnosable
    # straight from the download.
    try:
        from ..websocket import recent_debug_log
        diag["recent_log"] = recent_debug_log(150)
    except Exception as exc:
        diag["recent_log_error"] = str(exc)

    # Dedicated conversation/reply-routing log — survives observer/anomaly floods
    # that evict the reply-delivery decisions from the main log above.
    try:
        from ..websocket import recent_conversation_log
        diag["conversation_log"] = recent_conversation_log(80)
    except Exception as exc:
        diag["conversation_log_error"] = str(exc)

    return diag

