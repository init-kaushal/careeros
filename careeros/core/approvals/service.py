"""The approvals service: `propose`, `list_items`, `show`, the decisions, claims and recovery (`decide`, `queue`,
`begin`, `done`, `resolve`) and the read-only `validate_workspace` (spec sections 5, 6, 8, 9).

`propose` writes the item file first and the ledger events second, under the workspace lock, as one transaction: the file
is put back on any exception (`append_events` restores the ledger itself). A process kill between the two leaves an
orphan file, which `APR002` reports and nothing treats as actionable. The `approval.proposed` event is written only via
`fold.proposal_event_fields`, together with one `approval.superseded` per earlier pending or approved item of the same
kind and target, in one batch. Persons have no records: a person is identified by the canonical profile URL, and the
`per_` id is allocated for the first proposal of a URL and reused by every later one. Identity comes from the ledger (the
URL on the `approval.proposed` event); an orphan file with no proposal event is not an item and does not count.

`decide` and `queue` (the user, interactive), `begin` and `done` (the agent) and `resolve` (the user, interactive)
change state. Each takes the workspace lock, verifies the hash chain (`ledger_corrupt`, nothing written) and folds the
ledger before deciding anything. Each reads the clock ONCE, after taking the lock, through the `clock` callable its caller
passes (`models.utc_now` by default), and refuses `clock_skew` when that time is earlier than the item's approval, its
latest event or the ledger's clock floor (the latest `ts` of any event). As in `approve_job`, a decision does not hold
the lock while the user decides and re-checks everything under the lock before writing, keeping its phase-1 time and
floor; each queue item is its own operation. A refusal of an existing item appends an audit-only, deduped
`approval.rejected`; an id that names no item appends nothing.

`list_items` and `show` fold the ledger and read the item files; `validate_workspace` does the same and adds the `APR`
codes. None of the three takes the lock or writes anything. Each takes `now` once and passes it down; every
`clock_skewed` call uses `ItemState.last_ts`. Item files and the folded events are cross-checked: the event's kind,
target and hash must match the file, so an edited `action` text cannot move an item to another bucket. This is a
recorded decision, not a security boundary.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from careeros.core import ids, ledger, models
from careeros.core.approvals import fold, item as item_mod, manifest, people, render, rules
from careeros.core.approvals.model import Item, ItemState, ManifestEntry, Target
from careeros.core.memory.store import Transaction
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError, WorkspaceLock, ensure_writable, job_files, read_job, resolve_job

APPROVALS_DIR = "approvals"
INVALID_INPUT = "invalid_input"
LEDGER_CORRUPT = rules.LEDGER_CORRUPT
MAX_ITEM_BYTES = 1 << 20
ITEM_INVALID = "item_invalid"           # list/show problem codes that are not refusal codes
EVENT_MISMATCH = "event_mismatch"
SEQUENCE_ERROR = "sequence_error"
CHANNEL_PERSON = "linkedin"
CHANNEL_SUBMIT = "application"
_PERSON_KINDS = (rules.LINKEDIN_INVITE, rules.LINKEDIN_MESSAGE)
_TERMINAL = (rules.CONSUMED, rules.DECLINED, rules.EXPIRED, rules.SUPERSEDED, rules.VOIDED)


class Refused(WorkspaceError):
    """A refusal (exit 1): `code` is a stable code from `rules.REFUSAL_CODES`, or `invalid_input`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Proposed:
    id: str
    rel: str
    hash: str
    superseded: tuple[str, ...] = ()
    target_id: str = ""


@dataclass(frozen=True)
class View:
    """One item as list and show present it. `state` is the derived state; `problems` lists why the item is not usable."""
    id: str
    rel: str
    state: str
    kind: str | None
    target_id: str | None
    hash: str | None
    proposed_at: str | None
    approved_at: str | None
    expires_at: str | None
    problems: tuple[str, ...]
    clock_skew: bool
    item: Item | None = None

    @property
    def usable(self) -> bool:
        return not self.problems

    def to_dict(self, *, content: bool = False) -> dict:
        target = None
        if self.item is not None:
            t = self.item.target
            target = {"id": t.id, "channel": t.channel, "label": t.label, "url": t.url}
        out = {"id": self.id, "path": self.rel, "state": self.state, "kind": self.kind,
               "target": target or {"id": self.target_id}, "hash": self.hash,
               "hash_prefix": self.hash[:12] if self.hash else None, "proposed_at": self.proposed_at,
               "approved_at": self.approved_at, "expires_at": self.expires_at, "problems": list(self.problems),
               "usable": self.usable, "clock_skew": self.clock_skew}
        if content:
            out["content"] = None if self.item is None else self.item.content
            out["manifest"] = [] if self.item is None else [{"path": m.path, "sha256": m.sha256} for m in self.item.manifest]
        return out


# --- reading the approvals directory -------------------------------------------------------------------------------

@dataclass(frozen=True)
class _File:
    rel: str
    item: Item | None
    issues: tuple[Issue, ...]


def _scan(root: Path) -> dict[str, _File]:
    """Every `approvals/*.md`, parsed, keyed by file stem. Symlinks, non-regular files and files over `MAX_ITEM_BYTES` are
    APR001 and never read whole. Never raises, never writes."""
    out: dict[str, _File] = {}
    base = Path(root) / APPROVALS_DIR
    try:
        paths = sorted(base.glob("*.md"))
    except OSError:
        return out

    def bad(rel: str, why: str) -> _File:
        return _File(rel, None, (Issue("error", rules.APR001, rel, f"{rel} {why}", item_mod.FIX),))

    for path in paths:
        rel = f"{APPROVALS_DIR}/{path.name}"
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                out[path.stem] = bad(rel, "is a symlink: an approval item must be a regular file")
            elif not stat.S_ISREG(mode):
                out[path.stem] = bad(rel, "is not a regular file")
            else:
                with path.open("rb") as handle:
                    raw = handle.read(MAX_ITEM_BYTES + 1)
                if len(raw) > MAX_ITEM_BYTES:
                    out[path.stem] = bad(rel, f"is too large (more than {MAX_ITEM_BYTES} bytes)")
                else:
                    parsed, found = item_mod.parse_bytes(raw, rel)
                    out[path.stem] = _File(rel, parsed, tuple(found))
        except OSError as exc:
            out[path.stem] = bad(rel, f"cannot be read: {exc.strerror or exc}")
    return out


def _expected_rel(item_id: str) -> str:
    return f"{APPROVALS_DIR}/{item_id}.md"


def _expires_at(st: ItemState) -> str | None:
    approved = fold.parse_ts(st.approved_at)
    if approved is None or st.state is None or st.state.value not in (rules.APPROVED, rules.EXPIRED, rules.IN_FLIGHT):
        return None
    return (approved + timedelta(seconds=rules.TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mismatches(item_id: str, st: ItemState, file: _File | None) -> list[str]:
    """Problem codes from comparing the folded events with the item file."""
    problems: list[str] = []
    if st.errors:
        problems.append(SEQUENCE_ERROR)
    if st.rel is not None and st.rel != _expected_rel(item_id):
        problems.append(EVENT_MISMATCH)
    if file is None:
        problems.append(rules.CONTENT_MISSING)
    elif file.item is None:
        problems.append(ITEM_INVALID)
    else:
        if st.action_hash is not None and item_mod.action_hash(file.item) != st.action_hash:
            problems.append(rules.HASH_MISMATCH)
        if (st.kind, st.target_id, st.url) != (file.item.kind, file.item.target.id, file.item.target.url):
            problems.append(EVENT_MISMATCH)
    return list(dict.fromkeys(problems))


def _view(item_id: str, st: ItemState, file: _File | None, now: str, floor: str | None = None) -> View:
    skew = clock_skew_of(st, now, floor)
    problems = _mismatches(item_id, st, file) + ([rules.CLOCK_SKEW] if skew else [])
    return View(item_id, _expected_rel(item_id), st.state.value if st.state else "unknown", st.kind, st.target_id,
                st.action_hash, st.proposed_at, st.approved_at, _expires_at(st), tuple(problems), skew,
                file.item if file else None)


clock_floor, clock_skew_of = fold.clock_floor, fold.clock_skew_of     # kept here for the callers that import them from the service


def list_items(root: Path, status: str | None = None, now: str | None = None) -> list[View]:
    """Every item that has an `approval.proposed` event, in proposal order; read-only. Orphan files are not listed."""
    if status is not None and status not in rules.STATES:
        raise WorkspaceError(f"unknown status {status!r}: use one of {', '.join(rules.STATES)}")
    now = now or models.utc_now()
    files = _scan(root)
    events = ledger.read_events(root)
    result, floor = fold.fold_events(events, now), clock_floor(events)
    views = [_view(i, st, files.get(i), now, floor) for i, st in result.items.items() if st.state is not None]
    return [v for v in views if status is None or v.state == status]


def show(root: Path, item_id: str, now: str | None = None) -> View:
    """One item with its content; read-only. `Refused(not_found)` when no proposal event names it (an orphan file too)."""
    now = now or models.utc_now()
    files = _scan(root)
    events = ledger.read_events(root)
    st = fold.fold_events(events, now).items.get(item_id)
    if st is None or st.state is None:
        raise Refused(rules.NOT_FOUND, f"no approval item {item_id!r} (an item exists only once its approval.proposed event is recorded)")
    return _view(item_id, st, files.get(item_id), now, clock_floor(events))


# --- propose -------------------------------------------------------------------------------------------------------------

def _header_problem(value: str, name: str) -> None:
    problem = item_mod.header_text_problem(value, name)
    if problem:
        raise Refused(INVALID_INPUT, problem)


def propose(root: Path, *, kind: str, person_url: str | None, job_ref: str | None, label: str | None, content_text: str,
            manifest_paths: list[str], actor: str) -> Proposed:
    """Create an item: validate, write `approvals/<apr_id>.md`, append the proposal (and supersessions) in one batch.
    Raises `Refused` for a refusal and `WorkspaceError` for a misuse of the arguments; nothing is written either way."""
    root = Path(root)
    if kind not in rules.KINDS:
        raise WorkspaceError(f"unknown kind {kind!r}: use one of {', '.join(rules.KINDS)}")
    person = kind in _PERSON_KINDS
    if person and (not person_url or job_ref):
        raise WorkspaceError(f"{kind} needs --person-url and no --job")
    if not person and (not job_ref or person_url):
        raise WorkspaceError(f"{kind} needs --job and no --person-url")
    if manifest_paths and person:
        raise WorkspaceError(f"{kind} takes no manifest")
    with WorkspaceLock(root):
        ensure_writable(root)
        if person:
            url = item_mod.canonical_profile_url(person_url)
            if url is None:
                raise Refused(INVALID_INPUT, f"{person_url!r} is not a LinkedIn profile URL: use https://www.linkedin.com/in/<slug>")
            default_label, channel = url, CHANNEL_PERSON
        else:
            job = resolve_job(root, job_ref)       # an unresolvable ref is a usage error (exit 2), like `comp`
            url, default_label, channel = None, f"{job.company}, {job.title}", CHANNEL_SUBMIT
        label = default_label if label is None else label
        _header_problem(label, "label")
        if not label.strip():
            raise Refused(INVALID_INPUT, "the target label is empty")
        content = item_mod.normalize_content(content_text)
        if not content.strip():
            raise Refused(INVALID_INPUT, "the content is empty")
        entries: tuple[ManifestEntry, ...] = ()
        if not person:
            try:
                entries = manifest.hash_files(root, manifest_paths)
            except manifest.ManifestError as exc:
                raise Refused(exc.code, f"manifest: {exc}") from None

        _chain_ok(root, "proposed")
        now = models.utc_now()          # the one clock read, under the lock
        events = ledger.read_events(root)
        floor = clock_floor(events)
        if fold.clock_skewed(None, now, floor):
            raise Refused(rules.CLOCK_SKEW, _skew_message(floor, "proposed"))
        states = fold.fold_all(events, now)
        files = _scan(root)
        parsed = [f.item for f in files.values() if f.item is not None]
        # identity comes from the LEDGER (every proposal event and every `outreach.person_added` event, whatever the state or
        # the file); files are a cross-check. An inconsistent map fails closed: no winner is picked and nothing is written.
        by_url, map_errors = people.person_urls(events)
        conflicts = people.identity_problems(events) + map_errors if person else []
        if conflicts:
            raise Refused(rules.IDENTITY_CONFLICT, "the person identity map in the ledger is inconsistent: " + "; ".join(conflicts[:3])
                          + ". Restore ledger.jsonl from .careeros/backups or version control; nothing was proposed")
        taken_people = {i.target.id for i in parsed if i.target.url is not None} | set(by_url.values())
        taken_people |= {st.target_id for st in states.values() if st.target_id and st.target_id.startswith("per_")}
        if person:
            target_id = by_url.get(url) or ids.new_id("person", taken_people)
        else:
            target_id = job.id

        same = [(i, st) for i, st in states.items() if st.state is not None and st.kind == kind and st.target_id == target_id]
        for i, st in same:
            if st.state.value == rules.IN_FLIGHT:
                raise Refused(rules.IN_FLIGHT_CONFLICT,
                              f"{i} is in flight for the same {kind} target: it may already have been acted on. "
                              f"Ask the user to resolve it (careeros approval resolve {i}) before proposing again")
        consumed = [i for i, st in same if st.state.value == rules.CONSUMED]
        if consumed and kind == rules.LINKEDIN_INVITE:
            raise Refused(rules.INVITE_CONFLICT, f"{consumed[0]} already sent an invite to this person: one invite per person")
        if consumed and kind == rules.APPLICATION_SUBMIT:
            raise Refused(rules.ALREADY_SUBMITTED, f"{consumed[0]} already submitted the application for this job")
        superseded = [(i, st) for i, st in same if st.state.value in (rules.PENDING, rules.APPROVED)]

        new_id = ids.new_id("approval", set(files) | set(states))
        rel = _expected_rel(new_id)
        built = Item(new_id, kind, Target(target_id, channel, label, url), now, content, entries)
        try:
            data = item_mod.to_bytes(built)
        except ValueError as exc:
            raise Refused(INVALID_INPUT, f"the item cannot be stored: {exc}") from None
        again, problems = item_mod.parse_bytes(data, rel)
        if problems or again != built:
            raise Refused(INVALID_INPUT, "the item does not survive a write and read back: "
                          + ("; ".join(i.message for i in problems) or "the text contains characters that cannot be stored"))
        digest = item_mod.action_hash(built)
        specs = [{"type": rules.EV_PROPOSED, "actor": actor, "entity": new_id, "source": "cli", "new_state": rules.PENDING,
                  **fold.proposal_event_fields(kind, target_id, rel, digest, url)}]
        specs += [{"type": rules.EV_SUPERSEDED, "actor": actor, "entity": i, "source": "cli", "prev_state": st.state.value,
                   "new_state": rules.SUPERSEDED, "reason": f"superseded by {new_id}", "action": f"superseded by {new_id}"}
                  for i, st in superseded]
        directory = root / APPROVALS_DIR
        made_dir = not directory.exists()
        tx = Transaction()
        try:
            tx.write(root / rel, data)
            ledger.append_events(root, specs)
        except BaseException:
            tx.rollback()
            if made_dir:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        return Proposed(new_id, rel, digest, tuple(i for i, _ in superseded), target_id)


# --- decisions, claims and recovery ----------------------------------------------------------------------------------------

JOB_NOT_AWAITING = rules.JOB_NOT_AWAITING_APPROVAL
_NEEDS = {rules.OP_APPROVE: rules.PENDING, rules.OP_DECLINE: rules.PENDING, rules.OP_BEGIN: rules.APPROVED,
          rules.OP_DONE: rules.IN_FLIGHT, rules.OP_RELEASE: rules.IN_FLIGHT}


@dataclass(frozen=True)
class Decision:
    id: str
    outcome: str                 # approved | declined | skipped | changed (queue: no longer pending at its turn)
    events: tuple[dict, ...] = ()


@dataclass(frozen=True)
class QueueResult:
    id: str
    outcome: str                 # approved | declined | skipped | changed | refused
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class Started:
    id: str
    kind: str
    target_id: str
    rel: str
    hash: str
    item: Item
    event: dict


@dataclass(frozen=True)
class Done:
    id: str
    event: dict


@dataclass(frozen=True)
class Resolved:
    id: str
    outcome: str                 # consumed | released | expired (released, past its expiry) | voided
    reason: str | None
    event: dict
    expires_at: str | None = None


@dataclass(frozen=True)
class _Ctx:
    events: list
    states: dict[str, ItemState]
    st: ItemState
    file: _File | None
    floor: str | None


def _skew_message(ts: str | None, what: str) -> str:
    return (f"the system clock is earlier than the latest recorded event ({ts}); nothing was {what}. Fix the clock; "
            "if that event is future-dated by a wrong clock, restore ledger.jsonl from .careeros/backups")


def _chain_ok(root: Path, what: str) -> None:
    broken = ledger.verify_chain(root)
    if broken:
        raise Refused(LEDGER_CORRUPT, f"the ledger's hash chain is broken ({broken[0].message}); nothing was {what}. "
                                      "Restore ledger.jsonl from .careeros/backups or version control")


def _load(root: Path, item_id: str, now: str, what: str) -> _Ctx:
    """Under the lock: writable workspace, intact chain, then the folded state of every item. `not_found` (nothing is
    written) when no proposal event names `item_id`."""
    ensure_writable(root)
    _chain_ok(root, what)
    events = ledger.read_events(root)
    states = fold.fold_all(events, now)
    st = states.get(item_id)
    if st is None or st.state is None:
        raise Refused(rules.NOT_FOUND, f"no approval item {item_id!r} (an item exists only once its approval.proposed event is recorded)")
    return _Ctx(events, states, st, _scan(root).get(item_id), clock_floor(events))


def _refuse(root: Path, ctx: _Ctx, item_id: str, operation: str, actor: str, source: str, code: str, message: str):
    """Append an `approval.rejected` for this EXISTING item, unless the item's latest event of any type is an identical
    rejection, then raise `Refused`. Audit only: no fold, gate or check reads it. A failed audit write never turns the
    refusal into something else."""
    reason, action = f"{code}: {message}", f"rejected {operation} of {item_id}"
    last = next((e for e in reversed(ctx.events) if isinstance(e, dict) and e.get("entity") == item_id), None)
    duplicate = (last is not None and last.get("type") == rules.EV_REJECTED and last.get("reason") == reason
                 and last.get("action") == action and last.get("actor") == actor)
    if not duplicate:
        try:
            ledger.append_events(root, [{"type": rules.EV_REJECTED, "actor": actor, "entity": item_id, "action": action,
                                         "reason": reason, "source": source}])
        except (OSError, WorkspaceError):
            pass
    raise Refused(code, message)


def _state_problem(item_id: str, st: ItemState, operation: str, now: str, floor: str | None) -> tuple[str, str] | None:
    """The item's history is legal, the table allows `operation` from the effective state, and the clock is not behind
    the item's approval, its latest event (`ItemState.last_ts`) or the ledger's clock floor (strictly: equal is fine)."""
    state = st.state.value
    if st.errors:
        return rules.WRONG_STATE, f"{item_id} is {state} but its event history is illegal (careeros validate reports APR005)"
    got = fold.apply_transition(st.state, operation)
    if isinstance(got, fold.Refusal):
        if got.code == rules.EXPIRED_CODE:
            return got.code, (f"{item_id} expired at {_expires_at(st)}: an approval lasts {rules.APPROVAL_TTL_HOURS} hours; "
                              "propose it again")
        return got.code, f"{item_id} is {state}, not {_NEEDS[operation]}"
    if clock_skew_of(st, now, floor):
        latest = max(t for t in (st.approved_at, st.last_ts, floor) if fold.parse_ts(t) is not None)
        return rules.CLOCK_SKEW, _skew_message(latest, "recorded")
    return None


def _content_problem(item_id: str, st: ItemState, file: _File | None) -> tuple[str, str] | None:
    """The item file is the one the proposal names and still holds exactly what was proposed (the ledger is
    authoritative for kind, target and hash; the file is the cross-check and the content)."""
    rel = _expected_rel(item_id)
    if st.rel is not None and st.rel != rel:
        return rules.HASH_MISMATCH, f"the proposal event of {item_id} names another file ({st.rel})"
    if file is None:
        return rules.CONTENT_MISSING, f"{rel} is missing: the approved content cannot be checked"
    it = file.item
    if (it is None or it.id != item_id or (it.kind, it.target.id, it.target.url) != (st.kind, st.target_id, st.url)
            or item_mod.action_hash(it) != st.action_hash):
        return rules.HASH_MISMATCH, f"{rel} differs from what was proposed (the action hash no longer matches)"
    return None


def _manifest_problem(root: Path, it: Item) -> tuple[str, str] | None:
    if it.kind != rules.APPLICATION_SUBMIT:
        return None
    try:
        manifest.verify(root, it.manifest)
    except manifest.ManifestError as exc:
        return exc.code, f"manifest: {exc}"
    return None


def _others(item_id: str, st: ItemState, states: dict[str, ItemState], state: str) -> list[str]:
    """Other items of the same kind and target (ledger buckets) in `state`."""
    return [i for i, s in states.items() if i != item_id and s.state is not None and s.state.value == state
            and s.kind == st.kind and s.target_id == st.target_id]


def _in_flight_problem(item_id: str, st: ItemState, states: dict[str, ItemState]) -> tuple[str, str] | None:
    flying = _others(item_id, st, states, rules.IN_FLIGHT)
    if flying:
        return rules.IN_FLIGHT_CONFLICT, (f"{flying[0]} is in flight for the same {st.kind} target: it may already have "
                                          f"been acted on. The user resolves it first (careeros approval resolve {flying[0]})")
    return None


def _invite_problem(item_id: str, st: ItemState, states: dict[str, ItemState]) -> tuple[str, str] | None:
    consumed = _others(item_id, st, states, rules.CONSUMED)
    if consumed and st.kind == rules.LINKEDIN_INVITE:
        return rules.INVITE_CONFLICT, f"{consumed[0]} already sent an invite to this person: one invite per person"
    return None


def _submitted_problem(item_id: str, st: ItemState, states: dict[str, ItemState]) -> tuple[str, str] | None:
    consumed = _others(item_id, st, states, rules.CONSUMED)
    if consumed and st.kind == rules.APPLICATION_SUBMIT:
        return rules.ALREADY_SUBMITTED, f"{consumed[0]} already submitted the application for this job"
    return None


def _job_problem(root: Path, job_id: str, events: list, *, require_approved: bool) -> tuple[str, str] | None:
    """A submit item is decided and claimed only while its job awaits approval (the same check as `approve_job`); the
    claim also needs the job-level approval, so a sent submission is one `transition … APPLIED` can accept."""
    from careeros.core.state_machine import approval_status     # lazy: the state machine imports approvals.fold, and keeping this import out of module load avoids any cycle

    try:
        job = resolve_job(root, job_id)
    except WorkspaceError:
        return JOB_NOT_AWAITING, f"no job {job_id} exists in this workspace"
    if job.archived:
        return JOB_NOT_AWAITING, f"the job {job.id} is archived"
    if job.status != models.State.APPROVAL_REQUIRED:
        return JOB_NOT_AWAITING, f"the job {job.id} is {job.status.value}, not APPROVAL_REQUIRED"
    if require_approved and approval_status(events, job.id) != "approved":
        return JOB_NOT_AWAITING, f"the job-level approval of {job.id} is not recorded as approved"
    return None


def _first(*checks) -> tuple[str, str] | None:
    for check in checks:
        problem = check()
        if problem:
            return problem
    return None


def _decidable(root: Path, ctx: _Ctx, item_id: str, now: str, floor: str | None) -> tuple[Item, str]:
    st = ctx.st
    problem = _first(lambda: _state_problem(item_id, st, rules.OP_APPROVE, now, floor),
                     lambda: _content_problem(item_id, st, ctx.file),
                     lambda: _job_problem(root, st.target_id, ctx.events, require_approved=False)
                     if st.kind == rules.APPLICATION_SUBMIT else None)
    if problem:
        _refuse(root, ctx, item_id, "approve", "user", "tty", *problem)
    return ctx.file.item, st.action_hash


def _decide(root: Path, item_id: str, confirm, clock, *, in_queue: bool) -> Decision:
    root = Path(root)
    with WorkspaceLock(root):
        now = (clock or models.utc_now)()       # the one clock read, under the lock
        ctx = _load(root, item_id, now, "decided")
        floor = ctx.floor
        if in_queue and ctx.st.state.value != rules.PENDING:
            return Decision(item_id, "changed")
        shown, digest = _decidable(root, ctx, item_id, now, floor)
    choice = confirm(render.summary(shown, digest))       # the lock is NOT held while the user decides
    if choice not in ("approve", "decline"):
        return Decision(item_id, "skipped")
    with WorkspaceLock(root):
        ctx = _load(root, item_id, now, "decided")
        # re-checked: state, content (the hash the proposal recorded, so a draft changed meanwhile is hash_mismatch) and
        # the job. The phase-1 clock floor is kept: events others appended while the user decided are not clock skew.
        _decidable(root, ctx, item_id, now, floor)
        st, approve = ctx.st, choice == "approve"
        rel = _expected_rel(item_id)
        verb = "approved" if approve else "declined"
        specs = [{"type": rules.EV_APPROVED if approve else rules.EV_DECLINED, "actor": "user", "entity": item_id,
                  "source": "tty", "prev_state": rules.PENDING, "new_state": rules.APPROVED if approve else rules.DECLINED,
                  "approval": "approved" if approve else "denied", "artifacts": [rel, f"sha256:{digest}"],
                  "action": f"user {verb} {item_id} ({st.kind} for {st.target_id})"}]
        if st.kind == rules.APPLICATION_SUBMIT:
            specs.append({"type": "application.approved" if approve else "application.approval_denied", "actor": "user",
                          "entity": st.target_id, "source": "tty", "approval": "approved" if approve else "denied",
                          "artifacts": [rel, f"sha256:{digest}"],
                          "action": f"user {verb} the application submission {item_id}"})
        events = ledger.append_events(root, specs)
        return Decision(item_id, verb, tuple(events))


def decide(root: Path, item_id: str, *, confirm, clock=None) -> Decision:
    """The user decides one pending item. `confirm(text)` gets the escaped display (`render.summary`) and returns
    "approve", "decline" or anything else (skip: nothing is written). As in `approve_job`, the lock is not held while
    the user decides; state, content and (submit) job are re-checked under the lock before writing, and a draft that
    changed in between is `hash_mismatch`. A submit decision appends the paired job-level event in the same batch."""
    return _decide(root, item_id, confirm, clock, in_queue=False)


def queue(root: Path, *, prompt, clock=None, report=None) -> list[QueueResult]:
    """Every item pending at the start, in proposal order, each shown in full through `prompt` (see `decide`) and each
    its own operation (its own clock read and floor). An item that left `pending` before its turn is `changed`; a
    refusal is reported and the queue goes on. Each decision is written when it is made and passed to `report`; an
    interrupted queue (EOF, Ctrl-C) leaves the current and later items unchanged."""
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        _chain_ok(root, "decided")
        pending = [i for i, st in fold.fold_all(ledger.read_events(root), (clock or models.utc_now)()).items()
                   if st.state is not None and st.state.value == rules.PENDING]
    results: list[QueueResult] = []
    for item_id in pending:
        try:
            result = QueueResult(item_id, _decide(root, item_id, prompt, clock, in_queue=True).outcome)
        except Refused as exc:
            if exc.code == LEDGER_CORRUPT:
                raise
            result = QueueResult(item_id, "refused", exc.code, str(exc))
        results.append(result)
        if report is not None:
            report(result)
    return results


def begin(root: Path, item_id: str, *, actor: str, clock=None) -> Started:
    """The claim, immediately before acting. Under the lock, in the spec's order: state approved, unexpired, no clock
    skew; the file's action hash equals the proposal's; (submit) every manifest file re-validated and re-hashed; no
    other item of the same kind and target in flight; (invite) no other invite to the person consumed; (submit) no
    other submit for the job consumed, and the job awaiting approval with its job-level approval recorded. Then
    `approval.started`. A refusal of an existing item appends a deduped `approval.rejected`."""
    root = Path(root)
    with WorkspaceLock(root):
        now = (clock or models.utc_now)()       # the one clock read, under the lock
        ctx = _load(root, item_id, now, "started")
        st = ctx.st
        problem = _first(lambda: _state_problem(item_id, st, rules.OP_BEGIN, now, ctx.floor),
                         lambda: _content_problem(item_id, st, ctx.file),
                         lambda: _manifest_problem(root, ctx.file.item),
                         lambda: _in_flight_problem(item_id, st, ctx.states),
                         lambda: _invite_problem(item_id, st, ctx.states),
                         lambda: _submitted_problem(item_id, st, ctx.states),
                         lambda: _job_problem(root, st.target_id, ctx.events, require_approved=True)
                         if st.kind == rules.APPLICATION_SUBMIT else None)
        if problem:
            _refuse(root, ctx, item_id, rules.OP_BEGIN, actor, "cli", *problem)
        rel = _expected_rel(item_id)
        (event,) = ledger.append_events(root, [{
            "type": rules.EV_STARTED, "actor": actor, "entity": item_id, "source": "cli", "prev_state": rules.APPROVED,
            "new_state": rules.IN_FLIGHT, "artifacts": [rel, f"sha256:{st.action_hash}"],
            "action": f"started {item_id} ({st.kind} for {st.target_id})"}])
        return Started(item_id, st.kind, st.target_id, rel, st.action_hash, ctx.file.item, event)


def done(root: Path, item_id: str, *, actor: str, note: str | None = None, clock=None) -> Done:
    """`in_flight` -> `consumed`: the action happened. Never touches the job. A refusal appends a deduped rejection."""
    root = Path(root)
    with WorkspaceLock(root):
        now = (clock or models.utc_now)()       # the one clock read, under the lock
        ctx = _load(root, item_id, now, "recorded")
        st = ctx.st
        problem = _state_problem(item_id, st, rules.OP_DONE, now, ctx.floor)
        if problem:
            _refuse(root, ctx, item_id, rules.OP_DONE, actor, "cli", *problem)
        (event,) = ledger.append_events(root, [{
            "type": rules.EV_CONSUMED, "actor": actor, "entity": item_id, "source": "cli", "prev_state": rules.IN_FLIGHT,
            "new_state": rules.CONSUMED, "reason": note or None, "action": f"done {item_id} ({st.kind} for {st.target_id})"}])
        return Done(item_id, event)


def resolve(root: Path, item_id: str, *, sent: bool, clock=None) -> Resolved:
    """The user's account of an in-flight item (the CLI allows it only in an interactive terminal). `sent`: consumed.
    Not sent: revalidate the content hash, the manifest (submit) and invite uniqueness; all pass -> `approval.released`
    (approved again with its original `approved_at`, or computed `expired` when that has passed); any other failure ->
    `approval.voided` with the reason, so the old approval is never silently reused."""
    root = Path(root)
    with WorkspaceLock(root):
        now = (clock or models.utc_now)()       # the one clock read, under the lock
        ctx = _load(root, item_id, now, "recorded")
        st = ctx.st
        problem = _state_problem(item_id, st, rules.OP_DONE, now, ctx.floor)
        if problem:
            _refuse(root, ctx, item_id, "resolve", "user", "tty", *problem)
        base = {"actor": "user", "entity": item_id, "source": "tty", "prev_state": rules.IN_FLIGHT}
        if sent:
            (event,) = ledger.append_events(root, [{**base, "type": rules.EV_CONSUMED, "new_state": rules.CONSUMED,
                                                    "action": f"user reports {item_id} was sent"}])
            return Resolved(item_id, "consumed", None, event)
        failed = _first(lambda: _content_problem(item_id, st, ctx.file),
                        lambda: _manifest_problem(root, ctx.file.item),
                        lambda: _invite_problem(item_id, st, ctx.states))
        if failed:
            reason = f"{failed[0]}: {failed[1]}"
            (event,) = ledger.append_events(root, [{**base, "type": rules.EV_VOIDED, "new_state": rules.VOIDED,
                                                    "reason": reason, "action": f"user reports {item_id} was not sent; voided"}])
            return Resolved(item_id, "voided", reason, event)
        (event,) = ledger.append_events(root, [{**base, "type": rules.EV_RELEASED, "new_state": rules.APPROVED,
                                                "reason": "the user reports nothing was sent",
                                                "action": f"user reports {item_id} was not sent; released"}])
        return Resolved(item_id, "expired" if fold.is_expired(st.approved_at, now) else "released", None, event,
                        _expires_at(st))


def refuse_not_interactive(root: Path, item_id: str, *, operation: str, actor: str, clock=None) -> None:
    """Record the deduped `approval.rejected` (`not_interactive`) when `item_id` names an existing item; an unknown id,
    a broken chain or a failed write records nothing. Never raises: the caller refuses anyway."""
    root = Path(root)
    try:
        with WorkspaceLock(root):
            ctx = _load(root, item_id, (clock or models.utc_now)(), "recorded")
            _refuse(root, ctx, item_id, operation, actor, "cli", rules.NOT_INTERACTIVE,
                    f"{operation} needs an interactive terminal: the user runs it")
    except (WorkspaceError, OSError):
        pass


# --- validation ---------------------------------------------------------------------------------------------------------------

def _job_ids(root: Path) -> set[str]:
    found: set[str] = set()
    for path in job_files(root):
        try:
            found.add(read_job(path).id)
        except Exception:       # noqa: BLE001 - validate reports, never crashes
            continue
    return found


def validate_workspace(root: Path, events: list, now: str | None = None) -> list[Issue]:
    """APR001-APR008 and APR100. `events` is the lenient ledger read `validate` already holds. Read-only; never raises on a
    hand-edited file or a corrupt ledger line. A workspace with no `approvals/` and no `approval.*` events gives nothing."""
    root = Path(root)
    has_events = any(isinstance(e, dict) and str(e.get("type", "")).startswith(rules.EVENT_PREFIX) for e in events)
    if not has_events and not (root / APPROVALS_DIR).is_dir():
        return []
    now = now or models.utc_now()
    try:
        return _validate(root, events, now)
    except Exception as exc:    # noqa: BLE001
        return [Issue("error", rules.APR001, APPROVALS_DIR, f"approvals could not be checked: {exc!r}", item_mod.FIX)]


def _validate(root: Path, events: list, now: str) -> list[Issue]:
    issues: list[Issue] = []
    files = _scan(root)
    result = fold.fold_events(events, now)
    for file in files.values():
        issues.extend(file.issues)
    for error in result.unattributed:
        issues.append(Issue("error", "APR005", "ledger.jsonl", f"event {error.seq}: {error.message}",
                            "approval events are written only by the approval commands; restore ledger.jsonl from version control"))
    jobs = _job_ids(root)
    for stem, file in files.items():
        if stem not in result.items or result.items[stem].state is None:
            issues.append(Issue("warning", "APR002", file.rel, f"{file.rel} has no approval.proposed event, so it is not an approval item",
                                "delete the file, or propose the item again"))
    for item_id, st in result.items.items():
        rel, file = _expected_rel(item_id), files.get(item_id)
        for message in st.errors:
            issues.append(Issue("error", "APR005", rel, f"{item_id}: {message}",
                                "restore ledger.jsonl from version control; approval events follow the lifecycle in docs/approvals.md"))
        if st.state is None:
            continue
        state = st.state.value
        if st.rel is not None and st.rel != rel:
            issues.append(Issue("error", "APR005", rel, f"{item_id}: the proposal event names the file {st.rel}, not {rel}",
                                "restore ledger.jsonl from version control"))
        if file is None:
            if state not in _TERMINAL:
                issues.append(Issue("error", "APR003", rel, f"{item_id} is {state} but {rel} is missing",
                                    "restore the file from version control; the item cannot be used without it"))
        elif file.item is not None:
            if st.action_hash is not None and item_mod.action_hash(file.item) != st.action_hash:
                issues.append(Issue("error", "APR004", rel, f"{rel} differs from what was proposed (the action hash no longer matches)",
                                    "restore the file from version control, or propose a new item"))
            if (st.kind, st.target_id, st.url) != (file.item.kind, file.item.target.id, file.item.target.url):
                issues.append(Issue("error", "APR005", rel,
                                    f"{item_id}: the proposal event says {st.kind} for {st.target_id} (url {st.url}), the file says "
                                    f"{file.item.kind} for {file.item.target.id} (url {file.item.target.url})",
                                    "restore ledger.jsonl and the file from version control"))
            if state in (rules.APPROVED, rules.IN_FLIGHT) and file.item.kind == rules.APPLICATION_SUBMIT:
                try:
                    manifest.verify(root, file.item.manifest)
                except manifest.ManifestError as exc:
                    issues.append(Issue("warning", "APR007", rel, f"{item_id}: {exc}",
                                        "propose the submission again with the current files"))
        if state == rules.IN_FLIGHT:
            issues.append(Issue("warning", "APR008", rel, f"{item_id} is in flight: its outcome is not recorded",
                                f"the user runs `careeros approval resolve {item_id}`"))
        proposed = fold.parse_ts(st.proposed_at)
        current = fold.parse_ts(now)
        if (state == rules.PENDING and proposed is not None and current is not None
                and current - proposed > timedelta(days=rules.PENDING_STALE_DAYS)):
            issues.append(Issue("info", "APR100", rel, f"{item_id} has been pending for more than {rules.PENDING_STALE_DAYS} days",
                                "approve or decline it with `careeros approve`"))
    # job targets and person identity are checked on the item files; an event-only item is checked by its event's target
    for item_id, st in result.items.items():
        file = files.get(item_id)
        target = file.item.target.id if file and file.item else st.target_id
        kind = file.item.kind if file and file.item else st.kind
        if st.state is not None and kind == rules.APPLICATION_SUBMIT and target not in jobs:
            issues.append(Issue("error", "APR006", _expected_rel(item_id), f"{item_id} targets {target}, but no job has that id",
                                "restore the job directory from version control; archive jobs instead of deleting them"))
    # an orphan file (no proposal event) is not an item: only item files with a proposal event take part
    identities = [f.item for i, f in files.items() if f.item is not None and i in result.items
                  and result.items[i].state is not None] + [
        Item(i, st.kind, Target(st.target_id, "x", "x", st.url), "", "") for i, st in result.items.items()
        if st.state is not None and st.url is not None and st.target_id is not None]
    for problem in dict.fromkeys(item_mod.person_identity_problems(identities)):
        issues.append(Issue("error", "APR006", APPROVALS_DIR, problem,
                            "an approval item is immutable: restore the files from version control"))
    return issues
