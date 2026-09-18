from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from playwright.sync_api import Page


class Scraper(Protocol):
    source_board: str

    def search(self, page: Page, query: str, limit: int) -> list[dict]:
        ...

    def parse_listings(self, html: str) -> list[dict]:
        ...
