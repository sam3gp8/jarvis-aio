"""
JARVIS — Morning / on-demand briefing.

Gathers context from around the house and produces a Jarvis-style spoken
briefing. Respects the time (good morning / good afternoon / good evening)
and includes:
  - Time + weather (if weather.* entity exists)
  - Who's home (if person.* entities exist)
  - Overnight sentinel events (last N hours)
  - Open doors/windows right now
  - Calendar events today (if calendar.* entity exists)
  - Unusual energy usage
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .const import JARVIS_PERSONA
from .database import get_recent_messages, save_message
from .directive_helper import build_system_prompt
from .presence import get_presence_summary
from .tts_helper import async_announce

_LOGGER = logging.getLogger(__name__)

BRIEFING_MODEL = "llama-3.3-70b-versatile"


def _time_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    if hour < 22:
        return "Good evening"
    return "Still awake"


def _gather_weather(hass: HomeAssistant) -> str:
    """Return a single-line weather summary from the first weather.* entity."""
    for state in hass.states.async_all("weather"):
        attrs = state.attributes
        temp = attrs.get("temperature")
        cond = state.state
        unit = attrs.get("temperature_unit", "°")
        forecast = attrs.get("forecast") or []
        high = low = None
        if forecast:
            today = forecast[0]
            high = today.get("temperature")
            low  = today.get("templow")
        parts = [f"{cond}"]
        if temp is not None:
            parts.append(f"currently {temp}{unit}")
        if high is not None and low is not None:
            parts.append(f"high {high}{unit}, low {low}{unit}")
        return ", ".join(parts)
    return ""


def _gather_open_things(hass: HomeAssistant) -> list[str]:
    """Doors, windows, locks that are currently open/unlocked."""
    items = []
    for state in hass.states.async_all("binary_sensor"):
        dc = state.attributes.get("device_class")
        if dc in ("door", "window", "garage_door") and state.state == "on":
            name = state.attributes.get("friendly_name", state.entity_id)
            items.append(f"{name} is open")
    for state in hass.states.async_all("lock"):
        if state.state == "unlocked":
            name = state.attributes.get("friendly_name", state.entity_id)
            items.append(f"{name} is unlocked")
    return items


def _gather_calendar(hass: HomeAssistant) -> list[str]:
    """Today's calendar events, if a calendar.* entity exists with a next event."""
    events = []
    for state in hass.states.async_all("calendar"):
        if state.state != "on":
            continue
        msg = state.attributes.get("message")
        start = state.attributes.get("start_time")
        if msg and start:
            events.append(f"{msg} at {start[11:16]}")
    return events


def _gather_energy_anomalies(hass: HomeAssistant) -> list[str]:
    """Look for *power sensors showing unexpectedly high draw."""
    anomalies = []
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") != "power":
            continue
        try:
            val = float(state.state)
        except (ValueError, TypeError):
            continue
        if val > 500:  # arbitrary "that's a lot" threshold
            name = state.attributes.get("friendly_name", state.entity_id)
            unit = state.attributes.get("unit_of_measurement", "W")
            anomalies.append(f"{name} is drawing {val:.0f}{unit}")
    return anomalies


def _gather_overnight_events(hass: HomeAssistant, hours: int = 12) -> list[str]:
    """Sentinel events from the database in the last N hours."""
    import sqlite3
    from pathlib import Path
    db = Path("/config/jarvis/conversations.db")
    events = []
    try:
        if not db.exists():
            return []
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT timestamp, detail FROM sentinel_events WHERE timestamp > ? "
                "ORDER BY timestamp DESC LIMIT 10",
                (since,),
            ).fetchall()
        for ts, detail in rows:
            events.append(f"{ts[11:16]}: {detail}")
    except Exception as exc:
        _LOGGER.debug("JARVIS briefing: could not read sentinel events: %s", exc)
    return events


async def async_briefing(
    hass: HomeAssistant,
    call: ServiceCall,
    groq_client,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
) -> dict:
    """
    Service: jarvis.briefing
    Gather context and deliver a Jarvis-voiced briefing.
    """
    announce = call.data.get("announce", True)
    include_weather  = call.data.get("include_weather", True)
    include_calendar = call.data.get("include_calendar", True)
    include_presence = call.data.get("include_presence", True)
    include_events   = call.data.get("include_events", True)
    include_energy   = call.data.get("include_energy", True)
    overnight_hours  = int(call.data.get("hours", 12))

    # Gather everything
    context_lines = [f"It is {datetime.now().strftime('%A %B %-d, %-I:%M %p')}."]

    if include_weather:
        weather = _gather_weather(hass)
        if weather:
            context_lines.append(f"Weather: {weather}.")

    if include_presence:
        presence = get_presence_summary(hass)
        if presence["people"]:
            home = [p["name"] for p in presence["people"] if p["state"] == "home"]
            away = [p["name"] for p in presence["people"] if p["state"] != "home"]
            if home:
                context_lines.append(f"At home: {', '.join(home)}.")
            if away:
                context_lines.append(f"Away: {', '.join(away)}.")

    if include_events:
        events = _gather_overnight_events(hass, overnight_hours)
        if events:
            context_lines.append(
                f"Events in the last {overnight_hours} hours:\n- " + "\n- ".join(events)
            )

    open_things = _gather_open_things(hass)
    if open_things:
        context_lines.append(f"Currently open/unlocked: {', '.join(open_things)}.")

    if include_calendar:
        cal_events = _gather_calendar(hass)
        if cal_events:
            context_lines.append(f"Calendar: {', '.join(cal_events)}.")

    if include_energy:
        energy = _gather_energy_anomalies(hass)
        if energy:
            context_lines.append(f"High power draw: {', '.join(energy)}.")

    # Camera detection summary from proactive briefing system
    try:
        from .proactive_briefing import get_snapshot_summary
        snap_summary = get_snapshot_summary(hours=overnight_hours)
        if snap_summary:
            context_lines.append(snap_summary)
    except Exception:
        pass

    context = "\n".join(context_lines)

    # Ask Groq to compose the briefing
    greeting = _time_greeting()
    task = (
        f"You are delivering a spoken briefing to {honorific}. "
        f"Begin with '{greeting}, {honorific}.' Then concisely cover the important items. "
        f"Under 120 words. Be efficient — do not list trivia. "
        f"If nothing is noteworthy, say so briefly. "
        f"Your prime directive should inform what you surface — protect, steward, "
        f"anticipate. Lead with anything that affects their safety or wellbeing."
    )
    system = build_system_prompt(hass, honorific, task)

    try:
        result = await hass.async_add_executor_job(
            lambda: groq_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Context:\n{context}"},
                ],
                max_tokens=400,
                temperature=0.6,
            )
        )
        briefing_text = result["text"].strip()
    except Exception as exc:
        _LOGGER.error("JARVIS briefing error: %s", exc)
        briefing_text = f"{greeting}, {honorific}. I am having trouble compiling your briefing at the moment."

    await hass.async_add_executor_job(
        save_message, "assistant", f"[Briefing] {briefing_text}", "briefing"
    )

    if announce:
        _LOGGER.info(
            "JARVIS briefing announce: tts=%s, speakers=%s, text_len=%d, first_50=%s",
            tts_entity, speakers, len(briefing_text or ""),
            (briefing_text or "")[:50],
        )
        if not briefing_text:
            _LOGGER.warning("JARVIS briefing: empty text, skipping announce")
        elif not tts_entity:
            _LOGGER.warning("JARVIS briefing: no TTS entity resolved")
        elif not speakers:
            _LOGGER.warning("JARVIS briefing: no speakers available")
        else:
            await async_announce(hass, briefing_text, tts_entity, speakers)

    return {"success": True, "briefing": briefing_text, "context": context}
