from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_JOB_CARD = re.compile(r'<div[^>]+class="[^"]*\bjob-card-container\b[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_LINK = re.compile(r'class="[^"]*\bjob-card-container__link\b[^"]*"[^>]+href="([^"]+)"[^>]*>([^<]+)', re.DOTALL)
_COMPANY = re.compile(r'class="[^"]*\bjob-card-container__company-name\b[^"]*"[^>]*>([^<]+)')
_LOCATION = re.compile(r'class="[^"]*\bjob-card-container__metadata-item\b[^"]*"[^>]*>([^<]+)')

_BASE_URL = "https://www.linkedin.com/jobs/search/?keywords="


class LinkedInScraper:
    source_board = "linkedin"

    def parse_listings(self, html: str) -> list[dict]:
        results = []
        for card in _JOB_CARD.findall(html):
            link_m = _LINK.search(card)
            if not link_m:
                continue
            url = link_m.group(1).split("?")[0]
            title = link_m.group(2).strip()
            company_m = _COMPANY.search(card)
            location_m = _LOCATION.search(card)
            results.append({
                "source_board": self.source_board,
                "title": title,
                "company": company_m.group(1).strip() if company_m else "",
                "location": location_m.group(1).strip() if location_m else None,
                "url": url,
            })
        return results

    def search(self, page: Page, query: str, limit: int) -> list[dict]:
        import urllib.parse
        url = _BASE_URL + urllib.parse.quote(query)
        page.goto(url, timeout=30000)
        page.wait_for_selector(".job-card-container", timeout=15000)
        results = []
        while len(results) < limit:
            cards = page.locator(".job-card-container").all()
            for card in cards[len(results):]:
                try:
                    link = card.locator("a.job-card-container__link").first
                    title = link.inner_text().strip()
                    href = link.get_attribute("href") or ""
                    href = href.split("?")[0]
                    company = card.locator(".job-card-container__company-name").first.inner_text().strip()
                    loc_el = card.locator(".job-card-container__metadata-item").first
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
            else:
                break
        return results[:limit]
