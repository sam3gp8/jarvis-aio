#!/usr/bin/env python3
"""
JARVIS AIO bootstrap orchestrator.
Completes post-install steps programmatically so the user touches nothing:

  1. Ensure Piper / Whisper / openWakeWord addons are installed + started
  2. Download the Jarvis voice model (integrity-verified)
  3. RESTART the Piper addon so it re-scans /share/piper for the new voice
  4. Reload Wyoming integration so HA reconnects with the updated voice list
  5. Create the JARVIS Assist pipeline (best-effort — HA WS schemas vary)
  6. Set JARVIS pipeline as default

Uses ONLY Supervisor REST + HA WebSocket proxy. Never touches .storage directly.
All steps idempotent. Each failure is logged but does not abort the chain.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

SUPERVISOR = "http://supervisor"
TOKEN      = os.environ.get("SUPERVISOR_TOKEN", "")
PIPER_DIR  = Path("/share/piper")
HF_BASE    = "https://huggingface.co/jgkawell/jarvis/resolve/main/en/en_GB/jarvis"
MIN_ONNX_SIZE = 1_000_000  # smaller = corrupt download

# Addon slugs — these are the official HA core addon slugs
REQUIRED_ADDONS = {
    "core_piper":         "Piper TTS",
    "core_whisper":       "Whisper STT",
    "core_openwakeword":  "openWakeWord",
}


# ─── Logging ──────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "INFO") -> None:
    print(f"  [{level}] {msg}", flush=True)


# ─── Supervisor REST ──────────────────────────────────────────────────────────

def supervisor_request(
    method: str, path: str, data: dict | None = None, timeout: int = 60
) -> tuple[int, dict | None]:
    """GET/POST supervisor REST API. Returns (status_code, body | None)."""
    url = f"{SUPERVISOR}{path}"
    if data is None:
        body = b""
    else:
        body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body if method != "GET" else None, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if method != "GET":
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None
    except Exception as exc:
        log(f"supervisor {method} {path} error: {exc}", "WARN")
        return 0, None


def addon_info(slug: str) -> dict | None:
    status, body = supervisor_request("GET", f"/addons/{slug}/info")
    if status == 200 and body and "data" in body:
        return body["data"]
    return None


def addon_is_installed(slug: str) -> bool:
    info = addon_info(slug)
    return info is not None and bool(info.get("version"))


def addon_install(slug: str, friendly: str) -> bool:
    """Install addon from the store. Uses the correct /store/addons path."""
    log(f"Installing {friendly} from store...")
    # Longer timeout — install pulls a Docker image (~100+ MB)
    status, body = supervisor_request("POST", f"/store/addons/{slug}/install", timeout=600)
    if status in (200, 202):
        return True
    # Fallback for older supervisor versions
    status2, body2 = supervisor_request("POST", f"/addons/{slug}/install", timeout=600)
    if status2 in (200, 202):
        return True
    log(f"Install of {friendly} failed (primary={status}, fallback={status2}): {body or body2}", "WARN")
    return False


def addon_start(slug: str, friendly: str) -> bool:
    status, body = supervisor_request("POST", f"/addons/{slug}/start", timeout=60)
    if status in (200, 202):
        return True
    log(f"Start of {friendly} failed ({status}): {body}", "WARN")
    return False


def addon_restart(slug: str, friendly: str) -> bool:
    status, body = supervisor_request("POST", f"/addons/{slug}/restart", timeout=60)
    if status in (200, 202):
        return True
    log(f"Restart of {friendly} failed ({status}): {body}", "WARN")
    return False


def wait_for_addon_state(slug: str, target_state: str, tries: int = 30, sleep_s: float = 2) -> bool:
    for _ in range(tries):
        info = addon_info(slug) or {}
        if info.get("state") == target_state:
            return True
        time.sleep(sleep_s)
    return False


def ensure_addon(slug: str, friendly: str) -> bool:
    """Install (if needed) and start (if needed). Returns True on success."""
    # Check current state
    info = addon_info(slug)

    if info is None:
        # Addon not found in supervisor at all — not installed
        if not addon_install(slug, friendly):
            return False
        # Wait up to 5 min for install to complete (image pull)
        for _ in range(60):
            time.sleep(5)
            if addon_is_installed(slug):
                break
        else:
            log(f"{friendly} install timed out — check Supervisor logs.", "WARN")
            return False
        info = addon_info(slug) or {}

    elif not info.get("version"):
        # Known to store but not installed
        if not addon_install(slug, friendly):
            return False
        for _ in range(60):
            time.sleep(5)
            if addon_is_installed(slug):
                break
        info = addon_info(slug) or {}

    state = info.get("state", "unknown")
    if state == "started":
        log(f"{friendly} already running.")
        return True

    log(f"Starting {friendly}...")
    if not addon_start(slug, friendly):
        return False

    if wait_for_addon_state(slug, "started"):
        log(f"{friendly} started.")
        return True

    log(f"{friendly} did not reach 'started' state in time.", "WARN")
    return False


# ─── Jarvis voice download ────────────────────────────────────────────────────

def _download_one(url: str, dest: Path) -> int:
    """Download a single file. Returns size in bytes (0 on failure)."""
    try:
        urllib.request.urlretrieve(url, dest)
        return dest.stat().st_size if dest.exists() else 0
    except Exception as exc:
        log(f"Download of {url} failed: {exc}", "WARN")
        dest.unlink(missing_ok=True)
        return 0


def _try_quality(quality: str) -> bool:
    """
    Attempt to download the voice for a specific quality level.
    HF file structure (as of 2026-04): en/en_GB/jarvis/{quality}/jarvis-{quality}.onnx
    Local file structure (what Piper/Wyoming expects): en_GB-jarvis-{quality}.onnx
    So we download under the HF name and RENAME to the Piper name.
    """
    onnx = PIPER_DIR / f"en_GB-jarvis-{quality}.onnx"
    json_file = PIPER_DIR / f"en_GB-jarvis-{quality}.onnx.json"

    # Try the real HF paths (shorter filename at HF)
    candidates = [
        # Actual structure on HF as of 2026-04
        (
            f"{HF_BASE}/{quality}/jarvis-{quality}.onnx",
            f"{HF_BASE}/{quality}/jarvis-{quality}.onnx.json",
        ),
        # Fallback: some older forks mirror with the full name
        (
            f"{HF_BASE}/{quality}/en_GB-jarvis-{quality}.onnx",
            f"{HF_BASE}/{quality}/en_GB-jarvis-{quality}.onnx.json",
        ),
    ]

    for attempt, (onnx_url, json_url) in enumerate(candidates, 1):
        log(f"Downloading voice (attempt {attempt}/{len(candidates)}, quality={quality})...")
        size = _download_one(onnx_url, onnx)
        if size > MIN_ONNX_SIZE:
            json_size = _download_one(json_url, json_file)
            if json_size > 0:
                log(f"Voice model downloaded and verified ({size} bytes).")
                return True
            log("ONNX downloaded but JSON fetch failed — cleaning up.", "WARN")
            onnx.unlink(missing_ok=True)
        else:
            log(f"ONNX too small or missing ({size} bytes) — trying next URL.", "WARN")

    return False


def download_jarvis_voice(quality: str) -> bool:
    """
    Download + verify the JARVIS voice model.
    If 'high' fails, falls back to 'medium' (which is the more reliably hosted
    variant). Returns True if at least one quality level downloaded successfully.
    """
    PIPER_DIR.mkdir(parents=True, exist_ok=True)

    # Short-circuit if ANY usable voice is already present
    for q in ("high", "medium"):
        onnx = PIPER_DIR / f"en_GB-jarvis-{q}.onnx"
        json_file = PIPER_DIR / f"en_GB-jarvis-{q}.onnx.json"
        if onnx.exists() and json_file.exists() and onnx.stat().st_size > MIN_ONNX_SIZE:
            log(f"Voice model already present: en_GB-jarvis-{q} ({onnx.stat().st_size} bytes).")
            return True

    # Try requested quality first
    if _try_quality(quality):
        return True

    # Fall back to medium if user asked for high
    if quality == "high":
        log("'high' quality failed — trying 'medium' as fallback.", "WARN")
        if _try_quality("medium"):
            log("Fell back to medium voice. This is normal — 'high' isn't always hosted.")
            return True

    log("Voice download failed after all attempts.", "ERROR")
    log(f"Manual install: https://huggingface.co/jgkawell/jarvis/tree/main/en/en_GB/jarvis/{quality}", "ERROR")
    log(f"  → copy both files to {PIPER_DIR}/  (rename to en_GB-jarvis-{quality}.onnx[.json])", "ERROR")
    return False


# ─── HA WebSocket (via supervisor proxy) ──────────────────────────────────────

async def ws_call(messages: list[dict], timeout: float = 20.0) -> list[dict]:
    """
    Open proxied WebSocket to HA, auth with SUPERVISOR_TOKEN, send each message,
    return list of responses in order. Handles both old and new websockets lib API.
    """
    try:
        import websockets
    except ImportError:
        log("websockets library not installed — cannot talk to HA WebSocket API.", "ERROR")
        return []

    # Handle API rename between websockets versions: extra_headers → additional_headers
    connect_kwargs: dict = {}
    try:
        # Newer API
        ws = await asyncio.wait_for(
            websockets.connect(
                "ws://supervisor/core/websocket",
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
            ),
            timeout=timeout,
        )
    except TypeError:
        # Older websockets library
        ws = await asyncio.wait_for(
            websockets.connect(
                "ws://supervisor/core/websocket",
                extra_headers={"Authorization": f"Bearer {TOKEN}"},
            ),
            timeout=timeout,
        )

    results: list[dict] = []
    try:
        # Auth handshake
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if hello.get("type") not in ("auth_required", "auth_ok"):
            log(f"Unexpected WS greeting: {hello}", "WARN")

        if hello.get("type") != "auth_ok":
            await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
            auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if auth_resp.get("type") != "auth_ok":
                raise RuntimeError(f"WS auth failed: {auth_resp}")

        # Send messages sequentially
        for i, msg in enumerate(messages, start=1):
            await ws.send(json.dumps({**msg, "id": i}))
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if reply.get("id") == i:
                    results.append(reply)
                    break
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    return results


# ─── Wyoming reload ──────────────────────────────────────────────────────────

async def reload_wyoming() -> None:
    """
    Reload all Wyoming config entries so HA reconnects to addon services.

    HA's WS API has had schema churn around config_entries/get — specifically
    the 'domain_filter' param was removed in newer versions (2026.x).
    We therefore skip the optional filter entirely and always do client-side
    filtering, which works across all recent HA versions.
    """
    try:
        entries = await ws_call([{"type": "config_entries/get"}])
        result = entries[0].get("result") if entries else None
        all_entries: list = []
        if isinstance(result, list):
            all_entries = result
        elif isinstance(result, dict):
            all_entries = result.get("entries", [])

        wyoming_entries = [e for e in all_entries if e.get("domain") == "wyoming"]

        if not wyoming_entries:
            log("No Wyoming config entries found — skipping reload.", "WARN")
            return

        msgs = [
            {"type": "config_entries/reload", "entry_id": e.get("entry_id")}
            for e in wyoming_entries if e.get("entry_id")
        ]
        if msgs:
            await ws_call(msgs)
            log(f"Reloaded {len(msgs)} Wyoming integration entr(y|ies).")
    except Exception as exc:
        log(f"Wyoming reload failed (non-fatal): {exc}", "WARN")


# ─── Pipeline creation (best-effort) ─────────────────────────────────────────

async def create_jarvis_pipeline(voice_quality: str) -> bool:
    """
    Build the JARVIS pipeline and set it as preferred.
    Best-effort: if any HA WebSocket command fails, logs and returns False.
    """
    try:
        results = await ws_call([
            {"type": "conversation/agent/list"},
            {"type": "stt/engine/list"},
            {"type": "tts/engine/list"},
            {"type": "assist_pipeline/pipeline/list"},
        ])

        if len(results) < 4:
            log("Not all introspection calls returned — skipping pipeline build.", "WARN")
            return False

        # Defensive parsing — every HA version might shape results slightly differently
        def get_list(resp: dict, key: str) -> list:
            r = resp.get("result") or {}
            if isinstance(r, list):
                return r
            return r.get(key, [])

        conv_agents = get_list(results[0], "agents")
        stt_engines = get_list(results[1], "providers")
        tts_engines = get_list(results[2], "providers")
        pipelines   = get_list(results[3], "pipelines")

        # Conversation agent
        jarvis_conv = None
        for a in conv_agents:
            agent_id = str(a.get("id") or a.get("agent_id") or "")
            if "jarvis" in agent_id.lower():
                jarvis_conv = agent_id
                break
        if not jarvis_conv:
            log(
                "JARVIS conversation agent not found in conversation/agent/list.",
                "WARN",
            )
            log(f"  Available agents: {[a.get('id') or a.get('agent_id') for a in conv_agents]}", "WARN")
            return False

        # STT — prefer whisper
        def pick_engine(engines: list, hint: str) -> str | None:
            for e in engines:
                eid = str(e.get("engine_id") or e.get("id") or "")
                if hint in eid.lower():
                    return eid
            if engines:
                first = engines[0]
                return str(first.get("engine_id") or first.get("id") or "")
            return None

        stt_engine = pick_engine(stt_engines, "whisper")
        tts_engine = pick_engine(tts_engines, "piper")

        if not stt_engine or not tts_engine:
            log(
                f"Missing STT ({stt_engine}) or TTS ({tts_engine}) engines — "
                "Whisper/Piper addons may not be ready yet.",
                "WARN",
            )
            return False

        tts_voice = f"en_GB-jarvis-{voice_quality}"

        log(f"Pipeline: conv={jarvis_conv} stt={stt_engine} tts={tts_engine}/{tts_voice}")

        payload = {
            "name": "JARVIS",
            "conversation_engine": jarvis_conv,
            "conversation_language": "en",
            "language": "en",
            "stt_engine": stt_engine,
            "stt_language": "en",
            "tts_engine": tts_engine,
            "tts_language": "en",
            "tts_voice": tts_voice,
            # HA 2026.x requires these keys (can be None if wake word handled elsewhere)
            "wake_word_entity": None,
            "wake_word_id": None,
        }

        existing = next((p for p in pipelines if p.get("name") == "JARVIS"), None)
        if existing:
            pipeline_id = existing.get("pipeline_id") or existing.get("id")
            log(f"JARVIS pipeline exists ({pipeline_id}) — updating.")
            result = await ws_call([{
                "type": "assist_pipeline/pipeline/update",
                "pipeline_id": pipeline_id,
                **payload,
            }])
        else:
            log("Creating JARVIS pipeline...")
            result = await ws_call([{
                "type": "assist_pipeline/pipeline/create",
                **payload,
            }])
            created = result[0].get("result") or {}
            pipeline_id = created.get("pipeline_id") or created.get("id")
            if not pipeline_id:
                log(f"Pipeline creation returned no ID: {result[0]}", "WARN")
                return False

        # Check for errors in update/create
        if result and not result[0].get("success", True):
            log(f"Pipeline API error: {result[0].get('error')}", "WARN")
            return False

        # Set as preferred
        pref_result = await ws_call([{
            "type": "assist_pipeline/pipeline/set_preferred",
            "pipeline_id": pipeline_id,
        }])
        if pref_result and not pref_result[0].get("success", True):
            log(f"set_preferred error (non-fatal): {pref_result[0].get('error')}", "WARN")

        log(f"JARVIS pipeline ({pipeline_id}) is now default.")
        return True

    except Exception as exc:
        log(f"Pipeline creation failed: {exc}", "WARN")
        log(traceback.format_exc(), "DEBUG")
        return False


# ─── Main flow ───────────────────────────────────────────────────────────────

STATUS_FILE = Path("/tmp/jarvis_bootstrap_status.json")


def _write_status(status: dict) -> None:
    """Write bootstrap phase results so run.sh can print an honest banner."""
    try:
        STATUS_FILE.write_text(json.dumps(status))
    except Exception as exc:
        log(f"Could not write status file: {exc}", "WARN")


async def main() -> int:
    honorific     = os.environ.get("JARVIS_HONORIFIC", "sir")
    voice_quality = os.environ.get("JARVIS_VOICE_QUALITY", "medium")
    tts_provider  = os.environ.get("JARVIS_TTS_PROVIDER", "piper_jarvis")
    auto_pipeline = os.environ.get("JARVIS_AUTO_PIPELINE", "true").lower() == "true"

    status = {
        "addons_ok":       False,
        "voice_ok":        False,
        "agent_ok":        False,
        "pipeline_ok":     False,
        "voice_quality":   voice_quality,
    }
    _write_status(status)  # Baseline: everything false

    if not TOKEN:
        log("SUPERVISOR_TOKEN not set — aborting.", "ERROR")
        _write_status(status)
        return 1

    log(f"Bootstrap start: honorific={honorific} quality={voice_quality} "
        f"provider={tts_provider} auto_pipeline={auto_pipeline}")

    # ── Phase 1: Prerequisite addons ─────────────────────────────────────────
    log("─" * 60)
    log("Phase 1/5: Ensure prerequisite addons installed + running")
    addons_all_ok = True
    for slug, name in REQUIRED_ADDONS.items():
        if not ensure_addon(slug, name):
            addons_all_ok = False
    status["addons_ok"] = addons_all_ok
    _write_status(status)

    # ── Phase 2: Voice model ─────────────────────────────────────────────────
    voice_ok = False
    if tts_provider == "piper_jarvis":
        log("─" * 60)
        log("Phase 2/5: Download JARVIS voice model")
        voice_ok = download_jarvis_voice(voice_quality)
    else:
        voice_ok = True  # not applicable
    status["voice_ok"] = voice_ok
    _write_status(status)

    # ── Phase 3: Restart Piper addon (so it rescans /share/piper) ────────────
    if tts_provider == "piper_jarvis" and voice_ok:
        log("─" * 60)
        log("Phase 3/5: Restart Piper addon to load the new voice")
        if addon_restart("core_piper", "Piper TTS"):
            if wait_for_addon_state("core_piper", "started", tries=30, sleep_s=2):
                log("Piper restarted with Jarvis voice loaded.")
            else:
                log("Piper restart requested but state didn't reach 'started'.", "WARN")
        # Give Piper a moment to fully initialize before Wyoming reconnects
        await asyncio.sleep(5)

    # ── Phase 4: Reload Wyoming integration ──────────────────────────────────
    log("─" * 60)
    log("Phase 4/5: Reload Wyoming integration")
    await reload_wyoming()

    # Wait for JARVIS custom component to register its conversation agent.
    # On first install HA has to pip-install groq, which can take 30-60s.
    log("Waiting for JARVIS conversation agent to register (up to 120s)...")
    agent_found = False
    last_agents = []
    for attempt in range(40):  # 40 × 3s = 120s
        try:
            res = await ws_call([{"type": "conversation/agent/list"}])
            if res:
                agents_raw = res[0].get("result") or {}
                agents = agents_raw if isinstance(agents_raw, list) else agents_raw.get("agents", [])
                last_agents = [str(a.get("id") or a.get("agent_id") or "") for a in agents]
                if any("jarvis" in a.lower() for a in last_agents):
                    log(f"JARVIS conversation agent ready (after {attempt * 3}s).")
                    agent_found = True
                    break
        except Exception:
            pass
        if attempt > 0 and attempt % 10 == 0:
            log(f"  Still waiting... ({attempt * 3}s elapsed, agents: {last_agents})")
        await asyncio.sleep(3)

    if not agent_found:
        log("JARVIS conversation agent did not register within 120s timeout.", "WARN")
        log(f"Agents HA has registered: {last_agents}", "WARN")
        log("Common causes:", "WARN")
        log("  1. HA failed to pip-install 'groq' package (check HA core logs)", "WARN")
        log("  2. Python error in custom_components/jarvis — check logs for", "WARN")
        log("     'Error importing custom_components.jarvis' or similar", "WARN")
        log("  3. jarvis_config.json has a malformed field", "WARN")
        log("Try: Settings → System → Logs, search 'jarvis' for the actual error.", "WARN")
    status["agent_ok"] = agent_found
    _write_status(status)

    # ── Phase 5: Create Assist pipeline ──────────────────────────────────────
    pipeline_ok = False
    if auto_pipeline and agent_found:
        log("─" * 60)
        log("Phase 5/5: Create JARVIS Assist pipeline")
        pipeline_ok = await create_jarvis_pipeline(voice_quality)
        if not pipeline_ok:
            log(
                "Pipeline auto-setup incomplete. Create manually: "
                "Settings → Voice Assistants → Add Assistant",
                "INFO",
            )
            log(f"  Conversation: JARVIS     TTS: piper / en_GB-jarvis-{voice_quality}", "INFO")
            log("  STT: faster-whisper      Wake word: hey_jarvis", "INFO")
    elif auto_pipeline and not agent_found:
        log("Phase 5/5: Skipped — JARVIS agent not registered, pipeline would fail.", "WARN")
    else:
        log("Phase 5/5: Skipped (auto_pipeline=false)")
    status["pipeline_ok"] = pipeline_ok
    _write_status(status)

    log("─" * 60)
    log("Bootstrap complete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        log(f"Unhandled error: {exc}", "ERROR")
        log(traceback.format_exc(), "DEBUG")
        sys.exit(1)
