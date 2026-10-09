"""Phase M enforce — the learning loop is closed: per-capability trust is
persisted and fed back as the prior (owner-gated, default OFF).

Default OFF keeps the neutral 0.5 prior and never writes the store; on, a
verified-outcome window nudges the capability's trust, which is persisted to
<config>/jarvis/capability_trust.json and reloads across a restart. Fail-safe:
a hass without a usable config path never raises and never corrupts the store.
"""
import types

import pytest


def _reset(m):
    m._LEARN_STORE.clear()
    m._LEARN_STORE_LOADED = False
    m._LEARN_STORE_PATH = None
    m._LEARN_LAST_SAVE = 0.0
    m._recent_outcomes.clear()
    m.LEARNING_ENFORCE = False


@pytest.fixture
def actuation(load):
    m = load("actuation")
    # Module-level store is shared via the cached module — reset to a clean slate
    # for each test AND on teardown, so this file never pollutes others.
    _reset(m)
    yield m
    _reset(m)


@pytest.fixture
def O(load):
    return load("kernel.outcome")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


def _hass(tmp_path):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p))))


def _load_window(actuation, O, cap, signals):
    for s in signals:
        actuation._recent_outcomes.append(
            O.record_outcome(capability=cap, intended_result="x",
                             observed_result="x", success=s >= 0,
                             confidence=1.0, learning_signal=s))


# ── gate + accessor ──────────────────────────────────────────────────────────
def test_enforce_off_by_default(actuation):
    assert actuation._learning_enforce_on() is False


def test_enforce_on_via_config(actuation, cfg):
    cfg["learning_enforce"] = True
    assert actuation._learning_enforce_on() is True


def test_learned_trust_default_is_neutral(actuation):
    assert actuation.learned_trust("nope") == actuation._CAP_TRUST_PRIOR


# ── default OFF: nothing persists ────────────────────────────────────────────
def test_off_persists_nothing(actuation, O, tmp_path):
    hass = _hass(tmp_path)
    _load_window(actuation, O, "light", [1.0, 1.0, 1.0])
    oc = actuation._recent_outcomes[-1]
    actuation._emit_learning_enforce(oc, hass)
    assert actuation._LEARN_STORE == {}
    assert not tmp_path.joinpath("jarvis", "capability_trust.json").exists()


# ── enforce ON: closed loop persists + reloads ───────────────────────────────
def test_on_persists_and_reloads(actuation, O, cfg, tmp_path):
    cfg["learning_enforce"] = True
    hass = _hass(tmp_path)
    _load_window(actuation, O, "light", [1.0, 1.0, 1.0])     # positive track record
    oc = actuation._recent_outcomes[-1]
    actuation._emit_learning_enforce(oc, hass)

    trust = actuation.learned_trust("light")
    assert trust > actuation._CAP_TRUST_PRIOR                # nudged up from 0.5
    assert tmp_path.joinpath("jarvis", "capability_trust.json").exists()

    # A fresh process (store cleared, not loaded) reloads the persisted value.
    actuation._LEARN_STORE.clear()
    actuation._LEARN_STORE_LOADED = False
    actuation._LEARN_STORE_PATH = None
    actuation._learn_store_load(hass)
    assert actuation.learned_trust("light") == pytest.approx(trust)


def test_on_feeds_prior_back(actuation, O, cfg, tmp_path):
    cfg["learning_enforce"] = True
    hass = _hass(tmp_path)
    actuation._LEARN_STORE["lock"] = 0.80                    # an already-earned prior
    _load_window(actuation, O, "lock", [1.0, 1.0])
    oc = actuation._recent_outcomes[-1]
    actuation._emit_learning_enforce(oc, hass)
    # Prior was the stored 0.80, not the neutral 0.5 → stays high (closed loop).
    assert actuation.learned_trust("lock") >= 0.80


# ── fail-safe ────────────────────────────────────────────────────────────────
def test_fail_safe_bad_hass(actuation, O, cfg):
    cfg["learning_enforce"] = True
    bad = types.SimpleNamespace(config=types.SimpleNamespace())   # no .path
    _load_window(actuation, O, "light", [1.0])
    oc = actuation._recent_outcomes[-1]
    actuation._emit_learning_enforce(oc, bad)                # must not raise
    assert actuation.learned_trust("light") == actuation._CAP_TRUST_PRIOR
