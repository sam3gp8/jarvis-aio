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
