"""
JARVIS speaker-ID STT tee (vocal biometrics Path B, Step 2; issue #397).

This is the *capture* point for in-process vocal biometrics. A conversation
agent never sees audio — the utterance is already text by the time JARVIS runs —
so the only HA-native place to tap it is an ``stt`` entity, which receives the
raw PCM by contract. This entity **tees** that stream: it forwards every chunk
to a configured underlying STT (Whisper/cloud) for the real transcription and
keeps a copy to embed into a speaker voiceprint.

Hard safety rules (this sits in the critical speech path):
  • **Opt-in / zero blast radius.** The entity is registered only when local
    voice recognition is enabled, a delegate STT is configured, and the
    embedding backend is present. A default install adds no entity at all.
  • **Fail-open.** Transcription is returned from the delegate verbatim; the
    embedding runs as a *background task after* the result is returned, so it
    adds no latency and any embedding error can never affect what the user said.
"""
from __future__ import annotations

import logging
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# How much teed audio to keep for the embedding (bounded so a long/stuck stream
# can never grow without limit). 30s at 16 kHz 16-bit mono ≈ ~960 KB.
_MAX_BUFFER_BYTES = 16000 * 2 * 30


def _cfg(hass: HomeAssistant, key: str, default):
    try:
        from . import jarvis_config
        return jarvis_config.get(key, default)
    except Exception:
        return default


def _as_bool(v, default: bool = False) -> bool:
    """Coerce a stored config flag to a real boolean. A panel toggle that saved
    its state as the STRING ``"false"`` (a bespoke-toggle bug, #542) would read
    back truthy under a plain ``bool()`` — so the local voice tier could never
    be turned off. Treat the usual false-y strings as False."""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the tee STT entity ONLY when the maintainer has opted in and the
    pieces are present. Otherwise add nothing — a default install is untouched,
    so this platform is a no-op and can't affect anyone's voice pipeline."""
    try:
        if not _as_bool(_cfg(hass, "voice_recognition_local", False)):
            return
        delegate = (_cfg(hass, "voice_recognition_delegate_stt", "") or "").strip()
        if not delegate:
            _LOGGER.info("Local voice ID on but no delegate STT configured — tee not added")
            return
        from . import speaker_id
        if not await hass.async_add_executor_job(speaker_id.backend_available):
            _LOGGER.info("Local voice ID on but speaker-embedding backend absent — tee not added")
            return
        async_add_entities([JarvisSpeakerIdStt(hass, entry, delegate)])
        _LOGGER.info("JARVIS speaker-ID STT tee registered (delegate=%s)", delegate)
    except Exception as exc:  # never break integration setup
        _LOGGER.warning("speaker-ID STT setup skipped: %s", exc)


def _delegate_entity(hass: HomeAssistant, entity_id: str):
    """Resolve the underlying STT entity object across HA versions. None if it
    can't be found (then the tee can't transcribe and must not be used)."""
    try:
        from homeassistant.components import stt as ha_stt
        getter = getattr(ha_stt, "async_get_speech_to_text_entity", None)
        if getter is not None:
            return getter(hass, entity_id)
    except Exception:
        pass
    try:  # fallback: the stt entity component
        comp = hass.data.get("entity_components", {}).get("stt")
        if comp is not None:
            return comp.get_entity(entity_id)
    except Exception:
        pass
    return None


try:  # keep the module importable (audit/tests) even without HA's stt component
    from homeassistant.components import stt as _ha_stt
    _SttBase = _ha_stt.SpeechToTextEntity
except Exception:  # pragma: no cover - exercised only where HA stt is absent
    _ha_stt = None
    _SttBase = object


class JarvisSpeakerIdStt(_SttBase):
    """A pass-through STT that tees the utterance audio to the speaker embedder.
    Transcription is 100% the delegate's; the embedding is a fire-and-forget
    side effect that runs after the result is returned."""

    _attr_has_entity_name = True
    _attr_name = "JARVIS Speaker-ID"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, delegate_id: str):
        self.hass = hass
        self._entry = entry
        self._delegate_id = delegate_id
        self._attr_unique_id = f"{entry.entry_id}_jarvis_speaker_id_stt"

    # The pipeline negotiates formats with us; we mirror a safe, common profile
    # and otherwise defer transcription entirely to the delegate.
    @property
    def supported_languages(self):
        dele = _delegate_entity(self.hass, self._delegate_id)
        if dele is not None:
            try:
                return dele.supported_languages
            except Exception:
                pass
        return ["en-US"]

    @property
    def supported_formats(self):
        return [_ha_stt.AudioFormats.WAV] if _ha_stt else []

    @property
    def supported_codecs(self):
        return [_ha_stt.AudioCodecs.PCM] if _ha_stt else []

    @property
    def supported_bit_rates(self):
        return [_ha_stt.AudioBitRates.BITRATE_16] if _ha_stt else []

    @property
    def supported_sample_rates(self):
        return [_ha_stt.AudioSampleRates.SAMPLERATE_16000] if _ha_stt else []

    @property
    def supported_channels(self):
        return [_ha_stt.AudioChannels.CHANNEL_MONO] if _ha_stt else []

    async def async_process_audio_stream(self, metadata, stream):
        """Forward the audio to the delegate for transcription while buffering a
        copy; embed the copy afterwards, off the critical path. On any trouble,
        the delegate's result is what the user gets."""
        delegate = _delegate_entity(self.hass, self._delegate_id)
        if delegate is None:
            _LOGGER.warning("speaker-ID STT: delegate %s unavailable", self._delegate_id)
            # Can't transcribe without a delegate; surface an empty failure
            # rather than a crash. (Only reachable if the user mis-configured.)
            return _ha_stt.SpeechResult("", _ha_stt.SpeechResultState.ERROR)

        buf = bytearray()

        async def _tee():
            async for chunk in stream:
                if chunk and len(buf) < _MAX_BUFFER_BYTES:
                    buf.extend(chunk)
                yield chunk

        try:
            result = await delegate.async_process_audio_stream(metadata, _tee())
        except Exception as exc:
            _LOGGER.warning("speaker-ID STT: delegate transcription failed: %s", exc)
            return _ha_stt.SpeechResult("", _ha_stt.SpeechResultState.ERROR)

        # Fire-and-forget: embed + publish the speaker AFTER returning text.
        try:
            sr = getattr(getattr(metadata, "sample_rate", None), "value", None)
            self.hass.async_create_background_task(
                self._embed_and_publish(bytes(buf), int(sr or 16000)),
                name="jarvis_speaker_embed")
        except Exception as exc:
            _LOGGER.debug("speaker embed scheduling skipped: %s", exc)
        return result

    async def _embed_and_publish(self, pcm: bytes, sample_rate: int) -> None:
        try:
            from . import speaker_id, voice_recognition
            emb = await self.hass.async_add_executor_job(
                speaker_id.embed_pcm, pcm, sample_rate)
            if not emb:
                return
            match = await self.hass.async_add_executor_job(speaker_id.identify, emb)
            voice_recognition.note_local_speaker(match)  # None clears
        except Exception as exc:  # never affects the completed transcription
            _LOGGER.debug("speaker embed/publish failed: %s", exc)
