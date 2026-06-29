"""JARVIS conversation agent — provider-agnostic via LLMProvider interface."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Literal

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, llm
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALL_SPEAKERS_VALUE,
    CONF_API_KEY,
    CONF_BROADCAST_SPEAKERS,
    CONF_CAST_ANNOUNCE,
    CONF_CAST_SPEAKERS,
    CONF_DIRECTIVE,
    CONF_DIRECTIVE_PRESET,
    CONF_HONORIFIC,
    CONF_MODEL,
    CONF_REPLY_SPEAKERS,
    CONF_ROOM_ROUTING,
    CONF_TTS_ENGINE,
    CONF_USE_HASS_API,
    CONF_VOICE_SATELLITES,
    DEFAULT_DIRECTIVE_PRESET,
    DEFAULT_HONORIFIC,
    DEFAULT_MODEL,
    DEFAULT_ROOM_ROUTING,
    DEFAULT_TTS_ENGINE,
    DOMAIN,
    JARVIS_PERSONA,
    get_directive,
)
from .audio_routing import reply_targets
from .database import save_message
from .llm_provider import create_provider
from .presence import presence_context_string
from .tts_helper import resolve_tts_entity, async_announce


# ── Multi-wake dedup ────────────────────────────────────────────────────────
# When multiple satellites hear "Hey Jarvis" simultaneously, HA fires
# separate pipelines for each. This dedup ensures only the FIRST pipeline
# actually processes the request and routes audio. Subsequent duplicates
# within the window get a silent cached response (no duplicate TTS).

_DEDUP_WINDOW = 4.0  # seconds — covers STT variance between satellites
_dedup_cache: dict[str, tuple[float, str | None, str | None]] = {}
# key = normalized text, value = (timestamp, response_text_or_None, winning_device_id)


def _dedup_key(text: str) -> str:
    """Normalize text for dedup comparison."""
    return text.lower().strip().rstrip(".,!?")


def _check_and_claim_dedup(text: str, device_id: str | None) -> tuple[bool, str | None]:
    """
    Check if this is a duplicate wake-up AND claim the slot if not.

    Returns (is_duplicate, cached_response_or_None).
    If is_duplicate is True, caller should return a silent response without
    processing or routing audio again.
    """
    key = _dedup_key(text)
    now = time.time()

    # Clean old entries
    stale = [k for k, (ts, _, _) in _dedup_cache.items() if now - ts > _DEDUP_WINDOW * 2]
    for k in stale:
        _dedup_cache.pop(k, None)

    if key in _dedup_cache:
        ts, cached_resp, winning_device = _dedup_cache[key]
        if now - ts < _DEDUP_WINDOW and device_id != winning_device:
            # Another device already claimed this text
            return True, cached_resp

    # Claim the slot — first pipeline to arrive wins
    _dedup_cache[key] = (now, None, device_id)
    return False, None


def _record_dedup_response(text: str, response: str) -> None:
    """Update the cached response after processing completes."""
    key = _dedup_key(text)
    if key in _dedup_cache:
        ts, _, device_id = _dedup_cache[key]
        _dedup_cache[key] = (ts, response, device_id)

try:
    from .recognition import recognition_context_string
    _RECOGNITION_CTX = True
except ImportError:
    _RECOGNITION_CTX = False

_LOGGER = logging.getLogger(__name__)

MAX_HISTORY  = 20   # messages kept in per-conversation context window
MAX_ITERS    = 8    # max agentic tool-call iterations per request
PERSONA_FILE = "/config/jarvis_persona.txt"

# Module-level persona cache. Loaded lazily via executor to avoid blocking
# the event loop with file I/O on every conversation turn. Invalidated
# automatically when the file's mtime changes.
_persona_cache: dict = {"mtime": 0.0, "text": None, "checked": 0.0}
_PERSONA_MTIME_TTL = 30.0  # re-stat at most every 30s


_COMMAND_VERBS = {
    "turn", "set", "lock", "unlock", "open", "close", "shut", "dim", "brighten",
    "arm", "disarm", "activate", "run", "play", "pause", "stop", "mute", "unmute",
    "increase", "decrease", "switch", "enable", "disable", "start", "resume",
    "lower", "raise", "toggle", "cancel", "snooze", "remind", "ignore",
    "tell", "show", "give", "list", "check", "read", "announce", "make",
}
_QUESTION_STARTS = {
    "what", "what's", "whats", "when", "when's", "where", "where's", "who",
    "who's", "why", "how", "how's", "is", "are", "can", "could", "would",
    "should", "do", "does", "did", "will", "whose", "which",
}
# Device/control nouns that signal a genuine home command. Deliberately EXCLUDES
# bare room names (basement, kitchen…) — those appear in ambient speech too, and
# a real room command almost always also carries a device or action word.
_DOMAIN_KEYWORDS = {
    "light", "lights", "lamp", "lamps", "door", "doors", "lock", "locks",
    "window", "windows", "thermostat", "temperature", "heat", "heating",
    "cooling", "alarm", "scene", "fan", "blinds", "shades", "cover", "curtains",
    "volume", "plug", "outlet", "sensor", "camera", "climate", "brightness",
    "weather", "sump", "dehumidifier", "washer", "dryer", "thermostat",
}
_FILLER = {
    # Pure discourse markers / sentence fragments that are NEVER a valid
    # standalone instruction or answer. Deliberately EXCLUDES greetings (hi,
    # hello), affirmatives/negatives (yes, no, sure, okay) and acknowledgments
    # (thanks) — those are real inputs (greeting handler, yes/no answers) and
    # must pass the gate.
    "so", "um", "uh", "hmm", "oh", "mean", "like", "know", "and", "but",
    "wait", "well", "anyway", "actually", "i", "me", "you", "we", "they",
    "it", "that", "this", "there", "here",
}


def _is_addressed_to_jarvis(text: str) -> bool:
    """
    Relevance gate: does this utterance look like it's actually addressed to
    JARVIS (a command or question), versus ambient speech a satellite happened
    to transcribe — TV dialogue, background conversation, sentence fragments?

    Tuned for HIGH PRECISION on the PASS side: it never drops anything carrying
    a command verb, a question word, a device keyword, or the name "Jarvis", so
    a real instruction is never rejected. It only filters input that has NONE of
    those signals and clearly reads as filler or a stray fragment. The PRIMARY
    defense against TV noise is wake-word gating on the satellite; this is a
    backstop for whatever slips through. Returns True = process, False = ignore.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    words = t.split()

    # ── Positive signals — any one means: definitely process ──
    if "jarvis" in words:
        return True
    if words[0].strip(".,!?") in _COMMAND_VERBS:
        return True          # imperative — also protects compound commands
    if words[0].strip(".,!?") in _QUESTION_STARTS:
        return True          # a question — including multi-part ones
    if any(w.strip(".,!?") in _DOMAIN_KEYWORDS for w in words):
        return True          # mentions a device/control noun

    # ── No command signal at all. Reject obvious non-commands. ──
    # Bare interjection / filler ("So", "Yeah", "I mean", "Wait but")
    if len(words) <= 3 and all(w.strip(".,!?'") in _FILLER for w in words):
        return False
    # Rambling narrative with no command structure (TV dialogue tends to span
    # multiple clauses). Only applies when NO positive signal was found above.
    sentence_breaks = t.count(".") + t.count("?") + t.count("!")
    if sentence_breaks >= 2 and len(words) >= 8:
        return False

    # Default: PASS. Better to occasionally answer ambient speech than to drop
    # a real request — wake-word gating is the real filter.
    return True


def _is_connectivity_failure(text: str) -> bool:
    """
    Detect the sentinel strings run_agent returns when ALL providers fail.

    run_agent never raises on network failure — it returns an apologetic
    string. We match on the stable phrase fragments so a real LLM answer
    that happens to mention connectivity isn't misclassified (those sentinels
    always pair 'reasoning systems' with a connectivity phrase).
    """
    if not text:
        return True
    t = text.lower()
    return "reasoning systems" in t and (
        "connecting to" in t or "connectivity issues" in t
    )


def _sync_load_persona() -> tuple[float, str | None]:
    """Synchronous persona file read. Must be called from an executor thread."""
    try:
        if os.path.exists(PERSONA_FILE):
            mtime = os.path.getmtime(PERSONA_FILE)
            with open(PERSONA_FILE) as f:
                text = f.read().strip() or None
            return mtime, text
    except OSError:
        pass
    return 0.0, None


async def _ensure_persona_loaded(hass: HomeAssistant) -> None:
    """Check & refresh the persona cache if needed. Called from async context."""
    import time as _time
    now = _time.time()
    # Rate-limit mtime checks so we don't stat every turn
    if (now - _persona_cache["checked"]) < _PERSONA_MTIME_TTL and _persona_cache["text"] is not None:
        return
    _persona_cache["checked"] = now
    mtime, text = await hass.async_add_executor_job(_sync_load_persona)
    if mtime != _persona_cache["mtime"] or _persona_cache["text"] is None:
        _persona_cache["mtime"] = mtime
        _persona_cache["text"] = text


_FALLBACKS = [
    "Technical difficulties, {honorific}. Even I have them occasionally. Please try again.",
    "I appear to be having connectivity issues, {honorific}. Bear with me.",
    "Something is interfering with my systems, {honorific}. One moment.",
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([JarvisAgent(hass, config_entry)])


class JarvisAgent(conversation.ConversationEntity):
    """
    JARVIS conversation agent — Groq-powered, HA home control.
    ConversationEntityFeature.CONTROL enables home device control.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass  = hass
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="JARVIS",
            manufacturer="Stark Industries",
            model="JARVIS AI Assistant",
            sw_version="4.0.0",
        )
        self._histories: dict[str, list[dict]] = {}
        self._fallback_idx = 0

        # Pull the shared LLM provider from hass.data (created in async_setup_entry).
        # This way conversation automatically honours the user's chosen backend
        # (Groq/OpenAI/Anthropic/Ollama/custom) without any code changes here.
        shared = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        self._client = shared.get("client")
        if self._client is None:
            # Fallback — create a provider directly (should only happen in unusual
            # setup order cases; the shared client is normally always present)
            from .llm_provider import create_provider as _cp
            provider_name = entry.options.get("llm_provider",
                            entry.data.get("llm_provider", "groq"))
            base_url = entry.options.get("llm_base_url",
                            entry.data.get("llm_base_url", "")) or None
            self._client = _cp(
                provider_name,
                entry.data[CONF_API_KEY],
                self._model(),
                base_url,
            )
        _LOGGER.info(
            "JARVIS agent initialised — provider=%s, model=%s",
            getattr(self._client, "name", "unknown"),
            self._model(),
        )

    # ── Config helpers ────────────────────────────────────────────────────────

    def _opt(self, key: str, default=None):
        """Read from options (Configure) first, then data, then default."""
        return self.entry.options.get(key, self.entry.data.get(key, default))

    def _rt_opt(self, key: str, default=None):
        """
        Runtime-aware read: panel runtime_config FIRST, then options, then data.
        The panel writes model/provider changes to runtime_config; reading it
        here lets those changes take effect on the next request without a
        restart (run_agent re-resolves provider/model per call).
        """
        try:
            data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
            rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
            if key in rc:
                return rc[key]
        except Exception:
            pass
        return self.entry.options.get(key, self.entry.data.get(key, default))

    def _model(self) -> str:
        return self._opt(CONF_MODEL, DEFAULT_MODEL)

    def _honorific(self) -> str:
        return self._opt(CONF_HONORIFIC, DEFAULT_HONORIFIC)

    def _use_hass_api(self) -> bool:
        return bool(self._opt(CONF_USE_HASS_API, True))

    def _speakers(self, device_id: str | None = None) -> list[str]:
        """
        Choose which speakers to broadcast a DIRECT REPLY to.

        Uses the three-tier audio architecture (audio_routing.reply_targets):
          - voice_satellites are excluded from speaking
          - reply_speakers used for direct conversation
          - room-aware: speak to kitchen puck → reply via kitchen Google
          - falls back to broadcast_speakers if no reply_speakers configured
          - falls back to legacy cast_speakers for backward compatibility
          - satellite_pairings from panel Settings override area registry
        """
        # Read satellite_pairings from runtime_config
        sat_pairings = None
        try:
            import json as _json
            _data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
            _rc = _data.get("runtime_config", {}) if isinstance(_data, dict) else {}
            _raw = _rc.get("satellite_pairings")
            if _raw:
                _parsed = _json.loads(_raw) if isinstance(_raw, str) else _raw
                if isinstance(_parsed, dict) and _parsed:
                    sat_pairings = _parsed
        except Exception:
            pass

        return reply_targets(
            self.hass,
            device_id=device_id,
            voice_satellites=self._opt(CONF_VOICE_SATELLITES, []) or [],
            reply_speakers=self._opt(CONF_REPLY_SPEAKERS, []) or [],
            broadcast_speakers=self._opt(CONF_BROADCAST_SPEAKERS, []) or [],
            legacy_cast_speakers=self._opt(CONF_CAST_SPEAKERS, []) or [],
            room_routing=bool(self._opt(CONF_ROOM_ROUTING, DEFAULT_ROOM_ROUTING)),
            satellite_pairings=sat_pairings,
        )

    def _satellite_speaker(self, device_id: str) -> str | None:
        """
        Given the device_id of the wake-word/voice satellite, return its
        associated media_player entity (the same physical box).
        """
        try:
            from homeassistant.helpers import device_registry as dr, entity_registry as er
            ent_reg = er.async_get(self.hass)
            for ent in ent_reg.entities.values():
                if ent.device_id == device_id and ent.domain == "media_player":
                    if self.hass.states.get(ent.entity_id):
                        return ent.entity_id
        except Exception as exc:
            _LOGGER.debug("JARVIS: satellite speaker lookup error: %s", exc)
        return None

    def _tts_entity(self) -> str | None:
        return resolve_tts_entity(self.hass, self._opt(CONF_TTS_ENGINE, DEFAULT_TTS_ENGINE))

    def _use_announce(self) -> bool:
        return bool(self._opt(CONF_CAST_ANNOUNCE, True))

    # ── Supported languages ───────────────────────────────────────────────────

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    # ── Persona ───────────────────────────────────────────────────────────────

    def _directive(self) -> str:
        """Return the active prime directive text."""
        preset = self._opt(CONF_DIRECTIVE_PRESET, DEFAULT_DIRECTIVE_PRESET)
        custom = self._opt(CONF_DIRECTIVE, "")
        return get_directive(preset, custom)

    def _persona(self) -> str:
        """Build the full system prompt.

        Order matters: prime directive FIRST (unrelenting core purpose),
        then character/style, then live context. The directive appears
        at the top of every single LLM call.
        """
        base = JARVIS_PERSONA
        cached = _persona_cache.get("text")
        if cached:
            base = cached

        # Prime directive always comes first
        directive = self._directive()
        base = f"{directive}\n\n---\n\n{base}"

        # Inject live context — time, presence, weather summary
        import datetime as _dt
        ctx_parts = [f"Current time: {_dt.datetime.now().strftime('%A %B %-d, %-I:%M %p')}."]

        try:
            presence = presence_context_string(self.hass)
            if presence:
                ctx_parts.append(f"Presence: {presence}")
        except Exception as exc:
            _LOGGER.debug("JARVIS: presence context error: %s", exc)

        # Recent face recognitions
        if _RECOGNITION_CTX:
            try:
                faces = recognition_context_string(self.hass)
                if faces:
                    ctx_parts.append(faces)
            except Exception as exc:
                _LOGGER.debug("JARVIS: recognition context error: %s", exc)

        # Weather summary
        try:
            for state in self.hass.states.async_all("weather"):
                temp = state.attributes.get("temperature")
                unit = state.attributes.get("temperature_unit", "°")
                ctx_parts.append(f"Weather: {state.state}, {temp}{unit}.")
                break
        except Exception:
            pass

        # v5.6.0: Full home state awareness (HGA-inspired)
        try:
            from .home_state import get_home_summary
            home_summary = get_home_summary(self.hass)
            if home_summary:
                ctx_parts.append(f"\n## Home state snapshot\n{home_summary}")
        except Exception as exc:
            _LOGGER.debug("JARVIS: home state summary error: %s", exc)

        context_block = "\n\n## Current context (live data)\n" + "\n".join(ctx_parts)
        return (base + context_block).replace("{honorific}", self._honorific())

    # ── Conversation history ──────────────────────────────────────────────────

    def _history(self, cid: str) -> list[dict]:
        h = self._histories.setdefault(cid, [])
        if len(h) > MAX_HISTORY:
            self._histories[cid] = h[-MAX_HISTORY:]
        return self._histories[cid]

    # ── HA LLM tool integration ───────────────────────────────────────────────

    async def _get_hass_api(self, user_input: conversation.ConversationInput):
        try:
            ctx = llm.LLMContext(
                platform=DOMAIN,
                context=user_input.context,
                user_prompt=user_input.text,
                language=user_input.language,
                assistant=conversation.HOME_ASSISTANT_AGENT,
                device_id=user_input.device_id,
            )
            api = await llm.async_get_api(self.hass, "assist", ctx)
            _LOGGER.debug("JARVIS: %d HA tools available", len(api.tools))
            return api
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.debug("JARVIS: HA Assist API unavailable (%s) — chat-only mode", exc)
            return None

    # ── LLM calls (provider-agnostic) ─────────────────────────────────────────

    def _llm_text(self, messages: list[dict], persona: str) -> str:
        """Plain text call — no tools. Uses the LLMProvider interface."""
        result = self._client.chat(
            messages=[{"role": "system", "content": persona}] + messages,
            max_tokens=512,
            temperature=0.7,
        )
        return result["text"]

    def _llm_with_tools(self, messages: list[dict], persona: str, tools: list[dict]) -> dict:
        """Call with function-calling tools. Returns standardised dict.

        Returns:
          {"type": "text", "text": "..."}               when no tool calls
          {"type": "tool_calls",
           "raw_message": <provider-specific>,
           "calls": [{"id", "name", "args"}, ...]}      when tools invoked
        """
        result = self._client.chat(
            messages=[{"role": "system", "content": persona}] + messages,
            tools=tools or None,
            max_tokens=1024,
            temperature=0.7,
        )
        if result["tool_calls"]:
            return {
                "type":        "tool_calls",
                "raw_message": result["raw"],
                "calls":       result["tool_calls"],
            }
        return {"type": "text", "text": result["text"]}

    # ── Agentic loop ──────────────────────────────────────────────────────────

    async def _agentic_loop(
        self,
        messages: list[dict],
        persona: str,
        hass_api,
        user_input: conversation.ConversationInput,
    ) -> str:
        """LLM → execute HA tools → feed results back → repeat until text."""
        if hass_api is None or not hass_api.tools:
            return await self.hass.async_add_executor_job(
                self._llm_text, messages, persona
            )

        tools = [
            {
                "type": "function",
                "function": {
                    "name":        t.name,
                    "description": t.description,
                    "parameters":  t.parameters,
                },
            }
            for t in hass_api.tools
        ]

        working = list(messages)
        for _ in range(MAX_ITERS):
            result = await self.hass.async_add_executor_job(
                self._llm_with_tools, working, persona, tools
            )
            if result["type"] == "text":
                return result["text"]

            raw_msg = result["raw_message"]
            working.append({
                "role":       "assistant",
                "content":    raw_msg.content or "",
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in raw_msg.tool_calls
                ],
            })

            for call in result["calls"]:
                try:
                    tool_input = llm.ToolInput(
                        tool_name=call["name"],
                        tool_args=call["args"],
                        platform=DOMAIN,
                        context=user_input.context,
                        user_prompt=user_input.text,
                        language=user_input.language,
                        assistant=conversation.HOME_ASSISTANT_AGENT,
                        device_id=user_input.device_id,
                    )
                    tool_result = await hass_api.async_call_tool(tool_input)
                    result_str = (
                        json.dumps(tool_result)
                        if isinstance(tool_result, dict)
                        else str(tool_result)
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    _LOGGER.warning("JARVIS tool '%s' failed: %s", call["name"], exc)
                    result_str = f"Error: {exc}"

                working.append({
                    "role":         "tool",
                    "tool_call_id": call["id"],
                    "content":      result_str,
                })

        # Max iterations — ask for a plain summary of what was done
        working.append({"role": "user", "content": "Briefly summarise what you have done."})
        return await self.hass.async_add_executor_job(self._llm_text, working, persona)

    # ── Main entry point ──────────────────────────────────────────────────────

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        from .websocket import jarvis_log
        device_id = getattr(user_input, 'device_id', None)
        jarvis_log("CONV", f"ENTRY text='{user_input.text[:60]}' device={device_id}")
        _LOGGER.warning(
            "JARVIS async_process ENTRY: text='%s' device_id='%s'",
            user_input.text[:60], device_id,
        )

        # ── v5.7.01: Multi-wake dedup ────────────────────────────────────
        # If another satellite already processed this exact text within
        # the dedup window, return the cached response WITHOUT routing
        # audio again. This prevents 3 speakers all talking at once.
        is_dup, cached = _check_and_claim_dedup(user_input.text, device_id)
        if is_dup:
            _LOGGER.warning(
                "JARVIS dedup: suppressing duplicate from device=%s "
                "(already handled), text='%s'",
                device_id, user_input.text[:40],
            )
            jarvis_log("DEDUP", f"suppressed duplicate from {device_id}")
            ir = intent.IntentResponse(language=user_input.language)
            # Return empty speech so HA pipeline completes silently
            ir.async_set_speech(cached or "")
            return conversation.ConversationResult(
                response=ir,
                conversation_id=user_input.conversation_id or device_id or "default",
            )

        # ── v5.7.01: Presence gate — only the occupied room responds ─────
        # Uses mmWave / occupancy sensors to determine if anyone is in the
        # satellite's area. If the area is NOT occupied, suppress this
        # pipeline so only the satellite in the user's actual room answers.
        if device_id:
            try:
                from .audio_routing import entity_area, is_area_occupied
                from homeassistant.helpers import (
                    device_registry as _dr,
                    entity_registry as _er,
                )
                dev_reg = _dr.async_get(self.hass)
                dev = dev_reg.async_get(device_id)
                sat_area = dev.area_id if dev else None

                if sat_area and not is_area_occupied(self.hass, sat_area):
                    # Double check: are ANY areas occupied? If no sensors
                    # report occupancy anywhere, skip the gate entirely
                    # (sensors might be offline / not configured).
                    from .audio_routing import currently_occupied_areas
                    occupied = currently_occupied_areas(self.hass)
                    if occupied:
                        _LOGGER.warning(
                            "JARVIS presence gate: suppressing pipeline from "
                            "device=%s area='%s' (not occupied, occupied=%s)",
                            device_id, sat_area, occupied,
                        )
                        jarvis_log(
                            "GATE",
                            f"suppressed {sat_area} (not occupied, "
                            f"occupied={occupied})",
                        )
                        ir = intent.IntentResponse(language=user_input.language)
                        ir.async_set_speech("")
                        return conversation.ConversationResult(
                            response=ir,
                            conversation_id=(
                                user_input.conversation_id
                                or device_id or "default"
                            ),
                        )
                    else:
                        _LOGGER.debug(
                            "JARVIS presence gate: no occupied areas "
                            "detected anywhere — skipping gate (sensors "
                            "may be offline)"
                        )
            except Exception as exc:
                _LOGGER.debug("Presence gate check failed (non-fatal): %s", exc)

        cid       = user_input.conversation_id or user_input.device_id or "default"
        honorific = self._honorific()
        # Warm persona cache (reads file in executor) before sync _persona()
        await _ensure_persona_loaded(self.hass)
        persona   = self._persona()
        history   = self._history(cid)

        history.append({"role": "user", "content": user_input.text})
        save_message("user", user_input.text, device_id=cid)

        # v5.6.1: Store user message in long-term memory
        try:
            from .memory import store_memory, get_conversation_context
            await self.hass.async_add_executor_job(
                lambda: store_memory(user_input.text, role="user",
                    device_id=user_input.device_id or "", conversation_id=cid)
            )
            # Retrieve relevant past context and inject into persona
            mem_context = await self.hass.async_add_executor_job(
                get_conversation_context, user_input.text, 3,
            )
            if mem_context:
                persona = persona + "\n\n" + mem_context
        except Exception as exc:
            _LOGGER.debug("Memory store/retrieve: %s", exc)

        # v6.25.0: Inject curated knowledge — durable facts/preferences JARVIS
        # knows (distinct from the transcript recall above), scored against the
        # current message so the most relevant facts lead.
        # v6.29.0: scope to *this* person + household so one resident's private
        # facts don't leak into another's context.
        try:
            from . import knowledge, identity
            ident = identity.resolve(
                self.hass, device_id=getattr(user_input, "device_id", None))
            subjects = [identity.subject_for(ident), "household"]
            kn_block = await self.hass.async_add_executor_job(
                lambda: knowledge.prompt_block(user_input.text, subjects=subjects))
            if kn_block:
                persona = persona + "\n\n" + kn_block
        except Exception as exc:
            _LOGGER.debug("Knowledge inject: %s", exc)

        hass_api = await self._get_hass_api(user_input) if self._use_hass_api() else None

        cast_routed = False  # tracks whether Cast speaker is handling TTS

        # v5.9.07: Proactive offer yes/no — resolved before main pipeline.
        offer_reply = None
        try:
            from . import cognitive_core
            pending = cognitive_core.get_pending_offer()
            if pending:
                low = user_input.text.strip().lower()
                affirm = low in ("yes", "yes please", "yeah", "yep", "sure",
                                 "do it", "go ahead", "please do", "okay", "ok",
                                 "affirmative", "please")
                deny = low in ("no", "no thanks", "nope", "don't", "do not",
                               "negative", "leave it", "nevermind", "never mind",
                               "cancel", "stop")
                if affirm:
                    res = await cognitive_core.accept_pending_offer()
                    if res.get("now_autonomous"):
                        offer_reply = (
                            f"Done, {honorific}. I've noticed you consistently "
                            f"want this — I'll handle it automatically from now on. "
                            f"Say 'stop doing that on your own' to revoke."
                        )
                    elif res.get("ok"):
                        left = max(0, 3 - res.get("approvals", 0))
                        offer_reply = f"Done, {honorific}."
                        if 0 < left <= 2:
                            offer_reply += (
                                " (A couple more times and I'll handle this "
                                "automatically.)"
                            )
                    else:
                        offer_reply = f"I wasn't able to complete that, {honorific}."
                    jarvis_log("OFFER", f"accepted: {offer_reply[:60]}")
                elif deny:
                    cognitive_core.decline_pending_offer()
                    offer_reply = f"Understood, {honorific}. I'll leave it."
                    jarvis_log("OFFER", "declined")
                else:
                    cognitive_core.decline_pending_offer()
        except Exception as exc:
            _LOGGER.debug("Offer handling skipped: %s", exc)

        if offer_reply is not None:
            # Short-circuit: deliver the offer response directly.
            offer_reply = offer_reply.replace("{honorific}", honorific)
            ir = intent.IntentResponse(language=user_input.language)
            ir.async_set_speech(offer_reply)
            return conversation.ConversationResult(response=ir, conversation_id=cid)

        # v5.9.16: Relevance gate. Satellites without strict wake-word gating
        # pick up TV/background speech, which the pipeline transcribes and sends
        # here as if it were a command. Drop input that clearly isn't addressed
        # to JARVIS (filler, fragments, rambling dialogue) BEFORE it reaches the
        # local engine or the agent — staying silent rather than acting on, or
        # chattering back at, ambient noise. Toggle off via `relevance_gate`.
        if self._opt("relevance_gate", True) and not _is_addressed_to_jarvis(user_input.text):
            jarvis_log("GATE", f"ignored ambient input: '{user_input.text.strip()[:60]}'")
            _LOGGER.info("JARVIS relevance gate: ignored '%s'", user_input.text.strip()[:80])
            ir = intent.IntentResponse(language=user_input.language)
            ir.async_set_speech("")  # silence — do not respond to ambient speech
            return conversation.ConversationResult(response=ir, conversation_id=cid)

        try:
            # v5.7.00: Local engine is PRIMARY. Complexity scoring decides
            # whether to escalate to LLM. Handles 95%+ of requests at zero
            # API cost. Only genuinely complex/creative/analytical requests
            # fall through to the LLM agent (Groq/Gemini).
            from .local_engine import try_local, score_complexity
            local_result = await try_local(self.hass, user_input.text, honorific)

            if local_result and local_result.handled:
                response_text = local_result.text
                _LOGGER.info("JARVIS local: %s", response_text[:100])
                jarvis_log("LOCAL", f"handled: {response_text[:80]}")
            else:
                complexity = score_complexity(user_input.text)
                # v5.9.06: Connectivity-aware escalation. If the circuit breaker
                # is OPEN (LLM known-unreachable), skip the doomed network call
                # entirely and attempt an offline salvage pass instead.
                from . import connectivity
                if not connectivity.allow_request():
                    jarvis_log("OFFLINE", f"LLM down — local salvage: {user_input.text[:60]}")
                    salvage = await try_local(
                        self.hass, user_input.text, honorific, force=True
                    )
                    if salvage and salvage.handled:
                        response_text = salvage.text
                        _LOGGER.info("JARVIS offline-salvage: %s", response_text[:100])
                    else:
                        response_text = (
                            f"I'm offline at the moment, {honorific}, so I can't "
                            f"handle that request — it needs my reasoning systems. "
                            f"I can still control your devices, report status, and "
                            f"run scenes. I'll be back to full capability once "
                            f"connectivity returns."
                        )
                else:
                    jarvis_log("AGENT", f"LLM needed (complexity={complexity}): {user_input.text[:60]}")
                    # Complex request — use LLM agent (Groq/Gemini fallback)
                    from .agent import run_agent
                    provider_name = self._rt_opt("llm_provider", "groq")
                    api_key_val = (
                        self._rt_opt("api_key", "")
                        or self.entry.data.get("api_key", "")
                    )
                    model_val = self._rt_opt(CONF_MODEL, DEFAULT_MODEL)
                    base_url_val = self._rt_opt("llm_base_url", "") or None

                    try:
                        response_text = await run_agent(
                            self.hass,
                            messages=history,
                            persona=persona,
                            provider_name=provider_name,
                            api_key=api_key_val,
                            model=model_val,
                            base_url=base_url_val,
                            hass_api=hass_api,
                            user_input=user_input,
                            temperature=0.7,
                            config=dict(self.entry.options) | dict(self.entry.data),
                        )
                        # Agent returns a connectivity sentinel string on total
                        # failure; treat that as a breaker failure + salvage.
                        if _is_connectivity_failure(response_text):
                            connectivity.record_failure()
                            salvage = await try_local(
                                self.hass, user_input.text, honorific, force=True
                            )
                            if salvage and salvage.handled:
                                response_text = salvage.text
                        else:
                            connectivity.record_success()
                    except Exception as agent_exc:  # pylint: disable=broad-except
                        _LOGGER.warning("Agent call raised: %s", agent_exc)
                        connectivity.record_failure()
                        salvage = await try_local(
                            self.hass, user_input.text, honorific, force=True
                        )
                        if salvage and salvage.handled:
                            response_text = salvage.text
                        else:
                            response_text = (
                                f"I've lost connection to my reasoning systems, "
                                f"{honorific}. I can still control devices and "
                                f"report status while I reconnect."
                            )
            response_text = response_text.replace("{honorific}", honorific)

            # v5.7.01: Record the winning response so duplicate pipelines
            # arriving slightly later will get caught by dedup
            _record_dedup_response(user_input.text, response_text)

            # v5.8.03: Log command for cognitive core pattern learning
            # v6.29.0: attribute to the resolved person so per-person command
            # patterns are real (falls back to "unknown" when not confident).
            try:
                from . import cognitive_core, identity
                handler = "local" if local_result and local_result.handled else "agent"
                who = identity.resolve(
                    self.hass, device_id=getattr(user_input, "device_id", None)).person
                cognitive_core.log_command(
                    text=user_input.text,
                    handled_by=handler,
                    person=who,
                )
            except Exception:
                pass

            # v5.7.03: Route reply to paired Cast speaker. The satellite
            # handles wake/STT, JARVIS routes TTS output to the real
            # speaker in the room (Google Home, Nest Audio, etc.) for
            # better audio quality. Satellite pairings from the panel
            # Settings determine which speaker each satellite uses.
            try:
                device_id_route = getattr(user_input, 'device_id', None)
                if device_id_route:
                    from .audio_routing import reply_target
                    # Read satellite_pairings from runtime_config
                    sat_pairings = None
                    try:
                        import json as _json
                        _data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
                        _rc = _data.get("runtime_config", {}) if isinstance(_data, dict) else {}
                        _raw = _rc.get("satellite_pairings")
                        if _raw:
                            _parsed = _json.loads(_raw) if isinstance(_raw, str) else _raw
                            if isinstance(_parsed, dict) and _parsed:
                                sat_pairings = _parsed
                    except Exception:
                        pass

                    speaker = reply_target(
                        self.hass,
                        device_id=device_id_route,
                        satellite_pairings=sat_pairings,
                    )
                    if speaker and not speaker.startswith("assist_satellite."):
                        tts_ent = resolve_tts_entity(
                            self.hass, self._opt("tts_engine", "auto"),
                        )
                        if tts_ent:
                            _LOGGER.info(
                                "JARVIS reply → Cast: tts=%s speaker=%s",
                                tts_ent, speaker,
                            )
                            self.hass.async_create_task(
                                async_announce(
                                    self.hass, response_text,
                                    tts_ent, [speaker],
                                    context="reply",
                                )
                            )
                            cast_routed = True
                        else:
                            _LOGGER.warning("JARVIS reply: no TTS entity found")
                    else:
                        _LOGGER.info(
                            "JARVIS reply: no Cast pairing for device=%s, "
                            "pipeline speaker fallback",
                            device_id_route,
                        )
            except Exception as exc:
                _LOGGER.warning("Reply routing error: %s", exc)

            # Now safe to do blocking DB operations
            history.append({"role": "assistant", "content": response_text})
            try:
                await self.hass.async_add_executor_job(
                    save_message, "assistant", response_text, cid,
                )
            except Exception:
                pass
            _LOGGER.debug("JARVIS → %s", response_text[:120])

            # Store assistant response in long-term memory
            try:
                await self.hass.async_add_executor_job(
                    lambda: store_memory(response_text, role="assistant",
                        device_id=user_input.device_id or "", conversation_id=cid)
                )
            except Exception:
                pass

        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.error(
                "JARVIS API error (%s): %s | model=%s",
                type(exc).__name__, exc, self._model(),
            )
            response_text = _FALLBACKS[self._fallback_idx % len(_FALLBACKS)].format(
                honorific=honorific
            )
            self._fallback_idx += 1
            if history and history[-1]["role"] == "user":
                history.pop()

        ir = intent.IntentResponse(language=user_input.language)
        # When Cast routing succeeded, suppress the pipeline's TTS to the
        # satellite speaker — only the Cast device should talk. If Cast
        # routing failed or wasn't attempted, let the pipeline play through
        # the satellite as fallback.
        if cast_routed:
            ir.async_set_speech("")  # silence satellite — Cast has it
        else:
            ir.async_set_speech(response_text)  # fallback: satellite speaks
        return conversation.ConversationResult(response=ir, conversation_id=cid)
