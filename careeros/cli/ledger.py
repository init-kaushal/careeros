"""careeros ledger: read and append the audit ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ids
from careeros.core import ledger as core_ledger
from careeros.core.workspace import WorkspaceError, ensure_writable, job_files, read_job

ledger_app = typer.Typer(help="Read and extend the append-only audit ledger (ledger.jsonl).", no_args_is_help=True)


def _entity_problem(root: Path, entity: str) -> str | None:
    """Why this entity ID cannot go in the ledger, or None when it can."""
    if entity.startswith("job_"):
        for path in job_files(root):
            try:
                if read_job(path).id == entity:
                    return None
            except WorkspaceError:
                continue
        return f"no job in this workspace has the id {entity}"
    for kind, prefix in ids.PREFIXES.items():
        if entity.startswith(f"{prefix}_"):
            return None if ids.is_valid_id(kind, entity) else f"{entity!r} is not a valid {kind} id"
    return f"{entity!r} is not a recognised entity id"


@ledger_app.command("append")
def append_cmd(
    event_type: str = typer.Option(..., "--type", help="Dotted event type, for example note.added"),
    action: str = typer.Option(..., "--action", help="One sentence describing what happened"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name> (default: user at a terminal, else agent:cli)"),
    entity: Optional[str] = typer.Option(None, "--entity", help="ID of the affected entity"),
    prev_state: Optional[str] = typer.Option(None, "--prev-state"),
    new_state: Optional[str] = typer.Option(None, "--new-state"),
    approval: str = typer.Option("not_required", "--approval", help="not_required, required, approved or denied"),
    artifact: Optional[list[str]] = typer.Option(None, "--artifact", help="Workspace-relative path; repeatable"),
    source: Optional[str] = typer.Option(None, "--source"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Append one event. Reserved types are written only by their own commands."""
    root = _util.resolve_root(workspace)
    if core_ledger.is_reserved(event_type):
        _util.fail(
            f"{event_type} is reserved: only its own command writes it (transition, approve, archive, migrate, upgrade)",
            _util.EXIT_USAGE,
        )
    if entity is not None:
        problem = _entity_problem(root, entity)
        if problem:
            _util.fail(f"--entity: {problem}", _util.EXIT_USAGE)
    try:
        ensure_writable(root)
        event = core_ledger.append_event(
            root, type=event_type, actor=actor or _util.default_actor(), action=action, entity=entity,
            prev_state=prev_state, new_state=new_state, approval=approval, artifacts=artifact or (),
            source=source, reason=reason,
        )
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo(f"appended event {event['seq']} ({event['id']})")


@ledger_app.command("list")
def list_cmd(
    entity: Optional[str] = typer.Option(None, "--entity", help="Only events for this entity ID"),
    since: Optional[str] = typer.Option(None, "--since", help="Only events at or after this UTC timestamp"),
    type_prefix: Optional[str] = typer.Option(None, "--type", help="Only event types starting with this text"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List ledger events."""
    root = _util.resolve_root(workspace)
    try:
        events = core_ledger.read_events(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    selected = [
        e for e in events
        if (not entity or e["entity"] == entity)
        and (not since or e["ts"] >= since)
        and (not type_prefix or e["type"].startswith(type_prefix))
    ]
    if as_json:
        _util.echo_json(selected)
        return
    for e in selected:
        typer.echo(f"{e['seq']:>5}  {e['ts']}  {e['type']:<28} {e['entity'] or '-':<16} {e['action']}")


@ledger_app.command("verify")
def verify_cmd(workspace: Optional[Path] = _util.WorkspaceOption) -> None:
    """Check the ledger's structure, sequence numbers and hash chain."""
    root = _util.resolve_root(workspace)
    issues = core_ledger.verify_chain(root)
    if issues:
        for issue in issues:
            typer.echo(f"{issue.code}  {issue.message}")
            typer.echo(f"        fix: {issue.fix}")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"ledger OK ({len(core_ledger.read_events(root))} events)")
