# JARVIS — Settings guide & FAQ

Everything JARVIS can be configured with, what each setting does, and how to set
it up. JARVIS ships with sensible defaults — a fresh install needs only **one
LLM provider** to be useful — so treat this as a reference, not a checklist.

**Where settings live**

- **First‑time setup** and **provider API keys** are in Home Assistant's config
  flow: **Settings → Devices & Services → JARVIS → Configure** (and the initial
  "Add integration" wizard).
- **Everything else** is in the **JARVIS panel → Settings** tab (the sidebar
  "JARVIS" item). The panel writes live, without reloading the integration, so
  changes take effect immediately. Every non‑secret option in the config flow is
  also available in the panel.
- **Secrets** (provider API keys, the IMAP password) live in the config flow or
  Home Assistant's `secrets.yaml` — never in the panel — so they're never sent to
  the browser.

> A setting you change in the panel is stored under `/config/jarvis/` and
> survives restarts. Nothing in here sends your data anywhere except the LLM
> provider(s) **you** configure.

---

## 1. First‑time setup (the quickest path)

1. **Add a provider.** In the setup wizard pick a provider and enter its key:
   - **Groq**, **OpenAI**, **Anthropic**, **Gemini** → paste the API key.
   - **Custom (OpenAI‑compatible)** → enter the base URL (e.g. your own gateway).
   - **Ollama (local)** → enter the base URL, or leave blank for the default
     local endpoint (`http://localhost:11434`). Pull a model first
     (`ollama pull llama3.2`).
   You can add more providers later and mix them per role.
2. **Pick the Main Agent provider and conversation model.** The model list is
   fetched live from the provider you chose.
3. **Choose how JARVIS addresses you** (the *honorific*, e.g. "sir" — leave blank
   for none).

That's it — JARVIS is now your conversation agent and voice‑pipeline brain. Open
the **JARVIS panel** to tune the rest.

---

## 2. FAQ — "How do I set up…?"

### …notifications to my phone?
Set **`notify_service`** to your Home Assistant companion‑app service, e.g.
`notify.mobile_app_yourphone` (**Settings → Routing**, or the panel). JARVIS uses
it when you're away or asleep, and for the `send_notification` tool.

### …spoken announcements on my speakers?
Set **`broadcast_group`** to a media_player group (typically your Chromecast/Nest
home group) — used for critical and high‑urgency announcements. For a curated
list of specific speakers use **`announcement_speakers`** (panel → Home/▶ list).
To mute speakers and send proactive announcements to notifications only, turn on
**`announce_notify_only`**.

### …JARVIS speaking a particular language?
JARVIS follows your Home Assistant language automatically. To force a specific
one, set **`output_language`** to an ISO code (e.g. `de`) — or `auto` to follow
HA. The panel UI language is **`ui_language`**. Spoken voice requests are always
answered in the language you spoke.

### …camera / vision checks?
Cameras are read on demand ("look at the garage camera") and for auto event
reviews. Key settings (panel → **AI Models → Vision**, and **Cameras**):
- **`vision_provider` / `vision_model`** — which model sees images.
- **`vision_min_interval`** — minimum seconds between *automatic* analyses, to
  protect your provider's daily token budget (`0` = off). Explicit "look at the
  camera" requests and doorbell presses are never throttled.
- **`camera_alert_level`** — `off | urgent | important | all`: how notable an
  auto‑review must be before it's announced.
- **`camera_property_scope_only`** — auto reviews consider only on‑property
  activity (draw the property line in the floor‑plan editor first).
- **`disabled_cameras`** — cameras JARVIS must never use.
- **`package_detection`** — watch porch cameras for packages and mail.

### …who's‑speaking / face recognition?
- **`identity_enabled`** — recognise who's home + recent camera face recognition.
  No special hardware.
- **`recognition_source`** — `frigate | doubletake | both` for face recognition.
- **`llm_face_recognition`** — best‑effort resident recognition via the local
  vision model (opt‑in); **`llm_face_autotrain`** auto‑enrols reference photos
  from confident recognitions.
- **`identity_voice_fingerprint`** — voice‑ID tier; needs a **local GPU** running
  a speaker‑ID model, so it's off by default.
- Local in‑process speaker ID (**Voices** tab, "Identify speakers in‑process",
  key `voice_recognition_local`) needs a backend + ONNX model installed; leave it
  off if you haven't set that up. See [voice‑recognition.md](voice-recognition.md).

### …proactive awareness (Observer)?
Turn on **`observer_enabled`** and set the three model tiers (**Classifier**,
**Reasoning**, optional **Review**) under **Settings → Observer**. Set **quiet
hours** (`observer_quiet_start` / `observer_quiet_end`) so JARVIS backs off
overnight. See `rich_reasoning` for cloud‑first judgment on medium+ events.

### …a morning / evening briefing?
Enable **`briefing_morning_enabled`** / **`briefing_evening_enabled`** and set the
times (`briefing_morning_time` / `briefing_evening_time`). Toggle the sections
with the `briefing_include_*` flags; **`briefing_require_home`** skips it when
nobody's home.

### …the floor plan (rooms, cameras, property line)?
Open **Residence → floor‑plan editor**. Place rooms, pin entities, place cameras
(position/angle/field‑of‑view), and draw the **property boundary** polygon. You
can **Import Map** from a robot‑vacuum/dashboard image and **Detect Rooms** to
auto‑segment it. This powers room‑aware announcements, camera coverage, and the
property‑line camera scope.

### …departure / "leave now" alerts?
Enable **`departure_alerts_enabled`**, set **`departure_origin_entity`** (home
location) and a travel‑time source (**`departure_travel_sensor`** or an OSRM URL
in **`departure_osrm_url`**), and a lead time (**`departure_lead_minutes`**).
**`departure_require_location`** skips online‑only meetings.

### …alerts to my car screen (Android Auto)?
Turn on **`driving_mode_enabled`** and pick the head‑unit sensor
(**`driving_car_sensor`**, blank = auto‑detect). Choose what routes to the car
(`driving_notify_briefings/travel/security`) and whether to mute the home audio
for routed alerts (`driving_suppress_home_audio`).

### …read‑only email?
**Settings → Email**: enable **`imap_enabled`**, set host/port/user/folder/SSL.
The password is **not** stored in the panel — put it in `secrets.yaml` under the
key named by **`imap_secret_key`** (default `jarvis_imap_password`).

### …let JARVIS act on its own (FRIDAY / autonomy)?
This is deliberately gated:
- **`friday_automator`** enables the FRIDAY sub‑agent, which **can control
  devices, scenes and scripts on its own** — every other sub‑agent is read‑only.
  You must also tick the acknowledgement (**`friday_confirm`**).
- **`autonomy_enforce`** turns on kernel‑governed graduated autonomy (security
  never auto‑acts); it's opt‑in and confirmed in‑panel.
- Fine‑grained kernel **enforce kill‑switches** live under **Settings →
  Governance** — each can be turned off if you want a capability to observe‑only.

### …home control (lights, locks, climate)?
**`use_hass_api`** lets JARVIS use Home Assistant's own LLM tools to control
devices. For sensitive actions, **`voice_confirm_enabled`** asks for spoken
confirmation (`voice_confirm_mode`: native | gated | auto).

---

## 3. Settings reference

Grouped by area. Unless noted, set these in the **JARVIS panel → Settings**.
Booleans are on/off toggles; defaults are JARVIS's out‑of‑the‑box behaviour.

### Providers & models
| Setting | What it does |
| --- | --- |
| `llm_provider`, `model` | Main Agent provider + conversation model. |
| `llm_base_url`, `custom_base_url`, `ollama_base_url` | Endpoint for Ollama / custom (OpenAI‑compatible) providers — point at your local GPU server. |
| `classifier_provider` / `classifier_model` | Observer Tier 1 (cheap triage). |
| `reasoning_provider` / `reasoning_model` | Observer Tier 2 (main judgment). |
| `review_enabled`, `review_provider` / `review_model` | Optional Tier 3 second‑opinion pass. |
| `vision_provider` / `vision_model` | Model that describes camera images. |
| `camera_reasoning_provider` / `camera_reasoning_model` | Model that interprets the described scene. |
| `vision_thinking_enabled`, `vision_thinking_max_tokens` | Let the vision model "think" (off by default) + its token budget. |
| `fast_chat_enabled`, `fast_provider` / `fast_model`, `fast_chat_max_complexity` | Route simple chat turns to a small/fast model for latency. |
| `ollama_num_ctx` | Ollama context‑window size for local models. |
| `classifier_rate_limit` | Cap on classifier calls. |

### Persona & language
| Setting | What it does |
| --- | --- |
| `honorific` | How JARVIS addresses you (blank = none). |
| `directive_preset` | Persona directive preset. |
| `directive` | Custom standing directive; overrides the preset when set. |
| `banter_level` | 0 plain · 1 dry (default) · 2 full MCU wit. |
| `output_language` | ISO code (e.g. `de`) or `auto` — steers spoken/written output. |
| `ui_language` | Panel UI language. |
| `use_hass_api` | Allow home control via Home Assistant's LLM tools. |

### Audio, announcements & satellites
| Setting | What it does |
| --- | --- |
| `announcements_enabled` | Master on/off for spoken announcements. |
| `announce_notify_only` | Mute speakers; proactive announcements go to notifications only. |
| `broadcast_group` | Media_player group for critical/high‑urgency announcements. |
| `announcement_speakers` | Curated list of cast speakers for announcements. |
| `satellite_pairings` | Map `{satellite → cast speaker}` for reply routing. |
| `satellite_audio_out`, `satellite_start_action` | Satellites whose audio routes out; per‑satellite ESPHome start action. |
| `proximity_volume` | Dampen TTS volume when you're close to the speaker (needs mmWave distance sensors). |
| `continued_conversation_enabled` | Keep the mic open for a natural follow‑up. |
| `continued_conversation_speaker_reopen` | Time the follow‑up reopen to the reply finishing. |
| `continued_conversation_multi_satellite` | Follow‑up follows you to another room's satellite. |
| `proactive_questions_interactive` | Open the mic for proactive questions. |

### Notifications, away & driving
| Setting | What it does |
| --- | --- |
| `notify_service` | Phone notify service (e.g. `notify.mobile_app_*`). |
| `quiet_hours_push_critical_only` | During quiet hours, push only critical items. |
| `departure_alerts_enabled` | "Leave now" / travel‑time heads‑ups for calendar events. |
| `departure_require_location` | Only alert for events with a physical location. |
| `departure_lead_minutes` | How far ahead to warn. |
| `departure_origin_entity`, `departure_travel_sensor`, `departure_osrm_url` | Home location + travel‑time source. |
| `arrival_greeting_enabled`, `arrival_min_away_minutes`, `arrival_greeting_start/end` | Welcome‑home greeting on arrival + its window. |
| `arrival_push_enabled` | Also push the "heading home" anticipation alert to phones. |
| `driving_mode_enabled`, `driving_car_sensor` | Route proactive alerts to the car (Android Auto) screen. |
| `driving_notify_briefings/travel/security`, `driving_suppress_home_audio` | What routes to the car, and whether to mute home audio for it. |

### Observer, proactive & briefings
| Setting | What it does |
| --- | --- |
| `observer_enabled` | Watch the event stream and surface what matters. |
| `observer_quiet_start` / `observer_quiet_end` | Quiet hours (HH:MM). |
| `rich_reasoning` | Cloud‑first judgment for medium/high‑urgency events. |
| `cognition_enabled` | The cognition/salience loop. |
| `briefing_morning_enabled` / `briefing_evening_enabled` | Scheduled briefings. |
| `briefing_morning_time` / `briefing_evening_time` | Their times. |
| `briefing_require_home` | Skip when nobody's home. |
| `briefing_include_weather/calendar/presence/events/energy/hazards` | Which sections to include. |

### Identity, faces & voices
| Setting | What it does |
| --- | --- |
| `identity_enabled` | Recognise who's home + recent camera faces (no GPU). |
| `identity_min_confidence` | How sure before committing to a person. |
| `recognition_source` | Face source: `frigate | doubletake | both`. |
| `llm_face_recognition`, `llm_face_autotrain` | Local vision‑model recognition (opt‑in) + auto‑enrol reference photos. |
| `identity_voice_fingerprint` | Voice‑ID tier (needs a local GPU). |
| `voice_recognition_source` | Speaker‑recognition entity (e.g. `binary_sensor.*_voice`). |
| `voice_recognition_auto_enroll` | Auto‑flag voices to enrol as people speak. |
| `voice_recognition_local` + `voice_recognition_delegate_stt`, `voice_embedding_model_path`, `voice_local_*` | In‑process speaker ID (Path B) — needs a backend + ONNX model. |

### Cameras & vision
| Setting | What it does |
| --- | --- |
| `disabled_cameras` | Cameras JARVIS must never use. |
| `camera_alert_level` | `off | urgent | important | all` — auto‑review announce threshold. |
| `camera_auto_analyze_motion`, `camera_motion_vision` | Auto‑inspect motion events / generic motion → silent vision. |
| `camera_property_scope_only` | Auto reviews consider only on‑property activity. |
| `vision_min_interval` | Min seconds between automatic analyses (token‑budget guard; `0` = off). |
| `package_detection` | Watch porch cameras for packages/mail. |
| `visitor_learning` | Silent vision learning from person events. |
| `opening_watch_enabled`, `opening_watch_interval_min` | Vision‑check sensor‑less doors/windows in camera sightlines (owner‑gated, off by default). |

### Floor plan & residence
| Setting | What it does |
| --- | --- |
| `floor_plan_rooms`, `floor_plan_entities`, `floor_plan_cameras`, `floor_plan_property` | Room polygons, pinned entities, placed cameras, property boundary (set via the editor). |
| `floor_plan_bg`, `floor_plan_bg_opacity` | Imported background image(s) + opacity. |
| `floor_plan_units`, `floor_plan_sqft`, `floor_plan_elements` | Units, square footage, placed windows/doors (+ sensor map). |
| `residence_style`, `home_stories`, `has_basement`, `home_roof`, `garage_bays`, `home_bedrooms`, `home_bathrooms`, `chimney_*`, `dormers_*`, `home_attached`, `basement_entrance`, `door_mapping` | The 3D Residence model's shape and stats. |

### Security, intrusion & hazards
| Setting | What it does |
| --- | --- |
| `sentinel_enabled` | The security watch (door/window/lock sentinel). |
| `door_window_alerts_enabled` | Announce open‑at‑unusual‑time (awareness kept either way). |
| `nighttime_lockdown_covers` | Bound autonomous nighttime cover‑closing: `secure` (default) / `all` / `off`. |
| `intrusion_breach_only` | Interior motion never alarms; only a breach or camera‑confirmed person (default on). |
| `intrusion_vision_confirm`, `intrusion_vision_veto` | Verify person detection with vision before escalating; a clear "no person" vetoes low‑confidence alerts. |
| `intrusion_inward_depth`, `intrusion_requires_confinement`, `intrusion_response_timeout` | Intrusion‑confirmation tuning + escalation timeout. |
| `outdoor_areas`, `outdoor_entities`, `indoor_entities` | Indoor/outdoor classification for the false‑alarm guards. |
| `hazard_monitor_enabled` + `hazard_quakes_on` / `hazard_weather_on` / `hazard_disasters_on` | Earthquake / severe‑weather / disaster feeds + radii/thresholds (`hazard_*`). |

### Patterns, memory & storage
| Setting | What it does |
| --- | --- |
| `pattern_learn_doors/presence/buttons` | Which activity to learn for routines. |
| `pattern_include_entities`, `pattern_min_occurrences`, `pattern_confidence` | Always‑learn list + thresholds. |
| `excluded_entities`, `excluded_domains`, `excluded_labels` | Remove entities/domains/labels from JARVIS's awareness. |
| `memory_threading_enabled`, `memory_threading_hours`, `memory_threading_max` | Thread related memories together. |
| `db_retention_days`, `db_source_retention_days`, `db_storage_path` | Cap DB growth, flush raw learning sooner, host the DB on another volume. |
| `home_context_max_entities` | Entity names per domain in the system prompt (`0` = counts only). |

### Energy, documents, web & wellbeing
| Setting | What it does |
| --- | --- |
| `energy_agency` | `advisory | opt_in | autonomous` energy behaviour. |
| `energy_peak_watts`, `energy_power_sensor`, `energy_mode_bump` | Peak threshold, explicit whole‑home power sensor, modes that raise agency. |
| `document_watch_folders`, `semantic_search`, `embed_model`, `embed_base_url` | Auto‑ingest doc folders + optional Ollama‑embedding semantic search. |
| `search_backend`, `searxng_url`, `web_research_llm_fallback` | Web‑research backend (`duckduckgo`/`searxng`) + LLM web‑search fallback. |
| `biometrics_enabled`, `biometric_entities` | Read non‑medical wearable context (opt‑in). |
| `host_telemetry` | Monitor host CPU temp / memory / NVMe I/O. |

### Modes & agents
| Setting | What it does |
| --- | --- |
| `operational_mode_auto`, `custom_modes` | Auto away/normal by occupancy; user‑defined modes. |
| `lab_areas`, `movie_area`, `movie_media_player`, `movie_dim_pct` | Lab/Movie mode bindings. |
| `friday_automator` + `friday_confirm` | Enable the FRIDAY actuator sub‑agent (controls devices) — requires the acknowledgement. |
| `autonomy_enforce` | Kernel‑governed graduated autonomy (opt‑in; security never auto‑acts). |
| `voice_confirm_enabled`, `voice_confirm_mode`, `voice_confirm_entities` | Spoken confirmation for sensitive actions. |
| `goals_kernel_authoritative` | Kernel journal is authoritative for goal step state (default on). |

### Email (read‑only)
| Setting | What it does |
| --- | --- |
| `imap_enabled`, `imap_host`, `imap_port`, `imap_user`, `imap_folder`, `imap_ssl` | Read‑only inbox access. |
| `imap_secret_key` | `secrets.yaml` key holding the password (never stored in the panel). |

---

## 4. Governance & kill‑switches

JARVIS's kernel runs a set of capabilities (world model, authority, agency,
anticipation, …) that can each be in *observe‑only* or *enforce*. The
**Settings → Governance** panel lists every enforce switch with a plain‑language
explanation, so you can turn any one off if you'd rather it only observe.
Safety‑critical paths never auto‑act regardless.

## 5. Secrets & privacy

- Provider **API keys** are entered in the config flow and stored by Home
  Assistant, never shown in the panel.
- The **IMAP password** is read from `secrets.yaml` (`imap_secret_key`).
- Everything JARVIS learns persists locally under `/config/jarvis/`. Nothing
  leaves your home except requests to the LLM provider(s) you configure.

---

*See also: [voice-recognition.md](voice-recognition.md),
[floor-plan-import.md](floor-plan-import.md), and the README's
"Configuration highlights" and "Languages" sections.*
