# CareerOS Phase 4 — Auto-Apply Design

**Goal:** Add a `careeros apply <job-id>` command that generates a tailored cover letter via LLM, lets the user review and approve it, then uses the Phase 3 browser driver to fill and submit the job application form automatically.

**Architecture:** A new `careeros/browser/fillers/` package provides a `Filler` Protocol and per-platform implementations (Greenhouse, Lever, LinkedIn Easy Apply, Generic fallback). A new `careeros/skills/cover_letter.py` generates the cover letter. A new `careeros/cli/apply_cmd.py` orchestrates the full flow: generate → review → save → fill → update stage. No existing files are structurally changed beyond `main.py` (one import + `add_typer`) and a new workspace migration.

**Tech stack:** Python 3.11+, Playwright (existing dep from Phase 3), LiteLLM (existing), Pydantic v2, Typer + Rich.

**Phase context:**
- Phase 1: workspace, profile extraction, export/import.
- Phase 2: job pipeline — manual add, ATS search, stage tracking.
- Phase 3: browser automation — agent searches job boards, scores against profile.
- Phase 4 (this): auto-apply — generate cover letter, review, fill application form, update stage.

---

## Global Constraints

- Python 3.11+ — `str | None` union syntax; no walrus operator in type annotations.
- Pydantic v2 — `model_validate`, `model_dump_json`, `model_validate_json`, `model_copy` only.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/skills/`, `careeros/sources/`, or `careeros/browser/`.
- LLM calls via `litellm.completion()` only. Default model: `os.environ.get("CAREEROS_MODEL", "claude-haiku-4-5-20251001")`.
- Prompts: instruction string + concatenated user text. No `.format()` or f-strings with user data — prevents prompt injection.
- Activity log is append-only. No event is ever edited or deleted.
- Playwright imported lazily (inside function bodies, not at module level) except under `TYPE_CHECKING` guards.
- Integration tests marked `@pytest.mark.integration` and skipped by default (existing `conftest.py` hook handles this).
- No traceback ever reaches the user.
- Browser always runs `headless=False` — user watches the form fill and can intervene on CAPTCHAs or unexpected fields.

---

## 1. Workspace Migration

### 1.1 `careeros/workspace/migrations/m002_applications.py`

Creates the `applications/` directory in the workspace so cover letters and application artifacts have a home.

```python
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("002_applications")
def m002_applications(storage: StorageProvider) -> None:
    if not storage.exists("applications/.keep"):
        storage.write("applications/.keep", b"")
```

`m001_initial` must run first (already guaranteed by the migration runner's ordered execution).

---

## 2. Cover Letter Generation

### 2.1 `careeros/skills/cover_letter.py`

```python
import os
import litellm
from careeros.core.models import Goals, Profile, Skills

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_JD_CAP = 4000

_CL_INSTRUCTIONS = """\
Write a concise, professional cover letter (3-4 paragraphs) for the candidate below.
- Open with why this specific role interests them based on their goals
- Highlight 2-3 relevant skills from their profile that match the job description
- Close with a brief call to action
Return ONLY the cover letter text — no subject line, no markdown, no commentary.

Candidate profile:
"""

_FAILURE = ""


def _build_profile_text(profile: Profile, skills: Skills, goals: Goals) -> str:
    lines = []
    if profile.title:
        lines.append("Title: " + profile.title)
    if profile.summary:
        lines.append("Summary: " + profile.summary)
    if skills.skills:
        lines.append("Skills: " + ", ".join(s.name for s in skills.skills))
    if goals.short_term:
        lines.append("Short-term goals: " + "; ".join(goals.short_term))
    return "\n".join(lines)


def generate_cover_letter(
    jd_text: str,
    profile: Profile,
    skills: Skills,
    goals: Goals,
    model: str | None = None,
) -> str:
    """Generate a tailored cover letter. Returns "" on any failure. Never raises."""
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    profile_text = _build_profile_text(profile, skills, goals)
    prompt = _CL_INSTRUCTIONS + profile_text + "\n\nJob description:\n" + jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return _FAILURE
```

---

## 3. Filler Protocol

### 3.1 `careeros/browser/fillers/__init__.py`

Empty.

### 3.2 `careeros/browser/fillers/base.py`

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job


class Filler(Protocol):
    platform: str

    def can_handle(self, url: str) -> bool:
        """Pure URL pattern check — no browser needed. Must be deterministic."""
        ...

    def fill(self, page: Page, job: Job, cover_letter_path: str, resume_path: str) -> bool:
        """
        Navigate to job.url, fill the application form, upload documents, submit.
        Returns True on successful submission, False on detectable failure.
        Raises are caught by apply_cmd and shown as friendly messages.
        cover_letter_path and resume_path are absolute filesystem paths.
        """
        ...
```

**Normalized fill contract:**
- Always fills: name (from profile), email (from profile), resume upload
- Fills if field present: cover letter upload, phone, location
- On required field it cannot fill: returns `False` with a printed warning
- Never auto-submits on LinkedIn Easy Apply if a required step cannot be completed — returns `False` and leaves the browser open so the user can finish manually

### 3.3 Per-Platform Fillers

**`careeros/browser/fillers/greenhouse.py` — `GreenhouseFiller`**

`can_handle`: URL contains `boards.greenhouse.io` or matches `greenhouse.io/jobs`.

`fill` steps:
1. `page.goto(job.url, timeout=20000)`
2. Fill `input[name="job_application[first_name]"]` / `last_name` / `email` from profile
3. Locate resume upload input (`input[type="file"]` near "Resume" label) → `set_input_files(resume_path)`
4. If cover letter upload field present → `set_input_files(cover_letter_path)`
5. Click submit button (`input[type="submit"]` or `button[type="submit"]`)
6. Wait for URL change or success indicator (up to 10s); return `True` if detected, `False` if timeout

**`careeros/browser/fillers/lever.py` — `LeverFiller`**

`can_handle`: URL contains `jobs.lever.co`.

`fill` steps:
1. `page.goto(job.url, timeout=20000)`
2. Click "Apply" button to open the application form
3. Fill name / email fields
4. Upload resume → `input[type="file"]`
5. Upload cover letter if field present
6. Click submit; wait for confirmation; return `True`/`False`

**`careeros/browser/fillers/linkedin.py` — `LinkedInFiller`**

`can_handle`: URL contains `linkedin.com/jobs/`.

`fill` steps:
1. `page.goto(job.url, timeout=20000)`
2. Click "Easy Apply" button (locator: `button:has-text("Easy Apply")`)
3. Step through modal pages:
   - Fill contact info fields if present (name, email, phone)
   - On resume step: if an upload option is present, use `set_input_files(resume_path)`; otherwise accept LinkedIn's stored resume
   - On cover letter step: paste cover letter text into the textarea
   - On each page: click "Next" or "Review"
4. On the Review page: click "Submit application"
5. Wait for success modal ("Your application was sent"); return `True`
6. If any required step fails or "Easy Apply" button not found: return `False`, leave browser open

**`careeros/browser/fillers/generic.py` — `GenericFiller`**

`can_handle`: always `True` (fallback).

`fill` steps:
1. `page.goto(job.url, timeout=20000)`
2. Locate file inputs: `page.query_selector_all('input[type="file"]')` → upload resume to the first one
3. Fill text inputs matching heuristics:
   - `name` / `placeholder` / `label` containing "first name" → profile.name.split()[0]
   - "last name" → profile.name.split()[-1]
   - "email" → profile.email
   - "phone" / "telephone" → skip (not in profile model)
4. Print warning: "Generic filler applied — review the form before submitting. Fields may be incomplete."
5. Return `False` (never auto-submits on generic — too risky; leaves browser open for user)

---

## 4. CLI Command

### 4.1 `careeros/cli/apply_cmd.py`

```
careeros apply <JOB_ID>
  --workspace TEXT    override workspace path
  --model TEXT        override LLM model for cover letter generation
```

**Full flow:**

```python
apply_app = typer.Typer(help="Apply to saved jobs.")

FILLERS = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]
MAX_REGENERATIONS = 5

@apply_app.command()
def apply_cmd(
    job_id: str = typer.Argument(...),
    workspace: str = typer.Option(None, "--workspace"),
    model: str = typer.Option(None, "--model"),
) -> None:
```

Steps:

1. **Load workspace** — `_get_storage(workspace)` → exit 1 if no workspace configured
2. **Load job** — `Job.load(storage, job_id)` → exit 1 if not found
3. **Validate URL** — if `job.url` is None: print "Job has no URL — add one with `careeros job update`", exit 1
4. **Load profile, skills, goals** — `Profile.load_or_empty`, `Skills.load_or_empty`, `Goals.load_or_empty`
5. **Find resume** — scan `storage.list("resumes/versions/")` for `.pdf` and `.docx` files; if none: print "No resume found in resumes/versions/ — add one first", exit 1; if multiple: display numbered list, `Prompt.ask("Pick resume [1]")`, default to 1; resolve to absolute path via `storage` → stored as `resume_path`
6. **Get JD text** — use `job.description` if present (cap at 4000 chars); otherwise open browser briefly to `fetch_jd_text(page, job.url)` — reuse `launch_browser`
7. **Generate cover letter** — show Rich spinner "Generating cover letter..."; call `generate_cover_letter(jd_text, profile, skills, goals, model=model)`; if returns `""`: print "Cover letter generation failed. Check your LLM configuration.", exit 1
8. **Review loop** (up to `MAX_REGENERATIONS` regenerations):
   - Display cover letter in `Rich.Panel` titled "Cover Letter — [company] / [title]"
   - `Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a","r","q"], default="a")`
   - `q` → print "Aborted.", exit 0
   - `r` → regenerate (increment counter); if counter == MAX_REGENERATIONS: print "Max regenerations reached — accepting current version or quit"; show only `[A]ccept / [Q]uit`
   - `a` → break loop
9. **Save cover letter** — `storage.atomic_write("applications/" + job_id + "/cover_letter.txt", cover_letter.encode())`; resolve to absolute path as `cover_letter_path`
10. **Detect filler** — iterate `FILLERS`, first `filler.can_handle(job.url)` wins
11. **Confirm** — print "About to fill the [filler.platform] application for [company] — [title]. Proceed? [y/N]"; `Confirm.ask(...)` default False; if no → print "Aborted.", exit 0
12. **Launch browser + fill**:
    ```python
    try:
        with launch_browser(headless=False) as (_, page):
            success = filler.fill(page, job, cover_letter_path, resume_path)
    except ImportError:
        rprint("Playwright not installed. Run: pip install playwright && playwright install chrome")
        raise typer.Exit(1)
    except Exception as e:
        rprint("Browser error: " + str(e))
        raise typer.Exit(1)
    ```
13. **On success** (`success is True`):
    - Load job fresh, `model_copy(update={"stage": "applied", "applied_at": now, "updated_at": now})`, save
    - Log `job_applied` activity event: `"Applied to " + job.company + " — " + job.title`
    - Print "Applied to [company] — [title]. Stage updated to 'applied'."
14. **On failure** (`success is False`):
    - Print "Form fill incomplete — review the browser window. Stage not updated."
    - Exit 1

### 4.2 `careeros/cli/main.py` (modify)

```python
from careeros.cli.apply_cmd import apply_app
app.add_typer(apply_app, name="apply")
```

---

## 5. Storage Layout

```
workspace/
  applications/
    <job_id>/
      cover_letter.txt    # saved after user accepts
  resumes/
    versions/
      resume.pdf          # user's master resume (pre-existing from Phase 1)
```

`cover_letter_path` and `resume_path` passed to `filler.fill()` are absolute filesystem paths resolved from the `LocalFilesystemStorage` root. The `Filler.fill` contract receives paths (not bytes) because Playwright's `set_input_files` requires a filesystem path.

`LocalFilesystemStorage` must expose a public `resolve(path: str) -> str` method that calls `_resolve(path)` and returns `str(result)`. This preserves path-escape validation while giving `apply_cmd.py` an absolute filesystem path to hand to Playwright's `set_input_files`. Add to `careeros/storage/filesystem.py`:

```python
def resolve(self, path: str) -> str:
    """Return the absolute filesystem path for a workspace-relative path."""
    return str(self._resolve(path))
```

---

## 6. Error Handling

| Scenario | Behaviour |
|---|---|
| Playwright not installed | Print install instructions, exit 1 |
| No workspace configured | "No workspace configured. Run `careeros onboard` first.", exit 1 |
| Job ID not found | "Job [id] not found.", exit 1 |
| Job has no URL | "Job has no URL — add one with `careeros job update`", exit 1 |
| No resume in workspace | "No resume found in resumes/versions/ — add one first", exit 1 |
| Cover letter generation fails | "Cover letter generation failed. Check your LLM configuration.", exit 1 |
| Filler returns False | "Form fill incomplete — review the browser window. Stage not updated.", exit 1 |
| Browser exception | "Browser error: [message]. Stage not updated.", exit 1 |
| User quits review loop | "Aborted.", exit 0 |
| User declines final confirm | "Aborted.", exit 0 |

No traceback ever reaches the user.

---

## 7. Testing

### 7.1 `tests/test_cover_letter.py` (~8 tests, offline)

- LLM returns text → `generate_cover_letter` returns it stripped
- LLM raises → returns `""`
- Bad JSON / unexpected response → returns `""`
- `CAREEROS_MODEL` env var respected
- `model` param overrides env var
- Profile title appears in prompt (concatenation, not f-string)
- JD text capped at 4000 chars in prompt
- Goals short_term included in prompt

### 7.2 `tests/test_fillers.py` (~10 tests, offline)

`can_handle` URL pattern tests — no browser required:

- `GreenhouseFiller.can_handle("https://boards.greenhouse.io/acme/jobs/123")` → True
- `GreenhouseFiller.can_handle("https://jobs.lever.co/acme/123")` → False
- `LeverFiller.can_handle("https://jobs.lever.co/stripe/abc")` → True
- `LeverFiller.can_handle("https://linkedin.com/jobs/view/123")` → False
- `LinkedInFiller.can_handle("https://www.linkedin.com/jobs/view/123456")` → True
- `LinkedInFiller.can_handle("https://boards.greenhouse.io/acme/jobs/123")` → False
- `GenericFiller.can_handle("https://anything.com/careers/job")` → True
- `GenericFiller.can_handle("")` → True (always)
- FILLERS list order: Generic is last

### 7.3 `tests/test_apply_cmd.py` (~14 tests, offline)

Typer CliRunner tests with mocked browser, mocked fillers, mocked `generate_cover_letter`:

- Happy path: filler returns True → job stage updated to "applied", `applied_at` set, `job_applied` logged
- Filler returns False → stage not updated, exit 1
- No workspace → exit 1
- Job not found → exit 1
- Job has no URL → exit 1
- No resume in workspace → exit 1
- Cover letter generation returns `""` → exit 1, "generation failed"
- User enters `q` in review loop → exit 0, nothing saved
- User regenerates then accepts → cover letter re-generated, saved on accept
- Max regenerations reached → forced Accept/Quit prompt shown
- User declines final confirm → exit 0, nothing saved
- Playwright ImportError → exit 1 with install instructions
- `--model` flag propagated to `generate_cover_letter`
- Activity log contains `job_applied` event after successful apply

### 7.4 `tests/integration/test_live_apply.py` (marked `@pytest.mark.integration`)

- `test_greenhouse_fill_live` — requires Chrome + active session + a known Greenhouse test posting URL
- `test_lever_fill_live` — same for Lever

---

## 8. File Map

| Path | Action |
|---|---|
| `careeros/browser/fillers/__init__.py` | New — empty |
| `careeros/browser/fillers/base.py` | New — `Filler` Protocol |
| `careeros/browser/fillers/greenhouse.py` | New — `GreenhouseFiller` |
| `careeros/browser/fillers/lever.py` | New — `LeverFiller` |
| `careeros/browser/fillers/linkedin.py` | New — `LinkedInFiller` |
| `careeros/browser/fillers/generic.py` | New — `GenericFiller` |
| `careeros/skills/cover_letter.py` | New — `generate_cover_letter` |
| `careeros/cli/apply_cmd.py` | New — `apply_app`, `apply_cmd` |
| `careeros/cli/main.py` | Modify — register `apply_app` |
| `careeros/workspace/migrations/m002_applications.py` | New — creates `applications/` dir |
| `careeros/workspace/migrations/__init__.py` | Modify — import `m002_applications` at bottom to trigger `@register` |
| `careeros/storage/filesystem.py` | Modify — add public `resolve(path: str) -> str` method |
| `tests/test_cover_letter.py` | New |
| `tests/test_fillers.py` | New |
| `tests/test_apply_cmd.py` | New |
| `tests/integration/test_live_apply.py` | New |
