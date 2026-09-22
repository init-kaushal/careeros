from __future__ import annotations

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.operations.apply import ACTION, execute_apply, propose_apply
from careeros.operations.approvals import resolve_approval
from careeros.operations.errors import (
    BoardSessionRequired, BrowserUnavailable, FillIncomplete, OperationError,
)
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.runtime.factory import (
    WorkspaceNotConfigured, open_local_runtime, resolve_storage,
)
from careeros.runtime.local import LocalRuntime

apply_app = typer.Typer(help="Apply to saved jobs.")
console = Console()

MAX_REGENERATIONS = 5


def _open_runtime(workspace_path: str | None) -> LocalRuntime:
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


@apply_app.command()
def apply_cmd(
    job_id: str = typer.Argument(..., help="Job ID to apply to"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model for cover letter"),
) -> None:
    runtime = _open_runtime(workspace)

    try:
        proposal = propose_apply(runtime, job_id, model=model, action_label="apply")

        if proposal.resume.tailored:
            if proposal.resume.variant is None:
                # No entry count is printed: the sidecar is missing, corrupt, or
                # describes a different document, so any number here would be a
                # claim about a file this one is not.
                rprint("[yellow]Resume: tailored for this job, but its evidence record is "
                       + "unavailable — the upload is unchanged, only its provenance is "
                       + "unknown. Run 'careeros resume variant --job " + job_id
                       + "' to regenerate it.[/yellow]")
            else:
                rprint("[green]Resume: tailored for this job — "
                       + str(proposal.resume.variant.entry_count())
                       + " evidence-backed entries[/green]")
                if proposal.resume.stale_master:
                    rprint("[yellow]This variant was generated from a superseded master "
                           + "resume, so its cited lines no longer match "
                           + proposal.resume.variant.source_file
                           + ". Run 'careeros resume variant --job " + job_id
                           + "' to regenerate it.[/yellow]")
        else:
            rprint("[yellow]Resume: " + proposal.resume.storage_path
                   + " — NOT tailored to this job. Run 'careeros resume variant --job "
                   + job_id + "' to tailor it.[/yellow]")

        # Review loop
        regenerations = 0
        while True:
            console.print(Panel(
                proposal.cover_letter,
                title="Cover Letter — " + proposal.company + " / " + proposal.title,
            ))

            if regenerations >= MAX_REGENERATIONS:
                choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
            else:
                choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")

            if choice == "q":
                resolve_approval(
                    runtime, proposal.approval_id,
                    ApprovalResult(approved=False, reason="aborted at review"),
                    action_label="apply",
                )
                rprint("Aborted.")
                raise typer.Exit(0)
            if choice == "r":
                regenerations += 1
                proposal = propose_apply(runtime, job_id, model=model, action_label="apply")
                continue
            break  # choice == "a"

        # Final approval
        result = runtime.request_approval(ActionProposal(
            action=ACTION,
            summary=proposal.summary,
            entity_type="job", entity_id=job_id,
        ))
        resolve_approval(runtime, proposal.approval_id, result, action_label="apply")

        if not result.approved:
            rprint("Aborted.")
            raise typer.Exit(0)

        outcome = execute_apply(runtime, proposal.approval_id, headless=False, action_label="apply")
    except BoardSessionRequired as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    except BrowserUnavailable as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    except FillIncomplete as exc:
        rprint("[yellow]" + str(exc) + "[/yellow]")
        raise typer.Exit(1)
    except OperationError as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)

    rprint("[green]Applied to " + outcome.company + " — " + outcome.title
           + ". Stage updated to 'applied'.[/green]")
