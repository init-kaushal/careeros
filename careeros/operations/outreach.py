from __future__ import annotations

from dataclasses import dataclass

from careeros.core.ids import slugify
from careeros.core.models import (
    Company, Goals, Job, OutreachMessage, Person, PolicyConfig, Profile,
)
from careeros.core.policy_engine import PolicyEngine
from careeros.mailer import send_email
from careeros.operations._shared import digest_text as draft_digest
from careeros.operations._shared import now as _now
from careeros.operations.approvals import (
    APPROVED, DECLINED, mark_executed, mark_failed, open_approval, payload_value,
    require_state,
)
from careeros.operations.errors import (
    ArtifactChanged, DraftFailed, EntityNotFound, MissingRecipient, PolicyBlocked,
    SendFailed, WrongApprovalAction,
)
from careeros.runtime.base import AgentRuntime
from careeros.skills.outreach_draft import generate_outreach_message

ACTION = "send_outreach"


@dataclass(frozen=True)
class OutreachProposal:
    approval_id: str
    message_id: str
    summary: str
    draft_text: str
    recipient_name: str
    recipient_email: str | None
    subject: str
    already_sent_at: str | None


@dataclass(frozen=True)
class OutreachResult:
    message_id: str
    recipient_name: str
    sent_at: str


def make_message_id(job_id: str, person_id: str) -> str:
    # job/person arrive from CLI arguments or an agent call; slugify before
    # using them as path segments so an arbitrary or malformed value never
    # reaches storage._resolve() as a raw path component — which would
    # otherwise either write outside outreach/ for a value like "../foo", or
    # surface as an unhandled ValueError from the workspace-root check.
    return slugify(job_id) + "__" + slugify(person_id)


def subject_for(job: Job) -> str:
    return "Regarding " + job.title + " at " + job.company


def propose_outreach_send(
    runtime: AgentRuntime,
    job_id: str,
    person_id: str,
    *,
    model: str | None = None,
    action_label: str,
) -> OutreachProposal:
    """Draft an outreach email and record a pending approval for sending it.

    Everything that can fail cheaply fails before the LLM is called. Calling
    this again is how regeneration works: the new call supersedes the prior
    pending approval and overwrites the draft.
    """
    try:
        job = Job.load(runtime.storage, job_id)
        person = Person.load(runtime.storage, person_id)
        company = Company.load(runtime.storage, person.company_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Job, person, or company not found.") from exc

    policy_result = PolicyEngine(PolicyConfig.load(runtime.storage)).check_job(job)
    if policy_result.blocked:
        rule = policy_result.rule or "unknown"
        runtime.record_activity(runtime.new_event(
            "policy_blocked", action_label,
            "Blocked by policy (" + rule + "): " + job.company + " — " + job.title,
            status="failed", entity_type="job", entity_id=job.id,
        ))
        raise PolicyBlocked(rule)

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    draft_text = generate_outreach_message(person, job, company, profile, goals, model=model)
    if not draft_text:
        raise DraftFailed("Outreach message generation failed.")

    message_id = make_message_id(job_id, person_id)
    referral_state = "research"
    already_sent_at: str | None = None
    last_touched_at: str | None = None
    touch_count = 0
    closed_reason: str | None = None
    try:
        existing = OutreachMessage.load(runtime.storage, message_id)
        referral_state = existing.referral_state
        # Keyed off sent_at, not send_state: this propose is about to rewrite
        # the message to "drafted", and send_state would then no longer
        # remember that a real send happened. sent_at is only ever set by an
        # actual send, so it is the durable fact — and it is carried forward
        # below so the warning survives regeneration.
        already_sent_at = existing.sent_at
        # Cadence state must survive regeneration too, for the same reason:
        # a rewritten draft is not a new relationship, and resetting the
        # touch count or last-touched timestamp here would let a follow-up
        # regeneration silently restart a cadence the user is already in.
        last_touched_at = existing.last_touched_at
        touch_count = existing.touch_count
        closed_reason = existing.closed_reason
    except (FileNotFoundError, ValueError):
        pass

    OutreachMessage(
        id=message_id, job_id=job_id, person_id=person_id, draft_text=draft_text,
        send_state="drafted", referral_state=referral_state,
        created_at=_now(), sent_at=already_sent_at,
        last_touched_at=last_touched_at, touch_count=touch_count,
        closed_reason=closed_reason,
    ).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_drafted", action_label,
        "Drafted outreach to " + person.name + " re: " + job.company + " — " + job.title,
        entity_type="outreach_message", entity_id=message_id,
    ))

    summary = (
        "Send outreach email to " + person.name + " re: "
        + job.company + " — " + job.title + "?"
    )
    if already_sent_at:
        summary = (
            "Already sent to " + person.name + " on " + already_sent_at
            + " — send ANOTHER outreach email re: " + job.company + " — " + job.title + "?"
        )

    subject = subject_for(job)
    approval = open_approval(
        runtime, ACTION, summary,
        {
            "message_id": message_id,
            "job_id": job_id,
            "person_id": person_id,
            "draft_sha256": draft_digest(draft_text),
            "subject": subject,
        },
        entity_type="outreach_message", entity_id=message_id,
        action_label=action_label,
    )

    return OutreachProposal(
        approval_id=approval.id, message_id=message_id, summary=summary,
        draft_text=draft_text, recipient_name=person.name,
        recipient_email=person.email, subject=subject,
        already_sent_at=already_sent_at,
    )


def _load_message_and_person(
    runtime: AgentRuntime, message_id: str, person_id: str,
) -> tuple[OutreachMessage, Person]:
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
        person = Person.load(runtime.storage, person_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Outreach message or person not found.") from exc
    return message, person


def execute_outreach_send(
    runtime: AgentRuntime, approval_id: str, *, action_label: str,
) -> OutreachResult:
    """Send the email an approved approval authorized, and nothing else.

    Drafts nothing and calls no LLM: the text that sends is read back from the
    stored OutreachMessage and checked against the digest recorded when the
    approval was created, so the bytes reviewed are the bytes transmitted. The
    subject line is likewise never re-derived from the Job: it was recorded on
    the payload at propose time, so editing the job's title or company between
    approval and execution cannot change what goes out.

    The approval is consumed before the send: the external action happens with
    the approval already marked executed. This ordering defeats process death —
    a crash between the two writes leaves the record executed, not approved, so
    a retry cannot act on it a second time — and narrows, though it does not
    eliminate, the window for a second, concurrently racing executor (see
    require_state).

    approval.action is checked against ACTION before anything else is read
    off the payload, and before any state change: passing an apply approval
    id here would otherwise be safe only by accident of the two actions'
    payload keys not colliding, which is exactly the mistake an opaque
    cross-process approval id invites.
    """
    approval = require_state(runtime.storage, approval_id, APPROVED)
    if approval.action != ACTION:
        raise WrongApprovalAction(approval_id, ACTION, approval.action)
    message_id = payload_value(approval, "message_id")
    person_id = payload_value(approval, "person_id")
    expected_digest = payload_value(approval, "draft_sha256")
    subject = payload_value(approval, "subject")

    message, person = _load_message_and_person(runtime, message_id, person_id)

    if draft_digest(message.draft_text) != expected_digest:
        raise ArtifactChanged("outreach/" + message_id + ".json")

    if not person.email:
        # Nothing has been attempted, so the approval stays approved: adding
        # the address and retrying must still work without re-approving.
        raise MissingRecipient(person_id, person.name)

    # Consume the approval before attempting the external action.
    mark_executed(runtime, approval_id)

    try:
        send_email(person.email, subject, message.draft_text)
    except Exception as exc:
        message.model_copy(update={"send_state": "failed"}).save(runtime.storage)
        mark_failed(runtime, approval_id, type(exc).__name__)
        runtime.record_activity(runtime.new_event(
            "outreach_send_failed", action_label,
            "Send failed for outreach to " + person.name + ": "
            + type(exc).__name__,
            status="failed", entity_type="outreach_message", entity_id=message_id,
        ))
        raise SendFailed(str(exc)) from exc

    sent_at = _now()
    message.model_copy(update={
        "send_state": "sent", "sent_at": sent_at,
        "last_touched_at": sent_at, "touch_count": message.touch_count + 1,
    }).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_sent", action_label, "Sent outreach to " + person.name,
        entity_type="outreach_message", entity_id=message_id,
    ))
    return OutreachResult(
        message_id=message_id, recipient_name=person.name, sent_at=sent_at
    )


def decline_outreach_send(
    runtime: AgentRuntime, approval_id: str, *, action_label: str,
) -> None:
    """Record that a declined approval's message will not be sent.

    Lives here rather than inside resolve_approval because approvals.py is
    deliberately action-agnostic — it knows approval states and nothing about
    outreach messages. Callers branch on the decision they already hold.

    Loads only the message, not the person: this is pure bookkeeping, and a
    missing people/<id>.json must not turn "record that the user said no"
    into an error. The person's name is used in the activity summary when
    the record is present; the raw person id is used otherwise, so a decline
    can always be recorded.
    """
    approval = require_state(runtime.storage, approval_id, DECLINED)
    message_id = payload_value(approval, "message_id")
    person_id = payload_value(approval, "person_id")
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Outreach message not found.") from exc

    try:
        recipient = Person.load(runtime.storage, person_id).name
    except (FileNotFoundError, ValueError):
        recipient = person_id

    message.model_copy(update={"send_state": "declined"}).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_send_declined", action_label,
        "Send declined for outreach to " + recipient,
        entity_type="outreach_message", entity_id=message_id,
    ))
