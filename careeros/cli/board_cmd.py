from __future__ import annotations

import json
import re
import time

import typer
from rich import print as rprint
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from careeros.boards_config import (
    ALL_KNOWN_BOARDS,
    API_BOARD_NAMES,
    BROWSER_BOARD_NAMES,
    load_registered_boards,
    register_board,
)
from careeros.config_sources import load_board_entries
from careeros.runtime.factory import WorkspaceNotConfigured, open_local_runtime, resolve_storage
from careeros.runtime.local import LocalRuntime

board_app = typer.Typer(name="board", help="Manage job boards.")
console = Console()

_LOGIN_TIMEOUT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 2


def _open_runtime(workspace_path: str | None) -> LocalRuntime:
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _is_browser_board_ready(name: str) -> bool:
    from careeros.browser.session import check_board_sessions
    from careeros.browser.driver import BrowserProfileBusy
    try:
        return bool(check_board_sessions([name]).get(name))
    except BrowserProfileBusy:
        return False


def _is_api_board_ready(name: str, runtime: LocalRuntime) -> bool:
    entries = load_board_entries(runtime.storage)
    return any(e.source == name for e in entries)


@board_app.command("list")
def list_cmd(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    """Show registered boards and their setup status."""
    runtime = _open_runtime(workspace)
    registered = set(load_registered_boards(runtime.storage))

    if not registered:
        rprint("No boards registered. Add them with: [bold]careeros board setup <name>[/bold]")
        rprint(f"[dim]Known boards: {', '.join(sorted(ALL_KNOWN_BOARDS))}[/dim]")
        return

    table = Table(show_header=True)
    table.add_column("Board")
    table.add_column("Type")
    table.add_column("Set up")

    for name in sorted(registered):
        board_type = "browser" if name in BROWSER_BOARD_NAMES else "api"
        if board_type == "browser":
            ready = _is_browser_board_ready(name)
        else:
            ready = _is_api_board_ready(name, runtime)
        status = "[green]yes[/green]" if ready else "[yellow]no — run: careeros board setup " + name + "[/yellow]"
        table.add_row(name, board_type, status)

    console.print(table)


@board_app.command("setup")
def setup_cmd(
    name: str = typer.Argument(..., help="Board name, e.g. linkedin, greenhouse"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    """Configure a job board. Browser boards trigger a sign-in; API boards ask for the board URL."""
    name = name.lower().strip()
    if name not in ALL_KNOWN_BOARDS:
        rprint(f"[red]Unknown board '{name}'.[/red]")
        rprint(f"[dim]Known boards: {', '.join(sorted(ALL_KNOWN_BOARDS))}[/dim]")
        raise typer.Exit(1)

    runtime = _open_runtime(workspace)
    register_board(runtime.storage, name)

    if name in BROWSER_BOARD_NAMES:
        _setup_browser_board(name, runtime)
    else:
        _setup_api_board(name, runtime)


def _setup_browser_board(name: str, runtime: LocalRuntime) -> None:
    from careeros.browser.boards import BOARDS
    from careeros.browser.driver import BrowserProfileBusy, launch_browser

    board_obj = BOARDS.get(name)
    if board_obj is None:
        rprint(f"[red]No browser scraper configured for '{name}'.[/red]")
        raise typer.Exit(1)

    rprint(f"Opening {board_obj.login_url}")
    rprint("[yellow]Sign in in the browser window. CareerOS detects it and closes automatically.[/yellow]")

    authorized = False
    try:
        with launch_browser(headless=False) as (context, page):
            page.goto(board_obj.login_url, timeout=30000)
            deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                cookies = context.cookies(board_obj.cookie_domain)
                if any(c.get("name") == board_obj.session_cookie for c in cookies):
                    authorized = True
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
    except BrowserProfileBusy as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        rprint("[red]Login failed: " + str(exc) + "[/red]")
        raise typer.Exit(1)

    if not authorized:
        rprint(f"[red]Timed out — session cookie never appeared. Not signed in.[/red]")
        raise typer.Exit(1)

    runtime.record_activity(runtime.new_event(
        "board_session_authorized", "board-setup",
        f"Authorized browser session for {name}",
        entity_type="board", entity_id=name,
    ))
    rprint(f"[green]Signed in to {name}. Board is ready.[/green]")


def _setup_api_board(name: str, runtime: LocalRuntime) -> None:
    examples = {
        "greenhouse": "https://boards.greenhouse.io/stripe",
        "lever": "https://jobs.lever.co/acme",
    }
    example = examples.get(name, "")
    rprint(f"\n[bold]{name.capitalize()} Board URL[/bold]")
    if example:
        rprint(f"[dim]Example: {example}[/dim]")

    raw = Prompt.ask("Board URL")
    slug = _extract_api_slug(name, raw.strip())
    if not slug:
        rprint(f"[yellow]Could not extract a company slug from '{raw}'.[/yellow]")
        rprint(f"[dim]Expected format: {example}[/dim]")
        raise typer.Exit(1)

    _append_to_sources(runtime, name, slug)
    runtime.record_activity(runtime.new_event(
        "board_api_configured", "board-setup",
        f"Configured API board {name}/{slug}",
        entity_type="board", entity_id=name,
    ))
    rprint(f"[green]{name.capitalize()} board '{slug}' added. Run 'careeros board list' to verify.[/green]")


def _extract_api_slug(source: str, raw: str) -> str | None:
    patterns = {
        "greenhouse": r'boards\.greenhouse\.io/([^/?#\s]+)',
        "lever": r'jobs\.lever\.co/([^/?#\s]+)',
    }
    pat = patterns.get(source)
    if not pat:
        return None
    m = re.search(pat, raw)
    if m:
        return m.group(1).strip("/")
    # Accept bare slug (no URL)
    if raw and "/" not in raw and "." not in raw:
        return raw
    return None


def _append_to_sources(runtime: LocalRuntime, source: str, slug: str) -> None:
    path = "config/sources.json"
    try:
        raw = json.loads(runtime.storage.read(path).decode())
        sources = raw.get("sources", [])
    except Exception:
        sources = []
    sources.append({"source": source, "board": slug, "company": slug, "mode": "SEARCH_ONLY"})
    runtime.storage.atomic_write(path, json.dumps({"sources": sources}, indent=2).encode())
