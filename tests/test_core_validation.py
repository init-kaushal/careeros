import json
import shutil
from pathlib import Path

import pytest
from helpers import add_job, make_workspace

from careeros.core import ledger, state_machine, versions
from careeros.core import workspace as ws
from careeros.core.models import Issue, State, WorkspaceMeta
from careeros.core.validation import validate_workspace

TS = "2026-10-01T00:00:00Z"


@pytest.fixture
def root(tmp_path: Path, clock) -> Path:
    return make_workspace(tmp_path / "ws")


def _find(issues: list[Issue], code: str) -> Issue:
    matches = [i for i in issues if i.code == code]
    assert matches, f"expected {code}, got {[i.code for i in issues]}"
    assert matches[0].fix.strip(), f"{code} has no fix text"
    return matches[0]


def test_a_healthy_workspace_has_no_issues(root: Path) -> None:
    add_job(root, "one")
    add_job(root, "two", status=State.APPLIED, bullet="applied")
    pipeline = root / "jobs" / "pipeline.md"
    pipeline.write_text(pipeline.read_text().replace("- [ ] **Acme** — Backend Engineer · Remote · Score 8 · 2026-10-01 · https://example.com/jobs/two",
                                                     "- [~] **Acme** — Backend Engineer · Remote · Score 8 · 2026-10-01 · https://example.com/jobs/two"))
    assert validate_workspace(root) == []


def test_str001_missing_profile_and_jobs(root: Path) -> None:
    (root / "profile.md").unlink()
    shutil.rmtree(root / "jobs")
    issues = validate_workspace(root)
    assert [i.severity for i in issues if i.code == "STR001"] == ["error", "error"]
    _find(issues, "STR001")


def test_str002_missing_activity_and_pipeline_are_warnings(root: Path) -> None:
    (root / "activity.md").unlink()
    (root / "jobs" / "pipeline.md").unlink()
    issues = validate_workspace(root)
    assert [i.severity for i in issues if i.code == "STR002"] == ["warning", "warning"]


def test_ws001_legacy_workspace_without_metadata(root: Path) -> None:
    (root / ".careeros" / "workspace.yaml").unlink()
    issue = _find(validate_workspace(root), "WS001")
    assert issue.severity == "error" and "careeros migrate" in issue.fix


def test_ws002_schema_newer_than_supported(root: Path) -> None:
    ws.save_meta(root, WorkspaceMeta(2, versions.installed_version(), TS, TS, ()))
    assert _find(validate_workspace(root), "WS002").severity == "error"


def test_ws003_unreadable_metadata(root: Path) -> None:
    (root / ".careeros" / "workspace.yaml").write_text("schema_version: [")
    issues = validate_workspace(root)
    assert _find(issues, "WS003").severity == "error"
    assert "WS001" not in [i.code for i in issues]


def test_ws004_and_ws005_framework_version_relations(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "installed_version", lambda: "0.4.0")
    ws.save_meta(root, WorkspaceMeta(1, "0.3.0", TS, TS, ()))
    older = _find(validate_workspace(root), "WS004")
    assert older.severity == "warning" and "careeros upgrade" in older.fix
    ws.save_meta(root, WorkspaceMeta(1, "9.0.0", TS, TS, ()))
    newer = _find(validate_workspace(root), "WS005")
    assert newer.severity == "error"


def test_job001_legacy_job_file_without_frontmatter(root: Path) -> None:
    path = root / "jobs" / "discovered" / "legacy" / "job.md"
    path.parent.mkdir(parents=True)
    path.write_text("# T at C\n- **URL:** https://x.test\n")
    issue = _find(validate_workspace(root), "JOB001")
    assert issue.severity == "warning" and "careeros migrate" in issue.fix


def test_job002_missing_key_and_unreadable_frontmatter(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    del fm["company"]
    job["path"].write_text(ws.join_frontmatter(fm, body))
    assert "company" in _find(validate_workspace(root), "JOB002").message
    job["path"].write_text("---\nkey: [oops\n---\nbody\n")
    assert _find(validate_workspace(root), "JOB002").severity == "error"


def test_job003_invalid_and_duplicate_ids(root: Path) -> None:
    one = add_job(root, "one")
    two = add_job(root, "two")
    fm, body = ws.split_frontmatter(two["path"].read_text())
    fm["id"] = one["id"]
    two["path"].write_text(ws.join_frontmatter(fm, body))
    assert "also used" in _find(validate_workspace(root), "JOB003").message
    fm["id"] = "job_BAD"
    two["path"].write_text(ws.join_frontmatter(fm, body))
    assert "not a valid job id" in _find(validate_workspace(root), "JOB003").message


def test_job004_invalid_status(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["status"] = "NONSENSE"
    job["path"].write_text(ws.join_frontmatter(fm, body))
    assert _find(validate_workspace(root), "JOB004").severity == "error"


def test_job005_status_line_disagrees_with_frontmatter(root: Path) -> None:
    add_job(root, "one", status=State.DISCOVERED, bullet="applied")
    issue = _find(validate_workspace(root), "JOB005")
    assert issue.severity == "warning" and "careeros transition" in issue.fix


def test_job006_timestamp_must_be_full_utc(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["created_at"] = "2026-10-01"
    job["path"].write_text(ws.join_frontmatter(fm, body))
    assert _find(validate_workspace(root), "JOB006").severity == "error"


def test_pipeline_rules(root: Path) -> None:
    add_job(root, "one", status=State.APPLIED, bullet="applied")  # entry still shows "[ ]"
    add_job(root, "two", pipeline=False)
    archived = add_job(root, "three", pipeline=False)
    from careeros.core import state_machine as sm
    sm.archive_job(root, archived["id"], actor="user")
    with (root / "jobs" / "pipeline.md").open("a") as handle:
        handle.write("- [ ] **Ghost** — Nobody · Remote · Score 1 · 2026-10-01 · https://example.com/ghost\n")
    issues = validate_workspace(root)
    assert _find(issues, "PIPE001").severity == "warning"
    assert [i.path for i in issues if i.code == "PIPE002"] == ["jobs/discovered/two/job.md"]
    assert "[~]" in _find(issues, "PIPE003").message


def test_pipeline_history_below_the_fold_is_ignored(root: Path) -> None:
    add_job(root, "one")
    with (root / "jobs" / "pipeline.md").open("a") as handle:
        handle.write("\n## -- HISTORY (on demand; do not read past this line) --\n"
                     "- [x] **Old** — Thing · Remote · Score 1 · 2026-01-01 · https://example.com/old\n")
    assert "PIPE001" not in [i.code for i in validate_workspace(root)]


def test_led001_and_led002_come_from_the_chain_check(root: Path) -> None:
    add_job(root, "one")
    path = root / "ledger.jsonl"
    path.write_text(path.read_text() + "garbage\n")
    assert _find(validate_workspace(root), "LED001").severity == "error"
    path.write_text(path.read_text().replace('"seq":1', '"seq":7', 1))
    assert _find(validate_workspace(root), "LED002").severity == "error"


def test_led003_deleted_job_is_reported_with_archive_advice(root: Path) -> None:
    job = add_job(root, "one")
    shutil.rmtree(job["path"].parent)
    issue = _find(validate_workspace(root), "LED003")
    assert issue.severity == "error" and "archive" in issue.fix and job["id"] in issue.message
    assert len([i for i in validate_workspace(root) if i.code == "LED003"]) == 1


def test_archived_job_keeps_ledger_references_valid(root: Path) -> None:
    from careeros.core import state_machine as sm
    job = add_job(root, "one")
    sm.archive_job(root, job["id"], actor="user")
    assert "LED003" not in [i.code for i in validate_workspace(root)]


def test_led004_illegal_logged_change_but_corrections_are_allowed(root: Path) -> None:
    job = add_job(root, "one")
    ledger.append_event(root, type="job.status_changed", actor="agent:claude", entity=job["id"],
                        prev_state="DISCOVERED", new_state="OFFER", action="bogus")
    assert _find(validate_workspace(root), "LED004").severity == "error"


def test_led004_not_raised_for_forced_corrections(root: Path) -> None:
    job = add_job(root, "one", status=State.SCREEN, bullet="interview")
    ledger.append_event(root, type="job.status_corrected", actor="user", entity=job["id"],
                        prev_state="SCREEN", new_state="DISCOVERED", action="fix", reason="entered by mistake")
    codes = [i.code for i in validate_workspace(root)]
    assert "LED004" not in codes


def test_led005_job_status_not_reflected_in_ledger(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["status"] = "EVALUATED"  # changed by hand, or a crash before the ledger append
    job["path"].write_text(ws.join_frontmatter(fm, body))
    issue = _find(validate_workspace(root), "LED005")
    assert issue.severity == "warning" and "careeros transition" in issue.fix


def test_every_issue_serialises(root: Path) -> None:
    (root / "profile.md").unlink()
    issue = validate_workspace(root)[0]
    assert set(issue.to_dict()) == {"severity", "code", "path", "message", "fix"}
    json.dumps(issue.to_dict())


@pytest.mark.parametrize("value", [12345, ["a", "b"]])
def test_non_string_url_does_not_crash_the_validator(root: Path, value) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["url"] = value
    job["path"].write_text(ws.join_frontmatter(fm, body))
    validate_workspace(root)


def test_job005_fix_names_the_display_spelling_and_the_transition_command(root: Path) -> None:
    add_job(root, "one", status=State.DISCOVERED, bullet="applied")
    issue = _find(validate_workspace(root), "JOB005")
    assert "source of truth" in issue.fix and "careeros transition" in issue.fix
    assert state_machine.display_for(State.DISCOVERED) in issue.fix
