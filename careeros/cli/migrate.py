"""careeros migrate."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import migration
from careeros.core.workspace import WorkspaceError


def _print_plan(plan: migration.MigrationPlan) -> None:
    typer.echo(f"Migration plan: schema {plan.source_schema} → {plan.target_schema}")
    if plan.meta_to_write is not None:
        if plan.meta_original is None:
            typer.echo("  - create .careeros/workspace.yaml")
        else:
            typer.echo(f"  - update .careeros/workspace.yaml to schema {plan.target_schema}")
    if plan.job_changes:
        typer.echo(f"  - add frontmatter and a stable id to {len(plan.job_changes)} job file(s):")
        for change in plan.job_changes:
            typer.echo(f"      {change.rel}  →  {change.job_id}  {change.status.value}")
    for note in plan.notes:
        typer.echo(f"  note: {note}")
    for error in plan.errors:
        typer.echo(f"  ERROR: {error}")


def migrate_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and change nothing"),
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (after you have reviewed the plan)"),
) -> None:
    """Add versions, ids and frontmatter to a workspace. Makes a verified backup first."""
    root = _util.resolve_root(workspace)
    try:
        plan = migration.plan_migration(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    _print_plan(plan)
    if plan.errors:
        _util.fail("nothing was changed; fix the files above and run again")
    if plan.empty:
        typer.echo("Nothing to migrate: the workspace is already current.")
        raise typer.Exit(_util.EXIT_OK)
    if dry_run:
        typer.echo("Dry run: nothing was changed.")
        raise typer.Exit(_util.EXIT_OK)
    if not _util.confirm_or_exit("Apply this migration? A verified backup is made first.", assume_yes=yes):
        _util.log_decline(root, "workspace.migrate_declined", "user declined the migration")
        typer.echo("Cancelled. Nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    try:
        result = migration.run_migration(root, plan)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo(f"Migrated {result.jobs} job(s). Backup and manifest: {result.backup.relative_to(root)}")
