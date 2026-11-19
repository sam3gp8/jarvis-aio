"""#581 — the morning briefing must never speak internal [directives], and the
presence summary must honour excluded people / not double-count the same human.

- presence.get_presence_summary() now applies entity_filter.is_excluded() to
  person.* entities (it only filtered binary sensors before), and de-duplicates
  two person.* entities that track an identical device_trackers set.
- briefing._plain_briefing() (the deterministic fallback used when the model
  returns empty) strips square-bracketed annotations so a read-only-sensor
  directive like "[report as open only; …]" is never read aloud.
"""
import pathlib

from fakes import FakeHass

_BRIEF = (pathlib.Path(__file__).resolve().parents[2]
          / "custom_components" / "jarvis" / "briefing.py")


# ── presence: honour exclusions + de-dup the same human ──────────────────────

def _hass_with_people(const, excluded=None, people=()):
    h = FakeHass()
    h.data = {const.DOMAIN: {"e1": {"runtime_config": {
        "excluded_entities": list(excluded or [])}}}}
    for eid, state, name, trackers in people:
        h.states.set(eid, state, friendly_name=name, device_trackers=list(trackers))
    return h


def test_excluded_person_is_not_announced(load):
    presence, const = load("presence"), load("const")
    # the reporter's case: person.my_home aliases Joseph's phone and is excluded
    h = _hass_with_people(const, excluded=["person.my_home"], people=[
        ("person.my_home", "not_home", "My Home Administrator", ["device_tracker.joseph_s_iphone"]),
        ("person.joseph", "not_home", "Joseph", ["device_tracker.joseph_s_iphone"]),
    ])
    names = [p["name"] for p in presence.get_presence_summary(h).get("people", [])]
    assert "My Home Administrator" not in names
    assert names == ["Joseph"]


def test_duplicate_people_sharing_trackers_are_deduped(load):
    presence, const = load("presence"), load("const")
    # even WITHOUT an explicit exclusion, two person.* on the same phone = one human
    h = _hass_with_people(const, people=[
        ("person.my_home", "not_home", "My Home Administrator", ["device_tracker.joseph_s_iphone"]),
        ("person.joseph", "not_home", "Joseph", ["device_tracker.joseph_s_iphone"]),
    ])
    people = presence.get_presence_summary(h).get("people", [])
    assert len(people) == 1


def test_distinct_people_are_both_kept(load):
    presence, const = load("presence"), load("const")
    h = _hass_with_people(const, people=[
        ("person.alice", "home", "Alice", ["device_tracker.alice_phone"]),
        ("person.bob", "not_home", "Bob", ["device_tracker.bob_phone"]),
    ])
    summ = presence.get_presence_summary(h)
    assert len(summ["people"]) == 2
    assert summ["home_count"] == 1 and summ["away_count"] == 1


def test_people_without_trackers_are_not_deduped(load):
    presence, const = load("presence"), load("const")
    # empty device_trackers set must NOT collapse two distinct people together
    h = _hass_with_people(const, people=[
        ("person.alice", "home", "Alice", []),
        ("person.bob", "home", "Bob", []),
    ])
    assert len(presence.get_presence_summary(h).get("people", [])) == 2


# ── briefing fallback: strip internal [directives] before TTS ────────────────

_ANNOTATED = ("Dryer Door is open [read-only sensor: report as open only; "
              "do not offer to close it and do not say you cannot]")


def test_plain_briefing_strips_bracketed_annotations(load):
    briefing = load("briefing")
    out = briefing._plain_briefing("Good morning", "sir", [_ANNOTATED, "2 unread messages"])
    assert "[" not in out and "]" not in out
    assert "read-only sensor" not in out
    assert "Dryer Door is open" in out
    assert "2 unread messages" in out


def test_plain_briefing_drops_annotation_only_line(load):
    briefing = load("briefing")
    out = briefing._plain_briefing("Good evening", "sir", ["[internal directive]", "All quiet"])
    assert "internal" not in out
    assert "All quiet" in out


def test_plain_briefing_empty_is_graceful(load):
    briefing = load("briefing")
    out = briefing._plain_briefing("Good morning", "sir", [])
    assert "Nothing notable" in out


def test_briefing_llm_prompt_forbids_speaking_annotations():
    # the LLM path is instructed never to read bracketed annotations aloud
    src = _BRIEF.read_text()
    assert "internal directives" in src
    assert "never read or repeat them verbatim" in src


def test_empty_model_output_logs_a_redacted_reason():
    # the fallback warning carries a redacted reason (finish_reason / exception),
    # never the briefing content or context
    src = _BRIEF.read_text()
    assert "_empty_reason" in src
    assert "finish_reason=" in src
