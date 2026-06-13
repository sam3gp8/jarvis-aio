<div align="center">

# JARVIS AI Assistant

### *Just A Rather Very Intelligent System*

An autonomous AI butler for Home Assistant — voice, vision, and a reasoning core that learns your home and watches over it.

[![Add-on Repository](https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5?logo=home-assistant&logoColor=white)](https://github.com/sam3gp8/jarvis-aio)
[![Version](https://img.shields.io/badge/version-6.3.2-00d9ff)](https://github.com/sam3gp8/jarvis-aio/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/sam3gp8)

</div>

---

JARVIS turns Home Assistant into a proactive household intelligence. It speaks in a custom voice, sees through your cameras, reasons about what's worth telling you, and quietly learns the rhythms of your home over weeks and months. It runs as a single Home Assistant **add-on** that installs and configures everything for you — no terminal, no manual integration setup.

The guiding principle is **suggest, don't act** until you grant otherwise: JARVIS starts conservative, surfaces what it notices, and expands its autonomy only as you allow.

## What it does

**Voice & conversation.** A pluggable LLM brain (Groq, Gemini, OpenAI, Anthropic, or a local Ollama server) drives natural conversation through the Home Assistant voice pipeline, answered in a custom Piper TTS voice. Works with ESP32-S3 satellites, Wyoming, and Google speakers.

**Vision & cameras.** Automatic doorbell-press analysis with a two-pass live-clip / recorded-event approach, package and mail detection on porch cameras, and silent visitor learning that quietly builds a picture of who comes and goes — all powered by vision models reasoning over Nest and Frigate feeds.

**The Cognitive Core.** A reasoning loop that classifies every household event by urgency and decides whether it's worth your attention. It grounds decisions in your home's actual history ("the kitchen light at 7am is routine; the basement window has never opened before"), escalates security-relevant events when you're away, and proposes automations from patterns it observes.

**The Local Mind.** When the cloud is unreachable, JARVIS doesn't go dumb — an offline reasoning brain replicates the full decision procedure (self-awareness, historical grounding, case-based memory, situational judgment, persona phrasing) so it keeps making sound, well-spoken calls with no internet at all.

**Safety & security.** Proactive monitoring for freezing pipes, smoke/CO/water, unauthorized entry, and nighttime lockdown — occupancy-gated so enforcement only happens when it should.

**An Iron Man HUD dashboard.** A dark-cyan glassmorphism control panel with a live isometric 3D house, per-room occupancy glow, radial telemetry gauges, an event feed, a doorbell-training view, and surfaced automation suggestions.

## Requirements

- **Home Assistant OS** (Supervisor required — this is an add-on).
- At least one **LLM API key** (Groq has a generous free tier and is the recommended starting point).
- *Optional but recommended:* a Gemini API key for camera/vision reasoning, Nest cameras + doorbell, Frigate NVR, ESP32-S3 voice satellites, and a Piper TTS voice.
- *On the horizon:* a local GPU server running Ollama, for fully local inference — JARVIS is already wired for it.

## Installation

**1. Add this repository to Home Assistant.**

Go to **Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories**, and add:

```
https://github.com/sam3gp8/jarvis-aio
```

Or click:

[![Open your Home Assistant instance and show the add add-on repository dialog.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsam3gp8%2Fjarvis-aio)

**2. Install the JARVIS AI Assistant add-on** from the store list that appears.

**3. Configure it.** Open the add-on's **Configuration** tab and at minimum set your `groq_api_key` and an `honorific` (what JARVIS calls you). Everything else has sensible defaults.

**4. Press Start.** The add-on installs the integration into Home Assistant, registers the conversation agent, and sets up the voice pipeline automatically. When it finishes, JARVIS appears in the sidebar.

**5. Fine-tune (optional).** Advanced routing, observer mode, camera watching, and the AI-model-per-role assignments are configured from the JARVIS panel and from **Settings → Devices & Services → JARVIS → Configure**, where you get proper area and entity pickers.

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

JARVIS is an **all-in-one add-on**: the add-on container bootstraps a bundled Home Assistant custom integration (domain `jarvis`, 43 Python modules) into `/config/custom_components/jarvis/`, wires up the conversation agent and voice pipeline, and serves the custom dashboard panel. State and learned behavior persist under `/config/jarvis/` (a SQLite `patterns.db`, the reasoning cache, the doorbell-training dataset, and lockdown state) so JARVIS keeps getting smarter across restarts.

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
