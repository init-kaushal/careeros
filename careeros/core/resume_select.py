from __future__ import annotations

from dataclasses import dataclass

from careeros.core.models import ResumeVariant
from careeros.storage.interface import StorageProvider

RESUME_EXTENSIONS = (".pdf", ".docx")
_VERSIONS_PREFIX = "resumes/versions/"


@dataclass(frozen=True)
class ResumeChoice:
    path: str          # absolute filesystem path, ready for the form uploader
    storage_path: str  # workspace-relative, for display and audit
    tailored: bool
    variant: ResumeVariant | None = None


def select_resume(storage: StorageProvider, job_id: str) -> ResumeChoice | None:
    """Pick the resume to upload for one job.

    A variant tailored to this job wins. Otherwise the alphabetically-last
    top-level file in resumes/versions/ is used, untailored. Returns None
    when there is no resume at all.
    """
    pdf_path = ResumeVariant.pdf_path(job_id)
    if storage.exists(pdf_path):
        variant = None
        json_path = ResumeVariant.json_path(job_id)
        if storage.exists(json_path):
            try:
                variant = ResumeVariant.model_validate_json(storage.read(json_path).decode())
            except Exception:
                # The sidecar is descriptive, not load-bearing. A missing or
                # corrupt one costs the provenance display, not the upload.
                variant = None
        return ResumeChoice(
            path=storage.resolve(pdf_path),
            storage_path=pdf_path,
            tailored=True,
            variant=variant,
        )

    # Top-level files only. storage.list is rglob-based, so without this
    # filter a per-job variant directory joins the alphabetical pool and one
    # job's tailored resume silently becomes every other job's resume.
    candidates = sorted(
        p for p in storage.list(_VERSIONS_PREFIX)
        if p.endswith(RESUME_EXTENSIONS) and "/" not in p[len(_VERSIONS_PREFIX):]
    )
    if not candidates:
        return None
    chosen = candidates[-1]
    return ResumeChoice(path=storage.resolve(chosen), storage_path=chosen, tailored=False)
