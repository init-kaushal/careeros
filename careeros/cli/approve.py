"""careeros approve: record the user's own decision to submit an application."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import state_machine as sm
from careeros.core.workspace import WorkspaceError


def approve_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Approve (or decline) submitting an application. Needs an interactive terminal.

    This is a human-confirmation guard against accidental execution, not a security boundary.
    """
    root = _util.resolve_root(workspace)
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
