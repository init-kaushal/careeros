"""careeros approve: record the user's own decision to submit an application, or on approval items (one, or the queue)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ids, models
from careeros.core import state_machine as sm
from careeros.core.approvals import rules, service
from careeros.core.ledger import LedgerError
from careeros.core.workspace import LockTimeout, WorkspaceError

_ANSWERS = {"a": "approve", "approve": "approve", "d": "decline", "decline": "decline", "s": "skip", "skip": "skip"}


def approve_cmd(
    target: Optional[str] = typer.Argument(
        None, metavar="[JOB|ID]",
        help="Job ID, directory name or path; an approval item ID (apr_…); or nothing for the queue of pending items",
    ),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Approve (or decline) submitting an application, one approval item, or every pending item. Needs an
    interactive terminal.

    This is a human-confirmation guard against accidental execution, not a security boundary.
    """
    root = _util.resolve_root(workspace)
    if target is not None and not target.startswith(f"{ids.PREFIXES['approval']}_"):
        _approve_job(root, target)
        return
    if not _util.is_interactive():
        if target is not None:
            service.refuse_not_interactive(root, target, operation="approve", actor=_util.default_actor(),
                                           clock=models.utc_now)
        hint = "! careeros approve" + (f" {target}" if target and ids.is_valid_id("approval", target) else "")
        typer.echo(
            "careeros approve records YOUR decision and needs an interactive terminal. "
            f"Ask the user to run it themselves (in Claude Code: `{hint}`). [{rules.NOT_INTERACTIVE}]",
            err=True,
        )
        raise typer.Exit(_util.EXIT_USAGE)
    if target is None:
        _queue(root)
    else:
        _single(root, target)


def _ask(text: str) -> str:
    """Show the item in full and ask until the answer is explicit. EOF or Ctrl-C aborts (nothing recorded)."""
    typer.echo("")
    typer.echo(text)
    while True:
        answer = _ANSWERS.get(typer.prompt("Approve, decline or skip? [a/d/s]").strip().lower())
        if answer:
            return answer
        typer.echo("Answer a (approve), d (decline) or s (skip).")


def _guard(fn):
    try:
        return fn()
    except service.Refused as exc:
        _util.fail(f"{exc} [{exc.code}]")
    except LedgerError as exc:
        _util.fail(str(exc))
    except LockTimeout:
        _util.fail(_util.BUSY)
    except WorkspaceError as exc:
        _util.fail(str(exc), _util.EXIT_USAGE)
    except OSError as exc:
        _util.fail(f"could not complete: {exc}")


def _single(root: Path, item_id: str) -> None:
    decision = _guard(lambda: service.decide(root, item_id, confirm=_ask, clock=models.utc_now))
    if decision.outcome == "approved":
        typer.echo(f"Approved {item_id}. It may be used once, within {rules.APPROVAL_TTL_HOURS} hours.")
        return
    if decision.outcome == "declined":
        typer.echo(f"Declined {item_id}. A new decision needs a new proposal.")
    else:
        typer.echo(f"Skipped; {item_id} stays pending.")
    raise typer.Exit(_util.EXIT_FAIL)


def _report(r: service.QueueResult) -> None:
    """One line per item, printed as soon as its decision is written (or refused)."""
    if r.outcome == "refused":
        typer.echo(f"not recorded {r.id}: {r.message} [{r.code}]")
    elif r.outcome == "changed":
        typer.echo(f"skipped {r.id}: no longer pending")
    else:
        typer.echo(f"{r.outcome} {r.id}")


def _queue(root: Path) -> None:
    try:
        results = _guard(lambda: service.queue(root, prompt=_ask, clock=models.utc_now, report=_report))
    except typer.Abort:
        typer.echo("\nStopped. Decisions already confirmed above stay recorded; the current and later items are unchanged.",
                   err=True)
        raise typer.Exit(_util.EXIT_FAIL) from None
    if not results:
        typer.echo("nothing to approve: no pending items")
        return
    count = {o: sum(r.outcome == o for r in results) for o in ("approved", "declined", "skipped")}
    typer.echo(f"{count['approved']} approved, {count['declined']} declined, {count['skipped']} skipped")
    if any(r.outcome == "refused" for r in results):
        raise typer.Exit(_util.EXIT_FAIL)


def _approve_job(root: Path, job: str) -> None:
    if not _util.is_interactive():
        typer.echo(
            "careeros approve records YOUR decision and needs an interactive terminal. "
            "Ask the user to run it themselves (in Claude Code: `! careeros approve <job>`).",
            err=True,
        )
        raise typer.Exit(_util.EXIT_USAGE)
    try:
        result = sm.approve_job(root, job, confirm=lambda prompt: typer.confirm(prompt, default=False))
    except sm.TransitionError as exc:
        _util.fail(f"{exc} [{exc.code}]")
    except WorkspaceError as exc:
        _util.fail(str(exc))
    outcome = result["outcome"]
    if outcome == "approved":
        typer.echo("Approved. The application may now move to APPLIED.")
    elif outcome == "unchanged" and result.get("decision") == "approved":
        typer.echo(f"Already approved at {result['event']['ts']}.")
    elif outcome == "unchanged":
        typer.echo("Already declined; nothing was recorded again.")
        raise typer.Exit(_util.EXIT_FAIL)
    else:
        typer.echo("Declined. The application stays at APPROVAL_REQUIRED.")
        raise typer.Exit(_util.EXIT_FAIL)
