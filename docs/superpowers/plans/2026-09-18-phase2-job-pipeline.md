# CareerOS Phase 2 — Job Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a job pipeline to the CareerOS workspace — discover jobs manually or via Greenhouse/Lever ATS APIs, track them through five stages, and log all transitions to the existing activity log.

**Architecture:** One JSON file per job in `jobs/`. Stage transitions reuse the existing append-only `ActivityLogger`. LiteLLM parses raw JD text into structured fields. Greenhouse and Lever unauthenticated public APIs power `job search`. All I/O goes through `StorageProvider` — no direct file access.

**Tech stack:** Python 3.11+, Pydantic v2, LiteLLM (already in deps), `urllib.request` (stdlib), Typer + Rich.

**Spec:** `docs/superpowers/specs/2026-09-18-phase2-job-pipeline-design.md`

## Global Constraints

- Python 3.11+ — `str | None` union syntax; no walrus operator in type annotations.
- Pydantic v2 — `model_validate`, `model_dump_json`, `model_validate_json` only; no v1 APIs.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/skills/`, or `careeros/sources/`.
- LLM calls via `litellm.completion()` only. Model: `os.environ.get("CAREEROS_MODEL", "claude-haiku-4-5-20251001")`.
- Prompts: instruction string + concatenated user text. No `.format()` or f-strings with user data.
- Activity log is append-only. No event is ever edited or deleted.
- No new runtime dependencies. HTTP via `urllib.request` stdlib.
- All tests offline — `litellm.completion` and `urllib.request.urlopen` are mocked.
- Run tests with: `/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/ -q`

---

## Task 1: Job data model and ID generation

**Files:**
- Modify: `careeros/core/models.py`
- Create: `careeros/core/job_id.py`
- Test: `tests/test_job_model.py`

**Interfaces:**
- Produces:
  - `JOB_STAGES: tuple[str, ...]` — the five valid stage strings
  - `Job` — Pydantic v2 model with `save(storage)`, `load(storage, job_id)`, `list_all(storage)`
  - `make_job_id(company: str, title: str) -> str` — from `careeros.core.job_id`

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_model.py`:

```python
import pytest
from datetime import datetime, timezone
from careeros.core.models import Job, JOB_STAGES
from careeros.core.job_id import make_job_id
from careeros.storage.filesystem import LocalFilesystemStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_job(**kwargs) -> Job:
    defaults = dict(
        id="acme-sre-ab12",
        source="manual",
        company="Acme",
        title="Senior SRE",
        stage="saved",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return Job(**defaults)


def test_job_save_writes_file(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    job = _make_job()
    job.save(storage)
    assert (tmp_path / "jobs" / "acme-sre-ab12.json").exists()


def test_job_load_round_trips(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    job = _make_job(company="Stripe", title="Staff Engineer", url="https://stripe.com/jobs/1")
    job.save(storage)
    loaded = Job.load(storage, job.id)
    assert loaded.company == "Stripe"
    assert loaded.title == "Staff Engineer"
    assert loaded.url == "https://stripe.com/jobs/1"


def test_job_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        Job.load(storage, "nonexistent-id")


def test_list_all_empty(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert Job.list_all(storage) == []


def test_list_all_sorted_by_created_at_desc(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    j1 = _make_job(id="job-1", created_at="2026-01-01T00:00:00+00:00", updated_at=_now())
    j2 = _make_job(id="job-2", created_at="2026-06-01T00:00:00+00:00", updated_at=_now())
    j1.save(storage)
    j2.save(storage)
    result = Job.list_all(storage)
    assert result[0].id == "job-2"
    assert result[1].id == "job-1"


def test_description_capped_at_4000_chars(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    job = _make_job(description="x" * 5000)
    job.save(storage)
    loaded = Job.load(storage, job.id)
    assert len(loaded.description) == 4000


def test_job_stages_has_five_values():
    assert set(JOB_STAGES) == {"saved", "applied", "interviewing", "offer", "closed"}
    assert len(JOB_STAGES) == 5


def test_make_job_id_format():
    job_id = make_job_id("Acme Corp", "Senior SRE")
    parts = job_id.rsplit("-", 1)
    assert len(parts) == 2
    assert len(parts[1]) == 4
    assert all(c in "0123456789abcdef" for c in parts[1])


def test_make_job_id_unique():
    id1 = make_job_id("Acme", "SRE")
    id2 = make_job_id("Acme", "SRE")
    assert id1 != id2


def test_make_job_id_slugified():
    job_id = make_job_id("Acme Corp!", "Senior SRE / DevOps")
    prefix = job_id.rsplit("-", 1)[0]
    assert prefix == prefix.lower()
    assert all(c.isalnum() or c == "-" for c in prefix)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_model.py -q
```

Expected: `ImportError` or `AttributeError` — `Job`, `JOB_STAGES`, `make_job_id` don't exist yet.

- [ ] **Step 3: Create `careeros/core/job_id.py`**

```python
import os
import re


def make_job_id(company: str, title: str) -> str:
    def slugify(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    company_slug = slugify(company)[:12]
    title_slug = slugify(title)[:16]
    suffix = os.urandom(2).hex()
    parts = [p for p in [company_slug, title_slug] if p]
    base = "-".join(parts)[:28].rstrip("-")
    return f"{base}-{suffix}"
```

- [ ] **Step 4: Add `Job` and `JOB_STAGES` to `careeros/core/models.py`**

Add these imports at the top of `models.py` (it already imports `StorageProvider`):

```python
from datetime import datetime, timezone
```

Then add after the existing `Goals` class at the bottom of `careeros/core/models.py`:

```python
JOB_STAGES = ("saved", "applied", "interviewing", "offer", "closed")

_JOB_DESC_CAP = 4000


class Job(BaseModel):
    id: str
    source: str
    source_id: str | None = None
    url: str | None = None
    company: str
    title: str
    location: str | None = None
    remote: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    description: str | None = None
    requirements: list[str] = []
    stage: str = "saved"
    applied_at: str | None = None
    notes: list[str] = []
    created_at: str
    updated_at: str

    def save(self, storage: StorageProvider) -> None:
        data = self
        if data.description and len(data.description) > _JOB_DESC_CAP:
            data = data.model_copy(update={"description": data.description[:_JOB_DESC_CAP]})
        storage.atomic_write(f"jobs/{self.id}.json", data.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, job_id: str) -> "Job":
        path = f"jobs/{job_id}.json"
        if not storage.exists(path):
            raise FileNotFoundError(f"Job {job_id!r} not found")
        return cls.model_validate_json(storage.read(path).decode())

    @classmethod
    def list_all(cls, storage: StorageProvider) -> list["Job"]:
        paths = [p for p in storage.list("jobs") if p.endswith(".json")]
        jobs = [cls.model_validate_json(storage.read(p).decode()) for p in paths]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_model.py -q
```

Expected: all 11 tests pass.

- [ ] **Step 6: Run full suite — verify no regressions**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/ -q
```

Expected: all existing tests + 11 new ones pass.

- [ ] **Step 7: Commit**

```bash
git add careeros/core/models.py careeros/core/job_id.py tests/test_job_model.py
git commit -m "feat: add Job model, JOB_STAGES constant, and make_job_id"
```

---

## Task 2: LLM JD parsing

**Files:**
- Create: `careeros/skills/job_extract.py`
- Test: `tests/test_job_extract.py`

**Interfaces:**
- Consumes: `litellm.completion` (mocked in tests)
- Produces: `extract_job_fields(jd_text: str, model: str | None = None) -> dict`
  - Returns dict with keys: `company`, `title`, `location`, `remote`, `salary_min`, `salary_max`, `currency`, `requirements`, `summary`. Missing fields are `None` or `[]`. Never raises.

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_extract.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from careeros.skills.job_extract import extract_job_fields, DEFAULT_LLM_MODEL


def _mock_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


_FULL = '{"company":"Acme","title":"Senior SRE","location":"SF, CA","remote":false,"salary_min":150000,"salary_max":200000,"currency":"USD","requirements":["Python","Kubernetes"],"summary":"Great SRE role."}'
_PARTIAL = '{"company":"Stripe","title":"Engineer","location":null,"remote":null,"salary_min":null,"salary_max":null,"currency":null,"requirements":[],"summary":null}'
_FENCED = '```json\n' + _FULL + '\n```'


def test_full_extraction():
    with patch("litellm.completion", return_value=_mock_resp(_FULL)):
        result = extract_job_fields("dummy jd")
    assert result["company"] == "Acme"
    assert result["salary_min"] == 150000
    assert result["requirements"] == ["Python", "Kubernetes"]


def test_partial_extraction_null_salary():
    with patch("litellm.completion", return_value=_mock_resp(_PARTIAL)):
        result = extract_job_fields("dummy jd")
    assert result["company"] == "Stripe"
    assert result["salary_min"] is None


def test_fenced_json_stripped():
    with patch("litellm.completion", return_value=_mock_resp(_FENCED)):
        result = extract_job_fields("dummy jd")
    assert result["company"] == "Acme"


def test_llm_failure_returns_empty_dict():
    with patch("litellm.completion", side_effect=Exception("API error")):
        result = extract_job_fields("dummy jd")
    assert result == {}


def test_bad_json_returns_empty_dict():
    with patch("litellm.completion", return_value=_mock_resp("not valid json")):
        result = extract_job_fields("dummy jd")
    assert result == {}


def test_uses_default_model(monkeypatch):
    monkeypatch.delenv("CAREEROS_MODEL", raising=False)
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields("dummy jd")
    assert mock_c.call_args.kwargs["model"] == DEFAULT_LLM_MODEL


def test_respects_careeros_model_env_var(monkeypatch):
    monkeypatch.setenv("CAREEROS_MODEL", "gpt-4o-mini")
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields("dummy jd")
    assert mock_c.call_args.kwargs["model"] == "gpt-4o-mini"


def test_model_param_overrides_env(monkeypatch):
    monkeypatch.setenv("CAREEROS_MODEL", "gpt-4o-mini")
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields("dummy jd", model="ollama/llama3.2")
    assert mock_c.call_args.kwargs["model"] == "ollama/llama3.2"


def test_jd_text_in_prompt():
    jd = "Looking for a Senior SRE with Python experience"
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields(jd)
    messages = mock_c.call_args.kwargs["messages"]
    assert jd in messages[0]["content"]


def test_jd_truncated_to_4000_chars():
    long_jd = "x" * 6000
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields(long_jd)
    content = mock_c.call_args.kwargs["messages"][0]["content"]
    assert "x" * 4001 not in content
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_extract.py -q
```

Expected: `ImportError` — `careeros.skills.job_extract` doesn't exist yet.

- [ ] **Step 3: Create `careeros/skills/job_extract.py`**

```python
import json
import os
import litellm

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

_JD_INSTRUCTIONS = """\
Extract the following fields from this job description as JSON.
Return ONLY valid JSON — no markdown, no explanation.
Use null for any field not found.

{
  "company": "<company name or null>",
  "title": "<job title or null>",
  "location": "<city/region or null>",
  "remote": <true | false | null>,
  "salary_min": <integer annual salary minimum or null>,
  "salary_max": <integer annual salary maximum or null>,
  "currency": "<USD | GBP | EUR | AUD | null>",
  "requirements": ["<requirement>", "..."],
  "summary": "<1-2 sentence job summary or null>"
}

Job description:
"""

_JD_CAP = 4000


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_job_fields(jd_text: str, model: str | None = None) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    truncated = jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": _JD_INSTRUCTIONS + truncated}],
        )
        return _parse_json(resp.choices[0].message.content)
    except Exception:
        return {}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_extract.py -q
```

Expected: all 11 tests pass.

- [ ] **Step 5: Run full suite — verify no regressions**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/ -q
```

- [ ] **Step 6: Commit**

```bash
git add careeros/skills/job_extract.py tests/test_job_extract.py
git commit -m "feat: add LLM JD parsing via job_extract"
```

---

## Task 3: ATS integration — Greenhouse and Lever

**Files:**
- Create: `careeros/sources/__init__.py`
- Create: `careeros/sources/ats.py`
- Test: `tests/test_ats.py`

**Interfaces:**
- Produces:
  - `ATSFetchError(Exception)` — raised on 404, timeout, or malformed JSON
  - `fetch_greenhouse(company: str) -> list[dict]`
  - `fetch_lever(company: str) -> list[dict]`
  - Both return list of dicts: `{"source_id": str, "title": str, "url": str, "location": str|None, "description": str|None}`

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ats.py`:

```python
import json
import pytest
import urllib.error
from unittest.mock import patch, MagicMock
from careeros.sources.ats import fetch_greenhouse, fetch_lever, ATSFetchError


GH_FIXTURE = {
    "jobs": [
        {
            "id": 123456,
            "title": "Senior SRE",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123456",
            "location": {"name": "San Francisco, CA"},
            "content": "<p>We are looking for a <b>Senior SRE</b> with Python experience.</p>",
        }
    ]
}

LEVER_FIXTURE = [
    {
        "id": "abc-def-123",
        "text": "Staff Engineer",
        "hostedUrl": "https://jobs.lever.co/stripe/abc-def-123",
        "categories": {"location": "New York, NY"},
        "descriptionPlain": "Great role at Stripe.",
    }
]


def _mock_urlopen(payload: object, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_greenhouse_parses_jobs():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(GH_FIXTURE)):
        result = fetch_greenhouse("acme")
    assert len(result) == 1
    assert result[0]["source_id"] == "123456"
    assert result[0]["title"] == "Senior SRE"
    assert result[0]["location"] == "San Francisco, CA"
    assert result[0]["url"] == "https://boards.greenhouse.io/acme/jobs/123456"


def test_fetch_greenhouse_strips_html():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(GH_FIXTURE)):
        result = fetch_greenhouse("acme")
    assert "<p>" not in result[0]["description"]
    assert "<b>" not in result[0]["description"]
    assert "Senior SRE" in result[0]["description"]


def test_fetch_greenhouse_empty_jobs():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"jobs": []})):
        result = fetch_greenhouse("acme")
    assert result == []


def test_fetch_lever_parses_postings():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(LEVER_FIXTURE)):
        result = fetch_lever("stripe")
    assert len(result) == 1
    assert result[0]["source_id"] == "abc-def-123"
    assert result[0]["title"] == "Staff Engineer"
    assert result[0]["location"] == "New York, NY"
    assert result[0]["url"] == "https://jobs.lever.co/stripe/abc-def-123"


def test_fetch_lever_description_plain():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(LEVER_FIXTURE)):
        result = fetch_lever("stripe")
    assert result[0]["description"] == "Great role at Stripe."


def test_fetch_greenhouse_404_raises():
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        with pytest.raises(ATSFetchError):
            fetch_greenhouse("unknown-company")


def test_fetch_greenhouse_timeout_raises():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(ATSFetchError):
            fetch_greenhouse("acme")


def test_fetch_lever_404_raises():
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        with pytest.raises(ATSFetchError):
            fetch_lever("unknown-company")


def test_description_capped_at_4000():
    long_content = "<p>" + "x" * 5000 + "</p>"
    fixture = {"jobs": [{**GH_FIXTURE["jobs"][0], "content": long_content}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(fixture)):
        result = fetch_greenhouse("acme")
    assert len(result[0]["description"]) <= 4000
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_ats.py -q
```

Expected: `ImportError` — `careeros.sources` package doesn't exist yet.

- [ ] **Step 3: Create `careeros/sources/__init__.py`**

```python
```

(empty file)

- [ ] **Step 4: Create `careeros/sources/ats.py`**

```python
import json
import urllib.error
import urllib.request
from html.parser import HTMLParser


class ATSFetchError(Exception):
    pass


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _get_json(url: str) -> object:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "careeros/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ATSFetchError("not_found")
        raise ATSFetchError(str(e))
    except urllib.error.URLError as e:
        raise ATSFetchError(str(e))
    except json.JSONDecodeError as e:
        raise ATSFetchError(f"malformed JSON: {e}")


_DESC_CAP = 4000
_GH_BASE = "https://boards-api.greenhouse.io/v1/boards"
_LEVER_BASE = "https://api.lever.co/v0/postings"


def fetch_greenhouse(company: str) -> list[dict]:
    data = _get_json(f"{_GH_BASE}/{company}/jobs?content=true")
    result = []
    for job in data.get("jobs", []):
        loc = job.get("location") or {}
        raw = job.get("content") or ""
        result.append({
            "source_id": str(job["id"]),
            "title": job["title"],
            "url": job["absolute_url"],
            "location": loc.get("name"),
            "description": _strip_html(raw)[:_DESC_CAP],
        })
    return result


def fetch_lever(company: str) -> list[dict]:
    data = _get_json(f"{_LEVER_BASE}/{company}?mode=json")
    result = []
    for posting in data:
        cats = posting.get("categories") or {}
        desc = posting.get("descriptionPlain") or ""
        result.append({
            "source_id": posting["id"],
            "title": posting["text"],
            "url": posting["hostedUrl"],
            "location": cats.get("location"),
            "description": desc[:_DESC_CAP],
        })
    return result
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_ats.py -q
```

Expected: all 10 tests pass.

- [ ] **Step 6: Run full suite — verify no regressions**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add careeros/sources/__init__.py careeros/sources/ats.py tests/test_ats.py
git commit -m "feat: add Greenhouse and Lever ATS fetch with ATSFetchError"
```

---

## Task 4: CLI — `job add`, `job list`, `job show`, wire main.py

**Files:**
- Create: `careeros/cli/job_cmd.py`
- Modify: `careeros/cli/main.py`
- Test: `tests/test_job_cmd.py` (partial — add/list/show tests only)

**Interfaces:**
- Consumes:
  - `Job`, `JOB_STAGES` from `careeros.core.models`
  - `make_job_id(company, title)` from `careeros.core.job_id`
  - `extract_job_fields(jd_text)` from `careeros.skills.job_extract`
  - `ActivityLogger`, `ActivityEvent` from `careeros.core.activity`
  - `open_workspace` from `careeros.workspace.manager`
  - `GlobalConfig` from `careeros.config`
  - `LocalFilesystemStorage` from `careeros.storage.filesystem`
- Produces: `job_app` Typer sub-app registered as `careeros job`

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_cmd.py`:

```python
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from typer.testing import CliRunner
from careeros.cli.main import app
from careeros.core.models import Job, JOB_STAGES
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ws(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    return tmp_path


@pytest.fixture
def ws_with_job(ws):
    storage = LocalFilesystemStorage(str(ws))
    job = Job(
        id="acme-sre-ab12",
        source="manual",
        company="Acme",
        title="Senior SRE",
        stage="saved",
        created_at=_now(),
        updated_at=_now(),
    )
    job.save(storage)
    return ws, job


@pytest.fixture
def mock_extraction():
    fields = {
        "company": "Acme",
        "title": "Senior SRE",
        "location": "SF, CA",
        "remote": False,
        "salary_min": 150000,
        "salary_max": 200000,
        "currency": "USD",
        "requirements": ["Python", "Kubernetes"],
        "summary": "Great role.",
    }
    with patch("careeros.cli.job_cmd.extract_job_fields", return_value=fields):
        yield fields


def test_job_add_writes_job_file(ws, mock_extraction):
    runner = CliRunner()
    # url (blank), jd text, blank line (end jd),
    # company (accept Acme), title (accept Senior SRE), location (accept SF, CA),
    # remote (n=False), salary_min (blank=skip), salary_max (blank=skip), currency (accept USD)
    user_input = "\nsome jd text\n\n\n\n\nn\n\n\nUSD\n"
    result = runner.invoke(app, ["job", "add", "--workspace", str(ws)], input=user_input)
    assert result.exit_code == 0, result.output
    job_files = list((ws / "jobs").glob("*.json"))
    assert len(job_files) == 1


def test_job_add_logs_activity(ws, mock_extraction):
    runner = CliRunner()
    user_input = "\nsome jd text\n\n\n\n\nn\n\n\nUSD\n"
    runner.invoke(app, ["job", "add", "--workspace", str(ws)], input=user_input)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = (ws / "activity" / f"{today}.jsonl").read_text()
    event_types = [json.loads(l)["event_type"] for l in log.strip().splitlines() if l]
    assert "job_added" in event_types


def test_job_add_extraction_failure_proceeds(ws):
    with patch("careeros.cli.job_cmd.extract_job_fields", return_value={}):
        runner = CliRunner()
        # blank url, blank jd, blank (end jd), company=Acme, title=Engineer,
        # location blank, remote n, sal_min blank, sal_max blank, currency USD
        user_input = "\n\n\nAcme\nEngineer\n\nn\n\n\nUSD\n"
        result = runner.invoke(app, ["job", "add", "--workspace", str(ws)], input=user_input)
    assert result.exit_code == 0, result.output
    job_files = list((ws / "jobs").glob("*.json"))
    assert len(job_files) == 1


def test_job_list_empty(ws):
    runner = CliRunner()
    result = runner.invoke(app, ["job", "list", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


def test_job_list_shows_jobs(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(app, ["job", "list", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Acme" in result.output
    assert "Senior SRE" in result.output


def test_job_list_stage_filter(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(app, ["job", "list", "--workspace", str(ws), "--stage", "applied"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


def test_job_show_known_id(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(app, ["job", "show", job.id, "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Acme" in result.output


def test_job_show_unknown_id(ws):
    runner = CliRunner()
    result = runner.invoke(app, ["job", "show", "nonexistent-id", "--workspace", str(ws)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_cmd.py -q
```

Expected: `ImportError` or command-not-found errors — `job_cmd` doesn't exist yet.

- [ ] **Step 3: Create `careeros/cli/job_cmd.py`** with `add`, `list`, `show`

```python
from datetime import datetime, timezone
import typer
from rich import print as rprint
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.prompt import Confirm, Prompt

from careeros.config import GlobalConfig
from careeros.core.activity import ActivityLogger
from careeros.core.job_id import make_job_id
from careeros.core.models import Job, JOB_STAGES
from careeros.skills.job_extract import extract_job_fields
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace

job_app = typer.Typer(name="job", help="Manage your job pipeline.")
console = Console()


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


@job_app.command("add")
def add_cmd(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    ctx = open_workspace(storage)
    logger = ActivityLogger(ctx.storage)

    url = Prompt.ask("URL (optional)", default="") or None

    rprint("Paste job description (blank line + Enter to finish):")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
    except EOFError:
        pass
    jd_text = "\n".join(lines)

    fields: dict = {}
    if jd_text:
        rprint("Extracting fields from job description...")
        fields = extract_job_fields(jd_text)
        if not fields:
            rprint("[yellow]Could not extract fields automatically.[/yellow]")

    company = Prompt.ask("Company", default=fields.get("company") or "")
    if not company:
        rprint("[red]Company is required.[/red]")
        raise typer.Exit(1)

    title = Prompt.ask("Title", default=fields.get("title") or "")
    if not title:
        rprint("[red]Title is required.[/red]")
        raise typer.Exit(1)

    location = Prompt.ask("Location", default=fields.get("location") or "") or None

    remote_default = bool(fields.get("remote")) if fields.get("remote") is not None else False
    remote = Confirm.ask("Remote?", default=remote_default)

    sal_min_str = Prompt.ask("Salary min", default=str(fields.get("salary_min") or ""))
    salary_min = int(sal_min_str) if sal_min_str.strip().isdigit() else None

    sal_max_str = Prompt.ask("Salary max", default=str(fields.get("salary_max") or ""))
    salary_max = int(sal_max_str) if sal_max_str.strip().isdigit() else None

    currency = Prompt.ask("Currency", default=fields.get("currency") or "USD")

    now = _now()
    job_id = make_job_id(company, title)
    job = Job(
        id=job_id,
        source="manual",
        url=url,
        company=company,
        title=title,
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        description=jd_text or None,
        requirements=fields.get("requirements") or [],
        stage="saved",
        created_at=now,
        updated_at=now,
    )
    job.save(storage)
    logger.log(logger.new_event(
        "job_added", "add", f"Job added: {company} — {title}",
        entity_type="job", entity_id=job_id,
    ))
    rprint(f"\n[green]Saved[/green] as [bold]{job_id}[/bold]  (stage: saved)")


@job_app.command("list")
def list_cmd(
    stage: str = typer.Option(None, "--stage", help="Filter by stage"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    jobs = Job.list_all(storage)

    if stage:
        if stage not in JOB_STAGES:
            rprint(f"[red]Invalid stage '{stage}'. Valid: {' '.join(JOB_STAGES)}[/red]")
            raise typer.Exit(1)
        jobs = [j for j in jobs if j.stage == stage]

    if not jobs:
        rprint("No jobs found.")
        return

    table = Table(show_header=True)
    table.add_column("ID")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Stage")
    table.add_column("Applied")
    for job in jobs:
        table.add_row(job.id, job.company, job.title, job.stage, job.applied_at or "")
    console.print(table)


@job_app.command("show")
def show_cmd(
    id: str = typer.Argument(..., help="Job ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    try:
        job = Job.load(storage, id)
    except FileNotFoundError:
        rprint(f"[red]Job {id!r} not found.[/red]")
        raise typer.Exit(1)

    lines = [
        f"[bold]Company:[/bold]  {job.company}",
        f"[bold]Title:[/bold]    {job.title}",
        f"[bold]Stage:[/bold]    {job.stage}",
        f"[bold]Source:[/bold]   {job.source}",
        f"[bold]URL:[/bold]      {job.url or '—'}",
        f"[bold]Location:[/bold] {job.location or '—'}",
        f"[bold]Remote:[/bold]   {job.remote}",
        f"[bold]Salary:[/bold]   {job.salary_min}–{job.salary_max} {job.currency}",
        f"[bold]Applied:[/bold]  {job.applied_at or '—'}",
        f"[bold]Created:[/bold]  {job.created_at[:10]}",
    ]
    if job.requirements:
        lines.append("[bold]Requirements:[/bold]")
        for req in job.requirements:
            lines.append(f"  • {req}")
    if job.notes:
        lines.append("[bold]Notes:[/bold]")
        for note in job.notes:
            lines.append(f"  • {note}")

    rprint(Panel("\n".join(lines), title=f"[bold]{id}[/bold]"))
```

- [ ] **Step 4: Wire `job_app` into `careeros/cli/main.py`**

Open `careeros/cli/main.py` and add two lines:

```python
import typer
from careeros.cli.onboard import onboard_cmd
from careeros.cli.portability import export_cmd, import_workspace_cmd
from careeros.cli.workspace_cmd import workspace_app
from careeros.cli.job_cmd import job_app          # add this line

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")
app.add_typer(job_app, name="job")                # add this line
app.command("export")(export_cmd)
app.command("import")(import_workspace_cmd)

if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_cmd.py -q
```

Expected: all 9 tests in this file pass.

- [ ] **Step 6: Run full suite — verify no regressions**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/job_cmd.py careeros/cli/main.py tests/test_job_cmd.py
git commit -m "feat: add job add/list/show CLI commands"
```

---

## Task 5: CLI — `job update`, `job note`, `job search`

**Files:**
- Modify: `careeros/cli/job_cmd.py`
- Modify: `tests/test_job_cmd.py`

**Interfaces:**
- Consumes:
  - `ATSFetchError`, `fetch_greenhouse`, `fetch_lever` from `careeros.sources.ats`
  - All models and helpers from Tasks 1–4
- Produces: complete `careeros job` command group

---

- [ ] **Step 1: Write the failing tests**

Add these tests to the bottom of `tests/test_job_cmd.py`:

```python
def test_job_update_changes_stage(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "update", job.id, "--stage", "applied", "--workspace", str(ws)]
    )
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(str(ws))
    updated = Job.load(storage, job.id)
    assert updated.stage == "applied"


def test_job_update_sets_applied_at(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    runner.invoke(app, ["job", "update", job.id, "--stage", "applied", "--workspace", str(ws)])
    storage = LocalFilesystemStorage(str(ws))
    updated = Job.load(storage, job.id)
    assert updated.applied_at is not None


def test_job_update_logs_activity(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    runner.invoke(app, ["job", "update", job.id, "--stage", "applied", "--workspace", str(ws)])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = (ws / "activity" / f"{today}.jsonl").read_text()
    event_types = [json.loads(l)["event_type"] for l in log.strip().splitlines() if l]
    assert "job_stage_changed" in event_types


def test_job_update_invalid_stage(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "update", job.id, "--stage", "badstage", "--workspace", str(ws)]
    )
    assert result.exit_code == 1
    assert "Invalid stage" in result.output


def test_job_update_unknown_id(ws):
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "update", "nonexistent", "--stage", "applied", "--workspace", str(ws)]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_job_note_appends(ws_with_job):
    ws, job = ws_with_job
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "note", job.id, "Great team culture", "--workspace", str(ws)]
    )
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(str(ws))
    updated = Job.load(storage, job.id)
    assert any("Great team culture" in n for n in updated.notes)


def test_job_note_unknown_id(ws):
    runner = CliRunner()
    result = runner.invoke(
        app, ["job", "note", "nonexistent", "some note", "--workspace", str(ws)]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_job_search_saves_jobs(ws):
    from careeros.sources.ats import ATSFetchError

    postings = [
        {
            "source_id": "123",
            "title": "Senior SRE",
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "location": "SF",
            "description": "Great role.",
        }
    ]
    with patch("careeros.cli.job_cmd.fetch_greenhouse", return_value=postings):
        runner = CliRunner()
        # user picks job 1
        result = runner.invoke(
            app,
            ["job", "search", "--source", "greenhouse", "--company", "acme", "--workspace", str(ws)],
            input="1\n",
        )
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(str(ws))
    jobs = Job.list_all(storage)
    assert len(jobs) == 1
    assert jobs[0].title == "Senior SRE"
    assert jobs[0].source == "greenhouse"


def test_job_search_ats_error(ws):
    from careeros.sources.ats import ATSFetchError

    with patch("careeros.cli.job_cmd.fetch_greenhouse", side_effect=ATSFetchError("not_found")):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["job", "search", "--source", "greenhouse", "--company", "nobody", "--workspace", str(ws)],
        )
    assert result.exit_code == 1


def test_job_search_quit(ws):
    postings = [
        {"source_id": "1", "title": "SRE", "url": "https://example.com", "location": None, "description": ""}
    ]
    with patch("careeros.cli.job_cmd.fetch_greenhouse", return_value=postings):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["job", "search", "--source", "greenhouse", "--company", "acme", "--workspace", str(ws)],
            input="q\n",
        )
    assert result.exit_code == 0
    storage = LocalFilesystemStorage(str(ws))
    assert Job.list_all(storage) == []
```

- [ ] **Step 2: Run new tests — verify they fail**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_cmd.py -q
```

Expected: the 11 new tests fail — `update`, `note`, `search` commands don't exist yet.

- [ ] **Step 3: Add `update`, `note`, `search` to `careeros/cli/job_cmd.py`**

Add these imports at the top of `job_cmd.py` (after the existing imports):

```python
from careeros.sources.ats import ATSFetchError, fetch_greenhouse, fetch_lever
```

Then add these three command functions after `show_cmd`:

```python
@job_app.command("update")
def update_cmd(
    id: str = typer.Argument(..., help="Job ID"),
    stage: str = typer.Option(..., "--stage", help="New stage"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    if stage not in JOB_STAGES:
        rprint(f"[red]Invalid stage '{stage}'. Valid: {' '.join(JOB_STAGES)}[/red]")
        raise typer.Exit(1)

    storage = _get_storage(workspace)
    try:
        job = Job.load(storage, id)
    except FileNotFoundError:
        rprint(f"[red]Job {id!r} not found.[/red]")
        raise typer.Exit(1)

    ctx = open_workspace(storage)
    logger = ActivityLogger(ctx.storage)

    from_stage = job.stage
    now = _now()
    updates: dict = {"stage": stage, "updated_at": now}
    if stage == "applied" and job.applied_at is None:
        updates["applied_at"] = now

    job = job.model_copy(update=updates)
    job.save(storage)
    logger.log(logger.new_event(
        "job_stage_changed", "update",
        f"Job stage changed: {id} {from_stage} → {stage}",
        entity_type="job", entity_id=id,
    ))
    rprint(f"[bold]{id}[/bold]  {from_stage} → [green]{stage}[/green]")


@job_app.command("note")
def note_cmd(
    id: str = typer.Argument(..., help="Job ID"),
    text: str = typer.Argument(..., help="Note text"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    try:
        job = Job.load(storage, id)
    except FileNotFoundError:
        rprint(f"[red]Job {id!r} not found.[/red]")
        raise typer.Exit(1)

    ctx = open_workspace(storage)
    logger = ActivityLogger(ctx.storage)

    timestamp = _now()
    note_entry = f"{timestamp} — {text}"
    job = job.model_copy(update={"notes": job.notes + [note_entry], "updated_at": timestamp})
    job.save(storage)
    logger.log(logger.new_event(
        "job_note_added", "note", f"Note added to {id}",
        entity_type="job", entity_id=id,
    ))
    rprint(f"Note added to [bold]{id}[/bold]")


@job_app.command("search")
def search_cmd(
    source: str = typer.Option(..., "--source", help="greenhouse | lever"),
    company: str = typer.Option(..., "--company", help="Company slug (e.g. stripe, acme)"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    storage = _get_storage(workspace)
    ctx = open_workspace(storage)
    logger = ActivityLogger(ctx.storage)

    try:
        if source == "greenhouse":
            postings = fetch_greenhouse(company)
        elif source == "lever":
            postings = fetch_lever(company)
        else:
            rprint(f"[red]Unknown source '{source}'. Valid: greenhouse lever[/red]")
            raise typer.Exit(1)
    except ATSFetchError as e:
        if "not_found" in str(e):
            rprint(f"[red]Company '{company}' not found on {source}.[/red]")
        else:
            rprint(f"[red]Could not reach {source} API. Check your connection.[/red]")
        raise typer.Exit(1)

    if not postings:
        rprint(f"No open roles found for '{company}' on {source}.")
        return

    table = Table(show_header=True)
    table.add_column("#", style="bold")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("URL")
    for i, p in enumerate(postings, 1):
        table.add_row(str(i), p["title"], p.get("location") or "—", p["url"])
    console.print(table)

    picks_str = Prompt.ask("Pick jobs to save (e.g. 1 3 5, or q to quit)")
    if picks_str.strip().lower() == "q":
        return

    indices = []
    for part in picks_str.split():
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(postings):
                indices.append(idx)

    saved = 0
    now = _now()
    for idx in indices:
        p = postings[idx]
        job_id = make_job_id(company, p["title"])
        job = Job(
            id=job_id,
            source=source,
            source_id=p["source_id"],
            url=p["url"],
            company=company,
            title=p["title"],
            location=p.get("location"),
            description=p.get("description"),
            stage="saved",
            created_at=now,
            updated_at=now,
        )
        job.save(storage)
        logger.log(logger.new_event(
            "job_added", "search", f"Job saved from {source}: {company} — {p['title']}",
            entity_type="job", entity_id=job_id,
        ))
        saved += 1

    rprint(f"[green]Saved {saved} job(s)[/green]")
```

- [ ] **Step 4: Run all tests — verify they pass**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/test_job_cmd.py -q
```

Expected: all 20 tests in the file pass.

- [ ] **Step 5: Run full suite — verify no regressions**

```bash
/Users/kaushal/Projects/careeros/.venv/bin/pytest tests/ -q
```

Expected: all tests pass. Count should be 79 (existing) + 11 (job model) + 11 (job extract) + 10 (ats) + 20 (job cmd) = 131 tests.

- [ ] **Step 6: Smoke test the CLI**

```bash
careeros job --help
careeros job add --help
careeros job list --help
careeros job search --help
```

Expected: all commands show their help text with no errors.

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/job_cmd.py tests/test_job_cmd.py
git commit -m "feat: add job update/note/search commands — Phase 2 complete"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by |
|---|---|
| §1.1 Job model + JOB_STAGES | Task 1 |
| §1.2 make_job_id | Task 1 |
| §1.3 Storage layout `jobs/<id>.json` | Task 1 |
| §1.4 Activity log events (job_added, job_stage_changed, job_note_added) | Tasks 4 & 5 |
| §2.1 extract_job_fields, _parse_json, JD cap, env var, failure fallback | Task 2 |
| §3.1 ATSFetchError, fetch_greenhouse, fetch_lever, HTML strip, field mapping | Task 3 |
| §4.1 job add (interactive, LLM, confirm) | Task 4 |
| §4.1 job search (ATS fetch, numbered table, pick) | Task 5 |
| §4.1 job list (table, stage filter) | Task 4 |
| §4.1 job show (panel, notes list) | Task 4 |
| §4.1 job update (stage validation, applied_at, activity log) | Task 5 |
| §4.1 job note (timestamp prefix, activity log) | Task 5 |
| §5 Error handling (unknown ID → exit 1, invalid stage, ATS errors, no workspace) | Tasks 4 & 5 |
| §6.1 test_job_model.py | Task 1 |
| §6.2 test_job_extract.py | Task 2 |
| §6.3 test_ats.py | Task 3 |
| §6.4 test_job_cmd.py | Tasks 4 & 5 |
| §7 File map | All tasks |

All spec requirements are covered. No placeholders remain.
