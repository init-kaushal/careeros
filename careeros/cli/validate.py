"""careeros validate."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core.validation import validate_workspace


def validate_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Check the workspace structure, versions, jobs, pipeline and ledger. Changes nothing."""
    root = _util.resolve_root(workspace)
    issues = validate_workspace(root)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    if as_json:
        _util.echo_json({"errors": len(errors), "warnings": len(warnings), "issues": [i.to_dict() for i in issues]})
    else:
        for issue in issues:
            typer.echo(f"{issue.severity.upper():<7} {issue.code}  {issue.path}")
            typer.echo(f"        {issue.message}")
            typer.echo(f"        fix: {issue.fix}")
        typer.echo(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    raise typer.Exit(_util.EXIT_FAIL if errors or (strict and warnings) else _util.EXIT_OK)
