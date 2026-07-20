"""Tests for the Web Research and Communication agents (v6.51.0). The HTTP
paths need live egress this harness lacks, so we test the PURE shapers
(_shape_ddg / _shape_searxng) and the pure conflict analysis exhaustively —
those hold the actual logic; the fetch wrappers are thin."""
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wr(load):
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


def test_new_agent_tools_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert {"web_research", "calendar_agenda"} <= names
    assert "web_research" in agent._TOOL_MAP
    assert "calendar_agenda" in agent._TOOL_MAP


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
