# Phase 8 — Compensation Research — Design Spec

**Date:** 2026-09-19
**Status:** Approved for implementation planning
**Author:** Kaushal + Claude

---

## 1. What This Is

The master design spec (`docs/superpowers/specs/2026-09-18-careeros-design.md`, §7) lists "Compensation research skill + evidence storage (source, date, geo, role, seniority, currency, base/bonus/equity, confidence)" under Phase 2 ("Career Intelligence"), but it was never built when that phase's job-matching pipeline shipped. This spec picks it up as its own phase, following the same pattern established in Phase 7 (`research company`/`research people`): a new `careeros research compensation --job <id>` command, browser-driven data collection from a public source, LLM-extracted structured output.

**Scope decisions from brainstorming:**
- Tied to a specific saved `Job` (not a free-form query) — matches the existing `--job`-anchored shape of `research company`/`research people`.
- Browser-driven against levels.fyi's public company/role pages, not LLM-only — consistent with Phase 7's "real public sources, not model guesses" precedent, accepting that levels.fyi's public (non-login) pages may yield thin data for less common companies/roles. The `confidence` field exists precisely to make that honesty visible in the stored data rather than hiding it behind a plausible-sounding number.
- Each research run creates a new dated evidence record rather than overwriting a prior one — market compensation data is meant to accumulate as a history (comp shifts over time, across sources), unlike `Company`/`Person` facts (Phase 7) which are idempotent-by-identity.

---

## 2. Architecture

New model `CompensationDataPoint` appended to `careeros/core/models.py`. No new `Scraper`-Protocol class — company/role page fetching reuses the existing `fetch_jd_text(page, url) -> str` helper directly, for the same reason Phase 7's `research company` did: a single-page fetch doesn't fit the multi-result `Scraper.search` shape. A new skill `careeros/skills/compensation_research.py` (`extract_compensation_data`) follows `company_research.py`'s exact browser-content-to-LLM-extraction pattern. One new CLI command, `careeros research compensation --job <id>`, added as a third subcommand of the existing `research_app` Typer group (alongside `company`/`people` from Phase 7).

---

## 3. Model

```python
class CompensationDataPoint(BaseModel):
    id: str
    job_id: str
    role: str
    seniority: str | None = None
    geo: str | None = None
    company: str | None = None
    currency: str = "USD"
    base_min: int | None = None
    base_max: int | None = None
    bonus: str | None = None
    equity: str | None = None
    source: str = "levels.fyi"
    source_url: str | None = None
    confidence: str = "low"
    researched_at: str
```

`role` comes from `job.title`, `geo` from `job.location`, `company` from `job.company`. `seniority` comes from `Preferences.load_or_empty(storage).seniority` — the only place seniority already exists in the data model (`Profile` has no seniority field; only `years_of_experience`). `confidence` (`"low"`/`"medium"`/`"high"`) is set by `extract_compensation_data` based on how much real data the fetched page actually contained — a thin or empty page always yields `"low"`, never an unearned-sounding number.

Stored at `compensation/<id>.json`. `id` is generated via a new `make_compensation_id(company: str, role: str) -> str` helper in `careeros/core/ids.py`, following `make_job_id`'s random-suffix pattern (not `make_company_id`/`make_person_id`'s deterministic pattern) — each research run produces a new record, since repeated research over time is meant to build a history, not overwrite the prior data point.

---

## 4. Page Fetching + Extraction Skill

URL construction, in `careeros/cli/research_cmd.py`'s new `compensation` command:

```python
import urllib.parse
url = (
    "https://www.levels.fyi/companies/" + urllib.parse.quote(_slugify(job_obj.company))
    + "/salaries/" + urllib.parse.quote(_slugify(job_obj.title))
)
```

Both `company` and `role` segments are URL-encoded via `urllib.parse.quote` — Phase 7's final review caught an unencoded-URL bug in the analogous `research company` command; this spec builds the fix in from the start rather than repeating it. `_slugify` reuses the existing lowercase-and-hyphenate logic already duplicated across `careeros/core/job_id.py` and `careeros/core/ids.py` (a small local copy in `research_cmd.py`, matching this codebase's established per-file small-helper convention rather than a new shared import).

```python
# careeros/skills/compensation_research.py
def extract_compensation_data(page_content: str, model: str | None = None) -> dict:
```

Returns `{"base_min": int | None, "base_max": int | None, "bonus": str | None, "equity": str | None, "confidence": str}`. Follows `company_research.py`'s exact structure: a fixed instruction string, `litellm.completion` with the `CAREEROS_MODEL` env var pattern, JSON parsing with markdown-fence stripping, and a fallback return value (`{"base_min": None, "base_max": None, "bonus": None, "equity": None, "confidence": "low"}`) on any exception — never raises.

---

## 5. CLI Flow

```
careeros research compensation --job <id>
```

1. Load `Job` (missing → exit 1, matching `research company`/`research people`)
2. Load `Preferences.load_or_empty(storage)` for `seniority`
3. Build the levels.fyi URL (URL-encoded, per §4)
4. Launch browser `headless=True`, fetch page content via `fetch_jd_text(page, url)`
5. Call `extract_compensation_data(page_content)`
6. Construct `CompensationDataPoint(id=make_compensation_id(job_obj.company, job_obj.title), job_id=job, role=job_obj.title, seniority=prefs.seniority, geo=job_obj.location, company=job_obj.company, source_url=url, base_min=..., base_max=..., bonus=..., equity=..., confidence=..., researched_at=_now())`, save it — `source_url=url` is the exact levels.fyi page fetched in step 4, so the evidence record is traceable back to where the data came from (the master spec's whole point in calling this "evidence storage")
7. Log `compensation_researched` (activity summary via string concatenation, includes company/role/confidence — never raw scraped page content)

No approval gate — this is a local research action with no external side effect, same reasoning as `research company`/`research people`.

---

## 6. Testing

- `tests/test_compensation_research.py` — `extract_compensation_data` with mocked LLM: successful extraction, LLM failure returns the all-fallback dict with `confidence="low"`, malformed JSON handled gracefully
- `tests/test_company_person_outreach_models.py` (extended, or a new file) — `CompensationDataPoint` save/load round-trip
- `tests/test_ids.py` (extended) — `make_compensation_id` produces a non-deterministic result (two calls with identical inputs produce different IDs, unlike `make_company_id`)
- `tests/test_research_cmd.py` (extended) — `TestResearchCompensation` class: job-not-found exits 1; successful research saves a `CompensationDataPoint` and logs `compensation_researched`; thin/empty page content still saves a record with `confidence="low"` rather than failing the command

---

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/`
- Activity summaries built via string concatenation only — no f-strings/`.format()` with user data
- Every meaningful action produces an activity event — standing principle from Phase 7, applies here unchanged
- URLs built from user/workspace data (company name, job title) are always passed through `urllib.parse.quote` before concatenation — no repeat of the Phase 7 unencoded-URL defect
- `confidence` must never be `"medium"` or `"high"` when the underlying page content was empty or clearly too thin to support a real estimate — the extraction skill's fallback path always returns `"low"`

---

## Exit Condition

`careeros research compensation --job <id>` fetches a real levels.fyi page for the job's company and role, extracts whatever structured compensation data is actually present (with an honest `confidence` rating reflecting data quality), saves a new dated `CompensationDataPoint` record, and logs the action — without ever fabricating a number when the source page had none to offer.
