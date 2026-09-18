import pytest


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
