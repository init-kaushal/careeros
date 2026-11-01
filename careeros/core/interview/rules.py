"""Constants, enums, event types, refusal and validation codes and the transition table for interview rounds (spec
sections 4 to 8). Pure data; changing any constant is a rules version bump."""

from __future__ import annotations

SCHEMA = 1

# --- numbers (spec 6.1); the last three bound inputs (record and command validation) and nothing else
UPCOMING_DEFAULT_DAYS = 14
MAX_INTERVIEWERS = 10
MAX_DEBRIEF_ITEMS = 10
MAX_TEXT = 500

# --- enums (spec 4)
STAGES = ("recruiter_screen", "hiring_manager", "technical", "system_design", "panel", "final", "other")
FORMATS = ("phone", "video", "onsite", "take_home", "other")
RATINGS = ("strong", "mixed", "weak")
SOURCES = ("user", "observed")

# --- ledger events (reserved prefix `interview.`, spec 5)
EVENT_PREFIX = "interview."
EV_ROUND_ADDED = "interview.round_added"
EV_HELD = "interview.held"
EV_CANCELLED = "interview.cancelled"
EV_RESCHEDULED = "interview.rescheduled"
EV_DEBRIEFED = "interview.debriefed"
EV_REJECTED = "interview.rejected"
STATE_EVENTS = (EV_ROUND_ADDED, EV_HELD, EV_CANCELLED, EV_RESCHEDULED, EV_DEBRIEFED)
# recorded for the audit trail only: they never change any state, flag or sequence check
AUDIT_ONLY_TYPES = (EV_REJECTED,)
KNOWN_EVENTS = STATE_EVENTS + AUDIT_ONLY_TYPES
SOURCED_EVENTS = (EV_HELD, EV_CANCELLED)        # these need `source` in SOURCES; `cancelled` also needs a reason

# --- round labels (spec 6.2), first match (the flag each label needs); `scheduled` is the default
CANCELLED, DEBRIEFED, HELD, SCHEDULED = "cancelled", "debriefed", "held", "scheduled"
LABEL_FLAGS = ((CANCELLED, "cancelled_at"), (DEBRIEFED, "debriefed_at"), (HELD, "held_at"))
LABEL_PRECEDENCE = tuple(label for label, _ in LABEL_FLAGS) + (SCHEDULED,)

# --- transitions: (current label, event type) -> new label; anything absent is refused `wrong_state`. Each of held,
# cancelled and debriefed happens at most once because its own label is never a source of it; cancelled is terminal.
TRANSITIONS = {
    (SCHEDULED, EV_HELD): HELD,
    (SCHEDULED, EV_CANCELLED): CANCELLED,
    (SCHEDULED, EV_RESCHEDULED): SCHEDULED,
    (HELD, EV_DEBRIEFED): DEBRIEFED,
}
FLAG_FOR_EVENT = {EV_HELD: "held_at", EV_CANCELLED: "cancelled_at", EV_DEBRIEFED: "debriefed_at"}

# --- job statuses the derivations compare against (pinned to `models.State` and `state_machine.TERMINAL` by a test)
PRE_INTERVIEW_STATUSES = ("DISCOVERED", "EVALUATED", "SHORTLISTED", "RESEARCHED", "PREPARING", "READY_TO_APPLY",
                          "APPROVAL_REQUIRED", "APPLIED")
TERMINAL_JOB_STATUSES = ("ACCEPTED", "REJECTED", "WITHDRAWN")

# --- prep status (spec 6.4) and the record status of an incomplete round (spec 6.5)
PREP_NONE, PREP_DRAFTED, PREP_CHECKED = "none", "drafted", "checked"
PREP_STATUSES = (PREP_NONE, PREP_DRAFTED, PREP_CHECKED)
RECORD_MISSING, RECORD_INVALID, RECORD_MISMATCHED = "missing", "invalid", "mismatched"
RECORD_STATUSES = (RECORD_MISSING, RECORD_INVALID, RECORD_MISMATCHED)

# --- refusal codes (stable; used in `interview.rejected` reasons and command output)
NOT_FOUND = "not_found"
WRONG_STATE = "wrong_state"
DUPLICATE = "duplicate"
INVALID_INPUT = "invalid_input"
RECORD_UNAVAILABLE = "record_unavailable"
CLOCK_SKEW = "clock_skew"
LEDGER_CORRUPT = "ledger_corrupt"
REFUSAL_CODES = (NOT_FOUND, WRONG_STATE, DUPLICATE, INVALID_INPUT, RECORD_UNAVAILABLE, CLOCK_SKEW, LEDGER_CORRUPT)

# --- validation codes (spec 8)
INT001, INT002, INT003, INT004, INT005, INT006, INT007, INT008, INT009, INT100 = (
    "INT001", "INT002", "INT003", "INT004", "INT005", "INT006", "INT007", "INT008", "INT009", "INT100")
