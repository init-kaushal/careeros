from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_LISTING = re.compile(r'<div[^>]+class="[^"]*\bjob-listing\b[^"]*"[^>]*>(.*?)(?=<div[^>]+class="[^"]*\bjob-listing\b|$)', re.DOTALL)
_TITLE_URL = re.compile(r'class="[^"]*\bjob-listing__title\b[^"]*"[^>]+href="([^"]+)"[^>]*>([^<]+)', re.DOTALL)
_COMPANY = re.compile(r'class="[^"]*\bjob-listing__company\b[^"]*"[^>]*>([^<]+)')
_LOCATION = re.compile(r'class="[^"]*\bjob-listing__location\b[^"]*"[^>]*>([^<]+)')

_BASE_URL = "https://wellfound.com/jobs?q="


class WellfoundScraper:
    source_board = "wellfound"

    def parse_listings(self, html: str) -> list[dict]:
        results = []
        for card in _LISTING.findall(html):
            m = _TITLE_URL.search(card)
            if not m:
                continue
            company_m = _COMPANY.search(card)
            location_m = _LOCATION.search(card)
            results.append({
                "source_board": self.source_board,
                "title": m.group(2).strip(),
                "company": company_m.group(1).strip() if company_m else "",
                "location": location_m.group(1).strip() if location_m else None,
                "url": m.group(1),
            })
        return results

    def search(self, page: Page, query: str, limit: int) -> list[dict]:
        url = _BASE_URL + urllib.parse.quote(query)
        page.goto(url, timeout=30000)
        page.wait_for_selector(".job-listing", timeout=15000)
        results = []
        prev_count = 0
        while len(results) < limit:
            cards = page.locator(".job-listing").all()
            for card in cards[len(results):]:
                try:
                    link = card.locator("a.job-listing__title").first
                    title = link.inner_text().strip()
                    href = link.get_attribute("href") or ""
                    company = card.locator(".job-listing__company").first.inner_text().strip()
                    loc_el = card.locator(".job-listing__location").first
                    location = loc_el.inner_text().strip() if loc_el.count() else None
                    results.append({
                        "source_board": self.source_board,
                        "title": title,
                        "company": company,
                        "location": location,
                        "url": href,
                    })
                except Exception:
                    continue
                if len(results) >= limit:
                    break
            current_count = len(page.locator(".job-listing").all())
            if current_count == prev_count:
                break
            prev_count = current_count
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        return results[:limit]
