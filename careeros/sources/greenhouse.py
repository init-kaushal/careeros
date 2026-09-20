from __future__ import annotations

from careeros.sources.ats import fetch_greenhouse
from careeros.sources.base import Posting


class GreenhouseSource:
    """JobSource over Greenhouse's public board API.

    company_name is supplied by configuration because the API response never
    carries it — it is implied by the board slug. Deriving it would be a guess,
    and company is half the dedup fingerprint.
    """

    name = "greenhouse"

    def __init__(self, company_name: str) -> None:
        self._company = company_name

    def fetch(self, board: str) -> list[Posting]:
        postings = []
        for raw in fetch_greenhouse(board):
            url = raw.get("url")
            if not url:
                continue  # unusable without a URL: it is half the dedup key
            postings.append(Posting(
                source=self.name,
                title=raw["title"],
                company=self._company,
                url=url,
                location=raw.get("location"),
                description=raw.get("description"),
                source_id=raw.get("source_id"),
            ))
        return postings
