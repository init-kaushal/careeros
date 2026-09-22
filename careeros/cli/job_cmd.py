from datetime import datetime, timezone
import typer
from rich import print as rprint
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.prompt import Confirm, Prompt

from careeros.core.activity import ActivityLogger
from careeros.core.job_id import make_job_id
from careeros.core.job_store import JobStore
from careeros.core.models import Job, JOB_STAGES
from careeros.runtime.factory import WorkspaceNotConfigured, resolve_storage
from careeros.skills.job_extract import extract_job_fields
from careeros.sources.ats import ATSFetchError
from careeros.sources.base import job_from_posting
from careeros.sources.greenhouse import GreenhouseSource
from careeros.sources.lever import LeverSource
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace

job_app = typer.Typer(name="job", help="Manage your job pipeline.")
console = Console()


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    try:
        return resolve_storage(workspace_path)
    except WorkspaceNotConfigured:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@job_app.command("add")
def add_cmd(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    force: bool = typer.Option(False, "--force", help="Save even if a matching job already exists"),
) -> None:
    storage = _get_storage(workspace)
    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    logger = ActivityLogger(ctx.storage)

    url = Prompt.ask("URL (optional)", default="") or None

    rprint("Paste job description (blank line + Enter to finish):")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
    except EOFError:
        pass
    jd_text = "\n".join(lines)

    fields: dict = {}
    if jd_text:
        rprint("Extracting fields from job description...")
        fields = extract_job_fields(jd_text)
        if not fields:
            rprint("[yellow]Could not extract fields automatically.[/yellow]")

    company = Prompt.ask("Company", default=fields.get("company") or "")
    if not company:
        rprint("[red]Company is required.[/red]")
        raise typer.Exit(1)

    title = Prompt.ask("Title", default=fields.get("title") or "")
    if not title:
        rprint("[red]Title is required.[/red]")
        raise typer.Exit(1)

    location = Prompt.ask("Location", default=fields.get("location") or "") or None

    remote_default = bool(fields.get("remote")) if fields.get("remote") is not None else False
    remote = Confirm.ask("Remote?", default=remote_default)

    sal_min_str = Prompt.ask("Salary min", default=str(fields.get("salary_min") or ""))
    salary_min = int(sal_min_str) if sal_min_str.strip().isdigit() else None

    sal_max_str = Prompt.ask("Salary max", default=str(fields.get("salary_max") or ""))
    salary_max = int(sal_max_str) if sal_max_str.strip().isdigit() else None

    currency = Prompt.ask("Currency", default=fields.get("currency") or "USD")

    now = _now()
    job_id = make_job_id(company, title)
    job = Job(
        id=job_id,
        source="manual",
        url=url,
        company=company,
        title=title,
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        description=jd_text or None,
        requirements=fields.get("requirements") or [],
        stage="saved",
        created_at=now,
        updated_at=now,
    )
    outcome = JobStore(storage).save_new(job, force=force)
    if not outcome.created:
        rprint("[yellow]Matches existing job " + outcome.job.id
               + " — enriched instead of duplicating. Use --force to save anyway.[/yellow]")
    logger.log(logger.new_event(
        "job_added" if outcome.created else "job_merged", "add",
        "Job added: " + company + " — " + title,
        entity_type="job", entity_id=outcome.job.id,
    ))
    rprint("\n[green]Saved[/green] as [bold]" + outcome.job.id + "[/bold]  (stage: "
           + outcome.job.stage + ")")


@job_app.command("list")
def list_cmd(
    stage: str = typer.Option(None, "--stage", help="Filter by stage"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    jobs = Job.list_all(storage)

    if stage:
        if stage not in JOB_STAGES:
            rprint(f"[red]Invalid stage '{stage}'. Valid: {' '.join(JOB_STAGES)}[/red]")
            raise typer.Exit(1)
        jobs = [j for j in jobs if j.stage == stage]

    if not jobs:
        rprint("No jobs found.")
        return

    table = Table(show_header=True)
    table.add_column("ID")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Stage")
    table.add_column("Applied")
    for job in jobs:
        table.add_row(job.id, job.company, job.title, job.stage, job.applied_at or "")
    console.print(table)


@job_app.command("show")
def show_cmd(
    id: str = typer.Argument(..., help="Job ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    try:
        job = Job.load(storage, id)
    except FileNotFoundError:
        rprint(f"[red]Job {id!r} not found.[/red]")
        raise typer.Exit(1)

    lines = [
        f"[bold]Company:[/bold]  {job.company}",
        f"[bold]Title:[/bold]    {job.title}",
        f"[bold]Stage:[/bold]    {job.stage}",
        f"[bold]Source:[/bold]   {job.source}",
        f"[bold]URL:[/bold]      {job.url or '—'}",
        f"[bold]Location:[/bold] {job.location or '—'}",
        f"[bold]Remote:[/bold]   {job.remote}",
        f"[bold]Salary:[/bold]   {job.salary_min}–{job.salary_max} {job.currency}",
        f"[bold]Applied:[/bold]  {job.applied_at or '—'}",
        f"[bold]Created:[/bold]  {job.created_at[:10]}",
    ]
    if job.requirements:
        lines.append("[bold]Requirements:[/bold]")
        for req in job.requirements:
            lines.append(f"  • {req}")
    if job.notes:
        lines.append("[bold]Notes:[/bold]")
        for note in job.notes:
            lines.append(f"  • {note}")

    rprint(Panel("\n".join(lines), title=f"[bold]{id}[/bold]"))


@job_app.command("update")
def update_cmd(
    id: str = typer.Argument(..., help="Job ID"),
    stage: str = typer.Option(..., "--stage", help="New stage"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    if stage not in JOB_STAGES:
        rprint(f"[red]Invalid stage '{stage}'. Valid: {' '.join(JOB_STAGES)}[/red]")
        raise typer.Exit(1)

    storage = _get_storage(workspace)
    try:
        job = Job.load(storage, id)
    except FileNotFoundError:
        rprint(f"[red]Job {id!r} not found.[/red]")
        raise typer.Exit(1)

    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    logger = ActivityLogger(ctx.storage)

    from_stage = job.stage
    now = _now()
    updates: dict = {"stage": stage, "updated_at": now}
    if stage == "applied" and job.applied_at is None:
        updates["applied_at"] = now

    job = job.model_copy(update=updates)
    job.save(storage)
    logger.log(logger.new_event(
        "job_stage_changed", "update",
        f"Job stage changed: {id} {from_stage} → {stage}",
        entity_type="job", entity_id=id,
    ))
    rprint(f"[bold]{id}[/bold]  {from_stage} → [green]{stage}[/green]")


@job_app.command("note")
def note_cmd(
    id: str = typer.Argument(..., help="Job ID"),
    text: str = typer.Argument(..., help="Note text"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    try:
        job = Job.load(storage, id)
    except FileNotFoundError:
        rprint(f"[red]Job {id!r} not found.[/red]")
        raise typer.Exit(1)

    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    logger = ActivityLogger(ctx.storage)

    timestamp = _now()
    note_entry = f"{timestamp} — {text}"
    job = job.model_copy(update={"notes": job.notes + [note_entry], "updated_at": timestamp})
    job.save(storage)
    logger.log(logger.new_event(
        "job_note_added", "note", f"Note added to {id}",
        entity_type="job", entity_id=id,
    ))
    rprint(f"Note added to [bold]{id}[/bold]")


@job_app.command("search")
def search_cmd(
    source: str = typer.Option(..., "--source", help="greenhouse | lever"),
    company: str = typer.Option(..., "--company", help="Company slug (e.g. stripe, acme)"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    logger = ActivityLogger(ctx.storage)

    try:
        if source == "greenhouse":
            postings = GreenhouseSource(company).fetch(company)
        elif source == "lever":
            postings = LeverSource(company).fetch(company)
        else:
            rprint("[red]Unknown source '" + source + "'. Valid: greenhouse lever[/red]")
            raise typer.Exit(1)
    except ATSFetchError as e:
        if "not_found" in str(e):
            rprint("[red]Company '" + company + "' not found on " + source + ".[/red]")
        else:
            rprint("[red]Could not reach " + source + " API. Check your connection.[/red]")
        raise typer.Exit(1)

    if not postings:
        rprint("No open roles found for '" + company + "' on " + source + ".")
        return

    table = Table(show_header=True)
    table.add_column("#", style="bold")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("URL")
    for i, p in enumerate(postings, 1):
        table.add_row(str(i), p.title, p.location or "—", p.url)
    console.print(table)

    picks_str = Prompt.ask("Pick jobs to save (e.g. 1 3 5, or q to quit)")
    if picks_str.strip().lower() == "q":
        return

    indices = []
    for part in picks_str.split():
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(postings):
                indices.append(idx)

    saved = 0
    duplicates = 0
    now = _now()
    store = JobStore(storage)
    for idx in indices:
        outcome = store.save_new(job_from_posting(postings[idx], now))
        if outcome.created:
            saved += 1
        else:
            duplicates += 1
        logger.log(logger.new_event(
            "job_added" if outcome.created else "job_merged", "search",
            "Job saved from " + source + ": " + company + " — " + postings[idx].title,
            entity_type="job", entity_id=outcome.job.id,
        ))

    rprint("[green]Saved " + str(saved) + " job(s), Duplicates: " + str(duplicates) + "[/green]")
