"""Phase AA (pure) — omnipresent multimodal presence continuity.

`kernel/surfaces.py` arbitrates one utterance onto exactly one surface (no
double-announce), honors mutes everywhere, and lets an Interaction survive a
handoff between surfaces while keeping its identity.
"""
import pytest


@pytest.fixture
def S(load):
    return load("kernel.surfaces")


def _s(S, sid, kind="text", present=True, muted=False, focused=False, priority=None):
    return S.Surface(surface_id=sid, kind=kind, present=present, muted=muted,
                     focused=focused, priority=priority)


def test_arbitrate_focused_wins(S):
    surfaces = [_s(S, "hud", "hud"), _s(S, "voice", "voice", focused=True)]
    # HUD out-ranks voice by default, but the focused surface wins attention.
    assert S.arbitrate(surfaces).surface_id == "voice"


def test_arbitrate_priority_when_none_focused(S):
    surfaces = [_s(S, "voice", "voice"), _s(S, "hud", "hud"), _s(S, "mobile", "mobile")]
    assert S.arbitrate(surfaces).surface_id == "hud"   # lowest default rank


def test_arbitrate_override_priority(S):
    surfaces = [_s(S, "hud", "hud"), _s(S, "sat", "satellite", priority=-1)]
    assert S.arbitrate(surfaces).surface_id == "sat"   # override beats kind rank


def test_muted_and_absent_never_win(S):
    surfaces = [_s(S, "hud", "hud", muted=True), _s(S, "panel", "panel", present=False),
                _s(S, "voice", "voice")]
    assert S.arbitrate(surfaces).surface_id == "voice"


def test_arbitrate_none_when_all_muted_or_absent(S):
    surfaces = [_s(S, "a", muted=True), _s(S, "b", present=False)]
    assert S.arbitrate(surfaces) is None
    assert S.arbitrate([]) is None


def test_single_emit_no_double_announce(S):
    surfaces = [_s(S, "hud", "hud"), _s(S, "voice", "voice"), _s(S, "mobile", "mobile")]
    assert S.would_double_announce(surfaces) is True   # 3 would naively speak
    winner = S.arbitrate(surfaces)
    assert winner is not None                           # exactly one is chosen
    # Only one surface is selected — the others are suppressed.
    assert sum(1 for s in surfaces if s.surface_id == winner.surface_id) == 1


def test_muted_surfaces_listed(S):
    surfaces = [_s(S, "a", muted=True), _s(S, "b"), _s(S, "c", muted=True)]
    assert set(S.muted_surfaces(surfaces)) == {"a", "c"}


# ── interaction continuity across a handoff ─────────────────────────────────────
def test_handoff_preserves_interaction_identity(S):
    i0 = S.start_interaction("int-1", "voice")
    i1 = S.handoff(i0, "mobile")
    i2 = S.handoff(i1, "hud")
    assert i2.interaction_id == "int-1"                 # same interaction throughout
    assert i2.surface_id == "hud" and i2.previous_surface_id == "mobile"
    assert i2.handoffs == 2
    assert S.is_same_interaction(i0, i2) is True


def test_handoff_to_same_surface_is_noop(S):
    i0 = S.start_interaction("int-1", "voice")
    i1 = S.handoff(i0, "voice")
    assert i1.handoffs == 0 and i1.surface_id == "voice"


def test_different_interactions_are_not_same(S):
    a = S.start_interaction("int-1", "voice")
    b = S.start_interaction("int-2", "voice")
    assert S.is_same_interaction(a, b) is False
    assert S.is_same_interaction(a, None) is False


def test_summarize(S):
    surfaces = [_s(S, "hud", "hud"), _s(S, "voice", "voice", muted=True),
                _s(S, "panel", "panel", present=False)]
    out = S.summarize(surfaces)
    assert out["surfaces"] == 3 and out["muted"] == 1
    assert out["emit_on"] == "hud" and out["would_double_announce"] is False
