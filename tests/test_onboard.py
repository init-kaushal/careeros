import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch
from careeros.cli.main import app
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace
from datetime import datetime, timezone


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture
def resume_file(tmp_path):
    f = tmp_path / "resume.md"
    f.write_text("Alice Johnson\nSenior SRE\n8 years")
    return f


@pytest.fixture
def mock_extraction():
    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    skills = Skills(skills=[])
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=(profile, skills)):
        yield


def _run_onboard(runner, tmp_path, resume_file, ws_name="workspace"):
    ws_path = str(tmp_path / ws_name)
    # Input sequence: workspace path, resume path, confirm profile (y),
    # roles (blank), remote (any), comp (blank), locations (blank),
    # sources (greenhouse), goals (n)
    user_input = f"{ws_path}\n{resume_file}\ny\n\nany\n\n\ngreenhouse\nn\n"
    return runner.invoke(app, ["onboard"], input=user_input), ws_path


def test_onboard_creates_manifest(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    result, ws_path = _run_onboard(runner, tmp_path, resume_file)
    assert result.exit_code == 0, result.output
    assert (Path(ws_path) / "manifest.json").exists()


def test_onboard_writes_profile(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    storage = LocalFilesystemStorage(ws_path)
    profile = Profile.load(storage)
    assert profile.name == "Alice Johnson"


def test_onboard_writes_global_config(tmp_path, resume_file, mock_extraction, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("careeros.config.CONFIG_PATH", config_path)
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["workspace_path"] == ws_path


def test_onboard_logs_activity_events(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    storage = LocalFilesystemStorage(ws_path)
    log = storage.read(f"activity/{_today()}.jsonl").decode()
    event_types = [json.loads(l)["event_type"] for l in log.strip().split("\n") if l]
    assert "workspace_created" in event_types
    assert "resume_imported" in event_types
    assert "onboard_complete" in event_types


def test_onboard_missing_resume_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    ws_path = str(tmp_path / "ws")
    result = runner.invoke(app, ["onboard"], input=f"{ws_path}\n/nonexistent/resume.md\n")
    assert result.exit_code != 0
