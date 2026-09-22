from __future__ import annotations

from careeros.runtime.base import ActionProposal, ApprovalResult

DEFERRED_REASON = "deferred to out-of-process approval"


def queue_only(proposal: ActionProposal) -> ApprovalResult:
    """The approval callback for a runtime that decides out of process.

    A shell-driven runtime records its own pending Approval via open_approval,
    exits, and takes the human's decision through resolve_approval in a later
    process — so this callback is never on the decision path. It exists so
    that any *other* request_approval call such a runtime encounters gets a
    safe answer rather than an accidental yes, and so that every integrator
    does not have to invent the same stub.

    Deny-by-default is deliberate: the Phase 5 constraint is that a missing
    or unwired approval path must never silently auto-approve.
    """
    return ApprovalResult(approved=False, reason=DEFERRED_REASON)
