"""Phase 3 consumer: intrusion mirrors its lifecycle into kernel.situation (shadow).

Exercises `intrusion._mirror_situation_sync` directly (the shadow logic) with a
real SituationManager on a tmp SQLite db injected into the module. The async
hooks (`async_record_event` / `async_dismiss_intrusion`) just call this off-loop.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def intr(load):
    return load("intrusion")


@pytest.fixture
def wired(intr, load, tmp_path, monkeypatch):
    """intrusion with a fresh, isolated SituationManager and no open episode."""
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(intr, "_situation_mgr", mgr)
    monkeypatch.setattr(intr, "_situation_id", None)
    monkeypatch.setattr(intr, "_last_parity", None)
    return S, mgr


def test_investigating_opens_and_enters_investigating(intr, wired):
    S, mgr = wired
    intr._mirror_situation_sync(FakeHass(), "investigating",
                                reason="motion while away", breach_area="garage")
    opens = mgr.open_situations("intrusion")
    assert len(opens) == 1
    sit = opens[0]
    assert sit.state == S.INVESTIGATING and sit.location == "garage"
    assert sit.history[0]["to"] == S.POSSIBLE        # opened
    assert sit.history[-1]["to"] == S.INVESTIGATING


def test_confirmed_episode(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    intr._mirror_situation_sync(h, "confirmed", reason="person on camera")
    opens = mgr.open_situations("intrusion")
    assert len(opens) == 1 and opens[0].state == S.CONFIRMED
    assert [h_["to"] for h_ in opens[0].history] == [
        S.POSSIBLE, S.INVESTIGATING, S.CONFIRMED]


def test_unresolved_resolves_episode(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    intr._mirror_situation_sync(h, "unresolved", reason="no response")
    assert mgr.open_situations("intrusion") == []
    assert intr._situation_id is None


def test_dismissed_marks_benign_then_resolved(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    sid = intr._situation_id
    intr._mirror_situation_sync(h, "dismissed", reason="it's just me")
    assert mgr.open_situations("intrusion") == []
    sit = mgr.get(sid)
    states = [h_["to"] for h_ in sit.history]
    assert S.BENIGN in states and sit.state == S.RESOLVED


def test_confirmed_without_prior_investigating(intr, wired):
    S, mgr = wired
    intr._mirror_situation_sync(FakeHass(), "confirmed", breach_area="garage")
    opens = mgr.open_situations("intrusion")
    assert len(opens) == 1 and opens[0].state == S.CONFIRMED


def test_new_episode_after_resolution(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    intr._mirror_situation_sync(h, "unresolved")
    intr._mirror_situation_sync(h, "investigating", breach_area="porch")
    opens = mgr.open_situations("intrusion")
    assert len(opens) == 1 and opens[0].location == "porch"  # fresh episode


def test_repeated_investigating_is_idempotent(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    opens = mgr.open_situations("intrusion")
    assert len(opens) == 1 and opens[0].state == S.INVESTIGATING


def test_mirror_never_raises_on_bad_manager(intr, monkeypatch):
    # A broken manager must not propagate out of the shadow mirror.
    class _Boom:
        def get(self, *_a, **_k):
            raise RuntimeError("db down")
    monkeypatch.setattr(intr, "_situation_mgr", _Boom())
    monkeypatch.setattr(intr, "_situation_id", "sit_x")
    intr._mirror_situation_sync(FakeHass(), "confirmed")   # must not raise


# ── D1: parity — the kernel situation is verified to agree with the verdict ─────

def test_parity_agrees_across_the_lifecycle(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    assert intr._last_parity["action"] == "investigating"
    assert intr._last_parity["actual_state"] == S.INVESTIGATING
    assert intr._last_parity["agree"] is True

    intr._mirror_situation_sync(h, "confirmed", reason="person on camera")
    assert intr._last_parity["actual_state"] == S.CONFIRMED
    assert intr._last_parity["agree"] is True

    intr._mirror_situation_sync(h, "unresolved")
    assert intr._last_parity["actual_state"] == S.RESOLVED
    assert intr._last_parity["agree"] is True


def test_parity_agrees_for_dismissed(intr, wired):
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    intr._mirror_situation_sync(h, "dismissed", reason="just me")
    assert intr._last_parity["action"] == "dismissed"
    assert intr._last_parity["actual_state"] == S.RESOLVED
    assert intr._last_parity["agree"] is True


def test_parity_already_confirmed_investigating_still_agrees(intr, wired):
    # A redundant 'investigating' verdict on an already-CONFIRMED episode is not a
    # divergence — the kernel is further along, which satisfies the monotonic rank.
    S, mgr = wired
    h = FakeHass()
    intr._mirror_situation_sync(h, "investigating", breach_area="garage")
    intr._mirror_situation_sync(h, "confirmed")
    intr._mirror_situation_sync(h, "investigating")   # redundant, late
    assert intr._last_parity["action"] == "investigating"
    assert intr._last_parity["actual_state"] == S.CONFIRMED
    assert intr._last_parity["agree"] is True


def test_parity_not_scored_when_no_open_episode(intr, wired):
    # A dismiss with nothing active has no episode to compare → not scored.
    S, mgr = wired
    intr._mirror_situation_sync(FakeHass(), "dismissed", reason="noise")
    assert intr._last_parity is None


def test_parity_detects_divergence(intr, monkeypatch, caplog):
    # If the kernel situation lags the verdict (verdict=confirmed but situation is
    # still POSSIBLE), _record_parity flags a divergence at WARNING, log-only.
    import logging

    class _StubSit:
        state = "possible"

    class _StubMgr:
        def get(self, _id):
            return _StubSit()

    monkeypatch.setattr(intr, "_last_parity", None)
    with caplog.at_level(logging.WARNING):
        intr._record_parity(_StubMgr(), "confirmed", "sit_lagging")
    assert intr._last_parity["agree"] is False
    assert intr._last_parity["actual_state"] == "possible"
    assert any("DIVERGENCE" in r.message for r in caplog.records)
