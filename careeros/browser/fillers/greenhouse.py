from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class GreenhouseFiller:
    platform = "Greenhouse"

    def can_handle(self, url: str) -> bool:
        return "boards.greenhouse.io" in url or "greenhouse.io" in url

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        parts = (profile.name or "").split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        try:
            page.goto(job.url, timeout=20000)
        except Exception:
            return False

        # Fill name and email
        try:
            page.locator('input[name="job_application[first_name]"]').fill(first_name)
            page.locator('input[name="job_application[last_name]"]').fill(last_name)
            page.locator('input[name="job_application[email]"]').fill(profile.email or "")
        except Exception:
            return False

        # Resume upload (first file input)
        try:
            page.locator('input[type="file"]').first.set_input_files(resume_path)
        except Exception:
            return False

        # Cover letter upload (second file input, if present)
        try:
            file_inputs = page.locator('input[type="file"]').all()
            if len(file_inputs) > 1:
                file_inputs[1].set_input_files(cover_letter_path)
        except Exception:
            pass

        # Submit
        try:
            page.locator('input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(5000)
        except Exception:
            return False

        return True
