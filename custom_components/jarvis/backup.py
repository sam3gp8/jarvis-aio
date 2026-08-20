"""One-command backup / restore of JARVIS state — memory, patterns, knowledge,
and config — so a device re-flash or migration doesn't lose it.

Everything JARVIS persists lives under the HA config dir. ``create_backup`` tars
it into ``/config/jarvis_backups/`` (download that file before re-flashing);
``restore_backup`` extracts it back into place. Both are plain, off-loop file I/O
so callers run them via ``hass.async_add_executor_job``.
"""
from __future__ import annotations

import glob
import os
import tarfile
import time

# Everything JARVIS persists, relative to the HA config dir (/config).
_STATE_PATHS = [
    "jarvis",              # DBs (knowledge/patterns/conversations/reminders), config.json, docs, json state
    "jarvis.db",           # fts5 keyword-memory fallback
    "jarvis_memory",       # ChromaDB semantic memory
    "jarvis_persona.txt",
    "jarvis_routines.yaml",
]
_BACKUP_SUBDIR = "jarvis_backups"


def create_backup(config_dir: str) -> str:
    """Archive all JARVIS state into a timestamped tar.gz. Returns its path."""
    bdir = os.path.join(config_dir, _BACKUP_SUBDIR)
    os.makedirs(bdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(bdir, f"jarvis-state-{stamp}.tar.gz")
    with tarfile.open(path, "w:gz") as tar:
        for rel in _STATE_PATHS:
            full = os.path.join(config_dir, rel)
            if os.path.exists(full):
                tar.add(full, arcname=rel)
    return path


def restore_backup(config_dir: str, archive: str = "") -> str:
    """Restore JARVIS state from a backup archive (the latest if none named).
    Returns the archive path used. Restart HA afterwards to load it."""
    bdir = os.path.join(config_dir, _BACKUP_SUBDIR)
    if not archive:
        cands = sorted(glob.glob(os.path.join(bdir, "jarvis-state-*.tar.gz")))
        if not cands:
            raise FileNotFoundError(f"no JARVIS backups found in {bdir}")
        archive = cands[-1]
    elif not os.path.isabs(archive):
        archive = os.path.join(bdir, archive)
    if not os.path.exists(archive):
        raise FileNotFoundError(archive)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(config_dir, filter="data")   # py3.12+ safe extraction
        except TypeError:
            tar.extractall(config_dir)
    return archive
