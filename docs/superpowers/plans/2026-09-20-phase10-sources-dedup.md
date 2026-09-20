# Phase 10 Job Source Connectors + Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every path that creates a `Job` routes through one deduplicating seam, and Greenhouse/Lever become first-class API discovery sources feeding the same pipeline as the browser scrapers.

**Architecture:** A `Posting` record and `JobSource` Protocol sit parallel to the existing `Scraper` Protocol — the two differ only at the fetch boundary (a scraper needs a live Playwright `Page`; an API connector needs a board slug), so they converge immediately after into one scoring → policy → dedup → save pipeline. A pure `dedup` module decides identity by canonical URL, else by normalized company/title/location. A `JobStore` creation seam applies that decision, enriching an existing record rather than writing a duplicate.

**Tech Stack:** Python 3.11+, pydantic v2 (`BaseModel`, model defaults), `urllib.parse` (URL canonicalization), Typer, Rich, pytest with `unittest.mock.patch`.

**Spec:** `docs/superpowers/specs/2026-09-20-phase10-sources-dedup-design.md`

## Global Constraints

- All workspace I/O goes through the `StorageProvider` protocol.
- Activity logs are append-only; `status` is one of `success` / `failed` / `blocked`. `blocked` belongs to the `PolicyEngine`; a missing/unavailable source uses `failed`.
- String concatenation only for user-facing strings; no f-strings with user data (existing project-wide convention).
- Tests run fully offline: no network, no real browser. `urllib` is mocked at the transport boundary; Playwright at the `launch_browser` seam.
- Nothing under `careeros/browser/` may import from `careeros/core/` or `careeros/workspace/` at runtime.
- `careeros/core/dedup.py` performs no I/O and imports no storage.
- **Absence is type-specific, never truthiness.** For merge purposes a field is absent when it is `None` (optionals), `[]` (`requirements`), or `None` for `remote`. `remote=False` and `salary_min=0` are *present* values and must never be overwritten.
- `make_job_id` is random-suffixed and is **not** an identity. Dedup must run before an id is persisted.
- Test counts below are stated per-task as *tests added*. Report the actual full-suite total after each task; if it drifts from the running total, report it rather than adjusting anything to match.

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `careeros/sources/base.py` | `Posting`, `JobSource` Protocol, `job_from_posting`, `posting_from_scrape`. No I/O. |
| `careeros/sources/greenhouse.py` | `GreenhouseSource` — a `JobSource` over `fetch_greenhouse`. |
| `careeros/sources/lever.py` | `LeverSource` — a `JobSource` over `fetch_lever`. |
| `careeros/core/dedup.py` | URL/title/location normalization and `is_same_posting`. Pure functions. |
| `careeros/core/job_store.py` | `JobStore`, `SaveOutcome`. The single creation seam. |
| `careeros/config_sources.py` | Reader for `config/sources.json` board entries. |
| `tests/test_sources_base.py`, `tests/test_ats_sources.py`, `tests/test_dedup.py`, `tests/test_job_store.py`, `tests/test_config_sources.py` | |

**Modify:**

| File | Change |
|---|---|
| `careeros/core/models.py` | Add `Sighting`; add `Job.sightings`; docstring on `Job.save` pointing at `JobStore`. |
| `careeros/cli/job_cmd.py` | `add` and `search` route through `JobStore`; `add --force`; `search` uses `JobSource`. |
| `careeros/cli/browse_cmd.py` | Creation routes through `JobStore`. |
| `careeros/cli/discover_and_apply_cmd.py` | Delete the inline stopgap; route through `JobStore`; add API sources, failure handling, summary segment. |
| `careeros/cli/onboard.py` | Source prompt drops `naukri`, collects board slugs. |
| `careeros/cli/workspace_cmd.py` | `validate` reports inert `sources.json` entries. |
| `README.md`, `ROADMAP.md` | Document API discovery; mark Phase 10 shipped. |

---

### Task 1: `Posting`, `JobSource`, and the two converters

**Files:**
- Create: `careeros/sources/base.py`
- Create: `tests/test_sources_base.py`

**Interfaces:**
- Produces: `Posting` (frozen dataclass: `source: str`, `title: str`, `company: str`, `url: str`, `location: str | None = None`, `description: str | None = None`, `source_id: str | None = None`); `JobSource` Protocol with `name: str` and `fetch(self, board: str) -> list[Posting]`; `job_from_posting(posting: Posting, now: str) -> Job`; `posting_from_scrape(raw: dict) -> Posting`. Used by Tasks 2, 5, 6, 7, 8.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sources_base.py`:

```python
import dataclasses

import pytest

from careeros.sources.base import Posting, job_from_posting, posting_from_scrape


def _posting(**over):
    base = dict(
        source="greenhouse", title="Senior SRE", company="Acme",
        url="https://boards.greenhouse.io/acme/jobs/1",
    )
    base.update(over)
    return Posting(**base)


def test_posting_is_frozen():
    p = _posting()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.title = "tampered"


def test_posting_optional_fields_default_to_none():
    p = _posting()
    assert p.location is None
    assert p.description is None
    assert p.source_id is None


def test_job_from_posting_maps_every_field():
    p = _posting(location="SF", description="jd text", source_id="42")
    job = job_from_posting(p, "2026-09-20T00:00:00+00:00")
    assert job.source == "greenhouse"
    assert job.title == "Senior SRE"
    assert job.company == "Acme"
    assert job.url == "https://boards.greenhouse.io/acme/jobs/1"
    assert job.location == "SF"
    assert job.description == "jd text"
    assert job.source_id == "42"
    assert job.stage == "saved"
    assert job.created_at == "2026-09-20T00:00:00+00:00"
    assert job.updated_at == "2026-09-20T00:00:00+00:00"


def test_job_from_posting_ids_are_not_stable():
    # make_job_id is random-suffixed, so the id is NOT an identity. This is
    # why dedup compares fields, never ids, and why JobStore must run before
    # an id is persisted.
    p = _posting()
    a = job_from_posting(p, "2026-09-20T00:00:00+00:00")
    b = job_from_posting(p, "2026-09-20T00:00:00+00:00")
    assert a.id != b.id


def test_posting_from_scrape_maps_scraper_dict():
    raw = {
        "source_board": "linkedin", "title": "Staff Engineer", "company": "Beta",
        "location": "Remote", "url": "https://www.linkedin.com/jobs/view/1",
    }
    p = posting_from_scrape(raw)
    assert p.source == "linkedin"
    assert p.title == "Staff Engineer"
    assert p.company == "Beta"
    assert p.location == "Remote"
    assert p.url == "https://www.linkedin.com/jobs/view/1"


def test_posting_from_scrape_rejects_missing_company():
    # company is half the dedup fingerprint; defaulting it to "" would
    # silently merge unrelated postings from sources that omit it.
    raw = {"source_board": "linkedin", "title": "Staff Engineer",
           "url": "https://x.test/1", "company": ""}
    with pytest.raises(ValueError, match="company"):
        posting_from_scrape(raw)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sources_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.sources.base'`

- [ ] **Step 3: Create `careeros/sources/base.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from careeros.core.job_id import make_job_id
from careeros.core.models import Job


@dataclass(frozen=True)
class Posting:
    """One job posting as returned by any source, browser or API.

    A typed record rather than a dict because the two producers disagree:
    scrapers emit source_board/title/company/location/url, while the ATS
    transport emits source_id/title/url/location/description with no company.
    """

    source: str
    title: str
    company: str
    url: str
    location: str | None = None
    description: str | None = None
    source_id: str | None = None


class JobSource(Protocol):
    name: str

    def fetch(self, board: str) -> list[Posting]:
        """Fetch postings for one company board. Raises ATSFetchError on failure."""
        ...


def job_from_posting(posting: Posting, now: str) -> Job:
    """Build a new, unsaved Job from a Posting.

    The id assigned here is random-suffixed and therefore NOT an identity —
    two calls for the same posting produce different ids. Identity is decided
    by careeros.core.dedup against stored records, which is why this Job must
    go through JobStore.save_new rather than Job.save.
    """
    return Job(
        id=make_job_id(posting.company, posting.title),
        source=posting.source,
        source_id=posting.source_id,
        url=posting.url,
        company=posting.company,
        title=posting.title,
        location=posting.location,
        description=posting.description,
        stage="saved",
        created_at=now,
        updated_at=now,
    )


def posting_from_scrape(raw: dict) -> Posting:
    """Adapt a browser scraper's dict into a Posting.

    The only surviving use of the old scraper dict shape — one call site to
    delete when those dicts are retired.
    """
    company = (raw.get("company") or "").strip()
    if not company:
        raise ValueError("scraped posting has no company; cannot fingerprint it")
    return Posting(
        source=raw["source_board"],
        title=raw["title"],
        company=company,
        url=raw["url"],
        location=raw.get("location"),
        description=raw.get("description"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sources_base.py -v`
Expected: 6 PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 6 tests added, no failures.

- [ ] **Step 6: Commit**

```bash
git add careeros/sources/base.py tests/test_sources_base.py
git commit -m "feat: add Posting record and JobSource protocol (Phase 10)"
```

---

### Task 2: Greenhouse and Lever `JobSource` implementations

**Files:**
- Create: `careeros/sources/greenhouse.py`
- Create: `careeros/sources/lever.py`
- Create: `tests/test_ats_sources.py`

**Interfaces:**
- Consumes: `Posting`, `JobSource` (Task 1); the existing `fetch_greenhouse(company) -> list[dict]` and `fetch_lever(company) -> list[dict]` in `careeros/sources/ats.py`.
- Produces: `GreenhouseSource(company_name: str)` and `LeverSource(company_name: str)`, each with `name` and `fetch(board) -> list[Posting]`. Used by Tasks 5 and 8.

**Deviation from the spec, deliberate:** the spec's module table says `ats.py` is "unchanged except returning `Posting`". Leave `ats.py` returning dicts and do the conversion in these two classes instead. Two reasons: the same table defines `ats.py`'s responsibility as "HTTP + JSON transport", which returning a domain record contradicts; and `job_cmd.py:search` still reads those dicts until Task 5, so changing the return type here would break it mid-plan.

**Why the constructor takes a company name:** Greenhouse's `/boards/{slug}/jobs` response never returns the company name — it is implied by the slug. Deriving `"stripe"` → `"Stripe"` would be a guess, and `company` is half the dedup fingerprint, so a derived name that differs between sources defeats cross-source dedup.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ats_sources.py`:

```python
from unittest.mock import patch

import pytest

from careeros.sources.ats import ATSFetchError
from careeros.sources.greenhouse import GreenhouseSource
from careeros.sources.lever import LeverSource

_GH_RAW = [
    {"source_id": "1", "title": "Senior SRE",
     "url": "https://boards.greenhouse.io/stripe/jobs/1",
     "location": "SF", "description": "jd"},
]
_LV_RAW = [
    {"source_id": "a", "title": "Staff Engineer",
     "url": "https://jobs.lever.co/acme/a",
     "location": "Remote", "description": "jd"},
]


def test_greenhouse_name():
    assert GreenhouseSource("Stripe").name == "greenhouse"


def test_lever_name():
    assert LeverSource("Acme").name == "lever"


def test_greenhouse_fetch_returns_postings_with_configured_company():
    with patch("careeros.sources.greenhouse.fetch_greenhouse", return_value=_GH_RAW) as f:
        out = GreenhouseSource("Stripe").fetch("stripe")
    f.assert_called_once_with("stripe")
    assert len(out) == 1
    assert out[0].company == "Stripe"          # configured, never derived from the slug
    assert out[0].source == "greenhouse"
    assert out[0].title == "Senior SRE"
    assert out[0].url == "https://boards.greenhouse.io/stripe/jobs/1"
    assert out[0].location == "SF"
    assert out[0].description == "jd"
    assert out[0].source_id == "1"


def test_lever_fetch_returns_postings_with_configured_company():
    with patch("careeros.sources.lever.fetch_lever", return_value=_LV_RAW) as f:
        out = LeverSource("Acme").fetch("acme")
    f.assert_called_once_with("acme")
    assert out[0].company == "Acme"
    assert out[0].source == "lever"
    assert out[0].url == "https://jobs.lever.co/acme/a"


def test_greenhouse_fetch_propagates_ats_error():
    # The caller decides how to report an unreachable board; swallowing it
    # here would make a bad slug indistinguishable from an empty board.
    with patch("careeros.sources.greenhouse.fetch_greenhouse",
               side_effect=ATSFetchError("not_found")):
        with pytest.raises(ATSFetchError):
            GreenhouseSource("Stripe").fetch("nope")


def test_lever_fetch_propagates_ats_error():
    with patch("careeros.sources.lever.fetch_lever",
               side_effect=ATSFetchError("not_found")):
        with pytest.raises(ATSFetchError):
            LeverSource("Acme").fetch("nope")


def test_greenhouse_skips_posting_missing_a_url_without_aborting_the_batch():
    raw = [{"source_id": "1", "title": "A", "location": None, "description": None},
           {"source_id": "2", "title": "B", "url": "https://x.test/2",
            "location": None, "description": None}]
    with patch("careeros.sources.greenhouse.fetch_greenhouse", return_value=raw):
        out = GreenhouseSource("Stripe").fetch("stripe")
    assert [p.title for p in out] == ["B"]


def test_empty_board_returns_empty_list():
    with patch("careeros.sources.greenhouse.fetch_greenhouse", return_value=[]):
        assert GreenhouseSource("Stripe").fetch("stripe") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ats_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.sources.greenhouse'`

- [ ] **Step 3: Create `careeros/sources/greenhouse.py`**

```python
from __future__ import annotations

from careeros.sources.ats import fetch_greenhouse
from careeros.sources.base import Posting


class GreenhouseSource:
    """JobSource over Greenhouse's public board API.

    company_name is supplied by configuration because the API response never
    carries it — it is implied by the board slug. Deriving it would be a guess,
    and company is half the dedup fingerprint.
    """

    name = "greenhouse"

    def __init__(self, company_name: str) -> None:
        self._company = company_name

    def fetch(self, board: str) -> list[Posting]:
        postings = []
        for raw in fetch_greenhouse(board):
            url = raw.get("url")
            if not url:
                continue  # unusable without a URL: it is half the dedup key
            postings.append(Posting(
                source=self.name,
                title=raw["title"],
                company=self._company,
                url=url,
                location=raw.get("location"),
                description=raw.get("description"),
                source_id=raw.get("source_id"),
            ))
        return postings
```

- [ ] **Step 4: Create `careeros/sources/lever.py`**

```python
from __future__ import annotations

from careeros.sources.ats import fetch_lever
from careeros.sources.base import Posting


class LeverSource:
    """JobSource over Lever's public postings API. See GreenhouseSource for
    why company_name comes from configuration rather than the API response."""

    name = "lever"

    def __init__(self, company_name: str) -> None:
        self._company = company_name

    def fetch(self, board: str) -> list[Posting]:
        postings = []
        for raw in fetch_lever(board):
            url = raw.get("url")
            if not url:
                continue  # unusable without a URL: it is half the dedup key
            postings.append(Posting(
                source=self.name,
                title=raw["title"],
                company=self._company,
                url=url,
                location=raw.get("location"),
                description=raw.get("description"),
                source_id=raw.get("source_id"),
            ))
        return postings
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ats_sources.py -v`
Expected: 8 PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 8 tests added since Task 1, no failures.

- [ ] **Step 7: Commit**

```bash
git add careeros/sources/greenhouse.py careeros/sources/lever.py tests/test_ats_sources.py
git commit -m "feat: add Greenhouse and Lever JobSource implementations (Phase 10)"
```

---

### Task 3: The dedup engine

**Files:**
- Create: `careeros/core/dedup.py`
- Create: `tests/test_dedup.py`

**Interfaces:**
- Consumes: `Job` from `careeros.core.models`.
- Produces: `canonical_url(url: str | None) -> str`, `normalize_title(title: str) -> str`, `normalize_location(loc: str | None) -> str | None`, `is_same_posting(candidate: Job, existing: Job) -> bool`. Used by Task 4.

This is the centre of gravity for the phase. Two rules carry the most weight and both have a catastrophic failure mode if implemented naively:

1. **`canonical_url` uses a tracking-parameter DENYLIST, never "strip all query params."** Some ATS links carry the job ID in a query parameter; blanket-stripping would collapse every posting on such a board into a single record.
2. **Abbreviation expansion runs on whole tokens only.** A substring replace turns "engineering" into "engineerineering" and silently stops matching anything.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dedup.py`:

```python
from careeros.core.dedup import (
    canonical_url,
    is_same_posting,
    normalize_location,
    normalize_title,
)
from careeros.core.models import Job

_NOW = "2026-09-20T00:00:00+00:00"


def _job(**over):
    base = dict(
        id="x", source="linkedin", company="Acme", title="Senior SRE",
        url="https://x.test/1", created_at=_NOW, updated_at=_NOW,
    )
    base.update(over)
    return Job(**base)


# --- canonical_url ---

def test_canonical_url_lowercases_scheme_and_host():
    assert canonical_url("HTTPS://Boards.Greenhouse.IO/acme/jobs/1") == \
        "https://boards.greenhouse.io/acme/jobs/1"


def test_canonical_url_strips_fragment_and_trailing_slash():
    assert canonical_url("https://x.test/jobs/1/#apply") == "https://x.test/jobs/1"


def test_canonical_url_strips_each_denylisted_tracking_param():
    for param in ("utm_source=li", "utm_campaign=x", "gh_src=abc",
                  "trk=feed", "refId=zz", "originalSubdomain=uk"):
        assert canonical_url("https://x.test/jobs/1?" + param) == "https://x.test/jobs/1"


def test_canonical_url_preserves_a_non_denylisted_query_param():
    # THE catastrophic case: some boards put the job id in the query string.
    # Stripping all params would collapse every posting into one record.
    assert canonical_url("https://x.test/apply?jobId=1234") == \
        "https://x.test/apply?jobId=1234"


def test_canonical_url_keeps_real_params_while_dropping_tracking_ones():
    out = canonical_url("https://x.test/apply?jobId=1234&utm_source=li")
    assert "jobId=1234" in out
    assert "utm_source" not in out


def test_canonical_url_of_none_is_empty_string():
    assert canonical_url(None) == ""


# --- normalize_title ---

def test_normalize_title_lowercases_and_strips_punctuation():
    assert normalize_title("Senior  Engineer, Platform!") == "senior engineer platform"


def test_normalize_title_expands_each_abbreviation():
    assert normalize_title("Sr SRE") == "senior site reliability engineer"
    assert normalize_title("Jr SWE") == "junior software engineer"
    assert normalize_title("Eng Mgr") == "engineer manager"
    assert normalize_title("Dev") == "developer"


def test_normalize_title_expands_whole_tokens_only():
    # A substring replace would produce "engineerineering" and silently stop
    # matching anything.
    assert normalize_title("Engineering Manager") == "engineering manager"
    assert normalize_title("Sreepathi") == "sreepathi"


# --- normalize_location ---

def test_normalize_location_none_stays_none():
    assert normalize_location(None) is None
    assert normalize_location("   ") is None


def test_normalize_location_lowercases_and_collapses():
    assert normalize_location("  San Francisco,  CA ") == "san francisco ca"


# --- is_same_posting ---

def test_url_tier_matches_even_when_titles_differ_entirely():
    a = _job(title="Senior SRE", url="https://x.test/1?utm_source=li")
    b = _job(title="Completely Different", url="https://x.test/1")
    assert is_same_posting(a, b)


def test_title_variants_match_on_the_fuzzy_tier():
    a = _job(title="Senior SRE", url="https://linkedin.test/1")
    b = _job(title="Senior Site Reliability Engineer", url="https://greenhouse.test/9")
    assert is_same_posting(a, b)


def test_genuinely_different_titles_do_not_match():
    a = _job(title="Senior Engineer", url="https://a.test/1")
    b = _job(title="Staff Engineer", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_different_companies_never_match_on_the_fuzzy_tier():
    a = _job(company="Acme", url="https://a.test/1")
    b = _job(company="Beta", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_company_match_is_case_and_whitespace_insensitive():
    a = _job(company=" acme ", url="https://a.test/1")
    b = _job(company="Acme", url="https://b.test/2")
    assert is_same_posting(a, b)


def test_null_location_is_a_wildcard():
    a = _job(location=None, url="https://a.test/1")
    b = _job(location="San Francisco, CA", url="https://b.test/2")
    assert is_same_posting(a, b)
    assert is_same_posting(b, a)


def test_two_different_non_empty_locations_do_not_match():
    # The accepted limitation, pinned so it cannot silently drift into a
    # false positive: "Remote" and "San Francisco, CA" for one job WILL
    # produce two records. That is the recoverable direction.
    a = _job(location="Remote", url="https://a.test/1")
    b = _job(location="San Francisco, CA", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_identical_non_empty_locations_match():
    a = _job(location="Remote", url="https://a.test/1")
    b = _job(location="remote", url="https://b.test/2")
    assert is_same_posting(a, b)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dedup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.core.dedup'`

- [ ] **Step 3: Create `careeros/core/dedup.py`**

```python
from __future__ import annotations

import re
import urllib.parse

from careeros.core.models import Job

# Tracking parameters safe to drop. A DENYLIST, not "strip everything":
# some ATS links carry the job id in a query parameter, and blanket-stripping
# would collapse every posting on such a board into a single record.
_TRACKING_PARAMS = ("gh_src", "trk", "refid", "originalsubdomain")

_ABBREVIATIONS = {
    "sr": "senior",
    "jr": "junior",
    "eng": "engineer",
    "sre": "site reliability engineer",
    "swe": "software engineer",
    "mgr": "manager",
    "dev": "developer",
}

_PUNCT = re.compile(r"[^a-z0-9]+")


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in _TRACKING_PARAMS


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not _is_tracking(k)]
    )
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, query, "")
    )


def normalize_title(title: str) -> str:
    cleaned = _PUNCT.sub(" ", (title or "").lower()).strip()
    # Whole-token expansion only. A substring replace would turn
    # "engineering" into "engineerineering".
    return " ".join(_ABBREVIATIONS.get(tok, tok) for tok in cleaned.split())


def normalize_location(loc: str | None) -> str | None:
    if not loc or not loc.strip():
        return None
    cleaned = _PUNCT.sub(" ", loc.lower()).strip()
    return " ".join(cleaned.split()) or None


def _normalize_company(company: str) -> str:
    return " ".join(_PUNCT.sub(" ", (company or "").lower()).split())


def is_same_posting(candidate: Job, existing: Job) -> bool:
    """Identity: same canonical URL, else same company + title + location.

    Location is a wildcard when absent on either side, because boards word it
    inconsistently. Two differently-worded non-empty locations do NOT match —
    an accepted limitation that errs toward a visible duplicate rather than
    silently collapsing two distinct roles into one.
    """
    a_url = canonical_url(candidate.url)
    if a_url and a_url == canonical_url(existing.url):
        return True

    if _normalize_company(candidate.company) != _normalize_company(existing.company):
        return False
    if normalize_title(candidate.title) != normalize_title(existing.title):
        return False

    a_loc = normalize_location(candidate.location)
    b_loc = normalize_location(existing.location)
    if a_loc is None or b_loc is None:
        return True
    return a_loc == b_loc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dedup.py -v`
Expected: 19 PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 19 tests added since Task 2, no failures.

- [ ] **Step 6: Commit**

```bash
git add careeros/core/dedup.py tests/test_dedup.py
git commit -m "feat: add canonical-URL and normalized-title dedup engine (Phase 10)"
```

---

### Task 4: `Sighting` model and the `JobStore` creation seam

**Files:**
- Modify: `careeros/core/models.py` (add `Sighting`; add `Job.sightings`; docstring on `Job.save`)
- Create: `careeros/core/job_store.py`
- Create: `tests/test_job_store.py`

**Interfaces:**
- Consumes: `is_same_posting` (Task 3); `Job`, `StorageProvider`.
- Produces: `Sighting` (pydantic `BaseModel`: `source: str`, `url: str | None = None`, `seen_at: str`); `Job.sightings: list[Sighting] = []`; `SaveOutcome` (frozen dataclass: `job: Job`, `created: bool`, `enriched: tuple[str, ...]`); `JobStore(storage).save_new(job: Job, *, force: bool = False) -> SaveOutcome`. Used by Tasks 5, 6, 7, 8.

**The absence rule is the trap in this task.** A field is absent when it is `None`, or `[]` for `requirements`. `remote=False` and `salary_min=0` are *present* values. A truthiness test (`if not existing.remote:`) silently overwrites both, and two tests below exist solely to catch that.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_store.py`:

```python
from careeros.core.job_store import JobStore
from careeros.core.models import Job, Sighting
from careeros.storage.filesystem import LocalFilesystemStorage

_NOW = "2026-09-20T00:00:00+00:00"
_LATER = "2026-09-21T00:00:00+00:00"


def _job(**over):
    base = dict(
        id="acme-sre-aaaa", source="linkedin", company="Acme", title="Senior SRE",
        url="https://linkedin.test/1", created_at=_NOW, updated_at=_NOW,
    )
    base.update(over)
    return Job(**base)


def _store(tmp_path):
    return JobStore(LocalFilesystemStorage(str(tmp_path)))


def test_save_new_creates_when_no_match_exists(tmp_path):
    out = _store(tmp_path).save_new(_job())
    assert out.created is True
    assert out.enriched == ()
    assert Job.load(LocalFilesystemStorage(str(tmp_path)), out.job.id).title == "Senior SRE"


def test_save_new_merges_a_title_variant_instead_of_duplicating(tmp_path):
    store = _store(tmp_path)
    first = store.save_new(_job())
    second = store.save_new(_job(
        id="acme-sre-bbbb", source="greenhouse", title="Senior Site Reliability Engineer",
        url="https://greenhouse.test/9",
    ))
    assert second.created is False
    assert second.job.id == first.job.id
    assert len(Job.list_all(LocalFilesystemStorage(str(tmp_path)))) == 1


def test_merge_enriches_only_absent_fields(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job(description=None, location=None))
    out = store.save_new(_job(
        id="b", url="https://greenhouse.test/9",
        description="full jd", location="SF",
    ))
    assert out.created is False
    assert out.job.description == "full jd"
    assert out.job.location == "SF"
    assert set(out.enriched) == {"description", "location"}


def test_merge_never_overwrites_a_populated_field(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job(description="original"))
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9",
                              description="replacement"))
    assert out.job.description == "original"
    assert "description" not in out.enriched


def test_merge_does_not_treat_remote_false_as_absent(tmp_path):
    # Truthiness bug guard: `if not existing.remote` would overwrite False.
    store = _store(tmp_path)
    store.save_new(_job(remote=False))
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9", remote=True))
    assert out.job.remote is False
    assert "remote" not in out.enriched


def test_merge_does_not_treat_salary_zero_as_absent(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job(salary_min=0))
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9", salary_min=200000))
    assert out.job.salary_min == 0
    assert "salary_min" not in out.enriched


def test_merge_never_alters_user_state(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    store = JobStore(storage)
    created = store.save_new(_job())
    advanced = created.job.model_copy(update={
        "stage": "interviewing", "applied_at": _NOW, "notes": ["spoke to recruiter"],
    })
    advanced.save(storage)

    out = store.save_new(_job(id="b", url="https://greenhouse.test/9",
                              stage="saved", applied_at=None, notes=[]))
    assert out.created is False
    assert out.job.stage == "interviewing"
    assert out.job.applied_at == _NOW
    assert out.job.notes == ["spoke to recruiter"]


def test_first_merge_seeds_sightings_from_the_existing_record(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    out = store.save_new(_job(id="b", source="greenhouse",
                              url="https://greenhouse.test/9"))
    assert [s.source for s in out.job.sightings] == ["linkedin", "greenhouse"]
    assert out.job.sightings[0].url == "https://linkedin.test/1"
    assert out.job.sightings[1].url == "https://greenhouse.test/9"


def test_second_merge_appends_without_reseeding(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    store.save_new(_job(id="b", source="greenhouse", url="https://greenhouse.test/9"))
    out = store.save_new(_job(id="c", source="wellfound", url="https://wf.test/3"))
    assert [s.source for s in out.job.sightings] == ["linkedin", "greenhouse", "wellfound"]


def test_force_creates_a_duplicate(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9"), force=True)
    assert out.created is True
    assert len(Job.list_all(LocalFilesystemStorage(str(tmp_path)))) == 2


def test_updated_at_unchanged_when_nothing_was_enriched(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9"))
    assert out.enriched == ()
    assert out.job.updated_at == _NOW


def test_sightings_default_empty_so_existing_job_json_loads(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("jobs/old.json", _job(id="old").model_dump_json().encode())
    loaded = Job.load(storage, "old")
    assert loaded.sightings == []


def test_sighting_is_a_pydantic_model():
    s = Sighting(source="greenhouse", url="https://x.test/1", seen_at=_NOW)
    assert s.model_dump()["source"] == "greenhouse"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_job_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'JobStore'`

- [ ] **Step 3: Add `Sighting` and `Job.sightings` to `careeros/core/models.py`**

Declare `Sighting` immediately above `class Job`:

```python
class Sighting(BaseModel):
    """One place a posting was seen. Recorded when dedup merges a new
    sighting into an existing record."""

    source: str
    url: str | None = None
    seen_at: str
```

Add the field to `Job`, after `notes`:

```python
    sightings: list[Sighting] = []
```

And extend `Job.save`'s behaviour with a docstring — do not change its logic:

```python
    def save(self, storage: StorageProvider) -> None:
        """Write this job, overwriting any record with the same id.

        For a NEW job use JobStore.save_new instead: it deduplicates against
        existing records, which this method deliberately does not do.
        """
```

- [ ] **Step 4: Create `careeros/core/job_store.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from careeros.core.dedup import is_same_posting
from careeros.core.models import Job, Sighting
from careeros.storage.interface import StorageProvider

# Fields a merge may fill in. stage, applied_at, and notes are deliberately
# absent: user state is structurally excluded, not guarded by a conditional a
# later edit could weaken.
_ENRICHABLE = (
    "location", "description", "salary_min", "salary_max",
    "remote", "requirements", "source_id",
)


def _is_absent(value: object) -> bool:
    """Absence is type-specific, never truthiness.

    remote=False and salary_min=0 are PRESENT values; a `not value` test
    would silently overwrite both.
    """
    if value is None:
        return True
    if isinstance(value, (list, str)) and len(value) == 0:
        return True
    return False


@dataclass(frozen=True)
class SaveOutcome:
    job: Job
    created: bool
    enriched: tuple[str, ...]


class JobStore:
    """The single seam through which new Job records are created.

    Creation only. Updates to a known job (stage changes, notes) call
    Job.save directly — routing those through dedup would be nonsense, since
    a job always matches itself.
    """

    def __init__(self, storage: StorageProvider) -> None:
        self._storage = storage

    def save_new(self, job: Job, *, force: bool = False) -> SaveOutcome:
        if not force:
            for existing in Job.list_all(self._storage):
                if is_same_posting(job, existing):
                    return self._merge(job, existing)
        job.save(self._storage)
        return SaveOutcome(job=job, created=True, enriched=())

    def _merge(self, incoming: Job, existing: Job) -> SaveOutcome:
        updates: dict = {}
        enriched: list[str] = []
        for field in _ENRICHABLE:
            if _is_absent(getattr(existing, field)) and not _is_absent(getattr(incoming, field)):
                updates[field] = getattr(incoming, field)
                enriched.append(field)

        sightings = list(existing.sightings)
        if not sightings:
            # Seed with the existing record's own first sighting so the list
            # is complete from the first merge onward, without backfilling
            # every workspace.
            sightings.append(Sighting(
                source=existing.source, url=existing.url, seen_at=existing.created_at,
            ))
        sightings.append(Sighting(
            source=incoming.source, url=incoming.url, seen_at=incoming.created_at,
        ))
        updates["sightings"] = sightings

        if enriched:
            updates["updated_at"] = incoming.created_at

        merged = existing.model_copy(update=updates)
        merged.save(self._storage)
        return SaveOutcome(job=merged, created=False, enriched=tuple(enriched))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_job_store.py -v`
Expected: 13 PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 13 tests added since Task 3, no failures. If any pre-existing test fails because `Job` gained a field, the test is asserting on a full serialized dict — update the expectation, do not remove the field.

- [ ] **Step 7: Commit**

```bash
git add careeros/core/models.py careeros/core/job_store.py tests/test_job_store.py
git commit -m "feat: add Sighting model and JobStore creation seam (Phase 10)"
```

---

### Task 5: Route `job add` and `job search` through `JobStore`

**Files:**
- Modify: `careeros/cli/job_cmd.py` (imports; `add_cmd` ~line 93-116; `search_cmd` ~line 258-333)
- Modify: `tests/test_job_cmd.py`

**Interfaces:**
- Consumes: `JobStore`, `SaveOutcome` (Task 4); `GreenhouseSource`, `LeverSource` (Task 2); `job_from_posting` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_job_cmd.py`, following the file's existing fixture and invocation conventions (it invokes the `job_app` sub-app):

```python
    def test_add_deduplicates_against_an_existing_job(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="manual", company="Acme", title="Senior SRE",
            url="https://x.test/1", created_at=_NOW, updated_at=_NOW,
        ))
        with patch("careeros.cli.job_cmd.Prompt.ask", side_effect=[
            "https://x.test/1", "Acme", "Senior SRE", "", "", "", "USD",
        ]), patch("careeros.cli.job_cmd.Confirm.ask", return_value=False), \
             patch("careeros.cli.job_cmd.extract_job_fields", return_value={}):
            result = runner.invoke(job_app, ["add", "--workspace", ws])
        assert result.exit_code == 0
        assert len(Job.list_all(storage)) == 1

    def test_add_force_creates_a_duplicate(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="manual", company="Acme", title="Senior SRE",
            url="https://x.test/1", created_at=_NOW, updated_at=_NOW,
        ))
        with patch("careeros.cli.job_cmd.Prompt.ask", side_effect=[
            "https://x.test/1", "Acme", "Senior SRE", "", "", "", "USD",
        ]), patch("careeros.cli.job_cmd.Confirm.ask", return_value=False), \
             patch("careeros.cli.job_cmd.extract_job_fields", return_value={}):
            result = runner.invoke(job_app, ["add", "--force", "--workspace", ws])
        assert result.exit_code == 0
        assert len(Job.list_all(storage)) == 2

    def test_search_deduplicates_on_a_second_run(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws)
        postings = [Posting(source="greenhouse", title="Senior SRE", company="stripe",
                            url="https://boards.greenhouse.io/stripe/jobs/1",
                            location="SF", source_id="1")]
        with patch("careeros.cli.job_cmd.GreenhouseSource") as gs, \
             patch("careeros.cli.job_cmd.Prompt.ask", return_value="1"):
            gs.return_value.fetch.return_value = postings
            runner.invoke(job_app, ["search", "--source", "greenhouse",
                                    "--company", "stripe", "--workspace", ws])
            result = runner.invoke(job_app, ["search", "--source", "greenhouse",
                                             "--company", "stripe", "--workspace", ws])
        assert len(Job.list_all(storage)) == 1
        assert "Duplicates: 1" in result.output

    def test_search_reports_saved_and_duplicate_counts(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        postings = [Posting(source="greenhouse", title="Senior SRE", company="stripe",
                            url="https://boards.greenhouse.io/stripe/jobs/1")]
        with patch("careeros.cli.job_cmd.GreenhouseSource") as gs, \
             patch("careeros.cli.job_cmd.Prompt.ask", return_value="1"):
            gs.return_value.fetch.return_value = postings
            result = runner.invoke(job_app, ["search", "--source", "greenhouse",
                                             "--company", "stripe", "--workspace", ws])
        assert "Saved 1" in result.output
        assert "Duplicates: 0" in result.output
```

Add the imports the tests need at the top of the file: `from careeros.core.job_store import JobStore`, `from careeros.sources.base import Posting`, and `_NOW = "2026-09-20T00:00:00+00:00"` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_job_cmd.py -v`
Expected: the four new tests FAIL (`--force` is an unknown option; `GreenhouseSource` is not imported in `job_cmd`).

- [ ] **Step 3: Rewire `add_cmd`**

Add to the imports:

```python
from careeros.core.job_store import JobStore
```

Add the option to the signature:

```python
    force: bool = typer.Option(False, "--force", help="Save even if a matching job already exists"),
```

Replace the `job.save(storage)` block (the `job = Job(...)` construction stays, minus the `make_job_id` line if you prefer, but keep the id assignment) with:

```python
    outcome = JobStore(storage).save_new(job, force=force)
    if not outcome.created:
        rprint("[yellow]Matches existing job " + outcome.job.id
               + " — enriched instead of duplicating. Use --force to save anyway.[/yellow]")
    logger.log(logger.new_event(
        "job_added" if outcome.created else "job_merged", "add",
        "Job added: " + company + " — " + title,
        entity_type="job", entity_id=outcome.job.id,
    ))
    rprint("\n[green]Saved[/green] as [bold]" + outcome.job.id + "[/bold]  (stage: "
           + outcome.job.stage + ")")
```

- [ ] **Step 4: Rewire `search_cmd` onto `JobSource` and `JobStore`**

Replace the `fetch_greenhouse`/`fetch_lever` imports with:

```python
from careeros.sources.greenhouse import GreenhouseSource
from careeros.sources.lever import LeverSource
from careeros.sources.base import job_from_posting
```

Replace the fetch block:

```python
    try:
        if source == "greenhouse":
            postings = GreenhouseSource(company).fetch(company)
        elif source == "lever":
            postings = LeverSource(company).fetch(company)
        else:
            rprint("[red]Unknown source '" + source + "'. Valid: greenhouse lever[/red]")
            raise typer.Exit(1)
    except ATSFetchError as e:
```

The table rows now read from `Posting` attributes rather than dict keys:

```python
    for i, p in enumerate(postings, 1):
        table.add_row(str(i), p.title, p.location or "—", p.url)
```

And the save loop becomes:

```python
    saved = 0
    duplicates = 0
    now = _now()
    store = JobStore(storage)
    for idx in indices:
        outcome = store.save_new(job_from_posting(postings[idx], now))
        if outcome.created:
            saved += 1
        else:
            duplicates += 1
        logger.log(logger.new_event(
            "job_added" if outcome.created else "job_merged", "search",
            "Job saved from " + source + ": " + company + " — " + postings[idx].title,
            entity_type="job", entity_id=outcome.job.id,
        ))

    rprint("[green]Saved " + str(saved) + " job(s)[/green], Duplicates: " + str(duplicates))
```

Note `job_from_posting` uses `posting.company`, which `GreenhouseSource(company)` set from the `--company` argument — so the slug doubles as the display name here, matching today's behaviour.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_job_cmd.py -v`
Expected: all PASS. Pre-existing tests in this file that assert on `Saved N job(s)` need the new `Duplicates:` suffix accommodated — update the assertion, do not weaken it to a substring that would pass either way.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 4 tests added since Task 4, no failures.

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/job_cmd.py tests/test_job_cmd.py
git commit -m "feat: route job add and job search through JobStore (Phase 10)"
```

---

### Task 6: Route `browse` through `JobStore`

**Files:**
- Modify: `careeros/cli/browse_cmd.py` (imports; the save loop at ~line 141-160)
- Modify: `tests/test_browse_cmd.py`

**Interfaces:**
- Consumes: `JobStore` (Task 4), `posting_from_scrape` and `job_from_posting` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_browse_cmd.py`, using its existing `_setup_workspace`, `_mock_launch`, `_mock_postings`, `_mock_score` helpers and the `browse_app` sub-app:

```python
    def test_saving_the_same_listing_twice_creates_one_record(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        mock_page = MagicMock()
        for _ in range(2):
            with patch("careeros.cli.preflight.check_board_sessions",
                       return_value={"linkedin": True}), \
                 patch("careeros.cli.browse_cmd.launch_browser", _mock_launch(mock_page)), \
                 patch("careeros.cli.browse_cmd.SCRAPERS") as scrapers, \
                 patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
                 patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()), \
                 patch("careeros.cli.browse_cmd.Prompt.ask", return_value="1"):
                scrapers.__getitem__.return_value.search.return_value = _mock_postings()
                runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws_path])
        assert len(Job.list_all(storage)) == 1

    def test_browse_reports_duplicates(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        mock_page = MagicMock()
        with patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.browse_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.browse_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()), \
             patch("careeros.cli.browse_cmd.Prompt.ask", return_value="1"):
            scrapers.__getitem__.return_value.search.return_value = _mock_postings()
            result = runner.invoke(browse_app, ["--board", "linkedin", "--workspace", ws_path])
        assert "Duplicates: 0" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_browse_cmd.py -v`
Expected: the two new tests FAIL — two records are created, and no `Duplicates:` appears.

- [ ] **Step 3: Rewire the save loop**

Add the imports:

```python
from careeros.core.job_store import JobStore
from careeros.sources.base import job_from_posting, posting_from_scrape
```

Replace the body of the save loop (from `job_id = make_job_id(...)` through `saved += 1`) with:

```python
        outcome = store.save_new(job_from_posting(posting_from_scrape(p), now))
        runtime.record_activity(runtime.new_event(
            "job_added" if outcome.created else "job_merged", "browse",
            "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=outcome.job.id,
        ))
        if outcome.created:
            saved += 1
        else:
            duplicates += 1
```

Initialise `duplicates = 0` and `store = JobStore(runtime.storage)` beside the existing `saved = 0`, and change the final report to:

```python
    rprint("[green]Saved " + str(saved) + " job(s)[/green], Duplicates: " + str(duplicates))
```

Remove the now-unused `make_job_id` and `Job` imports if nothing else in the file uses them — check first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_browse_cmd.py -v`
Expected: all PASS. Pre-existing tests asserting on `Saved N job(s)` need the suffix accommodated.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 2 tests added since Task 5, no failures.

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/browse_cmd.py tests/test_browse_cmd.py
git commit -m "feat: route browse job creation through JobStore (Phase 10)"
```

---

### Task 7: Replace the `discover-and-apply` stopgap with `JobStore`

**Files:**
- Modify: `careeros/cli/discover_and_apply_cmd.py` (delete the inline dedup block at ~line 117-155; rewire)
- Modify: `tests/test_discover_and_apply_cmd.py`

**Interfaces:**
- Consumes: `JobStore` (Task 4), `posting_from_scrape` / `job_from_posting` (Task 1).
- Produces: nothing new.

The existing `already_applied` behaviour must survive exactly: a job whose `applied_at` is set is excluded from `eligible`. It now reads that from the `SaveOutcome`'s returned record rather than from a hand-built index.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_discover_and_apply_cmd.py`:

```python
    def test_title_variant_rediscovery_does_not_create_a_second_record(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        first = _posting(title="Senior SRE", url="https://linkedin.test/1")
        second = _posting(title="Senior Site Reliability Engineer",
                          url="https://greenhouse.test/9")
        for postings in ([first], [second]):
            with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                       return_value={"linkedin": True}), \
                 patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
                 patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
                 patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
                 patch("careeros.cli.discover_and_apply_cmd.score_job",
                       return_value={"score": 10, "reasoning": "meh"}):
                scrapers.__getitem__.return_value.search.return_value = postings
                runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert len(Job.list_all(storage)) == 1

    def test_already_applied_job_is_not_reapplied_after_rediscovery(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=50, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="linkedin", company="Acme", title="Senior SRE",
            url="https://linkedin.test/1", stage="applied", applied_at=_NOW,
            created_at=_NOW, updated_at=_NOW,
        ))
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            scrapers.__getitem__.return_value.search.return_value = [
                _posting(title="Senior SRE", url="https://linkedin.test/1")
            ]
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        assert "Duplicates: 1" in result.output

    def test_cross_source_same_job_resolves_to_one_record(self, tmp_path):
        # The ROADMAP's exit condition, asserted directly.
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        JobStore(storage).save_new(Job(
            id="acme-sre-aaaa", source="greenhouse", company="Acme", title="Senior SRE",
            url="https://boards.greenhouse.io/acme/jobs/1",
            created_at=_NOW, updated_at=_NOW,
        ))
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            scrapers.__getitem__.return_value.search.return_value = [
                _posting(title="Senior SRE", url="https://www.linkedin.com/jobs/view/7")
            ]
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert len(Job.list_all(storage)) == 1
```

Add `from careeros.core.job_store import JobStore` and `_NOW = "2026-09-20T00:00:00+00:00"` to the file's imports if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discover_and_apply_cmd.py -v`
Expected: the title-variant and cross-source tests FAIL — the `(company, title)` stopgap matches neither case.

- [ ] **Step 3: Delete the stopgap and rewire**

Add the imports:

```python
from careeros.core.job_store import JobStore
from careeros.sources.base import job_from_posting, posting_from_scrape
```

Delete the entire block from the `# Minimal dedup:` comment through `existing_by_key[key] = job`, and replace it with:

```python
    store = JobStore(runtime.storage)
    saved_count = 0
    duplicate_count = 0
    for p in discovered:
        posting = posting_from_scrape(p)
        job = job_from_posting(posting, now)
        job = job.model_copy(update={"description": p["jd_text"]})
        outcome = store.save_new(job)
        p["job_id"] = outcome.job.id
        p["already_applied"] = outcome.job.applied_at is not None
        if outcome.created:
            runtime.record_activity(runtime.new_event(
                "job_added", "discover-and-apply",
                "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
                entity_type="job", entity_id=outcome.job.id,
            ))
            saved_count += 1
        else:
            duplicate_count += 1
```

The `eligible` filter below is unchanged — it already reads `p["already_applied"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discover_and_apply_cmd.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 3 tests added since Task 6, no failures.

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/discover_and_apply_cmd.py tests/test_discover_and_apply_cmd.py
git commit -m "feat: replace discover-and-apply dedup stopgap with JobStore (Phase 10)"
```

---

### Task 8: API sources in `discover-and-apply`

**Files:**
- Create: `careeros/config_sources.py`
- Create: `tests/test_config_sources.py`
- Modify: `careeros/cli/discover_and_apply_cmd.py`
- Modify: `tests/test_discover_and_apply_cmd.py`

**Interfaces:**
- Consumes: `GreenhouseSource`, `LeverSource` (Task 2); `Posting` and the `JobSource` Protocol (Task 1). It does **not** use `job_from_posting` — Task 7 already converts everything in `discovered` into a `Job` via `JobStore`, and this task only appends to that same list.
- Produces: `BoardEntry` (frozen dataclass: `source: str`, `board: str`, `company: str`); `load_board_entries(storage) -> list[BoardEntry]`; `build_source(entry) -> JobSource`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_sources.py`:

```python
import json

from careeros.config_sources import BoardEntry, build_source, load_board_entries
from careeros.storage.filesystem import LocalFilesystemStorage


def _write(tmp_path, payload):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("config/sources.json", json.dumps(payload).encode())
    return storage


def test_loads_well_formed_entries(tmp_path):
    storage = _write(tmp_path, {"sources": [
        {"source": "greenhouse", "board": "stripe", "company": "Stripe",
         "mode": "SEARCH_ONLY"},
    ]})
    assert load_board_entries(storage) == [
        BoardEntry(source="greenhouse", board="stripe", company="Stripe")
    ]


def test_skips_legacy_entries_without_a_board(tmp_path):
    # Legacy Phase 1 entries are inert now exactly as they always were.
    storage = _write(tmp_path, {"sources": [
        {"source": "greenhouse", "mode": "SEARCH_ONLY"},
        {"source": "naukri", "mode": "SEARCH_ONLY"},
    ]})
    assert load_board_entries(storage) == []


def test_skips_entries_naming_an_unknown_source(tmp_path):
    storage = _write(tmp_path, {"sources": [
        {"source": "naukri", "board": "x", "company": "X"},
    ]})
    assert load_board_entries(storage) == []


def test_defaults_company_to_the_board_slug_when_absent(tmp_path):
    storage = _write(tmp_path, {"sources": [
        {"source": "lever", "board": "acme"},
    ]})
    assert load_board_entries(storage) == [
        BoardEntry(source="lever", board="acme", company="acme")
    ]


def test_missing_file_yields_no_entries(tmp_path):
    assert load_board_entries(LocalFilesystemStorage(str(tmp_path))) == []


def test_malformed_json_yields_no_entries(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("config/sources.json", b"{ not json")
    assert load_board_entries(storage) == []


def test_build_source_returns_the_matching_connector():
    gh = build_source(BoardEntry(source="greenhouse", board="stripe", company="Stripe"))
    lv = build_source(BoardEntry(source="lever", board="acme", company="Acme"))
    assert gh.name == "greenhouse"
    assert lv.name == "lever"
```

Add to `tests/test_discover_and_apply_cmd.py`:

```python
    def test_api_source_postings_join_the_pipeline(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=[])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "stripe", "company": "Stripe"}]}).encode())
        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.return_value = [Posting(
            source="greenhouse", title="Senior SRE", company="Stripe",
            url="https://boards.greenhouse.io/stripe/jobs/1")]
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={}), \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 0
        assert len(Job.list_all(storage)) == 1

    def test_unavailable_source_is_skipped_and_logged(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "nope", "company": "Nope"}]}).encode())
        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.side_effect = ATSFetchError("not_found")
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            scrapers.__getitem__.return_value.search.return_value = []
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert "Unavailable sources: greenhouse:nope" in result.output
        logs = sorted((tmp_path / "activity").glob("*.jsonl"))
        events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
        unavail = [e for e in events if e["event_type"] == "source_unavailable"]
        assert len(unavail) == 1
        assert unavail[0]["status"] == "failed"

    def test_all_boards_unauthorized_and_all_sources_unavailable_exits_non_zero(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5,
                                  boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "board": "nope", "company": "Nope"}]}).encode())
        source = MagicMock()
        source.name = "greenhouse"
        source.fetch.side_effect = ATSFetchError("not_found")
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.discover_and_apply_cmd.build_source", return_value=source):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config_sources.py tests/test_discover_and_apply_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.config_sources'`

- [ ] **Step 3: Create `careeros/config_sources.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from careeros.sources.base import JobSource
from careeros.sources.greenhouse import GreenhouseSource
from careeros.sources.lever import LeverSource
from careeros.storage.interface import StorageProvider

_SOURCES_PATH = "config/sources.json"

_BUILDERS = {"greenhouse": GreenhouseSource, "lever": LeverSource}


@dataclass(frozen=True)
class BoardEntry:
    source: str
    board: str
    company: str


def load_board_entries(storage: StorageProvider) -> list[BoardEntry]:
    """Read API board entries from config/sources.json.

    Entries without a `board`, or naming a source with no connector, are
    skipped: legacy Phase 1 entries are inert now exactly as they always were.
    Surfacing them is `workspace validate`'s job, not this reader's.
    """
    if not storage.exists(_SOURCES_PATH):
        return []
    try:
        raw = json.loads(storage.read(_SOURCES_PATH).decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    entries = []
    for item in raw.get("sources", []):
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        board = item.get("board")
        if not board or source not in _BUILDERS:
            continue
        entries.append(BoardEntry(
            source=source, board=board, company=item.get("company") or board,
        ))
    return entries


def build_source(entry: BoardEntry) -> JobSource:
    return _BUILDERS[entry.source](entry.company)
```

- [ ] **Step 4: Wire API sources into `discover_and_apply_cmd`**

Add the imports:

```python
from careeros.config_sources import build_source, load_board_entries
from careeros.sources.ats import ATSFetchError
```

(`job_from_posting` is deliberately **not** imported here — the loop below appends plain
dicts to `discovered`, and Task 7's `JobStore` block converts them.)

After the board discovery loop and before the dedup block, add:

```python
    entries = load_board_entries(runtime.storage)
    unavailable: list[str] = []
    for entry in entries:
        label = entry.source + ":" + entry.board
        try:
            postings = build_source(entry).fetch(entry.board)
        except ATSFetchError as exc:
            unavailable.append(label)
            runtime.record_activity(runtime.new_event(
                "source_unavailable", "discover-and-apply",
                "Could not fetch " + label + ": " + str(exc),
                status="failed", entity_type="source", entity_id=label,
            ))
            continue
        for posting in postings:
            jd_text = posting.description or ""
            result = score_job(jd_text, profile, skills)
            discovered.append({
                "source_board": posting.source,
                "title": posting.title,
                "company": posting.company,
                "location": posting.location,
                "url": posting.url,
                "score": result["score"],
                "jd_text": jd_text,
            })
```

Extend the all-failed guard. Phase 9c placed an `if not boards:` check immediately after
the session pre-flight, *before* the board discovery loop. **Delete it from that position**
— it now depends on `unavailable`, which does not exist until after the API fetch loop —
and add this in its place, directly after the API fetch loop above:

```python
    if not boards and (not entries or len(unavailable) == len(entries)):
        rprint(
            "[red]No authorized board sessions and no reachable API sources. "
            "Run: careeros browser login --board <name>, or check config/sources.json[/red]"
        )
        raise typer.Exit(1)
```

Moving it is safe: with `boards` empty the discovery loop iterates nothing, so the only
cost of reaching the guard later is the API fetch attempt that has to happen anyway to know
whether a source is reachable. Note the existing Phase 9c test
`test_all_boards_unauthorized_exits_non_zero` must still pass unchanged — with no
`sources.json` entries, `not entries` is true and the guard fires exactly as before.

Add the summary segment:

```python
        + ", Unavailable sources: " + (", ".join(unavailable) if unavailable else "none")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config_sources.py tests/test_discover_and_apply_cmd.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 10 tests added since Task 7, no failures.

- [ ] **Step 7: Commit**

```bash
git add careeros/config_sources.py tests/test_config_sources.py careeros/cli/discover_and_apply_cmd.py tests/test_discover_and_apply_cmd.py
git commit -m "feat: fetch configured API board sources in discover-and-apply (Phase 10)"
```

---

### Task 9: Onboard prompt, `workspace validate`, and documentation

**Files:**
- Modify: `careeros/cli/onboard.py` (lines 103-111)
- Modify: `careeros/cli/workspace_cmd.py` (after the `required_paths` loop, ~line 92)
- Modify: `tests/test_onboard.py`, `tests/test_workspace_cmd.py`
- Modify: `README.md`, `ROADMAP.md`

**Interfaces:**
- Consumes: `load_board_entries` (Task 8).
- Produces: nothing code-facing.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workspace_cmd.py`:

```python
    def test_validate_reports_inert_source_entries(self, tmp_path):
        ws = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws)
        storage.atomic_write("config/sources.json", json.dumps({"sources": [
            {"source": "greenhouse", "mode": "SEARCH_ONLY"},
            {"source": "greenhouse", "board": "stripe", "company": "Stripe"},
        ]}).encode())
        result = runner.invoke(workspace_app, ["validate", "--workspace", ws])
        assert "inert (no board slug)" in result.output
        assert result.exit_code == 0   # inert entries are a note, not an error
```

In `tests/test_onboard.py`, the existing `_run_onboard` helper hardcodes the stdin sequence
with `greenhouse` as the sources answer. Give it a parameter so the new tests can vary that
answer, leaving every existing caller's behaviour unchanged:

```python
def _run_onboard(runner, tmp_path, resume_file, ws_name="workspace", sources="greenhouse"):
    ws_path = str(tmp_path / ws_name)
    # Input sequence: workspace path, resume path, confirm profile (y),
    # roles (blank), remote (any), comp (blank), locations (blank),
    # sources, goals (n)
    user_input = f"{ws_path}\n{resume_file}\ny\n\nany\n\n\n{sources}\nn\n"
    return runner.invoke(app, ["onboard"], input=user_input), ws_path
```

Then add:

```python
def test_onboard_writes_board_entries(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file,
                              sources="greenhouse:stripe:Stripe")
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("config/sources.json").decode())
    assert raw["sources"] == [
        {"source": "greenhouse", "board": "stripe", "company": "Stripe",
         "mode": "SEARCH_ONLY"}
    ]


def test_onboard_defaults_company_to_the_board_slug(tmp_path, resume_file,
                                                    mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file, sources="lever:acme")
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("config/sources.json").decode())
    assert raw["sources"] == [
        {"source": "lever", "board": "acme", "company": "acme", "mode": "SEARCH_ONLY"}
    ]


def test_onboard_skips_an_entry_with_no_board(tmp_path, resume_file,
                                              mock_extraction, monkeypatch):
    # The legacy answer shape. It must produce no entry rather than an inert one.
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file, sources="greenhouse")
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("config/sources.json").decode())
    assert raw["sources"] == []


def test_onboard_no_longer_offers_naukri(tmp_path, resume_file,
                                         mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    result, _ = _run_onboard(runner, tmp_path, resume_file,
                             sources="greenhouse:stripe:Stripe")
    assert "naukri" not in result.output
```

Note for Step 3: `test_onboard_skips_an_entry_with_no_board` pins the fact that every
*existing* caller of `_run_onboard` (which still answers `greenhouse`) now writes an empty
sources list. That is correct and intended — none of those tests assert on `sources.json`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workspace_cmd.py tests/test_onboard.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Update the onboard source prompt**

Replace lines 103-111 of `careeros/cli/onboard.py`:

```python
    rprint("\n[bold]Job Sources[/bold]")
    rprint("API job boards to poll, as source:board:Company triples.")
    rprint("Available sources: greenhouse, lever. Example: greenhouse:stripe:Stripe")
    sources_raw = Prompt.ask("Sources (comma-separated, or press enter to skip)", default="")
    sources = []
    for chunk in sources_raw.split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        sources.append({
            "source": parts[0],
            "board": parts[1],
            "company": parts[2] if len(parts) > 2 and parts[2] else parts[1],
            "mode": "SEARCH_ONLY",
        })
    runtime.storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())
```

`naukri` is gone: it has never had code behind it.

- [ ] **Step 4: Report inert entries in `workspace validate`**

After the `required_paths` loop in `careeros/cli/workspace_cmd.py`, add:

```python
    if storage.exists("config/sources.json"):
        try:
            raw = json.loads(storage.read("config/sources.json").decode())
        except json.JSONDecodeError:
            raw = {}
        inert = [
            str(item.get("source", "?"))
            for item in raw.get("sources", [])
            if isinstance(item, dict) and not item.get("board")
        ]
        for name in inert:
            rprint("[yellow]NOTE[/yellow] config/sources.json: '" + name
                   + "' entry is inert (no board slug)")
```

This is a note, not an error — it must not append to `errors` or change the exit code.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workspace_cmd.py tests/test_onboard.py -v`
Expected: all PASS.

- [ ] **Step 6: Update the documentation**

In `README.md`, under **Job discovery + scoring**, add:

```markdown
- **`careeros job search --source <greenhouse|lever> --company <slug>`** — search a company's
  public ATS board directly, no browser required. Deduplicates against jobs you already have.
```

Extend the `discover-and-apply` bullet to mention API sources:

```markdown
  It also polls any API boards configured in `config/sources.json`, so discovery does not
  depend on browser scraping alone.
```

Add a short subsection under **Workspace layout** documenting the `sources.json` entry shape:

```markdown
`config/sources.json` lists API job boards to poll:

​```json
{"sources": [{"source": "greenhouse", "board": "stripe", "company": "Stripe", "mode": "SEARCH_ONLY"}]}
​```

`board` is the company's slug on that ATS; `company` is the display name used for
deduplication. Entries without a `board` are inert — `careeros workspace validate` reports them.
```

In `ROADMAP.md`, mark Phase 10 shipped in the same voice as Phase 9's entry, and record what it did not solve:

```markdown
**Status: shipped.** `JobSource` connectors for Greenhouse and Lever, and a dedup engine
behind a single `JobStore` creation seam that all four job-creation paths route through.

**Known limitation:** dedup matches on canonical URL, else normalized company + title +
location. A location worded differently on two boards ("Remote" vs "San Francisco, CA")
still produces two records — an accepted trade for never silently collapsing two genuinely
different roles into one.
```

Bump the shipped-phase count from nine to ten in `README.md`, `ROADMAP.md`, and both places in `docs/index.html` (the `stat-n` figure and the "Nine phases" prose), and add a Phase 10 line to the shipped list.

- [ ] **Step 7: Verify the docs match reality**

Run: `grep -rn "naukri" README.md docs/ ROADMAP.md careeros/ --exclude-dir=superpowers`
Expected: no matches.

Run: `grep -rn "Nine phases\|>9<" README.md ROADMAP.md docs/index.html --exclude-dir=superpowers`
Expected: no matches.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 4 tests added since Task 8, no failures.

- [ ] **Step 9: Commit**

```bash
git add careeros/cli/onboard.py careeros/cli/workspace_cmd.py tests/test_onboard.py tests/test_workspace_cmd.py README.md ROADMAP.md docs/index.html
git commit -m "docs: document API board sources, report inert entries, mark Phase 10 shipped"
```
