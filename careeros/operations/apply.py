from __future__ import annotations

import hashlib
from dataclasses import dataclass

from careeros.browser.driver import BrowserProfileBusy
from careeros.browser.fillers.generic import GenericFiller
from careeros.browser.fillers.greenhouse import GreenhouseFiller
from careeros.browser.fillers.lever import LeverFiller
from careeros.browser.fillers.linkedin import LinkedInFiller
from careeros.browser.session import check_board_sessions
from careeros.core.models import Goals, Job, PolicyConfig, Profile, Skills
from careeros.core.policy_engine import PolicyEngine
from careeros.core.resume_select import ResumeChoice, select_resume
from careeros.operations.approvals import open_approval
from careeros.operations.errors import (
    BoardSessionRequired, BrowserUnavailable, DraftFailed, EntityNotFound,
    PolicyBlocked,
)
from careeros.runtime.base import AgentRuntime
from careeros.skills.cover_letter import generate_cover_letter
from careeros.storage.interface import StorageProvider

ACTION = "apply_to_job"

FILLERS = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]

_JD_CAP = 4000


@dataclass(frozen=True)
class ApplyProposal:
    approval_id: str
    job_id: str
    company: str
    title: str
    summary: str
    cover_letter: str
    cover_letter_storage_path: str
    resume: ResumeChoice
    filler_platform: str


@dataclass(frozen=True)
class ApplyResult:
    job_id: str
    company: str
    title: str
    resume_storage_path: str
    applied_at: str


def _digest_stored(storage: StorageProvider, path: str) -> str:
    """sha256 of the bytes actually on disk at `path`.

    Not a digest of the in-memory value that produced them: the payload
    must bind to what execute_apply will read back later, not to what
    propose happened to hold a moment before the write.
    """
    return hashlib.sha256(storage.read(path)).hexdigest()


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def propose_apply(
    runtime: AgentRuntime,
    job_id: str,
    *,
    model: str | None = None,
    action_label: str,
    summary: str | None = None,
    jd_text: str | None = None,
) -> ApplyProposal:
    """Draft a cover letter and record a pending approval for filling and submitting the application.

    Everything that can fail cheaply fails before the LLM is called: job/URL
    validation, resume selection, the policy check, filler detection, and
    (for LinkedIn) the board session check all happen first. Calling this
    again is how regeneration works — the new call supersedes the prior
    pending approval and overwrites the cover letter on disk.

    `summary` and `jd_text` are optional overrides for callers that need
    their own approval wording or their own view of the job description
    (the scheduled discover-and-apply command embeds a score and threshold
    in its summary, for instance) — when omitted, both default to what the
    interactive CLI has always used.
    """
    try:
        job = Job.load(runtime.storage, job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Job not found.") from exc

    if not job.url:
        raise EntityNotFound(
            "Job " + job_id + " has no URL. Run 'careeros job update " + job_id
            + "' to add one."
        )

    # Resume lookup before the policy check and before profile load, so a
    # missing resume fails fast and clear without touching either.
    resume_choice = select_resume(runtime.storage, job_id)
    if resume_choice is None:
        raise EntityNotFound("No resume found in resumes/versions/ — add one first.")

    policy_result = PolicyEngine(PolicyConfig.load(runtime.storage)).check_job(job)
    if policy_result.blocked:
        rule = policy_result.rule or "unknown"
        runtime.record_activity(runtime.new_event(
            "policy_blocked", action_label,
            "Blocked by policy (" + rule + "): " + job.company + " — " + job.title,
            status="failed", entity_type="job", entity_id=job_id,
        ))
        raise PolicyBlocked(rule)

    # Detect the filler before any LLM spend, and before the session check
    # below, since the session check only applies to one filler.
    filler = next((f for f in FILLERS if f.can_handle(job.url)), None)
    if filler is None:
        raise EntityNotFound("No filler available for this URL: " + job.url)

    # Only LinkedIn Easy Apply needs a session; Greenhouse, Lever, and the
    # generic fallback all work signed-out. Checked here, ahead of the cover
    # letter, rather than after it as apply_cmd historically did: a user who
    # is not signed in should not pay for an LLM call before being told to
    # sign in. Because the CLI's review loop re-calls propose_apply to
    # regenerate, this now runs on every regeneration too. That is accepted:
    # it also catches a session that expires during a long review, which a
    # single upfront check could never have caught, and each repeated check
    # is preceded by nothing expensive.
    if isinstance(filler, LinkedInFiller):
        try:
            authorized = check_board_sessions(["linkedin"]).get("linkedin")
        except BrowserProfileBusy as exc:
            raise BrowserUnavailable(str(exc), profile_busy=True) from exc
        if not authorized:
            raise BoardSessionRequired("linkedin")

    profile = Profile.load_or_empty(runtime.storage)
    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    effective_jd_text = jd_text if jd_text is not None else (job.description or "")[:_JD_CAP]
    cover_letter = generate_cover_letter(effective_jd_text, profile, skills, goals, model=model)
    if not cover_letter:
        raise DraftFailed("Cover letter generation failed.")

    cl_storage_path = "applications/" + job_id + "/cover_letter.txt"
    runtime.storage.atomic_write(cl_storage_path, cover_letter.encode())

    effective_summary = summary if summary is not None else (
        "About to fill the " + filler.platform + " application for "
        + job.company + " — " + job.title + ". Proceed?"
    )

    # job_url and profile_sha256 are the deviation from the spec text this
    # module implements: filler.fill navigates to job.url and populates form
    # fields from profile, so both are transmitted content, exactly like the
    # cover letter and resume, and the same rule applies — everything
    # execute_apply sends must have been written before the approval and be
    # digest- or value-verified against it, never re-derived at send time.
    # The previous phase shipped exactly this bug for outreach's subject
    # line and its final review caught it; binding job_url and profile_sha256
    # here is how apply avoids repeating it. Paths are workspace-relative,
    # never absolute, because the workspace itself is portable — an absolute
    # path recorded on one machine would be wrong resolved on another.
    approval = open_approval(
        runtime, ACTION, effective_summary,
        {
            "job_id": job_id,
            "job_url": job.url,
            "cover_letter_storage_path": cl_storage_path,
            "resume_storage_path": resume_choice.storage_path,
            "filler_platform": filler.platform,
            "cover_letter_sha256": _digest_stored(runtime.storage, cl_storage_path),
            "resume_sha256": _digest_stored(runtime.storage, resume_choice.storage_path),
            "profile_sha256": _digest_text(profile.model_dump_json()),
        },
        entity_type="job", entity_id=job_id,
        action_label=action_label,
    )

    return ApplyProposal(
        approval_id=approval.id, job_id=job_id, company=job.company, title=job.title,
        summary=effective_summary, cover_letter=cover_letter,
        cover_letter_storage_path=cl_storage_path, resume=resume_choice,
        filler_platform=filler.platform,
    )
