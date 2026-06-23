"""Regression tests for the EntityLockRegistry concurrency control.

Stdlib-only; loads standalone. Pins acquire/discard/preempt semantics, release
hygiene (a preempted token must not disturb the new holder), and the guard
context manager.
"""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "jarvis_assistant" / "jarvis_component"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mx = _load_standalone("jarvis_mutex", "automation/mutex.py")
P = mx.Priority


def test_acquire_free_entity():
    reg = mx.EntityLockRegistry()
    tok = reg.try_acquire("light.office", P.INTENT)
    assert tok is not None
    assert reg.is_locked("light.office")
    assert reg.held_priority("light.office") == int(P.INTENT)


def test_equal_priority_is_discarded_while_held():
    reg = mx.EntityLockRegistry()
    reg.try_acquire("light.office", P.INTENT)
    assert reg.try_acquire("light.office", P.INTENT) is None


def test_lower_priority_is_discarded_while_held():
    reg = mx.EntityLockRegistry()
    reg.try_acquire("light.office", P.VISUAL)
    assert reg.try_acquire("light.office", P.PREDICTIVE) is None
    assert reg.held_priority("light.office") == int(P.VISUAL)


def test_higher_priority_preempts_holder():
    reg = mx.EntityLockRegistry()
    low = reg.try_acquire("climate.office", P.PREDICTIVE)
    high = reg.try_acquire("climate.office", P.VISUAL)   # real-time presence wins
    assert high is not None
    assert low.valid is False                            # predictive command revoked
    assert reg.held_priority("climate.office") == int(P.VISUAL)


def test_release_frees_lock():
    reg = mx.EntityLockRegistry()
    tok = reg.try_acquire("lock.front", P.INTENT)
    reg.release(tok)
    assert reg.is_locked("lock.front") is False
    # now re-acquirable, even at lower priority
    assert reg.try_acquire("lock.front", P.PREDICTIVE) is not None


def test_releasing_preempted_token_does_not_free_new_holder():
    reg = mx.EntityLockRegistry()
    low = reg.try_acquire("climate.office", P.PREDICTIVE)
    high = reg.try_acquire("climate.office", P.VISUAL)   # preempts low
    reg.release(low)                                      # stale token — must be a no-op on the lock
    assert reg.is_locked("climate.office") is True
    assert reg.held_priority("climate.office") == int(P.VISUAL)
    reg.release(high)
    assert reg.is_locked("climate.office") is False


@pytest.mark.asyncio
async def test_guard_acquires_and_releases():
    reg = mx.EntityLockRegistry()
    async with reg.guard("light.kitchen", P.INTENT) as token:
        assert token is not None
        assert reg.is_locked("light.kitchen")
    assert reg.is_locked("light.kitchen") is False


@pytest.mark.asyncio
async def test_guard_yields_none_when_contended():
    reg = mx.EntityLockRegistry()
    reg.try_acquire("light.kitchen", P.VISUAL)
    async with reg.guard("light.kitchen", P.PREDICTIVE) as token:
        assert token is None                             # couldn't acquire
    # the original VISUAL holder is untouched
    assert reg.held_priority("light.kitchen") == int(P.VISUAL)


def test_priority_ladder_orders_presence_over_prediction():
    assert P.VISUAL > P.PREDICTIVE
    assert P.SAFETY > P.VISUAL
    assert P.INTENT > P.ROUTINE
