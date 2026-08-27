"""Guards for the conversation entity's HA dispatch contract.

HA's ConversationEntity.async_process is @final and sets up the chat session/log
before calling _async_handle_message. JARVIS must implement ONLY
_async_handle_message and must not override async_process — a shim that did
(v7.48.1) bypassed that setup on current HA and crashed voice turns with an
opaque "Unexpected error during intent recognition". These are source-level
guards because exercising the entity needs a live HA conversation stack.
"""
import inspect
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "conversation.py"


def test_does_not_override_final_async_process():
    src = SRC.read_text()
    # No `async def async_process` method on the entity (it's @final in HA).
    assert "async def async_process(" not in src


def test_handler_is_wrapped_against_crashes():
    src = SRC.read_text()
    # The public handler HA calls delegates to an impl inside try/except, so a
    # crash surfaces with a trace instead of HA's opaque error.
    assert "async def _async_handle_message(" in src
    assert "async def _handle_message_impl(" in src
    assert "conversation handler crashed" in src
