"""MCU Phase E (E3): reasoning_loop runs the kernel local-first route in shadow.

The reasoning path routes to the cloud LLM when the connectivity breaker is
closed and to the local Mind when it is OPEN. `kernel.router` formalises that as
a local-first provider selection; `reasoning_loop._router_shadow` computes it
alongside the live decision and logs divergence. Shadow: the kernel result is
ignored and the breaker stays authoritative.
"""
import logging

import pytest


@pytest.fixture
def rl(load):
    return load("reasoning_loop")


def test_router_shadow_never_raises(rl):
    rl._router_shadow(True)
    rl._router_shadow(False)


def test_router_shadow_agrees_online_and_offline(rl, caplog):
    # When online the kernel picks cloud; offline it picks local — both agree
    # with the live decision, so no divergence line is emitted.
    with caplog.at_level(logging.DEBUG,
                         logger="custom_components.jarvis.reasoning_loop"):
        rl._router_shadow(True)
        rl._router_shadow(False)
    assert not any("router shadow divergence" in r.message for r in caplog.records)


# ── the underlying kernel route mapping ─────────────────────────────────────────

def test_kernel_route_prefers_cloud_when_available(load):
    R = load("kernel.router")
    providers = [
        R.Provider("local", capabilities=frozenset({"*"}), local=True,
                   available=True, quality=0.5),
        R.Provider("cloud", capabilities=frozenset({"*"}), local=False,
                   available=True, quality=0.9),
    ]
    res = R.route(R.TaskRequirements(capability="reasoning"), providers)
    assert res.provider.name == "cloud"  # higher quality, privacy ANY


def test_kernel_route_falls_back_to_local_when_cloud_down(load):
    R = load("kernel.router")
    providers = [
        R.Provider("local", capabilities=frozenset({"*"}), local=True,
                   available=True, quality=0.5),
        R.Provider("cloud", capabilities=frozenset({"*"}), local=False,
                   available=False, quality=0.9),
    ]
    res = R.route(R.TaskRequirements(capability="reasoning"), providers)
    assert res.provider.name == "local"
