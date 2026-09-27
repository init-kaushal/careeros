import json
import typer
from rich import print as rprint
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import box
from rich.console import Console
from careeros.config import GlobalConfig
from careeros.config_sources import KNOWN_SOURCES
from careeros.core.models import Goals, Preferences, Profile, Skills
from careeros.runtime.factory import WorkspaceNotConfigured, resolve_storage
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace
from careeros.workspace.manifest import Manifest, check_schema_compatibility, UnsupportedSchemaVersion

try:
    import readline  # noqa: F401
except ImportError:
    pass

_console = Console()

workspace_app = typer.Typer(name="workspace", help="Manage your CareerOS workspace.")


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    try:
        return resolve_storage(workspace_path)
    except WorkspaceNotConfigured:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


@workspace_app.command("status")
def status_cmd(workspace: str = typer.Option(None, "--workspace", help="Workspace path")) -> None:
    storage = _get_storage(workspace)
    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        config = GlobalConfig.load()
        ws_path = workspace or config.workspace_path or "(unknown)"
        rprint(f"[red]No workspace found at {ws_path}. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    except UnsupportedSchemaVersion as e:
        rprint(f"[red]Schema incompatibility: {e}[/red]")
        raise typer.Exit(1)
    profile = Profile.load_or_empty(storage)
    skills = Skills.load_or_empty(storage)

    rprint("[bold]CareerOS Workspace[/bold]")
    rprint(f"  ID:      {ctx.manifest.workspace_id}")
    rprint(f"  Created: {ctx.manifest.created_at[:10]}")
    rprint(f"  Schema:  v{ctx.manifest.schema_version}")
    rprint(f"  Name:    {profile.name or '(not set)'}")
    rprint(f"  Title:   {profile.title or '(not set)'}")
    rprint(f"  Skills:  {len(skills.skills)} recorded")

    activity_files = sorted(p for p in storage.list("activity/") if p.endswith(".jsonl"))
    if activity_files:
        last_content = storage.read(activity_files[-1]).decode()
        lines = [l for l in last_content.strip().split("\n") if l]
        if lines:
            last = json.loads(lines[-1])
            last_ts = last.get('timestamp', '')[:19]
            rprint(f"  Last:    {last_ts} — {last.get('summary', '')}")
    else:
        rprint("  Last:    (no activity yet)")


@workspace_app.command("validate")
def validate_cmd(workspace: str = typer.Option(None, "--workspace", help="Workspace path")) -> None:
    storage = _get_storage(workspace)
    errors: list[str] = []

    if not storage.exists("manifest.json"):
        rprint("[red]FAIL[/red] manifest.json missing")
        raise typer.Exit(1)

    try:
        manifest = Manifest.from_json(storage.read("manifest.json").decode())
        check_schema_compatibility(manifest)
        rprint(f"[green]OK[/green]   manifest.json (schema v{manifest.schema_version})")
    except UnsupportedSchemaVersion as e:
        errors.append(str(e))
        rprint(f"[red]FAIL[/red] manifest.json: {e}")
    except Exception as e:
        errors.append(str(e))
        rprint(f"[red]FAIL[/red] manifest.json parse error: {e}")

    required_paths = [
        "profile/.keep",
        "resumes/versions/.keep",
        "activity/.keep",
        "config/sources.json",
        "config/policies.json",
    ]
    for path in required_paths:
        if storage.exists(path):
            rprint(f"[green]OK[/green]   {path}")
        else:
            errors.append(f"missing: {path}")
            rprint(f"[red]FAIL[/red] missing: {path}")

    if storage.exists("config/sources.json"):
        try:
            raw = json.loads(storage.read("config/sources.json").decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raw = {}
        for item in raw.get("sources", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("source", "?"))
            if not item.get("board"):
                rprint("[yellow]NOTE[/yellow] config/sources.json: '" + name
                       + "' entry is inert (no board slug)")
            elif item.get("source") not in KNOWN_SOURCES:
                rprint("[yellow]NOTE[/yellow] config/sources.json: '" + name
                       + "' entry is inert (no connector for this source)")

    activity_files = sorted(p for p in storage.list("activity/") if p.endswith(".jsonl"))
    for log_path in activity_files[-1:]:
        content = storage.read(log_path).decode()
        lines = [l for l in content.strip().split("\n") if l]
        bad_lines = 0
        for line in lines[-10:]:
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"bad activity line: {e}")
                bad_lines += 1
        if bad_lines:
            rprint(f"[red]FAIL[/red] {log_path}: {bad_lines} unparseable line(s)")
        else:
            rprint(f"[green]OK[/green]   {log_path} ({len(lines)} events)")

    if not errors:
        rprint("\n[bold green]Workspace is valid.[/bold green]")
    else:
        rprint(f"\n[bold red]{len(errors)} error(s) found.[/bold red]")
        raise typer.Exit(1)


@workspace_app.command("configure")
def configure_cmd(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    """Update preferences, goals, or API sources interactively."""
    storage = _get_storage(workspace)

    rprint("[bold]What would you like to update?[/bold]")
    rprint("  [cyan]1[/cyan]  Job preferences (roles, remote, salary, locations)")
    rprint("  [cyan]2[/cyan]  Career goals (short-term, long-term)")
    rprint("  [cyan]3[/cyan]  API job sources (Greenhouse / Lever slugs)")
    rprint("  [cyan]4[/cyan]  All of the above")
    choice = Prompt.ask("Choice", default="1")

    update_prefs = choice in ("1", "4")
    update_goals = choice in ("2", "4")
    update_sources = choice in ("3", "4")

    if update_prefs:
        prefs = Preferences.load_or_empty(storage)

        rprint("\n[bold]Job Preferences[/bold]")
        current_roles = ", ".join(prefs.target_roles) if prefs.target_roles else ""
        roles_raw = Prompt.ask("Target roles (comma-separated)", default=current_roles)
        target_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

        remote_pref = Prompt.ask(
            "Remote preference",
            choices=["remote", "hybrid", "onsite", "any"],
            default=prefs.remote_preference or "any",
        )

        current_comp = str(prefs.minimum_compensation) if prefs.minimum_compensation else ""
        comp_raw = Prompt.ask("Minimum annual compensation in USD (blank to clear)", default=current_comp)
        min_comp = int(comp_raw) if comp_raw.strip().isdigit() else None

        current_locs = ", ".join(prefs.locations) if prefs.locations else ""
        locs_raw = Prompt.ask("Preferred locations (comma-separated)", default=current_locs)
        locations = [l.strip() for l in locs_raw.split(",") if l.strip()]

        Preferences(
            target_roles=target_roles,
            remote_preference=remote_pref,
            minimum_compensation=min_comp,
            locations=locations,
        ).save(storage)
        rprint("[green]Preferences saved.[/green]")

    if update_goals:
        goals = Goals.load_or_empty(storage)

        rprint("\n[bold]Career Goals[/bold]")
        current_st = ", ".join(goals.short_term) if goals.short_term else ""
        current_lt = ", ".join(goals.long_term) if goals.long_term else ""
        st_raw = Prompt.ask("Short-term goals (comma-separated)", default=current_st)
        lt_raw = Prompt.ask("Long-term goals (comma-separated)", default=current_lt)
        Goals(
            short_term=[g.strip() for g in st_raw.split(",") if g.strip()],
            long_term=[g.strip() for g in lt_raw.split(",") if g.strip()],
        ).save(storage)
        rprint("[green]Goals saved.[/green]")

    if update_sources:
        rprint("\n[bold]API Job Sources[/bold]")
        rprint("Enter company board slugs for each ATS. Leave blank to keep current entries.")
        rprint("  Greenhouse: [dim]boards.greenhouse.io/[bold]stripe[/bold][/dim] → slug is [cyan]stripe[/cyan]")
        rprint("  Lever:      [dim]jobs.lever.co/[bold]acme[/bold][/dim]          → slug is [cyan]acme[/cyan]\n")

        try:
            raw = json.loads(storage.read("config/sources.json").decode())
            existing = raw.get("sources", [])
        except Exception:
            existing = []

        current_gh = ", ".join(e["board"] for e in existing if e.get("source") == "greenhouse")
        current_lv = ", ".join(e["board"] for e in existing if e.get("source") == "lever")

        gh_raw = Prompt.ask("Greenhouse slugs (comma-separated)", default=current_gh)
        lv_raw = Prompt.ask("Lever slugs (comma-separated)", default=current_lv)

        sources = []
        for slug in (s.strip() for s in gh_raw.split(",") if s.strip()):
            sources.append({"source": "greenhouse", "board": slug, "company": slug, "mode": "SEARCH_ONLY"})
        for slug in (s.strip() for s in lv_raw.split(",") if s.strip()):
            sources.append({"source": "lever", "board": slug, "company": slug, "mode": "SEARCH_ONLY"})

        storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())
        rprint("[green]Sources saved.[/green]")

    rprint("\n[bold green]Done.[/bold green] Run [bold]careeros workspace status[/bold] to confirm.")
