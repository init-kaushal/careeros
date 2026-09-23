from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from careeros.core.models import (
    CadencePolicy, Company, Goals, Job, OutreachMessage, Person, PolicyConfig, Profile,
)
from careeros.core.policy_engine import PolicyEngine
from careeros.operations._shared import digest_text as draft_digest
from careeros.operations.approvals import open_approval
from careeros.operations.errors import (
    CadenceExhausted, DraftFailed, EntityNotFound, NotDueForFollowUp, PolicyBlocked,
    RelationshipClosed,
)
from careeros.operations.outreach import make_message_id
from careeros.operations.outreach import subject_for as _outreach_subject_for
from careeros.runtime.base import AgentRuntime
from careeros.skills.follow_up_draft import generate_follow_up_message

ACTION = "send_follow_up"

# referral_state values that mean the relationship is over, not just paused.
_TERMINAL_REFERRAL_STATES = {"referral_confirmed", "closed"}


@dataclass(frozen=True)
class FollowUpProposal:
    approval_id: str
    message_id: str
    summary: str
    draft_text: str
    recipient_name: str
    recipient_email: str | None
    subject: str
    touch_number: int
    days_since_last_touch: int


def subject_for(job: Job) -> str:
    return "Re: " + _outreach_subject_for(job)


def propose_follow_up(
    runtime: AgentRuntime,
    job_id: str,
    person_id: str,
    *,
    model: str | None = None,
    action_label: str,
) -> FollowUpProposal:
    """Draft a cadence follow-up and record a pending approval for sending it.

    Only proposes against a relationship that already has an OutreachMessage
    (propose_outreach_send owns the first message to a person). Every refusal
    below — terminal relationship, exhausted cadence, not yet due, and the
    policy block — is checked, in that cheapest-first order, before the LLM
    is ever called or anything is written: a refusal must leave the workspace
    exactly as it found it, so a scheduled run that skips a relationship
    leaves nothing behind.
    """
    message_id = make_message_id(job_id, person_id)
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
        job = Job.load(runtime.storage, job_id)
        person = Person.load(runtime.storage, person_id)
        company = Company.load(runtime.storage, person.company_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound(
            "Outreach message, job, person, or company not found."
        ) from exc

    # Let FileNotFoundError propagate: no cadence config means no cadence, on
    # purpose (see CadencePolicy.load) — a follow-up must never start on a
    # schedule the user never chose.
    policy = CadencePolicy.load(runtime.storage)

    if message.referral_state in _TERMINAL_REFERRAL_STATES or message.closed_reason:
        reason = message.closed_reason or message.referral_state
        raise RelationshipClosed(message_id, reason)

    # last_touched_at and touch_count are new fields that default to
    # None/0 and are only populated going forward, by execute_outreach_send.
    # A relationship whose initial message was sent before this phase has
    # sent_at set but last_touched_at still None — reading these fields raw
    # would treat that as "never touched" and permanently exclude it from
    # follow-up. Falling back to sent_at, and counting a real send as one
    # touch, makes a legacy sent message due on the same schedule as one
    # sent after this phase shipped.
    last_touch = message.last_touched_at or message.sent_at
    effective_touch_count = message.touch_count or (1 if message.sent_at else 0)

    if effective_touch_count >= policy.max_touches:
        raise CadenceExhausted(message_id, effective_touch_count, policy.max_touches)

    if last_touch is None:
        # Never sent at all, so there is no touch to follow up on yet.
        # propose_outreach_send owns the first message — modeled as "not
        # due" rather than a distinct error, since to a caller both mean
        # exactly the same thing: don't act yet.
        raise NotDueForFollowUp(message_id, 0, policy.days_between_touches)

    days_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_touch)).days
    if days_since < policy.days_between_touches:
        raise NotDueForFollowUp(message_id, days_since, policy.days_between_touches)

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

    touch_number = effective_touch_count + 1
    draft_text = generate_follow_up_message(
        person, job, company, profile, goals, message.draft_text, touch_number,
        model=model,
    )
    if not draft_text:
        raise DraftFailed("Follow-up message generation failed.")

    # Only draft_text changes here: model_copy carries every other field —
    # sent_at, last_touched_at, touch_count, referral_state, closed_reason —
    # forward by construction, so nothing has to be remembered and re-listed
    # by hand. execute_follow_up owns advancing the touch fields.
    message.model_copy(update={"draft_text": draft_text}).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "follow_up_drafted", action_label,
        "Drafted follow-up #" + str(touch_number) + " to " + person.name
        + " re: " + job.company + " — " + job.title,
        entity_type="outreach_message", entity_id=message_id,
    ))

    summary = (
        "Send follow-up #" + str(touch_number) + " to " + person.name + " re: "
        + job.company + " — " + job.title + "?"
    )
    subject = subject_for(job)

    approval = open_approval(
        runtime, ACTION, summary,
        {
            "message_id": message_id,
            "job_id": job_id,
            "person_id": person_id,
            "draft_sha256": draft_digest(draft_text),
            "touch_number": str(touch_number),
        },
        entity_type="outreach_message", entity_id=message_id,
        action_label=action_label,
    )

    return FollowUpProposal(
        approval_id=approval.id, message_id=message_id, summary=summary,
        draft_text=draft_text, recipient_name=person.name,
        recipient_email=person.email, subject=subject,
        touch_number=touch_number, days_since_last_touch=days_since,
    )
