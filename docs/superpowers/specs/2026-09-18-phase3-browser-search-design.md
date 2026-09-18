# CareerOS Phase 3 — Browser-Driven Job Search Design

**Goal:** Add an autonomous browser agent that searches job boards using your real Chrome session, scores discovered jobs 1-100 against your stored profile via LLM, and saves picks to the workspace pipeline.

**Architecture:** A new `careeros/browser/` package provides a Playwright persistent-context driver (reuses your logged-in Chrome profile) and per-board scrapers behind a shared protocol. A new `careeros/skills/job_score.py` scores each job against your profile via LiteLLM. A new `careeros browse` CLI command orchestrates the full flow. No existing files are structurally changed.

**Tech stack:** Python 3.11+, Playwright (new dep, `playwright` + `playwright install chrome`), LiteLLM (existing), Pydantic v2, Typer + Rich.

**Phase context:**
- Phase 1: workspace, profile extraction, export/import.
- Phase 2: job pipeline — manual add, ATS search (Greenhouse/Lever), stage tracking.
- Phase 3 (this): browser automation — agent searches job boards, scores against profile.
- Phase 4 (planned): auto-apply — agent fills forms, uploads tailored documents.

---

## Global Constraints

- Python 3.11+ — `str | None` union syntax; no walrus operator in type annotations.
- Pydantic v2 — `model_validate`, `model_dump_json`, `model_validate_json`, `model_copy` only.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/skills/`, `careeros/sources/`, or `careeros/browser/`.
- LLM calls via `litellm.completion()` only. Model: `os.environ.get("CAREEROS_MODEL", "claude-haiku-4-5-20251001")`.
- Prompts: instruction string + concatenated user text. No `.format()` or f-strings with user data.
- Activity log is append-only. No event is ever edited or deleted.
- New runtime dependency: `playwright>=1.40`. One-time browser install: `playwright install chrome`.
- Offline unit tests: `litellm.completion`, `launch_browser`, `scraper.search`, and `score_job` are all mocked. Integration tests (live browser) are marked `@pytest.mark.integration` and skipped by default.
- No traceback ever reaches the user.

---

## 1. Browser Driver

### 1.1 `careeros/browser/driver.py`

Manages the Playwright session. Uses a **persistent context** pointing at the user's real Chrome profile so all existing logins (LinkedIn, Indeed, Wellfound) are live without storing credentials.

```python
from playwright.sync_api import sync_playwright, BrowserContext, Page
from contextlib import contextmanager

def get_chrome_profile_path() -> str:
    """Return platform-appropriate Chrome user data directory."""
    # macOS:   ~/Library/Application Support/Google/Chrome
    # Linux:   ~/.config/google-chrome
    # Windows: %LOCALAPPDATA%\Google\Chrome\User Data

@contextmanager
def launch_browser(headless: bool = False):
    """
    Context manager yielding (context, page).
    Uses system Chrome binary (channel="chrome") with the user's real profile.
    headless=False by default so the user can see progress and solve CAPTCHAs.
    """
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
```

**`channel="chrome"`** uses the system Chrome binary — the same one where the user is already logged in — not Playwright's separate Chromium download.

**Error handling:** If Playwright is not installed, `ImportError` is caught in `browse_cmd.py` and a friendly install message is printed (`pip install playwright && playwright install chrome`).

---

## 2. Scraper Interface

### 2.1 `careeros/browser/scrapers/base.py`

```python
from typing import Protocol
from playwright.sync_api import Page

class Scraper(Protocol):
    source_board: str  # "linkedin" | "indeed" | "wellfound" | "generic"

    def search(self, page: Page, query: str, limit: int) -> list[dict]:
        """
        Navigate to the board, enter the query, extract up to `limit` job cards.
        Returns list of normalized posting dicts (see §2.2).
        Does NOT fetch the JD body — listing data only.
        """

    def parse_listings(self, html: str) -> list[dict]:
        """
        Pure function: parse HTML of a results page → list of posting dicts.
        Exists separately from search() so unit tests can feed HTML fixtures
        without a live browser.
        """
```

**Normalized posting dict** (returned by every scraper):
```python
{
    "source_board": str,   # "linkedin" | "indeed" | "wellfound" | "generic"
    "title": str,
    "company": str,
    "location": str | None,
    "url": str,            # link to the full JD page
}
```

### 2.2 Per-board scrapers

**`careeros/browser/scrapers/linkedin.py`**
- Navigates to `https://www.linkedin.com/jobs/search/?keywords=<query>`
- Scrolls results panel to load up to `limit` cards
- Extracts: job title, company name, location, job detail URL from each card

**`careeros/browser/scrapers/indeed.py`**
- Navigates to `https://www.indeed.com/jobs?q=<query>`
- Paginates until `limit` reached (25 results/page)
- Extracts: job title, company, location, URL from each card

**`careeros/browser/scrapers/wellfound.py`**
- Navigates to `https://wellfound.com/jobs?q=<query>`
- Scrolls infinite feed to load `limit` results
- Extracts: job title, company, location, URL from each card

**`careeros/browser/scrapers/generic.py`**
- Receives a full URL directly (no query construction)
- Loads the page, extracts all `<a>` hrefs that match common job-URL patterns (contain `/jobs/`, `/careers/`, `/positions/`, `/openings/`)
- Returns up to `limit` links as posting dicts with `title` extracted from link text or page `<title>`

### 2.3 JD body extraction

After listing extraction, the driver opens each posting URL and extracts body text using the existing `_HTMLStripper` from `careeros/sources/ats.py` (re-exported or imported directly). Body text is capped at 4000 chars before being passed to the scorer.

```python
def fetch_jd_text(page: Page, url: str) -> str:
    """Navigate to url, extract and return plain-text body (4000-char cap)."""
```

This lives in `careeros/browser/driver.py`.

---

## 3. Query Construction

### 3.1 `careeros/skills/browse_query.py`

```python
def job_query_from_profile(profile: Profile, goals: Goals) -> str:
    """
    Build a job board search query from the user's profile.
    Simple string join — no LLM call.
    Example: "Senior SRE Kubernetes platform reliability"
    """
```

Logic: `profile.title` + top-3 goal keywords extracted from `goals.items` (split on whitespace, deduplicate, take first 3). Returns a space-joined string, max 80 chars.

---

## 4. LLM Scoring

### 4.1 `careeros/skills/job_score.py`

```python
DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

def score_job(
    jd_text: str,
    profile: Profile,
    skills: Skills,
    model: str | None = None,
) -> dict:
    """
    Score a job description against the candidate's profile.
    Returns:
    {
        "score": int,           # 1-100 (100 = perfect match)
        "reasoning": str,       # 1-2 sentence explanation
        "strengths": list[str], # where the candidate fits well
        "gaps": list[str],      # where they fall short
    }
    On failure: {"score": 0, "reasoning": "Could not score.", "strengths": [], "gaps": []}
    Never raises.
    """
```

**Prompt construction:** Instruction string + concatenated profile text + JD text. No f-strings with user data.

```python
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
# Prompt = _SCORE_INSTRUCTIONS + profile_text + "\n\nJob description:\n" + jd_text[:4000]
```

**Profile text** built by `_build_profile_text(profile, skills) -> str`:
- `f"Title: {profile.title}"` (plain concatenation, not f-string with user data in prompt)
- Skills list joined with commas
- Goals text appended

**`_parse_json`** defined locally (same 5-line helper as `job_extract.py` — strips markdown fences, then `json.loads`).

**Cost at scale:** `--limit 20` → 20 LLM calls. With `claude-haiku-4-5-20251001` ≈ $0.002 total. Zero cost on Ollama.

---

## 5. CLI Command

### 5.1 `careeros/cli/browse_cmd.py`

```
careeros browse
  --board     TEXT   linkedin | indeed | wellfound | url  [required]
  --url       TEXT   Target URL (only with --board url)
  --limit     INT    Max job listings to fetch  [default: 20]
  --min-score INT    Only show jobs scoring at or above this  [default: 0]
  --headless         Run browser headlessly (default: visible)
  --workspace TEXT   Override workspace path
```

**Full command flow:**

1. Load workspace: `profile`, `skills`, `goals` via `StorageProvider`
2. Build search query: `job_query_from_profile(profile, goals)`
3. Check Playwright import; if missing → `"Run: pip install playwright && playwright install chrome"`, exit 1
4. `with launch_browser(headless=headless) as (ctx, page):`
5. Run scraper: `scraper.search(page, query, limit)` → list of posting dicts
6. For each posting (progress bar via Rich):
   - `fetch_jd_text(page, posting["url"])` → jd_text
   - `score_job(jd_text, profile, skills)` → score dict
   - Attach score to posting
7. Filter: `[p for p in postings if p["score"] >= min_score]`
8. Sort descending by score
9. Display Rich table: `#  score  company  title  location  url`
10. If no results after filter → `"No jobs found matching min-score {min_score}."`, return
11. `Prompt.ask("Pick jobs to save (e.g. 1 3 5, or q to quit):")`
12. For each picked posting:
    - `job_id = make_job_id(posting["company"], posting["title"])`
    - `Job(source=posting["source_board"], source_id=None, url=posting["url"], ...)`
    - `job.save(storage)` 
    - `logger.log(logger.new_event("job_added", "browse", ..., entity_type="job", entity_id=job_id))`
13. `rprint(f"Saved {saved} job(s)")`

**Wire into `careeros/cli/main.py`:**
```python
from careeros.cli.browse_cmd import browse_app
app.add_typer(browse_app, name="browse")
```

### 5.2 Error handling

| Scenario | Behaviour |
|---|---|
| Playwright not installed | Print install instructions, exit 1 |
| No workspace configured | "No workspace configured. Run `careeros onboard` first.", exit 1 |
| Scraper fails (login expired, CAPTCHA, layout change) | Print warning with board name + URL, skip that board, continue with any collected results |
| JD fetch fails for a posting | `jd_text = ""`, score defaults to 0, posting still shown |
| LLM scoring fails for a posting | `{"score": 0, "reasoning": "Could not score.", ...}`, posting still shown |
| `--board url` missing `--url` | "Provide --url when using --board url.", exit 1 |
| No profile in workspace | "No profile found. Run `careeros onboard` first.", exit 1 |

No traceback ever reaches the user.

---

## 6. Testing

### 6.1 `tests/test_job_score.py` (~10 tests, offline)

- Full scoring: LLM returns valid JSON → all fields populated, score is int
- Failure fallback: `litellm.completion` raises → returns `{"score": 0, ...}`
- Bad JSON: LLM returns non-JSON → returns failure dict
- Fenced response: ` ```json\n{...}\n``` ` → `_parse_json` strips fences
- `CAREEROS_MODEL` env var respected
- Model param overrides env var
- Profile text included in prompt (concatenation, not f-string)
- JD text capped at 4000 chars in prompt

### 6.2 `tests/test_scrapers.py` (~10 tests, offline)

Uses pre-saved HTML fixture strings (in `tests/fixtures/`). Tests `parse_listings(html)` pure function per board:

- LinkedIn: parses fixture HTML → returns normalized dicts with title/company/url
- LinkedIn: empty results page → returns `[]`
- Indeed: parses fixture HTML → returns normalized dicts
- Wellfound: parses fixture HTML → returns normalized dicts
- Generic: extracts job-pattern hrefs from fixture HTML
- Generic: page with no job-pattern links → returns `[]`

### 6.3 `tests/test_browse_cmd.py` (~12 tests, offline)

Typer CliRunner tests with mocked browser, scraper, and scorer:

- `browse --board linkedin` end-to-end: mocked scraper returns 2 postings, scorer returns scores, user picks 1 → 1 job saved, `job_added` logged
- `browse --min-score 80`: postings with score < 80 filtered out
- `browse --min-score 80` all below threshold: prints "No jobs found"
- `browse` Playwright not installed: prints install instructions, exit 1
- `browse --board url` missing `--url`: exit 1
- `browse` no workspace: exit 1
- `browse` scraper raises: warning printed, proceeds with empty results
- `browse` JD fetch fails: posting shown with score 0
- `browse` user quits with `q`: 0 jobs saved
- `browse --headless`: `launch_browser` called with `headless=True`
- `browse` activity log contains `job_added` after save
- `browse --board indeed`: indeed scraper called (not linkedin)

### 6.4 `tests/integration/test_live_browser.py` (marked `@pytest.mark.integration`)

- `test_linkedin_search_returns_results` — opens real Chrome, searches LinkedIn, asserts ≥ 1 result
- `test_indeed_search_returns_results` — same for Indeed

### 6.5 `pyproject.toml` addition

```toml
[tool.pytest.ini_options]
markers = ["integration: requires live browser and network (skipped by default)"]
```

Default run (`pytest tests/ -q`) skips integration tests automatically.

---

## 7. File Map

| Path | Action |
|---|---|
| `careeros/browser/__init__.py` | New — empty |
| `careeros/browser/driver.py` | New — `launch_browser`, `get_chrome_profile_path`, `fetch_jd_text` |
| `careeros/browser/scrapers/__init__.py` | New — empty |
| `careeros/browser/scrapers/base.py` | New — `Scraper` Protocol, normalized dict shape |
| `careeros/browser/scrapers/linkedin.py` | New — `LinkedInScraper` |
| `careeros/browser/scrapers/indeed.py` | New — `IndeedScraper` |
| `careeros/browser/scrapers/wellfound.py` | New — `WellfoundScraper` |
| `careeros/browser/scrapers/generic.py` | New — `GenericScraper` |
| `careeros/skills/browse_query.py` | New — `job_query_from_profile` |
| `careeros/skills/job_score.py` | New — `score_job`, `_parse_json`, `_build_profile_text` |
| `careeros/cli/browse_cmd.py` | New — `browse_app`, `browse_cmd` |
| `careeros/cli/main.py` | Modify — register `browse_app` |
| `pyproject.toml` | Modify — add `playwright>=1.40`, pytest markers |
| `tests/test_job_score.py` | New |
| `tests/test_scrapers.py` | New |
| `tests/test_browse_cmd.py` | New |
| `tests/fixtures/linkedin_results.html` | New — HTML fixture |
| `tests/fixtures/indeed_results.html` | New — HTML fixture |
| `tests/fixtures/wellfound_results.html` | New — HTML fixture |
| `tests/fixtures/generic_jobs_page.html` | New — HTML fixture |
| `tests/integration/test_live_browser.py` | New |
| `tests/integration/__init__.py` | New — empty |
