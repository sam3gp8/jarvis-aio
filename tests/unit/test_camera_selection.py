"""Camera selection — choose all / some / none (camera.active_cameras, v7.0.0)."""
import sys
import types

import pytest


@pytest.fixture
def cam(load, monkeypatch):
    # camera.py does a bare `import aiohttp`, which isn't in the sandbox; a stub
    # module satisfies the import (aiohttp code paths aren't exercised here).
    if "aiohttp" not in sys.modules:
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    return load("camera")


class _St:
    def __init__(self, eid):
        self.entity_id = eid


class _States:
    def __init__(self, ids):
        self._c = [_St(i) for i in ids]

    def async_all(self, domain):
        return self._c if domain == "camera" else []


class _Hass:
    def __init__(self, ids):
        self.states = _States(ids)


def test_none_disabled_uses_all(cam, monkeypatch):
    monkeypatch.setattr(cam, "_disabled_cameras", lambda: set())
    h = _Hass(["camera.a", "camera.b", "camera.c"])
    assert cam.active_cameras(h) == ["camera.a", "camera.b", "camera.c"]


def test_some_disabled(cam, monkeypatch):
    monkeypatch.setattr(cam, "_disabled_cameras", lambda: {"camera.b"})
    h = _Hass(["camera.a", "camera.b", "camera.c"])
    assert cam.active_cameras(h) == ["camera.a", "camera.c"]


def test_all_disabled_uses_none(cam, monkeypatch):
    monkeypatch.setattr(cam, "_disabled_cameras", lambda: {"camera.a", "camera.b", "camera.c"})
    h = _Hass(["camera.a", "camera.b", "camera.c"])
    assert cam.active_cameras(h) == []


def test_camera_enabled_predicate(cam, monkeypatch):
    monkeypatch.setattr(cam, "_disabled_cameras", lambda: {"camera.x"})
    assert cam.camera_enabled("camera.y") is True
    assert cam.camera_enabled("camera.x") is False


def test_active_states_filtered(cam, monkeypatch):
    monkeypatch.setattr(cam, "_disabled_cameras", lambda: {"camera.b"})
    h = _Hass(["camera.a", "camera.b"])
    assert [s.entity_id for s in cam.active_camera_states(h)] == ["camera.a"]


def test_disabled_never_raises(cam, monkeypatch):
    def _boom():
        raise RuntimeError("bad config")
    monkeypatch.setattr(cam, "_disabled_cameras", _boom)
    # active_camera_states swallows errors -> treated as "no cameras" not a crash
    assert cam.active_camera_states(_Hass(["camera.a"])) == []
