# CareerOS Phase 2 — Job Pipeline Design

**Goal:** Add a job pipeline to the CareerOS workspace: discover jobs (manually or via ATS APIs), track them through five stages, and log all transitions to the activity log.

**Architecture:** One JSON file per job in `jobs/`. Stage transitions reuse the existing append-only activity log. LLM parses raw JD text into structured fields on `job add`. Greenhouse and Lever public APIs (no auth) power `job search`. All storage goes through `StorageProvider` — no direct file I/O outside the storage layer.

**Tech stack:** Python 3.11+, Pydantic v2, LiteLLM (already in deps), `urllib.request` (stdlib) for ATS HTTP calls, Typer + Rich for CLI.

**Phase context:**
- Phase 1 delivered: workspace, profile extraction, skills/goals/preferences, export/import.
- Phase 2 delivers: job data model, job pipeline CLI, LLM JD parsing, Greenhouse/Lever search.
- Phase 3 (planned): browser automation — agent searches job boards, scores against profile.
- Phase 4 (planned): auto-apply — agent fills forms, uploads tailored documents, logs results.

---

## Global Constraints

- Python 3.11+ — no walrus operator in type annotations, `str | None` union syntax is fine.
- Pydantic v2 — `model_validate`, `model_dump_json`, `model_validate_json` only; no v1 APIs.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/skills/`, or `careeros/sources/`.
- LLM calls via `litellm.completion()` only. Model selected from `CAREEROS_MODEL` env var, fallback `claude-haiku-4-5-20251001`.
- Prompts are instruction string + concatenated user text. No `.format()` or f-strings with user data — prevents prompt injection.
- Activity log is append-only. No event is ever edited or deleted.
- No new runtime dependencies beyond `litellm` (already declared). HTTP via `urllib.request` stdlib.
- All tests run offline — `litellm.completion` and `urllib.request.urlopen` are mocked.

---

## 1. Data Model

### 1.1 `Job` — `careeros/core/models.py`

Add `Job` to the existing models file alongside `Profile`, `Skills`, `Preferences`, `Goals`.

```python
class Job(BaseModel):
    id: str                       # "acme-senior-sre-a1b2" — see §1.2
    source: str                   # "manual" | "greenhouse" | "lever"
    source_id: str | None = None  # ATS posting ID; None for manual adds
    url: str | None = None
    company: str
    title: str
    location: str | None = None
    remote: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    description: str | None = None   # raw JD text, capped at 4 000 chars on write
    requirements: list[str] = []
    stage: str = "saved"
    applied_at: str | None = None    # ISO 8601; set automatically when stage → "applied"
    notes: list[str] = []            # each entry: "2026-09-18T10:00:00Z — <text>"
    created_at: str                  # ISO 8601
    updated_at: str                  # ISO 8601
```

**Valid stages (constant):**
```python
JOB_STAGES = ("saved", "applied", "interviewing", "offer", "closed")
```

**Methods on `Job`:**

```python
def save(self, storage: StorageProvider) -> None:
    # writes to jobs/<self.id>.json via atomic_write

@classmethod
def load(cls, storage: StorageProvider, job_id: str) -> "Job":
    # reads jobs/<job_id>.json; raises FileNotFoundError if absent

@classmethod
def list_all(cls, storage: StorageProvider) -> list["Job"]:
    # storage.list("jobs") → filter *.json → load each → return sorted by created_at desc
```

### 1.2 ID generation — `careeros/core/job_id.py` (new file)

```python
def make_job_id(company: str, title: str) -> str:
    # slugify company + title (lowercase, alphanumeric + hyphens, max 32 chars total)
    # append "-" + 4 hex chars from os.urandom(2).hex()
    # example: "acme-senior-sre-a1b2"
```

The 4-hex suffix makes collisions astronomically unlikely for a personal workspace.

### 1.3 Storage layout

```
~/my-career/
  jobs/
    acme-senior-sre-a1b2.json
    stripe-staff-eng-c3d4.json
    ...
```

### 1.4 Activity log events (new event types)

All written via existing `ActivityLogger.log()` — no new infrastructure.

| `event_type` | When |
|---|---|
| `job_added` | `job add` or `job search` saves a job |
| `job_stage_changed` | `job update --stage` transitions a job |
| `job_note_added` | `job note` appends a note |

`job_stage_changed` event metadata includes `job_id`, `from_stage`, `to_stage`.

---

## 2. LLM JD Parsing

### 2.1 New file: `careeros/skills/job_extract.py`

```python
DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"  # same constant as profile_extract

def extract_job_fields(jd_text: str, model: str | None = None) -> dict:
    """
    Call LiteLLM once with jd_text to extract structured job fields.
    Returns a dict with keys: company, title, location, remote, salary_min,
    salary_max, currency, requirements, summary.
    Missing fields are None or []. Never raises — returns partial dict on failure.
    """
```

**Prompt contract:** instruction-only string concatenated with `jd_text`. No `.format()`.

**Response contract:** LLM returns JSON. `_parse_json()` (same helper as `profile_extract`) strips markdown fences before `json.loads`.

**Failure handling:** if `json.loads` fails or `litellm.completion` raises, return `{}`. The CLI shows what it got and lets the user fill in fields manually — nothing is saved until the user confirms.

**JD length cap:** truncate `jd_text` to 4 000 chars before sending. Enough for requirements; avoids burning tokens on boilerplate.

**Target JSON shape the prompt requests:**
```json
{
  "company": "<string or null>",
  "title": "<string or null>",
  "location": "<string or null>",
  "remote": "<true | false | null>",
  "salary_min": "<integer or null>",
  "salary_max": "<integer or null>",
  "currency": "<USD | GBP | EUR | ... or null>",
  "requirements": ["<string>", "..."],
  "summary": "<1-2 sentence summary or null>"
}
```

---

## 3. ATS Integration

### 3.1 New file: `careeros/sources/ats.py`

```python
class ATSFetchError(Exception):
    pass

def fetch_greenhouse(company: str) -> list[dict]:
    """
    GET https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true
    Returns list of normalized job dicts.
    Raises ATSFetchError on 404 (company not found), timeout, or malformed JSON.
    """

def fetch_lever(company: str) -> list[dict]:
    """
    GET https://api.lever.co/v0/postings/{company}?mode=json
    Returns list of normalized job dicts.
    Raises ATSFetchError on 404, timeout, or malformed JSON.
    """
```

**Normalized job dict shape** (same for both sources):
```python
{
    "source_id": str,
    "title": str,
    "url": str,
    "location": str | None,
    "description": str | None,   # plain text, truncated to 4 000 chars
}
```

**Greenhouse field mapping:**
- `source_id` ← `job["id"]` (cast to str)
- `title` ← `job["title"]`
- `url` ← `job["absolute_url"]`
- `location` ← `job["location"]["name"]` (or None)
- `description` ← strip HTML from `job["content"]` using `html.parser` (stdlib)

**Lever field mapping:**
- `source_id` ← `posting["id"]`
- `title` ← `posting["text"]`
- `url` ← `posting["hostedUrl"]`
- `location` ← `posting["categories"]["location"]` (or None)
- `description` ← `posting.get("descriptionPlain", "")` truncated

**HTTP:** `urllib.request.urlopen` with a 10-second timeout. No third-party HTTP library.

**HTML stripping:** `html.parser` from stdlib (`html.parser.HTMLParser` subclass). No `beautifulsoup4`.

---

## 4. CLI Commands

### 4.1 New file: `careeros/cli/job_cmd.py`

Register a `job` Typer sub-app in `careeros/cli/main.py` (same pattern as `workspace`).

#### `careeros job add`

```
Options:
  --workspace TEXT   override active workspace path
```

Flow:
1. Prompt: `URL (optional, press enter to skip):`
2. Prompt: `Paste job description (blank line + enter when done):`  
   — multi-line input, stops on empty line
3. If JD text provided: call `extract_job_fields(jd_text)`, show extracted fields
4. Confirm/edit each field interactively (pre-filled with extracted value or blank)
5. Generate ID via `make_job_id(company, title)`
6. Create `Job`, save to workspace, log `job_added`
7. Print: `Saved as jobs/<id>  (stage: saved)`

#### `careeros job search`

```
Options:
  --source TEXT    greenhouse | lever  [required]
  --company TEXT   company slug (e.g. stripe, acme)  [required]
  --workspace TEXT
```

Flow:
1. Call `fetch_greenhouse` or `fetch_lever`; catch `ATSFetchError` → print friendly message, exit 1
2. Display numbered table: `#  title  location  url`
3. Prompt: `Pick jobs to save (e.g. 1 3 5, or q to quit):`
4. For each picked posting: create `Job(source=..., stage="saved")`, save, log `job_added`
5. Print: `Saved N job(s)`

#### `careeros job list`

```
Options:
  --stage TEXT   filter by stage (all if omitted)
  --workspace TEXT
```

Output: Rich table — `id`, `company`, `title`, `stage`, `applied_at` (blank if None).  
Empty pipeline: print `No jobs found.`

#### `careeros job show <id>`

Output: Rich panel with all Job fields. Notes printed as a bulleted list.  
Unknown ID: print `Job <id> not found.`, exit 1.

#### `careeros job update <id> --stage <stage>`

```
Arguments:
  id     job ID
Options:
  --stage TEXT   one of: saved applied interviewing offer closed  [required]
  --workspace TEXT
```

Validation: reject unknown stage, print valid options, exit 1.  
On success: update `stage`, set `applied_at` if moving to `applied`, update `updated_at`, save, log `job_stage_changed`.  
Print: `<id>  saved → applied`

#### `careeros job note <id> <text>`

Append `"<ISO timestamp> — <text>"` to `job.notes`, update `updated_at`, save, log `job_note_added`.  
Print: `Note added to <id>`

---

## 5. Error Handling

| Scenario | Behaviour |
|---|---|
| `job show` / `job update` / `job note` with unknown ID | Print "Job `<id>` not found.", exit 1 |
| `job update` with invalid stage | Print "Invalid stage `<s>`. Valid: saved applied interviewing offer closed", exit 1 |
| `job search` — company not found (404) | Print "Company `<slug>` not found on `<source>`.", exit 1 |
| `job search` — network timeout | Print "Could not reach `<source>` API. Check your connection.", exit 1 |
| `job add` — LLM extraction fails | Warn "Could not extract fields automatically.", proceed with blank pre-fills |
| No active workspace configured | Print "No workspace configured. Run `careeros onboard` first.", exit 1 |

No traceback ever reaches the user.

---

## 6. Testing

### 6.1 `tests/test_job_model.py`

- `Job.save` writes to `jobs/<id>.json`
- `Job.load` round-trips through JSON
- `Job.list_all` returns all jobs sorted by `created_at` descending
- `Job.list_all` with empty `jobs/` returns `[]`
- `applied_at` is set when `stage` transitions to `"applied"` (tested via `job update` logic)
- `make_job_id` produces `<slug>-<4hex>` format, two calls produce different IDs

### 6.2 `tests/test_job_extract.py`

- Full extraction: LLM returns valid JSON → all fields populated
- Partial extraction: LLM returns JSON with `null` salary → `Job.salary_min` is None
- Fenced response: LLM returns ` ```json\n{...}\n``` ` → `_parse_json` strips fences
- Failed extraction: `litellm.completion` raises → returns `{}`
- `CAREEROS_MODEL` env var used when set
- Resume text included in prompt (anti-injection: concatenated, not formatted)

### 6.3 `tests/test_ats.py`

- `fetch_greenhouse` parses fixture JSON into normalized dicts
- `fetch_lever` parses fixture JSON into normalized dicts
- 404 response → raises `ATSFetchError`
- Timeout (`urllib.error.URLError`) → raises `ATSFetchError`
- HTML stripped from Greenhouse `content` field

### 6.4 `tests/test_job_cmd.py`

- `job add` end-to-end: mocked `extract_job_fields`, user confirms → job file written, activity logged
- `job add` extraction failure: proceeds with blank fields
- `job list` empty: prints "No jobs found."
- `job list` with jobs: table contains job IDs
- `job list --stage applied`: only shows applied jobs
- `job show` known ID: exits 0
- `job show` unknown ID: exits 1
- `job update` valid stage: stage changes, activity logged, `applied_at` set when → applied
- `job update` invalid stage: exits 1
- `job search` success: mocked ATS fetch, user picks job → saved
- `job search` `ATSFetchError`: exits 1 with friendly message
- `job note` appends note to job

---

## 7. File Map

| Path | Action |
|---|---|
| `careeros/core/models.py` | Add `Job`, `JOB_STAGES` |
| `careeros/core/job_id.py` | New — `make_job_id` |
| `careeros/skills/job_extract.py` | New — `extract_job_fields`, `_parse_json` (shared or imported) |
| `careeros/sources/__init__.py` | New — empty |
| `careeros/sources/ats.py` | New — `fetch_greenhouse`, `fetch_lever`, `ATSFetchError` |
| `careeros/cli/job_cmd.py` | New — all six `job` subcommands |
| `careeros/cli/main.py` | Modify — register `job` sub-app |
| `tests/test_job_model.py` | New |
| `tests/test_job_extract.py` | New |
| `tests/test_ats.py` | New |
| `tests/test_job_cmd.py` | New |
