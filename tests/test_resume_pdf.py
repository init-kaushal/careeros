from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import Error as PlaywrightError

from careeros.render.resume_pdf import RendererUnavailable, render_pdf

_HTML = "<!DOCTYPE html><html><body><h1>Alice Johnson</h1></body></html>"


def _playwright_stub(pdf_bytes=b"%PDF-1.4 fake"):
    page = MagicMock()
    page.pdf.return_value = pdf_bytes
    browser = MagicMock()
    browser.new_page.return_value = page
    chromium = MagicMock()
    chromium.launch.return_value = browser
    p = MagicMock()
    p.chromium = chromium
    ctx = MagicMock()
    ctx.__enter__.return_value = p
    ctx.__exit__.return_value = False
    return ctx, p, browser, page


def test_returns_the_pdf_bytes_playwright_produced():
    ctx, _p, _browser, _page = _playwright_stub(b"%PDF-1.4 real")
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        assert render_pdf(_HTML) == b"%PDF-1.4 real"


def test_uses_an_ephemeral_headless_chromium_not_the_persistent_profile():
    # launch_persistent_context uses the browsing profile Phase 9c isolated.
    # Rendering through it would contend with a live session.
    ctx, p, _browser, _page = _playwright_stub()
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        render_pdf(_HTML)
    p.chromium.launch.assert_called_once_with(headless=True)
    assert not p.chromium.launch_persistent_context.called


def test_sets_content_directly_and_never_loads_a_url():
    ctx, _p, _browser, page = _playwright_stub()
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        render_pdf(_HTML)
    page.set_content.assert_called_once_with(_HTML)
    assert not page.goto.called


def test_pdf_is_letter_sized_and_written_to_no_path():
    ctx, _p, _browser, page = _playwright_stub()
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        render_pdf(_HTML)
    kwargs = page.pdf.call_args.kwargs
    assert kwargs["format"] == "Letter"
    assert "path" not in kwargs


def test_browser_is_closed_even_when_pdf_generation_fails():
    ctx, _p, browser, page = _playwright_stub()
    page.pdf.side_effect = RuntimeError("boom")
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        with pytest.raises(RuntimeError, match="boom"):
            render_pdf(_HTML)
    browser.close.assert_called_once()


def test_missing_chromium_raises_renderer_unavailable_with_the_install_hint():
    # Playwright browsers are not installed by `pip install`, so this is the
    # expected first-run path, not an edge case. It must not surface as a raw
    # Playwright traceback.
    err = PlaywrightError(
        "BrowserType.launch: Executable doesn't exist at /x/chrome-headless-shell"
    )
    with patch("careeros.render.resume_pdf.sync_playwright", side_effect=err):
        with pytest.raises(RendererUnavailable) as exc:
            render_pdf(_HTML)
    assert "playwright install chromium" in str(exc.value)
    assert "install-deps" not in str(exc.value)


def test_missing_system_deps_raises_renderer_unavailable_with_the_deps_hint():
    # The Chromium binary is present here, but its OS shared libraries
    # (libnss3 and friends) are not — the classic fresh-Linux/CI failure.
    # This message contains "install" but must NOT be told to run
    # `playwright install chromium`, which would succeed and change nothing.
    err = PlaywrightError(
        "Missing system dependencies required to run browser chromium. "
        "Install them with: sudo npx playwright install-deps chromium"
    )
    with patch("careeros.render.resume_pdf.sync_playwright", side_effect=err):
        with pytest.raises(RendererUnavailable) as exc:
            render_pdf(_HTML)
    assert "install-deps" in str(exc.value)
    assert "install chromium" not in str(exc.value)


def test_other_playwright_errors_are_not_disguised_as_missing_chromium():
    err = PlaywrightError("Target page, context or browser has been closed")
    with patch("careeros.render.resume_pdf.sync_playwright", side_effect=err):
        with pytest.raises(PlaywrightError):
            render_pdf(_HTML)


def test_non_playwright_errors_from_pdf_generation_propagate_unchanged():
    ctx, _p, _browser, page = _playwright_stub()
    page.pdf.side_effect = RuntimeError("boom")
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        with pytest.raises(RuntimeError, match="boom"):
            render_pdf(_HTML)


@pytest.mark.integration
def test_real_chromium_renders_a_pdf():
    # Requires `playwright install chromium`. Skipped by default like every
    # other browser-dependent test in this suite.
    data = render_pdf(_HTML)
    assert data.startswith(b"%PDF")
    assert len(data) > 500
