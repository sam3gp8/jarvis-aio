"""Phase AC enforce — contextual autonomy atop the N ceiling (owner-gated, opt-in).

When the household opts in (`contextual_autonomy_enforce`), a granted convenience
pattern that would otherwise auto-act is held to ask-first while a restrictive
context is present (a guest in the home). Downward-only: it can only ever
withhold, never grant, and never loosens the N gate. Default OFF is
behaviour-preserving; fail-safe leaves the legacy gate (grant + mode flag)
untouched; no context passed → no effect.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def modes_auto_on(load, monkeypatch):
    modes = load("modes")
    monkeypatch.setattr(modes, "mode_allows_auto_actions", lambda: True)
    return modes


@pytest.fixture
def A(load):
    return load("kernel.autonomy")


def _granted(am, key):
    am._grants[key] = {"granted": True, "approvals": 5}


# ── the switch ────────────────────────────────────────────────────────────────
def test_enforce_off_by_default(cc):
    assert cc._contextual_autonomy_enforce_on() is False


def test_enforce_on_via_config(cc, cfg):
    cfg["contextual_autonomy_enforce"] = True
    assert cc._contextual_autonomy_enforce_on() is True


def test_enforce_on_via_flag(cc, monkeypatch):
    monkeypatch.setattr(cc, "CONTEXTUAL_AUTONOMY_ENFORCE", True)
    assert cc._contextual_autonomy_enforce_on() is True


# ── the live gate ────────────────────────────────────────────────────────────
def test_guest_holds_convenience_only_under_enforce(cc, cfg, modes_auto_on, A):
    am = cc.AutonomyManager()
    _granted(am, "lights_on_when_dark:den")
    guest = A.AutonomyContext(guests_present=True)
    # Default OFF: a guest context is ignored — still autonomous (legacy).
    assert am.is_autonomous("lights_on_when_dark:den", context=guest) is True
    # Opt-in ON: a guest in the home holds the convenience action to ask-first.
    cfg["contextual_autonomy_enforce"] = True
    assert am.is_autonomous("lights_on_when_dark:den", context=guest) is False


def test_no_guest_still_autonomous_under_enforce(cc, cfg, modes_auto_on, A):
    am = cc.AutonomyManager()
    _granted(am, "lights_on_when_dark:den")
    cfg["contextual_autonomy_enforce"] = True
    # Unrestricted context → unchanged (acts as today).
    assert am.is_autonomous(
        "lights_on_when_dark:den", context=A.AutonomyContext()) is True


def test_no_context_is_behaviour_preserving(cc, cfg, modes_auto_on):
    am = cc.AutonomyManager()
    _granted(am, "lights_on_when_dark:den")
    cfg["contextual_autonomy_enforce"] = True
    # No context passed → AC never engages (fail-safe = act as today).
    assert am.is_autonomous("lights_on_when_dark:den") is True


def test_ungranted_never_autonomous(cc, cfg, modes_auto_on, A):
    am = cc.AutonomyManager()
    cfg["contextual_autonomy_enforce"] = True
    assert am.is_autonomous(
        "lights_on_when_dark:den", context=A.AutonomyContext(guests_present=True)
    ) is False  # no grant


def test_context_never_loosens_n_gate(cc, cfg, modes_auto_on, A, monkeypatch):
    # A SECURITY pattern is blocked by the N ceiling; an UNrestrictive context
    # must never promote it back to autonomous.
    monkeypatch.setitem(cc._PATTERN_CAPABILITY, "unlock_front", "lock.unlock")
    am = cc.AutonomyManager()
    _granted(am, "unlock_front:door")
    cfg["autonomy_enforce"] = True
    cfg["contextual_autonomy_enforce"] = True
    assert am.is_autonomous(
        "unlock_front:door", context=A.AutonomyContext()) is False


# ── the guest signal ─────────────────────────────────────────────────────────
def test_guests_present_true_for_non_resident(cc, fake_hass, monkeypatch):
    from jc import recognition, face_roster
    monkeypatch.setattr(recognition, "who_is_where", lambda h: {"camera.door": "Visitor Bob"})
    monkeypatch.setattr(recognition, "_is_unknown", lambda n: False)
    monkeypatch.setattr(face_roster, "is_resident", lambda n: False)
    assert cc._guests_present(fake_hass) is True


def test_guests_present_false_for_resident(cc, fake_hass, monkeypatch):
    from jc import recognition, face_roster
    monkeypatch.setattr(recognition, "who_is_where", lambda h: {"camera.door": "Sam"})
    monkeypatch.setattr(recognition, "_is_unknown", lambda n: False)
    monkeypatch.setattr(face_roster, "is_resident", lambda n: n == "Sam")
    assert cc._guests_present(fake_hass) is False


def test_guests_present_false_when_none_seen(cc, fake_hass, monkeypatch):
    from jc import recognition
    monkeypatch.setattr(recognition, "who_is_where", lambda h: {})
    assert cc._guests_present(fake_hass) is False


def test_guests_present_defensive_on_error(cc, fake_hass, monkeypatch):
    from jc import recognition
    def _boom(h):
        raise RuntimeError("recognition down")
    monkeypatch.setattr(recognition, "who_is_where", _boom)
    assert cc._guests_present(fake_hass) is False
