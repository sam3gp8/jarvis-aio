"""
JARVIS Web Research Agent (v6.51.0).

The blueprint's "Web Research Agent": real-time external knowledge retrieval so
JARVIS can answer "look into X" instead of shrugging at anything past its
training. Deliberately dependency-light and self-hosted-friendly:

  - Default backend is DuckDuckGo's Instant Answer API (api.duckduckgo.com) —
    no API key, no signup, returns a structured abstract + related topics.
  - Configurable to a SearXNG instance (search_backend="searxng",
    searxng_url=...) for richer results on a box that runs one.

This is a retrieval *tool*, not a scraper: it returns a short synthesized
summary the agent LLM then reasons over and speaks in JARVIS's voice. It never
dumps raw pages, never follows arbitrary links, and never raises — a failed
lookup returns an honest "couldn't find that" the model can relay.

Design notes:
  - Call-time config resolution (jarvis_config.get at call, not import) — the
    same discipline the DB layer learned the hard way.
  - HTTP via HA's shared aiohttp session, hard 10s timeout, one attempt (a
    voice interaction can't wait on retries; the model can re-ask).
  - Result capped and sanitized so an enormous abstract can't blow the context.
"""
from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_DDG_ENDPOINT = "https://api.duckduckgo.com/"
_MAX_ABSTRACT = 1200          # chars — plenty for the model, bounded for context
_MAX_RELATED = 5              # related topics to include
_TIMEOUT = 10                 # seconds — a voice turn can't wait longer


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        val = jarvis_config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


async def research(hass, query: str) -> dict:
    """
    Look something up on the web. Returns a dict the agent tool serializes:
        {"query", "answer", "source", "related": [...], "backend"}
    or {"query", "error"} on failure. Never raises.
    """
    q = " ".join(str(query or "").split())
    if not q:
        return {"query": "", "error": "empty query"}

    backend = str(_cfg("search_backend", "duckduckgo")).lower()
    try:
        if backend == "searxng":
            result = await _searxng(hass, q)
        else:
            result = await _duckduckgo(hass, q)
    except Exception as exc:
        _LOGGER.debug("web_research(%r) failed: %s", q, exc)
        result = {"query": q, "error": f"lookup failed: {exc}"}

    err = str(result.get("error") or "")
    if err and err.lower().startswith("no results"):
        grounded = await _llm_grounded_fallback(hass, q)
        if grounded:
            return grounded
    return result


async def _llm_grounded_fallback(hass, q: str) -> Optional[dict]:
    """Fall back to the configured LLM's own live web-grounding when the
    primary backend (DDG/SearXNG) comes back empty — common for fast-moving
    or very recent facts DDG's Instant Answer API was never built to answer.
    Opt-in (config: web_research_llm_fallback, off by default) — it's an
    extra LLM call the user hasn't explicitly asked for. Only Gemini has a
    grounding path today; any other provider (or a missing key) simply
    leaves the original error in place. Never raises."""
    try:
        from . import jarvis_config, ha_secrets

        if not _cfg("web_research_llm_fallback", False):
            return None

        provider = str(jarvis_config.get("llm_provider", "groq") or "").lower()
        model = str(jarvis_config.get("model", "") or "")
        if provider != "gemini":
            # The Main Agent may run a cheaper provider while the reasoning
            # tier (often used alongside web_research) runs Gemini — try that.
            provider = str(jarvis_config.get("reasoning_provider", "") or "").lower()
            model = str(jarvis_config.get("reasoning_model", "") or "")
        if provider != "gemini":
            return None

        api_key = await ha_secrets.async_get_provider_key(hass, "gemini")
        if not api_key:
            return None

        text = await _gemini_grounded_search(hass, api_key, model or "gemini-2.5-flash", q)
        if not text:
            return None
        return {
            "query": q,
            "answer": _clip(text, _MAX_ABSTRACT),
            "source": "",
            "source_name": "Gemini web grounding",
            "related": [],
            "backend": "gemini_grounding",
        }
    except Exception as exc:
        _LOGGER.debug("llm grounded fallback for %r failed: %s", q, exc)
        return None


async def _gemini_grounded_search(hass, api_key: str, model: str, q: str) -> Optional[str]:
    """Ask Gemini to answer using its own Google Search grounding tool.

    Uses Google's native Interactions API via google-genai rather than the
    OpenAI-compatible surface, where Google Search grounding is unavailable.
    One-shot, no conversation history, no JARVIS tool declarations. Returns
    the answer text, or None on any failure — this is a best-effort fallback.
    """
    if not api_key or not model:
        return None
    try:
        from google import genai
    except ImportError:
        _LOGGER.debug("google-genai is unavailable for grounded search")
        return None

    # The model picker may store the API resource prefix; the SDK expects the
    # bare model ID.
    model = model.removeprefix("models/")

    def _call():
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": _TIMEOUT * 1000},
        )
        return client.interactions.create(
            model=model,
            input=f"Search the web and answer concisely: {q}",
            tools=[{"type": "google_search"}],
        )

    try:
        # genai.Client() loads TLS certs and the interactions.create() call
        # does blocking network I/O — both are sync SDK calls, so they must
        # run off the event loop (see llm_provider.GeminiProvider.chat).
        interaction = await hass.async_add_executor_job(_call)
    except Exception as exc:
        _LOGGER.debug("gemini grounded search request failed: %s", exc)
        return None
    return (getattr(interaction, "output_text", "") or "").strip() or None



async def _duckduckgo(hass, q: str) -> dict:
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    params = {"q": q, "format": "json", "no_html": "1",
              "skip_disambig": "1", "t": "jarvis_home"}
    async with session.get(
        _DDG_ENDPOINT, params=params,
        timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
    ) as resp:
        # DDG (or a CDN/cache in front of it) sometimes answers 202 Accepted
        # with a perfectly good body rather than 200 — only a real error status
        # should give up without even reading it.
        if resp.status not in (200, 202):
            return {"query": q, "error": f"search returned HTTP {resp.status}"}
        data = await resp.json(content_type=None)

    return _shape_ddg(q, data)


def _shape_ddg(q: str, data: dict) -> dict:
    """Pure DDG-response → result shaper (unit-testable without network)."""
    data = data or {}
    abstract = (data.get("AbstractText") or data.get("Abstract") or "").strip()
    answer = (data.get("Answer") or "").strip()
    definition = (data.get("Definition") or "").strip()
    best = abstract or answer or definition

    related = []
    for topic in (data.get("RelatedTopics") or [])[:_MAX_RELATED * 2]:
        if isinstance(topic, dict) and topic.get("Text"):
            related.append(_clip(topic["Text"], 160))
        if len(related) >= _MAX_RELATED:
            break

    if not best and not related:
        return {"query": q, "error": "no results — try rephrasing, or this "
                                     "may need a full web search"}

    source = (data.get("AbstractURL") or data.get("DefinitionURL") or "").strip()
    source_name = (data.get("AbstractSource")
                   or data.get("DefinitionSource") or "").strip()
    return {
        "query": q,
        "answer": _clip(best, _MAX_ABSTRACT) if best else "",
        "source": source,
        "source_name": source_name,
        "related": related,
        "backend": "duckduckgo",
    }


async def _searxng(hass, q: str) -> dict:
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    base = str(_cfg("searxng_url", "")).rstrip("/")
    if not base:
        return {"query": q, "error": "searxng_url not configured"}
    session = async_get_clientsession(hass)
    params = {"q": q, "format": "json"}
    async with session.get(
        f"{base}/search", params=params,
        timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
    ) as resp:
        # Same 202-with-a-real-body allowance as the DuckDuckGo path above.
        if resp.status not in (200, 202):
            return {"query": q, "error": f"searxng returned HTTP {resp.status}"}
        data = await resp.json(content_type=None)
    return _shape_searxng(q, data)


def _shape_searxng(q: str, data: dict) -> dict:
    """Pure SearXNG-response → result shaper."""
    results = (data or {}).get("results") or []
    if not results:
        answers = (data or {}).get("answers") or []
        if answers:
            return {"query": q, "answer": _clip(answers[0], _MAX_ABSTRACT),
                    "source": "", "source_name": "", "related": [],
                    "backend": "searxng"}
        return {"query": q, "error": "no results"}
    top = results[0]
    related = [_clip(r.get("title", ""), 160) for r in results[1:_MAX_RELATED + 1]
               if r.get("title")]
    return {
        "query": q,
        "answer": _clip(top.get("content") or top.get("title") or "", _MAX_ABSTRACT),
        "source": top.get("url", ""),
        "source_name": top.get("engine", ""),
        "related": related,
        "backend": "searxng",
    }
