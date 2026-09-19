from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_PERSON_CARD = re.compile(
    r'<li[^>]+class="[^"]*\breusable-search__result-container\b[^"]*"[^>]*>(.*?)</li>', re.DOTALL
)
_PERSON_LINK = re.compile(
    r'class="[^"]*\bentity-result__title-text\b[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>.*?<span[^>]*>([^<]+)</span>',
    re.DOTALL,
)
_PERSON_TITLE = re.compile(r'class="[^"]*\bentity-result__primary-subtitle\b[^"]*"[^>]*>([^<]+)')

_BASE_URL = "https://www.linkedin.com/search/results/people/?keywords="


class PeopleSearchScraper:
    source_board = "linkedin_people"

    def parse_listings(self, html: str) -> list[dict]:
        results = []
        for card in _PERSON_CARD.findall(html):
            link_m = _PERSON_LINK.search(card)
            if not link_m:
                continue
            url = link_m.group(1).split("?")[0]
            name = link_m.group(2).strip()
            title_m = _PERSON_TITLE.search(card)
            results.append({
                "name": name,
                "title": title_m.group(1).strip() if title_m else "",
                "linkedin_url": url,
            })
        return results

    def search(self, page: Page, query: str, limit: int) -> list[dict]:
        url = _BASE_URL + urllib.parse.quote(query)
        page.goto(url, timeout=30000)
        try:
            page.wait_for_selector(".reusable-search__result-container", timeout=15000)
        except Exception:
            return []
        html = page.content()
        return self.parse_listings(html)[:limit]
