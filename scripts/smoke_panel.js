/**
 * Behavioral smoke test for the Command Center panel (jarvis-command).
 *
 * node --check only validates syntax; it can't catch an orphaned stylesheet
 * (defined but never injected) or a data-contract mismatch (reading d.cameras
 * when the backend nests it under d.config.cameras). Both of those shipped and
 * broke the live panel. This renders the component under jsdom with a realistic
 * jarvis/get_panel_data payload and asserts it actually draws.
 *
 * Run:
 *   npm install jsdom --no-save
 *   NODE_PATH=node_modules node scripts/smoke_panel.js
 * Exit 0 = clean, 1 = a regression in the panel's render/data wiring.
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const COMPONENT = path.resolve(
  __dirname, "..", "jarvis_assistant", "jarvis_component", "frontend", "jarvis-command.js"
);

const dom = new JSDOM("<!DOCTYPE html><body></body>", { url: "http://localhost/", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
["HTMLElement", "customElements", "Node", "Event", "CustomEvent"].forEach((k) => { global[k] = window[k]; });

window.eval(fs.readFileSync(COMPONENT, "utf8"));

// Mirror the REAL get_panel_data contract: presence under `dominant`,
// cameras + lockdown nested under `config`.
const PANEL = {
  status: { observer: { state: "RUNNING", level: "live" }, gemini: { state: "READY", level: "live" } },
  dominant: { area_id: "backyard", name: "Backyard", temp: "21.5" },
  areas: [
    { id: "backyard", name: "Backyard", active: true },
    { id: "front_yard", name: "Front Yard", active: true },
    { id: "kitchen", name: "Kitchen", active: false },
  ],
  config: {
    cameras: [{ entity_id: "camera.front", name: "Front Door" }, { entity_id: "camera.back", name: "Backyard" }],
    lockdown: { active: false },
  },
};
const hass = {
  states: {
    "assist_satellite.a": { state: "idle", attributes: {} },
    "assist_satellite.b": { state: "idle", attributes: {} },
    "camera.front": { attributes: { access_token: "tok123" } },
  },
  callWS: async (m) =>
    m.type === "jarvis/get_panel_data" ? PANEL : { entries: [{ ts: "08:59", urgency: "low", tag: "OBS", msg: "event" }] },
  connection: { subscribeEvents: async () => () => {} },
  callService: async () => {},
};

const el = window.document.createElement("jarvis-command");
window.document.body.appendChild(el);
el.hass = hass;

setTimeout(() => {
  const sr = el.shadowRoot, html = sr.innerHTML;
  const checks = [
    ["stylesheet injected into shadow DOM", html.includes("<style>") && html.includes("--cyan:#00d2ff")],
    ["HUD grid container present", !!sr.querySelector(".hud")],
    ["tactical modules rendered (5)", sr.querySelectorAll(".mod").length === 5],
    ["system status rows live", /OBSERVER/.test(html) && /RUNNING/.test(html)],
    ["satellites counted from states (2/2)", /2\/2/.test(html)],
    ["camera chips from config.cameras (2)", sr.querySelectorAll(".camchip").length === 2],
    ["active-area nodes on plan (2)", sr.querySelectorAll(".fp .node").length === 2],
    ["dominant area + temp label", /BACKYARD/.test(html) && /21\.5/.test(html)],
    ['camera auto-selected (no "NO CAMERA")', !/NO CAMERA SELECTED/.test(html)],
    ["live MJPEG src wired with token",
      !!(sr.querySelector("img") && /camera_proxy_stream\/camera\.front\?token=tok123/.test(sr.querySelector("img").src))],
  ];
  let ok = true;
  for (const [n, p] of checks) { console.log((p ? "  PASS  " : "  FAIL  ") + n); if (!p) ok = false; }
  if (typeof el._teardown === "function") el._teardown();
  console.log(ok ? "\nSMOKE TEST CLEAN" : "\nSMOKE TEST FAILED");
  process.exit(ok ? 0 : 1);
}, 250);
