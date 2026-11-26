"""
JARVIS — Generic two-way camera audio (talk half: headless TTS → camera speaker).

Issue #552. JARVIS speaks through a camera's speaker (deterrence, intercom,
announcements) without any browser involvement, using go2rtc's backchannel —
the universal media engine Frigate bundles and HA's native WebRTC stack ships.
Because the push runs server-to-server on the LAN (JARVIS → go2rtc → camera), it
works even when the owner is remote over Nabu Casa, where live browser WebRTC
usually fails.

This is the Tier-1 (go2rtc/Frigate) provider and the headless-TTS path only.
Interactive live browser mic (WebRTC `sendrecv`) and the RTSP-listen → STT
direction are separate follow-up tranches; the capability model and config here
are shaped so they can drop in without a rewrite.

Design notes baked in from real-world NVR testing:
  * Capability discovery, never assume 2-way works. Many cameras are listen-only
    (recvonly) — Nest via SDM strictly rejects outbound talk. We probe go2rtc's
    `/api/streams` for an `audio, sendonly` track and refuse talkback on a
    confirmed recvonly camera. An on-demand stream reports `medias: null` while
    idle — that is UNKNOWN, not unsupported, so we still allow a push attempt.
  * Single-backchannel NVR contention. Many multi-channel NVRs expose one RTSP
    backchannel for the whole box; a second session is locked out. We serialise
    pushes per `backchannel_group` (default: the stream name) with an async lock,
    and the TTS clip is a finite source so go2rtc drops the producer at EOF and
    frees the slot — no idle session holds the backchannel hostage.
  * Codec bridging is go2rtc's job. We ask go2rtc to transcode the TTS clip to
    the camera's backchannel codec (`pcma`/`pcmu`/`l16`); we never encode RTP.

Everything HA-specific is imported lazily inside functions so the orchestration
can be unit-tested with the HTTP + TTS-URL layers mocked.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_GO2RTC_BASE_URL = "http://127.0.0.1:1984"
# go2rtc backchannels commonly expect one of these; pcma (G.711 A-law) is the
# safest default and go2rtc transcodes the TTS clip to it.
DEFAULT_BACKCHANNEL_CODEC = "pcma"
_VALID_CODECS = ("pcma", "pcmu", "l16", "opus", "aac")
_HTTP_TIMEOUT = 15


# ── Capability model ─────────────────────────────────────────────────────────

@dataclass
class CameraAudioCapabilities:
    """What a camera's go2rtc stream can do for audio talkback.

    talkback is tri-state on purpose:
      True  — a `sendonly` audio track is present: talkback supported.
      False — the stream has producers but no sendonly audio: recvonly (refuse).
      None  — unknown (stream idle / `medias: null`): allow an attempt, don't hide.
    """
    stream: str
    talkback: Optional[bool]
    codecs: list[str] = field(default_factory=list)
    online: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream": self.stream,
            "talkback": self.talkback,
            "codecs": self.codecs,
            "online": self.online,
            "detail": self.detail,
        }


# ── Config readers ───────────────────────────────────────────────────────────

def _cfg(hass, entry, key: str, default):
    from .websocket import _runtime_opt
    return _runtime_opt(hass, entry, key, default)


def _cfg_json(hass, entry, key: str, default):
    from .websocket import _get_runtime_json
    return _get_runtime_json(hass, entry, key, default)


def audio_enabled(hass, entry) -> bool:
    """Master 'muted-until-requested' gate. Ships OFF: nothing speaks through a
    camera until the owner turns it on in Settings → Cameras."""
    return bool(_cfg(hass, entry, "camera_audio_enabled", False))


def base_url(hass, entry) -> str:
    raw = str(_cfg(hass, entry, "go2rtc_base_url", "") or "").strip()
    return (raw or DEFAULT_GO2RTC_BASE_URL).rstrip("/")


def _entity_name(hass, camera_id: str) -> str:
    """The camera's short name, used as the default go2rtc stream name when the
    owner hasn't mapped one explicitly (Frigate names its go2rtc streams after
    the camera)."""
    if "." in camera_id:
        return camera_id.split(".", 1)[1]
    return camera_id


def stream_for(hass, entry, camera_id: str) -> str:
    """Resolve the go2rtc stream name for a camera. The 2-way stream is often NOT
    the base camera name (owners split a `_2` on-demand stream to dodge the
    single-backchannel trap), so an explicit map wins; else fall back to the
    camera's short name."""
    name = _entity_name(hass, camera_id)
    mapping = _cfg_json(hass, entry, "camera_backchannel_map", {}) or {}
    if isinstance(mapping, dict):
        # accept a map keyed by full entity_id or by short name
        val = mapping.get(camera_id) or mapping.get(name)
        if val:
            return str(val)
    return name


def group_for(hass, entry, camera_id: str) -> str:
    """The backchannel mutex group — cameras sharing one NVR's single backchannel
    should share a group so pushes serialise. Defaults to the stream name (each
    stream independent) when unmapped."""
    name = _entity_name(hass, camera_id)
    groups = _cfg_json(hass, entry, "camera_backchannel_groups", {}) or {}
    if isinstance(groups, dict):
        val = groups.get(camera_id) or groups.get(name)
        if val:
            return str(val)
    return stream_for(hass, entry, camera_id)


# ── go2rtc REST ──────────────────────────────────────────────────────────────

async def _session(hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    return async_get_clientsession(hass)


async def probe_capabilities(hass, entry, camera_id: str) -> CameraAudioCapabilities:
    """Probe go2rtc for whether this camera's stream accepts outbound audio.

    Reads `GET {base}/api/streams`, finds the camera's stream, and inspects
    `producers[].medias` for an `audio` media with `sendonly` direction.
    """
    stream = stream_for(hass, entry, camera_id)
    url = f"{base_url(hass, entry)}/api/streams"
    try:
        session = await _session(hass)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)) as resp:
            if resp.status != 200:
                return CameraAudioCapabilities(
                    stream=stream, talkback=None, online=False,
                    detail=f"go2rtc returned HTTP {resp.status}")
            data = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 — probe must never raise into callers
        _LOGGER.debug("camera_audio probe failed for %s: %s", stream, exc)
        return CameraAudioCapabilities(
            stream=stream, talkback=None, online=False, detail=str(exc))

    return _parse_capabilities(stream, data)


def _parse_capabilities(stream: str, data: Any) -> CameraAudioCapabilities:
    """Pure parse of a go2rtc /api/streams body → capabilities for `stream`."""
    streams = data if isinstance(data, dict) else {}
    info = streams.get(stream)
    if info is None:
        return CameraAudioCapabilities(
            stream=stream, talkback=None, online=False,
            detail="stream not found in go2rtc")

    producers = (info or {}).get("producers")
    if not producers:
        # Configured but idle (on-demand). medias unknown — allow an attempt.
        return CameraAudioCapabilities(
            stream=stream, talkback=None, online=False,
            detail="stream idle (on-demand); capabilities unknown until active")

    # medias are human strings like "audio, sendonly, PCMA/8000". Only an AUDIO
    # media decides talkback: a sendonly audio track = supported; an audio track
    # that is not sendonly (recvonly) = listen-only; no audio track at all =
    # unknown (audio may just not be negotiated yet), never "unsupported".
    sendonly_codecs: list[str] = []
    saw_audio = False
    for prod in producers:
        for media in (prod or {}).get("medias") or []:
            text = str(media).lower()
            if "audio" not in text:
                continue
            saw_audio = True
            if "sendonly" in text:
                for codec in _VALID_CODECS:
                    if codec in text:
                        sendonly_codecs.append(codec)
                if not sendonly_codecs:
                    sendonly_codecs.append(DEFAULT_BACKCHANNEL_CODEC)
    if sendonly_codecs:
        return CameraAudioCapabilities(
            stream=stream, talkback=True, codecs=sorted(set(sendonly_codecs)),
            online=True, detail="sendonly audio backchannel present")
    if saw_audio:
        return CameraAudioCapabilities(
            stream=stream, talkback=False, online=True,
            detail="active audio track is not sendonly (listen-only)")
    return CameraAudioCapabilities(
        stream=stream, talkback=None, online=True,
        detail="active but no audio track reported; capabilities unknown")


async def _push_audio(hass, entry, stream: str, src_url: str, codec: str) -> None:
    """POST a finite audio source into the camera's backchannel via go2rtc.

    go2rtc pulls `src_url` with ffmpeg, transcodes to `codec`, and pushes it to
    the stream's sendonly backchannel. ffmpeg exits at EOF, so go2rtc drops the
    producer and frees the NVR's single backchannel slot automatically.
    """
    dst = quote(stream, safe="")
    src = quote(f"ffmpeg:{src_url}#audio={codec}", safe="")
    url = f"{base_url(hass, entry)}/api/streams?dst={dst}&src={src}"
    session = await _session(hass)
    async with session.post(url, timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)) as resp:
        if resp.status not in (200, 201, 204):
            body = ""
            try:
                body = (await resp.text())[:200]
            except Exception:
                pass
            raise RuntimeError(f"go2rtc push HTTP {resp.status}: {body}")


# ── Per-group backchannel mutex ──────────────────────────────────────────────

_GROUP_LOCKS: dict[str, "asyncio.Lock"] = {}


def _lock_for(group: str) -> "asyncio.Lock":
    lock = _GROUP_LOCKS.get(group)
    if lock is None:
        lock = asyncio.Lock()
        _GROUP_LOCKS[group] = lock
    return lock


# ── TTS → URL (the one hardware-coupled bit, isolated for mocking) ────────────

async def _tts_audio_url(hass, entry, message: str) -> Optional[str]:
    """Render `message` to an absolute audio URL go2rtc's ffmpeg can pull.

    Uses the TTS media-source (`media-source://tts/<engine>?message=...`),
    resolved to a `/api/tts_proxy/...` path, then made absolute with the HA
    internal URL so go2rtc on the LAN can fetch it. Best-effort: returns None if
    no TTS engine is configured or resolution fails (caller reports honestly).
    """
    tts_entity = None
    try:
        from . import tts_helper
        configured = str(_cfg(hass, entry, "tts_engine", "") or "").strip()
        if configured and configured.lower() != "auto":
            tts_entity = tts_helper.resolve_tts_entity(hass, configured)
        if not tts_entity:
            tts_entity = tts_helper.find_best_tts_entity(hass)
    except Exception:
        tts_entity = None
    if not tts_entity:
        return None

    engine = tts_entity.split(".", 1)[1] if "." in tts_entity else tts_entity
    uri = f"media-source://tts/{engine}?message={quote(message)}"
    try:
        from homeassistant.components import media_source
        from homeassistant.helpers.network import get_url
        resolved = await media_source.async_resolve_media(hass, uri, None)
        path = getattr(resolved, "url", None) or ""
        if not path:
            return None
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base = get_url(hass, allow_internal=True, prefer_external=False)
        return f"{base.rstrip('/')}{path}"
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("camera_audio TTS url resolution failed: %s", exc)
        return None


# ── Orchestration ────────────────────────────────────────────────────────────

async def speak_to_camera(
    hass,
    entry,
    camera_id: str,
    message: str,
    codec: Optional[str] = None,
) -> dict[str, Any]:
    """Speak `message` through `camera_id`'s backchannel. Returns a result dict
    with success/error for the service + panel to surface honestly."""
    if not message or not str(message).strip():
        return {"success": False, "error": "message is required"}
    if not audio_enabled(hass, entry):
        return {"success": False,
                "error": "camera audio is off — enable it in Settings → Cameras (it ships muted until you request it)"}

    stream = stream_for(hass, entry, camera_id)
    codec = (codec or DEFAULT_BACKCHANNEL_CODEC).lower()
    if codec not in _VALID_CODECS:
        return {"success": False, "error": f"unsupported codec '{codec}'"}

    caps = await probe_capabilities(hass, entry, camera_id)
    if caps.talkback is False:
        return {"success": False, "camera": camera_id, "stream": stream,
                "error": "camera is listen-only (recvonly); talkback is not supported"}
    # caps.talkback True or None (idle/unknown) → proceed.

    src_url = await _tts_audio_url(hass, entry, message)
    if not src_url:
        return {"success": False, "camera": camera_id, "stream": stream,
                "error": "no TTS engine available to render the message"}

    # Prefer a codec the camera actually advertised, if any.
    if caps.codecs and codec not in caps.codecs:
        codec = caps.codecs[0]

    group = group_for(hass, entry, camera_id)
    lock = _lock_for(group)
    async with lock:
        try:
            await _push_audio(hass, entry, stream, src_url, codec)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("camera_audio push to %s failed: %s", stream, exc)
            return {"success": False, "camera": camera_id, "stream": stream,
                    "group": group, "error": f"backchannel push failed: {exc}"}
    return {"success": True, "camera": camera_id, "stream": stream,
            "group": group, "codec": codec, "spoken": message}
