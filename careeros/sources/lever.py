from __future__ import annotations

from careeros.sources.ats import fetch_lever
from careeros.sources.base import Posting, postings_from_ats


class LeverSource:
    """JobSource over Lever's public postings API. See GreenhouseSource for
    why company_name comes from configuration rather than the API response."""

    name = "lever"

    def __init__(self, company_name: str) -> None:
        self._company = company_name

    def fetch(self, board: str) -> list[Posting]:
        return postings_from_ats(self.name, self._company, fetch_lever(board))
