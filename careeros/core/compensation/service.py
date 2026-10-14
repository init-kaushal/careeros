"""The `comp` service: validate a job's `compensation.md`, derive, and record the outcome in the ledger (spec 7 and 8).

`compensation.md` is agent-owned and is never written here, apart from `init`, which creates the skeleton when none
exists. `run` changes nothing but `ledger.jsonl`, and only by appending `compensation.recorded` (the record is valid and
its canonical hash differs from the latest valid `recorded` event for the job) or `compensation.rejected` (the record is
invalid and the latest `compensation.*` event for the job is not the same rejection). A failed ledger read or append is
reported on the `Report` and nothing is claimed. `check_job` is the one per-job entry shared by `comp`, `validate` and
(later) `evaluate`; `validate_workspace` is read-only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

from careeros.core import ledger, models
from careeros.core.compensation import record, rules
from careeros.core.compensation.derive import Derivation, FloorInfo, cap_applies, derive_compensation, to_basis
from careeros.core.compensation.model import CompRecord
from careeros.core.evaluation import canon
from careeros.core.evaluation.markets import MarketError, Markets, MarketRow
from careeros.core.evaluation.markets import parse as parse_markets
from careeros.core.evaluation.rubric import norm_label
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError, WorkspaceLock, atomic_write_bytes, ensure_writable, resolve_job

JD_FILE = "jd.md"
UNVERIFIED = FloorInfo(None, None, None, False)
EMPTY = CompRecord(None, (), (), "")
_HASH = re.compile(r"sha256:([0-9a-f]{64})")


class InvalidInputs(WorkspaceError):
    """`--init` cannot proceed (exit 1)."""


@dataclass(frozen=True)
class Report:
    job_id: str
    rel: str
    record: CompRecord | None
    derivation: Derivation | None
    issues: list[Issue]
    recorded: bool                      # True only after a successful append of `compensation.recorded`
    floor: FloorInfo = UNVERIFIED
    exists: bool = True
    outcome: str = ""                   # recorded | unchanged | absent | rejected | rejection_repeated | not_recorded
    ledger_error: str | None = None

    @property
    def valid(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def cap_fires(self) -> bool:
        return self.derivation is not None and cap_fires(self.derivation, self.floor)

    def to_dict(self) -> dict:
        d = self.derivation
        return {
            "job": self.job_id, "path": self.rel, "exists": self.exists, "valid": self.valid, "recorded": self.recorded,
            "outcome": self.outcome, "ledger_error": self.ledger_error,
            "market": self.record.market if self.record else None,
            "floor": {"amount": self.floor.amount, "currency": self.floor.currency, "basis": self.floor.basis,
                      "verified": self.floor.verified},
            "observations": [] if d is None else [_obs_dict(r) for r in d.observations],
            "effective": None if d is None else {"status": d.status, "kind": d.kind, "low": _num(d.low), "high": _num(d.high)},
            "cap_fires": self.cap_fires,
            "notes": [] if d is None else [{"code": n.code, "ref": n.ref, "detail": n.detail} for n in d.notes],
            "issues": [i.to_dict() for i in self.issues],
        }


def _num(value) -> int | str | None:
    return None if value is None else canon.exact(value)


def _span(span) -> dict | None:
    return None if span is None else {"low": _num(span.low), "high": _num(span.high)}


def _obs_dict(r) -> dict:
    o = r.original
    return {"id": r.id, "kind": r.kind, "confidence": r.confidence, "comparable": r.comparable, "reason": r.reason,
            "original": {"low": o.low, "high": o.high, "currency": o.currency, "period": o.period, "source": o.source},
            "annualised": _span(r.annualised), "converted": _span(r.converted)}


# --- market contract ------------------------------------------------------------------------------------

def floor_for(markets: Markets, label: str | None) -> tuple[MarketRow | None, FloorInfo]:
    """Resolve `label` in the spec's fixed order (the order of `evaluation.markets.verify_market`): null, ambiguous and
    unknown (`MarketError`, mapped to CMP007 by the caller), unusable table, row without a machine-readable floor, verified."""
    name = (label or "").strip()
    if not name:
        return None, UNVERIFIED
    key = norm_label(name)
    if key in markets.ambiguous:
        raise MarketError(f"market {name!r} is ambiguous: the Markets table lists it more than once", "CMP007")
    row = markets.rows.get(key)
    if row is None:
        raise MarketError(f"market {name!r} names no row of the Markets table in profile.md", "CMP007")
    if markets.unusable or row.floor is None:
        return row, UNVERIFIED
    return row, FloorInfo(row.floor.amount, row.floor.currency, str(row.floor.basis), True)


def cap_fires(d: Derivation, floor: FloorInfo) -> bool:
    """Whether the compensation cap would fire, for display: a call to the one decision, `derive.cap_applies`."""
    amount = None if floor.amount is None else Fraction(floor.amount)
    return cap_applies(to_basis(d), amount, floor.verified)


# --- per-job check --------------------------------------------------------------------------------------

def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read(path: Path) -> bytes | OSError | None:
    """The file's bytes, None when absent, or the `OSError` that stopped the read. The one read `run` works from."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return exc


def _check(job_dir: Path, source: bytes | OSError | None, jd_text: str | None,
           markets: Markets) -> tuple[CompRecord | None, list[Issue]]:
    path = job_dir / rules.COMPENSATION_FILE
    if source is None:
        return EMPTY, []
    rel = path.relative_to(job_dir.parents[2]).as_posix()
    try:
        if isinstance(source, OSError):
            raise record.RecordError(f"{rel} cannot be read: {source}")
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise record.RecordError(f"{rel} cannot be read: {exc}") from None
        loaded = record.from_text(text, rel, source)
    except record.RecordError as exc:
        return None, [record.error_issue(exc, rel)]
    rec, _ = record.parse(loaded)
    issues = record.validate(loaded, jd_text)
    if rec is not None and rec.market is not None:
        try:
            floor_for(markets, rec.market)
        except MarketError as exc:
            issues.append(Issue("error", "CMP007", rel, str(exc),
                                "set `market` to the exact `## Markets` row label in profile.md (listed once), or `null`"))
    return rec, issues


def check_job(job_dir: Path, jd_text: str | None, markets: Markets) -> tuple[CompRecord | None, list[Issue]]:
    """Load, parse and validate a job's `compensation.md` (CMP001-CMP006 incl. CMP004 against `jd_text`), then check the
    `market` label (CMP007). The file is read once.

    An absent file is the empty record: valid, no issues, and `comp` records no event for it (so deleting a file that
    was recorded earlier appends nothing). The record is returned whenever it parsed, even when CMP004 or CMP007 were
    raised; it is None only when the file could not be parsed, so callers must look at the issues. Never raises on a
    bad file.

    `job_dir` must be `<workspace>/jobs/<group>/<slug>` (the layout `resolve_job` and `validate` use): the workspace
    root, for workspace-relative paths in issues, is taken as `job_dir.parents[2]`."""
    job_dir = Path(job_dir)
    return _check(job_dir, _read(job_dir / rules.COMPENSATION_FILE), jd_text, markets)


def stale_warnings(rec: CompRecord | None, rel: str, today: date) -> list[Issue]:
    """CMP100 for every observation older than `rules.OBSERVATION_STALE_DAYS` at `today`. Informational only."""
    if rec is None:
        return []
    limit = today - timedelta(days=rules.OBSERVATION_STALE_DAYS)
    return [Issue("warning", "CMP100", rel,
                  f"observation {o.id!r} is from {o.as_of.isoformat()}, more than {rules.OBSERVATION_STALE_DAYS} days old",
                  "check the pay again and update the observation, or add a newer one")
            for o in rec.observations if o.as_of < limit]


def _today(now: str | None) -> date:
    return date.fromisoformat((now or models.utc_now())[:10])


def validate_workspace(root: Path, now: str | None = None) -> list[Issue]:
    """CMP001-CMP007 and CMP100 for every `jobs/*/*/compensation.md`. Read-only; absent files produce nothing."""
    root = Path(root)
    files = sorted(root.glob(f"jobs/*/*/{rules.COMPENSATION_FILE}"))
    if not files:
        return []
    markets = parse_markets(_text(root / "profile.md") or "")
    issues: list[Issue] = []
    today = None
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            rec, found = check_job(path.parent, _text(path.parent / JD_FILE), markets)
            issues += found
            if rec is not None and rec.observations:
                today = today or _today(now)
                issues += stale_warnings(rec, rel, today)
        except Exception as exc:        # validate must report, never crash, on a hand-edited file
            issues.append(Issue("error", "CMP001", rel, f"could not be checked: {exc!r}", record._FIX_FILE))
    return issues


# --- ledger dedupe (pure) -------------------------------------------------------------------------------

def _hash_of(event: object) -> str | None:
    arts = event.get("artifacts") if isinstance(event, dict) else None
    if not isinstance(arts, list) or len(arts) != 2 or not all(isinstance(a, str) for a in arts):
        return None
    match = _HASH.fullmatch(arts[1])
    return match.group(1) if match else None


def latest_valid_recorded(events: list, job_id: str) -> str | None:
    """The hex hash of the latest well-formed `compensation.recorded` event for `job_id`, else None. Malformed events,
    other entities and bad hash strings are ignored (they count as no baseline)."""
    for event in reversed(events):
        if (isinstance(event, dict) and event.get("type") == "compensation.recorded" and event.get("entity") == job_id):
            found = _hash_of(event)
            if found:
                return found
    return None


def is_duplicate_rejection(events: list, job_id: str, raw_hash: str, codes) -> bool:
    """True when the latest `compensation.*` event for `job_id` is a rejection with this raw-bytes hash and code set."""
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("entity") != job_id:
            continue
        kind = event.get("type")
        if not (isinstance(kind, str) and kind.startswith("compensation.")):
            continue
        return (kind == "compensation.rejected" and event.get("reason") == ",".join(sorted(set(codes)))
                and _hash_of(event) == raw_hash)
    return False


# --- commands -------------------------------------------------------------------------------------------

def init(root: Path, ref: str) -> str:
    """Write the skeleton (atomically) and return its workspace-relative path. Refuses an existing file; no event."""
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        path = job.path.parent / rules.COMPENSATION_FILE
        if path.exists():
            raise InvalidInputs(f"{rules.COMPENSATION_FILE} already exists for {job.id}; edit it, or run `careeros comp {job.id}`")
        atomic_write_bytes(path, record.skeleton(models.utc_now()))
        return path.relative_to(root).as_posix()


def _append(root: Path, spec: dict) -> str | None:
    try:
        ledger.append_events(root, [spec])
    except Exception as exc:        # a malformed ledger can raise anything; report it, claim nothing
        return str(exc) or type(exc).__name__
    return None


def run(root: Path, ref: str, actor: str) -> Report:
    """Validate and derive, then append the ledger event the outcome calls for. Never writes `compensation.md`."""
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        job_dir = job.path.parent
        path = job_dir / rules.COMPENSATION_FILE
        rel = path.relative_to(root).as_posix()
        markets = parse_markets(_text(root / "profile.md") or "")
        source = _read(path)
        rec, issues = _check(job_dir, source, _text(job_dir / JD_FILE), markets)
        exists = source is not None
        floor = UNVERIFIED
        errors = [i for i in issues if i.severity == "error"]
        derivation = None
        if not errors and rec is not None:
            floor = floor_for(markets, rec.market)[1]
            derivation = derive_compensation(rec, floor)
            if rec.observations:
                issues = issues + stale_warnings(rec, rel, _today(None))
        try:
            events = ledger.read_events(root)
        except Exception as exc:
            return Report(job.id, rel, rec, derivation, issues, False, floor, exists, "not_recorded",
                          f"could not read the ledger: {exc}")

        def finish(outcome: str, spec: dict | None) -> Report:
            error = _append(root, spec) if spec else None
            if error:
                return Report(job.id, rel, rec, derivation, issues, False, floor, exists, "not_recorded", error)
            return Report(job.id, rel, rec, derivation, issues, bool(spec) and outcome == "recorded", floor, exists, outcome)

        if not errors:
            if not exists:
                return finish("absent", None)
            digest = record.stamp(rec, None)
            if latest_valid_recorded(events, job.id) == digest:
                return finish("unchanged", None)
            count = len(rec.observations)
            return finish("recorded", {
                "type": "compensation.recorded", "actor": actor, "entity": job.id, "source": "cli",
                "artifacts": [rel, f"sha256:{digest}"],
                "action": f"recorded compensation: {count} observation{'' if count == 1 else 's'}"})
        codes = sorted({i.code for i in errors})
        raw_hash = hashlib.sha256(source if isinstance(source, bytes) else b"").hexdigest()
        if is_duplicate_rejection(events, job.id, raw_hash, codes):
            return finish("rejection_repeated", None)
        return finish("rejected", {
            "type": "compensation.rejected", "actor": actor, "entity": job.id, "source": "cli",
            "reason": ",".join(codes), "artifacts": [rel, f"sha256:{raw_hash}"],
            "action": f"rejected compensation: {len(errors)} issue(s)"})
