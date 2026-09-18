from pathlib import Path
import typer
from rich import print as rprint
from rich.prompt import Confirm, Prompt

from careeros.config import GlobalConfig
from careeros.core.activity import ActivityLogger
from careeros.core.models import Goals, Preferences, Profile, Skills
from careeros.skills.profile_extract import extract_basic_profile
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace
import json


def onboard_cmd(
    workspace: str = typer.Option(None, "--workspace", "-w", help="Path for new workspace"),
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
    ctx = init_workspace(storage)
    logger = ActivityLogger(ctx.storage)
    logger.log(logger.new_event("workspace_created", "init", f"Workspace initialized at {ws_path}"))
    rprint(f"\n[green]Workspace created at {ws_path}[/green]")

    # Step 2: resume
    resume_path_str = Prompt.ask("\nPath to your resume (Markdown or plain text)")
    resume_file = Path(resume_path_str).expanduser()
    if not resume_file.exists():
        rprint(f"[red]File not found: {resume_file}[/red]")
        raise typer.Exit(1)

    resume_text = resume_file.read_text()
    storage.atomic_write("resumes/master.md", resume_text.encode())
    logger.log(logger.new_event("resume_imported", "import", f"Resume imported from {resume_path_str}", entity_type="resume"))

    # Step 3: profile extraction
    rprint("\nExtracting profile from resume...")
    try:
        profile, skills = extract_basic_profile(resume_text)
    except Exception as e:
        rprint(f"[yellow]Extraction failed ({e}). Starting with empty profile.[/yellow]")
        profile, skills = Profile(), Skills()

    rprint("\n[bold]Extracted profile:[/bold]")
    rprint(f"  Name:       {profile.name or '(not found)'}")
    rprint(f"  Title:      {profile.title or '(not found)'}")
    rprint(f"  Experience: {profile.years_of_experience or '?'} years")
    rprint(f"  Skills:     {len(skills.skills)} found")

    if not Confirm.ask("\nDoes this look right?", default=True):
        rprint("[yellow]Edit profile/profile.json in your workspace to correct it.[/yellow]")

    profile.save(storage)
    skills.save(storage)
    logger.log(logger.new_event("profile_extracted", "extract", f"Profile extracted: {profile.name}", entity_type="profile"))

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
    prefs.save(storage)

    # Step 5: job sources
    rprint("\n[bold]Job Sources[/bold]")
    rprint("Which sources may CareerOS search? Available: greenhouse, linkedin, lever, naukri")
    sources_raw = Prompt.ask("Sources (comma-separated)", default="greenhouse")
    sources = [
        {"source": s.strip(), "mode": "SEARCH_ONLY"}
        for s in sources_raw.split(",")
        if s.strip()
    ]
    storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())

    # Step 6: goals (optional)
    goals = Goals()
    if Confirm.ask("\nWould you like to set career goals now?", default=False):
        st_raw = Prompt.ask("Short-term goals (comma-separated)", default="")
        lt_raw = Prompt.ask("Long-term goals (comma-separated)", default="")
        goals = Goals(
            short_term=[g.strip() for g in st_raw.split(",") if g.strip()],
            long_term=[g.strip() for g in lt_raw.split(",") if g.strip()],
        )
    goals.save(storage)

    # Save global config
    GlobalConfig(workspace_path=ws_path).save()
    logger.log(logger.new_event("onboard_complete", "onboard", "Onboarding complete"))

    rprint(f"\n[bold green]CareerOS ready.[/bold green]")
    rprint(f"Workspace: {ws_path}")
    rprint("Run [bold]careeros workspace status[/bold] to see your profile summary.")
