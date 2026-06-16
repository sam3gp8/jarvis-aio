/**
 * JARVIS Command Center Panel
 * v6.7.0 (session 2 · audio routing fix, areas with icons+codes)
 *
 * Registered as a custom element via panel_custom. Home Assistant sets:
 *   - this.hass   — the hass object (live state, services, connection)
 *   - this.panel  — panel config from registration
 *   - this.narrow — true when viewport is narrow (mobile)
 *   - this.route  — route object
 *
 * This session: visual port of the HTML mockup. Live clock, mock data
 * elsewhere. Real HA data wiring comes in session 2.
 */

class JarvisPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._narrow = false;
    this._roomRotationIdx = 0;
    this._clockInterval = null;
    this._rotationInterval = null;
    this._fetchInterval = null;
    this._renderedOnce = false;
    this._liveData = null;       // populated by _fetchLiveData()
    this._liveDataErr = null;    // last fetch error (for status display)
    this._activityData = null;   // populated by _fetchActivityLog()
    this._currentTab = "dashboard"; // "dashboard" or "settings"
    this._logFilter = "all";       // log category filter
    this._currentFloor = "all";     // floor plan tab — 3D default shows all
    this._editorFloor = "1f";      // floor plan editor tab
    this._dragState = null;        // floor plan drag state
    this._editingPlan = null;      // working copy for editor
    this._rot3dY = 45;             // 3D house rotation Y
    this._rot3dX = -28;            // 3D house rotation X
    this._zoom3d = 1;              // 3D house zoom level
    this._pendingRender = false;   // owed full render deferred during editing
    this._lastLogSig = null;       // signature of currently-rendered log
    this._lastLogFilter = null;    // filter the log was last rendered under
  }

  // ─── HA property setters ─────────────────────────────────────────────────

  set hass(hass) {
    const first = this._hass === null;
    this._hass = hass;
    if (first) {
      this._render();
      this._startIntervals();
    } else {
      this._updateLiveValues();
    }
  }
  get hass() { return this._hass; }

  set panel(panel) { this._config = panel?.config || {}; }
  set narrow(narrow) {
    if (narrow !== this._narrow) {
      this._narrow = narrow;
      if (this._renderedOnce) this._render();
    }
  }
  set route(route) { this._route = route; }

  connectedCallback() {
    // v6 aesthetic: load the panel's typefaces at document level (shadow DOM
    // can't reliably pull remote @font-face). Graceful: if offline, the
    // font-family fallbacks render and nothing breaks.
    if (!document.getElementById("jarvis-fonts-v6")) {
      const l = document.createElement("link");
      l.id = "jarvis-fonts-v6";
      l.rel = "stylesheet";
      l.href = "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;700&family=JetBrains+Mono:wght@300;400;500&display=swap";
      document.head.appendChild(l);
    }
    if (this._hass) {
      this._render();
      this._startIntervals();
    }
  }

  disconnectedCallback() {
    this._stopIntervals();
  }

  // ─── Lifecycle ───────────────────────────────────────────────────────────

  _startIntervals() {
    if (!this._clockInterval) {
      this._clockInterval = setInterval(() => this._updateClock(), 1000);
    }
    // Demo rotation kept as fallback — stops once live data arrives
    if (!this._rotationInterval) {
      this._rotationInterval = setInterval(() => this._rotateDominantRoom(), 6000);
    }
    // Live data polling
    if (!this._fetchInterval) {
      this._fetchLiveData();  // immediate first call
      this._fetchInterval = setInterval(() => this._fetchLiveData(), 5000);
    }
  }

  _stopIntervals() {
    if (this._clockInterval)    { clearInterval(this._clockInterval);    this._clockInterval = null; }
    if (this._rotationInterval) { clearInterval(this._rotationInterval); this._rotationInterval = null; }
    if (this._fetchInterval)    { clearInterval(this._fetchInterval);    this._fetchInterval = null; }
  }

  async _fetchLiveData() {
    if (!this._hass) return;
    try {
      const result = await this._hass.callWS({ type: "jarvis/get_panel_data" });
      const prev = this._liveData;
      this._liveData = result;
      this._liveDataErr = null;
      if (this._rotationInterval) {
        clearInterval(this._rotationInterval);
        this._rotationInterval = null;
      }
      // Also fetch activity log from DB
      try {
        const logResult = await this._hass.callWS({
          type: "jarvis/get_activity_log", hours: 2, limit: 30
        });
        this._activityData = logResult?.entries || [];
      } catch (_) { /* no entries yet — fine */ }

      // Auto-refresh logs tab if active
      if (this._currentTab === 'logs') {
        this._fetchDebugLog();
      }

      // Fetch cognitive core stats for dashboard
      try {
        const cogEl = this.shadowRoot?.querySelector("#cognitive-stats .loading-cog");
        if (cogEl) {
          // We embed the status as a WebSocket call — the agent's
          // cognitive_status tool is for conversation, this is direct
          const cogData = await this._hass.callWS({ type: "jarvis/get_cognitive_status" });
          if (cogData) {
            const days = cogData.learning?.days_of_data || 0;
            const changes = cogData.learning?.state_changes || 0;
            const cmds = cogData.learning?.commands || 0;
            const pending = cogData.learning?.suggestions || 0;
            const ignores = cogData.ignore_rules || 0;
            cogEl.innerHTML =
              `Data: <span>${days}d</span> · ` +
              `States: <span>${changes}</span> · ` +
              `Cmds: <span>${cmds}</span><br>` +
              `Suggestions: <span>${pending}</span> · ` +
              `Ignores: <span>${ignores}</span>`;
          }
        }
      } catch (_) {}

      // A full DOM rebuild is only warranted when the STRUCTURE changes —
      // i.e. the number of area tiles differs (an HA area was added/removed).
      // The dominant area changing is handled entirely in place by
      // _patchLiveDom (it updates #dom-name, the stats, the 3D house, etc.),
      // so it must NOT trigger a teardown. Previously a dominant-area change
      // rebuilt the whole shadow DOM — and since the dominant area flips with
      // every motion event, that rebuilt the panel every few seconds, wiping
      // in-progress settings edits and yanking the log scroll. Never rebuild
      // while the user is on the settings or logs tab; defer until they return.
      const structuralChange = !prev ||
        prev.areas?.length !== result.areas?.length;
      const interacting = this._currentTab === "settings" || this._currentTab === "logs";
      if (structuralChange && !interacting) {
        this._render();
      } else {
        if (structuralChange) this._pendingRender = true; // owed once they leave
        this._patchLiveDom(result);
      }
    } catch (err) {
      this._liveDataErr = err?.message || String(err);
      console.warn("JARVIS: live data fetch failed", err);
    }
  }

  async _fetchAndRender() {
    // Used by toggle/dropdown handlers: fetch fresh data then force
    // a full re-render so button states update immediately. Unlike
    // _fetchLiveData which skips re-render on the settings tab (to
    // keep dropdowns stable during auto-refresh), this always renders.
    await this._fetchLiveData();
    this._render();
  }

  async _fetchDebugLog() {
    if (!this._hass) return;
    try {
      const result = await this._hass.callWS({ type: "jarvis/get_debug_log" });
      const entries = result?.entries || [];
      const container = this.shadowRoot?.getElementById("debug-log-entries");
      if (!container) return;
      if (!entries.length) {
        container.innerHTML = '<div class="log-loading">No entries yet. Talk to JARVIS to generate log entries.</div>';
        return;
      }
      const cc = {
        CONV:     { color: '#0ff',    icon: '💬', label: 'Conversation' },
        LOCAL:    { color: '#00f5a0', icon: '⚡', label: 'Local Engine' },
        AGENT:    { color: '#ff9d2e', icon: '🤖', label: 'Agent LLM' },
        ROUTE:    { color: '#f80',    icon: '🔀', label: 'Audio Route' },
        CLASSIFY: { color: '#88f',    icon: '🏷️', label: 'Classifier' },
        REASON:   { color: '#b47aff', icon: '🧠', label: 'Reasoning' },
        TTS:      { color: '#0ff',    icon: '🔊', label: 'TTS' },
        ERROR:    { color: '#ff3b3b', icon: '❌', label: 'Error' },
        GATE:     { color: '#567685', icon: '🚧', label: 'Presence Gate' },
        DEDUP:    { color: '#567685', icon: '🔇', label: 'Dedup' },
        CAMERA:   { color: '#00f5a0', icon: '📷', label: 'Camera' },
      };
      // Get active filter
      const activeFilter = this._logFilter || 'all';
      const filtered = activeFilter === 'all'
        ? entries
        : entries.filter(e => e.cat === activeFilter);

      // Newest-first for display (deque is oldest→newest; reverse it).
      const ordered = filtered.slice().reverse();

      // Skip the DOM rebuild entirely when nothing changed — the common case
      // on the 5s poll. Rebuilding unconditionally is what made the log flicker
      // and jump every few seconds. Signature = count + first/last identity.
      const first = ordered[0];
      const last = ordered[ordered.length - 1];
      const sig = ordered.length + "|" +
        (first ? first.ts + first.msg : "") + "|" +
        (last ? last.ts + last.msg : "");
      if (sig === this._lastLogSig && activeFilter === this._lastLogFilter) {
        return; // unchanged — leave the DOM and the user's scroll position alone
      }
      const filterChanged = activeFilter !== this._lastLogFilter;

      // Preserve scroll: capture where the user is BEFORE touching the DOM.
      // Newest entries are at the TOP, so "near top" means they're reading the
      // latest; keep them pinned there. Otherwise leave them where they were.
      const nearTop = container.scrollTop < 40;
      const prevTop = container.scrollTop;

      container.innerHTML = ordered.map(e => {
        const cat = cc[e.cat] || { color: 'var(--text)', icon: '•', label: e.cat };
        const isError = e.cat === 'ERROR' || e.msg.toLowerCase().includes('error') || e.msg.toLowerCase().includes('failed');
        const bgClass = isError ? 'log-entry-error' : '';
        return `<div class="log-entry ${bgClass}" data-cat="${e.cat}">
          <span class="log-ts">${e.ts}</span>
          <span class="log-cat" style="color:${cat.color}">${cat.icon} ${e.cat}</span>
          <span class="log-msg">${e.msg}</span>
        </div>`;
      }).join('');

      this._lastLogSig = sig;
      this._lastLogFilter = activeFilter;

      // Restore scroll. On a deliberate filter change, or when the user was
      // already viewing the latest, show the newest (top). Otherwise keep
      // their position so reading older history isn't interrupted.
      if (filterChanged || nearTop) {
        container.scrollTop = 0;
      } else {
        container.scrollTop = prevTop;
      }
    } catch (err) {
      const c = this.shadowRoot?.getElementById("debug-log-entries");
      if (c) c.innerHTML = '<div class="log-entry-error" style="padding:12px;">Error loading logs: ' + err + '</div>';
    }
  }

  // ─── Data (mock for session 1; live HA hookup session 2) ────────────────

  _greeting() {
    const h = new Date().getHours();
    if (h < 5)  return "Good evening";
    if (h < 12) return "Good morning";
    if (h < 18) return "Good afternoon";
    return "Good evening";
  }

  _mockData() {
    return {
      observer:   { state: "RUNNING", level: "live" },
      sleep:      { state: "AWAKE",   level: "live" },
      gemini:     { state: "READY",   level: "live" },
      broadcast:  { state: "ONLINE",  level: "live" },
      notify:     { state: "UNSET",   level: "warn" },
      satellites: { state: "8 / 8",   level: "live" },
      bedrooms: 3,
      areas: 11,
      announcements_today: 14,
      est_cost: "$0.03",
      uptime: "2d 14h",
      dominantRoom: {
        name: "Kitchen",
        subtitle: "Occupied · 14m",
        coord: "#02 · Second Floor",
        temp: "72°",
        humidity: "44%",
        lights: "ON",
        satellite: "ES-E3E534",
        lastMotion: "00:08",
      },
      areasGrid: [
        { name: "Kitchen",         meta: "sat · spkr · mmwave", active: true,  bedroom: false },
        { name: "Office",          meta: "sat · spkr · mmwave", active: true,  bedroom: false },
        { name: "Great Room",      meta: "sat · spkr · mmwave", active: false, bedroom: false },
        { name: "Dining Room",     meta: "sat · spkr",           active: false, bedroom: false },
        { name: "Entry",           meta: "sat · spkr · mmwave", active: false, bedroom: false },
        { name: "Master Bedroom",  meta: "sat · spkr · mmwave", active: false, bedroom: true },
        { name: "Guest Room",      meta: "sat · spkr · mmwave", active: false, bedroom: true },
        { name: "Eliana's Room",   meta: "sat · spkr · mmwave", active: false, bedroom: true },
        { name: "Garage",          meta: "sat · mmwave · cam",  active: true,  bedroom: false },
        { name: "Basement",        meta: "moisture · smoke",    active: false, bedroom: false },
        { name: "Outdoor",         meta: "3 cameras",           active: false, bedroom: false },
      ],
      activity: [
        { ts:"14:31", urgency:"medium",   tag:"KITCHEN",        msg:"motion detected, routing reply here" },
        { ts:"14:18", urgency:"low",      tag:"WASHER",         msg:"cycle complete — announcement suppressed, existing automation handles this" },
        { ts:"14:03", urgency:"medium",   tag:"FRONT DOOR",     msg:"opened — Sam home" },
        { ts:"13:47", urgency:"high",     tag:"DOORBELL",       msg:"rang — no one recognized, broadcast sent" },
        { ts:"13:41", urgency:"muted",    tag:"shush",          msg:"laundry room muted until reset" },
        { ts:"13:22", urgency:"medium",   tag:"BRIEFING",       msg:"requested, delivered to home group" },
        { ts:"12:58", urgency:"low",      tag:"GARAGE",         msg:"door closed" },
        { ts:"12:14", urgency:"medium",   tag:"OFFICE",         msg:"Sam entered, switching observer context" },
        { ts:"11:30", urgency:"low",      tag:"OBSERVER",       msg:"quiet interval — 23 events classified, 0 spoken" },
        { ts:"09:12", urgency:"critical", tag:"LEAK",           msg:"moisture detected in basement — broadcast override fired" },
        { ts:"08:45", urgency:"medium",   tag:"GOOD MORNING",   msg:"briefing delivered on schedule" },
        { ts:"08:03", urgency:"low",      tag:"MASTER BEDROOM", msg:"motion, sleep mode cleared" },
      ],
      roomRotation: [
        { name: "Kitchen", subtitle: "Occupied · 14m", coord: "#02 · Second Floor", temp: "72°", humidity: "44%", lights: "ON"  },
        { name: "Office",  subtitle: "Occupied · 3m",  coord: "#05 · Second Floor", temp: "70°", humidity: "41%", lights: "ON"  },
        { name: "Garage",  subtitle: "Occupied · 1m",  coord: "#09 · Ground Floor", temp: "66°", humidity: "52%", lights: "OFF" },
      ],
    };
  }

  /**
   * Return the data used for rendering. Prefers live WS data; falls back to
   * mock if WS hasn't responded yet. Shapes returned to match _mockData().
   */
  _data() {
    if (!this._liveData) return this._mockData();
    const live = this._liveData;
    return {
      observer:   live.status.observer,
      sleep:      live.status.sleep,
      gemini:     live.status.gemini,
      broadcast:  live.status.broadcast,
      notify:     live.status.notify,
      satellites: live.status.satellites,
      bedrooms:            live.meta.bedrooms,
      areas:               live.meta.areas_monitored,
      announcements_today: live.meta.announcements_today,
      est_cost:            live.meta.est_cost,
      uptime:              live.meta.uptime,
      dominantRoom: {
        name:       live.dominant.name,
        subtitle:   live.dominant.subtitle,
        coord:      live.dominant.coord,
        temp:       live.dominant.temp,
        humidity:   live.dominant.humidity,
        lights:     live.dominant.lights,
        satellite:  live.dominant.satellite,
        lastMotion: live.dominant.last_motion,
      },
      areasGrid: live.areas.map(a => ({
        id: a.id,
        name: a.name,
        caps: a.caps || [],
        active: a.active,
        bedroom: a.bedroom,
        lights_on: a.lights_on || 0,
        lights_total: a.lights_total || 0,
      })),
      activity: this._activityData && this._activityData.length > 0
        ? this._activityData
        : [{ ts: "--:--", urgency: "low", tag: "SYSTEM", msg: "No activity yet. Enable announcements or observer to see events here." }],
      config: live.config || {},
    };
  }

  /**
   * Patch only the fields that change frequently, without tearing down the
   * whole DOM. Called on every 5s WS refresh when shape is unchanged.
   */
  _patchLiveDom(live) {
    const root = this.shadowRoot;
    if (!root) return;
    // On settings tab, DON'T re-render automatically — it destroys
    // open dropdown menus and resets user interaction state. Settings
    // data only updates when user clicks a toggle/dropdown (which
    // calls _fetchLiveData → _render explicitly after the WS call).
    if (this._currentTab === "settings") {
      return;
    }
    if (this._currentTab === "logs") {
      this._fetchDebugLog();
      return;
    }
    // Status rows — scoped to dashboard's .c-status panel only
    const statusPanel = root.querySelector(".c-status");
    if (statusPanel) {
      const rows = statusPanel.querySelectorAll(".status-row");
      const statusKeys = ["observer", "sleep", "gemini", "broadcast", "notify", "satellites"];
      rows.forEach((row, i) => {
        const key = statusKeys[i];
        const st = live.status[key];
        if (!st) return;
        row.className = `status-row ${st.level}`;
        const dot = row.querySelector(".dot");
        if (dot) dot.className = `dot ${st.level}`;
        const v = row.querySelector(".v");
        if (v) v.textContent = st.state;
      });
    }
    // Meta block — scoped to .meta inside .c-status
    const metaEl = statusPanel ? statusPanel.querySelector(".meta") : null;
    if (metaEl) {
      const metaSpans = metaEl.querySelectorAll("span");
      if (metaSpans.length >= 4) {
        metaSpans[0].textContent = live.meta.bedrooms;
        metaSpans[1].textContent = live.meta.areas_monitored;
        metaSpans[2].textContent = live.meta.announcements_today;
        metaSpans[3].textContent = live.meta.uptime;
      }
    }
    // Dominant room
    const nameEl  = root.querySelector("#dom-name");
    const subEl   = root.querySelector("#dom-sub");
    const coordEl = root.querySelector("#dom-coord");
    const footSpans = root.querySelectorAll(".anchor-foot span");
    if (nameEl)  nameEl.textContent  = live.dominant.name;
    if (subEl)   subEl.textContent   = live.dominant.subtitle;
    if (coordEl) coordEl.textContent = live.dominant.coord;
    this._updateDomGauges(live.dominant);
    if (footSpans.length >= 2) {
      footSpans[footSpans.length - 2].textContent = live.dominant.last_motion;
      footSpans[footSpans.length - 1].textContent = live.dominant.satellite;
    }
    // Area tiles — patch active state only (names don't change)
    const areaEls = root.querySelectorAll(".area");
    areaEls.forEach((el, i) => {
      const a = live.areas[i];
      if (!a) return;
      el.classList.toggle("active", !!a.active);
    });

    // Floor plan — rebuild 3D with updated presence data
    if (this._currentTab === 'dashboard') {
      this._build3DHouse();
    }
  }

  // ─── Rendering ───────────────────────────────────────────────────────────

  _render() {
    this.shadowRoot.innerHTML = this._styles() + this._html();
    this._wire();
    this._renderedOnce = true;
    this._pendingRender = false;
  }

  _updateLiveValues() {
    this._updateClock();
  }

  _updateClock() {
    const timeEl = this.shadowRoot.querySelector("#clock-time");
    const dateEl = this.shadowRoot.querySelector("#clock-date");
    const greetEl = this.shadowRoot.querySelector("#greeting-text");
    if (!timeEl) return;
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    const ss = String(now.getSeconds()).padStart(2, "0");
    timeEl.textContent = `${hh}:${mm}:${ss}`;

    const days = ["SUN","MON","TUE","WED","THU","FRI","SAT"];
    const mons = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
    dateEl.textContent = `${days[now.getDay()]} · ${String(now.getDate()).padStart(2,"0")} · ${mons[now.getMonth()]} · ${now.getFullYear()}`;

    if (greetEl) greetEl.textContent = this._greeting();
  }

  _rotateDominantRoom() {
    // Only runs as pre-live-data demo; stops once WS returns data
    if (this._liveData) return;
    const data = this._mockData();
    this._roomRotationIdx = (this._roomRotationIdx + 1) % data.roomRotation.length;
    const r = data.roomRotation[this._roomRotationIdx];
    const nameEl  = this.shadowRoot.querySelector("#dom-name");
    const subEl   = this.shadowRoot.querySelector("#dom-sub");
    const coordEl = this.shadowRoot.querySelector("#dom-coord");
    if (!nameEl) return;
    nameEl.style.opacity = 0;
    setTimeout(() => {
      nameEl.textContent = r.name;
      subEl.textContent  = r.subtitle;
      if (coordEl) coordEl.textContent = r.coord;
      this._updateDomGauges(r);
      nameEl.style.opacity = 1;
    }, 250);
  }

  // ─── HTML helpers (avoid nested template literals) ─────────────────────────

  // Radial (donut) gauge — used for the dominant-room environment readout.
  _radialGauge(id, frac, display, label, hue, dim) {
    const R = 26, C = 2 * Math.PI * R;
    const f = Math.max(0, Math.min(1, frac || 0));
    const off = C * (1 - f);
    return `<div class="rgauge${dim ? ' dim' : ''}">
      <svg viewBox="0 0 64 64" class="rgauge-svg" aria-hidden="true">
        <circle class="rgauge-track" cx="32" cy="32" r="${R}"></circle>
        <circle class="rgauge-fill" id="${id}-arc" cx="32" cy="32" r="${R}"
          style="stroke:hsl(${hue},90%,62%);stroke-dasharray:${C.toFixed(1)};stroke-dashoffset:${off.toFixed(1)};"></circle>
      </svg>
      <div class="rgauge-val" id="${id}-val">${display}</div>
      <div class="rgauge-lbl">${label}</div>
    </div>`;
  }

  _tempFrac(t) { return isFinite(t) ? (t - 50) / 40 : 0; }      // 50–90°F → 0–1
  _tempHue(t) {
    const f = Math.max(0, Math.min(1, this._tempFrac(t)));
    return Math.round(210 - f * 190);                          // cool blue → warm orange
  }

  _domGauges(dr) {
    const t = parseFloat(String(dr.temp));
    const h = parseFloat(String(dr.humidity));
    const lon = /on|^[1-9]/i.test(String(dr.lights || ""));
    return (
      this._radialGauge("g-temp", this._tempFrac(t), dr.temp ?? "—", "Temp", this._tempHue(t), false) +
      this._radialGauge("g-hum", (isFinite(h) ? h / 100 : 0), dr.humidity ?? "—", "Humidity", 190, false) +
      this._radialGauge("g-lite", lon ? 1 : 0.04, lon ? "ON" : "OFF", "Lights", lon ? 48 : 205, !lon)
    );
  }

  _setGauge(id, frac, display, hue, dim) {
    const arc = this.shadowRoot.querySelector(`#${id}-arc`);
    const val = this.shadowRoot.querySelector(`#${id}-val`);
    if (arc) {
      const R = 26, C = 2 * Math.PI * R;
      const f = Math.max(0, Math.min(1, frac || 0));
      arc.style.strokeDashoffset = (C * (1 - f)).toFixed(1);
      if (hue != null) arc.style.stroke = `hsl(${hue},90%,62%)`;
    }
    if (val) {
      val.textContent = display;
      const g = val.closest(".rgauge");
      if (g) g.classList.toggle("dim", !!dim);
    }
  }

  _updateDomGauges(dr) {
    const t = parseFloat(String(dr.temp));
    const h = parseFloat(String(dr.humidity));
    const lon = /on|^[1-9]/i.test(String(dr.lights || ""));
    this._setGauge("g-temp", this._tempFrac(t), dr.temp ?? "—", this._tempHue(t), false);
    this._setGauge("g-hum", (isFinite(h) ? h / 100 : 0), dr.humidity ?? "—", 190, false);
    this._setGauge("g-lite", lon ? 1 : 0.04, lon ? "ON" : "OFF", lon ? 48 : 205, !lon);
  }

  // ─── AI Models (Settings) ───────────────────────────────────────────────
  _modelRoles() {
    return [
      { role: 'llm',        label: 'Main Agent', provKey: 'llm_provider',        modelKey: 'model' },
      { role: 'classifier', label: 'Classifier', provKey: 'classifier_provider', modelKey: 'classifier_model' },
      { role: 'reasoning',  label: 'Reasoning',  provKey: 'reasoning_provider',  modelKey: 'reasoning_model' },
      { role: 'review',     label: 'Review',     provKey: 'review_provider',     modelKey: 'review_model' },
      { role: 'vision',     label: 'Vision',     provKey: 'vision_provider',     modelKey: 'vision_model' },
      { role: 'camrsn',     label: 'Camera Rsn', provKey: 'camera_reasoning_provider', modelKey: 'camera_reasoning_model' },
    ];
  }

  _renderModelRoles(d) {
    const PROVIDERS = ['groq', 'openai', 'gemini', 'ollama', 'anthropic', 'custom'];
    const cfg = d.config || {};
    return this._modelRoles().map(r => {
      const curProv = cfg[r.provKey] || 'groq';
      const curModel = cfg[r.modelKey] || '';
      const provOpts = PROVIDERS.map(p =>
        `<option value="${p}"${p === curProv ? ' selected' : ''}>${p}</option>`).join('');
      // Model select starts with the current value + a loading hint; it's
      // repopulated live from the provider via _loadModelsFor().
      const modelOpts =
        (curModel ? `<option value="${this._esc(curModel)}" selected>${this._esc(curModel)}</option>` : '') +
        `<option value="" disabled>loading…</option>` +
        `<option value="__custom__">✎ Custom…</option>`;
      return `
        <div class="model-row" data-role="${r.role}">
          <span class="model-label">${r.label}</span>
          <select class="notify-select prov-select" data-role="${r.role}" data-cfg-key="${r.provKey}">${provOpts}</select>
          <select class="notify-select model-select" data-role="${r.role}" data-cfg-key="${r.modelKey}" data-current="${this._esc(curModel)}">${modelOpts}</select>
          <input class="model-custom" data-role="${r.role}" data-cfg-key="${r.modelKey}"
                 type="text" placeholder="enter model id" value="${this._esc(curModel)}" />
        </div>`;
    }).join('');
  }

  _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async _saveConfig(key, value) {
    if (!this._hass || !key) return;
    try {
      await this._hass.callWS({ type: 'jarvis/update_config', key, value });
      this._toast(`✓ ${key} → ${value}`, 'ok');
    } catch (err) {
      this._toast(`✗ ${key} — ${err?.message || err}`, 'err');
    }
  }

  _applianceTypes() {
    return ['washer', 'dryer', 'dishwasher', 'oven', 'microwave', 'appliance'];
  }

  _renderApplianceEntityOptions(selected) {
    const states = this._hass?.states || {};
    const cands = [];
    Object.keys(states).forEach(eid => {
      const s = states[eid];
      const dom = eid.split('.')[0];
      const dc = (s.attributes && s.attributes.device_class) || '';
      const unit = ((s.attributes && s.attributes.unit_of_measurement) || '').toLowerCase();
      const isPower = dc === 'power' || dc === 'energy' || unit === 'w' || unit === 'kw';
      const isStatus = (dom === 'binary_sensor' || dom === 'sensor') &&
        /(washer|dryer|dishwash|laundry|appliance|run_complete|cycle_complete|job_state|machine_state)/i.test(eid);
      if (isPower || isStatus) cands.push(eid);
    });
    cands.sort();
    if (selected && !cands.includes(selected)) cands.unshift(selected);
    return cands.map(eid => {
      const fn = (states[eid] && states[eid].attributes && states[eid].attributes.friendly_name) || eid;
      return `<option value="${this._esc(eid)}"${eid === selected ? ' selected' : ''}>${this._esc(fn)}</option>`;
    }).join('');
  }

  _applianceRow(a, learnedW) {
    const types = this._applianceTypes();
    const t = a.type || 'appliance';
    const learnedTxt = (learnedW && a.watts && Math.abs(learnedW - a.watts) > 5)
      ? `learned ~${Math.round(learnedW)}W` : '';
    return `<div class="appliance-row">
      <div class="ar-line1">
        <input class="appliance-name" type="text" placeholder="Name (e.g. Washer)" value="${this._esc(a.name || '')}"/>
        <button class="appliance-remove" title="Remove appliance" aria-label="Remove appliance">✕</button>
      </div>
      <div class="ar-line2">
        <select class="appliance-type">
          ${types.map(x => `<option value="${x}"${x === t ? ' selected' : ''}>${x}</option>`).join('')}
        </select>
        <select class="appliance-entity">
          <option value="">— no entity (use watts) —</option>
          ${this._renderApplianceEntityOptions(a.entity || '')}
        </select>
        <input class="appliance-watts" type="number" min="0" step="10" placeholder="watts" value="${a.watts || ''}"/>
      </div>
      ${learnedTxt ? `<div class="appliance-learned">${learnedTxt}</div>` : ''}
    </div>`;
  }

  _renderAppliances(d) {
    const prof = (d.config && d.config.appliance_profile) || [];
    const learned = {};
    (((d.config && d.config.appliances) || {}).profile || []).forEach(p => { learned[p.name] = p.learned_w; });
    if (!prof.length) {
      return `<div class="appliance-empty">No appliances declared yet — JARVIS falls back to generic power guesses until you add some.</div>`;
    }
    return prof.map(a => this._applianceRow(a, learned[a.name])).join('');
  }

  async _loadModelsFor(provider, selectEl) {
    if (!this._hass || !selectEl) return;
    const cur = selectEl.getAttribute('data-current') || '';
    try {
      const res = await this._hass.callWS({ type: 'jarvis/list_models', provider });
      const models = (res && res.models) || [];
      let opts = '';
      if (models.length) {
        if (cur && !models.includes(cur)) {
          opts += `<option value="${this._esc(cur)}" selected>${this._esc(cur)} (current)</option>`;
        }
        opts += models.map(m =>
          `<option value="${this._esc(m)}"${m === cur ? ' selected' : ''}>${this._esc(m)}</option>`).join('');
      } else {
        const err = res && res.error ? ` — ${String(res.error).slice(0, 48)}` : '';
        opts += (cur ? `<option value="${this._esc(cur)}" selected>${this._esc(cur)}</option>` : '');
        opts += `<option value="" disabled>no models found${this._esc(err)}</option>`;
      }
      opts += `<option value="__custom__">✎ Custom…</option>`;
      selectEl.innerHTML = opts;
    } catch (_) {
      /* leave current options in place on error */
    }
  }

  _renderCameraOptions(d) {
    const cams = (d.config && d.config.cameras) || [];
    if (!cams.length) return '<option value="">— no cameras —</option>';
    return cams.map(c =>
      `<option value="${this._esc(c.entity_id)}">${this._esc(c.name)}</option>`).join('');
  }

  _renderSuggestions(d) {
    const sugs = d.suggestions || [];
    if (!sugs.length) return '';
    const rows = sugs.map(s => {
      const pct = Math.round((s.confidence || 0) * 100);
      return `<div class="sug" data-sug-id="${s.id}">
        <div class="sug-top">
          <span class="sug-desc">${this._esc(s.description)}</span>
        </div>
        <div class="sug-meta">
          <span class="sug-conf"><i style="width:${pct}%"></i></span>
          <span class="sug-pct">${pct}% · ×${s.count || '?'}</span>
          <button class="sug-btn sug-yaml-btn" title="View automation YAML">YAML</button>
          <button class="sug-btn sug-approve" title="Approve">✓</button>
          <button class="sug-btn sug-dismiss" title="Dismiss">✕</button>
        </div>
        <pre class="sug-yaml" hidden>${this._esc(s.yaml || '')}</pre>
      </div>`;
    }).join('');
    return `
      <div class="meta sug-wrap" style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px;">
        <span style="color:var(--green);font-family:var(--font-display);font-size:9px;letter-spacing:0.2em;">SUGGESTIONS · ${sugs.length} PENDING</span>
        <div class="sug-list">${rows}</div>
      </div>`;
  }

  _renderDoorbellTraining(d) {
    const t = d.doorbell_training || {};
    const stats = t.stats || {};
    const events = t.recent || [];
    const total = stats.total || 0;
    const notable = stats.notable || 0;
    const bySource = stats.by_source || {};
    const srcLine = Object.keys(bySource).length
      ? Object.entries(bySource).map(([k, v]) => `${k} ${v}`).join(' · ')
      : 'none yet';
    const rows = events.length
      ? events.slice().reverse().map(e => this._dbTrainRow(e)).join('')
      : `<div class="dbt-empty">No analysed doorbell events yet. Run a backlog scan, or wait for the next doorbell press.</div>`;
    return `
      <div class="dbt-controls">
        <button class="btn dbt-scan">Scan backlog</button>
        <input class="dbt-limit" type="number" min="1" max="500" value="40" title="Max events to analyse"/>
        <span class="dbt-stat">${total} analysed · ${notable} notable · ${srcLine}</span>
      </div>
      <div class="dbt-list">${rows}</div>
    `;
  }

  _dbTrainRow(e) {
    const ts = String(e.ts || '').replace('T', ' ').replace('Z', '').slice(5, 16);
    const src = String(e.image_source || '?');
    const srcCls = src.replace(/[^a-z]/gi, '').toLowerCase();
    const cat = e.category || '';
    const desc = this._esc(e.summary || e.analysis || '');
    return `<div class="dbt-row${e.notable ? ' dbt-notable' : ''}">
      <span class="dbt-ts">${this._esc(ts)}</span>
      <span class="dbt-src dbt-src-${srcCls}">${this._esc(src)}</span>
      ${cat ? `<span class="dbt-cat">${this._esc(cat)}</span>` : ''}
      <span class="dbt-desc">${desc}</span>
    </div>`;
  }

  _renderNotifyOptions(d) {
    const svcs = d.config?.notify_services_available || [];
    const current = d.config?.notify_service || '';
    let html = '<option value="">— none —</option>';
    for (const svc of svcs) {
      const sel = svc === current ? ' selected' : '';
      const label = svc.replace('notify.', '');
      html += '<option value="' + svc + '"' + sel + '>' + label + '</option>';
    }
    return html;
  }

  _renderSentinelRules(d) {
    const rules = d.config?.sentinel_rules || [];
    const disabled = d.config?.disabled_sentinel_rules || [];
    let html = '';
    for (const r of rules) {
      const isOff = disabled.includes(r.id);
      const cls = isOff ? 'off' : 'on';
      const label = isOff ? 'OFF' : 'ON';
      const name = r.id.replace(/_/g, ' ');
      const desc = (r.desc || '').slice(0, 60);
      html += '<div class="rule-row">'
        + '<span class="rule-name">' + name + '</span>'
        + '<span class="rule-desc">' + desc + '</span>'
        + '<button class="toggle-btn ' + cls + ' rule-toggle" data-rule-id="' + r.id + '">' + label + '</button>'
        + '</div>';
    }
    return html;
  }

  _renderSatellitePairings(d) {
    const satellites = d.config?.satellites || [];
    const castDevs = d.config?.cast_devices || [];
    const pairings = d.config?.satellite_pairings || {};
    if (!satellites.length) return '<div class="toggle-desc" style="padding:10px">No satellites found</div>';
    let html = '';
    for (const sat of satellites) {
      const paired = pairings[sat.entity_id] || '';
      const label = sat.area ? (sat.area) : sat.name;
      html += '<div class="pairing-row">'
        + '<span class="pairing-label">' + label + '</span>'
        + '<select class="notify-select sat-pair-select" data-sat-id="' + sat.entity_id + '">'
        + '<option value="">— none —</option>';
      for (const cd of castDevs) {
        const sel = cd.entity_id === paired ? ' selected' : '';
        html += '<option value="' + cd.entity_id + '"' + sel + '>' + cd.name + '</option>';
      }
      html += '</select></div>';
    }
    return html;
  }

  _renderAnnouncementSpeakers(d) {
    const castDevs = d.config?.cast_devices || [];
    const selected = d.config?.announcement_speakers || [];
    if (!castDevs.length) return '<div class="toggle-desc" style="padding:10px">No Cast devices found</div>';
    let html = '';
    for (const cd of castDevs) {
      const isOn = selected.includes(cd.entity_id);
      html += '<div class="rule-row">'
        + '<span class="rule-name">' + cd.name + '</span>'
        + '<span class="rule-desc">' + cd.entity_id + '</span>'
        + '<button class="toggle-btn ' + (isOn ? 'on' : 'off') + ' ann-speaker-toggle" data-speaker-id="' + cd.entity_id + '">'
        + (isOn ? 'ON' : 'OFF') + '</button>'
        + '</div>';
    }
    return html;
  }

  // ─── Floor plan: data-driven with config editor ────────────────────────

  _defaultFloorPlan() {
    return {
      "1f": {
        label: "1st Floor", viewBox: "0 0 320 150",
        rooms: [
          {name:"Garage",x:5,y:5,w:100,h:88,type:"room"},
          {name:"Kitchen",x:115,y:5,w:65,h:40,type:"room"},
          {name:"Bath",x:185,y:5,w:28,h:22,type:"bath"},
          {name:"Guest Room",x:218,y:5,w:95,h:40,type:"room"},
          {name:"Dining Room",x:115,y:50,w:65,h:38,type:"room"},
          {name:"Stairs",x:185,y:32,w:28,h:32,type:"stairs"},
          {name:"Living Room",x:218,y:50,w:95,h:38,type:"room"},
          {name:"Downstairs Hallway",x:115,y:93,w:198,h:20,type:"room"},
          {name:"Front Door",x:185,y:117,w:50,h:12,type:"door"},
        ],
      },
      "2f": {
        label: "2nd Floor", viewBox: "0 0 320 140",
        rooms: [
          {name:"Eliana's Room",x:50,y:25,w:95,h:80,type:"room"},
          {name:"Bath",x:150,y:25,w:30,h:40,type:"bath"},
          {name:"Master Bedroom",x:185,y:25,w:85,h:80,type:"room"},
          {name:"Upstairs Hallway",x:150,y:70,w:30,h:35,type:"room"},
          {name:"Stairs",x:150,y:108,w:25,h:20,type:"stairs"},
        ],
      },
      "bsmt": {
        label: "Basement", viewBox: "0 0 320 130",
        rooms: [
          {name:"Basement",x:50,y:10,w:220,h:90,type:"room"},
          {name:"Stairs",x:120,y:20,w:28,h:35,type:"stairs"},
        ],
        labels: [
          {text:"SUMP PUMP",x:95,y:55},{text:"DEHUMIDIFIER",x:95,y:75},
          {text:"HOME ENERGY",x:235,y:55},{text:"WASHER",x:235,y:75},
        ],
      },
    };
  }

  _getFloorPlan() {
    // Dashboard view: always reads from saved config (NOT _editingPlan)
    const d = this._data();
    try {
      const raw = d.config?.floor_plan_rooms;
      if (raw) {
        const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (parsed && typeof parsed === 'object' && Object.keys(parsed).length) {
          return parsed;
        }
      }
    } catch (_) {}
    return this._defaultFloorPlan();
  }

  _getEditingPlan() {
    // Editor view: maintains a working copy for drag operations
    if (this._editingPlan) return this._editingPlan;
    // Initialize from saved config or defaults
    this._editingPlan = JSON.parse(JSON.stringify(this._getFloorPlan()));
    return this._editingPlan;
  }

  _renderFloorPlan(d, floor) {
    // Legacy — no longer used for dashboard, kept for editor
    const plan = this._getFloorPlan();
    const floorData = plan[floor];
    if (!floorData) return '<div style="color:var(--text-dim);padding:20px;text-align:center;">No floor data</div>';

    const areaMap = {};
    (d.areasGrid || []).forEach(a => { areaMap[a.name.toLowerCase()] = a.active; });
    const isOcc = (name) => !!areaMap[name.toLowerCase()];

    let svg = '<svg viewBox="' + (floorData.viewBox || '0 0 320 140') + '" class="fp-svg"><defs><filter id="glow-fp"><feGaussianBlur stdDeviation="3" result="g"/><feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';

    for (const rm of (floorData.rooms || [])) {
      const {name, x, y, w, h, type} = rm;
      if (type === 'bath') { svg += '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="2" fill="rgba(10,13,18,0.6)" stroke="#0f2029" stroke-width="0.5"/><text x="'+(x+w/2)+'" y="'+(y+h/2+2)+'" text-anchor="middle" fill="#2a3b47" font-size="5" font-family="JetBrains Mono, monospace">BATH</text>'; continue; }
      if (type === 'stairs') { svg += '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="1" fill="rgba(10,20,30,0.4)" stroke="#0a7d94" stroke-width="0.5" stroke-dasharray="2,2"/>'; continue; }
      if (type === 'door') { const occ=isOcc(name); svg += '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="2" fill="'+(occ?'rgba(255,157,46,0.1)':'rgba(10,13,18,0.4)')+'" stroke="'+(occ?'#ff9d2e':'#0f2029')+'" stroke-width="0.5"/><text x="'+(x+w/2)+'" y="'+(y+h/2+2)+'" text-anchor="middle" fill="'+(occ?'#ff9d2e':'#2a3b47')+'" font-size="4" font-family="JetBrains Mono, monospace">'+name.toUpperCase()+'</text>'; continue; }
      const occ = isOcc(name); const fill = occ ? 'rgba(0,242,254,0.12)' : 'rgba(10,13,18,0.6)'; const stroke = occ ? '#00f2fe' : '#0f2029';
      svg += '<g class="fp-room"><rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="2" fill="'+fill+'" stroke="'+stroke+'" stroke-width="'+(occ?1.5:0.8)+'"/><text x="'+(x+w/2)+'" y="'+(y+h/2)+'" text-anchor="middle" fill="'+(occ?'#00f2fe':'#567685')+'" font-size="'+(w>80?7:5)+'" font-family="Orbitron, monospace">'+name.toUpperCase()+'</text></g>';
    }
    svg += '</svg>';
    return svg;
  }

  async _toggleAreaLights(areaId, roomName, isOn) {
    if (!this._hass || !areaId) return;
    const turnOn = !isOn;
    try {
      await this._hass.callService('light', turnOn ? 'turn_on' : 'turn_off',
        {}, { area_id: areaId });
      this._toast((turnOn ? '◯ ' : '● ') + roomName + ' lights ' + (turnOn ? 'on' : 'off'), 'ok');
      // Reflect the new state quickly rather than waiting for the 5s poll.
      setTimeout(() => { try { this._fetchLiveData(); } catch (e) {} }, 500);
    } catch (err) {
      this._toast('✗ ' + roomName + ' lights — ' + (err && err.message || err), 'err');
    }
  }

  _build3DHouse() {
    const scene = this.shadowRoot?.querySelector('#house3d-scene');
    const el = this.shadowRoot?.querySelector('#house3d');
    if (!scene || !el) return;
    const d = this._data();
    const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const areaList = (d.areasGrid || []).map(a => ({ k: norm(a.name), active: !!a.active }));
    const isOcc = (n) => {
      const nn = norm(n);
      if (!nn) return false;
      let hit = areaList.find(a => a.k === nn);
      if (!hit) hit = areaList.find(a => a.k.length >= 4 && nn.length >= 4 && (a.k.includes(nn) || nn.includes(a.k)));
      return hit ? hit.active : false;
    };
    const domKey = norm(d.dominantRoom && d.dominantRoom.name);
    const isDominant = (n) => !!domKey && norm(n) === domKey;

    // Per-room light state (on/total/area_id) for the light indicator + toggle.
    const lightControlOn = (d.config && d.config.light_control_enabled) !== false;
    const lightInfo = (d.areasGrid || []).map(a => ({
      k: norm(a.name), on: a.lights_on || 0, total: a.lights_total || 0, area_id: a.id,
    }));
    const lightFor = (n) => {
      const nn = norm(n);
      if (!nn) return null;
      let hit = lightInfo.find(a => a.k === nn);
      if (!hit) hit = lightInfo.find(a => a.k.length >= 4 && nn.length >= 4 && (a.k.includes(nn) || nn.includes(a.k)));
      return (hit && hit.total > 0) ? hit : null;
    };

    const fl = this._currentFloor;
    el.innerHTML = '';

    // Read room geometry from floor plan editor config
    const plan = this._getFloorPlan();
    const S = 2.8; // 2D editor units to 3D px scale
    const WH = {
      '1f': 28,
      '2f': 22,
      'bsmt': 16,
    };

    // X/Z center always uses the FULL house footprint (union of all floors),
    // independent of which floor is shown — prevents the house shifting when
    // switching floor tabs.
    let gMinX = Infinity, gMaxX = -Infinity, gMinZ = Infinity, gMaxZ = -Infinity;
    Object.keys(plan).forEach(fk => {
      const fd = plan[fk];
      if (!fd || !fd.rooms) return;
      fd.rooms.forEach(rm => {
        if (rm.type === 'door' || rm.type === 'stairs') return;
        gMinX = Math.min(gMinX, rm.x * S);
        gMaxX = Math.max(gMaxX, (rm.x + rm.w) * S);
        gMinZ = Math.min(gMinZ, rm.y * S);
        gMaxZ = Math.max(gMaxZ, (rm.y + rm.h) * S);
      });
    });
    if (!isFinite(gMinX)) return; // no rooms at all
    const centerX = (gMinX + gMaxX) / 2;
    const centerZ = (gMinZ + gMaxZ) / 2;

    const floorsToShow = fl === 'all' ? ['bsmt', '1f', '2f'] : [fl];

    // Visual air between stacked floors so each level reads as a distinct
    // stratum instead of merging into one tall block. Floors still physically
    // stack (cumulative wall heights), now with a gap inserted between them.
    const FLOOR_GAP = 26;
    const floorBaseY = {
      '1f': 0,
      '2f': -(WH['1f'] + FLOOR_GAP),
      'bsmt': WH['bsmt'] + FLOOR_GAP,
    };

    // Re-center the shown floors vertically so inserting gaps (or viewing a
    // single floor) doesn't drift the house off-frame.
    const _bases = floorsToShow.map(fk => floorBaseY[fk] || 0);
    const _tops  = floorsToShow.map(fk => (floorBaseY[fk] || 0) - (WH[fk] || 22));
    const yMid = (Math.max(..._bases) + Math.min(..._tops)) / 2;

    const self = this;

    function mkFace(p, x, y, z, w, h, rx, ry, rz, bg, bd) {
      const e = document.createElement('div');
      e.className = 'h3d-face';
      e.style.cssText = 'width:' + w + 'px;height:' + h + 'px;background:' + bg
        + ';border:.5px solid ' + (bd || 'rgba(0,242,254,.15)')
        + ';transform:translate3d(' + (x - w/2) + 'px,' + (y - h/2) + 'px,' + z + 'px)'
        + ' rotateX(' + rx + 'deg) rotateY(' + ry + 'deg) rotateZ(' + rz + 'deg)';
      p.appendChild(e);
      return e;
    }

    // A horizontal plane placed in world space (used for per-floor decks).
    function mkPlane(cx, cy, cz, w, dpth, bg, bd, cls) {
      const e = document.createElement('div');
      e.className = 'h3d-face' + (cls ? ' ' + cls : '');
      e.style.cssText = 'width:' + w + 'px;height:' + dpth + 'px;background:' + bg
        + ';border:.5px solid ' + (bd || 'rgba(0,242,254,.15)')
        + ';transform:translate3d(' + (cx - w/2) + 'px,' + (cy - dpth/2) + 'px,' + cz
        + 'px) rotateX(90deg)';
      el.appendChild(e);
      return e;
    }

    // A glowing deck + corner badge for each floor — reinforces the strata.
    function addFloorPlatform(fk) {
      const fd = plan[fk];
      if (!fd || !fd.rooms) return;
      let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
      fd.rooms.forEach(rm => {
        if (rm.type === 'door' || rm.type === 'stairs') return;
        minX = Math.min(minX, rm.x * S); maxX = Math.max(maxX, (rm.x + rm.w) * S);
        minZ = Math.min(minZ, rm.y * S); maxZ = Math.max(maxZ, (rm.y + rm.h) * S);
      });
      if (!isFinite(minX)) return;
      const pad = 7;
      const pw = (maxX - minX) + pad * 2, pd = (maxZ - minZ) + pad * 2;
      const px = (minX + maxX) / 2 - centerX, pz = (minZ + maxZ) / 2 - centerZ;
      const baseY = (floorBaseY[fk] || 0) - yMid;
      // Deck sits a hair below the room floor slabs.
      mkPlane(px, baseY + 2, pz, pw, pd,
        'rgba(0,242,254,.07)', 'rgba(0,242,254,.42)', 'h3d-platform');
      // Floor badge at the near corner.
      const badge = document.createElement('div');
      badge.className = 'h3d-floor-badge';
      badge.textContent = fd.label || fk.toUpperCase();
      badge.style.cssText = 'transform:translate3d(' + (px - pw/2 - 1) + 'px,'
        + (baseY + 1) + 'px,' + (pz + pd/2 - 8) + 'px) rotateX(90deg)';
      el.appendChild(badge);
    }

    function addRoom(rm, fk) {
      const h = WH[fk] || 22;
      // Group origin centered on wall mid-height: floor slab ends up at floorBaseY.
      const by = (floorBaseY[fk] || 0) - yMid - h / 2;
      const oc = isOcc(rm.name);
      const dom = oc && isDominant(rm.name);
      const li = lightFor(rm.name);
      const lit = !!(li && li.on > 0);
      const w = rm.w * S, dd = rm.h * S;
      const x = rm.x * S + w/2 - centerX;
      const z = rm.y * S + dd/2 - centerZ;

      // Occupancy glow tiers: dominant (most-recent activity) brightest + pulse,
      // occupied solid cyan, idle dim wireframe.
      const al = dom ? .20 : (oc ? .12 : .025);
      const floorAl = dom ? .26 : (oc ? .16 : .012);
      const bc = dom ? 'rgba(130,235,255,.85)' : (oc ? 'rgba(0,242,254,.6)' : 'rgba(0,242,254,.12)');
      const fc = 'rgba(0,242,254,' + al + ')';

      const g = document.createElement('div');
      g.style.cssText = 'position:absolute;transform-style:preserve-3d;transform:translate3d('
        + x + 'px,' + by + 'px,' + z + 'px)';

      // 4 walls
      mkFace(g, 0, 0,  dd/2, w, h, 0, 0, 0, fc, bc);
      mkFace(g, 0, 0, -dd/2, w, h, 0, 0, 0, fc, bc);
      mkFace(g, -w/2, 0, 0, dd, h, 0, 90, 0, fc, bc);
      mkFace(g,  w/2, 0, 0, dd, h, 0, 90, 0, fc, bc);
      // Floor — the glowing slab that reads as room OCCUPANCY (cyan) from above
      const floor = mkFace(g, 0, h/2, 0, w, dd, 90, 0, 0, 'rgba(0,242,254,' + floorAl + ')', bc);
      if (dom) floor.classList.add('h3d-glow-dom');
      else if (oc) floor.classList.add('h3d-glow');
      // Ceiling/cap — glows WARM AMBER when the room's LIGHTS are on, so light
      // state reads distinctly from the cyan occupancy on the floor.
      const ceil = mkFace(g, 0, -h/2, 0, w, dd, 90, 0, 0,
        lit ? 'rgba(255,184,72,.14)' : 'rgba(6,14,22,.4)',
        lit ? 'rgba(255,196,96,.45)' : 'rgba(0,130,170,.1)');
      if (lit) ceil.classList.add('h3d-lit');

      // Label on floor
      const lbl = document.createElement('div');
      lbl.className = 'h3d-label';
      const fs = w > 60 ? '7px' : (w > 35 ? '5.5px' : '4px');
      lbl.style.cssText = 'width:' + w + 'px;color:'
        + (dom ? 'rgba(190,245,255,1)' : (oc ? 'rgba(0,242,254,.95)' : 'rgba(0,242,254,.4)'))
        + ';transform:translate3d(' + (-w/2) + 'px,' + (h/2 - 1) + 'px,0) rotateX(90deg);font-size:' + fs;
      lbl.textContent = rm.name.toUpperCase();
      g.appendChild(lbl);

      // Occupancy dot (presence marker) — larger + glowing for the dominant room
      if (oc) {
        const dot = document.createElement('div');
        dot.className = 'h3d-occ-dot' + (dom ? ' h3d-occ-dot-dom' : '');
        dot.style.cssText = 'transform:translate3d(-3px,' + (h/2 - 1) + 'px,0) rotateX(90deg)';
        g.appendChild(dot);
      }

      // Light indicator + control — a clickable bulb on the ceiling. Amber +
      // glow when on, dim outline when off. Click toggles the area's lights.
      if (li) {
        const lamp = document.createElement('div');
        lamp.className = 'h3d-lamp' + (lit ? ' on' : '') + (lightControlOn ? '' : ' static');
        lamp.style.cssText = 'transform:translate3d(-8px,' + (-h/2 - 2) + 'px,0) rotateX(90deg)';
        lamp.title = (li.total > 1 ? li.on + '/' + li.total + ' lights on' : (lit ? 'Light on' : 'Light off'))
          + (lightControlOn ? ' — tap to toggle' : '');
        lamp.innerHTML = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">'
          + '<path d="M9 21h6v-1.5H9V21zm3-19a7 7 0 00-4.2 12.6c.5.4.7.7.7 1.2v.7h7v-.7c0-.5.2-.8.7-1.2A7 7 0 0012 2z" fill="currentColor"/></svg>';
        if (lightControlOn) {
          // Don't let a tap on the bulb start a rotate-drag.
          lamp.addEventListener('pointerdown', function(ev) { ev.stopPropagation(); });
          lamp.addEventListener('mousedown', function(ev) { ev.stopPropagation(); });
          lamp.addEventListener('click', function(ev) {
            ev.stopPropagation();
            self._toggleAreaLights(li.area_id, rm.name, lit);
          });
        }
        g.appendChild(lamp);
      }

      el.appendChild(g);
    }

    function addGarageDoors(garageRoom) {
      if (!garageRoom) return;
      const h = WH['1f'] || 28;
      // Match addRoom's group origin: floor base minus half wall height.
      const by = (floorBaseY['1f'] || 0) - yMid - h / 2;
      const w = garageRoom.w * S;
      const dd = garageRoom.h * S;
      const gx = garageRoom.x * S + w/2 - centerX;
      const gz = garageRoom.y * S + dd/2 - centerZ;
      const doorW = w * 0.27;
      const doorH = h * 0.72;
      const frontZ = gz + dd/2 + 0.3;

      // Find cover entities with "garage" in name, sorted by entity_id for
      // stable bay assignment (Object.keys order is not guaranteed).
      var coverStates = [];
      try {
        Object.keys(self._hass?.states || {})
          .filter(function(eid) {
            if (!eid.startsWith('cover.')) return false;
            var s = self._hass.states[eid];
            var fn = (s.attributes && s.attributes.friendly_name || eid).toLowerCase();
            return fn.indexOf('garage') >= 0 || eid.indexOf('garage') >= 0;
          })
          .sort()
          .forEach(function(eid) {
            coverStates.push({ id: eid, state: self._hass.states[eid].state });
          });
      } catch(e) {}

      [-1, 0, 1].forEach(function(i, idx) {
        var isOpen = coverStates[idx] ? coverStates[idx].state === 'open' : false;
        var doorColor = isOpen ? 'rgba(0,245,160,.06)' : 'rgba(0,242,254,.025)';
        var doorBorder = isOpen ? 'rgba(0,245,160,.3)' : 'rgba(0,242,254,.13)';

        var e = document.createElement('div');
        e.className = 'h3d-face';

        var doorX = gx + i * w * 0.31 - doorW/2;

        if (isOpen) {
          e.style.cssText = 'width:' + doorW + 'px;height:' + doorH + 'px;background:' + doorColor
            + ';border:1px solid ' + doorBorder
            + ';transform:translate3d(' + doorX + 'px,' + (by - h/2 + 2) + 'px,' + frontZ + 'px)'
            + ' rotateX(-80deg);transform-origin:center top';
        } else {
          e.style.cssText = 'width:' + doorW + 'px;height:' + doorH + 'px;background:' + doorColor
            + ';border:1px solid ' + doorBorder + ';border-radius:2px 2px 0 0'
            + ';transform:translate3d(' + doorX + 'px,' + (by + h/2 - doorH/2) + 'px,' + frontZ + 'px)';
        }

        // Panel lines
        [.25, .5, .75].forEach(function(f) {
          var ln = document.createElement('div');
          ln.style.cssText = 'position:absolute;left:5%;width:90%;height:1px;top:' + Math.round(f*100) + '%;background:' + (isOpen ? 'rgba(0,245,160,.1)' : 'rgba(0,242,254,.06)');
          e.appendChild(ln);
        });

        el.appendChild(e);
      });
    }

    // Build rooms from floor plan config
    var garageRoom = null;
    floorsToShow.forEach(function(fk) {
      var fd = plan[fk];
      if (!fd || !fd.rooms) return;
      addFloorPlatform(fk);   // glowing deck + badge first, rooms sit on top
      fd.rooms.forEach(function(rm) {
        if (rm.type === 'stairs' || rm.type === 'door') return;
        if (rm.type === 'bath') {
          // Bath is a room too, just render it
          addRoom(rm, fk);
          return;
        }
        addRoom(rm, fk);
        if (rm.name.toLowerCase() === 'garage') garageRoom = rm;
      });
    });

    // Garage doors on 1F
    if ((fl === 'all' || fl === '1f') && garageRoom) {
      addGarageDoors(garageRoom);
    }

    // Utility labels for basement
    if (fl === 'all' || fl === 'bsmt') {
      var bsmtData = plan['bsmt'];
      if (bsmtData && bsmtData.labels) {
        bsmtData.labels.forEach(function(lbl) {
          var lx = lbl.x * S - centerX;
          var lz = lbl.y * S - centerZ;
          var e = document.createElement('div');
          e.className = 'h3d-label';
          e.style.cssText = 'width:60px;color:rgba(0,242,254,.2);font-size:4px;transform:translate3d('
            + (lx - 30) + 'px,' + ((floorBaseY['bsmt'] || 0) - yMid - 1) + 'px,' + lz + 'px) rotateX(90deg)';
          e.textContent = lbl.text;
          el.appendChild(e);
        });
      }
    }

    this._update3DTransform();
  }

  _update3DTransform() {
    var el = this.shadowRoot?.querySelector('#house3d');
    var hud = this.shadowRoot?.querySelector('#house3d-angle');
    if (!el) return;
    var z = this._zoom3d || 1;
    var ry = this._rot3dY || 45;
    var rx = this._rot3dX || -28;
    el.style.transform = 'translate(-50%,-50%) scale3d(' + z + ',' + z + ',' + z + ')'
      + ' rotateX(' + rx + 'deg) rotateY(' + ry + 'deg)';
    if (hud) hud.textContent = Math.round(((ry % 360) + 360) % 360) + '°';
  }

  _wire3DDrag() {
    var scene = this.shadowRoot?.querySelector('#house3d-scene');
    if (!scene) return;
    var dragging = false, lastX = 0;
    var self = this;
    scene.addEventListener('mousedown', function(e) { dragging = true; lastX = e.clientX; scene.style.cursor = 'grabbing'; });
    scene.addEventListener('mousemove', function(e) {
      if (!dragging) return;
      self._rot3dY = (self._rot3dY || 45) + (e.clientX - lastX) * 0.4;
      lastX = e.clientX;
      self._update3DTransform();
    });
    document.addEventListener('mouseup', function() { dragging = false; if (scene) scene.style.cursor = 'grab'; });
    scene.addEventListener('touchstart', function(e) { dragging = true; lastX = e.touches[0].clientX; }, {passive: true});
    scene.addEventListener('touchmove', function(e) {
      if (!dragging) return;
      self._rot3dY = (self._rot3dY || 45) + (e.touches[0].clientX - lastX) * 0.4;
      lastX = e.touches[0].clientX;
      self._update3DTransform();
    }, {passive: true});
    scene.addEventListener('touchend', function() { dragging = false; });
    scene.addEventListener('wheel', function(e) {
      e.preventDefault();
      var z = self._zoom3d || 1;
      self._zoom3d = Math.max(0.4, Math.min(3, z + (e.deltaY > 0 ? -0.08 : 0.08)));
      self._update3DTransform();
    }, {passive: false});
  }

  _renderFloorPlanEditor(d) {
    const plan = this._getEditingPlan();
    const floor = this._editorFloor || '1f';
    const floorData = plan[floor];
    if (!floorData) return '';

    let html = '';

    // Floor selector + address
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">';
    html += '<div class="floor-tabs">';
    for (const fk of Object.keys(plan)) {
      html += '<button class="floor-tab fp-ed-floor ' + (fk === floor ? 'active' : '') + '" data-ed-floor="' + fk + '">' + plan[fk].label + '</button>';
    }
    html += '</div>';
    html += '<div style="display:flex;gap:6px;align-items:center;">';
    html += '<label class="ctrl" style="padding:5px 10px;font-size:9px;cursor:pointer;">Import BG <input type="file" accept="image/*" class="fp-import-img" style="display:none"/></label>';
    html += '<button class="ctrl" id="fp-add-room" style="padding:5px 10px;font-size:9px;">+ Add Room</button>';
    html += '</div>';
    html += '</div>';

    // Instructions
    html += '<div style="font-size:9px;color:var(--text-dim);font-family:var(--font-mono);letter-spacing:0.06em;margin-bottom:6px;">DRAG to move · Bottom-right handle to resize · Right-click to delete · Click to select</div>';

    // Address bar for OSM overlay
    const savedAddr = this._data().config?.floor_plan_address || '';
    html += '<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;">';
    html += '<span style="font-family:var(--font-mono);font-size:9px;color:var(--text-dim);white-space:nowrap;">Address:</span>';
    html += '<input type="text" id="fp-address" value="' + (savedAddr || '') + '" placeholder="123 Main St, City, State" style="flex:1;padding:5px 8px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);color:var(--cyan);font-family:var(--font-mono);font-size:10px;outline:none;"/>';
    html += '<button class="ctrl" id="fp-load-map" style="padding:5px 10px;font-size:9px;white-space:nowrap;">Load Map</button>';
    html += '</div>';

    // Map + Canvas side by side
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;min-height:420px;" id="fp-split-view">';

    // Left: OSM map
    html += '<div style="border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;min-height:400px;background:rgba(0,5,10,0.9);" id="fp-map-container">';
    if (savedAddr) {
      const q = encodeURIComponent(savedAddr);
      html += '<iframe src="https://www.openstreetmap.org/export/embed.html?bbox=&layer=mapnik&marker=&query=' + q + '" style="width:100%;height:100%;border:none;filter:hue-rotate(180deg) invert(0.9) saturate(0.3);"></iframe>';
    } else {
      html += '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-family:var(--font-mono);font-size:10px;text-align:center;padding:20px;">Enter address above and click Load Map<br/>to show satellite/street view overlay</div>';
    }
    html += '</div>';

    // Right: Floor plan canvas
    html += '<div class="fp-editor-canvas" id="fp-editor-canvas" style="min-height:400px;">';
    html += this._renderEditableSVG(plan, floor);
    html += '</div>';

    html += '</div>'; // end split view

    // Selected room info
    html += '<div id="fp-selected-info" style="font-family:var(--font-mono);font-size:11px;color:var(--cyan);padding:8px 0;min-height:22px;letter-spacing:0.08em;"></div>';

    // Actions
    html += '<div style="display:flex;gap:8px;">';
    html += '<button class="ctrl primary" id="fp-save">Save Layout</button>';
    html += '<button class="ctrl" id="fp-reset">Reset Default</button>';
    html += '</div>';
    return html;
  }

  _renderEditableSVG(plan, floor) {
    const floorData = plan[floor];
    if (!floorData) return '';
    const vb = floorData.viewBox || '0 0 320 140';

    let svg = '<svg viewBox="' + vb + '" class="fp-svg fp-editor-svg" id="fp-editor-svg" style="width:100%;height:100%;min-height:380px;background:rgba(0,5,10,0.9);border:1px solid var(--line);border-radius:var(--radius);cursor:crosshair;">';

    // Grid — 10px with 50px major lines
    svg += '<defs>';
    svg += '<pattern id="fp-grid-sm" width="10" height="10" patternUnits="userSpaceOnUse"><path d="M 10 0 L 0 0 0 10" fill="none" stroke="rgba(0,242,254,0.04)" stroke-width="0.2"/></pattern>';
    svg += '<pattern id="fp-grid-lg" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M 50 0 L 0 0 0 50" fill="none" stroke="rgba(0,242,254,0.1)" stroke-width="0.3"/></pattern>';
    svg += '</defs>';
    svg += '<rect width="100%" height="100%" fill="url(#fp-grid-sm)"/>';
    svg += '<rect width="100%" height="100%" fill="url(#fp-grid-lg)"/>';

    // Axis labels
    for (let x = 50; x < 320; x += 50) {
      svg += '<text x="' + x + '" y="8" fill="rgba(0,242,254,0.15)" font-size="3" font-family="JetBrains Mono">' + x + '</text>';
    }
    for (let y = 50; y < 200; y += 50) {
      svg += '<text x="2" y="' + y + '" fill="rgba(0,242,254,0.15)" font-size="3" font-family="JetBrains Mono">' + y + '</text>';
    }

    // Background image
    const bgs = this._data().config?.floor_plan_bg;
    if (bgs) {
      try {
        const bgData = typeof bgs === 'string' ? JSON.parse(bgs) : bgs;
        if (bgData[floor]) {
          svg += '<image href="' + bgData[floor] + '" x="0" y="0" width="100%" height="100%" opacity="0.2" preserveAspectRatio="xMidYMid meet"/>';
        }
      } catch (_) {}
    }

    // Rooms
    for (let i = 0; i < (floorData.rooms || []).length; i++) {
      const rm = floorData.rooms[i];
      const colors = {room:'#00f2fe',bath:'#567685',stairs:'#0a7d94',door:'#ff9d2e',outdoor:'#00f5a0'};
      const c = colors[rm.type] || '#00f2fe';
      const fs = rm.w > 80 ? 7 : (rm.w > 50 ? 5.5 : (rm.w > 25 ? 4 : 3));
      svg += '<g class="fp-drag-room" data-idx="' + i + '" style="cursor:move">';
      svg += '<rect x="' + rm.x + '" y="' + rm.y + '" width="' + rm.w + '" height="' + rm.h + '" rx="2" fill="rgba(0,242,254,0.06)" stroke="' + c + '" stroke-width="1" class="fp-drag-rect"/>';
      svg += '<text x="' + (rm.x + rm.w/2) + '" y="' + (rm.y + rm.h/2 + 2) + '" text-anchor="middle" fill="' + c + '" font-size="' + fs + '" font-family="Orbitron, monospace" letter-spacing="0.3" pointer-events="none">' + rm.name.toUpperCase() + '</text>';
      svg += '<rect x="' + (rm.x + rm.w - 8) + '" y="' + (rm.y + rm.h - 8) + '" width="8" height="8" fill="' + c + '" opacity="0.3" rx="1" class="fp-resize-handle" data-idx="' + i + '" style="cursor:nwse-resize"/>';
      svg += '</g>';
    }

    // Labels
    for (const lbl of (floorData.labels || [])) {
      svg += '<text x="' + lbl.x + '" y="' + lbl.y + '" text-anchor="middle" fill="#1a3040" font-size="4" font-family="JetBrains Mono, monospace">' + lbl.text + '</text>';
    }

    svg += '</svg>';
    return svg;
  }



  _html() {
    const d = this._data();
    const now = new Date();
    const hh = String(now.getHours()).padStart(2,"0");
    const mm = String(now.getMinutes()).padStart(2,"0");
    const ss = String(now.getSeconds()).padStart(2,"0");
    const days = ["SUN","MON","TUE","WED","THU","FRI","SAT"];
    const mons = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

    const statusRow = (key, stateObj) => `
      <div class="status-row ${stateObj.level}">
        <span class="k"><span class="dot ${stateObj.level}"></span>${key}</span>
        <span class="v">${stateObj.state}</span>
      </div>`;

    // Capability icons + labels — option C: icons above text codes
    const CAP_ICONS = {
      sat:    '<svg viewBox="0 0 24 24"><path d="M12,2A3,3 0 0,1 15,5V11A3,3 0 0,1 12,14A3,3 0 0,1 9,11V5A3,3 0 0,1 12,2M19,11C19,14.53 16.39,17.44 13,17.93V21H11V17.93C7.61,17.44 5,14.53 5,11H7A5,5 0 0,0 12,16A5,5 0 0,0 17,11H19Z"/></svg>',
      spkr:   '<svg viewBox="0 0 24 24"><path d="M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z"/></svg>',
      mmwave: '<svg viewBox="0 0 24 24"><path d="M5,17L9.5,12.5L13.5,16.5L17,13L21,17L19.59,18.41L17,15.83L13.5,19.33L9.5,15.33L5,19.83L3.59,18.41L5,17M5,10.5L9.5,6L13.5,10L17,6.5L21,10.5L19.59,11.91L17,9.33L13.5,12.83L9.5,8.83L5,13.33L3.59,11.91L5,10.5Z"/></svg>',
      cam:    '<svg viewBox="0 0 24 24"><path d="M17,10.5V7A1,1 0 0,0 16,6H4A1,1 0 0,0 3,7V17A1,1 0 0,0 4,18H16A1,1 0 0,0 17,17V13.5L21,17.5V6.5L17,10.5Z"/></svg>',
      light:  '<svg viewBox="0 0 24 24"><path d="M12,2A7,7 0 0,0 5,9C5,11.38 6.19,13.47 8,14.74V17A1,1 0 0,0 9,18H15A1,1 0 0,0 16,17V14.74C17.81,13.47 19,11.38 19,9A7,7 0 0,0 12,2M9,21A1,1 0 0,0 10,22H14A1,1 0 0,0 15,21V20H9V21Z"/></svg>',
      switch: '<svg viewBox="0 0 24 24"><path d="M17,7H7A5,5 0 0,0 2,12A5,5 0 0,0 7,17H17A5,5 0 0,0 22,12A5,5 0 0,0 17,7M17,15A3,3 0 0,1 14,12A3,3 0 0,1 17,9A3,3 0 0,1 20,12A3,3 0 0,1 17,15Z"/></svg>',
      lock:   '<svg viewBox="0 0 24 24"><path d="M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z"/></svg>',
      climate:'<svg viewBox="0 0 24 24"><path d="M15,13V5A3,3 0 0,0 12,2A3,3 0 0,0 9,5V13A5,5 0 1,0 15,13M12,4A1,1 0 0,1 13,5V8H11V5A1,1 0 0,1 12,4Z"/></svg>',
      door:   '<svg viewBox="0 0 24 24"><path d="M12,3V6H7V18H12V21H19V3H12M17,19H13V18H15V6H13V5H17V19Z"/></svg>',
      leak:   '<svg viewBox="0 0 24 24"><path d="M12,20A6,6 0 0,1 6,14C6,10 12,3.25 12,3.25C12,3.25 18,10 18,14A6,6 0 0,1 12,20Z"/></svg>',
      alarm:  '<svg viewBox="0 0 24 24"><path d="M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.2 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.2 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21"/></svg>',
    };
    const capLabel = (c) => c.toUpperCase();
    const capIcon  = (c) => CAP_ICONS[c] || '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/></svg>';

    const areaTile = (a) => {
      const caps = a.caps || [];
      const iconsRow = caps.length
        ? `<div class="area-caps">
             ${caps.slice(0, 5).map(c => `
               <div class="cap" title="${capLabel(c)}">
                 <span class="cap-icon">${capIcon(c)}</span>
                 <span class="cap-lbl">${capLabel(c)}</span>
               </div>
             `).join("")}
           </div>`
        : `<div class="area-caps"><div class="cap cap-empty"><span class="cap-lbl">—</span></div></div>`;
      const hasLights = (a.lights_total || 0) > 0;
      const lit = hasLights && (a.lights_on || 0) > 0;
      const ctlOn = (this._liveData && this._liveData.config && this._liveData.config.light_control_enabled) !== false;
      const lightCtl = hasLights
        ? `<button class="area-light ${lit ? 'on' : ''} ${ctlOn ? '' : 'static'}" data-light-area="${this._esc(a.id || '')}" data-area-name="${this._esc(a.name)}" title="${a.lights_on}/${a.lights_total} lights on${ctlOn ? ' — tap to toggle' : ''}">
             <span class="al-dot"></span>${lit ? 'ON' : 'OFF'}
           </button>`
        : '';
      return `
        <div class="area ${a.active ? 'active' : ''} ${a.bedroom ? 'bedroom' : ''}">
          ${iconsRow}
          <div class="area-foot">
            <div class="area-name">${a.name}</div>
            ${lightCtl}
          </div>
        </div>`;
    };

    const evtRow = (e) => `
      <div class="evt ${e.urgency}">
        <div class="ts">${e.ts}</div>
        <div class="msg"><b>${e.tag}</b> · ${e.msg}</div>
      </div>`;

    return `
<div class="app">

  <!-- MASTHEAD -->
  <div class="masthead">
    <button class="menu-btn" id="menu-btn" title="Menu" aria-label="Open sidebar">
      <svg viewBox="0 0 24 24"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg>
    </button>
    <div class="brand"><img class="brand-logo" src="/jarvis_panel_static/jarvis-logo.png" alt="" onerror="this.style.display='none'"/>J·A·R·V·I·S <span>// v${this._liveData?.version || '—'}</span><span class="status-badge ${this._liveData?.lockdown?.active ? 'alert' : ''}">[ STATUS: ${this._liveData?.lockdown?.active ? 'LOCKDOWN' : 'NOMINAL'} ]</span></div>
    <div class="greeting"><span id="greeting-text">${this._greeting()}</span>, <b>sir</b></div>
    <div class="clock">
      <div class="time" id="clock-time">${hh}:${mm}:${ss}</div>
      <div class="date" id="clock-date">${days[now.getDay()]} · ${String(now.getDate()).padStart(2,"0")} · ${mons[now.getMonth()]} · ${now.getFullYear()}</div>
    </div>
    <button class="lockdown-pill ${this._liveData?.lockdown?.active ? 'on' : ''}" id="lockdown-btn" title="${this._liveData?.lockdown?.active ? 'Lockdown active — tap to lift' : 'Tap to engage lockdown'}">
      <span class="ld-dot"></span>${this._liveData?.lockdown?.active ? 'EXIT LOCKDOWN' : 'LOCKDOWN'}
    </button>
  </div>

  <!-- TAB NAV -->
  <div class="tab-bar">
    <button class="tab ${this._currentTab === 'dashboard' ? 'active' : ''}" data-tab="dashboard">Command Center</button>
    <button class="tab ${this._currentTab === 'settings' ? 'active' : ''}" data-tab="settings">Settings</button>
    <button class="tab ${this._currentTab === 'logs' ? 'active' : ''}" data-tab="logs">Logs</button>
  </div>

  ${this._currentTab === 'dashboard' ? `
  <!-- ═══ DASHBOARD TAB ═══ -->

  <!-- MAIN GRID -->
  <div class="grid">

    <!-- LEFT: STATUS -->
    <div class="c-status panel">
      <div class="head">
        <span>System Status</span>
        <span class="side">◉ LIVE</span>
      </div>
      <div class="status-list">
        ${statusRow("Observer",   d.observer)}
        ${statusRow("Sleep",      d.sleep)}
        ${statusRow("Gemini",     d.gemini)}
        ${statusRow("Broadcast",  d.broadcast)}
        ${statusRow("Notify",     d.notify)}
        ${statusRow("Satellites", d.satellites)}
      </div>
      <div class="meta">
        Bedrooms <span>${d.bedrooms}</span><br>
        Areas Monitored <span>${d.areas}</span><br>
        Announcements Today <span>${d.announcements_today}</span><br>
        Uptime <span>${d.uptime}</span>
      </div>
      <div class="meta" id="cognitive-stats" style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px;">
        <span style="color:var(--cyan);font-family:var(--font-display);font-size:9px;letter-spacing:0.2em;">COGNITIVE CORE</span><br>
        <span class="loading-cog" style="font-size:10px;color:var(--text-dim);">Loading...</span>
      </div>
      ${this._renderSuggestions(d)}
    </div>

    <!-- CENTER: FLOOR PLAN + DOMINANT ROOM -->
    <div class="c-anchor panel floorplan-panel">
      <div class="head">
        <span>Residence Overview</span>
        <span class="side">◉ PRESENCE</span>
      </div>
      <div class="floor-tabs">
        <button class="floor-tab ${this._currentFloor === 'all' ? 'active' : ''}" data-floor="all">All</button>
        <button class="floor-tab ${this._currentFloor === '1f' ? 'active' : ''}" data-floor="1f">1st Floor</button>
        <button class="floor-tab ${this._currentFloor === '2f' ? 'active' : ''}" data-floor="2f">2nd Floor</button>
        <button class="floor-tab ${this._currentFloor === 'bsmt' ? 'active' : ''}" data-floor="bsmt">Basement</button>
      </div>
      <div class="floorplan-wrap" id="floorplan-wrap">
        <div class="house3d-scene" id="house3d-scene">
          <div class="house3d" id="house3d"></div>
          <div class="house3d-hud-tl">1111 MYRTLE RD</div>
          <div class="house3d-hud-tr" id="house3d-angle">45°</div>
        </div>
      </div>
      <!-- Dominant room info below the plan -->
      <div class="dom-info">
        <div class="dom-left">
          <div class="dom-name" id="dom-name">${d.dominantRoom.name}</div>
          <div class="dom-sub" id="dom-sub">${d.dominantRoom.subtitle}</div>
        </div>
        <div class="dom-gauges">
          ${this._domGauges(d.dominantRoom)}
        </div>
      </div>
    </div>

    <!-- RIGHT: ACTIVITY -->
    <div class="c-log panel">
      <div class="head">
        <span>Activity Feed</span>
        <span class="side">LAST ${d.activity.length}</span>
      </div>
      <div class="log">
        ${d.activity.map(evtRow).join("")}
      </div>
    </div>

  </div>

  <!-- AREAS -->
  <div class="panel">
    <div class="head">
      <span>Areas · ${d.areas} Registered</span>
      <span class="side">◉ ${d.areasGrid.filter(a => a.active).length} Occupied</span>
    </div>
    <div class="areas">
      ${d.areasGrid.map(areaTile).join("")}
    </div>
  </div>

  <!-- QUICK ACTIONS (dashboard only — full settings in Settings tab) -->
  <div class="panel">
    <div class="head">
      <span>Quick Actions</span>
      <span class="side">CMD</span>
    </div>
    <div class="controls">
      <button class="ctrl primary" data-svc="jarvis.briefing">Briefing</button>
      <button class="ctrl"         data-svc="jarvis.nap" data-svc-data='{"duration_minutes":30}'>Nap 30m</button>
      <button class="ctrl"         data-svc="jarvis.nap" data-svc-data='{"duration_minutes":60}'>Nap 60m</button>
      <button class="ctrl"         data-svc="jarvis.unshush">Unshush All</button>
      <button class="ctrl"         data-svc="jarvis.observer_status">Status Dump</button>
    </div>
  </div>
  ` : ''}

  ${this._currentTab === 'settings' ? `
  <!-- ═══ SETTINGS TAB ═══ -->
  <div class="settings-page">

    <div class="settings-grid">
      <!-- GENERAL -->
      <div class="panel">
        <div class="head">
          <span>General</span>
          <span class="side">CORE</span>
        </div>
        <div class="toggle-list">
          <div class="toggle-row">
            <span class="toggle-label">Announcements</span>
            <span class="toggle-desc">Master switch — all proactive speech</span>
            <button class="toggle-btn ${(d.config?.announcements_enabled) ? 'on' : 'off'}"
              data-cfg-key="announcements_enabled"
              data-cfg-val="${(d.config?.announcements_enabled) ? 'false' : 'true'}">
              ${(d.config?.announcements_enabled) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Sentinel</span>
            <span class="toggle-desc">Door/garage/lock-left-open alerts</span>
            <button class="toggle-btn ${(d.config?.sentinel_enabled) ? 'on' : 'off'}"
              data-cfg-key="sentinel_enabled"
              data-cfg-val="${(d.config?.sentinel_enabled) ? 'false' : 'true'}">
              ${(d.config?.sentinel_enabled) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Observer</span>
            <span class="toggle-desc">AI event awareness (uses API)</span>
            <button class="toggle-btn ${(d.config?.observer_enabled) ? 'on' : 'off'}"
              data-cfg-key="observer_enabled"
              data-cfg-val="${(d.config?.observer_enabled) ? 'false' : 'true'}">
              ${(d.config?.observer_enabled) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Cognition</span>
            <span class="toggle-desc">Local triage — sees all telemetry, gates cloud</span>
            <button class="toggle-btn ${(d.config?.cognition_enabled) ? 'on' : 'off'}"
              data-cfg-key="cognition_enabled"
              data-cfg-val="${(d.config?.cognition_enabled) ? 'false' : 'true'}">
              ${(d.config?.cognition_enabled) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Camera Watch</span>
            <span class="toggle-desc">Inspect doorbell presses (vision + event-media)</span>
            <button class="toggle-btn ${(d.config?.camera_auto_analyze) ? 'on' : 'off'}"
              data-cfg-key="camera_auto_analyze"
              data-cfg-val="${(d.config?.camera_auto_analyze) ? 'false' : 'true'}">
              ${(d.config?.camera_auto_analyze) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Package Watch</span>
            <span class="toggle-desc">Detect packages &amp; mail at the door</span>
            <button class="toggle-btn ${(d.config?.package_detection) ? 'on' : 'off'}"
              data-cfg-key="package_detection"
              data-cfg-val="${(d.config?.package_detection) ? 'false' : 'true'}">
              ${(d.config?.package_detection) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Visitor Learning</span>
            <span class="toggle-desc">Silently learn from person events — never spoken</span>
            <button class="toggle-btn ${(d.config?.visitor_learning) ? 'on' : 'off'}"
              data-cfg-key="visitor_learning"
              data-cfg-val="${(d.config?.visitor_learning) ? 'false' : 'true'}">
              ${(d.config?.visitor_learning) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Rich Reasoning</span>
            <span class="toggle-desc">Cloud-first judgment for medium+ events</span>
            <button class="toggle-btn ${(d.config?.rich_reasoning) ? 'on' : 'off'}"
              data-cfg-key="rich_reasoning"
              data-cfg-val="${(d.config?.rich_reasoning) ? 'false' : 'true'}">
              ${(d.config?.rich_reasoning) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Light Control</span>
            <span class="toggle-desc">Off = still show which rooms have lights on, but disable toggling from the dashboard</span>
            <button class="toggle-btn ${(d.config?.light_control_enabled !== false) ? 'on' : 'off'}"
              data-cfg-key="light_control_enabled"
              data-cfg-val="${(d.config?.light_control_enabled !== false) ? 'false' : 'true'}">
              ${(d.config?.light_control_enabled !== false) ? 'ON' : 'OFF'}
            </button>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Appliance Power Guessing</span>
            <span class="toggle-desc">Off = only announce appliances with native sensors or ones you've mapped (no guessing from the power meter)</span>
            <button class="toggle-btn ${(d.config?.appliance_power_guessing) ? 'on' : 'off'}"
              data-cfg-key="appliance_power_guessing"
              data-cfg-val="${(d.config?.appliance_power_guessing) ? 'false' : 'true'}">
              ${(d.config?.appliance_power_guessing) ? 'ON' : 'OFF'}
            </button>
          </div>
        </div>
      </div>

      <!-- AI MODELS -->
      <div class="panel">
        <div class="head">
          <span>AI Models</span>
          <span class="side">LLM</span>
        </div>
        <div class="model-list">
          ${this._renderModelRoles(d)}
        </div>
        <div class="model-hint">Model lists are fetched live from each provider. Pick "Custom…" to enter one manually.</div>
        <div class="llm-url-row">
          <span class="llm-url-label">LOCAL LLM URL</span>
          <input class="llm-url-input" type="text"
            placeholder="http://gpu-server:11434/v1"
            value="${this._esc(d.config?.llm_base_url || '')}"
            title="OpenAI-compatible endpoint for the ollama/custom providers — your GPU server. Leave empty for the default."/>
        </div>
      </div>

      <!-- NOTIFICATIONS -->
      <div class="panel">
        <div class="head">
          <span>Notifications</span>
          <span class="side">PUSH</span>
        </div>
        <div class="toggle-list">
          <div class="toggle-row">
            <span class="toggle-label">Notify Device</span>
            <span class="toggle-desc">Phone push for high/critical alerts</span>
            <select class="notify-select" id="notify-select">
              ${this._renderNotifyOptions(d)}
            </select>
          </div>
        </div>
      </div>

      <!-- APPLIANCES / ENERGY PROFILE -->
      <div class="panel">
        <div class="head">
          <span>Appliances</span>
          <span class="side">ENERGY PROFILE</span>
        </div>
        <div class="appliance-intro">Tell JARVIS which appliances exist so it names cycles correctly instead of guessing from the whole-home meter. Map a dedicated power or status entity when one exists (most accurate); otherwise set typical running watts so the meter can match it.</div>
        <div class="appliance-list" id="appliance-list">
          ${this._renderAppliances(d)}
        </div>
        <div class="appliance-actions">
          <button class="btn" id="appliance-add">+ Add appliance</button>
          <button class="btn primary" id="appliance-save">Save appliances</button>
        </div>
        <label class="appliance-unknown">
          <input type="checkbox" id="appliance-unknown-toggle" ${d.config?.appliance_announce_unknown ? 'checked' : ''}/>
          <span>Announce unidentified loads (loads matching no declared appliance)</span>
        </label>
      </div>

      <!-- SENTINEL RULES -->
      <div class="panel">
        <div class="head">
          <span>Sentinel Rules</span>
          <span class="side">${(d.config?.sentinel_rules || []).length} RULES</span>
        </div>
        <div class="rule-list">
          ${this._renderSentinelRules(d)}
        </div>
      </div>

      <!-- OBSERVER STATS -->
      <div class="panel">
        <div class="head">
          <span>Observer Tuning</span>
          <span class="side">STATS</span>
        </div>
        <div class="status-list">
          <div class="status-row ${d.config?.observer_stats?.running ? 'live' : 'off'}">
            <span class="k">Status</span>
            <span class="v">${d.config?.observer_stats?.running ? 'RUNNING' : 'STOPPED'}</span>
          </div>
          <div class="status-row live">
            <span class="k">Calls / Hour</span>
            <span class="v">${d.config?.observer_stats?.calls_last_hour || 0} / ${(d.config?.observer_stats?.rate_limit ?? 30) <= 0 ? '&#8734;' : (d.config?.observer_stats?.rate_limit ?? 30)}</span>
          </div>
          <div class="status-row live">
            <span class="k">Hourly Cap</span>
            <span class="v"><input type="number" min="0" step="1" class="rate-limit-input" value="${d.config?.observer_stats?.rate_limit ?? 30}" title="Max observer LLM calls per hour. 0 = unlimited (local LLM / high-quota tiers)." style="width:58px;background:rgba(0,0,0,0.4);border:1px solid var(--line-hot,#2a3f4a);color:var(--text,#cde);padding:2px 6px;border-radius:4px;font-family:inherit;font-size:inherit;text-align:right;"></span>
          </div>
          <div class="status-row live">
            <span class="k">Events 24h</span>
            <span class="v">${d.config?.observer_stats?.events_24h || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">Flagged 24h</span>
            <span class="v">${d.config?.observer_stats?.flagged_24h || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">Spoken 24h</span>
            <span class="v">${d.config?.observer_stats?.spoken_24h || 0}</span>
          </div>
          <div class="status-row ${d.config?.observer_stats?.cognition_enabled ? 'live' : 'off'}">
            <span class="k">Cognition</span>
            <span class="v">${d.config?.observer_stats?.cognition_enabled ? 'ACTIVE' : 'OFF'}</span>
          </div>
          <div class="status-row live">
            <span class="k">Tracked Entities</span>
            <span class="v">${d.config?.observer_stats?.cog_entities || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">Predictable</span>
            <span class="v">${d.config?.observer_stats?.cog_predictable || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">Routines Learned</span>
            <span class="v">${d.config?.observer_stats?.cog_routines || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">Presence Routines</span>
            <span class="v">${d.config?.observer_stats?.cog_presence || 0}</span>
          </div>
          ${(d.config?.observer_stats?.presence || []).map(p => `
          <div class="status-row live">
            <span class="k">${p.name}${p.gps ? ' 📍' : ''}</span>
            <span class="v">${p.zone}${p.distance_km != null ? ' · ' + p.distance_km + ' km' : ''}</span>
          </div>`).join('')}
          <div class="status-row live">
            <span class="k">Cog Escalated</span>
            <span class="v">${d.config?.observer_stats?.cog_escalated || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">Local Decisions</span>
            <span class="v">${d.config?.observer_stats?.local_rate || 0}% (${d.config?.observer_stats?.local_decisions || 0} local / ${d.config?.observer_stats?.cloud_calls || 0} cloud)</span>
          </div>
          <div class="status-row live">
            <span class="k">Learned Patterns</span>
            <span class="v">${d.config?.observer_stats?.learned_patterns || 0}</span>
          </div>
          <div class="status-row live">
            <span class="k">LLM Link</span>
            <span class="v" style="${(d.config?.observer_stats?.llm_breaker === 'open') ? 'color:#ff8a8a' : ((d.config?.observer_stats?.llm_breaker === 'half_open') ? 'color:#ffcf6a' : '')}">${(d.config?.observer_stats?.llm_breaker === 'open') ? 'LOCAL-ONLY' : ((d.config?.observer_stats?.llm_breaker === 'half_open') ? 'PROBING' : 'ONLINE')}</span>
          </div>
        </div>
      </div>

      <!-- MEMORY -->
      <div class="panel">
        <div class="head">
          <span>Memory</span>
          <span class="side">RECALL</span>
        </div>
        <div class="status-list">
          <div class="status-row live">
            <span class="k">Backend</span>
            <span class="v">${d.config?.memory_stats?.backend || '—'}</span>
          </div>
          <div class="status-row live">
            <span class="k">Stored Memories</span>
            <span class="v">${d.config?.memory_stats?.total_memories || 0}</span>
          </div>
        </div>
      </div>

      <!-- SATELLITE ROUTING -->
      <div class="panel">
        <div class="head">
          <span>Satellite → Speaker</span>
          <span class="side">ROUTING</span>
        </div>
        <div class="pairing-list">
          ${this._renderSatellitePairings(d)}
        </div>
      </div>

      <!-- ANNOUNCEMENT SPEAKERS -->
      <div class="panel">
        <div class="head">
          <span>Announcement Speakers</span>
          <span class="side">BROADCAST</span>
        </div>
        <div class="rule-list">
          ${this._renderAnnouncementSpeakers(d)}
        </div>
      </div>

      <!-- DIAGNOSTICS (moved under announcements) -->
      <div class="panel">
        <div class="head">
          <span>Diagnostics</span>
          <span class="side">TEST</span>
        </div>
        <div class="diag-row">
          <div class="label">TTS — JARVIS voice test</div>
          <button class="btn" data-svc="jarvis.test_tts">Run</button>
        </div>
        <div class="diag-row">
          <div class="label">Observer — fire status event</div>
          <button class="btn" data-svc="jarvis.observer_status">Run</button>
        </div>
        <div class="diag-row">
          <div class="label">Briefing — manual trigger</div>
          <button class="btn" data-svc="jarvis.briefing">Run</button>
        </div>
        <div class="diag-row">
          <div class="label">Doorbell — run diagnostics</div>
          <button class="btn" data-svc="jarvis.diagnose_doorbell">Run</button>
        </div>
        <div class="diag-row">
          <div class="label">Notification — test phone push</div>
          <button class="btn" data-svc="jarvis.test_notify">Run</button>
        </div>
        <div class="diag-row">
          <div class="label">Routing — dump routing state to log</div>
          <button class="btn" data-svc="jarvis.test_routing">Run</button>
        </div>
        <div class="diag-row">
          <div class="label">Camera — analyze now (vision → reasoning)</div>
          <select class="notify-select diag-camera-select">${this._renderCameraOptions(d)}</select>
          <button class="btn diag-camera-run">Run</button>
        </div>
      </div>
    </div>

    <!-- FLOOR PLAN EDITOR — full width below settings grid -->
    <div class="panel" style="margin-top:16px;">
      <div class="head">
        <span>Floor Plan Editor</span>
        <span class="side">LAYOUT</span>
      </div>
      <div class="fp-editor" id="fp-editor-wrap">
        ${this._renderFloorPlanEditor(d)}
      </div>
    </div>

    <!-- DOORBELL TRAINING — backlog scan + analysed-event dataset -->
    <div class="panel" style="margin-top:16px;">
      <div class="head">
        <span>Doorbell Training</span>
        <span class="side">DATASET</span>
      </div>
      <div class="dbt-intro">Analysed doorbell events — JARVIS's visitor training data. Each press is logged automatically; run a backlog scan to mine the Nest recorded-event history into the dataset.</div>
      ${this._renderDoorbellTraining(d)}
    </div>

  </div>
  ` : ''}

  ${this._currentTab === 'logs' ? `
  <div class="logs-tab">
    <div class="panel">
      <div class="head">
        <span>System Log</span>
        <span class="side">JARVIS INTERNAL</span>
      </div>
      <div class="log-filters">
        <button class="log-filter active" data-filter="all">ALL</button>
        <button class="log-filter" data-filter="CONV">CONV</button>
        <button class="log-filter" data-filter="LOCAL">LOCAL</button>
        <button class="log-filter" data-filter="AGENT">AGENT</button>
        <button class="log-filter" data-filter="GATE">GATE</button>
        <button class="log-filter" data-filter="DEDUP">DEDUP</button>
        <button class="log-filter" data-filter="CLASSIFY">CLASSIFY</button>
        <button class="log-filter" data-filter="CAMERA">CAMERA</button>
        <button class="log-filter" data-filter="ROUTE">ROUTE</button>
        <button class="log-filter" data-filter="ERROR">ERROR</button>
      </div>
      <div id="debug-log-entries" class="log-entries">
        <div class="log-loading">Loading...</div>
      </div>
    </div>
  </div>
  ` : ''}

  <!-- FOOTER -->
  <div class="footer">
    <div>NODE: <span class="hl">HOMEASSISTANT.LOCAL</span></div>
    <div class="mid">// JARVIS · v${this._liveData?.version || '—'} · ${this._currentTab.toUpperCase()}</div>
    <div>STATUS: <span class="hl">NOMINAL</span></div>
  </div>

  <!-- Toast container -->
  <div class="toast-wrap" id="toast-wrap"></div>
</div>
    `;
  }

  // ─── Event wiring ────────────────────────────────────────────────────────

  _wire() {
    // Hamburger menu → HA sidebar toggle
    const mBtn = this.shadowRoot.querySelector("#menu-btn");
    if (mBtn) {
      mBtn.addEventListener("click", () => {
        this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
      });
    }

    // Lockdown toggle
    const ldBtn = this.shadowRoot.querySelector("#lockdown-btn");
    if (ldBtn) {
      ldBtn.addEventListener("click", async () => {
        const active = !!this._liveData?.lockdown?.active;
        ldBtn.disabled = true;
        try {
          await this._hass.callWS({ type: "jarvis/set_lockdown", on: !active });
          await this._fetchLiveData();
        } catch (e) {
          console.warn("JARVIS: lockdown toggle failed", e);
        } finally {
          ldBtn.disabled = false;
        }
      });
    }

    // Tab switching
    this.shadowRoot.querySelectorAll(".tab").forEach(tab => {
      tab.addEventListener("click", (e) => {
        const newTab = e.currentTarget.getAttribute("data-tab");
        if (newTab && newTab !== this._currentTab) {
          this._currentTab = newTab;
          this._render();
          if (newTab === "logs") this._fetchDebugLog();
        }
      });
    });

    // Log filter buttons
    this.shadowRoot.querySelectorAll(".log-filter").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const filter = e.currentTarget.getAttribute("data-filter");
        this._logFilter = filter;
        // Update active state
        this.shadowRoot.querySelectorAll(".log-filter").forEach(b => b.classList.remove("active"));
        e.currentTarget.classList.add("active");
        this._fetchDebugLog();
      });
    });

    // Floor plan tabs — rebuild 3D house
    this.shadowRoot.querySelectorAll(".floor-tab").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const floor = e.currentTarget.getAttribute("data-floor");
        if (floor && floor !== this._currentFloor) {
          this._currentFloor = floor;
          this.shadowRoot.querySelectorAll(".floor-tab").forEach(b => b.classList.remove("active"));
          e.currentTarget.classList.add("active");
          this._build3DHouse();
        }
      });
    });

    // Build 3D house on initial render
    if (this._currentTab === 'dashboard') {
      this._build3DHouse();
      this._wire3DDrag();
    }

    // Service-call buttons
    this.shadowRoot.querySelectorAll("[data-svc]").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const svcAttr = e.currentTarget.getAttribute("data-svc");
        const dataAttr = e.currentTarget.getAttribute("data-svc-data");
        if (!svcAttr || !this._hass) return;
        const [domain, service] = svcAttr.split(".");
        let serviceData = {};
        if (dataAttr) {
          try { serviceData = JSON.parse(dataAttr); } catch (_) { serviceData = {}; }
        }
        try {
          await this._hass.callService(domain, service, serviceData);
          this._toast(`✓ ${svcAttr}`, "ok");
        } catch (err) {
          this._toast(`✗ ${svcAttr} — ${err?.message || err}`, "err");
        }
      });
    });

    // Observer hourly call cap (0 = unlimited). Saves live to runtime_config.
    const rl = this.shadowRoot.querySelector(".rate-limit-input");
    if (rl) {
      rl.addEventListener("change", async () => {
        let v = parseInt(rl.value, 10);
        if (isNaN(v) || v < 0) v = 0;
        rl.value = v;
        await this._saveConfig("classifier_rate_limit", v);
        this._toast(v === 0 ? "✓ hourly cap → unlimited" : `✓ hourly cap → ${v}/hr`, "ok");
      });
    }

    // Area-card light toggles (flat, always-clickable control mirroring the 3D lamp).
    // Skipped entirely when light control is disabled — the pill stays as a pure indicator.
    const _lightCtlOn = (this._liveData && this._liveData.config && this._liveData.config.light_control_enabled) !== false;
    if (_lightCtlOn) {
      this.shadowRoot.querySelectorAll('.area-light').forEach(btn => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const areaId = btn.getAttribute('data-light-area');
          const name = btn.getAttribute('data-area-name') || 'Area';
          const isOn = btn.classList.contains('on');
          this._toggleAreaLights(areaId, name, isOn);
        });
      });
    }

    // Pattern-engine suggestions: approve / dismiss / YAML reveal
    this.shadowRoot.querySelectorAll(".sug").forEach(card => {
      const sid = parseInt(card.getAttribute("data-sug-id"), 10);
      const act = async (action) => {
        if (!this._hass || isNaN(sid)) return;
        try {
          await this._hass.callWS({ type: "jarvis/suggestion_action", suggestion_id: sid, action });
          this._toast(action === "approve"
            ? "✓ approved — YAML is ready to paste into your automations"
            : "✓ dismissed", "ok");
          card.style.opacity = "0.35";
          card.querySelectorAll("button").forEach(b => b.disabled = true);
        } catch (err) {
          this._toast(`✗ suggestion — ${err?.message || err}`, "err");
        }
      };
      card.querySelector(".sug-approve")?.addEventListener("click", () => act("approve"));
      card.querySelector(".sug-dismiss")?.addEventListener("click", () => act("dismiss"));
      card.querySelector(".sug-yaml-btn")?.addEventListener("click", () => {
        const pre = card.querySelector(".sug-yaml");
        if (pre) pre.hidden = !pre.hidden;
      });
    });

    // Local LLM base URL (Ollama / GPU server endpoint)
    const llmUrl = this.shadowRoot.querySelector(".llm-url-input");
    if (llmUrl) {
      llmUrl.addEventListener("change", async () => {
        const v = llmUrl.value.trim();
        await this._saveConfig("llm_base_url", v);
        this._toast(v ? `✓ local LLM endpoint → ${v}` : "✓ local LLM endpoint cleared", "ok");
      });
    }

    // Doorbell backlog training scan
    const dbtScan = this.shadowRoot.querySelector(".dbt-scan");
    if (dbtScan) {
      dbtScan.addEventListener("click", async () => {
        if (!this._hass) return;
        const limInput = this.shadowRoot.querySelector(".dbt-limit");
        let limit = limInput ? parseInt(limInput.value, 10) : 40;
        if (isNaN(limit) || limit < 1) limit = 40;
        this._toast(`⏳ scanning doorbell backlog (up to ${limit})…`, "ok");
        try {
          await this._hass.callService("jarvis", "train_doorbell_backlog", { limit });
          this._toast("✓ backlog scan running — dataset updates shortly (see Logs)", "ok");
          setTimeout(() => this._fetchLiveData && this._fetchLiveData(), 4000);
        } catch (err) {
          this._toast(`✗ backlog — ${err?.message || err}`, "err");
        }
      });
    }

    // Camera diagnostic: run a full vision → reasoning → incorporate review
    // on the selected camera. Manual call, so it always reports the result.
    const camRun = this.shadowRoot.querySelector(".diag-camera-run");
    if (camRun) {
      camRun.addEventListener("click", async () => {
        const sel = this.shadowRoot.querySelector(".diag-camera-select");
        const entity_id = sel ? sel.value : "";
        if (!entity_id || !this._hass) {
          this._toast("✗ no camera selected", "err");
          return;
        }
        this._toast(`⏳ analyzing ${entity_id}…`, "ok");
        try {
          await this._hass.callService("jarvis", "analyze_camera", {
            entity_id, announce: true,
          });
          this._toast(`✓ camera review ran — check Logs for the result`, "ok");
        } catch (err) {
          this._toast(`✗ camera — ${err?.message || err}`, "err");
        }
      });
    }

    // Config toggle buttons (Settings panel). Scope to elements that carry a
    // toggle VALUE (data-cfg-val) — i.e. the on/off buttons. Without this,
    // the selector also matched the AI-Models provider/model <select>s (which
    // carry data-cfg-key but no data-cfg-val), and their click fired this
    // handler too — writing data-cfg-val (null) over the just-saved value and
    // reverting the provider to groq.
    this.shadowRoot.querySelectorAll("[data-cfg-key][data-cfg-val]").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const key = e.currentTarget.getAttribute("data-cfg-key");
        const rawVal = e.currentTarget.getAttribute("data-cfg-val");
        const value = rawVal === "true" ? true : rawVal === "false" ? false : rawVal;
        if (!key || !this._hass) return;
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: key,
            value: value,
          });
          this._toast(`✓ ${key} → ${value}`, "ok");
          await this._fetchAndRender();
        } catch (err) {
          this._toast(`✗ ${key} — ${err?.message || err}`, "err");
        }
      });
    });

    // AI Models: provider + live-fetched model dropdowns, with custom fallback.
    // No _fetchAndRender on change — that would tear down the just-populated
    // model list. The native <select> already reflects the new value, and the
    // 5s poll leaves the settings tab untouched (see _patchLiveDom guard).
    this.shadowRoot.querySelectorAll(".model-row").forEach(row => {
      const provSel = row.querySelector(".prov-select");
      const modelSel = row.querySelector(".model-select");
      const customInput = row.querySelector(".model-custom");
      if (!provSel || !modelSel) return;
      // Custom input is shown only when the current model isn't a listed one;
      // hidden by default, revealed when "Custom…" is chosen.
      if (customInput) customInput.style.display = "none";
      // Populate live models for the current provider.
      this._loadModelsFor(provSel.value, modelSel);
      // Provider change → persist + refetch this role's model list, then
      // persist the resulting first model so provider+model stay CONSISTENT.
      // (Without this, switching groq→gemini→groq left a gemini model under
      // the groq provider, which fails at call time.)
      provSel.addEventListener("change", async (e) => {
        const provider = e.target.value;
        const provKey = provSel.getAttribute("data-cfg-key");
        await this._saveConfig(provKey, provider);
        // Main Agent only: clear the shared base_url when switching to a
        // provider that has a built-in endpoint, so a stale base_url can't
        // misroute the call (e.g. a Groq client pointed at Google's URL →
        // 500 INTERNAL). Ollama/custom keep their URL (they need one).
        if (provKey === "llm_provider" &&
            ["groq", "openai", "gemini", "anthropic"].includes(provider)) {
          await this._saveConfig("llm_base_url", "");
        }
        modelSel.setAttribute("data-current", "");
        if (customInput) customInput.style.display = "none";
        await this._loadModelsFor(provider, modelSel);
        const newModel = modelSel.value;
        if (newModel && newModel !== "__custom__" && newModel !== "") {
          await this._saveConfig(modelSel.getAttribute("data-cfg-key"), newModel);
          modelSel.setAttribute("data-current", newModel);
        }
      });
      // Model change → persist, or reveal custom input.
      modelSel.addEventListener("change", async (e) => {
        if (e.target.value === "__custom__") {
          if (customInput) { customInput.style.display = ""; customInput.focus(); }
          return;
        }
        if (customInput) customInput.style.display = "none";
        await this._saveConfig(modelSel.getAttribute("data-cfg-key"), e.target.value);
        modelSel.setAttribute("data-current", e.target.value);
      });
      // Custom model entry → persist on commit.
      if (customInput) {
        customInput.addEventListener("change", async (e) => {
          const v = (e.target.value || "").trim();
          if (v) {
            await this._saveConfig(customInput.getAttribute("data-cfg-key"), v);
            modelSel.setAttribute("data-current", v);
          }
        });
      }
    });

    // Notify device dropdown
    const notifySel = this.shadowRoot.querySelector("#notify-select");
    if (notifySel) {
      notifySel.addEventListener("change", async (e) => {
        const value = e.target.value || "";
        if (!this._hass) return;
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "notify_service",
            value: value,
          });
          this._toast(`✓ notify → ${value || 'none'}`, "ok");
        } catch (err) {
          this._toast(`✗ notify — ${err?.message || err}`, "err");
        }
      });
    }

    // Appliance profile editor (Settings → Appliances)
    const apList = this.shadowRoot.querySelector("#appliance-list");
    const apAdd = this.shadowRoot.querySelector("#appliance-add");
    const apSave = this.shadowRoot.querySelector("#appliance-save");
    const apUnknown = this.shadowRoot.querySelector("#appliance-unknown-toggle");
    if (apAdd && apList) {
      apAdd.addEventListener("click", () => {
        const empty = apList.querySelector(".appliance-empty");
        if (empty) empty.remove();
        const tmp = document.createElement("div");
        tmp.innerHTML = this._applianceRow({ name: "", type: "appliance", entity: "", watts: "" }, 0);
        const row = tmp.firstElementChild;
        if (row) apList.appendChild(row);
      });
    }
    if (apList) {
      apList.addEventListener("click", (e) => {
        const rm = e.target.closest(".appliance-remove");
        if (rm) {
          e.preventDefault();
          const row = rm.closest(".appliance-row");
          if (row) row.remove();
        }
      });
    }
    if (apSave) {
      apSave.addEventListener("click", async () => {
        const rows = Array.from(this.shadowRoot.querySelectorAll(".appliance-row"));
        const out = [];
        rows.forEach(r => {
          const name = (r.querySelector(".appliance-name")?.value || "").trim();
          if (!name) return;
          out.push({
            name,
            type: r.querySelector(".appliance-type")?.value || "appliance",
            entity: r.querySelector(".appliance-entity")?.value || "",
            watts: parseFloat(r.querySelector(".appliance-watts")?.value || "0") || 0,
          });
        });
        await this._saveConfig("appliance_profile", JSON.stringify(out));
        try {
          await this._hass.callWS({ type: "jarvis/reload_appliances" });
          this._toast(`✓ ${out.length} appliance(s) applied`, "ok");
        } catch (err) {
          this._toast(`✗ reload — ${err?.message || err}`, "err");
        }
      });
    }
    if (apUnknown) {
      apUnknown.addEventListener("change", async (e) => {
        await this._saveConfig("appliance_announce_unknown", e.target.checked);
        try { await this._hass.callWS({ type: "jarvis/reload_appliances" }); } catch (err) {}
      });
    }

    // Sentinel rule toggles
    this.shadowRoot.querySelectorAll(".rule-toggle").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const ruleId = e.currentTarget.getAttribute("data-rule-id");
        if (!ruleId || !this._hass) return;
        const d = this._data();
        const current = d.config?.disabled_sentinel_rules || [];
        const isDisabled = current.includes(ruleId);
        const updated = isDisabled
          ? current.filter(id => id !== ruleId)
          : [...current, ruleId];
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "disabled_sentinel_rules",
            value: JSON.stringify(updated),
          });
          this._toast(`✓ ${ruleId} → ${isDisabled ? 'ON' : 'OFF'}`, "ok");
          await this._fetchAndRender();
        } catch (err) {
          this._toast(`✗ rule toggle — ${err?.message || err}`, "err");
        }
      });
    });

    // Satellite → Cast device pairing dropdowns
    this.shadowRoot.querySelectorAll(".sat-pair-select").forEach(sel => {
      sel.addEventListener("change", async (e) => {
        const satId = e.currentTarget.getAttribute("data-sat-id");
        const castId = e.currentTarget.value || "";
        if (!satId || !this._hass) return;
        const d = this._data();
        const pairings = {...(d.config?.satellite_pairings || {})};
        if (castId) {
          pairings[satId] = castId;
        } else {
          delete pairings[satId];
        }
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "satellite_pairings",
            value: JSON.stringify(pairings),
          });
          const label = castId ? castId.split(".").pop() : "none";
          this._toast(`✓ paired → ${label}`, "ok");
        } catch (err) {
          this._toast(`✗ pairing — ${err?.message || err}`, "err");
        }
      });
    });

    // Announcement speaker toggles
    this.shadowRoot.querySelectorAll(".ann-speaker-toggle").forEach(btn => {

    // Floor plan editor — floor tabs
    this.shadowRoot.querySelectorAll(".fp-ed-floor").forEach(btn => {
      btn.addEventListener("click", (e) => {
        this._editorFloor = e.currentTarget.getAttribute("data-ed-floor");
        const editorDiv = this.shadowRoot.querySelector("#fp-editor-wrap");
        if (editorDiv) { editorDiv.innerHTML = this._renderFloorPlanEditor(this._data()); }
        this._wireFloorPlanDrag();
      });
    });

    // Wire drag events
    this._wireFloorPlanDrag();

    // Floor plan editor — Save (uses shared _editingPlan)
    const fpSave = this.shadowRoot.querySelector("#fp-save");
    if (fpSave) {
      fpSave.addEventListener("click", async () => {
        if (!this._editingPlan) return;
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "floor_plan_rooms",
            value: JSON.stringify(this._editingPlan),
          });
          this._editingPlan = null; // clear so dashboard reads saved version
          this._toast("✓ Floor plan saved", "ok");
        } catch (err) {
          this._toast("✗ Save failed — " + err, "err");
        }
      });
    }

    // Floor plan editor — Reset
    const fpReset = this.shadowRoot.querySelector("#fp-reset");
    if (fpReset) {
      fpReset.addEventListener("click", async () => {
        this._editingPlan = null; // force fresh default on next _getFloorPlan
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "floor_plan_rooms",
            value: "",
          });
          this._editingPlan = null;
          this._toast("✓ Floor plan reset to default", "ok");
          const edWrap = this.shadowRoot.querySelector("#fp-editor-wrap");
          if (edWrap) { edWrap.innerHTML = this._renderFloorPlanEditor(this._data()); this._wireFloorPlanDrag(); }
        } catch (err) {
          this._toast("✗ Reset failed — " + err, "err");
        }
      });
    }

    // Floor plan editor — Add Room
    const fpAdd = this.shadowRoot.querySelector("#fp-add-room");
    if (fpAdd) {
      fpAdd.addEventListener("click", () => {
        const plan = this._getEditingPlan();
        const floor = this._editorFloor || '1f';
        if (!plan[floor]) return;
        const name = prompt("Room name:");
        if (!name) return;
        const type = prompt("Type (room, bath, stairs, door, outdoor):", "room") || "room";
        plan[floor].rooms = plan[floor].rooms || [];
        plan[floor].rooms.push({name: name, x: 50, y: 50, w: 60, h: 40, type: type});
        const edWrap = this.shadowRoot.querySelector("#fp-editor-wrap");
        if (edWrap) { edWrap.innerHTML = this._renderFloorPlanEditor(this._data()); this._wireFloorPlanDrag(); }
        this._toast("Added " + name + " — drag to position, then Save", "ok");
      });
    }

    // Floor plan editor — Load Map
    const fpMap = this.shadowRoot.querySelector("#fp-load-map");
    if (fpMap) {
      fpMap.addEventListener("click", async () => {
        const addrInput = this.shadowRoot.querySelector("#fp-address");
        const addr = addrInput?.value?.trim();
        if (!addr) { this._toast("Enter an address first", "err"); return; }
        // Save address
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "floor_plan_address",
            value: addr,
          });
        } catch (_) {}
        // Reload map
        const container = this.shadowRoot.querySelector("#fp-map-container");
        if (container) {
          const q = encodeURIComponent(addr);
          container.innerHTML = '<iframe src="https://www.openstreetmap.org/export/embed.html?bbox=&layer=mapnik&marker=&query=' + q + '" style="width:100%;height:100%;border:none;filter:hue-rotate(180deg) invert(0.9) saturate(0.3);"></iframe>';
        }
        this._toast("✓ Map loaded for " + addr, "ok");
      });
    }

    // Floor plan editor — Image import
    const fpImg = this.shadowRoot.querySelector(".fp-import-img");
    if (fpImg) {
      fpImg.addEventListener("change", async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async (ev) => {
          const base64 = ev.target.result;
          const floor = this._editorFloor || "1f";
          let bgs = {};
          try {
            const raw = this._data().config?.floor_plan_bg;
            if (raw) bgs = typeof raw === 'string' ? JSON.parse(raw) : raw;
          } catch (_) {}
          bgs[floor] = base64;
          try {
            await this._hass.callWS({
              type: "jarvis/update_config",
              key: "floor_plan_bg",
              value: JSON.stringify(bgs),
            });
            this._toast("✓ Background image set for " + floor, "ok");
            const edWrap = this.shadowRoot.querySelector("#fp-editor-wrap");
            if (edWrap) { edWrap.innerHTML = this._renderFloorPlanEditor(this._data()); this._wireFloorPlanDrag(); }
          } catch (err) {
            this._toast("✗ Image import failed — " + err, "err");
          }
        };
        reader.readAsDataURL(file);
      });
    }

    // Announcement speaker toggles (existing wiring below)
      btn.addEventListener("click", async (e) => {
        const spkId = e.currentTarget.getAttribute("data-speaker-id");
        if (!spkId || !this._hass) return;
        const d = this._data();
        const current = d.config?.announcement_speakers || [];
        const isOn = current.includes(spkId);
        const updated = isOn
          ? current.filter(id => id !== spkId)
          : [...current, spkId];
        try {
          await this._hass.callWS({
            type: "jarvis/update_config",
            key: "announcement_speakers",
            value: JSON.stringify(updated),
          });
          this._toast(`✓ ${spkId.split(".").pop()} → ${isOn ? 'OFF' : 'ON'}`, "ok");
          await this._fetchAndRender();
        } catch (err) {
          this._toast(`✗ speaker toggle — ${err?.message || err}`, "err");
        }
      });
    });
  }

  _toast(msg, kind = "ok") {
    const wrap = this.shadowRoot.querySelector("#toast-wrap");
    if (!wrap) return;
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => el.classList.add("out"), 2600);
    setTimeout(() => el.remove(), 3100);
  }

  _wireFloorPlanDrag() {
    const svgEl = this.shadowRoot.querySelector("#fp-editor-svg");
    if (!svgEl) return;
    const self = this;
    const plan = this._getEditingPlan(); // shared editing copy
    const floor = this._editorFloor || '1f';
    const rooms = plan[floor]?.rooms;
    if (!rooms) return;

    let dragging = null;
    const infoEl = this.shadowRoot.querySelector("#fp-selected-info");

    function svgPoint(e) {
      const pt = svgEl.createSVGPoint();
      const ctm = svgEl.getScreenCTM().inverse();
      pt.x = e.clientX; pt.y = e.clientY;
      return pt.matrixTransform(ctm);
    }

    function updateInfo(rm) {
      if (infoEl) infoEl.textContent = rm.name + '  X:' + rm.x + '  Y:' + rm.y + '  W:' + rm.w + '  H:' + rm.h + '  [' + rm.type + ']';
    }

    function redraw() {
      const canvas = self.shadowRoot.querySelector("#fp-editor-canvas");
      if (canvas) {
        canvas.innerHTML = self._renderEditableSVG(plan, floor);
        setTimeout(() => self._wireFloorPlanDrag(), 10);
      }
    }

    // Room drag start
    svgEl.querySelectorAll(".fp-drag-room").forEach(g => {
      const rect = g.querySelector(".fp-drag-rect");
      if (!rect) return;

      // Left click — drag
      rect.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        const idx = parseInt(g.getAttribute("data-idx"));
        const rm = rooms[idx];
        if (!rm) return;
        const pt = svgPoint(e);
        dragging = { idx, startX: pt.x, startY: pt.y, origX: rm.x, origY: rm.y, resize: false };
        rect.setAttribute("stroke-width", "2.5");
        updateInfo(rm);
      });

      // Right click — delete
      g.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const idx = parseInt(g.getAttribute("data-idx"));
        const rm = rooms[idx];
        if (!rm) return;
        if (confirm("Delete '" + rm.name + "' from floor plan?")) {
          rooms.splice(idx, 1);
          redraw();
          self._toast("Removed " + rm.name, "ok");
        }
      });
    });

    // Resize handles
    svgEl.querySelectorAll(".fp-resize-handle").forEach(handle => {
      handle.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const idx = parseInt(handle.getAttribute("data-idx"));
        const rm = rooms[idx];
        if (!rm) return;
        const pt = svgPoint(e);
        dragging = { idx, startX: pt.x, startY: pt.y, origW: rm.w, origH: rm.h, resize: true };
        updateInfo(rm);
      });
    });

    svgEl.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const pt = svgPoint(e);
      const rm = rooms[dragging.idx];
      if (!rm) return;

      if (dragging.resize) {
        rm.w = Math.max(15, Math.round(dragging.origW + (pt.x - dragging.startX)));
        rm.h = Math.max(10, Math.round(dragging.origH + (pt.y - dragging.startY)));
      } else {
        rm.x = Math.max(0, Math.round(dragging.origX + (pt.x - dragging.startX)));
        rm.y = Math.max(0, Math.round(dragging.origY + (pt.y - dragging.startY)));
      }
      updateInfo(rm);

      // Live update SVG elements
      const g = svgEl.querySelector('.fp-drag-room[data-idx="' + dragging.idx + '"]');
      if (g) {
        const r = g.querySelector(".fp-drag-rect");
        if (r) { r.setAttribute("x", rm.x); r.setAttribute("y", rm.y); r.setAttribute("width", rm.w); r.setAttribute("height", rm.h); }
        const t = g.querySelector("text");
        if (t) { t.setAttribute("x", rm.x + rm.w/2); t.setAttribute("y", rm.y + rm.h/2 + 2); }
        const rh = g.querySelector(".fp-resize-handle");
        if (rh) { rh.setAttribute("x", rm.x + rm.w - 8); rh.setAttribute("y", rm.y + rm.h - 8); }
      }
    });

    const endDrag = () => {
      if (dragging) { dragging = null; redraw(); }
    };
    svgEl.addEventListener("mouseup", endDrag);
    svgEl.addEventListener("mouseleave", endDrag);
  }

  // ─── Styles (ported from HTML mockup; pared for panel) ──────────────────

  _styles() {
    return `
<style>
  @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700&family=JetBrains+Mono:wght@300;400;500&family=Rajdhani:wght@300;400;500;600;700&display=swap');

  :host {
    display: block;
    height: 100%;
    background-color: #060a13;
    background-image:
      linear-gradient(rgba(0, 242, 254, 0.022) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0, 242, 254, 0.022) 1px, transparent 1px),
      radial-gradient(circle at 50% 0%, rgba(0, 242, 254, 0.05) 0%, transparent 50%),
      radial-gradient(circle at 100% 100%, rgba(10, 18, 36, 0.85) 0%, #060a13 100%);
    background-size: 44px 44px, 44px 44px, 100% 100%, 100% 100%;
    background-attachment: fixed;
    color: #e2e8f0;
    font-family: 'Space Grotesk', 'Rajdhani', 'Segoe UI', sans-serif;
    --bg:         #060a13;
    --bg-panel:   rgba(10, 18, 36, 0.45);
    --bg-elev:    rgba(14, 24, 44, 0.55);
    --line:       rgba(0, 242, 254, 0.12);
    --line-hot:   rgba(0, 242, 254, 0.35);
    --cyan:       #00f2fe;
    --cyan-dim:   #1fb6c9;
    --cyan-glow:  rgba(0, 242, 254, 0.45);
    --cyan-faint: rgba(0, 242, 254, 0.07);
    --amber:      #ffb454;
    --red:        #ff4d6d;
    --green:      #00f5a0;
    --purple:     #b48cff;
    --pink:       #ff6b9d;
    --text:       #e2e8f0;
    --text-dim:   #64748b;
    --text-faint: #334155;
    --font-display: 'Space Grotesk', 'Rajdhani', sans-serif;
    --font-body:    'Space Grotesk', 'Segoe UI', sans-serif;
    --font-mono:    'JetBrains Mono', 'Consolas', monospace;
    --radius:     8px;
    --radius-lg:  12px;
  }
  * { box-sizing: border-box; }
  *::-webkit-scrollbar { width: 4px; height: 4px; }
  *::-webkit-scrollbar-thumb { background: rgba(0, 242, 254, 0.14); border-radius: 2px; }
  *::-webkit-scrollbar-thumb:hover { background: rgba(0, 242, 254, 0.3); }
  *::-webkit-scrollbar-track { background: transparent; }

  .app {
    min-height: 100%;
    background: radial-gradient(ellipse at 50% 0%, rgba(0, 242, 254, 0.03) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 100%, rgba(0, 100, 180, 0.02) 0%, transparent 50%),
                var(--bg);
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    position: relative;
  }
  .app::before {
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      to bottom,
      transparent 0, transparent 3px,
      rgba(0, 242, 254, 0.008) 3px, rgba(0, 242, 254, 0.008) 4px
    );
    pointer-events: none;
    z-index: 0;
  }
  .app > * { position: relative; z-index: 1; }

  /* MASTHEAD */
  .masthead {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 24px;
    padding: 16px 20px;
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    background: var(--bg-panel);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    position: relative;
  }
  .status-badge {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    font-weight: 500;
    background: rgba(0, 245, 160, 0.05);
    border: 1px solid rgba(0, 245, 160, 0.3);
    padding: 3px 9px;
    border-radius: 4px;
    letter-spacing: 1px;
    color: var(--green);
    margin-left: 14px;
    vertical-align: middle;
    white-space: nowrap;
  }
  .status-badge.alert {
    background: rgba(255, 77, 109, 0.06);
    border-color: rgba(255, 77, 109, 0.4);
    color: var(--red);
    animation: ldpulse 1.6s infinite;
  }
  .masthead::before, .masthead::after {
    content: ''; position: absolute; width: 14px; height: 14px; border-color: var(--cyan);
  }
  .masthead::before { top: -1px; left: -1px; border-top: 2px solid; border-left: 2px solid; border-radius: var(--radius-lg) 0 0 0; }
  .masthead::after  { bottom: -1px; right: -1px; border-bottom: 2px solid; border-right: 2px solid; border-radius: 0 0 var(--radius-lg) 0; }

  .menu-btn {
    display: none;
    width: 40px; height: 40px;
    border-radius: 4px;
    border: 1px solid var(--line);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    padding: 0;
  }
  .menu-btn:hover { background: rgba(255,255,255,0.05); border-color: var(--cyan-dim); }
  .menu-btn svg { width: 22px; height: 22px; fill: currentColor; }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    font-family: var(--font-display);
    font-weight: 500;
    font-size: 19px;
    letter-spacing: 0.35em;
    color: var(--text);
  }
  .brand > span:not(.status-badge) {
    color: var(--cyan);
    text-shadow: 0 0 10px var(--cyan-glow);
    font-weight: 500;
  }
  .brand-logo {
    width: 38px;
    height: 38px;
    border-radius: 9px;
    flex: 0 0 auto;
    box-shadow: 0 0 16px var(--cyan-glow);
  }
  .brand span { color: var(--text-dim); font-weight: 400; }

  .lockdown-pill {
    display: flex; align-items: center; gap: 7px;
    background: rgba(0,0,0,0.35);
    border: 1px solid var(--line-hot, #2a3f4a);
    color: var(--text-dim);
    font-family: var(--font-display);
    font-size: 12px; letter-spacing: 0.18em;
    padding: 6px 13px; border-radius: 999px; cursor: pointer;
    transition: all .2s ease; white-space: nowrap;
  }
  .lockdown-pill:hover { border-color: var(--cyan); color: var(--text); }
  .lockdown-pill .ld-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--text-dim); transition: all .2s ease;
  }
  .lockdown-pill.on {
    border-color: #ff5a5a; color: #ff9a9a;
    box-shadow: 0 0 18px rgba(255,60,60,0.35);
  }
  .lockdown-pill.on .ld-dot {
    background: #ff5a5a; box-shadow: 0 0 9px #ff5a5a;
    animation: ldpulse 1.6s ease-in-out infinite;
  }
  @keyframes ldpulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }

  .greeting {
    font-size: 15px;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--text-dim);
    text-align: center;
  }
  .greeting b { color: var(--text); font-weight: 500; margin-left: 0.4em; }

  .clock { text-align: right; font-family: var(--font-mono); }
  .clock .time {
    font-size: 26px;
    font-weight: 500;
    color: var(--cyan);
    letter-spacing: 0.12em;
    line-height: 1;
    text-shadow: 0 0 12px var(--cyan-glow);
  }
  .clock .date {
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 0.3em;
    margin-top: 4px;
  }

  /* GRID */
  .grid {
    display: grid;
    grid-template-columns: 240px 1fr 300px;
    gap: 16px;
    align-items: start;
  }

  /* PANEL base */
  .panel {
    background: var(--bg-panel);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    padding: 20px;
    position: relative;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    transition: border-color 0.3s, box-shadow 0.3s;
  }
  .panel:hover {
    border-color: var(--line-hot);
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37), 0 0 24px rgba(0, 242, 254, 0.05);
  }
  .panel::before, .panel::after {
    content: ''; position: absolute; width: 10px; height: 10px; border-color: var(--cyan-dim);
  }
  .panel::before { top: -1px; left: -1px; border-top: 1.5px solid; border-left: 1.5px solid; border-radius: var(--radius-lg) 0 0 0; }
  .panel::after  { bottom: -1px; right: -1px; border-bottom: 1.5px solid; border-right: 1.5px solid; border-radius: 0 0 var(--radius-lg) 0; }

  .panel .head {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.25em;
    color: var(--text-dim);
    text-transform: uppercase;
    padding-bottom: 10px;
    margin-bottom: 15px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    display: flex; justify-content: space-between; align-items: center;
  }
  .panel .head > span:first-child { color: var(--text-main, var(--text)); }
  .panel:hover .head > span:first-child { color: var(--cyan); transition: color 0.4s; }
  .panel .head .side {
    font-family: var(--font-mono);
    color: var(--text-dim);
    font-size: 9px;
    letter-spacing: 0.2em;
  }

  /* STATUS */
  .status-list { display: flex; flex-direction: column; gap: 10px; }
  .status-row {
    display: grid; grid-template-columns: 1fr auto;
    align-items: center;
    padding: 10px 12px;
    background: var(--bg-elev);
    border-left: 2px solid var(--line);
    border-radius: var(--radius);
    font-size: 13px; letter-spacing: 0.08em;
    transition: border-color 0.3s, background 0.3s;
  }
  .status-row.live { border-left-color: var(--green); background: rgba(0, 245, 160, 0.03); }
  .status-row.warn { border-left-color: var(--amber); background: rgba(255, 157, 46, 0.03); }
  .status-row.off  { border-left-color: var(--text-faint); }
  .status-row .k {
    color: var(--text-dim);
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.22em;
  }
  .status-row .v {
    font-family: var(--font-mono);
    color: var(--text);
    font-size: 12px;
    letter-spacing: 0.1em;
  }
  .status-row.live .v { color: var(--green); }
  .status-row.warn .v { color: var(--amber); }
  .dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    margin-right: 8px;
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2.6s ease-in-out infinite;
  }
  .dot.warn { background: var(--amber); box-shadow: 0 0 8px var(--amber); }
  .dot.off  { background: var(--text-faint); box-shadow: none; animation: none; }

  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.5; transform: scale(0.85); }
  }

  .meta {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px dashed var(--line);
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 0.18em;
    font-family: var(--font-mono);
    line-height: 1.8;
    text-transform: uppercase;
  }
  .meta span { color: var(--cyan); }

  /* ANCHOR */
  .anchor {
    background: linear-gradient(135deg, rgba(5, 7, 9, 0.9), rgba(0, 30, 50, 0.3));
    border: 1px solid var(--line-hot);
    border-radius: var(--radius-lg);
    padding: 28px 24px 24px;
    position: relative;
    min-height: 360px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    backdrop-filter: blur(12px);
    box-shadow: inset 0 0 60px rgba(0, 242, 254, 0.02);
  }
  .anchor::before {
    content: ''; position: absolute;
    top: 50%; left: 50%;
    width: 420px; height: 420px;
    border: 1px solid var(--cyan-faint);
    border-radius: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
  }
  .anchor::after {
    content: ''; position: absolute;
    top: 50%; left: 50%;
    width: 280px; height: 280px;
    border: 1px solid var(--cyan-faint);
    border-radius: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
    animation: rotate-slow 90s linear infinite;
    border-top-color: var(--cyan-dim);
  }
  @keyframes rotate-slow {
    from { transform: translate(-50%, -50%) rotate(0deg); }
    to   { transform: translate(-50%, -50%) rotate(360deg); }
  }

  .anchor-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    position: relative; z-index: 2;
  }
  .anchor-label {
    font-family: var(--font-display);
    font-size: 10px; letter-spacing: 0.4em;
    color: var(--cyan); text-transform: uppercase;
  }
  .anchor-coord {
    font-family: var(--font-mono);
    font-size: 10px; color: var(--text-dim); letter-spacing: 0.15em;
  }

  .anchor-core {
    flex: 1;
    display: flex; flex-direction: column;
    justify-content: center; align-items: center;
    position: relative; z-index: 2;
    padding: 20px 0;
  }

  .room-name {
    font-family: var(--font-display);
    font-size: 42px; font-weight: 500;
    letter-spacing: 0.1em;
    color: var(--text);
    text-transform: uppercase;
    text-shadow: 0 0 30px var(--cyan-glow);
    margin-bottom: 6px;
    text-align: center;
    transition: opacity 0.25s;
  }
  .room-sub {
    font-size: 11px;
    color: var(--cyan);
    letter-spacing: 0.3em;
    text-transform: uppercase;
    margin-bottom: 24px;
    display: flex; align-items: center; gap: 10px;
  }
  .room-sub::before, .room-sub::after {
    content: ''; width: 30px; height: 1px; background: var(--cyan-dim);
  }

  .reactor {
    width: 72px; height: 72px;
    border-radius: 50%;
    background: radial-gradient(circle at center, var(--cyan) 0%, var(--cyan-dim) 40%, transparent 70%);
    box-shadow: 0 0 40px var(--cyan-glow), inset 0 0 20px rgba(255,255,255,0.1);
    animation: reactor-pulse 3s ease-in-out infinite;
    position: relative;
    margin-bottom: 24px;
  }
  .reactor::before {
    content: ''; position: absolute; inset: 10px;
    border-radius: 50%;
    border: 1px solid rgba(255,255,255,0.25);
  }
  .reactor::after {
    content: ''; position: absolute; inset: 22px;
    border-radius: 50%;
    background: rgba(255,255,255,0.9);
    box-shadow: 0 0 20px rgba(255,255,255,0.6);
  }
  @keyframes reactor-pulse {
    0%, 100% { box-shadow: 0 0 40px var(--cyan-glow), inset 0 0 20px rgba(255,255,255,0.1); }
    50%      { box-shadow: 0 0 60px var(--cyan-glow), inset 0 0 25px rgba(255,255,255,0.2); }
  }

  .room-stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 18px;
    width: 100%;
    max-width: 380px;
  }
  .stat {
    text-align: center;
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: rgba(0, 242, 254, 0.02);
    backdrop-filter: blur(4px);
    transition: border-color 0.3s;
  }
  .stat:hover { border-color: var(--cyan-dim); }
  .stat .v {
    font-family: var(--font-display);
    font-size: 20px; font-weight: 500;
    color: var(--cyan); line-height: 1;
    margin-bottom: 6px;
  }
  .stat .k {
    font-size: 9px; color: var(--text-dim);
    letter-spacing: 0.25em; text-transform: uppercase;
  }

  .anchor-foot {
    position: relative; z-index: 2;
    display: flex; justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 10px; color: var(--text-dim);
    letter-spacing: 0.15em;
    padding-top: 16px;
    border-top: 1px solid var(--line);
  }
  .anchor-foot span { color: var(--cyan); }

  /* FLOOR PLAN */
  .floorplan-panel {
    padding: 16px;
  }
  .floor-tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 12px;
  }
  .floor-tab {
    padding: 5px 16px;
    border: 1px solid var(--line);
    border-radius: 20px;
    background: transparent;
    color: var(--text-dim);
    font-family: var(--font-display);
    font-size: 9px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.25s;
  }
  .floor-tab:hover {
    border-color: var(--cyan-dim);
    color: var(--text);
  }
  .floor-tab.active {
    border-color: var(--cyan);
    color: var(--cyan);
    background: rgba(0, 242, 254, 0.08);
    box-shadow: 0 0 10px rgba(0, 242, 254, 0.15);
  }
  .floorplan-wrap {
    background: rgba(0, 5, 10, 0.5);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 0;
    min-height: 450px;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }

  /* 3D House Scene */
  .house3d-scene {
    width: 100%;
    height: 450px;
    perspective: 1100px;
    background:
      radial-gradient(ellipse at 50% 42%, rgba(0, 242, 254, 0.05) 0%, transparent 55%),
      radial-gradient(ellipse at 50% 40%, rgba(0, 25, 45, 0.35), #04070d 72%);
    position: relative;
    overflow: hidden;
    cursor: grab;
    border-radius: var(--radius);
  }
  .house3d-scene::before {
    content: '';
    position: absolute; inset: -30%;
    background-image:
      linear-gradient(rgba(0, 242, 254, 0.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0, 242, 254, 0.035) 1px, transparent 1px);
    background-size: 34px 34px;
    transform: rotateX(60deg) scale(1.15);
    transform-origin: 50% 60%;
    pointer-events: none;
    -webkit-mask-image: radial-gradient(ellipse at 50% 55%, #000 30%, transparent 72%);
    mask-image: radial-gradient(ellipse at 50% 55%, #000 30%, transparent 72%);
  }
  .house3d-scene::after {
    content: '';
    position: absolute; inset: -25%;
    background: conic-gradient(from 0deg at 50% 55%,
      transparent 0deg, rgba(0, 242, 254, 0.05) 16deg,
      rgba(0, 242, 254, 0.012) 32deg, transparent 52deg);
    animation: radarSweep 16s linear infinite;
    pointer-events: none;
  }
  @keyframes radarSweep { to { transform: rotate(360deg); } }
  .house3d-scene:active { cursor: grabbing; }
  .house3d {
    position: absolute;
    left: 50%;
    top: 52%;
    transform-style: preserve-3d;
  }
  .h3d-face {
    position: absolute;
    backface-visibility: visible;
  }
  .h3d-label {
    position: absolute;
    font-family: var(--font-display);
    letter-spacing: 1.5px;
    text-align: center;
    pointer-events: none;
    text-transform: uppercase;
    font-weight: 500;
  }
  .h3d-occ-dot {
    position: absolute;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2.5s ease-in-out infinite;
  }
  .h3d-occ-dot-dom {
    width: 9px;
    height: 9px;
    box-shadow: 0 0 10px 3px rgba(0,245,160,0.75);
  }
  .h3d-glow { box-shadow: 0 0 16px 2px rgba(0,242,254,0.32); }  .h3d-glow-dom {
    box-shadow: 0 0 18px 3px rgba(0,242,254,0.45);
    animation: h3dDom 2.4s ease-in-out infinite;
  }
  @keyframes h3dDom {
    0%, 100% { box-shadow: 0 0 16px 3px rgba(0,242,254,0.40); }
    50%      { box-shadow: 0 0 32px 7px rgba(0,242,254,0.72); }
  }
  .h3d-platform {
    box-shadow: 0 0 30px 2px rgba(0,242,254,0.16);
    pointer-events: none;
  }
  .h3d-floor-badge {
    position: absolute;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 3px;
    color: rgba(120,225,255,0.85);
    text-shadow: 0 0 10px rgba(0,242,254,0.6);
    pointer-events: none;
    white-space: nowrap;
  }
  .h3d-lit {
    box-shadow: 0 0 18px 2px rgba(255,184,72,0.30);
  }
  .h3d-lamp {
    position: absolute;
    width: 17px;
    height: 17px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    color: rgba(0,242,254,0.35);
    background: rgba(6,14,22,0.55);
    border: 1px solid rgba(0,242,254,0.22);
    cursor: pointer;
    pointer-events: auto;
    transition: transform 0.12s ease, box-shadow 0.2s ease, color 0.2s ease;
  }
  .h3d-lamp:hover {
    color: #fff;
    border-color: rgba(255,200,110,0.7);
    transform: scale(1.18);
  }
  .h3d-lamp.on {
    color: #ffce6b;
    background: rgba(58,40,12,0.6);
    border-color: rgba(255,196,96,0.75);
    box-shadow: 0 0 13px 2px rgba(255,184,72,0.6);
  }
  .house3d-hud-tl, .house3d-hud-tr {
    position: absolute;
    color: rgba(0,242,254,0.4);
    font-family: var(--font-mono);
    font-size: 8px;
    letter-spacing: 2px;
    pointer-events: none;
  }
  .house3d-hud-tl { top: 8px; left: 12px; }
  .house3d-hud-tr { top: 8px; right: 12px; }
  .fp-svg {
    width: 100%;
    max-height: 240px;
  }
  .fp-room { cursor: pointer; transition: opacity 0.3s; }
  .fp-room:hover rect { stroke-width: 2 !important; }

  /* Dominant room info bar */
  .dom-info {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--line);
    gap: 16px;
  }
  .dom-left { flex: 1; }
  .dom-name {
    font-family: var(--font-display);
    font-size: 18px;
    font-weight: 500;
    letter-spacing: 0.1em;
    color: var(--cyan);
    text-transform: uppercase;
    text-shadow: 0 0 12px var(--cyan-glow);
  }
  .dom-sub {
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 0.12em;
    margin-top: 2px;
  }
  .dom-gauges {
    display: flex;
    gap: 14px;
  }
  .rgauge {
    position: relative;
    width: 64px;
    text-align: center;
    transition: opacity 0.3s ease;
  }
  .rgauge.dim { opacity: 0.45; }
  .rgauge-svg { width: 64px; height: 64px; display: block; }
  .rgauge-track {
    fill: none;
    stroke: rgba(0, 242, 254, 0.12);
    stroke-width: 5;
  }
  .rgauge-fill {
    fill: none;
    stroke-width: 5;
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: 50% 50%;
    transition: stroke-dashoffset 0.8s cubic-bezier(0.22, 1, 0.36, 1), stroke 0.6s ease;
    filter: drop-shadow(0 0 4px currentColor);
  }
  .rgauge-val {
    position: absolute;
    top: 26px;
    left: 0;
    width: 64px;
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    line-height: 1;
    text-shadow: 0 0 8px var(--cyan-glow);
  }
  .rgauge-lbl {
    margin-top: 2px;
    font-size: 8px;
    color: var(--text-dim);
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }

  /* AREAS */
  .areas {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 10px;
  }
  .area {
    padding: 12px 10px 10px;
    background: var(--bg-panel);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--line);
    border-radius: 10px;
    display: flex; flex-direction: column; gap: 8px;
    position: relative;
    cursor: default;
    transition: transform 0.2s, border-color 0.3s, box-shadow 0.3s;
    min-height: 88px;
  }
  .area:hover {
    border-color: rgba(0, 242, 254, 0.4);
    transform: translateY(-2px);
  }
  .area.active {
    border-color: var(--cyan);
    background: rgba(0, 242, 254, 0.03);
    box-shadow: inset 0 0 15px rgba(0, 242, 254, 0.05), 0 0 18px rgba(0, 242, 254, 0.08);
  }
  .area.active::before {
    content: ''; position: absolute;
    top: 6px; right: 6px;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2.6s ease-in-out infinite;
  }

  /* Capability row (icons above, labels below) */
  .area-caps {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: flex-start;
    min-height: 42px;
  }
  .cap {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    min-width: 26px;
  }
  .cap-icon {
    width: 18px; height: 18px;
    display: flex; align-items: center; justify-content: center;
    color: var(--text-dim);
    transition: color 0.25s;
  }
  .cap-icon svg {
    width: 18px; height: 18px;
    fill: currentColor;
  }
  .cap-lbl {
    font-family: var(--font-mono);
    font-size: 8px;
    letter-spacing: 0.1em;
    color: var(--text-dim);
    line-height: 1;
  }
  .cap-empty .cap-lbl { opacity: 0.4; }
  .area.active .cap-icon { color: var(--cyan); }
  .area.active .cap-lbl  { color: var(--cyan-dim); }

  .area-name {
    font-size: 12px; color: var(--text);
    letter-spacing: 0.1em; text-transform: uppercase;
    font-weight: 500;
    padding-top: 4px;
    border-top: 1px solid var(--line);
    margin-top: auto;
  }
  .area.active .area-name { color: var(--cyan); border-top-color: var(--cyan-faint); }
  .area-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-top: auto;
    border-top: 1px solid var(--line);
    padding-top: 4px;
  }
  .area-foot .area-name {
    border-top: none;
    padding-top: 0;
    margin-top: 0;
  }
  .area.active .area-foot { border-top-color: var(--cyan-faint); }
  .area-light {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 8.5px;
    letter-spacing: 0.08em;
    padding: 3px 7px;
    border-radius: 999px;
    cursor: pointer;
    background: transparent;
    border: 1px solid rgba(0,242,254,0.22);
    color: var(--text-dim);
    transition: color 0.15s, border-color 0.15s, box-shadow 0.2s;
    flex-shrink: 0;
  }
  .area-light .al-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: rgba(0,242,254,0.3);
    transition: background 0.15s, box-shadow 0.2s;
  }
  .area-light:hover { color: #fff; border-color: rgba(255,200,110,0.6); }
  .area-light.on {
    color: #ffce6b;
    border-color: rgba(255,196,96,0.7);
    box-shadow: 0 0 10px rgba(255,184,72,0.4);
  }
  .area-light.on .al-dot {
    background: #ffce6b;
    box-shadow: 0 0 8px 1px rgba(255,184,72,0.8);
  }
  .area-light.static { cursor: default; }
  .area-light.static:hover { color: var(--text-dim); border-color: rgba(0,242,254,0.22); }
  .area-light.static.on:hover { color: #ffce6b; border-color: rgba(255,196,96,0.7); }
  .h3d-lamp.static { cursor: default; }
  .area.bedroom .area-name::before { content: '◐ '; color: var(--amber); }

  /* LOG */
  .log {
    display: flex; flex-direction: column; gap: 1px;
    max-height: 480px;
    overflow-y: auto;
  }
  .log::-webkit-scrollbar { width: 2px; }
  .log::-webkit-scrollbar-track { background: var(--line); }
  .log::-webkit-scrollbar-thumb { background: var(--cyan-dim); }
  .evt {
    padding: 10px 12px;
    border-left: 2px solid var(--cyan);
    border-radius: 0 6px 6px 0;
    background: rgba(255, 255, 255, 0.02);
    display: grid; grid-template-columns: 52px 1fr;
    gap: 10px; font-size: 12px;
    animation: evt-in 0.5s ease-out;
    transition: background 0.2s, border-color 0.2s;
    margin-bottom: 2px;
  }
  .evt:hover { background: rgba(0, 242, 254, 0.04); }
  @keyframes evt-in {
    from { opacity: 0; transform: translateX(8px); }
    to   { opacity: 1; transform: translateX(0); }
  }
  .evt .ts {
    font-family: var(--font-mono);
    color: var(--text-dim);
    font-size: 10px; letter-spacing: 0.1em;
  }
  .evt .msg { color: var(--text); line-height: 1.4; }
  .evt .msg b {
    color: var(--cyan); font-weight: 500; letter-spacing: 0.08em;
  }
  .evt.critical { border-left-color: var(--red); background: rgba(255, 59, 59, 0.04); }
  .evt.critical .msg b { color: var(--red); }
  .evt.high     { border-left-color: var(--amber); background: rgba(255, 157, 46, 0.04); }
  .evt.high .msg b { color: var(--amber); }
  .evt.medium   { border-left-color: var(--cyan-dim); }
  .evt.low      { border-left-color: var(--text-faint); opacity: 0.75; }
  .evt.muted    { border-left-color: var(--text-faint); opacity: 0.5; }
  .evt.muted .msg { font-style: italic; }

  /* CONTROLS */
  .controls {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 8px;
  }

  /* LOGS TAB */
  .logs-tab { padding: 0; }
  .log-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 0 0 12px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--line);
  }
  .log-filter {
    padding: 4px 12px;
    border: 1px solid var(--line);
    border-radius: 20px;
    background: transparent;
    color: var(--text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.12em;
    cursor: pointer;
    transition: all 0.2s;
  }
  .log-filter:hover {
    border-color: var(--cyan-dim);
    color: var(--text);
    background: rgba(0, 242, 254, 0.04);
  }
  .log-filter.active {
    border-color: var(--cyan);
    color: var(--cyan);
    background: rgba(0, 242, 254, 0.1);
    box-shadow: 0 0 8px rgba(0, 242, 254, 0.15);
  }
  .log-entries {
    max-height: 65vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .log-entries::-webkit-scrollbar { width: 3px; }
  .log-entries::-webkit-scrollbar-track { background: var(--bg-elev); border-radius: 3px; }
  .log-entries::-webkit-scrollbar-thumb { background: var(--cyan-dim); border-radius: 3px; }
  .log-entry {
    display: grid;
    grid-template-columns: 60px 70px 1fr;
    gap: 10px;
    padding: 6px 10px;
    background: var(--bg-elev);
    border-radius: var(--radius);
    border-left: 2px solid var(--line);
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.5;
    transition: background 0.2s;
    animation: evt-in 0.3s ease-out;
  }
  .log-entry:hover { background: rgba(0, 242, 254, 0.02); }
  .log-entry-error {
    border-left-color: var(--red) !important;
    background: rgba(255, 59, 59, 0.04) !important;
  }
  .log-ts {
    color: var(--text-dim);
    font-size: 10px;
    letter-spacing: 0.05em;
    white-space: nowrap;
  }
  .log-cat {
    font-weight: 600;
    font-size: 10px;
    letter-spacing: 0.08em;
    white-space: nowrap;
  }
  .log-msg {
    color: var(--text);
    word-break: break-word;
  }
  .log-loading {
    color: var(--cyan-dim);
    padding: 16px;
    text-align: center;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.1em;
  }

  /* TAB BAR */
  .tab-bar {
    display: flex;
    gap: 2px;
    border-bottom: 1px solid var(--line);
    padding: 0 4px;
  }
  .tab {
    padding: 10px 24px;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: var(--radius) var(--radius) 0 0;
    color: var(--text-dim);
    font-family: var(--font-display);
    font-size: 11px;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.25s;
  }
  .tab:hover {
    color: var(--text);
    background: rgba(0, 242, 254, 0.04);
  }
  .tab.active {
    color: var(--cyan);
    border-bottom-color: var(--cyan);
    background: rgba(0, 242, 254, 0.06);
    text-shadow: 0 0 8px var(--cyan-glow);
  }

  /* SETTINGS PAGE */
  .settings-page {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .settings-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 16px;
    align-items: start;
  }
  .ctrl {
    padding: 12px 16px;
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    color: var(--text-dim);
    font-family: var(--font-body);
    font-size: 12px; font-weight: 500;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.25s;
    text-align: center;
  }
  .ctrl:hover {
    border-color: var(--cyan); color: var(--cyan);
    background: rgba(0, 242, 254, 0.06);
    box-shadow: 0 0 15px var(--cyan-glow);
    text-shadow: 0 0 8px var(--cyan-glow);
    transform: translateY(-1px);
  }
  .ctrl.primary { border-color: var(--cyan-dim); color: var(--cyan); }
  .ctrl.warn    { border-color: var(--amber); color: var(--amber); }
  .ctrl.warn:hover { background: rgba(255, 157, 46, 0.05); box-shadow: 0 0 15px rgba(255, 157, 46, 0.2); }

  /* SETTINGS TOGGLES */
  .toggle-list { display: flex; flex-direction: column; gap: 8px; }
  .toggle-row {
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: auto auto;
    align-items: center;
    padding: 10px 12px;
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    gap: 2px 12px;
    transition: border-color 0.2s;
  }
  .toggle-row:hover { border-color: var(--line-hot); }
  .toggle-label {
    font-size: 12px; font-weight: 500;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text);
    grid-column: 1; grid-row: 1;
  }
  .toggle-desc {
    font-size: 9px;
    color: var(--text-dim);
    letter-spacing: 0.1em;
    grid-column: 1; grid-row: 2;
  }
  .toggle-btn {
    grid-column: 2; grid-row: 1 / 3;
    padding: 6px 14px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-elev);
    font-family: var(--font-mono);
    font-size: 11px; font-weight: 500;
    letter-spacing: 0.2em;
    cursor: pointer;
    transition: all 0.25s;
    min-width: 52px;
    text-align: center;
  }
  .toggle-btn.on {
    border-color: var(--green);
    color: var(--green);
    background: rgba(0, 245, 160, 0.08);
    box-shadow: 0 0 10px rgba(0, 245, 160, 0.1);
  }
  .toggle-btn.off {
    border-color: var(--text-faint);
    color: var(--text-dim);
  }
  .toggle-btn:hover {
    border-color: var(--cyan);
    color: var(--cyan);
    background: rgba(0, 242, 254, 0.06);
  }
  .notify-select {
    grid-column: 2; grid-row: 1 / 3;
    padding: 6px 10px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-elev);
    color: var(--cyan);
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    cursor: pointer;
    min-width: 120px;
    max-width: 180px;
    transition: border-color 0.2s;
  }
  .notify-select:hover { border-color: var(--cyan-dim); }
  .notify-select option {
    background: var(--bg-panel);
    color: var(--text);
  }

  /* AI MODELS */
  .model-list { display: flex; flex-direction: column; gap: 8px; }
  .model-row {
    display: grid;
    grid-template-columns: 90px 1fr 1.4fr;
    grid-auto-rows: auto;
    gap: 6px;
    align-items: center;
  }
  .model-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.12em;
    color: var(--text-dim);
    text-transform: uppercase;
  }
  .model-row .prov-select,
  .model-row .model-select {
    grid-column: auto; grid-row: auto;
    min-width: 0; max-width: none; width: 100%;
  }
  .model-custom {
    grid-column: 2 / 4;
    padding: 6px 10px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-elev);
    color: var(--cyan);
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    width: 100%;
    box-sizing: border-box;
  }
  .model-custom:focus { outline: none; border-color: var(--cyan-dim); }
  .model-hint {
    margin-top: 8px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--text-dim);
    letter-spacing: 0.05em;
    opacity: 0.7;
  }

  /* APPLIANCES / ENERGY PROFILE */
  .appliance-intro {
    font-family: var(--font-mono); font-size: 9px; color: var(--text-dim);
    letter-spacing: 0.04em; opacity: 0.8; margin-bottom: 8px; line-height: 1.5;
  }
  .appliance-list { display: flex; flex-direction: column; gap: 8px; }
  .appliance-empty {
    font-family: var(--font-mono); font-size: 10px; color: var(--text-dim);
    opacity: 0.7; padding: 6px 2px;
  }
  .appliance-row {
    border: 1px solid rgba(0, 242, 254, 0.12);
    border-radius: var(--radius);
    background: rgba(0, 242, 254, 0.02);
    padding: 7px 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .ar-line1 { display: flex; gap: 6px; align-items: center; }
  .ar-line1 .appliance-name { flex: 1 1 auto; min-width: 0; }
  .ar-line2 {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) 56px;
    gap: 6px;
    align-items: center;
  }
  .appliance-row input, .appliance-row select {
    background: rgba(0, 242, 254, 0.04);
    border: 1px solid rgba(0, 242, 254, 0.2);
    color: var(--text); font-family: var(--font-mono); font-size: 10px;
    padding: 5px 6px; border-radius: 4px; min-width: 0; width: 100%;
    box-sizing: border-box;
  }
  .appliance-row input:focus, .appliance-row select:focus {
    outline: none; border-color: var(--cyan);
  }
  .appliance-learned {
    font-family: var(--font-mono); font-size: 8px; color: var(--cyan);
    opacity: 0.75; letter-spacing: 0.05em;
  }
  .appliance-remove {
    flex: 0 0 auto;
    width: 26px; height: 26px;
    padding: 0; font-size: 11px; line-height: 1;
    display: flex; align-items: center; justify-content: center;
    background: transparent; border: 1px solid rgba(255, 90, 90, 0.3);
    color: #ff8a8a; border-radius: 4px; cursor: pointer;
  }
  .appliance-remove:hover { border-color: #ff5a5a; background: rgba(255, 90, 90, 0.08); }
  .appliance-actions { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .appliance-actions .btn.primary {
    border-color: var(--cyan); color: var(--cyan);
  }
  .appliance-unknown {
    display: flex; align-items: center; gap: 8px; margin-top: 10px;
    font-family: var(--font-mono); font-size: 9px; color: var(--text-dim);
    letter-spacing: 0.04em; cursor: pointer;
  }
  .appliance-unknown input { accent-color: var(--cyan); flex: 0 0 auto; }

  .rule-list { display: flex; flex-direction: column; gap: 4px; }
  .rule-row {
    display: grid; grid-template-columns: 1fr auto;
    grid-template-rows: auto auto;
    padding: 8px 12px;
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    gap: 2px 10px;
    transition: border-color 0.2s;
  }
  .rule-row:hover { border-color: var(--line-hot); }
  .rule-name {
    font-size: 11px; font-weight: 500;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--text); grid-column: 1; grid-row: 1;
  }
  .rule-desc {
    font-size: 9px; color: var(--text-dim);
    letter-spacing: 0.08em; grid-column: 1; grid-row: 2;
  }
  .rule-toggle {
    grid-column: 2; grid-row: 1 / 3;
    padding: 4px 10px;
    font-size: 10px;
    min-width: 42px;
    border-radius: var(--radius);
  }

  /* PAIRING ROWS */
  .pairing-list { display: flex; flex-direction: column; gap: 6px; }
  .pairing-row {
    display: grid; grid-template-columns: 1fr auto;
    align-items: center;
    padding: 8px 12px;
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    gap: 8px;
    transition: border-color 0.2s;
  }
  .pairing-row:hover { border-color: var(--line-hot); }
  .pairing-label {
    font-size: 11px; font-weight: 500;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--text);
  }
  .sat-pair-select {
    min-width: 160px; max-width: 220px;
  }

  .diag-row {
    display: grid; grid-template-columns: 1fr auto;
    align-items: center;
    padding: 10px 12px;
    background: var(--bg-elev);
    border: 1px dashed var(--line);
    border-radius: var(--radius);
    margin-bottom: 6px; gap: 12px;
    transition: border-color 0.2s;
  }
  .diag-row:hover { border-color: var(--line-hot); }

  /* SUGGESTIONS */
  .sug-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .sug {
    background: rgba(0, 245, 160, 0.025);
    border: 1px solid rgba(0, 245, 160, 0.18);
    border-radius: 6px; padding: 8px 9px;
    transition: opacity 0.3s;
  }
  .sug-desc { font-size: 11px; color: var(--text); line-height: 1.45; }
  .sug-meta { display: flex; align-items: center; gap: 7px; margin-top: 7px; }
  .sug-conf {
    flex: 1; height: 3px; background: rgba(0, 245, 160, 0.12);
    border-radius: 2px; overflow: hidden;
  }
  .sug-conf i { display: block; height: 100%; background: var(--green); box-shadow: 0 0 6px rgba(0,245,160,0.6); }
  .sug-pct { font-family: var(--font-mono); font-size: 9px; color: var(--text-dim); white-space: nowrap; }
  .sug-btn {
    font-family: var(--font-mono); font-size: 9px; line-height: 1;
    padding: 4px 7px; border-radius: 4px; cursor: pointer;
    background: transparent; border: 1px solid var(--line); color: var(--text-dim);
  }
  .sug-btn:hover { border-color: var(--line-hot); color: var(--text); }
  .sug-approve { border-color: rgba(0,245,160,0.35); color: var(--green); }
  .sug-approve:hover { border-color: var(--green); color: var(--green); }
  .sug-dismiss { border-color: rgba(255,77,109,0.3); color: #ff8a9d; }
  .sug-dismiss:hover { border-color: var(--red); color: var(--red); }
  .sug-yaml {
    margin: 8px 0 0; padding: 8px; max-height: 160px; overflow: auto;
    background: rgba(4, 8, 14, 0.7); border: 1px solid var(--line);
    border-radius: 4px; font-family: var(--font-mono); font-size: 9.5px;
    color: var(--cyan-dim); white-space: pre-wrap; word-break: break-word;
    user-select: text;
  }

  /* LOCAL LLM URL */
  .llm-url-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
  .llm-url-label {
    font-family: var(--font-mono); font-size: 9px; color: var(--text-dim);
    letter-spacing: 0.1em; white-space: nowrap;
  }
  .llm-url-input {
    flex: 1; min-width: 0;
    background: rgba(0, 242, 254, 0.04);
    border: 1px solid rgba(0, 242, 254, 0.2); color: var(--text);
    font-family: var(--font-mono); font-size: 10px; padding: 6px 8px; border-radius: 4px;
  }
  .llm-url-input:focus { outline: none; border-color: var(--cyan); }

  /* DOORBELL TRAINING */
  .dbt-intro {
    font-family: var(--font-mono); font-size: 11px; color: var(--text-dim);
    line-height: 1.5; margin-bottom: 12px; opacity: 0.85;
  }
  .dbt-controls { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
  .dbt-scan {
    border-color: var(--cyan); color: var(--cyan); white-space: nowrap;
  }
  .dbt-limit {
    width: 64px; background: rgba(0,242,254,0.04);
    border: 1px solid rgba(0,242,254,0.2); color: var(--text);
    font-family: var(--font-mono); font-size: 11px; padding: 6px 8px; border-radius: 4px;
  }
  .dbt-stat {
    font-family: var(--font-mono); font-size: 11px; color: var(--text-dim);
    letter-spacing: 0.04em;
  }
  .dbt-list { display: flex; flex-direction: column; gap: 5px; max-height: 320px; overflow-y: auto; }
  .dbt-empty {
    font-family: var(--font-mono); font-size: 11px; color: var(--text-dim);
    opacity: 0.7; padding: 14px 6px; text-align: center;
  }
  .dbt-row {
    display: grid; grid-template-columns: 84px 78px auto 1fr; gap: 8px;
    align-items: baseline; padding: 7px 9px;
    background: var(--bg-elev); border: 1px solid var(--line);
    border-left: 2px solid rgba(0,242,254,0.3);
    border-radius: 4px; font-family: var(--font-mono); font-size: 11px;
  }
  .dbt-row.dbt-notable { border-left-color: var(--amber, #ffb300); }
  .dbt-ts { color: var(--text-dim); white-space: nowrap; }
  .dbt-src {
    text-transform: uppercase; font-size: 9px; letter-spacing: 0.08em;
    padding: 2px 5px; border-radius: 3px; text-align: center; white-space: nowrap;
    color: var(--cyan); border: 1px solid rgba(0,242,254,0.3);
  }
  .dbt-src-backlog { color: #b48cff; border-color: rgba(180,140,255,0.4); }
  .dbt-src-eventmedia { color: #ffb300; border-color: rgba(255,179,0,0.4); }
  .dbt-cat {
    color: var(--text-dim); text-transform: uppercase; font-size: 9px;
    letter-spacing: 0.06em; align-self: center; white-space: nowrap;
  }
  .dbt-desc { color: var(--text); line-height: 1.4; }

  /* FLOOR PLAN EDITOR */
  .fp-editor { display: flex; flex-direction: column; gap: 4px; }
  .fp-editor-canvas { min-height: 400px; }
  #fp-editor-svg { min-height: 380px; }
  .diag-row .label {
    font-size: 11px; color: var(--text-dim);
    letter-spacing: 0.15em; text-transform: uppercase;
  }
  .diag-row .btn {
    padding: 6px 12px;
    border: 1px solid var(--cyan-dim);
    border-radius: var(--radius);
    color: var(--cyan);
    background: transparent;
    font-family: var(--font-mono);
    font-size: 10px; letter-spacing: 0.2em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.25s;
  }
  .diag-row .btn:hover {
    background: rgba(0, 242, 254, 0.1);
    box-shadow: 0 0 12px var(--cyan-glow);
    transform: translateY(-1px);
  }

  /* FOOTER */
  .footer {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 24px;
    padding: 10px 20px;
    border-top: 1px solid var(--line);
    border-radius: 0 0 var(--radius-lg) var(--radius-lg);
    font-family: var(--font-mono);
    font-size: 10px; letter-spacing: 0.2em;
    color: var(--text-dim);
    text-transform: uppercase;
    background: linear-gradient(to right, rgba(5, 7, 9, 0.5), transparent, rgba(5, 7, 9, 0.5));
  }
  .footer .mid { text-align: center; color: var(--cyan-dim); }
  .footer .hl { color: var(--cyan); }

  /* TOASTS */
  .toast-wrap {
    position: fixed;
    bottom: 20px; right: 20px;
    z-index: 9999;
    display: flex; flex-direction: column; gap: 8px;
    align-items: flex-end;
  }
  .toast {
    padding: 10px 16px;
    background: var(--bg-elev);
    border: 1px solid var(--cyan-dim);
    border-radius: var(--radius);
    color: var(--cyan);
    font-family: var(--font-mono);
    font-size: 12px; letter-spacing: 0.1em;
    box-shadow: 0 0 20px var(--cyan-glow);
    transition: opacity 0.3s, transform 0.3s;
    backdrop-filter: blur(8px);
  }
  .toast.err { border-color: var(--red); color: var(--red); box-shadow: 0 0 20px rgba(255,59,59,0.4); }
  .toast.out { opacity: 0; transform: translateX(20px); }

  /* MOBILE */
  @media (max-width: 900px) {
    .menu-btn { display: flex; }
    .app { padding: 12px; gap: 12px; }
    .grid { grid-template-columns: 1fr; }
    .masthead {
      grid-template-columns: auto 1fr auto;
      gap: 10px;
      padding: 10px 12px;
    }
    .brand { font-size: 14px; letter-spacing: 0.25em; }
    .greeting { font-size: 11px; letter-spacing: 0.15em; }
    .clock .time { font-size: 18px; }
    .clock .date { display: none; }
    .room-name { font-size: 28px; }
    .anchor { padding: 20px 16px; min-height: 280px; }
    .anchor::before { width: 320px; height: 320px; }
    .anchor::after  { width: 200px; height: 200px; animation: none; }
    .reactor { width: 56px; height: 56px; margin-bottom: 18px; }
    .settings-grid { grid-template-columns: 1fr; }
    .areas { grid-template-columns: repeat(2, 1fr); }
    .log { max-height: 320px; }
    .footer { grid-template-columns: 1fr; text-align: center; gap: 4px; padding: 10px; }
    .footer .mid, .footer > div:last-child { display: none; }
  }
</style>
    `;
  }
}

// Register the custom element — must match webcomponent_name in panel registration
if (!customElements.get("jarvis-panel")) {
  customElements.define("jarvis-panel", JarvisPanel);
}

console.info(
  "%c JARVIS Panel %c v6.7.0 ",
  "color: #00f2fe; background: #050709; padding: 2px 6px;",
  "color: #567685; background: #0a0d12; padding: 2px 6px;"
);
