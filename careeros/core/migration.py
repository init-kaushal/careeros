"""Backups with manifests, schema migration and framework upgrade."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from careeros.core import ids, ledger, models, versions
from careeros.core.models import State, WorkspaceMeta
from careeros.core.workspace import (
    BACKUPS_REL,
    LEDGER_REL,
    META_REL,
    WorkspaceError,
    WorkspaceLock,
    atomic_write_bytes,
    ensure_writable,
    job_files,
    join_frontmatter,
    load_meta,
    save_meta,
    split_frontmatter,
)
from careeros.workspace import scaffold as _scaffold

LEGACY_STATUS: dict[str, State] = {
    "discovered": State.DISCOVERED,
    "applied": State.APPLIED,
    "interview": State.SCREEN,
    "offer": State.OFFER,
    "accepted": State.ACCEPTED,
    "declined": State.WITHDRAWN,
    "closed": State.WITHDRAWN,
    "rejected": State.REJECTED,
}


class MigrationError(WorkspaceError):
    """A migration or upgrade could not be completed safely."""


@dataclass
class JobChange:
    path: Path
    rel: str
    original: bytes
    new_text: str
    job_id: str
    status: State
    legacy_status: str


@dataclass
class MigrationPlan:
    root: Path
    source_schema: int
    target_schema: int
    meta_to_write: WorkspaceMeta | None
    job_changes: list[JobChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.meta_to_write is None and not self.job_changes


@dataclass
class OperationResult:
    status: str  # "complete" | "noop"
    backup: Path | None
    jobs: int
    manifest: dict | None


# --- helpers ----------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _new_backup_dir(root: Path, kind: str) -> Path:
    stamp = models.utc_now().replace("-", "").replace(":", "")
    base = root / BACKUPS_REL
    candidate = base / f"{stamp}-{kind}"
    n = 2
    while candidate.exists():
        candidate = base / f"{stamp}-{kind}-{n}"
        n += 1
    candidate.mkdir(parents=True)
    return candidate


def _write_manifest(backup: Path, manifest: dict) -> None:
    atomic_write_bytes(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))


def _backup_files(backup: Path, items: list[tuple[str, bytes]]) -> None:
    for rel, data in items:
        destination = backup / rel
        atomic_write_bytes(destination, data)
        if _sha(destination.read_bytes()) != _sha(data):
            raise MigrationError(f"backup verification failed for {rel}; nothing was changed")


def _detect_runtimes(root: Path) -> tuple[str, ...]:
    return tuple(name for name, cfg in _scaffold._RUNTIME_CONFIG.items() if (root / cfg["sentinel"]).exists())


def find_incomplete_operations(root: Path) -> list[Path]:
    base = Path(root) / BACKUPS_REL
    pending: list[Path] = []
    if base.is_dir():
        for manifest in sorted(base.glob("*/manifest.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except ValueError:
                pending.append(manifest.parent)
                continue
            if data.get("status") == "pending":
                pending.append(manifest.parent)
    return pending


# --- legacy parsing ----------------------------------------------------------------------

_HEADING = re.compile(r"^# (.+?)[ \t]*$", re.M)


def _bullet(text: str, name: str) -> str | None:
    match = re.search(rf"^- \*\*{re.escape(name)}:\*\*[ \t]*(.*?)[ \t]*$", text, re.M)
    return match.group(1) if match and match.group(1) else None


def _parse_legacy(text: str) -> tuple[str, str, str, str, str]:
    heading = _HEADING.search(text)
    if not heading:
        raise ValueError("no '# Title at Company' heading")
    title, separator, company = heading.group(1).rpartition(" at ")
    if not separator or not title.strip() or not company.strip():
        raise ValueError(f"heading {heading.group(1)!r} does not look like '# Title at Company'")
    url = _bullet(text, "URL")
    if not url:
        raise ValueError("missing the '- **URL:**' line")
    discovered = _bullet(text, "Discovered")
    if not discovered:
        raise ValueError("missing the '- **Discovered:**' line")
    date_match = re.match(r"\d{4}-\d{2}-\d{2}", discovered)
    if not date_match:
        raise ValueError(f"Discovered {discovered!r} does not start with YYYY-MM-DD")
    try:
        models.date_to_utc(date_match.group(0))
    except ValueError:
        raise ValueError(f"Discovered {discovered!r} is not a real date") from None
    status = _bullet(text, "Status")
    if not status:
        raise ValueError("missing the '- **Status:**' line")
    return title.strip(), company.strip(), url, date_match.group(0), status.strip().lower()


# --- migration ---------------------------------------------------------------------------

def plan_migration(root: Path) -> MigrationPlan:
    """Compute everything in memory. Nothing is written."""
    root = Path(root)
    meta = load_meta(root)
    source = 0 if meta is None else meta.schema_version
    if source > versions.SCHEMA_VERSION:
        raise WorkspaceError(
            f"workspace schema {source} is newer than this CareerOS supports ({versions.SCHEMA_VERSION}); "
            "upgrade CareerOS first"
        )
    now = models.utc_now()
    plan = MigrationPlan(root, source, versions.SCHEMA_VERSION, None)
    if meta is None:
        plan.meta_to_write = WorkspaceMeta(
            versions.SCHEMA_VERSION, versions.installed_version(), now, now, _detect_runtimes(root)
        )
    existing_ids: set[str] = set()
    legacy: list[Path] = []
    for path in job_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            frontmatter, _ = split_frontmatter(path.read_bytes().decode("utf-8"))
        except (WorkspaceError, UnicodeDecodeError) as exc:
            plan.errors.append(f"{rel}: cannot read the file ({exc})")
            continue
        if frontmatter is None:
            legacy.append(path)
        elif frontmatter.get("id"):
            existing_ids.add(str(frontmatter["id"]))
    interview: list[str] = []
    for path in legacy:
        rel = path.relative_to(root).as_posix()
        original = path.read_bytes()
        if b"\r\n" in original:
            plan.errors.append(f"{rel}: uses CRLF line endings; convert it to LF line endings first")
            continue
        text = original.decode("utf-8")
        try:
            title, company, url, discovered, legacy_status = _parse_legacy(text)
        except ValueError as exc:
            plan.errors.append(f"{rel}: {exc}")
            continue
        state = LEGACY_STATUS.get(legacy_status)
        if state is None:
            plan.errors.append(
                f"{rel}: unknown Status {legacy_status!r} (known: {', '.join(sorted(LEGACY_STATUS))})"
            )
            continue
        job_id = ids.new_id("job", existing_ids)
        existing_ids.add(job_id)
        frontmatter = {
            "id": job_id,
            "type": "job",
            "schema": versions.SCHEMA_VERSION,
            "status": state.value,
            "company": company,
            "title": title,
            "url": url,
            "created_at": models.date_to_utc(discovered),
            "created_at_precision": "date",
            "updated_at": now,
        }
        plan.job_changes.append(
            JobChange(path, rel, original, join_frontmatter(frontmatter, text), job_id, state, legacy_status)
        )
        if legacy_status == "interview":
            interview.append(rel)
    if interview:
        plan.notes.append(
            f"{len(interview)} job(s) had Status 'interview' and are mapped to SCREEN because the old field "
            f"does not record the stage; correct them with `careeros transition --force --reason ...`: "
            + ", ".join(interview)
        )
    if plan.job_changes:
        plan.notes.append(
            f"{len(plan.job_changes)} job(s) have date-only creation times; they are stored as midnight UTC "
            "with created_at_precision: date"
        )
    return plan


def _verify_unchanged(changes: list[JobChange]) -> None:
    for change in changes:
        if _sha(change.path.read_bytes()) != _sha(change.original):
            raise MigrationError(f"{change.rel} changed since the plan was made; run the command again")


def run_migration(root: Path, plan: MigrationPlan) -> OperationResult:
    root = Path(root)
    if plan.errors:
        raise MigrationError("; ".join(plan.errors))
    ensure_writable(root)
    if plan.empty:
        return OperationResult("noop", None, 0, None)
    with WorkspaceLock(root):
        ensure_writable(root)
        _verify_unchanged(plan.job_changes)
        if plan.meta_to_write is not None and (root / META_REL).exists():
            raise MigrationError("workspace metadata appeared since the plan was made; run the command again")
        ledger_path = root / LEDGER_REL
        ledger_before = ledger_path.read_bytes() if ledger_path.exists() else None
        backup = _new_backup_dir(root, "migrate")
        backup_items = [(c.rel, c.original) for c in plan.job_changes]
        if ledger_before is not None:
            backup_items.append((LEDGER_REL.as_posix(), ledger_before))
        _backup_files(backup, backup_items)
        files: list[dict] = [
            {"path": c.rel, "existed": True, "before_sha256": _sha(c.original), "after_sha256": None}
            for c in plan.job_changes
        ]
        if plan.meta_to_write is not None:
            files.append({"path": META_REL.as_posix(), "existed": False, "before_sha256": None, "after_sha256": None})
        files.append({
            "path": LEDGER_REL.as_posix(),
            "existed": ledger_before is not None,
            "before_sha256": _sha(ledger_before) if ledger_before is not None else None,
            "after_sha256": None,
        })
        manifest = {
            "version": 1,
            "kind": "migrate",
            "created_at": models.utc_now(),
            "tool_version": versions.installed_version(),
            "source_schema": plan.source_schema,
            "target_schema": plan.target_schema,
            "status": "pending",
            "files": files,
        }
        _write_manifest(backup, manifest)
        written: list[JobChange] = []
        meta_created = False
        try:
            if plan.meta_to_write is not None:
                save_meta(root, plan.meta_to_write)
                meta_created = True
            for change in plan.job_changes:
                atomic_write_bytes(change.path, change.new_text.encode("utf-8"))
                written.append(change)
            backup_rel = backup.relative_to(root).as_posix()
            specs = [{
                "type": "workspace.migrated", "actor": "system", "source": "migration",
                "action": f"migrated workspace from schema {plan.source_schema} to {plan.target_schema}: "
                          f"{len(plan.job_changes)} job(s) given ids",
                "artifacts": [f"{backup_rel}/manifest.json"],
            }]
            for change in plan.job_changes:
                specs.append({
                    "type": "job.imported", "actor": "system", "source": "migration",
                    "entity": change.job_id, "new_state": change.status.value,
                    "action": f"imported legacy job {change.rel} as {change.status.value}",
                    "artifacts": [change.rel],
                })
            ledger.append_events(root, specs)
        except BaseException:
            for change in written:
                atomic_write_bytes(change.path, change.original)
            if meta_created:
                (root / META_REL).unlink(missing_ok=True)
            manifest["status"] = "rolled_back"
            _write_manifest(backup, manifest)
            raise
        for record in files:
            current = root / record["path"]
            record["after_sha256"] = _sha(current.read_bytes()) if current.exists() else None
        manifest["status"] = "complete"
        _write_manifest(backup, manifest)
        return OperationResult("complete", backup, len(plan.job_changes), manifest)


# --- upgrade -----------------------------------------------------------------------------

@dataclass
class UpgradeItem:
    rel: str
    status: str  # "new" | "changed" | "unchanged"
    src: Path
    dst: Path
    before: bytes | None


@dataclass
class UpgradePlan:
    root: Path
    items: list[UpgradeItem]
    meta: WorkspaceMeta | None
    schema_behind: bool

    @property
    def pending(self) -> list[UpgradeItem]:
        return [i for i in self.items if i.status != "unchanged"]

    @property
    def framework_stale(self) -> bool:
        return self.meta is not None and versions.compare_versions(versions.installed_version(), self.meta.framework_version) > 0

    @property
    def noop(self) -> bool:
        return not self.pending and not self.framework_stale


def plan_upgrade(root: Path) -> UpgradePlan:
    root = Path(root)
    meta = load_meta(root)
    items: list[UpgradeItem] = []
    for _runtime, cfg in _scaffold._RUNTIME_CONFIG.items():
        sentinel = cfg["sentinel"]
        if not (root / sentinel).exists():
            continue
        templates = cfg["templates_dir"]
        for src in sorted(templates.rglob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(templates).as_posix()
            if not (rel == sentinel or any(rel.startswith(p) for p in cfg["framework_prefixes"])):
                continue
            dst = root / rel
            if not dst.exists():
                items.append(UpgradeItem(rel, "new", src, dst, None))
            else:
                current = dst.read_bytes()
                items.append(UpgradeItem(rel, "unchanged" if current == src.read_bytes() else "changed", src, dst, current))
    schema_behind = meta is None or meta.schema_version < versions.SCHEMA_VERSION
    return UpgradePlan(root, items, meta, schema_behind)


def run_upgrade(root: Path, plan: UpgradePlan) -> OperationResult:
    root = Path(root)
    ensure_writable(root)
    if plan.noop:
        return OperationResult("noop", None, 0, None)
    with WorkspaceLock(root):
        meta = ensure_writable(root)
        for item in plan.pending:
            if item.status == "changed" and item.dst.read_bytes() != item.before:
                raise MigrationError(f"{item.rel} changed since the plan was made; run the command again")
            if item.status == "new" and item.dst.exists():
                raise MigrationError(f"{item.rel} appeared since the plan was made; run the command again")
        meta_path = root / META_REL
        meta_before = meta_path.read_bytes() if meta is not None else None
        backup = _new_backup_dir(root, "upgrade")
        _backup_files(backup, [(i.rel, i.before) for i in plan.pending if i.status == "changed" and i.before is not None])
        files: list[dict] = [
            {
                "path": i.rel,
                "existed": i.before is not None,
                "before_sha256": _sha(i.before) if i.before is not None else None,
                "after_sha256": None,
            }
            for i in plan.pending
        ]
        if meta_before is not None:
            _backup_files(backup, [(META_REL.as_posix(), meta_before)])
            files.append({"path": META_REL.as_posix(), "existed": True, "before_sha256": _sha(meta_before), "after_sha256": None})
        manifest = {
            "version": 1,
            "kind": "upgrade",
            "created_at": models.utc_now(),
            "tool_version": versions.installed_version(),
            "source_schema": meta.schema_version if meta else 0,
            "target_schema": meta.schema_version if meta else 0,
            "status": "pending",
            "files": files,
        }
        _write_manifest(backup, manifest)
        written: list[UpgradeItem] = []
        try:
            for item in plan.pending:
                atomic_write_bytes(item.dst, item.src.read_bytes())
                written.append(item)
            if meta is not None:
                now = models.utc_now()
                save_meta(root, WorkspaceMeta(
                    meta.schema_version, versions.installed_version(), meta.created_at, now, meta.runtimes
                ))
                if (root / LEDGER_REL).exists():
                    changed = len(plan.pending)
                    ledger.append_event(
                        root, type="workspace.upgraded", actor="system", source="upgrade",
                        action=f"upgraded framework files to CareerOS {versions.installed_version()}: {changed} file(s) written",
                        artifacts=[f"{backup.relative_to(root).as_posix()}/manifest.json"],
                    )
        except BaseException:
            for item in written:
                if item.before is None:
                    item.dst.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(item.dst, item.before)
            if meta_before is not None:
                atomic_write_bytes(meta_path, meta_before)
            manifest["status"] = "rolled_back"
            _write_manifest(backup, manifest)
            raise
        for record in files:
            current = root / record["path"]
            record["after_sha256"] = _sha(current.read_bytes()) if current.exists() else None
        manifest["status"] = "complete"
        _write_manifest(backup, manifest)
        return OperationResult("complete", backup, len(plan.pending), manifest)
