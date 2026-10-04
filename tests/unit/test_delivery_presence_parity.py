"""MCU Phase R (R1a): delivery decision-parity — the kernel situation store's
per-camera package-present view is compared against the legacy in-memory verdict
(``prev["package"]``) and divergence is logged.

LOG-ONLY: nothing is gated, no behaviour changes. This earns the kernel the right
to *own* the per-camera presence verdict (the enforce flip, R1b) only once the two
agree on real traffic. The kernel view = "is there an open (non-terminal)
``delivery`` episode for this camera?" tracked in the persistent situations.db.
"""
import logging

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
    monkeypatch.setattr(mod, "_last_presence_parity", None)
    return mod, S, mgr


def test_kernel_present_false_when_no_episode(ds):
    mod, S, mgr = ds
    assert mod.kernel_package_present_sync(FakeHass(), "camera.porch") is False


def test_kernel_present_true_after_delivered(ds):
    mod, S, mgr = ds
    h = FakeHass()
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    assert mod.kernel_package_present_sync(h, "camera.porch") is True
    # a different camera is independent
    assert mod.kernel_package_present_sync(h, "camera.side") is False


def test_kernel_present_false_after_removed(ds):
    mod, S, mgr = ds
    h = FakeHass()
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    mod.mirror_delivery_sync(h, "camera.porch", "removed", 0)
    assert mod.kernel_package_present_sync(h, "camera.porch") is False


def test_parity_agrees_present(ds):
    mod, S, mgr = ds
    h = FakeHass()
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    mod.record_presence_parity(h, "camera.porch", legacy_present=True)
    assert mod._last_presence_parity == {
        "entity_id": "camera.porch", "legacy": True, "kernel": True, "agree": True}


def test_parity_agrees_absent(ds):
    mod, S, mgr = ds
    mod.record_presence_parity(FakeHass(), "camera.porch", legacy_present=False)
    assert mod._last_presence_parity["agree"] is True
    assert mod._last_presence_parity["kernel"] is False


def test_parity_logs_divergence(ds, caplog):
    mod, S, mgr = ds
    h = FakeHass()
    # kernel has an open episode but legacy says no package → divergence logged.
    mod.mirror_delivery_sync(h, "camera.porch", "delivered", 1)
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.delivery_situation"):
        mod.record_presence_parity(h, "camera.porch", legacy_present=False)
    assert mod._last_presence_parity["agree"] is False
    assert any("presence parity DIVERGENCE" in r.message for r in caplog.records)


def test_parity_none_kernel_view_is_not_a_divergence(ds, monkeypatch):
    mod, S, mgr = ds
    # If the store can't be read, record nothing (None kernel opinion).
    monkeypatch.setattr(mod, "kernel_package_present_sync", lambda h, e: None)
    mod.record_presence_parity(FakeHass(), "camera.porch", legacy_present=True)
    assert mod._last_presence_parity is None


# ── the live path drives the parity check and is behaviour-unchanged ─────────

@pytest.fixture
def pm(load, tmp_path, monkeypatch):
    mod = load("package_monitor")
    ds = load("delivery_situation")
    S = load("kernel.situation")
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(ds, "_mgr", mgr)
    monkeypatch.setattr(ds, "_delivery_situation_ids", {})
    monkeypatch.setattr(mod, "_STATE", {})
    return mod, ds, mgr


async def test_evaluate_records_presence_parity(pm, monkeypatch):
    mod, ds, mgr = pm
    h = FakeHass()
    # First delivery: legacy prev had no package, kernel also empty → agree.
    monkeypatch.setattr(mod, "_announcements_on", lambda hass: False)  # silence TTS
    await mod.evaluate(h, None, "sir", None, [], "camera.porch",
                       {"package": True, "count": 1}, source="test")
    assert ds._last_presence_parity is not None
    assert ds._last_presence_parity["agree"] is True
    # The mirror opened a kernel episode; a second evaluate sees prev.package=True
    # and the kernel episode open → still agree.
    await mod.evaluate(h, None, "sir", None, [], "camera.porch",
                       {"package": True, "count": 1}, source="test")
    assert ds._last_presence_parity["legacy"] is True
    assert ds._last_presence_parity["kernel"] is True
    assert ds._last_presence_parity["agree"] is True
