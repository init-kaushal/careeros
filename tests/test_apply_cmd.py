from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from careeros.cli.apply_cmd import apply_app
from careeros.config import GlobalConfig
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.runtime.base import ApprovalResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path, monkeypatch):
    """Prevent tests from touching the real ~/.config/careeros/config.json.

    apply_cmd() calls GlobalConfig.load() whenever --workspace isn't passed
    on the CLI (which is every invocation in this file, matching the given
    spec). Without isolation, tests would depend on ambient state on the
    machine running them. Tests that need to exercise the "no workspace"
    failure path explicitly patch GlobalConfig.load themselves and take
    precedence over this fixture.
    """
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    GlobalConfig(workspace_path=str(tmp_path / "workspace")).save()


def _make_job(url="https://boards.greenhouse.io/acme/jobs/123"):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-abc1",
        source="browse",
        url=url,
        company="Acme",
        title="Senior SRE",
        stage="saved",
        created_at=now,
        updated_at=now,
    )


def _make_profile():
    return Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE")


def _mock_runtime(tmp_path, resume_filename="resume.pdf", approved=True):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    storage.exists.return_value = True
    runtime = MagicMock()
    runtime.storage = storage
    runtime.request_approval.return_value = ApprovalResult(approved=approved)
    return runtime


class TestApplyCmdHappyPath:
    def test_successful_apply_updates_stage_to_applied(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        profile = _make_profile()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"

        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=profile), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Hiring Manager,\n\nGreat fit."), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(apply_app, ["acme-sre-abc1"])

        assert result.exit_code == 0
        runtime.storage.atomic_write.assert_called()
        save_calls = [str(c) for c in runtime.storage.atomic_write.call_args_list]
        assert any("cover_letter" in c for c in save_calls)
        job_saves = [c for c in runtime.storage.atomic_write.call_args_list if c.args[0] == "jobs/acme-sre-abc1.json"]
        assert job_saves, "job.save() was not called"
        assert b'"applied"' in job_saves[0].args[1]

    def test_successful_apply_logs_job_applied_event(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"

        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter text"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])

        runtime.record_activity.assert_called_once()
        event_arg = runtime.new_event.call_args
        assert event_arg[0][0] == "job_applied"


class TestApplyCmdFailurePaths:
    def test_no_workspace_exits_1(self):
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig(workspace_path=None)):
            result = runner.invoke(apply_app, ["some-job-id"])
        assert result.exit_code == 1

    def test_job_not_found_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", side_effect=FileNotFoundError):
            result = runner.invoke(apply_app, ["nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_job_no_url_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        job_no_url = Job(id="x", source="manual", url=None, company="Co", title="Role",
                         stage="saved", created_at=now, updated_at=now)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job_no_url):
            result = runner.invoke(apply_app, ["x"])
        assert result.exit_code == 1
        assert "url" in result.output.lower()

    def test_no_resume_exits_1(self, tmp_path):
        runtime = MagicMock()
        storage = MagicMock()
        storage.list.return_value = []
        runtime.storage = storage
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "resume" in result.output.lower()

    def test_cover_letter_generation_failure_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value=""):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "generation failed" in result.output.lower()

    def test_user_quits_review_loop_exits_0(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_user_declines_final_approval_exits_0(self, tmp_path):
        runtime = _mock_runtime(tmp_path, approved=False)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_filler_returns_false_exits_1_stage_not_updated(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = False
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "incomplete" in result.output.lower()
        assert not any(c.args[0] == "jobs/acme-sre-abc1.json" for c in runtime.storage.atomic_write.call_args_list)

    def test_playwright_not_installed_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser", side_effect=ImportError), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "playwright" in result.output.lower()

    def test_regenerate_calls_generate_again(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["r", "a"]):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])
        assert mock_gen.call_count == 2

    def test_model_flag_propagated_to_generate(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1", "--model", "gpt-4o"])
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("model") == "gpt-4o"
