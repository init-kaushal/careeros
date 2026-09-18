# Phase 3 Browser-Driven Job Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `careeros browse` CLI command that searches job boards via Playwright (reusing the user's existing Chrome session), scores each discovered job 1-100 against their stored profile via LLM, and saves picks to the job pipeline.

**Architecture:** A new `careeros/browser/` package wraps Playwright with a persistent Chrome context driver and four per-board scrapers behind a shared Protocol. `careeros/skills/job_score.py` scores jobs via LiteLLM. `careeros/cli/browse_cmd.py` orchestrates the full flow. No existing files are structurally changed except `main.py` (adds one line) and `pyproject.toml` (adds one dep + pytest markers).

**Tech Stack:** Python 3.11+, Playwright ≥1.40 (new), LiteLLM (existing), Pydantic v2, Typer + Rich.

**Spec:** `docs/superpowers/specs/2026-09-18-phase3-browser-search-design.md`

## Global Constraints

- Python 3.11+ — `str | None` union syntax; `from __future__ import annotations` where needed for lazy type evaluation.
- Pydantic v2 — `model_validate`, `model_dump_json`, `model_validate_json`, `model_copy` only.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/skills/`, `careeros/sources/`, or `careeros/browser/`.
- LLM calls via `litellm.completion()` only. Default model: `os.environ.get("CAREEROS_MODEL", "claude-haiku-4-5-20251001")`.
- Prompts: instruction string constant + concatenated user text. No `.format()` or f-strings with user data.
- Activity log is append-only; no event is ever edited or deleted. Use `ActivityLogger.new_event` + `ActivityLogger.log`.
- `playwright` import is always lazy (inside functions/context managers that need it, or guarded by `TYPE_CHECKING`). Tests never import playwright at module level.
- Integration tests are marked `@pytest.mark.integration` and are skipped by default.
- No traceback ever reaches the user — all exceptions caught at CLI boundary with friendly messages.
- `atomic_write` must already use write-to-temp-then-rename (existing `LocalFilesystemStorage` guarantees this — do not bypass it).

---

### Task 1: Package Scaffolding + Scraper Protocol

**Files:**
- Modify: `pyproject.toml`
- Create: `careeros/browser/__init__.py`
- Create: `careeros/browser/scrapers/__init__.py`
- Create: `careeros/browser/scrapers/base.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/fixtures/` (directory — just create the path by adding the first fixture file here)

**Interfaces:**
- Produces: `Scraper` Protocol from `careeros.browser.scrapers.base` (imported by Tasks 3 and 5)
- Produces: `playwright>=1.40` in optional-style dep (required at runtime for browse; tests skip import)

- [ ] **Step 1: Add playwright dep and pytest markers to pyproject.toml**

Open `pyproject.toml`. Add `"playwright>=1.40",` to `dependencies`. Add `markers` to `[tool.pytest.ini_options]`:

```toml
[project]
name = "careeros"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer[all]>=0.12",
    "litellm>=1.40",
    "pydantic>=2.0",
    "playwright>=1.40",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.14",
]

[project.scripts]
careeros = "careeros.cli.main:app"

[tool.hatch.build.targets.wheel]
packages = ["careeros"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: requires live browser and network (skipped by default)"]
```

- [ ] **Step 2: Create empty package init files**

`careeros/browser/__init__.py` — empty file (zero bytes).

`careeros/browser/scrapers/__init__.py` — empty file (zero bytes).

`tests/integration/__init__.py` — empty file (zero bytes).

- [ ] **Step 3: Create Scraper Protocol**

`careeros/browser/scrapers/base.py`:

```python
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
```

- [ ] **Step 4: Verify existing tests still pass**

```bash
pytest tests/ -q --ignore=tests/integration
```

Expected: all existing tests pass (≥126). Zero new tests added in this task — scaffolding only.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml careeros/browser/__init__.py careeros/browser/scrapers/__init__.py careeros/browser/scrapers/base.py tests/integration/__init__.py
git commit -m "feat: scaffold careeros/browser package + Scraper Protocol"
```

---

### Task 2: Browser Driver

**Files:**
- Create: `careeros/browser/driver.py`

**Interfaces:**
- Consumes: `careeros.sources.ats._strip_html` (existing function — import directly)
- Produces:
  - `get_chrome_profile_path() -> str` (used by `launch_browser`)
  - `launch_browser(headless: bool = False) -> ContextManager[tuple[BrowserContext, Page]]` (used by browse_cmd)
  - `fetch_jd_text(page: Page, url: str) -> str` (used by browse_cmd)

Note: `page` in `fetch_jd_text` is a Playwright `Page` at runtime, but the type annotation uses `TYPE_CHECKING` guard so playwright is not imported at module level. Tests mock this function entirely — no playwright needed in tests.

- [ ] **Step 1: Write tests for get_chrome_profile_path**

`tests/test_browser_driver.py`:

```python
import platform
import pytest
from careeros.browser.driver import get_chrome_profile_path


def test_get_chrome_profile_path_macos(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    path = get_chrome_profile_path()
    assert "Google/Chrome" in path
    assert path.startswith("/")


def test_get_chrome_profile_path_linux(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    path = get_chrome_profile_path()
    assert "google-chrome" in path
    assert path.startswith("/")


def test_get_chrome_profile_path_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    path = get_chrome_profile_path()
    assert "Google" in path
    assert "Chrome" in path
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_browser_driver.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.browser.driver'`

- [ ] **Step 3: Implement driver.py**

`careeros/browser/driver.py`:

```python
from __future__ import annotations

import os
import platform
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


def get_chrome_profile_path() -> str:
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if system == "Linux":
        return os.path.expanduser("~/.config/google-chrome")
    return os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")


@contextmanager
def launch_browser(headless: bool = False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=get_chrome_profile_path(),
            headless=headless,
            channel="chrome",
        )
        page = context.new_page()
        try:
            yield context, page
        finally:
            context.close()


def fetch_jd_text(page: Page, url: str) -> str:
    from careeros.sources.ats import _strip_html
    try:
        page.goto(url, timeout=15000)
        html = page.content()
        return _strip_html(html)[:4000]
    except Exception:
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_browser_driver.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run full suite to verify no regressions**

```bash
pytest tests/ -q --ignore=tests/integration
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add careeros/browser/driver.py tests/test_browser_driver.py
git commit -m "feat: add browser driver with platform-aware Chrome profile path"
```

---

### Task 3: Scraper Implementations + HTML Fixtures + Tests

**Files:**
- Create: `careeros/browser/scrapers/linkedin.py`
- Create: `careeros/browser/scrapers/indeed.py`
- Create: `careeros/browser/scrapers/wellfound.py`
- Create: `careeros/browser/scrapers/generic.py`
- Create: `tests/fixtures/linkedin_results.html`
- Create: `tests/fixtures/indeed_results.html`
- Create: `tests/fixtures/wellfound_results.html`
- Create: `tests/fixtures/generic_jobs_page.html`
- Create: `tests/test_scrapers.py`

**Interfaces:**
- Consumes: `Scraper` Protocol from `careeros.browser.scrapers.base`
- Produces:
  - `LinkedInScraper` with `source_board = "linkedin"`, `search(page, query, limit)`, `parse_listings(html)`
  - `IndeedScraper` with `source_board = "indeed"`, `search(page, query, limit)`, `parse_listings(html)`
  - `WellfoundScraper` with `source_board = "wellfound"`, `search(page, query, limit)`, `parse_listings(html)`
  - `GenericScraper` with `source_board = "generic"`, `search(page, url, limit)`, `parse_listings(html)`

All scrapers return normalized dicts: `{"source_board": str, "title": str, "company": str, "location": str | None, "url": str}`.

`parse_listings(html)` is a pure function — no browser, no network. Used exclusively by tests. `search()` uses the Playwright `page` object passed in — no playwright import at module level.

Note: `parse_listings` uses `re` to extract from fixture HTML. The fixtures use simple, flat HTML that regex can handle reliably. Real-world scraping uses Playwright selectors in `search()`.

- [ ] **Step 1: Create HTML fixtures**

`tests/fixtures/linkedin_results.html`:
```html
<div class="job-card-container"><a class="job-card-container__link" href="https://www.linkedin.com/jobs/view/111">Senior SRE</a><span class="job-card-container__company-name">Acme Corp</span><span class="job-card-container__metadata-item">San Francisco, CA</span></div>
<div class="job-card-container"><a class="job-card-container__link" href="https://www.linkedin.com/jobs/view/222">Platform Engineer</a><span class="job-card-container__company-name">Beta Inc</span><span class="job-card-container__metadata-item">Remote</span></div>
```

`tests/fixtures/indeed_results.html`:
```html
<div class="job_seen_beacon"><h2 class="jobTitle"><a href="/rc/clk?jk=abc123" data-jk="abc123">Senior SRE</a></h2><span class="companyName">Acme Corp</span><div class="companyLocation">San Francisco, CA</div></div>
<div class="job_seen_beacon"><h2 class="jobTitle"><a href="/rc/clk?jk=def456" data-jk="def456">Platform Engineer</a></h2><span class="companyName">Beta Inc</span><div class="companyLocation">Remote</div></div>
```

`tests/fixtures/wellfound_results.html`:
```html
<div class="job-listing"><a class="job-listing__title" href="https://wellfound.com/jobs/1234-senior-sre">Senior SRE</a><span class="job-listing__company">Acme Corp</span><span class="job-listing__location">San Francisco, CA</span></div>
<div class="job-listing"><a class="job-listing__title" href="https://wellfound.com/jobs/5678-platform-engineer">Platform Engineer</a><span class="job-listing__company">Beta Inc</span><span class="job-listing__location">Remote</span></div>
```

`tests/fixtures/generic_jobs_page.html`:
```html
<html><head><title>Acme Corp Careers</title></head><body>
<h1>Open Positions</h1>
<a href="https://acme.com/jobs/senior-sre">Senior SRE</a>
<a href="https://acme.com/careers/platform-engineer">Platform Engineer</a>
<a href="https://acme.com/about">About Us</a>
<a href="https://acme.com/contact">Contact</a>
</body></html>
```

- [ ] **Step 2: Write failing tests**

`tests/test_scrapers.py`:

```python
import re
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestLinkedInScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        results = scraper.parse_listings(_read("linkedin_results.html"))
        assert len(results) == 2
        assert results[0]["source_board"] == "linkedin"
        assert results[0]["title"] == "Senior SRE"
        assert results[0]["company"] == "Acme Corp"
        assert results[0]["location"] == "San Francisco, CA"
        assert "linkedin.com/jobs/view/111" in results[0]["url"]

    def test_parse_listings_empty_html_returns_empty_list(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        assert scraper.parse_listings("<html><body></body></html>") == []

    def test_parse_listings_second_result(self):
        from careeros.browser.scrapers.linkedin import LinkedInScraper
        scraper = LinkedInScraper()
        results = scraper.parse_listings(_read("linkedin_results.html"))
        assert results[1]["title"] == "Platform Engineer"
        assert results[1]["company"] == "Beta Inc"


class TestIndeedScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.indeed import IndeedScraper
        scraper = IndeedScraper()
        results = scraper.parse_listings(_read("indeed_results.html"))
        assert len(results) == 2
        assert results[0]["source_board"] == "indeed"
        assert results[0]["title"] == "Senior SRE"
        assert results[0]["company"] == "Acme Corp"
        assert results[0]["location"] == "San Francisco, CA"
        assert "indeed.com" in results[0]["url"] or results[0]["url"].startswith("https://")

    def test_parse_listings_empty_html_returns_empty_list(self):
        from careeros.browser.scrapers.indeed import IndeedScraper
        assert IndeedScraper().parse_listings("<div></div>") == []


class TestWellfoundScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.wellfound import WellfoundScraper
        scraper = WellfoundScraper()
        results = scraper.parse_listings(_read("wellfound_results.html"))
        assert len(results) == 2
        assert results[0]["source_board"] == "wellfound"
        assert results[0]["title"] == "Senior SRE"
        assert results[0]["company"] == "Acme Corp"
        assert "wellfound.com" in results[0]["url"]


class TestGenericScraper:
    def test_parse_listings_extracts_job_pattern_hrefs(self):
        from careeros.browser.scrapers.generic import GenericScraper
        scraper = GenericScraper()
        results = scraper.parse_listings(_read("generic_jobs_page.html"))
        urls = [r["url"] for r in results]
        assert any("/jobs/" in u for u in urls)
        assert any("/careers/" in u for u in urls)
        assert all(r["source_board"] == "generic" for r in results)

    def test_parse_listings_ignores_non_job_links(self):
        from careeros.browser.scrapers.generic import GenericScraper
        scraper = GenericScraper()
        results = scraper.parse_listings(_read("generic_jobs_page.html"))
        urls = [r["url"] for r in results]
        assert not any("/about" in u for u in urls)
        assert not any("/contact" in u for u in urls)

    def test_parse_listings_empty_page_returns_empty_list(self):
        from careeros.browser.scrapers.generic import GenericScraper
        html = "<html><body><a href='/about'>About</a></body></html>"
        assert GenericScraper().parse_listings(html) == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_scrapers.py -v
```

Expected: FAIL — scraper modules do not exist yet.

- [ ] **Step 4: Implement LinkedIn scraper**

`careeros/browser/scrapers/linkedin.py`:

```python
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
```

- [ ] **Step 5: Implement Indeed scraper**

`careeros/browser/scrapers/indeed.py`:

```python
from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_BEACON = re.compile(r'<div[^>]+class="[^"]*\bjob_seen_beacon\b[^"]*"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
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
            url = url.split("?")[0]
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
                        "url": url_full.split("?")[0],
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
```

- [ ] **Step 6: Implement Wellfound scraper**

`careeros/browser/scrapers/wellfound.py`:

```python
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
```

- [ ] **Step 7: Implement Generic scraper**

`careeros/browser/scrapers/generic.py`:

```python
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_JOB_PATTERNS = re.compile(r'/(jobs?|careers?|positions?|openings?)/', re.IGNORECASE)
_HREF = re.compile(r'<a[^>]+href="([^"#"]+)"[^>]*>([^<]*)</a>', re.DOTALL)


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
        page.goto(query, timeout=30000)
        html = page.content()
        return self.parse_listings(html)[:limit]
```

Note: for `GenericScraper`, the `query` parameter passed to `search()` is actually the full URL (from `--url` CLI flag). This is the only scraper that uses `query` as a URL rather than a search term.

- [ ] **Step 8: Run tests to verify they pass**

```bash
pytest tests/test_scrapers.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 9: Run full suite**

```bash
pytest tests/ -q --ignore=tests/integration
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add careeros/browser/scrapers/linkedin.py careeros/browser/scrapers/indeed.py careeros/browser/scrapers/wellfound.py careeros/browser/scrapers/generic.py tests/fixtures/linkedin_results.html tests/fixtures/indeed_results.html tests/fixtures/wellfound_results.html tests/fixtures/generic_jobs_page.html tests/test_scrapers.py
git commit -m "feat: add LinkedIn/Indeed/Wellfound/Generic scrapers with HTML fixture tests"
```

---

### Task 4: LLM Scoring Skill + Query Builder + Tests

**Files:**
- Create: `careeros/skills/browse_query.py`
- Create: `careeros/skills/job_score.py`
- Create: `tests/test_job_score.py`

**Interfaces:**
- Consumes:
  - `Profile` from `careeros.core.models` — fields: `title: str | None`, `summary: str | None`
  - `Skills` from `careeros.core.models` — field: `skills: list[Skill]`, each `Skill` has `name: str`
  - `Goals` from `careeros.core.models` — fields: `short_term: list[str]`, `long_term: list[str]`
  - `litellm.completion` (existing pattern from `careeros/skills/job_extract.py`)
- Produces:
  - `job_query_from_profile(profile: Profile, goals: Goals) -> str` (used by browse_cmd Task 5)
  - `score_job(jd_text: str, profile: Profile, skills: Skills, model: str | None = None) -> dict` — returns `{"score": int, "reasoning": str, "strengths": list[str], "gaps": list[str]}`; never raises

- [ ] **Step 1: Write failing tests**

`tests/test_job_score.py`:

```python
import os
import pytest
from unittest.mock import MagicMock, patch
from careeros.core.models import Profile, Skill, Skills, Goals


def _make_profile():
    return Profile(name="Alice", title="Senior SRE", summary="SRE with 6 years experience")


def _make_skills():
    return Skills(skills=[Skill(name="Kubernetes"), Skill(name="Go"), Skill(name="Terraform")])


def _make_goals():
    return Goals(short_term=["platform role", "remote work"], long_term=["staff engineer"])


class TestScoreJob:
    def test_returns_full_dict_on_success(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 85, "reasoning": "Great fit.", "strengths": ["Kubernetes"], "gaps": ["Java"]}'
        with patch("litellm.completion", return_value=mock_resp):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result["score"] == 85
        assert result["reasoning"] == "Great fit."
        assert "Kubernetes" in result["strengths"]
        assert isinstance(result["gaps"], list)

    def test_returns_fallback_on_llm_error(self):
        from careeros.skills.job_score import score_job
        with patch("litellm.completion", side_effect=Exception("API error")):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result == {"score": 0, "reasoning": "Could not score.", "strengths": [], "gaps": []}

    def test_returns_fallback_on_bad_json(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not json at all"
        with patch("litellm.completion", return_value=mock_resp):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result["score"] == 0

    def test_strips_markdown_fences_from_response(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '```json\n{"score": 70, "reasoning": "ok", "strengths": [], "gaps": []}\n```'
        with patch("litellm.completion", return_value=mock_resp):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result["score"] == 70

    def test_uses_careeros_model_env_var(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            score_job("text", _make_profile(), _make_skills())
        call_model = mock_llm.call_args[1]["model"]
        assert call_model == "gpt-4o"

    def test_model_param_overrides_env(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            score_job("text", _make_profile(), _make_skills(), model="claude-haiku-4-5-20251001")
        assert mock_llm.call_args[1]["model"] == "claude-haiku-4-5-20251001"

    def test_profile_title_appears_in_prompt(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            score_job("some jd text", _make_profile(), _make_skills())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "Senior SRE" in prompt

    def test_jd_text_capped_at_4000_chars(self):
        from careeros.skills.job_score import score_job
        long_jd = "x" * 5000
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            score_job(long_jd, _make_profile(), _make_skills())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "x" * 4001 not in prompt
        assert "x" * 4000 in prompt or prompt.count("x") <= 4000


class TestJobQueryFromProfile:
    def test_includes_profile_title(self):
        from careeros.skills.browse_query import job_query_from_profile
        profile = Profile(name="Alice", title="Senior SRE")
        goals = Goals(short_term=["platform role", "Kubernetes focus"], long_term=["staff engineer"])
        query = job_query_from_profile(profile, goals)
        assert "Senior SRE" in query

    def test_max_80_chars(self):
        from careeros.skills.browse_query import job_query_from_profile
        profile = Profile(name="Alice", title="A" * 50)
        goals = Goals(short_term=["B" * 40], long_term=["C" * 40])
        query = job_query_from_profile(profile, goals)
        assert len(query) <= 80

    def test_empty_profile_does_not_crash(self):
        from careeros.skills.browse_query import job_query_from_profile
        query = job_query_from_profile(Profile(), Goals())
        assert isinstance(query, str)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_job_score.py -v
```

Expected: FAIL — modules do not exist yet.

- [ ] **Step 3: Implement browse_query.py**

`careeros/skills/browse_query.py`:

```python
from careeros.core.models import Goals, Profile


def job_query_from_profile(profile: Profile, goals: Goals) -> str:
    parts = []
    if profile.title:
        parts.append(profile.title)
    keywords = []
    for item in goals.short_term + goals.long_term:
        for word in item.split():
            if word not in keywords:
                keywords.append(word)
    parts.extend(keywords[:3])
    query = " ".join(parts)
    return query[:80]
```

- [ ] **Step 4: Implement job_score.py**

`careeros/skills/job_score.py`:

```python
import json
import os
import litellm
from careeros.core.models import Profile, Skills

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_JD_CAP = 4000

_SCORE_INSTRUCTIONS = """\
You are evaluating how well a candidate profile matches a job description.
Return ONLY valid JSON — no markdown, no explanation.

{
  "score": <integer 1-100>,
  "reasoning": "<1-2 sentences>",
  "strengths": ["<strength>", "..."],
  "gaps": ["<gap>", "..."]
}

Scoring guide:
90-100 = near-perfect match
70-89  = strong fit, minor gaps
50-69  = partial fit, notable gaps
1-49   = significant misalignment

Candidate profile:
"""

_FAILURE = {"score": 0, "reasoning": "Could not score.", "strengths": [], "gaps": []}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _build_profile_text(profile: Profile, skills: Skills) -> str:
    lines = []
    if profile.title:
        lines.append("Title: " + profile.title)
    if profile.summary:
        lines.append("Summary: " + profile.summary)
    if skills.skills:
        names = ", ".join(s.name for s in skills.skills)
        lines.append("Skills: " + names)
    return "\n".join(lines)


def score_job(
    jd_text: str,
    profile: Profile,
    skills: Skills,
    model: str | None = None,
) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    profile_text = _build_profile_text(profile, skills)
    prompt = _SCORE_INSTRUCTIONS + profile_text + "\n\nJob description:\n" + jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_json(resp.choices[0].message.content)
    except Exception:
        return dict(_FAILURE)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_job_score.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -q --ignore=tests/integration
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add careeros/skills/browse_query.py careeros/skills/job_score.py tests/test_job_score.py
git commit -m "feat: add job_score + browse_query skills with LLM scoring 1-100"
```

---

### Task 5: Browse CLI Command + Main Wiring + Tests

**Files:**
- Create: `careeros/cli/browse_cmd.py`
- Modify: `careeros/cli/main.py` (add 2 lines)
- Create: `tests/test_browse_cmd.py`

**Interfaces:**
- Consumes:
  - `launch_browser` from `careeros.browser.driver`
  - `fetch_jd_text` from `careeros.browser.driver`
  - `LinkedInScraper`, `IndeedScraper`, `WellfoundScraper`, `GenericScraper` from `careeros.browser.scrapers.*`
  - `job_query_from_profile` from `careeros.skills.browse_query`
  - `score_job` from `careeros.skills.job_score`
  - `Profile`, `Skills`, `Goals`, `Job` from `careeros.core.models`
  - `make_job_id` from `careeros.core.job_id`
  - `ActivityLogger` from `careeros.core.activity`
  - `_get_storage` pattern from existing `job_cmd.py` (re-implement same pattern, do not import from job_cmd)
  - `GlobalConfig` from `careeros.config`
  - `open_workspace` from `careeros.workspace.manager`
- Produces:
  - `browse_app` Typer app (imported by `main.py`)

**Command signature:**
```
careeros browse --board TEXT [--url TEXT] [--limit INT] [--min-score INT] [--headless] [--workspace TEXT]
```
- `--board`: required, one of `linkedin | indeed | wellfound | url`
- `--url`: required only when `--board url`
- `--limit`: default 20
- `--min-score`: default 0
- `--headless`: flag, default False

- [ ] **Step 1: Write failing tests**

`tests/test_browse_cmd.py`:

```python
import pytest
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from careeros.cli.browse_cmd import browse_app
from careeros.core.models import Profile, Skill, Skills, Goals, Job
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _setup_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    Profile(name="Alice", title="Senior SRE").save(storage)
    Skills(skills=[Skill(name="Kubernetes")]).save(storage)
    return str(tmp_path)


def _mock_launch(mock_page=None):
    if mock_page is None:
        mock_page = MagicMock()

    @contextmanager
    def _ctx(headless=False) -> Iterator:
        yield MagicMock(), mock_page

    return _ctx


def _mock_postings():
    return [
        {"source_board": "linkedin", "title": "Senior SRE", "company": "Acme", "location": "SF", "url": "https://example.com/jobs/1"},
        {"source_board": "linkedin", "title": "Platform Engineer", "company": "Beta", "location": "Remote", "url": "https://example.com/jobs/2"},
    ]


def _mock_score(score=85):
    return {"score": score, "reasoning": "Great fit.", "strengths": ["Kubernetes"], "gaps": []}


class TestBrowseEndToEnd:
    def test_saves_selected_job(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd text"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1\n")

        assert result.exit_code == 0
        assert "Saved 1 job" in result.output

    def test_job_written_to_workspace(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [_mock_postings()[0]]

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1\n")

        storage = LocalFilesystemStorage(ws)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].company == "Acme"
        assert jobs[0].source == "linkedin"

    def test_min_score_filters_out_low_scores(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        scores = [_mock_score(score=90), _mock_score(score=40)]
        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", side_effect=scores):
            result = runner.invoke(
                browse_app,
                ["--board", "linkedin", "--min-score", "80", "--workspace", ws],
                input="1\n",
            )

        assert result.exit_code == 0
        assert "Platform Engineer" not in result.output

    def test_all_below_min_score_prints_no_jobs_found(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [_mock_postings()[0]]

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score(score=20)):
            result = runner.invoke(
                browse_app,
                ["--board", "linkedin", "--min-score", "80", "--workspace", ws],
                input="",
            )

        assert "No jobs found" in result.output

    def test_user_quits_saves_zero_jobs(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = _mock_postings()

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="q\n")

        storage = LocalFilesystemStorage(ws)
        assert Job.list_all(storage) == []

    def test_headless_flag_passed_to_launch_browser(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = []
        calls = []

        @contextmanager
        def capturing_launch(headless=False):
            calls.append(headless)
            yield MagicMock(), MagicMock()

        with patch("careeros.cli.browse_cmd.launch_browser", capturing_launch), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--headless", "--workspace", ws], input="q\n")

        assert calls == [True]

    def test_activity_log_written_after_save(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [_mock_postings()[0]]

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="1\n")

        storage = LocalFilesystemStorage(ws)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = f"activity/{today}.jsonl"
        assert storage.exists(log_path)
        content = storage.read(log_path).decode()
        assert "job_added" in content

    def test_indeed_scraper_selected_for_indeed_board(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_linkedin = MagicMock()
        mock_indeed = MagicMock()
        mock_indeed.search.return_value = []

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_linkedin, "indeed": mock_indeed}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            runner.invoke(browse_app, ["--board", "indeed", "--workspace", ws], input="q\n")

        mock_indeed.search.assert_called_once()
        mock_linkedin.search.assert_not_called()


class TestBrowseErrorHandling:
    def test_playwright_not_installed_prints_install_instructions(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        with patch("careeros.cli.browse_cmd.launch_browser", side_effect=ImportError("playwright")):
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws])
        assert "playwright install" in result.output.lower() or "pip install playwright" in result.output

    def test_board_url_without_url_flag_exits_1(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        result = runner.invoke(browse_app, ["--board", "url", "--workspace", ws])
        assert result.exit_code == 1
        assert "--url" in result.output

    def test_no_workspace_exits_1(self):
        from careeros.config import GlobalConfig
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig(workspace_path=None)):
            result = runner.invoke(browse_app, ["--board", "linkedin"])
        assert result.exit_code == 1

    def test_scraper_exception_prints_warning_continues(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.side_effect = Exception("network error")

        with patch("careeros.cli.browse_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.browse_cmd.SCRAPERS", {"linkedin": mock_scraper}), \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()):
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws], input="q\n")

        assert result.exit_code == 0
        assert "No jobs found" in result.output or "warning" in result.output.lower() or "error" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_browse_cmd.py -v
```

Expected: FAIL — `careeros.cli.browse_cmd` does not exist.

- [ ] **Step 3: Implement browse_cmd.py**

Module-level imports are safe here: `driver.py` defers the actual `sync_playwright` import inside `launch_browser`'s body, and all scrapers defer their Playwright use the same way. This means patching `careeros.cli.browse_cmd.launch_browser` in tests replaces the name correctly.

`careeros/cli/browse_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from careeros.browser.driver import fetch_jd_text, launch_browser
from careeros.browser.scrapers.generic import GenericScraper
from careeros.browser.scrapers.indeed import IndeedScraper
from careeros.browser.scrapers.linkedin import LinkedInScraper
from careeros.browser.scrapers.wellfound import WellfoundScraper
from careeros.config import GlobalConfig
from careeros.core.activity import ActivityLogger
from careeros.core.job_id import make_job_id
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.job_score import score_job
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace

browse_app = typer.Typer(name="browse", help="Search job boards using your browser session.")
console = Console()

SCRAPERS: dict = {
    "linkedin": LinkedInScraper(),
    "indeed": IndeedScraper(),
    "wellfound": WellfoundScraper(),
}


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@browse_app.command("browse")
def browse_cmd(
    board: str = typer.Option(..., "--board", help="linkedin | indeed | wellfound | url"),
    url: str = typer.Option(None, "--url", help="Target URL (required when --board url)"),
    limit: int = typer.Option(20, "--limit", help="Max listings to fetch"),
    min_score: int = typer.Option(0, "--min-score", help="Minimum score to display"),
    headless: bool = typer.Option(False, "--headless", is_flag=True, help="Run browser headlessly"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    valid_boards = {"linkedin", "indeed", "wellfound", "url"}
    if board not in valid_boards:
        rprint(f"[red]Invalid --board '{board}'. Valid: {' '.join(sorted(valid_boards))}[/red]")
        raise typer.Exit(1)

    if board == "url" and not url:
        rprint("[red]Provide --url when using --board url.[/red]")
        raise typer.Exit(1)

    storage = _get_storage(workspace)
    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        profile = Profile.load(storage)
    except FileNotFoundError:
        rprint("[red]No profile found. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    skills = Skills.load_or_empty(storage)
    goals = Goals.load_or_empty(storage)
    query = job_query_from_profile(profile, goals) if board != "url" else (url or "")
    scraper = GenericScraper() if board == "url" else SCRAPERS[board]

    try:
        with launch_browser(headless=headless) as (_, page):
            try:
                postings = scraper.search(page, query, limit)
            except Exception as exc:
                rprint(f"[yellow]Warning: could not search {board}: {exc}[/yellow]")
                postings = []

            rprint(f"Scoring {len(postings)} listings...")
            scored = []
            for posting in postings:
                jd_text = fetch_jd_text(page, posting["url"])
                result = score_job(jd_text, profile, skills)
                scored.append({**posting, "score": result["score"], "reasoning": result["reasoning"]})
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        rprint("Run: [bold]pip install playwright && playwright install chrome[/bold]")
        raise typer.Exit(1)

    filtered = [p for p in scored if p["score"] >= min_score]
    filtered.sort(key=lambda p: p["score"], reverse=True)

    if not filtered:
        rprint(f"No jobs found matching min-score {min_score}.")
        return

    table = Table(show_header=True)
    table.add_column("#", style="bold")
    table.add_column("Score")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("URL")
    for i, p in enumerate(filtered, 1):
        table.add_row(str(i), str(p["score"]), p["company"], p["title"], p.get("location") or "—", p["url"])
    console.print(table)

    picks_str = Prompt.ask("Pick jobs to save (e.g. 1 3 5, or q to quit)")
    if picks_str.strip().lower() == "q":
        return

    logger = ActivityLogger(ctx.storage)
    now = _now()
    saved = 0
    for part in picks_str.split():
        if not part.isdigit():
            continue
        idx = int(part) - 1
        if not (0 <= idx < len(filtered)):
            continue
        p = filtered[idx]
        job_id = make_job_id(p["company"], p["title"])
        job = Job(
            id=job_id,
            source=p["source_board"],
            url=p["url"],
            company=p["company"],
            title=p["title"],
            location=p.get("location"),
            stage="saved",
            created_at=now,
            updated_at=now,
        )
        job.save(storage)
        logger.log(logger.new_event(
            "job_added", "browse",
            "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=job_id,
        ))
        saved += 1

    rprint(f"[green]Saved {saved} job(s)[/green]")
```

- [ ] **Step 4: Wire browse_app into main.py**

Open `careeros/cli/main.py`. Add two lines:

```python
import typer
from careeros.cli.onboard import onboard_cmd
from careeros.cli.portability import export_cmd, import_workspace_cmd
from careeros.cli.workspace_cmd import workspace_app
from careeros.cli.job_cmd import job_app
from careeros.cli.browse_cmd import browse_app

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")
app.add_typer(job_app, name="job")
app.add_typer(browse_app, name="browse")
app.command("export")(export_cmd)
app.command("import")(import_workspace_cmd)

if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_browse_cmd.py -v
```

Expected: all 12 tests PASS. If any test fails due to the `SCRAPERS` module-level dict being mutated between tests, add a `monkeypatch` reset or move scraper init inside the function — the `_init_scrapers()` lazy pattern handles this.

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -q --ignore=tests/integration
```

Expected: all tests pass (≥149).

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/browse_cmd.py careeros/cli/main.py tests/test_browse_cmd.py
git commit -m "feat: add careeros browse command with board search, LLM scoring, and job save"
```

---

### Task 6: Integration Test Skeleton

**Files:**
- Create: `tests/integration/test_live_browser.py`

**Interfaces:**
- Consumes: `launch_browser` from `careeros.browser.driver`; `LinkedInScraper`, `IndeedScraper` from their modules
- These tests are marked `@pytest.mark.integration` and skipped by default in CI

- [ ] **Step 1: Write integration tests**

`tests/integration/test_live_browser.py`:

```python
import pytest


@pytest.mark.integration
def test_linkedin_search_returns_results():
    """Requires Chrome with active LinkedIn session. Run: pytest -m integration"""
    from careeros.browser.driver import launch_browser
    from careeros.browser.scrapers.linkedin import LinkedInScraper
    scraper = LinkedInScraper()
    with launch_browser(headless=False) as (_, page):
        results = scraper.search(page, "site reliability engineer", limit=5)
    assert len(results) >= 1
    assert all(r["source_board"] == "linkedin" for r in results)
    assert all(r["title"] for r in results)
    assert all(r["url"].startswith("https://") for r in results)


@pytest.mark.integration
def test_indeed_search_returns_results():
    """Requires Chrome with active Indeed session. Run: pytest -m integration"""
    from careeros.browser.driver import launch_browser
    from careeros.browser.scrapers.indeed import IndeedScraper
    scraper = IndeedScraper()
    with launch_browser(headless=False) as (_, page):
        results = scraper.search(page, "site reliability engineer", limit=5)
    assert len(results) >= 1
    assert all(r["source_board"] == "indeed" for r in results)
    assert all(r["title"] for r in results)
```

- [ ] **Step 2: Verify integration tests are skipped by default**

```bash
pytest tests/ -q --ignore=tests/integration
```

Expected: integration tests not collected; all other tests pass.

- [ ] **Step 3: Verify integration marker is recognized**

```bash
pytest tests/integration/ --collect-only -q
```

Expected: 2 tests collected with `@pytest.mark.integration`.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_live_browser.py
git commit -m "test: add integration test skeleton for live browser scraping"
```
