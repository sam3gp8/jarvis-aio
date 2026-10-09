"""Integration smoke tests against a real Home Assistant instance (PHACC).

These prove what the unit fakes cannot: that the integration actually sets up
under a real `hass`, registers its services, and tears down cleanly on unload
and reload — the install / setup / reload / unload path. Run locally (or in CI)
where pytest-homeassistant-custom-component is installed; the directory conftest
skips them cleanly when it is absent.

Two things are stubbed, and only those:

* Provider construction — the integration imports its LLM SDKs lazily, so once
  ``create_provider`` is mocked, setup needs no groq/openai/etc. at all.
* The platform forward (``conversation``) — forwarding pulls in Home Assistant's
  full conversation stack (hassil, intents, gazetteer_matcher, …), whose imports
  are HA's concern, validated by hassfest, not jarvis's. Mocking the forward
  keeps the test focused on jarvis's OWN wiring (data store, services, scheduler
  timers, clean teardown) and deterministic across HA upgrades.

Everything else is the real thing: a real `hass`, real config-entry setup, real
service registration, real scheduled timers, and PHACC's real ``verify_cleanup``
teardown check (which fails on any lingering timer after unload).
"""
from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "jarvis"

ENTRY_DATA = {
    "api_key": "", "model": "llama3.1", "honorific": "Sir",
    "llm_provider": "ollama", "llm_base_url": "http://localhost:11434/v1",
    "schema_version": 7,
}


class _FakeProvider:
    name = "fake"

    def chat(self, *a, **k):
        return {"text": "", "tool_calls": [], "raw": None}

    def supports_vision(self):
        return False


def _entry():
    return MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id=DOMAIN)


def _mock_setup():
    """Stub provider construction and the HA platform forward/unload.

    Returns a list of patchers to enter together. The platform forward is
    stubbed so setup does not import HA's conversation backend (hassil,
    intents, gazetteer_matcher); the matching unload stub returns True so the
    unload path still pops jarvis's data and clears its services.
    """
    return [
        patch("custom_components.jarvis.create_provider", return_value=_FakeProvider()),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            AsyncMock(return_value=None),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            AsyncMock(return_value=True),
        ),
    ]


class _mock_provider:
    """Context manager entering every patch from :func:`_mock_setup`."""

    def __enter__(self):
        self._patchers = _mock_setup()
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False


async def test_config_flow_opens(hass):
    """The user-initiated config flow is reachable and returns a coherent result.

    jarvis auto-configures when it detects an existing runtime config, so the
    flow legitimately resolves to either a form (fresh install) or a created
    entry (existing config) — both are valid; a crash or malformed result is not.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    assert result["type"] in (
        FlowResultType.FORM,
        FlowResultType.MENU,
        FlowResultType.CREATE_ENTRY,
    )


async def test_setup_and_unload_lifecycle(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    with _mock_provider():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]
        assert hass.services.has_service(DOMAIN, "analyze_camera")
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert not hass.services.has_service(DOMAIN, "analyze_camera")


async def test_reload_leaves_integration_loaded(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    with _mock_provider():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.entry_id in hass.data[DOMAIN]
        assert hass.services.has_service(DOMAIN, "analyze_camera")


async def test_setup_fails_safely_on_provider_outage(hass):
    """A provider that cannot be constructed (outage, bad/missing key) must fail
    setup CLEANLY — no half-initialised jarvis in hass.data, no registered
    services, and (via PHACC's verify_cleanup teardown) no lingering timers —
    rather than crash Home Assistant or leave the integration partially wired.
    This is the audit's "handle provider outages safely" at the setup boundary.

    Only ``create_provider`` is made to fail; the platform-forward stubs are
    unnecessary because setup returns before any forward."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch("custom_components.jarvis.create_provider",
               side_effect=RuntimeError("provider unreachable")):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert ok is False
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert not hass.services.has_service(DOMAIN, "analyze_camera")


async def test_cold_resetup_after_unload(hass):
    """A full unload followed by a fresh setup (a restart proxy) re-initialises
    cleanly: the data store is repopulated, services re-register, and nothing
    leaks across the cycle — so a restart never leaves JARVIS half-initialised or
    doubly-wired. (Distinct from reload, which never fully tears down.)"""
    entry = _entry()
    entry.add_to_hass(hass)
    with _mock_provider():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
        # cold start again — must come up exactly as a first install would
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.entry_id in hass.data[DOMAIN]
        assert hass.services.has_service(DOMAIN, "analyze_camera")
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
