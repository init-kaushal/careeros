"""Constants, event types, enums, refusal and validation codes and the transition tables for outreach (spec sections 4
to 6). Pure data; changing any constant is a rules version bump."""

from __future__ import annotations

from careeros.core.approvals import people

SCHEMA = 1

# --- numbers (spec 6.1)
INVITE_FOLLOWUP_DAYS = 7
APPLICATION_FOLLOWUP_DAYS = 10
CAP_WINDOW_DAYS = 7
DEFAULT_WEEKLY_CAP = 15
TARGET_STALE_DAYS = 60
MAX_CONTACTS_PER_TARGET = 2      # at most one `recruiter` and one `hiring_manager`

# --- ledger events (reserved prefix `outreach.`)
EVENT_PREFIX = "outreach."
EV_PERSON_ADDED = people.EV_PERSON_ADDED
EV_CLOSED = people.EV_CLOSED
EV_TARGET_ADDED = "outreach.target_added"
EV_TARGET_APPROVED = "outreach.target_approved"
EV_TARGET_DROPPED = "outreach.target_dropped"
EV_ACCEPTED = "outreach.accepted"
EV_REPLIED = "outreach.replied"
EV_REJECTED = "outreach.rejected"
STATE_EVENTS = (EV_PERSON_ADDED, EV_TARGET_ADDED, EV_TARGET_APPROVED, EV_TARGET_DROPPED, EV_ACCEPTED, EV_REPLIED, EV_CLOSED)
# recorded for the audit trail only: they never change any state, flag or sequence check
AUDIT_ONLY_TYPES = (EV_REJECTED,)
KNOWN_EVENTS = STATE_EVENTS + AUDIT_ONLY_TYPES

# --- enums
ROLES = ("recruiter", "hiring_manager", "peer", "other")
SPONSORSHIP = ("offered", "likely", "unknown", "not_offered", "n/a")
SPONSORSHIP_NA = "n/a"
FOUND_IN_KINDS = ("job", "target")
CLOSED_REASONS = ("no_response", "declined", "not_a_fit", "other")
OUTCOME_SOURCES = ("user", "observed", "email")

# --- person outcomes (spec 6.2). `NEEDS_ANY`: at least one of these flags must be set; `BLOCKED_BY`: none may be set.
# Each outcome kind is recorded at most once, so an outcome is blocked by its own flag.
ACCEPTED, REPLIED, CLOSED = "accepted", "replied", "closed"
OUTCOMES = (ACCEPTED, REPLIED, CLOSED)
EVENT_FOR_OUTCOME = {ACCEPTED: EV_ACCEPTED, REPLIED: EV_REPLIED, CLOSED: EV_CLOSED}
OUTCOME_FOR_EVENT = {event: outcome for outcome, event in EVENT_FOR_OUTCOME.items()}
OUTCOME_NEEDS_ANY = {ACCEPTED: ("invited_at",), REPLIED: ("invited_at", "messaged_at"), CLOSED: ()}
OUTCOME_BLOCKED_BY = {ACCEPTED: ("accepted_at", "closed_at"), REPLIED: ("replied_at", "closed_at"), CLOSED: ("closed_at",)}

# --- person label, first match (the flag each label needs); `discovered` is the record-only default
LABEL_FLAGS = (("closed", "closed_at"), ("replied", "replied_at"), ("connected", "accepted_at"),
               ("messaged", "messaged_at"), ("invited", "invited_at"))
DISCOVERED = "discovered"
LABEL_PRECEDENCE = tuple(label for label, _ in LABEL_FLAGS) + (DISCOVERED,)

# --- target decisions (spec 6.5): (decision state, operation) -> new decision state; anything absent is refused
CANDIDATE, APPROVED, DROPPED = "candidate", "approved", "dropped"
DECISIONS = (CANDIDATE, APPROVED, DROPPED)
OP_APPROVE, OP_DROP = "approve", "drop"
DECISION_OPERATIONS = (OP_APPROVE, OP_DROP)
EVENT_FOR_DECISION = {OP_APPROVE: EV_TARGET_APPROVED, OP_DROP: EV_TARGET_DROPPED}
OPERATION_FOR_EVENT = {event: op for op, event in EVENT_FOR_DECISION.items()}
DECISION_TRANSITIONS = {
    (CANDIDATE, OP_APPROVE): APPROVED,
    (CANDIDATE, OP_DROP): DROPPED,
    (APPROVED, OP_DROP): DROPPED,
}

# --- displayed target status, first match wins (spec 6.5)
STATUS_PRECEDENCE = ("dropped", "warm", "contacted", "opening-seen", "approved", "candidate")

# --- refusal codes (stable; used in `outreach.rejected` reasons and command output)
NOT_FOUND = "not_found"
WRONG_STATE = "wrong_state"
DUPLICATE = "duplicate"
INVALID_INPUT = "invalid_input"
CLOCK_SKEW = "clock_skew"
LEDGER_CORRUPT = "ledger_corrupt"
IDENTITY_CONFLICT = "identity_conflict"
CAP_CONFIG_INVALID = "cap_config_invalid"
PERSON_CLOSED = "person_closed"
CAP_REACHED = "cap_reached"
REFUSAL_CODES = (NOT_FOUND, WRONG_STATE, DUPLICATE, INVALID_INPUT, CLOCK_SKEW, LEDGER_CORRUPT, IDENTITY_CONFLICT,
                 CAP_CONFIG_INVALID, PERSON_CLOSED, CAP_REACHED)

# --- validation codes (spec section 9)
OUT001, OUT002, OUT003, OUT004, OUT005, OUT006, OUT007, OUT100 = (
    "OUT001", "OUT002", "OUT003", "OUT004", "OUT005", "OUT006", "OUT007", "OUT100")
