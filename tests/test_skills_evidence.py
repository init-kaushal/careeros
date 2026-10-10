"""The evidence gate is mandatory wording in every Claude Code drafting skill, and refresh delivers it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from careeros.workspace.scaffold import scaffold

SKILLS = ("prep", "apply", "outreach", "follow-up", "humanize", "interview")
GATE_PHRASES = (
    "## EVIDENCE GATE (mandatory)",
    "careeros check <file> --against <job-id> --record",
    "Re-run the check after every edit. Never skip it and never ignore a failure.",
    "do not show the draft",
    "Never use `--allow` to get past a claim about the user's own history",
    "Never call a draft \"verified\" or \"true\"",
    "still only claimed, not confirmed",
    "Exit 2",
)
TEMPLATES = Path(__file__).parent.parent / "careeros" / "templates"


def skill_text(name: str) -> str:
    return (TEMPLATES / "workspace" / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", SKILLS)
def test_every_drafting_skill_carries_the_full_gate(skill: str) -> None:
    text = skill_text(skill)
    for phrase in GATE_PHRASES:
        assert phrase in text, f"{skill} is missing: {phrase}"
    assert text.count("## EVIDENCE GATE (mandatory)") == 1
    assert "**Applies to:**" in text


@pytest.mark.parametrize("skill,needle", [
    ("prep", "tailored resume"), ("apply", "free-text answer"), ("outreach", "connection note"),
    ("follow-up", "follow-up message"), ("humanize", "never facts"), ("interview", "STAR story"),
])
def test_each_gate_names_what_it_covers(skill: str, needle: str) -> None:
    assert needle in skill_text(skill)


def test_the_gate_comes_before_the_skills_first_step() -> None:
    for skill in SKILLS:
        text = skill_text(skill)
        first_step = min(m.start() for m in re.finditer(r"^## (STEP|MODE)", text, re.M))
        assert text.index("## EVIDENCE GATE") < first_step, skill


def test_the_memory_skill_covers_import_add_and_the_human_only_commands() -> None:
    text = skill_text("memory")
    for phrase in ("careeros memory import", "--apply --yes", "--quote", "! careeros memory confirm",
                   "Never try to pipe an answer into them", "claimed"):
        assert phrase in text


def test_claude_md_routes_to_the_memory_skill_and_describes_career(tmp_path: Path) -> None:
    text = (TEMPLATES / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")
    assert ".claude/skills/memory/SKILL.md" in text and "career/" in text and "careeros check" in text


def test_onboarding_imports_the_resume_into_the_memory() -> None:
    text = skill_text("onboard")
    assert "careeros memory import" in text and "claimed" in text


def test_refresh_delivers_the_gate_to_an_existing_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    (root / ".claude" / "skills" / "outreach" / "SKILL.md").write_text("old copy\n", encoding="utf-8")
    refreshed = scaffold(root, refresh=True, runtime="claude")
    assert ".claude/skills/outreach/SKILL.md" in refreshed and ".claude/skills/memory/SKILL.md" in refreshed
    assert "## EVIDENCE GATE (mandatory)" in (root / ".claude" / "skills" / "outreach" / "SKILL.md").read_text(encoding="utf-8")


def test_the_gpt_skills_do_not_claim_a_check_they_cannot_run() -> None:
    for path in (TEMPLATES / "gpt-workspace").rglob("*.md"):
        assert "careeros check" not in path.read_text(encoding="utf-8")
