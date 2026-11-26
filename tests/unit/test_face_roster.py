"""Tests for the household resident whitelist (#140) — a JARVIS-native
'who is a resident' flag layered on backend face recognition. Matching is
name-normalized (case/space-insensitive) and the list persists to JSON."""
import json

import pytest


@pytest.fixture
def fr(load, tmp_path, monkeypatch):
    mod = load("face_roster")
    monkeypatch.setattr(mod, "ROSTER_PATH", str(tmp_path / "face_roster.json"))
    mod._loaded = False
    mod._roster = {}
    mod._aliases = {}
    return mod


def test_add_and_is_resident_normalizes(fr):
    assert fr.add_resident("Sam") is True
    assert fr.add_resident("Sam") is False          # already present → not new
    assert fr.is_resident("sam") is True            # case-insensitive
    assert fr.is_resident("  SAM  ") is True         # whitespace-insensitive
    assert fr.is_resident("Quentin") is False
    assert fr.residents() == ["Sam"]


def test_remove(fr):
    fr.add_resident("Eliana")
    assert fr.remove_resident("eliana") is True     # normalized match removes it
    assert fr.remove_resident("eliana") is False    # already gone
    assert fr.residents() == []


def test_blank_names_ignored(fr):
    assert fr.add_resident("") is False
    assert fr.add_resident("   ") is False
    assert fr.residents() == []


def test_persists_across_reload(fr):
    fr.add_resident("Sam")
    fr.add_resident("Lee")
    # Simulate a fresh process: drop in-memory state, reload from disk.
    fr._loaded = False
    fr._roster = {}
    assert sorted(fr.residents()) == ["Lee", "Sam"]


def test_on_disk_format_is_a_plain_list(fr):
    fr.add_resident("Sam")
    with open(fr.ROSTER_PATH) as f:
        data = json.load(f)
    assert isinstance(data, list) and "Sam" in data


def test_tolerates_legacy_dict_file(fr):
    # An older {normalized: display} map should still load.
    with open(fr.ROSTER_PATH, "w") as f:
        json.dump({"sam_smith": "Sam Smith"}, f)
    fr._loaded = False
    fr._roster = {}
    assert fr.is_resident("Sam Smith") is True
    assert fr.residents() == ["Sam Smith"]


# ── resident / guest / unknown classification (issue #388) ──────────────────────

def test_relationship_resident_guest_unknown(fr):
    fr.add_resident("Sam")
    # A flagged resident.
    assert fr.relationship("Sam") == "resident"
    assert fr.relationship("  sam ") == "resident"      # normalized match
    # A recognized but unlisted person is a guest.
    assert fr.relationship("Quentin") == "guest"
    # Backend "no identity" markers are strangers, not guests.
    for n in ("Unknown", "unknown person", "unknown_face", "", "   "):
        assert fr.relationship(n) == "unknown", n


def test_is_guest(fr):
    fr.add_resident("Sam")
    assert fr.is_guest("Quentin") is True               # recognized, not a resident
    assert fr.is_guest("sam") is False                  # a resident is not a guest
    assert fr.is_guest("Unknown") is False              # a stranger is not a guest
    assert fr.is_guest("") is False


def test_relationship_flips_when_residency_toggled(fr):
    # The whole point of #388: managing status without touching face recognition.
    assert fr.relationship("Quentin") == "guest"
    fr.add_resident("Quentin")
    assert fr.relationship("Quentin") == "resident"
    fr.remove_resident("Quentin")
    assert fr.relationship("Quentin") == "guest"        # back to a guest, still recognized


def test_unrecognized_variants_are_unknown(fr):
    # Kept in step with recognition._is_unknown (A1 added the unrecognis(z)ed spellings).
    for n in ("unrecognized", "Unrecognised"):
        assert fr.relationship(n) == "unknown", n
        assert fr.is_guest(n) is False


# ── §4: backend aliases → one unified resident identity (#41) ────────────────

def test_object_form_file_loads_aliases(fr):
    # Frigate emits "sam_g", DoubleTake emits "Sam" — both are the one resident.
    with open(fr.ROSTER_PATH, "w") as f:
        json.dump({"residents": [{"name": "Sam", "aliases": ["sam_g", "Sam Smith"]},
                                  "Alex"]}, f)
    fr._loaded = False; fr._roster = {}; fr._aliases = {}
    assert fr.is_resident("sam_g") is True            # alias resolves
    assert fr.is_resident("Sam Smith") is True        # alias (normalized) resolves
    assert fr.is_resident("Sam") is True and fr.is_resident("Alex") is True
    assert fr.is_resident("Quentin") is False
    assert fr.canonical_name("sam_g") == "Sam"        # unified identity
    assert fr.canonical_name("SAM") == "Sam"
    assert fr.relationship("sam_g") == "resident"
    assert sorted(fr.residents()) == ["Alex", "Sam"]  # aliases aren't listed as residents


def test_sidecar_aliases_map_loads(fr):
    with open(fr.ROSTER_PATH, "w") as f:
        json.dump({"residents": ["Sam"], "aliases": {"sam_g": "Sam", "ghost": "Nobody"}}, f)
    fr._loaded = False; fr._roster = {}; fr._aliases = {}
    assert fr.is_resident("sam_g") is True
    # A sidecar alias whose canonical isn't a resident is dropped (no phantom).
    assert fr.is_resident("ghost") is False
    assert fr.canonical_name("ghost") == "ghost"


def test_add_alias_and_persist_roundtrip(fr):
    fr.add_resident("Sam")
    assert fr.add_alias("sam_g", "Sam") is True
    assert fr.add_alias("sam_g", "Sam") is False        # already mapped → not new
    # Persisted as the rich object form, and reloads with the alias intact.
    with open(fr.ROSTER_PATH) as f:
        disk = json.load(f)
    assert isinstance(disk, dict) and disk["residents"] == ["Sam"]
    assert disk["aliases"].get("sam_g") == "Sam"
    fr._loaded = False; fr._roster = {}; fr._aliases = {}
    assert fr.is_resident("sam_g") is True
    assert fr.canonical_name("sam_g") == "Sam"


def test_add_alias_adds_missing_resident(fr):
    # Registering an alias for a not-yet-listed name also flags that resident.
    assert fr.add_alias("ali", "Alex") is True
    assert fr.is_resident("Alex") is True
    assert fr.is_resident("ali") is True


def test_remove_resident_prunes_aliases(fr):
    fr.add_resident("Sam")
    fr.add_alias("sam_g", "Sam")
    assert fr.is_resident("sam_g") is True
    fr.remove_resident("Sam")
    assert fr.is_resident("sam_g") is False             # dangling alias is gone
    assert fr.canonical_name("sam_g") == "sam_g"        # no longer resolves
    # And the file falls back to a plain list once no aliases remain.
    with open(fr.ROSTER_PATH) as f:
        assert isinstance(json.load(f), list)


def test_canonical_name_passthrough_for_unknown(fr):
    fr.add_resident("Sam")
    assert fr.canonical_name("Quentin") == "Quentin"    # a guest keeps their name
    assert fr.canonical_name("  Quentin  ") == "Quentin"
    assert fr.canonical_name("") == ""


def test_list_of_objects_without_aliases_is_plain(fr):
    with open(fr.ROSTER_PATH, "w") as f:
        json.dump([{"name": "Sam"}, "Alex"], f)
    fr._loaded = False; fr._roster = {}; fr._aliases = {}
    assert sorted(fr.residents()) == ["Alex", "Sam"]
    assert fr.is_resident("Sam") and fr.is_resident("alex")


def test_unflagged_ha_persons(fr, fake_hass):
    fr.add_resident("Sam")
    fr.add_alias("alex_g", "Alex")                       # Alex is a resident via alias path
    fake_hass.states.set("person.sam", "home", friendly_name="Sam")
    fake_hass.states.set("person.alex", "home", friendly_name="alex_g")   # matches alias
    fake_hass.states.set("person.quentin", "away", friendly_name="Quentin")
    fake_hass.states.set("person.max", "home", friendly_name="Max")
    out = fr.unflagged_ha_persons(fake_hass)
    assert out == ["Max", "Quentin"]                    # Sam + alias-matched Alex excluded


def test_aliases_getter(fr):
    fr.add_resident("Sam")
    fr.add_alias("sam_g", "Sam")
    fr.add_alias("sam_smith", "Sam")
    amap = fr.aliases()
    assert amap == {"sam_g": "Sam", "sam_smith": "Sam"}
    # Removing the resident drops its aliases from the getter too.
    fr.remove_resident("Sam")
    assert fr.aliases() == {}
