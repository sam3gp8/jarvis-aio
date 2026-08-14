"""Tests for routine-start anticipation (cognition.predict_routine_start, v6.85.0)."""
import datetime
import json

import pytest


@pytest.fixture
def cog(load):
    c = load("cognition")
    c._RECUR_ALERTED.clear()
    return c


@pytest.fixture
def env(cog, load, monkeypatch):
    pp = load("person_patterns")
    jc = load("jarvis_config")
    idm = load("identity")
    holder = {"list": []}
    home = {"set": {"sam"}}
    monkeypatch.setattr(pp, "read", lambda person=None, db_path=None: holder["list"])
    monkeypatch.setattr(idm, "_home_people", lambda hass: list(home["set"]))
    monkeypatch.setattr(idm, "normalize", lambda n: n.strip().lower().replace(" ", "_"))
    cfg = {"routine_alerts_enabled": True}
    monkeypatch.setattr(jc, "get", lambda k, d=None: cfg.get(k, d))
    return holder, home, cfg


def _routine(hour, person="sam", desc="start the coffee", conf=0.8):
    return {"id": 1, "person": person, "pattern_type": "time_routine",
            "description": desc, "data": json.dumps({"hour": hour}),
            "confidence": conf, "occurrences": 8}


def _now_at(minute=5):
    now_dt = datetime.datetime.now().replace(minute=minute, second=0, microsecond=0)
    return now_dt.timestamp(), now_dt


def test_prompts_at_usual_time(cog, env, fake_hass):
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    holder["list"] = [_routine(now_dt.hour)]           # hour matches, +5 min → within tol
    preds = cog.predict_routine_start(fake_hass, now)
    assert len(preds) == 1
    assert preds[0]["type"] == "anticipation_routine"
    assert "start the coffee" in preds[0]["message"]


def test_no_prompt_if_person_away(cog, env, fake_hass):
    holder, home, cfg = env
    home["set"] = set()
    now, now_dt = _now_at(5)
    holder["list"] = [_routine(now_dt.hour)]
    assert cog.predict_routine_start(fake_hass, now) == []


def test_no_prompt_wrong_hour(cog, env, fake_hass):
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    holder["list"] = [_routine((now_dt.hour + 3) % 24)]
    assert cog.predict_routine_start(fake_hass, now) == []


def test_no_prompt_low_confidence(cog, env, fake_hass):
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    holder["list"] = [_routine(now_dt.hour, conf=0.4)]
    assert cog.predict_routine_start(fake_hass, now) == []


def test_dedup_same_day(cog, env, fake_hass):
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    holder["list"] = [_routine(now_dt.hour)]
    assert len(cog.predict_routine_start(fake_hass, now)) == 1
    assert cog.predict_routine_start(fake_hass, now) == []


def test_disabled(cog, env, fake_hass):
    holder, home, cfg = env
    cfg["routine_alerts_enabled"] = False
    now, now_dt = _now_at(5)
    holder["list"] = [_routine(now_dt.hour)]
    assert cog.predict_routine_start(fake_hass, now) == []


def test_no_hour_skipped(cog, env, fake_hass):
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    r = _routine(now_dt.hour)
    r["data"] = json.dumps({})                         # no hour
    holder["list"] = [r]
    assert cog.predict_routine_start(fake_hass, now) == []
