"""careeros doctor."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import migration, versions
from careeros.core.validation import validate_workspace
from careeros.core.workspace import WorkspaceError, load_meta


def _inside_git_repo(root: Path) -> bool:
    return any((p / ".git").exists() for p in (root, *root.parents))


def doctor_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Check the environment and the workspace. Changes nothing."""
    root = _util.resolve_root(workspace)
    checks: list[dict[str, str]] = []

    def add(level: str, name: str, message: str) -> None:
        checks.append({"level": level, "name": name, "message": message})

    installed = versions.installed_version()
    py = sys.version_info
    add("ok" if py >= (3, 11) else "error", "python", f"Python {py.major}.{py.minor}.{py.micro}" + ("" if py >= (3, 11) else " (CareerOS needs 3.11+)"))
    add("ok", "careeros", f"CareerOS {installed}")
    add("ok", "workspace", str(root))

    try:
        meta = load_meta(root)
    except WorkspaceError as exc:
        add("error", "metadata", str(exc))
    else:
        if meta is None:
            add("warn", "metadata", "legacy workspace (schema 0, no .careeros/workspace.yaml): run `careeros migrate`")
        else:
            add("ok", "metadata", f"schema {meta.schema_version}, framework {meta.framework_version}")
            if meta.schema_version > versions.SCHEMA_VERSION:
                add("error", "schema", f"schema {meta.schema_version} is newer than this CareerOS supports ({versions.SCHEMA_VERSION}); upgrade CareerOS")
            relation = versions.compare_versions(installed, meta.framework_version)
            if relation > 0:
                add("warn", "framework", f"upgrade available: the workspace is at {meta.framework_version}, installed is {installed}; run `careeros upgrade`")
            elif relation < 0:
                add("error", "framework", f"the workspace was last updated by a newer CareerOS ({meta.framework_version}); upgrade CareerOS before changing it")
            else:
                add("ok", "framework", "workspace framework version matches the installed CareerOS")

    try:
        plan = migration.plan_upgrade(root)
    except WorkspaceError as exc:
        add("error", "framework files", str(exc))
    else:
        pending = plan.pending
        if pending:
            add("warn", "framework files", f"{len(pending)} skill/entry file(s) differ from the installed templates; run `careeros upgrade`")
        else:
            add("ok", "framework files", "skill and entry files match the installed templates")

    issues = validate_workspace(root)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    if errors:
        add("error", "validation", f"{len(errors)} error(s), {len(warnings)} warning(s); run `careeros validate` for details")
    elif warnings:
        add("warn", "validation", f"{len(warnings)} warning(s); run `careeros validate` for details")
    else:
        add("ok", "validation", "no issues")

    for path in migration.find_incomplete_operations(root):
        add("error", "incomplete operation", f"{path} has a manifest still marked pending; restore from that backup, then run the command again")

    if _inside_git_repo(root):
        add("warn", "privacy", "this workspace is inside a git repository; it holds your resume, contacts and compensation, so keep any remote private")

    if as_json:
        _util.echo_json(checks)
    else:
        for check in checks:
            typer.echo(f"{check['level'].upper():<6} {check['name']:<20} {check['message']}")
    raise typer.Exit(_util.EXIT_FAIL if any(c["level"] == "error" for c in checks) else _util.EXIT_OK)
