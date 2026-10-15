"""careeros comp."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core.compensation import service
from careeros.core.workspace import WorkspaceError


def _fail(message: str, code: int, as_json: bool) -> None:
    if as_json:
        _util.echo_json({"error": message})
        raise typer.Exit(code)
    _util.fail(message, code)


def _figure(low, high) -> str:
    return f"{'open' if low is None else format(low, ',')} to {'open' if high is None else format(high, ',')}"


def _num(value) -> str:
    return "open" if value is None else (format(value, ",") if isinstance(value, int) else str(value))


def _span(span) -> str:
    return f"{_num(span['low'])} to {_num(span['high'])}"


def _render(report: service.Report) -> None:
    data = report.to_dict()
    if not report.exists:
        typer.echo(f"{report.rel}: no compensation.md (read as no pay information)")
    else:
        count = len(data["observations"]) if report.valid else (len(report.record.observations) if report.record else 0)
        typer.echo(f"{report.rel}: {'valid' if report.valid else 'INVALID'}, {count} observation{'' if count == 1 else 's'}")
    floor = report.floor
    if floor.verified:
        typer.echo(f"  floor: {floor.amount:,} {floor.currency} ({floor.basis}) from market {report.record.market!r}")
    elif report.valid and report.record is not None and report.record.market is not None:
        typer.echo(f"  floor: unverified for market {report.record.market!r}")
    for o in data["observations"]:
        orig = o["original"]
        line = f"  - {o['id']} [{o['kind']}, {o['confidence']}] {_figure(orig['low'], orig['high'])} {orig['currency']}/{orig['period']}"
        if o["converted"] is not None:
            line += f" -> {_span(o['converted'])} (annual, floor currency)"
        if o["reason"]:
            line += f" NOT COMPARABLE: {o['reason']}"
        typer.echo(line)
    eff = data["effective"]
    if eff and eff["kind"]:
        if eff["status"] == "effective":
            typer.echo(f"  effective: {eff['kind']} {_span(eff)}")
        elif eff["status"] == "conflict":
            typer.echo(f"  effective: none, conflicting {eff['kind']} figures")
        else:
            typer.echo(f"  effective: none, no comparable {eff['kind']} figure")
    elif report.valid:
        typer.echo("  effective: none, no pay information")
    if report.valid:
        typer.echo(f"  cap: {'WOULD fire (pay is below the floor)' if report.cap_fires else 'would not fire'}")
    for note in data["notes"]:
        what = {"estimate_only": "pay is estimated only", "conflict": f"resolve conflicting {note['detail']} pay figures",
                "incomparable": f"{note['ref']} is not comparable ({note['detail']})"}[note["code"]]
        typer.echo(f"  - {what}")
    for issue in report.issues:
        typer.echo(f"  {issue.severity.upper()} {issue.code}: {issue.message}")
        typer.echo(f"        fix: {issue.fix}")
    if report.ledger_error:
        typer.echo(f"  ledger failure: {report.ledger_error}; nothing was recorded")
    elif report.recorded:
        typer.echo("  recorded: compensation.recorded")
    elif report.outcome == "rejected":
        typer.echo("  recorded: compensation.rejected")


def comp_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    init: bool = typer.Option(False, "--init", help="Create the compensation.md skeleton for the agent to fill in"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Validate a job's compensation.md and show what it means. Appends only ledger events; never edits the file."""
    root = _util.resolve_root(workspace)
    try:
        if init:
            rel = service.init(root, job)
            if as_json:
                _util.echo_json({"outcome": "initialised", "path": rel})
            else:
                typer.echo(f"created {rel}; record the pay in it, then run `careeros comp {job}`")
            return
        report = service.run(root, job, actor or _util.default_actor())
    except service.InvalidInputs as exc:
        _fail(str(exc), _util.EXIT_FAIL, as_json)
    except WorkspaceError as exc:
        _fail(str(exc), _util.EXIT_USAGE, as_json)
    except OSError as exc:
        _fail(f"could not complete: {exc}", _util.EXIT_FAIL, as_json)
    if as_json:
        _util.echo_json(report.to_dict())
    else:
        _render(report)
    if not report.valid or report.ledger_error:
        raise typer.Exit(_util.EXIT_FAIL)
