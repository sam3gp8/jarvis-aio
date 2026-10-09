"""Conflict shadow on the presence read (#237, Epistemic Fabric).

``presence.get_presence_summary()`` adjudicates each person's own backing
``device_tracker`` entities through ``kernel.conflict``: one ``Provenance`` per
tracker (value = home/away/zone, reliability by ``source_type``), and logs the
resolved winner, whether it is CONTESTED (trackers disagree within the margin),
and whether it AGREES with HA's own ``person.state``. Observe-only: the summary
dict is unchanged whether the shadow runs or not, and the log-only path never
raises.
"""
import logging

from fakes import FakeHass


def _person(h, eid, state, trackers):
    h.states.set(eid, state, device_trackers=list(trackers),
                 friendly_name=eid.split(".", 1)[-1])


def _tracker(h, eid, state, source_type="gps"):
    h.states.set(eid, state, source_type=source_type)


def test_summary_identical_with_conflict_shadow_on_or_off(load):
    presence = load("presence")
    h = FakeHass()
    _person(h, "person.sam", "home",
            ["device_tracker.sam_phone", "device_tracker.sam_watch"])
    _tracker(h, "device_tracker.sam_phone", "home")
    _tracker(h, "device_tracker.sam_watch", "not_home", source_type="bluetooth_le")

    presence.CONFLICT_SHADOW = True
    with_shadow = presence.get_presence_summary(h)
    presence.CONFLICT_SHADOW = False
    try:
        without_shadow = presence.get_presence_summary(h)
    finally:
        presence.CONFLICT_SHADOW = True
    assert with_shadow == without_shadow
    assert with_shadow["home_count"] == 1 and with_shadow["anyone_home"] is True


def test_agreeing_trackers_resolve_and_agree(load, caplog):
    presence = load("presence")
    h = FakeHass()
    _person(h, "person.sam", "home",
            ["device_tracker.sam_phone", "device_tracker.sam_watch"])
    _tracker(h, "device_tracker.sam_phone", "home")
    _tracker(h, "device_tracker.sam_watch", "home", source_type="router")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    msgs = [r.message for r in caplog.records
            if "presence_conflict(shadow)" in r.message]
    assert msgs, "expected a presence_conflict(shadow) log line"
    assert "person.sam" in msgs[-1]
    assert "AGREEMENT" in msgs[-1] and "resolved=True" in msgs[-1]


def test_disagreeing_equal_trackers_are_contested(load, caplog):
    presence = load("presence")
    h = FakeHass()
    # Two GPS trackers of equal reliability pointing opposite ways → within the
    # contest margin → CONTESTED, so the conflict defers rather than picking.
    _person(h, "person.alex", "home",
            ["device_tracker.alex_phone", "device_tracker.alex_car"])
    _tracker(h, "device_tracker.alex_phone", "home", source_type="gps")
    _tracker(h, "device_tracker.alex_car", "not_home", source_type="gps")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    msgs = [r.message for r in caplog.records
            if "presence_conflict(shadow)" in r.message and "person.alex" in r.message]
    assert msgs and "CONTESTED" in msgs[-1]


def test_single_tracker_person_emits_no_conflict(load, caplog):
    presence = load("presence")
    h = FakeHass()
    _person(h, "person.sam", "home", ["device_tracker.sam_phone"])
    _tracker(h, "device_tracker.sam_phone", "home")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    assert not [r for r in caplog.records
                if "presence_conflict(shadow)" in r.message]


def test_conflict_shadow_helper_is_defensive(load):
    presence = load("presence")
    h = FakeHass()
    # Person with trackers that don't exist / have odd states must not raise.
    _person(h, "person.ghost", "unknown",
            ["device_tracker.missing", "device_tracker.bad"])
    h.states.set("device_tracker.bad", "unavailable")
    presence._emit_presence_conflict_shadow(h)   # no exception
