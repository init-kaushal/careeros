from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class Filler(Protocol):
    platform: str

    def can_handle(self, url: str) -> bool:
        """Pure URL pattern check — no browser needed. Must be deterministic."""
        ...

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        """
        Navigate to job.url, fill the application form, upload documents, submit.
        Returns True on successful submission, False on detectable failure.
        cover_letter_text: raw string for textarea fields.
        cover_letter_path: absolute filesystem path for file-upload fields.
        resume_path: absolute filesystem path for resume upload.
        """
        ...
