"""
JARVIS — Camera backend registry.

Different camera systems (Frigate, Nest, Reolink, UniFi Protect, Blue Iris,
etc.) each have their own way of producing high-quality snapshots beyond
HA's generic async_get_image. This registry lets any backend declare:
  - How to detect if a camera entity belongs to it
  - How to fetch the best available image

Adding support for a new camera system = add one class and register it.
No changes needed to the core analyze_camera logic.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)

MIN_IMAGE_SIZE = 2_000


# ─── Base class ──────────────────────────────────────────────────────────────

class CameraBackend(ABC):
    """Abstract interface for a camera integration backend."""

    name: str = "abstract"

    @abstractmethod
    def handles(self, hass: HomeAssistant, entity_id: str) -> bool:
        """True if this backend owns the camera entity."""
        ...

    @abstractmethod
    async def fetch_best_image(
        self,
        hass: HomeAssistant,
        entity_id: str,
        event_cache: dict,
    ) -> Optional[bytes]:
        """
        Return the highest-quality image available, or None if we should
        fall through to HA's standard snapshot.
        event_cache is the shared _EVENT_CACHE from camera.py.
        """
        ...


# ─── Frigate ─────────────────────────────────────────────────────────────────

class FrigateBackend(CameraBackend):
    name = "frigate"

    def handles(self, hass: HomeAssistant, entity_id: str) -> bool:
        reg = er.async_get(hass)
        entry = reg.async_get(entity_id)
        return entry is not None and entry.platform == "frigate"

    async def fetch_best_image(self, hass, entity_id, event_cache):
        session = async_get_clientsession(hass)
        try:
            base_url = get_url(hass, allow_internal=True, prefer_external=False)
        except Exception:
            base_url = "http://127.0.0.1:8123"

        cached = event_cache.get(entity_id)
        if cached and cached.get("source") == "frigate":
            event_id = cached.get("event_id")
            if event_id:
                url = f"{base_url}/api/frigate/notifications/{event_id}/snapshot.jpg"
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if len(data) > MIN_IMAGE_SIZE:
                                return data
                except Exception as exc:
                    _LOGGER.debug("Frigate event snapshot failed: %s", exc)
        return None


# ─── Nest ────────────────────────────────────────────────────────────────────

class NestBackend(CameraBackend):
    name = "nest"

    def handles(self, hass: HomeAssistant, entity_id: str) -> bool:
        reg = er.async_get(hass)
        entry = reg.async_get(entity_id)
        return entry is not None and entry.platform == "nest"

    async def fetch_best_image(self, hass, entity_id, event_cache):
        cached = event_cache.get(entity_id)
        if not cached or cached.get("source") != "nest":
            return None

        device_id = cached.get("device_id")
        event_id  = cached.get("event_id")
        if not device_id or not event_id:
            return None

        try:
            nest_data = hass.data.get("nest")
            if nest_data:
                for sub in getattr(nest_data, "subscribers", []):
                    try:
                        media_store = getattr(sub, "media_store", None)
                        if media_store:
                            media = await media_store.async_get_media(device_id, event_id)
                            if media and hasattr(media, "contents"):
                                data = media.contents
                                if data and len(data) > MIN_IMAGE_SIZE:
                                    return data
                    except Exception as exc:
                        _LOGGER.debug("Nest media fetch error: %s", exc)
        except Exception as exc:
            _LOGGER.debug("Nest hass.data access failed: %s", exc)
        return None


# ─── UniFi Protect (future-ready stub) ───────────────────────────────────────

class UniFiProtectBackend(CameraBackend):
    """
    UniFi Protect support is stubbed. When someone wants it:
    UniFi Protect exposes snapshot APIs via its integration. Implementation
    would call the unifiprotect service to fetch a higher-res image.
    """
    name = "unifiprotect"

    def handles(self, hass: HomeAssistant, entity_id: str) -> bool:
        reg = er.async_get(hass)
        entry = reg.async_get(entity_id)
        return entry is not None and entry.platform in ("unifiprotect", "unifi_protect")

    async def fetch_best_image(self, hass, entity_id, event_cache):
        # Fallback to standard snapshot path for now — this gets a good image
        # because UniFi Protect cameras already expose high-quality streams.
        return None


# ─── Reolink (future-ready stub) ─────────────────────────────────────────────

class ReolinkBackend(CameraBackend):
    name = "reolink"

    def handles(self, hass: HomeAssistant, entity_id: str) -> bool:
        reg = er.async_get(hass)
        entry = reg.async_get(entity_id)
        return entry is not None and entry.platform == "reolink"

    async def fetch_best_image(self, hass, entity_id, event_cache):
        # Reolink's HA integration already provides good snapshots via
        # async_get_image. Standard path is fine.
        return None


# ─── Registry ────────────────────────────────────────────────────────────────
#
# Ordered by specificity — more specific backends tried first. Generic
# fallbacks (like unifiprotect) come last.

BACKENDS: list[CameraBackend] = [
    FrigateBackend(),
    NestBackend(),
    UniFiProtectBackend(),
    ReolinkBackend(),
]


def find_backend(hass: HomeAssistant, entity_id: str) -> Optional[CameraBackend]:
    """Return the first registered backend that handles this entity."""
    for backend in BACKENDS:
        try:
            if backend.handles(hass, entity_id):
                return backend
        except Exception as exc:
            _LOGGER.debug("Backend '%s' handles() check failed: %s", backend.name, exc)
    return None


def register_backend(backend: CameraBackend, priority: int = -1) -> None:
    """
    Runtime registration so third parties can add their own backend without
    modifying this file. priority=-1 means 'append to end'.
    """
    if priority < 0 or priority >= len(BACKENDS):
        BACKENDS.append(backend)
    else:
        BACKENDS.insert(priority, backend)
    _LOGGER.info("JARVIS: registered camera backend '%s'", backend.name)
