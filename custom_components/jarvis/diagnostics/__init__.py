"""JARVIS diagnostics layer: infrastructure health triage + fault history.

Also provides Home Assistant's config-entry diagnostics entry point
(async_get_config_entry_diagnostics) so the "Download diagnostics" button on the
integration page produces a useful, credential-redacted dump (v6.70.2). Without
this function HA has nothing to call and the download fails with "File wasn't
available on site"."""
from __future__ import annotations

import logging
import math

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


def _json_safe(obj, _depth: int = 0, _seen: frozenset = frozenset()):
    """Return a structurally-equivalent object that serialises to STRICT, valid
    JSON — string keys only, no NaN/Infinity, no datetimes / sets / bytes / opaque
    objects. Never raises.

    Why this exists: Home Assistant serialises the diagnostics dict with
    ``json.dumps`` when the "Download diagnostics" button is used. Two things a
    live subsystem can hand back break that download even though building the dict
    succeeded:

    * a **non-string dict key** (an int, an enum, a tuple) → ``json.dumps`` raises
      ``TypeError`` and HA returns an error instead of a file; and
    * a **NaN / Infinity float** (a ``0/0`` rate, an unavailable sensor reading) →
      ``json.dumps`` emits the bare tokens ``NaN`` / ``Infinity``, which are not
      valid JSON, so the downloaded file won't parse in a strict reader.

    The dict-building code (``_redact`` and the collectors) guards against
    *exceptions*, but not against either of these — the value sails through and
    only blows up in HA's serializer, downstream of us. This final pass closes
    that gap so the download is always a valid, parseable ``.json`` file. (v9.78.0)
    """
    try:
        if obj is None or isinstance(obj, (bool, int, str)):
            return obj
        if isinstance(obj, float):
            # NaN / ±Infinity are not valid JSON — coerce to null.
            return obj if math.isfinite(obj) else None
        if isinstance(obj, (bytes, bytearray)):
            try:
                return bytes(obj).decode("utf-8", "replace")
            except Exception:
                return repr(bytes(obj))
        if _depth > 40:
            return "**max-depth**"
        if isinstance(obj, dict):
            if id(obj) in _seen:
                return "**circular**"
            seen = _seen | {id(obj)}
            out = {}
            for k, v in obj.items():
                if isinstance(k, str):
                    key = k
                elif isinstance(k, bool) or k is None or isinstance(k, (int, float)):
                    key = str(k)
                else:
                    key = repr(k)
                out[key] = _json_safe(v, _depth + 1, seen)
            return out
        if isinstance(obj, (list, tuple, set, frozenset)):
            if id(obj) in _seen:
                return "**circular**"
            seen = _seen | {id(obj)}
            return [_json_safe(v, _depth + 1, seen) for v in obj]
        # datetime / date / time and the like → isoformat; anything else → str.
        iso = getattr(obj, "isoformat", None)
        if callable(iso):
            try:
                return obj.isoformat()
            except Exception:
                pass
        return str(obj)
    except Exception:
        return "**unserializable**"


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

    # Authority enforce rollout (the engine-vs-gate parity tally). This is the
    # data that gates widening the enforce allowlist: per-capability agreement
    # counts [agree, disagree], the overall rate, and recent mismatches, plus the
    # current enforce state + allowlist. Read-only — surfacing it changes nothing;
    # it makes the "which capabilities does the engine already agree on?" question
    # answerable from the downloaded diagnostics instead of only the logs.
    try:
        from .. import authority_bridge as _ab
        block = {
            "enforce_on": bool(getattr(_ab, "AUTHORITY_ENFORCE", False)),
            "enforced_capabilities": sorted(_ab.AUTHORITY_ENFORCE_CAPABILITIES),
            "tally": _redact(_ab.parity_summary(hass)),
        }
        # Step B — surface whether the log-only state-change observer is on and
        # how many protected devices it's watching, so an empty tally can be read
        # as "no traffic yet" vs. "nothing is feeding it".
        try:
            from .. import authority_observer as _ao
            watched = sum(
                1 for s in hass.states.async_all()
                if s.entity_id.split(".", 1)[0] in _ao._WATCHED_DOMAINS)
            block["observer"] = {
                "enabled": bool(_ao._enabled(hass)),
                "watched_domains": list(_ao._WATCHED_DOMAINS),
                "watched_entities": watched,
            }
        except Exception as exc:  # observer is optional; never fail the block
            block["observer_error"] = str(exc)[:200]
        diag["authority_parity"] = block
    except Exception as exc:
        diag["authority_parity_error"] = str(exc)[:200]

    # Token-usage breakdown — session totals plus a per-tier and per-model split
    # of the measured LLM usage (TC4 + Tranche 1 tier attribution), so "how much
    # is each cognitive tier spending" is answerable from the dump. Session-scoped
    # and cost-estimated; empty when telemetry enforce is off or there are no
    # calls yet. Fail-safe helper, never raises.
    try:
        from .. import llm_provider as _llm
        tb = _llm.telemetry_breakdown()
        if tb:
            diag["token_telemetry"] = tb
    except Exception as exc:
        diag["token_telemetry_error"] = str(exc)[:200]

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

    # Final guarantee: whatever the collectors handed back, the download must be a
    # valid, parseable JSON file. HA serialises this with json.dumps; a non-string
    # key or a NaN/Infinity from a live subsystem would otherwise fail the download
    # or produce a file a strict parser rejects. (v9.78.0)
    return _json_safe(diag)

