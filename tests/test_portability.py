import json
import zipfile
import pytest
from pathlib import Path
from typer.testing import CliRunner
from careeros.cli.main import app
from careeros.config import GlobalConfig
from careeros.core.models import Profile
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, open_workspace


@pytest.fixture
def workspace_with_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    ws_path = str(tmp_path / "my-career")
    storage = LocalFilesystemStorage(ws_path)
    ctx = init_workspace(storage)
    Profile(name="Test User", title="Engineer").save(storage)
    GlobalConfig(workspace_path=ws_path).save()
    return tmp_path, ws_path, ctx.manifest.workspace_id


def test_export_creates_zip(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    result = runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])
    assert result.exit_code == 0, result.output
    assert Path(output).exists()


def test_export_zip_contains_manifest(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])
    with zipfile.ZipFile(output) as zf:
        assert "manifest.json" in zf.namelist()


def test_export_zip_contains_profile(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])
    with zipfile.ZipFile(output) as zf:
        assert "profile/profile.json" in zf.namelist()


def test_import_restores_workspace(workspace_with_profile):
    tmp_path, ws_path, original_id = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])

    new_dest = str(tmp_path / "restored")
    result = runner.invoke(app, ["import", output, "--dest", new_dest])
    assert result.exit_code == 0, result.output

    restored_storage = LocalFilesystemStorage(new_dest)
    ctx = open_workspace(restored_storage)
    assert ctx.manifest.workspace_id == original_id


def test_import_preserves_profile(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])

    new_dest = str(tmp_path / "restored")
    runner.invoke(app, ["import", output, "--dest", new_dest])
    storage = LocalFilesystemStorage(new_dest)
    profile = Profile.load(storage)
    assert profile.name == "Test User"


def test_import_nonexistent_zip_fails(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["import", "/nonexistent.zip", "--dest", str(tmp_path / "d")])
    assert result.exit_code != 0


def test_import_existing_dest_fails(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])

    existing_dest = tmp_path / "existing"
    existing_dest.mkdir()
    result = runner.invoke(app, ["import", output, "--dest", str(existing_dest)])
    assert result.exit_code != 0
