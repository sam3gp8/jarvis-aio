<div align="center">

# JARVIS AI Assistant

### *Just A Rather Very Intelligent System*

An autonomous AI butler for Home Assistant — voice, vision, and a reasoning core that learns your home and watches over it.

[![HACS Integration](https://img.shields.io/badge/HACS-Integration-41BDF5?logo=home-assistant&logoColor=white)](https://github.com/sam3gp8/jarvis-aio)
[![Release](https://img.shields.io/github/v/release/sam3gp8/jarvis-aio?color=00d9ff)](https://github.com/sam3gp8/jarvis-aio/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/sam3gp8)

</div>

---

JARVIS turns Home Assistant into a proactive household intelligence. It speaks in a custom voice, sees through your cameras, reasons about what's worth telling you, and quietly learns the rhythms of your home over weeks and months. It installs as a Home Assistant **custom integration** via HACS and runs entirely inside Home Assistant — no separate container.

The guiding principle is **suggest, don't act** until you grant otherwise: JARVIS starts conservative, surfaces what it notices, and expands its autonomy only as you allow.

## What it does

**Voice & conversation.** A pluggable LLM brain (Groq, Gemini, OpenAI, Anthropic, or a local Ollama server) drives natural conversation through the Home Assistant voice pipeline, answered in a custom Piper TTS voice. Works with ESP32-S3 satellites, Wyoming, and Google speakers.

**Web research & schedule awareness.** Ask JARVIS about the outside world — current events, facts, "what's the latest on…" — and it looks it up (DuckDuckGo Instant Answer out of the box, no API key; point it at a self-hosted SearXNG for richer results). It also reads your household `calendar.*` entities to surface upcoming events and flag scheduling conflicts — overlaps, and back-to-back commitments with too little gap between them.

**The JARVIS voice.** Modelled on Stark's JARVIS: dry, precise, unflappable, quietly witty — and strictly situational about it. The wit is a scalpel, not a hammer, and it goes silent the instant something is wrong. JARVIS does not quip during a smoke alarm. A **banter level** setting (plain / dry / full) tunes how much character surfaces, and urgent and grave events always speak plainly regardless.

**Vision & cameras.** Automatic doorbell-press analysis with a two-pass live-clip / recorded-event approach, package and mail detection on porch cameras, and silent visitor learning that quietly builds a picture of who comes and goes — all powered by vision models reasoning over Nest and Frigate feeds.

**The Cognitive Core.** A reasoning loop that classifies every household event by urgency and decides whether it's worth your attention. It grounds decisions in your home's actual history ("the kitchen light at 7am is routine; the basement window has never opened before"), escalates security-relevant events when you're away, and proposes automations from patterns it observes.

**The Local Mind.** When the cloud is unreachable, JARVIS doesn't go dumb — an offline reasoning brain replicates the full decision procedure (self-awareness, historical grounding, case-based memory, situational judgment, persona phrasing) so it keeps making sound, well-spoken calls with no internet at all.

**Safety & security.** Proactive monitoring for freezing pipes, smoke/CO/water, unauthorized entry, and nighttime lockdown — occupancy-gated so enforcement only happens when it should.

**An Iron Man HUD dashboard.** A dark-cyan glassmorphism control panel with a live isometric 3D house, per-room occupancy glow, radial telemetry gauges, an event feed, a doorbell-training view, and surfaced automation suggestions.

## Requirements

- **Home Assistant** with [HACS](https://hacs.xyz) installed. HA OS / Supervised is recommended — the optional voice-stack auto-setup (Piper / Whisper / openWakeWord) uses the Supervisor; on HA Container/Core you'd add those yourself.
- At least one **LLM API key** (Groq has a generous free tier and is the recommended starting point).
- *Optional but recommended:* a Gemini API key for camera/vision reasoning, Nest cameras + doorbell, Frigate NVR, ESP32-S3 voice satellites, and a Piper TTS voice.
- *On the horizon:* a local GPU server running Ollama, for fully local inference — JARVIS is already wired for it.

### Nest cameras (prerequisite for camera intelligence)

JARVIS consumes Nest cameras and doorbells **through the official [Google Nest integration](https://www.home-assistant.io/integrations/nest/)** — it does not (and legally cannot) talk to Google's Smart Device Management API with its own credentials, because Google binds SDM access to *your* Google account and Device Access project. One-time setup:

1. **Google SDM API** — create a project in the [Device Access Console](https://console.nest.google.com/device-access) (US $5 one-time fee) and a Google Cloud project with the SDM API enabled and OAuth credentials.
2. **Credentials in HA** — add your OAuth Client ID + Secret under *Settings → Devices & Services → Application Credentials*, then add the **Google Nest** integration and authorize it. Your cameras and doorbell appear as `camera.*` entities.
3. **That's it for JARVIS** — it auto-detects Nest-platform cameras and uses the right frame source for each event (event media, stream-wake, or its own snapshot path). Battery/WebRTC-only Nest cameras can't produce ordinary still images while idle; the JARVIS panel handles this automatically by escalating to its own snapshot tier, so the tile shows frames instead of going blank.

## Installation

**1. Add this repository to HACS.**

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sam3gp8&repository=jarvis-aio&category=Integration)

Click the badge above, or do it manually — in **HACS → ⋮ (top right) → Custom repositories**, add the URL below with category **Integration**:

```
https://github.com/sam3gp8/jarvis-aio
```

**2. Install "JARVIS AI Assistant"** from HACS, then restart Home Assistant.

**3. Add the integration.** Go to **Settings → Devices & Services → Add Integration → JARVIS**. Enter a cloud API key (e.g. Groq), *or* leave it blank and enter a local LLM URL (e.g. `http://homeassistant.local:11434/v1`) to run Ollama with no cloud account. JARVIS registers its conversation agent and appears in the sidebar.

**4. Set up voice (optional).** On Home Assistant OS / Supervised, JARVIS bootstraps the voice stack itself on first run — it installs and starts the **Piper**, **Whisper**, and **openWakeWord** add-ons, downloads the JARVIS voice, and creates an Assist pipeline with JARVIS as the conversation agent. On Container/Core installs (no Supervisor), install those pieces yourself and create the pipeline via Settings → Voice Assistants.

**5. Fine-tune (optional).** Advanced routing, observer mode, camera watching, and the AI-model-per-role assignments are all configured from the JARVIS panel → **Settings**.

> **Hard-refresh after updates.** The dashboard JavaScript is cached aggressively — after upgrading, refresh with `Ctrl+Shift+R` so the new panel loads.

## Configuration highlights

| Setting | What it does |
| --- | --- |
| `llm_provider` / per-role models | Choose Groq, Gemini, OpenAI, Anthropic, Ollama, or custom — independently for the main agent, classifier, reasoning, review, vision, and camera-reasoning roles. |
| `llm_base_url` | Point the Ollama/custom providers at your local GPU server (e.g. `http://gpu-server:11434/v1`). |
| `observer_enabled` | Let JARVIS watch the event stream and decide what's worth surfacing. |
| `rich_reasoning` | Cloud-first judgment for medium/high-urgency events (cheap, sharper). |
| `visitor_learning` | Silently learn from person events at the door — never spoken. |
| `package_detection` | Watch porch cameras for packages and mail. |
| `cognition_threshold` | How salient an event must be before JARVIS escalates it. |

## Architecture

JARVIS is a **Home Assistant custom integration** (domain `jarvis`, ~47 Python modules) installed via HACS into `custom_components/jarvis/`. It runs in-process: it registers the conversation agent and voice pipeline and serves the custom dashboard panel directly. State and learned behavior persist under `/config/jarvis/` (a SQLite `patterns.db`, the curated `knowledge.db`, the reasoning cache, the doorbell-training dataset, and lockdown state) so JARVIS keeps getting smarter across restarts.

The reasoning pipeline is layered for resilience and cost: local templates → learned cache → (cloud, or soon a local model) → the **Local Mind** offline brain as the floor beneath everything. A connectivity breaker guards cloud calls, and every local decision logs its reasoning chain to the dashboard's log view.

## Roadmap

- **Local GPU inference** — drop-in Ollama support is wired; the chain becomes templates → cache → local model → cloud once the hardware lands.
- **Pattern-driven automations** — the engine that proposes automations from observed behavior continues to mature.
- **Per-person routine inference** — learning each household member's patterns over a 1–2 year horizon.
- **UI Phase 2/3** — SVG floor plan with mmWave presence, sparklines, real-time WebSocket entity subscriptions.

See [CHANGELOG.md](CHANGELOG.md) for the full release history.

## Support

If JARVIS makes your home a little smarter, you can support continued development:

<a href="https://www.buymeacoffee.com/sam3gp8"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" height="48" alt="Buy Me A Coffee"></a>

Bugs and feature requests go to [GitHub Issues](https://github.com/sam3gp8/jarvis-aio/issues). Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © sam3gp8

<sub>Inspired by the JARVIS of the Marvel Cinematic Universe. This is an independent project, not affiliated with or endorsed by Marvel or Disney.</sub>
