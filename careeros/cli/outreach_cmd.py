from __future__ import annotations

from datetime import datetime, timezone

import typer
from pydantic import ValidationError
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.core.models import CadencePolicy, OutreachMessage, Person
from careeros.operations.approval_queue import queue_only
from careeros.operations.approvals import list_pending, resolve_approval
from careeros.operations.errors import (
    CadenceExhausted, DraftFailed, MalformedTouchTimestamp, MissingRecipient,
    NotDueForFollowUp, OperationError, PolicyBlocked, RelationshipClosed,
)
from careeros.operations.follow_up import ACTION as FOLLOW_UP_ACTION
from careeros.operations.follow_up import CadenceStatus, check_follow_up_due, propose_follow_up
from careeros.operations.outreach import (
    ACTION, decline_outreach_send, execute_outreach_send, make_message_id,
    propose_outreach_send,
)
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.runtime.factory import (
    WorkspaceNotConfigured, open_automation_runtime, open_local_runtime, resolve_storage,
)
from careeros.runtime.local import LocalRuntime

outreach_app = typer.Typer(help="Draft, approve, and send outreach messages.")
people_app = typer.Typer(help="Manage researched people.")
console = Console()

MAX_REGENERATIONS = 5

# action_label stamped on every operations call this command makes, so the
# activity log and any approval payload can be traced back to this specific
# scheduled entrypoint rather than to the interactive `outreach send` flow.
FOLLOW_UP_CMD_ACTION_LABEL = "outreach-follow-up"

# Three drafting failures in a row means the LLM provider is down or the key
# is dead, not that three relationships are individually unlucky — and every
# DraftFailed has already been paid for, because
# generate_follow_up_message swallows every provider exception into "".
# Stopping the whole run follows the precedent discover-and-apply sets for a
# profile_busy BrowserUnavailable, for exactly the same reason: continuing
# buys nothing but identical paid failures.
MAX_CONSECUTIVE_DRAFT_FAILURES = 3


@people_app.callback()
def _people_app_callback() -> None:
    """Manage researched people."""


def _open_runtime(workspace_path: str | None) -> LocalRuntime:
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _open_automation_runtime(workspace_path: str | None) -> AutomationRuntime:
    try:
        # AutomationRuntime's own default is auto-approve — the right default
        # for discover-and-apply, which is the one command in this project
        # where a wrong decision only costs an application. It is the wrong
        # default here: this command proposes and stops, and must never
        # accidentally approve anything if a future code path along this
        # command's route ever calls request_approval. Passing queue_only
        # makes that path deny-by-default instead of silently saying yes,
        # leaving the class-wide default untouched for everyone else.
        return open_automation_runtime(
            resolve_storage(workspace_path), approval_callback=queue_only,
        )
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _due_status(
    message: OutreachMessage, policy: CadencePolicy, now: datetime,
) -> CadenceStatus | None:
    """This command's non-raising view of check_follow_up_due.

    The shared checker in the operations layer raises, because each refusal
    carries structured detail its other callers need. An enumeration filter
    needs none of it — only "consider this one or not" — so it catches the
    three ordinary refusals here and answers None. A CadenceStatus rather
    than a bool comes back on the due path because the caller needs the
    days_since and touch count the check already computed; computing them
    again from a second `now` is precisely the drift this consolidation
    removes.

    MalformedTouchTimestamp is deliberately NOT caught here: an unusable
    timestamp is a workspace defect the operator has to see, not a
    relationship that merely isn't due yet.
    """
    try:
        return check_follow_up_due(message, policy, now)
    except (RelationshipClosed, CadenceExhausted, NotDueForFollowUp):
        return None


def _due_relationships(
    runtime: AutomationRuntime, policy: CadencePolicy, now: datetime,
) -> list[tuple[OutreachMessage, CadenceStatus]]:
    """Every relationship due at `now`, paired with what made it due.

    Nothing this loop does may escape it. A single unusable last-touch
    timestamp used to take the entire run down — the parse happened outside
    the try, so a hand-edited "not-a-date" (ValueError) or a legal but
    offset-naive "2026-01-01T10:00:00" (TypeError on the subtraction) exited
    1 with empty output and nothing proposed for any healthy relationship.
    The checker now reports that as a structured refusal, and it costs only
    the one record.
    """
    due: list[tuple[OutreachMessage, CadenceStatus]] = []
    seen: set[str] = set()
    for path in sorted(runtime.storage.list("outreach/")):
        if not path.endswith(".json"):
            continue
        path_id = path[len("outreach/"):-len(".json")]
        try:
            message = OutreachMessage.load(runtime.storage, path_id)
        except (FileNotFoundError, ValueError):
            continue
        # storage.list is a recursive rglob, but this slice assumes the flat
        # outreach/<id>.json layout. A nested copy — a backup, an archive/
        # subdir — yields path_id "archive/<id>" while the record it loads
        # still carries id "<id>", so trusting the slice enumerated one
        # relationship twice: two paid LLM calls, an immediately-superseded
        # approval, and two of the per-run cap spent on one person. Keying
        # off the loaded id, and requiring it to round-trip to the path it
        # was found at, drops the copy; the canonical file is enumerated in
        # its own right, so the relationship itself is never lost.
        if message.id != path_id or message.id in seen:
            continue
        seen.add(message.id)
        try:
            status = _due_status(message, policy, now)
        except MalformedTouchTimestamp as exc:
            # Visible, because careeros never writes such a value itself:
            # it came from a hand edit or an agent writing through the
            # documented integration contract, and only the operator can
            # fix it.
            rprint("[yellow]Skipping " + message.id + ": " + str(exc) + "[/yellow]")
            continue
        if status is not None:
            due.append((message, status))
    return due


def _log_propose_error(
    runtime: AutomationRuntime, message_id: str, exc: Exception,
) -> None:
    """The only durable trace that a relationship was tried and failed.

    propose_follow_up logs nothing of its own for these refusals — unlike
    PolicyBlocked, which it logs itself — so without this there is nothing
    in the activity log to distinguish "considered and failed" from "was not
    due". str(exc) goes in alongside the type name because the type alone
    discards the entire detail: which entity was missing, how many days
    short, what the unusable timestamp actually said.
    """
    runtime.record_activity(runtime.new_event(
        "follow_up_propose_error", FOLLOW_UP_CMD_ACTION_LABEL,
        "Error proposing a follow-up for " + message_id + ": "
        + type(exc).__name__ + ": " + str(exc),
        status="failed", entity_type="outreach_message", entity_id=message_id,
    ))


def _entity_ids_with_pending_follow_up(runtime: AutomationRuntime) -> set[str]:
    return {
        approval.entity_id for approval in list_pending(runtime.storage)
        if approval.action == FOLLOW_UP_ACTION
    }


@outreach_app.command()
def send(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    runtime = _open_runtime(workspace)

    try:
        proposal = propose_outreach_send(runtime, job, person, model=model, action_label="outreach")

        regenerations = 0
        while True:
            console.print(Panel(proposal.draft_text, title="Outreach to " + proposal.recipient_name))
            if regenerations >= MAX_REGENERATIONS:
                choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
            else:
                choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")
            if choice == "q":
                resolve_approval(
                    runtime, proposal.approval_id,
                    ApprovalResult(approved=False, reason="aborted at review"),
                    action_label="outreach",
                )
                decline_outreach_send(runtime, proposal.approval_id, action_label="outreach")
                rprint("Aborted.")
                raise typer.Exit(0)
            if choice == "r":
                regenerations += 1
                proposal = propose_outreach_send(runtime, job, person, model=model, action_label="outreach")
                continue
            break

        if proposal.already_sent_at:
            rprint(
                "[yellow]An outreach email to " + proposal.recipient_name
                + " was already sent on " + proposal.already_sent_at
                + ". This will send another.[/yellow]"
            )

        result = runtime.request_approval(ActionProposal(
            action=ACTION,
            summary=proposal.summary,
            entity_type="outreach_message", entity_id=proposal.message_id,
        ))
        resolve_approval(runtime, proposal.approval_id, result, action_label="outreach")

        if not result.approved:
            decline_outreach_send(runtime, proposal.approval_id, action_label="outreach")
            rprint("Aborted.")
            raise typer.Exit(0)

        outcome = execute_outreach_send(runtime, proposal.approval_id, action_label="outreach")
    except MissingRecipient as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    except OperationError as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)

    rprint("[green]Sent to " + outcome.recipient_name + "[/green]")


@outreach_app.command(name="mark-referral-requested")
def mark_referral_requested(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    runtime = _open_runtime(workspace)

    message_id = make_message_id(job, person)
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]No outreach message found for this job/person pair.[/red]")
        raise typer.Exit(1)

    message = message.model_copy(update={"referral_state": "referral_requested"})
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "referral_requested", "outreach",
        "Referral requested for job " + job,
        entity_type="outreach_message", entity_id=message_id,
    ))
    rprint("[green]Referral state updated.[/green]")


@outreach_app.command(name="follow-up")
def follow_up_cmd(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List relationships due for a follow-up without proposing anything.",
    ),
) -> None:
    """Scheduled proposer: drafts a follow-up for every due relationship and
    stops. It sends nothing — every draft is left as a pending Approval for
    a human to review with 'careeros outreach review'.
    """
    runtime = _open_automation_runtime(workspace)

    try:
        policy = CadencePolicy.load(runtime.storage)
    except FileNotFoundError:
        rprint(
            "[red]config/cadence_policy.json not found — create one before "
            "running outreach follow-up.[/red]"
        )
        raise typer.Exit(1)
    except ValidationError as exc:
        # A missing policy got a friendly message and an invalid one got a
        # raw pydantic traceback, though both land on the same operator with
        # the same remedy — open the file and fix it.
        rprint(
            "[red]config/cadence_policy.json is not a valid cadence policy — "
            "fix it before running outreach follow-up:[/red]"
        )
        rprint(str(exc))
        raise typer.Exit(1)

    # One instant for the whole run. Every due-ness decision below — the
    # enumeration filter and the check inside each propose_follow_up — is
    # made against this same value, so a run that straddles midnight cannot
    # classify one relationship two ways.
    now = datetime.now(timezone.utc)

    due = _due_relationships(runtime, policy, now)

    # Skip any relationship that already has a pending follow-up approval.
    # Nothing sends until a human reviews it, so an unreviewed relationship's
    # last_touched_at never advances and it stays due forever — without this
    # skip, a daily cron would re-draft it (and pay for another LLM call)
    # every single day, and the user would eventually review a draft written
    # days after the one they were first offered. open_approval's supersede
    # rule keeps the *approval* count at one but does nothing about the
    # repeated drafting, so the fix has to live here, before propose_follow_up
    # is ever called.
    #
    # Counted separately rather than filtered out of `due`, because these
    # relationships really are past due: folding them away made the summary
    # report "Due: 0" for three genuinely past-due relationships, and made
    # --dry-run claim none were due at all.
    already_pending = _entity_ids_with_pending_follow_up(runtime)
    actionable = [(m, s) for m, s in due if m.id not in already_pending]
    awaiting_review = len(due) - len(actionable)

    if dry_run:
        for message, status in actionable:
            rprint(
                message.id + ": touch_count=" + str(status.effective_touch_count)
                + ", days_since_last_touch=" + str(status.days_since)
            )
        if not due:
            rprint("No relationships are due for a follow-up.")
        elif awaiting_review:
            # Worded as a count of its own so it is true whether or not
            # anything was listed above it. When everything due is awaiting
            # review, the old code printed "No relationships are due for a
            # follow-up." instead, which was simply false.
            rprint(
                "Due but already awaiting review, so not re-drafted: "
                + str(awaiting_review)
            )
        return

    proposed_count = 0
    blocked_count = 0
    error_count = 0
    deferred_count = 0
    # Paid LLM attempts, which is what the cap has to bound. A DraftFailed
    # has already reached the provider, because generate_follow_up_message
    # swallows every litellm exception into "" — so counting successes
    # instead turned a cap of 1 into one paid, discarded call for every due
    # relationship. PolicyBlocked is refused before drafting and spends
    # nothing, so it must not spend a slot either.
    drafting_attempts = 0
    consecutive_draft_failures = 0
    unexamined_count = 0
    aborted = False
    for index, (message, _status) in enumerate(actionable):
        if drafting_attempts >= policy.max_follow_ups_per_run:
            deferred_count += 1
            continue
        try:
            propose_follow_up(
                runtime, message.job_id, message.person_id,
                action_label=FOLLOW_UP_CMD_ACTION_LABEL, now=now,
            )
        except PolicyBlocked:
            # Already logged (policy_blocked) inside propose_follow_up, and
            # refused before any LLM call, so the budget is untouched.
            blocked_count += 1
            continue
        except DraftFailed as exc:
            drafting_attempts += 1
            consecutive_draft_failures += 1
            error_count += 1
            _log_propose_error(runtime, message.id, exc)
            if consecutive_draft_failures >= MAX_CONSECUTIVE_DRAFT_FAILURES:
                unexamined_count = len(actionable) - index - 1
                aborted = True
                runtime.record_activity(runtime.new_event(
                    "follow_up_run_aborted", FOLLOW_UP_CMD_ACTION_LABEL,
                    "Aborted the run after "
                    + str(MAX_CONSECUTIVE_DRAFT_FAILURES)
                    + " consecutive drafting failures; "
                    + str(unexamined_count) + " due relationship(s) not examined.",
                    status="failed",
                ))
                rprint(
                    "[red]Aborting: " + str(MAX_CONSECUTIVE_DRAFT_FAILURES)
                    + " follow-up drafts failed in a row, so the LLM provider is "
                    "down rather than these relationships being unlucky. Every "
                    "further attempt would be paid for and discarded.[/red]"
                )
                break
            continue
        except OperationError as exc:
            error_count += 1
            _log_propose_error(runtime, message.id, exc)
            continue
        drafting_attempts += 1
        consecutive_draft_failures = 0
        proposed_count += 1

    # Distinct outcomes get distinct counters. The old single line folded
    # awaiting-review into "not due" and left cap-deferred relationships
    # unaccounted for entirely, so "Due: 7, Proposed: 2" said nothing about
    # the other five. This is the operator's whole view of the cadence in a
    # cron mail, so no number in it may be false.
    rprint(
        "Due: " + str(len(due)) + " — " + str(len(actionable)) + " actionable, "
        + str(awaiting_review) + " awaiting review."
    )
    rprint(
        "Proposed: " + str(proposed_count)
        + ", Deferred by cap: " + str(deferred_count)
        + ", Blocked: " + str(blocked_count)
        + ", Errors: " + str(error_count)
    )
    if aborted:
        rprint("Not examined (run aborted): " + str(unexamined_count))
        # Nonzero so a cron run that achieved nothing is not silently
        # indistinguishable from one with nothing to do.
        raise typer.Exit(1)


@people_app.command()
def update(
    person_id: str = typer.Argument(..., help="Person ID"),
    email: str = typer.Option(..., "--email", help="Email address to set"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    runtime = _open_runtime(workspace)

    try:
        person_obj = Person.load(runtime.storage, person_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Person " + person_id + " not found.[/red]")
        raise typer.Exit(1)

    person_obj = person_obj.model_copy(update={"email": email})
    person_obj.save(runtime.storage)
    rprint("[green]Updated email for " + person_obj.name + "[/green]")
