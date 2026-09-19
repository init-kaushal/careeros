from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.config import GlobalConfig
from careeros.core.models import Company, Goals, Job, OutreachMessage, Person, Profile
from careeros.mailer import send_email
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_local_runtime
from careeros.skills.outreach_draft import generate_outreach_message
from careeros.storage.filesystem import LocalFilesystemStorage

outreach_app = typer.Typer(help="Draft, approve, and send outreach messages.")
people_app = typer.Typer(help="Manage researched people.")
console = Console()

MAX_REGENERATIONS = 5


@people_app.callback()
def _people_app_callback() -> None:
    """Manage researched people."""


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@outreach_app.command()
def send(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
        person_obj = Person.load(runtime.storage, person)
        company_obj = Company.load(runtime.storage, person_obj.company_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job, person, or company not found.[/red]")
        raise typer.Exit(1)

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    draft_text = generate_outreach_message(person_obj, job_obj, company_obj, profile, goals, model=model)
    if not draft_text:
        rprint("[red]Outreach message generation failed.[/red]")
        raise typer.Exit(1)

    regenerations = 0
    while True:
        console.print(Panel(draft_text, title="Outreach to " + person_obj.name))
        if regenerations >= MAX_REGENERATIONS:
            choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
        else:
            choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")
        if choice == "q":
            rprint("Aborted.")
            raise typer.Exit(0)
        if choice == "r":
            regenerations += 1
            draft_text = generate_outreach_message(person_obj, job_obj, company_obj, profile, goals, model=model)
            if not draft_text:
                rprint("[red]Outreach message generation failed.[/red]")
                raise typer.Exit(1)
            continue
        break

    message_id = job + "__" + person
    message = OutreachMessage(
        id=message_id, job_id=job, person_id=person, draft_text=draft_text, created_at=_now(),
    )
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_drafted", "outreach",
        "Drafted outreach to " + person_obj.name + " re: " + job_obj.company + " — " + job_obj.title,
        entity_type="outreach_message", entity_id=message_id,
    ))

    result = runtime.request_approval(ActionProposal(
        action="send_outreach",
        summary="Send outreach email to " + person_obj.name + " re: " + job_obj.company + " — " + job_obj.title + "?",
        entity_type="outreach_message", entity_id=message_id,
    ))
    if not result.approved:
        message = message.model_copy(update={"send_state": "declined"})
        message.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "outreach_send_declined", "outreach",
            "Send declined for outreach to " + person_obj.name,
            entity_type="outreach_message", entity_id=message_id,
        ))
        rprint("Aborted.")
        raise typer.Exit(0)

    runtime.record_activity(runtime.new_event(
        "outreach_send_approved", "outreach",
        "Send approved for outreach to " + person_obj.name,
        entity_type="outreach_message", entity_id=message_id,
    ))

    if not person_obj.email:
        rprint(
            "[red]No email on file for " + person_obj.name
            + ". Run 'careeros people update " + person + " --email <address>' and retry.[/red]"
        )
        raise typer.Exit(1)

    subject = "Regarding " + job_obj.title + " at " + job_obj.company
    try:
        send_email(person_obj.email, subject, draft_text)
    except Exception as exc:
        message = message.model_copy(update={"send_state": "failed"})
        message.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "outreach_send_failed", "outreach",
            "Send failed for outreach to " + person_obj.name + ": " + type(exc).__name__,
            status="failed", entity_type="outreach_message", entity_id=message_id,
        ))
        rprint("[red]Send failed: " + str(exc) + "[/red]")
        raise typer.Exit(1)

    message = message.model_copy(update={"send_state": "sent", "sent_at": _now()})
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_sent", "outreach",
        "Sent outreach to " + person_obj.name + " (" + person_obj.email + ")",
        entity_type="outreach_message", entity_id=message_id,
    ))
    rprint("[green]Sent to " + person_obj.name + "[/green]")


@outreach_app.command(name="mark-referral-requested")
def mark_referral_requested(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    message_id = job + "__" + person
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
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        person_obj = Person.load(runtime.storage, person_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Person " + person_id + " not found.[/red]")
        raise typer.Exit(1)

    person_obj = person_obj.model_copy(update={"email": email})
    person_obj.save(runtime.storage)
    rprint("[green]Updated email for " + person_obj.name + "[/green]")
