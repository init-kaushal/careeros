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


def _record_failed(runtime, summary: str) -> None:
    # So the audit trail covers every invocation, not just the ones that
    # reached the model.
    runtime.record_activity(runtime.new_event(
        "resume_ingested", "ingest", summary, status="failed", entity_type="resume",
    ))


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

    # `new_resume_text` is set only when `path` names a file to adopt as the
    # new master. It is deliberately not written to storage yet — see below.
    new_resume_text: str | None = None
    source_desc = _MASTER
    if path:
        source = Path(path).expanduser()
        source_desc = str(source)
        if not source.exists():
            _record_failed(runtime, "File not found: " + source_desc)
            rprint("[red]File not found: " + source_desc + "[/red]")
            raise typer.Exit(1)
        try:
            new_resume_text = source.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            _record_failed(runtime, "Could not read " + source_desc + ": " + type(exc).__name__)
            rprint("[red]Could not read " + source_desc + ": " + type(exc).__name__ + "[/red]")
            raise typer.Exit(1)
        text = new_resume_text
    else:
        if not runtime.storage.exists(_MASTER):
            _record_failed(runtime, "No resume at " + _MASTER)
            rprint("[red]No resume at " + _MASTER + ". Run 'careeros onboard' first.[/red]")
            raise typer.Exit(1)
        text = runtime.storage.read(_MASTER).decode()

    rprint("Ingesting " + source_desc + "...")
    result = ingest_resume(text, _MASTER, model=model)

    verified = len(result.skills.skills)
    status = "success" if verified else "failed"

    runtime.record_activity(runtime.new_event(
        "resume_ingested", "ingest",
        "Ingested " + source_desc + ": " + str(verified) + " verified, "
        + str(len(result.dropped)) + " dropped",
        status=status, entity_type="resume",
    ))

    if result.dropped:
        rprint("[yellow]Dropped " + str(len(result.dropped))
               + " skill(s) with no verifiable quote: " + ", ".join(result.dropped)
               + "[/yellow]")

    if verified == 0:
        if result.error:
            rprint("[red]Skill extraction failed: " + result.error + ". Your existing "
                   + _SKILLS_PATH + " and " + _MASTER + " were left unchanged.[/red]")
        else:
            rprint("[red]No skills could be verified against " + source_desc
                   + ". Your existing " + _SKILLS_PATH + " and " + _MASTER
                   + " were left unchanged.[/red]")
        raise typer.Exit(1)

    # Only now that ingestion produced at least one verified skill do we
    # commit to replacing the stored master resume. Writing it earlier —
    # before knowing ingestion worked — would leave any skill retained from
    # a previous run with Evidence.line citing a file that no longer
    # contains the quoted text.
    if new_resume_text is not None:
        runtime.storage.atomic_write(_MASTER, new_resume_text.encode())

    result.skills.save(runtime.storage)
    rprint("[green]Stored " + str(verified) + " evidence-backed skill(s).[/green]")
