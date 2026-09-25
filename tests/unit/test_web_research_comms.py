"""Tests for the Web Research and Communication agents (v6.51.0). The HTTP
paths need live egress this harness lacks, so we test the PURE shapers
(_shape_ddg / _shape_searxng) and the pure conflict analysis exhaustively —
those hold the actual logic; the fetch wrappers are thin."""
import sys
import types
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wr(load, monkeypatch):
    if "aiohttp" not in sys.modules:
        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.ClientTimeout = lambda **k: None
        monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)
    return load("web_research")


@pytest.fixture
def comms(load):
    return load("comms")


# ── web_research shapers ─────────────────────────────────────────────────────

def test_ddg_abstract_becomes_answer(wr):
    out = wr._shape_ddg("who is ada lovelace", {
        "AbstractText": "Ada Lovelace was a 19th-century mathematician.",
        "AbstractURL": "https://en.wikipedia.org/wiki/Ada_Lovelace",
        "AbstractSource": "Wikipedia",
        "RelatedTopics": [],
    })
    assert "mathematician" in out["answer"]
    assert out["source_name"] == "Wikipedia"
    assert out["backend"] == "duckduckgo"
    assert "error" not in out


def test_ddg_answer_field_fallback(wr):
    out = wr._shape_ddg("2+2", {"Answer": "4", "AbstractText": ""})
    assert out["answer"] == "4"


def test_ddg_related_topics_capped_and_clipped(wr):
    topics = [{"Text": f"Topic number {i} " + "x" * 300} for i in range(12)]
    out = wr._shape_ddg("q", {"AbstractText": "abstract", "RelatedTopics": topics})
    assert len(out["related"]) <= wr._MAX_RELATED
    assert all(len(r) <= 161 for r in out["related"])   # clipped w/ ellipsis


def test_ddg_empty_is_honest_error(wr):
    out = wr._shape_ddg("asdfqwer nonsense", {"AbstractText": "", "RelatedTopics": []})
    assert "error" in out and "no results" in out["error"]


def test_ddg_abstract_clipped_to_max(wr):
    out = wr._shape_ddg("q", {"AbstractText": "word " * 1000})
    assert len(out["answer"]) <= wr._MAX_ABSTRACT + 1


def test_searxng_first_result_wins(wr):
    out = wr._shape_searxng("python gil", {"results": [
        {"title": "The GIL", "content": "The global interpreter lock…", "url": "http://x", "engine": "duckduckgo"},
        {"title": "More GIL", "url": "http://y"},
    ]})
    assert "interpreter" in out["answer"]
    assert out["source"] == "http://x"
    assert out["related"] == ["More GIL"]
    assert out["backend"] == "searxng"


def test_searxng_answers_fallback(wr):
    out = wr._shape_searxng("q", {"results": [], "answers": ["42"]})
    assert out["answer"] == "42"


def test_searxng_empty_error(wr):
    out = wr._shape_searxng("q", {"results": []})
    assert "error" in out


async def test_research_empty_query(wr):
    out = await wr.research(None, "   ")
    assert out["error"] == "empty query"


async def test_research_falls_back_to_gemini_grounding_when_ddg_empty(wr, monkeypatch, fake_hass):
    # trigger + grab the real (lazily-imported) sibling modules
    import importlib
    jarvis_config = importlib.import_module("jc.jarvis_config")
    ha_secrets = importlib.import_module("jc.ha_secrets")

    async def fake_ddg(hass, q):
        return {"query": q, "error": "no results — try rephrasing, or this "
                                     "may need a full web search"}

    async def fake_get_key(hass, provider):
        assert provider == "gemini"
        return "fake-key"

    async def fake_grounded(hass, api_key, model, query):
        assert api_key == "fake-key"
        assert "president" in query.lower()
        return "Example grounded answer from Gemini."

    monkeypatch.setattr(wr, "_duckduckgo", fake_ddg)
    monkeypatch.setattr(jarvis_config, "get",
                         lambda key, default=None: {"llm_provider": "gemini",
                                                     "model": "gemini-2.5-flash",
                                                     "web_research_llm_fallback": True}.get(key, default))
    monkeypatch.setattr(ha_secrets, "async_get_provider_key", fake_get_key)
    monkeypatch.setattr(wr, "_gemini_grounded_search", fake_grounded)

    out = await wr.research(fake_hass, "who is the current us president")
    assert "error" not in out
    assert "Example grounded answer" in out["answer"]
    assert out["backend"] == "gemini_grounding"


async def test_research_fallback_stays_off_by_default_even_for_gemini(wr, monkeypatch, fake_hass):
    # opt-in switch (web_research_llm_fallback) defaults to False — a Gemini
    # provider alone must not trigger the extra LLM call unless enabled
    import importlib
    jarvis_config = importlib.import_module("jc.jarvis_config")

    async def fake_ddg(hass, q):
        return {"query": q, "error": "no results"}

    def fail_if_called(*a, **k):
        raise AssertionError("grounded fallback must not run when the opt-in is off")

    monkeypatch.setattr(wr, "_duckduckgo", fake_ddg)
    monkeypatch.setattr(wr, "_gemini_grounded_search", fail_if_called)
    monkeypatch.setattr(jarvis_config, "get",
                         lambda key, default=None: {"llm_provider": "gemini"}.get(key, default))

    out = await wr.research(fake_hass, "who is the current us president")
    assert out["error"] == "no results"


async def test_research_keeps_original_error_for_non_gemini_provider(wr, monkeypatch, fake_hass):
    import importlib
    jarvis_config = importlib.import_module("jc.jarvis_config")

    async def fake_ddg(hass, q):
        return {"query": q, "error": "no results"}

    monkeypatch.setattr(wr, "_duckduckgo", fake_ddg)
    monkeypatch.setattr(jarvis_config, "get",
                         lambda key, default=None: {"llm_provider": "groq",
                                                     "web_research_llm_fallback": True}.get(key, default))

    out = await wr.research(fake_hass, "who is the current us president")
    assert out["error"] == "no results"


async def test_gemini_grounded_search_strips_models_prefix(wr, monkeypatch, fake_hass):
    # The model picker may return a models/ resource name; google-genai expects
    # the bare model ID for the native Interactions API.
    import sys
    captured = {}

    class _Interactions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return types.SimpleNamespace(output_text="answer")

    def _client(**kwargs):
        captured["client"] = kwargs
        return types.SimpleNamespace(interactions=_Interactions())

    genai = types.SimpleNamespace(Client=_client)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    text = await wr._gemini_grounded_search(
        fake_hass, "fake-key", "models/gemini-2.5-flash", "test query")

    assert text == "answer"
    assert captured["client"] == {
        "api_key": "fake-key",
        "http_options": {"timeout": 10000},
    }
    assert captured["request"] == {
        "model": "gemini-2.5-flash",
        "input": "Search the web and answer concisely: test query",
        "tools": [{"type": "google_search"}],
    }


def test_new_agent_tools_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert {"web_research", "calendar_agenda"} <= names
    assert "web_research" in agent._TOOL_MAP
    assert "calendar_agenda" in agent._TOOL_MAP


# ── HTTP 202: a real body must not be discarded as a hard failure ───────────
# DuckDuckGo (or a CDN/cache in front of it) sometimes answers 202 Accepted
# with a perfectly good result rather than 200 — treating any non-200 as an
# error threw away genuine answers and surfaced as "still processing".

class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
    def get(self, url, params=None, timeout=None):
        return self._resp


def _patch_session(monkeypatch, resp):
    import homeassistant.helpers.aiohttp_client as ac
    monkeypatch.setattr(ac, "async_get_clientsession",
                        lambda h: _FakeSession(resp), raising=False)


async def test_duckduckgo_202_with_body_is_used(wr, fake_hass, monkeypatch):
    _patch_session(monkeypatch, _FakeResp(202, {
        "AbstractText": "Ada Lovelace was a 19th-century mathematician.",
        "AbstractSource": "Wikipedia",
    }))
    out = await wr._duckduckgo(fake_hass, "who is ada lovelace")
    assert "error" not in out
    assert "mathematician" in out["answer"]


async def test_duckduckgo_real_error_status_still_fails(wr, fake_hass, monkeypatch):
    _patch_session(monkeypatch, _FakeResp(503, {}))
    out = await wr._duckduckgo(fake_hass, "q")
    assert "error" in out and "503" in out["error"]


async def test_searxng_202_with_body_is_used(wr, fake_hass, monkeypatch):
    monkeypatch.setattr(wr, "_cfg", lambda k, d: "http://searx.local" if k == "searxng_url" else d)
    _patch_session(monkeypatch, _FakeResp(202, {
        "results": [{"title": "The GIL", "content": "info", "url": "http://x", "engine": "ddg"}],
    }))
    out = await wr._searxng(fake_hass, "python gil")
    assert "error" not in out
    assert out["answer"] == "info"


async def test_searxng_real_error_status_still_fails(wr, fake_hass, monkeypatch):
    monkeypatch.setattr(wr, "_cfg", lambda k, d: "http://searx.local" if k == "searxng_url" else d)
    _patch_session(monkeypatch, _FakeResp(500, {}))
    out = await wr._searxng(fake_hass, "q")
    assert "error" in out and "500" in out["error"]


# ── comms conflict detection ────────────────────────────────────────────────

def _ev(title, start_h, end_h, all_day=False):
    base = datetime(2026, 7, 20, 0, 0)
    return {"calendar": f"calendar.{title}", "title": title,
            "start": base + timedelta(hours=start_h),
            "end": base + timedelta(hours=end_h),
            "all_day": all_day, "active": True}


def test_no_conflicts_when_spaced(comms):
    evts = [_ev("a", 9, 10), _ev("b", 12, 13)]
    r = comms.find_conflicts(evts, 15)
    assert r["overlaps"] == [] and r["tight"] == []


def test_overlap_detected(comms):
    evts = [_ev("a", 9, 11), _ev("b", 10, 12)]
    r = comms.find_conflicts(evts, 15)
    assert len(r["overlaps"]) == 1
    a, b = r["overlaps"][0]
    assert {a["title"], b["title"]} == {"a", "b"}


def test_tight_transition_flagged(comms):
    evts = [_ev("a", 9, 10), {"calendar": "calendar.b", "title": "b",
            "start": datetime(2026, 7, 20, 10, 10), "end": datetime(2026, 7, 20, 11, 0),
            "all_day": False, "active": True}]
    r = comms.find_conflicts(evts, 15)
    assert len(r["tight"]) == 1 and r["overlaps"] == []


def test_tight_not_flagged_when_gap_sufficient(comms):
    evts = [_ev("a", 9, 10), _ev("b", 11, 12)]   # 60-min gap
    r = comms.find_conflicts(evts, 15)
    assert r["tight"] == []


def test_all_day_events_dont_conflict(comms):
    evts = [_ev("holiday", 0, 24, all_day=True), _ev("meeting", 10, 11)]
    r = comms.find_conflicts(evts, 15)
    assert r["overlaps"] == [] and r["tight"] == []


def test_parse_handles_date_and_datetime(comms):
    assert comms._parse("2026-07-20") == datetime(2026, 7, 20, 0, 0)
    assert comms._parse("2026-07-20T14:30:00") == datetime(2026, 7, 20, 14, 30)
    assert comms._parse("2026-07-20T14:30:00+00:00") == datetime(2026, 7, 20, 14, 30)
    assert comms._parse("2026-07-20T14:30:00Z") == datetime(2026, 7, 20, 14, 30)
    assert comms._parse("garbage") is None
    assert comms._parse(None) is None


def test_agenda_never_raises_without_calendars(comms, fake_hass):
    out = comms.agenda(fake_hass, 24)
    assert out["count"] == 0 and out["events"] == [] and out["conflicts"] == []
