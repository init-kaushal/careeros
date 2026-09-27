import pytest
from pathlib import Path

from careeros.workspace.scaffold import scaffold, is_scaffolded


def test_claude_first_run_creates_sentinel(tmp_workspace: Path) -> None:
    written = scaffold(tmp_workspace, runtime="claude")
    assert (tmp_workspace / "CLAUDE.md").exists()
    assert len(written) > 0


def test_gpt_first_run_creates_sentinel(tmp_workspace: Path) -> None:
    written = scaffold(tmp_workspace, runtime="gpt")
    assert (tmp_workspace / "AGENTS.md").exists()
    assert len(written) > 0


def test_claude_writes_skill_files(tmp_workspace: Path) -> None:
    written = scaffold(tmp_workspace, runtime="claude")
    skill_files = [f for f in written if f.startswith(".claude/skills/")]
    assert len(skill_files) >= 3


def test_gpt_writes_skill_files(tmp_workspace: Path) -> None:
    written = scaffold(tmp_workspace, runtime="gpt")
    skill_files = [f for f in written if f.startswith(".gpt/skills/")]
    assert len(skill_files) >= 3


def test_is_scaffolded_false_before_init(tmp_workspace: Path) -> None:
    assert is_scaffolded(tmp_workspace, runtime="claude") is False
    assert is_scaffolded(tmp_workspace, runtime="gpt") is False


def test_is_scaffolded_true_after_init(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    assert is_scaffolded(tmp_workspace, runtime="claude") is True


def test_second_init_raises(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    with pytest.raises(FileExistsError, match="--refresh"):
        scaffold(tmp_workspace, runtime="claude")


def test_refresh_only_touches_skill_files(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    # Simulate user data
    profile = tmp_workspace / "profile.md"
    profile.write_text("my profile")

    # Corrupt a skill file to verify refresh replaces it
    skill = tmp_workspace / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("corrupted")

    refreshed = scaffold(tmp_workspace, refresh=True, runtime="claude")

    assert profile.read_text() == "my profile"  # untouched
    assert skill.read_text() != "corrupted"  # restored
    assert all(f.startswith(".claude/skills/") for f in refreshed)


def test_unknown_runtime_raises(tmp_workspace: Path) -> None:
    with pytest.raises(ValueError, match="Unknown runtime"):
        scaffold(tmp_workspace, runtime="copilot")


def test_claude_and_gpt_can_coexist(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    scaffold(tmp_workspace, runtime="gpt")
    assert (tmp_workspace / "CLAUDE.md").exists()
    assert (tmp_workspace / "AGENTS.md").exists()
