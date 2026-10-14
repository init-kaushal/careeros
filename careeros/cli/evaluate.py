"""careeros evaluate."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core.evaluation import service
from careeros.core.workspace import WorkspaceError


def _fail(message: str, code: int, as_json: bool, issues: list | None = None) -> None:
    if as_json:
        payload: dict = {"error": message}
        if issues:
            payload["issues"] = [i.to_dict() for i in issues]
        _util.echo_json(payload)
        raise typer.Exit(code)
    _util.fail(message, code)


def evaluate_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    init: bool = typer.Option(False, "--init", help="Create the evaluation.md skeleton for the agent to fill in"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Score a job's evaluation.md from the agent's judgements and the career memory. Changes only evaluation.md."""
    root = _util.resolve_root(workspace)
    try:
        if init:
            eval_id, rel = service.init(root, job)
            if as_json:
                _util.echo_json({"outcome": "initialised", "id": eval_id, "path": rel})
            else:
                typer.echo(f"created {rel} ({eval_id}); fill in the agent: half, then run `careeros evaluate {job}`")
            return
        summary = service.evaluate(root, job, actor or _util.default_actor())
    except service.InvalidInputs as exc:
        _fail(str(exc), _util.EXIT_FAIL, as_json, exc.issues)
    except WorkspaceError as exc:
        _fail(str(exc), _util.EXIT_USAGE, as_json)
    except OSError as exc:
        _fail(f"could not write the evaluation: {exc}", _util.EXIT_FAIL, as_json)
    if as_json:
        _util.echo_json(summary.to_dict())
        return
    r = summary.result
    note = {"unchanged": " (unchanged)", "replaced": f" (stored result replaced: {summary.replaced})"}.get(summary.outcome, "")
    typer.echo(f"{summary.path}: {r['tier']}, combined {r['combined']}, confidence {r['confidence']}{note}")
    for item in r["action_items"]:
        typer.echo(f"  - {item}")
