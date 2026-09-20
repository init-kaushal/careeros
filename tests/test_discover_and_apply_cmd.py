import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.discover_and_apply_cmd import discover_and_apply_app
from careeros.core.job_store import JobStore
from careeros.core.models import AutomationPolicy, Job, PolicyConfig, Profile, Skill, Skills
from careeros.sources.ats import ATSFetchError
from careeros.sources.base import Posting
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()
_NOW = "2026-09-20T00:00:00+00:00"


def _setup_workspace(tmp_path, policy: AutomationPolicy | None = None, with_resume: bool = True):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    Profile(name="Alice", title="Senior SRE").save(storage)
    Skills(skills=[Skill(name="Kubernetes")]).save(storage)
    if policy is not None:
        policy.save(storage)
    if with_resume:
        storage.atomic_write("resumes/versions/resume.pdf", b"%PDF-1.4 fake resume")
    return str(tmp_path)


def _mock_launch(mock_page=None):
    if mock_page is None:
        mock_page = MagicMock()

    @contextmanager
    def _ctx(headless=False) -> Iterator:
        yield MagicMock(), mock_page

    return _ctx


def _mock_launch_counting(counter: list, mock_page=None):
    """Same as _mock_launch, but appends to `counter` on each context entry."""
    if mock_page is None:
        mock_page = MagicMock()

    @contextmanager
    def _ctx(headless=False) -> Iterator:
        counter.append(headless)
        yield MagicMock(), mock_page

    return _ctx


def _posting(company="Acme", title="Senior SRE", url="https://boards.greenhouse.io/acme/jobs/1"):
    return {"source_board": "linkedin", "title": title, "company": company, "location": "SF", "url": url}


class TestDiscoverAndApplyCmd:
    def test_runs_without_the_gate_flag(self, tmp_path):
        # The gate is gone: neither --i-accept-the-risk nor the env var is needed.
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"]
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 0
        assert "i-accept-the-risk" not in result.output
        assert "unsafe automation" not in result.output.lower()

    def test_removed_gate_flag_is_rejected(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"]
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        result = runner.invoke(
            discover_and_apply_app, ["--workspace", ws_path, "--i-accept-the-risk"]
        )
        assert result.exit_code != 0

    def test_unauthorized_board_is_skipped_and_logged(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5,
            boards=["linkedin", "indeed"],
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False, "indeed": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert "Unauthorized boards: linkedin" in result.output

        logs = sorted((tmp_path / "activity").glob("*.jsonl"))
        events = [json.loads(line) for line in logs[-1].read_text().strip().split("\n") if line]
        unauth = [e for e in events if e["event_type"] == "session_unauthorized"]
        assert len(unauth) == 1
        assert unauth[0]["entity_id"] == "linkedin"
        assert unauth[0]["status"] == "failed"

    def test_authorized_boards_still_run_when_another_is_unauthorized(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5,
            boards=["linkedin", "indeed"],
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        launches: list = []

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False, "indeed": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser",
                   _mock_launch_counting(launches)), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        # One board authorized -> exactly one discovery browser launch, headless.
        assert launches == [True]

    def test_all_boards_unauthorized_exits_non_zero(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5,
            boards=["linkedin", "indeed"],
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        launches: list = []
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False, "indeed": False}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser",
                   _mock_launch_counting(launches)):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1
        assert "careeros browser login" in result.output
        assert launches == []

    def test_locked_profile_during_session_check_reports_a_plain_message(self, tmp_path):
        # check_board_sessions is called bare here (not through preflight),
        # and it's the first thing to open the isolated profile in this
        # command. Patch the real seam (careeros.browser.session.launch_browser)
        # so this exercises check_board_sessions's actual implementation
        # raising, not a stand-in for it.
        from careeros.browser.driver import BrowserProfileBusy

        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        with patch(
            "careeros.browser.session.launch_browser",
            side_effect=BrowserProfileBusy("already in use by another CareerOS process"),
        ):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1
        assert "already in use" in result.output
        assert "Traceback" not in result.output

    def test_unknown_board_override_errors_with_valid_list(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        result = runner.invoke(
            discover_and_apply_app, ["--board", "nonsense", "--workspace", ws_path]
        )
        assert result.exit_code == 1
        assert "Unknown board 'nonsense'" in result.output
        assert "linkedin" in result.output

        activity_dir = tmp_path / "activity"
        logs = sorted(activity_dir.glob("*.jsonl")) if activity_dir.exists() else []
        events = [
            json.loads(line)
            for log in logs
            for line in log.read_text().strip().split("\n")
            if line
        ]
        assert not any(e["event_type"] == "session_unauthorized" for e in events)

    def test_interviewing_job_with_applied_at_is_not_reapplied(self, tmp_path):
        # Review finding: dedup previously keyed "already_applied" off
        # stage == "applied", so a job advanced to interviewing/offer/closed
        # (routine via `careeros job update --stage`) would be re-applied to
        # if the posting were rediscovered. applied_at persists across later
        # stage changes and is the correct signal.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        now = datetime.now(timezone.utc).isoformat()
        Job(
            id="acme-sre-existing", source="browse", url="https://boards.greenhouse.io/acme/jobs/1",
            company="Acme", title="Senior SRE", stage="interviewing",
            applied_at=now, created_at=now, updated_at=now,
        ).save(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "interviewing"

    def test_saved_job_without_applied_at_is_still_eligible(self, tmp_path):
        # Regression guard for the fix above: a job that was saved but never
        # actually applied to must still be eligible for auto-apply.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        now = datetime.now(timezone.utc).isoformat()
        Job(
            id="acme-sre-existing", source="browse", url="https://boards.greenhouse.io/acme/jobs/1",
            company="Acme", title="Senior SRE", stage="saved",
            applied_at=None, created_at=now, updated_at=now,
        ).save(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_called_once()
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "applied"

    def test_missing_policy_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, policy=None)
        result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1
        assert "automation_policy" in result.output.lower()

    def test_job_below_threshold_saved_not_applied(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 50, "reasoning": "meh"}), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "saved"

    def test_job_at_threshold_auto_applied(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_called_once()
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "applied"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "automation" in log_content

    def test_max_auto_applies_per_run_stops_further_applies(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=1, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        postings = [
            _posting(company="Acme", title="Role One", url="https://boards.greenhouse.io/acme/jobs/1"),
            _posting(company="Beta", title="Role Two", url="https://boards.greenhouse.io/beta/jobs/2"),
        ]

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=postings))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_filler.fill.call_count == 1
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 2
        stages = sorted(j.stage for j in jobs)
        assert stages == ["applied", "saved"]

    def test_cover_letter_failure_skips_job_without_crashing(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value=""), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        assert "skipped" in result.output.lower()
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert jobs[0].stage == "saved"

    def test_no_filler_available_logs_activity_and_continues(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = False

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "no_filler_available" in log_content

    def test_storage_error_during_apply_skips_job_without_aborting_run(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        postings = [
            _posting(company="Acme", title="Role One", url="https://boards.greenhouse.io/acme/jobs/1"),
            _posting(company="Beta", title="Role Two", url="https://boards.greenhouse.io/beta/jobs/2"),
        ]
        original_atomic_write = LocalFilesystemStorage.atomic_write

        def flaky_atomic_write(self, path, data):
            if path.startswith("applications/acme-") and path.endswith("cover_letter.txt"):
                raise ValueError("simulated disk-full / corrupted write")
            return original_atomic_write(self, path, data)

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=postings))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch.object(LocalFilesystemStorage, "atomic_write", flaky_atomic_write), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        # Acme's cover-letter write raised inside the try/except — it must be skipped,
        # not crash the whole run, and Beta must still be attempted and applied.
        assert mock_filler.fill.call_count == 1
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 2
        stages = sorted(j.stage for j in jobs)
        assert stages == ["applied", "saved"]
        applied_jobs = [j for j in jobs if j.stage == "applied"]
        assert applied_jobs[0].company == "Beta"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "apply_error" in log_content

    def test_fill_returns_false_logs_apply_incomplete(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = False
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert jobs[0].stage == "saved"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "apply_incomplete" in log_content

    def test_second_run_does_not_reapply_to_already_applied_job(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            first = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
            second = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert first.exit_code == 0
        assert second.exit_code == 0
        # The identical posting rediscovered on the second run must not create a
        # second Job record or trigger a second real application.
        assert mock_filler.fill.call_count == 1
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "applied"
        assert "Duplicates: 1" in second.output

    def test_duplicate_saved_job_reuses_existing_id_instead_of_creating_new_one(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 50, "reasoning": "meh"}), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
            second = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert second.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert "Duplicates: 1" in second.output

    def test_policy_blocked_company_skipped_and_counted(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        PolicyConfig(blocked_companies=["Acme"]).save(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter") as mock_gen, \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_gen.assert_not_called()
        mock_filler.fill.assert_not_called()
        assert "Blocked: 1" in result.output
        jobs = Job.list_all(storage)
        assert jobs[0].stage == "saved"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "policy_blocked" in log_content

    def test_launch_browser_always_headless(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser") as mock_browser:
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        mock_browser.assert_called_with(headless=True)

    def test_locked_profile_during_auto_apply_aborts_the_run(self, tmp_path):
        from contextlib import contextmanager as _contextmanager

        from careeros.browser.driver import BrowserProfileBusy

        # Two eligible jobs: if a locked profile were treated as a per-job
        # failure, the second would still burn a cover-letter LLM call.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        postings = [
            _posting(company="Acme", title="Role One", url="https://boards.greenhouse.io/acme/jobs/1"),
            _posting(company="Beta", title="Role Two", url="https://boards.greenhouse.io/beta/jobs/2"),
        ]

        # The discovery loop's launch_browser call (once, for the "linkedin"
        # board) must succeed normally; only the auto-apply loop's calls must
        # raise BrowserProfileBusy, so this test exercises the auto-apply site
        # specifically and not the discovery site.
        call_count = {"n": 0}

        def _launch(headless=True):
            call_count["n"] += 1
            if call_count["n"] == 1:
                @_contextmanager
                def _ctx():
                    yield MagicMock(), mock_page
                return _ctx()
            raise BrowserProfileBusy("already in use by another CareerOS process")

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=postings))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", side_effect=_launch), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 1
        assert "already in use" in result.output
        assert mock_gen.call_count <= 1

    def test_title_variant_rediscovery_does_not_create_a_second_record(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        first = _posting(title="Senior SRE", url="https://linkedin.test/1")
        second = _posting(title="Senior Site Reliability Engineer",
                          url="https://greenhouse.test/9")
        for postings in ([first], [second]):
            with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                       return_value={"linkedin": True}), \
                 patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
                 patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
                 patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
                 patch("careeros.cli.discover_and_apply_cmd.score_job",
                       return_value={"score": 10, "reasoning": "meh"}):
                scrapers.__getitem__.return_value.search.return_value = postings
                runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert len(Job.list_all(storage)) == 1

    def test_already_applied_job_is_not_reapplied_after_rediscovery(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=50, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="linkedin", company="Acme", title="Senior SRE",
            url="https://linkedin.test/1", stage="applied", applied_at=_NOW,
            created_at=_NOW, updated_at=_NOW,
        ))
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            scrapers.__getitem__.return_value.search.return_value = [
                _posting(title="Senior SRE", url="https://linkedin.test/1")
            ]
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        assert "Duplicates: 1" in result.output

    def test_cross_source_same_job_resolves_to_one_record(self, tmp_path):
        # The ROADMAP's exit condition, asserted directly. Titles deliberately
        # differ ("Senior Site Reliability Engineer" on Greenhouse vs "Senior
        # SRE" on LinkedIn) so this exercises the fuzzy company+title tier's
        # abbreviation normalization across two boards — the realistic
        # cross-source case the old exact (company, title) stopgap could not
        # collapse.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="greenhouse", company="Acme",
            title="Senior Site Reliability Engineer",
            url="https://boards.greenhouse.io/acme/jobs/1",
            created_at=_NOW, updated_at=_NOW,
        ))
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            scrapers.__getitem__.return_value.search.return_value = [
                _posting(company="Acme", title="Senior SRE", url="https://www.linkedin.com/jobs/view/7")
            ]
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert len(Job.list_all(storage)) == 1

    def test_url_tier_alone_resolves_unrelated_looking_postings_to_one_record(self, tmp_path):
        # Pins the FIRST tier of is_same_posting independently of the fuzzy
        # company+title tier: two records with entirely different company
        # and title, whose URLs canonicalize equal once a tracking param is
        # stripped, must still resolve to one record.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="linkedin", company="Acme", title="Senior SRE",
            url="https://boards.greenhouse.io/acme/jobs/1",
            created_at=_NOW, updated_at=_NOW,
        ))
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            scrapers.__getitem__.return_value.search.return_value = [
                _posting(company="Globex", title="Staff Widget Designer",
                         url="https://boards.greenhouse.io/acme/jobs/1?utm_source=li")
            ]
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert len(Job.list_all(storage)) == 1

    def test_unusable_posting_is_skipped_without_aborting_the_batch(self, tmp_path):
        # Plan-defect guard: posting_from_scrape raises ValueError when a
        # scraped dict's company is empty (a missed selector on the browser
        # scraper's side). The bad card is FIRST here deliberately — an
        # implementation that aborted the loop on the first failure would
        # still pass a test where the bad card is last.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        bad = _posting(company="", title="Bad Posting", url="https://boards.greenhouse.io/bad/1")
        good = _posting(company="Acme", title="Senior SRE", url="https://boards.greenhouse.io/acme/jobs/2")
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            scrapers.__getitem__.return_value.search.return_value = [bad, good]
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].title == "Senior SRE"
        assert jobs[0].company == "Acme"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "posting_unusable" in log_content

    def test_api_source_postings_join_the_pipeline(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=[])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "stripe", "company": "Stripe"}]}).encode())
        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.return_value = [Posting(
            source="greenhouse", title="Senior SRE", company="Stripe",
            url="https://boards.greenhouse.io/stripe/jobs/1")]
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={}), \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 0
        assert len(Job.list_all(storage)) == 1

    def test_unavailable_source_is_skipped_and_logged(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "nope", "company": "Nope"}]}).encode())
        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.side_effect = ATSFetchError("not_found")
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            scrapers.__getitem__.return_value.search.return_value = []
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert "Unavailable sources: greenhouse:nope" in result.output
        logs = sorted((tmp_path / "activity").glob("*.jsonl"))
        events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
        unavail = [e for e in events if e["event_type"] == "source_unavailable"]
        assert len(unavail) == 1
        assert unavail[0]["status"] == "failed"

    def test_all_boards_unauthorized_and_all_sources_unavailable_exits_non_zero(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "nope", "company": "Nope"}]}).encode())
        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.side_effect = ATSFetchError("not_found")
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1

    def test_two_sources_surfacing_one_posting_applies_once(self, tmp_path):
        # Review CRITICAL 2: `eligible` used to be built per posting, not per
        # job. When the browser (linkedin) and an API source (greenhouse)
        # surface the same posting in one run, both discovery dicts merge to
        # the same job_id, both carry already_applied=False from discovery
        # time, and the apply loop never re-read persisted state — so it
        # applied twice. Reproduced here with two distinct sources resolving
        # to one Job via the fuzzy company+title tier, and asserted fixed.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "acme", "company": "Acme"}]}).encode())

        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()

        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.return_value = [Posting(
            source="greenhouse", title="Senior SRE", company="Acme",
            url="https://boards.greenhouse.io/acme/jobs/1")]

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS",
                   {"linkedin": MagicMock(search=MagicMock(return_value=[
                       _posting(company="Acme", title="Senior SRE",
                                url="https://www.linkedin.com/jobs/view/9")]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_filler.fill.call_count == 1
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "applied"
