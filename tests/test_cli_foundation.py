import json
from pathlib import Path

import pytest
from helpers import (
    add_job, legacy_job_text, make_claude_workspace, make_legacy_workspace, make_workspace, snapshot,
)
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger, versions
from careeros.core import workspace as ws
from careeros.core.models import State, WorkspaceMeta

runner = CliRunner()
TS = "2026-10-01T00:00:00Z"


def run(root: Path | None, *args: str, input: str | None = None, env: dict | None = None):
    argv = list(args)
    if root is not None:
        argv += ["--workspace", str(root)]
    return runner.invoke(app, argv, input=input, env=env)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_workspace(tmp_path / "ws")


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_util, "is_interactive", lambda: True)


def _events(root: Path) -> list[dict]:
    return ledger.read_events(root)


# --- workspace resolution ----------------------------------------------------------------

def test_workspace_from_env_var(root: Path) -> None:
    result = run(None, "status", "--json", env={"CAREEROS_WORKSPACE": str(root)})
    assert result.exit_code == 0
    assert json.loads(result.stdout)["workspace"] == str(root.resolve())


def test_no_workspace_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = run(None, "status")
    assert result.exit_code == 2 and "no CareerOS workspace found" in result.output


# --- validate ----------------------------------------------------------------------------

def test_validate_clean_and_json(root: Path) -> None:
    add_job(root, "one")
    result = run(root, "validate")
    assert result.exit_code == 0 and "0 error(s), 0 warning(s)" in result.output
    assert json.loads(run(root, "validate", "--json").stdout) == {"errors": 0, "warnings": 0, "issues": []}


def test_validate_legacy_workspace_fails_with_actionable_fix(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    result = run(legacy, "validate")
    assert result.exit_code == 1 and "WS001" in result.output and "careeros migrate" in result.output


def test_validate_strict_turns_warnings_into_failure(root: Path) -> None:
    add_job(root, "one", pipeline=False)
    assert run(root, "validate").exit_code == 0
    assert run(root, "validate", "--strict").exit_code == 1


# --- status and doctor -------------------------------------------------------------------

def test_status_json_summarises_jobs_events_and_versions(root: Path) -> None:
    add_job(root, "one")
    add_job(root, "two", status=State.APPLIED, bullet="applied")
    data = json.loads(run(root, "status", "--json").stdout)
    assert data["jobs"] == {"DISCOVERED": 1, "APPLIED": 1}
    assert (data["schema_version"], data["migrate_needed"], data["upgrade_available"], data["workspace_newer"]) == (1, False, False, False)
    assert [e["type"] for e in data["recent_events"]] == ["job.imported", "job.imported"]


def test_status_on_a_legacy_workspace_says_migrate_is_needed(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    data = json.loads(run(legacy, "status", "--json").stdout)
    assert data["schema_version"] == 0 and data["migrate_needed"] is True and data["unmigrated_jobs"] == 8


def test_doctor_healthy_workspace_exits_zero_and_warns_about_git(root: Path) -> None:
    assert run(root, "doctor").exit_code == 0
    (root / ".git").mkdir()
    result = run(root, "doctor")
    assert result.exit_code == 0 and "git repository" in result.output


def test_doctor_fails_on_a_legacy_workspace_and_reports_stale_framework_files(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    result = run(legacy, "doctor")
    assert result.exit_code == 1 and "careeros migrate" in result.output
    claude = make_claude_workspace(tmp_path / "claude")
    (claude / ".claude" / "skills" / "browse" / "SKILL.md").write_text("old")
    out = run(claude, "doctor").output
    assert "careeros upgrade" in out


def test_doctor_reports_a_pending_manifest(root: Path) -> None:
    backup = root / ".careeros" / "backups" / "20261006T090000Z-migrate"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text(json.dumps({"status": "pending", "files": []}))
    result = run(root, "doctor")
    assert result.exit_code == 1 and "pending" in result.output


# --- ledger ------------------------------------------------------------------------------

def test_ledger_append_list_and_verify(root: Path) -> None:
    result = run(root, "ledger", "append", "--type", "note.added", "--action", "called the recruiter",
                 "--actor", "user", "--entity", "job_aaaaaaaaaa", "--artifact", "notes.md")
    assert result.exit_code == 0 and "appended event 1" in result.output
    listed = json.loads(run(root, "ledger", "list", "--json").stdout)
    assert listed[0]["type"] == "note.added" and listed[0]["artifacts"] == ["notes.md"]
    assert json.loads(run(root, "ledger", "list", "--entity", "job_zzzzzzzzzz", "--json").stdout) == []
    assert run(root, "ledger", "verify").exit_code == 0


def test_ledger_append_refuses_reserved_types(root: Path) -> None:
    result = run(root, "ledger", "append", "--type", "application.approved", "--action", "sneaky", "--actor", "agent:claude")
    assert result.exit_code == 2 and "reserved" in result.output
    assert not (root / "ledger.jsonl").exists()


def test_ledger_verify_fails_after_tampering(root: Path) -> None:
    for i in range(3):
        run(root, "ledger", "append", "--type", "note.added", "--action", f"note {i}", "--actor", "user")
    path = root / "ledger.jsonl"
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace("note 0", "note X")
    path.write_text("\n".join(lines) + "\n")
    result = run(root, "ledger", "verify")
    assert result.exit_code == 1 and "LED002" in result.output


def test_ledger_append_refuses_a_workspace_from_the_future(tmp_path: Path) -> None:
    future = make_workspace(tmp_path / "ws", framework_version="9.0.0")
    result = run(future, "ledger", "append", "--type", "note.added", "--action", "x", "--actor", "user")
    assert result.exit_code == 1 and "newer than the installed" in result.output


# --- transition, approve, archive ---------------------------------------------------------

def test_transition_changes_state_and_is_idempotent(root: Path) -> None:
    job = add_job(root, "one")
    first = run(root, "transition", job["id"], "--to", "evaluated", "--actor", "user")
    assert first.exit_code == 0 and "DISCOVERED → EVALUATED" in first.output
    again = run(root, "transition", "one", "--to", "EVALUATED")
    assert again.exit_code == 0 and "nothing to do" in again.output
    assert [e["type"] for e in _events(root)].count("job.status_changed") == 1


def test_illegal_transition_exits_1_and_is_logged(root: Path) -> None:
    job = add_job(root, "one")
    result = run(root, "transition", job["id"], "--to", "OFFER", "--actor", "agent:claude")
    assert result.exit_code == 1 and "illegal_transition" in result.output
    assert _events(root)[-1]["type"] == "job.transition_rejected"


def test_unknown_state_is_a_usage_error(root: Path) -> None:
    job = add_job(root, "one")
    assert run(root, "transition", job["id"], "--to", "BANANA").exit_code == 2


def test_force_without_reason_fails(root: Path) -> None:
    job = add_job(root, "one", status=State.SCREEN, bullet="interview")
    result = run(root, "transition", job["id"], "--to", "EVALUATED", "--force", "--actor", "user")
    assert result.exit_code == 1 and "reason_required" in result.output
    ok = run(root, "transition", job["id"], "--to", "EVALUATED", "--force", "--reason", "mis-click", "--actor", "user")
    assert ok.exit_code == 0 and "corrected" in ok.output


def test_approve_needs_a_terminal(root: Path) -> None:
    job = add_job(root, "one", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    result = run(root, "approve", job["id"])
    assert result.exit_code == 2 and "interactive terminal" in result.output and "Ask the user" in result.output
    assert [e["type"] for e in _events(root)] == ["job.imported"]


def test_approve_records_the_users_decision(root: Path, interactive: None) -> None:
    job = add_job(root, "one", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    assert run(root, "transition", job["id"], "--to", "APPLIED", "--actor", "agent:claude").exit_code == 1
    approved = run(root, "approve", job["id"], input="y\n")
    assert approved.exit_code == 0
    event = _events(root)[-1]
    assert (event["type"], event["actor"], event["approval"]) == ("application.approved", "user", "approved")
    assert run(root, "transition", job["id"], "--to", "APPLIED", "--actor", "agent:claude").exit_code == 0


def test_declined_approval_is_recorded_and_exits_1(root: Path, interactive: None) -> None:
    job = add_job(root, "one", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    result = run(root, "approve", job["id"], input="n\n")
    assert result.exit_code == 1
    assert _events(root)[-1]["type"] == "application.approval_denied"


def test_archive_and_undo(root: Path) -> None:
    job = add_job(root, "one")
    assert run(root, "archive", job["id"], "--reason", "not a fit", "--actor", "user").exit_code == 0
    assert ws.read_job(job["path"]).archived
    assert run(root, "archive", job["id"], "--undo", "--actor", "user").exit_code == 0
    assert not ws.read_job(job["path"]).archived


# --- migrate -----------------------------------------------------------------------------

def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    before = snapshot(legacy)
    result = run(legacy, "migrate", "--dry-run")
    assert result.exit_code == 0 and "Dry run" in result.output and "8 job file(s)" in result.output
    assert snapshot(legacy) == before and not (legacy / ".careeros").exists()


def test_migrate_without_a_terminal_or_yes_exits_2_and_changes_nothing(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    before = snapshot(legacy)
    result = run(legacy, "migrate")
    assert result.exit_code == 2 and "--yes" in result.output
    assert snapshot(legacy) == before and not (legacy / ".careeros" / "workspace.yaml").exists()


def test_migrate_yes_applies_then_validates_and_a_second_run_is_a_no_op(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    result = run(legacy, "migrate", "--yes")
    assert result.exit_code == 0 and "Migrated 8 job(s)" in result.output and "backups" in result.output
    assert run(legacy, "validate").exit_code == 0
    again = run(legacy, "migrate", "--yes")
    assert again.exit_code == 0 and "already current" in again.output


def test_migrate_reports_unparseable_jobs_and_exits_1(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    broken = legacy / "jobs" / "discovered" / "broken" / "job.md"
    broken.parent.mkdir()
    broken.write_text(legacy_job_text("Engineer", "Weird", "https://x.test", "wibble"))
    before = snapshot(legacy)
    result = run(legacy, "migrate", "--yes")
    assert result.exit_code == 1 and "unknown Status" in result.output and "nothing was changed" in result.output
    assert snapshot(legacy) == before


def test_declined_migration_is_logged_when_a_ledger_exists(tmp_path: Path, interactive: None) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    assert run(legacy, "migrate", "--yes").exit_code == 0
    extra = legacy / "jobs" / "discovered" / "newco" / "job.md"
    extra.parent.mkdir()
    extra.write_text(legacy_job_text("Engineer", "NewCo", "https://x.test/newco", "discovered"))
    before = extra.read_bytes()
    result = run(legacy, "migrate", input="n\n")
    assert result.exit_code == 1 and "Cancelled" in result.output
    assert extra.read_bytes() == before
    assert _events(legacy)[-1]["type"] == "workspace.migrate_declined"


# --- upgrade -----------------------------------------------------------------------------

def test_upgrade_needs_confirmation_then_refreshes_and_reports_current(tmp_path: Path) -> None:
    claude = make_claude_workspace(tmp_path / "ws")
    skill = claude / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("customised")
    assert run(claude, "upgrade").exit_code == 2 and skill.read_text() == "customised"
    result = run(claude, "upgrade", "--yes")
    assert result.exit_code == 0 and "Upgraded" in result.output and skill.read_text() != "customised"
    assert ws.load_meta(claude).framework_version == versions.installed_version()
    assert "already current" in run(claude, "upgrade", "--yes").output


def test_declined_upgrade_is_logged(tmp_path: Path, interactive: None) -> None:
    claude = make_claude_workspace(tmp_path / "ws")
    (claude / ".claude" / "skills" / "browse" / "SKILL.md").write_text("customised")
    result = run(claude, "upgrade", input="n\n")
    assert result.exit_code == 1 and "Cancelled" in result.output
    assert _events(claude)[-1]["type"] == "workspace.upgrade_declined"
    assert (claude / ".claude" / "skills" / "browse" / "SKILL.md").read_text() == "customised"


def test_upgrade_on_a_legacy_workspace_points_to_migrate(tmp_path: Path) -> None:
    from careeros.workspace.scaffold import scaffold

    legacy = tmp_path / "ws"
    scaffold(legacy, runtime="claude")
    (legacy / "profile.md").write_text("# my profile\n", encoding="utf-8")
    (legacy / "jobs").mkdir()
    (legacy / ".claude" / "skills" / "browse" / "SKILL.md").write_text("old")
    result = run(legacy, "upgrade", "--yes")
    assert result.exit_code == 0 and "careeros migrate" in result.output


# --- init --------------------------------------------------------------------------------

def test_init_new_workspace_writes_metadata_and_a_created_event(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    assert runner.invoke(app, ["init", str(target)]).exit_code == 0
    meta = ws.load_meta(target)
    assert (meta.schema_version, meta.framework_version, meta.runtimes) == (1, versions.installed_version(), ("claude",))
    assert [e["type"] for e in ledger.read_events(target)] == ["workspace.created"]


def test_init_second_runtime_extends_metadata_without_a_second_created_event(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    assert runner.invoke(app, ["init", str(target), "--runtime", "gpt"]).exit_code == 0
    assert ws.load_meta(target).runtimes == ("claude", "gpt")
    assert [e["type"] for e in ledger.read_events(target)] == ["workspace.created"]


def test_init_into_a_legacy_directory_does_not_invent_metadata(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    assert runner.invoke(app, ["init", str(legacy), "--runtime", "gpt"]).exit_code == 0
    assert ws.load_meta(legacy) is None and not (legacy / "ledger.jsonl").exists()


def test_init_refresh_updates_framework_version_and_logs_an_upgrade(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    meta = ws.load_meta(target)
    ws.save_meta(target, WorkspaceMeta(meta.schema_version, "0.1.0", meta.created_at, meta.updated_at, meta.runtimes))
    assert runner.invoke(app, ["init", str(target), "--refresh"]).exit_code == 0
    assert ws.load_meta(target).framework_version == versions.installed_version()
    assert ledger.read_events(target)[-1]["type"] == "workspace.upgraded"


def test_init_refresh_refuses_a_workspace_from_the_future(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    meta = ws.load_meta(target)
    ws.save_meta(target, WorkspaceMeta(meta.schema_version, "9.0.0", meta.created_at, meta.updated_at, meta.runtimes))
    skill = target / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("corrupted")
    result = runner.invoke(app, ["init", str(target), "--refresh"])
    assert result.exit_code == 1 and "newer than the installed" in result.output
    assert skill.read_text() == "corrupted"
