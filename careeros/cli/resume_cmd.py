from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
from playwright.sync_api import Error as PlaywrightError
from rich import print as rprint

from careeros.config import GlobalConfig
from careeros.core.models import Job, Profile, ResumeVariant, Skills
from careeros.render.resume_html import build_resume_html
from careeros.render.resume_pdf import RendererUnavailable, render_pdf
from careeros.runtime.factory import open_local_runtime
from careeros.skills.resume_ingest import ingest_resume
from careeros.skills.resume_variant import select_variant_content
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


_JD_CAP = 4000


def _record_variant(runtime, job_id: str, summary: str, status: str) -> None:
    runtime.record_activity(runtime.new_event(
        "resume_variant", "variant", summary,
        status=status, entity_type="job", entity_id=job_id,
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


@resume_app.command()
def variant(
    job: str = typer.Option(..., "--job", help="Job id to tailor the resume for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    """Build a job-tailored resume from verbatim spans of your master resume."""
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_record = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        _record_variant(runtime, job, "Job " + job + " not found", "failed")
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    if not runtime.storage.exists(_MASTER):
        _record_variant(runtime, job, "No resume at " + _MASTER, "failed")
        rprint("[red]No resume at " + _MASTER
               + ". Run 'careeros resume ingest <path>' first.[/red]")
        raise typer.Exit(1)

    master_text = runtime.storage.read(_MASTER).decode()
    skills = Skills.load_or_empty(runtime.storage)
    profile = Profile.load_or_empty(runtime.storage)
    jd_text = (job_record.description or "")[:_JD_CAP]

    rprint("Tailoring resume for " + job_record.company + " / " + job_record.title + "...")
    result = select_variant_content(jd_text, master_text, skills, _MASTER, model=model)

    if result.dropped:
        rprint("[yellow]Dropped " + str(len(result.dropped))
               + " span(s) the model could not copy verbatim: "
               + "; ".join(result.dropped) + "[/yellow]")

    if result.entry_count() == 0:
        # An empty resume is worse than an untailored one, so refuse to write
        # anything and leave any existing variant exactly as it was.
        if result.error:
            rprint("[red]Tailoring failed: " + result.error
                   + ". No variant was written.[/red]")
            _record_variant(runtime, job, "Tailoring failed: " + result.error, "failed")
        else:
            rprint("[red]No span could be verified against " + _MASTER
                   + ". No variant was written.[/red]")
            _record_variant(runtime, job, "Nothing verified against " + _MASTER, "failed")
        raise typer.Exit(1)

    variant_doc = ResumeVariant(
        job_id=job,
        job_company=job_record.company,
        job_title=job_record.title,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_file=_MASTER,
        sections=list(result.sections),
        dropped=list(result.dropped),
    )

    try:
        pdf_bytes = render_pdf(build_resume_html(variant_doc, profile))
    except RendererUnavailable as exc:
        rprint("[red]" + str(exc) + "[/red]")
        _record_variant(runtime, job, "Renderer unavailable", "failed")
        raise typer.Exit(1)
    except PlaywrightError as exc:
        # render_pdf maps only the missing-binary and missing-system-libraries
        # cases to RendererUnavailable. Anything else would otherwise leave the
        # CLI with a traceback.
        rprint("[red]Rendering failed: " + str(exc) + "[/red]")
        _record_variant(runtime, job, "Rendering failed: " + type(exc).__name__, "failed")
        raise typer.Exit(1)

    pdf_path = ResumeVariant.pdf_path(job)
    json_path = ResumeVariant.json_path(job)

    # PDF first: Task 5's selection keys off the PDF, so a failure between
    # the two writes must leave the variant usable with its audit missing,
    # never a sidecar describing a document that is not there.
    try:
        runtime.storage.atomic_write(pdf_path, pdf_bytes)
    except OSError as exc:
        rprint("[red]Could not write " + pdf_path + ": " + str(exc)
               + ". No variant was written.[/red]")
        _record_variant(runtime, job, "Write failed: " + pdf_path, "failed")
        raise typer.Exit(1)

    try:
        runtime.storage.atomic_write(
            json_path, variant_doc.model_dump_json(indent=2).encode()
        )
    except OSError as exc:
        # The PDF is already the new variant, so a sidecar left over from a
        # previous run would now describe the wrong document. Remove it
        # rather than leave a misleading audit record.
        try:
            if runtime.storage.exists(json_path):
                runtime.storage.delete(json_path)
        except OSError:
            pass
        rprint("[red]Wrote " + pdf_path + " but could not write its evidence "
               + "sidecar: " + str(exc) + ". Re-run to restore it.[/red]")
        _record_variant(runtime, job, "Write failed: " + json_path, "failed")
        raise typer.Exit(1)

    _record_variant(
        runtime, job,
        "Tailored resume for " + job_record.company + " / " + job_record.title + ": "
        + str(variant_doc.entry_count()) + " evidence-backed entries, "
        + str(len(result.dropped)) + " dropped",
        "success",
    )

    for section in variant_doc.sections:
        rprint("  " + section.heading + ": " + str(len(section.entries)) + " entry(ies)")
    rprint("[green]Wrote " + ResumeVariant.pdf_path(job) + " ("
           + str(variant_doc.entry_count()) + " evidence-backed entries).[/green]")
    rprint("Evidence: " + ResumeVariant.json_path(job))
