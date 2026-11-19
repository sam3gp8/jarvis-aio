"""Tests for the known-guests roster (#549) — a JARVIS-native list of recurring
non-resident visitors with a free-text note of context. Matching is
name-normalized (case/space-insensitive) and the list persists to JSON as
{normalized: {name, note}}."""
import json

import pytest


@pytest.fixture
def gr(load, tmp_path, monkeypatch):
    mod = load("guest_roster")
    monkeypatch.setattr(mod, "ROSTER_PATH", str(tmp_path / "guest_roster.json"))
    mod._loaded = False
    mod._roster = {}
    return mod


def test_set_and_lookup_normalizes(gr):
    assert gr.set_guest("Bob", "best friend, fine to be here alone") is True
    assert gr.set_guest("Bob", "updated note") is False       # existing → not new
    assert gr.is_known_guest("bob") is True                   # case-insensitive
    assert gr.is_known_guest("  BOB  ") is True                # whitespace-insensitive
    assert gr.is_known_guest("Nancy") is False
    assert gr.note_for("BOB") == "updated note"               # latest note wins


def test_note_for_unknown_is_blank(gr):
    assert gr.note_for("Nobody") == ""
    gr.set_guest("Ada")                                        # no note
    assert gr.note_for("ada") == ""


def test_guests_sorted_with_notes(gr):
    gr.set_guest("Zoe", "neighbour")
    gr.set_guest("amy", "dog-sitter")
    assert gr.guests() == [
        {"name": "amy", "note": "dog-sitter"},
        {"name": "Zoe", "note": "neighbour"},
    ]


def test_remove(gr):
    gr.set_guest("Bob", "friend")
    assert gr.remove_guest("bob") is True                      # normalized match removes
    assert gr.remove_guest("bob") is False                     # already gone
    assert gr.guests() == []


def test_blank_names_ignored(gr):
    assert gr.set_guest("", "x") is False
    assert gr.set_guest("   ", "x") is False
    assert gr.guests() == []


def test_persists_across_reload(gr):
    gr.set_guest("Bob", "friend")
    gr.set_guest("Carl", "plumber")
    # Simulate a fresh process: drop in-memory state, reload from disk.
    gr._loaded = False
    gr._roster = {}
    assert gr.guests() == [
        {"name": "Bob", "note": "friend"},
        {"name": "Carl", "note": "plumber"},
    ]


def test_on_disk_format_is_norm_to_entry_map(gr):
    gr.set_guest("Bob Jones", "friend")
    with open(gr.ROSTER_PATH) as f:
        data = json.load(f)
    assert isinstance(data, dict)
    (entry,) = data.values()
    assert entry == {"name": "Bob Jones", "note": "friend"}


def test_tolerates_legacy_name_to_note_file(gr):
    # A hand-written {name: note_string} map should still load.
    with open(gr.ROSTER_PATH, "w") as f:
        json.dump({"Bob": "an old note"}, f)
    gr._loaded = False
    gr._roster = {}
    assert gr.is_known_guest("bob") is True
    assert gr.note_for("Bob") == "an old note"
    assert gr.guests() == [{"name": "Bob", "note": "an old note"}]
