from pathlib import Path
import typer
from rich import print as rprint
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import box
from rich.console import Console

try:
    import readline  # noqa: F401 — enables arrow-key / cursor editing in prompts on macOS/Linux
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
import uuid

_MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB — generous for any real resume as text
_console = Console()

_SEARCH_DIRS = [
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home(),
]
_RESUME_EXTS = {".md", ".txt", ".pdf"}


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


def _find_resume_candidates() -> list[Path]:
    """Return up to 10 .md/.txt files from common locations, resume-named files first."""
    seen: set[Path] = set()
    priority: list[Path] = []
    rest: list[Path] = []
    for d in _SEARCH_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in _RESUME_EXTS or not f.is_file():
                continue
            if f in seen:
                continue
            seen.add(f)
            if any(kw in f.name.lower() for kw in ("resume", "cv", "curriculum")):
                priority.append(f)
            else:
                rest.append(f)
    candidates = priority + rest
    return candidates[:10]


def _pick_resume_file() -> Path:
    """Interactive file picker: numbered list of candidates + manual-path fallback."""
    candidates = _find_resume_candidates()
    if candidates:
        rprint("\n[bold]Resume files found:[/bold]")
        for i, p in enumerate(candidates, 1):
            rprint(f"  [cyan]{i}[/cyan]  {p}")
        rprint(f"  [cyan]0[/cyan]  Enter a different path")
        choice = Prompt.ask("\nPick a number", default="1")
        if choice.strip().isdigit():
            idx = int(choice.strip())
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]
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
    rprint("CareerOS can poll company ATS boards directly (no browser needed).")
    rprint("Supported: [cyan]Greenhouse[/cyan] and [cyan]Lever[/cyan]\n")
    rprint("For each company, enter its board slug — the part after the ATS URL.")
    rprint("  Greenhouse: [dim]boards.greenhouse.io/[bold]stripe[/bold][/dim] → slug is [cyan]stripe[/cyan]")
    rprint("  Lever:      [dim]jobs.lever.co/[bold]acme[/bold][/dim]          → slug is [cyan]acme[/cyan]\n")

    sources = []
    gh_raw = Prompt.ask(
        "Greenhouse slugs (comma-separated, or press enter to skip)", default=""
    )
    for slug in (s.strip() for s in gh_raw.split(",") if s.strip()):
        sources.append({"source": "greenhouse", "board": slug, "company": slug, "mode": "SEARCH_ONLY"})

    lv_raw = Prompt.ask(
        "Lever slugs (comma-separated, or press enter to skip)", default=""
    )
    for slug in (s.strip() for s in lv_raw.split(",") if s.strip()):
        sources.append({"source": "lever", "board": slug, "company": slug, "mode": "SEARCH_ONLY"})

    runtime.storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())
    rprint("\n[dim]For browser-based boards (LinkedIn, Wellfound, Naukri, Indeed),[/dim]")
    rprint("[dim]run [bold]careeros browser login --board <name>[/bold] after setup.[/dim]")

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
