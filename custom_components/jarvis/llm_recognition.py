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

import functools
import json
import logging
import re
import time as _time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Keep the matcher cheap: cap how many reference photos we send in one prompt,
# and how many per resident (lighting-matched) so one resident can't crowd out
# the others (#41 §7A).
_MAX_REFERENCES = 8
_MAX_REFS_PER_RESIDENT = 2

# Shadow calibration: at most one comparison per resident per this interval, so a
# busy camera doesn't spend vision tokens on the same person over and over (§7B).
_SHADOW_CAL_THROTTLE = 900.0   # 15 min
_last_shadow_cal: dict[str, float] = {}


def _enabled() -> bool:
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("llm_face_recognition", False))
    except Exception:
        return False


def _chat(client, **kwargs):
    """Call ``client.chat`` with ``thinking=False`` to suppress hidden reasoning
    tokens on models that honour it (e.g. gemma4:26b), falling back for minimal
    stub clients / signatures that don't accept the kwarg (#41 §7A)."""
    try:
        return client.chat(thinking=False, **kwargs)
    except TypeError:
        return client.chat(**kwargs)


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


def _match_sync(client, model: str, image_b64: str, *,
                prefer_night: Optional[bool] = None) -> Optional[dict]:
    """Read resident references, prompt the vision model, parse a best-effort
    match. Runs on the executor (file I/O + a blocking chat call). Sends up to
    ``_MAX_REFS_PER_RESIDENT`` lighting-matched references per resident, capped at
    ``_MAX_REFERENCES`` images total (#41 §7A)."""
    try:
        import base64
        from . import face_roster, recognition
        residents = face_roster.residents()
        if not residents:
            return None
        # Only residents that actually have a reference photo enrolled; prefer
        # lighting-matched frames from their gallery.
        refs = []
        allowed = {}
        for name in residents:
            if len(refs) >= _MAX_REFERENCES:
                break
            try:
                paths = recognition.face_reference_paths(
                    name, prefer_night=prefer_night, limit=_MAX_REFS_PER_RESIDENT)
            except TypeError:
                # Minimal stub without the multi-sample API → single reference.
                p = recognition.face_reference_path(name)
                paths = [p] if p else []
            got = False
            for path in paths:
                if len(refs) >= _MAX_REFERENCES:
                    break
                try:
                    with open(path, "rb") as f:
                        refs.append((name, base64.b64encode(f.read()).decode()))
                    got = True
                except Exception:
                    continue
            if got:
                allowed[name.lower()] = name
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
        # Unique resident names for the allowed-values list (a resident may have
        # several reference frames above).
        _seen, uniq = set(), []
        for n, _ in refs:
            if n.lower() not in _seen:
                _seen.add(n.lower())
                uniq.append(n)
        names = ", ".join(uniq)
        content.append({"type": "text", "text": (
            f'Reply with ONLY a compact JSON object: {{"name": "<one of: {names} '
            f'| unknown>", "confidence": <integer 0-100>}}. Name a resident only '
            f'if their face clearly matches a reference photo. If there is no '
            f'clear face, or no confident match, use "unknown". Do not guess.')})

        result = _chat(
            client,
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


async def identify(hass, image_b64: str, camera_entity: str, *,
                   prefer_night: Optional[bool] = None) -> Optional[dict]:
    """Best-effort: is the person in ``image_b64`` an enrolled resident?
    Returns {name, confidence} or None. Opt-in and never raises. ``prefer_night``
    selects lighting-matched references; when unset it's derived from the sun."""
    if not image_b64 or not _enabled():
        return None
    try:
        from . import camera, jarvis_config, recognition
        if prefer_night is None:
            try:
                prefer_night = recognition._lighting_bucket(hass) == "night"
            except Exception:
                prefer_night = None
        provider = jarvis_config.get("vision_provider", "groq") or "groq"
        model = jarvis_config.get("vision_model", camera.VISION_MODEL) or camera.VISION_MODEL
        client = await camera.async_make_client(hass, provider, model, None)
        if client is None:
            return None
        return await hass.async_add_executor_job(
            functools.partial(_match_sync, client, model, image_b64,
                              prefer_night=prefer_night))
    except Exception as exc:
        _LOGGER.debug("llm identify failed: %s", exc)
        return None


def _tier1_has_fresh_match(hass, camera_entity: str) -> bool:
    """True when Frigate/DoubleTake already has a fresh (≤60s), confident, named
    match on this camera — so the LLM matcher can stand down and waste no vision
    tokens (Tier 1 always leads, #41 §7A). Best-effort; never raises."""
    try:
        from . import recognition
        last = recognition.last_seen_at(hass, camera_entity)
        if not last:
            return False
        name = last.get("name", "")
        conf = float(last.get("confidence", 0) or 0)
        age = last.get("age_seconds", 999)
        return bool(name and not recognition._is_unknown(name)
                    and conf >= recognition.CONFIDENCE_THRESHOLD
                    and age is not None and age <= 60)
    except Exception:
        return False


async def identify_and_store(hass, image_b64: str, camera_entity: str) -> Optional[dict]:
    """Run :func:`identify` and, on a match, record it as a low-confidence LLM
    guess (separate cache; never feeds intrusion). Returns the match or None.
    Short-circuits with no vision call when Tier 1 already named this camera."""
    if _tier1_has_fresh_match(hass, camera_entity):
        return None
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


async def shadow_calibrate(hass, camera_entity: str, expected_name: str,
                           backend_conf: float, image_bytes: bytes) -> None:
    """Fire-and-forget shadow comparison (#41 §7B): run the LLM matcher on a frame
    that a backend already named confidently, and record whether it agreed, so the
    matcher's accuracy is measured without ever affecting the trusted cache or
    intrusion. Gated on ``llm_face_recognition`` + ``llm_face_autotrain``,
    ``backend_conf >= 85`` and ``expected_name`` being a resident; throttled to at
    most once per resident per ``_SHADOW_CAL_THROTTLE``. Never raises."""
    try:
        if not image_bytes or not _enabled():
            return
        from . import face_roster, jarvis_config, recognition
        if not bool(jarvis_config.get("llm_face_autotrain", False)):
            return
        if float(backend_conf or 0.0) < 85.0 or not face_roster.is_resident(expected_name):
            return
        canon = face_roster.canonical_name(expected_name) or expected_name
        gkey = canon.strip().lower()
        now = _time.time()
        if now - _last_shadow_cal.get(gkey, 0.0) < _SHADOW_CAL_THROTTLE:
            return
        _last_shadow_cal[gkey] = now

        import base64
        b64 = base64.b64encode(image_bytes).decode()
        match = await identify(hass, b64, camera_entity)
        if match is None:
            outcome, llm_conf = "abstained", 0.0
        else:
            same = (face_roster.canonical_name(match.get("name", "")).strip().lower()
                    == canon.strip().lower())
            outcome = "agreed" if same else "wrong_person"
            llm_conf = float(match.get("confidence", 0.0) or 0.0)
        await hass.async_add_executor_job(
            functools.partial(recognition.record_llm_calibration, expected_name,
                              outcome, backend_conf=float(backend_conf or 0.0),
                              llm_conf=llm_conf, source="shadow"))
        _LOGGER.debug("JARVIS: shadow calibration for %s → %s (~%.0f%%)",
                      expected_name, outcome, llm_conf)
    except Exception:
        pass
