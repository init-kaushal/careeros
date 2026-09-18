# Phase 5 — Agent Interoperability — Design Spec

**Date:** 2026-09-18
**Status:** Approved for implementation planning
**Author:** Kaushal + Claude

---

## 1. What This Is

The master design spec (`docs/superpowers/specs/2026-09-18-careeros-design.md`, §5.4) defines an `AgentRuntime` Protocol as the seam between CareerOS and any LLM framework, with `LocalRuntime` slated for Phase 1 and Claude/Hermes/ChatGPT adapters for Phase 5. In practice, Phases 1-4 were built without this seam: `onboard_cmd`, `browse_cmd`, and `apply_cmd` all call `StorageProvider` and Rich prompts directly. `PolicyEngine`, `ApprovalEngine`, `ActionProposal`, and `ApprovalResult` were never built either.

This spec retrofits the `AgentRuntime` seam onto the existing CLI and adds a second, real adapter (`ClaudeCodeRuntime`) so the same workspace can be opened correctly by two different runtimes — proving the abstraction rather than just declaring it.

**Deliberately out of scope for this phase** (deferred, not forgotten):
- `PolicyEngine` (deterministic policy checks) — `request_approval` is a direct yes/no gate this phase, no policy layer in front of it
- ChatGPT Work / Hermes / Generic adapters — only `LocalRuntime` and `ClaudeCodeRuntime` ship
- Async/deferred approval (`approvals/<id>.json` pending queue) — approval stays synchronous
- The master spec's Phase 3 ("People + Outreach") — never built; not a dependency of this phase

---

## 2. Architecture

New package: `careeros/runtime/`

```text
careeros/runtime/
├── __init__.py
├── base.py           ← AgentRuntime Protocol, ActionProposal, ApprovalResult
├── local.py           ← LocalRuntime (CLI: Rich Confirm/Prompt)
├── claude_code.py      ← ClaudeCodeRuntime (injected approval callback)
└── factory.py          ← open_local_runtime, open_claude_code_runtime
```

Every CLI command (`onboard`, `browse`, `apply`) is refactored to obtain an `AgentRuntime` via the factory and route all storage I/O and approval gates through it — `StorageProvider` and `Confirm.ask`/`Prompt.ask` are no longer called directly from command bodies (Prompt.ask for non-approval input, like the cover-letter review loop's A/R/Q choice, stays direct — only the yes/no *approval* gates move to `request_approval`).

---

## 3. Protocol + Types

```python
# careeros/runtime/base.py
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
```

Deviations from the master spec's literal Protocol (both intentional, both scoped to what Phase 4 code actually needs):
- **`storage: StorageProvider` property added.** `read_workspace`/`write_workspace` are text-only (`str`), matching the master spec exactly. But Phase 4's `apply_cmd` needs `storage.resolve(path) -> str` (absolute paths for Playwright file uploads) and binary writes (`atomic_write` with `bytes` for cover letters). Rather than widen the Protocol's text methods to `str | bytes`, each runtime exposes its underlying `StorageProvider` directly for these cases.
- **No `PolicyEngine` reference.** Per the "lightweight stub" scope decision — `request_approval` is a direct gate, not policy-checked.

---

## 4. LocalRuntime

```python
# careeros/runtime/local.py
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
```

`record_activity` overwrites `event.agent_runtime`/`event.session_id` unconditionally — callers build events via `ActivityLogger.new_event(...)`-style construction without needing to know which runtime they're in; the runtime stamps identity at the point of logging. This is what makes the cross-runtime exit-condition test meaningful (see §7).

---

## 5. ClaudeCodeRuntime

```python
# careeros/runtime/claude_code.py
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
```

`approval_callback` is a required constructor argument with no default. A caller embedding CareerOS as a library (an agent session with its own conversational channel to the user) supplies a callback that resolves synchronously in-process — e.g. by presenting the proposal to the user and returning their decision. Construction fails loudly (`TypeError`, missing arg) if omitted, rather than silently auto-denying every approval-gated action.

---

## 6. Discovery + Factory

```python
# careeros/runtime/factory.py
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

No new discovery logic — `open_workspace()` (manifest read, `check_schema_compatibility`, pending migrations) already implements workspace discovery. Phase 5 exposes it per-runtime rather than only from CLI command bodies. `FileNotFoundError` (no manifest) and schema-mismatch errors from `open_workspace` propagate unchanged — both factory functions are thin wrappers, no new error handling.

---

## 7. CLI Retrofit

Every command in `careeros/cli/onboard.py`, `careeros/cli/browse_cmd.py`, `careeros/cli/apply_cmd.py` changes from direct storage/prompt calls to routing through a `LocalRuntime`:

```python
# apply_cmd.py — before
storage = _get_storage(workspace)
ctx = open_workspace(storage)
confirmed = Confirm.ask("About to fill the " + filler.platform + " application...", default=False)

# apply_cmd.py — after
runtime = open_local_runtime(_get_storage(workspace))
result = runtime.request_approval(ActionProposal(
    action="apply_to_job",
    summary="About to fill the " + filler.platform + " application for " + job.company + " — " + job.title + ". Proceed?",
    entity_type="job", entity_id=job_id,
))
if not result.approved:
    rprint("Aborted.")
    raise typer.Exit(0)
```

Every existing storage call site in the three commands is already bytes-based: model methods (`Job.save(storage)`, `Profile.load(storage)`, etc.) take a `StorageProvider` and encode internally, and the remaining ad-hoc writes (`storage.atomic_write("resumes/master.md", resume_text.encode())`, cover letter `.txt`) already call `.encode()` before `atomic_write`. None of these become `write_workspace`/`read_workspace` calls — they retrofit onto `runtime.storage` unchanged, since `runtime.storage` *is* the same `StorageProvider` instance, just reached via the runtime rather than a bare local variable. `read_workspace`/`write_workspace` (`str`-only, per the master spec) have no call site in this retrofit — they exist on the Protocol for future text-only skills and are exercised directly by the cross-runtime interop test (§8), not by any CLI command this phase. `ActivityLogger` calls become `runtime.record_activity(logger.new_event(...))` — event construction is unchanged, only dispatch moves.

`onboard_cmd`'s wizard uses `request_approval` nowhere new (it has no destructive/external actions) — it only moves its storage writes onto `runtime.write_workspace`/`runtime.storage`.

`browse_cmd`'s job-save loop has no approval gate today (saving to the workspace isn't external) — it stays a plain `runtime.write_workspace`/`runtime.storage` call, no new `ActionProposal`.

---

## 8. Testing

- `tests/test_runtime.py` — unit tests for `LocalRuntime` (wraps `Confirm.ask` correctly, stamps `agent_runtime`/`session_id` on logged events) and `ClaudeCodeRuntime` (invokes the injected callback, raises `TypeError` if callback omitted at construction)
- `tests/test_runtime_factory.py` — `open_local_runtime`/`open_claude_code_runtime` correctly bootstrap via `open_workspace` (missing manifest → `FileNotFoundError`; schema mismatch → existing error unchanged)
- **Cross-runtime exit-condition test** (`tests/test_runtime_interop.py`): a single `tmp_path` workspace, opened first by `LocalRuntime` (write one job via `write_workspace`, log one event), then independently opened by `ClaudeCodeRuntime` with a stub callback (write a second job, log a second event) — assert both jobs are readable from either runtime instance, and the activity log for that day contains two events with `agent_runtime` values `"local"` and `"claude_code"` respectively
- Existing `test_apply_cmd.py`, `test_browse_cmd.py`, `test_onboard.py` updated: mocks move from `careeros.cli.apply_cmd.Confirm.ask` etc. to `careeros.cli.apply_cmd.open_local_runtime` returning a `MagicMock(spec=LocalRuntime)` with `request_approval` configured per test case

---

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/`
- Activity logs are append-only; no event is ever edited or deleted
- `atomic_write` must use write-to-temp-then-rename (never write directly to final path)
- Prompts (`ActionProposal.summary`, activity `summary`) are built via string concatenation only — no `.format()` or f-strings with user data
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id` with the runtime's own identity — callers never set these fields expecting them to survive
- `ClaudeCodeRuntime` construction without an `approval_callback` must fail at call time (missing required arg), never silently default to auto-deny or auto-approve

---

## Exit Condition

Per the master spec: the same workspace, opened in two different agent runtimes, both read/write correctly, and the activity log shows both sessions. Concretely: the cross-runtime interop test in §8 passing is the exit condition for this phase — no manual verification step beyond that, since there is no second real external agent (ChatGPT Work, Hermes) integrated yet.
