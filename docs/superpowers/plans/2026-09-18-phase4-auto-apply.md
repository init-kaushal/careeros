# Phase 4 Auto-Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `careeros apply <job-id>` — generates a tailored cover letter via LLM, presents a review loop (Accept / Regenerate / Quit), then uses the Phase 3 browser driver to fill and submit job application forms across Greenhouse, Lever, LinkedIn Easy Apply, and a generic fallback.

**Architecture:** A new `Filler` Protocol in `careeros/browser/fillers/` mirrors Phase 3's `Scraper` pattern — `can_handle(url)` dispatches to the right per-platform implementation, `fill(page, job, profile, cover_letter_text, cover_letter_path, resume_path)` fills the form. `cover_letter.py` generates the letter via LiteLLM. `apply_cmd.py` orchestrates: load workspace → find resume → generate → review loop → save → detect filler → confirm → fill → update stage.

**Tech Stack:** Python 3.11+, Playwright (existing from Phase 3), LiteLLM (existing), Pydantic v2, Typer + Rich.

**Spec:** `docs/superpowers/specs/2026-09-18-phase4-auto-apply-design.md`

## Global Constraints

- Python 3.11+ — `str | None` syntax; no walrus operator in type annotations
- Pydantic v2 — `model_validate`, `model_dump_json`, `model_validate_json`, `model_copy` only
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/skills/`, `careeros/sources/`, or `careeros/browser/`
- LLM calls via `litellm.completion()` only; default model: `os.environ.get("CAREEROS_MODEL", "claude-haiku-4-5-20251001")`
- Prompts: instruction string + concatenated user text — no `.format()` or f-strings with user data
- Activity log is append-only; no event is ever edited or deleted
- Playwright imported lazily (inside function bodies), never at module level, except under `TYPE_CHECKING` guards
- Integration tests marked `@pytest.mark.integration`; skipped by default (existing `tests/conftest.py` hook handles this automatically)
- No traceback ever reaches the user
- Browser always runs `headless=False`

---

### Task 1: Storage resolve method + workspace migration

**Files:**
- Modify: `careeros/storage/filesystem.py` — add public `resolve(path: str) -> str` method
- Create: `careeros/workspace/migrations/m002_applications.py`
- Modify: `careeros/workspace/migrations/__init__.py` — import m002 to trigger `@register`
- Test: `tests/test_storage.py` — add 2 tests for `resolve`
- Test: `tests/test_migrations.py` — add 1 test for m002

**Interfaces:**
- Produces: `LocalFilesystemStorage.resolve(path: str) -> str` — absolute filesystem path string for a workspace-relative path, with path-escape validation. Used by Task 4 (`apply_cmd.py`) to hand absolute paths to Playwright's `set_input_files`.

---

- [ ] **Step 1: Write failing tests for `resolve`**

Add to `tests/test_storage.py`:

```python
def test_resolve_returns_absolute_path(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("resumes/resume.pdf", b"data")
    result = storage.resolve("resumes/resume.pdf")
    assert result == str(tmp_path / "resumes" / "resume.pdf")


def test_resolve_rejects_path_traversal(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(ValueError):
        storage.resolve("../outside.txt")
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python3 -m pytest tests/test_storage.py::test_resolve_returns_absolute_path tests/test_storage.py::test_resolve_rejects_path_traversal -v
```

Expected: `AttributeError: 'LocalFilesystemStorage' object has no attribute 'resolve'`

- [ ] **Step 3: Add `resolve` to `LocalFilesystemStorage`**

In `careeros/storage/filesystem.py`, add after the `append` method:

```python
    def resolve(self, path: str) -> str:
        """Return the absolute filesystem path for a workspace-relative path."""
        return str(self._resolve(path))
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
python3 -m pytest tests/test_storage.py -v
```

Expected: all storage tests pass.

- [ ] **Step 5: Write failing test for m002**

Add to `tests/test_migrations.py`:

```python
def test_m002_creates_applications_dir(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("applications/.keep")
```

- [ ] **Step 6: Run to confirm it fails**

```bash
python3 -m pytest tests/test_migrations.py::test_m002_creates_applications_dir -v
```

Expected: `AssertionError` — `applications/.keep` does not exist yet.

- [ ] **Step 7: Create `m002_applications.py`**

Create `careeros/workspace/migrations/m002_applications.py`:

```python
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("002_applications")
def m002_applications(storage: StorageProvider) -> None:
    if not storage.exists("applications/.keep"):
        storage.write("applications/.keep", b"")
```

- [ ] **Step 8: Register m002 in `migrations/__init__.py`**

In `careeros/workspace/migrations/__init__.py`, add at the bottom (after the existing `m001_initial` import):

```python
from careeros.workspace.migrations import m002_applications  # noqa: F401, E402
```

- [ ] **Step 9: Run all migration tests**

```bash
python3 -m pytest tests/test_migrations.py -v
```

Expected: all pass, including the new `test_m002_creates_applications_dir`.

- [ ] **Step 10: Run full suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all existing tests still pass (migration is additive; run_pending skips already-applied migrations).

- [ ] **Step 11: Commit**

```bash
git add careeros/storage/filesystem.py careeros/workspace/migrations/m002_applications.py careeros/workspace/migrations/__init__.py tests/test_storage.py tests/test_migrations.py
git commit -m "feat: add storage resolve method and m002 applications workspace migration"
```

---

### Task 2: Cover letter skill

**Files:**
- Create: `careeros/skills/cover_letter.py`
- Create: `tests/test_cover_letter.py`

**Interfaces:**
- Consumes: `Profile`, `Skills`, `Goals` from `careeros.core.models`; `litellm.completion`
- Produces: `generate_cover_letter(jd_text: str, profile: Profile, skills: Skills, goals: Goals, model: str | None = None) -> str` — returns cover letter text; returns `""` on any failure; never raises

---

- [ ] **Step 1: Write failing tests**

Create `tests/test_cover_letter.py`:

```python
import os
import pytest
from unittest.mock import MagicMock, patch
from careeros.core.models import Goals, Profile, Skill, Skills


def _make_profile():
    return Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE", summary="SRE with 6 years")


def _make_skills():
    return Skills(skills=[Skill(name="Kubernetes"), Skill(name="Go"), Skill(name="Terraform")])


def _make_goals():
    return Goals(short_term=["platform engineering role", "remote work"], long_term=["staff engineer"])


class TestGenerateCoverLetter:
    def test_returns_text_on_success(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Dear Hiring Manager,\n\nI am excited..."
        with patch("litellm.completion", return_value=mock_resp):
            result = generate_cover_letter("SRE job description", _make_profile(), _make_skills(), _make_goals())
        assert result == "Dear Hiring Manager,\n\nI am excited..."

    def test_returns_empty_string_on_llm_error(self):
        from careeros.skills.cover_letter import generate_cover_letter
        with patch("litellm.completion", side_effect=Exception("API error")):
            result = generate_cover_letter("SRE job", _make_profile(), _make_skills(), _make_goals())
        assert result == ""

    def test_returns_empty_string_on_missing_content(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = None
        with patch("litellm.completion", return_value=mock_resp):
            result = generate_cover_letter("SRE job", _make_profile(), _make_skills(), _make_goals())
        assert result == ""

    def test_strips_whitespace_from_response(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "  \nDear Hiring Manager,\n\nText here.\n  "
        with patch("litellm.completion", return_value=mock_resp):
            result = generate_cover_letter("SRE job", _make_profile(), _make_skills(), _make_goals())
        assert result == "Dear Hiring Manager,\n\nText here."

    def test_uses_careeros_model_env_var(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            generate_cover_letter("jd", _make_profile(), _make_skills(), _make_goals())
        assert mock_llm.call_args[1]["model"] == "gpt-4o"

    def test_model_param_overrides_env(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            generate_cover_letter("jd", _make_profile(), _make_skills(), _make_goals(), model="claude-haiku-4-5-20251001")
        assert mock_llm.call_args[1]["model"] == "claude-haiku-4-5-20251001"

    def test_profile_title_in_prompt(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            generate_cover_letter("some jd text", _make_profile(), _make_skills(), _make_goals())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "Senior SRE" in prompt

    def test_jd_capped_at_4000_chars(self):
        from careeros.skills.cover_letter import generate_cover_letter
        long_jd = "x" * 5000
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            generate_cover_letter(long_jd, _make_profile(), _make_skills(), _make_goals())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "x" * 4001 not in prompt

    def test_goals_in_prompt(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            generate_cover_letter("jd text", _make_profile(), _make_skills(), _make_goals())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "platform engineering role" in prompt
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python3 -m pytest tests/test_cover_letter.py -v
```

Expected: `ModuleNotFoundError: No module named 'careeros.skills.cover_letter'`

- [ ] **Step 3: Implement `cover_letter.py`**

Create `careeros/skills/cover_letter.py`:

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
    """Generate a tailored cover letter. Returns '' on any failure. Never raises."""
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    profile_text = _build_profile_text(profile, skills, goals)
    prompt = _CL_INSTRUCTIONS + profile_text + "\n\nJob description:\n" + jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content
        if not content:
            return ""
        return content.strip()
    except Exception:
        return ""
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_cover_letter.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add careeros/skills/cover_letter.py tests/test_cover_letter.py
git commit -m "feat: add cover letter generation skill with LLM tailoring"
```

---

### Task 3: Filler Protocol + all 4 filler implementations + can_handle tests

**Files:**
- Create: `careeros/browser/fillers/__init__.py`
- Create: `careeros/browser/fillers/base.py`
- Create: `careeros/browser/fillers/greenhouse.py`
- Create: `careeros/browser/fillers/lever.py`
- Create: `careeros/browser/fillers/linkedin.py`
- Create: `careeros/browser/fillers/generic.py`
- Create: `tests/test_fillers.py`

**Interfaces:**
- Consumes: `Job`, `Profile` from `careeros.core.models`; `Page` from playwright (TYPE_CHECKING only)
- Produces:
  - `Filler` Protocol — `platform: str`, `can_handle(url: str) -> bool`, `fill(page, job, profile, cover_letter_text, cover_letter_path, resume_path) -> bool`
  - `GreenhouseFiller`, `LeverFiller`, `LinkedInFiller`, `GenericFiller` — concrete implementations
  - All imported by Task 4's `apply_cmd.py`

**Design note:** The spec's `fill` signature is `(page, job, cover_letter_path, resume_path)`. This plan adds `profile: Profile` (for name/email form fields) and `cover_letter_text: str` (for textarea cover letter fields, avoiding direct file reads in `careeros/browser/`). This is a necessary improvement that keeps the global constraint intact.

---

- [ ] **Step 1: Write failing `can_handle` tests**

Create `tests/test_fillers.py`:

```python
import pytest


class TestCanHandle:
    def test_greenhouse_handles_boards_url(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        assert GreenhouseFiller().can_handle("https://boards.greenhouse.io/acme/jobs/123456") is True

    def test_greenhouse_handles_greenhouse_jobs_url(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        assert GreenhouseFiller().can_handle("https://acme.greenhouse.io/jobs/apply") is True

    def test_greenhouse_rejects_lever_url(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        assert GreenhouseFiller().can_handle("https://jobs.lever.co/acme/abc") is False

    def test_lever_handles_lever_url(self):
        from careeros.browser.fillers.lever import LeverFiller
        assert LeverFiller().can_handle("https://jobs.lever.co/stripe/abc123") is True

    def test_lever_rejects_greenhouse_url(self):
        from careeros.browser.fillers.lever import LeverFiller
        assert LeverFiller().can_handle("https://boards.greenhouse.io/acme/jobs/1") is False

    def test_linkedin_handles_linkedin_jobs_url(self):
        from careeros.browser.fillers.linkedin import LinkedInFiller
        assert LinkedInFiller().can_handle("https://www.linkedin.com/jobs/view/1234567") is True

    def test_linkedin_rejects_greenhouse_url(self):
        from careeros.browser.fillers.linkedin import LinkedInFiller
        assert LinkedInFiller().can_handle("https://boards.greenhouse.io/acme/jobs/1") is False

    def test_generic_handles_any_url(self):
        from careeros.browser.fillers.generic import GenericFiller
        assert GenericFiller().can_handle("https://anything.com/careers/senior-engineer") is True

    def test_generic_handles_empty_string(self):
        from careeros.browser.fillers.generic import GenericFiller
        assert GenericFiller().can_handle("") is True

    def test_fillers_list_ends_with_generic(self):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller
        from careeros.browser.fillers.lever import LeverFiller
        from careeros.browser.fillers.linkedin import LinkedInFiller
        from careeros.browser.fillers.generic import GenericFiller
        fillers = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]
        assert isinstance(fillers[-1], GenericFiller)
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python3 -m pytest tests/test_fillers.py -v
```

Expected: `ModuleNotFoundError: No module named 'careeros.browser.fillers'`

- [ ] **Step 3: Create package and Protocol**

Create `careeros/browser/fillers/__init__.py` (empty file).

Create `careeros/browser/fillers/base.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class Filler(Protocol):
    platform: str

    def can_handle(self, url: str) -> bool:
        """Pure URL pattern check — no browser needed. Must be deterministic."""
        ...

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        """
        Navigate to job.url, fill the application form, upload documents, submit.
        Returns True on successful submission, False on detectable failure.
        cover_letter_text: raw string for textarea fields.
        cover_letter_path: absolute filesystem path for file-upload fields.
        resume_path: absolute filesystem path for resume upload.
        """
        ...
```

- [ ] **Step 4: Implement `GreenhouseFiller`**

Create `careeros/browser/fillers/greenhouse.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class GreenhouseFiller:
    platform = "Greenhouse"

    def can_handle(self, url: str) -> bool:
        return "boards.greenhouse.io" in url or "greenhouse.io" in url

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        parts = (profile.name or "").split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        try:
            page.goto(job.url, timeout=20000)
        except Exception:
            return False

        # Fill name and email
        try:
            page.locator('input[name="job_application[first_name]"]').fill(first_name)
            page.locator('input[name="job_application[last_name]"]').fill(last_name)
            page.locator('input[name="job_application[email]"]').fill(profile.email or "")
        except Exception:
            return False

        # Resume upload (first file input)
        try:
            page.locator('input[type="file"]').first.set_input_files(resume_path)
        except Exception:
            return False

        # Cover letter upload (second file input, if present)
        try:
            file_inputs = page.locator('input[type="file"]').all()
            if len(file_inputs) > 1:
                file_inputs[1].set_input_files(cover_letter_path)
        except Exception:
            pass

        # Submit
        try:
            page.locator('input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(5000)
        except Exception:
            return False

        return True
```

- [ ] **Step 5: Implement `LeverFiller`**

Create `careeros/browser/fillers/lever.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class LeverFiller:
    platform = "Lever"

    def can_handle(self, url: str) -> bool:
        return "jobs.lever.co" in url

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        try:
            page.goto(job.url, timeout=20000)
        except Exception:
            return False

        # Click Apply button if visible
        try:
            apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")').first
            if apply_btn.is_visible():
                apply_btn.click()
                page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        # Fill name field (Lever uses either a single "name" or separate first/last)
        try:
            name_inputs = page.locator('input[name="name"]')
            if name_inputs.count() > 0:
                name_inputs.fill(profile.name or "")
            else:
                parts = (profile.name or "").split(" ", 1)
                page.locator('input[placeholder*="First" i]').first.fill(parts[0])
                if len(parts) > 1:
                    page.locator('input[placeholder*="Last" i]').first.fill(parts[1])
            page.locator('input[name="email"]').fill(profile.email or "")
        except Exception:
            return False

        # Resume upload
        try:
            page.locator('input[type="file"]').first.set_input_files(resume_path)
        except Exception:
            return False

        # Cover letter textarea (optional)
        try:
            cl_area = page.locator('textarea[name="comments"]')
            if cl_area.count() > 0 and cover_letter_text:
                cl_area.fill(cover_letter_text)
        except Exception:
            pass

        # Submit
        try:
            page.locator('input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(5000)
        except Exception:
            return False

        return True
```

- [ ] **Step 6: Implement `LinkedInFiller`**

Create `careeros/browser/fillers/linkedin.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class LinkedInFiller:
    platform = "LinkedIn Easy Apply"

    def can_handle(self, url: str) -> bool:
        return "linkedin.com/jobs/" in url

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        try:
            page.goto(job.url, timeout=20000)
        except Exception:
            return False

        # Click Easy Apply button
        try:
            easy_apply = page.locator('button:has-text("Easy Apply")').first
            if not easy_apply.is_visible(timeout=5000):
                return False
            easy_apply.click()
            page.wait_for_timeout(1500)
        except Exception:
            return False

        # Step through the modal (up to 10 pages)
        for _ in range(10):
            # Fill contact info fields if present
            try:
                email_input = page.locator('input[id*="email" i]').first
                if email_input.is_visible() and not email_input.input_value():
                    email_input.fill(profile.email or "")
            except Exception:
                pass

            try:
                phone_input = page.locator('input[id*="phone" i]').first
                if phone_input.is_visible() and not phone_input.input_value():
                    phone_input.fill("")
            except Exception:
                pass

            # Resume upload step
            try:
                file_input = page.locator('input[type="file"]').first
                if file_input.count() > 0:
                    file_input.set_input_files(resume_path)
            except Exception:
                pass

            # Cover letter textarea step
            try:
                cl_area = page.locator('textarea[aria-label*="cover letter" i], textarea[id*="cover-letter"]').first
                if cl_area.count() > 0 and cl_area.is_visible() and cover_letter_text:
                    cl_area.fill(cover_letter_text)
            except Exception:
                pass

            # Submit if on the final review page
            try:
                submit_btn = page.locator('button:has-text("Submit application")').first
                if submit_btn.is_visible():
                    submit_btn.click()
                    page.wait_for_timeout(3000)
                    return True
            except Exception:
                pass

            # Otherwise click Next / Review / Continue
            try:
                next_btn = page.locator(
                    'button:has-text("Next"), button:has-text("Review"), button:has-text("Continue")'
                ).first
                if next_btn.is_visible():
                    next_btn.click()
                    page.wait_for_timeout(1000)
                else:
                    break
            except Exception:
                break

        return False
```

- [ ] **Step 7: Implement `GenericFiller`**

Create `careeros/browser/fillers/generic.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

from rich import print as rprint

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from careeros.core.models import Job, Profile


class GenericFiller:
    platform = "Generic"

    def can_handle(self, url: str) -> bool:
        return True

    def fill(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        cover_letter_text: str,
        cover_letter_path: str,
        resume_path: str,
    ) -> bool:
        try:
            page.goto(job.url, timeout=20000)
        except Exception:
            return False

        parts = (profile.name or "").split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        # Best-effort name/email fill by label/placeholder heuristics
        for selector, value in [
            ('input[placeholder*="First name" i], input[name*="first" i]', first_name),
            ('input[placeholder*="Last name" i], input[name*="last" i]', last_name),
            ('input[placeholder*="Full name" i], input[name="name"]', profile.name or ""),
            ('input[placeholder*="Email" i], input[name="email"], input[type="email"]', profile.email or ""),
        ]:
            try:
                el = page.locator(selector).first
                if el.count() > 0 and el.is_visible() and value:
                    el.fill(value)
            except Exception:
                pass

        # Resume upload (first file input)
        try:
            page.locator('input[type="file"]').first.set_input_files(resume_path)
        except Exception:
            pass

        rprint("[yellow]Generic filler applied — review the form before submitting. Fields may be incomplete.[/yellow]")
        # Generic never auto-submits — leaves browser open for user to verify and submit
        return False
```

- [ ] **Step 8: Run `can_handle` tests**

```bash
python3 -m pytest tests/test_fillers.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 9: Run full suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all existing + new tests pass.

- [ ] **Step 10: Commit**

```bash
git add careeros/browser/fillers/ tests/test_fillers.py
git commit -m "feat: add Filler Protocol with Greenhouse, Lever, LinkedIn, and Generic implementations"
```

---

### Task 4: CLI apply command + main wiring + tests

**Files:**
- Create: `careeros/cli/apply_cmd.py`
- Modify: `careeros/cli/main.py` — register `apply_app`
- Create: `tests/test_apply_cmd.py`

**Interfaces:**
- Consumes (from Tasks 1–3):
  - `LocalFilesystemStorage.resolve(path: str) -> str`
  - `generate_cover_letter(jd_text, profile, skills, goals, model=None) -> str`
  - `GreenhouseFiller`, `LeverFiller`, `LinkedInFiller`, `GenericFiller` — all with `.can_handle(url)` and `.fill(page, job, profile, cover_letter_text, cover_letter_path, resume_path) -> bool`
  - `launch_browser` from `careeros.browser.driver`
  - `Job.load(storage, job_id)`, `Job.save(storage)`, `Job.model_copy(update=...)`
  - `Profile.load_or_empty(storage)`, `Skills.load_or_empty(storage)`, `Goals.load_or_empty(storage)`
  - `ActivityLogger`, `ActivityLogger.new_event`, `ActivityLogger.log`
  - `open_workspace(storage)` from `careeros.workspace.manager`
- Produces: `apply_app` Typer app registered as `careeros apply`

---

- [ ] **Step 1: Write failing tests**

Create `tests/test_apply_cmd.py`:

```python
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from typer.testing import CliRunner

from careeros.cli.apply_cmd import apply_app
from careeros.config import GlobalConfig
from careeros.core.models import Goals, Job, Profile, Skill, Skills
from careeros.workspace.manager import WorkspaceContext
from careeros.workspace.manifest import Manifest

runner = CliRunner()


def _make_job(url="https://boards.greenhouse.io/acme/jobs/123"):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-abc1",
        source="browse",
        url=url,
        company="Acme",
        title="Senior SRE",
        stage="saved",
        created_at=now,
        updated_at=now,
    )


def _make_profile():
    return Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE")


def _make_manifest():
    return Manifest(schema_version="1.0", migrations_applied=["001_initial", "002_applications"])


def _mock_storage(tmp_path, resume_filename="resume.pdf"):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    storage.exists.return_value = True
    return storage


def _mock_ctx(storage):
    ctx = MagicMock(spec=WorkspaceContext)
    ctx.storage = storage
    return ctx


class TestApplyCmdHappyPath:
    def test_successful_apply_updates_stage_to_applied(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        job = _make_job()
        profile = _make_profile()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"

        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=profile), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Hiring Manager,\n\nGreat fit."), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=True):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(apply_app, ["acme-sre-abc1"])

        assert result.exit_code == 0
        storage.atomic_write.assert_called()
        # Cover letter saved
        save_calls = [str(c) for c in storage.atomic_write.call_args_list]
        assert any("cover_letter" in c for c in save_calls)

    def test_successful_apply_logs_job_applied_event(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        job = _make_job()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"

        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter text"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=True), \
             patch("careeros.cli.apply_cmd.ActivityLogger") as MockLogger:
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])

        logger_instance = MockLogger.return_value
        logger_instance.log.assert_called_once()
        event_arg = logger_instance.new_event.call_args
        assert event_arg[0][0] == "job_applied"


class TestApplyCmdFailurePaths:
    def test_no_workspace_exits_1(self):
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig(workspace_path=None)):
            result = runner.invoke(apply_app, ["some-job-id"])
        assert result.exit_code == 1

    def test_job_not_found_exits_1(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", side_effect=FileNotFoundError):
            result = runner.invoke(apply_app, ["nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_job_no_url_exits_1(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        now = datetime.now(timezone.utc).isoformat()
        job_no_url = Job(id="x", source="manual", url=None, company="Co", title="Role",
                         stage="saved", created_at=now, updated_at=now)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job_no_url):
            result = runner.invoke(apply_app, ["x"])
        assert result.exit_code == 1
        assert "url" in result.output.lower()

    def test_no_resume_exits_1(self, tmp_path):
        storage = MagicMock()
        storage.list.return_value = []  # no resume files
        ctx = _mock_ctx(storage)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "resume" in result.output.lower()

    def test_cover_letter_generation_failure_exits_1(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value=""):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "generation failed" in result.output.lower()

    def test_user_quits_review_loop_exits_0(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_user_declines_final_confirm_exits_0(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=False):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_filler_returns_false_exits_1_stage_not_updated(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        job = _make_job()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = False
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=True):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "incomplete" in result.output.lower()

    def test_playwright_not_installed_exits_1(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser", side_effect=ImportError), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=True):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "playwright" in result.output.lower()

    def test_regenerate_calls_generate_again(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["r", "a"]), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=True):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])
        # Called once initially + once on regenerate
        assert mock_gen.call_count == 2

    def test_model_flag_propagated_to_generate(self, tmp_path):
        storage = _mock_storage(tmp_path)
        ctx = _mock_ctx(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        with patch("careeros.cli.apply_cmd._get_storage", return_value=storage), \
             patch("careeros.cli.apply_cmd.open_workspace", return_value=ctx), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.cli.apply_cmd.Confirm.ask", return_value=True):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1", "--model", "gpt-4o"])
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("model") == "gpt-4o"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python3 -m pytest tests/test_apply_cmd.py -v
```

Expected: `ImportError: cannot import name 'apply_app' from 'careeros.cli.apply_cmd'`

- [ ] **Step 3: Implement `apply_cmd.py`**

Create `careeros/cli/apply_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from careeros.browser.driver import launch_browser
from careeros.browser.fillers.generic import GenericFiller
from careeros.browser.fillers.greenhouse import GreenhouseFiller
from careeros.browser.fillers.lever import LeverFiller
from careeros.browser.fillers.linkedin import LinkedInFiller
from careeros.config import GlobalConfig
from careeros.core.activity import ActivityLogger
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.skills.cover_letter import generate_cover_letter
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace

apply_app = typer.Typer(help="Apply to saved jobs.")
console = Console()

FILLERS = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]
MAX_REGENERATIONS = 5
_RESUME_EXTENSIONS = (".pdf", ".docx")


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


@apply_app.command()
def apply_cmd(
    job_id: str = typer.Argument(..., help="Job ID to apply to"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model for cover letter"),
) -> None:
    storage = _get_storage(workspace)
    try:
        ctx = open_workspace(storage)
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    logger = ActivityLogger(ctx.storage)

    # Load job
    try:
        job = Job.load(storage, job_id)
    except FileNotFoundError:
        rprint("[red]Job " + job_id + " not found.[/red]")
        raise typer.Exit(1)

    if not job.url:
        rprint("[red]Job has no URL — add one with `careeros job update`.[/red]")
        raise typer.Exit(1)

    # Load profile data
    profile = Profile.load_or_empty(storage)
    skills = Skills.load_or_empty(storage)
    goals = Goals.load_or_empty(storage)

    # Find resume
    resume_entries = [
        p for p in storage.list("resumes/versions/")
        if p.endswith(_RESUME_EXTENSIONS)
    ]
    if not resume_entries:
        rprint("[red]No resume found in resumes/versions/ — add one first.[/red]")
        raise typer.Exit(1)

    if len(resume_entries) == 1:
        resume_path = storage.resolve(resume_entries[0])
    else:
        console.print("Multiple resumes found:")
        for i, r in enumerate(resume_entries, 1):
            console.print(str(i) + ". " + r)
        pick = Prompt.ask("Pick resume", default="1")
        idx = int(pick) - 1 if pick.isdigit() and 0 <= int(pick) - 1 < len(resume_entries) else 0
        resume_path = storage.resolve(resume_entries[idx])

    # Get JD text
    jd_text = (job.description or "")[:4000]

    # Generate cover letter
    with console.status("Generating cover letter..."):
        cover_letter = generate_cover_letter(jd_text, profile, skills, goals, model=model)

    if not cover_letter:
        rprint("[red]Cover letter generation failed. Check your LLM configuration.[/red]")
        raise typer.Exit(1)

    # Review loop
    regenerations = 0
    while True:
        console.print(Panel(cover_letter, title="Cover Letter — " + job.company + " / " + job.title))

        if regenerations >= MAX_REGENERATIONS:
            choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
        else:
            choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")

        if choice == "q":
            rprint("Aborted.")
            raise typer.Exit(0)
        if choice == "r":
            regenerations += 1
            with console.status("Regenerating..."):
                cover_letter = generate_cover_letter(jd_text, profile, skills, goals, model=model)
            if not cover_letter:
                rprint("[red]Cover letter generation failed.[/red]")
                raise typer.Exit(1)
            continue
        break  # choice == "a"

    # Save cover letter
    cl_storage_path = "applications/" + job_id + "/cover_letter.txt"
    storage.atomic_write(cl_storage_path, cover_letter.encode())
    cover_letter_path = storage.resolve(cl_storage_path)

    # Detect filler
    filler = next((f for f in FILLERS if f.can_handle(job.url)), None)
    if filler is None:
        rprint("[red]No filler available for this URL.[/red]")
        raise typer.Exit(1)

    # Final confirm
    confirmed = Confirm.ask(
        "About to fill the " + filler.platform + " application for "
        + job.company + " — " + job.title + ". Proceed?",
        default=False,
    )
    if not confirmed:
        rprint("Aborted.")
        raise typer.Exit(0)

    # Launch browser and fill
    try:
        with launch_browser(headless=False) as (_, page):
            success = filler.fill(page, job, profile, cover_letter, cover_letter_path, resume_path)
    except ImportError:
        rprint("[red]Playwright not installed. Run: pip install playwright && playwright install chrome[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        rprint("[red]Browser error: " + str(exc) + ". Stage not updated.[/red]")
        raise typer.Exit(1)

    if success:
        now = _now()
        job = job.model_copy(update={"stage": "applied", "applied_at": now, "updated_at": now})
        job.save(storage)
        logger.log(logger.new_event(
            "job_applied", "apply",
            "Applied to " + job.company + " — " + job.title,
            entity_type="job", entity_id=job_id,
        ))
        rprint("[green]Applied to " + job.company + " — " + job.title + ". Stage updated to 'applied'.[/green]")
    else:
        rprint("[yellow]Form fill incomplete — review the browser window. Stage not updated.[/yellow]")
        raise typer.Exit(1)
```

- [ ] **Step 4: Wire into `main.py`**

Read `careeros/cli/main.py` to find the right insertion point, then add after the existing `browse_app` import and `add_typer` call:

```python
from careeros.cli.apply_cmd import apply_app
app.add_typer(apply_app, name="apply")
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_apply_cmd.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Verify `careeros apply --help` works**

```bash
python3 -m careeros.cli.main apply --help
```

Expected: shows `apply` command with `JOB_ID`, `--workspace`, `--model` options.

- [ ] **Step 7: Run full suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass, 2 integration tests skipped.

- [ ] **Step 8: Commit**

```bash
git add careeros/cli/apply_cmd.py careeros/cli/main.py tests/test_apply_cmd.py
git commit -m "feat: add careeros apply command with cover letter review and browser form fill"
```

---

### Task 5: Integration test skeleton

**Files:**
- Create: `tests/integration/test_live_apply.py`

**Interfaces:**
- Consumes: `launch_browser` from `careeros.browser.driver`; `GreenhouseFiller`, `LeverFiller` from `careeros.browser.fillers`; `Job`, `Profile` from `careeros.core.models`

---

- [ ] **Step 1: Create integration tests**

Create `tests/integration/test_live_apply.py`:

```python
import pytest
from datetime import datetime, timezone


@pytest.mark.integration
def test_greenhouse_filler_can_handle_live_url():
    """Smoke test: can_handle does not require a browser."""
    from careeros.browser.fillers.greenhouse import GreenhouseFiller
    filler = GreenhouseFiller()
    assert filler.can_handle("https://boards.greenhouse.io/example/jobs/1234567") is True


@pytest.mark.integration
def test_lever_filler_can_handle_live_url():
    """Smoke test: can_handle does not require a browser."""
    from careeros.browser.fillers.lever import LeverFiller
    filler = LeverFiller()
    assert filler.can_handle("https://jobs.lever.co/example/abc-123") is True


@pytest.mark.integration
def test_greenhouse_fill_live():
    """
    Live browser test — requires Chrome with an active session and a real Greenhouse job URL.
    Set CAREEROS_TEST_GREENHOUSE_URL env var to the apply URL before running.
    Run with: pytest -m integration
    """
    import os
    from careeros.browser.driver import launch_browser
    from careeros.browser.fillers.greenhouse import GreenhouseFiller
    from careeros.core.models import Job, Profile

    url = os.environ.get("CAREEROS_TEST_GREENHOUSE_URL")
    if not url:
        pytest.skip("CAREEROS_TEST_GREENHOUSE_URL not set")

    now = datetime.now(timezone.utc).isoformat()
    job = Job(id="test-job", source="test", url=url, company="Test Co", title="Test Role",
              stage="saved", created_at=now, updated_at=now)
    profile = Profile(name="Test User", email="test@example.com", title="SRE")
    filler = GreenhouseFiller()

    with launch_browser(headless=False) as (_, page):
        # Does not submit — returns False or True based on form detection
        result = filler.fill(page, job, profile, "Test cover letter.", "/tmp/test_resume.pdf", "/tmp/test_cl.txt")
    # Just verify no uncaught exception
    assert isinstance(result, bool)


@pytest.mark.integration
def test_lever_fill_live():
    """
    Live browser test — requires Chrome with an active session and a real Lever job URL.
    Set CAREEROS_TEST_LEVER_URL env var to the apply URL before running.
    Run with: pytest -m integration
    """
    import os
    from careeros.browser.driver import launch_browser
    from careeros.browser.fillers.lever import LeverFiller
    from careeros.core.models import Job, Profile

    url = os.environ.get("CAREEROS_TEST_LEVER_URL")
    if not url:
        pytest.skip("CAREEROS_TEST_LEVER_URL not set")

    now = datetime.now(timezone.utc).isoformat()
    job = Job(id="test-job", source="test", url=url, company="Test Co", title="Test Role",
              stage="saved", created_at=now, updated_at=now)
    profile = Profile(name="Test User", email="test@example.com", title="SRE")
    filler = LeverFiller()

    with launch_browser(headless=False) as (_, page):
        result = filler.fill(page, job, profile, "Test cover letter.", "/tmp/test_resume.pdf", "/tmp/test_cl.txt")
    assert isinstance(result, bool)
```

- [ ] **Step 2: Verify integration tests are auto-skipped in normal run**

```bash
python3 -m pytest tests/ -q
```

Expected: all existing tests pass; 6 integration tests collected and skipped (4 from live_browser + 2 smoke tests from this file are skipped by conftest hook; actually the 2 smoke can_handle tests don't need a browser but are marked integration — they will be skipped too, which is fine).

- [ ] **Step 3: Run full suite one final time**

```bash
python3 -m pytest tests/ -q
```

Expected: all non-integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_live_apply.py
git commit -m "test: add integration test skeleton for live browser apply flow"
```
