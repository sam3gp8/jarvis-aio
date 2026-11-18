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


# ── #560: dedup + structured preference + noise-domain skip ──────────────────
def _routine_state(hour, state, desc, person="sam", entity=None, conf=0.8, rid=1):
    data = {"hour": hour, "state": state}
    if entity:
        data["entity"] = entity
    return {"id": rid, "person": person, "pattern_type": "time_routine",
            "description": desc, "data": json.dumps(data),
            "confidence": conf, "occurrences": 8}


def test_560_dedup_duplicate_rows(cog, env, fake_hass):
    """Several rows for one routine (rolling N-of-M stats baked into the desc) →
    ONE alert per (person, state, hour) per cycle."""
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    h = now_dt.hour
    holder["list"] = [
        _routine_state(h, "on", "coffee on 19 of 28 days", rid=1),
        _routine_state(h, "on", "coffee on 22 of 30 days", rid=2),
        _routine_state(h, "on", "coffee on 20 of 31 days", rid=3),
    ]
    assert len(cog.predict_routine_start(fake_hass, now)) == 1


def test_560_prefers_structured_row(cog, env, fake_hass):
    """A structured (entity+state) row wins dedup over a bare English
    routine_other row for the same routine, so the surviving alert localizes."""
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    h = now_dt.hour
    holder["list"] = [
        _routine_state(h, "in_progress",
                       "Box IMOU turns in_progress around 05:00 on 20 of 30 days", rid=1),
        _routine_state(h, "in_progress", "irrelevant", entity="switch.box_imou", rid=2),
    ]
    preds = cog.predict_routine_start(fake_hass, now)
    assert len(preds) == 1
    assert "on 20 of 30 days" not in preds[0]["message"]   # the structured one won


def test_560_skips_event_domain_routine(cog, env, fake_hass):
    """A stored event.*/update.* routine never produces a push (#560 flaw 4
    belt-and-suspenders for rows already in the DB)."""
    holder, home, cfg = env
    now, now_dt = _now_at(5)
    h = now_dt.hour
    holder["list"] = [
        _routine_state(h, "in_progress", "Backup turns in_progress",
                       entity="event.backup_backup_automatico", rid=1),
    ]
    assert cog.predict_routine_start(fake_hass, now) == []
