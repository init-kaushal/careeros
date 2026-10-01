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


@pytest.mark.parametrize("runtime,prefix", [("claude", ".claude/skills/"), ("gpt", ".gpt/skills/")])
def test_boards_catalog_ships_in_onboard_skill(tmp_workspace: Path, runtime: str, prefix: str) -> None:
    written = scaffold(tmp_workspace, runtime=runtime)
    assert f"{prefix}onboard/boards-catalog.md" in written


def test_boards_catalog_is_refreshed(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    catalog = tmp_workspace / ".claude" / "skills" / "onboard" / "boards-catalog.md"
    catalog.write_text("stale")
    scaffold(tmp_workspace, refresh=True, runtime="claude")
    assert "Singapore" in catalog.read_text()


@pytest.mark.parametrize("path", [
    ".claude/skills/onboard/SKILL.md",
    ".gpt/skills/onboard/SKILL.md",
])
def test_onboard_skills_ask_about_markets(tmp_workspace: Path, path: str) -> None:
    runtime = "claude" if path.startswith(".claude") else "gpt"
    scaffold(tmp_workspace, runtime=runtime)
    text = (tmp_workspace / path).read_text()
    assert "## Markets" in text
    assert "boards-catalog.md" in text


@pytest.mark.parametrize("runtime,prefix", [("claude", ".claude/skills/"), ("gpt", ".gpt/skills/")])
def test_market_skill_ships_and_is_cautious(tmp_workspace: Path, runtime: str, prefix: str) -> None:
    written = scaffold(tmp_workspace, runtime=runtime)
    assert f"{prefix}market/SKILL.md" in written
    text = (tmp_workspace / prefix / "market" / "SKILL.md").read_text()
    assert "not legal or immigration advice" in text
    assert "unverified" in text
    assert "Refresh after" in text


@pytest.mark.parametrize("runtime,sentinel", [("claude", "CLAUDE.md"), ("gpt", "AGENTS.md")])
def test_entrypoint_routes_to_market_skill(tmp_workspace: Path, runtime: str, sentinel: str) -> None:
    scaffold(tmp_workspace, runtime=runtime)
    assert "market/SKILL.md" in (tmp_workspace / sentinel).read_text()


def test_browse_checks_sponsorship_and_market_floor(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = (tmp_workspace / ".claude" / "skills" / "browse" / "SKILL.md").read_text()
    assert "Sponsorship check" in text
    assert "**Market:**" in text


def test_browse_finds_contacts_and_offers_route(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = (tmp_workspace / ".claude" / "skills" / "browse" / "SKILL.md").read_text()
    assert "people.md" in text
    assert "reach out first" in text
    assert "apply directly" in text


def test_gitignore_blocks_assistant_output() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / ".gitignore").read_text()
    assert "Claude outputs/" in text
