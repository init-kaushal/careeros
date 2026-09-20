from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from careeros.core.job_id import make_job_id
from careeros.core.models import Job


@dataclass(frozen=True)
class Posting:
    """One job posting as returned by any source, browser or API.

    A typed record rather than a dict because the two producers disagree:
    scrapers emit source_board/title/company/location/url, while the ATS
    transport emits source_id/title/url/location/description with no company.
    """

    source: str
    title: str
    company: str
    url: str
    location: str | None = None
    description: str | None = None
    source_id: str | None = None


class JobSource(Protocol):
    name: str

    def fetch(self, board: str) -> list[Posting]:
        """Fetch postings for one company board. Raises ATSFetchError on failure."""
        ...


def job_from_posting(posting: Posting, now: str) -> Job:
    """Build a new, unsaved Job from a Posting.

    The id assigned here is random-suffixed and therefore NOT an identity —
    two calls for the same posting produce different ids. Identity is decided
    by careeros.core.dedup against stored records, which is why this Job must
    go through JobStore.save_new rather than Job.save.
    """
    return Job(
        id=make_job_id(posting.company, posting.title),
        source=posting.source,
        source_id=posting.source_id,
        url=posting.url,
        company=posting.company,
        title=posting.title,
        location=posting.location,
        description=posting.description,
        stage="saved",
        created_at=now,
        updated_at=now,
    )


def posting_from_scrape(raw: dict) -> Posting:
    """Adapt a browser scraper's dict into a Posting.

    The only surviving use of the old scraper dict shape — one call site to
    delete when those dicts are retired.
    """
    company = (raw.get("company") or "").strip()
    if not company:
        raise ValueError("scraped posting has no company; cannot fingerprint it")
    return Posting(
        source=raw["source_board"],
        title=raw["title"],
        company=company,
        url=raw["url"],
        location=raw.get("location"),
        description=raw.get("description"),
    )
