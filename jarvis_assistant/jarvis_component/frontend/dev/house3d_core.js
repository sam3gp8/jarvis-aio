/* JARVIS Residence — 3D house core (v3 rebuild)
 * Real dimensions from the architect's ApexSketch; labels/layout from the JARVIS editor.
 * Pure-geometry axonometric projection rendered to SVG so it is (a) rotatable in the
 * browser and (b) rasterizable here via cairosvg for verification. Same math both places.
 * Works under Node (module.exports) and in the browser (window.JARVIS3D).
 */
(function (root) {
  'use strict';

  // ---------- real dimensions (feet) ----------
  var GW = 30, HW = 33, D = 24;                 // garage W, house W, depth
  var XG0 = 0, XGH = GW, XHE = GW + HW;         // garage 0..30, house 30..63
  var WALL = 9;                                 // 1st-floor wall height = main eave
  var BASE_RISE = 11, GBASE_RISE = 5;           // roof rises at pitch 1.0
  var RISE = BASE_RISE, RIDGE = WALL + RISE;    // main roof: eave 9 -> ridge 20
  var GWALL = 9, GRISE = GBASE_RISE, GRIDGE = GWALL + GRISE; // garage roof: eave 9 -> ridge 14
  var RY = D / 2;                               // ridge centerline (depth) = 12
  var OVH = 1.2;                                // roof overhang
  var SCALE = 8.6;                              // feet -> px
  var PITCH = 30 * Math.PI / 180;               // camera elevation
  var CENTER = [(XG0 + XHE) / 2, RY, WALL * 0.5]; // rotate about model center

  // ---------- per-render home spec (type/specs); fields left unset = approved default ----------
  // garageBays, dormersFront, dormersRear: counts · chimney: 'right'|'left'|'none' · pitch: roof-rise scale
  var SPEC = {};
  function applySpec(s) {
    SPEC = s || {};
    var p = SPEC.pitch > 0 ? SPEC.pitch : 1;
    RISE = BASE_RISE * p; RIDGE = WALL + RISE;
    GRISE = GBASE_RISE * p; GRIDGE = GWALL + GRISE;
  }

  // ---------- palette (JARVIS dark-cyan HUD) ----------
  var C = {
    wallF: 'rgba(22,46,66,0.34)', wallS: 'rgba(0,242,254,0.5)',
    wallDk:'rgba(14,32,48,0.40)', wallSdk:'rgba(0,242,254,0.34)',
    roofF: 'rgba(10,24,36,0.94)', roofS: 'rgba(0,206,247,0.5)',
    roofDk:'rgba(6,17,27,0.96)',  roofSdk:'rgba(0,206,247,0.3)',
    gableF:'rgba(18,38,56,0.6)',  gableS:'rgba(0,242,254,0.46)',
    chimF: 'rgba(13,28,42,0.97)', chimS:'rgba(0,242,254,0.42)',
    doorOff:'rgba(0,242,254,0.10)', doorOn:'rgba(0,242,254,0.30)', doorS:'rgba(0,242,254,0.6)',
    doorOpen:'rgba(255,170,40,0.32)', doorOpenS:'rgba(255,190,72,0.95)', doorOpenGlow:'rgba(255,170,40,0.42)',
    winOff:'rgba(0,242,254,0.07)', winOn:'rgba(0,242,254,0.72)', winDom:'rgba(0,245,160,0.82)',
    glassOff:'rgba(0,242,254,0.32)', glassOn:'rgba(120,240,255,0.92)', glassDom:'rgba(150,255,210,0.95)',
    edge:'rgba(0,242,254,0.5)', dim:'rgba(0,242,254,0.26)', faint:'rgba(0,242,254,0.13)',
    glowOn:'rgba(0,242,254,0.5)', glowDom:'rgba(0,245,160,0.55)'
  };

  // ---------- projection (turntable axonometric, orthographic) ----------
  function rot(p, t) {
    var x = p[0] - CENTER[0], y = p[1] - CENTER[1], z = p[2] - CENTER[2];
    var c = Math.cos(t), s = Math.sin(t);
    return [x * c + y * s, -x * s + y * c, z]; // rotated (rx, ry, z)
  }
  function project(p, thetaDeg) {
    var r = rot(p, thetaDeg * Math.PI / 180);
    return [r[0] * SCALE, -(r[1] * Math.sin(PITCH) + r[2] * Math.cos(PITCH)) * SCALE];
  }
  function faceDepth(face, thetaDeg) {
    var t = thetaDeg * Math.PI / 180, cy = 0, cz = 0, n = face.p.length, i, r;
    for (i = 0; i < n; i++) { r = rot(face.p[i], t); cy += r[1]; cz += r[2]; }
    cy /= n; cz /= n;
    return cy * Math.cos(PITCH) - cz * Math.sin(PITCH); // larger = farther from camera
  }

  // ---------- face helpers ----------
  function F(list, pts, fill, stroke, sw, extra) {
    var o = { p: pts, f: fill, s: stroke || C.edge, w: (sw == null ? 0.9 : sw) };
    if (extra) for (var k in extra) o[k] = extra[k];
    list.push(o);
  }
  // quad on a vertical plane y=const (a wall facing front/back)
  function wallY(L, y, x0, x1, z0, z1, f, s, w) { F(L, [[x0,y,z0],[x1,y,z0],[x1,y,z1],[x0,y,z1]], f, s, w); }
  // gable end on plane x=const: wall rect + triangle to ridge
  function gableEnd(L, x, yA, yB, zWall, yPk, zPk, f, s, w) {
    F(L, [[x,yA,0],[x,yB,0],[x,yB,zWall],[x,yPk,zPk],[x,yA,zWall]], f, s, w);
  }

  // ---------- a lit window on the front/back plane (y=const) ----------
  function winY(L, GL, y, x0, x1, z0, z1, state, mull, faceOut, cols) {
    var f = state === 'dom' ? C.winDom : state === 'on' ? C.winOn : C.winOff;
    var st = state === 'dom' ? C.glassDom : state === 'on' ? C.glassOn : C.glassOff;
    var n = faceOut == null ? -0.06 : faceOut;
    var yy = y + n;
    F(L, [[x0,yy,z0],[x1,yy,z0],[x1,yy,z1],[x0,yy,z1]], f, st, 0.7);
    if (mull) {
      F(L, [[x0,yy,(z0+z1)/2],[x1,yy,(z0+z1)/2]], 'none', st, 0.4);
      var nc = cols || 2, k;
      for (k = 1; k < nc; k++) { var xm = x0 + (x1 - x0) * k / nc; F(L, [[xm,yy,z0],[xm,yy,z1]], 'none', st, 0.4); }
    }
    if (state !== 'off' && GL) {
      var g = state === 'dom' ? C.glowDom : C.glowOn;
      GL.push({ p: [[x0-1.4,yy,z0-1.4],[x1+1.4,yy,z0-1.4],[x1+1.4,yy,z1+1.4],[x0-1.4,yy,z1+1.4]], f: g });
    }
  }
  // window on the end plane (x=const)
  function winX(L, GL, x, y0, y1, z0, z1, state, mull, faceOut) {
    var f = state === 'dom' ? C.winDom : state === 'on' ? C.winOn : C.winOff;
    var st = state === 'dom' ? C.glassDom : state === 'on' ? C.glassOn : C.glassOff;
    var n = faceOut == null ? 0.06 : faceOut;
    var xx = x + n;
    F(L, [[xx,y0,z0],[xx,y1,z0],[xx,y1,z1],[xx,y0,z1]], f, st, 0.7);
    if (mull) {
      F(L, [[xx,y0,(z0+z1)/2],[xx,y1,(z0+z1)/2]], 'none', st, 0.4);
      F(L, [[xx,(y0+y1)/2,z0],[xx,(y0+y1)/2,z1]], 'none', st, 0.4);
    }
    if (state !== 'off' && GL) {
      var g = state === 'dom' ? C.glowDom : C.glowOn;
      GL.push({ p: [[xx,y0-1.4,z0-1.4],[xx,y1+1.4,z0-1.4],[xx,y1+1.4,z1+1.4],[xx,y0-1.4,z1+1.4]], f: g });
    }
  }

  // ---------- a door on a front/back plane (y=const). hinge 'left'|'right'; ----------
  // state 'open' → swings out (amber + glow), else flush cyan-dim. faceOut sets the side.
  function doorY(L, GL, y, x0, x1, z0, z1, faceOut, hinge, state) {
    var n = faceOut, yy = y + n;
    if (state !== 'open') {
      F(L, [[x0,yy,z0],[x1,yy,z0],[x1,yy,z1],[x0,yy,z1]], C.doorOff, C.doorS, 0.8, { cls: 'door' });
      return;
    }
    var w = x1 - x0, ang = 66 * Math.PI / 180, dir = n >= 0 ? 1 : -1;
    var dx = w * Math.cos(ang), dy = dir * w * Math.sin(ang);
    var hx = hinge === 'right' ? x1 : x0;
    var fx = hinge === 'right' ? x1 - dx : x0 + dx;
    var fy = yy + dy;
    if (GL) GL.push({ p: [[hx,yy,z0],[fx,fy,z0],[fx,fy,z1],[hx,yy,z1]], f: C.doorOpenGlow });
    F(L, [[x0,yy,z0],[x1,yy,z0],[x1,yy,z1],[x0,yy,z1]], 'rgba(2,8,14,0.92)', C.doorOpenS, 0.45);   // dark opening
    F(L, [[hx,yy,z0],[fx,fy,z0],[fx,fy,z1],[hx,yy,z1]], C.doorOpen, C.doorOpenS, 0.9, { cls: 'door door-open' }); // swung leaf
  }
  // ---------- a slanted cellar bulkhead at the base of the rear wall ----------
  function bulkhead(L, GL, x0, x1, state) {
    var open = state === 'open', yTop = D, zTop = 3.0, yBot = D + 2.6, xm = (x0 + x1) / 2;
    F(L, [[x0,yTop,0],[x0,yTop,zTop],[x0,yBot,0]], 'rgba(8,19,29,0.92)', C.dim, 0.5);   // left cheek
    F(L, [[x1,yTop,0],[x1,yTop,zTop],[x1,yBot,0]], 'rgba(8,19,29,0.92)', C.dim, 0.5);   // right cheek
    if (!open) {
      F(L, [[x0,yTop,zTop],[x1,yTop,zTop],[x1,yBot,0],[x0,yBot,0]], 'rgba(11,26,38,0.95)', C.doorS, 0.8, { cls: 'door' });
      F(L, [[xm,yTop,zTop],[xm,yBot,0]], 'none', C.doorS, 0.4);   // center seam
    } else {
      if (GL) GL.push({ p: [[x0,yTop,zTop],[x1,yTop,zTop],[x1,yTop,zTop+3.4],[x0,yTop,zTop+3.4]], f: C.doorOpenGlow });
      F(L, [[x0,yTop,zTop],[x1,yTop,zTop],[x1,yBot,0],[x0,yBot,0]], 'rgba(2,8,14,0.95)', C.doorOpenS, 0.5);  // hole into ground
      F(L, [[x0,yTop,zTop],[x1,yTop,zTop],[x1,yTop,zTop+3.4],[x0,yTop,zTop+3.4]], C.doorOpen, C.doorOpenS, 0.85, { cls: 'door door-open' }); // raised leaves
    }
  }

  function dormerFront(L, GL, cx, state) {
    var w = 6, yF = 1.6, zSill = WALL + 2.2, zHead = WALL + 6.2, zPk = WALL + 8.2, yBack = 6.2;
    var wf = 'rgba(14,30,44,0.96)', rf = 'rgba(8,19,29,0.97)', es = C.roofSdk;
    // side walls (triang│ following slope back into roof)
    F(L, [[cx-w/2,yF,zSill],[cx-w/2,yF,zHead],[cx-w/2,yBack,WALL+RISE*(1-(yBack)/RY)]], wf, es, 0.55);
    F(L, [[cx+w/2,yF,zSill],[cx+w/2,yF,zHead],[cx+w/2,yBack,WALL+RISE*(1-(yBack)/RY)]], wf, es, 0.55);
    // little gable roof (two slopes from the dormer peak back to the main slope)
    F(L, [[cx-w/2,yF,zHead],[cx,yF,zPk],[cx,yBack,WALL+RISE*(1-(yBack)/RY)+1.2],[cx-w/2,yBack,WALL+RISE*(1-(yBack)/RY)]], rf, es, 0.55);
    F(L, [[cx+w/2,yF,zHead],[cx,yF,zPk],[cx,yBack,WALL+RISE*(1-(yBack)/RY)+1.2],[cx+w/2,yBack,WALL+RISE*(1-(yBack)/RY)]], rf, es, 0.55);
    // front face (the bit that holds the window)
    F(L, [[cx-w/2,yF,zSill],[cx+w/2,yF,zSill],[cx+w/2,yF,zHead],[cx-w/2,yF,zHead]], wf, C.wallS, 0.7);
    F(L, [[cx-w/2,yF,zHead],[cx+w/2,yF,zHead],[cx,yF,zPk]], wf, C.wallS, 0.7);
    // window
    winY(L, GL, yF, cx-1.95, cx+1.95, zSill+0.4, zHead-0.4, state, true, -0.05);
  }
  // ---------- the rear dormer with a ROUND window (the upstairs bath) ----------
  function dormerRearRound(L, GL, cx, state) {
    var w = 7, yB = D - 1.6, zSill = WALL + 2.0, zHead = WALL + 6.6, zPk = WALL + 8.4, yFwd = D - 6.2;
    var wf = 'rgba(14,30,44,0.96)', rf = 'rgba(8,19,29,0.97)', es = C.roofSdk;
    var zSlope = function (yy) { return WALL + RISE * (1 - (D - yy) / RY); };
    F(L, [[cx-w/2,yB,zSill],[cx-w/2,yB,zHead],[cx-w/2,yFwd,zSlope(yFwd)]], wf, es, 0.55);
    F(L, [[cx+w/2,yB,zSill],[cx+w/2,yB,zHead],[cx+w/2,yFwd,zSlope(yFwd)]], wf, es, 0.55);
    F(L, [[cx-w/2,yB,zHead],[cx,yB,zPk],[cx,yFwd,zSlope(yFwd)+1.2],[cx-w/2,yFwd,zSlope(yFwd)]], rf, es, 0.55);
    F(L, [[cx+w/2,yB,zHead],[cx,yB,zPk],[cx,yFwd,zSlope(yFwd)+1.2],[cx+w/2,yFwd,zSlope(yFwd)]], rf, es, 0.55);
    F(L, [[cx-w/2,yB,zSill],[cx+w/2,yB,zSill],[cx+w/2,yB,zHead],[cx-w/2,yB,zHead]], wf, C.wallS, 0.7);
    F(L, [[cx-w/2,yB,zHead],[cx+w/2,yB,zHead],[cx,yB,zPk]], wf, C.wallS, 0.7);
    // round window approximated by an octagon on the y=yB plane
    var cz = (zSill + zHead) / 2 + 0.3, r = 1.7, pts = [], i, a;
    var f = state === 'dom' ? C.winDom : state === 'on' ? C.winOn : C.winOff;
    var stk = state === 'dom' ? C.glassDom : state === 'on' ? C.glassOn : C.glassOff;
    for (i = 0; i < 8; i++) { a = Math.PI / 8 + i * Math.PI / 4; pts.push([cx + r * Math.cos(a), yB - 0.05, cz + r * Math.sin(a)]); }
    F(L, pts, f, stk, 0.7);
    if (state !== 'off' && GL) GL.push({ p: [[cx-r-1.2,yB-0.05,cz-r-1.2],[cx+r+1.2,yB-0.05,cz-r-1.2],[cx+r+1.2,yB-0.05,cz+r+1.2],[cx-r-1.2,yB-0.05,cz+r+1.2]], f: state==='dom'?C.glowDom:C.glowOn });
  }

  // ---------- garage doors (count = SPEC.garageBays, default 3; fill the garage front) ----------
  function garageDoors(L, GL, state, openState) {
    var bays = SPEC.garageBays > 0 ? SPEC.garageBays : 3, gap = 1.8;
    var dw = (GW - gap * (bays + 1)) / bays, z0 = 0.4, z1 = 7.4, i, x0;
    var open = openState === 'open', lit = state === 'on' || state === 'dom';
    var f = open ? C.doorOpen : lit ? C.doorOn : C.doorOff;
    var s = open ? C.doorOpenS : lit ? C.glassOn : C.doorS;
    var cls = open ? 'gdoor door-open' : 'gdoor';
    for (i = 0; i < bays; i++) {
      x0 = gap + i * (dw + gap);
      F(L, [[x0,-0.06,z0],[x0+dw,-0.06,z0],[x0+dw,-0.06,z1],[x0,-0.06,z1]], f, s, 1.0, { cls: cls });
      for (var k = 1; k < 4; k++) { var zz = z0 + (z1 - z0) * k / 4; F(L, [[x0,-0.06,zz],[x0+dw,-0.06,zz]], 'none', s, 0.45); }
      if ((open || lit) && GL) GL.push({ p: [[x0-1.2,-0.06,z0],[x0+dw+1.2,-0.06,z0],[x0+dw+1.2,-0.06,z1+1.2],[x0-1.2,-0.06,z1+1.2]], f: open ? C.doorOpenGlow : C.glowOn });
    }
  }

  // ---------- BUILD: exterior shell + roof ----------
  // A roof slope drawn as fill strips (so a protruding dormer in front sorts correctly
  // per-strip instead of being swallowed by one big quad) plus a single crisp outline.
  function roofPlane(L, xL, xR, yE, zE, yR, zR, fill, stroke, sw) {
    var N = 12, i, xa, xb;
    for (i = 0; i < N; i++) {
      xa = xL + (xR - xL) * i / N; xb = xL + (xR - xL) * (i + 1) / N;
      F(L, [[xa,yE,zE],[xb,yE,zE],[xb,yR,zR],[xa,yR,zR]], fill, 'none', 0);
    }
    F(L, [[xL,yE,zE],[xR,yE,zE],[xR,yR,zR],[xL,yR,zR]], 'none', stroke, sw);
  }

  function buildShell(L, GL) {
    // garage walls
    wallY(L, 0, XG0, XGH, 0, GWALL, C.wallF, C.wallS, 0.85);            // garage front
    wallY(L, D, XG0, XGH, 0, GWALL, C.wallDk, C.wallSdk, 0.7);          // garage back
    gableEnd(L, XG0, 0, D, GWALL, RY, GRIDGE, C.gableF, C.gableS, 0.8); // garage left gable
    // house walls
    wallY(L, 0, XGH, XHE, 0, WALL, C.wallF, C.wallS, 0.85);             // house front
    wallY(L, D, XGH, XHE, 0, WALL, C.wallDk, C.wallSdk, 0.7);           // house back
    gableEnd(L, XHE, 0, D, WALL, RY, RIDGE, C.gableF, C.gableS, 0.85);  // house right gable (chimney end)
    gableEnd(L, XGH, 0, D, WALL, RY, RIDGE, C.gableF, C.gableSdk || C.gableS, 0.7); // house left gable (above garage)

    // garage roof (ridge ∥ house, lower)
    F(L, [[XG0-OVH,-OVH,GWALL],[XGH,-OVH,GWALL],[XGH,RY,GRIDGE],[XG0-OVH,RY,GRIDGE]], C.roofF, C.roofS, 0.85);  // front slope
    F(L, [[XG0-OVH,D+OVH,GWALL],[XGH,D+OVH,GWALL],[XGH,RY,GRIDGE],[XG0-OVH,RY,GRIDGE]], C.roofDk, C.roofSdk, 0.7); // back slope

    // main roof (strip-split so dormers in front sort correctly)
    roofPlane(L, XGH - OVH, XHE + OVH, -OVH, WALL, RY, RIDGE, C.roofF, C.roofS, 0.9);   // front slope
    roofPlane(L, XGH - OVH, XHE + OVH, D + OVH, WALL, RY, RIDGE, C.roofDk, C.roofSdk, 0.7); // back slope
  }

  function chimney(L, side) {
    if (side === 'none') return;
    var ya = 9.6, yb = 13.2, zt, x0, x1;
    if (side === 'left') { x0 = XG0; x1 = XG0 - 2.2; zt = GRIDGE + 4; }   // west gable (garage end)
    else { x0 = XHE; x1 = XHE + 2.2; zt = RIDGE + 4; }                    // default: east gable
    F(L, [[x1,ya,0],[x1,yb,0],[x1,yb,zt],[x1,ya,zt]], C.chimF, C.chimS, 0.7);      // outer
    F(L, [[x0,ya,0],[x1,ya,0],[x1,ya,zt],[x0,ya,zt]], 'rgba(8,19,29,0.97)', C.chimS, 0.6); // front side
    F(L, [[x0,yb,0],[x1,yb,0],[x1,yb,zt],[x0,yb,zt]], 'rgba(8,19,29,0.97)', C.dim, 0.5);    // back side
    F(L, [[x0,ya,zt],[x1,ya,zt],[x1,yb,zt],[x0,yb,zt]], 'rgba(0,242,254,0.08)', C.chimS, 0.5); // cap
  }

  // ---------- interior rooms (labels/layout from JARVIS editor; sizes from the plan) ----------
  // [x0, y0, w, d, label, occupancy-key]   (front y=0 .. rear y=24; garage 0..30, house 30..63)
  var ROOMS = {
    '1f': [
      [0, 0, 30, 24, 'GARAGE', 'garage'],
      [30, 0, 13, 11, 'DINING', 'dining room'],
      [43, 0, 20, 11, 'LIVING ROOM', 'living room'],
      [30, 13, 14, 11, 'KITCHEN', 'kitchen'],
      [49, 13, 14, 11, 'GUEST RM', 'guest room'],
      [44, 16.5, 5, 7.5, 'BATH', 'bath'],
      [43, 11, 20, 2, 'HALL', 'downstairs hallway'],
      [44, 3, 4, 8, 'STAIRS', 'stairs']
    ],
    '2f': [
      [31, 2, 15, 20, "ELIANA'S", "eliana's room"],
      [48, 2, 14, 20, 'MASTER', 'master bedroom'],
      [44, 16, 7, 8, 'BATH', 'bath'],
      [45, 11, 6, 5, 'U.HALL', 'upstairs hallway'],
      [45.5, 7, 4, 4, 'STAIRS', 'stairs']
    ],
    'b': [
      [30, 0, 33, 24, 'BASEMENT', 'basement']
    ]
  };
  var BSMT_ITEMS = [[34, 4, 'SUMP'], [34, 9.5, 'DEHUM'], [58, 9.5, 'ENERGY'], [58, 18, 'WASHER'], [46, 12, 'STAIRS']];
  var FLOOR_Z = { '1f': [0.4, 8.6], '2f': [9.0, 13.8], 'b': [-7, -0.6] };

  function roomBox(L, LBL, x0, y0, w, d, z0, z1, name, state) {
    var x1 = x0 + w, y1 = y0 + d, occ = state !== 'off';
    var ff = state === 'dom' ? 'rgba(0,245,160,0.15)' : occ ? 'rgba(0,242,254,0.13)' : 'rgba(0,242,254,0.035)';
    var ss = state === 'dom' ? 'rgba(130,255,205,0.9)' : occ ? 'rgba(0,242,254,0.62)' : 'rgba(0,242,254,0.24)';
    var sw = occ ? 1.0 : 0.6;
    var wf = state === 'dom' ? 'rgba(0,245,160,0.06)' : occ ? 'rgba(0,242,254,0.05)' : 'rgba(0,242,254,0.018)';
    F(L, [[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0]], ff, ss, sw * 0.7);            // floor
    F(L, [[x0,y0,z0],[x1,y0,z0],[x1,y0,z1],[x0,y0,z1]], wf, ss, sw * 0.5);
    F(L, [[x0,y1,z0],[x1,y1,z0],[x1,y1,z1],[x0,y1,z1]], wf, ss, sw * 0.5);
    F(L, [[x0,y0,z0],[x0,y1,z0],[x0,y1,z1],[x0,y0,z1]], wf, ss, sw * 0.5);
    F(L, [[x1,y0,z0],[x1,y1,z0],[x1,y1,z1],[x1,y0,z1]], wf, ss, sw * 0.5);
    LBL.push({ x: (x0 + x1) / 2, y: (y0 + y1) / 2, z: z0 + 0.2, t: name, st: state, big: w > 14 });
    if (occ) LBL.push({ x: (x0 + x1) / 2, y: (y0 + y1) / 2, z: z1 - 0.5, st: state, dot: true });
  }

  // interior door on an x=const wall (shown on the floor-plan views)
  function intDoorX(L, x, y0, y1, z0, z1, state) {
    if (state === 'open') {
      var w = y1 - y0, ang = 58 * Math.PI / 180;
      var fy = y0 + w * Math.cos(ang), fx = x + w * Math.sin(ang);   // swing into the kitchen (+x)
      F(L, [[x,y0,z0],[fx,fy,z0],[fx,fy,z1],[x,y0,z1]], C.doorOpen, C.doorOpenS, 0.8, { cls: 'door door-open' });
    } else {
      F(L, [[x,y0,z0],[x,y1,z0],[x,y1,z1],[x,y0,z1]], 'rgba(0,242,254,0.14)', C.doorS, 0.7, { cls: 'door' });
    }
  }
  // interior door on a y=const wall (e.g. the basement door in the rear foundation wall)
  function intDoorY(L, y, x0, x1, z0, z1, faceOut, hinge, state) {
    var n = faceOut == null ? -0.06 : faceOut, yy = y + n;
    if (state !== 'open') {
      F(L, [[x0,yy,z0],[x1,yy,z0],[x1,yy,z1],[x0,yy,z1]], 'rgba(0,242,254,0.14)', C.doorS, 0.7, { cls: 'door' });
      return;
    }
    var w = x1 - x0, ang = 58 * Math.PI / 180, dir = n >= 0 ? 1 : -1;
    var dx = w * Math.cos(ang), dy = dir * w * Math.sin(ang);
    var hx = hinge === 'right' ? x1 : x0, fx = hinge === 'right' ? x1 - dx : x0 + dx, fy = yy + dy;
    F(L, [[hx,yy,z0],[fx,fy,z0],[fx,fy,z1],[hx,yy,z1]], C.doorOpen, C.doorOpenS, 0.8, { cls: 'door door-open' });
  }

  function buildRooms(floor, lit, doors, L, LBL) {
    var stOf = function (n) { var s = lit[String(n).toLowerCase()]; return s === 'dom' ? 'dom' : s ? 'on' : 'off'; };
    var dOf = function (k) { return doors && doors[k] === 'open' ? 'open' : 'closed'; };
    var z = FLOOR_Z[floor] || FLOOR_Z['1f'];
    (ROOMS[floor] || []).forEach(function (r) { roomBox(L, LBL, r[0], r[1], r[2], r[3], z[0], z[1], r[4], stOf(r[5])); });
    if (floor === '1f') intDoorX(L, XGH, 19.5, 22.5, z[0], z[0] + 6.5, dOf('kitchen_garage')); // kitchen ↔ garage
    if (floor === 'b') {
      intDoorY(L, D, 33.5, 38.5, z[0] + 0.3, z[1] - 0.1, -0.06, 'left', dOf('basement'));      // basement door (foot of the cellar stairs, inline w/ the bulkhead above)
      BSMT_ITEMS.forEach(function (it) { LBL.push({ x: it[0], y: it[1], z: z[1] - 0.3, t: it[2], st: 'off', small: true }); });
    }
  }

  function buildContext(L, floor) {
    var fe = 'rgba(0,242,254,0.13)';
    F(L, [[XG0,0,0],[XHE,0,0],[XHE,D,0],[XG0,D,0]], 'none', fe, 0.5);   // footprint
    F(L, [[XGH,0,0],[XGH,D,0]], 'none', fe, 0.4);                       // garage/house split
    if (floor === '2f') {
      var rw = 'rgba(0,242,254,0.10)';
      F(L, [[XGH,-OVH,WALL],[XHE,-OVH,WALL],[XHE,RY,RIDGE],[XGH,RY,RIDGE]], 'none', rw, 0.4);
      F(L, [[XGH,D+OVH,WALL],[XHE,D+OVH,WALL],[XHE,RY,RIDGE],[XGH,RY,RIDGE]], 'none', rw, 0.4);
      F(L, [[XGH,RY,RIDGE],[XHE,RY,RIDGE]], 'none', 'rgba(0,242,254,0.16)', 0.5);
    }
  }

  // ---------- assemble a frame for given options ----------
  function build(opts) {
    opts = opts || {};
    applySpec(opts.spec);                           // home type/specs (empty = default)
    var lit = opts.lit || {};                       // { 'master bedroom':'on'|'dom', ... }
    var doors = opts.doors || {};                   // { front:'open'|'closed', garage:..., cellar:..., ... }
    var floor = opts.floor || 'all';
    var stOf = function (name) { var s = lit[String(name).toLowerCase()]; return s === 'dom' ? 'dom' : s ? 'on' : 'off'; };
    var dOf = function (k) { return doors[k] === 'open' ? 'open' : 'closed'; };
    var L = [], GL = [], LBL = [];

    if (floor !== 'all') {
      // floor isolation: faint shell context + translucent labeled rooms for this level
      buildContext(L, floor);
      buildRooms(floor, lit, doors, L, LBL);
      return { faces: L, glow: GL, labels: LBL };
    }

    buildShell(L, GL);
    chimney(L, SPEC.chimney);
    garageDoors(L, GL, stOf('garage'), dOf('garage'));

    // dormers — counts configurable; unset = the approved default layout
    if (SPEC.dormersFront == null) {
      dormerFront(L, GL, XGH + 9, stOf("eliana's room"));
      dormerFront(L, GL, XGH + 24, stOf('master bedroom'));
    } else {
      var fKeys = ["eliana's room", 'master bedroom'], df;
      for (df = 0; df < SPEC.dormersFront; df++)
        dormerFront(L, GL, XGH + HW * (df + 1) / (SPEC.dormersFront + 1), stOf(fKeys[df] || fKeys[fKeys.length - 1]));
    }
    if (SPEC.dormersRear == null) {
      dormerRearRound(L, GL, XGH + 16, stOf('bath'));
    } else {
      var dr;
      for (dr = 0; dr < SPEC.dormersRear; dr++)
        dormerRearRound(L, GL, XGH + HW * (dr + 1) / (SPEC.dormersRear + 1), stOf('bath'));
    }

    // front facade: Dining (one window, L) · front door (centered) · Living Room (one window, R) — matching pair
    winY(L, GL, 0, XGH + 4, XGH + 8.5, 3, 7, stOf('dining room'), true);              // dining window
    doorY(L, GL, 0, XGH + 14.5, XGH + 17.5, 0, 7, -0.06, 'left', dOf('front'));        // front entry (centered)
    winY(L, GL, 0, XGH + 23.5, XGH + 28, 3, 7, stOf('living room'), true);            // living-room window (matches dining)
    // right (east) gable corners: Living Rm front (SE), Guest Rm rear (NE) — flank the chimney
    winX(L, GL, XHE, 3.5, 7.5, 3, 7, stOf('living room'), true);
    winX(L, GL, XHE, 16.5, 20.5, 3, 7, stOf('guest room'), true);
    // rear (north) facade: Kitchen (NW) · Guest (NE) windows · garage man-door · cellar bulkhead
    winY(L, GL, D, XGH + 3, XGH + 9, 3, 7, stOf('kitchen'), true, 0.06);
    winY(L, GL, D, XGH + 22, XGH + 28, 3, 7, stOf('guest room'), true, 0.06);
    doorY(L, GL, D, 25.2, 28.2, 0, 6.8, 0.06, 'right', dOf('garage_rear'));            // garage rear man-door (~3ft W of junction)
    bulkhead(L, GL, 33.5, 38.5, dOf('cellar'));                                        // cellar door under the kitchen window

    return { faces: L, glow: GL, labels: LBL };
  }

  // ---------- render to SVG ----------
  function renderSVG(opts) {
    opts = opts || {};
    var theta = opts.theta == null ? 35 : opts.theta;
    var built = build(opts);
    var faces = built.faces, glow = built.glow, labels = built.labels || [];
    // depth sort: farthest first
    faces.sort(function (a, b) { return faceDepth(b, theta) - faceDepth(a, theta); });

    // bounds
    var mnx = 1e9, mny = 1e9, mxx = -1e9, mxy = -1e9, all = faces.concat(glow), i, j, q;
    for (i = 0; i < all.length; i++) for (j = 0; j < all[i].p.length; j++) {
      q = project(all[i].p[j], theta);
      if (q[0] < mnx) mnx = q[0]; if (q[0] > mxx) mxx = q[0];
      if (q[1] < mny) mny = q[1]; if (q[1] > mxy) mxy = q[1];
    }
    var pad = 40, X0, Y0, W, H;
    if (opts.box) { X0 = opts.box[0]; Y0 = opts.box[1]; W = opts.box[2]; H = opts.box[3]; }
    else { X0 = mnx - pad; Y0 = mny - pad; W = (mxx - mnx) + 2 * pad; H = (mxy - mny) + 2 * pad; }
    var vb = X0.toFixed(1) + ' ' + Y0.toFixed(1) + ' ' + W.toFixed(1) + ' ' + H.toFixed(1);
    var pp = function (pts) { return pts.map(function (p) { var s = project(p, theta); return s[0].toFixed(1) + ',' + s[1].toFixed(1); }).join(' '); };

    var body = '';
    body += '<defs><filter id="g3" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>'
         + '<radialGradient id="bg3" cx="50%" cy="40%" r="65%"><stop offset="0%" stop-color="rgba(0,60,90,0.20)"/><stop offset="100%" stop-color="rgba(0,0,0,0)"/></radialGradient>'
         + '<radialGradient id="sh3" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="rgba(0,0,0,0.55)"/><stop offset="100%" stop-color="rgba(0,0,0,0)"/></radialGradient></defs>';
    body += '<rect x="' + X0.toFixed(1) + '" y="' + Y0.toFixed(1) + '" width="' + W.toFixed(1) + '" height="' + H.toFixed(1) + '" fill="url(#bg3)"/>';
    // ground shadow
    var gc = project([CENTER[0], CENTER[1], 0], theta);
    body += '<ellipse cx="' + gc[0].toFixed(1) + '" cy="' + (mxy + pad * 0.2).toFixed(1) + '" rx="' + ((mxx - mnx) * 0.42).toFixed(1) + '" ry="20" fill="url(#sh3)"/>';
    // glow
    for (i = 0; i < glow.length; i++) body += '<polygon points="' + pp(glow[i].p) + '" fill="' + glow[i].f + '" filter="url(#g3)"/>';
    // faces
    for (i = 0; i < faces.length; i++) {
      var fc = faces[i], closed = fc.f !== 'none';
      body += '<poly' + (closed ? 'gon' : 'line') + ' points="' + pp(fc.p) + '"' + (fc.cls ? ' class="' + fc.cls + '"' : '')
            + ' fill="' + (closed ? fc.f : 'none') + '" stroke="' + fc.s + '" stroke-width="' + fc.w + '" stroke-linejoin="round" stroke-linecap="round"/>';
    }
    // room labels + occupancy pulses (upright, drawn on top)
    for (i = 0; i < labels.length; i++) {
      var lb = labels[i], sp = project([lb.x, lb.y, lb.z], theta);
      if (lb.dot) {
        body += '<circle cx="' + sp[0].toFixed(1) + '" cy="' + sp[1].toFixed(1) + '" r="2.4" fill="' + (lb.st === 'dom' ? '#7dffcd' : '#7af0ff') + '">'
              + '<animate attributeName="opacity" values="0.35;1;0.35" dur="2s" repeatCount="indefinite"/></circle>';
      } else {
        var tc = lb.st === 'dom' ? '#9effd0' : lb.st === 'on' ? '#7af0ff' : 'rgba(120,200,225,0.5)';
        var fs = lb.small ? 6 : (lb.big ? 9 : 7.5);
        body += '<text x="' + sp[0].toFixed(1) + '" y="' + sp[1].toFixed(1) + '" text-anchor="middle" dominant-baseline="middle"'
              + ' font-family="JetBrains Mono, ui-monospace, monospace" font-size="' + fs + '" font-weight="600" letter-spacing="0.8"'
              + ' paint-order="stroke" stroke="#04080c" stroke-width="0.8" stroke-linejoin="round" fill="' + tc + '">' + lb.t + '</text>';
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + W.toFixed(0) + '" height="' + H.toFixed(0) + '" viewBox="' + vb + '">' + body + '</svg>';
  }

  // ---------- a stable viewBox covering the model across all rotations ----------
  function fixedBox(opts) {
    var b = build(opts || {}), all = b.faces.concat(b.glow);
    var mnx = 1e9, mny = 1e9, mxx = -1e9, mxy = -1e9, t, i, j, q;
    for (t = 0; t < 360; t += 15)
      for (i = 0; i < all.length; i++) for (j = 0; j < all[i].p.length; j++) {
        q = project(all[i].p[j], t);
        if (q[0] < mnx) mnx = q[0]; if (q[0] > mxx) mxx = q[0];
        if (q[1] < mny) mny = q[1]; if (q[1] > mxy) mxy = q[1];
      }
    var pad = 46;
    return [mnx - pad, mny - pad, (mxx - mnx) + 2 * pad, (mxy - mny) + 2 * pad];
  }

  var api = { build: build, renderSVG: renderSVG, fixedBox: fixedBox, project: project, dims: { GW: GW, HW: HW, D: D, WALL: WALL, RIDGE: RIDGE } };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.JARVIS3D = api;
})(typeof window !== 'undefined' ? window : this);
