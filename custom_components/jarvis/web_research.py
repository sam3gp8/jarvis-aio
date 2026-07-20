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
            return await _searxng(hass, q)
        return await _duckduckgo(hass, q)
    except Exception as exc:
        _LOGGER.debug("web_research(%r) failed: %s", q, exc)
        return {"query": q, "error": f"lookup failed: {exc}"}


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
        if resp.status != 200:
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
        if resp.status != 200:
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
