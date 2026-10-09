import typer
from pathlib import Path
from rich import print as rprint

from careeros.core import ledger, models, versions
from careeros.core.models import WorkspaceMeta
from careeros.core.workspace import LEDGER_REL, WorkspaceError, ensure_writable, load_meta, save_meta
from careeros.workspace.scaffold import scaffold


def _record_init(target: Path, runtime: str, *, refresh: bool, was_empty: bool) -> None:
    """Keep .careeros/workspace.yaml and the ledger in step with what init just did."""
    meta = load_meta(target)
    now = models.utc_now()
    installed = versions.installed_version()
    if meta is None:
        if refresh or not was_empty:
            return  # a legacy workspace: `careeros migrate` adopts it, init must not pretend it is current
        save_meta(target, WorkspaceMeta(versions.SCHEMA_VERSION, installed, now, now, (runtime,)))
        ledger.append_event(
            target, type="workspace.created", actor="system", source="cli",
            action=f"workspace created for runtime {runtime}",
        )
        return
    runtimes = tuple(dict.fromkeys((*meta.runtimes, runtime)))
    if refresh:
        save_meta(target, WorkspaceMeta(meta.schema_version, installed, meta.created_at, now, runtimes))
        if (target / LEDGER_REL).exists():
            ledger.append_event(
                target, type="workspace.upgraded", actor="system", source="init --refresh",
                action=f"refreshed framework files to CareerOS {installed}",
            )
    elif runtime not in meta.runtimes:
        save_meta(target, WorkspaceMeta(meta.schema_version, meta.framework_version, meta.created_at, now, runtimes))


def init_cmd(
    path: str = typer.Argument(..., help="Directory to scaffold (e.g. ~/my-job-search)"),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Refresh skill files and the entry file (CLAUDE.md / AGENTS.md) from the latest CareerOS version; user data is never touched",
    ),
    runtime: str = typer.Option(
        "claude",
        "--runtime",
        help="Agent runtime to scaffold for: 'claude' (Claude Code / Claude.ai) or 'gpt' (ChatGPT Projects)",
    ),
) -> None:
    """Scaffold a new CareerOS Cowork workspace."""
    target = Path(path).expanduser().resolve()
    was_empty = not target.exists() or not any(target.iterdir())

    try:
        ensure_writable(target)  # refuse before touching any file; a no-op without metadata
    except WorkspaceError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    try:
        written = scaffold(target, refresh=refresh, runtime=runtime)
    except (FileExistsError, ValueError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    try:
        _record_init(target, runtime, refresh=refresh, was_empty=was_empty)
    except (WorkspaceError, OSError) as exc:
        rprint(f"[yellow]Workspace files were written, but the metadata could not be updated: {exc}[/yellow]")

    if refresh:
        rprint(f"\n[bold green]Skills refreshed[/bold green] in {target}\n")
        for f in written:
            label = "backup   " if f.endswith(".bak") else "updated  "
            rprint(f"  [dim]{label}{f}[/dim]")
        rprint("\n[dim]Profile, boards, and pipeline were not touched.[/dim]")
        return

    rprint(f"\n[bold green]Workspace created[/bold green] at {target}  [dim](runtime: {runtime})[/dim]\n")
    for f in written:
        rprint(f"  [dim]wrote  {f}[/dim]")

    if runtime == "gpt":
        rprint(f"""
[bold]Next steps (GPT Work):[/bold]

  1. Go to [link]https://chatgpt.com[/link] and create a new Project
  2. Open [cyan]{target}/AGENTS.md[/cyan] and paste its contents into [bold]Project Instructions[/bold]
  3. Upload your workspace files to the project (start with profile.md once onboarding writes it)
  4. Start a chat — GPT will run the onboarding interview and output your files as code blocks
  5. Save each file output to [cyan]{target}/[/cyan]

[dim]To update skills after a CareerOS upgrade:[/dim]
  careeros init {path} --runtime gpt --refresh
""")
    else:
        rprint(f"""
[bold]Next steps (Claude Code):[/bold]

  1. Open [cyan]{target}[/cyan] as a project in Claude Code or Claude.ai
  2. Claude detects the fresh workspace and runs the onboarding interview
  3. Answer 8 quick questions → your profile and boards get written
  4. Say [bold]"browse linkedin"[/bold] to start finding jobs

[dim]To update skills after a CareerOS upgrade:[/dim]
  careeros init {path} --refresh
""")
