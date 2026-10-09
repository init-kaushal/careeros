"""careeros status."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ledger, versions
from careeros.core.workspace import WorkspaceError, job_files, load_meta, read_job


def status_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """One-screen summary of versions, jobs and recent activity. Changes nothing."""
    root = _util.resolve_root(workspace)
    try:
        meta = load_meta(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    installed = versions.installed_version()
    counts: Counter[str] = Counter()
    archived = 0
    unmigrated = 0
    for path in job_files(root):
        try:
            job = read_job(path)
        except WorkspaceError:
            unmigrated += 1
            continue
        if job.archived:
            archived += 1
        else:
            counts[job.status.value] += 1
    ledger_note = None
    try:
        events = ledger.read_events(root)
    except WorkspaceError as exc:
        events, ledger_note = [], str(exc)
    relation = versions.compare_versions(installed, meta.framework_version) if meta else 0
    newer = bool(meta and (relation < 0 or meta.schema_version > versions.SCHEMA_VERSION))
    data = {
        "workspace": str(root),
        "installed_version": installed,
        "framework_version": meta.framework_version if meta else None,
        "schema_version": meta.schema_version if meta else 0,
        "jobs": dict(counts),
        "archived": archived,
        "unmigrated_jobs": unmigrated,
        "recent_events": [
            {"seq": e["seq"], "ts": e["ts"], "type": e["type"], "entity": e["entity"], "action": e["action"]}
            for e in events[-5:]
        ],
        "ledger_note": ledger_note,
        "migrate_needed": meta is None or unmigrated > 0 or meta.schema_version < versions.SCHEMA_VERSION,
        "upgrade_available": bool(meta and relation > 0),
        "workspace_newer": newer,
    }
    if as_json:
        _util.echo_json(data)
        return
    typer.echo(f"Workspace   {root}")
    typer.echo(f"CareerOS    {installed}   workspace framework {data['framework_version'] or '—'}   schema {data['schema_version']}")
    typer.echo("Jobs        " + (", ".join(f"{state} {n}" for state, n in counts.items()) or "none") + f"   archived {archived}")
    if unmigrated:
        typer.echo(f"            {unmigrated} job file(s) have no frontmatter yet")
    typer.echo("Recent      " + ("(no events)" if not events else ""))
    for event in data["recent_events"]:
        typer.echo(f"  {event['seq']:>4}  {event['ts']}  {event['type']}  {event['action']}")
    if ledger_note:
        typer.echo(f"Ledger      {ledger_note}")
    if data["workspace_newer"]:
        typer.echo("Next        this workspace was last changed by a newer CareerOS: upgrade CareerOS")
    if data["migrate_needed"]:
        typer.echo("Next        run `careeros migrate` to add versions and ids")
    if data["upgrade_available"]:
        typer.echo("Next        run `careeros upgrade` to refresh skill files")
