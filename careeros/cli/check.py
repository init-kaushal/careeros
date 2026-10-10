"""careeros check: do the checkable claims in a draft trace to the career memory?"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ledger
from careeros.core.memory import check as core_check
from careeros.core.memory import store
from careeros.core.workspace import WorkspaceError, ensure_writable, resolve_job


def _read_draft(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _util.fail(f"cannot read {path}: {exc}", _util.EXIT_USAGE)


def _print(result: core_check.CheckResult) -> None:
    typer.echo("Evidence check: " + ("PASSED" if result.ok else "FAILED"))
    typer.echo(f"\nSupported ({len(result.supported)})")
    for item in result.supported:
        tag = "claimed" if item["claimed_only"] else "confirmed"
        typer.echo(f"  {item['claim']}  <- {', '.join(item['facts'][:3])} ({tag})")
    if result.allowed_mentions:
        typer.echo(f"\nUnverified mentions (passed with --allow, not evidence): {', '.join(result.allowed_mentions)}")
    typer.echo(f"\nReview required ({len(result.review_required)})")
    for f in result.review_required:
        typer.echo(f"  {f.code}  {f.atom or '(memory)'}  {f.reason}")
        if f.sentence:
            typer.echo(f"         sentence: {f.sentence}")
        typer.echo(f"         fix: {f.fix}")
    typer.echo(f"\nNot evaluated ({len(result.not_evaluated)} sentence(s) with nothing the checker can assess)")
    for sentence in result.not_evaluated:
        typer.echo(f"  - {sentence}")
    typer.echo("  Also not assessed: " + "; ".join(core_check.NOT_EVALUATED_CATEGORIES) + ".")
    for f in result.info:
        typer.echo(f"\nNote {f.code}: {f.reason}. {f.fix}")
    if result.ok:
        typer.echo(
            f"\nPassed: all {len(result.supported)} detected checkable claims (numbers, years, durations, technologies, "
            f"employers, schools, titles, certifications) are supported by your career memory; "
            f"{len(result.not_evaluated)} sentence(s) had nothing the checker can evaluate and {result.claimed_support} "
            f"supporting fact(s) are claimed rather than confirmed. A pass does not mean every claim in the draft was detected."
        )
    else:
        typer.echo(f"\nFailed: {len(result.review_required)} item(s) need review. Remove or rewrite them, or ask the user whether they are true.")


def check_cmd(
    draft: str = typer.Argument(..., metavar="PATH", help="The draft to check, or - to read it from standard input"),
    against: Optional[str] = typer.Option(None, "--against", help="Job the draft is for (its company and title may be named)"),
    allow: Optional[list[str]] = typer.Option(None, "--allow", help="A term to mention without evidence (listed as unverified); repeatable"),
    require_confirmed: bool = typer.Option(False, "--require-confirmed", help="Fail claims backed only by claimed facts"),
    record: bool = typer.Option(False, "--record", help="Write a draft.checked event to the ledger"),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable results and nothing else"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Check a draft against your career memory. Exit 0 only when every detected claim is supported."""
    root = _util.resolve_root(workspace)
    text = _read_draft(draft)
    if not text.strip():
        _util.fail("the draft is empty; there is nothing to check", _util.EXIT_USAGE)
    career = store.load_career(root)
    if career.errors:
        for issue in career.errors:
            typer.echo(f"error: {issue.code} {issue.path}: {issue.message}", err=True)
        _util.fail("the career memory has errors, so the draft cannot be checked; run `careeros validate`", _util.EXIT_USAGE)
    target = None
    if against:
        try:
            job = resolve_job(root, against)
        except WorkspaceError as exc:
            _util.fail(str(exc), _util.EXIT_USAGE)
        target = (job.company, job.title)
    try:
        result = core_check.run_check(
            root, text, allow=list(allow or ()), against=target, require_confirmed=require_confirmed, career=career
        )
    except WorkspaceError as exc:
        _util.fail(str(exc), _util.EXIT_USAGE)
    if record:
        try:
            ensure_writable(root)
            ledger.append_event(
                root, type="draft.checked", actor=actor or _util.default_actor(),
                entity=job.id if against else None, source="cli",
                action=(f"checked draft: {'passed' if result.ok else 'failed'} ({len(result.supported)} supported, "
                        f"{len(result.review_required)} need review, {len(result.not_evaluated)} not evaluated)"),
                artifacts=[f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"],
            )
        except WorkspaceError as exc:
            _util.fail(f"the check ran but could not be recorded: {exc}")
    if as_json:
        _util.echo_json(result.to_dict())
    else:
        _print(result)
    raise typer.Exit(_util.EXIT_OK if result.ok else _util.EXIT_FAIL)
