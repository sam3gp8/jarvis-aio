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
    { id: "garage", name: "Garage", caps: ["cam", "light"], active: true, bedroom: false, lights_on: 1, lights_total: 1 },
    { id: "backyard", name: "Backyard", caps: ["cam", "mmwave"], active: true, bedroom: false, lights_on: 0, lights_total: 0 },
    { id: "kitchen", name: "Kitchen", caps: ["sat", "spkr"], active: false, bedroom: false, lights_on: 0, lights_total: 0 },
  ],
  config: { cameras: [{ entity_id: "camera.front", name: "Front Door" }, { entity_id: "camera.back", name: "Backyard" }], lockdown: { active: false } },
};
const hass = {
  states: { "assist_satellite.a": { state: "idle", attributes: {} }, "camera.front": { attributes: { access_token: "tok123" } } },
  callWS: async (m) => {
    if (m.type === "jarvis/get_panel_data") return PANEL;
    if (m.type === "jarvis/get_activity_log") return { entries: [{ ts: "08:59", urgency: "low", tag: "OBS", msg: "event" }] };
    if (m.type === "jarvis/get_cognitive_status") return { learning: { days_of_data: 48, state_changes: 217802, commands: 93, suggestions: 0 }, ignore_rules: 0 };
    return {};
  },
  connection: { subscribeEvents: async () => () => {} },
  callService: async () => {},
};

const el = window.document.createElement("jarvis-panel");
window.document.body.appendChild(el);
el.hass = hass;

setTimeout(() => {
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
  ];

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

  let ok = true;
  for (const [n, p] of checks) { console.log((p ? "  PASS  " : "  FAIL  ") + n); if (!p) ok = false; }
  if (typeof el._stopIntervals === "function") el._stopIntervals();
  console.log(ok ? "\nSMOKE TEST CLEAN" : "\nSMOKE TEST FAILED");
  process.exit(ok ? 0 : 1);
}, 350);
