"""careeros outreach: person and target records and the target decisions. Records are source data the agent writes through
these commands; state (who was contacted, what happened) is derived from the ledger and never stored in a record."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import models
from careeros.core.ledger import LedgerError
from careeros.core.outreach import rules, service
from careeros.core.workspace import LockTimeout, WorkspaceError

outreach_app = typer.Typer(help="Person and target-company records, and target decisions. Changes only records and "
                                "outreach.* ledger events; nothing here sends anything.",
                           no_args_is_help=True)
person_app = typer.Typer(help="Add, link, list and show person records.", no_args_is_help=True)
target_app = typer.Typer(help="Add, approve, drop, list and show target-company records.", no_args_is_help=True)
outreach_app.add_typer(person_app, name="person")
outreach_app.add_typer(target_app, name="target")


def _fail(message: str, code: int, as_json: bool, refusal: str | None = None) -> None:
    if as_json:
        _util.echo_json({"error": message, **({"code": refusal} if refusal else {})})
        raise typer.Exit(code)
    _util.fail(f"{message} [{refusal}]" if refusal else message, code)


def _guard(as_json: bool, fn):
    """Run `fn`, mapping the service's errors to exit codes: refusals and ledger or disk failures 1, usage 2."""
    try:
        return fn()
    except service.Refused as exc:
        _fail(str(exc), _util.EXIT_FAIL, as_json, exc.code)
    except LedgerError as exc:
        _fail(str(exc), _util.EXIT_FAIL, as_json)
    except LockTimeout:
        _fail(_util.BUSY, _util.EXIT_FAIL, as_json)
    except WorkspaceError as exc:
        _fail(str(exc), _util.EXIT_USAGE, as_json)
    except OSError as exc:
        _fail(f"could not complete: {exc}", _util.EXIT_FAIL, as_json)


def _split_evidence(items: list[str], as_json: bool) -> list[tuple[str, str]]:
    out = []
    for text in items:
        source, sep, read_on = text.rpartition(":")
        if not sep:
            _fail(f"--evidence {text!r} must be SOURCE:YYYY-MM-DD", _util.EXIT_FAIL, as_json, rules.INVALID_INPUT)
        out.append((source, read_on))
    return out


def _problems(problems: tuple[str, ...]) -> str:
    return f"  [{'; '.join(problems)}]" if problems else ""


# --- person --------------------------------------------------------------------------------------------------------------

@person_app.command("add")
def person_add_cmd(
    url: str = typer.Option(..., "--url", help="LinkedIn profile URL; its canonical form is the person's identity"),
    name: str = typer.Option(..., "--name", help="The person's name"),
    company: str = typer.Option(..., "--company", help="Their company"),
    role: str = typer.Option(..., "--role", help="recruiter, hiring_manager, peer or other"),
    headline: Optional[str] = typer.Option(None, "--headline", help="Their profile headline"),
    market: Optional[str] = typer.Option(None, "--market", help="Exact ## Markets row label"),
    found_in: Optional[list[str]] = typer.Option(None, "--found-in", help="job:<REF> or target:<SLUG>; repeatable"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Write people/<per_id>.md and append outreach.person_added. A person already proposed for an invite keeps that ID."""
    root = _util.resolve_root(workspace)
    got = _guard(as_json, lambda: service.person_add(
        root, url=url, name=name, company=company, role=role, headline=headline, market=market,
        found_in=list(found_in or ()), actor=actor or _util.default_actor(), clock=models.utc_now))
    if as_json:
        _util.echo_json({"outcome": "added", "id": got.id, "path": got.rel, "hash": got.hash, "url": got.url,
                         "reused_id": got.reused_id})
        return
    typer.echo(f"added {got.id} at {got.rel}" + (" (the ID was already known from the ledger)" if got.reused_id else ""))


@person_app.command("link")
def person_link_cmd(
    person_id: str = typer.Argument(..., metavar="PER_ID", help="Person ID"),
    found_in: list[str] = typer.Option(..., "--found-in", help="job:<REF> or target:<SLUG>; repeatable"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Add found_in entries to the person's record file. Edits only that field; appends no event."""
    root = _util.resolve_root(workspace)
    got = _guard(as_json, lambda: service.person_link(root, person_id, found_in, actor=actor or _util.default_actor(),
                                                      clock=models.utc_now))
    if as_json:
        _util.echo_json({"outcome": "linked", "id": got.id, "path": got.rel,
                         "found_in": [{"kind": f.kind, "id": f.id} for f in got.found_in]})
        return
    typer.echo(f"linked {got.id}: found_in is now {', '.join(f'{f.kind}:{f.id}' for f in got.found_in)}")


@person_app.command("list")
def person_list_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List people with their derived label. Changes nothing. Exits 1 when ledger.jsonl has a corrupt line."""
    root = _util.resolve_root(workspace)
    views = _guard(as_json, lambda: service.person_list(root))
    if as_json:
        _util.echo_json({"people": [v.to_dict() for v in views]})
        return
    for v in views:
        r = v.record
        who = f"{r.name}, {r.company} ({r.role})" if r else "(no readable record)"
        typer.echo(f"{v.id}  {v.label:<10} {who}{_problems(v.problems)}")
    typer.echo(f"{len(views)} person(s)")


@person_app.command("show")
def person_show_cmd(
    person_id: str = typer.Argument(..., metavar="PER_ID", help="Person ID"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show one person: the record and the state derived from the ledger. Changes nothing."""
    root = _util.resolve_root(workspace)
    view = _guard(as_json, lambda: service.person_show(root, person_id))
    if as_json:
        _util.echo_json(view.to_dict())
        return
    r = view.record
    typer.echo(f"{view.id}  {view.label}")
    if r is not None:
        typer.echo(f"name:     {r.name}\nurl:      {r.url}\ncompany:  {r.company} ({r.company_slug})\nrole:     {r.role}")
        typer.echo("found in: " + (", ".join(f"{f.kind}:{f.id}" for f in r.found_in) or "-"))
    state = view.to_dict()["state"]
    typer.echo("state:    " + ", ".join(f"{k}={v}" for k, v in state.items() if v) or "state:    -")
    if view.problems:
        typer.echo(f"problems: {'; '.join(view.problems)}")


# --- target ---------------------------------------------------------------------------------------------------------------

@target_app.command("add")
def target_add_cmd(
    company: str = typer.Option(..., "--company", help="Company name"),
    slug: str = typer.Option(..., "--slug", help="Lower-case ASCII letters and digits separated by hyphens; unique for ever"),
    market: str = typer.Option(..., "--market", help="The market the company is in"),
    fit: int = typer.Option(..., "--fit", help="0 to 10, same rubric as browse"),
    sponsorship: str = typer.Option(..., "--sponsorship", help="offered, likely, unknown, not_offered or n/a (home market)"),
    evidence: Optional[list[str]] = typer.Option(None, "--evidence", help="SOURCE:YYYY-MM-DD; repeatable; required unless n/a"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Write targets/<slug>.md and append outreach.target_added."""
    root = _util.resolve_root(workspace)
    pairs = _split_evidence(list(evidence or ()), as_json)
    got = _guard(as_json, lambda: service.target_add(
        root, company=company, slug=slug, market=market, fit=fit, sponsorship=sponsorship, evidence=pairs,
        actor=actor or _util.default_actor(), clock=models.utc_now))
    if as_json:
        _util.echo_json({"outcome": "added", "id": got.id, "slug": got.slug, "path": got.rel, "hash": got.hash})
        return
    typer.echo(f"added {got.id} at {got.rel}")


def _decide(decision: str, slug: str, source: str, reason: Optional[str], as_json: bool, actor: Optional[str],
            workspace: Optional[Path]) -> None:
    root = _util.resolve_root(workspace)
    got = _guard(as_json, lambda: service.target_decide(root, slug, decision, source=source, reason=reason,
                                                        actor=actor or _util.default_actor(), clock=models.utc_now))
    if as_json:
        _util.echo_json({"outcome": got.decision, "id": got.id, "slug": got.slug, "decision": got.decision,
                         "status": got.status})
        return
    typer.echo(f"{got.decision} {got.slug} ({got.id}); status {got.status}")


@target_app.command("approve")
def target_approve_cmd(
    slug: str = typer.Argument(..., help="Target slug"),
    source: str = typer.Option("user", "--source", help="user, observed or email"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Why (optional)"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record the user's decision to approve a candidate target (outreach.target_approved)."""
    _decide(rules.OP_APPROVE, slug, source, reason, as_json, actor, workspace)


@target_app.command("drop")
def target_drop_cmd(
    slug: str = typer.Argument(..., help="Target slug"),
    source: str = typer.Option("user", "--source", help="user, observed or email"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Why (required)"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record the user's decision to drop a target (outreach.target_dropped). Dropped is final: the slug cannot be added again."""
    _decide(rules.OP_DROP, slug, source, reason, as_json, actor, workspace)


@target_app.command("list")
def target_list_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List targets with their derived status. Changes nothing. Exits 1 when ledger.jsonl has a corrupt line."""
    root = _util.resolve_root(workspace)
    views = _guard(as_json, lambda: service.target_list(root))
    if as_json:
        _util.echo_json({"targets": [v.to_dict() for v in views]})
        return
    for v in views:
        fit = f"fit {v.record.fit}, {v.record.sponsorship}" if v.record else "(no readable record)"
        typer.echo(f"{v.slug}  {v.status:<12} {fit}{'  [stale]' if v.stale else ''}{_problems(v.problems)}")
    typer.echo(f"{len(views)} target(s)")


@target_app.command("show")
def target_show_cmd(
    slug: str = typer.Argument(..., help="Target slug"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show one target: the record and the status derived from the ledger. Changes nothing."""
    root = _util.resolve_root(workspace)
    view = _guard(as_json, lambda: service.target_show(root, slug))
    if as_json:
        _util.echo_json(view.to_dict())
        return
    typer.echo(f"{view.slug}  {view.status}{'  [stale]' if view.stale else ''}  ({view.id}, decision {view.decision})")
    if view.record is not None:
        r = view.record
        typer.echo(f"company:     {r.company}\nmarket:      {r.market}\nfit:         {r.fit}\nsponsorship: {r.sponsorship}")
        for e in r.evidence:
            typer.echo(f"evidence:    {e.source} (read {e.read_on})")
    typer.echo("people:      " + (", ".join(view.people) or "-"))
    if view.problems:
        typer.echo(f"problems:    {'; '.join(view.problems)}")
