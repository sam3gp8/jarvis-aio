# JARVIS AI Assistant

This add-on installs and runs JARVIS — an autonomous AI butler for Home Assistant
with voice, vision, and a learning reasoning core. Full project documentation lives
in the [repository README](https://github.com/sam3gp8/jarvis-aio).

## Quick start

1. Set your `groq_api_key` and `honorific` in the **Configuration** tab. (Groq has
   a free tier and is the recommended starting provider.)
2. Press **Start**. The add-on installs the integration, registers the conversation
   agent, and sets up the voice pipeline automatically.
3. JARVIS appears in the sidebar. Hard-refresh (`Ctrl+Shift+R`) after updates so the
   dashboard reloads.

## Configuration

### Core
- `groq_api_key` — your Groq API key (or use another provider below).
- `llm_provider` — `groq`, `openai`, `gemini`, `anthropic`, `ollama`, or `custom`.
- `llm_base_url` — endpoint for `ollama`/`custom` (e.g. `http://gpu-server:11434/v1`).
- `honorific` — what JARVIS calls you (e.g. "sir").
- `model` — the main-agent model.

### Voice
- `tts_provider`, `tts_engine`, `voice_quality` — text-to-speech setup.
- `auto_pipeline` — auto-create the assist pipeline.

### Observer & cognition
- `observer_enabled` — watch the event stream and decide what's worth surfacing.
- `announcements_enabled` — allow spoken announcements.
- `sentinel_enabled` — safety monitoring (pipes, smoke/CO/water, entry).
- `cognition_enabled` / `cognition_threshold` — anticipation and escalation sensitivity.
- `observer_quiet_start` / `observer_quiet_end` — quiet hours.

### Per-role AI models
Independently choose the provider and model for the classifier, reasoning, review,
vision, and camera-reasoning roles. Leave blank to inherit the main provider.

### Vision
- `gemini_api_key` — recommended for camera/doorbell reasoning.

## Persistent data

JARVIS stores learned behavior and state under `/config/jarvis/`:
`patterns.db`, the reasoning cache, the doorbell-training dataset, and lockdown
state. This survives restarts and upgrades.

## Support

[Buy Me a Coffee](https://www.buymeacoffee.com/sam3gp8) ·
[Issues](https://github.com/sam3gp8/jarvis-aio/issues)
