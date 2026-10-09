"""Phase X — physical-world intelligence (kernel/environment.py).

Pure model + objective functions. The load fixture pulls the real module under
the jc namespace; priority is imported by it, so these also exercise the
safety-ladder guard end to end.
"""
import pytest


@pytest.fixture
def E(load):
    return load("kernel.environment")


@pytest.fixture
def PR(load):
    return load("kernel.priority")


# ── ComfortBand ──────────────────────────────────────────────────────────────

def test_comfort_band_classify(E):
    band = E.ComfortBand(low=20.0, high=24.0)
    assert band.classify(18.0) == E.BELOW
    assert band.classify(22.0) == E.COMFORTABLE
    assert band.classify(26.0) == E.ABOVE
    assert band.contains(20.0) and band.contains(24.0) and not band.contains(19.9)


def test_comfort_band_target_defaults_to_midpoint(E):
    assert E.ComfortBand(low=20.0, high=24.0).target == 22.0
    assert E.ComfortBand(low=20.0, high=24.0, ideal=21.0).target == 21.0


def test_comfort_band_score_peaks_at_ideal_and_clamps(E):
    band = E.ComfortBand(low=20.0, high=24.0)   # width 4, target 22
    assert band.score(22.0) == 1.0              # at the ideal
    assert band.score(100.0) == 0.0             # far away → clamped to 0
    assert band.score(0.0) == 0.0
    # one band-width from target (22 ± 4) → 0.0; a band edge (±2) → ~0.5
    assert band.score(26.0) == pytest.approx(0.0, abs=1e-9)
    assert band.score(24.0) == pytest.approx(0.5, abs=1e-9)
    assert 0.0 < band.score(23.0) < 1.0


# ── EnvironmentState queries ─────────────────────────────────────────────────

def test_state_queries(E):
    r1 = E.Reading(E.TEMPERATURE, 21.0, area="living")
    r2 = E.Reading(E.TEMPERATURE, 25.0, area="bed")
    r3 = E.Reading(E.HUMIDITY, 55.0, area="living")
    st = E.EnvironmentState((r1, r2, r3))
    assert not st.is_empty
    assert st.by_kind(E.TEMPERATURE) == (r1, r2)
    assert st.by_area("living") == (r1, r3)
    assert st.latest(E.TEMPERATURE) is r2            # last provided
    assert st.latest(E.TEMPERATURE, area="living") is r1
    assert st.latest(E.AIR_QUALITY) is None
    assert E.EnvironmentState().is_empty


# ── comfort objective ────────────────────────────────────────────────────────

def test_comfort_report_scores_only_kinds_with_bands(E):
    st = E.EnvironmentState((
        E.Reading(E.TEMPERATURE, 22.0, area="living"),   # ideal → 1.0
        E.Reading(E.TEMPERATURE, 28.0, area="bed"),      # above → low score
        E.Reading(E.AIR_QUALITY, 999.0, area="bed"),     # no band → ignored
    ))
    rep = E.comfort(st, {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    assert len(rep.entries) == 2                          # air_quality skipped
    assert rep.worst.reading.area == "bed"
    assert rep.worst.classification == E.ABOVE
    assert 0.0 <= rep.overall <= 1.0
    assert rep.overall == pytest.approx((1.0 + rep.worst.score) / 2)


def test_comfort_report_empty_is_fully_comfortable(E):
    rep = E.comfort(E.EnvironmentState(), {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    assert rep.entries == () and rep.overall == 1.0 and rep.worst is None


# ── efficiency objective ─────────────────────────────────────────────────────

def test_efficiency_over_and_under_peak(E):
    over = E.efficiency(9000.0, peak_w=8000.0)
    assert over.over and over.headroom == -1000.0 and over.utilization > 1.0
    under = E.efficiency(4000.0, peak_w=8000.0)
    assert not under.over and under.headroom == 4000.0
    assert under.utilization == pytest.approx(0.5)


# ── recommender + safety guard (the invariant) ───────────────────────────────

def test_recommend_comfort_and_efficiency_when_no_safety(E):
    comfort_rep = E.comfort(
        E.EnvironmentState((E.Reading(E.TEMPERATURE, 30.0, area="bed"),)),
        {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    eff = E.efficiency(9000.0, peak_w=8000.0)
    recs = E.recommend(comfort_rep, eff)
    objectives = {r.objective for r in recs}
    assert objectives == {E.COMFORT, E.EFFICIENCY}
    assert all(r.actionable for r in recs)            # no safety concern active
    assert all(not r.blocked_by_safety for r in recs)


def test_comfortable_readings_produce_no_recommendation(E):
    comfort_rep = E.comfort(
        E.EnvironmentState((E.Reading(E.TEMPERATURE, 22.0, area="living"),)),
        {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    assert E.recommend(comfort_rep, None) == ()


def test_safety_active_blocks_every_recommendation(E, PR):
    comfort_rep = E.comfort(
        E.EnvironmentState((E.Reading(E.TEMPERATURE, 30.0, area="bed"),)),
        {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    eff = E.efficiency(9000.0, peak_w=8000.0)
    recs = E.recommend(comfort_rep, eff, safety_active=True,
                       safety_tier=PR.LIFE_SAFETY)
    assert recs                                        # still surfaced…
    assert all(r.blocked_by_safety for r in recs)      # …but every one yields
    assert all(not r.actionable for r in recs)


def test_security_concern_also_outranks_comfort_and_efficiency(E, PR):
    # comfort=CONVENIENCE, efficiency=HOUSEHOLD; both rank below SECURITY
    comfort_rep = E.comfort(
        E.EnvironmentState((E.Reading(E.TEMPERATURE, 30.0),)),
        {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    eff = E.efficiency(9000.0, peak_w=8000.0)
    recs = E.recommend(comfort_rep, eff, safety_active=True,
                       safety_tier=PR.SECURITY)
    assert all(r.blocked_by_safety for r in recs)


def test_recommendation_tiers_are_below_safety(E, PR):
    comfort_rep = E.comfort(
        E.EnvironmentState((E.Reading(E.TEMPERATURE, 30.0),)),
        {E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    eff = E.efficiency(9000.0, peak_w=8000.0)
    recs = {r.objective: r for r in E.recommend(comfort_rep, eff)}
    assert recs[E.COMFORT].tier == PR.CONVENIENCE
    assert recs[E.EFFICIENCY].tier == PR.HOUSEHOLD
    # a non-safety (household) concern does NOT block comfort/efficiency
    unguarded = E.recommend(comfort_rep, eff, safety_active=True,
                            safety_tier=PR.HOUSEHOLD)
    # efficiency (HOUSEHOLD) may_override an equal HOUSEHOLD tier → not blocked;
    # comfort (CONVENIENCE) cannot override HOUSEHOLD → blocked
    by_obj = {r.objective: r for r in unguarded}
    assert not by_obj[E.EFFICIENCY].blocked_by_safety
    assert by_obj[E.COMFORT].blocked_by_safety


# ── assess() one-shot ────────────────────────────────────────────────────────

def test_assess_bundles_comfort_efficiency_and_recs(E):
    st = E.EnvironmentState((
        E.Reading(E.TEMPERATURE, 30.0, area="bed"),
        E.Reading(E.HUMIDITY, 50.0, area="bed"),
    ))
    a = E.assess(st,
                 bands={E.TEMPERATURE: E.ComfortBand(20.0, 24.0),
                        E.HUMIDITY: E.ComfortBand(40.0, 60.0)},
                 power_w=9000.0, peak_w=8000.0)
    assert a.efficiency is not None and a.efficiency.over
    assert len(a.comfort.entries) == 2
    assert any(r.objective == E.EFFICIENCY for r in a.recommendations)
    assert all(r.actionable for r in a.actionable)


def test_assess_skips_efficiency_without_power(E):
    st = E.EnvironmentState((E.Reading(E.TEMPERATURE, 22.0),))
    a = E.assess(st, bands={E.TEMPERATURE: E.ComfortBand(20.0, 24.0)})
    assert a.efficiency is None
    assert a.recommendations == ()


def test_assess_safety_active_yields_all(E, PR):
    st = E.EnvironmentState((E.Reading(E.TEMPERATURE, 30.0),))
    a = E.assess(st, bands={E.TEMPERATURE: E.ComfortBand(20.0, 24.0)},
                 power_w=9000.0, peak_w=8000.0, safety_active=True)
    assert a.recommendations and a.actionable == ()    # nothing actionable


def test_to_dict_is_serialisable(E):
    st = E.EnvironmentState((E.Reading(E.TEMPERATURE, 30.0, area="bed"),))
    a = E.assess(st, bands={E.TEMPERATURE: E.ComfortBand(20.0, 24.0)},
                 power_w=9000.0, peak_w=8000.0)
    d = a.to_dict()
    assert d["efficiency"]["over"] is True
    assert isinstance(d["recommendations"], list)
    assert d["recommendations"][0]["actionable"] in (True, False)
