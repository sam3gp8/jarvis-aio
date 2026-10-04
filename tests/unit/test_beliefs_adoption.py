"""MCU Phase E (E1): WorldModel.beliefs() seeds the kernel belief model from
knowledge-store confidences, prefixed by the minimal identity self-assertion.

Shadow adoption: this is a read-only view (surfaced by the cognitive_status
tool); no decision consumes it and the knowledge store stays authoritative.
"""
import pytest


# ── the identity self-assertion (north-star seed, minimal) ──────────────────────

def test_identity_assertion_is_a_high_confidence_belief(load):
    B = load("kernel.beliefs")
    ia = B.identity_assertion()
    assert "JARVIS" in ia.proposition
    assert ia.probability > 0.95 and ia.supported
    # Customisable, still minimal.
    ia2 = B.identity_assertion(name="J", role="the butler")
    assert ia2.proposition == "J is the butler"


# ── WorldModel.beliefs seeds from knowledge confidences ─────────────────────────

@pytest.fixture
def wm(load, monkeypatch):
    mod = load("kernel.world_model")
    facts = [
        {"subject": "sam", "key": "favorite_color", "value": "blue",
         "confidence": 0.9, "source": "stated"},
        {"subject": "home", "key": "has_dog", "value": "true",
         "confidence": 0.6, "source": "inferred"},
    ]
    monkeypatch.setattr(mod, "_all_facts", lambda subject=None: list(facts))
    return mod


def test_beliefs_prefixes_identity_then_maps_facts(wm):
    WorldModel = wm.WorldModel
    bels = WorldModel(hass=None).beliefs()
    # First is always the identity assertion.
    assert "JARVIS" in bels[0].proposition
    props = {b.proposition: b.probability for b in bels[1:]}
    assert props["sam.favorite_color=blue"] == pytest.approx(0.9, abs=1e-3)
    assert props["home.has_dog=true"] == pytest.approx(0.6, abs=1e-3)


def test_beliefs_best_effort_returns_identity_on_failure(load, monkeypatch):
    mod = load("kernel.world_model")

    def _boom(subject=None):
        raise RuntimeError("knowledge down")
    monkeypatch.setattr(mod, "_all_facts", _boom)
    bels = mod.WorldModel(hass=None).beliefs()
    # Never empty: the identity belief always stands.
    assert len(bels) == 1 and "JARVIS" in bels[0].proposition


def test_beliefs_empty_knowledge_is_just_identity(load, monkeypatch):
    mod = load("kernel.world_model")
    monkeypatch.setattr(mod, "_all_facts", lambda subject=None: [])
    bels = mod.WorldModel(hass=None).beliefs()
    assert len(bels) == 1 and "JARVIS" in bels[0].proposition
