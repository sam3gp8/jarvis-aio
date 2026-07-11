"""Tests for voice recognition (v6.34.0) — consuming a speaker-recognition
service's 'who is speaking' signal + auto-labelling for enrollment."""
import types
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def vr(load):
    return load("voice_recognition")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def presence(load, monkeypatch):
    """Controllable presence for identity.resolve (used by enrollment)."""
    p = load("presence")
    state = {"home": []}
    monkeypatch.setattr(
        p, "get_presence_summary",
        lambda hass: {"people": [{"name": n, "state": "home"} for n in state["home"]]})
    return state


# ── identify: per-person voice sensors (VoiceBM style) ───────────────────────

def test_identify_per_person_binary_sensor(vr, cfg, fake_hass):
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    fake_hass.states.set("binary_sensor.sam_voice", "on")
    fake_hass.states.set("binary_sensor.alex_voice", "off")
    assert vr.identify(fake_hass) == {"sam": pytest.approx(0.85)}


def test_identify_binary_sensor_uses_score_attr(vr, cfg, fake_hass):
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    fake_hass.states.set("binary_sensor.sam_voice", "on", confidence=0.93)
    assert vr.identify(fake_hass) == {"sam": pytest.approx(0.93)}


# ── identify: single current-speaker sensor ──────────────────────────────────

def test_identify_current_speaker_sensor(vr, cfg, fake_hass):
    cfg["voice_recognition_source"] = "sensor.current_speaker"
    fake_hass.states.set("sensor.current_speaker", "Sam", score=88)  # percent
    assert vr.identify(fake_hass) == {"Sam": pytest.approx(0.88)}


def test_identify_no_speaker_state_is_empty(vr, cfg, fake_hass):
    cfg["voice_recognition_source"] = "sensor.current_speaker"
    fake_hass.states.set("sensor.current_speaker", "unknown")
    assert vr.identify(fake_hass) == {}


def test_identify_no_source_configured(vr, cfg, fake_hass):
    fake_hass.states.set("sensor.current_speaker", "Sam")
    assert vr.identify(fake_hass) == {}


# ── recency ──────────────────────────────────────────────────────────────────

def test_fresh_rejects_stale_timestamp(vr):
    old = types.SimpleNamespace(
        last_updated=datetime.now(timezone.utc) - timedelta(minutes=5))
    fresh = types.SimpleNamespace(last_updated=datetime.now(timezone.utc))
    assert vr._fresh(old) is False
    assert vr._fresh(fresh) is True
    assert vr._fresh(types.SimpleNamespace()) is True   # no timestamp → assume fresh


# ── provider registration ────────────────────────────────────────────────────

def test_register_wires_into_identity(vr, cfg, load, fake_hass):
    identity = load("identity")
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    cfg["identity_voice_fingerprint"] = True
    fake_hass.states.set("binary_sensor.sam_voice", "on")
    vr.register(fake_hass)
    try:
        assert identity.has_voice_provider() is True
        ident = identity.resolve(fake_hass)
        assert ident.person == "sam" and "voice" in ident.method
    finally:
        identity.register_voice_provider(None)


# ── enrollment (learn over time) ─────────────────────────────────────────────

def test_enrollment_candidate_when_face_knows_but_voice_doesnt(vr, cfg, presence, fake_hass):
    # Sole occupant Sam ⇒ identity is confident via presence, not voice; voice
    # source has no one speaking ⇒ Sam is an enrollment opportunity.
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"   # nothing ON
    presence["home"] = ["Sam"]
    assert vr.enrollment_candidate(fake_hass) == "Sam"


def test_no_enrollment_when_voice_already_knows(vr, cfg, presence, fake_hass):
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    cfg["identity_voice_fingerprint"] = True
    presence["home"] = ["Sam"]
    fake_hass.states.set("binary_sensor.sam_voice", "on")   # voice recognizes Sam
    assert vr.enrollment_candidate(fake_hass) is None


def test_no_enrollment_when_nobody_known(vr, cfg, presence, fake_hass):
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    presence["home"] = ["Sam", "Alex"]   # ambiguous → identity unknown
    assert vr.enrollment_candidate(fake_hass) is None


async def test_maybe_fire_enrollment_fires_once_then_cooldown(vr, cfg, presence, fake_hass):
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    cfg["identity_voice_fingerprint"] = True
    presence["home"] = ["Sam"]
    assert vr.maybe_fire_enrollment(fake_hass) == "Sam"
    assert fake_hass.bus.fired[-1][0] == "jarvis_voice_enroll_candidate"
    assert fake_hass.bus.fired[-1][1]["person"] == "Sam"
    # immediate second call is rate-limited
    assert vr.maybe_fire_enrollment(fake_hass) is None


async def test_maybe_fire_enrollment_gated_by_flag(vr, cfg, presence, fake_hass):
    cfg["voice_recognition_source"] = "binary_sensor.*_voice"
    cfg["identity_voice_fingerprint"] = False   # voice tier off
    presence["home"] = ["Sam"]
    assert vr.maybe_fire_enrollment(fake_hass) is None
    assert fake_hass.bus.fired == []
