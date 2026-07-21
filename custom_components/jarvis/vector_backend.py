"""
JARVIS optional vector backend (v6.56.0).

JARVIS-AIO ships light: memory and document retrieval work everywhere via the
built-in SQLite FTS5 keyword fallback, with no heavy dependencies. ChromaDB —
which upgrades that to true semantic vector search — is a large install
(onnxruntime, tokenizers, etc., often 300-500 MB) that can be slow or fail to
build on constrained hosts (HA Green/Yellow, a Pi). So rather than force it on
everyone, it's an opt-in the user enables from Settings when they want it.

This module:
  - reports whether chromadb is importable (already installed),
  - installs it at runtime via Home Assistant's supported package helper,
  - re-initializes the memory and document stores in place so vector search
    takes effect without an HA restart.

Nothing here raises to the caller; every path returns a status dict.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_PACKAGE = "chromadb>=0.4.22"
_IMPORT_NAME = "chromadb"


def is_installed() -> bool:
    """True if chromadb can be imported right now."""
    try:
        import importlib
        return importlib.util.find_spec(_IMPORT_NAME) is not None
    except Exception:
        return False


def _reinit_stores() -> dict:
    """Force memory.py and documents.py to re-run their lazy init so a freshly
    installed chromadb is picked up without restarting Home Assistant."""
    out = {"memory_vector": False, "documents_vector": False}
    # memory.py
    try:
        from . import memory
        memory._chromadb_available = False
        memory._fts_available = False
        memory._collection = None
        memory._ensure_initialized()
        out["memory_vector"] = bool(getattr(memory, "_chromadb_available", False))
    except Exception as exc:
        _LOGGER.debug("memory reinit failed: %s", exc)
    # documents.py
    try:
        from . import documents
        documents._chroma_ok = False
        documents._fts_ok = False
        documents._collection = None
        documents._initialized = False
        documents._ensure_init()
        out["documents_vector"] = bool(getattr(documents, "_chroma_ok", False))
    except Exception as exc:
        _LOGGER.debug("documents reinit failed: %s", exc)
    return out


def status() -> dict:
    """Current vector-backend status for the panel. Never raises."""
    installed = is_installed()
    info = {"installed": installed, "package": _PACKAGE,
            "memory_vector": False, "documents_vector": False}
    if installed:
        try:
            from . import memory, documents
            info["memory_vector"] = bool(getattr(memory, "_chromadb_available", False))
            info["documents_vector"] = bool(getattr(documents, "_chroma_ok", False))
        except Exception:
            pass
    return info


async def install(hass) -> dict:
    """
    Install ChromaDB at runtime and activate vector search. Returns a status
    dict: {"ok", "installed", "already"?, "memory_vector", "documents_vector",
    "error"?}. Safe to call when already installed (no-op activate).
    """
    if is_installed():
        reinit = await hass.async_add_executor_job(_reinit_stores)
        return {"ok": True, "installed": True, "already": True, **reinit}

    # HA's package installer is the SYNCHRONOUS install_package (it shells out to
    # pip/uv), so it must run in an executor, never on the event loop. There is
    # no async_install_package in homeassistant.util.package.
    try:
        from homeassistant.util.package import install_package
    except Exception as exc:
        return {"ok": False, "installed": False,
                "error": f"HA package helper unavailable: {exc}"}

    _LOGGER.info("JARVIS: installing optional vector backend (%s) — "
                 "this can take a few minutes on first install", _PACKAGE)
    try:
        ok = await hass.async_add_executor_job(install_package, _PACKAGE)
    except Exception as exc:
        _LOGGER.exception("chromadb install failed: %s", exc)
        return {"ok": False, "installed": False, "error": str(exc)}

    if not ok:
        return {"ok": False, "installed": False,
                "error": "install returned failure — the host may lack build "
                         "tools or memory for chromadb; keyword search remains active"}

    reinit = await hass.async_add_executor_job(_reinit_stores)
    _LOGGER.info("JARVIS: vector backend installed; memory=%s documents=%s",
                 reinit.get("memory_vector"), reinit.get("documents_vector"))
    return {"ok": True, "installed": True, "already": False, **reinit}
