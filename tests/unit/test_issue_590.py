"""#590 — briefings must stay grounded in the gathered context: no invented
actions/people/states, and "the perimeter is secure" is never spoken while
something is actually open.

Three defences, each tested here:
  A. `_gather_open_things` now sees an open perimeter COVER (a garage that exists
     only as a `cover`), so the open-door fact reaches the briefing context.
  B. the briefing task prompt forbids inventing facts / claiming unperformed
     actions (source-level assertion — the LLM output itself is non-deterministic).
  C. `_scrub_false_security_claims` deterministically drops a false all-secure
     claim when something is open.
"""
import pathlib
import types

import pytest


@pytest.fixture
def briefing(load):
    return load("briefing")


def _st(entity_id, state, **attrs):
    return types.SimpleNamespace(entity_id=entity_id, state=state, attributes=attrs)


def _wire(monkeypatch, fake_hass, *, binary=(), covers=(), locks=()):
    def _all(domain=None):
        return {"binary_sensor": list(binary), "cover": list(covers),
                "lock": list(locks)}.get(domain, [])
    monkeypatch.setattr(fake_hass.states, "async_all", _all)


# ── A: open perimeter covers reach the context ──────────────────────────────
def test_open_garage_cover_is_reported(briefing, fake_hass, monkeypatch):
    # The reporter's exact case: an open garage that is only a `cover`, no sensor.
    _wire(monkeypatch, fake_hass, covers=[
        _st("cover.joseph_s_garage_door", "open",
            device_class="garage", friendly_name="Joseph's Garage Door")])
    assert briefing._gather_open_things(fake_hass) == ["Joseph's Garage Door is open"]


def test_open_blind_cover_is_not_a_perimeter_opening(briefing, fake_hass, monkeypatch):
    _wire(monkeypatch, fake_hass, covers=[
        _st("cover.living_blind", "open",
            device_class="blind", friendly_name="Living Room Blind")])
    assert briefing._gather_open_things(fake_hass) == []


def test_cover_matching_a_sensor_is_not_double_reported(briefing, fake_hass, monkeypatch):
    # A garage with BOTH a binary_sensor and a cover of the same name reports once.
    _wire(monkeypatch, fake_hass,
          binary=[_st("binary_sensor.garage", "on",
                      device_class="garage_door", friendly_name="Garage")],
          covers=[_st("cover.garage", "open",
                      device_class="garage", friendly_name="Garage")])
    assert briefing._gather_open_things(fake_hass) == ["Garage is open"]


def test_closed_perimeter_cover_absent(briefing, fake_hass, monkeypatch):
    _wire(monkeypatch, fake_hass, covers=[
        _st("cover.gate", "closed", device_class="gate", friendly_name="Side Gate")])
    assert briefing._gather_open_things(fake_hass) == []


# ── C: the false-security scrub ─────────────────────────────────────────────
def test_scrub_removes_perimeter_secure_when_something_open(briefing):
    text = ("Good evening, sir. The perimeter is secure and dinner is simmering. "
            "Your schedule is clear.")
    out = briefing._scrub_false_security_claims(text, has_open=True)
    assert "perimeter is secure" not in out.lower()
    assert "Good evening, sir." in out
    assert "Your schedule is clear." in out


def test_scrub_is_a_noop_when_nothing_is_open(briefing):
    text = "Good evening. The perimeter is secure."
    # Nothing open → a 'secure' statement is legitimate, leave it untouched.
    assert briefing._scrub_false_security_claims(text, has_open=False) == text


@pytest.mark.parametrize("claim", [
    "The house is secure.",
    "Everything is locked up.",
    "All doors are locked.",
    "All clear, sir.",
    "The home is safe and secure.",
])
def test_scrub_removes_security_claim_variants_when_open(briefing, claim):
    out = briefing._scrub_false_security_claims(f"Good evening. {claim} Noted.", has_open=True)
    assert claim.rstrip(".").lower() not in out.lower(), claim
    assert "Good evening." in out and "Noted." in out


def test_scrub_keeps_truthful_non_security_sentences(briefing):
    # A 'locked in' that is NOT a security claim must survive the scrub.
    text = "Good evening. Your 9am meeting is locked in. Nothing else to report."
    out = briefing._scrub_false_security_claims(text, has_open=True)
    assert "Your 9am meeting is locked in." in out


def test_scrub_never_returns_empty(briefing):
    text = "The perimeter is secure."
    # Removing the only sentence would empty the briefing — keep the original.
    assert briefing._scrub_false_security_claims(text, has_open=True) == text


# ── B: the grounding prompt is present ──────────────────────────────────────
def test_briefing_prompt_forbids_invention_and_ungrounded_security():
    src = pathlib.Path("custom_components/jarvis/briefing.py").read_text()
    assert "State ONLY facts present in the context above." in src
    assert "Never claim you performed " in src
    assert "invent people, names, messages, appointments, or entity states." in src
    assert "anticipation must never become invented fact." in src
    assert "perimeter secure when the context shows no open doors, covers, or" in src
