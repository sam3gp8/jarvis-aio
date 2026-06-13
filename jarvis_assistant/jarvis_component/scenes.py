"""
JARVIS — Scene intelligence.

JARVIS learns what scenes exist and picks one intelligently based on natural-
language intent ("something relaxing", "bright and energetic", "going to bed").
Uses Groq to map intent → scene from the actual scene list in HA.
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant, ServiceCall

from .tts_helper import async_announce

_LOGGER = logging.getLogger(__name__)

SCENE_MODEL = "llama-3.3-70b-versatile"


def _list_scenes(hass: HomeAssistant) -> list[dict]:
    """Return [{entity_id, name, area}] for every scene.* in HA."""
    scenes = []
    for state in hass.states.async_all("scene"):
        name = state.attributes.get("friendly_name", state.entity_id)
        scenes.append({
            "entity_id": state.entity_id,
            "name":      name,
        })
    return scenes


async def async_activate_by_intent(
    hass: HomeAssistant,
    call: ServiceCall,
    groq_client,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
) -> dict:
    """
    Service: jarvis.scene_by_intent
    Pick and activate the scene that best matches the natural-language intent.
    """
    intent: str = call.data["intent"]
    announce = call.data.get("announce", True)

    scenes = _list_scenes(hass)
    if not scenes:
        msg = f"No scenes are defined, {honorific}. I have nothing to choose from."
        if announce:
            await async_announce(hass, msg, tts_entity, speakers)
        return {"success": False, "error": "no_scenes"}

    # Ask Groq to pick the best match
    scene_list = "\n".join(f"- {s['entity_id']}: {s['name']}" for s in scenes)
    now = datetime.now().strftime("%A, %-I:%M %p")
    system = (
        "You are a smart home assistant that picks the best Home Assistant scene "
        "for a user's stated intent. You have a list of available scenes, the "
        "current time, and the user's intent. Return ONLY the scene's entity_id "
        "(the full 'scene.xxx' format) and nothing else — no explanation, no "
        "punctuation, no quotes. If no scene fits, return the word 'none'."
    )
    user = (
        f"Current time: {now}\n\n"
        f"Available scenes:\n{scene_list}\n\n"
        f"User intent: {intent}\n\n"
        f"Best matching scene (entity_id only):"
    )

    try:
        result = await hass.async_add_executor_job(
            lambda: groq_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=40,
                temperature=0.2,
            )
        )
        pick = result["text"].strip().lower()
    except Exception as exc:
        _LOGGER.error("JARVIS scene-pick error: %s", exc)
        return {"success": False, "error": str(exc)}

    # Validate pick
    valid_ids = {s["entity_id"] for s in scenes}
    if pick == "none" or pick not in valid_ids:
        # Sometimes LLM returns extra whitespace or quotes — try to recover
        pick_clean = pick.strip('\'".,` ').split()[0] if pick else ""
        if pick_clean not in valid_ids:
            msg = f"Nothing quite matches that intent, {honorific}."
            if announce:
                await async_announce(hass, msg, tts_entity, speakers)
            return {"success": False, "error": "no_match", "pick": pick}
        pick = pick_clean

    # Activate
    try:
        await hass.services.async_call(
            "scene", "turn_on", {"entity_id": pick}, blocking=True
        )
    except Exception as exc:
        _LOGGER.error("JARVIS scene activation error: %s", exc)
        return {"success": False, "error": str(exc)}

    scene_name = next((s["name"] for s in scenes if s["entity_id"] == pick), pick)
    msg = f"Activating {scene_name}, {honorific}."
    if announce:
        await async_announce(hass, msg, tts_entity, speakers)

    _LOGGER.info("JARVIS: scene '%s' activated for intent '%s'", pick, intent)
    return {"success": True, "scene": pick, "scene_name": scene_name}
