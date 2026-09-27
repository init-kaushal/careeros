from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from careeros.browser.scrapers.generic import GenericScraper, _JOB_PATTERNS

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
            page.wait_for_timeout(2000)
            # Dismiss any popup/modal with common "no thanks" / "close" patterns.
            page.evaluate("""() => {
                const dismissPhrases = ['no thanks', 'maybe later', 'close', 'dismiss', 'skip', 'not now'];
                for (const el of document.querySelectorAll('a, button')) {
                    const text = (el.innerText || el.textContent || '').toLowerCase().trim();
                    if (dismissPhrases.some(p => text.includes(p)) && el.offsetParent !== null) {
                        el.click();
                        break;
                    }
                }
            }""")
            page.wait_for_timeout(2000)
            # Query live DOM — captures JS-rendered links that raw HTML parsing misses.
            raw_links: list[dict] = page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map(a => "
                "({href: a.href, text: (a.innerText || a.textContent || '').trim()}))"
            )
        except Exception:
            return []

        host = urllib.parse.urlsplit(page.url).netloc
        if host.startswith("www."):
            host = host[4:]

        # Normalise the current page URL so we can skip self-referential links.
        current_base = urllib.parse.urlsplit(page.url)._replace(fragment="", query="").geturl().rstrip("/")

        results = []
        seen: set[str] = set()
        for link in raw_links:
            href = link.get("href", "")
            if not href or not _JOB_PATTERNS.search(href):
                continue
            # Skip links that are just the current page (with/without query or fragment).
            href_base = urllib.parse.urlsplit(href)._replace(fragment="", query="").geturl().rstrip("/")
            if href_base == current_base:
                continue
            if href in seen:
                continue
            seen.add(href)
            text = link.get("text", "").strip()
            results.append({
                "source_board": self.source_board,
                "title": text or href.rstrip("/").split("/")[-1].replace("-", " ").title(),
                "company": host,
                "location": None,
                "url": href,
            })
            if len(results) >= limit:
                break
        return results
