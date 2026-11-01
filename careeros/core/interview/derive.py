"""`needs_update`, the `upcoming` and `needs-debrief` views and the status and record findings (spec 6.3 to 6.5, 7.1
and 8). Pure.

Every function takes plain data and, where time matters, `now` (a UTC timestamp string); an unreadable `now` raises
ValueError. Inputs:

* `rounds` maps round id to its validated, event-matched record, or to None when the round is incomplete (its record is
  missing, invalid or mismatched, spec 6.5); an id absent from `rounds` is treated as None;
* `flags` maps round id to the ledger-folded `RoundFlags`. Only ids in `flags` are rounds: a record without flags (an
  orphan) feeds nothing;
* `jobs` are `JobRef` values; `prep` maps job id to a prep status (`rules.PREP_STATUSES`, absent means `none`).

A round's job is its record's `job` when complete, otherwise the `job:` its `round_added` event recorded.

Output order is a documented total order, so shuffling any input never changes the result:

* upcoming: the `scheduled_for` instant ascending (a date-only value at its UTC midnight), then the round id;
* needs_debrief group: `held_at` ascending, then the round id; needs_update group: the `scheduled_for` instant ascending,
  then the round id;
* findings: round id, then code.

Nothing is capped or truncated. A date-only `scheduled_for` is overdue once `now` is at or after the start of the next UTC
day; a datetime once `now` is after it. `upcoming` keeps a round whose instant is at most `days` days after `now`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from careeros.core.approvals import fold as afold
from careeros.core.interview import _common as c
from careeros.core.interview import rules
from careeros.core.interview.model import Interviewer, JobRef, Round, RoundFlags


@dataclass(frozen=True)
class UpcomingItem:
    round_id: str
    job_id: str
    stage: str
    format: str
    scheduled_for: str
    interviewers: tuple[Interviewer, ...]
    prep: str                           # none | drafted | checked
    date_unrecorded: bool               # the record's date differs from the date the ledger last recorded (spec 7.1)


@dataclass(frozen=True)
class DebriefRow:
    round_id: str
    job_id: str
    stage: str | None                   # None for an incomplete round
    format: str | None
    scheduled_for: str | None
    held_at: str | None
    incomplete: bool
    date_unrecorded: bool


@dataclass(frozen=True)
class NeedsDebrief:
    needs_debrief: tuple[DebriefRow, ...]
    needs_update: tuple[DebriefRow, ...]


def _now(now: str) -> datetime:
    current = afold.parse_ts(now)
    if current is None:
        raise ValueError(f"unreadable now {now!r}")
    return current


def _overdue(when: str, current: datetime) -> bool:
    instant = c.parse_when(when)
    if instant is None:
        return False
    if c.is_date_only(when):
        return current >= instant + timedelta(days=1)
    return current > instant


def needs_update(round: Round | None, flags: RoundFlags, now: str) -> bool:
    """Label `scheduled` and the record's `scheduled_for` in the past. False for an incomplete round (no record)."""
    current = _now(now)
    return round is not None and flags.label == rules.SCHEDULED and _overdue(round.scheduled_for, current)


def _date_unrecorded(record: Round | None, fl: RoundFlags) -> bool:
    return record is not None and record.scheduled_for != fl.recorded_when


def _job_of(record: Round | None, fl: RoundFlags) -> str:
    return record.job if record is not None else fl.job


def build_upcoming(rounds: Mapping[str, Round | None], flags: Mapping[str, RoundFlags], jobs: Iterable[JobRef],
                   prep: Mapping[str, str], now: str, days: int) -> list[UpcomingItem]:
    """Complete rounds labelled `scheduled`, not `needs_update`, whose `scheduled_for` is within `days` days of `now`
    (inclusive), soonest first then by id. `jobs` is accepted for symmetry with the other views and is not read."""
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValueError(f"days must be a whole number of days, at least 0, not {days!r}")
    current = _now(now)
    limit = current + timedelta(days=days)
    out: list[tuple[tuple, UpcomingItem]] = []
    for rid, fl in flags.items():
        record = rounds.get(rid)
        if record is None or fl.label != rules.SCHEDULED or _overdue(record.scheduled_for, current):
            continue
        instant = c.parse_when(record.scheduled_for)
        if instant is None or instant > limit:
            continue
        out.append(((instant, rid), UpcomingItem(rid, record.job, record.stage, record.format, record.scheduled_for,
                                                 record.interviewers, prep.get(record.job, rules.PREP_NONE),
                                                 _date_unrecorded(record, fl))))
    out.sort(key=lambda pair: pair[0])
    return [item for _, item in out]


def _row(rid: str, record: Round | None, fl: RoundFlags) -> DebriefRow:
    return DebriefRow(rid, _job_of(record, fl), record.stage if record else None, record.format if record else None,
                      record.scheduled_for if record else None, fl.held_at, record is None, _date_unrecorded(record, fl))


def build_needs_debrief(rounds: Mapping[str, Round | None], flags: Mapping[str, RoundFlags], jobs: Iterable[JobRef],
                        now: str) -> NeedsDebrief:
    """Held rounds (complete or incomplete, flagged), oldest `held_at` first; and overdue scheduled complete rounds whose
    job is not terminal, oldest `scheduled_for` first. A held round on a terminal job stays in the first group."""
    current = _now(now)
    terminal = {j.id for j in jobs if j.terminal}
    held: list[tuple[tuple, DebriefRow]] = []
    late: list[tuple[tuple, DebriefRow]] = []
    for rid, fl in flags.items():
        record = rounds.get(rid)
        if fl.label == rules.HELD:
            held.append(((fl.held_at, rid), _row(rid, record, fl)))
        elif (record is not None and fl.label == rules.SCHEDULED and _overdue(record.scheduled_for, current)
              and record.job not in terminal):
            late.append(((c.parse_when(record.scheduled_for), rid), _row(rid, record, fl)))
    held.sort(key=lambda pair: pair[0])
    late.sort(key=lambda pair: pair[0])
    return NeedsDebrief(tuple(r for _, r in held), tuple(r for _, r in late))


def status_issues(rounds: Mapping[str, Round | None], flags: Mapping[str, RoundFlags], jobs: Iterable[JobRef],
                  now: str) -> list[tuple[str, str, str]]:
    """`(code, round_id, message)` for `INT007` (status consistency) and `INT100` (a round is `needs_update`). Nothing is
    ever run for the user; the messages only name the command they may want."""
    current = _now(now)
    by_id = {j.id: j for j in jobs}
    out: list[tuple[str, str, str]] = []
    for rid, fl in flags.items():
        record = rounds.get(rid)
        job_id = _job_of(record, fl)
        job = by_id.get(job_id)
        label = fl.label
        late = record is not None and label == rules.SCHEDULED and _overdue(record.scheduled_for, current)
        if job is not None and label in (rules.SCHEDULED, rules.HELD) and job.status in rules.PRE_INTERVIEW_STATUSES:
            out.append((rules.INT007, rid,
                        f"round {rid} is {label} but job {job_id} is still {job.status}; if the interview process has "
                        f"started, you may want `careeros transition {job_id} --to SCREEN` (or the stage it reached)"))
        elif job is not None and label == rules.SCHEDULED and job.terminal:
            out.append((rules.INT007, rid,
                        f"round {rid} is still scheduled but job {job_id} is {job.status}; you may want "
                        f"`careeros interview round cancel {rid} --reason ...` or `careeros interview round held {rid}`"))
        if late:
            out.append((rules.INT100, rid,
                        f"round {rid} was scheduled for {record.scheduled_for} and has no update; record "
                        f"`careeros interview round held`, `round cancel` or `round reschedule` for it"))
    out.sort(key=lambda t: (t[1], t[0]))
    return out


def record_issues(rounds: Mapping[str, Round | None], flags: Mapping[str, RoundFlags]) -> list[tuple[str, str, str]]:
    """`(code, round_id, message)` for `INT008` (the record's date differs from the date the ledger last recorded) and
    `INT009` (a debrief block without a `debriefed` event), for complete rounds only (spec 7.1)."""
    out: list[tuple[str, str, str]] = []
    for rid, fl in flags.items():
        record = rounds.get(rid)
        if record is None:
            continue
        if record.scheduled_for != fl.recorded_when:
            out.append((rules.INT008, rid,
                        f"round {rid}'s record says scheduled_for {record.scheduled_for} but the ledger last recorded "
                        f"{fl.recorded_when} (an interrupted reschedule or a hand edit); views use the record's date. "
                        f"Re-run `careeros interview round reschedule {rid} --when <date>` with the intended date"))
        if record.debrief is not None and fl.debriefed_at is None:
            out.append((rules.INT009, rid,
                        f"round {rid}'s record has a debrief block but the round has no debriefed event (an interrupted "
                        f"debrief or a hand edit); it is not counted as a debrief. Re-run `careeros interview debrief {rid}`"))
    out.sort(key=lambda t: (t[1], t[0]))
    return out
