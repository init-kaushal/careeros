import json
import typer
from rich import print as rprint
from careeros.config import GlobalConfig
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace
from careeros.workspace.manifest import Manifest, check_schema_compatibility, UnsupportedSchemaVersion

workspace_app = typer.Typer(name="workspace", help="Manage your CareerOS workspace.")


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


@workspace_app.command("status")
def status_cmd(workspace: str = typer.Option(None, "--workspace", help="Workspace path")) -> None:
    storage = _get_storage(workspace)
    ctx = open_workspace(storage)
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
            rprint(f"  Last:    {last['timestamp'][:19]} — {last['summary']}")
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
