from __future__ import annotations

import re
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
        return self.parse_listings(html)[:limit]
