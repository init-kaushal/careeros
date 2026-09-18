from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class LinkedInFiller:
    platform = "LinkedIn Easy Apply"

    def can_handle(self, url: str) -> bool:
        return "linkedin.com/jobs/" in url

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

        # Click Easy Apply button
        try:
            easy_apply = page.locator('button:has-text("Easy Apply")').first
            if not easy_apply.is_visible():
                return False
            easy_apply.click()
            page.wait_for_timeout(1500)
        except Exception:
            return False

        # Step through the modal (up to 10 pages)
        for _ in range(10):
            # Fill contact info fields if present
            try:
                email_input = page.locator('input[id*="email" i]').first
                if email_input.is_visible() and not email_input.input_value():
                    email_input.fill(profile.email or "")
            except Exception:
                pass

            try:
                phone_input = page.locator('input[id*="phone" i]').first
                if phone_input.is_visible() and not phone_input.input_value():
                    phone_input.fill("")
            except Exception:
                pass

            # Resume upload step
            try:
                file_input = page.locator('input[type="file"]').first
                if file_input.count() > 0:
                    file_input.set_input_files(resume_path)
            except Exception:
                pass

            # Cover letter textarea step
            try:
                cl_area = page.locator('textarea[aria-label*="cover letter" i], textarea[id*="cover-letter"]').first
                if cl_area.count() > 0 and cl_area.is_visible() and cover_letter_text:
                    cl_area.fill(cover_letter_text)
            except Exception:
                pass

            # Submit if on the final review page
            try:
                submit_btn = page.locator('button:has-text("Submit application")').first
                if submit_btn.is_visible():
                    submit_btn.click()
                    page.wait_for_timeout(3000)
                    return True
            except Exception:
                pass

            # Otherwise click Next / Review / Continue
            try:
                next_btn = page.locator(
                    'button:has-text("Next"), button:has-text("Review"), button:has-text("Continue")'
                ).first
                if next_btn.is_visible():
                    next_btn.click()
                    page.wait_for_timeout(1000)
                else:
                    break
            except Exception:
                break

        return False
