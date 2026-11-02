"""The interview service: round records, the `interview.*` events, the read-only `round list|show`, the `upcoming` and
`needs-debrief` views and the `INT` validation section (spec sections 4 to 8).

State-changing operations (`round_add`, `round_held`, `round_cancel`, `round_reschedule`, `debrief`) validate the actor
before anything else, take the workspace lock, verify the hash chain (`ledger_corrupt`, nothing written), read the clock
ONCE after the lock through the `clock` callable the caller passes and refuse `clock_skew` when that time is earlier than
the latest event of any type. A command that writes a record writes the file FIRST (atomically) and the event SECOND (an
atomic batch) inside a `Transaction`: the file is put back on any exception. A process killed between the two leaves the
new file and no event (spec 7.1): an orphan record after `round add` (`INT002`), a date the ledger did not record after
`round reschedule` (`INT008`), an unrecorded debrief block after `debrief` (`INT009`). Re-running the command completes
it: `round_reschedule` accepts the date the record already holds and `debrief` replaces a block that has no `debriefed`
event. Nothing here repairs or recreates a record.

Identity and state come from the ledger only. A round is complete when its id has an applied `round_added` event, the
record file `rounds/<id>.md` is a readable regular file whose `id` is the file stem and whose `job` is the `job:` element
of that event. Otherwise the round is incomplete (`Loaded.incomplete`: missing, invalid or mismatched): it keeps its
identity, label and history, every record field is unavailable, `held` and `cancel` still work and `reschedule` and
`debrief` are refused `record_unavailable`. An orphan record feeds nothing.

A refusal of an EXISTING round appends an audit-only `interview.rejected` (deduped: the round's latest event of any type is
an identical rejection by the same actor); an unknown id appends nothing. `load`, `round_list` and `round_show` never
write. Nothing here imports `careeros.core.outreach`; the small plumbing mirrors it. This is a recorded decision, not a
security boundary.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from careeros.core import ids, ledger, models
from careeros.core.approvals import fold as afold
from careeros.core.interview import _common as c
from careeros.core.interview import derive
from careeros.core.interview import fold as ifold
from careeros.core.interview import prep as iprep
from careeros.core.interview import round as rround
from careeros.core.interview import rules
from careeros.core.interview.model import Debrief, Interviewer, JobRef, Round, RoundFlags
from careeros.core.memory.store import Transaction
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError, WorkspaceLock, ensure_writable, job_files, read_job, resolve_job

ROUNDS_DIR = "rounds"
MAX_RECORD_BYTES = 1 << 20
RECORD_OK = "ok"
RESTORE = ("restore the file from a backup or version control, or hand-create rounds/{id}.md with the same id and job "
           "(see docs/interviews.md); nothing recreates it for you")


class Refused(WorkspaceError):
    """A refusal (exit 1): `code` is a stable code from `rules.REFUSAL_CODES`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def rel_for(rnd_id: str) -> str:
    return f"{ROUNDS_DIR}/{rnd_id}.md"


# --- reading the records -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordFile:
    rel: str
    stem: str
    record: Round | None            # parsed; `record.id` may differ from `stem` (a renamed or copied file)
    issues: tuple[Issue, ...]


def _parse(text: str, rel: str, stem: str) -> tuple[Round | None, list[Issue]]:
    """The record, or INT001 issues. A record that is valid except that its `id` is not the file stem is returned as is,
    so the caller reports it as a mismatch (INT004) instead of a shape problem."""
    parsed, found = rround.parse(text, rel)
    if parsed is not None:
        return parsed, []
    fm, _, _ = c.load(text, rel)
    other = fm.get("id") if isinstance(fm, dict) else None
    if ids.is_valid_id("round", other) and other != stem:
        again, _ = rround.parse(text, rel_for(other))
        if again is not None:
            return again, []
    return None, found


def _scan(root: Path) -> dict[str, RecordFile]:
    """Every `rounds/*.md`, parsed, keyed by file stem. Symlinks, non-regular files, oversized and undecodable files are
    INT001 and never read whole. Never raises, never writes."""
    out: dict[str, RecordFile] = {}
    try:
        paths = sorted((Path(root) / ROUNDS_DIR).glob("*.md"))
    except OSError:
        return out

    def bad(rel: str, stem: str, why: str) -> RecordFile:
        return RecordFile(rel, stem, None, (Issue("error", rules.INT001, rel, f"{rel} {why}", c.FIX),))

    for path in paths:
        rel, stem = f"{ROUNDS_DIR}/{path.name}", path.stem
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                out[stem] = bad(rel, stem, "is a symlink: a record must be a regular file")
            elif not stat.S_ISREG(mode):
                out[stem] = bad(rel, stem, "is not a regular file")
            else:
                with path.open("rb") as handle:
                    raw = handle.read(MAX_RECORD_BYTES + 1)
                if len(raw) > MAX_RECORD_BYTES:
                    out[stem] = bad(rel, stem, f"is too large (more than {MAX_RECORD_BYTES} bytes)")
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    out[stem] = bad(rel, stem, "is not valid UTF-8")
                    continue
                parsed, found = _parse(text, rel, stem)
                out[stem] = RecordFile(rel, stem, parsed, tuple(found))
        except OSError as exc:
            out[stem] = bad(rel, stem, f"cannot be read: {exc.strerror or exc}")
    return out


def _jobs(root: Path) -> list[JobRef]:
    out = []
    for path in job_files(root):
        try:
            job = read_job(path)
        except Exception:       # noqa: BLE001 - a broken job file is not a job here
            continue
        out.append(JobRef(job.id, job.status.value, job.status.value in rules.TERMINAL_JOB_STATUSES))
    return out


@dataclass
class Loaded:
    """Everything `round list|show` and the derivations need, read once: the ledger, the record files, the saved jobs and
    the round fold at `now`. Built without writing anything."""
    events: list
    now: str
    files: dict[str, RecordFile]
    fold: ifold.RoundsFold
    jobs: list[JobRef]

    @property
    def flags(self) -> dict[str, RoundFlags]:
        return self.fold.flags

    @cached_property
    def rounds(self) -> dict[str, Round | None]:
        """Every round (an id with an applied `round_added`) -> its complete record, or None when the round is incomplete.
        Orphan records are not here: they feed nothing."""
        out: dict[str, Round | None] = {}
        for rid, fl in self.flags.items():
            file = self.files.get(rid)
            record = file.record if file is not None else None
            out[rid] = record if record is not None and record.id == rid and record.job == fl.job else None
        return out

    @cached_property
    def incomplete(self) -> dict[str, str]:
        """round id -> missing | invalid | mismatched, for every round without a usable record (spec 6.5)."""
        out = {}
        for rid, record in self.rounds.items():
            if record is None:
                file = self.files.get(rid)
                out[rid] = (rules.RECORD_MISSING if file is None else
                            rules.RECORD_INVALID if file.record is None else rules.RECORD_MISMATCHED)
        return out

    def record_status(self, rid: str) -> str:
        return self.incomplete.get(rid, RECORD_OK)

    def record_issues(self) -> list[Issue]:
        """INT001 (record shape), INT002 (orphan record), INT003 (a round whose record file is missing) and INT004 (a
        record whose id or job disagrees with its event or its file name), by path. Read-only."""
        out: list[Issue] = []
        for stem, file in sorted(self.files.items()):
            rec = file.record
            if rec is None:
                out.extend(file.issues)
            elif rec.id != stem:
                out.append(Issue("error", rules.INT004, file.rel,
                                 f"{file.rel} says id {rec.id}, which is not its file name; it feeds nothing",
                                 "rename the file back or restore it; ids are immutable"))
            elif stem in self.flags and rec.job != self.flags[stem].job:
                out.append(Issue("error", rules.INT004, file.rel,
                                 f"{file.rel} says job {rec.job} but its interview.round_added event recorded "
                                 f"{self.flags[stem].job}; it feeds nothing",
                                 "put the job back as the event recorded it; a round's job is immutable"))
            elif stem not in self.flags:
                out.append(Issue("warning", rules.INT002, file.rel,
                                 f"{file.rel} has no interview.round_added event (an interrupted `round add` or a "
                                 "hand-made file); it feeds nothing",
                                 "delete it, or add the round again with `careeros interview round add`"))
        for rid in sorted(set(self.flags) - set(self.files)):
            rel = rel_for(rid)
            out.append(Issue("warning", rules.INT003, rel,
                             f"round {rid} has events but {rel} is missing; its record fields are unavailable: "
                             + RESTORE.format(id=rid), RESTORE.format(id=rid)))
        return out


def load(root: Path, now: str | None = None, events: list | None = None) -> Loaded:
    """Read-only. `ledger.LedgerError` when `ledger.jsonl` has a corrupt line (unless `events` is given)."""
    root = Path(root)
    now = now or models.utc_now()
    events = ledger.read_events(root) if events is None else events
    return Loaded(events, now, _scan(root), ifold.fold_rounds(events, now), _jobs(root))


# --- round list and show -------------------------------------------------------------------------------------------------

def _debrief_dict(d: Debrief | None) -> dict | None:
    if d is None:
        return None
    return {"rating": d.rating, "questions_asked": list(d.questions_asked), "do_differently": list(d.do_differently),
            "next_step": d.next_step, "next_step_date": d.next_step_date}


_HISTORY_KEYS = ("seq", "ts", "type", "actor", "source", "action", "reason", "artifacts")


@dataclass(frozen=True)
class RoundView:
    id: str
    rel: str
    flags: RoundFlags
    record: Round | None            # None when incomplete
    record_status: str              # ok | missing | invalid | mismatched
    needs_update: bool
    problems: tuple[str, ...]
    history: tuple[dict, ...] = ()

    @property
    def label(self) -> str:
        return self.flags.label

    def to_dict(self, history: bool = True) -> dict:
        r, fl = self.record, self.flags
        recorded = fl.debriefed_at is not None
        out = {"id": self.id, "path": self.rel, "job": r.job if r else fl.job, "label": self.label,
               "incomplete": r is None, "record": self.record_status,
               "stage": r.stage if r else None, "format": r.format if r else None,
               "scheduled_for": r.scheduled_for if r else None,
               "interviewers": [{"name": i.name, "title": i.title} for i in r.interviewers] if r else None,
               "debrief": _debrief_dict(r.debrief) if r and recorded else None,
               "unrecorded_debrief": _debrief_dict(r.debrief) if r and not recorded else None,
               "debrief_recorded": recorded,
               "recorded_when": fl.recorded_when,
               "date_unrecorded": r is not None and r.scheduled_for != fl.recorded_when,
               "added_at": fl.added_at, "held_at": fl.held_at, "cancelled_at": fl.cancelled_at,
               "debriefed_at": fl.debriefed_at, "cancel_reason": fl.cancel_reason,
               "needs_debrief": self.label == rules.HELD, "needs_update": self.needs_update,
               "problems": list(self.problems)}
        if history:
            out["history"] = [dict(h) for h in self.history]
        return out


def _view(loaded: Loaded, rid: str) -> RoundView:
    fl = loaded.flags[rid]
    record = loaded.rounds.get(rid)
    file = loaded.files.get(rid)
    problems = [i.message for i in file.issues] if file is not None else []
    status = loaded.record_status(rid)
    if status == rules.RECORD_MISSING:
        problems.append(f"{rel_for(rid)} is missing; " + RESTORE.format(id=rid))
    elif status == rules.RECORD_MISMATCHED:
        problems.append(f"{rel_for(rid)} disagrees with the round's event (id or job edited); it feeds nothing")
    history = tuple({k: e.get(k) for k in _HISTORY_KEYS} for e in loaded.events
                    if isinstance(e, dict) and e.get("entity") == rid and isinstance(e.get("type"), str)
                    and e["type"].startswith(rules.EVENT_PREFIX) and e["type"] not in rules.AUDIT_ONLY_TYPES)
    return RoundView(rid, rel_for(rid), fl, record, status, derive.needs_update(record, fl, loaded.now), tuple(problems),
                     history)


def round_list(root: Path, job: str | None = None, now: str | None = None) -> list[RoundView]:
    """Every round (an id with an applied `interview.round_added`), complete or incomplete, by id; none is left out. An
    orphan record is not a round. `job` narrows to one job (an id, slug or path; `not_found` otherwise). Read-only."""
    root = Path(root)
    job_id = None
    if job is not None:
        try:
            job_id = resolve_job(root, job).id
        except WorkspaceError as exc:
            raise Refused(rules.NOT_FOUND, f"no job {job!r}: {exc}") from None
    loaded = load(root, now)
    views = [_view(loaded, rid) for rid in sorted(loaded.flags)]
    return [v for v in views if job_id is None or v.to_dict(history=False)["job"] == job_id]


def round_show(root: Path, rnd_id: str, now: str | None = None) -> RoundView:
    loaded = load(root, now)
    if rnd_id not in loaded.flags:
        raise Refused(rules.NOT_FOUND, f"no round {rnd_id!r} (a round exists once its interview.round_added event is recorded)")
    return _view(loaded, rnd_id)


# --- the read-only views (spec 6.4) --------------------------------------------------------------------------------------

def _prep(root: Path, events: list) -> dict[str, str]:
    """job id -> prep status, for every readable saved job (the first file wins for a duplicated id)."""
    out: dict[str, str] = {}
    for path in job_files(root):
        try:
            job = read_job(path)
        except Exception:       # noqa: BLE001 - a broken job file is not a job here
            continue
        if job.id not in out:
            out[job.id] = iprep.prep_status(root, path.parent, job.id, events)
    return out


def upcoming(root: Path, days: int = rules.UPCOMING_DEFAULT_DAYS, now: str | None = None) -> list[derive.UpcomingItem]:
    """`interview upcoming`: `derive.build_upcoming` over one load at `now`, with the prep status of each job. Refused
    `invalid_input` for an unusable `days`. Creates no file, event or lock file."""
    root = Path(root)
    loaded = load(root, now)
    try:
        return derive.build_upcoming(loaded.rounds, loaded.flags, loaded.jobs, _prep(root, loaded.events), loaded.now, days)
    except ValueError as exc:
        raise Refused(rules.INVALID_INPUT, str(exc)) from None


def needs_debrief(root: Path, now: str | None = None) -> derive.NeedsDebrief:
    """`interview needs-debrief`: `derive.build_needs_debrief` over one load at `now`. Creates no file, event or lock
    file."""
    loaded = load(Path(root), now)
    return derive.build_needs_debrief(loaded.rounds, loaded.flags, loaded.jobs, loaded.now)


# --- validation (spec 8) -------------------------------------------------------------------------------------------------

_LEDGER = "ledger.jsonl"
_FIX_EVENTS = ("interview events are written only by the `careeros interview` commands; restore ledger.jsonl from "
               ".careeros/backups or version control")
_FIXES = {
    rules.INT007: "nothing is run for you: move the job with `careeros transition`, or record the round's outcome with "
                  "`careeros interview round held|cancel`, whichever is true",
    rules.INT008: "re-run `careeros interview round reschedule <id> --when <date>` with the intended date",
    rules.INT009: "re-run `careeros interview debrief <id>`; it replaces the unrecorded block",
    rules.INT100: "record what happened with `careeros interview round held`, `round cancel` or `round reschedule`",
}
_SEVERITY = {rules.INT007: "warning", rules.INT008: "warning", rules.INT009: "warning", rules.INT100: "info"}


def validate_workspace(root: Path, events: list, now: str | None = None) -> list[Issue]:
    """INT001 to INT009 and INT100 (spec 8). `events` is the lenient ledger read `validate` already holds and `now` its
    one clock read. Read-only; never raises. A workspace with no `rounds/` and no `interview.*` events gives nothing."""
    root = Path(root)
    events = list(events or ())
    has_events = any(isinstance(e, dict) and isinstance(e.get("type"), str) and e["type"].startswith(rules.EVENT_PREFIX)
                     for e in events)
    try:
        if not has_events and not os.path.lexists(root / ROUNDS_DIR):
            return []
        return _validate(root, events, now or models.utc_now())
    except Exception as exc:    # noqa: BLE001 - validate reports, it never crashes
        return [Issue("error", rules.INT001, ROUNDS_DIR, f"interview rounds could not be checked: {exc!r}", c.FIX)]


def _validate(root: Path, events: list, now: str) -> list[Issue]:
    loaded = load(root, now, events)
    issues = loaded.record_issues()                                     # INT001 to INT004 by path
    # INT004: one round id under two jobs (the fold applies the first round_added and reports the second as INT005)
    jobs_of: dict[str, list[str]] = {}
    for event in events:
        if isinstance(event, dict) and event.get("type") == rules.EV_ROUND_ADDED and ids.is_valid_id("round", event.get("entity")):
            job, _, problem, _ = ifold._round_added_shape(event)
            if problem is None and job not in jobs_of.setdefault(event["entity"], []):
                jobs_of[event["entity"]].append(job)
    for rid, jobs in sorted(jobs_of.items()):
        if len(jobs) > 1:
            issues.append(Issue("error", rules.INT004, _LEDGER,
                                f"round {rid} has interview.round_added events under {len(jobs)} jobs ({', '.join(jobs)}); "
                                "only the first one counts", _FIX_EVENTS))
    for error in loaded.fold.errors:                                    # INT005
        issues.append(Issue("error", rules.INT005, _LEDGER, f"event {error.seq}: {error.message}", _FIX_EVENTS))
    saved = {j.id for j in loaded.jobs}
    for rid, fl in sorted(loaded.flags.items()):                        # INT006
        if fl.job not in saved:
            issues.append(Issue("warning", rules.INT006, rel_for(rid), f"round {rid} is for job {fl.job}, which is not saved",
                                "restore the job's directory from .careeros/backups or version control; a round's job is "
                                "immutable"))
    found = [*derive.status_issues(loaded.rounds, loaded.flags, loaded.jobs, loaded.now),     # INT007, INT100
             *derive.record_issues(loaded.rounds, loaded.flags)]                              # INT008, INT009
    for code, rid, message in sorted(found, key=lambda t: (t[1], t[0])):
        issues.append(Issue(_SEVERITY[code], code, rel_for(rid), message, _FIXES[code]))
    return issues


# --- state-changing operations ------------------------------------------------------------------------------------------

@dataclass
class _Op:
    root: Path
    actor: str
    now: str
    events: list
    floor: str | None
    loaded: Loaded | None = None


@dataclass(frozen=True)
class Added:
    id: str
    rel: str
    hash: str
    job: str
    scheduled_for: str


@dataclass(frozen=True)
class Changed:
    id: str
    outcome: str                    # held | cancelled | rescheduled | debriefed
    label: str                      # the round's label after the event
    event: dict
    rel: str | None = None
    hash: str | None = None
    scheduled_for: str | None = None


def _check_actor(actor: object) -> None:
    if not isinstance(actor, str) or not ledger._ACTOR_RE.match(actor):
        raise Refused(rules.INVALID_INPUT, f"invalid actor {actor!r}: use 'user', 'system' or 'agent:<name>'")


def _begin(root: Path, actor: str, clock: Callable[[], str] | None, what: str) -> _Op:
    """Under the lock: writable workspace, an intact chain, the one clock read, the ledger and the loaded rounds."""
    ensure_writable(root)
    broken = ledger.verify_chain(root)
    if broken:
        raise Refused(rules.LEDGER_CORRUPT, f"the ledger's hash chain is broken ({broken[0].message}); nothing was {what}. "
                                            "Restore ledger.jsonl from .careeros/backups or version control")
    now = (clock or models.utc_now)()       # the one clock read, under the lock
    events = ledger.read_events(root)
    op = _Op(root, actor, now, events, afold.clock_floor(events))
    if afold.parse_ts(now) is None:
        _refuse(op, None, what, rules.CLOCK_SKEW, f"the system clock gives an unreadable time {now!r}; nothing was {what}")
    op.loaded = load(root, now, events)
    return op


def _refuse(op: _Op, entity: str | None, operation: str, code: str, message: str):
    """Append an `interview.rejected` for this EXISTING round, unless the round's latest event of any type is an identical
    rejection (nothing for an unknown round), then raise `Refused`. Audit only: no fold reads it. A failed audit write
    never turns the refusal into something else."""
    if entity is not None:
        fields = ifold.rejected_fields(operation, entity, code, message)
        last = next((e for e in reversed(op.events) if isinstance(e, dict) and e.get("entity") == entity), None)
        duplicate = (last is not None and last.get("type") == rules.EV_REJECTED and last.get("reason") == fields["reason"]
                     and last.get("action") == fields["action"] and last.get("actor") == op.actor)
        if not duplicate:
            try:
                ledger.append_events(op.root, [{"type": rules.EV_REJECTED, "actor": op.actor, "entity": entity,
                                                "source": "cli", **fields}])
            except (OSError, WorkspaceError):
                pass
    raise Refused(code, message)


def _skew(op: _Op, entity: str | None, operation: str) -> None:
    if afold.clock_skewed(None, op.now, op.floor):
        _refuse(op, entity, operation, rules.CLOCK_SKEW,
                f"the system clock is earlier than the latest recorded event ({op.floor}); nothing was recorded. Fix the clock; "
                "if that event is future-dated by a wrong clock, restore ledger.jsonl from .careeros/backups")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_then_append(root: Path, path: Path, data: bytes, specs: list[dict]) -> list[dict]:
    directory = path.parent
    made_dir = not directory.exists()
    tx = Transaction()
    try:
        tx.write(path, data)
        return ledger.append_events(root, specs)
    except BaseException:
        tx.rollback()
        if made_dir:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _text(op: _Op, entity: str | None, operation: str, value: object, name: str, *, allow_empty: bool = False) -> str:
    problems: list[str] = []
    got = c.text_value(value, name, problems, allow_empty=allow_empty)
    if got is None:
        _refuse(op, entity, operation, rules.INVALID_INPUT, "; ".join(problems))
    return got


def _encode(op: _Op, entity: str | None, operation: str, record: Round) -> bytes:
    """The record's bytes, refused `invalid_input` unless they parse back to exactly `record`."""
    try:
        data = rround.to_bytes(record)
    except (ValueError, TypeError) as exc:
        _refuse(op, entity, operation, rules.INVALID_INPUT, f"the record cannot be stored: {exc}")
    rel = rel_for(record.id)
    again, issues = rround.parse(data.decode("utf-8"), rel)
    if issues or again != record:
        _refuse(op, entity, operation, rules.INVALID_INPUT,
                "the record is not valid: " + ("; ".join(i.message for i in issues) or "it contains text that cannot be stored"))
    return data


def _known(op: _Op, operation: str, rnd_id: object) -> RoundFlags:
    """The round's flags, after the id shape and the clock checks; `not_found` (nothing appended) for an unknown id."""
    if not ids.is_valid_id("round", rnd_id):
        _refuse(op, None, operation, rules.INVALID_INPUT, f"{rnd_id!r} is not a round id (rnd_...)")
    flags = op.loaded.flags.get(rnd_id)
    _skew(op, rnd_id if flags is not None else None, operation)
    if flags is None:
        _refuse(op, None, operation, rules.NOT_FOUND,
                f"no round {rnd_id!r}: a round exists once its interview.round_added event is recorded")
    return flags


def _source(op: _Op, rnd_id: str, operation: str, source: object) -> str:
    if source not in rules.SOURCES:
        _refuse(op, rnd_id, operation, rules.INVALID_INPUT, f"--source must be one of {', '.join(rules.SOURCES)}")
    return source


def _transition(op: _Op, rnd_id: str, operation: str, flags: RoundFlags, etype: str, **kw) -> RoundFlags:
    new = ifold.apply_event(flags, etype, op.now, **kw)
    if isinstance(new, ifold.Refusal):
        _refuse(op, rnd_id, operation, rules.WRONG_STATE,
                f"{rnd_id} is {new.state}; {operation} is not legal from there (held and cancel need a scheduled round, "
                "reschedule a scheduled one, debrief a held one; each happens once and nothing follows cancelled)")
    return new


def _record_of(op: _Op, rnd_id: str, operation: str) -> Round:
    record = op.loaded.rounds.get(rnd_id)
    if record is None:
        status = op.loaded.record_status(rnd_id)
        _refuse(op, rnd_id, operation, rules.RECORD_UNAVAILABLE,
                f"{rel_for(rnd_id)} is {status}, so there is nothing to write into; " + RESTORE.format(id=rnd_id))
    return record


def _when(op: _Op, entity: str | None, operation: str, when: object) -> str:
    if not isinstance(when, str) or c.parse_when(when) is None:
        _refuse(op, entity, operation, rules.INVALID_INPUT,
                f"--when {when!r} must be an ISO date like 2026-11-05 or a UTC datetime like 2026-11-05T15:00:00Z")
    return when


def round_add(root: Path, *, job: str, stage: str, format: str, when: str,
              interviewers: Sequence[tuple[str, str]] = (), actor: str,
              clock: Callable[[], str] | None = None) -> Added:
    """Create `rounds/<rnd_id>.md` and append `interview.round_added` (file first, event second, one transaction). Refuses
    a job in a terminal status (`wrong_state`) and a live (not cancelled) complete round with the same job, stage and
    `scheduled_for` value (`duplicate`, audited on that round). Raises `Refused`."""
    root = Path(root)
    operation = "round add"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "added")
        _skew(op, None, operation)
        try:
            saved = resolve_job(root, job)
        except WorkspaceError as exc:
            _refuse(op, None, operation, rules.NOT_FOUND, f"no job {job!r}: {exc}")
        if saved.status.value in rules.TERMINAL_JOB_STATUSES:
            _refuse(op, None, operation, rules.WRONG_STATE,
                    f"job {saved.id} is {saved.status.value}; a round cannot be added to a job in a terminal status")
        if stage not in rules.STAGES:
            _refuse(op, None, operation, rules.INVALID_INPUT, f"--stage must be one of {', '.join(rules.STAGES)}")
        if format not in rules.FORMATS:
            _refuse(op, None, operation, rules.INVALID_INPUT, f"--format must be one of {', '.join(rules.FORMATS)}")
        when = _when(op, None, operation, when)
        people = list(interviewers or ())
        if len(people) > rules.MAX_INTERVIEWERS:
            _refuse(op, None, operation, rules.INVALID_INPUT, f"at most {rules.MAX_INTERVIEWERS} --interviewer entries")
        made = tuple(Interviewer(_text(op, None, operation, name, "--interviewer name"),
                                 _text(op, None, operation, title, "--interviewer title", allow_empty=True))
                     for name, title in people)
        L = op.loaded
        for rid in sorted(L.flags):
            rec = L.rounds[rid]
            if (L.flags[rid].label != rules.CANCELLED and rec is not None and rec.job == saved.id and rec.stage == stage
                    and rec.scheduled_for == when):
                _refuse(op, rid, operation, rules.DUPLICATE,
                        f"round {rid} is already a live {stage} round for {saved.id} on {when}; cancel it first if this "
                        "is a different interview")
        rnd_id = ids.new_id("round", set(L.flags) | set(L.files))
        path = root / rel_for(rnd_id)
        if path.exists() or path.is_symlink():
            _refuse(op, None, operation, rules.DUPLICATE, f"{rel_for(rnd_id)} already exists; nothing was written")
        record = Round(rnd_id, saved.id, stage, format, when, made)
        data = _encode(op, None, operation, record)
        digest = _sha(data)
        spec = {"type": rules.EV_ROUND_ADDED, "actor": actor, "entity": rnd_id, "source": "cli",
                **ifold.round_added_fields(rel_for(rnd_id), digest, saved.id, when, stage=stage)}
        _write_then_append(root, path, data, [spec])
        return Added(rnd_id, rel_for(rnd_id), digest, saved.id, when)


def round_held(root: Path, rnd_id: str, *, source: str = "user", actor: str,
               clock: Callable[[], str] | None = None) -> Changed:
    """Append `interview.held` (the round took place). Needs a scheduled round; works on an incomplete round and after the
    job reached a terminal status."""
    root = Path(root)
    operation = "round held"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "recorded")
        flags = _known(op, operation, rnd_id)
        source = _source(op, rnd_id, operation, source)
        new = _transition(op, rnd_id, operation, flags, rules.EV_HELD)
        (event,) = ledger.append_events(root, [{"type": rules.EV_HELD, "actor": actor, "entity": rnd_id, "source": source,
                                                "prev_state": flags.label, "new_state": new.label,
                                                "action": f"held round {rnd_id}"}])
        return Changed(rnd_id, "held", new.label, event)


def round_cancel(root: Path, rnd_id: str, *, reason: str, source: str = "user", actor: str,
                 clock: Callable[[], str] | None = None) -> Changed:
    """Append `interview.cancelled` with its reason. Needs a scheduled round; cancelled is terminal."""
    root = Path(root)
    operation = "round cancel"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "recorded")
        flags = _known(op, operation, rnd_id)
        source = _source(op, rnd_id, operation, source)
        reason = _text(op, rnd_id, operation, reason.strip() if isinstance(reason, str) else reason, "--reason")
        new = _transition(op, rnd_id, operation, flags, rules.EV_CANCELLED, reason=reason)
        (event,) = ledger.append_events(root, [{"type": rules.EV_CANCELLED, "actor": actor, "entity": rnd_id,
                                                "source": source, "prev_state": flags.label, "new_state": new.label,
                                                "reason": reason, "action": f"cancelled round {rnd_id}"}])
        return Changed(rnd_id, "cancelled", new.label, event)


def round_reschedule(root: Path, rnd_id: str, *, when: str, actor: str,
                     clock: Callable[[], str] | None = None) -> Changed:
    """Rewrite the record's `scheduled_for` (keeping everything else, the body included), then append
    `interview.rescheduled`. Needs a scheduled, complete round. Allowed when the record already holds `when` (the recovery
    of an interrupted reschedule, spec 7.1): identical bytes are rewritten and the event is appended."""
    root = Path(root)
    operation = "round reschedule"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "rescheduled")
        flags = _known(op, operation, rnd_id)
        when = _when(op, rnd_id, operation, when)
        new = _transition(op, rnd_id, operation, flags, rules.EV_RESCHEDULED, when=when)
        record = rround.with_when(_record_of(op, rnd_id, operation), when)
        data = _encode(op, rnd_id, operation, record)
        spec = {"type": rules.EV_RESCHEDULED, "actor": actor, "entity": rnd_id, "source": "cli",
                "prev_state": flags.label, "new_state": new.label, **ifold.rescheduled_fields(when)}
        (event,) = _write_then_append(root, root / rel_for(rnd_id), data, [spec])
        return Changed(rnd_id, "rescheduled", new.label, event, rel_for(rnd_id), _sha(data), when)


def debrief(root: Path, rnd_id: str, *, rating: str, asked: Sequence[str] = (), improve: Sequence[str] = (),
            next_step: str, next_date: str | None = None, actor: str,
            clock: Callable[[], str] | None = None) -> Changed:
    """Write the debrief block into the record (replacing a block that has no `debriefed` event, spec 7.1), then append
    `interview.debriefed` with the record's path and hash. Needs a held, complete round. The rating is the user's own words
    mapped to the enum by the caller; nothing here invents one."""
    root = Path(root)
    operation = "debrief"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "debriefed")
        flags = _known(op, operation, rnd_id)
        if rating not in rules.RATINGS:
            _refuse(op, rnd_id, operation, rules.INVALID_INPUT, f"--rating must be one of {', '.join(rules.RATINGS)}")
        lists = []
        for name, items in (("--asked", asked), ("--improve", improve)):
            items = list(items or ())
            if len(items) > rules.MAX_DEBRIEF_ITEMS:
                _refuse(op, rnd_id, operation, rules.INVALID_INPUT, f"at most {rules.MAX_DEBRIEF_ITEMS} {name} entries")
            lists.append(tuple(_text(op, rnd_id, operation, v, name) for v in items))
        step = _text(op, rnd_id, operation, next_step, "--next-step")
        if next_date is not None:
            problems: list[str] = []
            if c.date_value(next_date, "--next-date", problems) is None:
                _refuse(op, rnd_id, operation, rules.INVALID_INPUT, "; ".join(problems))
        new = _transition(op, rnd_id, operation, flags, rules.EV_DEBRIEFED)
        record = rround.with_debrief(_record_of(op, rnd_id, operation),
                                     Debrief(rating, lists[0], lists[1], step, next_date))
        data = _encode(op, rnd_id, operation, record)
        digest = _sha(data)
        spec = {"type": rules.EV_DEBRIEFED, "actor": actor, "entity": rnd_id, "source": "cli",
                "prev_state": flags.label, "new_state": new.label, "action": f"debriefed round {rnd_id} ({rating})",
                "artifacts": [rel_for(rnd_id), f"sha256:{digest}"]}
        (event,) = _write_then_append(root, root / rel_for(rnd_id), data, [spec])
        return Changed(rnd_id, "debriefed", new.label, event, rel_for(rnd_id), digest)
