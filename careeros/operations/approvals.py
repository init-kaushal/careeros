from __future__ import annotations

from datetime import datetime, timezone

from careeros.core.ids import make_approval_id
from careeros.core.models import Approval
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_pending(storage: StorageProvider) -> list[Approval]:
    """Every approval awaiting a decision, oldest first.

    How an out-of-process runtime rediscovers what it left open after losing
    its own context. An unparseable record is skipped rather than raised on:
    one corrupt file must not hide every other pending decision.
    """
    pending: list[Approval] = []
    for path in storage.list(_PREFIX):
        if not path.endswith(_SUFFIX):
            continue
        approval_id = path[len(_PREFIX):-len(_SUFFIX)]
        try:
            approval = Approval.load(storage, approval_id)
        except Exception:
            continue
        if approval.state == PENDING:
            pending.append(approval)
    return sorted(pending, key=lambda a: a.created_at)


def require_state(storage: StorageProvider, approval_id: str, expected: str) -> Approval:
    """Load an approval, or raise if it is not in the state this step needs.

    The single guard behind every execute_* and decline_*. Because executing
    advances the record to a terminal state, this is what makes a second
    execution of the same approval impossible rather than merely unlikely.
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
    """Record a pending approval, superseding any prior pending one.

    Superseding is what makes regeneration safe: re-proposing invalidates the
    older pending approval, so a stale approval ID cannot later execute
    against content that has since been overwritten.
    """
    for existing in list_pending(runtime.storage):
        if existing.action != action or existing.entity_id != entity_id:
            continue
        existing.model_copy(update={"state": SUPERSEDED}).save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "approval_superseded", action_label,
            "Superseded earlier pending approval " + existing.id,
            entity_type=existing.entity_type, entity_id=existing.entity_id,
        ))

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
    """Advance an approval to its terminal executed state.

    Logs nothing: the calling flow records its own domain event, which is the
    one that means something to a reader of the activity log.
    """
    approval = Approval.load(runtime.storage, approval_id)
    approval = approval.model_copy(update={"state": EXECUTED, "executed_at": _now()})
    approval.save(runtime.storage)
    return approval


def mark_failed(runtime: AgentRuntime, approval_id: str, detail: str) -> Approval:
    approval = Approval.load(runtime.storage, approval_id)
    approval = approval.model_copy(update={"state": FAILED, "detail": detail})
    approval.save(runtime.storage)
    return approval
