# Phase 5 — Agent Interoperability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retrofit the `AgentRuntime` seam onto CareerOS's existing CLI (`onboard`, `browse`, `apply`) and add a second real adapter (`ClaudeCodeRuntime`) so the same workspace can be opened correctly by two different runtimes.

**Architecture:** A new `careeros/runtime/` package defines the `AgentRuntime` Protocol plus two implementations — `LocalRuntime` (wraps `StorageProvider` + Rich `Confirm.ask`, used by the CLI) and `ClaudeCodeRuntime` (wraps `StorageProvider` + an injected synchronous approval callback). Every CLI command is refactored to obtain a runtime via a factory (`open_local_runtime`) instead of calling `StorageProvider`/`Confirm.ask` directly, and a cross-runtime test proves the exit condition: one workspace, two runtimes, both read/write correctly, activity log shows both sessions.

**Tech Stack:** Python 3.11+, `typing.Protocol`, dataclasses, Rich, existing `StorageProvider`/`ActivityLogger`/`open_workspace` infrastructure (unchanged).

**Spec:** `docs/superpowers/specs/2026-09-18-phase5-agent-interoperability-design.md`

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/`
- Activity logs are append-only; no event is ever edited or deleted
- `atomic_write` must use write-to-temp-then-rename (never write directly to final path)
- Prompts (`ActionProposal.summary`, activity `summary`) are built via string concatenation only — no `.format()` or f-strings with user data
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id` with the runtime's own identity — callers never set these fields expecting them to survive
- `ClaudeCodeRuntime` construction without an `approval_callback` must fail at call time (missing required arg), never silently default to auto-deny or auto-approve

**Note beyond the spec text:** Task 1 adds a `new_event(event_type, action, summary, status="success", **kwargs) -> ActivityEvent` method to the `AgentRuntime` Protocol, not present in the committed spec. Justification: `record_activity(event)` takes a fully-formed `ActivityEvent`, and `ActivityEvent` is a dataclass with no defaults for `agent_runtime`/`session_id` — every call site would otherwise need dummy placeholder values that immediately get overwritten. `ActivityLogger.new_event` already exists as exactly this convenience; `AgentRuntime.new_event` delegates to it. Same category of pragmatic addition as the spec's own `storage` property deviation (§3).

---

### Task 1: AgentRuntime Protocol + types + LocalRuntime

**Files:**
- Create: `careeros/runtime/__init__.py`
- Create: `careeros/runtime/base.py`
- Create: `careeros/runtime/local.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Produces: `ActionProposal(action: str, summary: str, entity_type: str | None = None, entity_id: str | None = None)` — dataclass
- Produces: `ApprovalResult(approved: bool, reason: str | None = None)` — dataclass
- Produces: `AgentRuntime` Protocol with `storage: StorageProvider`, `read_workspace(path: str) -> str`, `write_workspace(path: str, content: str) -> None`, `request_approval(proposal: ActionProposal) -> ApprovalResult`, `record_activity(event: ActivityEvent) -> None`, `new_event(event_type: str, action: str, summary: str, status: str = "success", **kwargs) -> ActivityEvent`
- Produces: `LocalRuntime(storage: StorageProvider, ctx: WorkspaceContext, session_id: str)` — concrete class implementing the above
- Consumes: `ActivityEvent`, `ActivityLogger` from `careeros.core.activity` (existing, unchanged)
- Consumes: `WorkspaceContext` from `careeros.workspace.manager` (existing, unchanged)
- Consumes: `StorageProvider` from `careeros.storage.interface` (existing, unchanged)

- [ ] **Step 1: Create the package and Protocol module**

Create `careeros/runtime/__init__.py` (empty file).

Create `careeros/runtime/base.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from careeros.core.activity import ActivityEvent
from careeros.storage.interface import StorageProvider


@dataclass
class ActionProposal:
    action: str
    summary: str
    entity_type: str | None = None
    entity_id: str | None = None


@dataclass
class ApprovalResult:
    approved: bool
    reason: str | None = None


class AgentRuntime(Protocol):
    storage: StorageProvider

    def read_workspace(self, path: str) -> str: ...
    def write_workspace(self, path: str, content: str) -> None: ...
    def request_approval(self, proposal: ActionProposal) -> ApprovalResult: ...
    def record_activity(self, event: ActivityEvent) -> None: ...
    def new_event(
        self, event_type: str, action: str, summary: str, status: str = "success", **kwargs
    ) -> ActivityEvent: ...
```

- [ ] **Step 2: Write the failing tests for LocalRuntime**

Create `tests/test_runtime.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from careeros.core.activity import ActivityEvent
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.runtime.local import LocalRuntime


def _make_event(agent_runtime="unset", session_id="unset"):
    return ActivityEvent(
        event_type="job_added", action="browse", status="success",
        summary="Job saved", agent_runtime=agent_runtime, session_id=session_id,
    )


class TestLocalRuntime:
    def test_read_workspace_decodes_bytes(self):
        storage = MagicMock()
        storage.read.return_value = b"hello"
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        assert runtime.read_workspace("profile/profile.json") == "hello"
        storage.read.assert_called_once_with("profile/profile.json")

    def test_write_workspace_encodes_and_atomic_writes(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        runtime.write_workspace("notes/note.txt", "hello world")
        storage.atomic_write.assert_called_once_with("notes/note.txt", b"hello world")

    def test_request_approval_calls_confirm_ask_with_summary(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="apply_to_job", summary="Apply to Acme?")
        with patch("careeros.runtime.local.Confirm.ask", return_value=True) as mock_ask:
            result = runtime.request_approval(proposal)
        mock_ask.assert_called_once_with("Apply to Acme?", default=False)
        assert result == ApprovalResult(approved=True)

    def test_request_approval_returns_approved_false_when_declined(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="apply_to_job", summary="Apply to Acme?")
        with patch("careeros.runtime.local.Confirm.ask", return_value=False):
            result = runtime.request_approval(proposal)
        assert result.approved is False

    def test_record_activity_stamps_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        event = _make_event(agent_runtime="unset", session_id="unset")
        with patch("careeros.runtime.local.ActivityLogger") as MockLogger:
            runtime = LocalRuntime(storage, ctx, session_id="sess-42")
            runtime.record_activity(event)
        MockLogger.return_value.log.assert_called_once_with(event)
        assert event.agent_runtime == "local"
        assert event.session_id == "sess-42"

    def test_new_event_sets_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-7")
        event = runtime.new_event("job_added", "browse", "Job saved", entity_type="job", entity_id="j1")
        assert event.event_type == "job_added"
        assert event.agent_runtime == "local"
        assert event.session_id == "sess-7"
        assert event.entity_type == "job"
        assert event.entity_id == "j1"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.runtime.local'`

- [ ] **Step 4: Implement LocalRuntime**

Create `careeros/runtime/local.py`:

```python
from __future__ import annotations
from rich.prompt import Confirm
from careeros.core.activity import ActivityEvent, ActivityLogger
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import WorkspaceContext


class LocalRuntime:
    agent_runtime_name = "local"

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
        approved = Confirm.ask(proposal.summary, default=False)
        return ApprovalResult(approved=approved)

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

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add careeros/runtime/__init__.py careeros/runtime/base.py careeros/runtime/local.py tests/test_runtime.py
git commit -m "feat: add AgentRuntime Protocol and LocalRuntime implementation"
```

---

### Task 2: ClaudeCodeRuntime

**Files:**
- Create: `careeros/runtime/claude_code.py`
- Test: `tests/test_runtime.py` (modify — append a new test class)

**Interfaces:**
- Consumes: `ActionProposal`, `ApprovalResult`, `AgentRuntime` from `careeros.runtime.base` (Task 1)
- Consumes: `ActivityEvent`, `ActivityLogger` from `careeros.core.activity` (existing)
- Produces: `ApprovalCallback = Callable[[ActionProposal], ApprovalResult]` type alias
- Produces: `ClaudeCodeRuntime(storage: StorageProvider, ctx: WorkspaceContext, session_id: str, approval_callback: ApprovalCallback)` — concrete class implementing `AgentRuntime`, `agent_runtime_name = "claude_code"`

- [ ] **Step 1: Write the failing tests for ClaudeCodeRuntime**

Append to `tests/test_runtime.py` (add `import pytest` at the top if not already present, and these imports alongside the existing ones):

```python
from careeros.runtime.claude_code import ClaudeCodeRuntime
```

Append this class to the end of `tests/test_runtime.py`:

```python
class TestClaudeCodeRuntime:
    def test_request_approval_invokes_callback_with_proposal(self):
        storage = MagicMock()
        ctx = MagicMock()
        proposal = ActionProposal(action="apply_to_job", summary="Apply to Acme?")
        callback = MagicMock(return_value=ApprovalResult(approved=True, reason="looks good"))
        runtime = ClaudeCodeRuntime(storage, ctx, session_id="sess-1", approval_callback=callback)
        result = runtime.request_approval(proposal)
        callback.assert_called_once_with(proposal)
        assert result == ApprovalResult(approved=True, reason="looks good")

    def test_missing_callback_raises_type_error(self):
        storage = MagicMock()
        ctx = MagicMock()
        with pytest.raises(TypeError):
            ClaudeCodeRuntime(storage, ctx, session_id="sess-1")

    def test_record_activity_stamps_claude_code_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        callback = MagicMock(return_value=ApprovalResult(approved=True))
        event = _make_event(agent_runtime="unset", session_id="unset")
        with patch("careeros.runtime.claude_code.ActivityLogger") as MockLogger:
            runtime = ClaudeCodeRuntime(storage, ctx, session_id="sess-99", approval_callback=callback)
            runtime.record_activity(event)
        MockLogger.return_value.log.assert_called_once_with(event)
        assert event.agent_runtime == "claude_code"
        assert event.session_id == "sess-99"

    def test_read_write_workspace_matches_local_behavior(self):
        storage = MagicMock()
        storage.read.return_value = b"content"
        ctx = MagicMock()
        callback = MagicMock()
        runtime = ClaudeCodeRuntime(storage, ctx, session_id="sess-1", approval_callback=callback)
        assert runtime.read_workspace("a.txt") == "content"
        runtime.write_workspace("b.txt", "hi")
        storage.atomic_write.assert_called_once_with("b.txt", b"hi")

    def test_new_event_sets_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        callback = MagicMock()
        runtime = ClaudeCodeRuntime(storage, ctx, session_id="sess-7", approval_callback=callback)
        event = runtime.new_event("job_added", "browse", "Job saved")
        assert event.agent_runtime == "claude_code"
        assert event.session_id == "sess-7"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.runtime.claude_code'`

- [ ] **Step 3: Implement ClaudeCodeRuntime**

Create `careeros/runtime/claude_code.py`:

```python
from __future__ import annotations
from typing import Callable
from careeros.core.activity import ActivityEvent, ActivityLogger
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import WorkspaceContext

ApprovalCallback = Callable[[ActionProposal], ApprovalResult]


class ClaudeCodeRuntime:
    agent_runtime_name = "claude_code"

    def __init__(
        self,
        storage: StorageProvider,
        ctx: WorkspaceContext,
        session_id: str,
        approval_callback: ApprovalCallback,
    ) -> None:
        self.storage = storage
        self.ctx = ctx
        self.session_id = session_id
        self._approval_callback = approval_callback
        self._logger = ActivityLogger(storage, session_id=session_id)

    def read_workspace(self, path: str) -> str:
        return self.storage.read(path).decode()

    def write_workspace(self, path: str, content: str) -> None:
        self.storage.atomic_write(path, content.encode())

    def request_approval(self, proposal: ActionProposal) -> ApprovalResult:
        return self._approval_callback(proposal)

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (11 tests total)

- [ ] **Step 5: Commit**

```bash
git add careeros/runtime/claude_code.py tests/test_runtime.py
git commit -m "feat: add ClaudeCodeRuntime with injectable approval callback"
```

---

### Task 3: Discovery + Factory

**Files:**
- Create: `careeros/runtime/factory.py`
- Test: `tests/test_runtime_factory.py`

**Interfaces:**
- Consumes: `LocalRuntime` from `careeros.runtime.local` (Task 1)
- Consumes: `ClaudeCodeRuntime`, `ApprovalCallback` from `careeros.runtime.claude_code` (Task 2)
- Consumes: `open_workspace` from `careeros.workspace.manager` (existing, unchanged — raises `FileNotFoundError` if no manifest)
- Produces: `open_local_runtime(storage: StorageProvider, session_id: str | None = None) -> LocalRuntime`
- Produces: `open_claude_code_runtime(storage: StorageProvider, approval_callback: ApprovalCallback, session_id: str | None = None) -> ClaudeCodeRuntime`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runtime_factory.py`:

```python
import pytest
from unittest.mock import MagicMock
from careeros.runtime.factory import open_claude_code_runtime, open_local_runtime
from careeros.runtime.local import LocalRuntime
from careeros.runtime.claude_code import ClaudeCodeRuntime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_open_local_runtime_bootstraps_existing_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_local_runtime(storage)
    assert isinstance(runtime, LocalRuntime)
    assert runtime.session_id


def test_open_local_runtime_uses_provided_session_id(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_local_runtime(storage, session_id="fixed-id")
    assert runtime.session_id == "fixed-id"


def test_open_local_runtime_missing_manifest_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        open_local_runtime(storage)


def test_open_claude_code_runtime_bootstraps_existing_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    callback = MagicMock()
    runtime = open_claude_code_runtime(storage, approval_callback=callback)
    assert isinstance(runtime, ClaudeCodeRuntime)
    assert runtime.session_id


def test_open_claude_code_runtime_missing_manifest_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    callback = MagicMock()
    with pytest.raises(FileNotFoundError):
        open_claude_code_runtime(storage, approval_callback=callback)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.runtime.factory'`

- [ ] **Step 3: Implement the factory**

Create `careeros/runtime/factory.py`:

```python
from __future__ import annotations
import uuid
from careeros.runtime.claude_code import ApprovalCallback, ClaudeCodeRuntime
from careeros.runtime.local import LocalRuntime
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import open_workspace


def open_local_runtime(storage: StorageProvider, session_id: str | None = None) -> LocalRuntime:
    ctx = open_workspace(storage)
    return LocalRuntime(storage, ctx, session_id or uuid.uuid4().hex)


def open_claude_code_runtime(
    storage: StorageProvider,
    approval_callback: ApprovalCallback,
    session_id: str | None = None,
) -> ClaudeCodeRuntime:
    ctx = open_workspace(storage)
    return ClaudeCodeRuntime(storage, ctx, session_id or uuid.uuid4().hex, approval_callback)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime_factory.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add careeros/runtime/factory.py tests/test_runtime_factory.py
git commit -m "feat: add runtime factory wrapping workspace discovery"
```

---

### Task 4: CLI Retrofit — onboard + browse

**Files:**
- Modify: `careeros/cli/onboard.py` (full rewrite)
- Modify: `careeros/cli/browse_cmd.py` (full rewrite)
- Test: `tests/test_onboard.py` (verify unchanged, no edits expected)
- Test: `tests/test_browse_cmd.py` (verify unchanged, no edits expected)

**Interfaces:**
- Consumes: `LocalRuntime` from `careeros.runtime.local` (Task 1) — `onboard_cmd` constructs it directly (workspace doesn't exist yet, so `open_local_runtime`'s `open_workspace` call would fail)
- Consumes: `open_local_runtime` from `careeros.runtime.factory` (Task 3) — `browse_cmd` uses this (workspace already exists)

Both `test_onboard.py` and `test_browse_cmd.py` are real-filesystem, real-`init_workspace`/`open_workspace` integration-style tests (no mocking of `Confirm`/`Prompt`/`ActivityLogger` internals) — they assert on final workspace state (files written, activity log entries, exit codes), not on which functions were called. The retrofit preserves all observable behavior, so these tests should pass unmodified. This task's test step is running them, not rewriting them.

- [ ] **Step 1: Retrofit onboard.py**

Replace the full contents of `careeros/cli/onboard.py`:

```python
from pathlib import Path
import typer
from rich import print as rprint
from rich.prompt import Confirm, Prompt

from careeros.config import GlobalConfig
from careeros.core.models import Goals, Preferences, Profile, Skills
from careeros.runtime.local import LocalRuntime
from careeros.skills.profile_extract import extract_basic_profile
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace
import json
import uuid


def onboard_cmd(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Path for new workspace"),
) -> None:
    rprint("[bold]Welcome to CareerOS[/bold]")
    rprint("Let's set up your workspace.\n")

    # Step 1: workspace path
    ws_path = workspace or Prompt.ask(
        "Where should I store your workspace?",
        default=str(Path.home() / "career"),
    )
    ws_path = str(Path(ws_path).expanduser())

    storage = LocalFilesystemStorage(ws_path)
    try:
        ctx = init_workspace(storage)
    except FileExistsError:
        rprint(f"[red]Workspace already exists at {ws_path}.[/red]")
        rprint("Run [bold]careeros workspace status[/bold] to inspect it.")
        raise typer.Exit(1)
    runtime = LocalRuntime(storage, ctx, session_id=uuid.uuid4().hex)
    runtime.record_activity(runtime.new_event("workspace_created", "init", "Workspace initialized at " + ws_path))
    rprint(f"\n[green]Workspace created at {ws_path}[/green]")

    # Step 2: resume
    resume_path_str = Prompt.ask("\nPath to your resume (Markdown or plain text)")
    resume_file = Path(resume_path_str).expanduser()
    if not resume_file.exists():
        rprint(f"[red]File not found: {resume_file}[/red]")
        raise typer.Exit(1)

    resume_text = resume_file.read_text()
    runtime.storage.atomic_write("resumes/master.md", resume_text.encode())
    runtime.record_activity(runtime.new_event(
        "resume_imported", "import", "Resume imported from " + resume_path_str, entity_type="resume"
    ))

    # Step 3: profile extraction
    rprint("\nExtracting profile from resume...")
    try:
        profile, skills = extract_basic_profile(resume_text)
    except Exception as e:
        rprint(f"[yellow]Extraction failed ({e}). Starting with empty profile.[/yellow]")
        profile, skills = Profile(), Skills()

    rprint("\n[bold]Extracted profile:[/bold]")
    rprint(f"  Name:       {profile.name or '(not found)'}")
    rprint(f"  Title:      {profile.title or '(not found)'}")
    rprint(f"  Experience: {profile.years_of_experience or '?'} years")
    rprint(f"  Skills:     {len(skills.skills)} found")

    if not Confirm.ask("\nDoes this look right?", default=True):
        rprint("[yellow]Edit profile/profile.json in your workspace to correct it.[/yellow]")

    profile.save(runtime.storage)
    skills.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "profile_extracted", "extract", "Profile extracted: " + (profile.name or ""), entity_type="profile"
    ))

    # Step 4: preferences
    rprint("\n[bold]Job Preferences[/bold]")
    roles_raw = Prompt.ask("Target roles (comma-separated, or press enter to skip)", default="")
    target_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    remote_pref = Prompt.ask(
        "Remote preference", choices=["remote", "hybrid", "onsite", "any"], default="any"
    )

    comp_raw = Prompt.ask("Minimum annual compensation in USD (or press enter to skip)", default="")
    min_comp = int(comp_raw) if comp_raw.strip().isdigit() else None

    locs_raw = Prompt.ask("Preferred locations (comma-separated, or press enter to skip)", default="")
    locations = [l.strip() for l in locs_raw.split(",") if l.strip()]

    prefs = Preferences(
        target_roles=target_roles,
        remote_preference=remote_pref,
        minimum_compensation=min_comp,
        locations=locations,
    )
    prefs.save(runtime.storage)

    # Step 5: job sources
    rprint("\n[bold]Job Sources[/bold]")
    rprint("Which sources may CareerOS search? Available: greenhouse, linkedin, lever, naukri")
    sources_raw = Prompt.ask("Sources (comma-separated)", default="greenhouse")
    sources = [
        {"source": s.strip(), "mode": "SEARCH_ONLY"}
        for s in sources_raw.split(",")
        if s.strip()
    ]
    runtime.storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())

    # Step 6: goals (optional)
    goals = Goals()
    if Confirm.ask("\nWould you like to set career goals now?", default=False):
        st_raw = Prompt.ask("Short-term goals (comma-separated)", default="")
        lt_raw = Prompt.ask("Long-term goals (comma-separated)", default="")
        goals = Goals(
            short_term=[g.strip() for g in st_raw.split(",") if g.strip()],
            long_term=[g.strip() for g in lt_raw.split(",") if g.strip()],
        )
    goals.save(runtime.storage)

    # Save global config
    GlobalConfig(workspace_path=ws_path).save()
    runtime.record_activity(runtime.new_event("onboard_complete", "onboard", "Onboarding complete"))

    rprint(f"\n[bold green]CareerOS ready.[/bold green]")
    rprint(f"Workspace: {ws_path}")
    rprint("Run [bold]careeros workspace status[/bold] to see your profile summary.")
```

- [ ] **Step 2: Run onboard tests to verify they still pass**

Run: `pytest tests/test_onboard.py -v`
Expected: PASS (5 tests, unmodified)

- [ ] **Step 3: Retrofit browse_cmd.py**

Replace the full contents of `careeros/cli/browse_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from careeros.browser.driver import fetch_jd_text, launch_browser
from careeros.browser.scrapers.generic import GenericScraper
from careeros.browser.scrapers.indeed import IndeedScraper
from careeros.browser.scrapers.linkedin import LinkedInScraper
from careeros.browser.scrapers.wellfound import WellfoundScraper
from careeros.config import GlobalConfig
from careeros.core.job_id import make_job_id
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.runtime.factory import open_local_runtime
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.job_score import score_job
from careeros.storage.filesystem import LocalFilesystemStorage

browse_app = typer.Typer(name="browse", help="Search job boards using your browser session.")
console = Console()

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


@browse_app.command()
def browse_cmd(
    board: str = typer.Option(..., "--board", help="linkedin | indeed | wellfound | url"),
    url: str = typer.Option(None, "--url", help="Target URL (required when --board url)"),
    limit: int = typer.Option(20, "--limit", help="Max listings to fetch"),
    min_score: int = typer.Option(0, "--min-score", help="Minimum score to display"),
    headless: bool = typer.Option(False, "--headless/--no-headless", help="Run browser headlessly"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    valid_boards = {"linkedin", "indeed", "wellfound", "url"}
    if board not in valid_boards:
        rprint(f"[red]Invalid --board '{board}'. Valid: {' '.join(sorted(valid_boards))}[/red]")
        raise typer.Exit(1)

    if board == "url" and not url:
        rprint("[red]Provide --url when using --board url.[/red]")
        raise typer.Exit(1)

    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        profile = Profile.load(runtime.storage)
    except FileNotFoundError:
        rprint("[red]No profile found. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)
    query = job_query_from_profile(profile, goals) if board != "url" else (url or "")
    scraper = GenericScraper() if board == "url" else SCRAPERS[board]

    try:
        with launch_browser(headless=headless) as (_, page):
            try:
                postings = scraper.search(page, query, limit)
            except Exception as exc:
                rprint(f"[yellow]Warning: could not search {board}: {exc}[/yellow]")
                postings = []

            rprint(f"Scoring {len(postings)} listings...")
            scored = []
            for posting in postings:
                jd_text = fetch_jd_text(page, posting["url"])
                result = score_job(jd_text, profile, skills)
                scored.append({**posting, "score": result["score"], "reasoning": result["reasoning"]})
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        rprint("Run: [bold]pip install playwright && playwright install chrome[/bold]")
        raise typer.Exit(1)

    filtered = [p for p in scored if p["score"] >= min_score]
    filtered.sort(key=lambda p: p["score"], reverse=True)

    if not filtered:
        rprint(f"No jobs found matching min-score {min_score}.")
        return

    table = Table(show_header=True)
    table.add_column("#", style="bold")
    table.add_column("Score")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("URL")
    for i, p in enumerate(filtered, 1):
        table.add_row(str(i), str(p["score"]), p["company"], p["title"], p.get("location") or "—", p["url"])
    console.print(table)

    picks_str = Prompt.ask("Pick jobs to save (e.g. 1 3 5, or q to quit)")
    if picks_str.strip().lower() == "q":
        return

    saved = 0
    seen_indices: set[int] = set()
    now = _now()
    for part in picks_str.split():
        if not part.isdigit():
            continue
        idx = int(part) - 1
        if not (0 <= idx < len(filtered)) or idx in seen_indices:
            continue
        seen_indices.add(idx)
        p = filtered[idx]
        job_id = make_job_id(p["company"], p["title"])
        job = Job(
            id=job_id,
            source=p["source_board"],
            url=p["url"],
            company=p["company"],
            title=p["title"],
            location=p.get("location"),
            stage="saved",
            created_at=now,
            updated_at=now,
        )
        job.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "job_added", "browse",
            "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=job_id,
        ))
        saved += 1

    rprint(f"[green]Saved {saved} job(s)[/green]")
```

- [ ] **Step 4: Run browse tests to verify they still pass**

Run: `pytest tests/test_browse_cmd.py -v`
Expected: PASS (all existing tests, unmodified)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/onboard.py careeros/cli/browse_cmd.py
git commit -m "refactor: retrofit onboard and browse commands onto AgentRuntime"
```

---

### Task 5: CLI Retrofit — apply

**Files:**
- Modify: `careeros/cli/apply_cmd.py` (full rewrite)
- Modify: `tests/test_apply_cmd.py` (full rewrite)

**Interfaces:**
- Consumes: `open_local_runtime` from `careeros.runtime.factory` (Task 3)
- Consumes: `ActionProposal` from `careeros.runtime.base` (Task 1)
- The final `Confirm.ask` approval gate becomes `runtime.request_approval(ActionProposal(...))`; the cover-letter review loop's A/R/Q choice stays a direct `Prompt.ask` call (not an approval gate — informational choice, not external-action consent)

- [ ] **Step 1: Retrofit apply_cmd.py**

Replace the full contents of `careeros/cli/apply_cmd.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.browser.driver import launch_browser
from careeros.browser.fillers.generic import GenericFiller
from careeros.browser.fillers.greenhouse import GreenhouseFiller
from careeros.browser.fillers.lever import LeverFiller
from careeros.browser.fillers.linkedin import LinkedInFiller
from careeros.config import GlobalConfig
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_local_runtime
from careeros.skills.cover_letter import generate_cover_letter
from careeros.storage.filesystem import LocalFilesystemStorage

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
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    # Load job
    try:
        job = Job.load(runtime.storage, job_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job_id + " not found.[/red]")
        raise typer.Exit(1)

    if not job.url:
        rprint("[red]Job has no URL — add one with `careeros job update`.[/red]")
        raise typer.Exit(1)

    # Find resume (before profile load so failure is fast and clear)
    resume_entries = sorted([
        p for p in runtime.storage.list("resumes/versions/")
        if p.endswith(_RESUME_EXTENSIONS)
    ])
    if not resume_entries:
        rprint("[red]No resume found in resumes/versions/ — add one first.[/red]")
        raise typer.Exit(1)

    resume_file = resume_entries[-1]
    resume_path = runtime.storage.resolve(resume_file)

    # Load profile data (after resume check so early failure avoids unnecessary I/O)
    profile = Profile.load_or_empty(runtime.storage)
    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    # Use stored JD text (two-session approach: avoid keeping browser open during interactive review)
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
    try:
        runtime.storage.atomic_write(cl_storage_path, cover_letter.encode())
        cover_letter_path = runtime.storage.resolve(cl_storage_path)
    except ValueError:
        rprint("[red]Invalid job ID.[/red]")
        raise typer.Exit(1)

    # Detect filler
    filler = next((f for f in FILLERS if f.can_handle(job.url)), None)
    if filler is None:
        rprint("[red]No filler available for this URL.[/red]")
        raise typer.Exit(1)

    # Final approval
    result = runtime.request_approval(ActionProposal(
        action="apply_to_job",
        summary="About to fill the " + filler.platform + " application for "
        + job.company + " — " + job.title + ". Proceed?",
        entity_type="job", entity_id=job_id,
    ))
    if not result.approved:
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
        job.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "job_applied", "apply",
            "Applied to " + job.company + " — " + job.title,
            entity_type="job", entity_id=job_id,
        ))
        rprint("[green]Applied to " + job.company + " — " + job.title + ". Stage updated to 'applied'.[/green]")
    else:
        rprint("[yellow]Form fill incomplete — review the browser window. Stage not updated.[/yellow]")
        raise typer.Exit(1)
```

- [ ] **Step 2: Rewrite tests/test_apply_cmd.py**

Replace the full contents of `tests/test_apply_cmd.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from careeros.cli.apply_cmd import apply_app
from careeros.config import GlobalConfig
from careeros.core.models import Goals, Job, Profile, Skills
from careeros.runtime.base import ApprovalResult

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


def _mock_runtime(tmp_path, resume_filename="resume.pdf", approved=True):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    storage.exists.return_value = True
    runtime = MagicMock()
    runtime.storage = storage
    runtime.request_approval.return_value = ApprovalResult(approved=approved)
    return runtime


class TestApplyCmdHappyPath:
    def test_successful_apply_updates_stage_to_applied(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        profile = _make_profile()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"

        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=profile), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Hiring Manager,\n\nGreat fit."), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(apply_app, ["acme-sre-abc1"])

        assert result.exit_code == 0
        runtime.storage.atomic_write.assert_called()
        save_calls = [str(c) for c in runtime.storage.atomic_write.call_args_list]
        assert any("cover_letter" in c for c in save_calls)
        job_saves = [c for c in runtime.storage.atomic_write.call_args_list if c.args[0] == "jobs/acme-sre-abc1.json"]
        assert job_saves, "job.save() was not called"
        assert b'"applied"' in job_saves[0].args[1]

    def test_successful_apply_logs_job_applied_event(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"

        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter text"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])

        runtime.record_activity.assert_called_once()
        event_arg = runtime.new_event.call_args
        assert event_arg[0][0] == "job_applied"


class TestApplyCmdFailurePaths:
    def test_no_workspace_exits_1(self):
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig(workspace_path=None)):
            result = runner.invoke(apply_app, ["some-job-id"])
        assert result.exit_code == 1

    def test_job_not_found_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", side_effect=FileNotFoundError):
            result = runner.invoke(apply_app, ["nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_job_no_url_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        job_no_url = Job(id="x", source="manual", url=None, company="Co", title="Role",
                         stage="saved", created_at=now, updated_at=now)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job_no_url):
            result = runner.invoke(apply_app, ["x"])
        assert result.exit_code == 1
        assert "url" in result.output.lower()

    def test_no_resume_exits_1(self, tmp_path):
        runtime = MagicMock()
        storage = MagicMock()
        storage.list.return_value = []
        runtime.storage = storage
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "resume" in result.output.lower()

    def test_cover_letter_generation_failure_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value=""):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "generation failed" in result.output.lower()

    def test_user_quits_review_loop_exits_0(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_user_declines_final_approval_exits_0(self, tmp_path):
        runtime = _mock_runtime(tmp_path, approved=False)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_filler_returns_false_exits_1_stage_not_updated(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        mock_page = MagicMock()
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = False
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "incomplete" in result.output.lower()
        assert not any(c.args[0] == "jobs/acme-sre-abc1.json" for c in runtime.storage.atomic_write.call_args_list)

    def test_playwright_not_installed_exits_1(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.platform = "Greenhouse"
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter"), \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser", side_effect=ImportError), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "playwright" in result.output.lower()

    def test_regenerate_calls_generate_again(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["r", "a"]):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])
        assert mock_gen.call_count == 2

    def test_model_flag_propagated_to_generate(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True
        mock_filler.fill.return_value = True
        mock_filler.platform = "Greenhouse"
        mock_page = MagicMock()
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Cover letter") as mock_gen, \
             patch("careeros.cli.apply_cmd.FILLERS", [mock_filler]), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="a"):
            mock_browser.return_value.__enter__ = MagicMock(return_value=(MagicMock(), mock_page))
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1", "--model", "gpt-4o"])
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("model") == "gpt-4o"
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_apply_cmd.py -v`
Expected: PASS (13 tests)

- [ ] **Step 4: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 5: Commit**

```bash
git add careeros/cli/apply_cmd.py tests/test_apply_cmd.py
git commit -m "refactor: retrofit apply command onto AgentRuntime with request_approval"
```

---

### Task 6: Cross-Runtime Interop Test (Exit Condition)

**Files:**
- Test: `tests/test_runtime_interop.py`

**Interfaces:**
- Consumes: `open_local_runtime`, `open_claude_code_runtime` from `careeros.runtime.factory` (Task 3)
- Consumes: `ApprovalResult` from `careeros.runtime.base` (Task 1)
- No production code produced — this task is the exit-condition proof

- [ ] **Step 1: Write the interop tests**

Create `tests/test_runtime_interop.py`:

```python
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from careeros.core.models import Job
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_claude_code_runtime, open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _make_job(job_id):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id=job_id, source="manual", url="https://example.com/job",
        company="Acme", title="Engineer", stage="saved",
        created_at=now, updated_at=now,
    )


def test_same_workspace_readable_writable_across_runtimes(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)

    local_runtime = open_local_runtime(storage, session_id="local-session")
    job_a = _make_job("acme-eng-aaa1")
    job_a.save(local_runtime.storage)
    local_runtime.record_activity(local_runtime.new_event(
        "job_added", "browse", "Job saved via local runtime",
        entity_type="job", entity_id="acme-eng-aaa1",
    ))

    callback = MagicMock(return_value=ApprovalResult(approved=True))
    claude_runtime = open_claude_code_runtime(storage, approval_callback=callback, session_id="claude-session")
    job_b = _make_job("acme-eng-bbb2")
    job_b.save(claude_runtime.storage)
    claude_runtime.record_activity(claude_runtime.new_event(
        "job_added", "browse", "Job saved via claude_code runtime",
        entity_type="job", entity_id="acme-eng-bbb2",
    ))

    # Both jobs visible from either runtime instance
    assert Job.load(local_runtime.storage, "acme-eng-aaa1").company == "Acme"
    assert Job.load(local_runtime.storage, "acme-eng-bbb2").company == "Acme"
    assert Job.load(claude_runtime.storage, "acme-eng-aaa1").company == "Acme"
    assert Job.load(claude_runtime.storage, "acme-eng-bbb2").company == "Acme"

    # Activity log for today has both agent_runtime values
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_lines = storage.read("activity/" + date + ".jsonl").decode().strip().split("\n")
    assert len(log_lines) == 2
    agent_runtimes = set()
    session_ids = set()
    for line in log_lines:
        event = json.loads(line)
        agent_runtimes.add(event["agent_runtime"])
        session_ids.add(event["session_id"])
    assert agent_runtimes == {"local", "claude_code"}
    assert session_ids == {"local-session", "claude-session"}


def test_read_write_workspace_text_roundtrip_across_runtimes(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)

    local_runtime = open_local_runtime(storage, session_id="local-session")
    local_runtime.write_workspace("notes/scratch.txt", "hello from local")

    callback = MagicMock(return_value=ApprovalResult(approved=True))
    claude_runtime = open_claude_code_runtime(storage, approval_callback=callback, session_id="claude-session")
    assert claude_runtime.read_workspace("notes/scratch.txt") == "hello from local"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_runtime_interop.py -v`
Expected: PASS (2 tests) — this is the exit condition for Phase 5

- [ ] **Step 3: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 4: Commit**

```bash
git add tests/test_runtime_interop.py
git commit -m "test: add cross-runtime interop test proving Phase 5 exit condition"
```
