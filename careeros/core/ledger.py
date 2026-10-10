"""Append-only, hash-chained activity ledger (ledger.jsonl)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from careeros.core import ids, models
from careeros.core.models import Issue
from careeros.core.workspace import LEDGER_REL, WorkspaceError, WorkspaceLock

ZERO_HASH = "0" * 64
APPROVALS = ("not_required", "required", "approved", "denied")
FIELDS = (
    "id", "seq", "ts", "type", "actor", "entity", "prev_state", "new_state",
    "action", "reason", "approval", "artifacts", "source", "prev",
)
RESERVED_TYPES = frozenset({
    "application.approved",
    "application.approval_denied",
    "job.status_changed",
    "job.status_corrected",
    "job.transition_rejected",
    "job.archived",
    "job.unarchived",
    "job.imported",
})
_SPEC_KEYS = {
    "type", "actor", "action", "entity", "prev_state", "new_state",
    "approval", "artifacts", "source", "reason",
}
_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_ACTOR_RE = re.compile(r"^(user|system|agent:[A-Za-z0-9._-]+)$")
_NEEDS_REASON = {"job.status_corrected", "job.transition_rejected"}


class LedgerError(WorkspaceError):
    """The ledger cannot be read or extended safely."""


def is_reserved(event_type: str) -> bool:
    return event_type in RESERVED_TYPES or event_type.startswith("workspace.")


def _line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _canon(event: dict) -> bytes:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _check_spec(spec: dict) -> None:
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        raise LedgerError(f"unknown ledger field(s): {', '.join(sorted(unknown))}")
    if not _TYPE_RE.match(str(spec.get("type", ""))):
        raise LedgerError(f"invalid event type {spec.get('type')!r}: use dotted lower-case names like 'note.added'")
    if not _ACTOR_RE.match(str(spec.get("actor", ""))):
        raise LedgerError(f"invalid actor {spec.get('actor')!r}: use 'user', 'system' or 'agent:<name>'")
    if not str(spec.get("action", "")).strip():
        raise LedgerError("action is required")
    if spec.get("approval", "not_required") not in APPROVALS:
        raise LedgerError(f"invalid approval {spec.get('approval')!r}: use one of {', '.join(APPROVALS)}")
    if spec["type"] in _NEEDS_REASON and not str(spec.get("reason") or "").strip():
        raise LedgerError(f"a reason is required for {spec['type']}")


def _read_lines(root: Path) -> list[bytes]:
    path = Path(root) / LEDGER_REL
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise LedgerError("ledger.jsonl ends with a partial line; run `careeros ledger verify`")
    return data[:-1].split(b"\n")


def read_events(root: Path) -> list[dict]:
    events = []
    for number, raw in enumerate(_read_lines(root), 1):
        try:
            events.append(json.loads(raw))
        except ValueError:
            raise LedgerError(f"ledger.jsonl line {number} is not valid JSON") from None
    return events


def append_events(root: Path, specs: list[dict]) -> list[dict]:
    """Append a batch in one write. On any failure the file is put back exactly as it was."""
    root = Path(root)
    for spec in specs:
        _check_spec(spec)
    with WorkspaceLock(root):
        path = root / LEDGER_REL
        lines = _read_lines(root)
        existed = path.exists()
        original_size = path.stat().st_size if existed else 0
        prev_hash = _line_hash(lines[-1]) if lines else ZERO_HASH
        seq = json.loads(lines[-1])["seq"] if lines else 0
        events: list[dict] = []
        buffer = bytearray()
        for spec in specs:
            seq += 1
            event = {
                "id": ids.new_id("event"),
                "seq": seq,
                "ts": models.utc_now(),
                "type": spec["type"],
                "actor": spec["actor"],
                "entity": spec.get("entity"),
                "prev_state": spec.get("prev_state"),
                "new_state": spec.get("new_state"),
                "action": spec["action"],
                "reason": spec.get("reason"),
                "approval": spec.get("approval", "not_required"),
                "artifacts": list(spec.get("artifacts") or ()),
                "source": spec.get("source"),
                "prev": prev_hash,
            }
            line = _canon(event)
            buffer += line + b"\n"
            prev_hash = _line_hash(line)
            events.append(event)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                view = memoryview(bytes(buffer))
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            _restore_size(path, original_size, existed)
            raise
        return events


def append_event(
    root: Path,
    *,
    type: str,
    actor: str,
    action: str,
    entity: str | None = None,
    prev_state: str | None = None,
    new_state: str | None = None,
    approval: str = "not_required",
    artifacts: Sequence[str] = (),
    source: str | None = None,
    reason: str | None = None,
) -> dict:
    spec = {
        "type": type, "actor": actor, "action": action, "entity": entity,
        "prev_state": prev_state, "new_state": new_state, "approval": approval,
        "artifacts": list(artifacts), "source": source, "reason": reason,
    }
    return append_events(root, [spec])[0]


def _restore_size(path: Path, size: int, existed: bool) -> None:
    if not existed:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    with open(path, "r+b") as handle:
        handle.truncate(size)


def verify_chain(root: Path) -> list[Issue]:
    """Structure, sequence and hash-chain checks. Entity and state checks live in validation."""
    path = Path(root) / LEDGER_REL
    if not path.exists():
        return []
    data = path.read_bytes()
    issues: list[Issue] = []
    if data and not data.endswith(b"\n"):
        issues.append(Issue(
            "error", "LED001", "ledger.jsonl",
            "the ledger ends with a partial line",
            "remove the partial last line, or restore ledger.jsonl from .careeros/backups or version control",
        ))
        lines = data.split(b"\n")
    else:
        lines = data[:-1].split(b"\n") if data else []
    prev_hash = ZERO_HASH
    expected_seq = 1
    for number, raw in enumerate(lines, 1):
        try:
            event = json.loads(raw)
        except ValueError:
            issues.append(Issue(
                "error", "LED001", "ledger.jsonl", f"line {number} is not valid JSON",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
            break
        if not isinstance(event, dict):
            issues.append(Issue("error", "LED001", "ledger.jsonl", f"line {number} is not a JSON object",
                                "restore ledger.jsonl from .careeros/backups or version control"))
            break
        missing = [f for f in FIELDS if f not in event]
        if missing:
            issues.append(Issue(
                "error", "LED001", "ledger.jsonl",
                f"line {number} is missing field(s): {', '.join(missing)}",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
        elif not _TYPE_RE.match(str(event["type"])):
            issues.append(Issue(
                "error", "LED001", "ledger.jsonl", f"line {number} has a malformed type {event['type']!r}",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
        seq = event.get("seq")
        if seq != expected_seq:
            issues.append(Issue(
                "error", "LED002", "ledger.jsonl",
                f"line {number}: expected seq {expected_seq}, found {seq}",
                "a line was removed, added or reordered; restore ledger.jsonl from .careeros/backups or version control",
            ))
        if event.get("prev") != prev_hash:
            issues.append(Issue(
                "error", "LED002", "ledger.jsonl",
                f"line {number}: hash chain broken (an earlier line was changed)",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
        prev_hash = _line_hash(raw)
        expected_seq = (seq if isinstance(seq, int) else expected_seq) + 1
    return issues
