"""Application state machine and the operations that change a job: transition, approve, archive."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from careeros.core import ledger, models
from careeros.core.models import Job, State
from careeros.core.workspace import (
    RECOVERY_REL,
    WorkspaceError,
    WorkspaceLock,
    atomic_write_bytes,
    ensure_writable,
    join_frontmatter,
    normalize_value,
    resolve_job,
    split_frontmatter,
)

PRE = (
    State.DISCOVERED, State.EVALUATED, State.SHORTLISTED,
    State.RESEARCHED, State.PREPARING, State.READY_TO_APPLY,
)
POST = (
    State.APPLIED, State.RECRUITER_REPLIED, State.SCREEN,
    State.TECHNICAL, State.HM, State.FINAL, State.OFFER,
)
TERMINAL = frozenset({State.ACCEPTED, State.REJECTED, State.WITHDRAWN})

STATUS_BULLET = re.compile(r"^(- \*\*Status:\*\* ?)(.*)$", re.M)
PIPE_ENTRY = re.compile(r"^(- \[)(.)(\] .*?)(https?://\S+)([ \t]*)$", re.M)

_DISPLAY = {
    State.DISCOVERED: "discovered",
    State.APPLIED: "applied",
    State.RECRUITER_REPLIED: "interview",
    State.SCREEN: "interview",
    State.TECHNICAL: "interview",
    State.HM: "interview",
    State.FINAL: "interview",
    State.OFFER: "offer",
    State.ACCEPTED: "accepted",
    State.REJECTED: "rejected",
    State.WITHDRAWN: "closed",
}
_ALIASES = {State.WITHDRAWN: {"closed", "declined", "withdrawn"}}
_STATUS_EVENTS = {"job.imported", "job.status_changed", "job.status_corrected"}


class TransitionError(WorkspaceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RecoveryError(WorkspaceError):
    """A failed write could not be undone; the original content was saved for recovery."""


@dataclass(frozen=True)
class TransitionResult:
    outcome: str  # "changed" | "corrected" | "unchanged" | "repaired"
    job_id: str
    from_state: State
    to_state: State
    event: dict | None = None


# --- rules ------------------------------------------------------------------------

def is_legal(frm: State, to: State) -> bool:
    if frm in TERMINAL or frm == to:
        return False
    if to == State.WITHDRAWN:
        return True
    if to == State.REJECTED:
        return frm in POST
    if to == State.ACCEPTED:
        return frm == State.OFFER
    if to == State.APPROVAL_REQUIRED:
        return frm == State.READY_TO_APPLY
    if to == State.APPLIED:
        return frm == State.APPROVAL_REQUIRED
    if frm in PRE and to in PRE:
        return PRE.index(to) > PRE.index(frm)
    if frm in POST and to in POST:
        return POST.index(to) > POST.index(frm)
    return False


def display_for(state: State) -> str:
    return _DISPLAY.get(state, state.value.lower().replace("_", "-"))


def bullet_matches(bullet: str, state: State) -> bool:
    value = bullet.strip().lower()
    return value in {display_for(state), state.value.lower(), *_ALIASES.get(state, set())}


def icon_for(state: State) -> str:
    if state in PRE or state == State.APPROVAL_REQUIRED:
        return " "
    if state == State.APPLIED:
        return "~"
    if state in (State.RECRUITER_REPLIED, State.SCREEN, State.TECHNICAL, State.HM, State.FINAL):
        return "?"
    if state in (State.OFFER, State.ACCEPTED):
        return "✓"
    return "x"


def approval_status(events: list[dict], job_id: str) -> str | None:
    """Latest approval decision since the job last entered APPROVAL_REQUIRED."""
    status: str | None = None
    for event in events:
        if event.get("entity") != job_id:
            continue
        kind = event.get("type")
        if kind in ("job.status_changed", "job.status_corrected") and event.get("new_state") == State.APPROVAL_REQUIRED.value:
            status = None
        elif kind == "application.approved":
            status = "approved"
        elif kind == "application.approval_denied":
            status = "denied"
    return status


def recorded_state(events: list[dict], job_id: str) -> str | None:
    state: str | None = None
    for event in events:
        if event.get("entity") == job_id and event.get("type") in _STATUS_EVENTS and event.get("new_state"):
            state = event["new_state"]
    return state


def update_pipeline_text(text: str, url: str, icon: str) -> str:
    target = url.rstrip("/")

    def replace(match: re.Match[str]) -> str:
        if match.group(4).rstrip("/") == target:
            return f"{match.group(1)}{icon}{match.group(3)}{match.group(4)}{match.group(5)}"
        return match.group(0)

    return PIPE_ENTRY.sub(replace, text)


# --- writing helpers --------------------------------------------------------------

def _set_status_bullet(body: str, display: str) -> str:
    return STATUS_BULLET.sub(lambda m: f"{m.group(1)}{display}", body, count=1)


def _restore(root: Path, originals: dict[Path, bytes], written: list[Path], job_id: str) -> None:
    first_failure: RecoveryError | None = None
    for path in written:
        try:
            atomic_write_bytes(path, originals[path])
        except OSError as exc:
            stamp = models.utc_now().replace(":", "")
            recovery = Path(root) / RECOVERY_REL / f"{stamp}-{job_id}-{path.name}"
            recovery.parent.mkdir(parents=True, exist_ok=True)
            recovery.write_bytes(originals[path])
            if first_failure is None:
                first_failure = RecoveryError(
                    f"could not restore {path}; its original content is saved at {recovery}"
                )
                first_failure.__cause__ = exc
    if first_failure is not None:
        raise first_failure


def _reject(root: Path, job: Job, to_state: State, actor: str, code: str, message: str, events: list[dict]) -> None:
    reason = f"{code}: {message}"
    last = next((e for e in reversed(events) if e.get("entity") == job.id), None)
    duplicate = (
        last is not None
        and last.get("type") == "job.transition_rejected"
        and last.get("new_state") == to_state.value
        and last.get("reason") == reason
    )
    if not duplicate:
        ledger.append_event(
            root, type="job.transition_rejected", actor=actor, entity=job.id,
            prev_state=job.status.value, new_state=to_state.value,
            action=f"rejected {job.status.value} → {to_state.value}", reason=reason, source="cli",
        )
    raise TransitionError(code, message)


# --- transition -------------------------------------------------------------------

def apply_transition(
    root: Path,
    ref: str,
    to_state: State | str,
    *,
    actor: str,
    reason: str | None = None,
    force: bool = False,
) -> TransitionResult:
    root = Path(root)
    to_state = State(to_state)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        events = ledger.read_events(root)
        current = job.status
        recorded = recorded_state(events, job.id)
        if job.archived:
            _reject(root, job, to_state, actor, "archived",
                    "the job is archived; run `careeros archive <job> --undo` first", events)
        if current == to_state:
            if recorded is not None and recorded != current.value:
                event = ledger.append_event(
                    root, type="job.status_changed", actor=actor, entity=job.id,
                    prev_state=recorded, new_state=current.value,
                    action=f"ledger repaired: the job file already shows {current.value}", source="recovery",
                )
                return TransitionResult("repaired", job.id, current, to_state, event)
            return TransitionResult("unchanged", job.id, current, to_state)

        corrected = False
        if not is_legal(current, to_state):
            if not force:
                _reject(root, job, to_state, actor, "illegal_transition",
                        f"{current.value} → {to_state.value} is not allowed", events)
            if not (reason or "").strip():
                raise TransitionError("reason_required", "--force needs --reason explaining the correction")
            if current in TERMINAL:
                _reject(root, job, to_state, actor, "terminal",
                        f"{current.value} is terminal and cannot be changed", events)
            corrected = True
        if to_state == State.APPLIED and approval_status(events, job.id) != "approved":
            _reject(root, job, to_state, actor, "approval_required",
                    "approval required — ask the user to run `careeros approve <job>` themselves", events)
        return _write_transition(root, job, to_state, actor=actor, reason=reason, corrected=corrected)


def _write_transition(root: Path, job: Job, to_state: State, *, actor: str, reason: str | None, corrected: bool) -> TransitionResult:
    originals: dict[Path, bytes] = {job.path: job.path.read_bytes()}
    frontmatter, body = split_frontmatter(originals[job.path].decode("utf-8"))
    frontmatter = {k: normalize_value(v) for k, v in (frontmatter or {}).items()}
    frontmatter["status"] = to_state.value
    frontmatter["updated_at"] = models.utc_now()
    new_texts: dict[Path, str] = {job.path: join_frontmatter(frontmatter, _set_status_bullet(body, display_for(to_state)))}
    pipeline = root / "jobs" / "pipeline.md"
    if pipeline.is_file() and job.url:
        pipeline_text = pipeline.read_bytes().decode("utf-8")
        updated = update_pipeline_text(pipeline_text, job.url, icon_for(to_state))
        if updated != pipeline_text:
            originals[pipeline] = pipeline_text.encode("utf-8")
            new_texts[pipeline] = updated
    written: list[Path] = []
    try:
        for path, text in new_texts.items():
            atomic_write_bytes(path, text.encode("utf-8"))
            written.append(path)
        event = ledger.append_event(
            root,
            type="job.status_corrected" if corrected else "job.status_changed",
            actor=actor, entity=job.id, prev_state=job.status.value, new_state=to_state.value,
            action=f"{job.company} — {job.title}: {job.status.value} → {to_state.value}",
            reason=reason if corrected else None, source="cli",
        )
    except BaseException:
        _restore(root, originals, written, job.id)
        raise
    return TransitionResult("corrected" if corrected else "changed", job.id, job.status, to_state, event)


# --- approvals --------------------------------------------------------------------

def _require_awaiting_approval(job: Job) -> None:
    if job.archived:
        raise TransitionError("archived", "the job is archived")
    if job.status != State.APPROVAL_REQUIRED:
        raise TransitionError("not_awaiting_approval", f"the job is {job.status.value}, not APPROVAL_REQUIRED")


def approve_job(root: Path, ref: str, *, confirm: Callable[[str], bool]) -> dict:
    """Record the user's approval or denial. The lock is not held while the user decides."""
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        _require_awaiting_approval(job)
        events = ledger.read_events(root)
        current = approval_status(events, job.id)
        if current == "approved":
            latest = [e for e in events if e.get("entity") == job.id and e.get("type") == "application.approved"][-1]
            return {"outcome": "unchanged", "decision": "approved", "event": latest}
        company, title = job.company, job.title
    decision = bool(confirm(f"Approve submitting the application for {company} — {title}?"))
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        _require_awaiting_approval(job)
        if approval_status(ledger.read_events(root), job.id) != current:
            raise TransitionError("state_changed", "the approval state changed while you were deciding; run the command again")
        if decision:
            event = ledger.append_event(
                root, type="application.approved", actor="user", entity=job.id, approval="approved",
                action=f"user approved the application for {company} — {title}", source="tty",
            )
            return {"outcome": "approved", "event": event}
        if current == "denied":
            return {"outcome": "unchanged", "decision": "denied"}
        event = ledger.append_event(
            root, type="application.approval_denied", actor="user", entity=job.id, approval="denied",
            action=f"user declined to approve the application for {company} — {title}", source="tty",
        )
        return {"outcome": "denied", "event": event}


# --- archive ----------------------------------------------------------------------

def archive_job(root: Path, ref: str, *, actor: str, undo: bool = False, reason: str | None = None) -> dict:
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        target = not undo
        if job.archived == target:
            return {"outcome": "unchanged"}
        original = job.path.read_bytes()
        frontmatter, body = split_frontmatter(original.decode("utf-8"))
        frontmatter = {k: normalize_value(v) for k, v in (frontmatter or {}).items()}
        now = models.utc_now()
        if target:
            frontmatter["archived"] = True
            frontmatter["archived_at"] = now
        else:
            frontmatter.pop("archived", None)
            frontmatter.pop("archived_at", None)
        frontmatter["updated_at"] = now
        atomic_write_bytes(job.path, join_frontmatter(frontmatter, body).encode("utf-8"))
        try:
            event = ledger.append_event(
                root, type="job.archived" if target else "job.unarchived", actor=actor, entity=job.id,
                action=f"{'archived' if target else 'unarchived'} {job.company} — {job.title}",
                reason=reason, source="cli",
            )
        except BaseException:
            _restore(root, {job.path: original}, [job.path], job.id)
            raise
        return {"outcome": "archived" if target else "unarchived", "event": event}
