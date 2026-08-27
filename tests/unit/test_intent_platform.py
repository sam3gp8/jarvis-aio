"""Guard: HA's intent loader does `await platform.async_setup_intents(hass)`.

If async_setup_intents is a plain function returning None, that await raises
'TypeError: NoneType can't be awaited' (GitHub issue #12) — which surfaces as
"Unexpected error during intent recognition". It must be `async def`.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "intent" / "__init__.py"


def test_async_setup_intents_is_coroutine():
    src = SRC.read_text()
    assert "async def async_setup_intents(" in src
    # ensure there's no sync `def async_setup_intents(` (would be awaited -> crash)
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("def async_setup_intents("):
            raise AssertionError("async_setup_intents must be `async def`, not a plain function")
