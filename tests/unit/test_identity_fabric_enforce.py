"""Phase I½ enforce — identity ≠ presence on a live path (#237).

When the owner-gated flip is on, a confident resolve() verdict resting ONLY on
presence-class signals (sole-occupant / home-prior / room / proximity) is
downgraded to UNKNOWN — presence locates a body, only a face/voiceprint says WHO.
Default OFF is behaviour-preserving; a face verdict is never downgraded; fail-safe
leaves the legacy verdict untouched on error.
"""
import pytest


@pytest.fixture
def identity(load):
    return load("identity")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def sigs(load, monkeypatch):
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


def _face(state, camera, name, confidence=0.95, age_seconds=3):
    state["seen"][camera] = name
    state["last"][camera] = {"name": name, "confidence": confidence, "age_seconds": age_seconds}


# ── the helper's identity ≠ presence rule ────────────────────────────────────
def test_helper_presence_only_does_not_establish(identity):
    assert identity._fabric_establishes_identity({"sole_occupant"}) is False
    assert identity._fabric_establishes_identity({"home_prior", "room", "proximity"}) is False


def test_helper_face_or_voice_establishes(identity):
    assert identity._fabric_establishes_identity({"sole_occupant", "face"}) is True
    assert identity._fabric_establishes_identity({"voice"}) is True


def test_helper_fail_safe_on_bad_input(identity):
    # Un-iterable → return True (do not downgrade something we couldn't classify).
    assert identity._fabric_establishes_identity(12345) is True


def test_enforce_off_by_default(identity):
    assert identity._identity_fabric_enforce_on() is False


# ── the live resolve() path ──────────────────────────────────────────────────
def test_default_off_presence_still_known(identity, cfg, sigs, fake_hass):
    sigs["home"] = ["Sam"]
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam" and ident.known   # unchanged behaviour


def test_enforce_downgrades_presence_only(identity, cfg, sigs, fake_hass):
    cfg["identity_fabric_enforce"] = True
    sigs["home"] = ["Sam"]                          # sole-occupant presence only
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown"
    assert ident.method == "presence_not_identity"


def test_enforce_keeps_face_verdict(identity, cfg, sigs, fake_hass):
    cfg["identity_fabric_enforce"] = True
    sigs["home"] = ["Sam"]
    _face(sigs, "camera.kitchen", "Sam")            # face establishes identity
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam" and ident.known
    assert "face" in ident.method
