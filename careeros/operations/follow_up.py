from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from careeros.core.models import (
    CadencePolicy, Company, Goals, Job, OutreachMessage, Person, PolicyConfig, Profile,
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
    ArtifactChanged, CadenceExhausted, DraftFailed, EntityNotFound,
    MalformedTouchTimestamp, MissingRecipient, NotDueForFollowUp, PolicyBlocked,
    RelationshipClosed, SendFailed, WrongApprovalAction,
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


@dataclass(frozen=True)
class CadenceStatus:
    """What check_follow_up_due established on the way to saying "due".

    Returned rather than recomputed: propose_follow_up needs days_since for
    the FollowUpProposal it hands back and the touch count for the next
    touch number, and a second `datetime.now()` for the same message is how
    the CLI filter and the operation drifted apart in the first place.
    """

    days_since: int
    effective_touch_count: int


@dataclass(frozen=True)
class FollowUpResult:
    message_id: str
    recipient_name: str
    sent_at: str
    touch_count: int


def subject_for(job: Job) -> str:
    return "Re: " + _outreach_subject_for(job)


def _require_live_relationship(message: OutreachMessage) -> None:
    """Raise unless `message`'s relationship is still open for a follow-up.

    The one copy of the terminal-relationship rule, shared by the propose
    path (check_follow_up_due, which calls it first) and the send path
    (execute_follow_up). It used to live only on the propose path, and that
    was a hole rather than an omission: an approval minted while the
    relationship was live stays `approved` with a matching digest across a
    close, and every other check execute_follow_up makes — state, action,
    digest, recipient address — still passes, so the send went out to a
    person the user had declared themselves finished with.

    `careeros outreach close` supersedes the approvals it can see, which
    keeps the workspace tidy, but it cannot be the guarantee: it only runs on
    the close path, and an approval stranded by any other route — minted by
    an agent before an out-of-band close, or held by an integrator who kept
    the id — is stopped only here, at the point of action.
    """
    if message.referral_state in _TERMINAL_REFERRAL_STATES or message.closed_reason:
        raise RelationshipClosed(
            message.id, message.closed_reason or message.referral_state,
        )


def check_follow_up_due(
    message: OutreachMessage, policy: CadencePolicy, now: datetime,
) -> CadenceStatus:
    """Raise unless `message` is due for a follow-up under `policy` at `now`.

    The single source of truth for the rule that decides whether this
    project spends an LLM call and interrupts the user for a decision.
    propose_follow_up calls it before drafting; the scheduled command calls
    it to filter its enumeration. It used to exist twice — inlined here as
    early raises and mirrored as a bool in the CLI — which was two sources
    of truth for one rule.

    It raises rather than returning a bool on purpose. Each refusal carries
    structured detail a caller needs to word its own message
    (RelationshipClosed.reason, CadenceExhausted.touch_count,
    NotDueForFollowUp.days_since), and the agent-integration contract
    documents all three; collapsing them into a bool would throw that away.
    A caller that only wants a yes/no catches them, which is cheap — the
    reverse is not recoverable.

    `now` is a parameter, not a datetime.now() call inside, so a caller
    checking many relationships can hold one instant for the whole batch.
    Two independent now() calls could straddle a midnight boundary and have
    the filter and the operation disagree by a day about the same record.
    """
    _require_live_relationship(message)

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
        raise CadenceExhausted(message.id, effective_touch_count, policy.max_touches)

    if last_touch is None:
        # Never sent at all, so there is no touch to follow up on yet.
        # propose_outreach_send owns the first message — modeled as "not
        # due" rather than a distinct error, since to a caller both mean
        # exactly the same thing: don't act yet.
        raise NotDueForFollowUp(message.id, 0, policy.days_between_touches)

    # Parsed only now that a comparison is actually needed, which keeps a
    # closed or exhausted relationship with a garbage timestamp a plain
    # skip rather than an error. Both failure modes are converted into one
    # structured refusal so no raw ValueError/TypeError escapes this layer:
    # fromisoformat rejects "not-a-date" outright, and accepts an
    # offset-naive value like "2026-01-01T10:00:00" that then cannot be
    # subtracted from an aware `now` at all.
    try:
        parsed_last_touch = datetime.fromisoformat(last_touch)
    except (TypeError, ValueError) as exc:
        raise MalformedTouchTimestamp(message.id, last_touch) from exc
    if parsed_last_touch.tzinfo is None:
        raise MalformedTouchTimestamp(message.id, last_touch)

    days_since = (now - parsed_last_touch).days
    if days_since < policy.days_between_touches:
        raise NotDueForFollowUp(message.id, days_since, policy.days_between_touches)

    return CadenceStatus(
        days_since=days_since, effective_touch_count=effective_touch_count,
    )


def propose_follow_up(
    runtime: AgentRuntime,
    job_id: str,
    person_id: str,
    *,
    model: str | None = None,
    action_label: str,
    now: datetime | None = None,
) -> FollowUpProposal:
    """Draft a cadence follow-up and record a pending approval for sending it.

    Only proposes against a relationship that already has an OutreachMessage
    (propose_outreach_send owns the first message to a person). Every refusal
    below — terminal relationship, exhausted cadence, not yet due, an
    unusable last-touch timestamp, and the policy block — is checked, in
    that cheapest-first order, before the LLM is ever called or anything is
    written: a refusal must leave the workspace exactly as it found it, so a
    scheduled run that skips a relationship leaves nothing behind.

    The cadence half of that lives in check_follow_up_due, shared with the
    scheduled command's enumeration filter. It is called *after* the entity
    loads, deliberately: checking due-ness first would turn a not-due orphan
    record from an EntityNotFound into a silent skip.

    `now` defaults to this instant. A caller proposing across a batch passes
    its own single value so its filter and this call cannot disagree by a
    day about the same record.
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

    status = check_follow_up_due(message, policy, now or datetime.now(timezone.utc))
    days_since = status.days_since

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

    touch_number = status.effective_touch_count + 1
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
            # Bound here, not recomputed at execute time: an out-of-band edit
            # to job.title/job.company between approval and execution must
            # not change the subject of a message the user already approved.
            # execute_outreach_send does the same, and applies the same
            # reasoning (Phase 12a's final review).
            "subject": subject,
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


def _load_message_and_person(
    runtime: AgentRuntime, message_id: str, person_id: str,
) -> tuple[OutreachMessage, Person]:
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
        person = Person.load(runtime.storage, person_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Outreach message or person not found.") from exc
    return message, person


def execute_follow_up(
    runtime: AgentRuntime, approval_id: str, *, action_label: str,
) -> FollowUpResult:
    """Send the follow-up an approved approval authorized, and nothing else.

    Drafts nothing and calls no LLM: the text that sends is read back from
    the stored OutreachMessage and checked against the digest recorded when
    the approval was created, so the bytes reviewed are the bytes
    transmitted. The subject line is likewise never re-derived from the Job:
    it was recorded on the payload at propose time (mirroring
    execute_outreach_send) and read back verbatim here, so editing the job's
    title or company between approval and execution cannot change what goes
    out. The Job itself is never loaded here as a result — subject_for(job)
    was its only consumer, and dropping the load means a missing or corrupt
    job file no longer blocks sending an already-approved follow-up.

    approval.action is checked against ACTION before anything else is read
    off the payload, and before any state change: passing an approval minted
    by a different flow here would otherwise be safe only by accident of the
    two actions' payload keys not colliding.

    The approval is consumed (mark_executed) before the send is attempted.
    This ordering is a Critical finding inherited from an earlier phase's
    final review of execute_outreach_send: consuming the approval after the
    send leaves it approved — and its digest matching — for the whole
    duration of the SMTP conversation, so a crash, a Ctrl-C, or a second
    concurrent process could send twice. Consuming first means a crash
    mid-send leaves a stale executed record with an unsent message, which is
    recoverable; a duplicate email to a person you want a referral from is
    not. The closed-relationship, digest and recipient checks all precede
    that consumption, so those three refusals still leave the approval
    approved and retryable — nothing was attempted yet when they fire.
    """
    approval = require_state(runtime.storage, approval_id, APPROVED)
    if approval.action != ACTION:
        raise WrongApprovalAction(approval_id, ACTION, approval.action)
    message_id = payload_value(approval, "message_id")
    person_id = payload_value(approval, "person_id")
    expected_digest = payload_value(approval, "draft_sha256")
    subject = payload_value(approval, "subject")

    message, person = _load_message_and_person(runtime, message_id, person_id)

    # Checked here and not only at propose time. This is the refusal that
    # actually closes the hole: an approval granted before the relationship
    # was closed passes every other gate above, so without this the user's
    # explicit off switch did not bind the one path that sends mail. First of
    # the post-load checks because it outranks them — a closed relationship
    # is not a draft to re-digest or an address to go and find — and, like
    # them, it fires before mark_executed, so a relationship closed by
    # mistake and re-opened leaves the approval still approved.
    _require_live_relationship(message)

    if draft_digest(message.draft_text) != expected_digest:
        raise ArtifactChanged("outreach/" + message_id + ".json")

    if not person.email:
        # Nothing has been attempted, so the approval stays approved: adding
        # the address and retrying must still work without re-approving.
        raise MissingRecipient(person_id, person.name)

    # Consume the approval before attempting the external action — see the
    # ordering note in the docstring above.
    mark_executed(runtime, approval_id)

    try:
        send_email(person.email, subject, message.draft_text)
    except Exception as exc:
        message.model_copy(update={"send_state": "failed"}).save(runtime.storage)
        mark_failed(runtime, approval_id, type(exc).__name__)
        runtime.record_activity(runtime.new_event(
            "follow_up_send_failed", action_label,
            "Send failed for follow-up to " + person.name + ": "
            + type(exc).__name__,
            status="failed", entity_type="outreach_message", entity_id=message_id,
        ))
        raise SendFailed(str(exc)) from exc

    sent_at = _now()
    new_touch_count = message.touch_count + 1
    message.model_copy(update={
        "send_state": "sent", "sent_at": sent_at,
        "last_touched_at": sent_at, "touch_count": new_touch_count,
    }).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "follow_up_sent", action_label, "Sent follow-up to " + person.name,
        entity_type="outreach_message", entity_id=message_id,
    ))
    return FollowUpResult(
        message_id=message_id, recipient_name=person.name, sent_at=sent_at,
        touch_count=new_touch_count,
    )


def decline_follow_up(
    runtime: AgentRuntime, approval_id: str, *, action_label: str,
) -> None:
    """Record that a declined approval's follow-up will not be sent.

    Loads only the message, not the person: this is pure bookkeeping, and a
    missing people/<id>.json must not turn "record that the user said no"
    into an error. The person's name is used in the activity summary when
    the record is present; the raw person id is used otherwise, so a decline
    can always be recorded.

    approval.action is checked the same way execute_follow_up checks it, and
    for a sharper reason: this decline writes last_touched_at, which is a
    *scheduling* field. The send_outreach and send_follow_up payloads share
    message_id and person_id, so a transposed id from the outreach flow would
    otherwise be accepted here and would silently defer the wrong
    relationship's cadence by a full period — and log the decline against it.
    """
    approval = require_state(runtime.storage, approval_id, DECLINED)
    if approval.action != ACTION:
        raise WrongApprovalAction(approval_id, ACTION, approval.action)
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

    # Advance last_touched_at even though nothing was sent. Without this the
    # relationship stays past-due and the very next scheduled run would
    # re-propose the exact follow-up the user just declined. Advancing it
    # defers by one full cadence period instead — a fresh choice to defer,
    # not to stop. touch_count is deliberately left alone: a decline is not
    # a touch, so a user who declines every time never exhausts max_touches
    # on that basis. `careeros outreach close` is the explicit off switch.
    message.model_copy(update={
        "send_state": "declined", "last_touched_at": _now(),
    }).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "follow_up_send_declined", action_label,
        "Send declined for follow-up to " + recipient,
        entity_type="outreach_message", entity_id=message_id,
    ))
