# Changelog

All notable changes to JARVIS are documented here. This project uses semantic-ish
versioning (`MAJOR.MINOR.PATCH`); UI reskins and capability expansions bump MINOR,
bug fixes bump PATCH.

## [6.10.2] — Fix memory-module collision (restore semantic memory)
- **Regression fix.** The `memory/` package added in 6.9.0 shadowed the existing
  top-level `memory.py` (ChromaDB / FTS5 semantic memory). Because Python resolves
  a package before a same-named module, `from .memory import get_memory_stats`
  (and `search_memory`, `store_memory`, `get_conversation_context`) silently
  imported the package — which only exported the fault store — so those calls hit
  their `except` paths: the panel's Memory card read **Backend: unavailable /
  Stored Memories: 0** and the conversation agent lost long-term recall. The
  collision was latent until 6.10.1 (which first actually deployed the
  subpackages) unmasked it.
- **Fix:** removed the `memory/` package and relocated its rolling fault-history
  store to **`diagnostics/fault_log.py`** as `FaultLog` (it was never semantic
  memory — it's an infrastructure fault ledger, and belongs with diagnostics).
  `proactive_audio` now imports `FaultLog` from `.diagnostics`; the audit's
  "this has occurred before" recall is unchanged. The on-disk file moves from
  `/config/jarvis/semantic_memory.json` to `/config/jarvis/fault_history.json`.
  `from .memory import …` once again resolves to the real `memory.py`, restoring
  `get_memory_stats`, `search_memory`, `store_memory`, and conversation context.
  Tests relocated accordingly (117 passing).


- **Critical install fix.** `run.sh` copied only the component's top-level
  `*.py`/`*.json`/`*.yaml` (plus the `frontend/`, `translations/`, `blueprints/`
  asset dirs) and never the Python subpackages. With `audio/`, `diagnostics/`,
  `vision/`, `memory/`, `intent/`, and `automation/` absent from
  `/config/custom_components/jarvis/`, `proactive_audio.py`'s top-level
  `from .audio import ProsodyController` raised `ModuleNotFoundError` and the
  whole integration failed to set up. The installer now copies every source
  subdirectory that is a Python package (selected by `__init__.py`, so future
  subpackages are picked up automatically), clearing previously-installed
  packages first so renamed/removed modules don't linger. Asset dirs have no
  `__init__.py` and are untouched. No Python changed (suite still 117 passing);
  this is purely the install step.


- **`intent/intent_router.py` — `LocalIntentRouter`:** local, cloud-free command
  matching with ordered regex patterns (`secure the garage`, `turn off the
  lights`, pronoun forms like `turn it off` / `close it`). Pronoun context
  resolves to the active entity in the target area — a playing `media_player`
  first, then an `on` `light` — and executes locally. Pure helpers
  `match_intent()` / `is_affirmative()` are stdlib-only and unit-tested; hass-
  touching code is lazily imported so the module loads standalone.
- **Interactive feedback loop:** `jarvis.speak` gains `expect_response` and
  `confirm_intent`; an actionable announcement opens a 10-second, wake-word-free
  confirmation window (fires `jarvis_feedback_window` for the voice satellite
  layer). New **`jarvis.process_intent`** service delivers a captured phrase — an
  affirmative completes the pending action, otherwise the phrase routes as a
  fresh command. One shared router per HA instance preserves the window between
  the two calls.
- **`automation/predictor.py` — `PredictiveHabitMatrix`:** time-bucketed habit
  model over a bounded on-disk log (5000 events). `probability(key, at)` is the
  share of distinct observed days the action recurred in that time bucket;
  `due_preemptions(now, lead_minutes)` surfaces actions whose probability clears
  90% in the window 5–10 minutes ahead. Wired into the 15-minute loop to sample
  occupancy and log candidates — pre-emptive **execution is OFF by default**
  (`PREDICTOR_AUTOEXECUTE`), in keeping with JARVIS earning autonomy.
- **`jarvis.speak` `user_id`:** accepted and threaded through (logged), reserved
  for per-user biometric/profile filtering.
- **Tests:** new `test_intent_router.py` (intent matching, affirmatives, area-
  scoped pronoun resolution) and `test_predictor.py` (probability moving average,
  90% threshold, bucketing, lookahead, persistence, rolling cap, corrupt-file
  tolerance), plus prosody/triage updates — **117 passing, 1 skipped**.


- **`vision/spatial.py` — `SpatialContextEngine`:** fuses three per-area presence
  signals into an occupancy-confidence score (`sensor.{area}_frigate_person_count`
  >0 → +0.60, `binary_sensor.{area}_camera_gaze_detected` → +0.20,
  `binary_sensor.{area}_mmwave_presence` → +0.35, clamped to [0,1]). When gaze AND
  mmWave presence are both established it sets `skip_preamble`, and `jarvis.speak`
  now feeds that into prosody.
- **Prosody `skip_preamble`:** when the listener is demonstrably present and
  attending, the speech rate eases by 0.05 so the terse, preamble-free status
  reads clearly. The telemetry key `media_active` is now accepted (alongside the
  legacy `media_playing`).
- **`memory/vector_store.py` — `LocalSemanticMemory`:** a rolling on-disk JSON
  buffer (last 1000 events) under `/config/jarvis/semantic_memory.json`, with
  `commit_event(text, tags)` and `query_related_faults(keywords)`. The 15-minute
  infrastructure audit now recalls prior occurrences of a fault (matched on the
  triage finding tags), folds a short history clause into the spoken warning, and
  commits each occurrence — all file I/O off the event loop. `InfrastructureTriage`
  verdicts now carry a `tags` list for this recall.
- **Tests:** 22 new unit tests for spatial fusion, the memory store (incl. rolling
  cap, persistence, corrupt-file tolerance), and the new prosody behaviour
  (81 passing total).


- **Target resolution now goes through `audio_routing`** instead of a private
  media_player enumeration. `jarvis.speak` resolves announcement speakers with
  `audio_routing.speakers_in_area` (the same area→speaker routing, including
  device-inherited areas and listen-only-satellite exclusion, used by briefings,
  the sentinel, and doorbell announcements), falling back to the house broadcast
  set (`announcement_speakers` panel override → configured `broadcast_group` →
  all non-satellite speakers) so an announcement is never silently dropped.
- Ambient light/noise telemetry now resolves area membership via the same
  `audio_routing.entity_area` helper, and media-playing state is read from the
  resolved target speakers — one area-resolution path instead of two.
- Ducking now applies to the resolved targets (the speakers actually used),
  restored in a `finally` block as before. Behaviour-equivalent for the common
  case (speakers in the area), but now consistent with the rest of JARVIS and
  correct for Cast-group and broadcast targets.


- **New service `jarvis.speak`** (`message`, `target_area`, `critical`): a
  context-aware spoken announcement. It resolves the target area's entities
  (direct *and* device-inherited), measures ambient light, noise, and media
  activity, and shapes delivery via a new `ProsodyController` — volume, speech
  rate, and a named style (authoritative / whisper / subdued / projected /
  neutral). Active media is ducked for the announcement and restored afterward
  in a `finally` block, so a TTS error never leaves your music turned down.
- **Infrastructure audit** (`InfrastructureTriage`): every 15 minutes JARVIS
  checks root storage (warn >90%, critical >96%), RAM (>92%), and the
  connectivity of the core network switch and basement freeze sensor, then
  synthesises a single natural-language verdict and announces failures to the
  office. Confirmed-offline is critical; unreadable/unavailable is a softer
  visibility warning. A startup probe runs ~60s after load so issues surface
  without waiting a full interval.
- New package layout: `audio/prosody.py` and `diagnostics/monitor.py` (both
  stdlib-only, no Home Assistant dependency), wired in through
  `proactive_audio.py` via two hooks in `async_setup_entry`/`async_unload_entry`.
- **Tests:** 23 new unit tests pin the prosody rule matrix and triage thresholds
  (59 passing total).
- **Config:** set your TTS entity (`proactive_tts_entity` in panel runtime config,
  else `tts.piper`) and the audit's target area (`office` by default).

## [6.7.3] — Safety-loop fix + regression test harness
- **Fix (critical):** since v6.7.1 the cognitive safety tick had been throwing
  `AttributeError` every cycle. `SafetyManager.tick` and `_check_intrusion` call
  `self._residents_away()`, but that method was defined on `LockdownManager`, not
  `SafetyManager` — so freeze, intrusion, and nighttime-lockdown checks were
  silently dying inside the loop's exception handler. `_residents_away` has been
  moved to `SafetyManager` where it is used; `LockdownManager` keeps the
  `_anyone_home` predicate it actually calls. Behaviour of both is unchanged from
  the v6.7.1 intent — they are now simply on the right classes.
- **Tooling:** introduced a Home-Assistant-free **pytest harness** under `tests/`.
  A `conftest.py` installs minimal `homeassistant.*` stubs into `sys.modules`
  before collection and loads integration modules under a synthetic `jc` package;
  hand-rolled fakes (`FakeHass`, `FakeProvider`) exercise `cognitive_core` and
  `reasoning_loop` as near-pure functions. A thin `pytest-homeassistant-custom-
  component` integration layer is scaffolded (skips cleanly until that dep is
  installed). 36 tests now cover the safety predicates, freeze thresholds, and the
  reasoning resilience cascade (cloud failure → breaker open → local floor). This
  is the harness that caught the bug above.

## [6.3.2] — Startup no longer blocked
- **Fix:** the cognitive loop was created with `async_create_task`, which Home
  Assistant tracks as part of config-entry setup — so HA's bootstrap waited the
  full startup timeout on a loop that never returns, logging "Something is
  blocking Home Assistant from wrapping up the start up phase." It now runs as a
  proper **background task** (exempt from the startup wait), with a guarded
  fallback for cores predating the helper.
- The loop also yields before its first tick so startup settles before any
  state-scanning work begins.

## [6.3.1] — Two root-cause fixes
- **Cognition:** `binary_sensor.backups_stale` was escalating as a
  "safety/security trigger" because device_class `problem` was lumped with
  smoke/CO/gas. `problem` now has its own moderate tier, and system-maintenance
  entities (backup, update, snapshot, certificate, HACS, supervisor, firmware…)
  are damped to informational so housekeeping never masquerades as a security
  event. Life-safety classes remain at top salience.
- **Doorbell backlog scanner:** device discovery now mirrors Home Assistant
  core's own Nest enumeration (`async_loaded_entries → runtime_data.device_manager`),
  fetches transcoded thumbnails for clip-preview events and image media for still
  events, and rejects raw MP4 bytes that a vision model can't read. Failure
  reporting is now stage-precise.

## [6.3.0] — Local speech parity
- All local speech now flows through one voice: the Local Mind's composer.
  The learned-cache replay, the legacy templates, and the fallback path no longer
  speak in three different vintages of phrasing.
- Device-aware language (a window "is open," not "is on"; motion "has detected
  motion"), safety-class phrasing ("is detecting smoke", "has cleared"), numeric
  readings (battery "is at 18%"), and named safety alerts ("a smoke alert from
  Kitchen Smoke Detector").

## [6.2.0] — The Local Mind
- An offline reasoning brain that replaces the crude fallback when the cloud is
  unreachable. It replicates a frontier model's decision *procedure*:
  self-awareness (duplicate + flap detection), historical grounding against
  `patterns.db`, case-based memory from past cloud decisions, situational
  judgment (urgency × novelty × presence × security), and persona verbalization.
- Every decision logs its reasoning chain to the dashboard's `LOCAL` log filter.

## [6.1.0] — Loosened reins (capability expansion)
- **Automation suggestions surfaced** in the dashboard with confidence bars,
  YAML reveal, and approve/dismiss — the pattern engine's intelligence is finally
  visible. Thresholds loosened and made runtime-tunable.
- **Visitor learning** — person events feed silent vision learning (training data
  only, never spoken).
- **Rich Reasoning** — optional cloud-first judgment for medium/high events.
- **Ollama groundwork** — a Local LLM URL field flows through every provider path
  for the upcoming GPU server.

## [6.0.0] — Glassmorphism UI
- A deep visual reskin: dark-cyan glassmorphism, Space Grotesk + JetBrains Mono,
  a perspective-grid 3D house with a rotating radar sweep, glass panels, and a
  status badge wired to lockdown state. No structural changes — pure aesthetics.

## [5.9.50] — Doorbell Training UI
- A Settings panel showing the analyzed doorbell dataset, with a backlog-scan
  button and source-tagged event rows (live / event-media / backlog).

## [5.9.49] — Package & mail detection
- Porch-camera watching for packages and mail with a per-camera state machine,
  15-minute sweeps, quiet-hours gating, and an on-demand `jarvis.check_packages`
  service.

## [5.9.48] — Doorbell-only analysis + backlog training
- Camera auto-analysis narrowed to intentional doorbell presses, with a two-pass
  live-clip / recorded-event approach and a JSONL training log.

## [5.9.47] — Automatic camera event analysis
- Doorbell and (optionally) motion events are now auto-analyzed as they happen,
  not just cached.

## [5.9.46] — Appliance profile loading fix
- The appliance monitor now reads its saved profile directly from live runtime
  config instead of falling back to legacy guessing.

## [5.9.45] — Appliance row UI fix
- Restructured appliance cards so delete buttons are no longer covered.

## [5.9.44] — Per-room 3D occupancy glow
- The isometric house lights rooms by occupancy: idle wireframe, occupied cyan
  glow, dominant room pulsing.

## [5.9.43] — Iron Man HUD radial gauges
- Temperature, humidity, and lighting for the dominant room rendered as SVG donut
  gauges.

---

Earlier releases (v5.7–v5.9.42) introduced the observer pipeline, the connectivity
breaker, lockdown management, multi-frame camera vision, constrained appliance
disaggregation, quiet-hours gating, and the reasoning cache.
