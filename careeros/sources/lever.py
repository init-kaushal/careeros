from __future__ import annotations

from careeros.sources.ats import fetch_lever
from careeros.sources.base import Posting


class LeverSource:
    """JobSource over Lever's public postings API. See GreenhouseSource for
    why company_name comes from configuration rather than the API response."""

    name = "lever"

    def __init__(self, company_name: str) -> None:
        self._company = company_name

    def fetch(self, board: str) -> list[Posting]:
        postings = []
        for raw in fetch_lever(board):
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
