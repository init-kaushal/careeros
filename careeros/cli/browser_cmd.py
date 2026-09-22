from __future__ import annotations

import time

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from careeros.browser.boards import BOARDS
from careeros.browser.driver import BrowserProfileBusy, get_careeros_profile_path, launch_browser
from careeros.browser.session import check_board_sessions
from careeros.runtime.factory import WorkspaceNotConfigured, open_local_runtime, resolve_storage
from careeros.runtime.local import LocalRuntime

browser_app = typer.Typer(name="browser", help="Manage the isolated CareerOS browser profile.")
console = Console()

_LOGIN_TIMEOUT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 2


def _open_runtime(workspace_path: str | None) -> LocalRuntime:
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


@browser_app.command()
def login(
    board: str = typer.Option(..., "--board", help="Board to sign in to"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    """Sign in to a job board in the isolated CareerOS browser profile."""
    board_obj = BOARDS.get(board)
    if board_obj is None:
        rprint("[red]Unknown board '" + board + "'. Valid: " + ", ".join(BOARDS) + "[/red]")
        raise typer.Exit(1)

    # A workspace is required here because this command writes an audit record.
    runtime = _open_runtime(workspace)

    rprint("Opening " + board_obj.login_url)
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
        rprint(
            "[red]Timed out — the " + board_obj.session_cookie
            + " session cookie never appeared. Not signed in.[/red]"
        )
        raise typer.Exit(1)

    runtime.record_activity(runtime.new_event(
        "board_session_authorized", "browser-login",
        "Authorized browser session for " + board,
        entity_type="board", entity_id=board,
    ))
    rprint("[green]Signed in to " + board + ".[/green]")


@browser_app.command()
def status() -> None:
    """Show which boards have an authorized session. Needs no workspace."""
    names = list(BOARDS)
    try:
        sessions = check_board_sessions(names)
    except BrowserProfileBusy as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)

    table = Table(show_header=True)
    table.add_column("Board")
    table.add_column("Authorized")
    for name in names:
        table.add_row(name, "[green]yes[/green]" if sessions.get(name) else "[red]no[/red]")
    console.print(table)
    rprint("Profile: " + get_careeros_profile_path())
