# Changelog

All notable changes to JARVIS are documented here. This project uses semantic-ish
versioning (`MAJOR.MINOR.PATCH`); UI reskins and capability expansions bump MINOR,
bug fixes bump PATCH.

## [6.15.0] — One panel: Command Center folded into JARVIS (camera-forward)
The separate Command Center panel is retired; its capability now lives in the main
JARVIS panel, which keeps its 3D isometric floor plan (the 2D top-down experiment is
dropped).
- **Camera Watch in the dashboard.** The live/selectable camera feed with event
  auto-focus — ported from the standalone panel into `jarvis-panel.js` — now sits in
  the Command Center tab. The dashboard centre is a 2-up: **Camera Watch (primary,
  wider) beside the 3D Residence Overview**. Cameras read from `config.cameras`, stream
  via HA's MJPEG proxy with the entity's access token, switch on chip click, and
  auto-focus the relevant feed on a `jarvis_camera_event` / `jarvis_face_recognized`
  (banner + 25s revert), with a still-image fallback for Nest/WebRTC. Subscriptions and
  timers are torn down in `_stopIntervals`.
- **Single panel.** `panel_register.py` registers only the JARVIS panel now and removes
  the stale `/jarvis-command` sidebar entry on upgrade. `jarvis-command.js` deleted.
- **JS behavioural test.** `scripts/smoke_panel.js` retargeted to the combined panel:
  renders it under jsdom with a realistic payload and asserts the dashboard draws —
  styles, the 3D scene, and the folded-in Camera Watch (chips from `config.cameras`,
  auto-selected stream wired with the token). 9/9 pass. Python audit clean, 170 tests
  passing.


First real-deployment look at the new panel surfaced two frontend bugs (data was
flowing — areas, presence, live log all correct — but the panel was broken):
- **Orphaned stylesheet.** The component's CSS (`JC_STYLES`) was defined but never
  injected into the shadow DOM — the `innerHTML` started at `<div class="app">` with
  no `<style>`. Result: a plain unstyled text stack, no grid/borders/colours. Now
  injected as `<style>${JC_STYLES}</style>…`.
- **Data-contract mismatches.** The panel read `d.cameras`, `d.presence`, and
  `d.lockdown`, but `get_panel_data` returns presence under `dominant` and nests
  `cameras` + `lockdown` inside `config`. So the camera picker was always empty
  ("NO CAMERA SELECTED") and the dominant-area temp showed "—". Now reads
  `d.config.cameras`, `d.dominant`, and `d.config.lockdown` (with fallbacks).
- **Why the audit missed it + the fix.** The release gates were Python-only
  (`scripts/audit.py`, pytest) plus `node --check`, which validates JS *syntax* but
  not behaviour — an orphaned const and a wrong object path are both valid syntax.
  Added `scripts/smoke_panel.js`: renders the component under jsdom with a realistic
  `get_panel_data` payload and asserts it actually draws (styles injected, grid +
  modules present, camera chips populated from `config.cameras`, dominant area/temp
  shown, live MJPEG `src` wired with the access token). 10/10 pass. Python audit
  clean, 170 tests passing.


Proactive audit (not wait-and-see) of the code paths that only began loading after
6.14.1 surfaced two real bugs in `intent/intent_router.py`:
- **`from . import audio_routing` → `from ..`.** `intent_router` lives in the
  `intent/` subpackage, so the single-dot form resolved to the non-existent
  `intent.audio_routing` instead of the top-level `audio_routing`. It's a lazy
  import inside `_area_of`, and `_call_domain_in_area` calls `_area_of` inside a
  `try/except` that swallows the `ImportError` — so `secure_area`/`lights_off`
  would have silently matched zero entities and done nothing. Now `..audio_routing`.
- **`from .automation.mutex import Priority` → `from ..automation.mutex`.** Same
  class of bug (added in 6.13.0): `.automation` resolved to `intent.automation`
  (doesn't exist) rather than the top-level `automation` package; would have thrown
  on any guarded intent execution. Now `..automation.mutex`.
- **New `scripts/audit.py`.** A real compile gate plus a cross-file resolver that
  verifies every relative import (top-level and lazy) points at a name that actually
  exists — the check that catches wrong levels and stale exports. Both bugs above
  compiled clean and passed the unit tests (which exercise pure functions, not these
  paths), which is exactly why this gate is now part of the release process. Audit
  reports clean; 170 tests passing.


- **Bug.** `audio/__init__.py` carried two module docstrings (a v6.13.0 edit
  prepended a second without removing the original), which pushed
  `from __future__ import annotations` to line 3 → `SyntaxError: from __future__
  imports must occur at the beginning of the file`. This aborted the whole
  integration import on HA startup (`Unable to import component: jarvis`). It was
  latent through 6.13.0–6.14.0 and only surfaced on the first HA restart after
  deploying. Fixed by collapsing to a single docstring.
- **Why the release audit missed it.** The pre-release syntax gate used
  `ast.parse`, which does **not** enforce `__future__` positioning — it parsed the
  broken file clean. Switched the gate to a real `compile()` / `py_compile`
  (bytecode compile), which catches `__future__` placement and matches how HA
  actually imports. Re-audited the full tree: all 62 modules compile clean. 170
  tests passing.


The tactical HUD ships as a real panel — a second sidebar entry, "Command Center"
(`/jarvis-command`), alongside the existing detailed JARVIS panel.
- **New panel (`frontend/jarvis-command.js`, `jarvis-command`).** The operational
  HUD: monospace/terminal styling, ASCII rules, the three-over-two layout. All data
  is live off `jarvis/get_panel_data` + `jarvis/get_activity_log` (polled): system
  status (observer/cognition/satellite count/LLM link), a 2D top-down occupancy plan
  whose nodes are driven by real per-area presence (dominant area labelled with live
  temp), and a live activity log. Quick actions are wired to real services —
  `jarvis.briefing`, `jarvis/set_lockdown` (toggles, reflects live state),
  `jarvis.diagnose_doorbell`.
- **Live, selectable camera feeds.** The camera panel streams the selected camera via
  HA's MJPEG proxy using the entity's rotating `access_token`, with a chip selector
  built from the live camera list and a still-image fallback for cameras that don't
  serve MJPEG (e.g. Nest/WebRTC). 
- **Event auto-focus.** `camera.py` now fires a `jarvis_camera_event` when a
  Frigate/Nest detection lands (entity, label, confidence); the panel subscribes to
  it (and to `jarvis_face_recognized`) and automatically switches the main feed to
  the camera of the event, banners "EVENT FOCUS · <label> <conf>%", flags the area on
  the plan, then reverts to the user's selection after ~25s.
- **Registration.** `panel_register.py` refactored to a shared `_register_one` helper
  registering both panels from the same static dir, each with independent content-hash
  cache-busting. The installer already copies `frontend/*.js`, so the new component
  ships with no run.sh change. No regressions — 170 passing.


Two additions from the resilience blueprint. (§7's `LocalSemanticMemory` was again
**not** re-added — `memory/` would shadow `memory.py`; the recovery ledger remains
top-level.)
- **Entity concurrency mutex (`automation/mutex.py`).** `EntityLockRegistry` enforces
  per-entity mutual exclusion on a priority ladder (PREDICTIVE < ROUTINE < INTENT <
  VISUAL < SAFETY): an equal-or-lower priority request is discarded while a lock is
  held, and a strictly-higher one preempts the holder — so a real-time presence
  command beats a stochastic predictive one rather than colliding. The intent router
  now acquires per-entity locks (at INTENT priority) before acting and releases after,
  skipping any entity already held at higher priority. Lock ops are synchronous dict
  mutations (safe on HA's single-threaded loop); the registry is stdlib-only and
  unit-tested, with an async `guard` context manager.
- **Differential noise gating (`audio/noise_gate.py`).** `NoiseGate` checks appliance
  power signatures (`sensor.<appliance>_power`) and subtracts the dominant running
  appliance's known dB contribution from the raw `ambient_db` before it reaches
  prosody — so a running dishwasher or microwave doesn't push JARVIS to project
  louder. Wired into `jarvis.speak`'s telemetry build; dB is floored at 0 and None
  passes through.
- **Tests:** +18 (mutex acquire/discard/preempt/release/guard, noise-gate threshold/
  dominant-attenuation/floor/passthrough) — 170 passing.


Two additions from the resilience blueprint. (§5's `LocalSemanticMemory` was again
**not** re-added, and the recovery ledger was placed at the top level rather than
the spec's `memory/ledger.py` — a `memory/` package would shadow `memory.py`.)
- **Heartbeat + failover (`diagnostics/heartbeat.py`).** `HeartbeatMonitor` probes
  fixed-IP audio satellites, flags a node unavailable after 3 missed cycles, and
  reroutes audio to the first available adjacent speaker (then any available node,
  then None). The probe defaults to a short TCP connect to the ESPHome API port but
  is injectable; the failover state machine is pure and fully unit-tested. This is a
  configured capability — construct it with your satellites' IPs/adjacency and drive
  `run_once` from an interval; it is not auto-started.
- **Write-ahead state ledger (`state_ledger.py`).** `StateLedger` durably appends a
  device intent (fsync'd JSON-lines) before a high-stakes action fires and a
  completion record after. On boot the integration reconciles any intent that never
  completed against the device's current state, logging actions a crash or power loss
  interrupted, then compacts the log. The intent router now records intent before
  `secure_area` (cover/lock) via an injected, duck-typed ledger — the router stays
  import-free and standalone-testable.
- **Tests:** +20 (heartbeat miss-threshold/recovery/failover ladder/injected probe,
  ledger record/complete/pending/reconcile/compact/torn-line tolerance) — 152 passing.


Three additions from the resilience blueprint (the spec's `LocalSemanticMemory`
was deliberately **not** re-added — it's the package that collided with `memory.py`;
its fault-history role already lives in `diagnostics/fault_log.py`).
- **Boot guard + alert queue (`boot_guard.py`).** `jarvis.speak` calls that arrive
  before the integration finishes initialising — or during a config-entry reload —
  are now buffered in a bounded in-memory queue and replayed in arrival order once
  setup reports ready, instead of being dropped or fired into a half-built system.
  Reload-safe (re-gates on each setup), drop-oldest overflow at 25, and the
  15-minute audit holds until ready. The buffer logic is a stdlib-only `AlertBuffer`
  so it's unit-tested directly.
- **Root-cause diagnostic trees (`diagnostics/monitor.py`).** When the core network
  switch drops offline, the triage engine now inspects its upstream power monitor
  (`sensor.core_switch_power_watts`) and folds the deduction into the spoken verdict:
  near-zero or unreachable power ⇒ "an upstream power loss on its utility circuit";
  power still present ⇒ "a network or uplink fault rather than a power loss."
- **Air-gapped fallback templates (`intent/templates.py`).** A curated set of
  hardcoded status phrases (grounded in this property's entities — switch, freeze
  sensor, sump pump, garage, storage, …) with keyword matching, for instant
  informational responses when every model link is unreachable. A starting
  scaffold, not 50 invented strings; extend `STATUS_TEMPLATES` as needed.
- **Tests:** +15 (root-cause branches, boot-queue buffer/replay/reload/overflow,
  template lookup/matching) — 132 passing.


- **Follow-up to 6.10.2.** Removing `memory/` from the repo isn't enough if the
  package still lingers in a deployed copy. Extracting a new release over an old
  add-on folder adds files but never deletes ones that were removed, so a stale
  `memory/` can survive in the source tree, ride into the rebuilt image, and get
  copied to `/config/custom_components/jarvis/memory/` on every start — where it
  again shadows `memory.py` and the Memory card reads "unavailable." `run.sh` now
  defensively deletes any `memory/` directory at the destination whenever the
  canonical `memory.py` is present, so a stale package cannot survive a deploy
  regardless of what the source tree carried. No Python change (117 passing).


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
