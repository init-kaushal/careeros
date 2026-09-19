from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.discover_and_apply_cmd import discover_and_apply_app
from careeros.core.models import AutomationPolicy, Job, Profile, Skill, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


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


def _posting(company="Acme", title="Senior SRE", url="https://boards.greenhouse.io/acme/jobs/1"):
    return {"source_board": "linkedin", "title": title, "company": company, "location": "SF", "url": url}


class TestDiscoverAndApplyCmd:
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

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
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

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
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

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=postings))}), \
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

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
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

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
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

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=postings))}), \
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

    def test_launch_browser_always_headless(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser") as mock_browser:
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        mock_browser.assert_called_with(headless=True)
