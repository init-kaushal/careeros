from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from careeros.browser.scrapers.generic import GenericScraper

if TYPE_CHECKING:
    from playwright.sync_api import Page


class CustomBoardScraper:
    """Generic scraper for user-configured boards with a search URL template."""

    def __init__(self, name: str, search_url: str) -> None:
        self.source_board = name
        self.search_url = search_url
        self._generic = GenericScraper()

    def search(self, page: "Page", query: str, limit: int) -> list[dict]:
        url = self.search_url.replace("{query}", urllib.parse.quote(query))
        try:
            page.goto(url, timeout=30000)
            # Allow JS to render before reading the DOM.
            page.wait_for_timeout(2000)
            html = page.content()
        except Exception:
            return []
        results = self._generic.parse_listings(html)[:limit]
        host = urllib.parse.urlsplit(page.url).netloc
        if host.startswith("www."):
            host = host[4:]
        for r in results:
            r["url"] = urllib.parse.urljoin(page.url, r["url"])
            r["source_board"] = self.source_board
            if not r.get("company"):
                r["company"] = host
        return results
