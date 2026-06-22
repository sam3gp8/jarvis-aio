#!/usr/bin/with-contenv bashio
# ==============================================================================
# JARVIS AI Assistant — All-In-One Installer v6.10.1
# Zero-touch install. After pressing Start you do nothing else.
# ==============================================================================
set -euo pipefail

COMPONENT_SRC="/jarvis_component"
COMPONENT_DST="/config/custom_components/jarvis"
PERSONA_FILE="/config/jarvis_persona.txt"
CONFIG_FILE="/config/jarvis_config.json"

# ── Read addon config ────────────────────────────────────────────────────────
API_KEY=$(bashio::config 'groq_api_key')
MODEL=$(bashio::config 'model')
LLM_PROVIDER=$(bashio::config 'llm_provider' || echo "groq")
LLM_BASE_URL=$(bashio::config 'llm_base_url' || echo "")
HONORIFIC=$(bashio::config 'honorific')
DIRECTIVE_PRESET=$(bashio::config 'directive_preset' || echo "guardian_steward")
DIRECTIVE=$(bashio::config 'directive' || echo "")
QUALITY=$(bashio::config 'voice_quality')
TTS_PROVIDER=$(bashio::config 'tts_provider')
TTS_ENGINE=$(bashio::config 'tts_engine')
TTS_PREMIUM_ENGINE=$(bashio::config 'tts_premium_engine' || echo "")
USE_HASS_API=$(bashio::config 'use_home_control')
AUTO_PIPELINE=$(bashio::config 'auto_pipeline')

# Observer config
OBSERVER_ENABLED=$(bashio::config 'observer_enabled' || echo "false")
ANNOUNCEMENTS_ENABLED=$(bashio::config 'announcements_enabled' || echo "false")
SENTINEL_ENABLED=$(bashio::config 'sentinel_enabled' || echo "true")
GEMINI_API_KEY=$(bashio::config 'gemini_api_key' || echo "")
CLASSIFIER_PROVIDER=$(bashio::config 'classifier_provider' || echo "groq")
CLASSIFIER_MODEL=$(bashio::config 'classifier_model' || echo "llama-3.3-70b-versatile")
REASONING_PROVIDER=$(bashio::config 'reasoning_provider' || echo "groq")
REASONING_MODEL=$(bashio::config 'reasoning_model' || echo "llama-3.3-70b-versatile")
REVIEW_PROVIDER=$(bashio::config 'review_provider' || echo "groq")
REVIEW_MODEL=$(bashio::config 'review_model' || echo "llama-3.3-70b-versatile")
OBSERVER_QUIET_START=$(bashio::config 'observer_quiet_start' || echo "22:00")
OBSERVER_QUIET_END=$(bashio::config 'observer_quiet_end' || echo "07:00")
CLASSIFIER_RATE_LIMIT=$(bashio::config 'classifier_rate_limit' || echo "30")
COGNITION_ENABLED=$(bashio::config 'cognition_enabled' || echo "true")
COGNITION_THRESHOLD=$(bashio::config 'cognition_threshold' || echo "0.6")

# ── Validate ─────────────────────────────────────────────────────────────────
# An LLM is required, but it can be a CLOUD key OR a LOCAL model. Local-first
# users running Ollama (or any OpenAI-compatible endpoint) don't need a cloud
# API key at all — provider=ollama (with an optional llm_base_url) is enough.
if bashio::var.is_empty "${API_KEY}"; then
    if [ "${LLM_PROVIDER}" = "ollama" ] || [ "${LLM_PROVIDER}" = "custom" ] || ! bashio::var.is_empty "${LLM_BASE_URL}"; then
        bashio::log.info "No cloud API key set — using a local LLM (provider=${LLM_PROVIDER}${LLM_BASE_URL:+, url=${LLM_BASE_URL}})."
    else
        bashio::log.fatal "No LLM configured. Either set a cloud API key, or set llm_provider to 'ollama' (optionally with llm_base_url) to use a local model."
        exit 1
    fi
fi

# ── Banner ───────────────────────────────────────────────────────────────────
bashio::log.info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bashio::log.info "  JARVIS AI Assistant — AIO Installer v6.10.1"
bashio::log.info "  Provider       : ${LLM_PROVIDER}"
bashio::log.info "  Model          : ${MODEL}"
bashio::log.info "  Honorific      : ${HONORIFIC}"
bashio::log.info "  Directive      : ${DIRECTIVE_PRESET}"
bashio::log.info "  Voice quality  : ${QUALITY}"
bashio::log.info "  TTS provider   : ${TTS_PROVIDER}"
bashio::log.info "  Observer mode  : ${OBSERVER_ENABLED}"
bashio::log.info "  Announcements  : ${ANNOUNCEMENTS_ENABLED} (sentinel=${SENTINEL_ENABLED})"
bashio::log.info "  Classifier     : ${CLASSIFIER_PROVIDER} / ${CLASSIFIER_MODEL}"
bashio::log.info "  Reasoning      : ${REASONING_PROVIDER} / ${REASONING_MODEL}"
bashio::log.info "  Review         : ${REVIEW_PROVIDER} / ${REVIEW_MODEL}"
bashio::log.info "  Quiet hours    : ${OBSERVER_QUIET_START} — ${OBSERVER_QUIET_END}"
bashio::log.info "  Auto pipeline  : ${AUTO_PIPELINE}"
bashio::log.info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Component files ───────────────────────────────────────────────────────
bashio::log.info "[1/4] Installing JARVIS component..."

if [ ! -d "${COMPONENT_SRC}" ]; then
    bashio::log.fatal "Source directory ${COMPONENT_SRC} does not exist!"
    exit 1
fi

SRC_PY_COUNT=$(ls -1 "${COMPONENT_SRC}"/*.py 2>/dev/null | wc -l)
if [ "${SRC_PY_COUNT}" -eq 0 ]; then
    bashio::log.fatal "No Python files found in ${COMPONENT_SRC}!"
    exit 1
fi
bashio::log.info "  Source: ${SRC_PY_COUNT} Python files"

mkdir -p "${COMPONENT_DST}/translations"
rm -f "${COMPONENT_DST}"/*.py "${COMPONENT_DST}"/*.json "${COMPONENT_DST}"/*.yaml 2>/dev/null || true
rm -rf "${COMPONENT_DST}/__pycache__" 2>/dev/null || true

cp "${COMPONENT_SRC}"/*.py   "${COMPONENT_DST}/"
cp "${COMPONENT_SRC}"/*.json "${COMPONENT_DST}/"
cp "${COMPONENT_SRC}"/*.yaml "${COMPONENT_DST}/" 2>/dev/null || true

# Python subpackages (audio/, diagnostics/, vision/, memory/, intent/,
# automation/, …). The top-level cp above only takes root *.py — without this,
# every `from .audio import …` fails at load with ModuleNotFoundError and the
# whole integration refuses to set up. Selection is by __init__.py, so current
# and future packages are picked up automatically; asset dirs (frontend/,
# translations/, blueprints/) have no __init__.py and are untouched here.
# First clear previously-installed packages so renamed/removed modules don't
# linger at the destination.
if [ -d "${COMPONENT_DST}" ]; then
    for dst_pkg in "${COMPONENT_DST}"/*/; do
        [ -f "${dst_pkg}__init__.py" ] && rm -rf "${dst_pkg}"
    done
fi
SUBPKG_COUNT=0
for src_pkg in "${COMPONENT_SRC}"/*/; do
    [ -f "${src_pkg}__init__.py" ] || continue
    pkg_name=$(basename "${src_pkg}")
    cp -r "${COMPONENT_SRC}/${pkg_name}" "${COMPONENT_DST}/"
    rm -rf "${COMPONENT_DST}/${pkg_name}/__pycache__"
    SUBPKG_COUNT=$((SUBPKG_COUNT + 1))
done
bashio::log.info "  Installed ${SUBPKG_COUNT} Python subpackages"

# Frontend panel assets (JS + images/icons + styles)
FRONTEND_CHANGED="false"
if [ -d "${COMPONENT_SRC}/frontend" ]; then
    mkdir -p "${COMPONENT_DST}/frontend"
    # Detect whether the panel JS actually changed (content hash) so we can apply
    # frontend-only updates without waiting on a version-gated full HA restart.
    NEW_JS_HASH=""
    if [ -f "${COMPONENT_SRC}/frontend/${PANEL_JS_FILENAME:-jarvis-panel.js}" ]; then
        NEW_JS_HASH=$(sha1sum "${COMPONENT_SRC}/frontend/jarvis-panel.js" 2>/dev/null | awk '{print $1}')
    fi
    OLD_JS_HASH=""
    [ -f /config/.jarvis_frontend_hash ] && OLD_JS_HASH=$(cat /config/.jarvis_frontend_hash 2>/dev/null)
    cp "${COMPONENT_SRC}/frontend/"*.js  "${COMPONENT_DST}/frontend/" 2>/dev/null || true
    cp "${COMPONENT_SRC}/frontend/"*.png "${COMPONENT_DST}/frontend/" 2>/dev/null || true
    cp "${COMPONENT_SRC}/frontend/"*.svg "${COMPONENT_DST}/frontend/" 2>/dev/null || true
    cp "${COMPONENT_SRC}/frontend/"*.css "${COMPONENT_DST}/frontend/" 2>/dev/null || true
    cp "${COMPONENT_SRC}/frontend/"*.woff2 "${COMPONENT_DST}/frontend/" 2>/dev/null || true
    if [ -n "${NEW_JS_HASH}" ] && [ "${NEW_JS_HASH}" != "${OLD_JS_HASH}" ]; then
        FRONTEND_CHANGED="true"
        echo "${NEW_JS_HASH}" > /config/.jarvis_frontend_hash 2>/dev/null || true
    fi
    bashio::log.info "  Panel frontend assets copied (js/png/svg/css)"
fi

# Translations
if [ -d "${COMPONENT_SRC}/translations" ]; then
    cp "${COMPONENT_SRC}/translations/"*.json "${COMPONENT_DST}/translations/" 2>/dev/null || true
fi

DST_PY_COUNT=$(ls -1 "${COMPONENT_DST}"/*.py 2>/dev/null | wc -l)
bashio::log.info "  Installed: ${DST_PY_COUNT} .py files at ${COMPONENT_DST}"

if [ ! -f "${COMPONENT_DST}/manifest.json" ]; then
    bashio::log.fatal "manifest.json missing!"
    exit 1
fi

# Install blueprints
BLUEPRINT_DIR="/config/blueprints/automation/jarvis"
if [ -d "${COMPONENT_SRC}/blueprints" ]; then
    mkdir -p "${BLUEPRINT_DIR}"
    cp "${COMPONENT_SRC}/blueprints/"*.yaml "${BLUEPRINT_DIR}/" 2>/dev/null || true
    bashio::log.info "  Blueprints installed to ${BLUEPRINT_DIR}"
fi

# configuration.yaml
CONFIG_YAML="/config/configuration.yaml"
if [ -f "${CONFIG_YAML}" ]; then
    if ! grep -qE "^jarvis:" "${CONFIG_YAML}"; then
        echo -e "\n# Added by JARVIS addon\njarvis:" >> "${CONFIG_YAML}"
        bashio::log.info "  Added 'jarvis:' to configuration.yaml"
    fi
fi

# ── 2. Persona file ──────────────────────────────────────────────────────────
bashio::log.info "[2/4] Checking persona file..."
if [ -f "${PERSONA_FILE}" ]; then
    bashio::log.info "  Custom persona preserved."
else
    JARVIS_HONORIFIC="${HONORIFIC}" python3 - << 'PYEOF'
import sys, os
sys.path.insert(0, '/jarvis_component')
from const import JARVIS_PERSONA
honorific = os.environ.get('JARVIS_HONORIFIC', 'sir')
with open('/config/jarvis_persona.txt', 'w') as f:
    f.write(JARVIS_PERSONA.replace('{honorific}', honorific))
print("  Default persona written.")
PYEOF
fi

# ── 3. Config JSON ───────────────────────────────────────────────────────────
bashio::log.info "[3/4] Writing JARVIS config JSON..."

PREMIUM_CONTEXTS_JSON=$(bashio::config 'tts_premium_contexts' | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
try:
    lst = json.loads(raw)
    print(json.dumps(lst))
except:
    if raw and raw != 'null':
        print(json.dumps([x.strip() for x in raw.split(',') if x.strip()]))
    else:
        print('[]')
" 2>/dev/null || echo '[]')

JARVIS_API_KEY="${API_KEY}" \
JARVIS_MODEL="${MODEL}" \
JARVIS_LLM_PROVIDER="${LLM_PROVIDER}" \
JARVIS_LLM_BASE_URL="${LLM_BASE_URL}" \
JARVIS_HONORIFIC="${HONORIFIC}" \
JARVIS_DIRECTIVE_PRESET="${DIRECTIVE_PRESET}" \
JARVIS_DIRECTIVE="${DIRECTIVE}" \
JARVIS_VOICE_QUALITY="${QUALITY}" \
JARVIS_TTS_PROVIDER="${TTS_PROVIDER}" \
JARVIS_TTS_ENGINE="${TTS_ENGINE}" \
JARVIS_TTS_PREMIUM_ENGINE="${TTS_PREMIUM_ENGINE}" \
JARVIS_PREMIUM_CONTEXTS="${PREMIUM_CONTEXTS_JSON}" \
JARVIS_USE_HASS_API="${USE_HASS_API}" \
JARVIS_AUTO_PIPELINE="${AUTO_PIPELINE}" \
JARVIS_OBSERVER_ENABLED="${OBSERVER_ENABLED}" \
JARVIS_ANNOUNCEMENTS_ENABLED="${ANNOUNCEMENTS_ENABLED}" \
JARVIS_SENTINEL_ENABLED="${SENTINEL_ENABLED}" \
JARVIS_GEMINI_API_KEY="${GEMINI_API_KEY}" \
JARVIS_CLASSIFIER_PROVIDER="${CLASSIFIER_PROVIDER}" \
JARVIS_CLASSIFIER_MODEL="${CLASSIFIER_MODEL}" \
JARVIS_REASONING_PROVIDER="${REASONING_PROVIDER}" \
JARVIS_REASONING_MODEL="${REASONING_MODEL}" \
JARVIS_REVIEW_PROVIDER="${REVIEW_PROVIDER}" \
JARVIS_REVIEW_MODEL="${REVIEW_MODEL}" \
JARVIS_OBSERVER_QUIET_START="${OBSERVER_QUIET_START}" \
JARVIS_OBSERVER_QUIET_END="${OBSERVER_QUIET_END}" \
JARVIS_CLASSIFIER_RATE_LIMIT="${CLASSIFIER_RATE_LIMIT}" \
JARVIS_COGNITION_ENABLED="${COGNITION_ENABLED}" \
JARVIS_COGNITION_THRESHOLD="${COGNITION_THRESHOLD}" \
python3 - << 'PYEOF'
import json, os, hashlib

use_hass_api     = os.environ.get('JARVIS_USE_HASS_API', 'true').lower() == 'true'
observer_enabled = os.environ.get('JARVIS_OBSERVER_ENABLED', 'false').lower() == 'true'
announcements_enabled = os.environ.get('JARVIS_ANNOUNCEMENTS_ENABLED', 'false').lower() == 'true'
sentinel_enabled = os.environ.get('JARVIS_SENTINEL_ENABLED', 'true').lower() == 'true'

try:
    premium = json.loads(os.environ.get('JARVIS_PREMIUM_CONTEXTS', '[]'))
except:
    premium = []

config = {
    "api_key":          os.environ.get('JARVIS_API_KEY', ''),
    "model":            os.environ.get('JARVIS_MODEL', 'llama-3.3-70b-versatile'),
    "llm_provider":     os.environ.get('JARVIS_LLM_PROVIDER', 'groq'),
    "llm_base_url":     os.environ.get('JARVIS_LLM_BASE_URL', ''),
    "honorific":        os.environ.get('JARVIS_HONORIFIC', 'sir'),
    "directive_preset": os.environ.get('JARVIS_DIRECTIVE_PRESET', 'guardian_steward'),
    "directive":        os.environ.get('JARVIS_DIRECTIVE', ''),
    "voice_quality":    os.environ.get('JARVIS_VOICE_QUALITY', 'high'),
    "tts_provider":     os.environ.get('JARVIS_TTS_PROVIDER', 'piper_jarvis'),
    "tts_engine":       os.environ.get('JARVIS_TTS_ENGINE', 'auto'),
    "tts_premium_engine": os.environ.get('JARVIS_TTS_PREMIUM_ENGINE', ''),
    "tts_premium_contexts": premium,
    "use_home_control": use_hass_api,
    "auto_pipeline":    os.environ.get('JARVIS_AUTO_PIPELINE', 'true').lower() == 'true',
    "observer_enabled":     observer_enabled,
    "announcements_enabled": announcements_enabled,
    "sentinel_enabled":     sentinel_enabled,
    "gemini_api_key":       os.environ.get('JARVIS_GEMINI_API_KEY', ''),
    "classifier_provider":  os.environ.get('JARVIS_CLASSIFIER_PROVIDER', '') or 'groq',
    "classifier_model":     os.environ.get('JARVIS_CLASSIFIER_MODEL', '') or 'llama-3.3-70b-versatile',
    "reasoning_provider":   os.environ.get('JARVIS_REASONING_PROVIDER', '') or 'groq',
    "reasoning_model":      os.environ.get('JARVIS_REASONING_MODEL', '') or 'llama-3.3-70b-versatile',
    "review_provider":      os.environ.get('JARVIS_REVIEW_PROVIDER', '') or 'groq',
    "review_model":         os.environ.get('JARVIS_REVIEW_MODEL', '') or 'llama-3.3-70b-versatile',
    "observer_quiet_start": os.environ.get('JARVIS_OBSERVER_QUIET_START', '') or '22:00',
    "observer_quiet_end":   os.environ.get('JARVIS_OBSERVER_QUIET_END', '') or '07:00',
    "classifier_rate_limit": int(os.environ.get('JARVIS_CLASSIFIER_RATE_LIMIT', '30') or '30'),
    "cognition_enabled": (os.environ.get('JARVIS_COGNITION_ENABLED', 'true') or 'true').lower() == 'true',
    "cognition_threshold": float(os.environ.get('JARVIS_COGNITION_THRESHOLD', '0.6') or '0.6'),
}

config["_addon_config_hash"] = hashlib.sha256(
    json.dumps(config, sort_keys=True).encode()
).hexdigest()[:16]

# Merge: keep existing fields not in addon config
config_path = '/config/jarvis_config.json'
try:
    existing = json.load(open(config_path))
    for key in ("bedroom_areas", "broadcast_group", "notify_service",
                "cast_speakers", "satellite_pairings", "announcement_speakers"):
        if key in existing and key not in config:
            config[key] = existing[key]
except:
    pass

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print(f"  Config written to {config_path}")
PYEOF

# ── 4. Trigger HA restart ONLY IF NEEDED ─────────────────────────────────────
# Only restart HA if the component version changed (new install or upgrade).
# This prevents the deadly restart loop: addon starts → restarts HA → HA
# restarts addon (start on boot) → addon restarts HA → infinite loop.
bashio::log.info "[4/4] Checking if HA restart needed..."

INSTALLED_VER=$(python3 -c "import json; print(json.load(open('${COMPONENT_DST}/manifest.json'))['version'])" 2>/dev/null || echo "unknown")
PREVIOUS_VER_FILE="/config/.jarvis_last_installed_version"
PREVIOUS_VER=$(cat "${PREVIOUS_VER_FILE}" 2>/dev/null || echo "none")

if [ "${INSTALLED_VER}" = "${PREVIOUS_VER}" ]; then
    bashio::log.info "  Component v${INSTALLED_VER} already loaded — skipping HA restart."
    bashio::log.info "  (To force restart: delete /config/.jarvis_last_installed_version)"
    # Backend code is unchanged, but if ONLY the panel JS changed we still need
    # the integration to re-register the panel (new content-hash URL) so the
    # browser fetches the new dashboard. A config-entry reload does this in
    # seconds without a full HA restart and cannot cause the restart loop.
    if [ "${FRONTEND_CHANGED}" = "true" ]; then
        bashio::log.info "  Panel JS changed — reloading JARVIS integration to refresh the dashboard..."
        ENTRY_ID=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            "http://supervisor/core/api/config/config_entries" 2>/dev/null \
            | python3 -c "import sys,json
try:
    d=json.load(sys.stdin)
    print(next((e['entry_id'] for e in d if e.get('domain')=='jarvis'), ''))
except Exception:
    print('')" 2>/dev/null)
        if [ -n "${ENTRY_ID}" ]; then
            if curl -s -X POST -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
                "http://supervisor/core/api/config/config_entries/entry/${ENTRY_ID}/reload" \
                > /dev/null 2>&1; then
                bashio::log.info "  Dashboard refreshed. Hard-refresh your browser (Ctrl+Shift+R)."
            else
                bashio::log.warning "  Auto-reload failed — reload JARVIS under Settings → Devices & Services, then hard-refresh."
            fi
        else
            bashio::log.warning "  Could not locate the JARVIS entry to reload — reload it manually, then hard-refresh."
        fi
    fi
else
    bashio::log.info "  Version changed: ${PREVIOUS_VER} → ${INSTALLED_VER} — restarting HA..."
    echo "${INSTALLED_VER}" > "${PREVIOUS_VER_FILE}"

    if curl -s -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        "http://supervisor/homeassistant/restart" > /dev/null; then
        bashio::log.info "  Restart triggered. Waiting for HA to come back up..."
    else
        bashio::log.warning "  Could not trigger restart — restart HA manually."
    fi

    # Wait for HA
    HA_READY=false
    for i in $(seq 1 90); do
        if curl -s -o /dev/null -w "%{http_code}" \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            "http://supervisor/core/api/" 2>/dev/null | grep -q "200"; then
            HA_READY=true
            break
        fi
        sleep 5
    done

    if [ "${HA_READY}" = "true" ]; then
        bashio::log.info "  Home Assistant is ready."
    else
        bashio::log.warning "HA didn't respond within 7.5 minutes."
    fi
fi

# ── Run bootstrap (only after version-triggered restart) ─────────────────────
if [ "${INSTALLED_VER}" != "${PREVIOUS_VER}" ]; then
    bashio::log.info "  Running JARVIS bootstrap orchestrator..."
    JARVIS_TTS_PROVIDER="${TTS_PROVIDER}" \
    JARVIS_VOICE_QUALITY="${QUALITY}" \
    JARVIS_AUTO_PIPELINE="${AUTO_PIPELINE}" \
    python3 /bootstrap.py || bashio::log.warning "Bootstrap reported a non-fatal issue."
else
    bashio::log.info "  Skipping bootstrap (no version change)."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
bashio::log.info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bashio::log.info "  JARVIS is ready."
bashio::log.info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Keep container alive
while true; do sleep 3600; done
