from __future__ import annotations

import os
import platform
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


class BrowserProfileBusy(RuntimeError):
    """The isolated CareerOS browser profile is locked by another CareerOS process."""


# Substrings Chrome/Chromium use when a profile directory is already locked.
_LOCK_MARKERS = (
    "processsingleton",
    "singletonlock",
    "profile appears to be in use",
)


def _is_profile_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _LOCK_MARKERS)


def get_careeros_profile_path() -> str:
    """Path to the dedicated CareerOS browser profile.

    Deliberately NOT the user's live Chrome profile: an unattended run must not
    hold the user's banking/email session cookies. Machine-global rather than
    per-workspace — a person has one LinkedIn account, not one per workspace.

    Session data is state, not config, hence XDG_STATE_HOME on Linux.
    """
    system = platform.system()
    if system == "Darwin":
        # os.path functions here resolve system paths, not workspace I/O — exempt from StorageProvider constraint
        return os.path.expanduser("~/Library/Application Support/careeros/browser")
    if system == "Linux":
        # os.path functions here resolve system paths, not workspace I/O — exempt from StorageProvider constraint
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        return os.path.join(base, "careeros", "browser")
    # os.path functions here resolve system paths, not workspace I/O — exempt from StorageProvider constraint
    return os.path.expandvars(r"%LOCALAPPDATA%\careeros\browser")


@contextmanager
def launch_browser(headless: bool = False) -> Generator[tuple[BrowserContext, Page], None, None]:
    from playwright.sync_api import sync_playwright

    profile_dir = Path(get_careeros_profile_path())
    profile_dir.mkdir(parents=True, exist_ok=True)
    # 0700: this directory holds live session cookies. Workspace JSON is 0600;
    # a directory of sessions should not be more permissive than that.
    profile_dir.chmod(0o700)

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            if _is_profile_lock_error(exc):
                raise BrowserProfileBusy(
                    "The CareerOS browser profile is already in use by another CareerOS "
                    "process. Wait for it to finish, or stop it, then retry."
                ) from exc
            raise
        # Remove the webdriver flag that sites use to detect automation.
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        try:
            yield context, page
        finally:
            context.close()


def fetch_jd_text(page: Page, url: str) -> str:
    from careeros.sources.ats import _strip_html
    try:
        page.goto(url, timeout=15000)
        html = page.content()
        return _strip_html(html)[:4000]
    except Exception:
        return ""
