"""The `evaluate` service: validate the agent half, score, write `evaluation.md`, record the event, all under the lock.

The file and the ledger are changed together: the file is written first, then one batch of events is appended; any
exception puts the previous file back (`append_events` restores the ledger itself). A process that dies between the two
steps leaves a file without its `evaluation.completed` event, which `validate` reports as EVL009 and a re-run repairs.
`evaluate` never touches the job's state, `jd.md` or `career/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from careeros.core import ledger, models
from careeros.core.evaluation import canon, integrity, ledgercheck, record
from careeros.core.evaluation.record import EVALUATION_FILE
from careeros.core.memory.store import Transaction
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError, WorkspaceLock, ensure_writable, resolve_job


class InvalidInputs(WorkspaceError):
    """The record or the posting cannot be scored (exit 1). `issues` says why."""

    def __init__(self, message: str, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


@dataclass(frozen=True)
class Summary:
    outcome: str                  # "scored" | "replaced" | "unchanged"
    job: str
    id: str
    path: str
    result: dict
    replaced: str | None = None   # the result_replaced reason, when a stored result was replaced

    def to_dict(self) -> dict:
        r = self.result
        return {"outcome": self.outcome, "job": self.job, "id": self.id, "path": self.path, "tier": r["tier"],
                "combined": r["combined"], "coverage": r["coverage"], "fit": r["fit"], "confidence": r["confidence"],
                "caps": [c["code"] for c in r["caps"]], "action_items": list(r["action_items"]),
                "replaced": self.replaced}


def _rejected(root: Path, job, actor: str, issues: list[Issue], events: list[dict]) -> None:
    reason = ",".join(sorted({i.code for i in issues}))
    last = next((e for e in reversed(events) if e.get("entity") == job.id), None)
    if not (last and last.get("type") == "evaluation.rejected" and last.get("reason") == reason):
        ledger.append_event(root, type="evaluation.rejected", actor=actor, entity=job.id, source="cli", reason=reason,
                            action=f"rejected evaluation: {len(issues)} issue(s)")
    first = issues[0]
    raise InvalidInputs(f"the evaluation cannot be scored ({reason}): {first.message}", issues)


def _all_records(root: Path) -> list[record.Record]:
    found = []
    for p in sorted((root / "jobs" / "discovered").glob(f"*/{EVALUATION_FILE}")):
        try:
            found.append(record.load(p, root))
        except record.RecordError:
            continue
    return found


def init(root: Path, ref: str) -> tuple[str, str]:
    """Write the skeleton. Returns (evaluation id, workspace-relative path). Appends no event: nothing was judged."""
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        path = job.path.parent / EVALUATION_FILE
        if path.exists():
            raise InvalidInputs(f"{record.EVALUATION_FILE} already exists for {job.id}; edit it, or run `careeros evaluate {job.id}`")
        events = ledger.read_events(root)
        data = record.skeleton(job.id, models.utc_now(), taken=record.taken_ids(root, events))
        Transaction().write(path, data)
        rec = record.load(path, root)
        return rec.id, rec.rel


def evaluate(root: Path, ref: str, actor: str) -> Summary:
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        job_dir = job.path.parent
        path = job_dir / EVALUATION_FILE
        inputs = record.read_inputs(root, job_dir)
        if inputs.career.errors:
            first = inputs.career.errors[0]
            raise WorkspaceError(f"the career memory has errors ({first.code} {first.path}: {first.message}); "
                                 "run `careeros validate`")
        if not path.is_file():
            raise InvalidInputs(f"{job.id} has no {EVALUATION_FILE}; run `careeros evaluate {job.id} --init` first")
        events = ledger.read_events(root)
        try:
            rec = record.load(path, root)
        except record.RecordError as exc:
            _rejected(root, job, actor, [Issue("error", exc.code, path.relative_to(root).as_posix(), str(exc), "")], events)
        issues = record.validate_agent(rec, inputs.jd_text, inputs.career, inputs.rubric, inputs.markets, inputs.job_id)
        if inputs.jd_text is None and not any(i.code == "EVL004" for i in issues):
            issues.append(Issue("error", "EVL004", rec.rel, f"{record.JD_FILE} is missing or unreadable", ""))
        issues += record.no_requirements(rec)
        if issues:
            _rejected(root, job, actor, issues, events)
        try:
            result = record.compute(rec, inputs)
        except WorkspaceError as exc:         # a derivation or market problem validate_agent could not foresee
            code = getattr(exc, "code", "")
            code = code if isinstance(code, str) and code.startswith("EVL") else "EVL003"
            _rejected(root, job, actor, [Issue("error", code, rec.rel, str(exc), "")], events)
        records = _all_records(root)
        twin = next((r for r in records if r.id == rec.id and r.rel != rec.rel), None)
        if twin is not None:
            message = (f"EVL009: duplicate evaluation id (also used by {twin.rel}); delete the copied {EVALUATION_FILE} "
                       f"and run `careeros evaluate {job.id} --init` for a fresh id")
            last = next((e for e in reversed(events) if e.get("entity") == job.id), None)
            reason = message.split(";")[0]
            if not (last and last.get("type") == "evaluation.rejected" and last.get("reason") == reason):
                ledger.append_event(root, type="evaluation.rejected", actor=actor, entity=job.id, source="cli",
                                    reason=reason, action="rejected evaluation: duplicate evaluation id")
            raise InvalidInputs(message, [Issue("error", "EVL009", rec.rel, message, "")])
        new = record.to_bytes(rec, result)
        old = path.read_bytes()
        digest = f"sha256:{canon.result_sha256(result)}"

        specs: list[dict] = []
        replaced = None
        if new == old and not ledgercheck.verify(rec, records, events):
            return Summary("unchanged", job.id, rec.id, rec.rel, result)
        if new != old:
            report = integrity.check(rec, inputs)
            # a tampered result always differs from the ledger's hash; the token is for what integrity cannot see
            mismatch = not report.tampered and any(i.message.startswith("mismatched") for i in ledgercheck.verify(rec, records, events))
            reasons = ([] + (["tampered"] if report.tampered else []) + (["stale"] if report.stale or report.unsupported else [])
                       + (["ledger_mismatch"] if mismatch else []))
            if report.tampered or report.unsupported or mismatch:
                replaced = ",".join(reasons)
                specs.append({"type": "evaluation.result_replaced", "actor": actor, "entity": rec.id, "source": "cli",
                              "reason": replaced, "artifacts": [rec.rel, digest],
                              "action": f"replaced the stored result ({replaced})"})
        specs.append({"type": "evaluation.completed", "actor": actor, "entity": rec.id, "source": "cli",
                      "new_state": result["tier"], "artifacts": [rec.rel, digest],
                      "action": f"evaluated: {result['tier']}, combined {result['combined']}, "
                                f"confidence {result['confidence']}"})
        tx = Transaction()
        try:
            if new != old:
                tx.write(path, new)
            ledger.append_events(root, specs)
        except BaseException:
            tx.rollback()
            raise
        return Summary("replaced" if replaced else "scored", job.id, rec.id, rec.rel, result, replaced)
