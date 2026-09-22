from __future__ import annotations

import hashlib
from dataclasses import dataclass

from careeros.core.models import ResumeVariant
from careeros.storage.interface import StorageProvider

RESUME_EXTENSIONS = (".pdf", ".docx")
_VERSIONS_PREFIX = "resumes/versions/"
_MASTER = "resumes/master.md"


@dataclass(frozen=True)
class ResumeChoice:
    path: str          # absolute filesystem path, ready for the form uploader
    storage_path: str  # workspace-relative, for display and audit
    tailored: bool
    variant: ResumeVariant | None = None
    # The variant's citations point into a master resume that has since been
    # replaced (typically by `resume ingest`), so its line numbers no longer
    # describe the file they name. The PDF is still the tailored document.
    stale_master: bool = False


def _digest(storage: StorageProvider, path: str) -> str | None:
    """sha256 of a stored file, or None if it cannot be read."""
    try:
        return hashlib.sha256(storage.read(path)).hexdigest()
    except Exception:
        return None


def select_resume(storage: StorageProvider, job_id: str) -> ResumeChoice | None:
    """Pick the resume to upload for one job.

    A variant tailored to this job wins. Otherwise the alphabetically-last
    top-level file in resumes/versions/ is used, untailored. Returns None
    when there is no resume at all.

    A sidecar is never trusted on sight: its recorded digests are checked
    against the PDF it describes and the master it cites. A mismatch costs
    the provenance record, never the upload — the PDF is the tailored file
    either way.
    """
    pdf_path = ResumeVariant.pdf_path(job_id)
    if storage.exists(pdf_path):
        variant = None
        stale_master = False
        json_path = ResumeVariant.json_path(job_id)
        if storage.exists(json_path):
            try:
                variant = ResumeVariant.model_validate_json(storage.read(json_path).decode())
            except Exception:
                # The sidecar is descriptive, not load-bearing. A missing or
                # corrupt one costs the provenance display, not the upload.
                variant = None
        if variant is not None and variant.pdf_sha256:
            # Written by two separate atomic_writes, so a concurrent
            # regeneration can commit its PDF between them and leave this
            # sidecar describing a document nobody holds. An empty hash is
            # a variant written before this check existed: unknown, not bad.
            if _digest(storage, pdf_path) != variant.pdf_sha256:
                variant = None
        if variant is not None and variant.master_sha256:
            # Unreadable or missing master counts as changed: the citations
            # name a file whose current content cannot be confirmed.
            if _digest(storage, _MASTER) != variant.master_sha256:
                stale_master = True
        return ResumeChoice(
            path=storage.resolve(pdf_path),
            storage_path=pdf_path,
            tailored=True,
            variant=variant,
            stale_master=stale_master,
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
