import pytest
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from careeros.cli.browse_cmd import browse_app
from careeros.core.models import Profile, Skill, Skills, Goals, Job
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _setup_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    Profile(name="Alice", title="Senior SRE").save(storage)
    Skills(skills=[Skill(name="Kubernetes")]).save(storage)
    return str(tmp_path)


def _mock_launch(mock_page=None):
    if mock_page is None:
        mock_page = MagicMock()

    @contextmanager
    def _ctx(headless=False) -> Iterator:
        yield MagicMock(), mock_page

    return _ctx


def _mock_postings():
    return [
        {"source_board": "linkedin", "title": "Senior SRE", "company": "Acme", "location": "SF", "url": "https://example.com/jobs/1"},
        {"source_board": "linkedin", "title": "Platform Engineer", "company": "Beta", "location": "Remote", "url": "https://example.com/jobs/2"},
    ]


def _mock_score(score=85):
    return {"score": score, "reasoning": "Great fit.", "strengths": ["Kubernetes"], "gaps": []}


class TestBrowseEndToEnd:
    def test_saves_selected_job(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd text"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1\n")

        assert result.exit_code == 0
        assert "Saved 1 job" in result.output

    def test_job_written_to_workspace(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [_mock_postings()[0]]

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1\n")

        storage = LocalFilesystemStorage(ws)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].company == "Acme"
        assert jobs[0].source == "linkedin"

    def test_min_score_filters_out_low_scores(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        scores = [_mock_score(score=90), _mock_score(score=40)]
        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", side_effect=scores):
            result = runner.invoke(
                browse_app,
                ["--board", "linkedin", "--min-score", "80", "--workspace", ws],
                input="1\n",
            )

        assert result.exit_code == 0
        assert "Platform Engineer" not in result.output

    def test_all_below_min_score_prints_no_jobs_found(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [_mock_postings()[0]]

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score(score=20)):
            result = runner.invoke(
                browse_app,
                ["--board", "linkedin", "--min-score", "80", "--workspace", ws],
                input="",
            )

        assert "No jobs found" in result.output

    def test_user_quits_saves_zero_jobs(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="q\n")

        storage = LocalFilesystemStorage(ws)
        assert Job.list_all(storage) == []

    def test_headless_flag_passed_to_launch_browser(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = []
        calls = []

        @contextmanager
        def capturing_launch(headless=False):
            calls.append(headless)
            yield MagicMock(), MagicMock()

        with patch("careeros.cli.browse_cmd.launch_browser", capturing_launch), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--headless", "--workspace", ws], input="q\n")

        assert calls == [True]

    def test_activity_log_written_after_save(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [_mock_postings()[0]]

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1\n")

        storage = LocalFilesystemStorage(ws)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = f"activity/{today}.jsonl"
        assert storage.exists(log_path)
        content = storage.read(log_path).decode()
        assert "job_added" in content

    def test_indeed_scraper_selected_for_indeed_board(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_linkedin = MagicMock()
        mock_indeed = MagicMock()
        mock_indeed.search.return_value = []

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_linkedin, "indeed": mock_indeed}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "indeed", "--workspace", ws], input="q\n")

        mock_indeed.search.assert_called_once()
        mock_linkedin.search.assert_not_called()


class TestBrowseDuplicateIndex:
    def test_duplicate_index_saves_once(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()), \
             patch("careeros.core.models.Job.save") as mock_save:
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1 1\n")

        assert result.exit_code == 0
        assert mock_save.call_count == 1
        assert "Saved 1 job" in result.output


class TestBrowseErrorHandling:
    def test_playwright_not_installed_prints_install_instructions(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        with patch("careeros.cli.browse_cmd.launch_browser", side_effect=ImportError("playwright")):
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws])
        assert "playwright install" in result.output.lower() or "pip install playwright" in result.output

    def test_board_url_without_url_flag_exits_1(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        result = runner.invoke(browse_app, ["--board", "url", "--workspace", ws])
        assert result.exit_code == 1
        assert "--url" in result.output

    def test_no_workspace_exits_1(self):
        from careeros.config import GlobalConfig
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig(workspace_path=None)):
            result = runner.invoke(browse_app, ["--board", "linkedin"])
        assert result.exit_code == 1

    def test_scraper_exception_prints_warning_continues(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.side_effect = Exception("network error")

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="q\n")

        assert result.exit_code == 0
        assert "No jobs found" in result.output or "warning" in result.output.lower() or "error" in result.output.lower()
