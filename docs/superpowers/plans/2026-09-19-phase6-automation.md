# Phase 6 — Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add unattended, scheduled discovery and score-threshold auto-apply to CareerOS via a new `careeros discover-and-apply` CLI command, backed by a new `AutomationRuntime`.

**Architecture:** A new `AutomationRuntime` (third `AgentRuntime` implementation, alongside `LocalRuntime` and `ClaudeCodeRuntime` from Phase 5) always auto-approves, because the score-threshold check happens before any approval is requested. A new `discover_and_apply_cmd` runs the same discover→score pipeline `browse_cmd` uses across all configured boards, saves every match, then auto-applies (using the same fill pipeline `apply_cmd` uses, minus the interactive cover-letter review loop) to jobs scoring at or above a configured threshold, capped at a max applies-per-run safety limit. The user schedules the command themselves via cron/launchd — CareerOS adds no daemon.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, Rich, Playwright (headless).

**Spec:** `docs/superpowers/specs/2026-09-19-phase6-automation-design.md`

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/`
- Activity logs are append-only; no event is ever edited or deleted
- `atomic_write` must use write-to-temp-then-rename (never write directly to final path)
- Activity summaries are built via string concatenation only — no `.format()` or f-strings with user data
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id` with the runtime's own identity
- `max_auto_applies_per_run` is enforced in `discover_and_apply_cmd`, not inside `AutomationRuntime` — the runtime resolves one proposal at a time and holds no run-level state
- `discover_and_apply_cmd` always launches the browser with `headless=True` — never interactive

---

### Task 1: AutomationRuntime + AutomationPolicy + Factory

**Files:**
- Create: `careeros/runtime/automation.py`
- Modify: `careeros/runtime/factory.py`
- Modify: `careeros/core/models.py` (append `AutomationPolicy` class)
- Test: `tests/test_automation_runtime.py`
- Test: `tests/test_runtime_factory.py` (modify — append tests for the new factory function)

**Interfaces:**
- Consumes: `ActionProposal`, `ApprovalResult`, `AgentRuntime` from `careeros.runtime.base` (Phase 5)
- Consumes: `ActivityEvent`, `ActivityLogger` from `careeros.core.activity` (existing)
- Consumes: `open_workspace` from `careeros.workspace.manager` (existing)
- Produces: `AutomationRuntime(storage: StorageProvider, ctx: WorkspaceContext, session_id: str)` — concrete `AgentRuntime` implementation, `agent_runtime_name = "automation"`
- Produces: `open_automation_runtime(storage: StorageProvider, session_id: str | None = None) -> AutomationRuntime`
- Produces: `AutomationPolicy(auto_apply_min_score: int, max_auto_applies_per_run: int, boards: list[str])` — Pydantic model with `.save(storage)` and `.load(storage)` (raises `FileNotFoundError` if missing, matching `Profile.load`'s strict pattern — no `load_or_empty` variant, since a missing policy file must be a hard error for a command that submits real applications)

- [ ] **Step 1: Write the failing test for AutomationPolicy**

Create `tests/test_automation_policy.py`:

```python
import pytest
from careeros.core.models import AutomationPolicy
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin", "indeed"])
    policy.save(storage)
    loaded = AutomationPolicy.load(storage)
    assert loaded.auto_apply_min_score == 90
    assert loaded.max_auto_applies_per_run == 5
    assert loaded.boards == ["linkedin", "indeed"]


def test_load_missing_file_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        AutomationPolicy.load(storage)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automation_policy.py -v`
Expected: FAIL with `ImportError: cannot import name 'AutomationPolicy'`

- [ ] **Step 3: Add AutomationPolicy to careeros/core/models.py**

Append this class to the end of `careeros/core/models.py` (after the `Job.list_all` classmethod, matching the file's existing indentation and style — do not modify any existing class):

```python
class AutomationPolicy(BaseModel):
    auto_apply_min_score: int
    max_auto_applies_per_run: int
    boards: list[str]

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("config/automation_policy.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "AutomationPolicy":
        if not storage.exists("config/automation_policy.json"):
            raise FileNotFoundError("config/automation_policy.json not found in workspace")
        return cls.model_validate_json(storage.read("config/automation_policy.json").decode())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_automation_policy.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing tests for AutomationRuntime**

Create `tests/test_automation_runtime.py`:

```python
from unittest.mock import MagicMock, patch
from careeros.core.activity import ActivityEvent
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.base import ActionProposal, ApprovalResult


def _make_event(agent_runtime="unset", session_id="unset"):
    return ActivityEvent(
        event_type="job_applied", action="apply", status="success",
        summary="Auto-applied", agent_runtime=agent_runtime, session_id=session_id,
    )


class TestAutomationRuntime:
    def test_read_workspace_decodes_bytes(self):
        storage = MagicMock()
        storage.read.return_value = b"hello"
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        assert runtime.read_workspace("profile/profile.json") == "hello"
        storage.read.assert_called_once_with("profile/profile.json")

    def test_write_workspace_encodes_and_atomic_writes(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        runtime.write_workspace("notes/note.txt", "hello world")
        storage.atomic_write.assert_called_once_with("notes/note.txt", b"hello world")

    def test_request_approval_always_approves(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="apply_to_job", summary="Auto-apply to Acme?")
        result = runtime.request_approval(proposal)
        assert result == ApprovalResult(approved=True, reason="auto-approved by automation policy")

    def test_request_approval_always_approves_regardless_of_proposal_content(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="anything", summary="Anything at all", entity_type="job", entity_id="x")
        result = runtime.request_approval(proposal)
        assert result.approved is True

    def test_record_activity_stamps_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        event = _make_event(agent_runtime="unset", session_id="unset")
        with patch("careeros.runtime.automation.ActivityLogger") as MockLogger:
            runtime = AutomationRuntime(storage, ctx, session_id="sess-42")
            runtime.record_activity(event)
        MockLogger.return_value.log.assert_called_once_with(event)
        assert event.agent_runtime == "automation"
        assert event.session_id == "sess-42"

    def test_new_event_sets_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-7")
        event = runtime.new_event("job_applied", "apply", "Auto-applied", entity_type="job", entity_id="j1")
        assert event.event_type == "job_applied"
        assert event.agent_runtime == "automation"
        assert event.session_id == "sess-7"
        assert event.entity_type == "job"
        assert event.entity_id == "j1"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_automation_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.runtime.automation'`

- [ ] **Step 7: Implement AutomationRuntime**

Create `careeros/runtime/automation.py`:

```python
from __future__ import annotations
from careeros.core.activity import ActivityEvent, ActivityLogger
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import WorkspaceContext


class AutomationRuntime:
    agent_runtime_name = "automation"

    def __init__(self, storage: StorageProvider, ctx: WorkspaceContext, session_id: str) -> None:
        self.storage = storage
        self.ctx = ctx
        self.session_id = session_id
        self._logger = ActivityLogger(storage, session_id=session_id)

    def read_workspace(self, path: str) -> str:
        return self.storage.read(path).decode()

    def write_workspace(self, path: str, content: str) -> None:
        self.storage.atomic_write(path, content.encode())

    def request_approval(self, proposal: ActionProposal) -> ApprovalResult:
        return ApprovalResult(approved=True, reason="auto-approved by automation policy")

    def record_activity(self, event: ActivityEvent) -> None:
        event.agent_runtime = self.agent_runtime_name
        event.session_id = self.session_id
        self._logger.log(event)

    def new_event(
        self, event_type: str, action: str, summary: str, status: str = "success", **kwargs
    ) -> ActivityEvent:
        return self._logger.new_event(
            event_type, action, summary, status=status, agent_runtime=self.agent_runtime_name, **kwargs
        )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_automation_runtime.py -v`
Expected: PASS (6 tests)

- [ ] **Step 9: Write the failing tests for the factory addition**

Append to `tests/test_runtime_factory.py` (add this import alongside the existing ones at the top of the file: `from careeros.runtime.automation import AutomationRuntime`, and `from careeros.runtime.factory import open_automation_runtime` alongside the existing factory import), then append these two test functions at the end of the file:

```python
def test_open_automation_runtime_bootstraps_existing_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_automation_runtime(storage)
    assert isinstance(runtime, AutomationRuntime)
    assert runtime.session_id


def test_open_automation_runtime_missing_manifest_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        open_automation_runtime(storage)
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `pytest tests/test_runtime_factory.py -v`
Expected: FAIL with `ImportError: cannot import name 'open_automation_runtime'`

- [ ] **Step 11: Add open_automation_runtime to the factory**

Modify `careeros/runtime/factory.py` — add this import alongside the existing ones at the top:

```python
from careeros.runtime.automation import AutomationRuntime
```

Then append this function at the end of the file:

```python
def open_automation_runtime(storage: StorageProvider, session_id: str | None = None) -> AutomationRuntime:
    ctx = open_workspace(storage)
    return AutomationRuntime(storage, ctx, session_id or uuid.uuid4().hex)
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `pytest tests/test_runtime_factory.py -v`
Expected: PASS (7 tests total)

- [ ] **Step 13: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 14: Commit**

```bash
git add careeros/runtime/automation.py careeros/runtime/factory.py careeros/core/models.py tests/test_automation_runtime.py tests/test_automation_policy.py tests/test_runtime_factory.py
git commit -m "feat: add AutomationRuntime, AutomationPolicy, and factory support"
```

---

### Task 2: discover-and-apply CLI Command

**Files:**
- Create: `careeros/cli/discover_and_apply_cmd.py`
- Modify: `careeros/cli/main.py` (wire in the new command)
- Test: `tests/test_discover_and_apply_cmd.py`

**Interfaces:**
- Consumes: `AutomationPolicy` from `careeros.core.models` (Task 1)
- Consumes: `open_automation_runtime` from `careeros.runtime.factory` (Task 1)
- Consumes: `ActionProposal` from `careeros.runtime.base` (Phase 5)
- Consumes: `SCRAPERS` dict, `_get_storage` pattern from `careeros.cli.browse_cmd` (existing) — this task does NOT import `browse_cmd`'s private `_get_storage`; it defines its own copy, matching the existing convention where `browse_cmd.py` and `apply_cmd.py` each define their own `_get_storage`
- Consumes: `fetch_jd_text`, `launch_browser` from `careeros.browser.driver` (existing)
- Consumes: `score_job` from `careeros.skills.job_score` (existing) — signature `score_job(jd_text: str, profile: Profile, skills: Skills) -> dict` returning `{"score": int, "reasoning": str}`
- Consumes: `job_query_from_profile` from `careeros.skills.browse_query` (existing) — signature `job_query_from_profile(profile: Profile, goals: Goals) -> str`
- Consumes: `generate_cover_letter` from `careeros.skills.cover_letter` (existing) — signature `generate_cover_letter(jd_text: str, profile: Profile, skills: Skills, goals: Goals, model: str | None = None) -> str`, returns `""` on failure
- Consumes: `FILLERS` from `careeros.cli.apply_cmd` (existing) — this task imports `FILLERS` directly from `careeros.cli.apply_cmd` rather than redefining it, so both commands share the exact same filler list. `_RESUME_EXTENSIONS` is NOT imported — matching this codebase's existing convention where each CLI file defines its own small private helpers/constants (`_get_storage`, `_now()` are already duplicated per-file across `browse_cmd.py`/`apply_cmd.py`, not shared) — this task defines its own `_RESUME_EXTENSIONS = (".pdf", ".docx")`
- Consumes: `Job`, `Profile`, `Skills`, `Goals` from `careeros.core.models` (existing)
- Consumes: `make_job_id` from `careeros.core.job_id` (existing)
- Produces: `discover_and_apply_app` — a Typer app with one command, wired into `careeros/cli/main.py`

**Note on an improvement over `browse_cmd`'s current behavior:** `browse_cmd` does not currently store the fetched job-description text on the `Job` object it saves (`Job(...)` in `browse_cmd.py` never sets `description`), so `apply_cmd`'s `jd_text = (job.description or "")[:4000]` is always empty for browse-discovered jobs. This is out of scope to fix in `browse_cmd` itself (not part of this plan), but `discover_and_apply_cmd` — since it already fetches `jd_text` during discovery for scoring — sets `description=jd_text` when constructing its own `Job` objects. This ensures automation's own auto-applied cover letters are generated from the real job description rather than an empty string, which matters more here than in the interactive flow since there is no human reviewing the letter before submission.

- [ ] **Step 1: Write the failing tests**

**Why real storage, not a fully-mocked one:** this command reloads a `Job` from storage (`Job.load`) after having just saved it, in the same run, and also loads `AutomationPolicy` from the same storage object. A single `MagicMock` storage with one blanket `read.return_value` cannot serve both call sites correctly — it would return the policy JSON when `Job.load` asks for job JSON, causing a Pydantic validation error instead of the intended behavior. Use a real `LocalFilesystemStorage` backed by `tmp_path` (same pattern as `tests/test_browse_cmd.py`'s `_setup_workspace`), and mock only the genuinely external/expensive/interactive pieces: `SCRAPERS`, `launch_browser`, `fetch_jd_text`, `score_job`, `generate_cover_letter`, `FILLERS`.

Create `tests/test_discover_and_apply_cmd.py`:

```python
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.discover_and_apply_cmd import discover_and_apply_app
from careeros.core.models import AutomationPolicy, Job, Profile, Skill, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _setup_workspace(tmp_path, policy: AutomationPolicy | None = None, with_resume: bool = True):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    Profile(name="Alice", title="Senior SRE").save(storage)
    Skills(skills=[Skill(name="Kubernetes")]).save(storage)
    if policy is not None:
        policy.save(storage)
    if with_resume:
        storage.atomic_write("resumes/versions/resume.pdf", b"%PDF-1.4 fake resume")
    return str(tmp_path)


def _mock_launch(mock_page=None):
    if mock_page is None:
        mock_page = MagicMock()

    @contextmanager
    def _ctx(headless=False) -> Iterator:
        yield MagicMock(), mock_page

    return _ctx


def _posting(company="Acme", title="Senior SRE", url="https://boards.greenhouse.io/acme/jobs/1"):
    return {"source_board": "linkedin", "title": title, "company": company, "location": "SF", "url": url}


class TestDiscoverAndApplyCmd:
    def test_missing_policy_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, policy=None)
        result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1
        assert "automation_policy" in result.output.lower()

    def test_job_below_threshold_saved_not_applied(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 50, "reasoning": "meh"}), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "saved"

    def test_job_at_threshold_auto_applied(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_called_once()
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 1
        assert jobs[0].stage == "applied"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "automation" in log_content

    def test_max_auto_applies_per_run_stops_further_applies(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=1, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        postings = [
            _posting(company="Acme", title="Role One", url="https://boards.greenhouse.io/acme/jobs/1"),
            _posting(company="Beta", title="Role Two", url="https://boards.greenhouse.io/beta/jobs/2"),
        ]

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=postings))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_filler.fill.call_count == 1
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert len(jobs) == 2
        stages = sorted(j.stage for j in jobs)
        assert stages == ["applied", "saved"]

    def test_cover_letter_failure_skips_job_without_crashing(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_page = MagicMock()

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter", return_value=""), \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        mock_filler.fill.assert_not_called()
        assert "skipped" in result.output.lower()
        storage = LocalFilesystemStorage(ws_path)
        jobs = Job.list_all(storage)
        assert jobs[0].stage == "saved"

    def test_launch_browser_always_headless(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser") as mock_browser:
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), MagicMock()))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        mock_browser.assert_called_with(headless=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discover_and_apply_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.cli.discover_and_apply_cmd'`

- [ ] **Step 3: Implement discover_and_apply_cmd.py**

Create `careeros/cli/discover_and_apply_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console

from careeros.browser.driver import fetch_jd_text, launch_browser
from careeros.browser.scrapers.generic import GenericScraper
from careeros.browser.scrapers.indeed import IndeedScraper
from careeros.browser.scrapers.linkedin import LinkedInScraper
from careeros.browser.scrapers.wellfound import WellfoundScraper
from careeros.cli.apply_cmd import FILLERS
from careeros.config import GlobalConfig
from careeros.core.job_id import make_job_id
from careeros.core.models import AutomationPolicy, Goals, Job, Profile, Skills
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_automation_runtime
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.cover_letter import generate_cover_letter
from careeros.skills.job_score import score_job
from careeros.storage.filesystem import LocalFilesystemStorage

discover_and_apply_app = typer.Typer(help="Unattended discover + auto-apply for scheduled runs.")
console = Console()

_RESUME_EXTENSIONS = (".pdf", ".docx")

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


@discover_and_apply_app.command()
def discover_and_apply_cmd(
    board: str = typer.Option(None, "--board", help="Single board to run (overrides policy's board list)"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_automation_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        policy = AutomationPolicy.load(runtime.storage)
    except FileNotFoundError:
        rprint("[red]config/automation_policy.json not found — create one before running discover-and-apply.[/red]")
        raise typer.Exit(1)

    try:
        profile = Profile.load(runtime.storage)
    except FileNotFoundError:
        rprint("[red]No profile found. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)
    boards = [board] if board else policy.boards
    query = job_query_from_profile(profile, goals)

    discovered: list[dict] = []
    now = _now()
    for b in boards:
        scraper = SCRAPERS.get(b)
        if scraper is None:
            rprint("[yellow]Unknown board '" + b + "', skipping.[/yellow]")
            continue
        try:
            with launch_browser(headless=True) as (_, page):
                try:
                    postings = scraper.search(page, query, 20)
                except Exception as exc:
                    rprint("[yellow]Warning: could not search " + b + ": " + str(exc) + "[/yellow]")
                    postings = []

                for posting in postings:
                    jd_text = fetch_jd_text(page, posting["url"])
                    result = score_job(jd_text, profile, skills)
                    discovered.append({**posting, "score": result["score"], "jd_text": jd_text})
        except ImportError:
            rprint("[red]Playwright is not installed.[/red]")
            raise typer.Exit(1)

    saved_count = 0
    for p in discovered:
        job_id = make_job_id(p["company"], p["title"])
        job = Job(
            id=job_id,
            source=p["source_board"],
            url=p["url"],
            company=p["company"],
            title=p["title"],
            location=p.get("location"),
            description=p["jd_text"],
            stage="saved",
            created_at=now,
            updated_at=now,
        )
        job.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "job_added", "discover-and-apply",
            "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=job_id,
        ))
        saved_count += 1
        p["job_id"] = job_id

    eligible = sorted(
        [p for p in discovered if p["score"] >= policy.auto_apply_min_score],
        key=lambda p: p["score"], reverse=True,
    )

    resume_entries = sorted([
        p for p in runtime.storage.list("resumes/versions/")
        if p.endswith(_RESUME_EXTENSIONS)
    ])
    resume_path = runtime.storage.resolve(resume_entries[-1]) if resume_entries else None

    applied_count = 0
    skipped_count = 0
    for p in eligible:
        if applied_count >= policy.max_auto_applies_per_run:
            break
        if resume_path is None:
            rprint("[yellow]No resume found — skipping auto-apply for " + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue

        job_id = p["job_id"]
        cover_letter = generate_cover_letter(p["jd_text"], profile, skills, goals)
        if not cover_letter:
            runtime.record_activity(runtime.new_event(
                "cover_letter_failed", "discover-and-apply",
                "Cover letter generation failed for " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        filler = next((f for f in FILLERS if f.can_handle(p["url"])), None)
        if filler is None:
            skipped_count += 1
            continue

        cl_storage_path = "applications/" + job_id + "/cover_letter.txt"
        runtime.storage.atomic_write(cl_storage_path, cover_letter.encode())
        cover_letter_path = runtime.storage.resolve(cl_storage_path)

        approval = runtime.request_approval(ActionProposal(
            action="apply_to_job",
            summary="Auto-apply (score " + str(p["score"]) + " >= threshold "
            + str(policy.auto_apply_min_score) + ") to " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=job_id,
        ))
        if not approval.approved:
            skipped_count += 1
            continue

        job = Job.load(runtime.storage, job_id)
        try:
            with launch_browser(headless=True) as (_, page):
                success = filler.fill(page, job, profile, cover_letter, cover_letter_path, resume_path)
        except Exception:
            skipped_count += 1
            continue

        if success:
            applied_now = _now()
            job = job.model_copy(update={"stage": "applied", "applied_at": applied_now, "updated_at": applied_now})
            job.save(runtime.storage)
            runtime.record_activity(runtime.new_event(
                "job_applied", "discover-and-apply",
                "Auto-applied (score " + str(p["score"]) + " >= threshold "
                + str(policy.auto_apply_min_score) + ") to " + p["company"] + " — " + p["title"],
                entity_type="job", entity_id=job_id,
            ))
            applied_count += 1
        else:
            skipped_count += 1

    rprint(
        "Discovered: " + str(saved_count) + ", Auto-applied: " + str(applied_count)
        + ", Skipped: " + str(skipped_count)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discover_and_apply_cmd.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Wire the command into main.py**

Modify `careeros/cli/main.py` — add this import alongside the existing ones:

```python
from careeros.cli.discover_and_apply_cmd import discover_and_apply_app
```

Then add this line alongside the existing `app.add_typer(...)` calls:

```python
app.add_typer(discover_and_apply_app, name="discover-and-apply")
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/discover_and_apply_cmd.py careeros/cli/main.py tests/test_discover_and_apply_cmd.py
git commit -m "feat: add discover-and-apply command for unattended scheduled runs"
```
