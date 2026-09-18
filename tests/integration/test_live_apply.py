"""
Integration tests for live browser apply flow.

These tests require:
- Chrome browser with an active session
- Real Greenhouse/Lever job URLs (set via env vars CAREEROS_TEST_GREENHOUSE_URL, CAREEROS_TEST_LEVER_URL)
- Valid ATS credentials/login session in the browser

Run with: pytest -m integration
"""
import pytest
from datetime import datetime, timezone


@pytest.mark.integration
def test_greenhouse_filler_can_handle_live_url():
    """Smoke test: can_handle does not require a browser."""
    from careeros.browser.fillers.greenhouse import GreenhouseFiller
    filler = GreenhouseFiller()
    assert filler.can_handle("https://boards.greenhouse.io/example/jobs/1234567") is True


@pytest.mark.integration
def test_lever_filler_can_handle_live_url():
    """Smoke test: can_handle does not require a browser."""
    from careeros.browser.fillers.lever import LeverFiller
    filler = LeverFiller()
    assert filler.can_handle("https://jobs.lever.co/example/abc-123") is True


@pytest.mark.integration
def test_greenhouse_fill_live():
    """
    Live browser test — requires Chrome with an active session and a real Greenhouse job URL.
    Set CAREEROS_TEST_GREENHOUSE_URL env var to the apply URL before running.
    Run with: pytest -m integration
    """
    import os
    from careeros.browser.driver import launch_browser
    from careeros.browser.fillers.greenhouse import GreenhouseFiller
    from careeros.core.models import Job, Profile

    url = os.environ.get("CAREEROS_TEST_GREENHOUSE_URL")
    if not url:
        pytest.skip("CAREEROS_TEST_GREENHOUSE_URL not set")

    now = datetime.now(timezone.utc).isoformat()
    job = Job(id="test-job", source="test", url=url, company="Test Co", title="Test Role",
              stage="saved", created_at=now, updated_at=now)
    profile = Profile(name="Test User", email="test@example.com", title="SRE")
    filler = GreenhouseFiller()

    with launch_browser(headless=False) as (_, page):
        # Does not submit — returns False or True based on form detection
        result = filler.fill(page, job, profile, "Test cover letter.", "/tmp/test_resume.pdf", "/tmp/test_cl.txt")
    # Just verify no uncaught exception
    assert isinstance(result, bool)


@pytest.mark.integration
def test_lever_fill_live():
    """
    Live browser test — requires Chrome with an active session and a real Lever job URL.
    Set CAREEROS_TEST_LEVER_URL env var to the apply URL before running.
    Run with: pytest -m integration
    """
    import os
    from careeros.browser.driver import launch_browser
    from careeros.browser.fillers.lever import LeverFiller
    from careeros.core.models import Job, Profile

    url = os.environ.get("CAREEROS_TEST_LEVER_URL")
    if not url:
        pytest.skip("CAREEROS_TEST_LEVER_URL not set")

    now = datetime.now(timezone.utc).isoformat()
    job = Job(id="test-job", source="test", url=url, company="Test Co", title="Test Role",
              stage="saved", created_at=now, updated_at=now)
    profile = Profile(name="Test User", email="test@example.com", title="SRE")
    filler = LeverFiller()

    with launch_browser(headless=False) as (_, page):
        result = filler.fill(page, job, profile, "Test cover letter.", "/tmp/test_resume.pdf", "/tmp/test_cl.txt")
    assert isinstance(result, bool)
