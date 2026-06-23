"""Integration-layer conftest.

The unit suite (tests/unit/) uses hand-rolled fakes for speed. This layer is for
the small number of tests that need a REAL Home Assistant instance — config-flow
validation, actual entity/service registration, the setup path — where a fake
can't credibly prove the integration loads.

It relies on `pytest-homeassistant-custom-component` (PHACC), which provides the
real `hass` fixture. PHACC is a heavy, version-pinned dependency, so it is NOT
required for the unit suite; these tests skip cleanly when it is absent.

    pip install pytest-homeassistant-custom-component
"""
import pytest

# Skip this entire directory unless PHACC is installed.
pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="install pytest-homeassistant-custom-component to run integration tests",
)

# PHACC requires this opt-in fixture to enable loading custom integrations.
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,  # re-exported for test modules
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """PHACC gate: makes `custom_components/jarvis` importable by `hass`."""
    yield
