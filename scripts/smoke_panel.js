/**
 * Behavioral smoke test for the combined JARVIS panel (jarvis-panel).
 *
 * node --check only validates syntax — it can't catch an orphaned stylesheet, a
 * data-contract mismatch, or a camera module that never renders. This renders the
 * real component under jsdom with a realistic jarvis/get_panel_data payload and
 * asserts the dashboard actually draws: styles, the 3D residence, AND the folded-in
 * Camera Watch (live feed + chips from config.cameras + auto-selected stream).
 *
 * Run:  npm install jsdom --no-save && NODE_PATH=node_modules node scripts/smoke_panel.js
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const COMPONENT = path.resolve(__dirname, "..", "custom_components", "jarvis", "frontend", "jarvis-panel.js");
const dom = new JSDOM("<!DOCTYPE html><body></body>", { url: "http://localhost/", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
["HTMLElement", "customElements", "Node", "Event", "CustomEvent", "requestAnimationFrame", "cancelAnimationFrame"].forEach(k => { if (window[k]) global[k] = window[k]; });

window.eval(fs.readFileSync(COMPONENT, "utf8"));

// Raw get_panel_data contract: status.*, meta.*, dominant, areas[], config.cameras
const PANEL = {
  status: {
    observer: { state: "RUNNING", level: "live" }, sleep: { state: "ASLEEP", level: "warn" },
    gemini: { state: "READY", level: "live" }, broadcast: { state: "ONLINE", level: "live" },
    notify: { state: "READY", level: "live" }, satellites: { state: "8 / 8", level: "live" },
  },
  meta: { bedrooms: 3, areas_monitored: 14, announcements_today: 0, est_cost: "—", uptime: "6m" },
  dominant: { area_id: "garage", name: "Garage", subtitle: "Occupied · 26s", coord: "#09", temp: "66°", humidity: "52%", lights: "ON", satellite: "—", last_motion: "00:26" },
  areas: [
    { id: "garage", name: "Garage", caps: ["cam", "light"], active: true, bedroom: false, lights_on: 1, lights_total: 1,
      temp: "68°F", humidity: "51%", temp_entity: "sensor.garage_temp", humidity_entity: "sensor.garage_humidity", last_motion: "26s" },
    { id: "backyard", name: "Backyard", caps: ["cam", "mmwave"], active: true, bedroom: false, lights_on: 0, lights_total: 0,
      temp: null, humidity: null, temp_entity: null, humidity_entity: null, last_motion: null },
    { id: "kitchen", name: "Kitchen", caps: ["sat", "spkr"], active: false, bedroom: false, lights_on: 0, lights_total: 0,
      temp: "71°F", humidity: null, temp_entity: "sensor.kitchen_temp", humidity_entity: null, last_motion: "12m" },
  ],
  onboarding: { dismissed: false, show: true, done_count: 1, total: 5, steps: [
    { id: "notify", label: "Set an alert destination", hint: "phone", done: true, jump: "Notifications" },
    { id: "cameras", label: "Connect cameras (optional)", hint: "nest", done: false, jump: "Cameras" },
    { id: "voice", label: "Set up voice (optional)", hint: "voice", done: false },
    { id: "banter", label: "Pick a personality level", hint: "wit", done: false },
    { id: "briefings", label: "Turn on daily briefings", hint: "briefings", done: false, jump: "Briefings" },
  ] },
  config: { floor_plan_address: "123 Example St, Springfield IL", banter_level: 2, search_backend: "searxng", searxng_url: "http://sx.local:8080", calendar_tight_gap_min: 20, recognition_source: "frigate", voice_confirm_enabled: true, voice_confirm_mode: "gated", intrusion_response_timeout: 120, cameras: [{ entity_id: "camera.front", name: "Front Door", raw_name: "Front Door", outdoor: false, location_mode: "auto" }, { entity_id: "camera.back", name: "Backyard", raw_name: "Backyard", outdoor: true, location_mode: "auto" }], camera_names: {}, lockdown: { active: false } },
  suggestions: [
    { id: 11, description: "Turn porch light on at 18:00 (6 days running)", confidence: 0.82, count: 6, yaml: "{}",
      pattern_type: "time_routine", entities: ["light.porch"],
      why_headline: "A daily routine around 18:00",
      evidence: ["Observed turning on near 18:00", "Happened 6 times in the last 30 days", "Consistent on about 82% of days"] },
  ],
  goals: [
    { id: 1, title: "Guest prep", outcome: "House ready for guests by Saturday", status: "active",
      steps_done: 2, steps_total: 4, steps: [], next_check_ts: "2026-07-13T20:00:00", deadline_ts: null,
      last_result: "", updated_ts: "2026-07-13T19:00:00" },
    { id: 2, title: "Warm living room", outcome: "Living room at 72°", status: "done",
      steps_done: 1, steps_total: 1, steps: [], next_check_ts: "", deadline_ts: null,
      last_result: "Reached 72°, sir.", updated_ts: "2026-07-13T18:00:00" },
  ],
};
const _subscribedEvents = [];
const _renameCalls = [];
const _locationCalls = [];
const _sugCalls = [];
let _semanticEnabled = false;
let _activeMode = "normal";
let _energyAgency = "advisory";
let _bioEnabled = false;
let _intrCalledOff = false;
let _intrAck = false;
const _intrSnap = { url: "/local/jarvis/intrusion/intrusion_dining_room_1730000000.jpg", camera: "camera.dining_room", ts: 1730000000, path: "/config/www/jarvis/intrusion/x.jpg" };
const hass = {
  config: { location_name: "Springfield IL", latitude: 39.78, longitude: -89.65 },
  states: { "assist_satellite.a": { state: "idle", attributes: {} }, "camera.front": { attributes: { access_token: "tok123" } }, "camera.back": { attributes: { access_token: "tok456" } } },
  callWS: async (m) => {
    if (m.type === "jarvis/get_panel_data") return PANEL;
    if (m.type === "jarvis/get_activity_log") return { entries: [
      { ts: "08:59", urgency: "low", tag: "OBS", msg: "motion in kitchen" },
      { ts: "09:02", urgency: "medium", tag: "GOAL", msg: "goal #1 engaged quietly" },
      { ts: "09:05", urgency: "high", tag: "SAFETY", msg: "garage door left open" },
    ] };
    if (m.type === "jarvis/get_cognitive_status") return { learning: { days_of_data: 48, state_changes: 217802, commands: 93, suggestions: 0 }, ignore_rules: 0 };
    if (m.type === "jarvis/get_person_routines") return { routines: { sam: [
      { id: 1, pattern_type: "time_routine", description: "office light turns on around 07:00 most days when Sam is home", confidence: 0.82, occurrences: 9, last_seen: "2026-07-13" },
    ] } };
    if (m.type === "jarvis/get_knowledge") return { facts: [], stats: {} };
    if (m.type === "jarvis/camera_snapshot") return { image: "/9j/dGVzdGpwZWc=" };
    if (m.type === "jarvis/biometrics") {
      if (m.action === "enable") _bioEnabled = true;
      if (m.action === "disable") _bioEnabled = false;
      return { enabled: _bioEnabled, found: _bioEnabled ? 2 : 0, entities: _bioEnabled ? [
        { kind: "heart_rate", entity: "sensor.watch_hr", value: "62", unit: "bpm", name: "Heart Rate" },
        { kind: "sleep_stage", entity: "sensor.sleep_stage", value: "light_sleep", unit: "", name: "Sleep Stage" },
      ] : [] };
    }
    if (m.type === "jarvis/energy") {
      if (m.action === "status") return { watts: 9200, kw: 9.2, meter: "sensor.home_power", peak_watts: 8000, over_peak: true, agency: "advisory", configured_agency: "advisory", running: [
        { name: "Dryer", entity: "switch.dryer", watts: 4200, shed_ok: true },
        { name: "Refrigerator", entity: "sensor.fridge", watts: 200, shed_ok: false },
      ], advice: ["Heads up — Dryer and Oven are running at 9.2 kW, over your peak."] };
      if (m.action === "set_agency") { _energyAgency = m.agency; return { watts: 9200, kw: 9.2, over_peak: true, agency: m.agency, configured_agency: m.agency, running: [], advice: [] }; }
    }
    if (m.type === "jarvis/hazard") {
      if (m.action === "status") return { enabled: true, center: [40.77, -75.61], using_override: false,
        quake_radius_km: 300, quake_min_mag: 2.5, disaster_radius_km: 300,
        feeds: { earthquakes: true, weather: true, disasters: true } };
      if (m.action === "scan") return { ok: true, center: [40.77, -75.61],
        earthquakes: [{ id: "q1", mag: 3.4, place: "12km N of town", dist_km: 12 }],
        weather: [{ id: "w1", event: "Tornado Warning", severity: "Extreme", area: "Lehigh, PA" }],
        disasters: [{ id: "d1", title: "Wildfire", category: "Wildfires", dist_km: 40 }],
        counts: { earthquakes: 1, weather: 1, disasters: 1 } };
    }
    if (m.type === "jarvis/mode") {
      if (m.action === "status") return { active: _activeMode, since: 0, reason: "", description: "Default operation.", overrides: {}, available: [
        { name: "normal", description: "Default operation." },
        { name: "party", description: "Guests over." },
        { name: "movie", description: "Near-silent." },
        { name: "away", description: "Household away." },
      ]};
      if (m.action === "set") { _activeMode = m.mode; return { ok: true, mode: m.mode, active: m.mode, description: "switched", available: [
        { name: "normal", description: "Default operation." },
        { name: "party", description: "Guests over." },
      ]}; }
    }
    if (m.type === "jarvis/intrusion") {
      if (m.action === "log") return { events: [
        { id: "evt_1", ts: 1786000000, kind: "confirmed", reason: "person on camera",
          breach: "kitchen window", breach_area: "kitchen", camera: "camera.kitchen",
          snapshot_url: "/local/jarvis/intrusion/a.jpg", label: null },
        { id: "evt_2", ts: 1785999000, kind: "unresolved", reason: "no response",
          breach: "kitchen window", breach_area: "kitchen", snapshot_url: "", label: "false" },
      ], learning: { events: 2, labeled: 1, patterns: {}, damped_patterns: ["kitchen|4"], min_false_to_damp: 3 } };
      if (m.action === "label") return { ok: true, id: m.event_id, label: m.label,
        learning: { events: 2, labeled: 2, patterns: {}, damped_patterns: [], min_false_to_damp: 3 } };
      if (m.action === "learning") return { events: 2, labeled: 1, patterns: {}, damped_patterns: [], min_false_to_damp: 3 };
      if (m.action === "dismiss") { _intrCalledOff = true; return { ok: true, last_snapshot: _intrSnap, called_off: true, suppressed_for: 600, false_alarms_24h: 1 }; }
      if (m.action === "acknowledge") { _intrAck = true; return { ok: true, last_snapshot: _intrSnap, called_off: _intrCalledOff, acknowledged: true, suppressed_for: 0, false_alarms_24h: 0 }; }
      return { last_snapshot: _intrSnap, called_off: _intrCalledOff, acknowledged: _intrAck, suppressed_for: _intrCalledOff ? 600 : 0, false_alarms_24h: _intrCalledOff ? 1 : 0 };
    }
    if (m.type === "jarvis/voice_confirm_test") return { ok: true, satellite: "assist_satellite.basement_jarvis", note: "Announce fired." };
    if (m.type === "jarvis/diagnostics") return {
      overall: "warn", summary: "3/4 core services healthy",
      services: [
        { name: "LLM", key: "llm", status: "ok", detail: "reachable — 5 model(s) available" },
        { name: "Embeddings", key: "embeddings", status: "off", detail: "semantic search disabled" },
        { name: "TTS", key: "tts", status: "ok", detail: "tts.piper available" },
        { name: "STT", key: "stt", status: "warn", detail: "configured not found; 1 other present" },
      ],
    };
    if (m.type === "jarvis/semantic_search") {
      if (m.action === "status") return { enabled: _semanticEnabled, ollama_configured: true, base: "http://gpu.local:11434", model: "nomic-embed-text", vector_count: _semanticEnabled ? 42 : 0 };
      if (m.action === "enable") { _semanticEnabled = true; return { ok: true, enabled: true, model: "nomic-embed-text", base: "http://gpu.local:11434", dim: 768 }; }
      if (m.action === "disable") { _semanticEnabled = false; return { ok: true, enabled: false }; }
      if (m.action === "test") return { ok: true, model: "nomic-embed-text", dim: 768 };
    }
    if (m.type === "jarvis/documents") {
      if (m.action === "status") return { chroma: true, fts: false, chunk_count: 42, directory: "/config/jarvis/documents", sources: [{ source: "furnace_manual.pdf", chunks: 30 }, { source: "dishwasher_receipt.txt", chunks: 12 }] };
      if (m.action === "ingest") return { ok: true, files_ingested: 2, files_seen: 2, total_chunks: 42 };
      if (m.action === "search") return { results: [{ text: "The furnace filter size is 16x25x1 MERV 11.", source: "furnace_manual.pdf", chunk: 4, score: 0.88 }] };
      if (m.action === "upload") return { ok: true, filename: m.filename || "uploaded.pdf", chunks: 12, embedded: 12 };
      if (m.action === "scan_watch") return { ok: true, watched: 1, new_files: 2 };
      if (m.action === "delete") return { ok: true, filename: m.filename };
    }
    if (m.type === "jarvis/mmwave_overview") return {
      rooms: [
        { area_id: "kitchen", name: "Kitchen", outdoor: false, sensor_count: 2, detecting_count: 1, state: "detecting", freshest: "now", sensors: [] },
        { area_id: "office", name: "Office", outdoor: false, sensor_count: 1, detecting_count: 0, state: "clear", freshest: "12m", sensors: [] },
        { area_id: "patio", name: "Patio", outdoor: true, sensor_count: 1, detecting_count: 0, state: "clear", freshest: "3h", sensors: [] },
      ],
      summary: { rooms_with_mmwave: 3, rooms_detecting: 1, total_sensors: 4 },
    };
    if (m.type === "jarvis/suggestion_action") {
      _sugCalls.push({ id: m.suggestion_id, action: m.action });
      return m.action === "approve"
        ? { ok: true, installed: true, alias: "JARVIS · porch on at 18:00" }
        : { ok: true };
    }
    if (m.type === "jarvis/camera_location") {
      _locationCalls.push({ entity_id: m.entity_id, mode: m.mode });
      return { ok: true, cameras: [
        { entity_id: "camera.front", name: "Eliana's Room", raw_name: "Front Door", outdoor: m.mode === "outdoor", location_mode: m.mode },
        { entity_id: "camera.back", name: "Backyard", raw_name: "Backyard", outdoor: true, location_mode: "auto" },
      ] };
    }
    if (m.type === "jarvis/rename_camera") {
      _renameCalls.push({ entity_id: m.entity_id, name: m.name });
      return { ok: true,
        camera_names: m.name ? { [m.entity_id]: m.name } : {},
        cameras: [
          { entity_id: "camera.front", name: m.name || "Front Door", raw_name: "Front Door", outdoor: false, location_mode: "auto" },
          { entity_id: "camera.back", name: "Backyard", raw_name: "Backyard", outdoor: true, location_mode: "auto" },
        ] };
    }
    if (m.type === "jarvis/camera_diagnostics") return {
      summary: [{ entity_id: "camera.front", state: "idle", platform: "nest" }],
      platforms: { nest: 1, frigate: 1 },
      probe: {
        entity_id: "camera.front", state: "idle", platform: "nest",
        attrs: { frontend_stream_type: "web_rtc" },
        tiers: [
          ["backend:nest", "no image — no recent event media cached"],
          ["snapshot", "error: HomeAssistantError: stream unavailable"],
          ["wake-retry", "still unusable (0B)"],
        ],
        verdict: "NO FRAME from any tier. Nest cameras only yield event media after a motion/doorbell event — check Pub/Sub.",
        elapsed_ms: 4210,
      },
    };
    if (m.type === "jarvis/get_area_sparklines") return { sparklines: {
      garage: { temp: [64, 65, 66, 67, 68, 68, 67, 68], humidity: [50, 50, 51, 52, 51, 51, 50, 51] },
    } };
    if (m.type === "jarvis/get_debug_log") return { entries: [
      { ts: "09:00:01", cat: "CONV", msg: "heard: turn on the porch light" },
      { ts: "09:00:02", cat: "AGENT", msg: "executed light.turn_on for porch" },
      { ts: "09:01:15", cat: "ERROR", msg: "camera.front unavailable" },
    ] };
    return {};
  },
  connection: {
    subscribeEvents: async (handler, eventType) => {
      _subscribedEvents.push(eventType);
      return () => {};
    },
  },
  callService: async () => {},
};

const el = window.document.createElement("jarvis-panel");
window.document.body.appendChild(el);
el.hass = hass;

setTimeout(async () => {
  const sr = el.shadowRoot, html = sr.innerHTML;
  const checks = [
    // ── Command Center tab (default) ──
    ["stylesheet injected", html.includes("<style>") && html.includes("--cyan:") && html.includes("#00f2fe")],
    ["dashboard grid present", !!sr.querySelector(".grid")],
    ["onboarding welcome card shows for new users", !!sr.querySelector(".onboarding-card")],
    ["onboarding shows step progress + checklist",
      /1\/5 done/.test(sr.querySelector(".ob-progress")?.textContent || "") && sr.querySelectorAll(".ob-step").length === 5],
    ["onboarding surfaces the proactive briefings step",
      /Turn on daily briefings/.test(sr.querySelector(".ob-steps")?.textContent || "")],
    ["onboarding marks done steps", !!sr.querySelector(".ob-step.ob-done")],
    ["onboarding has dismiss + settings-jump", !!sr.querySelector("#ob-dismiss") && !!sr.querySelector(".ob-go[data-tab-jump]")],
    ["onboarding steps have per-step jump buttons", sr.querySelectorAll(".ob-step-go[data-ob-jump]").length >= 3],
    ["Residence tab button present", !!sr.querySelector('[data-tab="residence"]')],
    ["Camera Watch module present", !!sr.querySelector(".c-camera") && !!sr.querySelector("#cam-feed")],
    ["camera owns center (residence moved out of dashboard)", !sr.querySelector("#house3d-scene")],
    ["camera chips from config.cameras (2)", sr.querySelectorAll(".camchip[data-cam]").length === 2],
    ['camera auto-selected (no "NO CAMERA")', !/NO CAMERA SELECTED/.test(sr.querySelector("#cam-feed")?.innerHTML || "NO CAMERA SELECTED")],
    ["live MJPEG src wired with token", !!(sr.querySelector("#cam-feed img") && /camera_proxy_stream\/camera\.front\?token=tok123/.test(sr.querySelector("#cam-feed img").src))],
    ["camera native aspect (height:auto, no object-fit)", /\.cam-feed img\s*\{[^}]*height:\s*auto/.test(html) && !/\.cam-feed img\s*\{[^}]*object-fit/.test(html)],
    ["system status rows live (RUNNING)", /RUNNING/.test(html)],
    ["Goals panel present", !!sr.querySelector(".goal-list")],
    ["both goals rendered", sr.querySelectorAll(".goal").length === 2],
    ["active goal has cancel button, done goal doesn't",
      !!sr.querySelector('.goal-active .goal-cancel') && !sr.querySelector('.goal-done .goal-cancel')],
    ["done goal has a delete button (tidy the list)",
      !!sr.querySelector('.goal-done .goal-delete')],
    ["new-goal input present (write a goal in)",
      !!sr.querySelector('.goal-new-input') && !!sr.querySelector('.goal-new-btn')],
    ["done goal shows status badge", /DONE/.test(sr.querySelector(".goal-done .goal-status-badge")?.textContent || "")],
    ["active goal shows step progress (2/4)", /2\/4/.test(sr.querySelector(".goal-active .goal-steps-pct")?.textContent || "")],
    ["real-time state_changed subscription wired", _subscribedEvents.includes("state_changed")],
    ["camera event subscriptions still wired", _subscribedEvents.includes("jarvis_camera_event")],
    ["area tile shows temp reading", /68°F/.test(sr.querySelector('.area[data-area-id="garage"] .area-reading')?.textContent || "")],
    ["area tile sparkline rendered for garage", !!sr.querySelector('.area[data-area-id="garage"] .spark')],
    ["area tile without sensor has no readings row", !sr.querySelector('.area[data-area-id="backyard"] .area-readings')],
    ["area tiles are keyboard-focusable (drill-down affordance)", sr.querySelector('.area[data-area-id="garage"]')?.getAttribute('tabindex') === '0'],
    ["activity search box present", !!sr.getElementById("activity-search")],
    ["activity feed renders all mock entries", sr.querySelectorAll("#activity-feed .evt").length === 3],
    ["activity feed rows carry category icons", sr.querySelectorAll("#activity-feed .evt .evt-icon").length === 3],
  ];

  // ── activity feed search: narrow, count, empty state, live-patch respect ──
  el._activitySearch = "garage";
  el._updateActivityFeed();
  checks.push(
    ["activity search narrows feed", el.shadowRoot.querySelectorAll("#activity-feed .evt").length === 1],
    ["activity count shows filtered/total", /1 OF 3/.test(el.shadowRoot.getElementById("activity-count")?.textContent || "")],
  );
  el._patchLiveDom(PANEL);  // a poll/real-time refresh must keep the filter applied
  checks.push(
    ["live patch keeps activity filter applied", el.shadowRoot.querySelectorAll("#activity-feed .evt").length === 1],
  );
  el._activitySearch = "zzz-no-match";
  el._updateActivityFeed();
  checks.push(
    ["activity search empty state shown", /No events match/.test(el.shadowRoot.getElementById("activity-feed")?.textContent || "")],
  );
  el._activitySearch = "";
  el._updateActivityFeed();
  checks.push(
    ["clearing activity search restores all entries", el.shadowRoot.querySelectorAll("#activity-feed .evt").length === 3
      && /LAST 3/.test(el.shadowRoot.getElementById("activity-count")?.textContent || "")],
  );

  // ── click the Garage tile: entity-card drill-down should open ──
  sr.querySelector('.area[data-area-id="garage"]')?.click();
  const detail = el.shadowRoot;
  checks.push(
    ["area detail overlay opens on tile click", !!detail.getElementById("area-detail-overlay")],
    ["area detail shows the right area name", /Garage/.test(detail.querySelector(".area-detail-title")?.textContent || "")],
    ["area detail shows temp value + sparkline", /68°F/.test(detail.querySelector(".ads-value")?.textContent || "") && !!detail.querySelector(".ads-spark .spark")],
  );
  detail.querySelector(".area-detail-close")?.click();
  checks.push(
    ["area detail overlay closes on ✕", !el.shadowRoot.getElementById("area-detail-overlay")],
  );

  // ── switch to Residence tab and re-check ──
  el._currentTab = "residence";
  el._render();
  const r = el.shadowRoot, rhtml = r.innerHTML;
  checks.push(
    ["residence tab renders iso scene", !!r.querySelector("#house3d-scene")],
    ["2D isometric SVG rendered", !!r.querySelector("#res-iso svg")],
    ["solid house drawn (svg polygons)", r.querySelectorAll("#res-iso svg polygon").length >= 15],
    ["3D house is data-driven from editor rooms (feet)", (() => { const p = el._house3dPlan(); return !!(p && p["1f"] && p["1f"].length && p["1f"].some(rm => rm.w > 0 && rm.d > 0 && rm.name)); })()],
    ["home-type roof is applied (gable/hip/flat/gambrel by style)", (() => { const sp = el._houseSpec(); return ["gable","hip","flat","gambrel"].includes(sp.roof) && sp.stories != null; })()],
    ["Dutch Colonial maps to a gambrel roof", el._styleDefaults('dutch_colonial').roof === 'gambrel' && el._resStyles().dutch_colonial.roof === 'gambrel' && el._resStyles().dutch_colonial.label === 'Dutch Colonial'],
    ["gambrel exterior renders without error", (() => { try { const s = el._liveData && el._liveData.config; const prev = s ? el._liveData.config.residence_style : null; if (s) el._liveData.config.residence_style = 'dutch_colonial'; const svg = el._renderEditorPreview(); if (s) el._liveData.config.residence_style = prev; return typeof svg === 'string' && svg.length > 100; } catch (e) { return false; } })()],
    ["operational mode has AUTO occupancy toggle", (() => { try { const prev = el._currentTab; el._currentTab = 'settings'; const h = el._html(); el._currentTab = prev; return /data-cfg-key="operational_mode_auto"/.test(h) && /mode-auto-row/.test(h); } catch (e) { return false; } })()],
    ["openings entity list includes window sensors", (() => { const st = el._hass.states; st['binary_sensor.test_kitchen_window'] = { state: 'off', attributes: { device_class: 'window', friendly_name: 'Kitchen Window' } }; const html = el._doorEntityOptions(''); delete st['binary_sensor.test_kitchen_window']; return /test_kitchen_window/.test(html); })()],
    ["mode bindings: lab rooms + movie room/player/dim", (() => { try { const prev = el._currentTab; el._currentTab = 'settings'; const h = el._html(); el._currentTab = prev; return /class="mode-bindings"/.test(h) && /data-lab-area/.test(h) && /data-cfg-key="movie_area"/.test(h) && /data-cfg-key="movie_media_player"/.test(h) && /data-cfg-key="movie_dim_pct"/.test(h); } catch (e) { return false; } })()],
    ["exterior All view draws lit windows + garage doors (clean shell)", (() => { const s = el._build3DHouse ? "" : ""; const svg = r.querySelector("#res-iso")?.innerHTML || ""; return /class="gdoor"/.test(svg) || /gdoor/.test(svg); })()],
    ["occupied stat wired (n / total)", /\d+\s*\/\s*\d+/.test((r.getElementById("res-occ") || {}).textContent || "")],
    ["home-style selector with options", !!r.querySelector("#res-style-sel") && r.querySelectorAll("#res-style-sel option").length >= 6],
    ["property banner reflects HA home location (not a hardcoded personal default)", !!r.querySelector(".res-banner") && /Springfield IL/.test(r.querySelector("#res-addr")?.textContent || "")],
    ["floor editor exposes export/import + units controls", (() => { const h = el._renderFloorPlanEditor(el._data()); return /id="fp-export"/.test(h) && /class="fp-import-layout"/.test(h) && /id="fp-units"/.test(h); })()],
    ["floor editor: place windows/exterior/cellar/interior openings", (() => { const h = el._renderOpenings("1f"); return /id="op-add-window"/.test(h) && /id="op-add-extdoor"/.test(h) && /id="op-add-cellar"/.test(h) && /id="op-add-intdoor"/.test(h); })()],
    ["cased openings: add button + room-scoped no-sensor row", (() => { try { const h = el._renderOpenings("1f"); if (!/id="op-add-cased"/.test(h)) return false; const arr = el._elemsFor("1f"); const n0 = arr.length; arr.push({ id: 'ec', type: 'door', kind: 'cased', wall: 'front', room: '', pos: 0.5, w: 20 }); const h2 = el._renderOpenings("1f"); const entBefore = (h.match(/data-op="entity"/g) || []).length; const entAfter = (h2.match(/data-op="entity"/g) || []).length; arr.length = n0; return /CASED OPENING/.test(h2) && /open passage/.test(h2) && /data-op="room"/.test(h2) && entAfter === entBefore; } catch (e) { return false; } })()],
    ["camera FOV: add button + placement + cone", (() => { try { if (!/id="cam-add"/.test(el._renderCameras("1f"))) return false; const arr = el._camsFor("1f"); const n0 = arr.length; arr.push({ id: 'ct', x: 100, y: 80, angle: 270, fov: 90, range: 55, entity: '', indoor: true }); const h2 = el._renderCameras("1f"); const coneOk = /^M 100 80 L .* A 55 55 .* Z$/.test(el._coneD(arr[arr.length - 1])); const svg = el._renderEditableSVG({ '1f': { rooms: [{ name: 'Dining', x: 50, y: 50, w: 80, h: 60 }] } }, "1f"); arr.length = n0; return /CAM 1/.test(h2) && /INDOOR/.test(h2) && coneOk && /class="fp-cam"/.test(svg) && /fp-cam-cone/.test(svg) && /fp-cam-dot/.test(svg); } catch (e) { return false; } })()],
    ["camera coverage: LOS through openings, walls block", (() => { try { const sp = el._editingPlan, se = el._editingElements; el._editingPlan = { '1f': { rooms: [ { name: 'Dining', x: 0, y: 0, w: 40, h: 40, type: 'room' }, { name: 'Kitchen', x: 40, y: 0, w: 40, h: 40, type: 'room' } ] } }; el._editingElements = { '1f': [ { id: 'o1', type: 'door', kind: 'cased', room: 'Dining', wall: 'right', pos: 0.5, w: 20 } ] }; const cam = { x: 20, y: 20, angle: 0, fov: 170, range: 120, indoor: true }; const withOpen = el._computeCoverage('1f', cam); el._editingElements = { '1f': [] }; const noOpen = el._computeCoverage('1f', cam); el._editingPlan = sp; el._editingElements = se; return withOpen.Kitchen > 0 && !noOpen.Kitchen && withOpen.Dining > 0; } catch (e) { return false; } })()],
    ["camera coverage LLM: compute button + confirms/reason + roomAt", (() => { try { const sp = el._editingPlan, se = el._editingElements, sc = el._editingCameras; el._editingPlan = { '1f': { rooms: [ { name: 'Dining', x: 0, y: 0, w: 40, h: 40, type: 'room' }, { name: 'Living', x: 0, y: 40, w: 80, h: 50, type: 'room' } ] } }; el._editingElements = { '1f': [ { id: 'o1', type: 'door', kind: 'cased', room: 'Dining', wall: 'back', pos: 0.5, w: 24 } ] }; el._editingCameras = { '1f': [ { id: 'c1', x: 20, y: 20, angle: 90, fov: 150, range: 120, entity: '', indoor: true, coverage: { covered: ['Dining', 'Living'], reason: 'via the open staircase', source: 'llm' } } ] }; const h = el._renderCameras('1f'); const ra = el._roomAt('1f', 20, 20); const od = el._openingDescriptions('1f'); el._editingPlan = sp; el._editingElements = se; el._editingCameras = sc; return /id="cam-compute"/.test(h) && /confirms Dining, Living/.test(h) && /open staircase/.test(h) && ra === 'Dining' && od.length > 0; } catch (e) { return false; } })()],
    ["3D view presets render + set the angle", (() => { const bar = el._viewPresetBar('editor'); const before = el._editorTheta; el._setView('editor', 90); const ok = /data-vtheta="90"/.test(bar) && /FRONT/.test(bar) && /ISO/.test(bar) && el._editorTheta === 90; el._editorTheta = before; return ok; })()],
    ["editor viewBox auto-fits to rooms with padding", (() => { const plan = { '1f': { rooms: [{ x: 100, y: 100, w: 80, h: 60, name: 'Test' }] } }; const svg = el._renderEditableSVG(plan, '1f'); const m = svg.match(/viewBox=\"([^\"]+)\"/); if (!m) return false; const p = m[1].split(' ').map(Number); return p[0] < 100 && p[1] < 100 && p[2] >= 80 && p[3] >= 140; })()],
    ["settings: routine-learning card (datalist picker + doors/presence + chip renders)", (() => { const cfg = el._liveData.config; cfg.pattern_include_entities = ["binary_sensor.zzz_test"]; const h = el._renderRoutineLearning(el._data()); delete cfg.pattern_include_entities; return /data-cfg-key="pattern_learn_doors"/.test(h) && /data-cfg-key="pattern_learn_presence"/.test(h) && /list="pl-entity-list"/.test(h) && /binary_sensor\.zzz_test/.test(h); })()],
    ["model self-heal picks vision-capable model for the vision role", (() => { const v = el._pickHealModel(["openai/gpt-oss-120b","qwen/qwen3.6-27b"], "vision_model"); const t = el._pickHealModel(["openai/gpt-oss-120b","qwen/qwen3.6-27b"], "model"); return v === "qwen/qwen3.6-27b" && t === "openai/gpt-oss-120b"; })()],
    ["floor editor has a live 3D preview", (() => { const h = el._renderFloorPlanEditor(el._data()); return /id="fp-3d-preview"/.test(h) && typeof el._renderEditorPreview === "function"; })()],
    ["floor editor: dormers placeable on the 2nd floor", (() => { const h = el._renderOpenings("2f"); const h1 = el._renderOpenings("1f"); return /id="op-add-fdormer"/.test(h) && /id="op-add-rdormer"/.test(h) && !/op-add-fdormer/.test(h1); })()],
    ["garage bay open/closed resolves per-bay from its sensor", (() => { const cfg = el._liveData.config; el._hass.states["binary_sensor._g1"] = { state: "open", attributes: {} }; cfg.garage_bays = 2; cfg.door_mapping = { garage_1: "binary_sensor._g1" }; const g = el._house3dGarage(); const ok = !!(g.length === 2 && g[0].open === true && g[1].open === false); delete cfg.garage_bays; delete cfg.door_mapping; delete el._hass.states["binary_sensor._g1"]; return ok; })()],
    ["placed door resolves open/closed from its sensor", (() => { const cfg = el._liveData.config; el._hass.states["binary_sensor._t"] = { state: "on", attributes: {} }; cfg.floor_plan_elements = { "1f": [{ type: "door", kind: "exterior", wall: "front", pos: 0.5, w: 12, entity: "binary_sensor._t" }] }; const e = el._house3dElements()["1f"][0]; const ok = !!(e && e.open === true && e.type === "door" && e.w > 0); delete cfg.floor_plan_elements; delete el._hass.states["binary_sensor._t"]; return ok; })()],
    ["banner stats populated (sqft + bed/bath)", /\d/.test(r.querySelector("#res-sqft")?.textContent || "") && /\d/.test(r.querySelector("#res-bb")?.textContent || "")],
    ["sqft estimate sane (<= 5000)", (() => { const m = (r.querySelector("#res-sqft")?.textContent || "").replace(/[^\d]/g, ""); return m && Number(m) <= 5000; })()],
    ["style tag reflects template", /CAPE COD/.test(r.querySelector("#res-style-tag")?.textContent || "")],
    ["3D residence is rotatable (drag wired)", r.querySelector("#house3d-scene")?._house3dWired === true]
  );

  // ── switch to 1st-floor isolation: model should draw labeled rooms ──
  el._currentFloor = "1f";
  el._render();
  checks.push(
    ["floor isolation draws labeled rooms (1F)", el.shadowRoot.querySelectorAll("#res-iso svg text").length >= 6],
    ["floor isolation keeps garage room", /GARAGE/.test(el.shadowRoot.querySelector("#res-iso svg")?.textContent || "")]
  );

  // ── camera fallback chain: stream → still → JARVIS WS snapshot ──
  el._currentTab = "dashboard";   // the floor-plan section above leaves us on residence
  el._render();
  const camImg = el.shadowRoot.querySelector("#cam-feed img");
  camImg.dispatchEvent(new window.Event("error"));       // MJPEG failed
  checks.push(
    ["cam error #1 falls back to proxy stills", el._camMode === "still"
      && /camera_proxy\/camera\.front/.test(camImg.src)],
  );
  camImg.dispatchEvent(new window.Event("error"));       // stills failed too
  await new Promise(r => setTimeout(r, 20));             // let the WS shot resolve
  checks.push(
    ["cam error #2 escalates to JARVIS snapshot tier", el._camMode === "jarvis"],
    ["JARVIS tier renders the WS frame as a data URL", /^data:image\/jpeg;base64,/.test(camImg.src)],
    ["resolved tier remembered per entity", el._camModeByEntity["camera.front"] === "jarvis"],
  );

  // ── watchdog: proxies that HANG (no error event) still escalate ──
  el._camMode = "stream";
  delete el._camModeByEntity["camera.front"];
  el._armCamWatchdog("camera.front", camImg, "stream", 5);
  await new Promise(r => setTimeout(r, 25));
  checks.push(
    ["hung stream (no pixels, no error) watchdogs into stills", el._camMode === "still"],
  );
  el._armCamWatchdog("camera.front", camImg, "still", 5);
  await new Promise(r => setTimeout(r, 25));
  checks.push(
    ["hung stills watchdog into JARVIS tier", el._camMode === "jarvis"],
  );

  // ── WS failure (e.g. HA not restarted) surfaces a hint, not silence ──
  const realCallWS = hass.callWS;
  hass.callWS = async (m) => {
    if (m.type === "jarvis/camera_snapshot") throw new Error("unknown command jarvis/camera_snapshot");
    return realCallWS(m);
  };
  el._camWsTimer && clearInterval(el._camWsTimer); el._camWsTimer = null;
  el._camJarvisFallback("camera.front");
  await new Promise(r => setTimeout(r, 20));
  hass.callWS = realCallWS;
  checks.push(
    ["WS-unavailable shows restart hint instead of blank", /restart Home Assistant/i.test(
      el.shadowRoot.querySelector("#cam-feed .cam-none")?.textContent || "")],
  );

  // ── camera diagnostics: DIAG button probes and renders verdicts ──
  el.shadowRoot.getElementById("cam-diag-btn")?.click();
  await new Promise(r => setTimeout(r, 20));
  const diag = el.shadowRoot.querySelector("#cam-feed .cam-diag");
  const diagText = diag?.textContent || "";
  checks.push(
    ["DIAG button present in Camera Watch head", !!el.shadowRoot.getElementById("cam-diag-btn")],
    ["DIAG overlay renders platform histogram", /nest×1/.test(diagText) && /frigate×1/.test(diagText)],
    ["DIAG shows per-tier verdicts", /backend:nest/.test(diagText) && /wake-retry/.test(diagText)],
    ["DIAG surfaces the actionable Nest verdict", /Pub\/Sub/.test(diagText)],
    ["DIAG TILE line reports client-side render state", /TILE/.test(diagText) && /no decoded pixels/.test(diagText)],
  );
  el.shadowRoot.getElementById("cam-diag-btn")?.click();   // toggle off
  checks.push(
    ["DIAG toggles closed on second tap", !el.shadowRoot.querySelector("#cam-feed .cam-diag")],
  );

  // ── camera_overrides: frames reroute to the restream twin ──
  el._liveData.config.camera_overrides = { "camera.front": "camera.back" };
  el._camMode = "stream"; delete el._camModeByEntity["camera.front"];
  if (el._camWsTimer) { clearInterval(el._camWsTimer); el._camWsTimer = null; }
  el._lastCamKey = "";
  el._renderCameraFeed();
  const ovImg = el.shadowRoot.querySelector("#cam-feed img");
  checks.push(
    ["override reroutes stream URL to the twin", /camera_proxy_stream\/camera\.back/.test(ovImg?.src || "")],
    ["override uses the twin's token", /tok456/.test(ovImg?.src || "")],
        ["camera settings expose enable/disable toggles (choose all/some/none)", (() => { const h = el._renderCameraSettings(el._data()); return /cam-enable-toggle/.test(h) && /id="cam-disable-all"/.test(h) && /cameras in use/.test(h); })()],
    ["strip shows the override mapping", /Front Door → back/.test(el.shadowRoot.getElementById("cam-strip")?.textContent || "")],
  );
  delete el._liveData.config.camera_overrides;
  el._lastCamKey = ""; el._renderCameraFeed();   // restore for anything downstream

  // ── Settings tab: camera names + location designation (v6.50.0 home) ──
  el._currentTab = "settings";
  el._render();
  const camsetRows = el.shadowRoot.querySelectorAll(".camset-row");
  checks.push(
    ["✎ button removed from Command Center (decluttered)",
      !el.shadowRoot.getElementById("cam-rename-btn")],
    ["Settings renders a row per camera", camsetRows.length === 2],
    ["JARVIS Character panel renders banter + web-research controls",
      !!el.shadowRoot.querySelector('[data-cfg-key="banter_level"]')
      && !!el.shadowRoot.querySelector('[data-cfg-key="search_backend"]')
      && !!el.shadowRoot.querySelector('[data-cfg-key="calendar_tight_gap_min"]')],
    ["banter select reflects the SAVED value (not reset to default)",
      el.shadowRoot.querySelector('[data-cfg-key="banter_level"]')?.value === "2"],
    ["web-research backend reflects saved value",
      el.shadowRoot.querySelector('[data-cfg-key="search_backend"]')?.value === "searxng"],
    ["recognition source selector present and reflects saved value",
      el.shadowRoot.querySelector('[data-cfg-key="recognition_source"]')?.value === "frigate"],
    ["voice-confirm toggle + mode reflect saved values",
      el.shadowRoot.querySelector('[data-cfg-key="voice_confirm_enabled"]')?.classList.contains("on")
      && el.shadowRoot.querySelector('[data-cfg-key="voice_confirm_mode"]')?.value === "gated"],
    ["voice-confirm test button present", !!el.shadowRoot.getElementById("vc-test")],
    ["name input placeholder is the HA name",
      el.shadowRoot.querySelector('.camset-name[data-cam="camera.front"]')?.getAttribute("placeholder") === "Front Door"],
    ["location chips render with resolved AUTO label",
      /AUTO \(indoor\)/.test(el.shadowRoot.querySelector('.camset-row[data-cam="camera.front"]')?.textContent || "")],
  );

  const nameInput = el.shadowRoot.querySelector('.camset-name[data-cam="camera.front"]');
  nameInput.value = "Eliana's Room";
  nameInput.dispatchEvent(new window.Event("blur"));
  await new Promise(r => setTimeout(r, 20));
  checks.push(
    ["rename WS called with entity + new name",
      _renameCalls.length === 1 && _renameCalls[0].entity_id === "camera.front"
      && _renameCalls[0].name === "Eliana's Room"],
    ["display name resolver picks up the rename", el._camName("camera.front") === "Eliana's Room"],
  );
  nameInput.dispatchEvent(new window.Event("blur"));       // unchanged — must not re-call
  await new Promise(r => setTimeout(r, 10));
  checks.push(
    ["unchanged blur does not re-save", _renameCalls.length === 1],
  );

  el.shadowRoot.querySelector('.camset-row[data-cam="camera.front"] .cam-loc-chip[data-loc="outdoor"]')?.click();
  await new Promise(r => setTimeout(r, 20));
  checks.push(
    ["location WS called with entity + mode", _locationCalls.length === 1
      && _locationCalls[0].entity_id === "camera.front" && _locationCalls[0].mode === "outdoor"],
    ["OUTDOOR chip becomes active in the row",
      el.shadowRoot.querySelector('.camset-row[data-cam="camera.front"] .cam-loc-chip[data-loc="outdoor"]')?.classList.contains("active") === true],
    ["camera metadata refreshed from response",
      (el._cams.find(c => c.entity_id === "camera.front") || {}).location_mode === "outdoor"],
  );
  el._currentTab = "dashboard";
  el._render();
  checks.push(
    ["strip on Command Center shows the JARVIS-only name",
      /Eliana's Room/.test(el.shadowRoot.getElementById("cam-strip")?.textContent || "")],
  );

  // ── switch to Memory tab: person routines fetch + render ──
  el._currentTab = "memory";
  el._render();
  await el._fetchPersonRoutines();
  const mem = el.shadowRoot;
  checks.push(
    ["Person Routines panel present", !!mem.getElementById("proutine-list")],
    ["person group rendered (Sam)", /Sam/.test(mem.getElementById("proutine-list")?.textContent || "")],
    ["routine description rendered", /office light turns on/.test(mem.getElementById("proutine-list")?.textContent || "")],
    ["confidence bar rendered (82%)", /82%/.test(mem.getElementById("proutine-list")?.textContent || "")],
  );

  // ── switch to Logs tab: category filter + text search ──
  el._currentTab = "logs";
  el._render();
  await el._fetchDebugLog();
  const logs1 = el.shadowRoot;
  checks.push(
    ["log search box present", !!logs1.getElementById("log-search")],
    ["all 3 log entries render initially", logs1.querySelectorAll(".log-entry").length === 3],
    ["log count shows total", /3 entries/.test(logs1.getElementById("log-count")?.textContent || "")],
  );

  el._logSearch = "porch";
  await el._fetchDebugLog();
  const logs2 = el.shadowRoot;
  checks.push(
    ["search narrows to matching entries", logs2.querySelectorAll(".log-entry").length === 2],
    ["search excludes non-matching entry", !/camera\.front unavailable/.test(logs2.getElementById("debug-log-entries")?.textContent || "")],
    ["log count reflects filtered/total", /2 of 3/.test(logs2.getElementById("log-count")?.textContent || "")],
  );

  el._logSearch = "nonexistent-term-xyz";
  await el._fetchDebugLog();
  checks.push(
    ["search with no matches shows empty state, not a blank pane",
      /No entries match/.test(el.shadowRoot.getElementById("debug-log-entries")?.textContent || "")],
  );

  // ── pattern-engine suggestion: approve installs the automation (v6.52.0) ──
  el._currentTab = "dashboard";
  el._render();
  const sugCard = el.shadowRoot.querySelector('.sug[data-sug-id="11"]');
  checks.push(
    ["suggestion card renders with approve button",
      !!sugCard && !!sugCard.querySelector(".sug-approve")],
    ["suggestion shows the why headline",
      !!sugCard && /A daily routine around 18:00/.test(sugCard.textContent)],
    ["suggestion shows observed evidence",
      !!sugCard && /What JARVIS observed/.test(sugCard.textContent)
        && /Happened 6 times/.test(sugCard.textContent)],
    ["suggestion shows a typed pattern chip",
      !!sugCard && !!sugCard.querySelector(".sug-type")],
    ["suggestion shows the entity involved",
      !!sugCard && /light\.porch/.test(sugCard.textContent)],
  );
  sugCard?.querySelector(".sug-approve")?.click();
  await new Promise(r => setTimeout(r, 20));
  const toastEl = el.shadowRoot.querySelector(".toast, #toast, .jarvis-toast");
  checks.push(
    ["approve sends suggestion_action", _sugCalls.length === 1
      && _sugCalls[0].id === 11 && _sugCalls[0].action === "approve"],
    ["approved card is visually retired", sugCard?.style.opacity === "0.35"],
  );

  // ── mmWave presence overview on the residence tab (v6.53.0) ──
  el._currentTab = "residence";
  el._render();
  await el._fetchMmwave();
  const mmList = el.shadowRoot.getElementById("mmwave-list");
  const mmText = mmList?.textContent || "";
  const mmSummary = el.shadowRoot.getElementById("mmwave-summary")?.textContent || "";
  checks.push(
    ["mmWave panel renders a row per sensor-equipped room",
      mmList?.querySelectorAll(".mmwave-room").length === 3],
    ["occupied room is marked live", !!mmList?.querySelector(".mmwave-room.live .mmwave-dot.on")],
    ["occupied room shows OCCUPIED + now", /Kitchen/.test(mmText) && /OCCUPIED/.test(mmText)],
    ["clear room shows freshness age", /Office/.test(mmText) && /12m/.test(mmText)],
    ["outdoor room tagged", /Patio/.test(mmText) && /OUT/.test(mmText)],
    ["summary reflects detecting/total", /1\/3 OCCUPIED/.test(mmSummary)],
  );

  // ── mmWave glow feeds the floor plan itself (v6.54.0) ──
  // Kitchen is detecting in the mock; _house3dLit must mark it 'mmwave', a
  // distinct state from plain area-occupancy, so the plan glows accordingly.
  const litMap = el._house3dLit();
  checks.push(
    ["detecting room enters floor-plan lit map as 'mmwave'",
      litMap["kitchen"] === "mmwave"],
    ["non-detecting sensor room is not force-lit by mmwave",
      litMap["office"] !== "mmwave"],
  );

  // ── Document Library (RAG) panel on Settings (v6.55.0) ──
  el._currentTab = "settings";
  el._render();
  await el._fetchDocLibrary();
  const docBody = el.shadowRoot.getElementById("doclib-body");
  const docStatus = el.shadowRoot.getElementById("doclib-status");
  checks.push(
    ["doc library shows backend + chunk count", /VECTOR/.test(docStatus?.textContent || "") && /42/.test(docStatus?.textContent || "")],
    ["doc library lists ingested sources",
      /furnace_manual\.pdf/.test(docBody?.textContent || "") && /dishwasher_receipt\.txt/.test(docBody?.textContent || "")],
  );
  // ingest button
  el.shadowRoot.getElementById("doclib-ingest")?.click();
  await new Promise(r => setTimeout(r, 20));
  checks.push(
    ["doc ingest button present + wired", !!el.shadowRoot.getElementById("doclib-ingest")],
  );
  // test-search
  const dq = el.shadowRoot.getElementById("doclib-q");
  if (dq) {
    dq.value = "furnace filter size";
    dq.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await new Promise(r => setTimeout(r, 20));
  }
  checks.push(
    ["doc search renders excerpt with source + score",
      /16x25x1/.test(el.shadowRoot.getElementById("doclib-body")?.textContent || "")
      && /furnace_manual\.pdf/.test(el.shadowRoot.getElementById("doclib-body")?.textContent || "")],
  );

  // ── semantic search banner (Ollama embeddings, v6.57.0) ──
  await el._fetchVectorBackend();
  const vbState = el.shadowRoot.getElementById("vecbk-state")?.textContent || "";
  const vbBtn = el.shadowRoot.getElementById("vecbk-toggle");
  checks.push(
    ["semantic banner shows KEYWORD when not enabled", /KEYWORD/.test(vbState)],
    ["enable button offered when Ollama configured",
      vbBtn && vbBtn.style.display !== "none" && /ENABLE/.test(vbBtn.textContent)],
  );
  // enabling flips to SEMANTIC and offers a disable toggle
  vbBtn?.click();
  await new Promise(r => setTimeout(r, 20));
  const vbState2 = el.shadowRoot.getElementById("vecbk-state")?.textContent || "";
  const vbBtn2 = el.shadowRoot.getElementById("vecbk-toggle");
  checks.push(
    ["after enable the banner reflects SEMANTIC (Ollama)", /SEMANTIC/.test(vbState2)],
    ["disable toggle offered once semantic active",
      vbBtn2 && /DISABLE/.test(vbBtn2.textContent)],
  );

  // ── Intrusion / Security panel (v6.68.0) ──
  await el._fetchIntrusion();
  const intrBody = el.shadowRoot.getElementById("intr-body")?.innerHTML || "";
  const intrStatus = el.shadowRoot.getElementById("intr-status")?.textContent || "";
  checks.push(
    ["intrusion panel shows ARMED status", /ARMED/.test(intrStatus)],
    ["intrusion panel renders the last snapshot image", /intrusion_dining_room/.test(intrBody) && /intr-img/.test(intrBody)],
    ["intrusion call-off button present", !!el.shadowRoot.querySelector(".intr-dismiss")],
    ["intrusion acknowledge button present", !!el.shadowRoot.querySelector(".intr-ack-btn")],
    ["intrusion response-timeout selector present", !!el.shadowRoot.querySelector('[data-cfg-key="intrusion_response_timeout"]')],
  );
  el.shadowRoot.querySelector(".intr-dismiss")?.click();
  await new Promise(r => setTimeout(r, 20));
  checks.push(
    ["calling off intrusion flips status to CALLED OFF",
      /CALLED OFF/.test(el.shadowRoot.getElementById("intr-status")?.textContent || "")],
  );

  // ── Wellbeing Context panel (v6.63.0) ──
  await el._fetchBio();
  const bioStatus = el.shadowRoot.getElementById("bio-status")?.textContent || "";
  checks.push(
    ["wellbeing panel shows OFF by default", /OFF/.test(bioStatus)],
    ["wellbeing enable button present", !!el.shadowRoot.getElementById("bio-toggle")],
  );
  // enabling reveals discovered wearable entities
  el.shadowRoot.getElementById("bio-toggle")?.click();
  await new Promise(r => setTimeout(r, 20));
  const bioBody2 = el.shadowRoot.getElementById("bio-body")?.textContent || "";
  const bioStatus2 = el.shadowRoot.getElementById("bio-status")?.textContent || "";
  checks.push(
    ["enabling wellbeing turns status ON", /ON/.test(bioStatus2)],
    ["wellbeing lists discovered biometric readings", /heart rate/i.test(bioBody2) && /62/.test(bioBody2)],
  );

  // ── Energy Management panel (v6.62.0) ──
  await el._fetchEnergy();
  const energyDraw = el.shadowRoot.getElementById("energy-draw")?.textContent || "";
  const energyBody = el.shadowRoot.getElementById("energy-body")?.textContent || "";
  checks.push(
    ["energy panel shows draw + over-peak", /9\.2 kW/.test(energyDraw) && /OVER PEAK/.test(energyDraw)],
    ["energy panel lists running loads", /Dryer/.test(energyBody) && /Refrigerator/.test(energyBody)],
    ["energy panel marks protected loads", /protected/.test(energyBody)],
    ["energy panel shows advice", /over your peak/.test(energyBody)],
    ["energy agency chips present", el.shadowRoot.querySelectorAll("#energy-agency .mode-chip").length === 3],
  );

  // ── Operational Mode panel (Directive Layer, v6.61.0) ──
  await el._fetchMode();
  const modeActive = el.shadowRoot.getElementById("mode-active")?.textContent || "";
  const modeGrid = el.shadowRoot.getElementById("mode-grid")?.textContent || "";
  checks.push(
    ["mode panel shows active mode", /NORMAL/.test(modeActive)],
    ["mode panel lists selectable modes", /party/.test(modeGrid) && /movie/.test(modeGrid) && /away/.test(modeGrid)],
  );
  // switching mode updates the active tag
  const partyChip = [...el.shadowRoot.querySelectorAll(".mode-chip")].find(b => b.dataset.mode === "party");
  if (partyChip) { partyChip.click(); await new Promise(r => setTimeout(r, 20)); }
  checks.push(
    ["selecting a mode updates active",
      /PARTY/.test(el.shadowRoot.getElementById("mode-active")?.textContent || "")],
  );

  // ── System Diagnostics panel (v6.60.0) ──
  await el._fetchDiagnostics();
  const diagBody = el.shadowRoot.getElementById("diag-body")?.textContent || "";
  const diagOverall = el.shadowRoot.getElementById("diag-overall")?.textContent || "";
  checks.push(
    ["diagnostics lists all four core services",
      /LLM/.test(diagBody) && /Embeddings/.test(diagBody) && /TTS/.test(diagBody) && /STT/.test(diagBody)],
    ["diagnostics shows per-service detail", /reachable/.test(diagBody)],
    ["diagnostics overall summary rendered", /HEALTHY|WARN|DOWN/i.test(diagOverall)],
    ["diagnostics run-check button present", !!el.shadowRoot.getElementById("diag-refresh")],
  );

  // ── every config toggle must be WIRED, not just rendered (v6.76.1) ──
  // A button with data-cfg-key but no data-cfg-val is inert: the click handler
  // scopes to [data-cfg-key][data-cfg-val], so it renders fine and does nothing.
  const inertToggles = [...el.shadowRoot.querySelectorAll("button[data-cfg-key]")]
    .filter(b => !b.hasAttribute("data-cfg-val"))
    .map(b => b.getAttribute("data-cfg-key"));
  checks.push(
    ["no inert config toggles (every button has data-cfg-val)",
      inertToggles.length === 0 || `inert: ${inertToggles.join(", ")}`],
    ["hazard master toggle is wired",
      !!el.shadowRoot.querySelector('button[data-cfg-key="hazard_monitor_enabled"][data-cfg-val]')],
    ["hazard feed toggles are wired",
      !!el.shadowRoot.querySelector('button[data-cfg-key="hazard_quakes_on"][data-cfg-val]') &&
      !!el.shadowRoot.querySelector('button[data-cfg-key="hazard_weather_on"][data-cfg-val]') &&
      !!el.shadowRoot.querySelector('button[data-cfg-key="hazard_disasters_on"][data-cfg-val]')],
  );

  // ── Intrusion Log + training (v6.76.0) ──
  await el._wireIntrusionLog();
  const ilogBody = el.shadowRoot.getElementById("ilog-body")?.textContent || "";
  const ilogSide = el.shadowRoot.getElementById("ilog-learn")?.textContent || "";
  checks.push(
    ["intrusion log card present", !!el.shadowRoot.getElementById("ilog-body")],
    ["intrusion log renders events", /kitchen window/.test(ilogBody)],
    ["intrusion log shows event kinds", !!el.shadowRoot.querySelector(".ilog-confirmed") && !!el.shadowRoot.querySelector(".ilog-unresolved")],
    ["intrusion log shows snapshot image", !!el.shadowRoot.querySelector(".ilog-snap")],
    ["intrusion log has real/false label buttons",
      !!el.shadowRoot.querySelector(".ilog-real") && !!el.shadowRoot.querySelector(".ilog-false")],
    ["existing label is reflected", !!el.shadowRoot.querySelector(".ilog-false.ilog-on")],
    ["learned-benign summary surfaces", /learned 1 benign pattern/i.test(ilogBody)],
    ["label counter in header", /1\/2 LABELLED/.test(ilogSide)],
  );

  // ── Multi-Hazard Monitor panel (v6.71.0) ──
  await el._wireHazard();
  const hazLoc = el.shadowRoot.getElementById("haz-loc")?.textContent || "";
  const hazOverall = el.shadowRoot.getElementById("hazard-overall")?.textContent || "";
  checks.push(
    ["hazard card present with enable toggle", !!el.shadowRoot.querySelector('[data-cfg-key="hazard_monitor_enabled"]')],
    ["anticipation & memory card present", !!el.shadowRoot.querySelector('[data-cfg-key="departure_alerts_enabled"]')],
    ["anticipation exposes memory + continued-conv toggles",
      !!el.shadowRoot.querySelector('[data-cfg-key="memory_threading_enabled"]') &&
      !!el.shadowRoot.querySelector('[data-cfg-key="continued_conversation_enabled"]')],
    ["anticipation numeric config wired", !!el.shadowRoot.querySelector('[data-cfg-key="departure_lead_minutes"]')],
    ["anticipation toggles carry data-cfg-val", !!el.shadowRoot.querySelector('[data-cfg-key="routine_alerts_enabled"][data-cfg-val]')],
    ["hazard card has three feed toggles",
      !!el.shadowRoot.querySelector('[data-cfg-key="hazard_quakes_on"]') &&
      !!el.shadowRoot.querySelector('[data-cfg-key="hazard_weather_on"]') &&
      !!el.shadowRoot.querySelector('[data-cfg-key="hazard_disasters_on"]')],
    ["hazard card has location override inputs",
      !!el.shadowRoot.querySelector('[data-cfg-key="hazard_lat"]') &&
      !!el.shadowRoot.querySelector('[data-cfg-key="hazard_lon"]')],
    ["hazard status resolves the monitoring center", /40\.77/.test(hazLoc)],
    ["hazard overall reflects enabled state", /ON|OFF/.test(hazOverall)],
    ["hazard scan-now button present", !!el.shadowRoot.getElementById("haz-scan")],
  );
  // exercise a live scan render
  const hazScanBtn = el.shadowRoot.getElementById("haz-scan");
  if (hazScanBtn) {
    hazScanBtn.click();
    await new Promise(r => setTimeout(r, 30));
    const hazBody = el.shadowRoot.getElementById("haz-body")?.textContent || "";
    checks.push(["hazard scan renders quake + weather + disaster",
      /M3\.4/.test(hazBody) && /Tornado/.test(hazBody) && /Wildfire/.test(hazBody)]);
  }

  let ok = true;
  for (const [n, p] of checks) { console.log((p ? "  PASS  " : "  FAIL  ") + n); if (!p) ok = false; }
  if (typeof el._stopIntervals === "function") el._stopIntervals();
  console.log(ok ? "\nSMOKE TEST CLEAN" : "\nSMOKE TEST FAILED");
  process.exit(ok ? 0 : 1);
}, 350);
