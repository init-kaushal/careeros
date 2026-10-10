"""Shared CLI helpers: workspace resolution, exit codes, JSON output, confirmation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn, Optional

import typer

from careeros.core.workspace import LEDGER_REL, WorkspaceError, ensure_writable, find_workspace

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

WorkspaceOption = typer.Option(
    None,
    "--workspace",
    "-w",
    envvar="CAREEROS_WORKSPACE",
    help="Workspace directory (default: search upward from the current directory)",
)


def fail(message: str, code: int = EXIT_FAIL) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


def resolve_root(workspace: Optional[Path]) -> Path:
    try:
        return find_workspace(workspace)
    except WorkspaceError as exc:
        fail(str(exc), EXIT_USAGE)


def echo_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def default_actor() -> str:
    return "user" if is_interactive() else "agent:cli"


def confirm_or_exit(prompt: str, *, assume_yes: bool) -> bool:
    """True when the user (or --yes) agrees. Exits 2 when there is no terminal and no --yes."""
    if assume_yes:
        return True
    if not is_interactive():
        typer.echo(
            "This changes your workspace and needs confirmation. Review the plan above, then run it in a "
            "terminal, or add --yes.",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    return typer.confirm(prompt, default=False)


def log_decline(root: Path, event_type: str, action: str) -> None:
    """Record a declined confirmation when the workspace already has a ledger. Never raises."""
    from careeros.core import ledger

    if not (root / LEDGER_REL).exists():
        return
    try:
        ensure_writable(root)
        ledger.append_event(root, type=event_type, actor="user", action=action, source="cli")
    except (WorkspaceError, OSError):
        pass
