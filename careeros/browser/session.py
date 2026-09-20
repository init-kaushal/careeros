from __future__ import annotations

from careeros.browser.boards import BOARDS
from careeros.browser.driver import launch_browser


def check_board_sessions(names: list[str]) -> dict[str, bool]:
    """Report which of `names` have an authorized session in the isolated profile.

    Cookie presence, not a live probe: browsers prune expired cookies, so
    presence is a reasonable proxy for "not stale", and it keeps session
    checking off the markup treadmill. A server-side-revoked session still
    shows a cookie; that degrades to "board returns zero listings", which
    callers already handle.

    The browser context is always closed before returning. Callers open the
    profile again immediately afterwards and launch_persistent_context holds an
    exclusive lock, so a context left open here would deadlock the very run
    this function is clearing.
    """
    result: dict[str, bool] = {name: False for name in names}
    known = [name for name in names if name in BOARDS]
    if not known:
        return result

    with launch_browser(headless=True) as (context, _page):
        for name in known:
            board = BOARDS[name]
            try:
                cookies = context.cookies(board.cookie_domain)
            except (AttributeError, TypeError):
                # A malformed Board or a changed Playwright signature is a bug,
                # not a signed-out user. Never let it read as "unauthorized".
                raise
            except Exception:
                result[name] = False
                continue
            result[name] = any(
                cookie.get("name") == board.session_cookie for cookie in cookies
            )
    return result
