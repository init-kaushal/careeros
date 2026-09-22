import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from careeros.browser.driver import BrowserProfileBusy
from careeros.core.models import Approval, Job, PolicyConfig, Profile
from careeros.operations.apply import execute_apply, propose_apply
from careeros.operations.approvals import (
    APPROVED, DECLINED, EXECUTED, FAILED, PENDING, SUPERSEDED, resolve_approval,
)
from careeros.operations.errors import (
    ApprovalNotGranted, ArtifactChanged, BoardSessionRequired, BrowserUnavailable,
    DraftFailed, EntityNotFound, FillIncomplete, MalformedApproval, NoFillerAvailable,
    PolicyBlocked, ResumeNotFound,
)
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

JOB_ID = "acme-sre-abc1"
GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"
LINKEDIN_URL = "https://www.linkedin.com/jobs/view/1"
COVER_LETTER = "Dear Hiring Manager,\n\nI would love to join Acme."
RESUME_BYTES = b"%PDF-1.4 fake resume bytes for testing"

PAYLOAD_KEYS = {
    "job_id", "job_url", "cover_letter_storage_path", "resume_storage_path",
    "filler_platform", "cover_letter_sha256", "resume_sha256", "profile_sha256",
}


def _runtime(tmp_path, url=GREENHOUSE_URL, with_resume=True, description=None):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Job(
        id=JOB_ID, source="browse", url=url, company="Acme", title="Senior SRE",
        stage="saved", description=description or "We need a strong SRE.",
        created_at=now, updated_at=now,
    ).save(storage)
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    if with_resume:
        storage.atomic_write("resumes/versions/resume.pdf", RESUME_BYTES)
    return open_local_runtime(storage, session_id="sess-1")


def _propose(runtime, **kwargs):
    with patch("careeros.operations.apply.generate_cover_letter", return_value=COVER_LETTER):
        return propose_apply(runtime, JOB_ID, action_label="apply", **kwargs)


def _log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


class TestProposeApply:
    def test_no_url_raises_entity_not_found_naming_job_update(self, tmp_path):
        runtime = _runtime(tmp_path, url=None)
        with pytest.raises(EntityNotFound) as exc:
            _propose(runtime)
        assert "careeros job update" in str(exc.value)

    def test_missing_job_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(EntityNotFound):
            propose_apply(runtime, "nobody", action_label="apply")

    def test_no_resume_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path, with_resume=False)
        with pytest.raises(EntityNotFound):
            _propose(runtime)

    def test_a_blocked_company_logs_then_raises_without_drafting(self, tmp_path):
        runtime = _runtime(tmp_path)
        PolicyConfig(blocked_companies=["Acme"]).save(runtime.storage)
        with patch("careeros.operations.apply.generate_cover_letter") as mock_gen:
            with pytest.raises(PolicyBlocked) as exc:
                propose_apply(runtime, JOB_ID, action_label="apply")
        assert exc.value.rule == "blocked_company:Acme"
        mock_gen.assert_not_called()
        assert "policy_blocked" in _log(runtime.storage)

    def test_an_empty_cover_letter_raises_draft_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(DraftFailed):
            with patch("careeros.operations.apply.generate_cover_letter", return_value=""):
                propose_apply(runtime, JOB_ID, action_label="apply")

    def test_no_filler_for_the_url_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path, url="https://unsupported-board.example.com/job/1")
        with patch("careeros.operations.apply.FILLERS", []):
            with pytest.raises(EntityNotFound):
                _propose(runtime)

    def test_no_resume_raises_the_specific_resume_not_found_type(self, tmp_path):
        # Pins both the type (so a caller like discover_and_apply_cmd can
        # dispatch on it instead of string-matching the message) and that it
        # is still an EntityNotFound (so apply_cmd.py's existing catch, and
        # the two tests above, keep working unmodified).
        runtime = _runtime(tmp_path, with_resume=False)
        with pytest.raises(ResumeNotFound) as exc:
            _propose(runtime)
        assert isinstance(exc.value, EntityNotFound)
        assert str(exc.value) == "No resume found in resumes/versions/ — add one first."

    def test_no_filler_raises_the_specific_no_filler_available_type(self, tmp_path):
        url = "https://unsupported-board.example.com/job/1"
        runtime = _runtime(tmp_path, url=url)
        with patch("careeros.operations.apply.FILLERS", []):
            with pytest.raises(NoFillerAvailable) as exc:
                _propose(runtime)
        assert isinstance(exc.value, EntityNotFound)
        assert str(exc.value) == "No filler available for this URL: " + url

    def test_linkedin_without_a_session_raises_board_session_required(self, tmp_path):
        runtime = _runtime(tmp_path, url=LINKEDIN_URL)
        with patch("careeros.operations.apply.check_board_sessions",
                    return_value={"linkedin": False}) as cbs:
            with pytest.raises(BoardSessionRequired) as exc:
                _propose(runtime)
        assert exc.value.board == "linkedin"
        cbs.assert_called_once_with(["linkedin"])

    def test_linkedin_profile_busy_raises_browser_unavailable(self, tmp_path):
        runtime = _runtime(tmp_path, url=LINKEDIN_URL)
        with patch("careeros.operations.apply.check_board_sessions",
                    side_effect=BrowserProfileBusy("already in use")):
            with pytest.raises(BrowserUnavailable) as exc:
                _propose(runtime)
        assert exc.value.profile_busy is True

    def test_greenhouse_apply_never_checks_a_session(self, tmp_path):
        runtime = _runtime(tmp_path, url=GREENHOUSE_URL)
        with patch("careeros.operations.apply.check_board_sessions") as cbs:
            _propose(runtime)
        cbs.assert_not_called()

    def test_happy_path_writes_cover_letter_and_opens_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)

        assert proposal.job_id == JOB_ID
        assert proposal.company == "Acme"
        assert proposal.title == "Senior SRE"
        assert proposal.cover_letter == COVER_LETTER
        assert proposal.filler_platform == "Greenhouse"
        assert proposal.cover_letter_storage_path == "applications/" + JOB_ID + "/cover_letter.txt"
        assert runtime.storage.read(proposal.cover_letter_storage_path).decode() == COVER_LETTER

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == PENDING
        assert approval.action == "apply_to_job"
        assert set(approval.payload.keys()) == PAYLOAD_KEYS

    def test_records_all_eight_payload_keys_correctly(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        payload = approval.payload

        assert payload["job_id"] == JOB_ID
        assert payload["job_url"] == GREENHOUSE_URL
        assert payload["cover_letter_storage_path"] == "applications/" + JOB_ID + "/cover_letter.txt"
        assert payload["resume_storage_path"] == "resumes/versions/resume.pdf"
        assert payload["filler_platform"] == "Greenhouse"
        assert payload["cover_letter_sha256"] == hashlib.sha256(COVER_LETTER.encode()).hexdigest()
        assert payload["resume_sha256"] == hashlib.sha256(RESUME_BYTES).hexdigest()

        profile = Profile.load_or_empty(runtime.storage)
        assert payload["profile_sha256"] == hashlib.sha256(profile.model_dump_json().encode()).hexdigest()

    def test_regenerating_supersedes_the_prior_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _propose(runtime)
        with patch("careeros.operations.apply.generate_cover_letter", return_value="A different letter."):
            second = propose_apply(runtime, JOB_ID, action_label="apply")
        assert Approval.load(runtime.storage, first.approval_id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.approval_id).state == PENDING

    def test_explicit_summary_overrides_the_default(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime, summary="Custom summary text.")
        assert proposal.summary == "Custom summary text."
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.summary == "Custom summary text."

    def test_default_summary_matches_the_cli_prompt_text(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        assert proposal.summary == (
            "About to fill the Greenhouse application for Acme — Senior SRE. Proceed?"
        )

    def test_explicit_jd_text_is_used_in_preference_to_job_description(self, tmp_path):
        runtime = _runtime(tmp_path, description="This is the stored job description.")
        with patch("careeros.operations.apply.generate_cover_letter",
                    return_value=COVER_LETTER) as mock_gen:
            propose_apply(runtime, JOB_ID, action_label="apply", jd_text="Override JD text.")
        assert mock_gen.call_args[0][0] == "Override JD text."

    def test_default_jd_text_is_derived_from_the_job_description(self, tmp_path):
        runtime = _runtime(tmp_path, description="This is the stored job description.")
        with patch("careeros.operations.apply.generate_cover_letter",
                    return_value=COVER_LETTER) as mock_gen:
            propose_apply(runtime, JOB_ID, action_label="apply")
        assert mock_gen.call_args[0][0] == "This is the stored job description."

    def test_action_label_is_required(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(TypeError):
            with patch("careeros.operations.apply.generate_cover_letter", return_value=COVER_LETTER):
                propose_apply(runtime, JOB_ID)


def _approve(runtime, approval_id, reason="user said yes"):
    return resolve_approval(
        runtime, approval_id, ApprovalResult(approved=True, reason=reason),
        action_label="apply",
    )


def _filler_mock(platform="Greenhouse", fill_return=True):
    filler = MagicMock()
    filler.platform = platform
    filler.fill.return_value = fill_return
    return filler


@contextmanager
def _ok_launch_browser(headless=False):
    yield MagicMock(), MagicMock()


class TestExecuteApply:
    @pytest.mark.parametrize(
        "state", [PENDING, DECLINED, SUPERSEDED, FAILED, EXECUTED]
    )
    def test_refuses_any_state_but_approved(self, tmp_path, state):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"state": state}).save(runtime.storage)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(ApprovalNotGranted):
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        filler.fill.assert_not_called()
        # Verify the approval state was not mutated by the refusal.
        assert Approval.load(runtime.storage, proposal.approval_id).state == state

    def test_refuses_to_execute_the_same_approval_twice(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
            with pytest.raises(ApprovalNotGranted):
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        # The single-use proof: the form must not have been submitted twice,
        # not merely that the second call raised.
        assert filler.fill.call_count == 1

    def test_refuses_when_the_cover_letter_changed_after_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        runtime.storage.atomic_write(proposal.cover_letter_storage_path, b"a different cover letter")
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(ArtifactChanged) as exc:
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert exc.value.path == proposal.cover_letter_storage_path
        filler.fill.assert_not_called()
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_refuses_when_the_resume_changed_after_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        runtime.storage.atomic_write("resumes/versions/resume.pdf", b"%PDF-1.4 a different resume")
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(ArtifactChanged) as exc:
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert exc.value.path == "resumes/versions/resume.pdf"
        filler.fill.assert_not_called()
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_refuses_when_the_profile_changed_after_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        Profile(name="Someone Else", email="someone@example.com").save(runtime.storage)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(ArtifactChanged) as exc:
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert exc.value.path == "profile/profile.json"
        filler.fill.assert_not_called()
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_a_malformed_payload_raises_rather_than_key_error(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"payload": {}}).save(runtime.storage)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(MalformedApproval):
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        filler.fill.assert_not_called()

    def test_no_matching_filler_raises_malformed_approval(self, tmp_path):
        """The approval names a platform this build no longer has a filler for.

        Submitting through a *different* filler than the one that was
        approved would be wrong, so this must fail rather than silently pick
        another filler.
        """
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        other_filler = _filler_mock(platform="SomeOtherPlatform")
        with patch("careeros.operations.apply.FILLERS", [other_filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(MalformedApproval):
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        other_filler.fill.assert_not_called()
        # Nothing was attempted, so the approval must stay approved and retryable.
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_fill_incomplete_leaves_the_stage_untouched(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock(fill_return=False)
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            with pytest.raises(FillIncomplete):
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert Job.load(runtime.storage, JOB_ID).stage == "saved"
        assert Job.load(runtime.storage, JOB_ID).applied_at is None
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == FAILED
        assert "apply_incomplete" in _log(runtime.storage)

    def test_import_error_raises_browser_unavailable_and_leaves_the_stage_untouched(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser",
                   MagicMock(side_effect=ImportError("no module named playwright"))):
            with pytest.raises(BrowserUnavailable) as exc:
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert "playwright install" in str(exc.value)
        assert Job.load(runtime.storage, JOB_ID).stage == "saved"
        filler.fill.assert_not_called()
        # The approval must be a truthful failed, not a stale executed: the
        # browser never launched, so nothing was submitted, but mark_executed
        # already ran and the attempt must be recorded as having failed.
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == FAILED
        assert approval.detail == "ImportError"
        assert "apply_failed" in _log(runtime.storage)

    def test_browser_profile_busy_raises_browser_unavailable_with_the_flag_set(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser",
                   MagicMock(side_effect=BrowserProfileBusy("already in use"))):
            with pytest.raises(BrowserUnavailable) as exc:
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert exc.value.profile_busy is True
        assert Job.load(runtime.storage, JOB_ID).stage == "saved"
        filler.fill.assert_not_called()
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == FAILED
        assert approval.detail == "BrowserProfileBusy"
        assert "apply_failed" in _log(runtime.storage)

    def test_a_generic_browser_exception_marks_the_approval_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock()
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser",
                   MagicMock(side_effect=RuntimeError("chrome crashed"))):
            with pytest.raises(BrowserUnavailable) as exc:
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")
        assert "chrome crashed" in str(exc.value)
        assert Job.load(runtime.storage, JOB_ID).stage == "saved"
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == FAILED
        assert approval.detail == "RuntimeError"
        assert "apply_failed" in _log(runtime.storage)

    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: ImportError(
                "No module named 'playwright' (checked "
                "/Users/fakeuser/Library/Application Support/careeros/browser)"
            ),
            lambda: BrowserProfileBusy(
                "The CareerOS browser profile is already in use: "
                "/Users/fakeuser/Library/Application Support/careeros/browser (lock held)"
            ),
            lambda: RuntimeError(
                "chrome crashed while reading "
                "/Users/fakeuser/Library/Application Support/careeros/browser/SingletonLock"
            ),
        ],
        ids=["import_error", "profile_busy", "generic_exception"],
    )
    def test_no_browser_exception_message_reaches_a_persisted_record(self, tmp_path, exc_factory):
        """Neither the approval's detail nor the activity log may ever hold
        the raw exception message — only its type name — because a
        Playwright or profile-lock message can contain the browser
        profile's filesystem path, and the activity log is append-only, so
        anything that landed there could never be scrubbed.
        """
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock()
        fake_path = "/Users/fakeuser/Library/Application Support/careeros/browser"
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser",
                   MagicMock(side_effect=exc_factory())):
            with pytest.raises(BrowserUnavailable):
                execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert fake_path not in (approval.detail or "")
        assert fake_path not in _log(runtime.storage)

    def test_happy_path_marks_applied_and_logs_the_resume_used(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock(fill_return=True)
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            result = execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")

        filler.fill.assert_called_once()
        assert result.job_id == JOB_ID
        assert result.company == "Acme"
        assert result.title == "Senior SRE"
        assert result.resume_storage_path == "resumes/versions/resume.pdf"
        assert result.applied_at

        job = Job.load(runtime.storage, JOB_ID)
        assert job.stage == "applied"
        assert job.applied_at == result.applied_at

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == EXECUTED

        log = _log(runtime.storage)
        assert "job_applied" in log
        assert "resumes/versions/resume.pdf" in log

        # filler.fill(page, job, profile, cover_letter_text, cover_letter_path, resume_path):
        # the last two arguments must be real, absolute filesystem paths —
        # a real form uploader cannot use a workspace-relative path — so
        # this pins runtime.storage.resolve() actually being used rather
        # than the raw storage-relative strings.
        _, _, _, _, cover_letter_path_arg, resume_path_arg = filler.fill.call_args[0]
        assert cover_letter_path_arg == runtime.storage.resolve(proposal.cover_letter_storage_path)
        assert resume_path_arg == runtime.storage.resolve("resumes/versions/resume.pdf")

    def test_the_url_navigated_to_is_the_one_bound_at_approval_time(self, tmp_path):
        """Editing job.url after approval must not change what gets navigated to.

        job_url is recorded on the payload at propose time, exactly like
        outreach's subject, and read back verbatim rather than re-derived
        from a freshly-reloaded Job.
        """
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        job = Job.load(runtime.storage, JOB_ID)
        job.model_copy(update={"url": "https://boards.greenhouse.io/acme/jobs/999"}).save(runtime.storage)

        filler = _filler_mock(fill_return=True)
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")

        called_job = filler.fill.call_args[0][1]
        assert called_job.url == GREENHOUSE_URL

    def test_a_post_approval_url_edit_survives_a_successful_execute(self, tmp_path):
        """The persisted Job record must not be silently reverted to the
        approved URL.

        job_for_fill (a copy carrying the bound job_url) is what the filler
        navigates to, but the record execute_apply saves back at the end
        must come from the untouched reload — otherwise a legitimate
        post-approval correction to job.url (e.g. the posting moved) would
        be clobbered by the stale value bound at propose time, even though
        execute_apply's contract is limited to updating stage, applied_at,
        and updated_at.
        """
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        edited_url = "https://boards.greenhouse.io/acme/jobs/999"
        job = Job.load(runtime.storage, JOB_ID)
        job.model_copy(update={"url": edited_url}).save(runtime.storage)

        filler = _filler_mock(fill_return=True)
        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
            execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")

        # The filler still navigated to the approved URL...
        called_job = filler.fill.call_args[0][1]
        assert called_job.url == GREENHOUSE_URL
        # ...but the persisted record keeps the post-approval edit.
        assert Job.load(runtime.storage, JOB_ID).url == edited_url

    def test_teardown_failure_after_a_successful_submit_still_counts_as_applied(self, tmp_path):
        """A submission that went out must not be recorded as a failure.

        filler.fill returns True (the application was submitted), and then
        the browser context's teardown (context.close(), inside the
        `with launch_browser(...)` block) raises. That is an operational
        anomaly, not an application failure: the job must still advance to
        applied, the approval must still read executed (never regressed to
        failed), both job_applied and a distinct apply_teardown_failed event
        must be logged, and execute_apply must return the ApplyResult
        normally rather than raising — an unhandled exception here would
        otherwise look identical to a failed submission to a caller such as
        the scheduled discover-and-apply command, whose retry would then
        resubmit an application that already went out.
        """
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock(fill_return=True)

        @contextmanager
        def teardown_fails(headless=False):
            yield MagicMock(), MagicMock()
            raise RuntimeError("context.close() failed: profile lock held")

        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", teardown_fails):
            result = execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")

        filler.fill.assert_called_once()
        assert result.job_id == JOB_ID
        assert result.applied_at

        job = Job.load(runtime.storage, JOB_ID)
        assert job.stage == "applied"
        assert job.applied_at == result.applied_at

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == EXECUTED

        log = _log(runtime.storage)
        assert "job_applied" in log
        assert "apply_teardown_failed" in log
        # Credential hygiene applies here too: the teardown exception's
        # message can carry a profile path, so only its type name may land
        # in the log.
        assert "profile lock held" not in log

    def test_mark_executed_happens_before_the_browser_is_launched(self, tmp_path):
        """Verify the approval is consumed before the external action.

        This prevents crashes or concurrency from causing a duplicate
        submission. Reasoned failure-on-reversal: if mark_executed ran after
        the browser step instead of before it, the approval would still read
        APPROVED at the moment launch_browser is called, so the recorded
        state below would be APPROVED instead of EXECUTED and this assertion
        would fail.
        """
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock(fill_return=True)

        observed_states = []

        @contextmanager
        def fake_launch_browser(headless=False):
            approval = Approval.load(runtime.storage, proposal.approval_id)
            observed_states.append(approval.state)
            yield MagicMock(), MagicMock()

        with patch("careeros.operations.apply.FILLERS", [filler]), \
             patch("careeros.operations.apply.launch_browser", fake_launch_browser):
            execute_apply(runtime, proposal.approval_id, headless=True, action_label="apply")

        assert observed_states == [EXECUTED]

    def test_action_label_is_required(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        filler = _filler_mock()
        with pytest.raises(TypeError):
            with patch("careeros.operations.apply.FILLERS", [filler]), \
                 patch("careeros.operations.apply.launch_browser", _ok_launch_browser):
                execute_apply(runtime, proposal.approval_id, headless=True)
