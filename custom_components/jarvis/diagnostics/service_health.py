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

import asyncio
import logging
import time

_LOGGER = logging.getLogger(__name__)

_OK = "ok"          # verified working (probe succeeded OR real use succeeded)
_IDLE = "idle"      # reachable but not recently exercised, or a synthetic poke
                    # missed — NOT a failure, shown calmly (never red)
_WARN = "warn"      # a soft concern (e.g. cloud breaker open)
_DOWN = "down"      # a GENUINE failure during real use — the only alarming state
_OFF = "off"        # not configured / not enabled — not a failure

# ── real-usage outcome tracking (v6.70.3) ────────────────────────────────────
# A synthetic health poke must never cry wolf: only a failure during ACTUAL use
# marks a service DOWN. Real call sites (an embedding during ingest/search, an
# STT transcription, an LLM/agent call, a TTS announce) report their outcome
# here; the checks below consult it. A poke miss can only ever yield IDLE.
_USAGE: dict = {}                  # key → {"ok_ts", "fail_ts", "fail_detail"}
_REAL_FAIL_TTL = 900.0             # a real failure marks DOWN for this long,
                                   # until a later success or the window clears
_RECENT_USE_TTL = 900.0            # "recently exercised" window for OK-by-use


def record_usage(key: str, ok: bool, detail: str = "") -> None:
    """Real call sites report actual success/failure here (never raises).
    ok=True on a genuine successful call; ok=False when a real call the user
    triggered actually failed. This is what can legitimately set DOWN."""
    try:
        rec = _USAGE.setdefault(key, {})
        now = time.time()
        if ok:
            rec["ok_ts"] = now
            rec.pop("fail_ts", None)      # a success clears a prior real failure
            rec.pop("fail_detail", None)
        else:
            rec["fail_ts"] = now
            rec["fail_detail"] = detail or "a real request failed"
    except Exception:
        pass


def _recent_real_failure(key: str):
    """Return fail detail if a real-use failure is within the DOWN window and no
    later success cleared it, else None."""
    rec = _USAGE.get(key) or {}
    ft = rec.get("fail_ts")
    if not ft:
        return None
    if (time.time() - ft) > _REAL_FAIL_TTL:
        return None
    ot = rec.get("ok_ts")
    if ot and ot >= ft:                   # a success after the failure clears it
        return None
    return rec.get("fail_detail", "a real request failed")


def _recently_used_ok(key: str) -> bool:
    """True if a real call succeeded within the recent-use window."""
    rec = _USAGE.get(key) or {}
    ot = rec.get("ok_ts")
    return bool(ot and (time.time() - ot) <= _RECENT_USE_TTL)


async def _retry_async(coro_fn, attempts: int = 3, delay: float = 1.2):
    """Call an async probe up to `attempts` times, tolerating transient misses
    (e.g. a cold model that 404s until loaded). Returns the first truthy-ok
    result, else the last result. Never raises."""
    last = None
    for i in range(attempts):
        try:
            last = await coro_fn()
        except Exception as exc:
            last = {"ok": False, "error": str(exc)}
        if isinstance(last, dict) and last.get("ok"):
            return last
        if i < attempts - 1:
            await asyncio.sleep(delay)
    return last


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

    # A real agent/conversation call that failed is authoritative → DOWN.
    real_fail = _recent_real_failure("llm")
    if real_fail:
        out["status"] = _DOWN
        out["detail"] = f"a real agent call failed: {real_fail}"
        if breaker:
            out["breaker"] = breaker.get("state")
        return out

    if base:
        ok, detail = await _ping_ollama(hass, base)
        if not ok:
            # retry once — a momentary miss shouldn't alarm if usage is fine
            await asyncio.sleep(1.0)
            ok, detail = await _ping_ollama(hass, base)
        if ok:
            out["status"] = _OK
        elif _recently_used_ok("llm"):
            out["status"] = _OK
            detail = "working (recent agent call succeeded; ping missed)"
        else:
            out["status"] = _IDLE
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

        # A real embedding failure during ingest/search is authoritative → DOWN.
        real_fail = _recent_real_failure("embeddings")
        if real_fail:
            out["status"] = _DOWN
            out["detail"] = f"a real embedding request failed: {real_fail}"
            return out

        # Otherwise probe — but tolerate a transient/cold miss (retry), and never
        # let a synthetic miss alone go red.
        res = await _retry_async(lambda: embeddings.probe(hass))
        if res and res.get("ok"):
            out["status"] = _OK
            out["detail"] = f"Ollama {res.get('model')} — {res.get('dim')}-dim vectors"
        elif _recently_used_ok("embeddings"):
            # poke missed (likely model unloaded at idle) but real use worked
            # recently — this is fine, not a failure.
            out["status"] = _OK
            out["detail"] = "working (recent embedding succeeded; model idle for the health poke)"
        else:
            out["status"] = _IDLE
            out["detail"] = ("not exercised recently — the embed model may be "
                             "unloaded at idle; it loads on demand when you "
                             "ingest or search documents")
    except Exception as exc:
        # An error in the CHECK itself isn't a service outage → IDLE, not DOWN.
        out["status"] = _IDLE
        out["detail"] = f"health check could not verify: {exc}"
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

    # A real transcription/synthesis failure during use is authoritative → DOWN.
    real_fail = _recent_real_failure(domain)
    if real_fail:
        out["status"] = _DOWN
        out["detail"] = f"a real {domain} request failed: {real_fail}"
        return out
    used_ok = _recently_used_ok(domain)

    try:
        states = hass.states.async_all(domain)
    except Exception:
        states = []
    if not states:
        # No engine entity at all. If real use worked recently it clearly exists
        # somewhere; otherwise this is a genuine "nothing installed" → but that's
        # a setup gap, shown as IDLE (calm), not a red failure.
        out["status"] = _OK if used_ok else _IDLE
        out["detail"] = (f"{domain} used successfully recently"
                         if used_ok else
                         f"no {domain} engine registered (install the add-on to enable)")
        return out

    if configured in ("", "auto"):
        avail = [s for s in states if s.state not in ("unavailable", "unknown")]
        if avail:
            out["status"] = _OK
            out["detail"] = f"{len(avail)} engine(s) available (auto)"
            out["entity"] = avail[0].entity_id
        elif used_ok:
            # engines report unavailable at idle but real use worked — fine.
            out["status"] = _OK
            out["detail"] = f"{len(states)} engine(s) present, idle (recent {domain} succeeded)"
        else:
            # present but idle/unavailable and not recently used — NOT a failure,
            # many engines only go available on demand.
            out["status"] = _IDLE
            out["detail"] = (f"{len(states)} engine(s) present, idle — they come "
                             f"available on demand when {domain} is used")
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
        out["status"] = _OK if used_ok else _IDLE
        out["detail"] = (f"{match.entity_id} idle (recent {domain} succeeded)"
                         if used_ok else
                         f"{match.entity_id} idle — comes available on demand")
        out["entity"] = match.entity_id
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


def _check_cameras(hass) -> dict:
    """Camera availability — informational (a WARN, never the alarming DOWN),
    since cameras are a feature, not the core brain. OFF when none are
    configured (they're optional)."""
    out = {"name": "Cameras", "key": "cameras", "status": _OFF, "detail": ""}
    try:
        states = hass.states.async_all("camera")
    except Exception:
        states = []
    if not states:
        out["detail"] = "no cameras configured (optional)"
        return out
    total = len(states)
    down = [s.entity_id for s in states
            if str(getattr(s, "state", "")).lower()
            in ("unavailable", "unknown", "none", "")]
    avail = total - len(down)
    if not down:
        out["status"] = _OK
        out["detail"] = "%d camera(s) available" % total
    else:
        out["status"] = _WARN
        out["detail"] = ("all %d camera(s) unavailable" % total if avail == 0
                         else "%d/%d available, %d unavailable" % (avail, total, len(down)))
    return out


def _check_routines(hass) -> dict:
    """Config sanity: a very high identity-confidence bar starves per-person
    routine attribution (few observations ever clear it), so surface it here."""
    out = {"name": "Routines", "key": "routines", "status": _OK, "detail": ""}
    try:
        conf = float(_cfg("identity_min_confidence", 0.45))
        if conf >= 0.85:
            out["status"] = _WARN
            out["detail"] = (f"identity confidence {conf:.2f} is very high \u2014 per-person "
                             "routines rarely attribute; lower to ~0.5\u20130.6")
        else:
            out["detail"] = f"identity confidence {conf:.2f} \u2014 attribution active"
    except Exception as exc:
        out["status"] = _IDLE
        out["detail"] = str(exc)[:80]
    return out


async def run_service_health(hass) -> dict:
    """Run all core dependency checks. Returns
    {overall, services: [...], summary}. Never raises."""
    services = []
    for label, key, fn, is_async in (
        ("LLM", "llm", _check_llm, True),
        ("Embeddings", "embeddings", _check_embeddings, True),
        ("TTS", "tts", _check_tts, False),
        ("STT", "stt", _check_stt, False),
        ("Cameras", "cameras", _check_cameras, False),
        ("Routines", "routines", _check_routines, False),
    ):
        try:
            services.append(await fn(hass) if is_async else fn(hass))
        except Exception as exc:
            services.append({"name": label, "key": key, "status": _DOWN,
                             "detail": str(exc)})

    active = [s for s in services if s.get("status") != _OFF]
    # DOWN (real-use failure) is the only alarming state. IDLE/OK are both "fine".
    if any(s["status"] == _DOWN for s in active):
        overall = _DOWN
    elif any(s["status"] == _WARN for s in active):
        overall = _WARN
    elif active:
        overall = _OK           # all OK or IDLE — nothing actually failing
    else:
        overall = _OFF

    ok = sum(1 for s in active if s["status"] == _OK)
    idle = sum(1 for s in active if s["status"] == _IDLE)
    down = sum(1 for s in active if s["status"] == _DOWN)
    if not active:
        summary = "no core services active"
    elif down:
        summary = f"{down} service(s) failing — {ok}/{len(active)} healthy"
    elif idle:
        summary = f"{ok}/{len(active)} healthy, {idle} idle (not recently used)"
    else:
        summary = f"all {len(active)} core services healthy"
    return {"overall": overall, "services": services, "summary": summary}
