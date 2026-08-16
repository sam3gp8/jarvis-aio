"""Tests for the deterministic briefing fallback (briefing._plain_briefing, v6.97.0)."""
import pytest


@pytest.fixture
def b(load):
    return load("briefing")


def test_reads_the_gathered_facts(b):
    out = b._plain_briefing("Good morning", "sir",
                            ["It is Monday.", "Weather: 45F.", "At home: Sam."])
    assert out.startswith("Good morning, sir.")
    assert "Weather: 45F." in out and "At home: Sam." in out


def test_empty_context_says_nothing_notable(b):
    assert "Nothing notable" in b._plain_briefing("Good evening", "sir", [])


def test_skips_blank_lines(b):
    out = b._plain_briefing("Good afternoon", "sir", ["", "  ", "Calendar: Dentist at 09:00."])
    assert out == "Good afternoon, sir. Calendar: Dentist at 09:00."
