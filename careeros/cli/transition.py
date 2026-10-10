"""careeros transition."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import state_machine as sm
from careeros.core.models import State
from careeros.core.workspace import WorkspaceError


def transition_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    to: str = typer.Option(..., "--to", help="Target state, for example EVALUATED"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    force: bool = typer.Option(False, "--force", help="Record a correction the rules would forbid (needs --reason)"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Why a forced correction is needed"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Move a job to a new state, atomically, and record it in the ledger."""
    root = _util.resolve_root(workspace)
    try:
        target = State(to.strip().upper())
    except ValueError:
        _util.fail(f"unknown state {to!r}; choose one of: {', '.join(s.value for s in State)}", _util.EXIT_USAGE)
    try:
        result = sm.apply_transition(
            root, job, target, actor=actor or _util.default_actor(), reason=reason, force=force
        )
    except sm.TransitionError as exc:
        _util.fail(f"{exc} [{exc.code}]")
    except WorkspaceError as exc:
        _util.fail(str(exc))
    messages = {
        "changed": f"{result.from_state.value} → {result.to_state.value}",
        "corrected": f"corrected {result.from_state.value} → {result.to_state.value} (recorded as a correction)",
        "unchanged": f"already {result.to_state.value}; nothing to do",
        "repaired": f"already {result.to_state.value}; the ledger was behind and has been repaired",
    }
    typer.echo(messages[result.outcome])
