from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.browser.driver import launch_browser
from careeros.browser.fillers.generic import GenericFiller
from careeros.browser.fillers.greenhouse import GreenhouseFiller
from careeros.browser.fillers.lever import LeverFiller
from careeros.browser.fillers.linkedin import LinkedInFiller
from careeros.config import GlobalConfig
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_local_runtime
from careeros.skills.cover_letter import generate_cover_letter
from careeros.storage.filesystem import LocalFilesystemStorage

apply_app = typer.Typer(help="Apply to saved jobs.")
console = Console()

FILLERS = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]
MAX_REGENERATIONS = 5
_RESUME_EXTENSIONS = (".pdf", ".docx")


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


@apply_app.command()
def apply_cmd(
    job_id: str = typer.Argument(..., help="Job ID to apply to"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model for cover letter"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    # Load job
    try:
        job = Job.load(runtime.storage, job_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job_id + " not found.[/red]")
        raise typer.Exit(1)

    if not job.url:
        rprint("[red]Job has no URL — add one with `careeros job update`.[/red]")
        raise typer.Exit(1)

    # Find resume (before profile load so failure is fast and clear)
    resume_entries = sorted([
        p for p in runtime.storage.list("resumes/versions/")
        if p.endswith(_RESUME_EXTENSIONS)
    ])
    if not resume_entries:
        rprint("[red]No resume found in resumes/versions/ — add one first.[/red]")
        raise typer.Exit(1)

    resume_file = resume_entries[-1]
    resume_path = runtime.storage.resolve(resume_file)

    # Load profile data (after resume check so early failure avoids unnecessary I/O)
    profile = Profile.load_or_empty(runtime.storage)
    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    # Use stored JD text (two-session approach: avoid keeping browser open during interactive review)
    jd_text = (job.description or "")[:4000]

    # Generate cover letter
    with console.status("Generating cover letter..."):
        cover_letter = generate_cover_letter(jd_text, profile, skills, goals, model=model)

    if not cover_letter:
        rprint("[red]Cover letter generation failed. Check your LLM configuration.[/red]")
        raise typer.Exit(1)

    # Review loop
    regenerations = 0
    while True:
        console.print(Panel(cover_letter, title="Cover Letter — " + job.company + " / " + job.title))

        if regenerations >= MAX_REGENERATIONS:
            choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
        else:
            choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")

        if choice == "q":
            rprint("Aborted.")
            raise typer.Exit(0)
        if choice == "r":
            regenerations += 1
            with console.status("Regenerating..."):
                cover_letter = generate_cover_letter(jd_text, profile, skills, goals, model=model)
            if not cover_letter:
                rprint("[red]Cover letter generation failed.[/red]")
                raise typer.Exit(1)
            continue
        break  # choice == "a"

    # Save cover letter
    cl_storage_path = "applications/" + job_id + "/cover_letter.txt"
    try:
        runtime.storage.atomic_write(cl_storage_path, cover_letter.encode())
        cover_letter_path = runtime.storage.resolve(cl_storage_path)
    except ValueError:
        rprint("[red]Invalid job ID.[/red]")
        raise typer.Exit(1)

    # Detect filler
    filler = next((f for f in FILLERS if f.can_handle(job.url)), None)
    if filler is None:
        rprint("[red]No filler available for this URL.[/red]")
        raise typer.Exit(1)

    # Final approval
    result = runtime.request_approval(ActionProposal(
        action="apply_to_job",
        summary="About to fill the " + filler.platform + " application for "
        + job.company + " — " + job.title + ". Proceed?",
        entity_type="job", entity_id=job_id,
    ))
    if not result.approved:
        rprint("Aborted.")
        raise typer.Exit(0)

    # Launch browser and fill
    try:
        with launch_browser(headless=False) as (_, page):
            success = filler.fill(page, job, profile, cover_letter, cover_letter_path, resume_path)
    except ImportError:
        rprint("[red]Playwright not installed. Run: pip install playwright && playwright install chrome[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        rprint("[red]Browser error: " + str(exc) + ". Stage not updated.[/red]")
        raise typer.Exit(1)

    if success:
        now = _now()
        job = job.model_copy(update={"stage": "applied", "applied_at": now, "updated_at": now})
        job.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "job_applied", "apply",
            "Applied to " + job.company + " — " + job.title,
            entity_type="job", entity_id=job_id,
        ))
        rprint("[green]Applied to " + job.company + " — " + job.title + ". Stage updated to 'applied'.[/green]")
    else:
        rprint("[yellow]Form fill incomplete — review the browser window. Stage not updated.[/yellow]")
        raise typer.Exit(1)
