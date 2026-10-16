"""Manifest rules for `application_submit` items: workspace-relative regular files, hashed by code (spec section 4).

The only module of the package that touches the filesystem, and only to read files under a given root; it writes
nothing, creates nothing and reads no clock. A symlink is accepted when its fully resolved target is a regular file
inside the workspace; the file is then hashed by the target's content, so retargeting the link changes the hash and
`verify` reports `manifest_changed`.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from careeros.core.approvals import rules
from careeros.core.approvals.model import ManifestEntry


class ManifestError(Exception):
    """A manifest path is unusable (`manifest_invalid`) or its file differs from the proposal (`manifest_changed`).
    `str(error)` is the exact per-path message."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.code, self.path, self.message = code, path, message


@dataclass(frozen=True)
class ResolvedFile:
    path: str          # normalised, workspace-relative, as written in the manifest
    sha256: str
    size: int


def _invalid(path: str, what: str) -> ManifestError:
    return ManifestError(rules.MANIFEST_INVALID, path, f"{path}: {what}")


def normalize_rel(path: str) -> str:
    """The normalised workspace-relative form of `path` ('./a//b' -> 'a/b'), or `ManifestError(manifest_invalid)`.
    Purely textual: absolute paths, '..' segments, control characters and empty paths are refused."""
    if not isinstance(path, str):
        raise _invalid(repr(path), "path must be text")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise _invalid(path, "path contains a control character")
    if path.startswith("/"):
        raise _invalid(path, "path must be relative to the workspace")
    parts = path.split("/")
    if ".." in parts:
        raise _invalid(path, "'..' segments are not allowed")
    kept = [p for p in parts if p not in ("", ".")]
    if not kept:
        raise _invalid(path, "path is empty")
    return "/".join(kept)


def _hash_one(root_real: Path, root: Path, raw: str, rel: str) -> ResolvedFile:
    try:
        real = (root / rel).resolve(strict=True)
    except FileNotFoundError:
        raise _invalid(raw, "file does not exist") from None
    except (OSError, RuntimeError):
        raise _invalid(raw, "path cannot be resolved") from None
    if real != root_real and root_real not in real.parents:
        raise _invalid(raw, "resolves outside the workspace")
    try:
        if not stat.S_ISREG(real.stat().st_mode):
            raise _invalid(raw, "not a regular file")
        digest, size = hashlib.sha256(), 0
        with real.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise _invalid(raw, f"cannot be read: {exc.strerror or exc}") from None
    return ResolvedFile(rel, digest.hexdigest(), size)


def resolve(root: Path, entries: Iterable[str | ManifestEntry]) -> list[ResolvedFile]:
    """Validate and hash each path (strings or manifest entries) under `root`; sorted by normalised path.
    Raises `ManifestError(manifest_invalid)` for the first unusable path."""
    root = Path(root)
    root_real = root.resolve()
    seen: set[str] = set()
    out: list[ResolvedFile] = []
    for entry in entries:
        raw = entry.path if isinstance(entry, ManifestEntry) else entry
        rel = normalize_rel(raw)
        if rel in seen:
            raise _invalid(raw, "listed more than once")
        seen.add(rel)
        out.append(_hash_one(root_real, root, raw, rel))
    return sorted(out, key=lambda f: f.path)


def hash_files(root: Path, rel_paths: Iterable[str]) -> tuple[ManifestEntry, ...]:
    """The manifest entries for `rel_paths`, hashed by code (the agent never types a hash)."""
    return tuple(ManifestEntry(f.path, f.sha256) for f in resolve(root, rel_paths))


def verify(root: Path, entries: Iterable[ManifestEntry]) -> None:
    """Re-validate and re-hash a recorded manifest. `manifest_invalid` when a path is no longer usable,
    `manifest_changed` when a file's content (or a link's target) differs from the recorded hash."""
    entries = tuple(entries)
    for entry, got in zip(sorted(entries, key=lambda e: e.path), resolve(root, entries)):
        if got.sha256 != entry.sha256:
            raise ManifestError(rules.MANIFEST_CHANGED, entry.path, f"{entry.path}: content changed since it was proposed")
