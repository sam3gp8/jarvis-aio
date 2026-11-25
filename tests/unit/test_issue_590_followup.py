"""Issue #590 follow-up — briefings still hallucinated after the 9.158.0 fix.

On 9.161.0 real briefings still invented **action** claims ("I routed his
playlist", "I adjusted the climate", "climate controls have adjusted") and
reached for false all-clear phrasings the first scrub missed ("the estate
remains secure"). briefing.py performs NO actions, so any action claim is
unconfirmed; the security scrub gains the grander place words. Both are
deterministic guards, since prompt grounding alone did not hold.
"""
import pathlib

import pytest


@pytest.fixture
def briefing(load):
    return load("briefing")


# ── broadened false-security scrub ──────────────────────────────────────────
@pytest.mark.parametrize("claim", [
    "The estate remains secure.",
    "The premises are secure.",
    "All perimeter sensors are secure.",
    "All sensors are secure.",
    "The grounds are secure.",
    "The building is secure.",
])
def test_security_scrub_catches_grander_phrasings_when_open(briefing, claim):
    out = briefing._scrub_false_security_claims(f"Good evening. {claim} Noted.", has_open=True)
    assert claim.rstrip(".").lower() not in out.lower(), claim
    assert "Good evening." in out and "Noted." in out


def test_security_scrub_still_noop_when_nothing_open(briefing):
    # Nothing open → an all-clear statement is legitimate; leave it untouched.
    text = "Good evening. The estate remains secure."
    assert briefing._scrub_false_security_claims(text, has_open=False) == text


# ── action-claim scrub (always on — briefing.py performs no actions) ─────────
@pytest.mark.parametrize("claim", [
    "I have routed his preferred playlist to the living area and illuminated the hallway.",
    "I have taken the liberty of routing his preferred playlist and adjusting the climate accordingly.",
    "Internal climate controls have adjusted to your preferred arrival profile.",
    "I've turned on the hallway lights.",
    "I illuminated the hallway.",
    "I then dimmed the bedroom lights.",
    "The lights have been turned on.",
    "I locked the front door.",
    "I have adjusted the climate to your arrival profile.",
])
def test_action_scrub_strips_claimed_actions(briefing, claim):
    out = briefing._scrub_unconfirmed_action_claims(
        f"Good morning, sir. {claim} The weather is clear.")
    assert claim.rstrip(".").lower() not in out.lower(), claim
    assert "Good morning, sir." in out
    assert "The weather is clear." in out


@pytest.mark.parametrize("keep", [
    "External temperature is holding at a pleasant 24.1 degrees Celsius under clear skies.",
    "Joseph has just arrived home.",
    "You have three reminders today.",
    "The garage door is open.",
    "Your 9am meeting is locked in.",
    "I noted the garage door is open.",
    "The music has been playing softly overnight.",
    "All internal systems are operating within nominal parameters.",
    "Good morning, sir.",
])
def test_action_scrub_keeps_facts_and_states(briefing, keep):
    # A report-only briefing full of facts must survive untouched.
    out = briefing._scrub_unconfirmed_action_claims(f"Good morning. {keep} Done.")
    assert keep in out, keep


def test_action_scrub_never_returns_empty(briefing):
    # If every sentence is a claim, keep the original rather than emit nothing.
    text = "I have routed the playlist and adjusted the climate."
    assert briefing._scrub_unconfirmed_action_claims(text) == text


def test_action_scrub_handles_empty(briefing):
    assert briefing._scrub_unconfirmed_action_claims("") == ""


# ── the prompt names the no-action rule ─────────────────────────────────────
def test_prompt_states_briefing_performs_no_actions():
    src = pathlib.Path("custom_components/jarvis/briefing.py").read_text()
    assert "never performs actions" in src
