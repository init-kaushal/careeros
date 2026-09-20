from __future__ import annotations

import typer
from rich import print as rprint

from careeros.browser.session import check_board_sessions


def require_board_session(board: str) -> None:
    """Exit non-zero with an actionable message if `board` has no authorized session.

    Called before launching a browser, never after: opening a browser window
    onto a login wall is worse than a one-line instruction.
    """
    if check_board_sessions([board]).get(board):
        return
    rprint(
        "[red]Not signed in to " + board
        + ". Run: careeros browser login --board " + board + "[/red]"
    )
    raise typer.Exit(1)
