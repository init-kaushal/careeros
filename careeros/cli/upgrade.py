"""careeros upgrade."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import migration
from careeros.core.workspace import WorkspaceError


def upgrade_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (after you have reviewed the plan)"),
) -> None:
    """Refresh skill files and the entry file from the installed CareerOS. Makes a verified backup first."""
    root = _util.resolve_root(workspace)
    try:
        plan = migration.plan_upgrade(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    if plan.noop:
        typer.echo("Framework files are already current.")
        _schema_hint(plan)
        raise typer.Exit(_util.EXIT_OK)
    typer.echo("Upgrade plan:")
    for item in plan.pending:
        typer.echo(f"  {item.status:<8} {item.rel}")
    if plan.meta is not None and plan.framework_stale:
        typer.echo(f"  workspace framework version {plan.meta.framework_version} → installed")
    if not _util.confirm_or_exit("Apply this upgrade? Changed files are backed up first.", assume_yes=yes):
        _util.log_decline(root, "workspace.upgrade_declined", "user declined the upgrade")
        typer.echo("Cancelled. Nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    try:
        result = migration.run_upgrade(root, plan)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo(f"Upgraded {result.jobs} file(s). Backup and manifest: {result.backup.relative_to(root)}")
    _schema_hint(plan)


def _schema_hint(plan: migration.UpgradePlan) -> None:
    if plan.schema_behind:
        typer.echo("Your workspace data is not on the current schema yet. Run `careeros migrate`.")
