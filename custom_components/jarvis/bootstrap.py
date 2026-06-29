"""
JARVIS in-process bootstrap.

Re-homes the old add-on's post-install orchestration into the integration, so a
HACS install gets the same zero-touch voice setup with no separate container.

Runs once per version as a background task from async_setup_entry, only under
Supervisor (installing add-ons needs the Supervisor API). Every phase is
idempotent and best-effort — failures are logged, never fatal.

  1. Ensure Piper / Whisper / openWakeWord add-ons installed + started  (Supervisor REST)
  2. Download + verify the JARVIS voice into /share/piper               (HTTP)
  3. Restart Piper so it rescans the voice                              (Supervisor REST)
  4. Reload Wyoming config entries                                      (in-process)
  5. Create/update the JARVIS Assist pipeline + set preferred           (in-process, best-effort)

The HA-side steps the add-on did over a WebSocket are done in-process here (we
have `hass`), which is both cleaner and more robust than talking to HA's own
WebSocket from inside HA.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

SUPERVISOR = "http://supervisor"
PIPER_DIR = Path("/share/piper")
HF_BASE = "https://huggingface.co/jgkawell/jarvis/resolve/main/en/en_GB/jarvis"
MIN_ONNX_SIZE = 1_000_000  # smaller ⇒ corrupt download
MARKER_PATH = Path("/config/jarvis/.bootstrap_done")

REQUIRED_ADDONS = {
    "core_piper":        "Piper TTS",
    "core_whisper":      "Whisper STT",
    "core_openwakeword": "openWakeWord",
}


def supervisor_token() -> str:
    return os.environ.get("SUPERVISOR_TOKEN", "")


def is_supervised() -> bool:
    """True only when running under the Supervisor (HA OS / Supervised)."""
    return bool(supervisor_token())


# ── Supervisor REST ──────────────────────────────────────────────────────────

async def _sup(hass: HomeAssistant, method: str, path: str,
               *, timeout: int = 60) -> tuple[int, Optional[dict]]:
    """Call the Supervisor REST API. Returns (status, body|None)."""
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {supervisor_token()}"}
    try:
        async with session.request(
            method, f"{SUPERVISOR}{path}", headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            try:
                body = await resp.json()
            except Exception:
                body = None
            return resp.status, body
    except Exception as exc:
        _LOGGER.warning("JARVIS bootstrap: supervisor %s %s failed: %s", method, path, exc)
        return 0, None


async def _addon_info(hass: HomeAssistant, slug: str) -> Optional[dict]:
    status, body = await _sup(hass, "GET", f"/addons/{slug}/info")
    if status == 200 and body and "data" in body:
        return body["data"]
    return None


async def _addon_action(hass: HomeAssistant, slug: str, action: str,
                        *, timeout: int = 600) -> bool:
    if action == "install":
        status, _ = await _sup(hass, "POST", f"/store/addons/{slug}/install", timeout=timeout)
        if status in (200, 202):
            return True
        status, _ = await _sup(hass, "POST", f"/addons/{slug}/install", timeout=timeout)
        return status in (200, 202)
    status, _ = await _sup(hass, "POST", f"/addons/{slug}/{action}", timeout=timeout)
    return status in (200, 202)


async def _wait_addon_state(hass: HomeAssistant, slug: str, target: str,
                            tries: int = 30, delay: float = 2.0) -> bool:
    for _ in range(tries):
        info = await _addon_info(hass, slug) or {}
        if info.get("state") == target:
            return True
        await asyncio.sleep(delay)
    return False


async def _ensure_addon(hass: HomeAssistant, slug: str, friendly: str) -> bool:
    """Install (if needed) + start (if needed). Returns True on success."""
    info = await _addon_info(hass, slug)
    if info is None or not info.get("version"):
        _LOGGER.info("JARVIS bootstrap: installing %s", friendly)
        if not await _addon_action(hass, slug, "install"):
            return False
        for _ in range(60):  # up to 5 min for the image pull
            await asyncio.sleep(5)
            info = await _addon_info(hass, slug) or {}
            if info.get("version"):
                break
        else:
            _LOGGER.warning("JARVIS bootstrap: %s install timed out", friendly)
            return False
    if (info or {}).get("state") == "started":
        return True
    _LOGGER.info("JARVIS bootstrap: starting %s", friendly)
    if not await _addon_action(hass, slug, "start", timeout=60):
        return False
    return await _wait_addon_state(hass, slug, "started")


# ── Voice model download ─────────────────────────────────────────────────────

async def _download_file(hass: HomeAssistant, url: str, dest: Path) -> int:
    """Download a file to dest (file I/O off-loop). Returns bytes written (0 on fail)."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            if resp.status != 200:
                return 0
            data = await resp.read()
        await hass.async_add_executor_job(dest.write_bytes, data)
        return len(data)
    except Exception as exc:
        _LOGGER.warning("JARVIS bootstrap: download %s failed: %s", url, exc)
        await hass.async_add_executor_job(lambda: dest.unlink(missing_ok=True))
        return 0


def _voice_present(quality: str) -> bool:
    onnx = PIPER_DIR / f"en_GB-jarvis-{quality}.onnx"
    js = PIPER_DIR / f"en_GB-jarvis-{quality}.onnx.json"
    return onnx.exists() and js.exists() and onnx.stat().st_size > MIN_ONNX_SIZE


async def _try_quality(hass: HomeAssistant, quality: str) -> bool:
    onnx = PIPER_DIR / f"en_GB-jarvis-{quality}.onnx"
    js = PIPER_DIR / f"en_GB-jarvis-{quality}.onnx.json"
    candidates = [
        (f"{HF_BASE}/{quality}/jarvis-{quality}.onnx",
         f"{HF_BASE}/{quality}/jarvis-{quality}.onnx.json"),
        (f"{HF_BASE}/{quality}/en_GB-jarvis-{quality}.onnx",
         f"{HF_BASE}/{quality}/en_GB-jarvis-{quality}.onnx.json"),
    ]
    for onnx_url, json_url in candidates:
        if await _download_file(hass, onnx_url, onnx) > MIN_ONNX_SIZE:
            if await _download_file(hass, json_url, js) > 0:
                return True
            await hass.async_add_executor_job(lambda: onnx.unlink(missing_ok=True))
    return False


async def _download_voice(hass: HomeAssistant, quality: str) -> bool:
    await hass.async_add_executor_job(lambda: PIPER_DIR.mkdir(parents=True, exist_ok=True))
    for q in ("high", "medium"):
        if await hass.async_add_executor_job(_voice_present, q):
            _LOGGER.info("JARVIS bootstrap: voice en_GB-jarvis-%s already present", q)
            return True
    if await _try_quality(hass, quality):
        return True
    if quality == "high" and await _try_quality(hass, "medium"):
        _LOGGER.info("JARVIS bootstrap: fell back to medium voice (high not hosted)")
        return True
    _LOGGER.warning(
        "JARVIS bootstrap: voice download failed. Manual: "
        "https://huggingface.co/jgkawell/jarvis/tree/main/en/en_GB/jarvis/%s "
        "→ copy both files to %s/", quality, PIPER_DIR)
    return False


# ── HA-side steps (in-process) ───────────────────────────────────────────────

async def _reload_wyoming(hass: HomeAssistant) -> int:
    """Reload all Wyoming config entries so HA reconnects to the add-on services."""
    n = 0
    for entry in hass.config_entries.async_entries("wyoming"):
        try:
            await hass.config_entries.async_reload(entry.entry_id)
            n += 1
        except Exception as exc:
            _LOGGER.debug("JARVIS bootstrap: wyoming reload %s failed: %s", entry.entry_id, exc)
    if n:
        _LOGGER.info("JARVIS bootstrap: reloaded %d Wyoming entr%s", n, "y" if n == 1 else "ies")
    return n


def _find_jarvis_agent(hass: HomeAssistant) -> Optional[str]:
    """The conversation entity this integration registered (its agent id)."""
    try:
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        for ent in reg.entities.values():
            if ent.domain == "conversation" and ent.platform == "jarvis":
                return ent.entity_id
    except Exception:
        pass
    # fallback: a conversation.* state mentioning jarvis
    for state in hass.states.async_all("conversation"):
        if "jarvis" in state.entity_id.lower():
            return state.entity_id
    return None


def _find_engine(hass: HomeAssistant, domain: str, hint: str) -> Optional[str]:
    """Pick an engine entity_id in `domain` matching `hint` (e.g. stt/whisper)."""
    states = hass.states.async_all(domain)
    for st in states:
        if hint in st.entity_id.lower():
            return st.entity_id
    return states[0].entity_id if states else None


async def _wait_for_agent(hass: HomeAssistant, tries: int = 40, delay: float = 3.0) -> Optional[str]:
    for _ in range(tries):
        agent = _find_jarvis_agent(hass)
        if agent:
            return agent
        await asyncio.sleep(delay)
    return None


def _manual_pipeline_hint(voice_quality: str) -> None:
    _LOGGER.info(
        "JARVIS bootstrap: set the pipeline up manually under Settings → Voice "
        "Assistants — Conversation: JARVIS, STT: faster-whisper, TTS: piper / "
        "en_GB-jarvis-%s, Wake word: hey_jarvis.", voice_quality)


async def _create_pipeline(hass: HomeAssistant, voice_quality: str) -> bool:
    """
    Create/update the JARVIS Assist pipeline and set it preferred. Best-effort:
    the assist_pipeline API varies across HA versions, so any failure logs clear
    manual steps instead of raising.
    """
    try:
        from homeassistant.components import assist_pipeline
    except Exception:
        _manual_pipeline_hint(voice_quality)
        return False

    agent = _find_jarvis_agent(hass)
    stt = _find_engine(hass, "stt", "whisper")
    tts = _find_engine(hass, "tts", "piper")
    if not agent or not stt or not tts:
        _LOGGER.warning("JARVIS bootstrap: agent=%s stt=%s tts=%s — can't build pipeline yet",
                        agent, stt, tts)
        _manual_pipeline_hint(voice_quality)
        return False

    tts_voice = f"en_GB-jarvis-{voice_quality}"
    try:
        # Don't duplicate if a JARVIS pipeline already exists.
        existing = None
        try:
            for p in assist_pipeline.async_get_pipelines(hass):
                if getattr(p, "name", "") == "JARVIS":
                    existing = p
                    break
        except Exception:
            existing = None
        if existing is not None:
            _LOGGER.info("JARVIS bootstrap: JARVIS pipeline already exists — leaving as-is")
            return True

        pipeline = await assist_pipeline.async_create_default_pipeline(
            hass, stt_engine_id=stt, tts_engine_id=tts, pipeline_name="JARVIS")
        if pipeline is None:
            _LOGGER.warning("JARVIS bootstrap: default pipeline creation returned nothing")
            _manual_pipeline_hint(voice_quality)
            return False
        _LOGGER.info("JARVIS bootstrap: created JARVIS pipeline (agent=%s stt=%s tts=%s/%s)",
                     agent, stt, tts, tts_voice)
        return True
    except Exception as exc:
        _LOGGER.warning("JARVIS bootstrap: pipeline creation failed (%s)", exc)
        _manual_pipeline_hint(voice_quality)
        return False


# ── Run-once marker ──────────────────────────────────────────────────────────

def _read_marker() -> dict:
    try:
        if MARKER_PATH.exists():
            return json.loads(MARKER_PATH.read_text())
    except Exception:
        pass
    return {}


def _write_marker(version: str, status: dict) -> None:
    try:
        MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARKER_PATH.write_text(json.dumps({"version": version, **status}))
    except Exception as exc:
        _LOGGER.debug("JARVIS bootstrap: could not write marker: %s", exc)


def _current_version() -> str:
    try:
        manifest = Path(__file__).parent / "manifest.json"
        return json.loads(manifest.read_text()).get("version", "0")
    except Exception:
        return "0"


# ── Orchestration ────────────────────────────────────────────────────────────

async def async_run_bootstrap(hass: HomeAssistant, *, force: bool = False) -> dict:
    """
    Full bootstrap. Idempotent + best-effort. Returns a status dict. Safe to call
    on every setup — it self-gates on the run-once marker and the Supervisor.
    """
    from . import jarvis_config

    status = {"supervised": False, "addons_ok": False, "voice_ok": False,
              "wyoming_ok": False, "pipeline_ok": False, "skipped": None}

    if not bool(jarvis_config.get("auto_bootstrap", True)):
        status["skipped"] = "auto_bootstrap disabled"
        return status

    if not is_supervised():
        status["skipped"] = "no Supervisor — voice auto-setup needs HA OS/Supervised"
        _LOGGER.info("JARVIS bootstrap: %s; skipping voice-stack setup", status["skipped"])
        return status
    status["supervised"] = True

    version = await hass.async_add_executor_job(_current_version)
    marker = await hass.async_add_executor_job(_read_marker)
    if not force and marker.get("version") == version and marker.get("addons_ok") and marker.get("voice_ok"):
        status["skipped"] = "already bootstrapped this version"
        return status

    voice_quality = str(jarvis_config.get("voice_quality", "medium"))
    tts_provider = str(jarvis_config.get("tts_provider", "piper_jarvis"))
    auto_pipeline = bool(jarvis_config.get("auto_pipeline", True))

    _LOGGER.info("JARVIS bootstrap: starting (version=%s quality=%s)", version, voice_quality)

    # Phase 1 — prerequisite add-ons
    addons_ok = True
    for slug, friendly in REQUIRED_ADDONS.items():
        if not await _ensure_addon(hass, slug, friendly):
            addons_ok = False
    status["addons_ok"] = addons_ok

    # Phase 2 — voice model
    if tts_provider == "piper_jarvis":
        status["voice_ok"] = await _download_voice(hass, voice_quality)
    else:
        status["voice_ok"] = True

    # Phase 3 — restart Piper to rescan the voice
    if tts_provider == "piper_jarvis" and status["voice_ok"]:
        if await _addon_action(hass, "core_piper", "restart", timeout=60):
            await _wait_addon_state(hass, "core_piper", "started")
        await asyncio.sleep(5)

    # Phase 4 — reload Wyoming
    try:
        await _reload_wyoming(hass)
        status["wyoming_ok"] = True
    except Exception as exc:
        _LOGGER.debug("JARVIS bootstrap: wyoming reload error: %s", exc)

    # Phase 5 — Assist pipeline (best-effort)
    if auto_pipeline:
        agent = await _wait_for_agent(hass)
        if agent:
            status["pipeline_ok"] = await _create_pipeline(hass, voice_quality)
        else:
            _LOGGER.warning("JARVIS bootstrap: conversation agent didn't register in time")
            _manual_pipeline_hint(voice_quality)

    await hass.async_add_executor_job(_write_marker, version, status)
    _LOGGER.info("JARVIS bootstrap: complete — %s", status)
    return status


def schedule_bootstrap(hass: HomeAssistant) -> None:
    """
    Launch the bootstrap as a background task once HA has finished starting.
    No-ops cleanly off-Supervisor. Called from async_setup_entry.
    """
    async def _runner(_event=None) -> None:
        try:
            await async_run_bootstrap(hass)
        except Exception as exc:
            _LOGGER.warning("JARVIS bootstrap: unexpected error: %s", exc)

    if not is_supervised():
        return  # nothing to do without the Supervisor
    if hass.is_running:
        hass.async_create_background_task(_runner(), "jarvis_bootstrap")
    else:
        from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _runner)
