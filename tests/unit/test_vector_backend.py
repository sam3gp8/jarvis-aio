"""Tests for the optional ChromaDB vector backend (v6.56.0): detection,
store re-initialization, and the runtime install flow. ChromaDB isn't
installed in the test env, so is_installed() is naturally False and the
install path is exercised with a mocked HA package helper."""
import sys
import types

import pytest


@pytest.fixture
def vb(load):
    return load("vector_backend")


def test_is_installed_false_without_chromadb(vb):
    # chromadb genuinely absent in the test env
    assert vb.is_installed() is False


def test_status_reports_keyword_when_absent(vb):
    st = vb.status()
    assert st["installed"] is False
    assert st["memory_vector"] is False
    assert st["documents_vector"] is False
    assert "chromadb" in st["package"]


async def test_install_missing_ha_helper_is_honest(vb, fake_hass, monkeypatch):
    # chromadb not installed, and HA's package helper import fails → clear error
    monkeypatch.setattr(vb, "is_installed", lambda: False)
    # ensure the helper import raises
    sys.modules.pop("homeassistant.util.package", None)
    res = await vb.install(fake_hass)
    assert res["ok"] is False
    assert "error" in res


async def test_install_already_present_is_noop_activate(vb, fake_hass, monkeypatch):
    monkeypatch.setattr(vb, "is_installed", lambda: True)
    monkeypatch.setattr(vb, "_reinit_stores",
                        lambda: {"memory_vector": True, "documents_vector": True})
    res = await vb.install(fake_hass)
    assert res["ok"] is True and res["already"] is True
    assert res["memory_vector"] is True


async def test_install_runs_helper_then_reinits(vb, fake_hass, monkeypatch):
    monkeypatch.setattr(vb, "is_installed", lambda: False)

    called = {}
    async def _fake_install(pkg):
        called["pkg"] = pkg
        return True
    stub = types.ModuleType("homeassistant.util.package")
    stub.async_install_package = _fake_install
    monkeypatch.setitem(sys.modules, "homeassistant.util.package", stub)
    monkeypatch.setattr(vb, "_reinit_stores",
                        lambda: {"memory_vector": True, "documents_vector": True})

    res = await vb.install(fake_hass)
    assert res["ok"] is True and res["installed"] is True and res["already"] is False
    assert "chromadb" in called["pkg"]
    assert res["documents_vector"] is True


async def test_install_helper_failure_keeps_keyword(vb, fake_hass, monkeypatch):
    monkeypatch.setattr(vb, "is_installed", lambda: False)
    async def _fail_install(pkg):
        return False
    stub = types.ModuleType("homeassistant.util.package")
    stub.async_install_package = _fail_install
    monkeypatch.setitem(sys.modules, "homeassistant.util.package", stub)

    res = await vb.install(fake_hass)
    assert res["ok"] is False and res["installed"] is False
    assert "error" in res


def test_reinit_stores_survives_missing_modules(vb, monkeypatch):
    # _reinit_stores must never raise even if a store import blows up
    out = vb._reinit_stores()
    assert set(out) == {"memory_vector", "documents_vector"}
    assert isinstance(out["memory_vector"], bool)
