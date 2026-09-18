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
    # run again — should apply nothing new
    newly = run_pending(storage, applied=["001_initial"])
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
    assert data["approval_required"] is True


def test_m001_idempotent(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    run_pending(storage, applied=[])  # running m001 twice must not error
    assert storage.exists("profile/.keep")
