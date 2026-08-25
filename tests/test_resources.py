"""Tests for the lifecycle resource registry (resources.py)."""
from __future__ import annotations


def _res(load):
    return load("resources").JarvisResources()


def test_close_all_disposes_each_kind(load):
    r = _res(load)
    calls = {"unsub": 0, "cancel": 0, "shutdown": 0}
    r.add_unsub(lambda: calls.__setitem__("unsub", calls["unsub"] + 1))

    class _Task:
        def cancel(self):
            calls["cancel"] += 1

    class _Closeable:
        def shutdown(self):
            calls["shutdown"] += 1

    r.add_task(_Task())
    r.add_closeable(_Closeable())
    summary = r.close_all()
    assert calls == {"unsub": 1, "cancel": 1, "shutdown": 1}
    assert summary["unsubs"] == 1 and summary["tasks"] == 1 and summary["closeables"] == 1
    assert summary["errors"] == 0


def test_one_failure_does_not_block_the_rest(load):
    r = _res(load)
    hit = {"second": False}

    def _boom():
        raise RuntimeError("bad unsub")

    r.add_unsub(_boom)
    r.add_unsub(lambda: hit.__setitem__("second", True))
    summary = r.close_all()
    assert hit["second"] is True          # the good unsub still ran
    assert summary["errors"] == 1 and summary["unsubs"] == 1


def test_close_all_is_idempotent(load):
    r = _res(load)
    n = {"c": 0}
    r.add_unsub(lambda: n.__setitem__("c", n["c"] + 1))
    first = r.close_all()
    second = r.close_all()                 # reload-safe: nothing left to dispose
    assert first["unsubs"] == 1
    assert second == {"unsubs": 0, "tasks": 0, "closeables": 0, "errors": 0}
    assert n["c"] == 1


def test_close_prefers_shutdown_then_close(load):
    r = _res(load)
    seen = []

    class _Both:
        def shutdown(self):
            seen.append("shutdown")
        def close(self):
            seen.append("close")

    class _OnlyClose:
        def close(self):
            seen.append("close-only")

    r.add_closeable(_Both())
    r.add_closeable(_OnlyClose())
    r.close_all()
    assert seen == ["shutdown", "close-only"]   # shutdown() wins when both exist


def test_add_unsubs_accepts_a_list_and_ignores_none(load):
    r = _res(load)
    n = {"c": 0}
    r.add_unsubs([lambda: n.__setitem__("c", n["c"] + 1),
                  lambda: n.__setitem__("c", n["c"] + 1)])
    r.add_unsub(None)               # ignored
    r.add_unsubs(None)              # ignored
    summary = r.close_all()
    assert summary["unsubs"] == 2 and n["c"] == 2
