"""
JARVIS — best-effort resident recognition via the local vision LLM (#140 Phase 3).

For households with NO dedicated face backend (Frigate/DoubleTake/CompreFace),
only JARVIS's own vision model, this asks that model whether the person on a
camera frame matches an enrolled resident reference photo.

It is a **guess**, not a recognition. LLM face-matching is unreliable for
identity, so the result is written only to ``recognition._LLM_GUESS_CACHE`` (via
``remember_llm_guess``) which the intrusion stand-down (`resident_present`)
never reads — a mis-guess can never disable an alert. It feeds only the Faces
panel, flagged low-confidence.

Opt-in: the ``llm_face_recognition`` config flag (default off). Best-effort
throughout — any failure returns None and never disturbs the vision pipeline
that triggered it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Keep the matcher cheap: cap how many reference photos we send in one prompt.
_MAX_REFERENCES = 8


def _enabled() -> bool:
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("llm_face_recognition", False))
    except Exception:
        return False


def _parse_result(raw: str, allowed: dict) -> Optional[dict]:
    """Extract {name, confidence} from the model reply. ``allowed`` maps
    lowercased resident name → display name. Returns a dict only when the model
    named one of the enrolled residents (not 'unknown'). Never raises."""
    try:
        s = (raw or "").strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\n?", "", s).rstrip("`").strip()
        data = None
        try:
            data = json.loads(s)
        except Exception:
            m = re.search(r"\{.*\}", s, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        if not name or name.lower() in ("unknown", "none", "null", ""):
            return None
        display = allowed.get(name.lower())
        if not display:
            return None  # model named someone who isn't an enrolled resident
        try:
            conf = float(data.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(100.0, conf))
        return {"name": display, "confidence": round(conf, 1)}
    except Exception:
        return None


def _match_sync(client, model: str, image_b64: str) -> Optional[dict]:
    """Read resident references, prompt the vision model, parse a best-effort
    match. Runs on the executor (file I/O + a blocking chat call)."""
    try:
        from . import face_roster, recognition
        residents = face_roster.residents()
        if not residents:
            return None
        # Only residents that actually have a reference photo enrolled.
        refs = []
        allowed = {}
        for name in residents:
            path = recognition.face_reference_path(name)
            if not path:
                continue
            try:
                with open(path, "rb") as f:
                    import base64
                    refs.append((name, base64.b64encode(f.read()).decode()))
                allowed[name.lower()] = name
            except Exception:
                continue
            if len(refs) >= _MAX_REFERENCES:
                break
        if not refs:
            return None

        content = [{
            "type": "text",
            "text": ("You are a face-matching assistant. Below are labelled "
                     "reference photos of known household residents, then a "
                     "current camera frame. Decide if the person in the current "
                     "frame is one of the residents."),
        }]
        for name, b64 in refs:
            content.append({"type": "text", "text": f"Reference photo of {name}:"})
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        content.append({"type": "text", "text": "Current camera frame:"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}})
        names = ", ".join(n for n, _ in refs)
        content.append({"type": "text", "text": (
            f'Reply with ONLY a compact JSON object: {{"name": "<one of: {names} '
            f'| unknown>", "confidence": <integer 0-100>}}. Name a resident only '
            f'if their face clearly matches a reference photo. If there is no '
            f'clear face, or no confident match, use "unknown". Do not guess.')})

        result = client.chat(
            messages=[
                {"role": "system",
                 "content": "Respond with JSON only. Be conservative; prefer 'unknown'."},
                {"role": "user", "content": content},
            ],
            max_tokens=120,
            model_override=model or None,
        )
        return _parse_result((result.get("text") or ""), allowed)
    except Exception as exc:
        _LOGGER.debug("llm face match failed: %s", exc)
        return None


async def identify(hass, image_b64: str, camera_entity: str) -> Optional[dict]:
    """Best-effort: is the person in ``image_b64`` an enrolled resident?
    Returns {name, confidence} or None. Opt-in and never raises."""
    if not image_b64 or not _enabled():
        return None
    try:
        from . import camera, jarvis_config
        provider = jarvis_config.get("vision_provider", "groq") or "groq"
        model = jarvis_config.get("vision_model", camera.VISION_MODEL) or camera.VISION_MODEL
        client = await camera.async_make_client(hass, provider, model, None)
        if client is None:
            return None
        return await hass.async_add_executor_job(_match_sync, client, model, image_b64)
    except Exception as exc:
        _LOGGER.debug("llm identify failed: %s", exc)
        return None


async def identify_and_store(hass, image_b64: str, camera_entity: str) -> Optional[dict]:
    """Run :func:`identify` and, on a match, record it as a low-confidence LLM
    guess (separate cache; never feeds intrusion). Returns the match or None."""
    match = await identify(hass, image_b64, camera_entity)
    if not match:
        return None
    try:
        from . import recognition
        cam = (camera_entity or "").split(".", 1)[-1]
        recognition.remember_llm_guess(cam, match["name"], match["confidence"])
        _LOGGER.info("JARVIS: best-effort LLM face guess — %s on %s (~%.0f%%)",
                     match["name"], camera_entity, match["confidence"])
    except Exception:
        pass
    return match
