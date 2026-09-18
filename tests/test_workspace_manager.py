import pytest
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, open_workspace
from careeros.workspace.manifest import SUPPORTED_SCHEMA_VERSION, UnsupportedSchemaVersion


def test_init_creates_manifest(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    assert storage.exists("manifest.json")
    assert ctx.manifest.schema_version == SUPPORTED_SCHEMA_VERSION


def test_init_runs_migrations(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    assert storage.exists("profile/.keep")
    assert storage.exists("config/sources.json")


def test_init_records_migrations_in_manifest(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    assert "001_initial" in ctx.manifest.migrations_applied


def test_init_twice_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileExistsError, match="already exists"):
        init_workspace(storage)


def test_open_reads_manifest(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx1 = init_workspace(storage)
    ctx2 = open_workspace(storage)
    assert ctx1.manifest.workspace_id == ctx2.manifest.workspace_id


def test_open_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        open_workspace(storage)


def test_open_too_new_schema_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    # Simulate a workspace created by a future CareerOS version
    import json
    from dataclasses import asdict
    manifest_data = asdict(ctx.manifest)
    manifest_data["schema_version"] = "999"
    storage.atomic_write("manifest.json", json.dumps(manifest_data, indent=2).encode())
    with pytest.raises(UnsupportedSchemaVersion):
        open_workspace(storage)
