"""Epistemic-Fabric Uncertainty enforce — identity gates on the BAND (#237).

The uncertainty parity emit measured the [identity_min_confidence, actionable)
divergence region, where the legacy threshold calls a verdict "known" but the
epistemic band still calls it a GUESS. This is the owner-gated enforce rung: when
`uncertainty_enforce` is on, resolve() treats a verdict below the actionable
(BELIEVED ≥ 0.60) band as NOT identified, so an uncertain read falls toward MORE
confirmation.

Invariants: default OFF is behaviour-preserving; downward-only (it only ever
WITHHOLDS an identification, never widens one); an actionable read is never
downgraded; fail-safe leaves the legacy verdict untouched on any error; and it is
independent of the identity-fabric (presence≠identity) gate.
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


def _face(state, camera, name, confidence, age_seconds=0):
    # age 0 → recency 1.0, so the resolved confidence is deterministically
    # 0.8 (W_FACE) * confidence.
    state["seen"][camera] = name
    state["last"][camera] = {"name": name, "confidence": confidence, "age_seconds": age_seconds}


# ── the switch ────────────────────────────────────────────────────────────────

def test_enforce_on_by_default(identity):
    # Owner-promoted to default-ON (9.50.0): the safe direction for a
    # security-adjacent gate is to ask when unsure.
    assert identity._uncertainty_enforce_on() is True


def test_kill_switch_via_config(identity, cfg):
    cfg["uncertainty_enforce"] = False          # explicit opt-out
    assert identity._uncertainty_enforce_on() is False


# ── the band threshold this gate depends on ──────────────────────────────────

def test_actionable_band_threshold(load):
    U = load("kernel.uncertainty")
    assert U.assess("sam", 0.52).is_actionable() is False   # guessed — below BELIEVED
    assert U.assess("sam", 0.60).is_actionable() is True    # at the actionable band
    assert U.assess("sam", 0.76).is_actionable() is True


# ── the live resolve() path ───────────────────────────────────────────────────

def test_believed_read_downgraded_by_default(identity, cfg, sigs, fake_hass):
    # A face at 0.65 → resolved confidence 0.52: ≥ min_confidence (legacy "known")
    # but below the actionable band. With the gate ON by default, it is treated as
    # not-yet-identified and the read falls toward more confirmation.
    _face(sigs, "camera.kitchen", "Sam", confidence=0.65)
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown"
    assert ident.method == "band_not_actionable"
    assert round(ident.confidence, 3) == 0.52


def test_kill_switch_restores_legacy_known(identity, cfg, sigs, fake_hass):
    # With the kill-switch off, the same believed read is "known" again (legacy
    # min-confidence behaviour) — the owner can always opt out.
    cfg["uncertainty_enforce"] = False
    _face(sigs, "camera.kitchen", "Sam", confidence=0.65)   # → 0.52
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam" and ident.known


def test_enforce_downgrades_below_actionable_band(identity, cfg, sigs, fake_hass):
    cfg["uncertainty_enforce"] = True                        # explicit on (= default)
    _face(sigs, "camera.kitchen", "Sam", confidence=0.65)   # → 0.52, guessed band
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown"
    assert ident.method == "band_not_actionable"


def test_enforce_keeps_actionable_read(identity, cfg, sigs, fake_hass):
    cfg["uncertainty_enforce"] = True
    _face(sigs, "camera.kitchen", "Sam", confidence=0.95)   # → 0.76, actionable
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam" and ident.known
    assert "face" in ident.method


def test_enforce_does_not_touch_low_confidence_path(identity, cfg, sigs, fake_hass):
    # Below min_confidence → the legacy low-confidence return fires first; the band
    # gate never runs (method stays the legacy one, not band_not_actionable).
    cfg["uncertainty_enforce"] = True
    _face(sigs, "camera.kitchen", "Sam", confidence=0.4)    # → 0.32, < min_confidence
    ident = identity.resolve(fake_hass)
    assert ident.person == "unknown"
    assert ident.method == "low_confidence"


def test_enforce_failsafe_on_error(identity, cfg, sigs, fake_hass, load, monkeypatch):
    # Any fault in the band computation → fall through to the legacy verdict.
    cfg["uncertainty_enforce"] = True
    U = load("kernel.uncertainty")
    monkeypatch.setattr(U, "assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _face(sigs, "camera.kitchen", "Sam", confidence=0.65)   # would otherwise downgrade
    ident = identity.resolve(fake_hass)
    assert ident.person == "Sam" and ident.known            # legacy verdict preserved
