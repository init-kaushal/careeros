from __future__ import annotations

import os
import platform
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


def get_chrome_profile_path() -> str:
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if system == "Linux":
        return os.path.expanduser("~/.config/google-chrome")
    return os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")


@contextmanager
def launch_browser(headless: bool = False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=get_chrome_profile_path(),
            headless=headless,
            channel="chrome",
        )
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
