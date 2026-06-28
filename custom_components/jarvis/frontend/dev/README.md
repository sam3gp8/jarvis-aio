# Residence 3D model — source

Canonical source for the rotatable 3D residence model (`JARVIS3D`) shown on the
panel's **Residence** tab.

## Files
- **`house3d_core.js`** — the model. A pure-geometry axonometric projection rendered
  to SVG (no build step, no CDN). Runs under Node (`module.exports`) and in the browser
  (`window.JARVIS3D`). API: `build(opts)`, `renderSVG(opts)`, `fixedBox(opts)`.
  `opts = { theta, floor: 'all'|'1f'|'2f'|'b', lit: { 'room name': 'on'|'dom' }, box }`.
- **`jarvis_house3d.html`** — standalone interactive viewer (drag to rotate, floor
  selector, view presets). Loads `./house3d_core.js`.
- **`render3d.js`** — Node harness that writes an SVG for a given angle/floor:
  `node render3d.js <theta> <out.svg> <floor>`. Rasterize with e.g. cairosvg.

## The house spec
Dimensions, room layout, garage doors, and dormers live in the labeled config block at
the **top of `house3d_core.js`** (`GW/HW/D`, `ROOMS`, `dormerFront/dormerRearRound`
positions, `garageDoors`). Edit those for a different home; the roof/dormer/garage
generator and the by-name occupancy wiring stay the same. The property address comes
from JARVIS config (`floor_plan_address`), not from this module.

## IMPORTANT — this is inlined into the panel
The integration is **no-build / no-CDN**, so the shipped copy of this model is **inlined**
into `../jarvis-panel.js` as a top-level `const JARVIS3D = (function () { … })();` block.
When you change the model:
1. Edit `house3d_core.js` here and verify with the viewer / `render3d.js`.
2. Re-inline into `jarvis-panel.js`: replace the `const JARVIS3D = (function () { … })();`
   block with this file's body (swap the `(function (root) { … })(…)` wrapper for
   `const JARVIS3D = (function () { … return { … }; })();`).
3. Run the full gate: `node --check`, `scripts/audit.py`, `pytest tests/ -q`,
   `scripts/smoke_panel.js`, then `scripts/bump_version.sh`.

## Opening the viewer locally
Browsers may block `file://` script loads. Serve the folder instead:

```
cd custom_components/jarvis/frontend/dev
python3 -m http.server 8099   # then open http://localhost:8099/jarvis_house3d.html
```
