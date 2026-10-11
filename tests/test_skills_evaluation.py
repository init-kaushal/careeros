"""The `evaluate` skill and the prep/browse/CLAUDE.md edits: pinned wording, delivery by refresh, and a worked example
that `careeros evaluate` really accepts (so the skill's example cannot drift from the code). Invented data only."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml
from helpers import add_job
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core.evaluation import record
from careeros.workspace.scaffold import scaffold

TEMPLATES = Path(__file__).parent.parent / "careeros" / "templates"
SKILLS = TEMPLATES / "workspace" / ".claude" / "skills"
runner = CliRunner()

# sha256 of browse/SKILL.md and of prep's EVIDENCE GATE block before Task F
BROWSE_BEFORE = "53e1e3aa88ee3b86b29847eacf77e35e9571bcea7af425703e519b4f3fffb2a8"
PREP_GATE_BEFORE = "caac6e951267c866b55512c66085694d10d621105c8e766648c110a654d59322"
BROWSE_HINT = ('- To see how well a saved job fits your career memory, say "evaluate [job]" (the `evaluate` skill); '
               "it scores the posting against your facts and never changes the job's state.\n")


def skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


EVALUATE_PHRASES = (
    "**Trigger:**",
    "Save the posting verbatim to `jd.md`",
    "careeros evaluate <job> --init",
    "verbatim quote",
    "`class`",
    "`kind`",
    "`term`",
    "`years`",
    "every fit row",
    "`quote: null`",
    "\"yes\"",
    "(home)",
    "`job_facts.market`",
    "sources, confidence and `as_of`",
    "careeros evaluate <job>",
    "exit 1",
    "EVL002", "EVL003", "EVL004", "EVL005", "EVL006", "EVL007", "EVL008", "EVL009",
    "never edit `result:`",
    "tier, scores, confidence, caps",
    "action items",
    "never changes the job's state",
    "Ask the user before",
    "`covered`",
    "`adjacent`",
    "never say a requirement is met",
    "`memory` skill",
    "never edit `career/`",
    "does not prove",
    "EVIDENCE GATE",
)


@pytest.mark.parametrize("phrase", EVALUATE_PHRASES)
def test_evaluate_skill_encodes_the_rule(phrase: str) -> None:
    assert phrase in skill("evaluate")


def test_evaluate_never_instructs_an_unconditional_transition() -> None:
    text = skill("evaluate")
    assert "careeros transition" in text
    for m in re.finditer(r"careeros transition", text):
        before = text[:m.start()]
        section = before[before.rfind("\n## "):]
        paragraph = before[before.rfind("\n\n"):]
        assert re.search(r"\bAsk\b", section), "a transition must follow an ask"
        assert re.search(r"only if the user (says|agrees|confirms)", paragraph, re.I), "a transition must be conditional"


def test_evaluate_trigger_and_workspace_routing() -> None:
    assert "evaluate [job]" in skill("evaluate").splitlines()[2]
    claude = (TEMPLATES / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")
    assert '"evaluate [job]"' in claude and '"should I apply to [job]"' in claude
    row = next(line for line in claude.splitlines() if "should I apply to [job]" in line)
    assert ".claude/skills/evaluate/SKILL.md" in row


def test_prep_checks_for_a_fresh_evaluation_and_keeps_the_gate() -> None:
    text = skill("prep")
    for phrase in ("`EVL008`", "careeros evaluate", "run the `evaluate` skill first", "`covered`", "`adjacent`",
                   "`gap`", "`unverifiable`", "never proof"):
        assert phrase in text, phrase
    step2 = text.index("## STEP 2")
    assert step2 < text.index("fresh evaluation") < text.index("## STEP 4")
    assert text.count("## EVIDENCE GATE (mandatory)") == 1
    start = text.index("## EVIDENCE GATE (mandatory)")
    assert sha(text[start:text.index("\n---\n", start)]) == PREP_GATE_BEFORE


def test_browse_gained_exactly_one_closing_hint_line() -> None:
    text = skill("browse")
    assert text.endswith(BROWSE_HINT) and text.count(BROWSE_HINT) == 1
    assert sha(text[:-len(BROWSE_HINT)]) == BROWSE_BEFORE


def test_refresh_delivers_the_evaluate_skill_to_an_existing_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    target = root / ".claude" / "skills" / "evaluate" / "SKILL.md"
    target.unlink()
    refreshed = scaffold(root, refresh=True, runtime="claude")
    assert ".claude/skills/evaluate/SKILL.md" in refreshed
    assert target.read_text(encoding="utf-8") == skill("evaluate")


def test_the_gpt_workspace_has_no_evaluation() -> None:
    gpt = TEMPLATES / "gpt-workspace"
    assert not any(p.name == "evaluate" for p in gpt.rglob("*"))
    for path in gpt.rglob("*.md"):
        body = path.read_text(encoding="utf-8")
        assert "careeros evaluate" not in body and "evaluation.md" not in body


# --- the worked example ---------------------------------------------------------------------------

def example_blocks() -> dict[str, str]:
    """The fenced blocks of the skill's complete example, by the label on the line before the fence."""
    text = skill("evaluate")
    section = text[text.index("## COMPLETE EXAMPLE"):]
    blocks = {}
    for m in re.finditer(r"^\*\*(?P<label>[^*\n]+)\*\*[^\n]*\n```[a-z]*\n(?P<body>.*?)\n```", section, re.M | re.S):
        blocks[m.group("label")] = m.group("body")
    return blocks


def test_the_skills_complete_example_is_accepted_by_careeros_evaluate(career) -> None:
    root, ids = career
    blocks = example_blocks()
    assert set(blocks) == {"profile.md (the parts the evaluation reads)", "jd.md", "evaluation.md agent half"}
    (root / "profile.md").write_text(blocks["profile.md (the parts the evaluation reads)"] + "\n", encoding="utf-8")
    job = add_job(root, "example-platform", company="Example Corp", title="Senior Platform Engineer")
    job_dir = job["path"].parent
    (job_dir / "jd.md").write_text(blocks["jd.md"] + "\n", encoding="utf-8")
    # the example names facts by placeholder; the invented memory fixture supplies the real ids
    placeholders = {"ach_incident": ids["incident"], "skl_kubernetes": ids["skill_kubernetes"]}
    agent_text = blocks["evaluation.md agent half"]
    used = set(re.findall(r"\b(?:ach|skl|exp|prj|crt|edu)_[a-z_0-9]+\b", agent_text))
    assert used and used <= set(placeholders), used
    for name, real in placeholders.items():
        agent_text = agent_text.replace(name, real)
    agent = yaml.safe_load(agent_text)
    assert list(agent) == ["agent"]

    res = runner.invoke(app, ["evaluate", job["id"], "--init", "--workspace", str(root)])
    assert res.exit_code == 0, res.output
    path = job_dir / "evaluation.md"
    rec = record.load(path, root)
    fm, body = rec.fm, rec.body
    from careeros.core.workspace import join_frontmatter
    fm["agent"] = agent["agent"]
    path.write_text(join_frontmatter(fm, body), encoding="utf-8")

    res = runner.invoke(app, ["evaluate", job["id"], "--workspace", str(root)])
    assert res.exit_code == 0, res.output
    result = record.load(path, root).result
    assert result["tier"] in {"pursue", "stretch", "weak", "skip"} and result["caps"] == []
    assert {r["id"]: r["outcome"] for r in result["requirements"]} == {
        "r1": "covered", "r2": "covered", "r3": "adjacent", "r4": "gap"}      # what the skill says follows the example
    again = runner.invoke(app, ["evaluate", job["id"], "--json", "--workspace", str(root)])
    assert again.exit_code == 0 and '"unchanged"' in again.stdout
