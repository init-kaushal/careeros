"""The approvals service: `propose`, `list_items`, `show` and the read-only `validate_workspace` (spec sections 6, 8, 9).

`propose` writes the item file first and the ledger events second, under the workspace lock, as one transaction: the file
is put back on any exception (`append_events` restores the ledger itself). A process kill between the two leaves an
orphan file, which `APR002` reports and nothing treats as actionable. The `approval.proposed` event is written only via
`fold.proposal_event_fields`, together with one `approval.superseded` per earlier pending or approved item of the same
kind and target, in one batch. Persons have no records: a person is identified by the canonical profile URL, and the
`per_` id is allocated for the first proposal of a URL and reused by every later one (any parsed item file counts, even an
orphan).

`list_items` and `show` fold the ledger and read the item files; `validate_workspace` does the same and adds the `APR`
codes. None of the three takes the lock or writes anything. Each takes `now` once and passes it down; every
`clock_skewed` call uses `ItemState.last_ts`. Item files and the folded events are cross-checked: the event's kind,
target and hash must match the file, so an edited `action` text cannot move an item to another bucket. This is a
recorded decision, not a security boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from careeros.core import ids, ledger, models
from careeros.core.approvals import fold, item as item_mod, manifest, render, rules
from careeros.core.approvals.model import Item, ItemState, ManifestEntry, Target
from careeros.core.memory.store import Transaction
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError, WorkspaceLock, ensure_writable, job_files, read_job, resolve_job

APPROVALS_DIR = "approvals"
INVALID_INPUT = "invalid_input"
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
    """Every `approvals/*.md`, parsed, keyed by file stem. Never raises, never writes."""
    out: dict[str, _File] = {}
    base = Path(root) / APPROVALS_DIR
    try:
        paths = sorted(p for p in base.glob("*.md") if p.is_file())
    except OSError:
        return out
    for path in paths:
        rel = f"{APPROVALS_DIR}/{path.name}"
        try:
            parsed, found = item_mod.parse_bytes(path.read_bytes(), rel)
        except OSError as exc:
            parsed, found = None, [Issue("error", rules.APR001, rel, f"{rel} cannot be read: {exc.strerror or exc}", item_mod._FIX)]
        out[path.stem] = _File(rel, parsed, tuple(found))
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
        if st.kind != file.item.kind or st.target_id != file.item.target.id:
            problems.append(EVENT_MISMATCH)
    return list(dict.fromkeys(problems))


def _view(item_id: str, st: ItemState, file: _File | None, now: str) -> View:
    skew = clock_skew_of(st, now)
    problems = _mismatches(item_id, st, file) + ([rules.CLOCK_SKEW] if skew else [])
    return View(item_id, _expected_rel(item_id), st.state.value if st.state else "unknown", st.kind, st.target_id,
                st.action_hash, st.proposed_at, st.approved_at, _expires_at(st), tuple(problems), skew,
                file.item if file else None)


def clock_skew_of(st: ItemState, now: str) -> bool:
    """`now` earlier than the approval or than the item's latest event (callers always pass `ItemState.last_ts`)."""
    return fold.clock_skewed(st.approved_at, now, st.last_ts)


def list_items(root: Path, status: str | None = None, now: str | None = None) -> list[View]:
    """Every item that has an `approval.proposed` event, in proposal order; read-only. Orphan files are not listed."""
    if status is not None and status not in rules.STATES:
        raise WorkspaceError(f"unknown status {status!r}: use one of {', '.join(rules.STATES)}")
    now = now or models.utc_now()
    files = _scan(root)
    result = fold.fold_events(ledger.read_events(root), now)
    views = [_view(i, st, files.get(i), now) for i, st in result.items.items() if st.state is not None]
    return [v for v in views if status is None or v.state == status]


def show(root: Path, item_id: str, now: str | None = None) -> View:
    """One item with its content; read-only. `Refused(not_found)` when no proposal event names it (an orphan file too)."""
    now = now or models.utc_now()
    files = _scan(root)
    st = fold.fold_events(ledger.read_events(root), now).items.get(item_id)
    if st is None or st.state is None:
        raise Refused(rules.NOT_FOUND, f"no approval item {item_id!r} (an item exists only once its approval.proposed event is recorded)")
    return _view(item_id, st, files.get(item_id), now)


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
            try:
                job = resolve_job(root, job_ref)
            except WorkspaceError as exc:
                raise Refused(INVALID_INPUT, str(exc)) from None
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

        now = models.utc_now()
        events = ledger.read_events(root)
        states = fold.fold_all(events, now)
        files = _scan(root)
        parsed = [f.item for f in files.values() if f.item is not None]
        taken_people = {i.target.id for i in parsed if i.target.url is not None}
        taken_people |= {st.target_id for st in states.values() if st.target_id and st.target_id.startswith("per_")}
        if person:
            target_id = item_mod.person_ids_by_url(parsed).get(url) or ids.new_id("person", taken_people)
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
                  **fold.proposal_event_fields(kind, target_id, rel, digest)}]
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
        return [Issue("error", rules.APR001, APPROVALS_DIR, f"approvals could not be checked: {exc!r}", item_mod._FIX)]


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
            if st.kind != file.item.kind or st.target_id != file.item.target.id:
                issues.append(Issue("error", "APR005", rel,
                                    f"{item_id}: the proposal event says {st.kind} for {st.target_id}, the file says "
                                    f"{file.item.kind} for {file.item.target.id}",
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
    parsed = [f.item for f in files.values() if f.item is not None]
    for problem in item_mod.person_identity_problems(parsed):
        issues.append(Issue("error", "APR006", APPROVALS_DIR, problem,
                            "an approval item is immutable: restore the files from version control"))
    return issues
