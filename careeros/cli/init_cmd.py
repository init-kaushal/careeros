import typer
from pathlib import Path
from rich import print as rprint

from careeros.workspace.scaffold import scaffold, is_scaffolded


def init_cmd(
    path: str = typer.Argument(..., help="Directory to scaffold (e.g. ~/my-job-search)"),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Refresh skill files from the latest CareerOS version (safe on existing workspaces)",
    ),
) -> None:
    """Scaffold a new CareerOS Cowork workspace."""
    target = Path(path).expanduser().resolve()

    try:
        written = scaffold(target, refresh=refresh)
    except FileExistsError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if refresh:
        rprint(f"\n[bold green]Skills refreshed[/bold green] in {target}\n")
        for f in written:
            rprint(f"  [dim]updated  {f}[/dim]")
        rprint("\n[dim]Profile, boards, and pipeline were not touched.[/dim]")
        return

    rprint(f"\n[bold green]Workspace created[/bold green] at {target}\n")
    for f in written:
        rprint(f"  [dim]wrote  {f}[/dim]")
    rprint(f"""
[bold]Next steps:[/bold]

  1. Open [cyan]{target}[/cyan] as a project in Claude Code or Claude.ai
  2. Claude detects the fresh workspace and runs the onboarding interview
  3. Answer 8 quick questions → your profile and boards get written
  4. Say [bold]"browse linkedin"[/bold] to start finding jobs

[dim]To update skills after a CareerOS upgrade:[/dim]
  careeros init {path} --refresh
""")
