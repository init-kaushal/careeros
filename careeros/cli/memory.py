"""careeros memory: import a resume and maintain the facts your drafts are checked against."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import typer
import yaml

from careeros.cli import _util
from careeros.core.memory import importer, ops, store
from careeros.core.memory.ops import MemoryOpError
from careeros.core.workspace import WorkspaceError

memory_app = typer.Typer(help="Import your resume and maintain the career facts that drafts are checked against.", no_args_is_help=True)
_SYMBOL = {"added": "+", "changed": "~", "removed": "-"}


def _confirm() -> Callable[[str], bool] | None:
    return (lambda prompt: typer.confirm(prompt, default=False)) if _util.is_interactive() else None


def _human_only(command: str, fact_id: str) -> None:
    if not _util.is_interactive():
        typer.echo(
            f"careeros memory {command} records YOUR decision and needs an interactive terminal. "
            f"Ask the user to run it themselves (in Claude Code: `! careeros memory {command} {fact_id}`).",
            err=True,
        )
        raise typer.Exit(_util.EXIT_USAGE)


def _run(action: Callable[[], object]) -> object:
    try:
        return action()
    except MemoryOpError as exc:
        _util.fail(str(exc), _util.EXIT_USAGE if "interactive terminal" in str(exc) else _util.EXIT_FAIL)
    except WorkspaceError as exc:
        _util.fail(str(exc))


def _pairs(values: Optional[list[str]]) -> dict:
    out: dict = {}
    for item in values or ():
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            _util.fail(f"--set expects key=value, not {item!r}", _util.EXIT_USAGE)
        out[key.strip()] = value.strip()
    return out


def _print_plan(plan: importer.ImportPlan) -> None:
    typer.echo(f"Import plan for {plan.source_rel}")
    for change in plan.changes:
        if change.result == "unchanged":
            continue
        symbol = _SYMBOL[change.result]
        note = {"skip_reviewed": "  (reviewed fact: left alone, needs your review)",
                "skip_manual": "  (manual fact: left alone)",
                "mark_stale": "  (its line is gone: will be marked stale)"}.get(change.action, "")
        typer.echo(f"  {symbol} {change.label}{note}")
    typer.echo(f"  = {plan.count('unchanged')} unchanged")
    typer.echo(
        f"Summary: {plan.count('added')} added, {plan.count('changed')} changed, {plan.count('removed')} removed, "
        f"{plan.count('unchanged')} unchanged; {plan.pending_review} need review"
    )
    for change in plan.changes:
        if change.action == "skip_reviewed":
            typer.echo(f"Needs review: {change.fact.id}\n  now:      {change.fact.get('text') or change.label.strip()}\n"
                       f"  resume:   {change.candidate.fields.get('text') or change.label.strip()}")
    for line in plan.skipped:
        typer.echo(f"Not imported: {line}")


@memory_app.command("import")
def import_cmd(
    source: str = typer.Option("resume.md", "--source", help="File to import, relative to the workspace"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and change nothing (the default)"),
    apply: bool = typer.Option(False, "--apply", help="Apply the plan"),
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (needed when there is no terminal)"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Turn resume.md into career facts. Shows a plan first; --apply makes the changes."""
    if dry_run and apply:
        _util.fail("choose --dry-run or --apply, not both", _util.EXIT_USAGE)
    root = _util.resolve_root(workspace)
    plan = _run(lambda: importer.plan_import(root, source))
    if plan.errors:
        for error in plan.errors:
            typer.echo(f"error: {error}", err=True)
        raise typer.Exit(_util.EXIT_FAIL)
    _print_plan(plan)
    if not apply:
        if not plan.actionable and plan.pending_review == 0:
            typer.echo("Nothing to import: the career memory already matches the resume.")
        else:
            typer.echo("Dry run: nothing was changed. Run again with --apply to import.")
        return
    if not plan.actionable and plan.pending_review == 0:
        typer.echo("Nothing to import: the career memory already matches the resume.")
        return
    if not _util.confirm_or_exit("Apply this import?", assume_yes=yes):
        typer.echo("Cancelled; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    result = _run(lambda: importer.apply_import(root, source, actor=actor or _util.default_actor()))
    if result.status == "noop":
        typer.echo("Nothing to import: the career memory already matches the resume.")
        return
    typer.echo(f"Imported. Backup and manifest: {result.backup.relative_to(root).as_posix()}")


@memory_app.command("add")
def add_cmd(
    kind: str = typer.Option(..., "--kind", help=f"One of: {', '.join(store.KINDS)}"),
    text: Optional[str] = typer.Option(None, "--text", help="The claim, for achievements, projects and preferences"),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="key=value for the kind's other fields; repeatable"),
    supports: Optional[list[str]] = typer.Option(None, "--supports", help="Evidence only: ID of a fact it backs; repeatable"),
    quote: Optional[str] = typer.Option(None, "--quote", help="The user's own words (required except for evidence)"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Add a fact the user has stated. It starts as `claimed`."""
    root = _util.resolve_root(workspace)
    fields = _pairs(set_)
    if text is not None:
        fields["text"] = text
    if supports:
        fields["supports"] = list(supports)
    if kind == "evidence":
        fields["kind"] = "document" if "path" in fields else "link"
    fact = _run(lambda: ops.add_fact(root, kind, fields, quote=quote, actor=actor or _util.default_actor()))
    typer.echo(f"Added {fact.kind} {fact.id} (claimed)")


@memory_app.command("update")
def update_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    text: Optional[str] = typer.Option(None, "--text"),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="key=value; repeatable"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Change a fact. Changing a confirmed or verified fact resets it to claimed and needs a terminal."""
    root = _util.resolve_root(workspace)
    changes = _pairs(set_)
    if text is not None:
        changes["text"] = text
    fact = _run(lambda: ops.update_fact(root, fact_id, changes, actor=actor or _util.default_actor(), confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Updated {fact.id}; it is claimed until you confirm it again.")


@memory_app.command("confirm")
def confirm_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Affirm that a claimed fact is true. Needs an interactive terminal."""
    root = _util.resolve_root(workspace)
    _human_only("confirm", fact_id)
    fact = _run(lambda: ops.confirm_fact(root, fact_id, actor="user", confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; the fact stays claimed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Confirmed {fact.id}.")


@memory_app.command("verify")
def verify_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    evidence: str = typer.Option(..., "--evidence", help="ID of an evidence record that lists this fact under supports"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Mark a confirmed fact as verified by evidence. Needs an interactive terminal."""
    root = _util.resolve_root(workspace)
    _human_only("verify", fact_id)
    fact = _run(lambda: ops.verify_fact(root, fact_id, evidence, actor="user", confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; the fact stays confirmed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Verified {fact.id} with {evidence}.")


@memory_app.command("dispute")
def dispute_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    reason: str = typer.Option(..., "--reason"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record that a fact is wrong. It stops supporting any claim."""
    root = _util.resolve_root(workspace)
    fact = _run(lambda: ops.dispute_fact(root, fact_id, reason, actor=actor or _util.default_actor(), confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Disputed {fact.id}.")


@memory_app.command("retire")
def retire_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    reason: str = typer.Option(..., "--reason"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Retire a fact that no longer applies. The file stays, so the ledger's references remain valid."""
    root = _util.resolve_root(workspace)
    fact = _run(lambda: ops.retire_fact(root, fact_id, reason, actor=actor or _util.default_actor(), confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Retired {fact.id}.")


def _loaded(root: Path) -> store.Career:
    career = store.load_career(root)
    if career.errors:
        for issue in career.errors:
            typer.echo(f"error: {issue.code} {issue.path}: {issue.message}", err=True)
        _util.fail("the career memory has errors; run `careeros validate`", _util.EXIT_USAGE)
    return career


@memory_app.command("list")
def list_cmd(
    kind: Optional[str] = typer.Option(None, "--kind"),
    status: Optional[str] = typer.Option(None, "--status"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List facts."""
    career = _loaded(_util.resolve_root(workspace))
    facts = [f for f in career.facts if (kind is None or f.kind == kind) and (status is None or f.status == status)]
    if as_json:
        _util.echo_json([{"id": f.id, "kind": f.kind, "status": f.status, "origin": f.get("origin"),
                          "stale": bool(f.get("stale")), "summary": store.heading(f.kind, f.fm)} for f in facts])
        return
    for f in facts:
        flag = " (stale)" if f.get("stale") else ""
        typer.echo(f"{f.id}  {f.kind:<13} {f.status:<9} {store.heading(f.kind, f.fm)}{flag}")


@memory_app.command("show")
def show_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show one fact in full."""
    fact = _loaded(_util.resolve_root(workspace)).by_id().get(fact_id)
    if fact is None:
        _util.fail(f"no fact with id {fact_id}")
    if as_json:
        _util.echo_json(fact.fm)
    else:
        typer.echo(yaml.safe_dump(fact.fm, sort_keys=False, allow_unicode=True, width=10_000).rstrip())


@memory_app.command("status")
def status_cmd(
    as_json: bool = typer.Option(False, "--json"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Summarise the memory: counts by kind and status, stale imports, how much is still only claimed."""
    root = _util.resolve_root(workspace)
    career = store.load_career(root)
    by_kind: dict[str, dict[str, int]] = {}
    for f in career.facts:
        by_kind.setdefault(f.kind, {}).setdefault(f.status, 0)
        by_kind[f.kind][f.status] += 1
    active = career.active()
    claimed = sum(1 for f in active if f.status == "claimed")
    data = {
        "facts": len(career.facts), "by_kind": by_kind, "claimed_share": round(claimed / len(active), 2) if active else 0.0,
        "stale_facts": sum(1 for f in career.facts if f.get("stale")),
        "stale_imports": [i.path for i in career.issues if i.code == "MEM006"],
        "pending_review": sum(int(e.get("pending_review") or 0) for e in store.load_sources(root).values()),
        "errors": [i.to_dict() for i in career.errors],
    }
    if as_json:
        _util.echo_json(data)
        return
    typer.echo(f"{data['facts']} fact(s); {claimed} of {len(active)} active fact(s) are still only claimed.")
    for kind, counts in sorted(by_kind.items()):
        typer.echo(f"  {kind:<13} " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    if data["stale_facts"]:
        typer.echo(f"{data['stale_facts']} fact(s) are stale: their source line is gone from the resume.")
    for path in data["stale_imports"]:
        typer.echo(f"{path} changed since it was imported: run `careeros memory import`.")
    if data["pending_review"]:
        typer.echo(f"{data['pending_review']} reviewed fact(s) differ from the resume: run `careeros memory import` to see them.")
    for issue in career.errors:
        typer.echo(f"error: {issue.code} {issue.path}: {issue.message}")
