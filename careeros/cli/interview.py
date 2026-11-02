"""careeros interview: interview round records, the round outcomes (held, cancelled, rescheduled), debriefs and the
read-only `round list|show`. Records are source data the agent writes through these commands; state (whether a round was
held, cancelled or debriefed) is derived from the ledger and never stored in a record."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import models
from careeros.core.interview import rules, service
from careeros.core.ledger import LedgerError
from careeros.core.workspace import LockTimeout, WorkspaceError

interview_app = typer.Typer(help="Interview rounds and debriefs: add rounds, record held, cancelled and rescheduled "
                                 "rounds, write debriefs, list and show rounds, and the upcoming and needs-debrief views. "
                                 "Changes only rounds/ records and interview.* ledger events.",
                            no_args_is_help=True)
round_app = typer.Typer(help="Add, list, show, hold, cancel and reschedule interview rounds.", no_args_is_help=True)
interview_app.add_typer(round_app, name="round")

ActorOption = typer.Option(None, "--actor", help="user, system or agent:<name>")
JsonOption = typer.Option(False, "--json", help="Machine-readable output")


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


def _changed(got: service.Changed, as_json: bool) -> None:
    if as_json:
        out = {"outcome": got.outcome, "id": got.id, "label": got.label, "seq": got.event["seq"]}
        if got.rel is not None:
            out.update({"path": got.rel, "hash": got.hash})
        if got.scheduled_for is not None:
            out["scheduled_for"] = got.scheduled_for
        _util.echo_json(out)
        return
    typer.echo(f"{got.outcome} {got.id}; now {got.label}" + (f" ({got.scheduled_for})" if got.scheduled_for else ""))


def _interviewers(items: list[str]) -> list[tuple[str, str]]:
    """`Name:Title` (the title may be empty or left out) -> (name, title)."""
    out = []
    for text in items:
        name, _, title = text.partition(":")
        out.append((name.strip(), title.strip()))
    return out


# --- round ---------------------------------------------------------------------------------------------------------------

@round_app.command("add")
def round_add_cmd(
    job: str = typer.Option(..., "--job", help="Job ID, directory slug or path"),
    stage: str = typer.Option(..., "--stage", help=", ".join(rules.STAGES)),
    format: str = typer.Option(..., "--format", help=", ".join(rules.FORMATS)),
    when: str = typer.Option(..., "--when", help="ISO date (2026-11-05) or UTC datetime (2026-11-05T15:00:00Z)"),
    interviewer: Optional[list[str]] = typer.Option(None, "--interviewer", help="\"Name:Title\"; repeatable"),
    as_json: bool = JsonOption,
    actor: Optional[str] = ActorOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Write rounds/<rnd_id>.md and append interview.round_added."""
    root = _util.resolve_root(workspace)
    got = _guard(as_json, lambda: service.round_add(
        root, job=job, stage=stage, format=format, when=when, interviewers=_interviewers(list(interviewer or ())),
        actor=actor or _util.default_actor(), clock=models.utc_now))
    if as_json:
        _util.echo_json({"outcome": "added", "id": got.id, "path": got.rel, "hash": got.hash, "job": got.job,
                         "scheduled_for": got.scheduled_for})
        return
    typer.echo(f"added {got.id} at {got.rel}")


def _line(d: dict) -> str:
    if d["incomplete"]:
        return f"{d['id']}  {d['label']:<10} {d['job']}  (record {d['record']})"
    flag = "  [date not recorded: re-run round reschedule]" if d["date_unrecorded"] else ""
    return f"{d['id']}  {d['label']:<10} {d['job']}  {d['stage']} {d['format']} {d['scheduled_for']}{flag}"


@round_app.command("list")
def round_list_cmd(
    job: Optional[str] = typer.Option(None, "--job", help="Only this job (ID, slug or path)"),
    as_json: bool = JsonOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List every round with its label, incomplete ones included. Changes nothing."""
    root = _util.resolve_root(workspace)
    views = _guard(as_json, lambda: service.round_list(root, job=job))
    if as_json:
        _util.echo_json({"rounds": [v.to_dict(history=False) for v in views]})
        return
    for v in views:
        typer.echo(_line(v.to_dict(history=False)))
    typer.echo(f"{len(views)} round(s)")


@round_app.command("show")
def round_show_cmd(
    rnd_id: str = typer.Argument(..., metavar="RND_ID", help="Round ID"),
    as_json: bool = JsonOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show one round: the record, the state derived from the ledger and its event history. Changes nothing."""
    root = _util.resolve_root(workspace)
    view = _guard(as_json, lambda: service.round_show(root, rnd_id))
    d = view.to_dict()
    if as_json:
        _util.echo_json(d)
        return
    typer.echo(_line(d))
    if d["interviewers"]:
        typer.echo("interviewers: " + ", ".join(f"{i['name']} ({i['title']})" if i["title"] else i["name"]
                                                for i in d["interviewers"]))
    if d["debrief"]:
        typer.echo(f"debrief: {d['debrief']['rating']}; next: {d['debrief']['next_step']}")
    if d["unrecorded_debrief"]:
        typer.echo("debrief: a block is in the record but no debriefed event was recorded; re-run `careeros interview debrief`")
    for h in d["history"]:
        typer.echo(f"  {h['ts']}  {h['type']}  {h['action']}")
    if d["problems"]:
        typer.echo(f"problems: {'; '.join(d['problems'])}")


@round_app.command("held")
def round_held_cmd(
    rnd_id: str = typer.Argument(..., metavar="RND_ID", help="Round ID"),
    source: str = typer.Option("user", "--source", help="user or observed"),
    as_json: bool = JsonOption,
    actor: Optional[str] = ActorOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record that the round took place (interview.held)."""
    root = _util.resolve_root(workspace)
    _changed(_guard(as_json, lambda: service.round_held(root, rnd_id, source=source, actor=actor or _util.default_actor(),
                                                        clock=models.utc_now)), as_json)


@round_app.command("cancel")
def round_cancel_cmd(
    rnd_id: str = typer.Argument(..., metavar="RND_ID", help="Round ID"),
    reason: str = typer.Option(..., "--reason", help="Why the round was cancelled"),
    source: str = typer.Option("user", "--source", help="user or observed"),
    as_json: bool = JsonOption,
    actor: Optional[str] = ActorOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record that the round was cancelled (interview.cancelled). A cancelled round is never reopened."""
    root = _util.resolve_root(workspace)
    _changed(_guard(as_json, lambda: service.round_cancel(root, rnd_id, reason=reason, source=source,
                                                          actor=actor or _util.default_actor(), clock=models.utc_now)),
             as_json)


@round_app.command("reschedule")
def round_reschedule_cmd(
    rnd_id: str = typer.Argument(..., metavar="RND_ID", help="Round ID"),
    when: str = typer.Option(..., "--when", help="ISO date or UTC datetime"),
    as_json: bool = JsonOption,
    actor: Optional[str] = ActorOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Rewrite the record's scheduled_for, then append interview.rescheduled. Re-run it to record a date the record
    already holds (an interrupted reschedule or a hand edit)."""
    root = _util.resolve_root(workspace)
    _changed(_guard(as_json, lambda: service.round_reschedule(root, rnd_id, when=when, actor=actor or _util.default_actor(),
                                                              clock=models.utc_now)), as_json)


# --- debrief -------------------------------------------------------------------------------------------------------------

@interview_app.command("debrief")
def debrief_cmd(
    rnd_id: str = typer.Argument(..., metavar="RND_ID", help="Round ID (the round must be held)"),
    rating: str = typer.Option(..., "--rating", help="strong, mixed or weak: the user's own words mapped, never invented"),
    asked: Optional[list[str]] = typer.Option(None, "--asked", help="A question you were asked; repeatable"),
    improve: Optional[list[str]] = typer.Option(None, "--improve", help="Something to do differently; repeatable"),
    next_step: str = typer.Option(..., "--next-step", help="What happens next"),
    next_date: Optional[str] = typer.Option(None, "--next-date", help="ISO date of the next step"),
    as_json: bool = JsonOption,
    actor: Optional[str] = ActorOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Write the debrief block into the round's record, then append interview.debriefed. Re-run it after an interrupted
    debrief; it replaces a block that was never recorded."""
    root = _util.resolve_root(workspace)
    _changed(_guard(as_json, lambda: service.debrief(
        root, rnd_id, rating=rating, asked=list(asked or ()), improve=list(improve or ()), next_step=next_step,
        next_date=next_date, actor=actor or _util.default_actor(), clock=models.utc_now)), as_json)


# --- the read-only views -------------------------------------------------------------------------------------------------

MAX_DAYS = 3650                         # the CLI bound on --days (ten years); not a rule


def _now() -> str:
    return models.utc_now()             # the one clock read of a read-only command


def _when_flag(date_unrecorded: bool) -> str:
    return "  [date not recorded: re-run round reschedule]" if date_unrecorded else ""


@interview_app.command("upcoming")
def upcoming_cmd(
    days: int = typer.Option(rules.UPCOMING_DEFAULT_DAYS, "--days", help=f"How many days ahead to look (0 to {MAX_DAYS})"),
    as_json: bool = JsonOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Scheduled rounds in the next N days, soonest first, with the job's prep status. Changes nothing."""
    root = _util.resolve_root(workspace)
    if not 0 <= days <= MAX_DAYS:
        _fail(f"--days must be a whole number from 0 to {MAX_DAYS}, not {days}", _util.EXIT_USAGE, as_json)
    now = _now()
    items = _guard(as_json, lambda: service.upcoming(root, days=days, now=now))
    if as_json:
        _util.echo_json({"now": now, "days": days, "rounds": [dataclasses.asdict(i) for i in items]})
        return
    for i in items:
        people = ", ".join(f"{p.name} ({p.title})" if p.title else p.name for p in i.interviewers)
        typer.echo(f"{i.scheduled_for}  {i.round_id}  {i.job_id}  {i.stage} {i.format}  prep: {i.prep}"
                   + (f"  with {people}" if people else "") + _when_flag(i.date_unrecorded))
    typer.echo(f"{len(items)} upcoming round(s) in the next {days} day(s)")


def _debrief_line(r) -> str:
    what = "(record unavailable: incomplete round)" if r.incomplete else f"{r.stage} {r.format} {r.scheduled_for}"
    held = f"  held {r.held_at}" if r.held_at else ""
    return f"  {r.round_id}  {r.job_id}  {what}{held}{_when_flag(r.date_unrecorded)}"


@interview_app.command("needs-debrief")
def needs_debrief_cmd(
    as_json: bool = JsonOption,
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Held rounds that still need a debrief (oldest first) and overdue scheduled rounds that need an update. Changes
    nothing."""
    root = _util.resolve_root(workspace)
    now = _now()
    got = _guard(as_json, lambda: service.needs_debrief(root, now=now))
    if as_json:
        _util.echo_json({"now": now, "needs_debrief": [dataclasses.asdict(r) for r in got.needs_debrief],
                         "needs_update": [dataclasses.asdict(r) for r in got.needs_update]})
        return
    typer.echo(f"Needs debrief ({len(got.needs_debrief)}), oldest held first:")
    for r in got.needs_debrief:
        typer.echo(_debrief_line(r))
    typer.echo(f"Needs update ({len(got.needs_update)}), oldest date first: record held, cancel or reschedule")
    for r in got.needs_update:
        typer.echo(_debrief_line(r))
