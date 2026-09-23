from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.core.models import CadencePolicy, OutreachMessage, Person
from careeros.operations.approval_queue import queue_only
from careeros.operations.approvals import list_pending, resolve_approval
from careeros.operations.errors import MissingRecipient, OperationError, PolicyBlocked
from careeros.operations.follow_up import ACTION as FOLLOW_UP_ACTION
from careeros.operations.follow_up import propose_follow_up
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


def _is_due_for_follow_up(message: OutreachMessage, policy: CadencePolicy) -> bool:
    """Mirrors propose_follow_up's own due-ness checks (careeros/operations/follow_up.py).

    The operations module exposes no reusable predicate for "is this
    relationship due" — propose_follow_up inlines the check as a sequence of
    early raises rather than a function returning a bool. Duplicated here
    rather than invented anew: this must stay in exact lockstep with
    propose_follow_up's own logic (last_touched_at-or-sent_at,
    touch_count-or-1-if-ever-sent, the >= max_touches and
    >= days_between_touches comparisons) or the enumeration filter and the
    operation's own refusal will drift apart. If propose_follow_up's due
    logic changes, this must change with it.
    """
    if message.referral_state in {"referral_confirmed", "closed"} or message.closed_reason:
        return False

    last_touch = message.last_touched_at or message.sent_at
    effective_touch_count = message.touch_count or (1 if message.sent_at else 0)

    if effective_touch_count >= policy.max_touches:
        return False

    if last_touch is None:
        return False

    days_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_touch)).days
    return days_since >= policy.days_between_touches


def _days_since_last_touch(message: OutreachMessage) -> int:
    last_touch = message.last_touched_at or message.sent_at
    return (datetime.now(timezone.utc) - datetime.fromisoformat(last_touch)).days


def _effective_touch_count(message: OutreachMessage) -> int:
    return message.touch_count or (1 if message.sent_at else 0)


def _due_relationships(runtime: AutomationRuntime, policy: CadencePolicy) -> list[OutreachMessage]:
    due: list[OutreachMessage] = []
    for path in runtime.storage.list("outreach/"):
        if not path.endswith(".json"):
            continue
        message_id = path[len("outreach/"):-len(".json")]
        try:
            message = OutreachMessage.load(runtime.storage, message_id)
        except (FileNotFoundError, ValueError):
            continue
        if _is_due_for_follow_up(message, policy):
            due.append(message)
    return due


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

    due = _due_relationships(runtime, policy)

    # Skip any relationship that already has a pending follow-up approval.
    # Nothing sends until a human reviews it, so an unreviewed relationship's
    # last_touched_at never advances and it stays due forever — without this
    # skip, a daily cron would re-draft it (and pay for another LLM call)
    # every single day, and the user would eventually review a draft written
    # days after the one they were first offered. open_approval's supersede
    # rule keeps the *approval* count at one but does nothing about the
    # repeated drafting, so the fix has to live here, before propose_follow_up
    # is ever called.
    already_pending = _entity_ids_with_pending_follow_up(runtime)
    due = [message for message in due if message.id not in already_pending]

    if dry_run:
        if not due:
            rprint("No relationships are due for a follow-up.")
        for message in due:
            rprint(
                message.id + ": touch_count=" + str(_effective_touch_count(message))
                + ", days_since_last_touch=" + str(_days_since_last_touch(message))
            )
        return

    proposed_count = 0
    blocked_count = 0
    error_count = 0
    for message in due:
        if proposed_count >= policy.max_follow_ups_per_run:
            break
        try:
            propose_follow_up(
                runtime, message.job_id, message.person_id,
                action_label=FOLLOW_UP_CMD_ACTION_LABEL,
            )
        except PolicyBlocked:
            # Already logged (policy_blocked) inside propose_follow_up.
            blocked_count += 1
            continue
        except OperationError as exc:
            # propose_follow_up logs nothing of its own for this refusal —
            # unlike PolicyBlocked above — so this is the only durable trace
            # that this relationship was considered and failed, rather than
            # simply not being due.
            runtime.record_activity(runtime.new_event(
                "follow_up_propose_error", FOLLOW_UP_CMD_ACTION_LABEL,
                "Error proposing a follow-up for " + message.id + ": "
                + type(exc).__name__,
                status="failed", entity_type="outreach_message", entity_id=message.id,
            ))
            error_count += 1
            continue
        proposed_count += 1

    rprint(
        "Due: " + str(len(due)) + ", Proposed: " + str(proposed_count)
        + ", Blocked: " + str(blocked_count) + ", Errors: " + str(error_count)
    )


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
