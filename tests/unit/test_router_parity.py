"""Phase 6 router PARITY — reasoning_loop logs the rolling agreement between the
kernel local-first route and the live breaker decision.

Observe-only: the breaker stays authoritative. The parity window quantifies that
the kernel router reproduces the incumbent decision on real reasoning traffic —
the bar the owner-gated enforce rung needs. Kill-switch ROUTER_PARITY /
`router_parity`.
"""
import pytest


@pytest.fixture
def rl(load):
    load("kernel.router")            # ensure jc.kernel.router is importable
    mod = load("reasoning_loop")
    mod._ROUTER_PARITY_WINDOW.clear()
    mod._ROUTER_PARITY_SEEN = 0
    return mod


def test_parity_enabled_by_default(rl):
    assert rl._router_parity_enabled() is True


def test_parity_kill_switch(rl, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: False if k == "router_parity" else d)
    assert rl._router_parity_enabled() is False


def test_shadow_tallies_agreement_online(rl):
    # breaker closed (online) → live picks cloud; kernel (cloud available, higher
    # quality) also picks cloud → agree.
    rl._router_shadow(online=True)
    assert list(rl._ROUTER_PARITY_WINDOW) == [True]


def test_shadow_tallies_agreement_offline(rl):
    # breaker open (offline) → live picks local; kernel (cloud unavailable) is left
    # with local → agree.
    rl._router_shadow(online=False)
    assert list(rl._ROUTER_PARITY_WINDOW) == [True]


def test_window_accumulates_and_reproduces_incumbent(rl):
    for _ in range(25):
        rl._router_shadow(online=True)
    assert rl._ROUTER_PARITY_SEEN == 25
    assert len(rl._ROUTER_PARITY_WINDOW) == 25
    assert all(rl._ROUTER_PARITY_WINDOW)      # kernel reproduces the breaker


def test_kill_switch_stops_the_tally(rl, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: False if k == "router_parity" else d)
    rl._router_shadow(online=True)
    assert len(rl._ROUTER_PARITY_WINDOW) == 0  # observe-only emit skipped


def test_window_is_bounded(rl):
    # The rolling window never grows without bound (maxlen), while the cumulative
    # seen counter keeps the log cadence honest.
    for _ in range(250):
        rl._router_shadow(online=True)
    assert rl._ROUTER_PARITY_SEEN == 250
    assert len(rl._ROUTER_PARITY_WINDOW) == rl._ROUTER_PARITY_WINDOW.maxlen == 200
