"""Deterministic workspace validation. Read-only: it never changes a file."""

from __future__ import annotations

import json
from pathlib import Path

from careeros.core import ids, ledger, models, versions
from careeros.core.memory import store
from careeros.core.models import Issue, State
from careeros.core.state_machine import (
    PIPE_ENTRY,
    STATUS_BULLET,
    bullet_matches,
    display_for,
    icon_for,
    is_legal,
    recorded_state,
)
from careeros.core.workspace import (
    JOB_KEYS,
    WorkspaceError,
    job_files,
    load_meta,
    normalize_value,
    split_frontmatter,
)

_FOLD_MARKER = "do not read past this line"
_RESTORE_LEDGER = "restore ledger.jsonl from .careeros/backups or version control"


def _above_fold(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if _FOLD_MARKER in line:
            break
        kept.append(line)
    return "".join(kept)


def _lenient_events(root: Path) -> list[dict]:
    path = root / "ledger.jsonl"
    events: list[dict] = []
    if not path.exists():
        return events
    for raw in path.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def validate_workspace(root: Path) -> list[Issue]:
    root = Path(root)
    issues: list[Issue] = []

    def add(severity: str, code: str, path: str, message: str, fix: str) -> None:
        issues.append(Issue(severity, code, path, message, fix))

    # --- structure ---
    if not (root / "profile.md").is_file():
        add("error", "STR001", "profile.md", "profile.md is missing", "run the onboarding skill, or create profile.md")
    if not (root / "jobs").is_dir():
        add("error", "STR001", "jobs/", "the jobs/ directory is missing", "create jobs/discovered/, or restore jobs/ from a backup")
    for rel in ("activity.md", "jobs/pipeline.md"):
        if not (root / rel).is_file():
            add("warning", "STR002", rel, f"{rel} is missing", f"create {rel}, or run the skill that writes it")

    # --- workspace metadata and versions ---
    meta_path = ".careeros/workspace.yaml"
    installed = versions.installed_version()
    meta = None
    meta_broken = False
    try:
        meta = load_meta(root)
    except WorkspaceError as exc:
        meta_broken = True
        add("error", "WS003", meta_path, str(exc), "fix the file, or restore it from .careeros/backups")
    if meta is None and not meta_broken:
        add("error", "WS001", meta_path, "no workspace metadata: this is a legacy (schema 0) workspace", "run `careeros migrate`")
    if meta is not None:
        if meta.schema_version > versions.SCHEMA_VERSION:
            add("error", "WS002", meta_path,
                f"workspace schema {meta.schema_version} is newer than this CareerOS supports ({versions.SCHEMA_VERSION})",
                "upgrade CareerOS before changing this workspace")
        relation = versions.compare_versions(installed, meta.framework_version)
        if relation > 0:
            add("warning", "WS004", meta_path,
                f"CareerOS {installed} is installed; the workspace was last updated by {meta.framework_version}",
                "run `careeros upgrade`")
        elif relation < 0:
            add("error", "WS005", meta_path,
                f"the workspace was last updated by CareerOS {meta.framework_version}, newer than the installed {installed}",
                "upgrade CareerOS before changing this workspace")

    # --- jobs ---
    jobs: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}
    all_ids: set[str] = set()
    for path in job_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except WorkspaceError as exc:
            add("error", "JOB002", rel, f"the frontmatter could not be read: {exc}", "fix the YAML between the --- lines")
            continue
        if frontmatter is None:
            add("warning", "JOB001", rel, "no frontmatter (legacy job file)", "run `careeros migrate`")
            continue
        fm = {k: normalize_value(v) for k, v in frontmatter.items()}
        missing = [k for k in JOB_KEYS if k not in fm]
        if missing:
            add("error", "JOB002", rel, f"frontmatter is missing: {', '.join(missing)}", "add the missing key(s) between the --- lines")
        job_id = fm.get("id")
        if job_id is not None:
            all_ids.add(str(job_id))
        valid_id = False
        if "id" in fm:
            if not ids.is_valid_id("job", job_id):
                add("error", "JOB003", rel, f"id {job_id!r} is not a valid job id", "use the form job_ followed by 10 lowercase base32 characters")
            elif job_id in seen_ids:
                add("error", "JOB003", rel, f"id {job_id} is also used by {seen_ids[job_id]}", "give one of the two jobs a new id")
            else:
                seen_ids[job_id] = rel
                valid_id = True
        state: State | None = None
        if "status" in fm:
            try:
                state = State(fm["status"])
            except ValueError:
                add("error", "JOB004", rel, f"status {fm['status']!r} is not a valid state",
                    "use one of: " + ", ".join(s.value for s in State))
        for key in ("created_at", "updated_at", "archived_at"):
            if key in fm and not models.is_utc_timestamp(fm[key]):
                add("error", "JOB006", rel, f"{key} {fm[key]!r} is not a UTC ISO-8601 timestamp", "use the form 2026-10-06T09:15:00Z")
        bullet = STATUS_BULLET.search(body)
        if state is not None and bullet and not bullet_matches(bullet.group(2), state):
            add("warning", "JOB005", rel,
                f"the Status line says {bullet.group(2).strip()!r} but the frontmatter status is {state.value}",
                f"the frontmatter is the source of truth: edit the Status line to read {display_for(state)!r}, or change the state with `careeros transition {job_id} --to <STATE>` (add --force --reason \"...\" for a correction) so file, pipeline and ledger agree")
        if valid_id:
            jobs[str(job_id)] = {
                "path": rel,
                "state": state,
                "url": str(fm.get("url") or "").rstrip("/"),
                "archived": bool(fm.get("archived", False)),
            }

    # --- pipeline ---
    pipeline = root / "jobs" / "pipeline.md"
    if pipeline.is_file() and jobs:
        entries: dict[str, str] = {}
        for match in PIPE_ENTRY.finditer(_above_fold(pipeline.read_text(encoding="utf-8"))):
            entries.setdefault(match.group(4).rstrip("/"), match.group(2))
        by_url = {info["url"]: info for info in jobs.values() if info["url"]}
        for url, icon in entries.items():
            info = by_url.get(url)
            if info is None:
                add("warning", "PIPE001", "jobs/pipeline.md", f"pipeline entry {url} matches no job",
                    "remove the line, or save the job with the browse skill")
            elif info["state"] is not None and icon != icon_for(info["state"]):
                add("warning", "PIPE003", "jobs/pipeline.md",
                    f"the pipeline shows [{icon}] for {url} but the job is {info['state'].value}, which is [{icon_for(info['state'])}]",
                    "change the icon, or run `careeros transition` so file, pipeline and ledger agree")
        for url, info in by_url.items():
            if url not in entries and not info["archived"]:
                add("warning", "PIPE002", info["path"], "the job has no line in jobs/pipeline.md",
                    "add a pipeline line for it, or archive the job with `careeros archive`")

    # --- career memory ---
    issues.extend(store.load_career(root).issues)

    # --- ledger ---
    issues.extend(ledger.verify_chain(root))
    events = _lenient_events(root)
    reported: set[str] = set()
    memory_ids = store.known_ids(root)
    for event in events:
        entity = event.get("entity")
        if not isinstance(entity, str) or entity in reported:
            continue
        if entity.startswith("job_") and entity not in all_ids:
            reported.add(entity)
            add("error", "LED003", "ledger.jsonl",
                f"event {event.get('seq')} refers to {entity}, but no job file has that id",
                "restore the job directory from .careeros/backups or version control; archive jobs with `careeros archive` instead of deleting them")
        elif entity.startswith(store.MEMORY_ID_PREFIXES) and entity not in memory_ids:
            reported.add(entity)
            add("error", "LED003", "ledger.jsonl",
                f"event {event.get('seq')} refers to {entity}, but no career file has that id",
                "restore the file from .careeros/backups or version control; retire facts with `careeros memory retire` instead of deleting them")
    for event in events:
        if event.get("type") != "job.status_changed":
            continue
        try:
            before, after = State(event.get("prev_state")), State(event.get("new_state"))
        except ValueError:
            continue
        if not is_legal(before, after):
            add("error", "LED004", "ledger.jsonl",
                f"event {event.get('seq')} moved {event.get('entity')} from {before.value} to {after.value}, which the state machine forbids",
                "record rule-breaking changes with `careeros transition --force --reason \"...\"`, which writes a job.status_corrected event")
    for job_id, info in jobs.items():
        recorded = recorded_state(events, job_id)
        if info["state"] is not None and recorded is not None and recorded != info["state"].value:
            add("warning", "LED005", info["path"],
                f"the job shows {info['state'].value} but its latest ledger event recorded {recorded}",
                f"run `careeros transition {job_id} --to {info['state'].value}` to bring the ledger up to date")
    return issues
