"""Verified backups and manifests, shared by every operation that rewrites workspace files."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from careeros.core import models
from careeros.core.workspace import BACKUPS_REL, WorkspaceError, atomic_write_bytes


class BackupError(WorkspaceError):
    """A backup could not be written or verified."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_backup_dir(root: Path, kind: str) -> Path:
    stamp = models.utc_now().replace("-", "").replace(":", "")
    base = Path(root) / BACKUPS_REL
    candidate = base / f"{stamp}-{kind}"
    n = 2
    while candidate.exists():
        candidate = base / f"{stamp}-{kind}-{n}"
        n += 1
    candidate.mkdir(parents=True)
    return candidate


def write_manifest(backup: Path, manifest: dict, write: Callable[[Path, bytes], None] = atomic_write_bytes) -> None:
    write(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))


def backup_files(
    backup: Path, items: list[tuple[str, bytes]], write: Callable[[Path, bytes], None] = atomic_write_bytes
) -> None:
    """Copy each (relative path, bytes) into the backup folder and read it back to prove it is intact."""
    for rel, data in items:
        destination = backup / rel
        write(destination, data)
        if sha256(destination.read_bytes()) != sha256(data):
            raise BackupError(f"backup verification failed for {rel}; nothing was changed")
