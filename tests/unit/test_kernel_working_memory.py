"""Tests for the working-memory primitive (kernel Phase K).

Pure: no Home Assistant, deterministic ``now`` passed in everywhere so scoring
and eviction are reproducible.
"""
import pytest


@pytest.fixture
def wm_mod(load):
    return load("kernel.working_memory")


@pytest.fixture
def wm(wm_mod):
    return wm_mod.WorkingMemory(capacity=4, half_life=100.0)


def test_canonical_kinds_wellformed(wm_mod):
    assert wm_mod.KINDS[0] == wm_mod.KIND_SITUATION
    assert wm_mod.KIND_CONSTRAINT in wm_mod.KINDS
    assert len(wm_mod.KINDS) == len(set(wm_mod.KINDS)), "kinds must be unique"


def test_remember_and_get(wm, wm_mod):
    it = wm.remember(wm_mod.KIND_PERSON, "sam", content="home", salience=0.8, now=0.0)
    assert it.kind == wm_mod.KIND_PERSON and it.subject == "sam"
    assert wm.get(wm_mod.KIND_PERSON, "sam").content == "home"
    assert (wm_mod.KIND_PERSON, "sam") in wm
    assert len(wm) == 1


def test_salience_is_clamped(wm, wm_mod):
    hi = wm.remember(wm_mod.KIND_OBSERVATION, "a", salience=5.0, now=0.0)
    lo = wm.remember(wm_mod.KIND_OBSERVATION, "b", salience=-3.0, now=0.0)
    assert hi.salience == 1.0 and lo.salience == 0.0


def test_dedup_refreshes_not_duplicates(wm, wm_mod):
    wm.remember(wm_mod.KIND_DEVICE, "lamp", content="on", salience=0.4, now=0.0)
    wm.remember(wm_mod.KIND_DEVICE, "lamp", content="off", salience=0.9, now=50.0)
    assert len(wm) == 1
    cur = wm.get(wm_mod.KIND_DEVICE, "lamp")
    assert cur.content == "off" and cur.salience == 0.9 and cur.ts == 50.0


def test_decay_lowers_score_over_time(wm_mod):
    it = wm_mod.WorkingItem(kind=wm_mod.KIND_OBSERVATION, subject="x",
                            salience=1.0, ts=0.0)
    assert it.score(now=0.0, half_life=100.0) == pytest.approx(1.0)
    assert it.score(now=100.0, half_life=100.0) == pytest.approx(0.5)
    assert it.score(now=200.0, half_life=100.0) == pytest.approx(0.25)


def test_pinned_item_does_not_decay(wm_mod):
    it = wm_mod.WorkingItem(kind=wm_mod.KIND_CONSTRAINT, subject="quiet",
                            salience=0.6, pinned=True, ts=0.0)
    assert it.score(now=10_000.0, half_life=100.0) == pytest.approx(0.6)


def test_zero_halflife_is_guarded(wm_mod):
    it = wm_mod.WorkingItem(kind="observation", subject="x", salience=1.0, ts=0.0)
    # Must not raise / divide-by-zero; a non-positive half-life collapses to the
    # minimum, so an aged item scores ~0 rather than erroring.
    assert it.score(now=5.0, half_life=0.0) == pytest.approx(0.0, abs=1e-6)


def test_eviction_drops_lowest_score(wm, wm_mod):
    # Capacity 4. Fill with 4, then add a 5th — the weakest must fall out.
    wm.remember(wm_mod.KIND_OBSERVATION, "weak", salience=0.1, now=0.0)
    wm.remember(wm_mod.KIND_OBSERVATION, "b", salience=0.5, now=0.0)
    wm.remember(wm_mod.KIND_OBSERVATION, "c", salience=0.6, now=0.0)
    wm.remember(wm_mod.KIND_OBSERVATION, "d", salience=0.7, now=0.0)
    wm.remember(wm_mod.KIND_OBSERVATION, "e", salience=0.9, now=0.0)
    assert len(wm) == 4
    assert (wm_mod.KIND_OBSERVATION, "weak") not in wm
    assert (wm_mod.KIND_OBSERVATION, "e") in wm


def test_pinned_survive_eviction(wm, wm_mod):
    # Four pinned-low + a flood of stronger non-pinned: the pins stay, since
    # non-pinned items are evicted first.
    for s in ("p1", "p2"):
        wm.remember(wm_mod.KIND_CONSTRAINT, s, salience=0.05, pinned=True, now=0.0)
    for s in ("n1", "n2", "n3", "n4"):
        wm.remember(wm_mod.KIND_OBSERVATION, s, salience=0.9, now=0.0)
    assert len(wm) == 4
    assert (wm_mod.KIND_CONSTRAINT, "p1") in wm
    assert (wm_mod.KIND_CONSTRAINT, "p2") in wm


def test_eviction_among_pins_is_bounded_and_deterministic(wm_mod):
    # When pins alone exceed capacity, the weakest pin is evicted — still bounded.
    m = wm_mod.WorkingMemory(capacity=2, half_life=100.0)
    m.remember(wm_mod.KIND_CONSTRAINT, "lo", salience=0.1, pinned=True, now=0.0)
    m.remember(wm_mod.KIND_CONSTRAINT, "mid", salience=0.5, pinned=True, now=0.0)
    m.remember(wm_mod.KIND_CONSTRAINT, "hi", salience=0.9, pinned=True, now=0.0)
    assert len(m) == 2
    assert (wm_mod.KIND_CONSTRAINT, "lo") not in m
    assert (wm_mod.KIND_CONSTRAINT, "hi") in m


def test_recent_low_can_outrank_stale_high(wm, wm_mod):
    wm.remember(wm_mod.KIND_OBSERVATION, "stale", salience=0.9, now=0.0)
    wm.remember(wm_mod.KIND_OBSERVATION, "fresh", salience=0.5, now=400.0)
    # At now=500: stale (0.9) has aged 5 half-lives → ~0.028; fresh (0.5) just
    # one → 0.25, so the recent lower-salience note outranks the stale strong one.
    top = wm.top(1, now=500.0)
    assert top[0].subject == "fresh"


def test_top_and_by_kind_ordering(wm, wm_mod):
    wm.remember(wm_mod.KIND_PERSON, "sam", salience=0.8, now=0.0)
    wm.remember(wm_mod.KIND_DEVICE, "lamp", salience=0.9, now=0.0)
    wm.remember(wm_mod.KIND_PERSON, "guest", salience=0.3, now=0.0)
    people = wm.by_kind(wm_mod.KIND_PERSON, now=0.0)
    assert [p.subject for p in people] == ["sam", "guest"]
    assert wm.top(1, now=0.0)[0].subject == "lamp"
    assert wm.top(0, now=0.0) == []


def test_snapshot_groups_and_bounds(wm_mod):
    m = wm_mod.WorkingMemory(capacity=20, half_life=100.0)
    m.remember(wm_mod.KIND_SITUATION, "normal", salience=0.7, now=0.0)
    for i in range(5):
        m.remember(wm_mod.KIND_OBSERVATION, f"o{i}", salience=0.1 * (i + 1), now=0.0)
    snap = m.snapshot(now=0.0, per_kind=3, top_k=4)
    assert snap.total == 6 and snap.capacity == 20 and not snap.is_empty
    # per_kind caps each section.
    assert len(snap.by_kind[wm_mod.KIND_OBSERVATION]) == 3
    # situation present, canonical order has it before observation.
    assert snap.kinds()[0] == wm_mod.KIND_SITUATION
    # top_k caps the flat salient list.
    assert len(snap.salient) == 4
    # strongest observation (o4, 0.5) appears before the weakest within its section.
    obs = snap.by_kind[wm_mod.KIND_OBSERVATION]
    assert obs[0].subject == "o4"
    d = snap.to_dict()
    assert d["total"] == 6 and "observation" in d["by_kind"]


def test_empty_snapshot(wm_mod):
    snap = wm_mod.WorkingMemory().snapshot(now=0.0)
    assert snap.is_empty and snap.summary() == "working memory empty"
    assert snap.kinds() == () and snap.salient == ()


def test_summary_is_deterministic(wm, wm_mod):
    wm.remember(wm_mod.KIND_PERSON, "sam", salience=0.8, now=0.0)
    wm.remember(wm_mod.KIND_DEVICE, "lamp", salience=0.9, now=0.0)
    s1 = wm.snapshot(now=0.0).summary()
    s2 = wm.snapshot(now=0.0).summary()
    assert s1 == s2
    assert "held" in s1 and "person×1" in s1 and "device×1" in s1


def test_touch_refreshes_recency(wm, wm_mod):
    wm.remember(wm_mod.KIND_OBSERVATION, "x", salience=1.0, now=0.0)
    refreshed = wm.touch(wm_mod.KIND_OBSERVATION, "x", now=100.0)
    assert refreshed is not None and refreshed.ts == 100.0
    # touching an absent item returns None.
    assert wm.touch(wm_mod.KIND_OBSERVATION, "absent", now=100.0) is None


def test_forget_and_clear(wm, wm_mod):
    wm.remember(wm_mod.KIND_ACTION, "lock_door", salience=0.5, now=0.0)
    assert wm.forget(wm_mod.KIND_ACTION, "lock_door") is True
    assert wm.forget(wm_mod.KIND_ACTION, "lock_door") is False
    wm.remember(wm_mod.KIND_ACTION, "a", now=0.0)
    wm.remember(wm_mod.KIND_ACTION, "b", now=0.0)
    wm.clear()
    assert len(wm) == 0
