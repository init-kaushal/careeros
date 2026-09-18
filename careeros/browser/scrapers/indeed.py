from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_TITLE = re.compile(r'class="jobTitle"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)', re.DOTALL)
_COMPANY = re.compile(r'class="companyName"[^>]*>([^<]+)')
_LOCATION = re.compile(r'class="companyLocation"[^>]*>([^<]+)')

_BASE_URL = "https://www.indeed.com/jobs?q="
_INDEED_BASE = "https://www.indeed.com"


class IndeedScraper:
    source_board = "indeed"

    def parse_listings(self, html: str) -> list[dict]:
        results = []
        for card in re.findall(r'<div[^>]+class="[^"]*\bjob_seen_beacon\b[^"]*"[^>]*>.*?(?=<div[^>]+class="[^"]*\bjob_seen_beacon\b|$)', html, re.DOTALL):
            title_m = _TITLE.search(card)
            if not title_m:
                continue
            href = title_m.group(1)
            url = href if href.startswith("http") else _INDEED_BASE + href
            title = title_m.group(2).strip()
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
        url = _BASE_URL + urllib.parse.quote(query)
        page.goto(url, timeout=30000)
        page.wait_for_selector(".job_seen_beacon", timeout=15000)
        results = []
        while len(results) < limit:
            cards = page.locator(".job_seen_beacon").all()
            for card in cards[len(results):]:
                try:
                    link = card.locator("h2.jobTitle a").first
                    title = link.inner_text().strip()
                    href = link.get_attribute("href") or ""
                    url_full = href if href.startswith("http") else _INDEED_BASE + href
                    company = card.locator(".companyName").first.inner_text().strip()
                    loc_el = card.locator(".companyLocation").first
                    location = loc_el.inner_text().strip() if loc_el.count() else None
                    results.append({
                        "source_board": self.source_board,
                        "title": title,
                        "company": company,
                        "location": location,
                        "url": url_full,
                    })
                except Exception:
                    continue
                if len(results) >= limit:
                    break
            next_btn = page.locator("a[data-testid='pagination-page-next']")
            if len(results) < limit and next_btn.count():
                next_btn.click()
                page.wait_for_selector(".job_seen_beacon", timeout=10000)
            else:
                break
        return results[:limit]
