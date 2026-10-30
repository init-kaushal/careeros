"""Data shapes for outreach. Frozen dataclasses only; dates and timestamps are strings (`YYYY-MM-DD`,
`YYYY-MM-DDTHH:MM:SSZ`)."""

from __future__ import annotations

from dataclasses import dataclass

from careeros.core.outreach import rules


@dataclass(frozen=True)
class FoundIn:
    kind: str       # "job" or "target"
    id: str         # a job_ or tgt_ id


@dataclass(frozen=True)
class Person:
    id: str
    url: str
    name: str
    headline: str
    company: str
    company_slug: str
    role: str
    market: str | None
    found_in: tuple[FoundIn, ...]
    found_at: str


@dataclass(frozen=True)
class Evidence:
    source: str
    read_on: str


@dataclass(frozen=True)
class Target:
    id: str
    slug: str
    company: str
    market: str
    fit: int
    sponsorship: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class PersonFlags:
    """A person's state folded from the ledger. Every field is a `ts` of the event it comes from (None when absent)."""
    invited_at: str | None = None       # the EARLIEST consumed linkedin_invite
    messaged_at: str | None = None      # the LATEST consumed linkedin_message
    accepted_at: str | None = None
    replied_at: str | None = None
    closed_at: str | None = None
    closed_reason: str | None = None
    invite_in_flight: bool = False      # a linkedin_invite item of the person is in_flight
    added_at: str | None = None         # ts of the earliest outreach.person_added
    last_activity: str | None = None    # latest ts of person_added, a consumed item, accepted or replied (not closed)

    @property
    def label(self) -> str:
        """closed, replied, connected, messaged, invited or discovered (first match)."""
        for label, flag in rules.LABEL_FLAGS:
            if getattr(self, flag) is not None:
                return label
        return rules.DISCOVERED


@dataclass(frozen=True)
class TargetView:
    id: str
    slug: str
    decision: str                       # candidate | approved | dropped (events only)
    status: str                         # displayed status by `rules.STATUS_PRECEDENCE`
    stale: bool
    last_activity: str | None
    people: tuple[str, ...] = ()        # ids of the person records whose found_in names this target


@dataclass(frozen=True)
class JobInfo:
    """Plain data about a saved job, built by callers: `status_events` is `((ts, new_state), ...)`."""
    id: str
    company: str
    status: str
    status_events: tuple[tuple[str, str], ...] = ()
