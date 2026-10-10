"""The memory and check commands: exit codes, the terminal guard, JSON purity, recording."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from helpers import add_job, make_workspace, snapshot
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger
from careeros.core.memory import store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"
GOOD = "Reduced AWS costs by 35% using Kafka-based pipelines at Acme Corp."
BAD = "Reduced AWS costs by 50% using Kafka. I worked at FakeCorp."


def run(root: Path | None, *args: str, input: str | None = None):
    argv = list(args)
    if root is not None:
        argv += ["--workspace", str(root)]
    return runner.invoke(app, argv, input=input)


@pytest.fixture
def root(tmp_path: Path, clock) -> Path:
    return make_workspace(tmp_path / "ws")


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_util, "is_interactive", lambda: True)


def events(root: Path, prefix: str) -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith(prefix)]


# --- check ---------------------------------------------------------------------------------

def test_check_exits_0_for_a_supported_draft_and_prints_the_narrow_guarantee(career, tmp_path: Path) -> None:
    root, _ = career
    draft = tmp_path / "draft.txt"
    draft.write_text(GOOD + "\nI enjoy mentoring.", encoding="utf-8")
    result = run(root, "check", str(draft))
    assert result.exit_code == 0, result.output
    assert "Evidence check: PASSED" in result.output and "Not evaluated (1 sentence(s)" in result.output
    assert "claimed rather than confirmed" in result.output
    assert "A pass does not mean every claim in the draft was detected." in result.output


def test_check_exits_1_with_codes_and_fixes_for_an_unsupported_draft(career, tmp_path: Path) -> None:
    root, _ = career
    draft = tmp_path / "draft.txt"
    draft.write_text(BAD, encoding="utf-8")
    result = run(root, "check", str(draft))
    assert result.exit_code == 1
    assert "FAILED" in result.output and "CHK001" in result.output and "CHK004" in result.output and "fix:" in result.output


def test_check_reads_standard_input(career) -> None:
    root, _ = career
    assert run(root, "check", "-", input=GOOD).exit_code == 0
    assert run(root, "check", "-", input=BAD).exit_code == 1


def test_check_json_prints_only_json(career) -> None:
    root, _ = career
    result = run(root, "check", "-", "--json", input=BAD)
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False and {f["code"] for f in data["review_required"]} >= {"CHK001", "CHK004"}
    assert set(data["not_evaluated"]) == {"sentences", "categories"}


def test_check_usage_errors_exit_2(career, tmp_path: Path) -> None:
    root, _ = career
    assert run(root, "check", str(tmp_path / "missing.txt")).exit_code == 2
    assert run(root, "check", "-", "--against", "nonexistent-job", input=GOOD).exit_code == 2


def test_check_will_not_run_against_a_broken_memory(career) -> None:
    root, ids = career
    store.load_career(root).by_id()[ids["costs"]].path.write_text("garbage", encoding="utf-8")
    result = run(root, "check", "-", input=GOOD)
    assert result.exit_code == 2 and "MEM001" in result.output


def test_check_against_a_job_allows_naming_its_company(career) -> None:
    root, _ = career
    job = add_job(root, "initech-eng", company="Initech", title="Backend Engineer")
    letter = "I am excited about the Backend Engineer role at Initech."
    assert run(root, "check", "-", "--against", job["id"], input=letter).exit_code == 0
    assert run(root, "check", "-", input=letter).exit_code == 1


def test_check_allow_and_require_confirmed(career) -> None:
    root, _ = career
    assert run(root, "check", "-", "--allow", "Flink", input="Your team runs Flink at scale.").exit_code == 0
    assert run(root, "check", "-", input="Your team runs Flink at scale.").exit_code == 1
    assert run(root, "check", "-", "--require-confirmed", input=GOOD).exit_code == 1


def test_check_fails_closed_on_an_empty_memory(root: Path) -> None:
    result = run(root, "check", "-", input="Anything.")
    assert result.exit_code == 1 and "CHK030" in result.output


def test_check_record_writes_the_hash_of_the_exact_draft(career) -> None:
    root, _ = career
    assert run(root, "check", "-", "--record", input=GOOD).exit_code == 0
    assert run(root, "check", "-", "--record", input=BAD).exit_code == 1
    recorded = events(root, "draft.checked")
    assert len(recorded) == 2 and recorded[0]["artifacts"] == [f"sha256:{hashlib.sha256(GOOD.encode()).hexdigest()}"]
    assert "passed" in recorded[0]["action"] and "failed" in recorded[1]["action"]
    assert ledger.verify_chain(root) == []
    assert not events(root, "draft.checked")[0].get("entity")


def test_check_without_record_leaves_the_workspace_untouched(career) -> None:
    root, _ = career
    before, ledger_bytes = snapshot(root), (root / "ledger.jsonl").read_bytes()
    run(root, "check", "-", input=GOOD)
    assert snapshot(root) == before and (root / "ledger.jsonl").read_bytes() == ledger_bytes


# --- memory import -------------------------------------------------------------------------

def test_import_defaults_to_a_dry_run(root: Path) -> None:
    shutil.copy(FIXTURE, root / "resume.md")
    before = snapshot(root)
    result = run(root, "memory", "import")
    assert result.exit_code == 0 and "Dry run: nothing was changed" in result.output
    assert "+ experience" in result.output and "Not imported: section 'Awards'" in result.output
    assert snapshot(root) == before and not (root / "career").exists()


def test_import_apply_needs_a_terminal_or_yes(root: Path) -> None:
    shutil.copy(FIXTURE, root / "resume.md")
    assert run(root, "memory", "import", "--apply").exit_code == 2
    assert not (root / "career").exists()
    result = run(root, "memory", "import", "--apply", "--yes")
    assert result.exit_code == 0 and "Backup and manifest" in result.output
    assert len(store.load_career(root).facts) > 20
    again = run(root, "memory", "import", "--apply", "--yes")
    assert again.exit_code == 0 and "Nothing to import" in again.output


def test_import_apply_in_a_terminal_asks_first(root: Path, interactive) -> None:
    shutil.copy(FIXTURE, root / "resume.md")
    declined = run(root, "memory", "import", "--apply", input="n\n")
    assert declined.exit_code == 1 and not (root / "career").exists()
    assert run(root, "memory", "import", "--apply", input="y\n").exit_code == 0


def test_import_reports_grammar_errors_and_changes_nothing(root: Path) -> None:
    (root / "resume.md").write_text("## Experience\n\n### Acme Engineer\n- no pipe\n", encoding="utf-8")
    result = run(root, "memory", "import", "--apply", "--yes")
    assert result.exit_code == 1 and "expected '### Employer | Title'" in result.output and not (root / "career").exists()
    assert run(root, "memory", "import", "--apply", "--dry-run").exit_code == 2


def test_import_of_a_missing_file_fails_cleanly(root: Path) -> None:
    result = run(root, "memory", "import")
    assert result.exit_code == 1 and "does not exist" in result.output


# --- confirm / verify guards ---------------------------------------------------------------

def test_confirm_and_verify_refuse_without_a_terminal_and_say_how(career) -> None:
    root, ids = career
    for args in (("confirm", ids["costs"]), ("verify", ids["costs"], "--evidence", "evd_0000000000")):
        result = run(root, "memory", *args)
        assert result.exit_code == 2 and "interactive terminal" in result.output and "! careeros memory" in result.output
    assert store.load_career(root).by_id()[ids["costs"]].status == "claimed" and not events(root, "memory.fact_confirmed")


def test_confirm_in_a_terminal_records_the_decision_or_the_refusal(career, interactive) -> None:
    root, ids = career
    assert run(root, "memory", "confirm", ids["costs"], input="n\n").exit_code == 1
    assert events(root, "memory.declined") and store.load_career(root).by_id()[ids["costs"]].status == "claimed"
    result = run(root, "memory", "confirm", ids["costs"], input="y\n")
    assert result.exit_code == 0 and store.load_career(root).by_id()[ids["costs"]].status == "confirmed"
    assert events(root, "memory.fact_confirmed")[0]["actor"] == "user"


def test_verify_with_evidence_end_to_end(career, interactive) -> None:
    root, ids = career
    run(root, "memory", "confirm", ids["costs"], input="y\n")
    added = run(root, "memory", "add", "--kind", "evidence", "--set", "url=https://example.com/report",
                "--set", "note=Finance report", "--supports", ids["costs"])
    assert added.exit_code == 0, added.output
    evidence_id = added.output.split()[2]
    result = run(root, "memory", "verify", ids["costs"], "--evidence", evidence_id, input="y\n")
    assert result.exit_code == 0 and store.load_career(root).by_id()[ids["costs"]].status == "verified"


# --- add / update / dispute / retire / read ---------------------------------------------------

def test_add_update_dispute_retire_round_trip(career) -> None:
    root, ids = career
    added = run(root, "memory", "add", "--kind", "achievement", "--set", f"parent={ids['globex']}",
                "--text", "Mentored 4 engineers.", "--quote", "I mentored four people")
    assert added.exit_code == 0 and "claimed" in added.output
    fact_id = added.output.split()[2]
    assert run(root, "memory", "update", fact_id, "--text", "Mentored 5 engineers.").exit_code == 0
    assert run(root, "memory", "dispute", fact_id, "--reason", "it was four").exit_code == 0
    assert run(root, "memory", "retire", fact_id, "--reason", "duplicate").exit_code == 2  # disputed -> retired needs a person
    assert store.load_career(root).by_id()[fact_id].status == "disputed"


def test_add_without_a_quote_is_refused(career) -> None:
    root, ids = career
    result = run(root, "memory", "add", "--kind", "skill", "--set", "name=Rust")
    assert result.exit_code == 1 and "--quote" in result.output


def test_update_of_a_confirmed_fact_needs_a_terminal(career, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ids = career
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    run(root, "memory", "confirm", ids["costs"], input="y\n")
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    result = run(root, "memory", "update", ids["costs"], "--text", "Reduced AWS costs by 99%.")
    assert result.exit_code == 2 and "interactive terminal" in result.output
    assert "35%" in store.load_career(root).by_id()[ids["costs"]].get("text")


def test_list_show_and_status(career) -> None:
    root, ids = career
    listing = run(root, "memory", "list", "--kind", "experience")
    assert listing.exit_code == 0 and listing.output.count("experience") >= 2 and "Acme Corp" in listing.output
    as_json = json.loads(run(root, "memory", "list", "--status", "claimed", "--json").stdout)
    assert len(as_json) == len(ids)
    shown = run(root, "memory", "show", ids["costs"])
    assert "Reduced AWS costs by 35%" in shown.output and "status: claimed" in shown.output
    assert run(root, "memory", "show", "ach_zzzzzzzzzz").exit_code == 1
    status = json.loads(run(root, "memory", "status", "--json").stdout)
    assert status["facts"] == len(ids) and status["claimed_share"] == 1.0 and status["errors"] == []
    assert "still only claimed" in run(root, "memory", "status").output


def test_ledger_append_refuses_memory_event_types(career) -> None:
    root, _ = career
    result = run(root, "ledger", "append", "--type", "memory.fact_confirmed", "--action", "forged")
    assert result.exit_code == 2 and "reserved" in result.output
    assert run(root, "ledger", "append", "--type", "draft.checked", "--action", "ok").exit_code == 0


def test_validate_reports_memory_problems(career) -> None:
    root, ids = career
    assert run(root, "validate").exit_code == 0
    store.load_career(root).by_id()[ids["costs"]].path.write_text("garbage", encoding="utf-8")
    result = run(root, "validate")
    assert result.exit_code == 1 and "MEM001" in result.output


def test_an_empty_draft_is_a_usage_error_not_a_pass(career) -> None:
    root, _ = career
    assert run(root, "check", "-", input="  \n").exit_code == 2
