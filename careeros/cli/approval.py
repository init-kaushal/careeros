"""careeros approval: propose, list and show recorded approval items (the user decides with `careeros approve`)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core.approvals import render, service
from careeros.core.ledger import LedgerError
from careeros.core.workspace import WorkspaceError

approval_app = typer.Typer(help="Propose, list and show approval items for outward messages and application submissions.",
                           no_args_is_help=True)


def _fail(message: str, code: int, as_json: bool, refusal: str | None = None) -> None:
    if as_json:
        _util.echo_json({"error": message, **({"code": refusal} if refusal else {})})
        raise typer.Exit(code)
    _util.fail(message, code)


def _guard(as_json: bool, fn):
    """Run `fn`, mapping the service's errors to exit codes: refusals and ledger or disk failures 1, usage 2."""
    try:
        return fn()
    except service.Refused as exc:
        _fail(str(exc), _util.EXIT_FAIL, as_json, exc.code)
    except LedgerError as exc:
        _fail(str(exc), _util.EXIT_FAIL, as_json)
    except WorkspaceError as exc:
        _fail(str(exc), _util.EXIT_USAGE, as_json)
    except OSError as exc:
        _fail(f"could not complete: {exc}", _util.EXIT_FAIL, as_json)


@approval_app.command("propose")
def propose_cmd(
    kind: str = typer.Option(..., "--kind", help="linkedin_invite, linkedin_message or application_submit"),
    person_url: Optional[str] = typer.Option(None, "--person-url", help="LinkedIn profile URL (invites and messages)"),
    job: Optional[str] = typer.Option(None, "--job", help="Job ID, directory name or path (application_submit)"),
    label: Optional[str] = typer.Option(None, "--label", help="Who or what the item is for, shown when the user decides"),
    content_file: Path = typer.Option(..., "--content-file", help="File holding the exact message, or the exact answers text"),
    manifest: Optional[list[str]] = typer.Option(None, "--manifest", help="Workspace-relative file to submit; repeatable"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record the exact action for the user to approve. Writes approvals/<id>.md and appends approval.proposed."""
    root = _util.resolve_root(workspace)
    try:
        text = content_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _fail(f"cannot read {content_file}: {getattr(exc, 'strerror', None) or exc}", _util.EXIT_USAGE, as_json)
    got = _guard(as_json, lambda: service.propose(
        root, kind=kind, person_url=person_url, job_ref=job, label=label, content_text=text,
        manifest_paths=list(manifest or ()), actor=actor or _util.default_actor()))
    if as_json:
        _util.echo_json({"outcome": "proposed", "id": got.id, "path": got.rel, "hash": got.hash, "kind": kind,
                         "target": got.target_id, "state": "pending", "superseded": list(got.superseded)})
        return
    typer.echo(f"proposed {got.id} ({kind}) at {got.rel}; hash {got.hash[:12]}")
    for old in got.superseded:
        typer.echo(f"  superseded {old}")
    typer.echo("  waiting for the user: run `careeros approve` in a terminal. Do not act until it shows approved.")


@approval_app.command("list")
def list_cmd(
    status: Optional[str] = typer.Option(None, "--status", help="Only items in this state"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List approval items with their derived state. Changes nothing."""
    root = _util.resolve_root(workspace)
    views = _guard(as_json, lambda: service.list_items(root, status))
    if as_json:
        _util.echo_json({"items": [v.to_dict() for v in views]})
        return
    for v in views:
        flags = f"  [{', '.join(v.problems)}]" if v.problems else ""
        typer.echo(f"{v.id}  {v.state:<10} {v.kind}  {v.hash[:12] if v.hash else '-'}{flags}")
    typer.echo(f"{len(views)} item(s)")


@approval_app.command("show")
def show_cmd(
    item_id: str = typer.Argument(..., metavar="ID", help="Approval item ID"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show an item's exact content, hash prefix and derived state. Changes nothing."""
    root = _util.resolve_root(workspace)
    view = _guard(as_json, lambda: service.show(root, item_id))
    if as_json:
        _util.echo_json(view.to_dict(content=True))
        return
    typer.echo(f"state:   {view.state}" + (f" (expires {view.expires_at})" if view.expires_at and view.state == "approved" else ""))
    if view.problems:
        typer.echo(f"problems: {', '.join(view.problems)} (the item is not usable)")
    if view.item is not None and view.hash:
        typer.echo(render.summary(view.item, view.hash))
    else:
        typer.echo(f"item:    {view.id}\nthe item file cannot be shown")
