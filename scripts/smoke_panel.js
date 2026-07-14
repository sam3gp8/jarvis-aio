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
  config: { cameras: [{ entity_id: "camera.front", name: "Front Door" }, { entity_id: "camera.back", name: "Backyard" }], lockdown: { active: false } },
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
const hass = {
  states: { "assist_satellite.a": { state: "idle", attributes: {} }, "camera.front": { attributes: { access_token: "tok123" } } },
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
    ["garage renders 3 doors", r.querySelectorAll("#res-iso svg polygon.gdoor").length === 3],
    ["occupied stat wired (n / total)", /\d+\s*\/\s*\d+/.test((r.getElementById("res-occ") || {}).textContent || "")],
    ["home-style selector with options", !!r.querySelector("#res-style-sel") && r.querySelectorAll("#res-style-sel option").length >= 6],
    ["property data-merge banner present", !!r.querySelector(".res-banner") && /MYRTLE/.test(r.querySelector("#res-addr")?.textContent || "")],
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

  let ok = true;
  for (const [n, p] of checks) { console.log((p ? "  PASS  " : "  FAIL  ") + n); if (!p) ok = false; }
  if (typeof el._stopIntervals === "function") el._stopIntervals();
  console.log(ok ? "\nSMOKE TEST CLEAN" : "\nSMOKE TEST FAILED");
  process.exit(ok ? 0 : 1);
}, 350);
