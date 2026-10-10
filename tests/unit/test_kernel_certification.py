"""Phase AE — the MCU certification suite as a pure kernel record.

kernel.certification enumerates the ten canonical scenario classes and derives a
CertificationReport: coverage (classes exercised / total), closed-loop rate (of
those exercised, how many closed), and is_certified (every class exercised AND
closed). Pure, deterministic, no HA import. These tests pin the derivations and
the defensive builders.
"""
import pytest


@pytest.fixture
def C(load):
    return load("kernel.certification")


def test_classes_are_the_ten_canonical(C):
    assert C.CLASSES == (
        C.CONVERSATIONAL, C.PROACTIVE, C.LONG_HORIZON, C.DELEGATION, C.FAILURE,
        C.SECURITY, C.CONFLICTING_PRIORITIES, C.PROVIDER_FAILURE,
        C.COGNITIVE_ERROR, C.RESTART,
    )
    assert len(C.CLASSES) == 10
    assert len(set(C.CLASSES)) == 10  # no duplicates


def test_empty_report_is_uncertified(C):
    rep = C.report()
    assert rep.coverage == 0.0
    assert rep.closed_loop_rate == 0.0
    assert rep.is_certified is False
    assert rep.exercised_classes == ()
    assert rep.closed_classes == ()
    assert set(rep.missing_classes) == set(C.CLASSES)
    assert rep.summary().startswith("incomplete:")


def test_closed_implies_exercised(C):
    r = C.result(C.FAILURE, closed_loop=True)
    assert r.exercised is True and r.closed_loop is True
    # and the inverse: exercised without closed stays open
    r2 = C.result(C.FAILURE, exercised=True)
    assert r2.exercised is True and r2.closed_loop is False


def test_coverage_counts_exercised_not_closed(C):
    # two exercised, only one closed
    rep = C.report([
        C.result(C.CONVERSATIONAL, closed_loop=True),
        C.result(C.PROACTIVE, exercised=True),      # exercised, open
    ])
    assert rep.exercised_classes == (C.CONVERSATIONAL, C.PROACTIVE)
    assert rep.closed_classes == (C.CONVERSATIONAL,)
    assert abs(rep.coverage - 2 / 10) < 1e-9
    assert abs(rep.closed_loop_rate - 1 / 2) < 1e-9
    assert rep.is_certified is False


def test_fully_certified(C):
    rep = C.from_map({c: True for c in C.CLASSES})
    assert rep.coverage == 1.0
    assert rep.closed_loop_rate == 1.0
    assert rep.is_certified is True
    assert rep.missing_classes == ()
    assert rep.summary().startswith("CERTIFIED:")


def test_one_class_short_is_not_certified(C):
    m = {c: True for c in C.CLASSES}
    m[C.RESTART] = False   # exercised but open
    rep = C.from_map(m)
    assert rep.is_certified is False
    assert C.RESTART in rep.missing_classes
    # it WAS exercised (from_map records every listed class), just not closed
    assert C.RESTART in rep.exercised_classes
    assert C.RESTART not in rep.closed_classes


def test_report_is_canonical_order_regardless_of_input(C):
    rep = C.report([
        C.result(C.RESTART, closed_loop=True),
        C.result(C.CONVERSATIONAL, closed_loop=True),
        C.result(C.DELEGATION, closed_loop=True),
    ])
    got = [r.scenario for r in rep.results]
    assert got == [C.CONVERSATIONAL, C.DELEGATION, C.RESTART]


def test_report_last_wins_per_class(C):
    rep = C.report([
        C.result(C.SECURITY, exercised=True),        # open first
        C.result(C.SECURITY, closed_loop=True),      # then closed
    ])
    assert rep.closed_classes == (C.SECURITY,)
    assert len([r for r in rep.results if r.scenario == C.SECURITY]) == 1


def test_report_drops_non_canonical_classes(C):
    rep = C.report([
        C.result("not_a_real_class", closed_loop=True),
        C.result(C.PROACTIVE, closed_loop=True),
    ])
    assert rep.exercised_classes == (C.PROACTIVE,)
    assert all(r.scenario in C.CLASSES for r in rep.results)


def test_report_accepts_tuple_rows(C):
    rep = C.report([
        (C.CONVERSATIONAL, True, True, "chat closed"),
        (C.PROACTIVE, True, False, "offered, no response yet"),
    ])
    assert rep.closed_classes == (C.CONVERSATIONAL,)
    assert rep.exercised_classes == (C.CONVERSATIONAL, C.PROACTIVE)


def test_result_normalises_name(C):
    r = C.result("  Conversational  ")
    assert r.scenario == C.CONVERSATIONAL


def test_from_map_only_lists_given_classes(C):
    rep = C.from_map({C.CONVERSATIONAL: True})
    assert rep.exercised_classes == (C.CONVERSATIONAL,)
    # every other class is missing (absent → not exercised)
    assert len(rep.missing_classes) == 9


def test_to_dict_is_json_shaped(C):
    rep = C.from_map({C.CONVERSATIONAL: True, C.PROACTIVE: False})
    d = rep.to_dict()
    assert d["is_certified"] is False
    assert d["coverage"] == round(2 / 10, 4)
    assert d["exercised"] == [C.CONVERSATIONAL, C.PROACTIVE]
    assert d["closed"] == [C.CONVERSATIONAL]
    assert C.PROACTIVE in d["missing"] and C.CONVERSATIONAL not in d["missing"]
    # per-class results carried, in canonical order
    assert [r["scenario"] for r in d["results"]] == [C.CONVERSATIONAL, C.PROACTIVE]


def test_scenario_result_to_dict(C):
    r = C.result(C.FAILURE, closed_loop=True, note="recovered from HA timeout")
    assert r.to_dict() == {
        "scenario": C.FAILURE, "exercised": True, "closed_loop": True,
        "note": "recovered from HA timeout",
    }


def test_report_is_defensive_on_junk(C):
    rep = C.report([None, 123, "nope", C.result(C.SECURITY, closed_loop=True)])
    assert rep.closed_classes == (C.SECURITY,)


def test_summary_names_missing_classes(C):
    rep = C.from_map({c: True for c in C.CLASSES[:8]})  # 8 closed, 2 missing
    s = rep.summary()
    assert "incomplete" in s
    assert C.COGNITIVE_ERROR in s and C.RESTART in s


# ── CertificationLedger (Phase AE shadow) ────────────────────────────────────
def test_ledger_empty(C):
    led = C.CertificationLedger()
    assert led.observations == 0
    assert led.summary() == "no observations recorded"
    assert led.report().is_certified is False


def test_ledger_records_a_proactive_observation(C):
    led = C.CertificationLedger()
    led.observe(C.PROACTIVE, closed_loop=True, note="loop closed")
    assert led.observations == 1
    rep = led.report()
    assert rep.exercised_classes == (C.PROACTIVE,)
    assert rep.closed_classes == (C.PROACTIVE,)
    assert "[1 obs]" in led.summary()


def test_ledger_is_sticky_best(C):
    # once a class closes it STAYS closed, even if a later pass is open
    led = C.CertificationLedger()
    led.observe(C.PROACTIVE, closed_loop=True)
    led.observe(C.PROACTIVE, closed_loop=False)   # a later open pass
    assert led.observations == 2
    assert led.report().closed_classes == (C.PROACTIVE,)


def test_ledger_open_then_closed_upgrades(C):
    led = C.CertificationLedger()
    led.observe(C.PROACTIVE, closed_loop=False)
    assert led.report().closed_classes == ()
    assert led.report().exercised_classes == (C.PROACTIVE,)
    led.observe(C.PROACTIVE, closed_loop=True)    # later pass closes it
    assert led.report().closed_classes == (C.PROACTIVE,)


def test_ledger_tracks_multiple_classes(C):
    led = C.CertificationLedger()
    led.observe(C.PROACTIVE, closed_loop=True)
    led.observe(C.CONVERSATIONAL, closed_loop=True)
    led.observe(C.FAILURE, closed_loop=False)
    rep = led.report()
    assert set(rep.closed_classes) == {C.PROACTIVE, C.CONVERSATIONAL}
    assert C.FAILURE in rep.exercised_classes and C.FAILURE not in rep.closed_classes
    assert rep.is_certified is False  # only 3 of 10 touched


def test_ledger_ignores_non_canonical(C):
    led = C.CertificationLedger()
    led.observe("not_a_class", closed_loop=True)
    assert led.observations == 0
    assert led.report().exercised_classes == ()


def test_ledger_to_dict_carries_observation_count(C):
    led = C.CertificationLedger()
    led.observe(C.PROACTIVE, closed_loop=True)
    led.observe(C.PROACTIVE, closed_loop=True)
    d = led.to_dict()
    assert d["observations"] == 2
    assert d["closed"] == [C.PROACTIVE]


def test_ledger_reset(C):
    led = C.CertificationLedger()
    led.observe(C.PROACTIVE, closed_loop=True)
    led.reset()
    assert led.observations == 0
    assert led.report().exercised_classes == ()


def test_ledger_full_window_certifies(C):
    # a ledger that eventually sees every class close is certified
    led = C.CertificationLedger()
    for c in C.CLASSES:
        led.observe(c, closed_loop=True)
    assert led.report().is_certified is True
    assert led.observations == 10
