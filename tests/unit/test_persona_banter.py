"""Tests for the MCU-JARVIS persona banter valve (v6.51.0). The load-bearing
guarantee: wit widens the phrase pools at LIGHT register when banter is maxed,
but URGENT/GRAVE are NEVER widened — JARVIS does not quip during a smoke
alarm, and that must be structurally impossible, not merely discouraged."""
import pytest


@pytest.fixture
def persona(load):
    p = load("persona")
    p.set_variety(True)
    p.set_banter(1)          # reset to default each test
    return p


def test_banter_clamps(persona):
    persona.set_banter(99);  assert persona._BANTER == 2
    persona.set_banter(-5);  assert persona._BANTER == 0
    persona.set_banter("x"); assert persona._BANTER == 1     # bad input → dry


def test_full_banter_widens_light_ack_pool(persona):
    persona.set_banter(0)
    base = persona._banter_pool(persona._ACK, "light", persona._ACK_FULL)
    persona.set_banter(2)
    full = persona._banter_pool(persona._ACK, "light", persona._ACK_FULL)
    assert len(full) > len(base)
    assert set(base).issubset(set(full))         # additive, never replaces


def test_urgent_register_never_widens(persona):
    """The core safety property: no banter level touches urgent/grave pools."""
    persona.set_banter(2)
    for reg in ("urgent", "grave"):
        widened = persona._banter_pool(persona._ACK, reg, persona._ACK_FULL)
        plain = list(persona._reg(persona._ACK, reg))
        assert widened == plain, f"{reg} must never gain banter lines"


def test_full_banter_lines_are_reachable(persona):
    """With banter maxed, the full-only lines actually surface over samples."""
    persona.set_banter(2)
    seen = {persona.completed("sir", "light") for _ in range(400)}
    full_lines = {persona._fill(t, "sir") for t in persona._DONE_FULL}
    assert seen & full_lines, "full-banter DONE lines never appeared"


def test_dry_default_does_not_use_full_lines(persona):
    persona.set_banter(1)
    seen = {persona.acknowledge("sir", "light") for _ in range(300)}
    full_lines = {persona._fill(t, "sir") for t in persona._ACK_FULL}
    # level-1 must stay within base phrasing (full lines gated at level 2)
    assert not (seen & (full_lines - {persona._fill(t, "sir") for t in persona._ACK["light"]}))


def test_aside_empty_below_full(persona):
    persona.set_banter(1)
    assert persona.aside("sir") == ""
    persona.set_banter(2)
    assert persona.aside("sir") != ""            # a real aside when maxed


def test_unable_gains_wit_at_full(persona):
    persona.set_banter(0)
    plain = {persona.unable("sir") for _ in range(200)}
    persona.set_banter(2)
    witty = {persona.unable("sir") for _ in range(200)}
    full_lines = {persona._fill(t, "sir") for t in persona._UNABLE_FULL}
    assert witty & full_lines
    assert not (plain & full_lines)              # plain never reached them


def test_config_drives_banter(persona, load, monkeypatch):
    jcfg = load("jarvis_config")
    monkeypatch.setattr(jcfg, "get",
                        lambda k, d=None: 2 if k == "banter_level" else d)
    persona.set_banter(0)                         # config should override on speak
    persona.acknowledge("sir", "light")           # triggers _banter_from_cfg
    assert persona._BANTER == 2


def test_honorific_filled(persona):
    persona.set_banter(2)
    line = persona.acknowledge("Chase", "light")
    assert "{h}" not in line and "{H}" not in line
