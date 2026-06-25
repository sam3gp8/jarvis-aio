/**
 * JARVIS — Command Center panel (jarvis-command).
 *
 * The operational HUD: live system status, a 2D top-down occupancy plan driven by
 * real area presence, live + selectable camera feeds that auto-focus when JARVIS
 * detects an event of importance, a live activity log, and quick actions.
 *
 * Data: jarvis/get_panel_data + jarvis/get_activity_log (polled). Cameras stream
 * via HA's camera proxy (MJPEG) using each entity's rotating access_token. Auto-
 * focus subscribes to the jarvis_camera_event / jarvis_face_recognized bus events.
 *
 * Self-contained vanilla web component (no build step, no external deps), matching
 * the existing jarvis-panel.js approach.
 */
const JC_STYLES = `
  :host{display:block;height:100%;overflow:auto;color:#cdd9e5;font-family:'JetBrains Mono','Consolas',monospace;
    background-color:#070b14;
    background-image:
      radial-gradient(ellipse at 50% -10%, rgba(0,210,255,0.06) 0%, transparent 55%),
      radial-gradient(ellipse at 100% 120%, rgba(10,30,60,0.5) 0%, transparent 60%);
    --bg:#070b14;--panel:rgba(9,17,31,0.55);--line:rgba(0,210,255,0.18);--line-hot:rgba(0,210,255,0.42);
    --cyan:#00d2ff;--cyan-soft:rgba(0,210,255,0.55);--green:#3fe6a0;--amber:#ffb454;--red:#ff4d52;
    --text:#cdd9e5;--text-dim:#5d7a8c;--text-faint:#33505e;}
  *{box-sizing:border-box;margin:0;padding:0}
  .app{position:relative;min-height:100%;padding:20px 22px;font-size:14px}
  .app::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:60;opacity:.4;
    background:repeating-linear-gradient(0deg,transparent 0 2px,rgba(0,0,0,.14) 3px)}
  ::-webkit-scrollbar{width:6px;height:6px}
  ::-webkit-scrollbar-thumb{background:rgba(0,210,255,.3)}
  ::-webkit-scrollbar-track{background:rgba(0,210,255,.05)}
  .rule{color:rgba(0,210,255,.32);font-size:13px;line-height:1;overflow:hidden;white-space:nowrap;user-select:none;height:13px}
  .head{display:flex;justify-content:space-between;align-items:baseline;padding:10px 2px 12px}
  .title{font-weight:700;font-size:21px;letter-spacing:.06em;color:#eaf2f7}
  .statusline{font-size:14px;letter-spacing:.12em;color:var(--text-dim)}
  .statusline b{color:var(--green);font-weight:600}
  .statusline b.warn{color:var(--amber)}.statusline b.alert{color:var(--red)}
  .hud{display:grid;grid-template-columns:288px minmax(340px,1fr) 384px;
    grid-template-areas:"sys res cam" "act act log";gap:13px;margin:12px 0}
  @media(max-width:1080px){.hud{grid-template-columns:1fr;grid-template-areas:"sys" "res" "cam" "act" "log"}}
  .sys{grid-area:sys}.res{grid-area:res}.cam{grid-area:cam}.act{grid-area:act}.log{grid-area:log}
  .mod{position:relative;background:var(--panel);border:1px solid var(--line);padding:16px 16px 14px;
    backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);display:flex;flex-direction:column}
  .cnr{position:absolute;width:9px;height:9px;border:1.5px solid var(--cyan-soft);pointer-events:none}
  .cnr.tl{top:-1px;left:-1px;border-right:0;border-bottom:0}.cnr.tr{top:-1px;right:-1px;border-left:0;border-bottom:0}
  .cnr.bl{bottom:-1px;left:-1px;border-right:0;border-top:0}.cnr.br{bottom:-1px;right:-1px;border-left:0;border-top:0}
  .mhead{font-size:13px;letter-spacing:.1em;color:var(--cyan);padding-bottom:9px;margin-bottom:13px;
    border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:baseline}
  .mhead .id{color:var(--text-dim)}
  .srow{display:flex;justify-content:space-between;align-items:center;padding:13px 4px;
    border-bottom:1px solid rgba(0,210,255,.08);font-size:14px;letter-spacing:.06em}
  .srow:last-child{border-bottom:0}
  .srow .v{letter-spacing:.04em}
  .v.live{color:var(--green)}.v.warn{color:var(--amber)}.v.off{color:var(--text-faint)}
  .subbox{margin-top:14px;border:1px solid var(--line);padding:14px;display:flex;justify-content:space-between;
    align-items:center;font-size:14px;letter-spacing:.06em;position:relative}
  .res{min-height:368px}
  .plan{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;padding:4px 0}
  .arealbl{text-align:center;line-height:1.35;min-height:30px}
  .arealbl .nm{font-size:14px;letter-spacing:.08em;color:#e7eff4}
  .arealbl .sub{font-size:11px;letter-spacing:.05em;color:var(--text-dim)}
  .fp{width:100%;max-width:330px;height:auto}
  .fp .wall{stroke:rgba(0,210,255,.5);stroke-width:1.3;fill:none}
  .fp .wall.dim{stroke:rgba(0,210,255,.22)}
  .fp .rmlbl{fill:rgba(120,160,180,.4);font:6px 'JetBrains Mono',monospace}
  .fp .link{stroke:rgba(0,210,255,.3);stroke-width:.8;stroke-dasharray:2 3;animation:flow 2.4s linear infinite}
  @keyframes flow{to{stroke-dashoffset:-10}}
  .fp .node{fill:rgba(0,210,255,.5)}
  .fp .node.occ{fill:var(--green);filter:drop-shadow(0 0 5px var(--green));animation:np 2.6s ease-in-out infinite}
  .fp .node.evt{fill:var(--red);filter:drop-shadow(0 0 6px var(--red));animation:np 1s ease-in-out infinite}
  .fp .hub{fill:#eaffff;filter:drop-shadow(0 0 6px var(--cyan))}
  .fp .nlbl{fill:rgba(150,190,210,.7);font:5.5px 'JetBrains Mono',monospace;text-anchor:middle}
  @keyframes np{0%,100%{filter:drop-shadow(0 0 5px var(--green))}50%{filter:drop-shadow(0 0 11px var(--green))}}
  .areafoot{text-align:center;font-size:12px;letter-spacing:.14em;color:var(--text-dim);
    padding-top:10px;margin-top:6px;border-top:1px solid var(--line)}
  .areafoot b{color:var(--cyan)}
  .camhead{display:flex;justify-content:space-between;align-items:baseline;font-size:14px;margin-bottom:4px}
  .camhead .lock{color:var(--amber);letter-spacing:.06em}
  .camhead .nm{color:#e7eff4;letter-spacing:.06em}
  .camsub{font-size:11px;color:var(--text-dim);margin-bottom:9px;display:flex;align-items:center;gap:5px}
  .camsel{display:flex;gap:6px;margin-bottom:9px;flex-wrap:wrap}
  .camchip{font-size:10px;letter-spacing:.05em;color:var(--text-dim);border:1px solid var(--line);
    padding:4px 8px;cursor:pointer;transition:all .2s;white-space:nowrap}
  .camchip:hover{border-color:var(--line-hot);color:var(--cyan)}
  .camchip.on{color:var(--cyan);border-color:var(--cyan-soft);background:rgba(0,210,255,.08)}
  .camchip.evt{color:var(--red);border-color:rgba(255,77,82,.5);background:rgba(255,77,82,.08)}
  .feed{position:relative;flex:1;min-height:200px;overflow:hidden;border:1px solid var(--line);background:#05090f}
  .feed img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
  .feed .noimg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    color:var(--text-faint);font-size:11px;letter-spacing:.1em;text-align:center;padding:16px}
  .feed .vig{position:absolute;inset:0;z-index:3;pointer-events:none;
    background:radial-gradient(ellipse 74% 78% at 50% 46%, transparent 52%, rgba(0,0,0,.82) 100%)}
  .feed .scan{position:absolute;inset:0;z-index:4;pointer-events:none;opacity:.4;
    background:linear-gradient(rgba(0,0,0,0) 50%,rgba(0,0,0,.22) 50%),
              linear-gradient(90deg,rgba(255,0,0,.05),rgba(0,255,0,.02),rgba(0,0,255,.05));
    background-size:100% 3px,7px 100%}
  .feed .tag{position:absolute;top:8px;left:9px;z-index:6;font-size:9px;letter-spacing:.08em;color:var(--cyan-soft)}
  .feed .focus{position:absolute;top:8px;right:9px;z-index:6;font-size:9px;letter-spacing:.08em;
    color:var(--red);display:flex;align-items:center;gap:5px;background:rgba(8,12,20,.7);padding:3px 7px;
    border:1px solid rgba(255,77,82,.4)}
  .feed .focus i{width:6px;height:6px;border-radius:50%;background:var(--red);animation:blink 1.1s steps(1) infinite}
  .camstrip{display:flex;justify-content:space-between;font-size:12px;letter-spacing:.06em;color:var(--cyan);
    padding:9px 2px 0;margin-top:9px;border-top:1px solid var(--line)}
  .camstrip b{color:var(--text)}
  .act{min-height:96px}
  .btns{display:flex;gap:14px;flex:1;align-items:center;flex-wrap:wrap}
  .btn{flex:1;min-width:150px;text-align:center;border:1px solid var(--line);background:rgba(0,210,255,.04);
    padding:15px 10px;font-size:14px;letter-spacing:.08em;color:var(--cyan);cursor:pointer;transition:all .22s;
    font-family:inherit}
  .btn:hover,.btn:focus-visible{background:rgba(0,210,255,.11);border-color:var(--line-hot);outline:none;
    box-shadow:0 0 14px rgba(0,210,255,.18)}
  .btn.danger{color:#ff9a6b;border-color:rgba(255,77,82,.32);background:rgba(255,77,82,.05)}
  .btn.danger.on{color:var(--red);border-color:var(--red);background:rgba(255,77,82,.15)}
  .btn.danger:hover{background:rgba(255,77,82,.13);box-shadow:0 0 14px rgba(255,77,82,.2)}
  .log{min-height:96px}
  .logbody{flex:1;overflow:auto;font-size:13px;line-height:1.85;letter-spacing:.02em;max-height:120px}
  .lrow{color:var(--text-dim);white-space:nowrap}
  .lrow .t{color:var(--text-faint)}
  .lrow.medium{color:var(--cyan)}.lrow.high{color:var(--red)}
  .cur{display:inline-block;width:7px;height:13px;background:var(--cyan);vertical-align:middle;animation:blink 1.1s steps(1) infinite}
  .foot{display:flex;justify-content:space-between;align-items:baseline;font-size:12px;letter-spacing:.08em;color:var(--text-dim);padding:10px 2px 2px}
  .foot b{color:var(--cyan-soft)}.foot .sec{color:var(--green)}
  .toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);z-index:80;background:rgba(8,14,24,.95);
    border:1px solid var(--line-hot);color:var(--cyan);font-size:12px;letter-spacing:.06em;padding:10px 16px;
    opacity:0;transition:opacity .3s;pointer-events:none}
  .toast.show{opacity:1}
  @keyframes blink{50%{opacity:0}}
  @media (prefers-reduced-motion: reduce){*{animation:none !important}}
`;

// fixed node slots over the floor-plan backdrop; live areas are assigned in order
const JC_SLOTS = [
  { x: 78,  y: 64  }, { x: 186, y: 44 }, { x: 272, y: 96 },
  { x: 66,  y: 168 }, { x: 172, y: 170 }, { x: 250, y: 182 },
];
const JC_HUB = { x: 168, y: 120 };

class JarvisCommand extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._inited = false;
    this._data = null;
    this._cams = [];
    this._activeCam = null;   // entity currently shown
    this._manualCam = null;   // last user-picked entity (to revert to after focus)
    this._focus = null;       // {entity,label,conf,until}
    this._evtArea = null;      // area_id flagged by the latest event
    this._subs = [];
    this._timers = [];
    this._lastTok = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._inited) this._init();
  }
  get hass() { return this._hass; }

  connectedCallback() { if (this._hass && !this._inited) this._init(); }
  disconnectedCallback() { this._teardown(); }

  _init() {
    this._inited = true;
    this.shadowRoot.innerHTML = `<style>${JC_STYLES}</style><div class="app">
      <div class="rule" id="rtop"></div>
      <div class="head"><div class="title">[J.A.R.V.I.S. // CORE_HUD]</div>
        <div class="statusline">STATUS: <b id="sysstate">…</b></div></div>
      <div class="hud">
        <section class="mod sys"><b class="cnr tl"></b><b class="cnr tr"></b><b class="cnr bl"></b><b class="cnr br"></b>
          <div class="mhead"><span><span class="id">[01]</span> SYSTEM_STATUS</span></div>
          <div id="statusrows"></div>
          <div class="subbox"><b class="cnr tl"></b><b class="cnr br"></b>
            <span>LLM_LINK</span><span class="v" id="llmlink">…</span></div>
        </section>
        <section class="mod res"><b class="cnr tl"></b><b class="cnr tr"></b><b class="cnr bl"></b><b class="cnr br"></b>
          <div class="mhead"><span><span class="id">[02]</span> RESIDENCE_OVERVIEW</span></div>
          <div class="plan">
            <div class="arealbl" id="lbltop"></div>
            <div id="plansvg"></div>
            <div class="arealbl" id="lblbot"></div>
          </div>
          <div class="areafoot" id="areafoot"></div>
        </section>
        <section class="mod cam"><b class="cnr tl"></b><b class="cnr tr"></b><b class="cnr bl"></b><b class="cnr br"></b>
          <div class="mhead"><span><span class="id">[03]</span> CAMERA_WATCH</span></div>
          <div class="camhead"><span class="lock" id="camlock">[LIVE]</span><span class="nm" id="camname">—</span></div>
          <div class="camsub">◉ WebRTC / proxy stream</div>
          <div class="camsel" id="camsel"></div>
          <div class="feed" id="feed"><div class="noimg">NO CAMERA SELECTED</div>
            <div class="tag" id="camtag"></div><div class="vig"></div><div class="scan"></div></div>
          <div class="camstrip" id="camstrip"></div>
        </section>
        <section class="mod act"><b class="cnr tl"></b><b class="cnr tr"></b><b class="cnr bl"></b><b class="cnr br"></b>
          <div class="mhead"><span><span class="id">[04]</span> QUICK_ACTIONS</span></div>
          <div class="btns">
            <div class="btn" id="btn-brief" tabindex="0">[ BRIEFING ]</div>
            <div class="btn danger" id="btn-lock" tabindex="0">[ LOCKDOWN ]</div>
            <div class="btn" id="btn-diag" tabindex="0">[ RUN_DIAG ]</div>
          </div>
        </section>
        <section class="mod log"><b class="cnr tl"></b><b class="cnr tr"></b><b class="cnr bl"></b><b class="cnr br"></b>
          <div class="mhead"><span><span class="id">[06]</span> REALTIME_LOGSTREAM</span></div>
          <div class="logbody" id="logbody"></div>
          <div style="font-size:13px;color:var(--cyan);margin-top:6px">&gt; await_event <span class="cur"></span></div>
        </section>
      </div>
      <div class="rule" id="rbot"></div>
      <div class="foot"><span><b>NODE:</b> HOMEASSISTANT.LOCAL // JARVIS</span>
        <span>SYS_MARK: <span class="sec">SECURE</span></span></div>
      <div class="toast" id="toast"></div>
    </div>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    $("btn-brief").addEventListener("click", () => this._action("briefing"));
    $("btn-lock").addEventListener("click", () => this._action("lockdown"));
    $("btn-diag").addEventListener("click", () => this._action("diag"));
    this._drawRules();
    window.addEventListener("resize", () => this._drawRules());

    this._loadData();
    this._loadLogs();
    this._timers.push(setInterval(() => this._loadData(), 5000));
    this._timers.push(setInterval(() => this._loadLogs(), 6000));
    this._timers.push(setInterval(() => this._tick(), 1000));
    this._subscribe();
  }

  _drawRules() {
    const w = this.shadowRoot.querySelector(".app")?.clientWidth || 1000;
    const n = Math.ceil(w / 7.7);
    ["rtop", "rbot"].forEach((id) => {
      const el = this.shadowRoot.getElementById(id);
      if (el) el.textContent = "=".repeat(n);
    });
  }

  async _ws(type, extra) {
    if (!this._hass) return null;
    try { return await this._hass.callWS({ type, ...(extra || {}) }); }
    catch (e) { return null; }
  }

  async _loadData() {
    const d = await this._ws("jarvis/get_panel_data");
    if (!d) return;
    this._data = d;
    const cams = (d.config && d.config.cameras) || d.cameras;
    this._cams = Array.isArray(cams) ? cams : [];
    if (!this._activeCam && this._cams.length) {
      this._activeCam = this._cams[0].entity_id;
      this._manualCam = this._activeCam;
    }
    this._renderStatus(d);
    this._renderPlan(d);
    this._renderCamSelector();
    this._renderCamera();
  }

  async _loadLogs() {
    const r = await this._ws("jarvis/get_activity_log");
    const entries = (r && r.entries) || [];
    const body = this.shadowRoot.getElementById("logbody");
    if (!body) return;
    if (!entries.length) { body.innerHTML = `<div class="lrow"><span class="t">--:--:--</span> — awaiting activity</div>`; return; }
    body.innerHTML = entries.slice(0, 14).map((e) => {
      const cls = e.urgency === "high" ? "high" : (e.urgency === "medium" ? "medium" : "");
      return `<div class="lrow ${cls}"><span class="t">${this._esc(e.ts || "")}</span> — ${this._esc((e.tag ? e.tag + " " : "") + (e.msg || ""))}</div>`;
    }).join("");
  }

  // ── System status ─────────────────────────────────────────────
  _renderStatus(d) {
    const s = d.status || {};
    const obs = s.observer || {};
    const sysOk = (obs.level === "live");
    const head = this.shadowRoot.getElementById("sysstate");
    if (head) { head.textContent = sysOk ? "OPERATIONAL" : "STANDBY"; head.className = sysOk ? "" : "warn"; }

    // satellites: live count of assist_satellite entities
    let satUp = 0, satTot = 0;
    const st = this._hass && this._hass.states;
    if (st) for (const eid in st) {
      if (eid.indexOf("assist_satellite.") === 0) { satTot++; if (st[eid].state !== "unavailable") satUp++; }
    }
    const cogLive = sysOk;
    const rows = [
      ["OBSERVER", obs.state || "—", obs.level || "off"],
      ["COGNITION", cogLive ? "ACT" : "IDLE", cogLive ? "live" : "off"],
      ["SATELLITES", satTot ? `${satUp}/${satTot}` : "—", satTot && satUp === satTot ? "live" : (satTot ? "warn" : "off")],
    ];
    const rowsEl = this.shadowRoot.getElementById("statusrows");
    if (rowsEl) rowsEl.innerHTML = rows.map(([k, v, lvl]) =>
      `<div class="srow">${k} <span class="v ${lvl}">[ ${this._esc(v)} ]</span></div>`).join("");

    const gem = s.gemini || {};
    const llm = this.shadowRoot.getElementById("llmlink");
    if (llm) {
      const on = gem.level === "live";
      llm.textContent = `[${on ? "ONLINE" : (gem.state || "UNSET")}]`;
      llm.className = "v " + (on ? "live" : "warn");
    }

    // lockdown button reflects live state if present
    const lk = (d.config && d.config.lockdown) || d.lockdown;
    const lock = this.shadowRoot.getElementById("btn-lock");
    if (lock && lk) lock.classList.toggle("on", !!lk.active);
  }

  // ── Residence: 2D plan from live areas ────────────────────────
  _renderPlan(d) {
    const areas = Array.isArray(d.areas) ? d.areas : [];
    const active = areas.filter((a) => a.active);
    const pres = d.dominant || d.presence || {};
    const domId = pres.area_id || null;

    // assign active areas to fixed node slots (dominant first)
    const ordered = active.slice().sort((a, b) =>
      (a.id === domId ? -1 : 0) - (b.id === domId ? -1 : 0));
    const assigned = ordered.slice(0, JC_SLOTS.length);

    const links = assigned.map((a, i) =>
      `<line class="link" x1="${JC_HUB.x}" y1="${JC_HUB.y}" x2="${JC_SLOTS[i].x}" y2="${JC_SLOTS[i].y}"/>`).join("");
    const nodes = assigned.map((a, i) => {
      const p = JC_SLOTS[i];
      const evt = (this._evtArea && a.id === this._evtArea);
      const cls = evt ? "node evt" : "node occ";
      const r = (a.id === domId) ? 4.4 : 3.4;
      const lbl = this._esc((a.name || a.id || "").toUpperCase().slice(0, 14));
      return `<circle class="${cls}" cx="${p.x}" cy="${p.y}" r="${r}"/>` +
             `<text class="nlbl" x="${p.x}" y="${p.y + 13}">${lbl}</text>`;
    }).join("");

    const svg = `<svg class="fp" viewBox="0 0 340 232" xmlns="http://www.w3.org/2000/svg">
      <rect class="wall" x="22" y="20" width="296" height="192"/>
      <line class="wall" x1="140" y1="20" x2="140" y2="112"/>
      <line class="wall dim" x1="140" y1="68" x2="232" y2="68"/>
      <line class="wall" x1="232" y1="20" x2="232" y2="212"/>
      <line class="wall" x1="22" y1="112" x2="232" y2="112"/>
      <line class="wall" x1="118" y1="112" x2="118" y2="212"/>
      <line x1="118" y1="60" x2="118" y2="74" stroke="#070b14" stroke-width="3"/>
      <line x1="160" y1="112" x2="176" y2="112" stroke="#070b14" stroke-width="3"/>
      <g class="links">${links}</g>
      ${nodes}
      <circle class="hub" cx="${JC_HUB.x}" cy="${JC_HUB.y}" r="4.6"/>
    </svg>`;
    const holder = this.shadowRoot.getElementById("plansvg");
    if (holder) holder.innerHTML = svg;

    // top/bottom labels: dominant + next occupied
    const top = assigned[0];
    const bot = assigned[1];
    const tEl = this.shadowRoot.getElementById("lbltop");
    const bEl = this.shadowRoot.getElementById("lblbot");
    if (tEl) tEl.innerHTML = top
      ? `<div class="nm">${this._esc((top.name || "").toUpperCase())}</div><div class="sub">(${this._esc(pres.temp || "—")}°C · OCCUPIED)</div>`
      : `<div class="nm">NO PRESENCE</div><div class="sub">all areas clear</div>`;
    if (bEl) bEl.innerHTML = bot
      ? `<div class="nm">${this._esc((bot.name || "").toUpperCase())}</div><div class="sub">OCCUPIED</div>` : "";

    const foot = this.shadowRoot.getElementById("areafoot");
    if (foot) foot.innerHTML = `AREAS: <b>${areas.length}_TOTAL</b> · ACTIVE: <b>${active.length}</b>`;
  }

  // ── Cameras: live, selectable, auto-focus ─────────────────────
  _renderCamSelector() {
    const sel = this.shadowRoot.getElementById("camsel");
    if (!sel) return;
    sel.innerHTML = this._cams.map((c) => {
      const on = c.entity_id === this._activeCam;
      const evt = this._focus && this._focus.entity === c.entity_id;
      const short = (c.name || c.entity_id).replace(/^camera\./, "").toUpperCase().slice(0, 16);
      return `<span class="camchip ${on ? "on" : ""} ${evt ? "evt" : ""}" data-cam="${this._esc(c.entity_id)}">${this._esc(short)}</span>`;
    }).join("");
    sel.querySelectorAll(".camchip").forEach((chip) =>
      chip.addEventListener("click", () => this._selectCam(chip.getAttribute("data-cam"))));
  }

  _camToken(entity) {
    const st = this._hass && this._hass.states && this._hass.states[entity];
    return st && st.attributes ? st.attributes.access_token : null;
  }

  _renderCamera() {
    const feed = this.shadowRoot.getElementById("feed");
    if (!feed) return;
    const entity = this._activeCam;
    const nameEl = this.shadowRoot.getElementById("camname");
    const lockEl = this.shadowRoot.getElementById("camlock");
    const tagEl = this.shadowRoot.getElementById("camtag");
    const stripEl = this.shadowRoot.getElementById("camstrip");

    if (!entity) {
      feed.querySelector("img")?.remove();
      let n = feed.querySelector(".noimg");
      if (!n) { n = document.createElement("div"); n.className = "noimg"; n.textContent = "NO CAMERA"; feed.prepend(n); }
      return;
    }
    const cam = this._cams.find((c) => c.entity_id === entity);
    if (nameEl) nameEl.textContent = (cam ? cam.name : entity).replace(/^camera\./, "").toUpperCase();
    if (tagEl) tagEl.textContent = "◱ " + entity;

    // focus banner
    let foc = feed.querySelector(".focus");
    if (this._focus && this._focus.entity === entity) {
      if (!foc) { foc = document.createElement("div"); foc.className = "focus"; feed.appendChild(foc); }
      const cf = this._focus.conf != null ? ` ${this._focus.conf}%` : "";
      foc.innerHTML = `<i></i>EVENT FOCUS · ${this._esc((this._focus.label || "").toUpperCase())}${cf}`;
      if (lockEl) { lockEl.textContent = "[EVENT]"; lockEl.style.color = "var(--red)"; }
    } else {
      foc?.remove();
      if (lockEl) { lockEl.textContent = "[LIVE]"; lockEl.style.color = "var(--green)"; }
    }

    // live MJPEG via camera proxy stream; only (re)attach when entity or token changes
    const tok = this._camToken(entity);
    const key = entity + "|" + (tok || "");
    if (key !== this._lastTok) {
      this._lastTok = key;
      feed.querySelector(".noimg")?.remove();
      let img = feed.querySelector("img");
      if (!img) { img = document.createElement("img"); feed.prepend(img); img.addEventListener("error", () => this._camFallback(entity)); }
      img.src = tok
        ? `/api/camera_proxy_stream/${entity}?token=${encodeURIComponent(tok)}`
        : `/api/camera_proxy_stream/${entity}`;
    }
    if (stripEl) {
      const conf = this._focus && this._focus.entity === entity && this._focus.conf != null
        ? `PERSON [${this._focus.conf}%]` : "STREAMING";
      stripEl.innerHTML = `<span>SRC <b>${this._esc(entity.split(".").pop())}</b></span><span>MJPEG</span><span>TARGET <b>${this._esc(conf)}</b></span>`;
    }
    this._renderCamSelector();
  }

  _camFallback(entity) {
    // some cameras (e.g. Nest WebRTC) won't serve MJPEG — fall back to a refreshed still
    if (entity !== this._activeCam) return;
    const feed = this.shadowRoot.getElementById("feed");
    const img = feed && feed.querySelector("img");
    if (!img) return;
    const tok = this._camToken(entity);
    const still = () => { img.src = `/api/camera_proxy/${entity}?token=${encodeURIComponent(tok || "")}&_=${Date.now()}`; };
    still();
    if (!this._stillTimer) this._stillTimer = setInterval(() => { if (this._activeCam === entity) still(); }, 2000);
  }

  _selectCam(entity) {
    if (!entity) return;
    this._activeCam = entity;
    this._manualCam = entity;
    this._focus = null;
    this._evtArea = null;
    this._renderCamera();
  }

  // ── Event-driven auto-focus ───────────────────────────────────
  async _subscribe() {
    const conn = this._hass && this._hass.connection;
    if (!conn) return;
    try { this._subs.push(await conn.subscribeEvents((e) => this._onCamEvent(e.data || {}), "jarvis_camera_event")); } catch (e) {}
    try {
      this._subs.push(await conn.subscribeEvents((e) => {
        const d = e.data || {};
        if (d.is_confident && d.camera_entity) this._onCamEvent({ entity_id: d.camera_entity, label: d.name || "FACE", confidence: Math.round(d.confidence || 0) });
      }, "jarvis_face_recognized"));
    } catch (e) {}
  }

  _onCamEvent(data) {
    const entity = data.entity_id;
    if (!entity) return;
    // make sure the camera is known even if it wasn't in the picker yet
    if (!this._cams.find((c) => c.entity_id === entity)) this._cams.push({ entity_id: entity, name: entity });
    this._manualCam = this._manualCam || this._activeCam;
    this._activeCam = entity;
    this._focus = { entity, label: data.label || "EVENT", conf: (data.confidence != null ? data.confidence : null), until: Date.now() + 25000 };
    // flag the camera's area on the plan if resolvable
    const st = this._hass && this._hass.states && this._hass.states[entity];
    this._evtArea = (st && st.attributes && st.attributes.area_id) || null;
    this._renderCamera();
    this._renderPlan(this._data || {});
    this._toast(`EVENT · ${entity.split(".").pop()} — focusing feed`);
  }

  _tick() {
    this._drawRules();
    if (this._focus && Date.now() > this._focus.until) {
      this._focus = null;
      this._evtArea = null;
      if (this._manualCam) this._activeCam = this._manualCam;
      this._renderCamera();
      if (this._data) this._renderPlan(this._data);
    }
  }

  // ── Quick actions ─────────────────────────────────────────────
  async _action(which) {
    if (!this._hass) return;
    try {
      if (which === "briefing") { await this._hass.callService("jarvis", "briefing", {}); this._toast("BRIEFING dispatched"); }
      else if (which === "lockdown") {
        const lk = this._data && ((this._data.config && this._data.config.lockdown) || this._data.lockdown);
        const cur = !!(lk && lk.active);
        await this._ws("jarvis/set_lockdown", { on: !cur });
        this._toast(!cur ? "LOCKDOWN engaged" : "LOCKDOWN released");
        this._loadData();
      }
      else if (which === "diag") { await this._hass.callService("jarvis", "diagnose_doorbell", {}); this._toast("DIAGNOSTIC running — see logs"); }
    } catch (e) { this._toast("ACTION FAILED: " + (e && e.message ? e.message : which)); }
  }

  _toast(msg) {
    const t = this.shadowRoot.getElementById("toast");
    if (!t) return;
    t.textContent = msg; t.classList.add("show");
    clearTimeout(this._toastT);
    this._toastT = setTimeout(() => t.classList.remove("show"), 3200);
  }

  _esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  _teardown() {
    this._timers.forEach((t) => clearInterval(t));
    this._timers = [];
    if (this._stillTimer) { clearInterval(this._stillTimer); this._stillTimer = null; }
    this._subs.forEach((u) => { try { u && u(); } catch (e) {} });
    this._subs = [];
    this._inited = false;
  }
}

if (!customElements.get("jarvis-command")) customElements.define("jarvis-command", JarvisCommand);
