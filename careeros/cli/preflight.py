from __future__ import annotations

import typer
from rich import print as rprint

from careeros.browser.driver import BrowserProfileBusy
from careeros.browser.session import check_board_sessions


def require_board_session(board: str) -> None:
    """Exit non-zero with an actionable message if `board` has no authorized session.

    Called before launching a browser, never after: opening a browser window
    onto a login wall is worse than a one-line instruction.

    This is itself the first thing to open the isolated profile, ahead of any
    try/except a caller wraps around its own later launch_browser call — so a
    locked profile must be handled here too, or it escapes as a raw traceback.
    """
    try:
        authorized = check_board_sessions([board]).get(board)
    except BrowserProfileBusy as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    if authorized:
        return
    rprint(
        "[red]Not signed in to " + board
        + ". Run: careeros browser login --board " + board + "[/red]"
    )
    raise typer.Exit(1)
