from __future__ import annotations
from typing import TYPE_CHECKING

from rich import print as rprint

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class GenericFiller:
    platform = "Generic"

    def can_handle(self, url: str) -> bool:
        return True

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

        parts = (profile.name or "").split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        # Best-effort name/email fill by label/placeholder heuristics
        for selector, value in [
            ('input[placeholder*="First name" i], input[name*="first" i]', first_name),
            ('input[placeholder*="Last name" i], input[name*="last" i]', last_name),
            ('input[placeholder*="Full name" i], input[name="name"]', profile.name or ""),
            ('input[placeholder*="Email" i], input[name="email"], input[type="email"]', profile.email or ""),
        ]:
            try:
                el = page.locator(selector).first
                if el.count() > 0 and el.is_visible() and value:
                    el.fill(value)
            except Exception:
                pass

        # Resume upload (first file input)
        try:
            page.locator('input[type="file"]').first.set_input_files(resume_path)
        except Exception:
            pass

        rprint("[yellow]Generic filler applied — review the form before submitting. Fields may be incomplete.[/yellow]")
        # Generic never auto-submits — leaves browser open for user to verify and submit
        return False
