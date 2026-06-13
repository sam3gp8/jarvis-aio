"""
JARVIS — Camera image analysis via Groq vision.

Smart about camera sources:
  - Frigate cameras → pulls event snapshot (high-res, cropped to detection)
    falls back to /latest.jpg if no recent event, then to standard snapshot
  - Nest cameras (Google Home-migrated, WebRTC) → uses event_media endpoint
    when a recent doorbell/motion event is available
  - Any other camera → standard async_get_image()

Also provides push-based auto-analysis helpers used by automations:
  - async_analyze_on_doorbell: listens for nest_event / doorbell events
  - async_analyze_on_frigate: listens for frigate/events MQTT new-person events
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from datetime import datetime
from typing import Optional

import aiohttp

from homeassistant.core import HomeAssistant, ServiceCall, Event, callback
from homeassistant.components.camera import async_get_image as camera_get_image
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

from .camera_backends import find_backend
from .const import JARVIS_PERSONA, DOMAIN
from .database import save_message
from .directive_helper import build_system_prompt
from .tts_helper import async_announce

try:
    from .recognition import last_seen_at, recognition_context_string
    _RECOGNITION_AVAILABLE = True
except ImportError:
    _RECOGNITION_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

VISION_MODEL = "llama-3.2-11b-vision-preview"

# Minimum JPEG size — anything smaller is almost certainly a black/broken frame
MIN_IMAGE_SIZE = 2_000  # ~2 KB

# Cache of recent nest/frigate events keyed by camera entity_id
# {entity_id: {"event_id": str, "device_id": str, "ts": datetime, "source": "nest"|"frigate"}}
_EVENT_CACHE: dict[str, dict] = {}
EVENT_FRESH_SECONDS = 120  # events older than this are considered stale


# ─── Configurable model resolution (vision + camera-reasoning) ───────────────
def _camera_entry(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries(DOMAIN):
        return entry
    return None


def _cfg_opt(hass: HomeAssistant, key: str, default=None):
    """Runtime-aware config read (runtime_config → options → data → default)."""
    entry = _camera_entry(hass)
    if entry is None:
        return default
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
    if key in rc:
        return rc[key]
    return entry.options.get(key, entry.data.get(key, default))


def _make_client(hass: HomeAssistant, provider: str, model: str, fallback):
    """
    Create an LLM provider for the given provider/model from current config.
    Returns `fallback` if creation isn't possible (missing key, error) so the
    camera pipeline degrades gracefully rather than failing.
    """
    try:
        if not provider or not model:
            return fallback
        if provider == "gemini":
            api_key = _cfg_opt(hass, "gemini_api_key", "") or ""
        else:
            api_key = _cfg_opt(hass, "api_key", "") or _cfg_opt(hass, "groq_api_key", "") or ""
        if not api_key:
            return fallback
        base_url = _cfg_opt(hass, "llm_base_url", "") or None
        from .llm_provider import create_provider
        return create_provider(provider, api_key, model, base_url)
    except Exception as exc:
        _LOGGER.warning(
            "camera: could not create %s/%s provider (%s) — using fallback",
            provider, model, exc,
        )
        return fallback


def _parse_json_obj(raw: str):
    """Best-effort extraction of a JSON object from an LLM response."""
    import json as _json
    import re
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s).rstrip("`").strip()
    try:
        return _json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(0))
            except Exception:
                return None
    return None


def _guess_detection_type(prompt: str, analysis: str) -> str:
    al = (analysis or "").lower()
    if "person" in al or "someone" in al or "individual" in al:
        return "person"
    if "vehicle" in al or "car" in al or "truck" in al:
        return "vehicle"
    if "doorbell" in (prompt or "").lower() or "package" in al:
        return "doorbell"
    return "motion"


async def _reason_about_scene(
    hass: HomeAssistant,
    reasoning_client,
    reasoning_model: str,
    camera_name: str,
    description: str,
    det_type: str,
) -> dict:
    """
    Camera-reasoning step: interpret a raw vision description into a judgment.
    Returns {notable: bool, category: str, summary: str, speak: str|None}.

    On any failure this falls back to treating the event as notable and speaking
    the raw description — it must never silently drop a real event due to a
    reasoning error.
    """
    now = datetime.now().strftime("%A %I:%M %p")
    context = ""
    try:
        from . import observer
        context = observer.get_recent_context(600) or ""
    except Exception:
        pass

    system = (
        "You are JARVIS's camera-reasoning module. You are given a description of "
        "what a camera saw. Decide whether it is notable enough to mention to the "
        "resident. Routine or benign activity — a recognised resident arriving, an "
        "empty scene, a car passing on the public street — is NOT notable. Notable: "
        "an unrecognised person approaching or lingering, a delivery or package, "
        "mail, someone at an unusual hour, property damage, an animal where it "
        "shouldn't be, or anything that genuinely warrants attention. Be conservative "
        "about what you flag. Respond with ONLY a JSON object: "
        '{"notable": true|false, '
        '"category": "delivery|package|mail|person|known_resident|vehicle|animal|empty|other", '
        '"summary": "<one concise factual sentence for the log>", '
        '"speak": "<exactly what JARVIS should say aloud, in his voice, or empty string if not notable>"}'
    )
    user = (
        f"Camera: {camera_name}\n"
        f"Time: {now}\n"
        f"Detection: {det_type}\n"
        f"Vision description: {description}\n"
        + (f"\nRecent home activity:\n{context}" if context and context != "quiet — no notable recent activity" else "")
    )
    try:
        result = await hass.async_add_executor_job(
            lambda: reasoning_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=220,
                temperature=0.3,
                model_override=reasoning_model or None,
            )
        )
        data = _parse_json_obj((result.get("text") or "").strip())
        if isinstance(data, dict):
            speak = str(data.get("speak", "") or "").strip()
            return {
                "notable": bool(data.get("notable", True)),
                "category": str(data.get("category", det_type) or det_type),
                "summary": (str(data.get("summary", "") or description))[:300],
                "speak": speak or None,
            }
    except Exception as exc:
        _LOGGER.warning("camera: reasoning step failed (%s) — falling back", exc)

    # Fallback: never drop a real event silently.
    return {"notable": True, "category": det_type, "summary": description, "speak": description}


# ─── Utility: detect camera integration ──────────────────────────────────────

def _camera_integration(hass: HomeAssistant, entity_id: str) -> str:
    """
    Return 'frigate', 'nest', or 'other' based on which integration owns the
    camera entity.
    """
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None:
        return "other"
    if entry.platform == "frigate":
        return "frigate"
    if entry.platform == "nest":
        return "nest"
    return "other"


def _camera_friendly_name(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state:
        name = state.attributes.get("friendly_name")
        if name:
            return name
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry:
        return entry.name or entry.original_name or entity_id
    return entity_id


# ─── Frigate-specific fetch ──────────────────────────────────────────────────

def _frigate_camera_name(entity_id: str) -> str:
    """
    Frigate entities are named like 'camera.front_door'. Frigate's API uses the
    camera name without the 'camera.' prefix and with original casing. HA
    lowercases entity IDs, but Frigate is case-insensitive on most endpoints.
    """
    return entity_id.split(".", 1)[-1]


async def _fetch_frigate_image(
    hass: HomeAssistant, entity_id: str
) -> Optional[bytes]:
    """
    Try Frigate's higher-quality endpoints in order:
      1. Recent event snapshot (cropped to detection, best quality)
      2. Latest full frame
      3. None — caller should fall back to HA's standard snapshot

    Uses HA's internal URL. The /api/frigate/notifications/... endpoint is
    public (no auth). The /api/frigate/{cam}/latest.jpg endpoint requires
    the Frigate integration to be configured with unauthenticated access
    or we need to fall through to the default snapshot.
    """
    session = async_get_clientsession(hass)
    try:
        base_url = get_url(hass, allow_internal=True, prefer_external=False)
    except Exception:
        base_url = "http://127.0.0.1:8123"

    camera = _frigate_camera_name(entity_id)

    # 1. Try cached event snapshot — best quality, event-cropped, public endpoint
    cached = _EVENT_CACHE.get(entity_id)
    if cached and cached.get("source") == "frigate":
        age = (datetime.utcnow() - cached["ts"]).total_seconds()
        if age < EVENT_FRESH_SECONDS:
            event_id = cached.get("event_id")
            if event_id:
                url = f"{base_url}/api/frigate/notifications/{event_id}/snapshot.jpg"
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if len(data) > MIN_IMAGE_SIZE:
                                _LOGGER.debug(
                                    "JARVIS: got Frigate event snapshot (%d bytes)",
                                    len(data),
                                )
                                return data
                        else:
                            _LOGGER.debug(
                                "JARVIS: Frigate event snapshot returned %d", resp.status
                            )
                except Exception as exc:
                    _LOGGER.debug("JARVIS: Frigate event snapshot failed: %s", exc)

    return None

    return None


# ─── Nest event-media fetch ──────────────────────────────────────────────────

async def _fetch_nest_event_image(
    hass: HomeAssistant, entity_id: str
) -> Optional[bytes]:
    """
    Fetch the event snapshot from the Nest integration's event_media endpoint.
    Only works if a recent doorbell/motion event is cached.

    Uses the HTTP API with no auth headers — inside HA core, the aiohttp
    session shares the same auth context when hitting 127.0.0.1. If that
    fails (which it often does for authenticated endpoints), tries the
    Nest integration's Python API directly.
    """
    cached = _EVENT_CACHE.get(entity_id)
    if not cached or cached.get("source") != "nest":
        return None

    age = (datetime.utcnow() - cached["ts"]).total_seconds()
    if age >= EVENT_FRESH_SECONDS:
        _LOGGER.debug("JARVIS: Nest event too old (%ds) for %s", int(age), entity_id)
        return None

    device_id = cached.get("device_id")
    event_id  = cached.get("event_id")
    if not device_id or not event_id:
        return None

    # Preferred: use the Nest integration's in-process API.
    # The integration stores its data at hass.data["nest"].
    try:
        nest_data = hass.data.get("nest")
        if nest_data:
            # Iterate subscribers to find device event media
            for sub in getattr(nest_data, "subscribers", []):
                try:
                    device = sub.device_manager.devices.get(device_id)
                    if device:
                        # Newer Nest integration exposes event media via MediaStore
                        media_store = getattr(sub, "media_store", None)
                        if media_store:
                            media = await media_store.async_get_media(device_id, event_id)
                            if media and hasattr(media, "contents"):
                                data = media.contents
                                if data and len(data) > MIN_IMAGE_SIZE:
                                    _LOGGER.debug(
                                        "JARVIS: got Nest media via Python API (%d bytes)",
                                        len(data),
                                    )
                                    return data
                except Exception as inner_exc:
                    _LOGGER.debug("JARVIS: Nest Python API error: %s", inner_exc)
    except Exception as exc:
        _LOGGER.debug("JARVIS: Nest hass.data access failed: %s", exc)

    _LOGGER.debug(
        "JARVIS: Nest event media not retrievable via in-process API — "
        "live snapshot fallback will be attempted."
    )
    return None


# ─── Unified snapshot function ───────────────────────────────────────────────

async def _prewarm_stream(hass: HomeAssistant, entity_id: str, settle: float = 2.5) -> bool:
    """
    Wake an on-demand stream so a live frame can be grabbed. Nest (and other
    WebRTC/RTSP) cameras don't keep a stream running when nobody is viewing —
    that's the "click the camera first" behaviour. Requesting the stream here
    mirrors that click. Best-effort: any failure is non-fatal.
    """
    try:
        from homeassistant.components import camera as ha_camera
        req = getattr(ha_camera, "async_request_stream", None)
        if req is not None:
            try:
                await req(hass, entity_id, fmt="hls")
            except TypeError:
                await req(hass, entity_id, "hls")  # older positional signature
            await asyncio.sleep(settle)
            return True
        # Fallback: resolving the stream source can be enough to spin it up.
        src = getattr(ha_camera, "async_get_stream_source", None)
        if src is not None:
            url = await src(hass, entity_id)
            if url:
                await asyncio.sleep(settle)
                return True
    except Exception as exc:
        _LOGGER.debug("JARVIS: stream pre-warm failed for %s: %s", entity_id, exc)
    return False


def _looks_blank(jpeg_bytes: bytes) -> bool:
    """
    Best-effort detection of a near-uniform black frame — what an idle Nest
    WebRTC stream tends to return. Uses Pillow if available; if it isn't, we
    can't tell and return False (never reject on this basis). Thresholds are
    conservative so a genuinely dark night scene (which still has sensor noise
    / variance) is NOT treated as blank.
    """
    try:
        import io
        from PIL import Image, ImageStat
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
        stat = ImageStat.Stat(img)
        return stat.mean[0] < 12 and stat.stddev[0] < 8
    except Exception:
        return False


async def _get_best_image(hass: HomeAssistant, entity_id: str) -> Optional[bytes]:
    """
    Try the best source for this camera type, fall back to standard snapshot.
    Returns raw JPEG bytes or None if nothing worked.

    Uses the camera_backends registry so new camera systems (UniFi, Reolink,
    Blue Iris, anything future) can be added without modifying this function.
    """
    # 1. Try the appropriate specialised backend (Frigate, Nest, etc.)
    backend = find_backend(hass, entity_id)
    if backend:
        try:
            data = await backend.fetch_best_image(hass, entity_id, _EVENT_CACHE)
            if data:
                _LOGGER.debug(
                    "JARVIS: got image via backend '%s' (%d bytes)",
                    backend.name, len(data),
                )
                return data
            _LOGGER.debug(
                "JARVIS: backend '%s' returned no image — falling back to standard snapshot",
                backend.name,
            )
        except Exception as exc:
            _LOGGER.debug(
                "JARVIS: backend '%s' error: %s — falling back",
                backend.name, exc,
            )

    # 2. Standard HA snapshot. On-demand streams (Nest WebRTC) return black when
    #    idle, so: grab → if blank/too-small, wake the stream and retry once.
    def _usable(img) -> Optional[bytes]:
        c = img.content if (img and img.content) else b""
        if len(c) > MIN_IMAGE_SIZE and not _looks_blank(c):
            return c
        return None

    image = None
    try:
        image = await camera_get_image(hass, entity_id, timeout=10)
    except Exception as exc:
        _LOGGER.debug("JARVIS: first snapshot attempt failed for %s: %s", entity_id, exc)

    good = _usable(image) if image else None
    if good:
        return good

    # Blank, empty, or errored — wake the on-demand stream and try once more.
    _LOGGER.info(
        "JARVIS: %s snapshot blank/empty — waking stream and retrying", entity_id,
    )
    await _prewarm_stream(hass, entity_id)
    try:
        image = await camera_get_image(hass, entity_id, timeout=15)
        good = _usable(image) if image else None
        if good:
            return good
        # Still unusable. Returning a black frame makes the vision model
        # hallucinate "obstructed", so signal a clean failure instead.
        size = len(image.content) if (image and image.content) else 0
        _LOGGER.warning(
            "JARVIS: %s still blank/empty after stream wake (%d bytes) — "
            "on-demand WebRTC camera with no active stream?",
            entity_id, size,
        )
    except Exception as exc:
        _LOGGER.error("JARVIS: snapshot retry failed for %s: %s", entity_id, exc)

    return None


# ─── Multi-frame (temporal) capture ──────────────────────────────────────────

def _downscale_jpeg(jpeg_bytes: bytes, max_dim: int = 1024) -> bytes:
    """Shrink a frame so its longest side is <= max_dim, cutting vision tokens.
    Uses Pillow if available; returns the original bytes if it isn't or on error."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg_bytes))
        w, h = img.size
        if max(w, h) <= max_dim:
            return jpeg_bytes
        scale = max_dim / float(max(w, h))
        img = img.convert("RGB").resize((max(1, int(w * scale)), max(1, int(h * scale))))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return jpeg_bytes


async def _capture_frame_sequence(
    hass: HomeAssistant,
    entity_id: str,
    count: int = 3,
    interval: float = 1.2,
    max_dim: int = 1024,
) -> list[bytes]:
    """
    Capture up to `count` usable frames spaced `interval`s apart for temporal
    analysis. Prewarms the stream once so on-demand (WebRTC) feeds aren't black
    on the first grab, rejects blank/too-small frames, and downscales each to
    keep tokens low. If the stream never yields a clean sequence, falls back to
    the single best image so callers always get at least one frame when possible.
    """
    frames: list[bytes] = []
    try:
        await _prewarm_stream(hass, entity_id)
    except Exception:
        pass
    count = max(1, count)
    for i in range(count):
        try:
            img = await camera_get_image(hass, entity_id, timeout=10)
            c = img.content if (img and img.content) else b""
            if len(c) > MIN_IMAGE_SIZE and not _looks_blank(c):
                frames.append(_downscale_jpeg(c, max_dim))
        except Exception as exc:
            _LOGGER.debug("JARVIS: frame %d capture failed for %s: %s", i, entity_id, exc)
        if i < count - 1:
            await asyncio.sleep(interval)
    if not frames:
        best = await _get_best_image(hass, entity_id)
        if best:
            frames.append(_downscale_jpeg(best, max_dim))
    _LOGGER.debug("JARVIS: captured %d frame(s) from %s", len(frames), entity_id)
    return frames


def _make_contact_sheet(frames: list[bytes], max_cols: int = 2,
                        cell_max: int = 512) -> Optional[bytes]:
    """
    Tile sequential frames into ONE labelled image (Frame 1, Frame 2, … in
    chronological order). This lets temporal analysis work with ANY vision
    provider — including single-image models like Groq — and uses far fewer
    tokens than sending each frame separately. Returns None if Pillow is
    unavailable, so the caller can fall back to multiple image parts.
    """
    if len(frames) < 2:
        return None
    try:
        import io
        from PIL import Image, ImageDraw
        imgs = []
        for fb in frames:
            try:
                imgs.append(Image.open(io.BytesIO(fb)).convert("RGB"))
            except Exception:
                pass
        if len(imgs) < 2:
            return None
        cells = []
        for im in imgs:
            w, h = im.size
            scale = cell_max / float(max(w, h))
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            cells.append(im)
        n = len(cells)
        cols = min(max_cols, n)
        rows = (n + cols - 1) // cols
        cw = max(im.width for im in cells)
        ch = max(im.height for im in cells)
        pad, label_h = 6, 18
        sheet = Image.new(
            "RGB",
            (cols * cw + (cols + 1) * pad, rows * (ch + label_h) + (rows + 1) * pad),
            (16, 16, 20),
        )
        draw = ImageDraw.Draw(sheet)
        for idx, im in enumerate(cells):
            r, c = divmod(idx, cols)
            x = pad + c * (cw + pad)
            y = pad + r * (ch + label_h + pad)
            draw.text((x + 2, y + 2), f"Frame {idx + 1}", fill=(120, 200, 255))
            sheet.paste(im, (x, y + label_h))
        out = io.BytesIO()
        sheet.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


# ─── Main service ────────────────────────────────────────────────────────────

async def async_analyze_camera(
    hass: HomeAssistant,
    call: ServiceCall,
    groq_client,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
    gate_announce: bool = False,
    force_images: Optional[list] = None,
) -> dict:
    """
    Service: jarvis.analyze_camera
    Captures the best snapshot, sends it to the configured VISION model, then
    runs a CAMERA-REASONING step that interprets the scene (notable? what is it?)
    and incorporates the result into JARVIS's awareness. When gate_announce is
    True (auto/event-triggered reviews), only notable scenes are spoken; manual
    service calls always report.
    """
    entity_id: str = call.data["entity_id"]
    prompt: str    = call.data.get("prompt", "Describe what you see. Note anything unusual or worth attention.")
    announce: bool = call.data.get("announce", True)

    camera_name = _camera_friendly_name(hass, entity_id)
    integration = _camera_integration(hass, entity_id)

    _LOGGER.info("JARVIS: analyzing %s (%s, source=%s)", entity_id, camera_name, integration)

    # Snapshot (default) or a short multi-frame clip for temporal understanding.
    # force_images lets a caller supply pre-fetched frames (e.g. doorbell event
    # media that already captured the subject) and skip live capture entirely.
    clip_frames = max(1, int(call.data.get("frames", 1) or 1))
    clip_interval = float(call.data.get("interval", 1.2) or 1.2)
    if force_images:
        frame_bytes = [fb for fb in force_images if fb]
    elif clip_frames > 1:
        frame_bytes = await _capture_frame_sequence(
            hass, entity_id, count=clip_frames, interval=clip_interval)
    else:
        one = await _get_best_image(hass, entity_id)
        frame_bytes = [one] if one else []

    if not frame_bytes:
        msg = (
            f"{honorific}, I could not get a usable image from {camera_name}. "
            f"This camera may use WebRTC (no snapshot support) or be offline."
        )
        _LOGGER.error("JARVIS: no usable image for %s", entity_id)
        try:
            from .websocket import jarvis_log
            jarvis_log("CAMERA", f"{camera_name}: no usable image (WebRTC/offline?)")
        except Exception:
            pass
        if announce:
            await async_announce(hass, msg, tts_entity, speakers)
        return {"success": False, "error": "no_image", "camera": camera_name}

    # Temporal clip: prefer a single labelled contact sheet (works with any
    # vision provider, fewer tokens); fall back to multiple image parts.
    is_clip = len(frame_bytes) > 1
    sheet = _make_contact_sheet(frame_bytes) if is_clip else None
    if sheet is not None:
        images_b64 = [base64.b64encode(sheet).decode()]
    else:
        images_b64 = [base64.b64encode(fb).decode() for fb in frame_bytes]

    # ── Incorporate face recognition (if we know who's on camera) ──────────
    recognition_hint = ""
    if _RECOGNITION_AVAILABLE:
        try:
            last = last_seen_at(hass, entity_id)
            if last and last.get("is_confident", False) is not False:
                name = last.get("name", "")
                confidence = last.get("confidence", 0)
                age = last.get("age_seconds", 999)
                if name and name.lower() != "unknown" and confidence >= 60 and age < 60:
                    recognition_hint = (
                        f"\n\nIMPORTANT: Face recognition has identified the person "
                        f"in frame as '{name}' (confidence {confidence:.0f}%). "
                        f"Refer to them by name in your description."
                    )
        except Exception:
            pass

    # ── Vision call ───────────────────────────────────────────────────────────
    if is_clip:
        seq = ("a single contact sheet whose tiles are labelled Frame 1, Frame 2, … "
               "in chronological order" if sheet is not None
               else f"{len(images_b64)} sequential frames in chronological order")
        task = (
            f"You are analysing {seq} from the camera feed '{camera_name}', captured "
            f"about {clip_interval:.0f}s apart. Describe what HAPPENS across the frames "
            f"— motion, who or what appears or leaves, packages set down or removed, "
            f"direction of travel. If the scene is static, say so in a few words. "
            f"Under 90 words.{recognition_hint}"
        )
    else:
        task = (
            f"You are analysing the camera feed '{camera_name}'. "
            f"Describe what you see clearly and concisely — as JARVIS would. "
            f"Note specific details: people, vehicles, packages, unusual activity. "
            f"Under 80 words unless something truly warrants more detail."
            f"{recognition_hint}"
        )
    system = build_system_prompt(hass, honorific, task)
    vision_provider = _cfg_opt(hass, "vision_provider", "groq") or "groq"
    vision_model = _cfg_opt(hass, "vision_model", VISION_MODEL) or VISION_MODEL
    vision_client = _make_client(hass, vision_provider, vision_model, groq_client)
    try:
        result = await hass.async_add_executor_job(
            lambda: vision_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": (
                            [
                                {"type": "image_url",
                                 "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
                                for b in images_b64
                            ]
                            + [{"type": "text", "text": prompt}]
                        ),
                    },
                ],
                max_tokens=300,
                model_override=vision_model or None,
            )
        )
        analysis = result["text"].strip()
    except Exception as exc:
        _LOGGER.error("JARVIS vision error (%s/%s): %s", vision_provider, vision_model, exc)
        try:
            from .websocket import jarvis_log
            jarvis_log("CAMERA", f"{camera_name}: vision error ({vision_provider}/{vision_model}) — {exc}")
        except Exception:
            pass
        return {"success": False, "error": str(exc)}

    # ── Camera-reasoning step: interpret the scene ────────────────────────────
    det_type = _guess_detection_type(prompt, analysis)
    rsn_provider = _cfg_opt(hass, "camera_reasoning_provider", "groq") or "groq"
    rsn_model = _cfg_opt(hass, "camera_reasoning_model", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile"
    rsn_client = _make_client(hass, rsn_provider, rsn_model, groq_client)
    judgment = await _reason_about_scene(
        hass, rsn_client, rsn_model, camera_name, analysis, det_type,
    )
    summary = judgment["summary"]

    # Surface the review in the JARVIS Logs tab (this is what the Diagnostics
    # "check Logs for the result" toast refers to).
    try:
        from .websocket import jarvis_log
        flag = " ⚠NOTABLE" if judgment["notable"] else ""
        jarvis_log("CAMERA", f"{camera_name} [{judgment['category']}]{flag}: {summary}")
    except Exception:
        pass

    # ── Incorporate into the rest of JARVIS ───────────────────────────────────
    # Memory (queryable later)
    await hass.async_add_executor_job(
        save_message, "assistant", f"[Camera: {camera_name}] {summary}", "camera"
    )
    # Observer awareness buffer (feeds briefings & "what happened outside")
    try:
        from . import observer
        observer.record_camera_event(
            camera_name, summary, judgment["category"], judgment["notable"],
        )
    except Exception:
        pass
    # Proactive-briefing snapshot record
    try:
        from .proactive_briefing import record_snapshot
        record_snapshot(
            camera_name=camera_name,
            camera_entity=entity_id,
            analysis=summary,
            detection_type=judgment["category"],
            urgency="medium" if judgment["notable"] else "low",
        )
    except Exception:
        pass

    # ── Announce — gated by notability for auto/event reviews ─────────────────
    spoke = False
    if announce:
        if judgment["notable"] and judgment.get("speak"):
            await async_announce(hass, judgment["speak"], tts_entity, speakers)
            spoke = True
        elif not gate_announce:
            # Manual analyze request on a non-notable scene — still report it.
            await async_announce(hass, analysis, tts_entity, speakers)
            spoke = True
        # else: auto event + not notable → stay silent

    _LOGGER.info(
        "JARVIS: %s → notable=%s cat=%s | %s",
        camera_name, judgment["notable"], judgment["category"], summary[:80],
    )
    return {
        "success": True,
        "analysis": analysis,
        "summary": summary,
        "notable": judgment["notable"],
        "category": judgment["category"],
        "speak": judgment.get("speak"),
        "spoke": spoke,
        "camera": camera_name,
        "source": integration,
        "image_bytes": sum(len(fb) for fb in frame_bytes),
    }


# ─── Event listeners for push-based analysis ─────────────────────────────────

def _nest_device_to_camera(hass: HomeAssistant, nest_device_id: str) -> Optional[str]:
    """Look up which camera entity belongs to this Nest device ID."""
    try:
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        # Find the HA device whose identifiers contain the nest_device_id
        target_device_id: Optional[str] = None
        for device in dev_reg.devices.values():
            for identifier_tuple in device.identifiers:
                # identifier_tuple is (domain, identifier_str)
                if len(identifier_tuple) >= 2 and nest_device_id in str(identifier_tuple[1]):
                    if identifier_tuple[0] == "nest":
                        target_device_id = device.id
                        break
            if target_device_id:
                break

        if not target_device_id:
            return None

        # Find the camera entity belonging to that device
        for ent in ent_reg.entities.values():
            if ent.device_id == target_device_id and ent.domain == "camera":
                return ent.entity_id
    except Exception as exc:
        _LOGGER.debug("JARVIS: _nest_device_to_camera error: %s", exc)

    return None


@callback
def _handle_nest_event(hass: HomeAssistant, event: Event) -> None:
    """
    Cache nest_event data so subsequent analyze_camera calls can fetch the
    high-res event-media image.
    """
    try:
        data = event.data
        device_id = data.get("device_id") or data.get("nest_device_id")
        event_id  = data.get("nest_event_id") or data.get("event_id")
        if not device_id or not event_id:
            return

        entity_id = _nest_device_to_camera(hass, device_id)
        if not entity_id:
            return

        _EVENT_CACHE[entity_id] = {
            "event_id": event_id,
            "device_id": device_id,
            "ts": datetime.utcnow(),
            "source": "nest",
        }
        _LOGGER.debug("JARVIS: cached Nest event %s for %s", event_id, entity_id)
    except Exception as exc:
        _LOGGER.debug("JARVIS: error caching Nest event: %s", exc)


@callback
def _handle_frigate_event(hass: HomeAssistant, event: Event) -> None:
    """
    Cache Frigate event data. Frigate publishes events via MQTT on
    frigate/events with payload containing type (new/update/end) and data.
    HA's MQTT integration fires state changes on frigate/events sensors.
    """
    try:
        data = event.data
        # Frigate events payload structure
        if data.get("type") == "new":
            after = data.get("after") or data.get("before") or {}
            camera_name = after.get("camera")
            event_id    = after.get("id")
            if not camera_name or not event_id:
                return

            entity_id = f"camera.{camera_name.lower()}"
            if not hass.states.get(entity_id):
                return

            _EVENT_CACHE[entity_id] = {
                "event_id": event_id,
                "device_id": camera_name,
                "ts": datetime.utcnow(),
                "source": "frigate",
            }
            _LOGGER.debug("JARVIS: cached Frigate event %s for %s", event_id, entity_id)
    except Exception as exc:
        _LOGGER.debug("JARVIS: error caching Frigate event: %s", exc)


def register_event_listeners(hass: HomeAssistant) -> list:
    """
    Register HA event listeners that populate _EVENT_CACHE for
    push-based camera analysis. Returns a list of unsub callables.
    """
    unsubs = []

    # Nest — the integration fires events like 'nest_event' or device triggers
    try:
        unsubs.append(hass.bus.async_listen("nest_event", lambda e: _handle_nest_event(hass, e)))
    except Exception as exc:
        _LOGGER.debug("JARVIS: could not subscribe to nest_event: %s", exc)

    # Frigate — events are on the MQTT bus; HA re-fires them as 'frigate_event'
    # but the most reliable source is the bus event from the Frigate HA integration
    try:
        unsubs.append(hass.bus.async_listen("frigate_event", lambda e: _handle_frigate_event(hass, e)))
    except Exception as exc:
        _LOGGER.debug("JARVIS: could not subscribe to frigate_event: %s", exc)

    _LOGGER.info("JARVIS: camera event listeners registered (%d subscriptions)", len(unsubs))
    return unsubs


# ─── Auto-analyze helpers for automation-less use ────────────────────────────

class _FakeCall:
    """Minimal ServiceCall stand-in for internal async_analyze_camera calls."""
    def __init__(self, data: dict):
        self.data = data


_SUBJECT_PAT = re.compile(
    r"\b(person|people|man|woman|men|women|someone|somebody|individual|child|"
    r"children|kid|figure|delivery|courier|mail\s*carrier|package|parcel|box|"
    r"vehicle|car|truck|van|suv|motorcyc|bicycle|dog|cat|animal|visitor|"
    r"stranger|guest|face)\b", re.I,
)
_NO_SUBJECT_PAT = re.compile(
    r"\b(no one|no-one|nobody|no person|no people|empty|deserted|unoccupied|"
    r"nothing notable|nothing of note|no visible|no activity|no movement|"
    r"appears? (?:empty|clear|quiet|still)|static scene|just (?:the )?(?:yard|"
    r"street|driveway|grass|lawn|porch))\b", re.I,
)


def _scene_has_subject(analysis: str) -> bool:
    """
    Heuristic: did the vision pass actually capture a subject (person/vehicle/
    package/animal) rather than an empty scene? Used to decide whether to fall
    back to the recorded event media for a doorbell press where the live frames
    may show an already-empty doorway.
    """
    if not analysis:
        return False
    if _NO_SUBJECT_PAT.search(analysis) and not _SUBJECT_PAT.search(analysis):
        return False
    return bool(_SUBJECT_PAT.search(analysis))


async def _analyze_doorbell_press(
    hass: HomeAssistant,
    groq_client,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
    entity_id: str,
    reason: str,
) -> dict:
    """
    Doorbell PRESS analysis. Someone deliberately rang, so capture matters.

    Strategy: analyse the live clip first (it shows the *current* state — the
    visitor may still be standing there). If that clip caught no subject — the
    person stepped away in the seconds before capture — fall back to the most
    recent recorded EVENT MEDIA, which froze the subject at the moment of the
    press. Whichever pass actually found someone is the one announced (gated on
    notability) and logged for training. Neither pass announces on its own; the
    announcement is issued once, here, for the chosen result.
    """
    prompt = (
        f"{reason}. Someone is at the door. Identify who is there and what they "
        f"are doing — appearance, clothing, whether they carry a package or wait, "
        f"any vehicle behind them. Focus on what {honorific} would want to know."
    )

    # Pass 1 — live clip (announce suppressed; we decide below)
    live_call = _FakeCall({
        "entity_id": entity_id, "prompt": prompt,
        "announce": False, "frames": 3, "interval": 1.2,
    })
    res = await async_analyze_camera(
        hass, live_call, groq_client, honorific, tts_entity, speakers, gate_announce=True,
    )
    used = "live"

    # Pass 2 — recorded event media fallback when the live clip caught no subject
    if not (res.get("success") and _scene_has_subject(res.get("analysis", ""))):
        ev = await _fetch_nest_event_image(hass, entity_id)
        if ev:
            ev = _downscale_jpeg(ev)
            ev_call = _FakeCall({
                "entity_id": entity_id,
                "prompt": prompt + " (This frame was captured at the moment the doorbell rang.)",
                "announce": False,
            })
            res2 = await async_analyze_camera(
                hass, ev_call, groq_client, honorific, tts_entity, speakers,
                gate_announce=True, force_images=[ev],
            )
            # Prefer the event-media pass if it found a subject, or if the live
            # pass failed outright.
            if res2.get("success") and (
                _scene_has_subject(res2.get("analysis", "")) or not res.get("success")
            ):
                res, used = res2, "event-media"

    # Single, notability-gated announcement for the chosen result
    if res.get("success"):
        if res.get("notable") and res.get("speak"):
            await async_announce(hass, res["speak"], tts_entity, speakers)
            res["spoke"] = True
        # Training data — every analysed press, regardless of whether it spoke
        try:
            from . import doorbell_training
            await hass.async_add_executor_job(
                doorbell_training.log_event,
                _camera_friendly_name(hass, entity_id), entity_id, used, res,
            )
        except Exception as exc:
            _LOGGER.debug("JARVIS: doorbell training log error: %s", exc)
        # Package/mail detection — reuse the press description (no extra vision call)
        try:
            from . import package_monitor
            await package_monitor.note_from_doorbell(
                hass, groq_client, honorific, tts_entity, speakers,
                entity_id, res.get("analysis", ""),
            )
        except Exception as exc:
            _LOGGER.debug("JARVIS: doorbell package note error: %s", exc)

    _LOGGER.info(
        "JARVIS doorbell press on %s analysed (%s frames) → notable=%s",
        entity_id, used, res.get("notable"),
    )
    return res


async def async_visitor_observation(
    hass: HomeAssistant,
    groq_client,
    honorific: str,
    entity_id: str,
) -> None:
    """
    SILENT visitor learning. A person event at the door is analysed for the
    training dataset and the cognitive record — never spoken, never pushed.
    Doorbell-press announcements remain the only voiced camera events; this just
    lets JARVIS quietly learn who comes and goes (couriers' patterns, regulars,
    strangers) now that vision calls cost effectively nothing.
    Prefers the recorded event media (it froze the person), falls back to live.
    """
    await asyncio.sleep(2)
    img = await _fetch_nest_event_image(hass, entity_id)
    if not img:
        img = await _get_best_image(hass, entity_id)
    if not img:
        return
    img = _downscale_jpeg(img)
    fc = _FakeCall({
        "entity_id": entity_id,
        "prompt": (
            "A person was detected at the door (no doorbell press). Briefly note "
            "who: appearance, apparent purpose (delivery, passer-by, approaching, "
            "leaving), any package or vehicle. One or two sentences."
        ),
        "announce": False,
    })
    res = await async_analyze_camera(
        hass, fc, groq_client, honorific, None, [],
        gate_announce=True, force_images=[img],
    )
    if res.get("success"):
        try:
            from . import doorbell_training
            await hass.async_add_executor_job(
                doorbell_training.log_event,
                _camera_friendly_name(hass, entity_id), entity_id, "visitor", res,
            )
        except Exception as exc:
            _LOGGER.debug("JARVIS: visitor learning log error: %s", exc)
        _LOGGER.info("JARVIS visitor observation on %s logged (silent)", entity_id)


async def async_auto_analyze_on_event(
    hass: HomeAssistant,
    groq_client,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
    entity_id: str,
    reason: str = "Motion detected",
    doorbell: bool = False,
) -> None:
    """
    Auto-analyze a camera event. For doorbell presses (doorbell=True) this uses
    the press-specific path with event-media fallback. Otherwise (manual service
    / non-doorbell) it analyses a short live clip with notability-gated announce.
    """
    await asyncio.sleep(3)  # let event propagate & media become fetchable

    if doorbell:
        await _analyze_doorbell_press(
            hass, groq_client, honorific, tts_entity, speakers, entity_id, reason,
        )
        return

    call = _FakeCall({
        "entity_id": entity_id,
        "prompt": (
            f"{reason}. Describe what you see clearly. "
            f"If there is a person, describe their appearance and what they're doing. "
            f"If there is a vehicle or package, note it. "
            f"Focus on what {honorific} would want to know."
        ),
        "announce": True,
        "frames": 3,
        "interval": 1.2,
    })
    await async_analyze_camera(
        hass, call, groq_client, honorific, tts_entity, speakers, gate_announce=True,
    )
