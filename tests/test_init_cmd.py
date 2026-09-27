import pytest
from pathlib import Path
from typer.testing import CliRunner

from careeros.cli.main import app

runner = CliRunner()


def test_init_claude_creates_workspace(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 0
    assert (target / "CLAUDE.md").exists()
    assert "Workspace created" in result.output


def test_init_gpt_creates_workspace(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(target), "--runtime", "gpt"])
    assert result.exit_code == 0
    assert (target / "AGENTS.md").exists()
    assert "Workspace created" in result.output


def test_init_second_run_fails(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 1
    assert "--refresh" in result.output


def test_init_refresh_succeeds_on_existing(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["init", str(target), "--refresh"])
    assert result.exit_code == 0
    assert "refreshed" in result.output.lower()


def test_init_unknown_runtime_fails(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(target), "--runtime", "copilot"])
    assert result.exit_code == 1
    assert "Unknown runtime" in result.output


def test_init_shows_gpt_next_steps(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(target), "--runtime", "gpt"])
    assert "AGENTS.md" in result.output
    assert "Project Instructions" in result.output
