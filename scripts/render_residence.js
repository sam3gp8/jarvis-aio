/**
 * Render the Residence cabinet-projection house to a standalone SVG for PNG preview.
 * Usage: NODE_PATH=node_modules node scripts/render_residence.js <out.svg>
 * Mirrors smoke_panel.js bootstrapping; lights several rooms so every window/door
 * state is visible for geometry inspection.
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const OUT = process.argv[2] || "/tmp/residence.svg";
const COMPONENT = path.resolve(__dirname, "..", "custom_components", "jarvis", "frontend", "jarvis-panel.js");
const dom = new JSDOM("<!DOCTYPE html><body></body>", { url: "http://localhost/", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
["HTMLElement", "customElements", "Node", "Event", "CustomEvent", "requestAnimationFrame", "cancelAnimationFrame"].forEach(k => { if (window[k]) global[k] = window[k]; });
window.eval(fs.readFileSync(COMPONENT, "utf8"));

// Light a representative spread so every window + all garage doors render lit:
const active = new Set(["garage", "living room", "kitchen", "dining room", "guest room", "master bedroom", "eliana's room", "basement"]);
const areas = [
  ["garage", "Garage"], ["living room", "Living Room"], ["kitchen", "Kitchen"], ["dining room", "Dining Room"],
  ["guest room", "Guest Room"], ["master bedroom", "Master Bedroom"], ["eliana's room", "Eliana's Room"],
  ["bath", "Bath"], ["basement", "Basement"], ["backyard", "Backyard"],
].map(([id, name]) => ({ id, name, caps: [], active: active.has(id), bedroom: false, lights_on: 0, lights_total: 0 }));

const PANEL = {
  status: { observer: { state: "RUNNING", level: "live" }, sleep: { state: "ASLEEP", level: "warn" }, gemini: { state: "READY", level: "live" }, broadcast: { state: "ONLINE", level: "live" }, notify: { state: "READY", level: "live" }, satellites: { state: "8 / 8", level: "live" } },
  meta: { bedrooms: 3, areas_monitored: areas.length, announcements_today: 0, est_cost: "—", uptime: "6m" },
  dominant: { area_id: "living room", name: "Living Room", subtitle: "Occupied", coord: "#01", temp: "70°", humidity: "50%", lights: "ON", satellite: "—", last_motion: "00:10" },
  areas,
  config: { cameras: [], lockdown: { active: false } },
};
const hass = {
  states: {},
  callWS: async (m) => { if (m.type === "jarvis/get_panel_data") return PANEL; if (m.type === "jarvis/get_activity_log") return { entries: [] }; if (m.type === "jarvis/get_cognitive_status") return { learning: { days_of_data: 48, state_changes: 1, commands: 0, suggestions: 0 }, ignore_rules: 0 }; return {}; },
  connection: { subscribeEvents: async () => () => {} },
  callService: async () => {},
};

const el = window.document.createElement("jarvis-panel");
window.document.body.appendChild(el);
el.hass = hass;

setTimeout(() => {
  el._currentTab = "residence";
  el._render();
  const iso = el.shadowRoot.querySelector("#res-iso svg");
  if (!iso) { console.error("ERROR: #res-iso svg not found"); process.exit(1); }
  const vb = iso.getAttribute("viewBox").split(/\s+/).map(Number);
  const [mnX, mnY, W, H] = vb;
  let s = iso.outerHTML
    .replace(/width="100%"/, `width="${W.toFixed(0)}"`)
    .replace(/height="100%"/, `height="${H.toFixed(0)}"`);
  // dark HUD backdrop behind the house, sized to the viewBox
  const bg = `<rect x="${mnX}" y="${mnY}" width="${W}" height="${H}" fill="#070b0f"/>`;
  s = s.replace(/(<svg[^>]*>)/, `$1${bg}`);
  s = '<?xml version="1.0" encoding="UTF-8"?>\n' + s;
  fs.writeFileSync(OUT, s);
  const polys = (s.match(/<polygon/g) || []).length, lines = (s.match(/<polyline/g) || []).length;
  console.log(`wrote ${OUT}  viewBox=${W.toFixed(0)}x${H.toFixed(0)}  polygons=${polys} polylines=${lines}`);
  process.exit(0);
}, 400);
