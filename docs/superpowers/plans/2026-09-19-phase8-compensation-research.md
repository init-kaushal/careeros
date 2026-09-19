# Phase 8 — Compensation Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `careeros research compensation --job <id>` — browser-driven, LLM-extracted compensation evidence collection for a saved job.

**Architecture:** A new `CompensationDataPoint` model (random-suffix ID, unlike `Company`/`Person`'s deterministic IDs — each research run is a new dated evidence record, not an overwrite). A new `careeros/skills/compensation_research.py` skill follows `company_research.py`'s exact browser-content-to-LLM-extraction pattern. One new subcommand added to the existing `research_app` Typer group in `careeros/cli/research_cmd.py` (alongside `company`/`people` from Phase 7), fetching a levels.fyi page via the existing `fetch_jd_text` helper (not a new `Scraper` class — a single-page fetch doesn't fit that Protocol's multi-result shape).

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, Rich, Playwright (headless).

**Spec:** `docs/superpowers/specs/2026-09-19-phase8-compensation-research-design.md`

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/`
- Activity summaries built via string concatenation only — no f-strings/`.format()` with user data
- Every meaningful action produces an activity event — standing principle from Phase 7, applies here unchanged
- URLs built from user/workspace data (company name, job title) are always passed through `urllib.parse.quote` before concatenation — Phase 7's final review caught an unencoded-URL bug in the analogous `research company` command; this plan builds the fix in from the start
- `confidence` must never be `"medium"` or `"high"` when the underlying page content was empty or too thin to support a real estimate — the extraction skill's fallback path always returns `"low"`

---

### Task 1: CompensationDataPoint Model + ID Helper

**Files:**
- Modify: `careeros/core/ids.py` (append `make_compensation_id`)
- Modify: `careeros/core/models.py` (append `CompensationDataPoint`)
- Test: `tests/test_ids.py` (extend)
- Test: `tests/test_company_person_outreach_models.py` (extend — or a differently-named file; see Step 5)

**Interfaces:**
- Produces: `make_compensation_id(company: str, role: str) -> str` — NON-deterministic (random suffix, following `make_job_id`'s pattern in `careeros/core/job_id.py`, NOT `make_company_id`'s deterministic pattern) — two calls with identical inputs produce different IDs, since each research run is a new evidence record
- Produces: `CompensationDataPoint(id, job_id, role, seniority=None, geo=None, company=None, currency="USD", base_min=None, base_max=None, bonus=None, equity=None, source="levels.fyi", source_url=None, confidence="low", researched_at)` with `.save(storage)` and strict `.load(storage, id)` (raises `FileNotFoundError`)

- [ ] **Step 1: Write the failing test for make_compensation_id**

Append to `tests/test_ids.py` (add this import alongside the existing one at the top: `from careeros.core.ids import make_company_id, make_compensation_id, make_person_id`):

```python
def test_make_compensation_id_is_non_deterministic():
    id_a = make_compensation_id("Acme Corp", "Senior SRE")
    id_b = make_compensation_id("Acme Corp", "Senior SRE")
    assert id_a != id_b


def test_make_compensation_id_includes_slugified_company_and_role():
    comp_id = make_compensation_id("Acme Corp", "Senior SRE")
    assert "acme-corp" in comp_id
    assert "senior-sre" in comp_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ids.py -v`
Expected: FAIL with `ImportError: cannot import name 'make_compensation_id'`

- [ ] **Step 3: Implement make_compensation_id**

Append to `careeros/core/ids.py`:

```python
import secrets


def make_compensation_id(company: str, role: str) -> str:
    company_slug = _slugify(company)[:20]
    role_slug = _slugify(role)[:20]
    suffix = secrets.token_hex(3)
    parts = [p for p in [company_slug, role_slug] if p]
    return "-".join(parts) + "-" + suffix
```

Note: `import secrets` goes at the top of the file alongside the existing `import re`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ids.py -v`
Expected: PASS (6 tests total — 4 existing + 2 new)

- [ ] **Step 5: Write the failing tests for CompensationDataPoint**

Create `tests/test_compensation_model.py`:

```python
import pytest
from careeros.core.ids import make_compensation_id
from careeros.core.models import CompensationDataPoint
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    comp_id = make_compensation_id("Acme Corp", "Senior SRE")
    point = CompensationDataPoint(
        id=comp_id, job_id="acme-sre-abc1", role="Senior SRE", seniority="senior",
        geo="San Francisco, CA", company="Acme Corp", base_min=180000, base_max=220000,
        bonus="10-15%", equity="0.01-0.05%", source_url="https://www.levels.fyi/companies/acme-corp/salaries/senior-sre",
        confidence="medium", researched_at="2026-09-19T00:00:00Z",
    )
    point.save(storage)
    loaded = CompensationDataPoint.load(storage, comp_id)
    assert loaded.base_min == 180000
    assert loaded.base_max == 220000
    assert loaded.confidence == "medium"
    assert loaded.source == "levels.fyi"


def test_defaults(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    comp_id = make_compensation_id("Acme Corp", "Senior SRE")
    point = CompensationDataPoint(
        id=comp_id, job_id="acme-sre-abc1", role="Senior SRE", researched_at="2026-09-19T00:00:00Z",
    )
    assert point.currency == "USD"
    assert point.confidence == "low"
    assert point.source == "levels.fyi"
    assert point.seniority is None
    assert point.base_min is None


def test_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        CompensationDataPoint.load(storage, "nonexistent")
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_compensation_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'CompensationDataPoint'`

- [ ] **Step 7: Append CompensationDataPoint to careeros/core/models.py**

Append to the end of `careeros/core/models.py` (after the `OutreachMessage` class from Phase 7 — do not modify any existing class):

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

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("compensation/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, data_point_id: str) -> "CompensationDataPoint":
        path = "compensation/" + data_point_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("CompensationDataPoint " + repr(data_point_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_compensation_model.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 10: Commit**

```bash
git add careeros/core/ids.py careeros/core/models.py tests/test_ids.py tests/test_compensation_model.py
git commit -m "feat: add CompensationDataPoint model and non-deterministic ID helper"
```

---

### Task 2: Compensation Research Skill

**Files:**
- Create: `careeros/skills/compensation_research.py`
- Test: `tests/test_compensation_research.py`

**Interfaces:**
- Produces: `extract_compensation_data(page_content: str, model: str | None = None) -> dict` returning `{"base_min": int | None, "base_max": int | None, "bonus": str | None, "equity": str | None, "confidence": str}`, never raises (returns the all-fallback dict with `confidence="low"` on any failure)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compensation_research.py`:

```python
from unittest.mock import MagicMock, patch


def test_extract_compensation_data_parses_llm_json():
    from careeros.skills.compensation_research import extract_compensation_data
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = (
        '{"base_min": 180000, "base_max": 220000, "bonus": "10-15%", "equity": "0.01-0.05%", "confidence": "medium"}'
    )
    with patch("careeros.skills.compensation_research.litellm.completion", return_value=mock_resp):
        result = extract_compensation_data("Senior SRE at Acme Corp: $180K-$220K base...")
    assert result["base_min"] == 180000
    assert result["base_max"] == 220000
    assert result["bonus"] == "10-15%"
    assert result["equity"] == "0.01-0.05%"
    assert result["confidence"] == "medium"


def test_extract_compensation_data_handles_llm_failure():
    from careeros.skills.compensation_research import extract_compensation_data
    with patch("careeros.skills.compensation_research.litellm.completion", side_effect=Exception("boom")):
        result = extract_compensation_data("some content")
    assert result["base_min"] is None
    assert result["base_max"] is None
    assert result["bonus"] is None
    assert result["equity"] is None
    assert result["confidence"] == "low"


def test_extract_compensation_data_handles_malformed_json():
    from careeros.skills.compensation_research import extract_compensation_data
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "not json at all"
    with patch("careeros.skills.compensation_research.litellm.completion", return_value=mock_resp):
        result = extract_compensation_data("some content")
    assert result["confidence"] == "low"


def test_extract_compensation_data_handles_thin_page_content():
    from careeros.skills.compensation_research import extract_compensation_data
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = (
        '{"base_min": null, "base_max": null, "bonus": null, "equity": null, "confidence": "low"}'
    )
    with patch("careeros.skills.compensation_research.litellm.completion", return_value=mock_resp):
        result = extract_compensation_data("")
    assert result["base_min"] is None
    assert result["confidence"] == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compensation_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.skills.compensation_research'`

- [ ] **Step 3: Implement compensation_research.py**

Create `careeros/skills/compensation_research.py`:

```python
import json
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_CONTENT_CAP = 4000

_EXTRACT_INSTRUCTIONS = """\
Extract structured compensation facts from the page content below, which was
fetched from a public salary-data page for a specific company and role.
If the page content is empty, too thin, or doesn't contain real compensation
figures, return all numeric/text fields as null and confidence as "low" —
never invent a plausible-sounding number.
Return ONLY valid JSON — no markdown, no explanation.

{
  "base_min": <integer base salary low end, or null>,
  "base_max": <integer base salary high end, or null>,
  "bonus": "<bonus description, e.g. '10-15%', or null>",
  "equity": "<equity description, e.g. '0.01-0.05%', or null>",
  "confidence": "<'low', 'medium', or 'high' based on how much real data was present>"
}

Page content:
"""

_FAILURE = {"base_min": None, "base_max": None, "bonus": None, "equity": None, "confidence": "low"}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_compensation_data(page_content: str, model: str | None = None) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    prompt = _EXTRACT_INSTRUCTIONS + page_content[:_CONTENT_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.choices[0].message.content)
        confidence = result.get("confidence")
        if confidence not in ("low", "medium", "high"):
            confidence = "low"
        return {
            "base_min": result.get("base_min"),
            "base_max": result.get("base_max"),
            "bonus": result.get("bonus"),
            "equity": result.get("equity"),
            "confidence": confidence,
        }
    except Exception:
        return dict(_FAILURE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_compensation_research.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add careeros/skills/compensation_research.py tests/test_compensation_research.py
git commit -m "feat: add compensation research extraction skill"
```

---

### Task 3: research compensation CLI Command

**Files:**
- Modify: `careeros/cli/research_cmd.py` (add a third subcommand — do not modify the existing `company`/`people` commands)
- Test: `tests/test_research_cmd.py` (extend — add a `TestResearchCompensation` class)

**Interfaces:**
- Consumes: `CompensationDataPoint`, `Preferences` from `careeros.core.models` (Task 1 for `CompensationDataPoint`; `Preferences` pre-existing)
- Consumes: `make_compensation_id` from `careeros.core.ids` (Task 1)
- Consumes: `extract_compensation_data` from `careeros.skills.compensation_research` (Task 2)
- Consumes: `fetch_jd_text`, `launch_browser` from `careeros.browser.driver` (existing, already imported in `research_cmd.py`)
- Produces: a third command, `compensation`, on the existing `research_app` Typer group

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_cmd.py` — add this import alongside the existing ones at the top: `from careeros.core.models import CompensationDataPoint, Preferences` (extend the existing `from careeros.core.models import Company, Job, Person` line to include these two), then append this test class at the end of the file:

```python
class TestResearchCompensation:
    def test_job_not_found_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_job=False)
        result = runner.invoke(research_app, ["compensation", "--job", "nonexistent", "--workspace", ws_path])
        assert result.exit_code == 1

    def test_researches_and_saves_compensation(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        Preferences(seniority="senior").save(storage)

        with patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value="Senior SRE at Acme Corp: $180K-$220K"), \
             patch("careeros.cli.research_cmd.extract_compensation_data", return_value={
                 "base_min": 180000, "base_max": 220000, "bonus": "10%", "equity": "0.02%", "confidence": "medium",
             }):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["compensation", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        # Find the saved CompensationDataPoint by scanning compensation/ (ID has a random suffix)
        comp_files = [p for p in storage.list("compensation/") if p.endswith(".json")]
        assert len(comp_files) == 1
        point = CompensationDataPoint.model_validate_json(storage.read(comp_files[0]).decode())
        assert point.base_min == 180000
        assert point.seniority == "senior"
        assert point.confidence == "medium"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "compensation_researched" in log_content

    def test_thin_page_content_saves_low_confidence(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value=""), \
             patch("careeros.cli.research_cmd.extract_compensation_data", return_value={
                 "base_min": None, "base_max": None, "bonus": None, "equity": None, "confidence": "low",
             }):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(research_app, ["compensation", "--job", "acme-sre-abc1", "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        comp_files = [p for p in storage.list("compensation/") if p.endswith(".json")]
        assert len(comp_files) == 1
        point = CompensationDataPoint.model_validate_json(storage.read(comp_files[0]).decode())
        assert point.confidence == "low"
        assert point.base_min is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_research_cmd.py -v`
Expected: FAIL with `AttributeError` (no `compensation` command on `research_app`) or a Typer "No such command" error

- [ ] **Step 3: Add the compensation command to research_cmd.py**

Modify `careeros/cli/research_cmd.py` — add `import re` alongside the existing `import urllib.parse` at the top of the file (module-level, matching the convention already used in `careeros/core/job_id.py` and `careeros/core/ids.py` — not imported inside the function), plus these additional imports alongside the existing ones:

```python
from careeros.core.ids import make_compensation_id, make_company_id, make_person_id
from careeros.core.models import Company, CompensationDataPoint, Job, Person, Preferences
from careeros.skills.compensation_research import extract_compensation_data
```

(Note: `make_company_id`/`make_person_id`/`Company`/`Job`/`Person` are already imported — just extend those existing import lines to include the new names rather than duplicating the lines.)

Then append this command function at the end of the file:

```python
def _slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@research_app.command()
def compensation(
    job: str = typer.Option(..., "--job", help="Job ID to research compensation for"),
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

    prefs = Preferences.load_or_empty(runtime.storage)

    url = (
        "https://www.levels.fyi/companies/" + urllib.parse.quote(_slugify(job_obj.company))
        + "/salaries/" + urllib.parse.quote(_slugify(job_obj.title))
    )

    try:
        with launch_browser(headless=True) as (_, page):
            page_content = fetch_jd_text(page, url)
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        raise typer.Exit(1)

    data = extract_compensation_data(page_content)
    comp_id = make_compensation_id(job_obj.company, job_obj.title)
    point = CompensationDataPoint(
        id=comp_id, job_id=job, role=job_obj.title, seniority=prefs.seniority,
        geo=job_obj.location, company=job_obj.company, source_url=url,
        base_min=data["base_min"], base_max=data["base_max"],
        bonus=data["bonus"], equity=data["equity"], confidence=data["confidence"],
        researched_at=_now(),
    )
    point.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "compensation_researched", "research",
        "Researched compensation for " + job_obj.title + " at " + job_obj.company
        + " (confidence: " + data["confidence"] + ")",
        entity_type="compensation", entity_id=comp_id,
    ))
    rprint("[green]Researched compensation for " + job_obj.title + " at " + job_obj.company + "[/green]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_research_cmd.py -v`
Expected: PASS (7 tests total — 4 existing from Phase 7 + 3 new)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/research_cmd.py tests/test_research_cmd.py
git commit -m "feat: add research compensation command"
```
