"""
Core service-dependency health (v6.60.0) — part of the JARVIS diagnostics
package.

Distinct from monitor.InfrastructureTriage, which watches *physical*
infrastructure (power draw, storage, network hardware) and speaks proactively.
This module checks the *software services JARVIS itself calls* and answers
"is everything JARVIS depends on actually up?" for:

  - LLM        — the conversation/reasoning backend (Ollama/Groq/etc.)
  - Embeddings — Ollama embedding endpoint, when semantic search is enabled
  - TTS        — the configured text-to-speech engine (Piper, etc.)
  - STT        — the speech-to-text engine (Whisper, etc.)

Each check returns a small dict {name, key, status, detail, ...} and never
raises. Deliberately narrow: the four services JARVIS uses directly, not the
whole home. Signals differ by service — the LLM reuses the connectivity circuit
breaker plus a live ping; embeddings reuse embeddings.probe(); TTS/STT are HA
entities, so we confirm the configured engine exists and isn't unavailable.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_OK = "ok"
_WARN = "warn"
_DOWN = "down"
_OFF = "off"        # not configured / not enabled — not a failure


def _cfg(key: str, default=None):
    try:
        from .. import jarvis_config
        v = jarvis_config.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


# ── individual checks ────────────────────────────────────────────────────────

async def _check_llm(hass) -> dict:
    out = {"name": "LLM", "key": "llm", "status": _OFF, "detail": ""}
    base = str(_cfg("llm_base_url", "") or "").strip()
    provider = str(_cfg("llm_provider", "") or _cfg("provider", "") or "").strip()

    breaker = None
    try:
        from .. import connectivity
        breaker = connectivity.status()
    except Exception:
        pass

    if not base and provider in ("", "ollama"):
        out["detail"] = "no LLM base URL configured"
        return out

    if base:
        ok, detail = await _ping_ollama(hass, base)
        out["status"] = _OK if ok else _DOWN
        out["detail"] = detail
        out["base"] = _redact(base)
    else:
        if breaker and breaker.get("state") == "OPEN":
            out["status"] = _WARN
            out["detail"] = f"{provider or 'cloud'} provider — breaker OPEN (recent failures)"
        else:
            out["status"] = _OK
            out["detail"] = f"{provider or 'cloud'} provider — no recent failures"
    if breaker:
        out["breaker"] = breaker.get("state")
    return out


async def _ping_ollama(hass, base: str) -> tuple[bool, str]:
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        import aiohttp
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(hass)
        async with session.get(
            f"{base}/api/tags", timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json(content_type=None)
                    n = len(data.get("models", []) or [])
                    return True, f"reachable — {n} model(s) available"
                except Exception:
                    return True, "reachable"
            return False, f"HTTP {resp.status} from {base}"
    except Exception as exc:
        return False, f"unreachable: {exc}"


async def _check_embeddings(hass) -> dict:
    out = {"name": "Embeddings", "key": "embeddings", "status": _OFF, "detail": ""}
    try:
        from .. import embeddings
        if not embeddings.is_enabled():
            out["detail"] = "semantic search disabled (keyword search active)"
            return out
        res = await embeddings.probe(hass)
        if res.get("ok"):
            out["status"] = _OK
            out["detail"] = f"Ollama {res.get('model')} — {res.get('dim')}-dim vectors"
        else:
            out["status"] = _DOWN
            out["detail"] = res.get("error", "embedding endpoint not responding")
    except Exception as exc:
        out["status"] = _DOWN
        out["detail"] = str(exc)
    return out


def _check_tts(hass) -> dict:
    return _check_speech_entity(
        hass, domain="tts", name="TTS",
        configured=str(_cfg("tts_engine", "auto") or "auto"))


def _check_stt(hass) -> dict:
    return _check_speech_entity(
        hass, domain="stt", name="STT",
        configured=str(_cfg("stt_engine", "auto") or "auto"))


def _check_speech_entity(hass, domain: str, name: str, configured: str) -> dict:
    out = {"name": name, "key": domain, "status": _OFF, "detail": ""}
    try:
        states = hass.states.async_all(domain)
    except Exception:
        states = []
    if not states:
        out["status"] = _DOWN
        out["detail"] = f"no {domain} engine present (is the add-on installed?)"
        return out

    if configured in ("", "auto"):
        avail = [s for s in states if s.state not in ("unavailable", "unknown")]
        if avail:
            out["status"] = _OK
            out["detail"] = f"{len(avail)} engine(s) available (auto)"
            out["entity"] = avail[0].entity_id
        else:
            out["status"] = _DOWN
            out["detail"] = f"{len(states)} engine(s) present but all unavailable"
        return out

    match = None
    for s in states:
        if s.entity_id == configured or configured in s.entity_id:
            match = s
            break
    if match is None:
        out["status"] = _WARN
        out["detail"] = f"configured '{configured}' not found; {len(states)} other(s) present"
    elif match.state in ("unavailable", "unknown"):
        out["status"] = _DOWN
        out["detail"] = f"{match.entity_id} is {match.state}"
    else:
        out["status"] = _OK
        out["detail"] = f"{match.entity_id} available"
        out["entity"] = match.entity_id
    return out


# ── aggregate ────────────────────────────────────────────────────────────────

def _redact(url: str) -> str:
    try:
        if "@" in url and "//" in url:
            scheme, rest = url.split("//", 1)
            return scheme + "//" + rest.split("@", 1)[1]
    except Exception:
        pass
    return url


async def run_service_health(hass) -> dict:
    """Run all core dependency checks. Returns
    {overall, services: [...], summary}. Never raises."""
    services = []
    for label, key, fn, is_async in (
        ("LLM", "llm", _check_llm, True),
        ("Embeddings", "embeddings", _check_embeddings, True),
        ("TTS", "tts", _check_tts, False),
        ("STT", "stt", _check_stt, False),
    ):
        try:
            services.append(await fn(hass) if is_async else fn(hass))
        except Exception as exc:
            services.append({"name": label, "key": key, "status": _DOWN,
                             "detail": str(exc)})

    active = [s for s in services if s.get("status") != _OFF]
    if any(s["status"] == _DOWN for s in active):
        overall = _DOWN
    elif any(s["status"] == _WARN for s in active):
        overall = _WARN
    elif active:
        overall = _OK
    else:
        overall = _OFF

    up = sum(1 for s in active if s["status"] == _OK)
    summary = (f"{up}/{len(active)} core services healthy" if active
               else "no core services active")
    return {"overall": overall, "services": services, "summary": summary}
