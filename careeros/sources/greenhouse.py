from __future__ import annotations

from careeros.sources.ats import fetch_greenhouse
from careeros.sources.base import Posting, postings_from_ats


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
        return postings_from_ats(self.name, self._company, fetch_greenhouse(board))
