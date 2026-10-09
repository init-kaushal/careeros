"""careeros archive."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import state_machine as sm
from careeros.core.workspace import WorkspaceError


def archive_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    undo: bool = typer.Option(False, "--undo", help="Unarchive the job"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Archive a job instead of deleting it, so the ledger's history stays verifiable."""
    root = _util.resolve_root(workspace)
    try:
        result = sm.archive_job(root, job, actor=actor or _util.default_actor(), undo=undo, reason=reason)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo({"archived": "archived", "unarchived": "unarchived", "unchanged": "nothing to do"}[result["outcome"]])
