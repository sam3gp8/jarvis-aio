"""Tests for the per-person identity resolver (v6.29.0)."""
import pytest


@pytest.fixture
def identity(load):
    return load("identity")


@pytest.fixture
def cfg(load, monkeypatch):
    """Controllable jarvis_config.get (defaults to passthrough)."""
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def sigs(load, monkeypatch):
    """Controllable presence + face-recognition signals."""
    presence = load("presence")
    recognition = load("recognition")
    state = {"home": [], "seen": {}, "last": {}}
    monkeypatch.setattr(
        presence, "get_presence_summary",
        lambda hass: {"people": [{"name": n, "state": "home"} for n in state["home"]]})
    monkeypatch.setattr(recognition, "who_is_where", lambda hass: dict(state["seen"]))
    monkeypatch.setattr(recognition, "last_seen_at",
                        lambda hass, cam: state["last"].get(cam))
    return state


def _face(state, camera, name, confidence=0.9, age_seconds=5):
    state["seen"][camera] = name
    state["last"][camera] = {"name": name, "confidence": confidence,
                             "age_seconds": age_seconds}


# ── tier 1: presence ─────────────────────────────────────────────────────────

def test_sole_occupant_resolves(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam"]
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam" and ident.known
    assert "sole_occupant" in ident.method


def test_nobody_home_no_signal_is_unknown(identity, cfg, sigs, fake_hass):
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown" and ident.method == "no_signal"


def test_two_home_alone_is_ambiguous_unknown(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam", "Alex"]   # a tie on weak priors → not confident
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown"


# ── tier 2: face ─────────────────────────────────────────────────────────────

def test_face_disambiguates_multi_home(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam", "Alex"]
    _face(sigs, "camera.office", "Alex", confidence=0.95, age_seconds=3)
    ident = identity.resolve(fake_hass)
    assert ident.person == "Alex" and ident.known
    assert "face" in ident.method


def test_stale_face_carries_no_weight(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam", "Alex"]
    _face(sigs, "camera.office", "Alex", confidence=0.95, age_seconds=10_000)
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown"   # face too old → back to ambiguous


def test_presence_and_face_agree_high_confidence(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam"]
    _face(sigs, "camera.kitchen", "Sam", confidence=0.95, age_seconds=2)
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam"
    assert ident.confidence > 0.8


# ── tier 3: voice fingerprint (GPU, optional) ────────────────────────────────

def test_voice_provider_ignored_when_flag_off(identity, cfg, sigs, fake_hass):
    cfg["identity_voice_fingerprint"] = False
    identity.register_voice_provider(lambda hass, dev: {"Alex": 1.0})
    sigs["home"] = ["Sam", "Alex"]
    ident = identity.resolve(fake_hass)
    identity.register_voice_provider(None)
    assert ident.person == "unknown"   # flag off → voice not consulted


def test_voice_provider_used_when_enabled(identity, cfg, sigs, fake_hass):
    cfg["identity_voice_fingerprint"] = True
    identity.register_voice_provider(lambda hass, dev: {"Alex": 1.0})
    sigs["home"] = ["Sam", "Alex"]
    ident = identity.resolve(fake_hass)
    identity.register_voice_provider(None)
    assert ident.person == "Alex" and "voice" in ident.method


def test_no_voice_provider_is_safe(identity, cfg, sigs, fake_hass):
    cfg["identity_voice_fingerprint"] = True   # on, but nothing registered
    assert identity.has_voice_provider() is False
    sigs["home"] = ["Sam"]
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam"               # falls through to presence


# ── master switch + confidence gating ────────────────────────────────────────

def test_disabled_returns_unknown(identity, cfg, sigs, fake_hass):
    cfg["identity_enabled"] = False
    sigs["home"] = ["Sam"]
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown" and ident.method == "disabled"


def test_min_confidence_gate(identity, cfg, sigs, fake_hass):
    cfg["identity_min_confidence"] = 0.95      # very strict
    sigs["home"] = ["Sam"]                       # sole occupant ≈0.6 confidence
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown" and ident.method == "low_confidence"


# ── subject mapping ──────────────────────────────────────────────────────────

def test_subject_for_known_and_unknown(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam Smith"]
    ident = identity.resolve(fake_hass)
    assert identity.subject_for(ident) == "sam_smith"
    assert identity.subject_for(identity.Identification()) == "primary"


# ── quick_person: cheap sole-occupant lookup (v6.41.0) ───────────────────────

def test_quick_person_sole_occupant(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam"]
    assert identity.quick_person(fake_hass) == "Sam"


def test_quick_person_multiple_home_is_unknown(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam", "Alex"]
    assert identity.quick_person(fake_hass) == "unknown"


def test_quick_person_nobody_home_is_unknown(identity, cfg, sigs, fake_hass):
    assert identity.quick_person(fake_hass) == "unknown"


def test_quick_person_ignores_face_and_voice(identity, cfg, sigs, fake_hass):
    # quick_person is presence-only by design — a face vote for a second
    # person must not disambiguate the way the full resolve() would.
    sigs["home"] = ["Sam", "Alex"]
    _face(sigs, "camera.office", "Alex", confidence=0.95, age_seconds=3)
    assert identity.quick_person(fake_hass) == "unknown"


def test_quick_person_disabled_is_unknown(identity, cfg, sigs, fake_hass):
    cfg["identity_enabled"] = False
    sigs["home"] = ["Sam"]
    assert identity.quick_person(fake_hass) == "unknown"


# ── room-scoped + proximity attribution (v6.77.0) ────────────────────────────
# The point: in a multi-occupant house, sole-occupancy of the HOUSE is rare but
# sole-occupancy of a ROOM is common. These make per-person routines possible.

class _Rec:
    """Stub recognition module: who_is_where + last_seen_at."""
    def __init__(self, seen, meta=None):
        self._seen = seen
        self._meta = meta or {}
    def who_is_where(self, hass): return self._seen
    def last_seen_at(self, hass, cam):
        return self._meta.get(cam, {"confidence": 0.9, "age_seconds": 10.0})


def _stub_room(identity, monkeypatch, seen, cam_areas, meta=None):
    import sys, types
    rec = _Rec(seen, meta)
    rmod = types.SimpleNamespace(who_is_where=rec.who_is_where,
                                 last_seen_at=rec.last_seen_at)
    amod = types.SimpleNamespace(entity_area=lambda h, e: cam_areas.get(e))
    monkeypatch.setitem(sys.modules, "jc.recognition", rmod)
    monkeypatch.setitem(sys.modules, "jc.audio_routing", amod)
    monkeypatch.setattr(sys.modules["jc"], "recognition", rmod, raising=False)
    monkeypatch.setattr(sys.modules["jc"], "audio_routing", amod, raising=False)


def test_room_votes_sole_person_in_room(identity, monkeypatch):
    _stub_room(identity, monkeypatch, {"camera.elianas_room": "Eliana"},
               {"camera.elianas_room": "elianas_room"})
    votes = identity._room_votes(None, "elianas_room", __import__("time").time())
    assert "Eliana" in votes
    assert votes["Eliana"] >= identity._W_ROOM_SOLE * 0.25


def test_room_votes_ignore_other_rooms(identity, monkeypatch):
    _stub_room(identity, monkeypatch, {"camera.kitchen": "Sam"},
               {"camera.kitchen": "kitchen"})
    # event happened in the bedroom; the kitchen sighting must not count
    assert identity._room_votes(None, "bedroom", __import__("time").time()) == {}


def test_room_votes_expire(identity, monkeypatch):
    _stub_room(identity, monkeypatch, {"camera.kitchen": "Sam"},
               {"camera.kitchen": "kitchen"},
               meta={"camera.kitchen": {"confidence": 0.9,
                                        "age_seconds": identity._ROOM_FRESH_SECS + 60}})
    assert identity._room_votes(None, "kitchen", __import__("time").time()) == {}


def test_room_votes_multiple_people_weigh_less(identity, monkeypatch):
    _stub_room(identity, monkeypatch,
               {"camera.a": "Sam", "camera.b": "Eliana"},
               {"camera.a": "living", "camera.b": "living"})
    votes = identity._room_votes(None, "living", __import__("time").time())
    assert set(votes) == {"Sam", "Eliana"}
    assert max(votes.values()) < identity._W_ROOM_SOLE


def test_room_votes_no_area_is_empty(identity):
    assert identity._room_votes(None, None, 0.0) == {}


def test_quick_identify_carries_confidence(identity, monkeypatch):
    monkeypatch.setattr(identity, "_home_people", lambda h: ["Sam"])
    out = identity.quick_identify(None)
    assert out.person == "Sam"
    assert out.confidence > 0
    assert "Sam" in out.candidates


def test_quick_identify_ambiguous_returns_candidates(identity, monkeypatch):
    # several people home, no room signal → unknown, but candidates preserved
    monkeypatch.setattr(identity, "_home_people", lambda h: ["Sam", "Eliana"])
    out = identity.quick_identify(None)
    assert out.person == identity.UNKNOWN
    assert set(out.candidates) == {"Sam", "Eliana"}


def test_quick_person_still_returns_a_string(identity, monkeypatch):
    # back-compat: external callers of quick_person keep working
    monkeypatch.setattr(identity, "_home_people", lambda h: ["Sam"])
    assert identity.quick_person(None) == "Sam"
