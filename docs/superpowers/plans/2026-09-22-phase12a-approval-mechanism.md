# Phase 12a — Approval Mechanism + Operations Layer (Outreach) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persisted-approval mechanism and a runtime-agnostic operations layer, proved end to end on outreach send, so an agent session outside the CLI can complete an approval-gated action across a process boundary.

**Architecture:** Each approval-gated flow splits into `propose_*` (load, policy-check, draft, persist, record a pending `Approval`) and `execute_*` (perform only the external action), with `resolve_approval` recording the decision in between. The split lets a short-lived subprocess propose, exit, and have a later process execute — and guarantees the artifact that was approved is the artifact that is transmitted, via approval superseding plus sha256 digest binding.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer + Rich (CLI only), pytest, litellm (existing), `StorageProvider` filesystem abstraction.

**Spec:** `docs/superpowers/specs/2026-09-22-phase12-second-runtime-design.md`

## Global Constraints

Copied from the spec's Global Constraints section. Every task's requirements implicitly include these.

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, `careeros/runtime/`, or the new `careeros/operations/`. (`os.environ` reads in `careeros/runtime/factory.py` are workspace *discovery*, not workspace I/O, and are permitted.)
- Activity logs are append-only; no event is ever edited or deleted.
- `atomic_write` must use write-to-temp-then-rename.
- Prompts and activity summaries are built by string concatenation only — **no `.format()` and no f-strings with user data**. This codebase uses `+` concatenation throughout; match it.
- No `rich`, `typer`, or `click` import anywhere under `careeros/operations/`, and no printing, prompting, or process exit from it.
- `execute_*` performs the external action and nothing else. It never drafts, never calls an LLM, never re-derives an artifact.
- An `Approval` is single-use. Only `approved` may execute, and executing moves it to a terminal state.
- Run the full suite with `.venv/bin/python -m pytest -q` before each commit; it must stay green.

## File Structure

**Create:**
- `careeros/workspace/migrations/m003_approvals.py` — creates `approvals/.keep`
- `careeros/operations/__init__.py` — empty, package marker
- `careeros/operations/errors.py` — the `OperationError` hierarchy; the only vocabulary a caller needs to branch on
- `careeros/operations/approvals.py` — action-agnostic `Approval` lifecycle over storage
- `careeros/operations/approval_queue.py` — `queue_only`, the shipped deny-by-default callback
- `careeros/operations/outreach.py` — `propose_` / `execute_` / `decline_outreach_send`
- `docs/agent-integration.md` — the integration contract
- `tests/test_approvals.py`, `tests/test_operations_outreach.py`, `tests/test_operations_purity.py`, `tests/test_agent_integration.py`

**Modify:**
- `careeros/core/models.py` — add `Approval` after `OutreachMessage`
- `careeros/core/ids.py` — add `make_approval_id`
- `careeros/workspace/migrations/__init__.py` — register `m003_approvals`
- `careeros/runtime/base.py` — declare `agent_runtime_name: str` on the Protocol
- `careeros/runtime/factory.py` — add `WorkspaceNotConfigured`, `resolve_storage`, `open_agent_runtime`
- `careeros/cli/outreach_cmd.py` — rewire onto the operations layer
- `tests/test_outreach_cmd.py` — move patch targets to the operations module
- `tests/test_migrations.py` — cover `003_approvals`
- `README.md`, `ROADMAP.md`, `docs/superpowers/DIVERGENCES.md`

**Out of scope for 12a** (belongs to 12b): `careeros/operations/apply.py`, the `apply_cmd` rewire, the `discover-and-apply` de-duplication, and the `BoardSessionRequired` / `FillIncomplete` / `BrowserUnavailable` error types. Do not add them; 12b's plan introduces them where they are first used.

---

### Task 1: The `Approval` record, its ID, and the `approvals/` directory

The model cannot save until the directory exists, so all three land together.

**Files:**
- Modify: `careeros/core/models.py` (insert after `OutreachMessage`, which ends at line 324)
- Modify: `careeros/core/ids.py`
- Create: `careeros/workspace/migrations/m003_approvals.py`
- Modify: `careeros/workspace/migrations/__init__.py`
- Test: `tests/test_approvals.py` (created here, extended in Task 2), `tests/test_ids.py`, `tests/test_migrations.py`

**Interfaces:**
- Consumes: `StorageProvider`, `careeros.core.ids._slugify` (already present), the `@register` decorator from `careeros/workspace/migrations/__init__.py`
- Produces: `Approval` (Pydantic model with `save(storage)` / `load(storage, approval_id)`), `make_approval_id(action: str, entity_id: str | None) -> str`, migration name `"003_approvals"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_approvals.py`:

```python
import pytest

from careeros.core.models import Approval
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _storage(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    return storage


class TestApprovalModel:
    def test_save_then_load_roundtrips_every_field(self, tmp_path):
        storage = _storage(tmp_path)
        approval = Approval(
            id="send-outreach-abc123", action="send_outreach",
            summary="Send outreach email to Jane Doe?", state="pending",
            entity_type="outreach_message", entity_id="job__person",
            payload={"message_id": "job__person"},
            created_at="2026-09-22T00:00:00+00:00",
        )
        approval.save(storage)
        loaded = Approval.load(storage, "send-outreach-abc123")
        assert loaded == approval

    def test_defaults_are_pending_with_empty_payload(self):
        approval = Approval(
            id="a-1", action="send_outreach", summary="s",
            created_at="2026-09-22T00:00:00+00:00",
        )
        assert approval.state == "pending"
        assert approval.payload == {}
        assert approval.decided_at is None
        assert approval.decided_by is None
        assert approval.reason is None
        assert approval.executed_at is None
        assert approval.detail is None

    def test_load_missing_raises_file_not_found(self, tmp_path):
        storage = _storage(tmp_path)
        with pytest.raises(FileNotFoundError):
            Approval.load(storage, "nope")

    def test_saves_under_approvals_directory(self, tmp_path):
        storage = _storage(tmp_path)
        Approval(id="a-1", action="send_outreach", summary="s",
                 created_at="2026-09-22T00:00:00+00:00").save(storage)
        assert storage.exists("approvals/a-1.json")
```

Append to `tests/test_ids.py`:

```python
def test_make_approval_id_slugifies_and_suffixes():
    from careeros.core.ids import make_approval_id
    approval_id = make_approval_id("send_outreach", "acme-sre-abc1__acme-corp-jane-doe")
    assert approval_id.startswith("send-outreach-acme-sre-abc1")
    assert len(approval_id.split("-")[-1]) == 6


def test_make_approval_id_is_unique_per_call():
    from careeros.core.ids import make_approval_id
    first = make_approval_id("send_outreach", "job-1")
    second = make_approval_id("send_outreach", "job-1")
    assert first != second


def test_make_approval_id_handles_none_entity():
    from careeros.core.ids import make_approval_id
    approval_id = make_approval_id("send_outreach", None)
    assert approval_id.startswith("send-outreach-")
    assert "--" not in approval_id


def test_make_approval_id_strips_path_traversal():
    from careeros.core.ids import make_approval_id
    approval_id = make_approval_id("send_outreach", "../../etc/passwd")
    assert "/" not in approval_id
    assert ".." not in approval_id
```

Append to `tests/test_migrations.py`:

```python
def test_init_workspace_creates_approvals_directory(tmp_path):
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    assert storage.exists("approvals/.keep")
    assert "003_approvals" in ctx.manifest.migrations_applied


def test_existing_workspace_acquires_approvals_on_open(tmp_path):
    import json
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace, open_workspace
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    # Simulate a workspace created before this migration existed.
    storage.delete("approvals/.keep")
    manifest = json.loads(storage.read("manifest.json").decode())
    manifest["migrations_applied"] = [
        m for m in manifest["migrations_applied"] if m != "003_approvals"
    ]
    storage.atomic_write("manifest.json", json.dumps(manifest).encode())

    ctx = open_workspace(storage)
    assert storage.exists("approvals/.keep")
    assert "003_approvals" in ctx.manifest.migrations_applied
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_approvals.py tests/test_ids.py tests/test_migrations.py -q`
Expected: FAIL — `ImportError: cannot import name 'Approval'`, `cannot import name 'make_approval_id'`, and the two migration tests failing on the missing `approvals/.keep`.

- [ ] **Step 3: Add `make_approval_id`**

Append to `careeros/core/ids.py`:

```python
def make_approval_id(action: str, entity_id: str | None) -> str:
    # Both components become a path segment under approvals/, so they are
    # slugified for the same reason outreach message IDs are: an arbitrary
    # entity_id must never reach storage._resolve() as a raw path component.
    # The random suffix keeps two proposals for the same entity distinct, so
    # the older one stays readable in its superseded state for the audit trail.
    parts = [p for p in [_slugify(action)[:20], _slugify(entity_id or "")[:30]] if p]
    return "-".join(parts) + "-" + secrets.token_hex(3)
```

- [ ] **Step 4: Add the `Approval` model**

Insert into `careeros/core/models.py` immediately after the `OutreachMessage` class (after line 324, before `class CompensationDataPoint`). `Field` and `BaseModel` are already imported at line 1.

```python
class Approval(BaseModel):
    """A durable record of one approval-gated action, readable across processes.

    States: pending -> approved -> executed, with declined, superseded, and
    failed as the other terminal outcomes. Only `approved` may execute, and
    executing advances the record, so one approval can never authorize two
    external actions.

    `payload` is an open string map on purpose: it holds only the identifiers
    and workspace-relative paths execute_* needs to find its inputs, which
    keeps this file a contract a third runtime can read without importing
    CareerOS. It never holds drafted content (there would be two sources of
    truth) and never holds absolute paths (the workspace is portable).
    """

    id: str
    action: str
    summary: str
    state: str = "pending"
    entity_type: str | None = None
    entity_id: str | None = None
    payload: dict[str, str] = Field(default_factory=dict)
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str | None = None
    executed_at: str | None = None
    detail: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write(
            "approvals/" + self.id + ".json", self.model_dump_json(indent=2).encode()
        )

    @classmethod
    def load(cls, storage: StorageProvider, approval_id: str) -> "Approval":
        path = "approvals/" + approval_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Approval " + repr(approval_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())
```

- [ ] **Step 5: Add the migration and register it**

Create `careeros/workspace/migrations/m003_approvals.py`:

```python
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("003_approvals")
def m003_approvals(storage: StorageProvider) -> None:
    if not storage.exists("approvals/.keep"):
        storage.write("approvals/.keep", b"")
```

Append to `careeros/workspace/migrations/__init__.py`, below the existing `m002_applications` import (keep the `# noqa` comments — the imports exist to trigger `@register`):

```python
from careeros.workspace.migrations import m003_approvals  # noqa: F401, E402
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_approvals.py tests/test_ids.py tests/test_migrations.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. The new migration runs on every `init_workspace`, so a regression here would surface across many suites.

- [ ] **Step 8: Commit**

```bash
git add careeros/core/models.py careeros/core/ids.py \
        careeros/workspace/migrations/m003_approvals.py \
        careeros/workspace/migrations/__init__.py \
        tests/test_approvals.py tests/test_ids.py tests/test_migrations.py
git commit -m "feat: add the Approval record and its approvals/ directory"
```

---

### Task 2: The approval lifecycle

**Files:**
- Create: `careeros/operations/__init__.py`, `careeros/operations/errors.py`, `careeros/operations/approvals.py`
- Modify: `careeros/runtime/base.py`
- Test: `tests/test_approvals.py` (extend)

**Interfaces:**
- Consumes: `Approval`, `make_approval_id` (Task 1); `ApprovalResult` from `careeros/runtime/base.py`; `runtime.record_activity` / `runtime.new_event` / `runtime.storage` / `runtime.agent_runtime_name`
- Produces:
  - `careeros/operations/errors.py`: `OperationError`, `EntityNotFound`, `PolicyBlocked(rule)`, `DraftFailed`, `MissingRecipient(person_id, person_name)`, `ApprovalNotGranted(approval_id, state)`, `MalformedApproval(approval_id, key)`, `ArtifactChanged(path)`, `SendFailed`
  - `careeros/operations/approvals.py`: state constants `PENDING`/`APPROVED`/`DECLINED`/`EXECUTED`/`FAILED`/`SUPERSEDED`; `open_approval(runtime, action, summary, payload, *, entity_type=None, entity_id=None, action_label) -> Approval`; `resolve_approval(runtime, approval_id, result, *, action_label) -> Approval`; `mark_executed(runtime, approval_id) -> Approval`; `mark_failed(runtime, approval_id, detail) -> Approval`; `require_state(storage, approval_id, expected) -> Approval`; `payload_value(approval, key) -> str`; `list_pending(storage) -> list[Approval]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_approvals.py`:

```python
from unittest.mock import MagicMock

from careeros.operations.approvals import (
    APPROVED, DECLINED, EXECUTED, FAILED, PENDING, SUPERSEDED,
    list_pending, mark_executed, mark_failed, open_approval, payload_value,
    require_state, resolve_approval,
)
from careeros.operations.errors import ApprovalNotGranted, MalformedApproval
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_local_runtime


def _runtime(tmp_path, session_id="sess-1"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    return open_local_runtime(storage, session_id=session_id)


def _open(runtime, entity_id="job__person", summary="Send outreach email to Jane Doe?"):
    return open_approval(
        runtime, "send_outreach", summary,
        {"message_id": entity_id},
        entity_type="outreach_message", entity_id=entity_id,
        action_label="outreach",
    )


def _log(storage):
    from datetime import datetime, timezone
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


class TestOpenApproval:
    def test_writes_a_pending_record_and_logs_requested(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        assert approval.state == PENDING
        assert approval.payload == {"message_id": "job__person"}
        assert Approval.load(runtime.storage, approval.id).state == PENDING
        assert "approval_requested" in _log(runtime.storage)

    def test_supersedes_a_prior_pending_for_the_same_entity(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _open(runtime)
        second = _open(runtime)
        assert first.id != second.id
        assert Approval.load(runtime.storage, first.id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.id).state == PENDING
        assert "approval_superseded" in _log(runtime.storage)

    def test_does_not_supersede_a_different_entity(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _open(runtime, entity_id="job-a__person")
        _open(runtime, entity_id="job-b__person")
        assert Approval.load(runtime.storage, first.id).state == PENDING

    def test_does_not_supersede_an_already_decided_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _open(runtime)
        resolve_approval(runtime, first.id, ApprovalResult(approved=True), action_label="outreach")
        _open(runtime)
        assert Approval.load(runtime.storage, first.id).state == APPROVED


class TestResolveApproval:
    def test_approve_records_state_decider_and_reason(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolved = resolve_approval(
            runtime, approval.id,
            ApprovalResult(approved=True, reason="user said yes in chat"),
            action_label="outreach",
        )
        assert resolved.state == APPROVED
        assert resolved.decided_by == "local"
        assert resolved.reason == "user said yes in chat"
        assert resolved.decided_at is not None

    def test_decline_records_declined(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolved = resolve_approval(
            runtime, approval.id, ApprovalResult(approved=False), action_label="outreach"
        )
        assert resolved.state == DECLINED
        assert "approval_declined" in _log(runtime.storage)

    def test_populates_the_activity_event_reason_field(self, tmp_path):
        import json
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(
            runtime, approval.id,
            ApprovalResult(approved=True, reason="user said yes in chat"),
            action_label="outreach",
        )
        events = [json.loads(line) for line in _log(runtime.storage).strip().split("\n")]
        granted = [e for e in events if e["event_type"] == "approval_granted"]
        assert len(granted) == 1
        assert granted[0]["reason"] == "user said yes in chat"
        assert granted[0]["action"] == "outreach"
        assert granted[0]["agent_runtime"] == "local"

    def test_refuses_to_resolve_twice(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        with pytest.raises(ApprovalNotGranted) as exc:
            resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        assert exc.value.state == APPROVED


class TestRequireState:
    def test_returns_the_approval_when_the_state_matches(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        assert require_state(runtime.storage, approval.id, APPROVED).id == approval.id

    @pytest.mark.parametrize("state", [PENDING, DECLINED, EXECUTED, FAILED, SUPERSEDED])
    def test_raises_with_the_state_it_found(self, tmp_path, state):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        approval.model_copy(update={"state": state}).save(runtime.storage)
        with pytest.raises(ApprovalNotGranted) as exc:
            require_state(runtime.storage, approval.id, APPROVED)
        assert exc.value.state == state


class TestMarkExecutedAndFailed:
    def test_mark_executed_sets_state_and_timestamp(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        marked = mark_executed(runtime, approval.id)
        assert marked.state == EXECUTED
        assert marked.executed_at is not None

    def test_mark_failed_records_detail(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        marked = mark_failed(runtime, approval.id, "SMTPAuthenticationError")
        assert marked.state == FAILED
        assert marked.detail == "SMTPAuthenticationError"


class TestPayloadValue:
    def test_returns_the_value(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        assert payload_value(approval, "message_id") == "job__person"

    @pytest.mark.parametrize("payload", [{}, {"message_id": ""}])
    def test_missing_or_empty_raises_malformed(self, payload):
        approval = Approval(id="a-1", action="send_outreach", summary="s",
                            payload=payload, created_at="2026-09-22T00:00:00+00:00")
        with pytest.raises(MalformedApproval) as exc:
            payload_value(approval, "message_id")
        assert exc.value.key == "message_id"


class TestListPending:
    def test_returns_only_pending_and_skips_the_keep_file(self, tmp_path):
        runtime = _runtime(tmp_path)
        pending = _open(runtime, entity_id="job-a__person")
        decided = _open(runtime, entity_id="job-b__person")
        resolve_approval(runtime, decided.id, ApprovalResult(approved=True), action_label="outreach")
        ids = [a.id for a in list_pending(runtime.storage)]
        assert ids == [pending.id]

    def test_ignores_an_unparseable_record(self, tmp_path):
        runtime = _runtime(tmp_path)
        pending = _open(runtime)
        runtime.storage.atomic_write("approvals/garbage.json", b"{not json")
        assert [a.id for a in list_pending(runtime.storage)] == [pending.id]

    def test_empty_when_nothing_pending(self, tmp_path):
        runtime = _runtime(tmp_path)
        assert list_pending(runtime.storage) == []


class TestProtocolDeclaresRuntimeName:
    def test_agent_runtime_name_is_part_of_the_protocol(self):
        from careeros.runtime.base import AgentRuntime
        assert "agent_runtime_name" in AgentRuntime.__annotations__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_approvals.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'careeros.operations'`.

- [ ] **Step 3: Declare `agent_runtime_name` on the Protocol**

In `careeros/runtime/base.py`, add the annotation to `AgentRuntime` (all three runtimes already define it as a class attribute, so this is a no-op at runtime — it makes the Protocol match reality, and `resolve_approval` reads it to record who decided):

```python
class AgentRuntime(Protocol):
    agent_runtime_name: str
    storage: StorageProvider

    def read_workspace(self, path: str) -> str: ...
    def write_workspace(self, path: str, content: str) -> None: ...
    def request_approval(self, proposal: ActionProposal) -> ApprovalResult: ...
    def record_activity(self, event: ActivityEvent) -> None: ...
    def new_event(
        self, event_type: str, action: str, summary: str, status: str = "success", **kwargs
    ) -> ActivityEvent: ...
```

- [ ] **Step 4: Create the operations package and errors**

Create `careeros/operations/__init__.py` as an empty file.

Create `careeros/operations/errors.py`:

```python
from __future__ import annotations


class OperationError(Exception):
    """Base for every failure an operation reports to its caller.

    Operations never print and never exit, so this hierarchy is the whole
    vocabulary a caller branches on. Each subclass carries the structured
    detail a caller needs to word its own message, so no caller has to
    string-match.
    """


class EntityNotFound(OperationError):
    pass


class PolicyBlocked(OperationError):
    def __init__(self, rule: str) -> None:
        super().__init__(
            "Blocked by policy (" + rule
            + "). Edit config/policies.json to change this."
        )
        self.rule = rule


class DraftFailed(OperationError):
    pass


class MissingRecipient(OperationError):
    def __init__(self, person_id: str, person_name: str) -> None:
        super().__init__("No email on file for " + person_name)
        self.person_id = person_id
        self.person_name = person_name


class ApprovalNotGranted(OperationError):
    def __init__(self, approval_id: str, state: str) -> None:
        super().__init__(
            "Approval " + approval_id + " is in state " + repr(state)
            + ", which does not permit this step."
        )
        self.approval_id = approval_id
        self.state = state


class MalformedApproval(OperationError):
    def __init__(self, approval_id: str, key: str) -> None:
        super().__init__(
            "Approval " + approval_id + " is missing payload key " + repr(key)
        )
        self.approval_id = approval_id
        self.key = key


class ArtifactChanged(OperationError):
    def __init__(self, path: str) -> None:
        super().__init__(
            "The approved content at " + path + " has changed since it was approved. "
            "Re-propose so the review covers what would actually be sent."
        )
        self.path = path


class SendFailed(OperationError):
    pass
```

- [ ] **Step 5: Implement the lifecycle**

Create `careeros/operations/approvals.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from careeros.core.ids import make_approval_id
from careeros.core.models import Approval
from careeros.operations.errors import ApprovalNotGranted, MalformedApproval
from careeros.runtime.base import AgentRuntime, ApprovalResult
from careeros.storage.interface import StorageProvider

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"
EXECUTED = "executed"
FAILED = "failed"
SUPERSEDED = "superseded"

_PREFIX = "approvals/"
_SUFFIX = ".json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_pending(storage: StorageProvider) -> list[Approval]:
    """Every approval awaiting a decision, oldest first.

    How an out-of-process runtime rediscovers what it left open after losing
    its own context. An unparseable record is skipped rather than raised on:
    one corrupt file must not hide every other pending decision.
    """
    pending: list[Approval] = []
    for path in storage.list(_PREFIX):
        if not path.endswith(_SUFFIX):
            continue
        approval_id = path[len(_PREFIX):-len(_SUFFIX)]
        try:
            approval = Approval.load(storage, approval_id)
        except Exception:
            continue
        if approval.state == PENDING:
            pending.append(approval)
    return sorted(pending, key=lambda a: a.created_at)


def require_state(storage: StorageProvider, approval_id: str, expected: str) -> Approval:
    """Load an approval, or raise if it is not in the state this step needs.

    The single guard behind every execute_* and decline_*. Because executing
    advances the record to a terminal state, this is what makes a second
    execution of the same approval impossible rather than merely unlikely.
    """
    approval = Approval.load(storage, approval_id)
    if approval.state != expected:
        raise ApprovalNotGranted(approval_id, approval.state)
    return approval


def payload_value(approval: Approval, key: str) -> str:
    value = approval.payload.get(key)
    if not value:
        raise MalformedApproval(approval.id, key)
    return value


def open_approval(
    runtime: AgentRuntime,
    action: str,
    summary: str,
    payload: dict[str, str],
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action_label: str,
) -> Approval:
    """Record a pending approval, superseding any prior pending one.

    Superseding is what makes regeneration safe: re-proposing invalidates the
    older pending approval, so a stale approval ID cannot later execute
    against content that has since been overwritten.
    """
    for existing in list_pending(runtime.storage):
        if existing.action != action or existing.entity_id != entity_id:
            continue
        existing.model_copy(update={"state": SUPERSEDED}).save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "approval_superseded", action_label,
            "Superseded earlier pending approval " + existing.id,
            entity_type=existing.entity_type, entity_id=existing.entity_id,
        ))

    approval = Approval(
        id=make_approval_id(action, entity_id),
        action=action,
        summary=summary,
        state=PENDING,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        created_at=_now(),
    )
    approval.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "approval_requested", action_label, summary,
        entity_type=entity_type, entity_id=entity_id,
    ))
    return approval


def resolve_approval(
    runtime: AgentRuntime,
    approval_id: str,
    result: ApprovalResult,
    *,
    action_label: str,
) -> Approval:
    """Record a decision against a pending approval.

    Only a pending approval can be decided, so a decision cannot be revised
    after the fact and cannot be applied twice.
    """
    approval = require_state(runtime.storage, approval_id, PENDING)
    approval = approval.model_copy(update={
        "state": APPROVED if result.approved else DECLINED,
        "decided_at": _now(),
        "decided_by": runtime.agent_runtime_name,
        "reason": result.reason,
    })
    approval.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "approval_granted" if result.approved else "approval_declined",
        action_label,
        ("Approved: " if result.approved else "Declined: ") + approval.summary,
        entity_type=approval.entity_type,
        entity_id=approval.entity_id,
        reason=result.reason,
    ))
    return approval


def mark_executed(runtime: AgentRuntime, approval_id: str) -> Approval:
    """Advance an approval to its terminal executed state.

    Logs nothing: the calling flow records its own domain event, which is the
    one that means something to a reader of the activity log.
    """
    approval = Approval.load(runtime.storage, approval_id)
    approval = approval.model_copy(update={"state": EXECUTED, "executed_at": _now()})
    approval.save(runtime.storage)
    return approval


def mark_failed(runtime: AgentRuntime, approval_id: str, detail: str) -> Approval:
    approval = Approval.load(runtime.storage, approval_id)
    approval = approval.model_copy(update={"state": FAILED, "detail": detail})
    approval.save(runtime.storage)
    return approval
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_approvals.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add careeros/operations/__init__.py careeros/operations/errors.py \
        careeros/operations/approvals.py careeros/runtime/base.py tests/test_approvals.py
git commit -m "feat: add the approval lifecycle and operation error vocabulary"
```

---

### Task 3: The out-of-process callback and the layer-purity guard

Small, but it gates every later task in this layer: the purity test must exist before `outreach.py` is written so the constraint is enforced from the first line rather than audited afterwards.

**Files:**
- Create: `careeros/operations/approval_queue.py`, `tests/test_operations_purity.py`
- Test: `tests/test_operations_purity.py`

**Interfaces:**
- Consumes: `ActionProposal`, `ApprovalResult` from `careeros/runtime/base.py`
- Produces: `queue_only(proposal: ActionProposal) -> ApprovalResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_operations_purity.py`:

```python
import ast
from pathlib import Path

import pytest

OPERATIONS = Path(__file__).resolve().parent.parent / "careeros" / "operations"
FORBIDDEN = {"rich", "typer", "click"}


def _modules():
    return sorted(OPERATIONS.glob("*.py"))


def test_there_is_something_to_check():
    # Guards against this file silently passing because the glob went empty.
    assert len(_modules()) >= 2


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_presentation_library_imports(path):
    """The operations layer exists so a non-CLI caller can use it.

    A rich or typer import here means presentation leaked back in, which
    would make the layer unusable from an agent session — the exact failure
    this phase was built to fix. Asserted, not trusted.
    """
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN), path.name + " imports " + str(imported & FORBIDDEN)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_print_calls(path):
    tree = ast.parse(path.read_text())
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "print" not in called, path.name + " calls print()"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_sys_exit_calls(path):
    source = path.read_text()
    assert "sys.exit" not in source
    assert "SystemExit" not in source
```

Create the failing behavior test for `queue_only` — append to `tests/test_operations_purity.py`:

```python
def test_queue_only_denies_by_default_with_a_reason():
    from careeros.operations.approval_queue import queue_only
    from careeros.runtime.base import ActionProposal

    result = queue_only(ActionProposal(action="send_outreach", summary="Send?"))
    assert result.approved is False
    assert result.reason == "deferred to out-of-process approval"


def test_queue_only_is_accepted_as_a_claude_code_approval_callback(tmp_path):
    from careeros.operations.approval_queue import queue_only
    from careeros.runtime.base import ActionProposal
    from careeros.runtime.factory import open_claude_code_runtime
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace

    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_claude_code_runtime(storage, approval_callback=queue_only)
    result = runtime.request_approval(ActionProposal(action="send_outreach", summary="Send?"))
    assert result.approved is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_operations_purity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'careeros.operations.approval_queue'`.

- [ ] **Step 3: Implement `queue_only`**

Create `careeros/operations/approval_queue.py`:

```python
from __future__ import annotations

from careeros.runtime.base import ActionProposal, ApprovalResult

DEFERRED_REASON = "deferred to out-of-process approval"


def queue_only(proposal: ActionProposal) -> ApprovalResult:
    """The approval callback for a runtime that decides out of process.

    A shell-driven runtime records its own pending Approval via open_approval,
    exits, and takes the human's decision through resolve_approval in a later
    process — so this callback is never on the decision path. It exists so
    that any *other* request_approval call such a runtime encounters gets a
    safe answer rather than an accidental yes, and so that every integrator
    does not have to invent the same stub.

    Deny-by-default is deliberate: the Phase 5 constraint is that a missing
    or unwired approval path must never silently auto-approve.
    """
    return ApprovalResult(approved=False, reason=DEFERRED_REASON)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_operations_purity.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add careeros/operations/approval_queue.py tests/test_operations_purity.py
git commit -m "feat: add queue_only callback and enforce operations layer purity"
```

---

### Task 4: `propose_outreach_send`

**Files:**
- Create: `careeros/operations/outreach.py`
- Test: `tests/test_operations_outreach.py`

**Interfaces:**
- Consumes: Task 2's `open_approval`, state constants, and errors; `generate_outreach_message(person, job, company, profile, goals, model=None) -> str` from `careeros/skills/outreach_draft.py`; `PolicyEngine(PolicyConfig).check_job(job) -> PolicyResult(blocked, rule)`
- Produces: `ACTION = "send_outreach"`; `make_message_id(job_id, person_id) -> str`; `subject_for(job) -> str`; `draft_digest(text) -> str`; `OutreachProposal(approval_id, message_id, summary, draft_text, recipient_name, recipient_email, subject, already_sent_at)`; `propose_outreach_send(runtime, job_id, person_id, *, model=None, action_label="outreach") -> OutreachProposal`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_operations_outreach.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from careeros.core.models import (
    Approval, Company, Job, OutreachMessage, Person, PolicyConfig, Profile,
)
from careeros.operations.approvals import PENDING, SUPERSEDED
from careeros.operations.errors import DraftFailed, EntityNotFound, PolicyBlocked
from careeros.operations.outreach import (
    make_message_id, propose_outreach_send,
)
from careeros.runtime.factory import open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
MESSAGE_ID = JOB_ID + "__" + PERSON_ID
DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp..."


def _runtime(tmp_path, with_email=True, session_id="sess-1"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
           title="Engineering Manager",
           email=("jane@acme.com" if with_email else None),
           researched_at=now).save(storage)
    return open_local_runtime(storage, session_id=session_id)


def _propose(runtime, draft=DRAFT):
    with patch("careeros.operations.outreach.generate_outreach_message", return_value=draft):
        return propose_outreach_send(runtime, JOB_ID, PERSON_ID)


def _log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


class TestProposeOutreachSend:
    def test_returns_a_proposal_with_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        assert proposal.message_id == MESSAGE_ID
        assert proposal.draft_text == DRAFT
        assert proposal.recipient_name == "Jane Doe"
        assert proposal.recipient_email == "jane@acme.com"
        assert proposal.subject == "Regarding Senior SRE at Acme Corp"
        assert proposal.already_sent_at is None
        assert Approval.load(runtime.storage, proposal.approval_id).state == PENDING

    def test_persists_the_draft_before_any_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        message = OutreachMessage.load(runtime.storage, proposal.message_id)
        assert message.draft_text == DRAFT
        assert message.send_state == "drafted"
        assert "outreach_drafted" in _log(runtime.storage)

    def test_records_the_draft_digest_in_the_payload(self, tmp_path):
        import hashlib
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.payload["draft_sha256"] == hashlib.sha256(DRAFT.encode()).hexdigest()
        assert approval.payload["message_id"] == MESSAGE_ID
        assert approval.payload["job_id"] == JOB_ID
        assert approval.payload["person_id"] == PERSON_ID

    def test_a_missing_person_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(EntityNotFound):
            with patch("careeros.operations.outreach.generate_outreach_message", return_value=DRAFT):
                propose_outreach_send(runtime, JOB_ID, "nobody")

    def test_an_empty_draft_raises_draft_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(DraftFailed):
            _propose(runtime, draft="")

    def test_a_blocked_company_logs_then_raises_without_drafting(self, tmp_path):
        runtime = _runtime(tmp_path)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(runtime.storage)
        with patch("careeros.operations.outreach.generate_outreach_message") as mock_gen:
            with pytest.raises(PolicyBlocked) as exc:
                propose_outreach_send(runtime, JOB_ID, PERSON_ID)
        assert exc.value.rule == "blocked_company:Acme Corp"
        mock_gen.assert_not_called()
        assert "policy_blocked" in _log(runtime.storage)
        assert not runtime.storage.exists("outreach/" + MESSAGE_ID + ".json")

    def test_regenerating_supersedes_the_prior_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _propose(runtime)
        second = _propose(runtime, draft="A different draft entirely.")
        assert Approval.load(runtime.storage, first.approval_id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.approval_id).state == PENDING
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == "A different draft entirely."

    def test_carries_referral_state_forward(self, tmp_path):
        runtime = _runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
                        draft_text="old", referral_state="referral_requested",
                        created_at=now).save(runtime.storage)
        _propose(runtime)
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).referral_state == "referral_requested"

    def test_surfaces_a_prior_send_and_preserves_it_across_regeneration(self, tmp_path):
        runtime = _runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
                        draft_text="the one we already sent", send_state="sent",
                        sent_at=now, created_at=now).save(runtime.storage)

        first = _propose(runtime)
        assert first.already_sent_at == now
        assert "send ANOTHER" in first.summary

        # Regenerating must not lose the fact that a send already happened:
        # the first propose rewrote the message to send_state="drafted".
        second = _propose(runtime, draft="second attempt")
        assert second.already_sent_at == now

    def test_summary_is_a_plain_question_when_never_sent(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        assert proposal.summary == (
            "Send outreach email to Jane Doe re: Acme Corp — Senior SRE?"
        )


class TestMakeMessageId:
    def test_matches_the_historical_format(self):
        assert make_message_id(JOB_ID, PERSON_ID) == MESSAGE_ID

    def test_slugifies_unsafe_values_instead_of_raising(self):
        assert "/" not in make_message_id("../../etc/passwd", "..")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_operations_outreach.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'careeros.operations.outreach'`.

- [ ] **Step 3: Implement the module and `propose_outreach_send`**

Create `careeros/operations/outreach.py`:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from careeros.core.models import (
    Company, Goals, Job, OutreachMessage, Person, PolicyConfig, Profile,
)
from careeros.core.policy_engine import PolicyEngine
from careeros.operations.approvals import open_approval
from careeros.operations.errors import DraftFailed, EntityNotFound, PolicyBlocked
from careeros.runtime.base import AgentRuntime
from careeros.skills.outreach_draft import generate_outreach_message

ACTION = "send_outreach"


@dataclass(frozen=True)
class OutreachProposal:
    approval_id: str
    message_id: str
    summary: str
    draft_text: str
    recipient_name: str
    recipient_email: str | None
    subject: str
    already_sent_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def make_message_id(job_id: str, person_id: str) -> str:
    # job/person arrive from CLI arguments or an agent call; slugify before
    # using them as path segments so an arbitrary or malformed value never
    # reaches storage._resolve() as a raw path component — which would
    # otherwise either write outside outreach/ for a value like "../foo", or
    # surface as an unhandled ValueError from the workspace-root check.
    return _slugify(job_id) + "__" + _slugify(person_id)


def subject_for(job: Job) -> str:
    return "Regarding " + job.title + " at " + job.company


def draft_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def propose_outreach_send(
    runtime: AgentRuntime,
    job_id: str,
    person_id: str,
    *,
    model: str | None = None,
    action_label: str = "outreach",
) -> OutreachProposal:
    """Draft an outreach email and record a pending approval for sending it.

    Everything that can fail cheaply fails before the LLM is called. Calling
    this again is how regeneration works: the new call supersedes the prior
    pending approval and overwrites the draft.
    """
    try:
        job = Job.load(runtime.storage, job_id)
        person = Person.load(runtime.storage, person_id)
        company = Company.load(runtime.storage, person.company_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Job, person, or company not found.") from exc

    policy_result = PolicyEngine(PolicyConfig.load(runtime.storage)).check_job(job)
    if policy_result.blocked:
        rule = policy_result.rule or "unknown"
        runtime.record_activity(runtime.new_event(
            "policy_blocked", action_label,
            "Blocked by policy (" + rule + "): " + job.company + " — " + job.title,
            status="failed", entity_type="job", entity_id=job.id,
        ))
        raise PolicyBlocked(rule)

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    draft_text = generate_outreach_message(person, job, company, profile, goals, model=model)
    if not draft_text:
        raise DraftFailed("Outreach message generation failed.")

    message_id = make_message_id(job_id, person_id)
    referral_state = "research"
    already_sent_at: str | None = None
    try:
        existing = OutreachMessage.load(runtime.storage, message_id)
        referral_state = existing.referral_state
        # Keyed off sent_at, not send_state: this propose is about to rewrite
        # the message to "drafted", and send_state would then no longer
        # remember that a real send happened. sent_at is only ever set by an
        # actual send, so it is the durable fact — and it is carried forward
        # below so the warning survives regeneration.
        already_sent_at = existing.sent_at
    except (FileNotFoundError, ValueError):
        pass

    OutreachMessage(
        id=message_id, job_id=job_id, person_id=person_id, draft_text=draft_text,
        send_state="drafted", referral_state=referral_state,
        created_at=_now(), sent_at=already_sent_at,
    ).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_drafted", action_label,
        "Drafted outreach to " + person.name + " re: " + job.company + " — " + job.title,
        entity_type="outreach_message", entity_id=message_id,
    ))

    summary = (
        "Send outreach email to " + person.name + " re: "
        + job.company + " — " + job.title + "?"
    )
    if already_sent_at:
        summary = (
            "Already sent to " + person.name + " on " + already_sent_at
            + " — send ANOTHER outreach email re: " + job.company + " — " + job.title + "?"
        )

    approval = open_approval(
        runtime, ACTION, summary,
        {
            "message_id": message_id,
            "job_id": job_id,
            "person_id": person_id,
            "draft_sha256": draft_digest(draft_text),
        },
        entity_type="outreach_message", entity_id=message_id,
        action_label=action_label,
    )

    return OutreachProposal(
        approval_id=approval.id, message_id=message_id, summary=summary,
        draft_text=draft_text, recipient_name=person.name,
        recipient_email=person.email, subject=subject_for(job),
        already_sent_at=already_sent_at,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_operations_outreach.py tests/test_operations_purity.py -q`
Expected: PASS. The purity test now also covers `outreach.py`.

- [ ] **Step 5: Commit**

```bash
git add careeros/operations/outreach.py tests/test_operations_outreach.py
git commit -m "feat: add propose_outreach_send"
```

---

### Task 5: `execute_outreach_send` and `decline_outreach_send`

**Files:**
- Modify: `careeros/operations/outreach.py`
- Test: `tests/test_operations_outreach.py` (extend)

**Interfaces:**
- Consumes: Task 2's `require_state`, `payload_value`, `mark_executed`, `mark_failed`, `APPROVED`, `DECLINED`, and `ArtifactChanged` / `MissingRecipient` / `SendFailed`; `send_email(to_address, subject, body)` from `careeros/mailer.py`
- Produces: `OutreachResult(message_id, recipient_name, sent_at)`; `execute_outreach_send(runtime, approval_id, *, action_label="outreach") -> OutreachResult`; `decline_outreach_send(runtime, approval_id, *, action_label="outreach") -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_operations_outreach.py`:

```python
from careeros.operations.approvals import APPROVED, EXECUTED, FAILED, resolve_approval
from careeros.operations.errors import (
    ApprovalNotGranted, ArtifactChanged, MissingRecipient, SendFailed,
)
from careeros.operations.outreach import decline_outreach_send, execute_outreach_send
from careeros.runtime.base import ApprovalResult


def _approve(runtime, approval_id, reason="user said yes"):
    return resolve_approval(
        runtime, approval_id, ApprovalResult(approved=True, reason=reason),
        action_label="outreach",
    )


class TestExecuteOutreachSend:
    def test_sends_the_approved_draft_and_marks_everything_sent(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        with patch("careeros.operations.outreach.send_email") as mock_send:
            result = execute_outreach_send(runtime, proposal.approval_id)

        mock_send.assert_called_once_with(
            "jane@acme.com", "Regarding Senior SRE at Acme Corp", DRAFT
        )
        assert result.recipient_name == "Jane Doe"
        assert result.sent_at
        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.send_state == "sent"
        assert message.sent_at == result.sent_at
        assert Approval.load(runtime.storage, proposal.approval_id).state == EXECUTED
        assert "outreach_sent" in _log(runtime.storage)

    @pytest.mark.parametrize("state", ["pending", "declined", "superseded", "failed"])
    def test_refuses_any_state_but_approved(self, tmp_path, state):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"state": state}).save(runtime.storage)
        with patch("careeros.operations.outreach.send_email") as mock_send:
            with pytest.raises(ApprovalNotGranted):
                execute_outreach_send(runtime, proposal.approval_id)
        mock_send.assert_not_called()

    def test_refuses_to_execute_the_same_approval_twice(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        with patch("careeros.operations.outreach.send_email") as mock_send:
            execute_outreach_send(runtime, proposal.approval_id)
            with pytest.raises(ApprovalNotGranted):
                execute_outreach_send(runtime, proposal.approval_id)
        assert mock_send.call_count == 1

    def test_refuses_when_the_draft_changed_after_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        # Simulate an out-of-band edit that superseding cannot catch.
        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        message.model_copy(update={"draft_text": "something else entirely"}).save(runtime.storage)

        with patch("careeros.operations.outreach.send_email") as mock_send:
            with pytest.raises(ArtifactChanged):
                execute_outreach_send(runtime, proposal.approval_id)
        mock_send.assert_not_called()

    def test_refuses_without_a_recipient_email(self, tmp_path):
        runtime = _runtime(tmp_path, with_email=False)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        with patch("careeros.operations.outreach.send_email") as mock_send:
            with pytest.raises(MissingRecipient) as exc:
                execute_outreach_send(runtime, proposal.approval_id)
        assert exc.value.person_name == "Jane Doe"
        mock_send.assert_not_called()
        # The approval is left approved, not failed: nothing was attempted, so
        # adding the address and retrying must still work.
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_smtp_failure_marks_the_message_and_approval_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        with patch("careeros.operations.outreach.send_email",
                   side_effect=RuntimeError("smtp down")):
            with pytest.raises(SendFailed):
                execute_outreach_send(runtime, proposal.approval_id)

        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).send_state == "failed"
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == FAILED
        assert approval.detail == "RuntimeError"
        assert "outreach_send_failed" in _log(runtime.storage)

    def test_a_malformed_payload_raises_rather_than_key_error(self, tmp_path):
        from careeros.operations.errors import MalformedApproval
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"payload": {}}).save(runtime.storage)
        with pytest.raises(MalformedApproval):
            execute_outreach_send(runtime, proposal.approval_id)


class TestDeclineOutreachSend:
    def test_marks_the_message_declined_and_logs(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        resolve_approval(runtime, proposal.approval_id,
                         ApprovalResult(approved=False), action_label="outreach")
        decline_outreach_send(runtime, proposal.approval_id)
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).send_state == "declined"
        assert "outreach_send_declined" in _log(runtime.storage)

    def test_refuses_when_the_approval_was_not_declined(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        with pytest.raises(ApprovalNotGranted):
            decline_outreach_send(runtime, proposal.approval_id)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_operations_outreach.py -q`
Expected: FAIL — `ImportError: cannot import name 'execute_outreach_send'`.

- [ ] **Step 3: Implement both functions**

In `careeros/operations/outreach.py`, extend the imports:

```python
from careeros.mailer import send_email
from careeros.operations.approvals import (
    APPROVED, DECLINED, mark_executed, mark_failed, open_approval, payload_value,
    require_state,
)
from careeros.operations.errors import (
    ArtifactChanged, DraftFailed, EntityNotFound, MissingRecipient, PolicyBlocked,
    SendFailed,
)
```

Add the result type next to `OutreachProposal`:

```python
@dataclass(frozen=True)
class OutreachResult:
    message_id: str
    recipient_name: str
    sent_at: str
```

Append both functions:

```python
def _load_for_execution(runtime: AgentRuntime, message_id: str, person_id: str):
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
        person = Person.load(runtime.storage, person_id)
        job = Job.load(runtime.storage, message.job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Outreach message, person, or job not found.") from exc
    return message, person, job


def execute_outreach_send(
    runtime: AgentRuntime, approval_id: str, *, action_label: str = "outreach",
) -> OutreachResult:
    """Send the email an approved approval authorized, and nothing else.

    Drafts nothing and calls no LLM: the text that sends is read back from the
    stored OutreachMessage and checked against the digest recorded when the
    approval was created, so the bytes reviewed are the bytes transmitted.
    """
    approval = require_state(runtime.storage, approval_id, APPROVED)
    message_id = payload_value(approval, "message_id")
    person_id = payload_value(approval, "person_id")
    expected_digest = payload_value(approval, "draft_sha256")

    message, person, job = _load_for_execution(runtime, message_id, person_id)

    if draft_digest(message.draft_text) != expected_digest:
        raise ArtifactChanged("outreach/" + message_id + ".json")

    if not person.email:
        # Nothing has been attempted, so the approval stays approved: adding
        # the address and retrying must still work without re-approving.
        raise MissingRecipient(person_id, person.name)

    try:
        send_email(person.email, subject_for(job), message.draft_text)
    except Exception as exc:
        message.model_copy(update={"send_state": "failed"}).save(runtime.storage)
        mark_failed(runtime, approval_id, type(exc).__name__)
        runtime.record_activity(runtime.new_event(
            "outreach_send_failed", action_label,
            "Send failed for outreach to " + person.name + ": " + type(exc).__name__,
            status="failed", entity_type="outreach_message", entity_id=message_id,
        ))
        raise SendFailed(str(exc)) from exc

    sent_at = _now()
    message.model_copy(update={"send_state": "sent", "sent_at": sent_at}).save(runtime.storage)
    mark_executed(runtime, approval_id)
    runtime.record_activity(runtime.new_event(
        "outreach_sent", action_label, "Sent outreach to " + person.name,
        entity_type="outreach_message", entity_id=message_id,
    ))
    return OutreachResult(
        message_id=message_id, recipient_name=person.name, sent_at=sent_at
    )


def decline_outreach_send(
    runtime: AgentRuntime, approval_id: str, *, action_label: str = "outreach",
) -> None:
    """Record that a declined approval's message will not be sent.

    Lives here rather than inside resolve_approval because approvals.py is
    deliberately action-agnostic — it knows approval states and nothing about
    outreach messages. Callers branch on the decision they already hold.
    """
    approval = require_state(runtime.storage, approval_id, DECLINED)
    message_id = payload_value(approval, "message_id")
    person_id = payload_value(approval, "person_id")
    message, person, _ = _load_for_execution(runtime, message_id, person_id)

    message.model_copy(update={"send_state": "declined"}).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "outreach_send_declined", action_label,
        "Send declined for outreach to " + person.name,
        entity_type="outreach_message", entity_id=message_id,
    ))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_operations_outreach.py tests/test_operations_purity.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add careeros/operations/outreach.py tests/test_operations_outreach.py
git commit -m "feat: add execute_outreach_send and decline_outreach_send"
```

---

### Task 6: Workspace discovery and the agent runtime bootstrap

**Files:**
- Modify: `careeros/runtime/factory.py`
- Test: `tests/test_runtime_factory.py` (extend)

**Interfaces:**
- Consumes: `GlobalConfig` from `careeros/config.py`; `LocalFilesystemStorage`; `open_workspace`
- Produces: `WorkspaceNotConfigured`; `resolve_storage(workspace_path: str | None = None) -> LocalFilesystemStorage`; `open_agent_runtime(*, workspace_path=None, approval_callback, session_id=None) -> ClaudeCodeRuntime`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime_factory.py`:

```python
class TestResolveStorage:
    def test_explicit_path_wins_over_everything(self, tmp_path, monkeypatch):
        from careeros.runtime.factory import resolve_storage
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path / "from-env"))
        storage = resolve_storage(str(tmp_path / "explicit"))
        assert storage.resolve("manifest.json").startswith(str(tmp_path / "explicit"))

    def test_env_var_is_used_when_no_explicit_path(self, tmp_path, monkeypatch):
        from careeros.runtime.factory import resolve_storage
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        storage = resolve_storage()
        assert storage.resolve("manifest.json").startswith(str(tmp_path))

    def test_config_file_is_the_last_tier(self, tmp_path, monkeypatch):
        from careeros.config import GlobalConfig
        from careeros.runtime.factory import resolve_storage
        monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
        with patch.object(GlobalConfig, "load",
                          return_value=GlobalConfig(workspace_path=str(tmp_path))):
            storage = resolve_storage()
        assert storage.resolve("manifest.json").startswith(str(tmp_path))

    def test_raises_when_nothing_is_configured(self, monkeypatch):
        from careeros.config import GlobalConfig
        from careeros.runtime.factory import WorkspaceNotConfigured, resolve_storage
        monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig()):
            with pytest.raises(WorkspaceNotConfigured):
                resolve_storage()

    def test_an_empty_env_var_falls_through_to_config(self, tmp_path, monkeypatch):
        from careeros.config import GlobalConfig
        from careeros.runtime.factory import resolve_storage
        monkeypatch.setenv("CAREEROS_WORKSPACE", "")
        with patch.object(GlobalConfig, "load",
                          return_value=GlobalConfig(workspace_path=str(tmp_path))):
            storage = resolve_storage()
        assert storage.resolve("manifest.json").startswith(str(tmp_path))


class TestOpenAgentRuntime:
    def test_bootstraps_a_claude_code_runtime_from_the_env_var(self, tmp_path, monkeypatch):
        from careeros.operations.approval_queue import queue_only
        from careeros.runtime.claude_code import ClaudeCodeRuntime
        from careeros.runtime.factory import open_agent_runtime
        from careeros.storage.filesystem import LocalFilesystemStorage
        from careeros.workspace.manager import init_workspace

        init_workspace(LocalFilesystemStorage(str(tmp_path)))
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))

        runtime = open_agent_runtime(approval_callback=queue_only)
        assert isinstance(runtime, ClaudeCodeRuntime)
        assert runtime.agent_runtime_name == "claude_code"
        assert runtime.session_id

    def test_honors_an_explicit_session_id(self, tmp_path, monkeypatch):
        from careeros.operations.approval_queue import queue_only
        from careeros.runtime.factory import open_agent_runtime
        from careeros.storage.filesystem import LocalFilesystemStorage
        from careeros.workspace.manager import init_workspace

        init_workspace(LocalFilesystemStorage(str(tmp_path)))
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-1")
        assert runtime.session_id == "agent-1"

    def test_missing_manifest_propagates_file_not_found(self, tmp_path, monkeypatch):
        from careeros.operations.approval_queue import queue_only
        from careeros.runtime.factory import open_agent_runtime
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        with pytest.raises(FileNotFoundError):
            open_agent_runtime(approval_callback=queue_only)

    def test_approval_callback_is_required_and_keyword_only(self, tmp_path, monkeypatch):
        from careeros.runtime.factory import open_agent_runtime
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        with pytest.raises(TypeError):
            open_agent_runtime()
```

Ensure `tests/test_runtime_factory.py` imports `pytest` and `patch` at the top; add them if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_runtime_factory.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_storage'`.

- [ ] **Step 3: Implement the additions**

Replace the imports at the top of `careeros/runtime/factory.py` and append the new functions:

```python
from __future__ import annotations
import os
import uuid
from careeros.config import GlobalConfig
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.claude_code import ApprovalCallback, ClaudeCodeRuntime
from careeros.runtime.local import LocalRuntime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import open_workspace

WORKSPACE_ENV_VAR = "CAREEROS_WORKSPACE"


class WorkspaceNotConfigured(Exception):
    pass
```

Keep `open_local_runtime`, `open_claude_code_runtime`, and `open_automation_runtime` exactly as they are, then append:

```python
def resolve_storage(workspace_path: str | None = None) -> LocalFilesystemStorage:
    """Find the workspace: explicit path, then CAREEROS_WORKSPACE, then config.

    The env-var tier is what an out-of-process agent needs — it exports the
    variable once and every subprocess it spawns finds the same workspace
    without a --workspace flag threaded through every call. Raises rather
    than printing, because a non-CLI caller has no terminal to print to.
    """
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    env_path = os.environ.get(WORKSPACE_ENV_VAR)
    if env_path:
        return LocalFilesystemStorage(env_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        raise WorkspaceNotConfigured(
            "No workspace configured. Run 'careeros onboard' first."
        )
    return LocalFilesystemStorage(config.workspace_path)


def open_agent_runtime(
    *,
    workspace_path: str | None = None,
    approval_callback: ApprovalCallback,
    session_id: str | None = None,
) -> ClaudeCodeRuntime:
    """Open a workspace and a ClaudeCodeRuntime over it in one call.

    approval_callback stays required and keyword-only: construction must fail
    loudly rather than silently auto-denying every approval-gated action.
    """
    storage = resolve_storage(workspace_path)
    ctx = open_workspace(storage)
    return ClaudeCodeRuntime(
        storage, ctx, session_id or uuid.uuid4().hex, approval_callback
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_runtime_factory.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add careeros/runtime/factory.py tests/test_runtime_factory.py
git commit -m "feat: add three-tier workspace discovery and open_agent_runtime"
```

---

### Task 7: Rewire `outreach send` onto the operations layer

The command keeps its exact prompts, messages, and exit codes. Its tests exist to prove nothing observable changed.

**Files:**
- Modify: `careeros/cli/outreach_cmd.py`
- Modify: `tests/test_outreach_cmd.py`

**Interfaces:**
- Consumes: everything from Tasks 4-6
- Produces: no new public interface; `careeros/cli/outreach_cmd.py` no longer defines `_get_storage`, `_slugify`, or `_message_id`

- [ ] **Step 1: Move the existing test patch targets**

The draft and send calls now happen inside the operations module, so patching the CLI module no longer intercepts them. In `tests/test_outreach_cmd.py`, replace throughout:

- `careeros.cli.outreach_cmd.generate_outreach_message` → `careeros.operations.outreach.generate_outreach_message`
- `careeros.cli.outreach_cmd.send_email` → `careeros.operations.outreach.send_email`

Leave `careeros.cli.outreach_cmd.Prompt.ask` and `careeros.runtime.local.Confirm.ask` untouched — the review loop and the approval prompt both stay in the command body.

Run: `.venv/bin/python -m pytest tests/test_outreach_cmd.py -q`
Expected: FAIL — patching a name the operations module does not import yet, plus the CLI still calling its own copies.

- [ ] **Step 2: Add a test for the behavior that legitimately changed**

Append to `tests/test_outreach_cmd.py`:

```python
class TestOutreachSendApprovalRecord:
    def test_an_approved_send_leaves_an_executed_approval_record(self, tmp_path):
        from careeros.core.models import Approval
        from careeros.operations.approvals import EXECUTED, list_pending
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email"):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        approvals = [p for p in storage.list("approvals/") if p.endswith(".json")]
        assert len(approvals) == 1
        approval_id = approvals[0][len("approvals/"):-len(".json")]
        approval = Approval.load(storage, approval_id)
        assert approval.state == EXECUTED
        assert approval.decided_by == "local"
        assert list_pending(storage) == []

    def test_regenerating_supersedes_and_sends_only_the_accepted_draft(self, tmp_path):
        from careeros.core.models import OutreachMessage
        ws_path = _setup_workspace(tmp_path)
        drafts = ["first draft", "second draft"]
        with patch("careeros.operations.outreach.generate_outreach_message", side_effect=drafts), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        # The draft that was on screen when the user accepted is the one sent.
        assert mock_send.call_args[0][2] == "second draft"
        storage = LocalFilesystemStorage(ws_path)
        assert OutreachMessage.load(storage, MESSAGE_ID).draft_text == "second draft"
```

- [ ] **Step 3: Rewire the command**

Replace the imports and the three command bodies in `careeros/cli/outreach_cmd.py`. New import block:

```python
from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.core.models import OutreachMessage, Person
from careeros.operations.approvals import resolve_approval
from careeros.operations.errors import OperationError
from careeros.operations.outreach import (
    ACTION, decline_outreach_send, execute_outreach_send, make_message_id,
    propose_outreach_send,
)
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import (
    WorkspaceNotConfigured, open_local_runtime, resolve_storage,
)

outreach_app = typer.Typer(help="Draft, approve, and send outreach messages.")
people_app = typer.Typer(help="Manage researched people.")
console = Console()

MAX_REGENERATIONS = 5
```

Delete `_slugify`, `_message_id`, and `_get_storage` — `make_message_id` replaces the first two and `resolve_storage` the third. Keep `_now` and the `_people_app_callback`. Add one shared helper:

```python
def _open_runtime(workspace_path: str | None):
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
```

Replace the body of `send` with:

```python
@outreach_app.command()
def send(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    runtime = _open_runtime(workspace)

    try:
        proposal = propose_outreach_send(runtime, job, person, model=model)

        regenerations = 0
        while True:
            console.print(Panel(proposal.draft_text, title="Outreach to " + proposal.recipient_name))
            if regenerations >= MAX_REGENERATIONS:
                choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
            else:
                choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")
            if choice == "q":
                rprint("Aborted.")
                raise typer.Exit(0)
            if choice == "r":
                regenerations += 1
                proposal = propose_outreach_send(runtime, job, person, model=model)
                continue
            break

        if proposal.already_sent_at:
            rprint(
                "[yellow]An outreach email to " + proposal.recipient_name
                + " was already sent on " + proposal.already_sent_at
                + ". This will send another.[/yellow]"
            )

        result = runtime.request_approval(ActionProposal(
            action=ACTION,
            summary=proposal.summary,
            entity_type="outreach_message", entity_id=proposal.message_id,
        ))
        resolve_approval(runtime, proposal.approval_id, result, action_label="outreach")

        if not result.approved:
            decline_outreach_send(runtime, proposal.approval_id)
            rprint("Aborted.")
            raise typer.Exit(0)

        outcome = execute_outreach_send(runtime, proposal.approval_id)
    except OperationError as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)

    rprint("[green]Sent to " + outcome.recipient_name + "[/green]")
```

Note the `MissingRecipient` message: the current command prints an actionable follow-up naming `careeros people update`. Preserve it by catching that case first, before the general handler:

```python
    except MissingRecipient as exc:
        rprint(
            "[red]No email on file for " + exc.person_name
            + ". Run 'careeros people update " + person
            + " --email <address>' and retry.[/red]"
        )
        raise typer.Exit(1)
    except OperationError as exc:
```

Import `MissingRecipient` alongside `OperationError`. In `mark_referral_requested` and `people update`, replace `_get_storage(workspace)` with `resolve_storage(workspace)` via `_open_runtime(workspace)`, and replace `_message_id(job, person)` with `make_message_id(job, person)`.

- [ ] **Step 4: Run the outreach tests**

Run: `.venv/bin/python -m pytest tests/test_outreach_cmd.py -q`
Expected: PASS — every pre-existing assertion about messages and exit codes still holds.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add careeros/cli/outreach_cmd.py tests/test_outreach_cmd.py
git commit -m "refactor: route outreach send through the operations layer"
```

---

### Task 8: The two-process integration test

This is the regression guard for the phase exit condition: it proves an approval survives a real process boundary, which no in-process test can show.

**Files:**
- Create: `tests/test_agent_integration.py`

**Interfaces:**
- Consumes: `open_agent_runtime`, `queue_only`, `propose_outreach_send`, `resolve_approval`, `execute_outreach_send`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_integration.py`:

```python
"""Proves an approval survives a real process boundary.

The shell-driven runtime this phase targets cannot round-trip to a human
inside one process, so the propose and execute halves genuinely run as
separate interpreters here — an in-process test cannot demonstrate that the
approval record, and not in-memory state, is what carries the decision.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from careeros.core.models import Approval, Company, Job, Person, Profile
from careeros.operations.approvals import EXECUTED
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

REPO_ROOT = Path(__file__).resolve().parent.parent

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp and would love to chat."

PROPOSE = """
import json, sys
from unittest.mock import patch
from careeros.operations.approval_queue import queue_only
from careeros.operations.outreach import propose_outreach_send
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-propose")
with patch("careeros.operations.outreach.generate_outreach_message", return_value=sys.argv[3]):
    proposal = propose_outreach_send(runtime, sys.argv[1], sys.argv[2], action_label="agent")
print(json.dumps({"approval_id": proposal.approval_id, "summary": proposal.summary}))
"""

EXECUTE = """
import sys
from unittest.mock import patch
from careeros.operations.approval_queue import queue_only
from careeros.operations.approvals import resolve_approval
from careeros.operations.outreach import execute_outreach_send
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-execute")
resolve_approval(
    runtime, sys.argv[1],
    ApprovalResult(approved=True, reason="user said yes in chat"),
    action_label="agent",
)
with patch("careeros.operations.outreach.send_email") as mock_send:
    result = execute_outreach_send(runtime, sys.argv[1], action_label="agent")
    assert mock_send.call_count == 1, mock_send.call_count
print(result.recipient_name)
"""


def _seed(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
           title="Engineering Manager", email="jane@acme.com",
           researched_at=now).save(storage)
    return storage


def _run(script, args, workspace):
    env = dict(os.environ)
    env["CAREEROS_WORKSPACE"] = str(workspace)
    completed = subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_two_processes_complete_one_approval_gated_send(tmp_path):
    storage = _seed(tmp_path)

    proposed = json.loads(_run(PROPOSE, [JOB_ID, PERSON_ID, DRAFT], tmp_path))
    approval_id = proposed["approval_id"]

    # The first process is gone. Only the workspace carries the state forward.
    assert Approval.load(storage, approval_id).state == "pending"

    assert _run(EXECUTE, [approval_id], tmp_path) == "Jane Doe"

    approval = Approval.load(storage, approval_id)
    assert approval.state == EXECUTED
    assert approval.decided_by == "claude_code"
    assert approval.reason == "user said yes in chat"
    assert approval.executed_at is not None


def test_the_activity_log_attributes_both_halves_to_the_agent_runtime(tmp_path):
    storage = _seed(tmp_path)
    proposed = json.loads(_run(PROPOSE, [JOB_ID, PERSON_ID, DRAFT], tmp_path))
    _run(EXECUTE, [proposed["approval_id"]], tmp_path)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events = [
        json.loads(line)
        for line in storage.read("activity/" + date + ".jsonl").decode().strip().split("\n")
    ]
    by_type = {e["event_type"]: e for e in events}

    assert by_type["approval_granted"]["agent_runtime"] == "claude_code"
    assert by_type["approval_granted"]["reason"] == "user said yes in chat"
    assert by_type["outreach_sent"]["agent_runtime"] == "claude_code"
    assert by_type["outreach_sent"]["action"] == "agent"
    assert {e["session_id"] for e in events} == {"agent-propose", "agent-execute"}


def test_the_second_process_cannot_execute_without_a_decision(tmp_path):
    _seed(tmp_path)
    proposed = json.loads(_run(PROPOSE, [JOB_ID, PERSON_ID, DRAFT], tmp_path))

    script = """
import sys
from careeros.operations.approval_queue import queue_only
from careeros.operations.errors import ApprovalNotGranted
from careeros.operations.outreach import execute_outreach_send
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-execute")
try:
    execute_outreach_send(runtime, sys.argv[1], action_label="agent")
except ApprovalNotGranted as exc:
    print(exc.state)
else:
    raise AssertionError("executed a pending approval")
"""
    assert _run(script, [proposed["approval_id"]], tmp_path) == "pending"
```

- [ ] **Step 2: Run it to verify it fails, then passes**

Run: `.venv/bin/python -m pytest tests/test_agent_integration.py -q`
Expected before Tasks 4-6 exist: collection error. With them in place: PASS. If `queue_only` were ever changed to auto-approve, `test_the_second_process_cannot_execute_without_a_decision` fails.

- [ ] **Step 3: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add tests/test_agent_integration.py
git commit -m "test: prove an approval survives a real process boundary"
```

---

### Task 9: The integration contract and the honest record

**Files:**
- Create: `docs/agent-integration.md`
- Modify: `README.md`, `ROADMAP.md`, `docs/superpowers/DIVERGENCES.md`

**Interfaces:**
- Consumes: everything above. No code changes.

- [ ] **Step 1: Write `docs/agent-integration.md`**

Cover, in this order, with a runnable example for each claim. Scope it to outreach — apply does not exist yet, and documenting it would be a promise the code does not keep.

1. **What this is** — how to drive CareerOS from an agent session that is not the CLI.
2. **Workspace discovery** — the three tiers in precedence order; that `CAREEROS_WORKSPACE` is the tier to use from a shell; that `resolve_storage` raises `WorkspaceNotConfigured` rather than printing.
3. **The `AgentRuntime` Protocol** — each member and its guarantee. State explicitly that `record_activity` is append-only and **overwrites** `agent_runtime` and `session_id` with the runtime's own identity, so a caller must not set them expecting them to survive.
4. **`approval_callback`** — required, keyword-only, must never be omitted; `queue_only` is the shipped choice for out-of-process decisions, and it denies by default; it is *not* the decision path for a shell-driven runtime.
5. **The propose → resolve → execute sequence** — the worked two-process example from the spec §7.3, verbatim and runnable.
6. **The `approvals/<id>.json` schema** — every field, the state machine diagram, and the payload keys for `send_outreach` (`message_id`, `job_id`, `person_id`, `draft_sha256`).
7. **The integrator's obligations** — an approval is single-use; only `approved` executes; re-proposing supersedes; the digest is re-checked at execute time, so editing `outreach/<id>.json` after approval makes the send fail with `ArtifactChanged` by design.
8. **Error vocabulary** — the `OperationError` subclasses and what each means for a retry decision.
9. **Recovering lost context** — `list_pending(storage)` to rediscover open decisions.

- [ ] **Step 2: Add a README section**

Add an "Agent integration" section after the existing `AgentRuntime` paragraph (around `README.md:103-107`), pointing at `docs/agent-integration.md` and stating plainly that outreach send is drivable from an external agent session today and apply is not yet.

- [ ] **Step 3: Update `DIVERGENCES.md` precisely**

Do not delete rows wholesale — 12a closes them only partly, and overclaiming here is exactly what past reviews of this repo caught.

- The `approvals/` row: closed. An `Approval` record now exists, and `list_pending` is the poll surface. Note that it is written synchronously by whichever process proposes, and that no notification or expiry exists.
- The `reason` row: **partly** closed. Populated on `approval_granted` and `approval_declined` only. Every other event type still leaves it `None`.
- The `CAREEROS_WORKSPACE` row: **partly** closed. Implemented in `factory.resolve_storage` and honored by `outreach send` and `open_agent_runtime`. `browse_cmd`, `research_cmd`, `onboard`, and `resume_cmd` still use private `_get_storage` helpers that do not read it.
- Add the two new deliberate deferrals from spec §10: the four un-converged `_get_storage` helpers, and `queue_only` being the only shipped out-of-process callback with no notification, expiry, or locking.

- [ ] **Step 4: Update `ROADMAP.md`**

Rewrite the Phase 12 section in the style Phase 11 uses: state **12a shipped**, name what it built, correct the phase's original premise (`ClaudeCodeRuntime` already existed; the gap was the operations layer and cross-process approval), and state that 12b — apply plus the `discover-and-apply` de-duplication — remains. Record that the exit condition's manual verification is still outstanding, and what it requires (`CAREEROS_SMTP_*` configured and a `Person` holding the user's own address).

- [ ] **Step 5: Verify the docs match the code**

Run: `.venv/bin/python -m pytest -q`
Then run each code block in `docs/agent-integration.md` against a scratch workspace and confirm it behaves as documented. A code block in an integration contract that does not run is worse than no example.

- [ ] **Step 6: Commit**

```bash
git add docs/agent-integration.md README.md ROADMAP.md docs/superpowers/DIVERGENCES.md
git commit -m "docs: document the agent integration contract for Phase 12a"
```

---

## Deferred to 12b

Named here so the boundary is explicit and 12b's plan does not have to rediscover it:

- `careeros/operations/apply.py` — `propose_apply` / `execute_apply`
- The `BoardSessionRequired`, `FillIncomplete`, and `BrowserUnavailable` error types, added where first used
- The `apply_cmd` rewire and the `discover-and-apply` de-duplication (spec §7.2)
- Extending `docs/agent-integration.md` with the `apply_to_job` payload keys and the apply sequence

## Exit Condition for 12a

Automated: `.venv/bin/python -m pytest -q` green, with `tests/test_agent_integration.py` proving a propose in one process and an execute in another complete one send, attributed to `claude_code` across two session IDs.

Manual (the phase's real exit condition, spec §Exit Condition): a Claude Code session proposes an outreach send against the user's real workspace via `CAREEROS_WORKSPACE`, surfaces the draft in conversation, records the user's actual decision, and executes a real send to the user's own address in a second process. Requires `CAREEROS_SMTP_*` configured and a `Person` record holding that address. This is a separate, user-present step after Task 9 — not something to claim from a green suite.
