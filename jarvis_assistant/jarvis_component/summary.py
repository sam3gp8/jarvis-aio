"""JARVIS — Conversation summary service."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from .const import JARVIS_PERSONA
from .database import get_recent_messages, save_message
from .directive_helper import build_system_prompt
from .tts_helper import async_announce

_LOGGER = logging.getLogger(__name__)

SUMMARY_MODEL = "llama-3.3-70b-versatile"


async def async_summarise(
    hass: HomeAssistant,
    call: ServiceCall,
    groq_client,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
) -> dict:
    """
    Service: jarvis.conversation_summary
    Summarises recent conversation history and delivers a Jarvis briefing.
    """
    hours:   int        = int(call.data.get("hours", 24))
    device_id: str|None = call.data.get("device_id")
    announce: bool      = call.data.get("announce", True)
    store: bool         = call.data.get("store", True)

    messages = await hass.async_add_executor_job(
        lambda: get_recent_messages(hours=hours, device_id=device_id, limit=300)
    )

    if not messages:
        summary = (
            f"Nothing to report, {honorific}. "
            f"The last {_period(hours)} appear to have been remarkably quiet."
        )
        if announce:
            await async_announce(hass, summary, tts_entity, speakers)
        return {"success": True, "summary": summary, "message_count": 0}

    # ── Build transcript ──────────────────────────────────────────────────────
    lines = []
    for m in messages:
        ts      = m["timestamp"][:16].replace("T", " ")
        speaker = "You" if m["role"] == "user" else "JARVIS"
        lines.append(f"[{ts}] {speaker}: {m['content']}")
    transcript = "\n".join(lines)

    task = (
        f"Summarise the following conversation transcript from the past {_period(hours)}. "
        f"3–5 sentences maximum. Highlight anything notable, unusual, or actionable. "
        f"Speak as JARVIS giving a briefing to {honorific}. "
        f"Extract the essence — do not list every exchange."
    )
    system = build_system_prompt(hass, honorific, task)

    try:
        result = await hass.async_add_executor_job(
            lambda: groq_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Transcript:\n\n{transcript}"},
                ],
                max_tokens=300,
            )
        )
        summary = result["text"].strip()
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.error("JARVIS summary Groq error: %s", exc)
        return {"success": False, "error": str(exc)}

    if store:
        await hass.async_add_executor_job(
            save_message, "assistant", f"[Summary — {_period(hours)}] {summary}", "summary"
        )

    if announce:
        await async_announce(hass, summary, tts_entity, speakers)

    _LOGGER.info("JARVIS summary: %d messages → %d chars", len(messages), len(summary))
    return {"success": True, "summary": summary, "message_count": len(messages), "period_hours": hours}


def _period(hours: int) -> str:
    if hours <= 1:
        return "hour"
    if hours <= 24:
        return f"{hours} hours"
    days = hours // 24
    return f"{days} day{'s' if days > 1 else ''}"
