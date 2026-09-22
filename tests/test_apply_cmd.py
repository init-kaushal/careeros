from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from careeros.cli.apply_cmd import apply_app
from careeros.config import GlobalConfig
from careeros.core.models import (
    Evidence,
    Goals,
    Job,
    PolicyConfig,
    Profile,
    ResumeVariant,
    Skills,
    VariantSection,
)
from careeros.core.resume_select import ResumeChoice
from careeros.runtime.base import ApprovalResult

runner = CliRunner()


def _unwrapped(output: str) -> str:
    """rich hard-wraps console output at the terminal width, splitting even a
    quoted command. Collapse it before matching on a phrase."""
    return " ".join(output.split())


def _make_variant():
    return ResumeVariant(
        job_id="acme-sre-abc1", job_company="Acme", job_title="Senior SRE",
        generated_at="2026-09-22T00:00:00+00:00", source_file="resumes/master.md",
        sections=[VariantSection(heading="Skills", entries=[
            Evidence(quote="Python, Go", line=2, source_file="resumes/master.md")
        ])],
    )


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


def _mock_runtime(tmp_path, resume_filename="resume.pdf", approved=True, tailored=False):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    # Path-aware: a blanket True would make every test claim a tailored
    # variant that does not exist.
    tailored_pdf = "resumes/versions/acme-sre-abc1/resume.pdf"
    tailored_json = "resumes/versions/acme-sre-abc1/variant.json"
    storage.exists.side_effect = lambda p: (
        tailored if p in (tailored_pdf, tailored_json) else True
    )
    storage.read.return_value = b"{}"
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

        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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

        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", side_effect=FileNotFoundError):
            result = runner.invoke(apply_app, ["nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_job_no_url_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        job_no_url = Job(id="x", source="manual", url=None, company="Co", title="Role",
                         stage="saved", created_at=now, updated_at=now)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job_no_url):
            result = runner.invoke(apply_app, ["x"])
        assert result.exit_code == 1
        assert "url" in result.output.lower()

    def test_no_resume_exits_1(self, tmp_path):
        runtime = MagicMock()
        storage = MagicMock()
        storage.list.return_value = []
        # No tailored variant either: select_resume must see this as "no
        # resume at all", not accidentally claim a tailored PDF exists just
        # because an unconfigured MagicMock.exists(...) is truthy by default.
        storage.exists.return_value = False
        runtime.storage = storage
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "resume" in result.output.lower()

    def test_policy_blocked_company_exits_1_without_requesting_approval(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig(blocked_companies=["Acme"])):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "blocked by policy" in result.output.lower()
        runtime.request_approval.assert_not_called()

    def test_policy_blocked_logs_policy_blocked_event(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig(blocked_companies=["Acme"])):
            runner.invoke(apply_app, ["acme-sre-abc1"])
        runtime.record_activity.assert_called_once()
        event_args = runtime.new_event.call_args
        assert event_args[0][0] == "policy_blocked"

    def test_cover_letter_generation_failure_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
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

    def test_linkedin_apply_requires_a_linkedin_session(self, tmp_path):
        from careeros.browser.fillers.linkedin import LinkedInFiller

        runtime = _mock_runtime(tmp_path)
        job = _make_job(url="https://www.linkedin.com/jobs/view/1")
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear team"), \
             patch("careeros.cli.apply_cmd.FILLERS", [LinkedInFiller()]), \
             patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])

        assert result.exit_code == 1
        assert "careeros browser login --board linkedin" in result.output
        mock_browser.assert_not_called()

    def test_linkedin_apply_reports_a_plain_message_when_profile_is_busy(self, tmp_path):
        # require_board_session (via preflight -> check_board_sessions) is the
        # first thing to open the isolated profile for a LinkedIn apply, ahead
        # of apply_cmd's own try/except around its later launch_browser call.
        # Patch the real seam so this exercises the pre-flight path itself.
        from careeros.browser.driver import BrowserProfileBusy
        from careeros.browser.fillers.linkedin import LinkedInFiller

        runtime = _mock_runtime(tmp_path)
        job = _make_job(url="https://www.linkedin.com/jobs/view/1")
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear team"), \
             patch("careeros.cli.apply_cmd.FILLERS", [LinkedInFiller()]), \
             patch("careeros.browser.session.launch_browser",
                   side_effect=BrowserProfileBusy("already in use by another CareerOS process")), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])

        assert result.exit_code == 1
        assert "already in use" in result.output
        assert "Traceback" not in result.output
        mock_browser.assert_not_called()

    def test_greenhouse_apply_needs_no_session(self, tmp_path):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller

        runtime = _mock_runtime(tmp_path)
        job = _make_job(url="https://boards.greenhouse.io/acme/jobs/1")
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear team"), \
             patch("careeros.cli.apply_cmd.FILLERS", [GreenhouseFiller()]), \
             patch("careeros.cli.preflight.check_board_sessions") as cbs, \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            mock_browser.return_value.__enter__ = MagicMock(
                return_value=(MagicMock(), MagicMock())
            )
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])

        cbs.assert_not_called()
        mock_browser.assert_called()


class TestApplyResumeSelection:
    def test_untailored_fallback_is_announced_as_not_tailored(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Acme,"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert "NOT tailored" in result.output

    def test_tailored_variant_is_used_and_announced(self, tmp_path):
        runtime = _mock_runtime(tmp_path, tailored=True)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Acme,"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert "tailored for this job" in result.output
        assert "NOT tailored" not in result.output
        runtime.storage.resolve.assert_any_call("resumes/versions/acme-sre-abc1/resume.pdf")

    def _run_with_choice(self, choice):
        runtime = MagicMock()
        runtime.request_approval.return_value = ApprovalResult(approved=False)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.select_resume", return_value=choice), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Acme,"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            return runner.invoke(apply_app, ["acme-sre-abc1"])

    def test_entry_count_is_announced_when_the_sidecar_is_valid(self, tmp_path):
        result = self._run_with_choice(ResumeChoice(
            path=str(tmp_path / "resume.pdf"),
            storage_path="resumes/versions/acme-sre-abc1/resume.pdf",
            tailored=True,
            variant=_make_variant(),
        ))
        assert "tailored for this job" in result.output
        assert "1 evidence-backed entries" in result.output
        assert "superseded" not in result.output

    def test_no_entry_count_is_announced_when_the_sidecar_is_unusable(self, tmp_path):
        # A sidecar whose pdf_sha256 does not match the PDF describes a
        # different document, so select_resume discards it. Printing a count
        # from it would be a claim about a file this is not.
        result = self._run_with_choice(ResumeChoice(
            path=str(tmp_path / "resume.pdf"),
            storage_path="resumes/versions/acme-sre-abc1/resume.pdf",
            tailored=True,
            variant=None,
        ))
        assert "tailored for this job" in result.output
        assert "evidence-backed entries" not in result.output
        assert "unavailable" in result.output
        # rich hard-wraps the line, so match against the unwrapped form.
        assert "careeros resume variant --job acme-sre-abc1" in _unwrapped(result.output)

    def test_a_superseded_master_is_warned_about(self, tmp_path):
        result = self._run_with_choice(ResumeChoice(
            path=str(tmp_path / "resume.pdf"),
            storage_path="resumes/versions/acme-sre-abc1/resume.pdf",
            tailored=True,
            variant=_make_variant(),
            stale_master=True,
        ))
        assert "superseded master" in result.output
        assert "resumes/master.md" in _unwrapped(result.output)
        assert "careeros resume variant --job acme-sre-abc1" in _unwrapped(result.output)
