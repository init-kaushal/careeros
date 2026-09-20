from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from careeros.browser.scrapers.indeed import IndeedScraper
from careeros.browser.scrapers.linkedin import LinkedInScraper
from careeros.browser.scrapers.wellfound import WellfoundScraper

if TYPE_CHECKING:
    from careeros.browser.scrapers.base import Scraper


@dataclass(frozen=True)
class Board:
    """A browser-driven job board.

    Single source of truth for everything CareerOS needs to know about a
    board: how to scrape it, where to send the user to sign in, and which
    cookie proves that sign-in happened.
    """

    name: str
    scraper: "Scraper"
    login_url: str
    session_cookie: str
    cookie_domain: str


# session_cookie values: LinkedIn's `li_at` is long-standing and well known.
# Indeed's and Wellfound's are recorded here as the best available values and
# are verified empirically in Task 4, Step 8 — a guessed cookie name produces a
# pre-flight that is confidently wrong, which is the failure mode this design
# rejected receipt-based checking for. Corrections land here and nowhere else.
BOARDS: dict[str, Board] = {
    "linkedin": Board(
        name="linkedin",
        scraper=LinkedInScraper(),
        login_url="https://www.linkedin.com/login",
        session_cookie="li_at",
        cookie_domain="https://www.linkedin.com",
    ),
    "indeed": Board(
        name="indeed",
        scraper=IndeedScraper(),
        login_url="https://secure.indeed.com/auth",
        session_cookie="CTK",
        cookie_domain="https://www.indeed.com",
    ),
    "wellfound": Board(
        name="wellfound",
        scraper=WellfoundScraper(),
        login_url="https://wellfound.com/login",
        session_cookie="_wellfound_session",
        cookie_domain="https://wellfound.com",
    ),
}

BOARD_NAMES: tuple[str, ...] = tuple(BOARDS)
