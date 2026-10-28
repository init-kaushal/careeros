"""The follow-up queue, the next contacts and the warm contacts (outreach spec sections 6.3 and 6.6). Pure.

Every function takes `now` (a UTC timestamp string such as `2026-03-01T00:00:00Z`) and plain data; nothing reads a clock or
a file. An unreadable `now` raises ValueError. Inputs: `people` maps person id to the parsed record, `flags` maps person id
to the ledger-folded `PersonFlags`, `targets` maps slug to a `TargetInfo` (the parsed record plus the folded `TargetView`),
`jobs` are `JobInfo` values built by the caller and `states` are the folded approval `ItemState`s (item id to state).

Output order is a documented total order, so shuffling any input never changes the result:

* queue: `due_at` ascending (most overdue first), then invites before applications, then the entity id;
* next: targets by rank (home market, `sponsorship == "n/a"`, by fit descending; then non-home by sponsorship tier `offered`,
  `likely`, `unknown`, then fit descending; then people with no usable target, by company slug), ties by group slug; inside a
  group the recruiter before the hiring manager. The first `count_remaining` contacts are returned;
* warm: `replied` before `connected`, then company slug, then person id.

Decisions where the spec is silent (kept deliberately small):

* only `recruiter` and `hiring_manager` people are ever offered by `next` (`MAX_CONTACTS_PER_TARGET` is then the sum of the
  two single slots); within a role the earliest `found_at`, then the person id, is offered; eligibility is decided BEFORE the
  pick, so an ineligible first choice never blocks the next person of the role;
* a person's group is the slug of the approved target they are linked to; otherwise the slug of the company of the saved
  non-terminal job they are linked to. A target-sourced and a job-sourced person of the same company share one group and so
  one limit. A job-sourced person whose company target is dropped, missing or `not_offered` is unranked and sorts last;
* the queue's pre-opening exclusion looks for ANY saved job (a terminal one too, like `opening-seen`), matching the person's
  `company_slug` or the slug of a linked target; a person with no record (flags only) is queued with no label.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from careeros.core.approvals import fold as afold
from careeros.core.approvals import rules as arules
from careeros.core.approvals.model import ItemState, State
from careeros.core.outreach import rules
from careeros.core.outreach.model import JobInfo, Person, PersonFlags, Target, TargetView
from careeros.core.outreach.slug import slugify

MAX_CONTACTS_PER_TARGET = rules.MAX_CONTACTS_PER_TARGET
APPLIED = "APPLIED"
TERMINAL_JOB_STATUSES = ("ACCEPTED", "REJECTED", "WITHDRAWN")        # pinned equal to state_machine.TERMINAL by a test
UNCOVERED_NOTE = ("Email and interview follow-ups are not covered: they are prose-only today and CareerOS sends no email, "
                  "so nothing here tracks them.")
_FMT = "%Y-%m-%dT%H:%M:%SZ"
_CONTACT_ROLES = ("recruiter", "hiring_manager")
_PERSON_KINDS = (arules.LINKEDIN_INVITE, arules.LINKEDIN_MESSAGE)
_TIER = {"offered": 0, "likely": 1, "unknown": 2}
_KIND_ORDER = {"invite": 0, "application": 1}


@dataclass(frozen=True)
class TargetInfo:
    """A target's parsed record (fit, sponsorship) and its folded view (decision, stale)."""
    record: Target
    view: TargetView


@dataclass(frozen=True)
class QueueItem:
    kind: str                       # "invite" | "application"
    entity_id: str                  # a per_ id or a job_ id
    label: str | None               # the person's name or the job's company; None for a person with no record
    due_at: str
    days_overdue: int               # whole days since `due_at`, never negative
    reason: str


@dataclass(frozen=True)
class Queue:
    items: tuple[QueueItem, ...]
    uncovered_note: str


@dataclass(frozen=True)
class NextContact:
    person_id: str
    name: str
    role: str
    company: str
    group: str                      # the company slug the per-target limit and the ranking use
    target_id: str | None
    source: str                     # "target" | "job"
    fit: int | None
    sponsorship: str | None
    stale: bool
    reason: str


@dataclass(frozen=True)
class WarmContact:
    person_id: str
    name: str
    role: str
    company: str
    label: str                      # "replied" | "connected"
    since: str | None               # replied_at or accepted_at
    job_ids: tuple[str, ...]        # the company's saved non-terminal jobs, sorted
    reason: str


def _parse(now: str) -> datetime:
    current = afold.parse_ts(now)
    if current is None:
        raise ValueError(f"unreadable now {now!r}")
    return current


def _fmt(moment: datetime) -> str:
    return moment.strftime(_FMT)


def _job_slugs(jobs: Iterable[JobInfo], *, live_only: bool = False) -> dict[str, list[str]]:
    """company slug -> sorted ids of the saved jobs there (only the non-terminal ones with `live_only`)."""
    out: dict[str, set[str]] = {}
    for j in jobs:
        slug = slugify(j.company)
        if slug is not None and not (live_only and j.status in TERMINAL_JOB_STATUSES):
            out.setdefault(slug, set()).add(j.id)
    return {slug: sorted(found) for slug, found in out.items()}


# --- queue ---------------------------------------------------------------------------------------------------------------

def _pre_opening(person: Person, job_slugs: Mapping[str, object], slug_of_target: Mapping[str, str]) -> bool:
    """Found only through targets, and no saved job at the company (by the person's slug or a linked target's slug)."""
    if not person.found_in or any(f.kind != "target" for f in person.found_in):
        return False
    slugs = {person.company_slug} | {slug_of_target[f.id] for f in person.found_in if f.id in slug_of_target}
    return not slugs & set(job_slugs)


def _latest_status(events: Iterable[tuple[str, str]]) -> tuple[datetime, str] | None:
    """The latest readable status event (ties go to the later position); None when there is none."""
    best: tuple[datetime, int, str] | None = None
    for position, (ts, state) in enumerate(events):
        moment = afold.parse_ts(ts)
        if moment is not None and (best is None or (moment, position) >= (best[0], best[1])):
            best = (moment, position, state)
    return None if best is None else (best[0], best[2])


def build_queue(people: Mapping[str, Person], flags: Mapping[str, PersonFlags], targets: Mapping[str, TargetInfo],
                jobs: Iterable[JobInfo], now: str) -> Queue:
    """Invites due 7 days after they were sent and applications due 10 days after APPLIED, most overdue first."""
    current = _parse(now)
    jobs = list(jobs)
    job_slugs = _job_slugs(jobs)
    slug_of_target = {info.view.id: slug for slug, info in targets.items()}
    items: list[QueueItem] = []
    for pid, fl in flags.items():
        if fl.invited_at is None or fl.accepted_at is not None or fl.replied_at is not None or fl.closed_at is not None:
            continue
        invited = afold.parse_ts(fl.invited_at)
        if invited is None:
            continue
        if fl.messaged_at is not None:
            messaged = afold.parse_ts(fl.messaged_at)
            if messaged is None or messaged > invited:          # the one follow-up message was already sent
                continue
        due = invited + timedelta(days=rules.INVITE_FOLLOWUP_DAYS)
        if due > current:
            continue
        record = people.get(pid)
        if record is not None and _pre_opening(record, job_slugs, slug_of_target):
            continue
        items.append(QueueItem("invite", pid, record.name if record is not None else None, _fmt(due), (current - due).days,
                               f"invited {fl.invited_at}; follow-up was due {_fmt(due)}"))
    for j in jobs:
        latest = _latest_status(j.status_events)
        if j.status != APPLIED or j.status in TERMINAL_JOB_STATUSES or latest is None or latest[1] != APPLIED:
            continue
        due = latest[0] + timedelta(days=rules.APPLICATION_FOLLOWUP_DAYS)
        if due > current:
            continue
        items.append(QueueItem("application", j.id, j.company, _fmt(due), (current - due).days,
                               f"applied {_fmt(latest[0])}; follow-up was due {_fmt(due)}"))
    items.sort(key=lambda i: (i.due_at, _KIND_ORDER[i.kind], i.entity_id))
    return Queue(tuple(items), UNCOVERED_NOTE)


# --- next ----------------------------------------------------------------------------------------------------------------

def _is_live(st: ItemState, now: str) -> bool:
    """pending, approved and not expired, or in flight (the state's own expiry is re-checked against `now`)."""
    if st.kind not in _PERSON_KINDS or st.state is None:
        return False
    if st.state in (State.PENDING, State.IN_FLIGHT):
        return True
    return st.state is State.APPROVED and not afold.is_expired(st.approved_at, now)


def _market_part(info: TargetInfo) -> str:
    if info.record.sponsorship == "n/a":
        return f"home market, fit {info.record.fit}"
    return f"sponsorship {info.record.sponsorship}, fit {info.record.fit}"


def _rank(info: TargetInfo | None, group: str) -> tuple:
    if info is None:
        return (2, 0, 0, group)
    if info.record.sponsorship == "n/a":
        return (0, 0, -info.record.fit, group)
    return (1, _TIER[info.record.sponsorship], -info.record.fit, group)


def _usable(info: TargetInfo | None) -> bool:
    """False for `not_offered` (and any sponsorship value the ranking does not know): such a target is never offered through the target route."""
    return info is None or info.record.sponsorship == "n/a" or info.record.sponsorship in _TIER


def build_next(people: Mapping[str, Person], flags: Mapping[str, PersonFlags], targets: Mapping[str, TargetInfo],
               jobs: Iterable[JobInfo], states: Mapping[str, ItemState], count_remaining: int, now: str) -> list[NextContact]:
    """Who to invite next, best first, at most `count_remaining` of them (see the module docstring). Only `recruiter` and
    `hiring_manager` people are offered (never peers or others), one of each per group. A `not_offered` target excludes the
    people reached through the target route only; a person found via a saved non-terminal job is offered regardless."""
    _parse(now)
    limit = max(0, count_remaining)
    live = {st.target_id for st in states.values() if _is_live(st, now)}
    by_target_id = {info.view.id: (slug, info) for slug, info in targets.items()}
    live_jobs = {j.id: j for j in sorted(jobs, key=lambda j: (j.id, j.company, j.status)) if j.status not in TERMINAL_JOB_STATUSES}
    groups: dict[str, dict[str, list[tuple]]] = {}
    infos: dict[str, TargetInfo | None] = {}
    for pid, record in people.items():
        fl = flags.get(pid, PersonFlags())
        if (record.role not in _CONTACT_ROLES or fl.label != rules.DISCOVERED or fl.invited_at is not None
                or fl.invite_in_flight or fl.closed_at is not None or pid in live):
            continue
        approved = sorted((slug, info) for f in record.found_in if f.kind == "target" and f.id in by_target_id
                          for slug, info in [by_target_id[f.id]] if info.view.decision == rules.APPROVED)
        if approved:
            group, info = approved[0][0], approved[0][1]
            source, job_id, reason_head = "target", None, f"approved target {group}"
        else:
            mine = sorted((live_jobs[f.id] for f in record.found_in if f.kind == "job" and f.id in live_jobs
                           and slugify(live_jobs[f.id].company) is not None), key=lambda j: j.id)
            if not mine:
                continue
            group = slugify(mine[0].company)
            info = targets.get(group)
            if info is not None and info.view.decision == rules.DROPPED:
                info = None
            if info is not None and not _usable(info):
                info = None                  # `not_offered` only excludes the target route: the user saved this job themselves
            source, job_id = "job", mine[0].id
            reason_head = f"saved job {job_id} at {group}" if info is None else f"saved job {job_id}; target {group}"
        if source == "target" and not _usable(info):
            continue
        reason = reason_head if info is None else f"{reason_head}: {_market_part(info)}"
        if info is not None and info.view.stale:
            reason += f" (stale: no activity for more than {rules.TARGET_STALE_DAYS} days)"
        infos[group] = info
        groups.setdefault(group, {}).setdefault(record.role, []).append(
            ((record.found_at, pid), NextContact(pid, record.name, record.role, record.company, group,
                                                  info.view.id if info is not None else None, source,
                                                  info.record.fit if info is not None else None,
                                                  info.record.sponsorship if info is not None else None,
                                                  info.view.stale if info is not None else False, reason)))
    out: list[NextContact] = []
    for group in sorted(groups, key=lambda g: _rank(infos[g], g)):
        for role in _CONTACT_ROLES:
            if role in groups[group]:
                out.append(min(groups[group][role], key=lambda pair: pair[0])[1])
    return out[:limit]


# --- warm ----------------------------------------------------------------------------------------------------------------

def build_warm(people: Mapping[str, Person], flags: Mapping[str, PersonFlags], jobs: Iterable[JobInfo], now: str) -> list[WarmContact]:
    """`connected` and `replied` people at a company with a saved, non-terminal job."""
    _parse(now)
    live = _job_slugs(jobs, live_only=True)
    out: list[tuple[tuple, WarmContact]] = []
    for pid, record in people.items():
        fl = flags.get(pid, PersonFlags())
        label = fl.label
        if label not in ("replied", "connected") or record.company_slug not in live:
            continue
        job_ids = tuple(live[record.company_slug])
        since = fl.replied_at if label == "replied" else fl.accepted_at
        out.append(((0 if label == "replied" else 1, record.company_slug, pid),
                    WarmContact(pid, record.name, record.role, record.company, label, since, job_ids,
                                f"{label} at {record.company_slug}, which has a saved job ({', '.join(job_ids)})")))
    out.sort(key=lambda pair: pair[0])
    return [contact for _, contact in out]
