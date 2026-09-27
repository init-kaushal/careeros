import json
import pytest
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.migrations import run_pending, MIGRATIONS


def test_run_pending_runs_unapplied(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    newly = run_pending(storage, applied=[])
    assert "001_initial" in newly


def test_run_pending_skips_applied(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    # run again — should apply nothing new when all are marked as applied
    newly = run_pending(storage, applied=["001_initial", "002_applications", "003_approvals", "004_boards", "005_custom_boards"])
    assert newly == []


def test_m001_creates_profile_dir(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("profile/.keep")


def test_m001_creates_resumes_versions(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("resumes/versions/.keep")


def test_m001_creates_activity_dir(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("activity/.keep")


def test_m001_creates_sources_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("config/sources.json")
    data = json.loads(storage.read("config/sources.json"))
    assert "sources" in data


def test_m001_creates_policies_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("config/policies.json")
    data = json.loads(storage.read("config/policies.json"))
    assert data == {"blocked_companies": [], "min_salary": None, "blocked_locations": []}


def test_m001_idempotent(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    run_pending(storage, applied=[])  # running m001 twice must not error
    assert storage.exists("profile/.keep")


def test_m002_creates_applications_dir(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("applications/.keep")


def test_init_workspace_creates_approvals_directory(tmp_path):
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    assert storage.exists("approvals/.keep")
    assert "003_approvals" in ctx.manifest.migrations_applied


def test_existing_workspace_acquires_approvals_on_open(tmp_path):
    import json
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace, open_workspace
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    # Simulate a workspace created before this migration existed.
    storage.delete("approvals/.keep")
    manifest = json.loads(storage.read("manifest.json").decode())
    manifest["migrations_applied"] = [
        m for m in manifest["migrations_applied"] if m != "003_approvals"
    ]
    storage.atomic_write("manifest.json", json.dumps(manifest).encode())

    ctx = open_workspace(storage)
    assert storage.exists("approvals/.keep")
    assert "003_approvals" in ctx.manifest.migrations_applied
