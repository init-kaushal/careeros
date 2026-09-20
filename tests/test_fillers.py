from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from careeros.core.models import Job, Profile


def _make_job(url="https://boards.greenhouse.io/acme/jobs/1"):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-1", source="browse", url=url, company="Acme", title="Senior SRE",
        stage="saved", created_at=now, updated_at=now,
    )


def _make_profile():
    return Profile(name="Jane Doe", email="jane@example.com")


def _make_page(submit_locator):
    """A page whose submit-button locator is the given mock; every other
    locator returns a generic mock with .first/.all() prefilled so
    unrelated fill steps (name, email, resume, cover letter) no-op cleanly."""
    page = MagicMock()

    def locator_side_effect(selector):
        m = MagicMock()
        if "submit" in selector:
            m.first = submit_locator
        else:
            m.first = MagicMock()
            m.first.is_visible.return_value = True
            m.first.count.return_value = 1
            m.all.return_value = [MagicMock(), MagicMock()]
            m.count.return_value = 1
            m.is_visible.return_value = True
        return m

    page.locator.side_effect = locator_side_effect
    return page


class TestGreenhouseFillerSubmitVerification:
    def test_returns_true_when_submit_button_disappears_after_click(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        submit_locator = MagicMock()
        submit_locator.is_visible.return_value = False
        page = _make_page(submit_locator)
        result = GreenhouseFiller().fill(page, _make_job(), _make_profile(), "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf")
        assert result is True

    def test_returns_false_when_submit_button_still_visible_after_click(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        submit_locator = MagicMock()
        submit_locator.is_visible.return_value = True
        page = _make_page(submit_locator)
        result = GreenhouseFiller().fill(page, _make_job(), _make_profile(), "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf")
        assert result is False

    def test_returns_true_when_post_click_verification_raises(self):
        # The click already fired successfully; an exception during the
        # verification wait must not be mistaken for a failed submission.
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        submit_locator = MagicMock()
        page = _make_page(submit_locator)
        page.wait_for_timeout.side_effect = Exception("boom")
        result = GreenhouseFiller().fill(page, _make_job(), _make_profile(), "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf")
        assert result is True

    def test_returns_false_when_submit_click_itself_raises(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        submit_locator = MagicMock()
        submit_locator.click.side_effect = Exception("click failed")
        page = _make_page(submit_locator)
        result = GreenhouseFiller().fill(page, _make_job(), _make_profile(), "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf")
        assert result is False


class TestLeverFillerSubmitVerification:
    def test_returns_true_when_submit_button_disappears_after_click(self):
        from careeros.browser.fillers.lever import LeverFiller
        submit_locator = MagicMock()
        submit_locator.is_visible.return_value = False
        page = _make_page(submit_locator)
        result = LeverFiller().fill(
            page, _make_job(url="https://jobs.lever.co/acme/1"), _make_profile(),
            "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf",
        )
        assert result is True

    def test_returns_false_when_submit_button_still_visible_after_click(self):
        from careeros.browser.fillers.lever import LeverFiller
        submit_locator = MagicMock()
        submit_locator.is_visible.return_value = True
        page = _make_page(submit_locator)
        result = LeverFiller().fill(
            page, _make_job(url="https://jobs.lever.co/acme/1"), _make_profile(),
            "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf",
        )
        assert result is False

    def test_returns_true_when_post_click_verification_raises(self):
        from careeros.browser.fillers.lever import LeverFiller
        submit_locator = MagicMock()
        page = _make_page(submit_locator)
        page.wait_for_timeout.side_effect = Exception("boom")
        result = LeverFiller().fill(
            page, _make_job(url="https://jobs.lever.co/acme/1"), _make_profile(),
            "cover letter", "/tmp/cl.txt", "/tmp/resume.pdf",
        )
        assert result is True


class TestCanHandle:
    def test_greenhouse_handles_boards_url(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        assert GreenhouseFiller().can_handle("https://boards.greenhouse.io/acme/jobs/123456") is True

    def test_greenhouse_handles_greenhouse_jobs_url(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        assert GreenhouseFiller().can_handle("https://acme.greenhouse.io/jobs/apply") is True

    def test_greenhouse_rejects_lever_url(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        assert GreenhouseFiller().can_handle("https://jobs.lever.co/acme/abc") is False

    def test_lever_handles_lever_url(self):
        from careeros.browser.fillers.lever import LeverFiller
        assert LeverFiller().can_handle("https://jobs.lever.co/stripe/abc123") is True

    def test_lever_rejects_greenhouse_url(self):
        from careeros.browser.fillers.lever import LeverFiller
        assert LeverFiller().can_handle("https://boards.greenhouse.io/acme/jobs/1") is False

    def test_linkedin_handles_linkedin_jobs_url(self):
        from careeros.browser.fillers.linkedin import LinkedInFiller
        assert LinkedInFiller().can_handle("https://www.linkedin.com/jobs/view/1234567") is True

    def test_linkedin_rejects_greenhouse_url(self):
        from careeros.browser.fillers.linkedin import LinkedInFiller
        assert LinkedInFiller().can_handle("https://boards.greenhouse.io/acme/jobs/1") is False

    def test_generic_handles_any_url(self):
        from careeros.browser.fillers.generic import GenericFiller
        assert GenericFiller().can_handle("https://anything.com/careers/senior-engineer") is True

    def test_generic_handles_empty_string(self):
        from careeros.browser.fillers.generic import GenericFiller
        assert GenericFiller().can_handle("") is True

    def test_fillers_list_ends_with_generic(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        from careeros.browser.fillers.lever import LeverFiller
        from careeros.browser.fillers.linkedin import LinkedInFiller
        from careeros.browser.fillers.generic import GenericFiller
        fillers = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]
        assert isinstance(fillers[-1], GenericFiller)
