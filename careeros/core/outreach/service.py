"""The outreach service: person and target records, their `outreach.*` events, the identity unification with approvals
and the read-only `validate_workspace` (spec sections 4, 5, 7, 8, 9).

State-changing operations (`person_add`, `person_link`, `target_add`, `target_decide`) take the workspace lock, verify the
hash chain (`ledger_corrupt`, nothing written), read the clock ONCE after the lock through the `clock` callable the caller
passes and refuse `clock_skew` when that time is earlier than the latest event of any type. `person_add` and `target_add`
write the record file FIRST and the ledger event SECOND inside a `Transaction`: the file is put back on any exception
(`append_events` restores the ledger itself). A process kill between the two leaves an orphan file, which `OUT002`
reports and nothing counts as state. `person_link` rewrites only the record file's `found_in` (documented source data,
no event). Nothing here ever repairs or rewrites a record it did not just create.

Identity is ledger-authoritative: the `per_` id of a canonical profile URL comes from the union of `approval.proposed`
url elements and `outreach.person_added` events (`approvals.people`), and an inconsistent map fails closed with
`identity_conflict`. Record files are a cross-check. A refusal of an EXISTING entity appends an audit-only `outreach.rejected`
(deduped: the entity's latest event of any type is an identical rejection); an unknown entity appends nothing.

`load`, the list and show functions and `validate_workspace` read the ledger and the files and never write. This is a
recorded decision, not a security boundary.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from careeros.core import ids, ledger, models
from careeros.core.approvals import cap as acap
from careeros.core.approvals import fold as afold
from careeros.core.approvals import item as aitem
from careeros.core.approvals import people as apeople
from careeros.core.approvals import rules as arules
from careeros.core.approvals import service as aservice
from careeros.core.memory.store import Transaction
from careeros.core.models import Issue
from careeros.core.outreach import _common as common
from careeros.core.outreach import derive
from careeros.core.outreach import fold as ofold
from careeros.core.outreach import person as person_mod
from careeros.core.outreach import rules
from careeros.core.outreach import target as target_mod
from careeros.core.outreach.model import Evidence, FoundIn, JobInfo, Person, PersonFlags, Target
from careeros.core.outreach.slug import slugify
from careeros.core.workspace import (
    WorkspaceError, WorkspaceLock, ensure_writable, job_files, read_job, resolve_job, split_frontmatter,
)

PEOPLE_DIR = "people"
TARGETS_DIR = "targets"
MAX_RECORD_BYTES = 1 << 20
_PERSON_KINDS = (arules.LINKEDIN_INVITE, arules.LINKEDIN_MESSAGE)
_STATUS_TYPES = ("job.status_changed", "job.status_corrected")


class Refused(WorkspaceError):
    """A refusal (exit 1): `code` is a stable code from `rules.REFUSAL_CODES`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Added:
    id: str
    rel: str
    hash: str
    reused_id: bool = False        # the person id was already known from the ledger (approvals or an earlier add)
    slug: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class Linked:
    id: str
    rel: str
    found_in: tuple[FoundIn, ...]


@dataclass(frozen=True)
class Decided:
    id: str
    slug: str
    decision: str                  # approved | dropped
    status: str                    # displayed status after the decision


@dataclass(frozen=True)
class PersonView:
    id: str
    rel: str
    record: Person | None
    flags: PersonFlags
    problems: tuple[str, ...]

    @property
    def label(self) -> str:
        return self.flags.label

    def to_dict(self) -> dict:
        r = self.record
        return {"id": self.id, "path": self.rel, "label": self.label, "problems": list(self.problems),
                "record": None if r is None else {
                    "url": r.url, "name": r.name, "headline": r.headline, "company": r.company,
                    "company_slug": r.company_slug, "role": r.role, "market": r.market,
                    "found_in": [{"kind": f.kind, "id": f.id} for f in r.found_in], "found_at": r.found_at},
                "state": {"invited_at": self.flags.invited_at, "messaged_at": self.flags.messaged_at,
                          "accepted_at": self.flags.accepted_at, "replied_at": self.flags.replied_at,
                          "closed_at": self.flags.closed_at, "closed_reason": self.flags.closed_reason,
                          "invite_in_flight": self.flags.invite_in_flight}}


@dataclass(frozen=True)
class TargetOut:
    id: str
    slug: str
    rel: str
    record: Target | None
    decision: str
    status: str
    stale: bool
    last_activity: str | None
    people: tuple[str, ...]
    problems: tuple[str, ...]

    def to_dict(self) -> dict:
        r = self.record
        return {"id": self.id, "slug": self.slug, "path": self.rel, "decision": self.decision, "status": self.status,
                "stale": self.stale, "last_activity": self.last_activity, "people": list(self.people),
                "problems": list(self.problems),
                "record": None if r is None else {
                    "company": r.company, "market": r.market, "fit": r.fit, "sponsorship": r.sponsorship,
                    "evidence": [{"source": e.source, "read_on": e.read_on} for e in r.evidence]}}


# --- reading the records -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordFile:
    rel: str
    stem: str
    record: Person | Target | None
    issues: tuple[Issue, ...]
    text: str | None = None


def _scan(root: Path, directory: str, parse) -> dict[str, RecordFile]:
    """Every `<directory>/*.md`, parsed, keyed by file stem. Symlinks, non-regular files, oversized and undecodable files
    are `OUT001` and never read whole. Never raises, never writes."""
    out: dict[str, RecordFile] = {}
    try:
        paths = sorted((Path(root) / directory).glob("*.md"))
    except OSError:
        return out

    def bad(rel: str, stem: str, why: str) -> RecordFile:
        return RecordFile(rel, stem, None, (Issue("error", rules.OUT001, rel, f"{rel} {why}", common.FIX),))

    for path in paths:
        rel, stem = f"{directory}/{path.name}", path.stem
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
                else:
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        out[stem] = bad(rel, stem, "is not valid UTF-8")
                        continue
                    parsed, found = parse(text, rel)
                    out[stem] = RecordFile(rel, stem, parsed, tuple(found), text)
        except OSError as exc:
            out[stem] = bad(rel, stem, f"cannot be read: {exc.strerror or exc}")
    return out


def _job_infos(root: Path, events: Sequence[object]) -> list[JobInfo]:
    changes: dict[str, list[tuple[str, str]]] = {}
    for event in events:
        if isinstance(event, dict) and event.get("type") in _STATUS_TYPES:
            entity, ts, new = event.get("entity"), event.get("ts"), event.get("new_state")
            if isinstance(entity, str) and isinstance(ts, str) and isinstance(new, str):
                changes.setdefault(entity, []).append((ts, new))
    out = []
    for path in job_files(root):
        try:
            job = read_job(path)
        except Exception:       # noqa: BLE001 - a broken job file is not a job here
            continue
        out.append(JobInfo(job.id, job.company, job.status.value, tuple(changes.get(job.id, ()))))
    return out


@dataclass
class Loaded:
    """Everything the read-only commands and the derivations need, read once: the ledger, the record files, the saved
    jobs and the folds at `now`. Built without writing anything."""
    events: list
    now: str
    people_files: dict[str, RecordFile]
    target_files: dict[str, RecordFile]
    jobs: list[JobInfo]
    people_fold: ofold.PeopleFold
    targets_fold: ofold.TargetsFold
    items: dict = field(default_factory=dict)

    @property
    def people(self) -> dict[str, Person]:
        """The records that may feed derived state: parsed, with an `outreach.person_added` event for their id, and with the
        url that event carries. An orphan file, or a record whose id or url disagrees with its event (OUT002, OUT004),
        is reported by `validate` and counted nowhere."""
        added = _person_added_ids(self.events)
        return {f.record.id: f.record for f in self.people_files.values()
                if isinstance(f.record, Person) and added.get(f.record.id) == f.record.url and f.stem == f.record.id}

    @property
    def targets(self) -> dict[str, Target]:
        return {f.record.slug: f.record for f in self.target_files.values() if isinstance(f.record, Target)}


def load(root: Path, now: str | None = None, events: list | None = None) -> Loaded:
    """Read-only. `ledger.LedgerError` when `ledger.jsonl` has a corrupt line (unless `events` is given)."""
    root = Path(root)
    now = now or models.utc_now()
    events = ledger.read_events(root) if events is None else events
    people_files = _scan(root, PEOPLE_DIR, person_mod.parse)
    target_files = _scan(root, TARGETS_DIR, target_mod.parse)
    jobs = _job_infos(root, events)
    loaded = Loaded(events, now, people_files, target_files, jobs, ofold.PeopleFold({}, ()), ofold.TargetsFold({}, ()))
    loaded.people_fold = ofold.fold_people(events, now)
    loaded.targets_fold = ofold.fold_targets(events, loaded.targets, loaded.people, jobs, now)
    loaded.items = afold.fold_all(events, now)
    return loaded


def _rel_person(per_id: str) -> str:
    return f"{PEOPLE_DIR}/{per_id}.md"


def _rel_target(slug: str) -> str:
    return f"{TARGETS_DIR}/{slug}.md"


def _issue_messages(file: RecordFile | None) -> tuple[str, ...]:
    return tuple(i.message for i in file.issues) if file is not None else ()


def _person_added_ids(events: Sequence[object]) -> dict[str, str | None]:
    """person id -> canonical url (None when the event is malformed) for every `outreach.person_added` event."""
    out: dict[str, str | None] = {}
    for event in events:
        if isinstance(event, dict) and event.get("type") == rules.EV_PERSON_ADDED:
            per, url, _ = apeople.parse_person_added(event)
            if per is not None:
                out.setdefault(per, url)
    return out


def _person_view(loaded: Loaded, per_id: str) -> PersonView:
    file = loaded.people_files.get(per_id)
    flags = loaded.people_fold.flags.get(per_id, PersonFlags())
    problems = list(_issue_messages(file))
    if file is None:
        problems.append(f"{_rel_person(per_id)} is missing (the person's state still comes from the ledger)")
    return PersonView(per_id, _rel_person(per_id), file.record if file and isinstance(file.record, Person) else None,
                      flags, tuple(problems))


def person_list(root: Path, now: str | None = None) -> list[PersonView]:
    """Every person with an `outreach.person_added` event, by id (an orphan file is not a person). Read-only."""
    loaded = load(root, now)
    return [_person_view(loaded, i) for i in sorted(_person_added_ids(loaded.events))]


def person_show(root: Path, per_id: str, now: str | None = None) -> PersonView:
    loaded = load(root, now)
    if per_id not in _person_added_ids(loaded.events):
        raise Refused(rules.NOT_FOUND, f"no person {per_id!r} (a person exists once its outreach.person_added event is recorded)")
    return _person_view(loaded, per_id)


def _target_out(loaded: Loaded, slug: str) -> TargetOut:
    view = loaded.targets_fold.targets[slug]
    file = loaded.target_files.get(slug)
    problems = list(_issue_messages(file))
    if file is None:
        problems.append(f"{_rel_target(slug)} is missing (the target's decision still comes from the ledger)")
    return TargetOut(view.id, slug, _rel_target(slug), file.record if file and isinstance(file.record, Target) else None,
                     view.decision, view.status, view.stale, view.last_activity, view.people, tuple(problems))


def target_list(root: Path, now: str | None = None) -> list[TargetOut]:
    """Every target with an `outreach.target_added` event, by slug. Orphan files are not targets. Read-only."""
    loaded = load(root, now)
    return [_target_out(loaded, s) for s in sorted(loaded.targets_fold.targets)]


def target_show(root: Path, slug: str, now: str | None = None) -> TargetOut:
    loaded = load(root, now)
    if slug not in loaded.targets_fold.targets:
        raise Refused(rules.NOT_FOUND, f"no target {slug!r} (a target exists once its outreach.target_added event is recorded)")
    return _target_out(loaded, slug)


# --- state-changing operations ------------------------------------------------------------------------------------------

@dataclass
class _Op:
    root: Path
    actor: str
    now: str
    events: list
    floor: str | None


def _check_actor(actor: object) -> None:
    if not isinstance(actor, str) or not ledger._ACTOR_RE.match(actor):
        raise Refused(rules.INVALID_INPUT, f"invalid actor {actor!r}: use 'user', 'system' or 'agent:<name>'")


def _begin(root: Path, actor: str, clock: Callable[[], str] | None, what: str) -> _Op:
    """Under the lock: writable workspace, an intact chain, the one clock read, the ledger."""
    ensure_writable(root)
    broken = ledger.verify_chain(root)
    if broken:
        raise Refused(rules.LEDGER_CORRUPT, f"the ledger's hash chain is broken ({broken[0].message}); nothing was {what}. "
                                            "Restore ledger.jsonl from .careeros/backups or version control")
    now = (clock or models.utc_now)()       # the one clock read, under the lock
    events = ledger.read_events(root)
    return _Op(root, actor, now, events, afold.clock_floor(events))


def _refuse(op: _Op, entity: str | None, operation: str, code: str, message: str):
    """Append an `outreach.rejected` for this EXISTING entity, unless the entity's latest event of any type is an identical
    rejection (nothing for an unknown entity), then raise `Refused`. Audit only: no fold or gate reads it. A failed audit
    write never turns the refusal into something else."""
    if entity is not None:
        reason, action = f"{code}: {message}", f"rejected {operation} of {entity}"
        last = next((e for e in reversed(op.events) if isinstance(e, dict) and e.get("entity") == entity), None)
        duplicate = (last is not None and last.get("type") == rules.EV_REJECTED and last.get("reason") == reason
                     and last.get("action") == action and last.get("actor") == op.actor)
        if not duplicate:
            try:
                ledger.append_events(op.root, [{"type": rules.EV_REJECTED, "actor": op.actor, "entity": entity,
                                                "action": action, "reason": reason, "source": "cli"}])
            except (OSError, WorkspaceError):
                pass
    raise Refused(code, message)


def _skew(op: _Op, entity: str | None, operation: str):
    if afold.clock_skewed(None, op.now, op.floor):
        _refuse(op, entity, operation, rules.CLOCK_SKEW,
                f"the system clock is earlier than the latest recorded event ({op.floor}); nothing was recorded. Fix the clock; "
                "if that event is future-dated by a wrong clock, restore ledger.jsonl from .careeros/backups")


def _identity_map(op: _Op, operation: str) -> tuple[dict[str, str], dict[str, str]]:
    """`(url -> id, id -> url)` from the ledger, or `identity_conflict` (nothing written, no entity named)."""
    problems = apeople.identity_problems(op.events)
    by_url, errors = apeople.person_urls(op.events)
    if problems or errors:
        _refuse(op, None, operation, rules.IDENTITY_CONFLICT,
                "the identity map built from the ledger is inconsistent: " + "; ".join((problems + errors)[:3])
                + ". Restore ledger.jsonl from .careeros/backups or version control; nothing was written")
    by_id, _ = apeople.person_ids(op.events)
    return by_url, by_id


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lexists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _write_then_append(root: Path, path: Path, data: bytes, specs: list[dict]) -> None:
    directory = path.parent
    made_dir = not directory.exists()
    tx = Transaction()
    try:
        tx.write(path, data)
        ledger.append_events(root, specs)
    except BaseException:
        tx.rollback()
        if made_dir:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _resolve_found_in(op: _Op, specs: Sequence[str], loaded_targets: dict, entity: str | None, operation: str) -> tuple[FoundIn, ...]:
    out: list[FoundIn] = []
    for spec in specs or ():
        kind, _, ref = str(spec).partition(":")
        if kind not in rules.FOUND_IN_KINDS or not ref.strip():
            _refuse(op, entity, operation, rules.INVALID_INPUT, f"--found-in {spec!r} must be job:<REF> or target:<SLUG>")
        if kind == "job":
            try:
                found = FoundIn("job", resolve_job(op.root, ref).id)
            except WorkspaceError as exc:
                _refuse(op, entity, operation, rules.INVALID_INPUT, f"--found-in {spec!r}: {exc}")
        else:
            view = loaded_targets.get(ref)
            if view is None:
                _refuse(op, entity, operation, rules.INVALID_INPUT,
                        f"--found-in {spec!r}: no target {ref!r} (add it with `careeros outreach target add`)")
            found = FoundIn("target", view.id)
        if found not in out:
            out.append(found)
    return tuple(out)


def _target_views(op: _Op) -> dict:
    return ofold.fold_targets(op.events, {}, {}, [], op.now).targets


def person_add(root: Path, *, url: str, name: str, company: str, role: str, headline: str | None = None,
               market: str | None = None, found_in: Sequence[str] = (), actor: str,
               clock: Callable[[], str] | None = None) -> Added:
    """Create `people/<per_id>.md` and append `outreach.person_added` (file first, event second, one transaction). The
    canonical URL is the identity: an id the ledger already knows for it (an earlier proposal or add) is reused, a new
    one is allocated otherwise. Raises `Refused`; nothing is written then, apart from the deduped audit event."""
    root = Path(root)
    operation = "person add"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "added")
        canonical = aitem.canonical_profile_url(url) if isinstance(url, str) else None
        problems = apeople.identity_problems(op.events)
        by_url_hint, _ = apeople.person_urls(op.events)
        hint = by_url_hint.get(canonical) if canonical and not problems else None
        _skew(op, hint, operation)
        if canonical is None:
            _refuse(op, None, operation, rules.INVALID_INPUT,
                    f"{url!r} is not a LinkedIn profile URL: use https://www.linkedin.com/in/<slug>")
        by_url, by_id = _identity_map(op, operation)
        reused = canonical in by_url
        files = _scan(root, PEOPLE_DIR, person_mod.parse)
        added = _person_added_ids(op.events)
        taken = set(by_id) | set(files) | set(added) | {s.target_id for s in afold.fold_all(op.events, op.now).values() if s.target_id}
        per_id = by_url[canonical] if reused else ids.new_id("person", taken)
        entity = per_id if reused else None
        path = root / _rel_person(per_id)
        if per_id in added:
            _refuse(op, per_id, operation, rules.DUPLICATE,
                    f"{per_id} already has an outreach.person_added event for {canonical}; if its file is missing, "
                    "restore it from version control instead of adding the person again")
        if _lexists(path):
            _refuse(op, per_id, operation, rules.DUPLICATE,
                    f"{_rel_person(per_id)} already exists but has no outreach.person_added event (an interrupted add); "
                    "delete the orphan file, then add the person again")
        company_slug = slugify(company)
        if company_slug is None:
            _refuse(op, entity, operation, rules.INVALID_INPUT,
                    "the company has no ASCII letter or digit, so it has no company_slug; use an ASCII spelling")
        links = _resolve_found_in(op, found_in, _target_views(op), entity, operation)
        record = Person(per_id, canonical, name, headline or "", company, company_slug, role, market, links, op.now[:10])
        try:
            data = person_mod.to_bytes(record)
        except (ValueError, TypeError) as exc:
            _refuse(op, entity, operation, rules.INVALID_INPUT, f"the record cannot be stored: {exc}")
        rel = _rel_person(per_id)
        again, issues = person_mod.parse(data.decode("utf-8"), rel)
        if issues or again != record:
            _refuse(op, entity, operation, rules.INVALID_INPUT,
                    "the record is not valid: " + ("; ".join(i.message for i in issues) or "it contains text that cannot be stored"))
        digest = _sha(data)
        spec = {"type": rules.EV_PERSON_ADDED, "actor": actor, "entity": per_id, "source": "cli",
                **apeople.person_added_fields(rel, digest, canonical)}
        _write_then_append(root, path, data, [spec])
        return Added(per_id, rel, digest, reused, None, canonical)


def person_link(root: Path, per_id: str, found_in: Sequence[str], *, actor: str,
                clock: Callable[[], str] | None = None) -> Linked:
    """Add `found_in` entries to the person's record, rewriting ONLY that field of the file (atomically). No event is
    appended: `found_in` is documented source data. An unreadable record is refused and left as it is."""
    root = Path(root)
    operation = "person link"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "linked")
        if not ids.is_valid_id("person", per_id):
            _refuse(op, None, operation, rules.INVALID_INPUT, f"{per_id!r} is not a person id")
        file = _scan(root, PEOPLE_DIR, person_mod.parse).get(per_id)
        known = per_id in _person_added_ids(op.events)
        entity = per_id if known else None
        _skew(op, entity, operation)
        if not known:
            _refuse(op, None, operation, rules.NOT_FOUND, f"no person {per_id!r}: add one with `careeros outreach person add`")
        if file is None:
            _refuse(op, entity, operation, rules.NOT_FOUND, f"{_rel_person(per_id)} is missing: restore it from version control")
        if not isinstance(file.record, Person):
            _refuse(op, entity, operation, rules.INVALID_INPUT,
                    f"{file.rel} is not a valid record ({'; '.join(_issue_messages(file))}); fix it by hand, nothing was changed")
        if not found_in:
            _refuse(op, entity, operation, rules.INVALID_INPUT, "give at least one --found-in job:<REF> or target:<SLUG>")
        links = _resolve_found_in(op, found_in, _target_views(op), entity, operation)
        merged = tuple(dict.fromkeys(file.record.found_in + links))
        record = replace(file.record, found_in=merged)
        _, body = split_frontmatter(file.text)
        data = person_mod.to_bytes(record, body)
        again, issues = person_mod.parse(data.decode("utf-8"), file.rel)
        if issues or again != record:
            _refuse(op, entity, operation, rules.INVALID_INPUT,
                    "the record is not valid: " + "; ".join(i.message for i in issues))
        tx = Transaction()
        try:
            tx.write(root / file.rel, data)
        except BaseException:
            tx.rollback()
            raise
        return Linked(per_id, file.rel, merged)


def target_add(root: Path, *, company: str, slug: str, market: str, fit: int, sponsorship: str,
               evidence: Sequence[tuple[str, str]] = (), actor: str, clock: Callable[[], str] | None = None) -> Added:
    """Create `targets/<slug>.md` and append `outreach.target_added` (file first, event second). A slug is unique for
    ever: an existing record or any `target_added` event for it (a dropped target included) is `duplicate`."""
    root = Path(root)
    operation = "target add"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "added")
        views = _target_views(op)
        existing = views.get(slug) if isinstance(slug, str) else None
        _skew(op, existing.id if existing else None, operation)
        if not isinstance(slug, str) or slugify(slug) != slug:
            _refuse(op, None, operation, rules.INVALID_INPUT,
                    f"slug {slug!r} must be lower-case ASCII letters and digits separated by single hyphens"
                    + (f" (try {slugify(slug)!r})" if slugify(slug) else ""))
        path = root / _rel_target(slug)
        if existing is not None or _lexists(path):
            _refuse(op, existing.id if existing else None, operation, rules.DUPLICATE,
                    f"the slug {slug!r} is already used"
                    + (f" by {existing.id} ({existing.decision})" if existing else " by a file in targets/")
                    + ": slugs are unique and a dropped slug cannot be added again")
        taken = {v.id for v in views.values()} | {f.record.id for f in _scan(root, TARGETS_DIR, target_mod.parse).values()
                                                  if isinstance(f.record, Target)}
        record = Target(ids.new_id("target", taken), slug, company, market, fit, sponsorship,
                        tuple(Evidence(s, d) for s, d in evidence))
        try:
            data = target_mod.to_bytes(record)
        except (ValueError, TypeError) as exc:
            _refuse(op, None, operation, rules.INVALID_INPUT, f"the record cannot be stored: {exc}")
        rel = _rel_target(slug)
        again, issues = target_mod.parse(data.decode("utf-8"), rel)
        if issues or again != record:
            _refuse(op, None, operation, rules.INVALID_INPUT,
                    "the record is not valid: " + ("; ".join(i.message for i in issues) or "it contains text that cannot be stored"))
        digest = _sha(data)
        spec = {"type": rules.EV_TARGET_ADDED, "actor": actor, "entity": record.id, "source": "cli",
                "new_state": rules.CANDIDATE, **ofold.target_added_fields(rel, digest, slug)}
        _write_then_append(root, path, data, [spec])
        return Added(record.id, rel, digest, False, slug)


def target_decide(root: Path, slug: str, decision: str, *, source: str = "user", reason: str | None = None, actor: str,
                  clock: Callable[[], str] | None = None) -> Decided:
    """Record the user's `approve` or `drop` of a target (`outreach.target_approved` / `outreach.target_dropped`). Legal:
    candidate to approved, candidate to dropped, approved to dropped. A drop needs a reason."""
    root = Path(root)
    if decision not in rules.DECISION_OPERATIONS:
        raise WorkspaceError(f"unknown decision {decision!r}: use one of {', '.join(rules.DECISION_OPERATIONS)}")
    operation = f"target {decision}"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "recorded")
        view = _target_views(op).get(slug)
        if view is None:
            _refuse(op, None, operation, rules.NOT_FOUND,
                    f"no target {slug!r} (a target exists once its outreach.target_added event is recorded)")
        _skew(op, view.id, operation)
        if source not in rules.OUTCOME_SOURCES:
            _refuse(op, view.id, operation, rules.INVALID_INPUT, f"source must be one of {', '.join(rules.OUTCOME_SOURCES)}")
        reason = (reason or "").strip() or None
        if decision == rules.OP_DROP and reason is None:
            _refuse(op, view.id, operation, rules.INVALID_INPUT, "dropping a target needs a --reason")
        if reason is not None:
            problems: list[str] = []
            if common.text_value(reason, "reason", problems) is None:
                _refuse(op, view.id, operation, rules.INVALID_INPUT, "; ".join(problems))
        new = ofold.apply_decision(view.decision, decision)
        if isinstance(new, ofold.Refusal):
            _refuse(op, view.id, operation, rules.WRONG_STATE, f"{slug} is {new.state}; {decision} is not legal from there")
        spec = {"type": rules.EVENT_FOR_DECISION[decision], "actor": actor, "entity": view.id, "source": source,
                "prev_state": view.decision, "new_state": new,
                "action": f"{decision} target {slug}"}
        if reason is not None:
            spec["reason"] = reason
        ledger.append_events(root, [spec])
        return Decided(view.id, slug, new, load(root, op.now).targets_fold.targets[slug].status)


@dataclass(frozen=True)
class Recorded:
    id: str
    outcome: str                   # accepted | replied | closed
    label: str                     # the person's label after the outcome
    event: dict


def _known_person(op: _Op, per_id: str) -> bool:
    """A person the ledger knows: an `outreach.person_added`, an invite or message item, or any event naming the id."""
    return per_id in ofold.fold_people(op.events, op.now).flags or per_id in apeople.closed_people(op.events)[0]


def record(root: Path, per_id: str, outcome: str, *, source: str = "user", reason: str | None = None,
           note: str | None = None, actor: str, clock: Callable[[], str] | None = None) -> Recorded:
    """Record an outcome the user reported or the agent observed: `outreach.accepted`, `outreach.replied` or
    `outreach.closed` (spec 5 and 6.2). `accepted` needs a consumed invite, `replied` a consumed invite or message, each kind
    is recorded at most once and nothing follows `closed` (`wrong_state`). `closed` needs a reason from
    `rules.CLOSED_REASONS`; the optional note goes in the event's `action`. Raises `Refused`; a refusal of a known person
    appends a deduped `outreach.rejected`, an unknown id appends nothing."""
    root = Path(root)
    if outcome not in rules.OUTCOMES:
        raise WorkspaceError(f"unknown outcome {outcome!r}: use one of {', '.join(rules.OUTCOMES)}")
    operation = f"record {outcome}"
    _check_actor(actor)
    with WorkspaceLock(root):
        op = _begin(root, actor, clock, "recorded")
        if not ids.is_valid_id("person", per_id):
            _refuse(op, None, operation, rules.INVALID_INPUT, f"{per_id!r} is not a person id")
        known = _known_person(op, per_id)
        entity = per_id if known else None
        _skew(op, entity, operation)
        if not known:
            _refuse(op, None, operation, rules.NOT_FOUND,
                    f"no person {per_id!r}: the ledger has no person_added, invite, message or outcome for that id")
        if source not in rules.OUTCOME_SOURCES:
            _refuse(op, entity, operation, rules.INVALID_INPUT, f"--source must be one of {', '.join(rules.OUTCOME_SOURCES)}")
        reason = (reason or "").strip() or None
        if outcome == rules.CLOSED and reason not in rules.CLOSED_REASONS:
            _refuse(op, entity, operation, rules.INVALID_INPUT,
                    f"closing a person needs --reason, one of {', '.join(rules.CLOSED_REASONS)}")
        if outcome != rules.CLOSED and reason is not None:
            _refuse(op, entity, operation, rules.INVALID_INPUT, "--reason is only for --closed (use --note for anything else)")
        if note is not None:
            problems: list[str] = []
            if common.text_value(note, "note", problems) is None:
                _refuse(op, entity, operation, rules.INVALID_INPUT, "; ".join(problems))
        flags = ofold.fold_people(op.events, op.now).flags.get(per_id, PersonFlags())
        new = ofold.apply_outcome(flags, outcome, op.now, reason)
        if per_id in apeople.closed_people(op.events)[0]:           # fail closed: a closed event whose ts is unreadable
            new = ofold.Refusal(rules.WRONG_STATE, rules.CLOSED)
        if isinstance(new, ofold.Refusal):
            _refuse(op, entity, operation, rules.WRONG_STATE,
                    f"{per_id} is {new.state}; {outcome} is not legal from there (accepted needs a sent invite, replied a sent "
                    "invite or message; each outcome is recorded once and nothing follows closed)")
        action = f"recorded {outcome} for {per_id}" + (f"; note: {note}" if note else "")
        spec = {"type": rules.EVENT_FOR_OUTCOME[outcome], "actor": actor, "entity": per_id, "source": source,
                "prev_state": flags.label, "new_state": new.label, "action": action}
        if reason is not None:
            spec["reason"] = reason
        (event,) = ledger.append_events(root, [spec])
        return Recorded(per_id, outcome, new.label, event)


# --- read-only derived commands ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CapView:
    cap: int
    configured: bool               # False: the default (no setting in profile.md)
    count: acap.CapCount
    remaining: int
    now: str

    def to_dict(self) -> dict:
        return {"cap": self.cap, "configured": self.configured, "count": self.count.total,
                "in_flight": self.count.in_flight, "consumed_in_window": self.count.consumed_in_window,
                "remaining": self.remaining, "oldest_leaves": self.count.oldest_leaves, "now": self.now}


@dataclass(frozen=True)
class NextView:
    contacts: tuple[derive.NextContact, ...]
    cap: CapView


def _target_infos(loaded: Loaded) -> dict[str, derive.TargetInfo]:
    """slug -> TargetInfo for every target with an event and a parsed record of the same id (a missing or unreadable
    record has no fit or sponsorship, so it is not ranked)."""
    out = {}
    for slug, view in loaded.targets_fold.targets.items():
        rec = loaded.targets.get(slug)
        if rec is not None and rec.id == view.id:
            out[slug] = derive.TargetInfo(rec, view)
    return out


def _cap_view(root: Path, loaded: Loaded) -> CapView:
    setting = aservice.profile_cap_setting(root)
    value = acap.effective_cap(setting)
    if isinstance(value, acap.CapRefusal):
        raise Refused(rules.CAP_CONFIG_INVALID, aservice.cap_config_message(value))
    count = acap.invite_count(loaded.items, loaded.now)
    return CapView(value, setting.value is not None, count, acap.remaining(value, count), loaded.now)


def queue(root: Path, now: str | None = None) -> derive.Queue:
    """The LinkedIn invite and application follow-ups due at `now` (spec 6.3). Email and interview follow-ups are not
    covered (`Queue.uncovered_note`). Read-only."""
    loaded = load(root, now)
    return derive.build_queue(loaded.people, loaded.people_fold.flags, _target_infos(loaded), loaded.jobs, loaded.now)


def cap(root: Path, now: str | None = None) -> CapView:
    """The weekly invite cap, the count and what remains (spec 6.4), with the same functions `approval begin` uses.
    `Refused(cap_config_invalid)` when profile.md holds an invalid setting. Read-only."""
    loaded = load(root, now)
    return _cap_view(root, loaded)


def next_contacts(root: Path, now: str | None = None) -> NextView:
    """Whom to invite next, limited to the remaining cap (spec 6.6). `Refused(cap_config_invalid)` like `cap`. Read-only."""
    loaded = load(root, now)
    view = _cap_view(root, loaded)
    contacts = derive.build_next(loaded.people, loaded.people_fold.flags, _target_infos(loaded), loaded.jobs, loaded.items,
                                 view.remaining, loaded.now)
    return NextView(tuple(contacts), view)


def warm(root: Path, now: str | None = None) -> list[derive.WarmContact]:
    """Connected or replied people at a company with a saved, non-terminal job (spec 6.6). Read-only."""
    loaded = load(root, now)
    return derive.build_warm(loaded.people, loaded.people_fold.flags, loaded.jobs, loaded.now)


# --- validation ------------------------------------------------------------------------------------------------------------

def _peek(file: RecordFile) -> dict:
    """The raw `id` / `url` / `slug` of a record that did not parse, for the OUT004 comparison. Never raises."""
    if file.text is None:
        return {}
    fm, _ = common.load(file.text, file.rel)
    return fm or {}


def validate_workspace(root: Path, events: list, now: str | None = None) -> list[Issue]:
    """OUT001 to OUT006 and OUT100 (OUT007, the cap setting, is checked in `careeros.core.validation`). `events` is the lenient ledger read `validate`
    already holds. Read-only; never raises on a hand-edited file or a corrupt ledger line. A workspace with no `people/`,
    no `targets/` and no `outreach.*` events gives nothing."""
    root = Path(root)
    events = [e for e in events if isinstance(e, dict)]
    has_events = any(str(e.get("type", "")).startswith(rules.EVENT_PREFIX) for e in events)
    if not has_events and not (root / PEOPLE_DIR).is_dir() and not (root / TARGETS_DIR).is_dir():
        return []
    now = now or models.utc_now()
    try:
        return _validate(root, events, now)
    except Exception as exc:    # noqa: BLE001
        return [Issue("error", rules.OUT001, PEOPLE_DIR, f"outreach records could not be checked: {exc!r}", common.FIX)]


def _validate(root: Path, events: list, now: str) -> list[Issue]:
    loaded = load(root, now, events)
    issues: list[Issue] = []
    for file in [*loaded.people_files.values(), *loaded.target_files.values()]:
        issues.extend(file.issues)
    seen: set[str] = set()
    for error in [*loaded.people_fold.errors, *loaded.targets_fold.errors]:
        if error.message not in seen:
            seen.add(error.message)
            issues.append(Issue("error", rules.OUT005, "ledger.jsonl", f"event {error.seq}: {error.message}",
                                "outreach events are written only by the outreach commands; restore ledger.jsonl from version control"))
    for message in apeople.identity_problems(events):
        issues.append(Issue("error", rules.OUT004, "ledger.jsonl", message,
                            "restore ledger.jsonl from version control; identity is never picked for you"))

    added = {}
    for event in events:
        if event.get("type") == rules.EV_PERSON_ADDED:
            per, url, _ = apeople.parse_person_added(event)
            if per is not None and url is not None:
                added.setdefault(per, url)
    by_url, _ = apeople.person_urls(events)
    views = loaded.targets_fold.targets
    people_files, target_files = loaded.people_files, loaded.target_files

    # --- orphan files (OUT002) and missing files (OUT003)
    for stem, file in people_files.items():
        if stem not in added:
            issues.append(Issue("warning", rules.OUT002, file.rel, f"{file.rel} has no outreach.person_added event, so it is not a person record",
                                "delete the orphan file, then add the person again with `careeros outreach person add`"))
    for slug, file in target_files.items():
        if slug not in views:
            issues.append(Issue("warning", rules.OUT002, file.rel, f"{file.rel} has no outreach.target_added event, so it is not a target record",
                                "delete the file, or add the target with `careeros outreach target add`"))
    approval_people = {s.target_id for s in loaded.items.values()
                       if s.kind in _PERSON_KINDS and ids.is_valid_id("person", s.target_id)}
    for per in sorted((approval_people | set(added)) - set(people_files)):
        why = "has an outreach.person_added event" if per in added else "has approval events"
        issues.append(Issue("warning", rules.OUT003, _rel_person(per), f"{per} {why} but {_rel_person(per)} is missing",
                            "restore the file from version control; the person's state still comes from the ledger"))
    for slug, view in views.items():
        if slug not in target_files:
            issues.append(Issue("warning", rules.OUT003, _rel_target(slug), f"{view.id} ({slug}) has an event but {_rel_target(slug)} is missing",
                                "restore the file from version control; the target's decision still comes from the ledger"))

    # --- records against events (OUT004)
    def mismatch(path: str, message: str) -> None:
        issues.append(Issue("error", rules.OUT004, path, message, "restore the record from version control; id and url are never edited"))

    for per, url in added.items():
        file = people_files.get(per)
        if file is None:
            continue
        raw = {"id": file.record.id, "url": file.record.url} if isinstance(file.record, Person) else _peek(file)
        if "id" in raw and raw["id"] != per:
            mismatch(file.rel, f"{file.rel} says id {raw['id']!r}, its event says {per}")
        if "url" in raw and raw["url"] != url:
            mismatch(file.rel, f"{file.rel} says url {raw['url']!r}, its event says {url}")
    for file in people_files.values():
        if isinstance(file.record, Person):
            owner = by_url.get(file.record.url)
            if owner is not None and owner != file.record.id:
                mismatch(file.rel, f"{file.rel} has the url of {owner}, not {file.record.id}")
    slug_of_id = {v.id: s for s, v in views.items()}
    for slug, view in views.items():
        file = target_files.get(slug)
        if file is None:
            continue
        raw = {"id": file.record.id} if isinstance(file.record, Target) else _peek(file)
        if "id" in raw and raw["id"] != view.id:
            mismatch(file.rel, f"{file.rel} says id {raw['id']!r}, its event says {view.id}")
    for slug, file in target_files.items():
        if isinstance(file.record, Target) and slug_of_id.get(file.record.id, slug) != slug:
            mismatch(file.rel, f"{file.rel} has the id of the target {slug_of_id[file.record.id]}")

    # --- found_in references (OUT006)
    job_ids = {j.id for j in loaded.jobs}
    target_ids = {v.id for v in views.values()} | {f.record.id for f in target_files.values() if isinstance(f.record, Target)}
    for file in people_files.values():
        if not isinstance(file.record, Person):
            continue
        for ref in file.record.found_in:
            if ref.id not in (job_ids if ref.kind == "job" else target_ids):
                issues.append(Issue("warning", rules.OUT006, file.rel, f"{file.rel} found_in names the {ref.kind} {ref.id}, which does not exist",
                                    "correct the entry by hand or with `careeros outreach person link`; an unknown reference links nothing"))
    # --- stale targets (OUT100)
    for slug, view in views.items():
        if view.stale:
            issues.append(Issue("info", rules.OUT100, _rel_target(slug), f"target {slug} has had no activity for more than {rules.TARGET_STALE_DAYS} days",
                                "refresh its evidence (update read_on) before drafting to its contacts, or drop it"))
    return issues
