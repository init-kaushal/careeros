"""The wording of the `targets`, `outreach`, `follow-up` and `browse` skills for the outreach state feature, the commands
they show (parsed and run with the real CLI), the numbers they quote (compared with the code constants), `docs/outreach.md`
compared with the code, and the 0.8.0 release text. Invented data only."""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

import pytest
from helpers import add_job, make_workspace
from test_skills_approvals import parse_cli, sentences
from test_skills_evaluation import OUTREACH_BROWSE_ADDITIONS, SKILLS, TEMPLATES, skill
from typer._click.exceptions import MissingParameter
from typer.testing import CliRunner

import careeros
from careeros.cli.main import app
from careeros.core.approvals import rules as approval_rules
from careeros.core.outreach import rules

ROOT = Path(__file__).parent.parent
DOC = ROOT / "docs" / "outreach.md"
OUTREACH_SKILLS = ("targets", "outreach", "follow-up", "browse")
runner = CliRunner()


def text(name: str) -> str:
    return skill(name)


def doc() -> str:
    return DOC.read_text(encoding="utf-8")


# --- wording pins (each is mutation-checked: removing it from a scratch copy of the skill makes its test fail) -------

TARGETS_PINS = (
    '**Trigger:** "build target companies" / "find targets for [market]" / "show targets" / "refresh targets"',
    "This skill never sends anything: no invite, no message, no email.",
    "at most 12 new candidates per market per run",
    "the \"Target companies\" line of `profile.md`",
    "saved jobs scoring 7 or higher",
    "the market file's sponsor register",
    "the market's regional boards",
    "Grade fit and sponsorship from evidence only.",
    "Unverified is an acceptable value; a guess is not.",
    "Show one table per market, in the ranking order",
    "home market by fit descending",
    "then fit descending",
    "`careeros outreach target add`",
    "--evidence",
    "Record an approval or a drop only when the user says so, with `--source user`.",
    "`careeros outreach target approve <slug> --source user`",
    "`careeros outreach target drop <slug> --source user --reason",
    "Mode B1b",
    "--found-in target:<slug>",
    "Refresh before drafting",
    "update the `read_on` date",
    "Say 'reach out to my targets' to draft introductions.",
    "A refusal such as `duplicate`, `invalid_input`, `identity_conflict` or `ledger_corrupt` ends that action; do not retry it or work around it.",
    "(one `recruiter`, one `hiring_manager`)",
    "run `careeros validate`",
    "If an evidence source can no longer be re-read, tell the user, leave the value as it is and do not guess.",
)

OUTREACH_PINS = (
    "Supports three outreach modes.",
    "## MODE C — PRE-OPENING INTRODUCTION",
    "Run `careeros outreach cap`",
    "Run `careeros outreach next --json`",
    "Never draft more than `remaining` notes",
    "If `careeros outreach cap` or `careeros outreach next` refuses with `cap_config_invalid`, stop and tell the user what it said",
    "Do not pick anyone `careeros outreach next` did not list.",
    "the `targets` skill's refresh before you draft",
    "**300 characters at most**",
    "says who the user is in one clause",
    "one concrete, real fact about the company or the person",
    "never says or implies that an opening exists",
    "never asks for a job or a referral",
    "never claims a relationship",
    "follows the local norms in `markets/[country].md`",
    "Run the note through the `humanize` skill first",
    "then follow A4 and A5 unchanged",
    "A refusal ends the action for that person and is relayed to the user",
    "`person_closed`, `cap_reached`, `cap_config_invalid` or `identity_conflict`",
    "Do not retry it, work around it or edit a record or `profile.md` to get past it.",
    "`careeros outreach record <per_id> --accepted --source user`",
    "`careeros outreach record <per_id> --replied --source user`",
    "Nothing in `careeros outreach` sends anything.",
    "`activity.md` is still appended to as a readable log, but nothing parses it for state.",
    "If `remaining` is 0, say so with the date, and stop.",
    "`cap_reached` ends Mode C",
    "**stop and wait**",
    "If it is `closed`, stop and tell the user.",
    "A contact with `source: job` has a posted opening: use Mode A for them, not Mode C. Only contacts with `source: target` use Mode C.",
    "For each draft the user has OK'd for sending, one contact at a time,",
    "invites left this week: R",
    "[N]/300",
    "On \"regenerate\": redraft with a different angle. Repeat until the user picks a note. Nothing is sent yet.",
)

FOLLOW_UP_PINS = (
    "The LinkedIn invite and application parts of the queue come from `careeros outreach queue`.",
    "Warm contacts come from `careeros outreach warm`.",
    "Email and interview follow-ups are not covered by `careeros outreach queue`; they stay prose and come from `activity.md`.",
    "`activity.md` is still appended to as a readable log, but nothing parses it for state.",
    "`careeros outreach record <per_id> --accepted --source user`",
    "`careeros outreach record <per_id> --replied --source user`",
    "A refusal ends the action for that person and is relayed to the user",
    "`person_closed`, `cap_reached`, `cap_config_invalid` or `identity_conflict`",
    "History from before 0.8.0 is not imported",
    "do not recompute them from `activity.md`",
    "also, on the user's say-so only, run `careeros outreach record <per_id> --closed --reason no_response --source user`",
    "Email cadences are closed in `activity.md` only.",
)

BROWSE_PINS = tuple(OUTREACH_BROWSE_ADDITIONS.values())


@pytest.mark.parametrize("phrase", TARGETS_PINS)
def test_targets_pins(phrase: str) -> None:
    assert phrase in text("targets")


@pytest.mark.parametrize("phrase", OUTREACH_PINS)
def test_outreach_pins(phrase: str) -> None:
    assert phrase in text("outreach")


@pytest.mark.parametrize("phrase", FOLLOW_UP_PINS)
def test_follow_up_pins(phrase: str) -> None:
    assert phrase in text("follow-up")


@pytest.mark.parametrize("key", sorted(OUTREACH_BROWSE_ADDITIONS))
def test_browse_pins(key: str) -> None:
    assert text("browse").count(OUTREACH_BROWSE_ADDITIONS[key]) == 1


def test_browse_checks_existing_people_before_searching_and_defaults_the_route_to_a_warm_contact() -> None:
    body = text("browse")
    d0, d, d2, e, e2 = (body.index(m) for m in ("**D0.", "**D. Find hiring contacts", "**D2.", "**E. Offer the route", "**E2."))
    assert d0 < d < d2 < e < e2
    block = OUTREACH_BROWSE_ADDITIONS["existing"]
    for phrase in ("`careeros outreach person list --json`", "`company_slug`", "before you search"):
        assert phrase in block, phrase
    assert "--found-in job:<REF>" in OUTREACH_BROWSE_ADDITIONS["add"]
    assert "reach out first via that warm contact" in OUTREACH_BROWSE_ADDITIONS["route"]
    assert "`careeros outreach warm`" in OUTREACH_BROWSE_ADDITIONS["route"]


def test_the_outreach_modes_are_ordered_and_pre_opening_uses_the_approvals_flow() -> None:
    body = text("outreach")
    a4, a6, b, c, notes = (body.index(m) for m in ("### A4", "### A6", "## MODE B", "## MODE C", "## NOTES"))
    assert a4 < a6 < b < c < notes
    mode_c = body[c:notes]
    order = ["### C1", "### C2", "### C3", "### C4", "### C5"]
    positions = [mode_c.index(p) for p in order]
    assert positions == sorted(positions)
    assert mode_c.index("careeros outreach cap") < mode_c.index("careeros outreach next") < mode_c.index("humanize")
    assert mode_c.index("humanize") < mode_c.index("careeros check") < mode_c.index("A4 and A5")
    assert "careeros approval" not in mode_c.replace("A4 and A5", ""), "mode C reuses A4 and A5; it does not restate the flow"


def test_pre_opening_note_is_asked_about_like_a3_and_the_stop_rules_sit_in_mode_c() -> None:
    body = text("outreach")
    mode_c = body[body.index("## MODE C"):body.index("## NOTES")]
    assert mode_c.count("Use this note, or regenerate?") == 1
    assert mode_c.index("Use this note, or regenerate?") < mode_c.index("### C4")
    assert mode_c.index("`source: job`") < mode_c.index("`stale`", mode_c.index("`source: job`"))


def test_follow_up_close_cadence_records_closed_only_for_linkedin_and_after_the_users_word() -> None:
    body = text("follow-up")
    step5 = body[body.index("## STEP 5"):body.index("## NOTES")]
    sentence = next(x for x in sentences(step5) if "outreach record" in x)
    assert "on the user's say-so only" in sentence and "LinkedIn contact" in sentence


def test_follow_up_step_one_names_the_queue_before_the_prose_rows() -> None:
    body = text("follow-up")
    step1 = body[body.index("## STEP 1"):body.index("## STEP 2")]
    assert step1.index("careeros outreach queue") < step1.index("careeros outreach warm") < step1.index("### Email and interview follow-ups")


# --- the rules every outreach skill carries -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", OUTREACH_SKILLS)
def test_no_skill_runs_approve_or_resolve_itself(name: str) -> None:
    for sentence in re.split(r"(?<=[.])\s+|\n+", text(name)):
        for m in re.finditer(r"`(! )?careeros (approve\b|approval resolve\b)[^`]*`", sentence):
            guarded = re.search(r"^[-*\s]*(Never run|Tell the user|Ask the user|Only the user)|\b(ask the user to run|tell the user to run|Please run|to run `!)", sentence)
            assert m.group(1) or guarded, sentence
        assert not re.match(r"[-*\d. ]*Run `careeros (approve|approval resolve)", sentence), sentence


@pytest.mark.parametrize("name", OUTREACH_SKILLS)
def test_a_transition_is_never_unconditional(name: str) -> None:
    for sentence in sentences(text(name)):
        if "careeros transition" in sentence:
            assert re.match(r"[-* ]*Only if the user\b", sentence), sentence


def test_targets_never_sends_and_never_drives_an_approval() -> None:
    body = text("targets")
    for forbidden in ("careeros approval", "careeros approve", "form_input", "Send invitation", "Click Connect", "careeros transition"):
        assert forbidden not in body, forbidden


@pytest.mark.parametrize("name", OUTREACH_SKILLS)
def test_no_skill_calls_the_feature_a_security_boundary_or_promises_enforcement(name: str) -> None:
    for sentence in sentences(text(name)):
        if "security boundary" in sentence.lower():
            assert re.search(r"\bnot\b|\bno\b|\bnever\b", sentence, re.I), sentence
        assert not re.search(r"\b(is|as) a security boundary|cannot be bypassed|\bguarantees? that\b", sentence, re.I), sentence


@pytest.mark.parametrize("name", ("outreach", "follow-up"))
def test_the_approval_rules_of_the_message_skills_are_untouched(name: str) -> None:
    body = text(name)
    start = body.index("## APPROVALS (mandatory)")
    section = body[start:body.index("\n---\n", start)]
    for phrase in ("**Stop and wait.**", "Never run `careeros approve` or `careeros approval resolve`: they record the user's own decision.",
                   "Send exactly the approved text: no last-second polishing, no appended greeting, no extra content.",
                   "not a security boundary"):
        assert phrase in section, phrase


# --- the numbers come from the code ---------------------------------------------------------------------------------------

def days(section: str) -> set[int]:
    return {int(n) for n in re.findall(r"(\d+) days", section)}


def test_follow_up_numbers_are_the_code_constants() -> None:
    body = text("follow-up")
    queue_part = body[body.index("## STEP 1"):body.index("### Email and interview follow-ups")]
    assert days(queue_part) == {rules.INVITE_FOLLOWUP_DAYS, rules.APPLICATION_FOLLOWUP_DAYS}
    prose_part = body[body.index("### Email and interview follow-ups"):body.index("## STEP 2")]
    assert days(prose_part) == {5, 3}, "the prose-only rows keep their old numbers; they are not in the code"


def test_outreach_numbers_are_the_code_constants() -> None:
    mode_c = text("outreach")
    mode_c = mode_c[mode_c.index("## MODE C"):mode_c.index("## NOTES")]
    assert days(mode_c) == {rules.CAP_WINDOW_DAYS}
    assert {int(n) for n in re.findall(r"default (\d+)", mode_c)} == {rules.DEFAULT_WEEKLY_CAP}
    assert {int(n) for n in re.findall(r"(\d+) characters", text("outreach"))} == {300}   # LinkedIn's limit; not a code constant
    assert {int(n) for n in re.findall(r"at most (\d+) contacts? per target", text("targets"))} == {rules.MAX_CONTACTS_PER_TARGET}
    assert {int(n) for n in re.findall(r"(\d+) days", text("targets"))} == {rules.TARGET_STALE_DAYS}


def test_targets_numbers_that_are_not_in_the_code_are_the_old_specs() -> None:
    body = text("targets")
    assert {int(n) for n in re.findall(r"at most (\d+) new candidates", body)} == {12}
    assert {int(n) for n in re.findall(r"scoring (\d+) or higher", body)} == {7}


# --- every command line parses with the real CLI and the examples run --------------------------------------------------------

BARE = {("careeros", "approval"), ("careeros", "approval", "resolve"), ("careeros", "approval", "done"), ("careeros", "outreach"),
        ("careeros", "approve"), ("careeros", "transition")}


def command_lines(name: str) -> list[list[str]]:
    out = []
    body = text(name)
    for m in re.finditer(r"`(?:! )?(careeros [^`]*)`|^(careeros [^\n]*)$", body, re.M):
        line = m.group(1) or m.group(2)
        if "..." in line or "→" in line:
            continue
        argv = shlex.split(re.sub(r"<([^>]*)>", lambda g: "<" + g.group(1).replace(" ", "_") + ">", line))
        if tuple(argv) in BARE or len(argv) < 2:
            continue
        out.append(argv)
    return out


def resolves(argv: list[str]) -> bool:
    import typer.main
    cmd = typer.main.get_command(app)
    ctx = cmd.make_context("careeros", [], resilient_parsing=False)
    args = list(argv)
    while hasattr(cmd, "resolve_command"):
        name, sub, args = cmd.resolve_command(ctx, args)
        if not hasattr(sub, "resolve_command"):
            return True
        cmd = sub
    return False


@pytest.mark.parametrize("name", OUTREACH_SKILLS)
def test_every_command_line_in_the_skills_parses(name: str) -> None:
    lines = command_lines(name)
    assert len(lines) >= 3, name
    outreach_lines = [a for a in lines if a[1] == "outreach"]
    if name != "browse":
        assert outreach_lines, name
    for argv in lines:
        try:
            parse_cli(argv[1:])
        except MissingParameter:                              # a bare command name in prose: it must still resolve
            assert not any(a.startswith("-") for a in argv), argv
            assert resolves(argv[1:]), argv


def test_every_outreach_subcommand_the_skills_use_exists_and_all_of_them_are_taught() -> None:
    used = {" ".join(a[2:4]) if a[2] in ("person", "target") else a[2] for n in OUTREACH_SKILLS for a in command_lines(n) if a[1] == "outreach"}
    for expected in ("person list", "person add", "person link", "target add", "target approve", "target drop", "target list",
                     "record", "queue", "cap", "next", "warm"):
        assert expected in used, expected


def fenced_examples() -> list[tuple[str, list[str]]]:
    out = []
    for name in OUTREACH_SKILLS:
        for m in re.finditer(r"```bash\n(?P<body>careeros outreach (?:person|target) add[^\n]*)\n```", text(name)):
            out.append((name, shlex.split(m.group("body"))))
    return out


def test_the_examples_exist() -> None:
    kinds = {(n, a[2]) for n, a in fenced_examples()}
    assert ("targets", "target") in kinds and ("targets", "person") in kinds
    assert ("outreach", "person") in kinds and ("browse", "person") in kinds
    assert len(fenced_examples()) == 4


VALUE_COMMANDS = (("outreach", "record"), ("outreach", "target", "drop"), ("outreach", "target", "approve"))


def test_decision_and_outcome_commands_pass_source_user_with_the_value() -> None:
    """click accepts any string for --source, so the pin is on the VALUE; `closed` also needs a valid reason."""
    seen = set()
    for name in OUTREACH_SKILLS:
        for argv in command_lines(name):
            key = next((k for k in VALUE_COMMANDS if tuple(argv[1:1 + len(k)]) == k), None)
            if key is None or not any(a.startswith("-") for a in argv):
                continue
            seen.add((name, key, "--closed" in argv))
            assert "--source" in argv and argv[argv.index("--source") + 1] == "user", argv
            if "--closed" in argv:
                reason = argv[argv.index("--reason") + 1]
                assert set(reason.strip("<>").split("|")) <= set(rules.CLOSED_REASONS), argv
    assert ("follow-up", ("outreach", "record"), True) in seen
    assert ("outreach", ("outreach", "record"), True) in seen
    assert any(k == ("outreach", "target", "drop") for _, k, _ in seen) and any(k == ("outreach", "target", "approve") for _, k, _ in seen)


def test_example_person_and_target_commands_run_on_an_invented_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    make_workspace(root)
    (root / "markets").mkdir()
    (root / "markets" / "australia.md").write_text("# Australia\n", encoding="utf-8")
    add_job(root, "example-corp-platform", company="Example Corp")
    examples = fenced_examples()
    for kind in ("target", "person"):                    # targets first: a person can be found in a target
        for name, argv in examples:
            if argv[2] != kind:
                continue
            res = runner.invoke(app, [*argv[1:], "--workspace", str(root), "--json"])
            assert res.exit_code == 0, (name, argv, res.output)
    listed = runner.invoke(app, ["outreach", "person", "list", "--workspace", str(root), "--json"])
    assert listed.exit_code == 0
    assert listed.output.count('"id": "per_') == len([1 for _, a in examples if a[2] == "person"])


# --- CLAUDE.md, docs, README, CHANGELOG, nav, version ------------------------------------------------------------------------

def claude() -> str:
    return (TEMPLATES / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_md_routes_the_new_skill_and_names_the_new_folders() -> None:
    body = claude()
    row = next(line for line in body.splitlines() if "build target companies" in line)
    assert ".claude/skills/targets/SKILL.md" in row
    assert "reach out to my targets" in next(line for line in body.splitlines() if ".claude/skills/outreach/SKILL.md" in line)
    layout = body[body.index("## Workspace layout"):]
    assert "\npeople/" in layout and "\ntargets/" in layout
    assert layout.index("\napprovals/") < layout.index("\npeople/") < layout.index("\ntargets/") < layout.index("\nactivity.md")
    assert "readable log" in layout and "source of truth for follow-up cadence" not in layout


def code_severities() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / "careeros" / "core").rglob("*.py"):
        for m in re.finditer(r'Issue\(\s*"(error|warning|info)",\s*(?:\w+\.)?(OUT\d+)', path.read_text(encoding="utf-8")):
            out[m.group(2)] = m.group(1)
    return out


def test_the_out_table_matches_the_code() -> None:
    section = doc().split("## Validation codes", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `(OUT\d+)` \| (error|warning|info) \|", section, re.M)
    expected = code_severities()
    assert sorted(expected) == ["OUT001", "OUT002", "OUT003", "OUT004", "OUT005", "OUT006", "OUT007", "OUT100"], expected
    assert dict(rows) == expected and len(rows) == len(expected)


def test_the_refusal_code_list_matches_the_code() -> None:
    section = doc().split("## Refusal codes", 1)[1].split("\n## ", 1)[0]
    found = re.findall(r"^\| `([a-z_]+)` \|", section, re.M)
    assert sorted(found) == sorted(rules.REFUSAL_CODES) and len(found) == len(set(found))
    for shared in ("person_closed", "cap_reached", "cap_config_invalid", "identity_conflict"):
        assert shared in approval_rules.REFUSAL_CODES, shared


def test_the_rules_table_matches_the_code() -> None:
    section = doc().split("## Rules", 1)[1].split("\n## ", 1)[0]
    rows = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\| `([A-Z_]+)` \| (\d+) \|", section, re.M)}
    assert rows == {name: getattr(rules, name) for name in (
        "INVITE_FOLLOWUP_DAYS", "APPLICATION_FOLLOWUP_DAYS", "CAP_WINDOW_DAYS", "DEFAULT_WEEKLY_CAP", "TARGET_STALE_DAYS",
        "MAX_CONTACTS_PER_TARGET")}


def test_every_days_number_in_the_docs_is_a_code_constant() -> None:
    allowed = {rules.INVITE_FOLLOWUP_DAYS, rules.APPLICATION_FOLLOWUP_DAYS, rules.CAP_WINDOW_DAYS, rules.TARGET_STALE_DAYS}
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Outreach and follow-up state", 1)[1].split("\n## ", 1)[0]
    assert days(doc()) == allowed
    assert days(section) <= allowed and days(section)


def test_the_doc_names_every_event_command_and_exit_code() -> None:
    body = doc()
    for event in rules.KNOWN_EVENTS:
        assert f"`{event}`" in body, event
    for command in ("person add", "person list", "person show", "person link", "target add", "target approve", "target drop",
                    "target list", "target show", "outreach record", "outreach queue", "outreach cap", "outreach next", "outreach warm"):
        assert f"careeros {command if command.startswith('outreach') else 'outreach ' + command}" in body, command
    assert "**Exit codes:**" in body
    for label in rules.LABEL_PRECEDENCE:
        assert f"`{label}`" in body, label
    for status in rules.STATUS_PRECEDENCE:
        assert f"`{status}`" in body, status
    for role in rules.ROLES:
        assert f"`{role}`" in body, role


DOC_PHRASES = (
    "not a security boundary",
    "## What each result is derived from",
    "ledger events only",
    "validated record fields",
    "a hand edit of `found_in` or `role` can change which target looks contacted or who qualifies for `next`",
    "it can never change identity, what was sent, the cap, or whether a person is closed",
    "every invite item currently `in_flight`, whatever its age",
    "`approval.consumed` event",
    "`approval.started` timestamp is never used",
    "A typo never silently changes enforcement.",
    "`0` is valid and blocks all invites",
    "`cap_config_invalid`",
    "`cap_reached`",
    "`person_closed`",
    "the same critical section",
    "Email and interview follow-ups are not covered",
    "A dropped target cannot be re-opened",
    "reported facts, not verified against LinkedIn",
    "The cap counts only invites recorded in CareerOS",
    "A company slug match is a heuristic",
    "Truncating the tail of `ledger.jsonl` cannot be detected by a hash chain",
    "importing existing `activity.md` history",
    "GPT workspace",
    "per_xxxxxxxxxx",
)


@pytest.mark.parametrize("phrase", DOC_PHRASES)
def test_the_doc_states_the_rules_and_the_limits(phrase: str) -> None:
    assert phrase in doc()


def test_the_doc_states_the_clock_floor_honestly_and_the_exit_codes_and_range() -> None:
    from careeros.core.approvals import cap
    body = doc()
    assert "all computations use one clock read" not in body
    assert "`approval begin` applies the [ledger clock floor]" in body
    assert "the read-only `outreach cap` and `outreach next` use the same count without it" in body
    assert "a clock behind the ledger can only over-count, never under-count" in body
    assert "**Exit codes:** 0 ok; 1 refused" in body and "2 usage, or no workspace" in body
    assert f"from 0 to {cap.MAX_WEEKLY_CAP}" in body
    assert "The id is a random id mapped to the canonical URL" in body
    limits = body[body.index("## Limits"):]
    assert "- This is not a security boundary." in limits


HARD_CLAIMS = r"\b(hard limit|absolute(ly)?|impossible|cannot be (bypassed|circumvented|evaded)|guarantees?|tamper-?proof|enforces? the approval|prevents? the agent|boundary)\b"


def test_no_doc_sentence_claims_a_hard_limit_or_boundary() -> None:
    for sentence in sentences(doc()):
        for m in re.finditer(HARD_CLAIMS, sentence, re.I):
            assert m.group(0).lower() == "boundary" and re.search(r"\bnot a security boundary\b", sentence), sentence


def test_no_docs_page_says_activity_md_drives_follow_up() -> None:
    pages = [*(ROOT / "docs").glob("*.md"), *(ROOT / "docs" / "cowork").glob("*.md"), ROOT / "README.md", TEMPLATES / "workspace" / "CLAUDE.md"]
    for path in pages:
        for sentence in sentences(path.read_text(encoding="utf-8")):
            if "activity.md" in sentence:
                assert not re.search(r"drives? follow-up|source of truth for follow-up|follow-up cadence\)|Scans `activity.md` for overdue|relies on what's recorded in `activity.md`", sentence), (path.name, sentence)


def test_no_doc_sentence_makes_no_promise_the_feature_does_not_keep() -> None:
    for sentence in sentences(doc()):
        if "security boundary" in sentence.lower():
            assert re.search(r"\bnot\b|\bno\b|\bnever\b", sentence, re.I), sentence
        assert not re.search(r"\bcannot be bypassed|\bguarantees? that\b|\bprevents? the agent\b", sentence, re.I), sentence


def test_approvals_doc_lists_the_new_codes_and_the_cap_rules() -> None:
    body = (ROOT / "docs" / "approvals.md").read_text(encoding="utf-8")
    for code in ("person_closed", "cap_reached", "cap_config_invalid", "identity_conflict"):
        assert f"| `{code}` |" in body, code
    assert "outreach.md" in body


def test_version_changelog_readme_and_nav() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.8.0"
    assert careeros.__version__ == "0.8.0"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = changelog.split("\n## ", 2)[1]
    assert head.startswith("0.8.0")
    for phrase in ("run `careeros upgrade` to receive the updated skills",
                   "follow-up queue and the weekly invite cap now come from recorded events",
                   "existing `activity.md` history is not imported", "`targets` skill", "OUT001", "OUT007", "OUT100",
                   "docs/outreach.md", "2026-10-05-target-company-outreach-design.md"):
        assert phrase in head, phrase
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Outreach and follow-up state" in readme and "docs/outreach.md" in readme
    for sub in ("careeros outreach queue", "careeros outreach cap", "careeros outreach next"):
        assert sub in readme, sub
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "outreach.md" in nav and nav.index("approvals.md") < nav.index("outreach.md") < nav.index("Cowork")


def test_the_new_skill_is_delivered_by_scaffold(tmp_path: Path) -> None:
    from careeros.workspace.scaffold import scaffold
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    assert (root / ".claude" / "skills" / "targets" / "SKILL.md").read_text(encoding="utf-8") == text("targets")
    (root / ".claude" / "skills" / "targets" / "SKILL.md").unlink()
    assert ".claude/skills/targets/SKILL.md" in scaffold(root, refresh=True, runtime="claude")


def test_the_gpt_workspace_has_no_targets_skill() -> None:
    assert not (TEMPLATES / "gpt-workspace" / ".gpt" / "skills" / "targets").exists()
