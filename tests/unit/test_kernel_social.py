"""Phase W (pure) — social & relationship intelligence, consent-bounded.

`kernel/social.py` holds per-person preferences and personalizes within the
information-flow boundary: a preference for person A is never surfaced to person B
without A's consent. Consent-gated, purgeable, and it holds no actuator.
"""
import pytest


@pytest.fixture
def S(load):
    # social imports kernel.privacy relatively — load privacy first so it's cached.
    load("kernel.privacy")
    return load("kernel.social")


def _pref(S, subject, key, value, cls=None, audiences=()):
    kw = {"subject": subject, "key": key, "value": value,
          "consented_audiences": frozenset(audiences)}
    if cls is not None:
        kw["classification"] = cls
    return S.Preference(**kw)


def test_remember_newest_wins_per_key(S):
    m = S.model_for("alice")
    m = S.remember(m, _pref(S, "alice", "lighting", "warm"))
    m = S.remember(m, _pref(S, "alice", "lighting", "cool"))
    assert S.preference(m, "lighting") == "cool"
    assert len(m.preferences) == 1


def test_remember_ignores_foreign_subject(S):
    m = S.model_for("alice")
    m = S.remember(m, _pref(S, "bob", "lighting", "warm"))   # wrong subject
    assert m.is_empty


def test_subject_reads_own_preference(S):
    m = S.remember(S.model_for("alice"), _pref(S, "alice", "music", "jazz"))
    assert S.preference(m, "music") == "jazz"
    assert S.preference(m, "absent") is None


def test_personalize_for_blocks_cross_person_without_consent(S):
    m = S.remember(S.model_for("alice"), _pref(S, "alice", "music", "jazz"))
    # Serving BOB must not reveal alice's personal preference → default.
    assert S.personalize_for(m, "music", audience="bob", default="radio") == "radio"
    # Serving ALICE herself → her preference.
    assert S.personalize_for(m, "music", audience="alice", default="radio") == "jazz"


def test_personalize_for_allows_consented_audience(S):
    m = S.remember(S.model_for("alice"),
                   _pref(S, "alice", "music", "jazz", audiences=("bob",)))
    assert S.personalize_for(m, "music", audience="bob", default="radio") == "jazz"


def test_personalize_household_preference_is_shared(S):
    m = S.remember(S.model_for("alice"),
                   _pref(S, "alice", "unit", "metric", cls="household"))
    assert S.personalize_for(m, "unit", audience="bob", default="imperial") == "metric"


def test_purge_empties_the_model(S):
    m = S.remember(S.model_for("alice"), _pref(S, "alice", "music", "jazz"))
    purged = S.purge(m)
    assert purged.is_empty and purged.subject == "alice"


def test_summarize(S):
    m = S.model_for("alice")
    m = S.remember(m, _pref(S, "alice", "music", "jazz"))
    m = S.remember(m, _pref(S, "alice", "lighting", "warm"))
    s = S.summarize(m)
    assert s["subject"] == "alice" and s["preferences"] == 2
    assert s["keys"] == ["lighting", "music"]
