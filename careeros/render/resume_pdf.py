from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

_MARGIN = {"top": "0.6in", "bottom": "0.6in", "left": "0.7in", "right": "0.7in"}

_INSTALL_HINT = (
    "Chromium is not installed for Playwright. Run: playwright install chromium"
)

# Substrings Playwright uses when the browser binary is absent.
_MISSING_MARKERS = ("Executable doesn't exist", "playwright install")


class RendererUnavailable(RuntimeError):
    """Chromium is not available, so no PDF can be produced.

    Playwright browsers are not installed by `pip install`, so this is an
    expected first-run condition rather than an edge case. Callers turn it
    into an actionable message instead of a traceback.
    """


def render_pdf(html_text: str) -> bytes:
    """Render an HTML document to PDF bytes with an ephemeral Chromium.

    Returns bytes rather than writing a file: all workspace I/O goes through
    the StorageProvider protocol, so the caller persists these with
    storage.atomic_write.

    Deliberately does NOT use launch_persistent_context. That profile is the
    one Phase 9c isolated for browsing sessions, and rendering through it
    would contend with a live session and raise BrowserProfileBusy. Nothing
    here loads a URL — set_content only — so rendering never touches the
    network.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html_text)
                return page.pdf(format="Letter", margin=_MARGIN, print_background=True)
            finally:
                browser.close()
    except PlaywrightError as exc:
        if any(marker in str(exc) for marker in _MISSING_MARKERS):
            raise RendererUnavailable(_INSTALL_HINT) from exc
        raise
