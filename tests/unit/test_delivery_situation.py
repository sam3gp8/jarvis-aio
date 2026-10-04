"""MCU Phase D (D3): package delivery lifecycle mirrored into kernel.situation.

Two layers, mirroring the D2 freeze tests:
  * the per-camera mirror/parity logic in `delivery_situation` exercised directly
    against a real SituationManager on a tmp db, and
  * an integration check that `package_monitor.evaluate` still announces the same
    way AND drives the mirror (delivered → removed), proving it is additive.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def ds(load, tmp_path, monkeypatch):
    mod = load("delivery_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(mod, "_mgr", mgr)
    monkeypatch.setattr(mod, "_delivery_situation_ids", {})
    monkeypatch.setattr(mod, "_last_parity", None)
    return mod, S, mgr


def test_delivered_opens_episode(ds):
    mod, S, mgr = ds
    mod.mirror_delivery_sync(FakeHass(), "camera.porch", "delivered", 2)
    opens = mgr.open_situations("delivery")
    assert len(opens) == 1
    assert opens[0].state == S.INVESTIGATING and opens[0].subject == "camera.porch"
    assert mod._last_parity["agree"] is True


def test_delivered_then_removed_resolves(ds):
    mod, S, mgr = ds
    h = FakeHass()
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    sid = mod._delivery_situation_ids["camera.porch"]
    mod.mirror_delivery_sync(h, "camera.porch", "removed", 0)
    assert mgr.open_situations("delivery") == []
    assert mgr.get(sid).state == S.RESOLVED
    assert "camera.porch" not in mod._delivery_situation_ids
    assert mod._last_parity["action"] == "removed" and mod._last_parity["agree"] is True


def test_two_cameras_track_independent_episodes(ds):
    mod, S, mgr = ds
    h = FakeHass()
    mod.mirror_delivery_sync(h, "camera.front", "delivered", 1)
    mod.mirror_delivery_sync(h, "camera.side", "delivered", 1)
    opens = {s.subject for s in mgr.open_situations("delivery")}
    assert opens == {"camera.front", "camera.side"}
    # Resolving one leaves the other open.
    mod.mirror_delivery_sync(h, "camera.front", "removed", 0)
    opens = {s.subject for s in mgr.open_situations("delivery")}
    assert opens == {"camera.side"}


def test_redundant_delivered_is_idempotent(ds):
    mod, S, mgr = ds
    h = FakeHass()
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    assert len(mgr.open_situations("delivery")) == 1


def test_removed_with_no_episode_not_scored(ds):
    mod, S, mgr = ds
    mod.mirror_delivery_sync(FakeHass(), "camera.porch", "removed", 0)
    assert mod._last_parity is None
    assert mgr.open_situations("delivery") == []


def test_parity_detects_divergence(ds, caplog):
    import logging
    mod, S, mgr = ds

    class _StubSit:
        state = "possible"

    class _StubMgr:
        def get(self, _id):
            return _StubSit()

    with caplog.at_level(logging.WARNING):
        mod._record_parity(_StubMgr(), "removed", "sit_lagging")
    assert mod._last_parity["agree"] is False
    assert any("DIVERGENCE" in r.message for r in caplog.records)


def test_mirror_never_raises_on_bad_manager(load, monkeypatch):
    mod = load("delivery_situation")

    class _Boom:
        def get(self, *_a, **_k):
            raise RuntimeError("db down")
    monkeypatch.setattr(mod, "_mgr", _Boom())
    monkeypatch.setattr(mod, "_delivery_situation_ids", {"camera.x": "sit_x"})
    mod.mirror_delivery_sync(FakeHass(), "camera.x", "removed", 0)  # must not raise


# ── integration: announcements unchanged AND the mirror is driven ──────────────

async def test_evaluate_announces_and_mirrors(load, fake_hass, tmp_path, monkeypatch):
    pm = load("package_monitor")
    ds = load("delivery_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(ds, "_mgr", mgr)
    monkeypatch.setattr(ds, "_delivery_situation_ids", {})

    tts = load("tts_helper")
    spoken = []

    async def _announce(hass, msg, *a, **k):
        spoken.append(msg)
    monkeypatch.setattr(tts, "async_announce", _announce)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    pm._STATE.clear()
    pm._ANNOUNCE_CD.clear()

    # Delivery arrives → announced once AND a delivery situation opens.
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", ["media_player.y"],
                      "camera.porch", {"package": True, "mail": False, "count": 1},
                      source="test")
    assert len(spoken) == 1 and "package has been delivered" in spoken[0]
    opens = mgr.open_situations("delivery")
    assert len(opens) == 1 and opens[0].state == S.INVESTIGATING

    # Package removed → situation resolves (announcement path unchanged).
    await pm.evaluate(fake_hass, None, "Sir", "tts.x", ["media_player.y"],
                      "camera.porch", {"package": False, "mail": False, "count": 0},
                      source="test")
    assert mgr.open_situations("delivery") == []
