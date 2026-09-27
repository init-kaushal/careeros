from pathlib import Path
import typer
from rich import print as rprint
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import box
from rich.console import Console

try:
    import readline
    # Keep left/right/backspace/Home/End for in-line editing.
    # Disable up/down history navigation — it replaces the visible prompt line,
    # making the question appear to vanish.
    readline.parse_and_bind(r'"\e[A": ""')
    readline.parse_and_bind(r'"\e[B": ""')
except ImportError:
    pass

from careeros.cli.resume_cmd import _MASTER
from careeros.config import GlobalConfig
from careeros.core.models import Goals, Preferences, Profile
from careeros.runtime.local import LocalRuntime
from careeros.skills.profile_extract import extract_basic_profile
from careeros.skills.resume_ingest import ingest_resume
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace
import json
import re
import uuid

_MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB — generous for any real resume as text
_console = Console()

_BROWSER_BOARDS = {"linkedin", "indeed", "wellfound", "naukri", "instahyre", "glassdoor"}


def _parse_board_entries(raw: str) -> tuple[list[dict], list[str]]:
    """Parse a comma-separated list of board URLs or slugs into source dicts.

    Returns (entries, unrecognized) where unrecognized items are passed back
    to the caller for display (e.g. browser-only boards like LinkedIn).
    Accepts:
      https://boards.greenhouse.io/stripe/jobs/123  → greenhouse:stripe
      https://jobs.lever.co/acme                   → lever:acme
      greenhouse:stripe                             → greenhouse:stripe
      lever:acme                                   → lever:acme
    """
    entries: list[dict] = []
    unrecognized: list[str] = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        # Full Greenhouse URL
        m = re.search(r'boards\.greenhouse\.io/([^/?#\s]+)', item)
        if m:
            slug = m.group(1).strip("/")
            entries.append({"source": "greenhouse", "board": slug, "company": slug, "mode": "SEARCH_ONLY"})
            continue
        # Full Lever URL
        m = re.search(r'jobs\.lever\.co/([^/?#\s]+)', item)
        if m:
            slug = m.group(1).strip("/")
            entries.append({"source": "lever", "board": slug, "company": slug, "mode": "SEARCH_ONLY"})
            continue
        # Short form source:slug
        if ":" in item:
            source, _, slug = item.partition(":")
            source, slug = source.strip().lower(), slug.strip()
            if source in ("greenhouse", "lever") and slug:
                entries.append({"source": source, "board": slug, "company": slug, "mode": "SEARCH_ONLY"})
                continue
        unrecognized.append(item)
    return entries, unrecognized

def _read_resume(path: Path) -> str:
    """Return the text content of a resume file. Supports .pdf, .md, .txt."""
    if path.suffix.lower() == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(p.strip() for p in pages if p.strip())
            if not text.strip():
                raise ValueError("PDF contained no extractable text — may be scanned/image-only.")
            return text
        except ImportError:
            raise RuntimeError(
                "pypdf is not installed. Run: pip install pypdf"
            )
    return path.read_text(encoding="utf-8", errors="replace")


def _pick_resume_file() -> Path:
    path_str = Prompt.ask("\nPath to your resume (PDF, Markdown, or plain text)")
    return Path(path_str).expanduser()


def _show_profile_summary(profile, ingested) -> None:
    """Print a rich table summarising everything extracted from the resume."""
    skills = ingested.skills
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("Name", profile.name or "(not found)")
    table.add_row("Title", profile.title or "(not found)")
    table.add_row("Experience", f"{profile.years_of_experience} years" if profile.years_of_experience else "?")
    if profile.summary:
        snippet = profile.summary[:120] + ("…" if len(profile.summary) > 120 else "")
        table.add_row("Summary", snippet)
    if skills.skills:
        table.add_row("Skills", ", ".join(s.name for s in skills.skills))
    else:
        table.add_row("Skills", "(none verified)")
    rprint("\n[bold]Extracted profile:[/bold]")
    _console.print(table)
    if ingested.error:
        rprint("[bold red]  Skill extraction failed: " + ingested.error + "[/bold red]")
        rprint("[yellow]  Run 'careeros resume ingest' once your API key is working.[/yellow]")
    elif ingested.dropped:
        rprint("[yellow]  Dropped " + str(len(ingested.dropped))
               + " unverifiable skill(s): " + ", ".join(ingested.dropped) + "[/yellow]")


def onboard_cmd(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Path for new workspace"),
) -> None:
    rprint("[bold]Welcome to CareerOS[/bold]")
    rprint("Let's set up your workspace.\n")

    # Step 1: workspace path
    ws_path = workspace or Prompt.ask(
        "Where should I store your workspace?",
        default=str(Path.home() / "career"),
    )
    ws_path = str(Path(ws_path).expanduser())

    storage = LocalFilesystemStorage(ws_path)
    try:
        ctx = init_workspace(storage)
    except FileExistsError:
        rprint(f"[red]Workspace already exists at {ws_path}.[/red]")
        rprint("Run [bold]careeros workspace status[/bold] to inspect it.")
        raise typer.Exit(1)
    runtime = LocalRuntime(storage, ctx, session_id=uuid.uuid4().hex)
    runtime.record_activity(runtime.new_event("workspace_created", "init", "Workspace initialized at " + ws_path))
    rprint(f"\n[green]Workspace created at {ws_path}[/green]")

    # Step 2: resume — file picker with numbered candidates
    resume_file = _pick_resume_file()
    if not resume_file.exists():
        rprint(f"[red]File not found: {resume_file}[/red]")
        raise typer.Exit(1)
    if resume_file.stat().st_size > _MAX_RESUME_BYTES:
        rprint("[red]File too large (max 10MB). Convert PDF to text first.[/red]")
        raise typer.Exit(1)

    try:
        resume_text = _read_resume(resume_file)
    except Exception as exc:
        rprint(f"[red]Could not read resume: {exc}[/red]")
        raise typer.Exit(1)
    runtime.storage.atomic_write(_MASTER, resume_text.encode())
    runtime.record_activity(runtime.new_event(
        "resume_imported", "import", "Resume imported from " + str(resume_file), entity_type="resume"
    ))

    # Step 3: profile extraction + confirmation loop
    # extract_basic_profile and ingest_resume never raise — failures surface via
    # ingested.error and an empty/partial result, which is the honest outcome of
    # verbatim-verification guarantees.
    while True:
        rprint("\nExtracting profile from resume… [dim](this may take up to a minute)[/dim]")
        profile = extract_basic_profile(resume_text)
        ingested = ingest_resume(resume_text, _MASTER)

        _show_profile_summary(profile, ingested)

        if Confirm.ask("\nDoes this look right?", default=True):
            break
        rprint("\n[yellow]Options:[/yellow]")
        rprint("  [cyan]1[/cyan]  Pick a different resume file")
        rprint("  [cyan]2[/cyan]  Continue anyway (edit profile/profile.json later)")
        fix = Prompt.ask("Choice", default="2")
        if fix.strip() == "1":
            resume_file = _pick_resume_file()
            if not resume_file.exists():
                rprint(f"[red]File not found: {resume_file}[/red]")
            else:
                try:
                    resume_text = _read_resume(resume_file)
                    runtime.storage.atomic_write(_MASTER, resume_text.encode())
                except Exception as exc:
                    rprint(f"[red]Could not read resume: {exc}[/red]")
        else:
            rprint("[yellow]Continuing — edit profile/profile.json in your workspace to correct it.[/yellow]")
            break

    profile.save(runtime.storage)
    ingested.skills.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "profile_extracted", "extract", "Profile extracted: " + (profile.name or ""), entity_type="profile"
    ))

    # Step 4: preferences
    rprint("\n[bold]Job Preferences[/bold]")
    roles_raw = Prompt.ask("Target roles (comma-separated, or press enter to skip)", default="")
    target_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    remote_pref = Prompt.ask(
        "Remote preference", choices=["remote", "hybrid", "onsite", "any"], default="any"
    )

    comp_raw = Prompt.ask("Minimum annual compensation in USD (or press enter to skip)", default="")
    min_comp = int(comp_raw) if comp_raw.strip().isdigit() else None

    locs_raw = Prompt.ask("Preferred locations (comma-separated, or press enter to skip)", default="")
    locations = [l.strip() for l in locs_raw.split(",") if l.strip()]

    prefs = Preferences(
        target_roles=target_roles,
        remote_preference=remote_pref,
        minimum_compensation=min_comp,
        locations=locations,
    )
    prefs.save(runtime.storage)

    # Step 5: job sources
    rprint("\n[bold]API Job Sources[/bold]")
    rprint("Paste Greenhouse or Lever board URLs (comma-separated), or press enter to skip.")
    rprint("[dim]Example: https://boards.greenhouse.io/stripe, https://jobs.lever.co/acme[/dim]")
    boards_raw = Prompt.ask("Board URLs", default="")
    sources, unrecognized = _parse_board_entries(boards_raw)
    runtime.storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())
    if sources:
        rprint("[green]Added " + str(len(sources)) + " board(s): " +
               ", ".join(e["source"] + ":" + e["board"] for e in sources) + "[/green]")
    for item in unrecognized:
        if item.lower() in _BROWSER_BOARDS:
            rprint(f"[yellow]{item}[/yellow] [dim]is browser-based — run [bold]careeros browser login --board {item.lower()}[/bold] after setup.[/dim]")
        else:
            rprint(f"[yellow]Skipped '{item}'[/yellow] [dim](not a recognized Greenhouse or Lever URL)[/dim]")

    # Step 6: goals (optional)
    goals = Goals()
    if Confirm.ask("\nWould you like to set career goals now?", default=False):
        st_raw = Prompt.ask("Short-term goals (comma-separated)", default="")
        lt_raw = Prompt.ask("Long-term goals (comma-separated)", default="")
        goals = Goals(
            short_term=[g.strip() for g in st_raw.split(",") if g.strip()],
            long_term=[g.strip() for g in lt_raw.split(",") if g.strip()],
        )
    goals.save(runtime.storage)

    # Save global config
    GlobalConfig(workspace_path=ws_path).save()
    runtime.record_activity(runtime.new_event("onboard_complete", "onboard", "Onboarding complete"))

    rprint(f"\n[bold green]CareerOS ready.[/bold green]")
    rprint(f"Workspace: {ws_path}")
    rprint("Run [bold]careeros workspace status[/bold] to see your profile summary.")
