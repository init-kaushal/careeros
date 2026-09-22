from __future__ import annotations

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.core.models import OutreachMessage, Person
from careeros.operations.approvals import resolve_approval
from careeros.operations.errors import MissingRecipient, OperationError
from careeros.operations.outreach import (
    ACTION, decline_outreach_send, execute_outreach_send, make_message_id,
    propose_outreach_send,
)
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.runtime.factory import (
    WorkspaceNotConfigured, open_local_runtime, resolve_storage,
)
from careeros.runtime.local import LocalRuntime

outreach_app = typer.Typer(help="Draft, approve, and send outreach messages.")
people_app = typer.Typer(help="Manage researched people.")
console = Console()

MAX_REGENERATIONS = 5


@people_app.callback()
def _people_app_callback() -> None:
    """Manage researched people."""


def _open_runtime(workspace_path: str | None) -> LocalRuntime:
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


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
