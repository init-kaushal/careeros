# Phase 7 — People + Outreach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add company/people research (browser-driven, LLM-extracted) and role-aware email outreach (draft → review → approve → send) to CareerOS, gated by the existing `AgentRuntime.request_approval` seam.

**Architecture:** New `Company`/`Person`/`OutreachMessage` models follow the existing `Job` save/load pattern. A new `PeopleSearchScraper` implements the existing `Scraper` Protocol; company-page fetching reuses the existing `fetch_jd_text` helper directly (a single-page fetch doesn't fit the multi-result `Scraper.search` shape, and `fetch_jd_text`'s actual behavior — navigate, strip HTML, truncate, swallow exceptions — is exactly what's needed regardless of its job-description-flavored name). Two new LLM skills (`company_research.py`, `people_research.py`) follow `job_score.py`'s exact pattern; `outreach_draft.py` follows `cover_letter.py`'s pattern with role-aware instruction variants. A new `careeros/mailer.py` wraps stdlib `smtplib` for SMTP sending — the only place in the codebase permitted to read credentials from the environment. Five new CLI commands route through `AgentRuntime` exactly as `browse_cmd`/`apply_cmd` already do.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, Rich, Playwright (headless for research), stdlib `smtplib`/`email.mime.text` (no new dependency).

**Spec:** `docs/superpowers/specs/2026-09-19-phase7-people-outreach-design.md`

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string — SMTP credentials are the sole codebase exception, read from environment variables in `careeros/mailer.py` only, never persisted
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/` (`careeros/mailer.py` and `careeros/cli/` are exempt for the narrow case of reading SMTP env vars and calling `smtplib`)
- Activity logs are append-only; no event is ever edited or deleted
- Every meaningful action — both outcomes of any approval/decision point, not just the success path — produces an activity event. This is a standing principle for all CareerOS work, not scoped to this phase alone.
- Activity summaries and `ActionProposal.summary` built via string concatenation only — no `.format()` or f-strings with user data
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id` with the runtime's own identity
- CareerOS never guesses an email address from a name/company pattern — `Person.email` is populated only from a public source that lists one, or manual entry via `careeros people update`
- `send_email` never logs the message body, only recipient address and subject-level metadata

**Note beyond the spec text (implementation refinements resolved during planning):**
- Company-page research reuses `careeros.browser.driver.fetch_jd_text(page, url) -> str` directly rather than a new `Scraper`-Protocol class — see Architecture above.
- `Company.id = make_company_id(name)` and `Person.id = make_person_id(name, company_id)` are **deterministic** (no random suffix, unlike `make_job_id`) — re-researching the same company/person updates the same record instead of creating duplicates. This is a deliberate deviation from `Job`'s ID scheme, justified because companies/people are facts about the world (idempotent by identity), not postings (which can genuinely recur across boards).
- `OutreachMessage.id = job_id + "__" + person_id` — a deterministic composite key, not a generated ID. This means re-running `careeros outreach --job <id> --person <id>` (e.g., retrying after adding a missing email) updates the same record rather than creating a new one, matching the spec's "draft stays saved... for retry" requirement.
- `research people --job <id>` computes `company_id = make_company_id(job.company)` independently — it does NOT require `research company` to have run first. `outreach --job <id> --person <id>` DOES require `research company` to have run (it loads `Company` via `Company.load`, which raises `FileNotFoundError` if missing) — this matches the spec's literal step 1 ("Load Job, Person, Company — missing any → exit 1").

---

### Task 1: Company, Person, OutreachMessage Models + ID Helpers

**Files:**
- Create: `careeros/core/ids.py`
- Modify: `careeros/core/models.py` (append three classes)
- Test: `tests/test_ids.py`
- Test: `tests/test_company_person_outreach_models.py`

**Interfaces:**
- Produces: `make_company_id(name: str) -> str` — deterministic
- Produces: `make_person_id(name: str, company_id: str) -> str` — deterministic
- Produces: `Company(id, name, url=None, industry=None, size=None, notes=None, researched_at)` with `.save(storage)`, `.load(storage, company_id)` classmethod (raises `FileNotFoundError`)
- Produces: `Person(id, company_id, name, role_category, title=None, linkedin_url=None, email=None, researched_at)` with `.save(storage)`, `.load(storage, person_id)` classmethod (raises `FileNotFoundError`)
- Produces: `OutreachMessage(id, job_id, person_id, draft_text, send_state="drafted", referral_state="research", created_at, sent_at=None)` with `.save(storage)`, `.load(storage, message_id)` classmethod (raises `FileNotFoundError`)

- [ ] **Step 1: Write the failing tests for ID helpers**

Create `tests/test_ids.py`:

```python
from careeros.core.ids import make_company_id, make_person_id


def test_make_company_id_is_deterministic():
    assert make_company_id("Acme Corp") == make_company_id("Acme Corp")


def test_make_company_id_slugifies():
    company_id = make_company_id("Acme Corp!")
    assert company_id == "acme-corp"


def test_make_person_id_is_deterministic():
    company_id = make_company_id("Acme Corp")
    assert make_person_id("Jane Doe", company_id) == make_person_id("Jane Doe", company_id)


def test_make_person_id_differs_across_companies():
    id_a = make_person_id("Jane Doe", make_company_id("Acme Corp"))
    id_b = make_person_id("Jane Doe", make_company_id("Beta Inc"))
    assert id_a != id_b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.core.ids'`

- [ ] **Step 3: Implement ID helpers**

Create `careeros/core/ids.py`:

```python
import re


def _slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def make_company_id(name: str) -> str:
    return _slugify(name)[:40]


def make_person_id(name: str, company_id: str) -> str:
    return (company_id + "-" + _slugify(name))[:60]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ids.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing tests for the three models**

Create `tests/test_company_person_outreach_models.py`:

```python
import pytest
from careeros.core.ids import make_company_id, make_person_id
from careeros.core.models import Company, OutreachMessage, Person
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_company_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    company_id = make_company_id("Acme Corp")
    company = Company(id=company_id, name="Acme Corp", industry="Software", researched_at="2026-09-19T00:00:00Z")
    company.save(storage)
    loaded = Company.load(storage, company_id)
    assert loaded.name == "Acme Corp"
    assert loaded.industry == "Software"


def test_company_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        Company.load(storage, "nonexistent")


def test_person_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    company_id = make_company_id("Acme Corp")
    person_id = make_person_id("Jane Doe", company_id)
    person = Person(
        id=person_id, company_id=company_id, name="Jane Doe",
        role_category="em", title="Engineering Manager", researched_at="2026-09-19T00:00:00Z",
    )
    person.save(storage)
    loaded = Person.load(storage, person_id)
    assert loaded.name == "Jane Doe"
    assert loaded.role_category == "em"
    assert loaded.email is None


def test_person_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        Person.load(storage, "nonexistent")


def test_outreach_message_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    message_id = "acme-sre-abc1__acme-corp-jane-doe"
    message = OutreachMessage(
        id=message_id, job_id="acme-sre-abc1", person_id="acme-corp-jane-doe",
        draft_text="Hello Jane...", created_at="2026-09-19T00:00:00Z",
    )
    message.save(storage)
    loaded = OutreachMessage.load(storage, message_id)
    assert loaded.draft_text == "Hello Jane..."
    assert loaded.send_state == "drafted"
    assert loaded.referral_state == "research"


def test_outreach_message_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        OutreachMessage.load(storage, "nonexistent")
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_company_person_outreach_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'Company'`

- [ ] **Step 7: Append the three models to careeros/core/models.py**

Append to the end of `careeros/core/models.py` (after the `Job.list_all` classmethod — do not modify any existing class):

```python
class Company(BaseModel):
    id: str
    name: str
    url: str | None = None
    industry: str | None = None
    size: str | None = None
    notes: str | None = None
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("companies/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, company_id: str) -> "Company":
        path = "companies/" + company_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Company " + repr(company_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class Person(BaseModel):
    id: str
    company_id: str
    name: str
    role_category: str
    title: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("people/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, person_id: str) -> "Person":
        path = "people/" + person_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Person " + repr(person_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class OutreachMessage(BaseModel):
    id: str
    job_id: str
    person_id: str
    draft_text: str
    send_state: str = "drafted"
    referral_state: str = "research"
    created_at: str
    sent_at: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("outreach/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, message_id: str) -> "OutreachMessage":
        path = "outreach/" + message_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("OutreachMessage " + repr(message_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_company_person_outreach_models.py -v`
Expected: PASS (6 tests)

- [ ] **Step 9: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 10: Commit**

```bash
git add careeros/core/ids.py careeros/core/models.py tests/test_ids.py tests/test_company_person_outreach_models.py
git commit -m "feat: add Company, Person, OutreachMessage models and deterministic ID helpers"
```

---

### Task 2: PeopleSearchScraper

**Files:**
- Create: `careeros/browser/scrapers/people_search.py`
- Create: `tests/fixtures/linkedin_people_results.html`
- Test: `tests/test_people_search_scraper.py`

**Interfaces:**
- Produces: `PeopleSearchScraper` implementing the existing `Scraper` Protocol (`careeros/browser/scrapers/base.py`): `source_board = "linkedin_people"`, `parse_listings(html: str) -> list[dict]` returning `{"name": str, "title": str, "linkedin_url": str}` per person, `search(page: Page, query: str, limit: int) -> list[dict]`

Like the existing `LinkedInScraper`, this scraper's selectors are a best-effort approximation of LinkedIn's markup (the existing scrapers in this codebase are not verified against live pages either — `parse_listings` is unit-tested against a fixture; `search`'s Playwright interaction is exercised only by the (skipped-by-default) integration tests).

- [ ] **Step 1: Create the fixture HTML**

Create `tests/fixtures/linkedin_people_results.html`:

```html
<html><body>
<ul>
<li class="reusable-search__result-container">
  <div class="entity-result__title-text">
    <a href="https://www.linkedin.com/in/jane-doe-123?miniProfileUrn=x">
      <span>Jane Doe</span>
    </a>
  </div>
  <div class="entity-result__primary-subtitle">Engineering Manager at Acme Corp</div>
</li>
<li class="reusable-search__result-container">
  <div class="entity-result__title-text">
    <a href="https://www.linkedin.com/in/john-smith-456?miniProfileUrn=y">
      <span>John Smith</span>
    </a>
  </div>
  <div class="entity-result__primary-subtitle">Technical Recruiter at Acme Corp</div>
</li>
</ul>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_people_search_scraper.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestPeopleSearchScraper:
    def test_parse_listings_returns_normalized_dicts(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        scraper = PeopleSearchScraper()
        results = scraper.parse_listings(_read("linkedin_people_results.html"))
        assert len(results) == 2
        assert results[0]["name"] == "Jane Doe"
        assert results[0]["title"] == "Engineering Manager at Acme Corp"
        assert "linkedin.com/in/jane-doe-123" in results[0]["linkedin_url"]

    def test_parse_listings_second_result(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        scraper = PeopleSearchScraper()
        results = scraper.parse_listings(_read("linkedin_people_results.html"))
        assert results[1]["name"] == "John Smith"
        assert results[1]["title"] == "Technical Recruiter at Acme Corp"

    def test_parse_listings_empty_html_returns_empty_list(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        scraper = PeopleSearchScraper()
        assert scraper.parse_listings("<html><body></body></html>") == []

    def test_source_board_attribute(self):
        from careeros.browser.scrapers.people_search import PeopleSearchScraper
        assert PeopleSearchScraper().source_board == "linkedin_people"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_people_search_scraper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.browser.scrapers.people_search'`

- [ ] **Step 4: Implement PeopleSearchScraper**

Create `careeros/browser/scrapers/people_search.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_people_search_scraper.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
git add careeros/browser/scrapers/people_search.py tests/fixtures/linkedin_people_results.html tests/test_people_search_scraper.py
git commit -m "feat: add PeopleSearchScraper for LinkedIn people search"
```

---

### Task 3: Company + People Research Skills

**Files:**
- Create: `careeros/skills/company_research.py`
- Create: `careeros/skills/people_research.py`
- Test: `tests/test_company_research.py`
- Test: `tests/test_people_research.py`

**Interfaces:**
- Produces: `extract_company_info(page_content: str, model: str | None = None) -> dict` returning `{"industry": str | None, "size": str | None, "notes": str | None}`, never raises (returns all-`None` dict on failure)
- Produces: `classify_person_role(name: str, title: str, model: str | None = None) -> str` returning one of `"ic"`, `"em"`, `"recruiter"`, `"hiring_manager"`; falls back to `"ic"` on any failure (the safest default — an IC-framed message is the least presumptuous if classification fails)

- [ ] **Step 1: Write the failing tests for company_research**

Create `tests/test_company_research.py`:

```python
from unittest.mock import MagicMock, patch


def test_extract_company_info_parses_llm_json():
    from careeros.skills.company_research import extract_company_info
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = (
        '{"industry": "Software", "size": "51-200", "notes": "Series B startup"}'
    )
    with patch("careeros.skills.company_research.litellm.completion", return_value=mock_resp):
        result = extract_company_info("Acme Corp is a software company...")
    assert result["industry"] == "Software"
    assert result["size"] == "51-200"
    assert result["notes"] == "Series B startup"


def test_extract_company_info_handles_llm_failure():
    from careeros.skills.company_research import extract_company_info
    with patch("careeros.skills.company_research.litellm.completion", side_effect=Exception("boom")):
        result = extract_company_info("some content")
    assert result["industry"] is None
    assert result["size"] is None
    assert result["notes"] is None


def test_extract_company_info_handles_malformed_json():
    from careeros.skills.company_research import extract_company_info
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "not json at all"
    with patch("careeros.skills.company_research.litellm.completion", return_value=mock_resp):
        result = extract_company_info("some content")
    assert result["industry"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_company_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.skills.company_research'`

- [ ] **Step 3: Implement company_research.py**

Create `careeros/skills/company_research.py`:

```python
import json
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_CONTENT_CAP = 4000

_EXTRACT_INSTRUCTIONS = """\
Extract structured facts about a company from the page content below.
Return ONLY valid JSON — no markdown, no explanation.

{
  "industry": "<industry, or null if unknown>",
  "size": "<employee count range, e.g. '51-200', or null if unknown>",
  "notes": "<1-2 sentences of other relevant context, or null>"
}

Page content:
"""

_FAILURE = {"industry": None, "size": None, "notes": None}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_company_info(page_content: str, model: str | None = None) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    prompt = _EXTRACT_INSTRUCTIONS + page_content[:_CONTENT_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.choices[0].message.content)
        return {
            "industry": result.get("industry"),
            "size": result.get("size"),
            "notes": result.get("notes"),
        }
    except Exception:
        return dict(_FAILURE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_company_research.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing tests for people_research**

Create `tests/test_people_research.py`:

```python
from unittest.mock import MagicMock, patch


def test_classify_person_role_returns_llm_classification():
    from careeros.skills.people_research import classify_person_role
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "em"
    with patch("careeros.skills.people_research.litellm.completion", return_value=mock_resp):
        result = classify_person_role("Jane Doe", "Engineering Manager")
    assert result == "em"


def test_classify_person_role_normalizes_whitespace_and_case():
    from careeros.skills.people_research import classify_person_role
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "  RECRUITER  \n"
    with patch("careeros.skills.people_research.litellm.completion", return_value=mock_resp):
        result = classify_person_role("John Smith", "Technical Recruiter")
    assert result == "recruiter"


def test_classify_person_role_falls_back_to_ic_on_failure():
    from careeros.skills.people_research import classify_person_role
    with patch("careeros.skills.people_research.litellm.completion", side_effect=Exception("boom")):
        result = classify_person_role("Jane Doe", "Software Engineer")
    assert result == "ic"


def test_classify_person_role_falls_back_to_ic_on_unrecognized_output():
    from careeros.skills.people_research import classify_person_role
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "not a valid category"
    with patch("careeros.skills.people_research.litellm.completion", return_value=mock_resp):
        result = classify_person_role("Jane Doe", "Some Title")
    assert result == "ic"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_people_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.skills.people_research'`

- [ ] **Step 7: Implement people_research.py**

Create `careeros/skills/people_research.py`:

```python
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_VALID_CATEGORIES = ("ic", "em", "recruiter", "hiring_manager")

_CLASSIFY_INSTRUCTIONS = """\
Classify this person's role at their company into exactly one category:
ic, em, recruiter, or hiring_manager.
Return ONLY the category word, nothing else.

Name: """


def classify_person_role(name: str, title: str, model: str | None = None) -> str:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    prompt = _CLASSIFY_INSTRUCTIONS + name + "\nTitle: " + title
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}],
        )
        category = resp.choices[0].message.content.strip().lower()
        if category in _VALID_CATEGORIES:
            return category
        return "ic"
    except Exception:
        return "ic"
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_people_research.py -v`
Expected: PASS (4 tests)

- [ ] **Step 9: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 10: Commit**

```bash
git add careeros/skills/company_research.py careeros/skills/people_research.py tests/test_company_research.py tests/test_people_research.py
git commit -m "feat: add company and people research skills"
```

---

### Task 4: Outreach Draft Skill

**Files:**
- Create: `careeros/skills/outreach_draft.py`
- Test: `tests/test_outreach_draft.py`

**Interfaces:**
- Consumes: `Person`, `Job`, `Company`, `Profile`, `Goals` from `careeros.core.models` (Task 1 for `Person`/`Company`, existing for `Job`/`Profile`/`Goals`)
- Produces: `generate_outreach_message(person: Person, job: Job, company: Company, profile: Profile, goals: Goals, model: str | None = None) -> str`, returns `""` on failure (never raises), matching `generate_cover_letter`'s exact contract

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outreach_draft.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from careeros.core.models import Company, Goals, Job, Person, Profile


def _make_job():
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-abc1", source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now,
    )


def _make_company():
    return Company(id="acme-corp", name="Acme Corp", industry="Software", researched_at="2026-09-19T00:00:00Z")


def _make_person(role_category="ic"):
    return Person(
        id="acme-corp-jane-doe", company_id="acme-corp", name="Jane Doe",
        role_category=role_category, title="Senior Engineer", researched_at="2026-09-19T00:00:00Z",
    )


def _make_profile():
    return Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE")


class TestGenerateOutreachMessage:
    def test_returns_generated_text_for_ic(self):
        from careeros.skills.outreach_draft import generate_outreach_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Hi Jane, I noticed we both..."
        with patch("careeros.skills.outreach_draft.litellm.completion", return_value=mock_resp):
            result = generate_outreach_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals()
            )
        assert result == "Hi Jane, I noticed we both..."

    def test_uses_different_instructions_per_role_category(self):
        from careeros.skills.outreach_draft import generate_outreach_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        captured_prompts = []

        def _capture(*args, **kwargs):
            captured_prompts.append(kwargs["messages"][0]["content"])
            return mock_resp

        with patch("careeros.skills.outreach_draft.litellm.completion", side_effect=_capture):
            generate_outreach_message(_make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals())
            generate_outreach_message(_make_person("recruiter"), _make_job(), _make_company(), _make_profile(), Goals())

        assert captured_prompts[0] != captured_prompts[1]

    def test_returns_empty_string_on_failure(self):
        from careeros.skills.outreach_draft import generate_outreach_message
        with patch("careeros.skills.outreach_draft.litellm.completion", side_effect=Exception("boom")):
            result = generate_outreach_message(
                _make_person("em"), _make_job(), _make_company(), _make_profile(), Goals()
            )
        assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_outreach_draft.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.skills.outreach_draft'`

- [ ] **Step 3: Implement outreach_draft.py**

Create `careeros/skills/outreach_draft.py`:

```python
import os
import litellm
from careeros.core.models import Company, Goals, Job, Person, Profile

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

_INSTRUCTIONS_BY_ROLE = {
    "ic": (
        "Write a concise, peer-to-peer outreach email (2-3 short paragraphs) from one engineer to another.\n"
        "Reference shared technical interest, not a job pitch. Close with a low-pressure ask to connect briefly.\n"
    ),
    "em": (
        "Write a concise outreach email (2-3 short paragraphs) from a job candidate to a hiring/engineering manager.\n"
        "Explain briefly why the candidate is a strong fit for their team, referencing the role and company.\n"
        "Close with a request for a brief conversation.\n"
    ),
    "hiring_manager": (
        "Write a concise outreach email (2-3 short paragraphs) from a job candidate to a hiring manager.\n"
        "Explain briefly why the candidate is a strong fit for their team, referencing the role and company.\n"
        "Close with a request for a brief conversation.\n"
    ),
    "recruiter": (
        "Write a concise, direct outreach email (2-3 short paragraphs) from a job candidate to a recruiter.\n"
        "State clear interest in the specific role and briefly highlight fit. Close with availability for a call.\n"
    ),
}

_COMMON_SUFFIX = (
    "Return ONLY the email body — no subject line, no markdown, no commentary.\n\n"
)


def _build_context_text(person: Person, job: Job, company: Company, profile: Profile, goals: Goals) -> str:
    lines = []
    if person.title:
        lines.append("Recipient: " + person.name + " (" + person.title + ")")
    else:
        lines.append("Recipient: " + person.name)
    lines.append("Company: " + company.name)
    if company.industry:
        lines.append("Industry: " + company.industry)
    lines.append("Job: " + job.title)
    if profile.title:
        lines.append("Candidate title: " + profile.title)
    if profile.summary:
        lines.append("Candidate summary: " + profile.summary)
    if goals.short_term:
        lines.append("Candidate goals: " + "; ".join(goals.short_term))
    return "\n".join(lines)


def generate_outreach_message(
    person: Person, job: Job, company: Company, profile: Profile, goals: Goals, model: str | None = None,
) -> str:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    instructions = _INSTRUCTIONS_BY_ROLE.get(person.role_category, _INSTRUCTIONS_BY_ROLE["ic"])
    context_text = _build_context_text(person, job, company, profile, goals)
    prompt = instructions + _COMMON_SUFFIX + context_text
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content
        if not content:
            return ""
        return content.strip()
    except Exception:
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_outreach_draft.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add careeros/skills/outreach_draft.py tests/test_outreach_draft.py
git commit -m "feat: add role-aware outreach message drafting skill"
```

---

### Task 5: Mailer

**Files:**
- Create: `careeros/mailer.py`
- Test: `tests/test_mailer.py`

**Interfaces:**
- Produces: `send_email(to_address: str, subject: str, body: str) -> None` — raises `KeyError` if any `CAREEROS_SMTP_*` env var is missing, raises whatever `smtplib` raises on connection/auth failure, returns `None` on success

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mailer.py`:

```python
import smtplib
from unittest.mock import MagicMock, patch

import pytest


def test_send_email_success(monkeypatch):
    from careeros.mailer import send_email
    monkeypatch.setenv("CAREEROS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PORT", "587")
    monkeypatch.setenv("CAREEROS_SMTP_USER", "me@example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PASSWORD", "secret")

    mock_server = MagicMock()
    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server
    mock_smtp_cls.return_value.__exit__.return_value = False

    with patch("careeros.mailer.smtplib.SMTP", mock_smtp_cls):
        send_email("jane@acme.com", "Hello", "Body text")

    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("me@example.com", "secret")
    mock_server.sendmail.assert_called_once()
    call_args = mock_server.sendmail.call_args[0]
    assert call_args[0] == "me@example.com"
    assert call_args[1] == ["jane@acme.com"]
    assert "Body text" in call_args[2]


def test_send_email_missing_env_var_raises_key_error(monkeypatch):
    from careeros.mailer import send_email
    monkeypatch.delenv("CAREEROS_SMTP_HOST", raising=False)
    monkeypatch.delenv("CAREEROS_SMTP_PORT", raising=False)
    monkeypatch.delenv("CAREEROS_SMTP_USER", raising=False)
    monkeypatch.delenv("CAREEROS_SMTP_PASSWORD", raising=False)
    with pytest.raises(KeyError):
        send_email("jane@acme.com", "Hello", "Body text")


def test_send_email_smtp_failure_propagates(monkeypatch):
    from careeros.mailer import send_email
    monkeypatch.setenv("CAREEROS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PORT", "587")
    monkeypatch.setenv("CAREEROS_SMTP_USER", "me@example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PASSWORD", "wrong")

    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")

    with patch("careeros.mailer.smtplib.SMTP", mock_smtp_cls):
        with pytest.raises(smtplib.SMTPAuthenticationError):
            send_email("jane@acme.com", "Hello", "Body text")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mailer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.mailer'`

- [ ] **Step 3: Implement mailer.py**

Create `careeros/mailer.py`:

```python
from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText


def send_email(to_address: str, subject: str, body: str) -> None:
    host = os.environ["CAREEROS_SMTP_HOST"]
    port = int(os.environ["CAREEROS_SMTP_PORT"])
    user = os.environ["CAREEROS_SMTP_USER"]
    password = os.environ["CAREEROS_SMTP_PASSWORD"]

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to_address

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_address], message.as_string())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mailer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add careeros/mailer.py tests/test_mailer.py
git commit -m "feat: add SMTP mailer for outreach sending"
```

---

### Task 6: research Command (company + people)

**Files:**
- Create: `careeros/cli/research_cmd.py`
- Modify: `careeros/cli/main.py` (wire in the new command)
- Test: `tests/test_research_cmd.py`

**Interfaces:**
- Consumes: `Company`, `Person` from `careeros.core.models` (Task 1)
- Consumes: `make_company_id`, `make_person_id` from `careeros.core.ids` (Task 1)
- Consumes: `PeopleSearchScraper` from `careeros.browser.scrapers.people_search` (Task 2)
- Consumes: `extract_company_info`, `classify_person_role` from `careeros.skills.company_research`/`careeros.skills.people_research` (Task 3)
- Consumes: `fetch_jd_text`, `launch_browser` from `careeros.browser.driver` (existing) — `fetch_jd_text` reused for company-page content, not `Scraper.search`
- Consumes: `open_local_runtime` from `careeros.runtime.factory` (Phase 5)
- Produces: `research_app` — a Typer app with two commands (`company`, `people`), wired into `careeros/cli/main.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_cmd.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.research_cmd import research_app
from careeros.core.ids import make_company_id, make_person_id
from careeros.core.models import Company, Job, Person
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _setup_workspace(tmp_path, with_job=True):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    if with_job:
        now = datetime.now(timezone.utc).isoformat()
        Job(
            id="acme-sre-abc1", source="browse", url="https://example.com/job",
            company="Acme Corp", title="Senior SRE", stage="saved",
            created_at=now, updated_at=now,
        ).save(storage)
    return str(tmp_path)


class TestResearchCompany:
    def test_job_not_found_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_job=False)
        result = runner.invoke(research_app, ["company", "--job", "nonexistent", "--workspace", ws_path])
        assert result.exit_code == 1

    def test_researches_and_saves_company(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value="Acme Corp page content"), \
             patch("careeros.cli.research_cmd.extract_company_info", return_value={"industry": "Software", "size": "51-200", "notes": "Series B"}):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["company", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        company_id = make_company_id("Acme Corp")
        company = Company.load(storage, company_id)
        assert company.industry == "Software"
        assert company.size == "51-200"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "company_researched" in log_content


class TestResearchPeople:
    def test_job_not_found_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_job=False)
        result = runner.invoke(research_app, ["people", "--job", "nonexistent", "--workspace", ws_path])
        assert result.exit_code == 1

    def test_researches_and_saves_people(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        mock_scraper = MagicMock()
        mock_scraper.search.return_value = [
            {"name": "Jane Doe", "title": "Engineering Manager", "linkedin_url": "https://linkedin.com/in/jane"},
        ]
        with patch("careeros.cli.research_cmd.PeopleSearchScraper", return_value=mock_scraper), \
             patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.classify_person_role", return_value="em"):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["people", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        company_id = make_company_id("Acme Corp")
        person = Person.load(storage, make_person_id("Jane Doe", company_id))
        assert person.role_category == "em"
        assert person.title == "Engineering Manager"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "people_researched" in log_content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_research_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.cli.research_cmd'`

- [ ] **Step 3: Implement research_cmd.py**

Create `careeros/cli/research_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint

from careeros.browser.driver import fetch_jd_text, launch_browser
from careeros.browser.scrapers.people_search import PeopleSearchScraper
from careeros.config import GlobalConfig
from careeros.core.ids import make_company_id, make_person_id
from careeros.core.models import Company, Job, Person
from careeros.runtime.factory import open_local_runtime
from careeros.skills.company_research import extract_company_info
from careeros.skills.people_research import classify_person_role
from careeros.storage.filesystem import LocalFilesystemStorage

research_app = typer.Typer(help="Research companies and people for outreach.")


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


@research_app.command()
def company(
    job: str = typer.Option(..., "--job", help="Job ID to research the company for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    company_id = make_company_id(job_obj.company)
    company_search_url = "https://www.linkedin.com/search/results/companies/?keywords=" + job_obj.company

    try:
        with launch_browser(headless=True) as (_, page):
            page_content = fetch_jd_text(page, company_search_url)
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        raise typer.Exit(1)

    info = extract_company_info(page_content)
    company_obj = Company(
        id=company_id, name=job_obj.company,
        industry=info["industry"], size=info["size"], notes=info["notes"],
        researched_at=_now(),
    )
    company_obj.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "company_researched", "research",
        "Researched company: " + job_obj.company,
        entity_type="company", entity_id=company_id,
    ))
    rprint("[green]Researched " + job_obj.company + "[/green]")


@research_app.command()
def people(
    job: str = typer.Option(..., "--job", help="Job ID to research people for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    company_id = make_company_id(job_obj.company)
    scraper = PeopleSearchScraper()

    try:
        with launch_browser(headless=True) as (_, page):
            try:
                results = scraper.search(page, job_obj.company, 10)
            except Exception as exc:
                rprint("[yellow]Warning: could not search people: " + str(exc) + "[/yellow]")
                results = []
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        raise typer.Exit(1)

    found = 0
    for r in results:
        role_category = classify_person_role(r["name"], r["title"])
        person_id = make_person_id(r["name"], company_id)
        person_obj = Person(
            id=person_id, company_id=company_id, name=r["name"],
            role_category=role_category, title=r["title"], linkedin_url=r.get("linkedin_url"),
            researched_at=_now(),
        )
        person_obj.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "people_researched", "research",
            "Found " + r["name"] + " (" + role_category + ") at " + job_obj.company,
            entity_type="person", entity_id=person_id,
        ))
        found += 1

    rprint("[green]Found " + str(found) + " people[/green]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_research_cmd.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire the command into main.py**

Modify `careeros/cli/main.py` — add this import alongside the existing ones:

```python
from careeros.cli.research_cmd import research_app
```

Then add this line alongside the existing `app.add_typer(...)` calls:

```python
app.add_typer(research_app, name="research")
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/research_cmd.py careeros/cli/main.py tests/test_research_cmd.py
git commit -m "feat: add research company/people commands"
```

---

### Task 7: outreach Command (draft, approve, send, referral state, email update)

**Files:**
- Create: `careeros/cli/outreach_cmd.py`
- Modify: `careeros/cli/main.py` (wire in the new command)
- Test: `tests/test_outreach_cmd.py`

**Interfaces:**
- Consumes: `Company`, `Person`, `OutreachMessage` from `careeros.core.models` (Task 1)
- Consumes: `generate_outreach_message` from `careeros.skills.outreach_draft` (Task 4)
- Consumes: `send_email` from `careeros.mailer` (Task 5)
- Consumes: `open_local_runtime` from `careeros.runtime.factory` (Phase 5)
- Consumes: `ActionProposal` from `careeros.runtime.base` (Phase 5)
- Produces: `outreach_app` — a Typer app with three commands (`send` as the default/main command, `mark-referral-requested`, and a separate `people_app` with `update` — see note below), wired into `careeros/cli/main.py`

**Note on command grouping:** the spec describes `careeros outreach --job <id> --person <id>` (draft+send) and `careeros outreach mark-referral-requested --job <id> --person <id>` as subcommands of one `outreach` group, and `careeros people update <id> --email <address>` as a separate top-level command under the existing `people` namespace. Since `careeros/cli/job_cmd.py` already establishes the pattern of a `job_app` Typer group with subcommands (e.g. `job update`), this task creates `outreach_app` (subcommands: `send`, `mark-referral-requested`) and a small separate `people_app` (subcommand: `update`) in the same file, both wired into `main.py`.

**Note on the approval gate:** `send` MUST route the send decision through `runtime.request_approval(ActionProposal(...))`, exactly as `apply_cmd` does — never call `Confirm.ask` directly from `outreach_cmd.py`. This is the whole reason `AgentRuntime` exists (Phase 5): so the same command works interactively (`LocalRuntime` blocks on `Confirm.ask` internally) or unattended (a future `ClaudeCodeRuntime`/`AutomationRuntime` caller supplies its own resolution) without the CLI code caring which. Because `send` loads `Job`, `Person`, and `Company` from the same storage object with three different expected JSON shapes, its tests use real `LocalFilesystemStorage` (the Phase 6 precedent — a single mocked `storage.read.return_value` cannot serve three different load calls correctly), which means `runtime` is a real `LocalRuntime`, and `request_approval` really calls `Confirm.ask` internally inside `careeros/runtime/local.py`. Tests therefore patch `careeros.runtime.local.Confirm.ask` (not `careeros.cli.outreach_cmd.Confirm.ask` — the CLI module never imports `Confirm` at all), matching the exact pattern already exercised by `tests/test_runtime.py`'s own `LocalRuntime` tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outreach_cmd.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.outreach_cmd import outreach_app, people_app
from careeros.core.models import Company, Job, OutreachMessage, Person, Profile
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
MESSAGE_ID = JOB_ID + "__" + PERSON_ID


def _setup_workspace(tmp_path, with_email=True):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(
        id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now,
    ).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(
        id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
        title="Engineering Manager", email=("jane@acme.com" if with_email else None),
        researched_at=now,
    ).save(storage)
    return str(tmp_path)


class TestOutreachSend:
    def test_declined_approval_logs_declined_and_does_not_send(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=False), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "declined"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_declined" in log_content

    def test_approved_with_email_sends_and_logs_sent(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=True)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert call_args[0] == "jane@acme.com"
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "sent"
        assert message.sent_at is not None
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_approved" in log_content
        assert "outreach_sent" in log_content

    def test_approved_without_email_blocks_send_and_does_not_log_failed(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=False)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        mock_send.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_approved" in log_content
        assert "outreach_send_failed" not in log_content

    def test_smtp_failure_logs_send_failed(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=True)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email", side_effect=Exception("smtp error")):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "failed"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_failed" in log_content

    def test_draft_generation_failure_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value=""):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])
        assert result.exit_code == 1


class TestMarkReferralRequested:
    def test_sets_referral_state_and_logs(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(
            id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
            draft_text="Hi Jane...", send_state="sent", created_at=now,
        ).save(storage)

        result = runner.invoke(outreach_app, ["mark-referral-requested", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.referral_state == "referral_requested"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "referral_requested" in log_content


class TestPeopleUpdate:
    def test_updates_email(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=False)
        result = runner.invoke(people_app, ["update", PERSON_ID, "--email", "jane@acme.com", "--workspace", ws_path])
        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        person = Person.load(storage, PERSON_ID)
        assert person.email == "jane@acme.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_outreach_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.cli.outreach_cmd'`

- [ ] **Step 3: Implement outreach_cmd.py**

Create `careeros/cli/outreach_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.config import GlobalConfig
from careeros.core.models import Company, Goals, Job, OutreachMessage, Person, Profile
from careeros.mailer import send_email
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_local_runtime
from careeros.skills.outreach_draft import generate_outreach_message
from careeros.storage.filesystem import LocalFilesystemStorage

outreach_app = typer.Typer(help="Draft, approve, and send outreach messages.")
people_app = typer.Typer(help="Manage researched people.")
console = Console()

MAX_REGENERATIONS = 5


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


@outreach_app.command()
def send(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
        person_obj = Person.load(runtime.storage, person)
        company_obj = Company.load(runtime.storage, person_obj.company_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job, person, or company not found.[/red]")
        raise typer.Exit(1)

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    draft_text = generate_outreach_message(person_obj, job_obj, company_obj, profile, goals, model=model)
    if not draft_text:
        rprint("[red]Outreach message generation failed.[/red]")
        raise typer.Exit(1)

    regenerations = 0
    while True:
        console.print(Panel(draft_text, title="Outreach to " + person_obj.name))
        if regenerations >= MAX_REGENERATIONS:
            choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
        else:
            choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")
        if choice == "q":
            rprint("Aborted.")
            raise typer.Exit(0)
        if choice == "r":
            regenerations += 1
            draft_text = generate_outreach_message(person_obj, job_obj, company_obj, profile, goals, model=model)
            if not draft_text:
                rprint("[red]Outreach message generation failed.[/red]")
                raise typer.Exit(1)
            continue
        break

    message_id = job + "__" + person
    message = OutreachMessage(
        id=message_id, job_id=job, person_id=person, draft_text=draft_text, created_at=_now(),
    )
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_drafted", "outreach",
        "Drafted outreach to " + person_obj.name + " re: " + job_obj.company + " — " + job_obj.title,
        entity_type="outreach_message", entity_id=message_id,
    ))

    result = runtime.request_approval(ActionProposal(
        action="send_outreach",
        summary="Send outreach email to " + person_obj.name + " re: " + job_obj.company + " — " + job_obj.title + "?",
        entity_type="outreach_message", entity_id=message_id,
    ))
    if not result.approved:
        message = message.model_copy(update={"send_state": "declined"})
        message.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "outreach_send_declined", "outreach",
            "Send declined for outreach to " + person_obj.name,
            entity_type="outreach_message", entity_id=message_id,
        ))
        rprint("Aborted.")
        raise typer.Exit(0)

    runtime.record_activity(runtime.new_event(
        "outreach_send_approved", "outreach",
        "Send approved for outreach to " + person_obj.name,
        entity_type="outreach_message", entity_id=message_id,
    ))

    if not person_obj.email:
        rprint(
            "[red]No email on file for " + person_obj.name
            + ". Run 'careeros people update " + person + " --email <address>' and retry.[/red]"
        )
        raise typer.Exit(1)

    subject = "Regarding " + job_obj.title + " at " + job_obj.company
    try:
        send_email(person_obj.email, subject, draft_text)
    except Exception as exc:
        message = message.model_copy(update={"send_state": "failed"})
        message.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "outreach_send_failed", "outreach",
            "Send failed for outreach to " + person_obj.name + ": " + type(exc).__name__,
            status="failed", entity_type="outreach_message", entity_id=message_id,
        ))
        rprint("[red]Send failed: " + str(exc) + "[/red]")
        raise typer.Exit(1)

    message = message.model_copy(update={"send_state": "sent", "sent_at": _now()})
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_sent", "outreach",
        "Sent outreach to " + person_obj.name + " (" + person_obj.email + ")",
        entity_type="outreach_message", entity_id=message_id,
    ))
    rprint("[green]Sent to " + person_obj.name + "[/green]")


@outreach_app.command(name="mark-referral-requested")
def mark_referral_requested(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    message_id = job + "__" + person
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]No outreach message found for this job/person pair.[/red]")
        raise typer.Exit(1)

    message = message.model_copy(update={"referral_state": "referral_requested"})
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "referral_requested", "outreach",
        "Referral requested for job " + job,
        entity_type="outreach_message", entity_id=message_id,
    ))
    rprint("[green]Referral state updated.[/green]")


@people_app.command()
def update(
    person_id: str = typer.Argument(..., help="Person ID"),
    email: str = typer.Option(..., "--email", help="Email address to set"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        person_obj = Person.load(runtime.storage, person_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Person " + person_id + " not found.[/red]")
        raise typer.Exit(1)

    person_obj = person_obj.model_copy(update={"email": email})
    person_obj.save(runtime.storage)
    rprint("[green]Updated email for " + person_obj.name + "[/green]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_outreach_cmd.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Wire the commands into main.py**

Modify `careeros/cli/main.py` — add this import alongside the existing ones:

```python
from careeros.cli.outreach_cmd import outreach_app, people_app
```

Then add these lines alongside the existing `app.add_typer(...)` calls:

```python
app.add_typer(outreach_app, name="outreach")
app.add_typer(people_app, name="people")
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/outreach_cmd.py careeros/cli/main.py tests/test_outreach_cmd.py
git commit -m "feat: add outreach send/mark-referral-requested and people update commands"
```
