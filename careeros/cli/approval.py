"""careeros approval: propose, list and show approval items; `begin` and `done` around the action; `resolve` (the user)
for an item whose outcome is uncertain. The user decides with `careeros approve`."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ids, models
from careeros.core.approvals import render, rules, service
from careeros.core.ledger import LedgerError
from careeros.core.workspace import WorkspaceError

approval_app = typer.Typer(help="Propose, list, show, begin, done and resolve approval items for outward messages and "
                                "application submissions.",
                           no_args_is_help=True)


def _fail(message: str, code: int, as_json: bool, refusal: str | None = None) -> None:
    if as_json:
        _util.echo_json({"error": message, **({"code": refusal} if refusal else {})})
        raise typer.Exit(code)
    _util.fail(f"{message} [{refusal}]" if refusal else message, code)


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
    """List approval items with their derived state. Changes nothing. Exits 1 when ledger.jsonl has a corrupt line."""
    root = _util.resolve_root(workspace)
    views = _guard(as_json, lambda: service.list_items(root, status))
    if as_json:
        _util.echo_json({"items": [v.to_dict() for v in views]})
        return
    for v in views:
        flags = f"  [{', '.join(v.problems)}]" if v.problems else ""
        if v.state == rules.IN_FLIGHT:
            flags += f"  [in flight: its outcome is not recorded; the user runs `careeros approval resolve {v.id}`]"
        typer.echo(f"{v.id}  {v.state:<10} {v.kind}  {v.hash[:12] if v.hash else '-'}{flags}")
    typer.echo(f"{len(views)} item(s)")


@approval_app.command("show")
def show_cmd(
    item_id: str = typer.Argument(..., metavar="ID", help="Approval item ID"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show an item's exact content, hash prefix and derived state. Changes nothing. Exits 1 when ledger.jsonl has a corrupt line."""
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


@approval_app.command("begin")
def begin_cmd(
    item_id: str = typer.Argument(..., metavar="ID", help="Approval item ID"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output, including the exact content"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Claim an approved item immediately before acting (approval.started). Refused, exit 1, unless it is approved,
    unexpired and unchanged, and nothing else for the same target is in flight."""
    root = _util.resolve_root(workspace)
    now = models.utc_now()
    got = _guard(as_json, lambda: service.begin(root, item_id, actor=actor or _util.default_actor(), now=now))
    if as_json:
        _util.echo_json({"outcome": "started", "id": got.id, "kind": got.kind, "target": got.target_id, "path": got.rel,
                         "hash": got.hash, "state": rules.IN_FLIGHT, "url": got.item.target.url,
                         "content": got.item.content,
                         "manifest": [{"path": m.path, "sha256": m.sha256} for m in got.item.manifest]})
        return
    typer.echo(f"started {got.id} ({got.kind}); hash {got.hash[:12]}")
    typer.echo(f"  act now, exactly as approved, then run `careeros approval done {got.id}`.")
    typer.echo(f"  if the outcome is uncertain, do not retry: ask the user to run `careeros approval resolve {got.id}`.")


@approval_app.command("done")
def done_cmd(
    item_id: str = typer.Argument(..., metavar="ID", help="Approval item ID"),
    note: Optional[str] = typer.Option(None, "--note", help="What confirmed the action, recorded with the event"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record that the claimed action happened (approval.consumed). Never changes the job."""
    root = _util.resolve_root(workspace)
    now = models.utc_now()
    got = _guard(as_json, lambda: service.done(root, item_id, actor=actor or _util.default_actor(), note=note, now=now))
    if as_json:
        _util.echo_json({"outcome": "consumed", "id": got.id, "state": rules.CONSUMED})
        return
    typer.echo(f"recorded {got.id} as done (consumed)")


@approval_app.command("resolve")
def resolve_cmd(
    item_id: str = typer.Argument(..., metavar="ID", help="Approval item ID"),
    sent: bool = typer.Option(False, "--sent", help="The action happened"),
    not_sent: bool = typer.Option(False, "--not-sent", help="Nothing was sent or submitted"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record YOUR account of an in-flight item. Needs an interactive terminal. --not-sent re-checks the item first:
    it is released when everything still matches, otherwise voided."""
    root = _util.resolve_root(workspace)
    if sent == not_sent:
        _fail("use exactly one of --sent or --not-sent", _util.EXIT_USAGE, as_json)
    now = models.utc_now()
    if not _util.is_interactive():
        service.refuse_not_interactive(root, item_id, operation="resolve", actor=_util.default_actor(), now=now)
        shown = item_id if ids.is_valid_id("approval", item_id) else "<id>"
        _fail("careeros approval resolve records YOUR account of what happened and needs an interactive terminal. "
              f"Ask the user to run it themselves (in Claude Code: `! careeros approval resolve {shown} --sent` or "
              f"`--not-sent`). [{rules.NOT_INTERACTIVE}]", _util.EXIT_USAGE, as_json, rules.NOT_INTERACTIVE)
    got = _guard(as_json, lambda: service.resolve(root, item_id, sent=sent, now=now))
    if as_json:
        _util.echo_json({"outcome": got.outcome, "id": got.id, "reason": got.reason, "expires_at": got.expires_at})
        return
    if got.outcome == "consumed":
        typer.echo(f"recorded {got.id} as sent (consumed)")
    elif got.outcome == "released":
        typer.echo(f"released {got.id}: nothing was sent; it is approved again until {got.expires_at}")
    elif got.outcome == "expired":
        typer.echo(f"released {got.id}: nothing was sent; the approval has expired, so propose it again")
    else:
        typer.echo(f"voided {got.id}: {got.reason}. Propose a new item if it is still wanted")
