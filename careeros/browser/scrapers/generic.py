from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_JOB_PATTERNS = re.compile(r'/(jobs?|careers?|positions?|openings?)/', re.IGNORECASE)
_HREF = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>([^<]*)</a>', re.DOTALL)


class GenericScraper:
    source_board = "generic"

    def parse_listings(self, html: str) -> list[dict]:
        results = []
        for m in _HREF.finditer(html):
            href = m.group(1).strip()
            text = m.group(2).strip()
            if _JOB_PATTERNS.search(href):
                results.append({
                    "source_board": self.source_board,
                    "title": text or href.rstrip("/").split("/")[-1].replace("-", " ").title(),
                    "company": "",
                    "location": None,
                    "url": href,
                })
        return results

    def search(self, page: Page, query: str, limit: int) -> list[dict]:
        try:
            page.goto(query, timeout=30000)
            html = page.content()
        except Exception:
            return []
        results = self.parse_listings(html)[:limit]
        # parse_listings is pure and has no access to the page, so it cannot
        # know the host. The host is a real, stable dedup key — unlike the
        # "" company placeholder it replaces, which made every listing here
        # fail posting_from_scrape's empty-company guard and get skipped.
        host = urllib.parse.urlsplit(page.url).netloc
        if host.startswith("www."):
            host = host[len("www."):]
        for r in results:
            r["url"] = urllib.parse.urljoin(page.url, r["url"])
            r["company"] = host
        return results
