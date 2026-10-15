"""The compensation wording of the `browse` and `evaluate` skills, the worked example that `careeros comp` and
`careeros evaluate` really accept, and the compensation docs and release text. Invented data only."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from helpers import add_job
from test_skills_evaluation import BROWSE_BEFORE, BROWSE_HINT, COMP_BROWSE_ADDITIONS, PREP_GATE_BEFORE, example_blocks, sha, skill
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core.compensation import rules
from careeros.workspace.scaffold import scaffold

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"
runner = CliRunner()

BROWSE_PHRASES = (
    "careeros comp [job-slug] --init",
    "compensation.md",
    "observations: []",
    "`posted`", "`recruiter`", "`estimate`",
    "`period`", "`as_of`", "`confidence`",
    "copied verbatim",
    "Never invent a figure, a source or a date",
    "careeros comp [job-slug]",
)


@pytest.mark.parametrize("phrase", BROWSE_PHRASES)
def test_browse_records_pay_in_compensation_md(phrase: str) -> None:
    assert phrase in skill("browse")


def test_browse_keeps_its_comp_check_and_table() -> None:
    text = skill("browse")
    assert "### Comp check (required — do not skip)" in text
    assert "mark them unknown rather than guessing" in text
    assert "| # | Score | Company | Title | Location | Comp (source, confidence) | Sponsor | URL |" in text


def test_browse_changes_only_the_comp_step_and_the_closing_hint() -> None:
    text = skill("browse")
    assert text.endswith(BROWSE_HINT + COMP_BROWSE_ADDITIONS["hint"])
    rest = text[:-len(COMP_BROWSE_ADDITIONS["hint"])][:-len(BROWSE_HINT)]
    assert rest.count(COMP_BROWSE_ADDITIONS["step"]) == 1
    assert sha(rest.replace(COMP_BROWSE_ADDITIONS["step"], "")) == BROWSE_BEFORE


EVALUATE_COMP_PHRASES = (
    "careeros comp <job>",
    "never edit `compensation.md`",
    "Never invent a conversion rate, a source or a date",
    "only if the user or a cited source gave one",
    "comparability",
    "the cap outcome",
    "## MIGRATING A 0.5.0 EVALUATION",
)


@pytest.mark.parametrize("phrase", EVALUATE_COMP_PHRASES)
def test_evaluate_reads_the_derivation(phrase: str) -> None:
    assert phrase in skill("evaluate")


def test_evaluate_no_longer_asks_for_pay() -> None:
    text = skill("evaluate")
    rules_part = text[:text.index("## STEP 0")]
    step5 = text[text.index("## STEP 5"):text.index("## STEP 6")]
    for part in (rules_part, step5):
        assert "range:" not in part and "floor:" not in part and "`floor`" not in part and "`range`" not in part
    assert "not part of the agent half" in step5 or "no longer part of the agent half" in step5
    example = text[text.index("## COMPLETE EXAMPLE"):text.index("## NOTES")]
    agent = example[example.index("**evaluation.md agent half**"):]
    assert "compensation:" not in agent.split("```")[1]


def migration() -> str:
    text = skill("evaluate")
    start = text.index("## MIGRATING A 0.5.0 EVALUATION")
    return text[start:text.index("\n---\n", start)]


MIGRATION_RULES = (
    "never overwrite",
    "`compensation.md` that exists",
    "Migrated from evaluation.md (0.5.0)",
    "verbatim",
    "Show the proposed file",
    "only after the user agrees",
    "suggest `estimate`",
    "`posted` also needs a verbatim quote",
    "ask for the `period`",
    "Never invent a source, an `as_of`, a confidence, a currency or a basis",
    "never remove the legacy block before a valid `compensation.md` exists",
    "confirm the record covers it",
    "no scorer writes",
    "the scorer never writes a file",
    "re-detect the state from the files on every run",
    "(unchanged)",
    "never duplicates an observation",
)


@pytest.mark.parametrize("phrase", MIGRATION_RULES)
def test_migration_encodes_the_rule(phrase: str) -> None:
    assert phrase.lower() in migration().lower()


def test_migration_has_the_four_ordered_steps_with_preconditions() -> None:
    text = migration()
    positions = [text.index(f"**{label}**") for label in ("1. Write.", "2. Validate.", "3. Remove.", "4. Re-evaluate.")]
    assert positions == sorted(positions)
    write, validate, remove, reeval = (text[a:b] for a, b in zip(positions, positions[1:] + [len(text)]))
    assert "Precondition: there is no `compensation.md`" in write
    assert "careeros comp <job>" in validate and "CMP" in validate and "stop" in validate.lower()
    assert "Precondition: `compensation.md` exists, validates and the legacy block is still present" in remove
    assert "beside the observations" in remove and "confirm" in remove
    assert "Precondition: the block is removed and the record validates" in reeval
    assert "careeros evaluate <job>" in reeval


RECOVERY_ROWS = (
    ("legacy block, no `compensation.md`", "step 1"),
    ("legacy block, `compensation.md` present and valid", "resume at step 3"),
    ("legacy block, `compensation.md` present but invalid", "step 2"),
    ("no legacy block, valid non-empty `compensation.md`, evaluation stale", "step 4"),
    ("no legacy block, no or empty `compensation.md`, a stale version 1 result", "result.basis.cap_inputs"),
    ("no legacy block, valid non-empty record, evaluation current", "nothing to do"),
)


def test_the_recovery_table_has_all_six_rows() -> None:
    text = migration()
    table = [line for line in text.splitlines() if line.startswith("| ") and "State found" not in line and "---" not in line]
    assert len(table) == 6
    for (state, action), line in zip(RECOVERY_ROWS, table):
        assert line.startswith(f"| {state}"), line
        assert action in line, line
    row5 = table[4]
    for phrase in ("range, currency, basis and `as_of`", "ask for the kind, the period and the source",
                   "no pay information", "step 4 with the empty record"):
        assert phrase in row5, phrase
    assert "never overwrite" in table[2].lower() and "delete the file" in table[2]


def test_migration_explains_the_four_evl003_refusals() -> None:
    text = migration()
    for phrase in ("there is no compensation.md yet", "compensation.md has errors", "compensation.md is empty",
                   "compensation.md exists and is valid"):
        assert phrase in text, phrase
    assert "EVL003" in text


@pytest.mark.parametrize("name", ["browse", "evaluate"])
def test_no_skill_instructs_an_unconditional_transition(name: str) -> None:
    text = skill(name)
    for m in re.finditer(r"careeros transition", text):
        before = text[:m.start()]
        paragraph = before[before.rfind("\n\n"):]
        assert re.search(r"only if the user (says|agrees|confirms)", paragraph, re.I), name


def test_the_migration_never_transitions() -> None:
    assert "transition" not in migration()


def test_prep_and_gpt_pins_still_hold() -> None:
    text = skill("prep")
    start = text.index("## EVIDENCE GATE (mandatory)")
    assert sha(text[start:text.index("\n---\n", start)]) == PREP_GATE_BEFORE


def test_scaffold_refresh_delivers_the_updated_skills(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    for name in ("browse", "evaluate"):
        target = root / ".claude" / "skills" / name / "SKILL.md"
        target.write_text("old", encoding="utf-8")
    refreshed = scaffold(root, refresh=True, runtime="claude")
    for name in ("browse", "evaluate"):
        assert f".claude/skills/{name}/SKILL.md" in refreshed
        assert (root / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8") == skill(name)


def test_the_workspace_layout_names_compensation_md() -> None:
    claude = (ROOT / "careeros" / "templates" / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "compensation.md" in claude
    assert claude.index("compensation.md") > claude.index("evaluation.md")


# --- the worked example --------------------------------------------------------------------------

def test_the_skills_compensation_example_is_accepted_by_careeros_comp(career) -> None:
    root, _ = career
    blocks = example_blocks()
    (root / "profile.md").write_text(blocks["profile.md (the parts the evaluation reads)"] + "\n", encoding="utf-8")
    job = add_job(root, "example-platform", company="Example Corp", title="Senior Platform Engineer")
    job_dir = job["path"].parent
    (job_dir / "jd.md").write_text(blocks["jd.md"] + "\n", encoding="utf-8")
    (job_dir / "compensation.md").write_text(blocks["compensation.md"] + "\n", encoding="utf-8")
    res = runner.invoke(app, ["comp", job["id"], "--json", "--workspace", str(root)])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["valid"] is True and data["market"] == "Atlantis (home)"
    assert data["floor"]["verified"] is True and data["floor"]["amount"] == 3500000
    assert data["effective"]["kind"] == "posted"
    assert (data["effective"]["low"], data["effective"]["high"]) == (3000000, 4200000)
    assert data["cap_fires"] is False
    assert [o["comparable"] for o in data["observations"]] == [True]
    again = runner.invoke(app, ["comp", job["id"], "--init", "--workspace", str(root)])
    assert again.exit_code == 1                        # the example is not rewritten by --init


# --- docs and release -----------------------------------------------------------------------------

def doc(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_docs_numbers_equal_the_code_constants() -> None:
    text = doc("compensation.md")
    assert f"{rules.FX_WINDOW_DAYS} days" in text and rules.FX_WINDOW_DAYS == 30
    assert f"{rules.OBSERVATION_STALE_DAYS} days" in text and rules.OBSERVATION_STALE_DAYS == 90


COMPENSATION_DOC_PHRASES = (
    "`compensation.md`", "`posted`", "`recruiter`", "`estimate`", "`FX_CONVERSION_MISSING`", "`FX_RATE_OUTSIDE_WINDOW`",
    "`FLOOR_UNVERIFIED`", "`BASIS_MISMATCH`", "CMP001", "CMP002", "CMP003", "CMP004", "CMP005", "CMP006", "CMP007",
    "CMP100", "`compensation.recorded`", "`compensation.rejected`", "careeros comp <job> --init", "EVL009",
    "Migrated from evaluation.md (0.5.0)", "read once as stale", "version 2", "touch at one point", "30–40",
    "equality does not cap", "never edits `compensation.md`",
)


@pytest.mark.parametrize("phrase", COMPENSATION_DOC_PHRASES)
def test_the_compensation_doc_covers(phrase: str) -> None:
    assert phrase.lower() in doc("compensation.md").lower()


def test_the_evaluation_doc_describes_version_2() -> None:
    text = doc("evaluation.md")
    for phrase in ("compensation.md", "version 2", "six hashes", "`compensation`", "EVL003", "EVL006", "stale"):
        assert phrase in text, phrase
    assert "Rules version 1" not in text


def test_nav_readme_changelog_and_version() -> None:
    assert "compensation.md" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "compensation" in (ROOT / "README.md").read_text(encoding="utf-8").lower()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert any(line.startswith("## 0.6.0") for line in changelog.splitlines()[:6])
    head = changelog[:changelog.index("## 0.5.0")]
    for phrase in ("careeros upgrade", "read as stale once", "explicit re-evaluation", "version 2"):
        assert phrase in head, phrase
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == "0.6.0"
