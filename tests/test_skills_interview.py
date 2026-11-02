"""The wording of the `interview` and `follow-up` skills for interview rounds and debriefs, the commands they and
`docs/interviews.md` show (parsed with the real CLI, enum values checked, the examples run), the numbers they quote
(compared with the code constants), `docs/interviews.md` compared with the code, and the 0.9.0 release text. Invented
data only."""

from __future__ import annotations

import hashlib
import re
import shlex
import tomllib
from pathlib import Path

import pytest
from helpers import add_job, make_workspace
from test_skills_approvals import parse_cli, sentences
from test_skills_evaluation import TEMPLATES, skill
from test_skills_outreach import BARE as OUTREACH_BARE
from test_skills_outreach import HARD_CLAIMS, resolves
from typer._click.exceptions import MissingParameter
from typer.testing import CliRunner

import careeros
from careeros.cli import interview as interview_cli
from careeros.cli.main import app
from careeros.core.interview import rules, service
from careeros.core.interview._common import parse_when

ROOT = Path(__file__).parent.parent
DOC = ROOT / "docs" / "interviews.md"
SKILL_NAMES = ("interview", "follow-up")
runner = CliRunner()

# sha256 of the interview skill's EVIDENCE GATE block and its STEP 5 (the prep file format) before this release
INTERVIEW_GATE_BEFORE = "2c98b1970f1a19af854459c4b7be8a7ea632b01490c4bd8f2de0df63fff409f4"
INTERVIEW_PREP_FORMAT_BEFORE = "14634d164b106f5fc465adb8c7a5b2b8645dd9cd5b59534e565302ca880862fc"
# sha256 of the follow-up skill's EVIDENCE GATE and APPROVALS blocks before this release
FOLLOW_UP_GATE_BEFORE = "2b77efaf890e5c16928cc0aa170983bb8308ec0e8510a7d1b7a77faa15e0012a"
FOLLOW_UP_APPROVALS_BEFORE = "b49b0ac90e9ed76f54a3597d99013896a69e679c0010391409d9dace3aebe8b8"


def text(name: str) -> str:
    return skill(name)


def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def block(body: str, heading: str) -> str:
    start = body.index(heading)
    return body[start:body.index("\n---\n", start)]


INTERVIEW_PINS = (
    "## STEP 0.5 \u2014 RECORD THE ROUND",
    "Rounds are recorded with `careeros interview`, not in `activity.md`.",
    "`careeros interview round list --job <job-slug> --json`",
    "Never add a round twice for the same job, stage and date.",
    "If the user says the round is already recorded, do not add it again: use that round's id.",
    "record it with the stage and interviewers exactly as the user gave them",
    "If the date is not known yet, ask for it; never guess one.",
    "Run `careeros interview upcoming` and tell the user what is due in the next 14 days",
    "A refusal ends that action and is relayed to the user",
    "do not retry it or work around it",
    "## STEP 7 \u2014 AFTER THE ROUND",
    "Only if the user says it took place, run `careeros interview round held <rnd_id> --source user`.",
    "Only if the user says it was cancelled, run `careeros interview round cancel <rnd_id> --reason \"<the user's reason>\" --source user`.",
    "Never record any of these from a guess, a calendar or the date alone.",
    "ask each of these on its own and wait for the answer before the next",
    "Never write a rating the user did not give.",
    "Record only what the user said, in their words",
    "`debrief` is refused unless the round is `held`.",
    "never edit the record by hand to fix it",
    "`activity.md` is a readable log, not the record of the round",
    "After the interview: run STEP 7 to record what happened and the debrief.",
)

FOLLOW_UP_PINS = (
    "## STEP 1.5 \u2014 INTERVIEW ROUNDS WAITING ON YOU",
    "Run `careeros interview needs-debrief`",
    "This step sends nothing and drafts nothing.",
    "use the `interview` skill (STEP 7)",
    "CareerOS records no email events; recorded interview rounds are listed in STEP 1.5.",
)


@pytest.mark.parametrize("phrase", INTERVIEW_PINS)
def test_interview_pins(phrase: str) -> None:
    assert phrase in text("interview")


@pytest.mark.parametrize("phrase", FOLLOW_UP_PINS)
def test_follow_up_pins(phrase: str) -> None:
    assert phrase in text("follow-up")


def test_the_interview_steps_are_ordered() -> None:
    body = text("interview")
    order = ["## EVIDENCE GATE", "## STEP 0 \u2014", "## STEP 0.5", "## STEP 1 \u2014", "## STEP 5", "## STEP 6", "## STEP 7", "## NOTES"]
    positions = [body.index(p) for p in order]
    assert positions == sorted(positions), positions
    record = body[body.index("## STEP 0.5"):body.index("## STEP 1 \u2014")]
    assert record.index("careeros interview round list") < record.index("careeros interview round add") \
        < record.index("careeros interview upcoming")
    after = body[body.index("## STEP 7"):body.index("## NOTES")]
    assert after.index("careeros interview round held") < after.index("careeros interview debrief")
    assert after.index("**Debrief, one question at a time.**") < after.index("careeros interview debrief")


def test_the_debrief_asks_the_rating_and_each_field_on_its_own() -> None:
    body = text("interview")
    after = body[body.index("**Debrief, one question at a time.**"):body.index("## NOTES")]
    questions = re.findall(r'^   - "([^"]+)"', after, re.M)
    assert len(questions) == 4, questions
    assert "own words" in questions[0]
    rating = next(x for x in sentences(after) if "`strong`, `mixed` or `weak`" in x)
    assert "only when" in rating and "clearly" in rating
    assert "ask the user to choose one" in after


def test_held_and_cancel_only_on_the_users_say_so() -> None:
    for name in SKILL_NAMES:
        for sentence in sentences(text(name)):
            if re.search(r"`careeros interview round (held|cancel)\b", sentence):
                assert re.match(r"[-*\d. ]*(\*\*[^*]+\*\* )?Only if the user says\b", sentence), sentence


def test_the_evidence_gate_and_the_prep_file_format_are_unchanged() -> None:
    body = text("interview")
    assert body.count("## EVIDENCE GATE (mandatory)") == 1
    assert sha(block(body, "## EVIDENCE GATE (mandatory)")) == INTERVIEW_GATE_BEFORE
    assert sha(block(body, "## STEP 5 \u2014 WRITE INTERVIEW PREP FILE")) == INTERVIEW_PREP_FORMAT_BEFORE
    body = text("follow-up")
    assert sha(block(body, "## EVIDENCE GATE (mandatory)")) == FOLLOW_UP_GATE_BEFORE
    assert sha(block(body, "## APPROVALS (mandatory)")) == FOLLOW_UP_APPROVALS_BEFORE


def test_step_6_no_longer_treats_activity_md_as_the_record() -> None:
    body = text("interview")
    step6 = body[body.index("## STEP 6"):body.index("## STEP 7")]
    assert step6.index("`activity.md` is a readable log, not the record of the round") < step6.index("Append to `activity.md`")
    notes = body[body.index("## NOTES"):]
    assert "log it to activity.md" not in notes


def test_the_follow_up_step_only_lists_and_sends_nothing() -> None:
    body = text("follow-up")
    step = body[body.index("## STEP 1.5"):body.index("## STEP 2")]
    assert body.index("## STEP 1 \u2014") < body.index("## STEP 1.5") < body.index("## STEP 2")
    assert "careeros approval" not in step and "careeros outreach" not in step
    for command in re.findall(r"`(careeros [^`]*)`", step):
        assert command.startswith("careeros interview needs-debrief"), command
    assert "CareerOS records no email and no interview events" not in body


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_no_skill_runs_approve_or_resolve_or_an_unconditional_transition(name: str) -> None:
    for sentence in re.split(r"(?<=[.])\s+|\n+", text(name)):
        for m in re.finditer(r"`(! )?careeros (approve\b|approval resolve\b)[^`]*`", sentence):
            guarded = re.search(r"^[-*\s]*(Never run|Tell the user|Ask the user|Only the user)|\b(ask the user to run|tell the user to run|Please run|to run `!)", sentence)
            assert m.group(1) or guarded, sentence
        if "careeros transition" in sentence:
            assert re.match(r"[-* ]*Only if the user\b", sentence), sentence


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_no_skill_calls_the_feature_a_security_boundary(name: str) -> None:
    for sentence in sentences(text(name)):
        if "security boundary" in sentence.lower():
            assert re.search(r"\bnot\b|\bno\b|\bnever\b", sentence, re.I), sentence
        assert not re.search(r"\b(is|as) a security boundary|cannot be bypassed|\bguarantees? that\b", sentence, re.I), sentence


def test_the_interview_skill_never_runs_approve_resolve_or_transition_for_rounds() -> None:
    body = text("interview")
    after = body[body.index("## STEP 0.5"):body.index("## STEP 1 \u2014")] + body[body.index("## STEP 7"):]
    for forbidden in ("careeros approve", "careeros approval", "careeros transition", "careeros ledger append"):
        assert forbidden not in after, forbidden


# --- the numbers come from the code --------------------------------------------------------------------------------------

def test_the_skill_numbers_are_the_code_constants() -> None:
    body = text("interview")
    assert {int(n) for n in re.findall(r"(\d+) days", body)} == {rules.UPCOMING_DEFAULT_DAYS}
    assert {int(n) for n in re.findall(r"at most (\d+)", body)} == {rules.MAX_INTERVIEWERS, rules.MAX_DEBRIEF_ITEMS}
    assert not re.findall(r"(\d+) days", text("follow-up")[text("follow-up").index("## STEP 1.5"):text("follow-up").index("## STEP 2")])


def test_the_doc_numbers_are_the_code_constants() -> None:
    body = doc()
    assert {int(n) for n in re.findall(r"(\d+) days", body)} == {rules.UPCOMING_DEFAULT_DAYS}
    assert {int(n) for n in re.findall(r"at most (\d+)", body)} == {rules.MAX_INTERVIEWERS, rules.MAX_DEBRIEF_ITEMS,
                                                                    rules.MAX_TEXT}
    assert f"from 0 to {interview_cli.MAX_DAYS}" in body


def test_the_rules_table_matches_the_code() -> None:
    section = doc().split("## Rules", 1)[1].split("\n## ", 1)[0]
    rows = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\| `([A-Z_]+)` \| (\d+) \|", section, re.M)}
    assert rows == {name: getattr(rules, name) for name in (
        "UPCOMING_DEFAULT_DAYS", "MAX_INTERVIEWERS", "MAX_DEBRIEF_ITEMS", "MAX_TEXT")}


# --- every command line parses with the real CLI and its values are real --------------------------------------------------

BARE = OUTREACH_BARE | {("careeros", "interview"), ("careeros", "interview", "round")}
ENUMS = {"--stage": rules.STAGES, "--format": rules.FORMATS, "--rating": rules.RATINGS, "--source": rules.SOURCES}


def command_lines(body: str) -> list[list[str]]:
    out = []
    for m in re.finditer(r"`(?:! )?(careeros [^`]*)`|^(careeros [^\n]*)$", body, re.M):
        line = (m.group(1) or m.group(2)).replace("\\|", "|")
        line = re.split(r"\s+#\s", line)[0]                 # a trailing shell comment is prose
        if "..." in line or "\u2192" in line:
            continue
        argv = shlex.split(re.sub(r"<([^>]*)>", lambda g: "<" + g.group(1).replace(" ", "_") + ">", line))
        if tuple(argv) in BARE or len(argv) < 2:
            continue
        out.append(argv)
    return out


def readme_section() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8").split("## Interview rounds and debriefs", 1)[1].split("\n## ", 1)[0]


def changelog_head() -> str:
    body = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return body[body.index("## 0.9.0"):body.index("## 0.8.0")]


SOURCES = {"interview": lambda: text("interview"), "follow-up": lambda: text("follow-up"), "doc": doc,
           "readme": readme_section, "changelog": changelog_head}


def check_values(argv: list[str], strict_source: bool) -> None:
    for opt, allowed in ENUMS.items():
        for i, a in enumerate(argv):
            if a != opt:
                continue
            value = argv[i + 1]
            if value.startswith("<") and value.endswith(">"):
                parts = value[1:-1].split("|")
                assert len(parts) == 1 or set(parts) <= set(allowed), argv
                assert not (strict_source and opt == "--source"), argv
            else:
                assert value in allowed, argv
            if strict_source and opt == "--source":
                assert value == "user", argv
    for opt in ("--when", "--next-date"):
        if opt in argv:
            value = argv[argv.index(opt) + 1]
            assert value.startswith("<") or parse_when(value) is not None, argv


@pytest.mark.parametrize("where", sorted(SOURCES))
def test_every_command_line_parses(where: str) -> None:
    lines = command_lines(SOURCES[where]())
    assert any(a[1] == "interview" for a in lines), where
    for argv in lines:
        try:
            parse_cli(argv[1:])
        except MissingParameter:                              # a bare command name in prose: it must still resolve
            assert not any(a.startswith("-") for a in argv), argv
            assert resolves(argv[1:]), argv
        if argv[1] == "interview":
            check_values(argv, strict_source=where in SKILL_NAMES)


def test_the_skills_mark_held_and_cancelled_with_source_user() -> None:
    seen = set()
    for name in SKILL_NAMES:
        for argv in command_lines(text(name)):
            if argv[1:3] == ["interview", "round"] and argv[3] in ("held", "cancel"):
                assert argv[argv.index("--source") + 1] == "user", argv
                seen.add(argv[3])
    assert seen == {"held", "cancel"}


def test_every_interview_command_is_taught_and_documented() -> None:
    def used(body: str) -> set[str]:
        return {" ".join(a[2:4]) if a[2] == "round" else a[2] for a in command_lines(body) if a[1] == "interview" and len(a) > 2}
    every = {"round add", "round list", "round show", "round held", "round cancel", "round reschedule", "debrief", "upcoming",
             "needs-debrief"}
    assert used(text("interview")) | used(text("follow-up")) == every
    assert used(doc()) == every
    commands = doc().split("## Commands", 1)[1].split("\n## ", 1)[0]
    table = "\n".join(line for line in commands.splitlines() if line.startswith("| `careeros interview"))
    assert used(table) == every, "the commands table names every command"


def fenced_examples(body: str) -> list[list[str]]:
    return [shlex.split(m.group(1)) for m in re.finditer(r"```bash\n(careeros interview round add[^\n]*)\n```", body)]


def test_the_round_add_examples_run_on_an_invented_workspace(tmp_path: Path) -> None:
    examples = fenced_examples(text("interview")) + fenced_examples(doc())
    assert len(examples) == 2
    for n, argv in enumerate(examples):
        root = tmp_path / f"ws{n}"
        make_workspace(root)
        add_job(root, "example-corp-platform", company="Example Corp")
        res = runner.invoke(app, [*argv[1:], "--workspace", str(root), "--json"])
        assert res.exit_code == 0, (argv, res.output)
        listed = runner.invoke(app, ["interview", "round", "list", "--workspace", str(root), "--json"])
        assert listed.exit_code == 0 and listed.output.count('"id": "rnd_') == 1


# --- docs/interviews.md compared with the code -------------------------------------------------------------------------

def code_severities() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / "careeros" / "core" / "interview").rglob("*.py"):
        for m in re.finditer(r'Issue\(\s*"(error|warning|info)",\s*rules\.(INT\d+)', path.read_text(encoding="utf-8")):
            assert out.setdefault(m.group(2), m.group(1)) == m.group(1), m.group(2)
    for code, severity in service._SEVERITY.items():
        assert out.setdefault(code, severity) == severity, code
    return out


def test_the_int_table_matches_the_code() -> None:
    section = doc().split("## Validation codes", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `(INT\d+)` \| (error|warning|info) \|", section, re.M)
    expected = code_severities()
    assert sorted(expected) == ["INT001", "INT002", "INT003", "INT004", "INT005", "INT006", "INT007", "INT008", "INT009",
                                "INT100"], expected
    assert dict(rows) == expected and len(rows) == len(expected)


def test_the_refusal_code_list_matches_the_code() -> None:
    section = doc().split("## Refusal codes", 1)[1].split("\n## ", 1)[0]
    found = re.findall(r"^\| `([a-z_]+)` \|", section, re.M)
    assert sorted(found) == sorted(rules.REFUSAL_CODES) and len(found) == len(set(found))
    assert "record_unavailable" in found


def test_the_doc_names_every_event_value_and_exit_code() -> None:
    body = doc()
    for value in (*rules.KNOWN_EVENTS, *rules.LABEL_PRECEDENCE, *rules.STAGES, *rules.FORMATS, *rules.RATINGS, *rules.SOURCES,
                  *rules.PREP_STATUSES, *rules.RECORD_STATUSES, *rules.TERMINAL_JOB_STATUSES):
        assert f"`{value}`" in body, value
    for flag in ("added_at", "held_at", "cancelled_at", "debriefed_at", "cancel_reason", "needs_debrief", "needs_update",
                 "incomplete", "date_unrecorded", "debrief_recorded"):
        assert re.search(rf"`{flag}[`:]", body), flag
    assert "**Exit codes:** 0 ok; 1 refused" in body and "2 usage, or no workspace" in body
    assert "rnd_xxxxxxxxxx" in body and "job_xxxxxxxxxx" in body


DOC_PHRASES = (
    "## What each result is derived from",
    "| Round identity, which events happened, `held`, `cancelled`, `debriefed` | the ledger only |",
    "the validated record (editable source data)",
    "A hand edit of a record can change what the views display. It never changes a round's identity",
    "An orphan record (no `round_added` event) or one whose `id` or `job` disagrees with its event feeds nothing.",
    "is reported as `INT005` and is not applied",
    "a round that has only malformed events gets no flags entry",
    "They create no file, event or lock file.",
    "No output is capped or truncated",
    "## Incomplete rounds",
    "never silently recreated or repaired",
    "`round reschedule` and `debrief` are refused `record_unavailable`",
    "## Interrupted updates",
    "a death between them leaves the new file and no event",
    "re-run `careeros interview round reschedule <rnd_id> --when <date>`",
    "it stays in `needs-debrief`",
    "Nothing repairs anything automatically",
    "the same way `careeros check` hashes a draft",
    "`careeros transition` for a job before the interview stages, and `round cancel` or `round held` for a terminal job",
    "Nothing is ever run for you.",
    "`careeros validate` is read-only",
)

LIMIT_PHRASES = (
    "The skills are pinned by their wording only and were not run live.",
    "This is not a security boundary.",
    "Truncating the tail of `ledger.jsonl` cannot be detected by a hash chain.",
    "The clock floor is fail-closed",
    "Debrief ratings are your own self-report and are not verified.",
    "`held` and `cancelled` are what you say happened",
    "An interrupted update is reported (`INT008`, `INT009`) and recovered by re-running the command, never repaired automatically.",
    "A held round whose record is gone stays in the debrief queue until the record is restored.",
    "A duplicate round means the same job, stage and exact `scheduled_for` text",
    "a date-only value and a datetime on the same day are both accepted",
    "Prep counts as `checked` if any passing check of the same text exists, even if a later check of that text failed.",
    "Rewriting a record drops YAML comments in its frontmatter and normalises body line endings.",
    "An interviewer name cannot contain a colon",
    "A malformed `interview.*` event is reported (`INT005`) and not applied",
    "No view or list is truncated",
    "importing existing `interview-prep.md` or `activity.md` history",
    "GPT workspace",
)


@pytest.mark.parametrize("phrase", DOC_PHRASES)
def test_the_doc_states_the_rules(phrase: str) -> None:
    assert phrase in doc()


@pytest.mark.parametrize("phrase", LIMIT_PHRASES)
def test_the_doc_states_the_limits(phrase: str) -> None:
    assert phrase in doc()[doc().index("## Limits"):]


def test_the_doc_states_the_incomplete_round_rules_in_one_place() -> None:
    body = doc()
    section = body[body.index("## Incomplete rounds"):body.index("\n## ", body.index("## Incomplete rounds") + 1)]
    for phrase in ("`INT003`", "`INT001`", "`INT004`", "`round list` includes it", "`upcoming` excludes it",
                   "the `needs_update` group excludes it", "the `needs_debrief` group includes a held incomplete round",
                   "`round held` and `round cancel` still work", "the same `id` and `job`"):
        assert phrase in section, phrase


def test_no_doc_sentence_claims_a_hard_limit_or_boundary() -> None:
    for sentence in sentences(doc()):
        for m in re.finditer(HARD_CLAIMS, sentence, re.I):
            assert m.group(0).lower() == "boundary" and re.search(r"\bnot a security boundary\b", sentence), sentence


ACTIVITY_DRIVES_ROUNDS = (r"(interview )?rounds? (are|is) (tracked|recorded|kept) in `activity\.md`|only record of the round"
                          r"|drives? (interview|round)|source of truth for (interview|round)|tracks? (interview )?rounds")


def test_no_docs_page_says_activity_md_drives_round_tracking() -> None:
    pages = [*(ROOT / "docs").glob("*.md"), *(ROOT / "docs" / "cowork").glob("*.md"), ROOT / "README.md",
             TEMPLATES / "workspace" / "CLAUDE.md", *(TEMPLATES / "workspace" / ".claude" / "skills").glob("*/SKILL.md")]
    for path in pages:
        for sentence in sentences(path.read_text(encoding="utf-8")):
            if "activity.md" in sentence:
                assert not re.search(ACTIVITY_DRIVES_ROUNDS, sentence, re.I), (path.name, sentence)


# --- CLAUDE.md, README, CHANGELOG, nav, version, delivery ----------------------------------------------------------------

def claude() -> str:
    return (TEMPLATES / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_md_routes_debriefs_and_names_rounds() -> None:
    body = claude()
    row = next(line for line in body.splitlines() if ".claude/skills/interview/SKILL.md" in line)
    for trigger in ('"prep for [company] interview"', '"debrief my [company] interview"', '"what interviews are coming up?"'):
        assert trigger in row, trigger
    layout = body[body.index("## Workspace layout"):]
    line = next(x for x in layout.splitlines() if x.startswith("rounds/"))
    assert "`careeros interview round add`" in line and "derived from the ledger" in line
    assert layout.index("\ntargets/") < layout.index("\nrounds/") < layout.index("\nactivity.md")


def test_version_changelog_readme_and_nav() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.9.0"
    assert careeros.__version__ == "0.9.0"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("\n## ", 2)[1].startswith("0.9.0")
    head = changelog_head()
    for phrase in ("run `careeros upgrade` to receive the updated skills", "Rounds start empty",
                   "existing `interview-prep.md` and `activity.md` history is not imported", "INT001", "INT009", "INT100",
                   "`record_unavailable`", "docs/interviews.md", "2026-10-31-interview-rounds-design.md",
                   "not a security boundary"):
        assert phrase in head, phrase
    section = readme_section()
    for phrase in ("careeros interview upcoming", "careeros interview needs-debrief", "docs/interviews.md",
                   "not a security boundary", "Run `careeros upgrade` to receive the updated skills"):
        assert phrase in section, phrase
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    layout = readme[readme.index("## Workspace layout"):readme.index("## Workspace health")]
    assert layout.index("\ntargets/") < layout.index("\nrounds/") < layout.index("\nactivity.md")
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "Interview rounds and debriefs: interviews.md" in nav
    assert nav.index("outreach.md") < nav.index("interviews.md") < nav.index("Cowork")


def test_the_updated_skills_are_delivered_by_upgrade_refresh(tmp_path: Path) -> None:
    from careeros.workspace.scaffold import scaffold
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    for name in SKILL_NAMES:
        (root / ".claude" / "skills" / name / "SKILL.md").write_text("old copy\n", encoding="utf-8")
    refreshed = scaffold(root, refresh=True, runtime="claude")
    for name in SKILL_NAMES:
        assert f".claude/skills/{name}/SKILL.md" in refreshed
        assert (root / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8") == text(name)


def test_the_gpt_workspace_has_no_interview_round_commands() -> None:
    for path in (TEMPLATES / "gpt-workspace").rglob("*"):
        if path.is_file():
            assert "careeros interview" not in path.read_text(encoding="utf-8", errors="replace"), path
