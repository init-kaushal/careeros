import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from careeros.cli.main import app
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


@pytest.fixture
def seeded_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    Profile(name="Alice", title="SRE").save(storage)
    Skills(skills=[]).save(storage)
    return tmp_path, ctx


def test_status_exits_zero(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "status", "--workspace", str(ws_path)])
    assert result.exit_code == 0


def test_status_shows_name(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "status", "--workspace", str(ws_path)])
    assert "Alice" in result.output


def test_status_shows_schema_version(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "status", "--workspace", str(ws_path)])
    assert "v1" in result.output


def test_validate_passes_fresh_workspace(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "validate", "--workspace", str(ws_path)])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_validate_fails_missing_manifest(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "validate", "--workspace", str(tmp_path)])
    assert result.exit_code != 0


def test_validate_fails_bad_activity_line(seeded_workspace):
    ws_path, _ = seeded_workspace
    from datetime import datetime, timezone
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = ws_path / "activity" / f"{date}.jsonl"
    log_path.parent.mkdir(exist_ok=True)
    log_path.write_text("not valid json\n")
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "validate", "--workspace", str(ws_path)])
    assert result.exit_code != 0
