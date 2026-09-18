import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from typer.testing import CliRunner
from careeros.cli.main import app
from careeros.core.models import Job, JOB_STAGES
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ws(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    return tmp_path


@pytest.fixture
def ws_with_job(ws):
    storage = LocalFilesystemStorage(str(ws))
    job = Job(
        id="acme-sre-ab12",
        source="manual",
        company="Acme",
        title="Senior SRE",
        stage="saved",
        created_at=_now(),
        updated_at=_now(),
    )
    job.save(storage)
    return ws, job


@pytest.fixture
def mock_extraction():
    fields = {
        "company": "Acme",
        "title": "Senior SRE",
        "location": "SF, CA",
        "remote": False,
        "salary_min": 150000,
        "salary_max": 200000,
        "currency": "USD",
        "requirements": ["Python", "Kubernetes"],
        "summary": "Great role.",
    }
    with patch("careeros.cli.job_cmd.extract_job_fields", return_value=fields):
        yield fields


def test_job_add_writes_job_file(ws, mock_extraction):
    runner = CliRunner()
    # url (blank), jd text, blank line (end jd),
    # company (accept Acme), title (accept Senior SRE), location (accept SF, CA),
    # remote (n=False), salary_min (blank=skip), salary_max (blank=skip), currency (accept USD)
    user_input = "\nsome jd text\n\n\n\n\nn\n\n\nUSD\n"
    result = runner.invoke(app, ["job", "add", "--workspace", str(ws)], input=user_input)
    assert result.exit_code == 0, result.output
    job_files = list((ws / "jobs").glob("*.json"))
    assert len(job_files) == 1


def test_job_add_logs_activity(ws, mock_extraction):
    runner = CliRunner()
    user_input = "\nsome jd text\n\n\n\n\nn\n\n\nUSD\n"
    runner.invoke(app, ["job", "add", "--workspace", str(ws)], input=user_input)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = (ws / "activity" / f"{today}.jsonl").read_text()
    event_types = [json.loads(l)["event_type"] for l in log.strip().splitlines() if l]
    assert "job_added" in event_types


def test_job_add_extraction_failure_proceeds(ws):
    with patch("careeros.cli.job_cmd.extract_job_fields", return_value={}):
        runner = CliRunner()
        # blank url, blank jd, blank (end jd), company=Acme, title=Engineer,
        # location blank, remote n, sal_min blank, sal_max blank, currency USD
        user_input = "\n\nAcme\nEngineer\n\nn\n\n\nUSD\n"
        result = runner.invoke(app, ["job", "add", "--workspace", str(ws)], input=user_input)
    assert result.exit_code == 0, result.output
    job_files = list((ws / "jobs").glob("*.json"))
    assert len(job_files) == 1


def test_job_list_empty(ws):
    runner = CliRunner()
    result = runner.invoke(app, ["job", "list", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


def test_job_list_shows_jobs(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(app, ["job", "list", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Acme" in result.output
    assert "Senior SRE" in result.output


def test_job_list_stage_filter(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(app, ["job", "list", "--workspace", str(ws), "--stage", "applied"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


def test_job_show_known_id(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(app, ["job", "show", job.id, "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Acme" in result.output


def test_job_show_unknown_id(ws):
    runner = CliRunner()
    result = runner.invoke(app, ["job", "show", "nonexistent-id", "--workspace", str(ws)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_job_update_changes_stage(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "update", job.id, "--stage", "applied", "--workspace", str(ws)]
    )
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(str(ws))
    updated = Job.load(storage, job.id)
    assert updated.stage == "applied"


def test_job_update_sets_applied_at(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    runner.invoke(app, ["job", "update", job.id, "--stage", "applied", "--workspace", str(ws)])
    storage = LocalFilesystemStorage(str(ws))
    updated = Job.load(storage, job.id)
    assert updated.applied_at is not None


def test_job_update_logs_activity(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    runner.invoke(app, ["job", "update", job.id, "--stage", "applied", "--workspace", str(ws)])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = (ws / "activity" / f"{today}.jsonl").read_text()
    event_types = [json.loads(l)["event_type"] for l in log.strip().splitlines() if l]
    assert "job_stage_changed" in event_types


def test_job_update_invalid_stage(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "update", job.id, "--stage", "badstage", "--workspace", str(ws)]
    )
    assert result.exit_code == 1
    assert "Invalid stage" in result.output


def test_job_update_unknown_id(ws):
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "update", "nonexistent", "--stage", "applied", "--workspace", str(ws)]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_job_note_appends(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "note", job.id, "Great team culture", "--workspace", str(ws)]
    )
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(str(ws))
    updated = Job.load(storage, job.id)
    assert any("Great team culture" in n for n in updated.notes)


def test_job_note_unknown_id(ws):
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "note", "nonexistent", "some note", "--workspace", str(ws)]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_job_search_saves_jobs(ws):
    from careeros.sources.ats import ATSFetchError

    postings = [
        {
            "source_id": "123",
            "title": "Senior SRE",
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "location": "SF",
            "description": "Great role.",
        }
    ]
    with patch("careeros.cli.job_cmd.fetch_greenhouse", return_value=postings):
        runner = CliRunner()
        # user picks job 1
        result = runner.invoke(
            app,
            ["job", "search", "--source", "greenhouse", "--company", "acme", "--workspace", str(ws)],
            input="1\n",
        )
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(str(ws))
    jobs = Job.list_all(storage)
    assert len(jobs) == 1
    assert jobs[0].title == "Senior SRE"
    assert jobs[0].source == "greenhouse"


def test_job_search_ats_error(ws):
    from careeros.sources.ats import ATSFetchError

    with patch("careeros.cli.job_cmd.fetch_greenhouse", side_effect=ATSFetchError("not_found")):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["job", "search", "--source", "greenhouse", "--company", "nobody", "--workspace", str(ws)],
        )
    assert result.exit_code == 1


def test_job_search_quit(ws):
    postings = [
        {"source_id": "1", "title": "SRE", "url": "https://example.com", "location": None, "description": ""}
    ]
    with patch("careeros.cli.job_cmd.fetch_greenhouse", return_value=postings):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["job", "search", "--source", "greenhouse", "--company", "acme", "--workspace", str(ws)],
            input="q\n",
        )
    assert result.exit_code == 0
    storage = LocalFilesystemStorage(str(ws))
    assert Job.list_all(storage) == []
