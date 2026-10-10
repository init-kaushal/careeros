"""Verified backups and manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from careeros.core import backup


def test_new_backup_dirs_never_collide(tmp_path: Path, clock) -> None:
    first = backup.new_backup_dir(tmp_path, "kind")
    second = backup.new_backup_dir(tmp_path, "kind")
    assert first != second and first.is_dir() and second.is_dir()


def test_backup_files_copies_and_verifies(tmp_path: Path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    backup.backup_files(folder, [("a/b.txt", b"hello")])
    assert (folder / "a" / "b.txt").read_bytes() == b"hello"


def test_a_corrupted_copy_is_caught(tmp_path: Path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    from careeros.core.workspace import atomic_write_bytes

    def corrupting(path: Path, data: bytes) -> None:
        atomic_write_bytes(path, data + b"x")

    with pytest.raises(backup.BackupError, match="backup verification failed for a.txt"):
        backup.backup_files(folder, [("a.txt", b"hello")], write=corrupting)


def test_manifest_is_written_as_json(tmp_path: Path) -> None:
    backup.write_manifest(tmp_path, {"status": "pending"})
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"status": "pending"}
    assert backup.sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
