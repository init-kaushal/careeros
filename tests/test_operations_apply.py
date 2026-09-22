import hashlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from careeros.browser.driver import BrowserProfileBusy
from careeros.core.models import Approval, Job, PolicyConfig, Profile
from careeros.operations.apply import propose_apply
from careeros.operations.approvals import PENDING, SUPERSEDED
from careeros.operations.errors import (
    BoardSessionRequired, BrowserUnavailable, DraftFailed, EntityNotFound,
    PolicyBlocked,
)
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
