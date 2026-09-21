from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from careeros.config import GlobalConfig
from careeros.runtime.factory import open_local_runtime
from careeros.skills.resume_ingest import ingest_resume
from careeros.storage.filesystem import LocalFilesystemStorage

resume_app = typer.Typer(name="resume", help="Ingest and inspect your resume.")

_MASTER = "resumes/master.md"
_SKILLS_PATH = "profile/skills.json"


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


@resume_app.command()
def ingest(
    path: str = typer.Argument(None, help="Resume file to ingest; defaults to the stored master"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    """Extract skills from your resume, keeping only evidence-backed ones."""
    # A workspace is required because this writes both the skill file and an
    # audit event.
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    if path:
        source = Path(path).expanduser()
        if not source.exists():
            rprint("[red]File not found: " + str(source) + "[/red]")
            raise typer.Exit(1)
        try:
            text = source.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            rprint("[red]Could not read " + str(source) + ": " + type(exc).__name__ + "[/red]")
            raise typer.Exit(1)
        runtime.storage.atomic_write(_MASTER, text.encode())
    else:
        if not runtime.storage.exists(_MASTER):
            rprint("[red]No resume at " + _MASTER + ". Run 'careeros onboard' first.[/red]")
            raise typer.Exit(1)
        text = runtime.storage.read(_MASTER).decode()

    rprint("Ingesting " + _MASTER + "...")
    result = ingest_resume(text, _MASTER, model=model)

    verified = len(result.skills.skills)
    status = "success" if verified else "failed"

    runtime.record_activity(runtime.new_event(
        "resume_ingested", "ingest",
        "Ingested " + _MASTER + ": " + str(verified) + " verified, "
        + str(len(result.dropped)) + " dropped",
        status=status, entity_type="resume",
    ))

    if result.dropped:
        rprint("[yellow]Dropped " + str(len(result.dropped))
               + " skill(s) with no verifiable quote: " + ", ".join(result.dropped)
               + "[/yellow]")

    if verified == 0:
        rprint("[red]No skills could be verified against " + _MASTER
               + ". Your existing " + _SKILLS_PATH + " was left unchanged.[/red]")
        raise typer.Exit(1)

    result.skills.save(runtime.storage)
    rprint("[green]Stored " + str(verified) + " evidence-backed skill(s).[/green]")
