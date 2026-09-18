from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class LeverFiller:
    platform = "Lever"

    def can_handle(self, url: str) -> bool:
        return "jobs.lever.co" in url

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        try:
            page.goto(job.url, timeout=20000)
        except Exception:
            return False

        # Click Apply button if visible
        try:
            apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")').first
            if apply_btn.is_visible():
                apply_btn.click()
                page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        # Fill name field (Lever uses either a single "name" or separate first/last)
        try:
            name_inputs = page.locator('input[name="name"]')
            if name_inputs.count() > 0:
                name_inputs.fill(profile.name or "")
            else:
                parts = (profile.name or "").split(" ", 1)
                page.locator('input[placeholder*="First" i]').first.fill(parts[0])
                if len(parts) > 1:
                    page.locator('input[placeholder*="Last" i]').first.fill(parts[1])
            page.locator('input[name="email"]').fill(profile.email or "")
        except Exception:
            return False

        # Resume upload
        try:
            page.locator('input[type="file"]').first.set_input_files(resume_path)
        except Exception:
            return False

        # Cover letter textarea (optional)
        try:
            cl_area = page.locator('textarea[name="comments"]')
            if cl_area.count() > 0 and cover_letter_text:
                cl_area.fill(cover_letter_text)
        except Exception:
            pass

        # Submit
        try:
            page.locator('input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(5000)
        except Exception:
            return False

        return True
