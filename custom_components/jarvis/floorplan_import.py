"""Import a floor-plan background from a vacuum-robot map or any dashboard image
entity, so the Residence floor-plan editor needs no manual upload (#405).

Home Assistant already surfaces a robot vacuum's cleaned-area map as an `image.*`
or `camera.*` entity (e.g. `image.cleaner_living_room_map_0`, Valetudo's
`camera.<robot>_map`), and dashboards often carry a floor-plan picture the same
way. This module:

  * `is_floorplan_source` / `list_sources` — find those entities, and
  * `async_import_to_floor` — fetch the entity's current image and store it as
    the `floor_plan_bg` background for a floor, exactly as the manual file upload
    does (a `data:` URL merged into the per-floor JSON map).

Everything is best-effort and defensive: a fetch failure raises a clear message
for the panel to show, and nothing else in JARVIS depends on this path.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Entity-id / name tokens that mark an image as a map or floor plan. Vacuum map
# entities and dashboard floor-plan pictures both land here.
_MAP_TOKENS = (
    "map", "floor", "plan", "vacuum", "cleaner", "roborock", "valetudo",
    "robot", "lidar", "mop", "neato", "dreame", "ecovacs", "rooms",
)

# Image bytes smaller than this are almost certainly an error/placeholder.
_MIN_IMAGE_SIZE = 1024


def is_floorplan_source(entity_id: str, friendly_name: Optional[str] = None) -> bool:
    """True when this entity looks like a usable floor-plan / vacuum-map image.

    Any ``image.*`` entity qualifies (that domain is essentially always a map,
    snapshot or plan), as does any ``image``/``camera`` entity whose id or name
    names a map/floor/vacuum. Pure and cheap."""
    if not entity_id:
        return False
    domain = entity_id.split(".", 1)[0].lower()
    if domain not in ("image", "camera"):
        return False
    if domain == "image":
        return True
    hay = f"{entity_id} {friendly_name or ''}".lower()
    return any(tok in hay for tok in _MAP_TOKENS)


def list_sources(hass) -> list[dict]:
    """Candidate floor-plan source entities, as ``{entity_id, name, domain}``.

    Image entities first (most likely), then matching cameras; both sorted by
    name so the panel dropdown is stable."""
    out: list[dict] = []
    try:
        for domain in ("image", "camera"):
            rows = []
            for st in hass.states.async_all(domain):
                name = st.attributes.get("friendly_name", st.entity_id)
                if is_floorplan_source(st.entity_id, name):
                    rows.append({"entity_id": st.entity_id,
                                 "name": str(name), "domain": domain})
            rows.sort(key=lambda r: r["name"].lower())
            out.extend(rows)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        _LOGGER.debug("floorplan sources enumeration failed: %s", exc)
    return out


async def _fetch_image_bytes(hass, entity_id: str) -> tuple[bytes, str]:
    """Return ``(content, content_type)`` for an image/camera entity's current
    frame.

    Prefers the domain's **in-process** helper (``image``/``camera``
    ``async_get_image``) — the canonical bytes straight from the entity, with no
    HTTP round-trip and no proxy access-token that can go stale. That is what
    lets official integrations import cleanly: a Roborock ``image.*`` map renders
    fine in the dashboard yet its cached ``entity_picture`` proxy fetch was
    failing here (#405), because ``image.*`` had no in-process fallback — only
    ``camera.*`` did. Falls back to fetching the entity's ``entity_picture`` proxy
    URL (covers entities HA serves only that way). Raises ValueError with a
    readable message on failure."""
    state = hass.states.get(entity_id)
    if state is None:
        raise ValueError(f"{entity_id} is not available")
    domain = entity_id.split(".", 1)[0].lower()

    # 1. In-process domain helper — no HTTP, no proxy token. image.* was the gap
    #    that made official vacuum-map imports (#405) fail; camera.* keeps the
    #    same canonical read it always had.
    _dom_get = None
    default_ctype = "image/png"
    try:
        if domain == "image":
            from homeassistant.components.image import async_get_image as _dom_get
        elif domain == "camera":
            from homeassistant.components.camera import async_get_image as _dom_get
            default_ctype = "image/jpeg"
    except Exception as exc:  # noqa: BLE001 - helper not importable; use the proxy
        _LOGGER.debug("floorplan helper import failed for %s: %s", entity_id, exc)
        _dom_get = None
    if _dom_get is not None:
        try:
            img = await _dom_get(hass, entity_id, timeout=15)
            content = getattr(img, "content", None)
            if content:
                return content, (getattr(img, "content_type", None) or default_ctype)
        except Exception as exc:  # noqa: BLE001 - fall through to the proxy URL
            _LOGGER.debug("floorplan in-process fetch failed for %s: %s", entity_id, exc)

    # 2. entity_picture proxy URL fallback — universal for image.* and camera.*.
    pic = state.attributes.get("entity_picture")
    if pic:
        try:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            from homeassistant.helpers.network import get_url
            import aiohttp
            try:
                base = get_url(hass, allow_internal=True, prefer_external=False)
            except Exception:
                base = "http://127.0.0.1:8123"
            url = pic if pic.startswith("http") else f"{base}{pic}"
            session = async_get_clientsession(hass)
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if data and len(data) >= _MIN_IMAGE_SIZE:
                        ctype = resp.headers.get("Content-Type", "image/png").split(";")[0]
                        return data, ctype
                _LOGGER.debug("floorplan fetch %s via picture: HTTP %s / %s bytes",
                              entity_id, resp.status, len(data) if 'data' in dir() else 0)
        except Exception as exc:  # noqa: BLE001 - fall through to the error below
            _LOGGER.debug("floorplan picture fetch failed for %s: %s", entity_id, exc)

    raise ValueError(f"couldn't read an image from {entity_id}")


def _merge_background(raw, floor: str, data_url: str) -> str:
    """Merge ``data_url`` into the ``floor_plan_bg`` JSON under ``floor`` and
    return the new JSON string. Tolerant of a str, dict or empty prior value —
    the same per-floor shape the manual file upload writes."""
    bgs = {}
    if isinstance(raw, dict):
        bgs = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                bgs = parsed
        except Exception:
            bgs = {}
    bgs[floor] = data_url
    return json.dumps(bgs)


async def async_import_to_floor(hass, entity_id: str, floor: str = "1f") -> dict:
    """Fetch ``entity_id``'s image and store it as the ``floor_plan_bg`` for
    ``floor``. Returns ``{"floor", "entity_id", "bytes"}``. Raises ValueError with
    a readable message on failure (no image, nothing to import)."""
    floor = str(floor or "1f")
    content, ctype = await _fetch_image_bytes(hass, entity_id)
    if not ctype.startswith("image/"):
        ctype = "image/png"
    data_url = f"data:{ctype};base64," + base64.b64encode(content).decode("ascii")

    # Read the current floor_plan_bg, merge, and persist via the panel's own
    # config store (runtime_config + config.json), so it shows up immediately and
    # survives restarts — same key the manual upload writes.
    from . import jarvis_config
    try:
        raw = jarvis_config.get("floor_plan_bg", {})
    except Exception:
        raw = {}
    merged = _merge_background(raw, floor, data_url)
    await hass.async_add_executor_job(
        jarvis_config.set_many, {"floor_plan_bg": merged})
    # Mirror into the live runtime_config so the open panel re-renders at once.
    try:
        from .const import DOMAIN
        for data in (hass.data.get(DOMAIN) or {}).values():
            if isinstance(data, dict):
                rc = data.setdefault("runtime_config", {})
                rc["floor_plan_bg"] = merged
    except Exception:
        pass

    _LOGGER.info("JARVIS: imported floor plan for %s from %s (%d bytes)",
                 floor, entity_id, len(content))
    return {"floor": floor, "entity_id": entity_id, "bytes": len(content)}
