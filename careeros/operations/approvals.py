from __future__ import annotations

from pydantic import ValidationError

from careeros.core.ids import make_approval_id
from careeros.core.models import Approval
from careeros.operations._shared import now as _now
from careeros.operations.errors import ApprovalNotGranted, MalformedApproval
from careeros.runtime.base import AgentRuntime, ApprovalResult
from careeros.storage.interface import StorageProvider

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"
EXECUTED = "executed"
FAILED = "failed"
SUPERSEDED = "superseded"

_PREFIX = "approvals/"
_SUFFIX = ".json"


def list_by_state(storage: StorageProvider, state: str) -> list[Approval]:
    """Every approval currently in `state`, oldest first.

    A separate entry point rather than a `state` parameter bolted onto
    list_pending, deliberately: list_pending is simultaneously `outreach
    review`'s queue source *and* the scheduled proposer's dedup set
    (_entity_ids_with_pending_follow_up), so widening it would silently
    change both — the drainer would start offering approvals it cannot
    legally resolve, and the proposer would stop re-drafting relationships
    it is meant to re-draft.

    An unparseable record is skipped rather than raised on: one corrupt
    file must not hide every other record in the state being asked about.
    An unexpected error is deliberately *not* swallowed, though: catching
    bare Exception would also hide a genuine bug — a future field rename
    raising TypeError on every record would make this function report
    nothing while looking healthy.
    """
    matching: list[Approval] = []
    for path in storage.list(_PREFIX):
        if not path.endswith(_SUFFIX):
            continue
        approval_id = path[len(_PREFIX):-len(_SUFFIX)]
        try:
            approval = Approval.load(storage, approval_id)
        except (ValueError, FileNotFoundError, ValidationError):
            continue
        if approval.state == state:
            matching.append(approval)
    return sorted(matching, key=lambda a: a.created_at)


def list_pending(storage: StorageProvider) -> list[Approval]:
    """Every approval awaiting a decision, oldest first.

    How an out-of-process runtime rediscovers what it left open after losing
    its own context. Kept as its own named function even though it is now a
    one-liner over list_by_state: this is the queue every reviewer drains
    and the set every proposer dedups against, and those callers mean
    "awaiting a decision", not "in some state I passed in".
    """
    return list_by_state(storage, PENDING)


def has_executed_approval(storage: StorageProvider, action: str, entity_id: str) -> bool:
    """Is there an executed approval for this action against this entity?

    Iterates approvals/ the same way list_pending does, and reuses its same
    narrow exception handling: an unparseable record is skipped rather than
    raised on, since one corrupt file must not hide a real executed record,
    but an unexpected error is not swallowed, for the same reason list_pending
    does not swallow one.

    Exists for callers whose eligibility check needs to tell "outcome
    unknown" (an executed approval whose action never advanced the entity's
    own durable state) apart from "never attempted" — see
    discover_and_apply_cmd's job-skip predicate.
    """
    for path in storage.list(_PREFIX):
        if not path.endswith(_SUFFIX):
            continue
        approval_id = path[len(_PREFIX):-len(_SUFFIX)]
        try:
            approval = Approval.load(storage, approval_id)
        except (ValueError, FileNotFoundError, ValidationError):
            continue
        if approval.state == EXECUTED and approval.action == action and approval.entity_id == entity_id:
            return True
    return False


def require_state(storage: StorageProvider, approval_id: str, expected: str) -> Approval:
    """Load an approval, or raise if it is not in the state this step needs.

    The single guard behind every execute_* and decline_*. Because executing
    advances the record to a terminal state, this is what closes the window
    between a crash and a retry — a process that dies after mark_executed
    leaves the record executed or failed, never approved, so a retry cannot
    act on it a second time. A process that dies earlier, before
    mark_executed, leaves the record approved by design: the pre-execution
    checks (digest comparison, missing-recipient check) must be safe to
    re-run against a still-approved record. It is a read-then-compare with
    no compare-and-swap, though, so it does not by itself exclude two
    processes racing execute_* concurrently: both can read approved before
    either writes past it. See docs/agent-integration.md
    §10 for that residual window and the integrator's obligation not to run
    two executors against the same approval id at once.
    """
    approval = Approval.load(storage, approval_id)
    if approval.state != expected:
        raise ApprovalNotGranted(approval_id, approval.state)
    return approval


def payload_value(approval: Approval, key: str) -> str:
    value = approval.payload.get(key)
    if not value:
        raise MalformedApproval(approval.id, key)
    return value


def open_approval(
    runtime: AgentRuntime,
    action: str,
    summary: str,
    payload: dict[str, str],
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action_label: str,
) -> Approval:
    """Record a pending approval, superseding any prior open one.

    Superseding is what makes regeneration safe: re-proposing invalidates the
    older approval, so a stale approval ID cannot later execute against
    content that has since been overwritten.

    Both `pending` and `approved` count as open, and this is the part that
    was missing. Every execute_* refuses *before* calling mark_executed when
    the recipient has no address or a digest no longer matches — by design,
    so those refusals stay retryable — which leaves a durable `approved`
    record. list_pending does not report it, so no reviewer ever sees it
    again. Superseding only `pending` therefore left it `approved` forever
    while a fresh draft was queued alongside it, and if the redraft came
    back byte-identical its draft_sha256 still matched the stored draft:
    executing that stranded id sent the message a *second* time. The digest
    binding was the reason the `approved` case was believed inert, and an
    identical redraft is exactly the case the digest cannot catch. Because
    mark_executed always precedes the external action, `approved`-and-not-
    executed is precisely the set of pre-attempt refusals, so nothing that
    was actually attempted is reached by this.

    Terminal records are left alone: `declined`, `executed`, `failed` and
    `superseded` are the audit trail, and nothing can act on them anyway.
    """
    # Two scans rather than one, because list_by_state answers about one
    # state. The directory holds one small file per proposal ever made in
    # this workspace, and this runs once per propose.
    open_approvals = (
        list_by_state(runtime.storage, PENDING)
        + list_by_state(runtime.storage, APPROVED)
    )
    for existing in open_approvals:
        if existing.action != action or existing.entity_id != entity_id:
            continue
        supersede_approval(runtime, existing, action_label=action_label)

    approval = Approval(
        id=make_approval_id(action, entity_id),
        action=action,
        summary=summary,
        state=PENDING,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        created_at=_now(),
    )
    approval.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "approval_requested", action_label, summary,
        entity_type=entity_type, entity_id=entity_id,
    ))
    return approval


def supersede_approval(
    runtime: AgentRuntime,
    approval: Approval,
    *,
    action_label: str,
    cause: str | None = None,
) -> Approval:
    """Invalidate an open approval, so nothing can ever act on it.

    The one implementation of the `pending`/`approved` -> `superseded`
    transition. open_approval reaches it by proposing a replacement, which is
    the usual route; `careeros outreach close` reaches it with no replacement
    to propose, because the relationship is over. Both need exactly this
    write and exactly this event, and a second copy of a state transition in
    the CLI layer is how the two drift apart.

    `cause` is for the caller whose reason is not "a newer proposal replaced
    this one". It is appended to the summary and set on the event's
    structured `reason` field, so why a granted authorization was revoked is
    recoverable from the log rather than inferred from what happened to be
    written next to it.

    Takes the loaded Approval rather than an id: every caller has already
    read the record — open_approval from its two list_by_state scans — and
    the state this names in the log has to be the one that was read, not one
    re-fetched after the fact.
    """
    superseded = approval.model_copy(update={"state": SUPERSEDED})
    superseded.save(runtime.storage)
    # The state it was superseded *from* is named: the two are not equally
    # alarming to a reader of the log. A superseded `approved` means an
    # approval a human had already granted was invalidated, which is worth
    # being able to grep for.
    summary = "Superseded earlier " + approval.state + " approval " + approval.id
    if cause:
        summary = summary + " (" + cause + ")"
    runtime.record_activity(runtime.new_event(
        "approval_superseded", action_label, summary,
        entity_type=approval.entity_type, entity_id=approval.entity_id,
        reason=cause,
    ))
    return superseded


def resolve_approval(
    runtime: AgentRuntime,
    approval_id: str,
    result: ApprovalResult,
    *,
    action_label: str,
) -> Approval:
    """Record a decision against a pending approval.

    Only a pending approval can be decided, so a decision cannot be revised
    after the fact and cannot be applied twice.
    """
    approval = require_state(runtime.storage, approval_id, PENDING)
    approval = approval.model_copy(update={
        "state": APPROVED if result.approved else DECLINED,
        "decided_at": _now(),
        "decided_by": runtime.agent_runtime_name,
        "reason": result.reason,
    })
    approval.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "approval_granted" if result.approved else "approval_declined",
        action_label,
        ("Approved: " if result.approved else "Declined: ") + approval.summary,
        entity_type=approval.entity_type,
        entity_id=approval.entity_id,
        reason=result.reason,
    ))
    return approval


def mark_executed(runtime: AgentRuntime, approval_id: str) -> Approval:
    """Advance an approved approval to its terminal executed state.

    Logs nothing: the calling flow records its own domain event, which is the
    one that means something to a reader of the activity log.

    Guarded through require_state rather than a bare load: execute_* already
    loads the approval once (via its own require_state call) before doing the
    digest check and the model loads that sit between that gate and this
    write, so re-checking here costs nothing but a comparison already paid
    for by the load, and it narrows the accepted concurrency window from
    "digest plus several file reads" down to a single load-then-write gap.
    It also means this helper cannot be called on an approval that was never
    approved.
    """
    approval = require_state(runtime.storage, approval_id, APPROVED)
    approval = approval.model_copy(update={"state": EXECUTED, "executed_at": _now()})
    approval.save(runtime.storage)
    return approval


def mark_failed(runtime: AgentRuntime, approval_id: str, detail: str) -> Approval:
    approval = require_state(runtime.storage, approval_id, EXECUTED)
    approval = approval.model_copy(update={"state": FAILED, "detail": detail})
    approval.save(runtime.storage)
    return approval
