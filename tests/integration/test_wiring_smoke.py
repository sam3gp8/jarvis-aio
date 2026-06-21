"""Integration smoke tests against a real Home Assistant instance (PHACC).

These are the wiring tests the hand-rolled fakes cannot prove: that the
integration actually sets up, registers its conversation agent, and that the
config flow validates input. They are intentionally few — the bulk of coverage
lives in tests/unit/ where it is fast and deterministic.

Skipped unless pytest-homeassistant-custom-component is installed (see the
directory conftest). The bodies below are scaffolds: wire them to the real
DOMAIN/config-flow shape, then enable.
"""
import pytest

from homeassistant.setup import async_setup_component  # noqa: E402

from .conftest import MockConfigEntry  # noqa: E402

DOMAIN = "jarvis"


@pytest.mark.skip(reason="scaffold — wire to the real config-entry data, then enable")
async def test_setup_entry_registers_integration(hass):
    """Setting up a config entry should leave the integration loaded and its
    runtime data registered under hass.data[DOMAIN]."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"llm_provider": "ollama", "llm_base_url": "http://localhost:11434/v1"},
        options={},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN in hass.data


@pytest.mark.skip(reason="scaffold — assert the agent id once registration lands")
async def test_conversation_agent_is_registered(hass):
    """JARVIS should register as a conversation agent so it can be selected as
    the assist pipeline's conversation engine."""
    assert await async_setup_component(hass, "conversation", {})
    # ... after entry setup, assert the jarvis agent is discoverable.


@pytest.mark.skip(reason="scaffold — exercise the real config_flow steps, then enable")
async def test_config_flow_accepts_local_llm(hass):
    """A local (Ollama) endpoint with no cloud key must be a valid config — the
    v6.7.0 'local-first install' contract."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"llm_base_url": "http://localhost:11434/v1"},
    )
    assert result["type"] == "create_entry"
