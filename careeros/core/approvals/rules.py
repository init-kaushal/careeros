"""Constants, event types, refusal codes and the transition table for approvals (spec sections 4 to 6). Pure data."""

from __future__ import annotations

APPROVAL_TTL_HOURS = 24
TTL_SECONDS = APPROVAL_TTL_HOURS * 3600
PENDING_STALE_DAYS = 7
SCHEMA = 1

# --- kinds and the entity each one targets
LINKEDIN_INVITE = "linkedin_invite"
LINKEDIN_MESSAGE = "linkedin_message"
APPLICATION_SUBMIT = "application_submit"
KINDS = (LINKEDIN_INVITE, LINKEDIN_MESSAGE, APPLICATION_SUBMIT)
TARGET_ID_KIND = {LINKEDIN_INVITE: "person", LINKEDIN_MESSAGE: "person", APPLICATION_SUBMIT: "job"}   # key of ids.PREFIXES

# --- states (raw states are everything but `expired`, which is computed from the clock)
PENDING, APPROVED, IN_FLIGHT, CONSUMED = "pending", "approved", "in_flight", "consumed"
DECLINED, EXPIRED, SUPERSEDED, VOIDED = "declined", "expired", "superseded", "voided"
STATES = (PENDING, APPROVED, IN_FLIGHT, CONSUMED, DECLINED, EXPIRED, SUPERSEDED, VOIDED)
UNRESOLVED_STATES = (PENDING, APPROVED, IN_FLIGHT)

# --- ledger events
EV_PROPOSED = "approval.proposed"
EV_APPROVED = "approval.approved"
EV_DECLINED = "approval.declined"
EV_SUPERSEDED = "approval.superseded"
EV_STARTED = "approval.started"
EV_CONSUMED = "approval.consumed"
EV_RELEASED = "approval.released"
EV_VOIDED = "approval.voided"
EV_REJECTED = "approval.rejected"
EVENT_PREFIX = "approval."
STATE_EVENTS = (EV_PROPOSED, EV_APPROVED, EV_DECLINED, EV_SUPERSEDED, EV_STARTED, EV_CONSUMED, EV_RELEASED, EV_VOIDED)
# recorded for the audit trail only: they never change a state, a gate, a conflict check or a sequence check.
# (`job.transition_rejected` is not an `approval.*` type, so the fold ignores it anyway; it is listed so callers and the
# audit-only tests name every rejection that must never feed back into a decision.)
AUDIT_ONLY_TYPES = (EV_REJECTED, "job.transition_rejected")

# --- operations (what a caller asks the table about) and the event each one writes
OP_APPROVE, OP_DECLINE, OP_SUPERSEDE = "approve", "decline", "supersede"
OP_BEGIN, OP_DONE, OP_RELEASE, OP_VOID = "begin", "done", "release", "void"
OPERATIONS = (OP_APPROVE, OP_DECLINE, OP_SUPERSEDE, OP_BEGIN, OP_DONE, OP_RELEASE, OP_VOID)
EVENT_FOR_OPERATION = {OP_APPROVE: EV_APPROVED, OP_DECLINE: EV_DECLINED, OP_SUPERSEDE: EV_SUPERSEDED, OP_BEGIN: EV_STARTED,
                       OP_DONE: EV_CONSUMED, OP_RELEASE: EV_RELEASED, OP_VOID: EV_VOIDED}
OPERATION_FOR_EVENT = {event: op for op, event in EVENT_FOR_OPERATION.items()}

# --- refusal codes (stable; used in `approval.rejected` reasons and command output)
NOT_FOUND = "not_found"
WRONG_STATE = "wrong_state"
EXPIRED_CODE = "expired"
CLOCK_SKEW = "clock_skew"
CONTENT_MISSING = "content_missing"
HASH_MISMATCH = "hash_mismatch"
MANIFEST_INVALID = "manifest_invalid"
MANIFEST_CHANGED = "manifest_changed"
INVITE_CONFLICT = "invite_conflict"
ALREADY_SUBMITTED = "already_submitted"
IN_FLIGHT_CONFLICT = "in_flight_conflict"
SUBMISSION_UNRESOLVED = "submission_unresolved"
SUBMISSION_NOT_CONSUMED = "submission_not_consumed"
NOT_INTERACTIVE = "not_interactive"
REFUSAL_CODES = (NOT_FOUND, WRONG_STATE, EXPIRED_CODE, CLOCK_SKEW, CONTENT_MISSING, HASH_MISMATCH, MANIFEST_INVALID,
                 MANIFEST_CHANGED, INVITE_CONFLICT, ALREADY_SUBMITTED, IN_FLIGHT_CONFLICT, SUBMISSION_UNRESOLVED,
                 SUBMISSION_NOT_CONSUMED, NOT_INTERACTIVE)

# --- validation codes owned by the item parser
APR001 = "APR001"

# --- the transition table (spec section 5): (state, operation) -> new state. Anything absent is refused.
# `release` leads back to `approved`; whether that is then `expired` is computed from the clock, never stored.
TRANSITIONS = {
    (PENDING, OP_APPROVE): APPROVED,
    (PENDING, OP_DECLINE): DECLINED,
    (PENDING, OP_SUPERSEDE): SUPERSEDED,
    (APPROVED, OP_SUPERSEDE): SUPERSEDED,
    (APPROVED, OP_BEGIN): IN_FLIGHT,
    (IN_FLIGHT, OP_DONE): CONSUMED,
    (IN_FLIGHT, OP_RELEASE): APPROVED,
    (IN_FLIGHT, OP_VOID): VOIDED,
}
# the refusal code for a pair that is not in TRANSITIONS; every other refused pair is `wrong_state`
SPECIAL_REFUSALS = {
    (IN_FLIGHT, OP_SUPERSEDE): IN_FLIGHT_CONFLICT,   # a newer proposal never supersedes an action of uncertain outcome
    (EXPIRED, OP_BEGIN): EXPIRED_CODE,
}
