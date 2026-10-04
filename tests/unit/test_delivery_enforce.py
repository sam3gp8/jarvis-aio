"""MCU Phase R (R1b): the kernel situation store is AUTHORITATIVE for the
per-camera package-present verdict that drives the delivered/removed transition.

`package_monitor._evaluate_locked` now keys the transition off the kernel store's
open-episode view (`prev_present`) instead of the in-memory `_STATE["package"]`
flag, when enforce is on and the store has an opinion. Non-safety, and fail-safe:
a kernel read error (None) or the kill-switch off falls back to the legacy flag,
so announcements can never break.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def env(load, tmp_path, monkeypatch):
    pm = load("package_monitor")
    ds = load("delivery_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(ds, "_mgr", mgr)
    monkeypatch.setattr(ds, "_delivery_situation_ids", {})
    monkeypatch.setattr(ds, "_last_presence_parity", None)

    tts = load("tts_helper")
    spoken = []

    async def _announce(hass, msg, *a, **k):
        spoken.append(msg)
    monkeypatch.setattr(tts, "async_announce", _announce)
    monkeypatch.setattr(pm, "_in_quiet_hours", lambda h: False)
    monkeypatch.setattr(pm, "_announcements_on", lambda h: True)
    pm._STATE.clear()
    pm._ANNOUNCE_CD.clear()
    return pm, ds, S, mgr, spoken


async def _deliver(pm, h, cam="camera.porch"):
    await pm.evaluate(h, None, "Sir", "tts.x", ["media_player.y"], cam,
                      {"package": True, "mail": False, "count": 1}, source="test")


async def test_enforce_happy_path_announces_once_and_tracks(env):
    pm, ds, S, mgr, spoken = env
    h = FakeHass()
    await _deliver(pm, h)                       # first delivery
    assert len(spoken) == 1 and "delivered" in spoken[0]
    assert len(mgr.open_situations("delivery")) == 1
    # A second identical detection must NOT re-announce: the kernel view now says
    # present, so the transition does not re-fire.
    await _deliver(pm, h)
    assert len(spoken) == 1
    assert len(mgr.open_situations("delivery")) == 1


async def test_enforce_kernel_is_authoritative_on_divergence(env, caplog):
    import logging
    pm, ds, S, mgr, spoken = env
    h = FakeHass()
    await _deliver(pm, h)                        # _STATE.package=True, episode open
    # Kernel "forgets" (episode resolved) while the legacy flag still says present.
    ds.mirror_delivery_sync(h, "camera.porch", "removed", 0)
    assert mgr.open_situations("delivery") == []
    assert pm._STATE["camera.porch"]["package"] is True   # legacy still present
    # Under enforce the kernel view (False) wins → the transition re-fires and a
    # NEW episode opens, and the override is logged.
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.package_monitor"):
        await _deliver(pm, h)
    assert len(mgr.open_situations("delivery")) == 1       # kernel drove a re-delivery
    assert any("ENFORCE: kernel view" in r.message for r in caplog.records)


async def test_kill_switch_off_uses_legacy(env, monkeypatch):
    pm, ds, S, mgr, spoken = env
    monkeypatch.setattr(pm, "DELIVERY_SITUATION_ENFORCE", False)
    h = FakeHass()
    await _deliver(pm, h)
    ds.mirror_delivery_sync(h, "camera.porch", "removed", 0)   # kernel forgets
    assert mgr.open_situations("delivery") == []
    # Legacy flag still True → with enforce OFF the transition does NOT re-fire.
    await _deliver(pm, h)
    assert mgr.open_situations("delivery") == []               # legacy owned it


async def test_none_kernel_view_falls_back_to_legacy(env, monkeypatch):
    pm, ds, S, mgr, spoken = env

    async def _none(hass, entity_id):
        return None
    monkeypatch.setattr(pm, "_kernel_present", _none)
    h = FakeHass()
    await _deliver(pm, h)                        # legacy _STATE.package=True now
    ds.mirror_delivery_sync(h, "camera.porch", "removed", 0)
    # Enforce is on by default, but the kernel has no opinion (None) → fall back to
    # the legacy flag (True) → no re-fire. Fail-safe.
    await _deliver(pm, h)
    assert mgr.open_situations("delivery") == []


async def test_removed_announces_when_away_under_enforce(env, monkeypatch):
    pm, ds, S, mgr, spoken = env
    monkeypatch.setattr(pm, "_anyone_home", lambda h: False)   # away → removal speaks
    h = FakeHass()
    await _deliver(pm, h)
    spoken.clear()
    # Package gone: the kernel view (present) drives the removed transition.
    await pm.evaluate(h, None, "Sir", "tts.x", ["media_player.y"], "camera.porch",
                      {"package": False, "mail": False, "count": 0}, source="test")
    assert any("removed" in m for m in spoken)
    assert mgr.open_situations("delivery") == []
