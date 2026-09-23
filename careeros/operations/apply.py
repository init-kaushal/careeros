from __future__ import annotations

import hashlib
from dataclasses import dataclass

from careeros.browser.driver import BrowserProfileBusy, launch_browser
from careeros.browser.fillers.generic import GenericFiller
from careeros.browser.fillers.greenhouse import GreenhouseFiller
from careeros.browser.fillers.lever import LeverFiller
from careeros.browser.fillers.linkedin import LinkedInFiller
from careeros.browser.session import check_board_sessions
from careeros.core.models import Goals, Job, PolicyConfig, Profile, Skills
from careeros.core.policy_engine import PolicyEngine
from careeros.core.resume_select import ResumeChoice, select_resume
from careeros.operations._shared import digest_stored as _digest_stored
from careeros.operations._shared import digest_text as _digest_text
from careeros.operations._shared import now as _now
from careeros.operations.approvals import (
    APPROVED, mark_executed, mark_failed, open_approval, payload_value,
    require_state,
)
from careeros.operations.errors import (
    ArtifactChanged, BoardSessionRequired, BrowserUnavailable, DraftFailed,
    EntityNotFound, FillIncomplete, MalformedApproval, NoFillerAvailable,
    PolicyBlocked, ResumeNotFound, WrongApprovalAction,
)
from careeros.runtime.base import AgentRuntime
from careeros.skills.cover_letter import generate_cover_letter

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
    # True only on the teardown-anomaly path: the submission itself
    # succeeded and the stage advanced normally, but browser teardown then
    # failed and a durable apply_teardown_failed event was logged. Callers
    # should surface this as a warning, not silently print unqualified
    # success while that anomaly sits unread in the activity log.
    teardown_failed: bool = False


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
        raise ResumeNotFound()

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
        raise NoFillerAvailable(job.url)

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


def _mark_applied(
    runtime: AgentRuntime, job: Job, job_id: str, resume_storage_path: str,
    action_label: str, *, teardown_failed: bool = False,
) -> ApplyResult:
    """Advance the job to stage="applied" and log job_applied, then return the result.

    Shared by the ordinary success path and by a teardown failure that
    happens after filler.fill already returned True: in both cases the
    application already went out, so the stored job record and the
    activity log must say so identically either way. `teardown_failed` only
    ever comes in True from the latter caller, which also logs its own
    distinct apply_teardown_failed event — it is threaded through here
    purely so the returned ApplyResult carries that signal onward to the
    CLI callers.
    """
    applied_at = _now()
    job = job.model_copy(update={"stage": "applied", "applied_at": applied_at, "updated_at": applied_at})
    job.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "job_applied", action_label,
        "Applied to " + job.company + " — " + job.title
        + " with " + resume_storage_path,
        entity_type="job", entity_id=job_id,
    ))
    return ApplyResult(
        job_id=job_id, company=job.company, title=job.title,
        resume_storage_path=resume_storage_path, applied_at=applied_at,
        teardown_failed=teardown_failed,
    )


def _record_apply_failed(
    runtime: AgentRuntime, approval_id: str, job: Job, job_id: str,
    action_label: str, exc: BaseException,
) -> None:
    """Mark the approval failed and log the attempt, exception type name only.

    Never the exception's message: a Playwright or browser-profile-lock
    exception's str() can contain the browser profile's filesystem path, and
    both records this writes are durable — the activity log is
    append-only — so nothing landing here could ever be scrubbed later.
    Mutates and logs before the caller raises, so the audit trail always
    records that the attempt happened even though the caller never returns
    normally.
    """
    exc_name = type(exc).__name__
    mark_failed(runtime, approval_id, exc_name)
    runtime.record_activity(runtime.new_event(
        "apply_failed", action_label,
        "Application submission failed for " + job.company + " — " + job.title
        + ": " + exc_name,
        status="failed", entity_type="job", entity_id=job_id,
    ))


def execute_apply(
    runtime: AgentRuntime, approval_id: str, *, headless: bool, action_label: str,
) -> ApplyResult:
    """Fill and submit the application an approved approval authorized, and nothing else.

    Drafts nothing and calls no LLM: the cover letter, resume, and profile
    are read back from the workspace and each re-verified against the
    digest recorded when the approval was created, so the bytes reviewed
    are the bytes transmitted. job_url is likewise never re-derived from a
    freshly-reloaded Job: it was recorded on the payload at propose time
    (the same treatment outreach gives its subject line), so editing the
    job's URL between approval and execution cannot change what gets
    navigated to.

    approval.action is checked against ACTION before anything else is read
    off the payload, and before any state change: passing an outreach
    approval id here would otherwise be safe only by accident of the two
    actions' payload keys not colliding, which is exactly the mistake an
    opaque cross-process approval id invites.

    The approval is consumed before the browser is launched: the external
    action happens with the approval already marked executed. This ordering
    defeats process death — a crash between the two writes leaves the
    record executed, not approved, so a retry cannot act on it a second
    time — and narrows, though it does not eliminate, the window for a
    second, concurrently racing executor (see require_state). A digest
    mismatch or a missing job is checked before that consumption, so either
    one leaves the approval approved and retryable, exactly like outreach's
    treatment of ArtifactChanged and MissingRecipient: nothing has been
    attempted in those cases.

    A browser error must never advance the job's stage — except in the one
    case where the "error" is teardown failing after filler.fill already
    returned True. There, the application already went out; advancing the
    stage anyway (via the same path the ordinary success case uses) is what
    stops the unattended discover-and-apply command, which skips a job once
    its applied_at is set (equivalently, once _mark_applied has run — stage
    and applied_at always advance together here), from re-proposing and
    resubmitting the same application on its next run. That branch logs a
    distinct apply_teardown_failed event and returns the ApplyResult normally
    rather than raising: the caller's real question is "did the application go
    out", and it did, so this is an operational anomaly to record, not an
    application failure to raise.

    Every other browser-failure branch — Playwright missing, the browser
    profile locked, or any exception raised before filler.fill returns
    True — marks the approval failed rather than leaving it executed:
    mark_executed already ran, so the external action was attempted (even
    "attempted" as narrowly as "tried to launch and could not"), and
    executed would otherwise misrepresent that as a successful send to a
    reader of the activity log. failed is the truthful terminal state, the
    approval still cannot be retried either way (mark_executed already
    consumed it — an integrator re-proposes rather than retrying), and only
    the exception's type name is ever persisted, to the approval's detail
    and to the activity log: a browser exception's message can contain the
    browser profile's filesystem path, and both of those records are
    durable — the log is append-only — so nothing that landed there could
    later be scrubbed. str(exc) is used only to build the in-memory
    BrowserUnavailable raised back to the caller.
    """
    approval = require_state(runtime.storage, approval_id, APPROVED)
    if approval.action != ACTION:
        raise WrongApprovalAction(approval_id, ACTION, approval.action)
    job_id = payload_value(approval, "job_id")
    job_url = payload_value(approval, "job_url")
    cl_storage_path = payload_value(approval, "cover_letter_storage_path")
    resume_storage_path = payload_value(approval, "resume_storage_path")
    filler_platform = payload_value(approval, "filler_platform")
    expected_cl_digest = payload_value(approval, "cover_letter_sha256")
    expected_resume_digest = payload_value(approval, "resume_sha256")
    expected_profile_digest = payload_value(approval, "profile_sha256")

    filler = next((f for f in FILLERS if f.platform == filler_platform), None)
    if filler is None:
        raise MalformedApproval(approval.id, "filler_platform")

    # Read once: the bytes digested here are the bytes decoded into
    # cover_letter_text below, so what gets hashed and what gets uploaded
    # are provably the same read, not two reads that could observe two
    # different states of the file.
    cover_letter_bytes = runtime.storage.read(cl_storage_path)
    if hashlib.sha256(cover_letter_bytes).hexdigest() != expected_cl_digest:
        raise ArtifactChanged(cl_storage_path)
    if _digest_stored(runtime.storage, resume_storage_path) != expected_resume_digest:
        raise ArtifactChanged(resume_storage_path)

    profile = Profile.load_or_empty(runtime.storage)
    if _digest_text(profile.model_dump_json()) != expected_profile_digest:
        raise ArtifactChanged("profile/profile.json")

    try:
        job = Job.load(runtime.storage, job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Job " + job_id + " not found.") from exc
    # job_url is bound content, read back from the approval rather than
    # re-derived from the reload above — see the docstring. This is kept in
    # a separate variable, never assigned back onto `job`: `job` itself
    # stays the untouched reload, because it is `job` that later gets
    # mutated to stage="applied" and saved, and a post-approval edit to
    # job.url must survive that save rather than being silently reverted to
    # the approved value.
    job_for_fill = job.model_copy(update={"url": job_url})

    cover_letter_text = cover_letter_bytes.decode()
    cover_letter_path = runtime.storage.resolve(cl_storage_path)
    resume_path = runtime.storage.resolve(resume_storage_path)

    # Consume the approval before attempting the external action.
    mark_executed(runtime, approval_id)

    # None, not False, until filler.fill actually returns: the except
    # Exception branch below distinguishes "the submission itself
    # happened, only teardown afterward failed" (success is True) from
    # "nothing conclusive happened" (success is still None) — a plain
    # False initial value would be indistinguishable from an unattempted
    # fill and could never signal the former.
    success = None
    try:
        with launch_browser(headless=headless) as (_, page):
            success = filler.fill(
                page, job_for_fill, profile, cover_letter_text, cover_letter_path, resume_path
            )
    except ImportError as exc:
        _record_apply_failed(runtime, approval_id, job, job_id, action_label, exc)
        raise BrowserUnavailable(
            "Playwright not installed. Run: pip install playwright "
            "&& playwright install chrome"
        ) from exc
    except BrowserProfileBusy as exc:
        _record_apply_failed(runtime, approval_id, job, job_id, action_label, exc)
        raise BrowserUnavailable(str(exc), profile_busy=True) from exc
    except Exception as exc:
        if success is True:
            # filler.fill already returned True — the application went out
            # before this exception fired, so it came from context teardown
            # (e.g. context.close()), not from the submission itself. That
            # is an operational anomaly, not an application failure: the
            # caller's real question is "did the application go out", and
            # it did. Recording it as a distinct failed event rather than
            # raising is what stops the unattended discover-and-apply
            # command from re-proposing and resubmitting the same
            # application on its next run, since that command skips a job
            # once its applied_at is set.
            result = _mark_applied(
                runtime, job, job_id, resume_storage_path, action_label,
                teardown_failed=True,
            )
            runtime.record_activity(runtime.new_event(
                "apply_teardown_failed", action_label,
                "Browser teardown failed after a successful submission for "
                + job.company + " — " + job.title + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            return result
        _record_apply_failed(runtime, approval_id, job, job_id, action_label, exc)
        raise BrowserUnavailable(str(exc)) from exc

    if not success:
        mark_failed(runtime, approval_id, "FillIncomplete")
        runtime.record_activity(runtime.new_event(
            "apply_incomplete", action_label,
            "Form fill incomplete for " + job.company + " — " + job.title,
            status="failed", entity_type="job", entity_id=job_id,
        ))
        raise FillIncomplete(
            "Form fill incomplete for " + job.company + " — " + job.title
            + ". Stage not updated."
        )

    return _mark_applied(runtime, job, job_id, resume_storage_path, action_label)
