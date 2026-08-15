"""
JARVIS — continued conversation / turn-taking (v6.88.0).

Natural turn-taking: after JARVIS asks something, keep the satellite listening
for the reply without a new wake word. This module decides when a response
invites a follow-up (should_continue) and reads the enable flag; conversation.py
sets continue_conversation on the result accordingly, and the assist pipeline
honors it by reopening the mic.

Conservative and OFF by default. It only continues on a clear question or an
explicit offer to act — it will not hold the mic open after a plain statement.
The fuller ambient behavior (no-wake response, barge-in, multi-satellite
continuity) and the external-speaker reopen timing are hardware-validated on the
real satellites, not here; this is the in-process foundation they build on.
"""
from __future__ import annotations

import re

_INVITE = re.compile(
    r"(?i)\b(let me know|which (?:one )?would you|shall i|would you like|"
    r"do you want me to|should i|want me to)\b"
)


def enabled() -> bool:
    """Whether continued conversation is turned on (off by default)."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("continued_conversation_enabled", False))
    except Exception:
        return False


def should_continue(text: str) -> bool:
    """True when the response invites a reply, so the satellite should keep
    listening without a new wake word. Conservative: a trailing question, or a
    clear offer to act. A plain statement returns False."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    return bool(_INVITE.search(t))
