"""Data shapes for interview rounds. Frozen dataclasses only; dates and timestamps are strings (`YYYY-MM-DD`,
`YYYY-MM-DDTHH:MM:SSZ`)."""

from __future__ import annotations

from dataclasses import dataclass

from careeros.core.interview import rules


@dataclass(frozen=True)
class Interviewer:
    name: str
    title: str                          # may be empty


@dataclass(frozen=True)
class Debrief:
    rating: str                         # one of rules.RATINGS: the user's own words mapped to the enum, never invented
    questions_asked: tuple[str, ...]
    do_differently: tuple[str, ...]
    next_step: str
    next_step_date: str | None = None


@dataclass(frozen=True)
class Round:
    """A validated `rounds/<id>.md` record: editable source data, never state."""
    id: str
    job: str
    stage: str
    format: str
    scheduled_for: str                  # an ISO date or a UTC datetime
    interviewers: tuple[Interviewer, ...] = ()
    debrief: Debrief | None = None
    body: str = ""                      # the free text after the frontmatter; nothing parses it


@dataclass(frozen=True)
class RoundFlags:
    """A round's state folded from the ledger. `job` and `recorded_when` come from the event artifacts (`job:` of
    `round_added`; `when:` of the latest applied `round_added` or `rescheduled`); every `*_at` is the `ts` of its event."""
    job: str
    added_at: str
    recorded_when: str
    held_at: str | None = None
    cancelled_at: str | None = None
    debriefed_at: str | None = None
    cancel_reason: str | None = None

    @property
    def label(self) -> str:
        """cancelled, debriefed, held or scheduled (first match)."""
        for label, flag in rules.LABEL_FLAGS:
            if getattr(self, flag) is not None:
                return label
        return rules.SCHEDULED


@dataclass(frozen=True)
class JobRef:
    """Plain data about a saved job, built by callers."""
    id: str
    status: str
    terminal: bool
