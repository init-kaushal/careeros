# Agent integration

This document is the contract for driving CareerOS from a process that is not the
`careeros` CLI — an agent session (Claude Code or otherwise), a script, or a future
third runtime. It is scoped to **outreach send only**. Job apply (`careeros/operations/apply.py`)
is Phase 12b work and does not exist yet in this codebase; nothing below should be read as a
promise about it.

Everything here is grounded in code that ships today: `careeros/operations/approvals.py`,
`careeros/operations/outreach.py`, `careeros/operations/errors.py`,
`careeros/operations/approval_queue.py`, `careeros/runtime/factory.py`, `careeros/runtime/base.py`,
and `careeros/core/models.py`. `tests/test_agent_integration.py` is the working example this
document's snippets are drawn from.

## 1. What this is

Phase 12a moved the outreach-send flow out of the Typer CLI and into an operations layer
(`careeros/operations/outreach.py`) that takes an `AgentRuntime` instead of talking to storage or
`typer`/`rich` directly. Paired with a durable `Approval` record under `approvals/`, this makes it
possible for one process to propose an approval-gated action — draft an outreach email and ask
"should this be sent?" — and a **different, later process** to carry out the human's decision and
execute the send. That second process does not have to be the CLI, and it does not have to run
while the first process is still alive. This is what lets an agent session hold a conversation with
a human, propose a send, and only actually call `send_email` once the human's answer has been
durably recorded.

## 2. Workspace discovery

`careeros.runtime.factory.resolve_storage` finds the workspace in this precedence order:

1. An explicit path passed as an argument.
2. The `CAREEROS_WORKSPACE` environment variable.
3. The workspace path saved in the global config (`~/.config/careeros/config.json`, written by
   `careeros onboard`).

```python
# careeros/runtime/factory.py
def resolve_storage(workspace_path: str | None = None) -> LocalFilesystemStorage:
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
```

For an agent session, **`CAREEROS_WORKSPACE` is the tier to use**. A parent process (your agent
runtime's shell, or the orchestrator that spawns each turn as a new subprocess) exports it once,
and every subprocess it spawns afterward — propose in one, execute in another — resolves the same
workspace without a `--workspace` flag threaded through every call.

`resolve_storage` **raises `WorkspaceNotConfigured`** rather than printing an error, because a
non-CLI caller has no terminal to print to. Catch it; do not expect a message on stdout.

## 3. The `AgentRuntime` Protocol

```python
# careeros/runtime/base.py
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

- `agent_runtime_name` — a fixed string identifying the runtime (`"claude_code"` for
  `ClaudeCodeRuntime`, `"local"` for `LocalRuntime`, `"automation"` for `AutomationRuntime`). This
  is the value written into every activity event and into `Approval.decided_by`.
- `storage` — the `StorageProvider` this runtime is bound to. Operations read and write through
  it; they never construct their own storage.
- `read_workspace` / `write_workspace` — thin wrappers over `storage.read`/`atomic_write`, decoding
  and encoding UTF-8 text.
- `request_approval` — calls the runtime's `approval_callback` (see §4). Operations that gate an
  action synchronously within one process (there are none left in the outreach flow — see §4) go
  through this.
- `new_event` — builds an `ActivityEvent` stamped with this runtime's `agent_runtime_name`.
- `record_activity` — appends the event to the day's `activity/<date>.jsonl` file. **It is
  append-only, and it unconditionally overwrites `event.agent_runtime` and `event.session_id` with
  the runtime's own identity before writing:**

```python
# careeros/runtime/claude_code.py
def record_activity(self, event: ActivityEvent) -> None:
    event.agent_runtime = self.agent_runtime_name
    event.session_id = self.session_id
    self._logger.log(event)
```

  Do not set `agent_runtime` or `session_id` on an event expecting them to survive — they will be
  replaced with whatever runtime and session actually recorded the event, which is the point: the
  activity log attributes every event to the runtime that truly wrote it, not to whatever a caller
  claimed.

## 4. `approval_callback`

`ClaudeCodeRuntime` and `open_agent_runtime` both take `approval_callback` as a **required,
keyword-only** parameter:

```python
# careeros/runtime/factory.py
def open_agent_runtime(
    *,
    workspace_path: str | None = None,
    approval_callback: ApprovalCallback,
    session_id: str | None = None,
) -> ClaudeCodeRuntime:
```

There is no default. Omitting it is a `TypeError` at construction time, not a silent auto-deny at
call time — construction must fail loudly rather than let a missing approval path slip through.

`careeros.operations.approval_queue.queue_only` is the one shipped callback for out-of-process use:

```python
# careeros/operations/approval_queue.py
def queue_only(proposal: ActionProposal) -> ApprovalResult:
    return ApprovalResult(approved=False, reason="deferred to out-of-process approval")
```

It denies by default. Read that correctly: **for a shell-driven runtime, this callback is not the
decision path.** The actual decision goes through `resolve_approval` in a later process, driven by
whatever the human told the agent in conversation. `queue_only` exists only to give a safe,
non-auto-approving answer to any *other* `request_approval` call the runtime happens to receive —
so that no integrator has to invent the same deny-by-default stub, and so a code path that still
calls `request_approval` synchronously can never accidentally approve something nobody reviewed.

## 5. The propose → resolve → execute sequence

The outreach flow spans three operations functions, each taking an `AgentRuntime`:

- `propose_outreach_send(runtime, job_id, person_id, *, model=None, action_label="outreach")` —
  drafts the message, saves it as an `OutreachMessage`, and opens a `pending` `Approval`.
- `resolve_approval(runtime, approval_id, result, *, action_label)` — records a human decision
  against a `pending` approval, moving it to `approved` or `declined`.
- `execute_outreach_send(runtime, approval_id, *, action_label="outreach")` — re-verifies the
  draft against the digest recorded at proposal time, marks the approval `executed`, and calls
  `send_email`.

Because these take an `AgentRuntime` and read/write only through `storage`, they run correctly
across a process boundary as long as both processes point `CAREEROS_WORKSPACE` at the same
workspace. This is the worked example from `tests/test_agent_integration.py`, run as two literally
separate Python processes:

**Process 1 — propose** (an agent session drafts and asks):

```python
import json
from careeros.operations.approval_queue import queue_only
from careeros.operations.outreach import propose_outreach_send
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-propose")
proposal = propose_outreach_send(runtime, "acme-sre-abc1", "acme-corp-jane-doe", action_label="agent")
print(json.dumps({"approval_id": proposal.approval_id, "summary": proposal.summary}))
# -> surface proposal.summary and proposal.draft_text to the human in conversation
```

This process can exit. Nothing about the pending decision lives in memory — it is all in
`approvals/<approval_id>.json` and `outreach/<message_id>.json`.

**Process 2 — resolve the human's answer, then execute** (same or a later agent session, once the
human has actually replied):

```python
from careeros.operations.approvals import resolve_approval
from careeros.operations.outreach import execute_outreach_send
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_agent_runtime
from careeros.operations.approval_queue import queue_only

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-execute")
resolve_approval(
    runtime, approval_id,
    ApprovalResult(approved=True, reason="user said yes in chat"),
    action_label="agent",
)
result = execute_outreach_send(runtime, approval_id, action_label="agent")
print(result.recipient_name, result.sent_at)
```

If the human declined instead, call `resolve_approval` with `ApprovalResult(approved=False,
reason=...)` and then `decline_outreach_send(runtime, approval_id, action_label="agent")` — never
`execute_outreach_send`, which requires the `approved` state and raises otherwise (§8).

Section 8 below verifies this sequence actually runs, with `send_email` patched so nothing real is
sent.

## 6. The `approvals/<id>.json` schema

Every field of the `Approval` model (`careeros/core/models.py`):

| Field | Type | Set by | Notes |
|---|---|---|---|
| `id` | `str` | `open_approval` | `<action>-<entity_id-slug>-<6 hex chars>`, from `make_approval_id` |
| `action` | `str` | `open_approval` | `"send_outreach"` for the outreach flow |
| `summary` | `str` | `open_approval` | Human-readable description, shown to the reviewer |
| `state` | `str` | see state machine below | one of `pending`, `approved`, `declined`, `executed`, `superseded`, `failed` |
| `entity_type` | `str \| None` | `open_approval` | `"outreach_message"` for outreach |
| `entity_id` | `str \| None` | `open_approval` | the `message_id` |
| `payload` | `dict[str, str]` | `open_approval` | see below; identifiers and a digest only, never drafted content |
| `created_at` | `str` | `open_approval` | ISO-8601 UTC |
| `decided_at` | `str \| None` | `resolve_approval` | set when moving out of `pending` |
| `decided_by` | `str \| None` | `resolve_approval` | the deciding runtime's `agent_runtime_name` |
| `reason` | `str \| None` | `resolve_approval` | the `ApprovalResult.reason` given to the decision |
| `executed_at` | `str \| None` | `mark_executed` | set when moving to `executed` |
| `detail` | `str \| None` | `mark_failed` | set when moving to `failed`; the exception type name |

State machine: `pending` is the only state a decision can be recorded against.
`pending -> approved -> executed` is the happy path, and on that path `executed` is durably
terminal — nothing moves the record out of it. `pending -> declined` and `pending -> superseded`
(a re-propose for the same action and entity invalidates the older pending approval — see §7) are
two more ways out of an open approval, both terminal.

The fourth path is not a direct `approved -> failed` edge: it is **`approved -> executed ->
failed`**, all within one call to `execute_outreach_send`. `execute_outreach_send` calls
`mark_executed` — writing `state="executed"` to disk — *before* it calls `send_email`, specifically
so that a crash mid-send leaves a stale `executed` record rather than an `approved` one a second
process could still act on (see §10). If `send_email` then raises, `mark_failed` overwrites that
same record to `state="failed"`, which is the true terminal state on that path. So `executed` is
written and then moved out of again, within the same function call, whenever the send itself
fails. **`declined`, `superseded`, and `failed` are always terminal. `executed` is terminal only on
the success path** — during a send failure it is a transient state that `execute_outreach_send`
passes through and immediately overwrites before returning control to the caller.

What this means if you are the one reading `approvals/<id>.json` later, from a different process:
finding `state == "executed"` on disk means the send was attempted and either succeeded or the
process died before it could record the outcome — it never means a send is still pending, and it
is never safe to retry against that approval id (see `SendFailed` in §8).

Payload keys for `send_outreach` (set by `propose_outreach_send`, read by `execute_outreach_send`
via `payload_value`):

- `message_id` — the `OutreachMessage` id the payload refers to
- `job_id`
- `person_id`
- `draft_sha256` — `sha256` hex digest of the draft text at proposal time

## 7. The integrator's obligations

- **An approval is single-use.** `execute_outreach_send` calls `mark_executed`, which moves the
  record out of `approved` to `executed`, before it calls `send_email`. Whether `send_email`
  then succeeds (record stays `executed`) or raises (record moves on to `failed` — see §6), the
  state is no longer `approved` either way. A second `execute_*` call against the same id fails
  `require_state`'s check and raises `ApprovalNotGranted` — it cannot send twice.
- **Only `approved` executes.** `execute_outreach_send` and `decline_outreach_send` both call
  `require_state` for the specific state they need (`approved`, `declined` respectively) and raise
  `ApprovalNotGranted` for anything else, including `pending`.
- **Re-proposing supersedes.** Calling `propose_outreach_send` again for the same `job_id` and
  `person_id` finds the prior *pending* approval for that action/entity pair (via `open_approval`'s
  scan of `list_pending`) and moves it to `superseded` before opening the new one. A stale approval
  id from before a regeneration cannot later execute against content that has since been
  overwritten. Superseding only reaches a `pending` approval — an already-`approved` one is left
  alone (nothing currently re-checks that case; do not assume approving, then regenerating,
  invalidates the approval you already hold).
- **The digest is re-verified at execute time**, against the `OutreachMessage.draft_text` read back
  from storage — not against anything held in memory:

  ```python
  # careeros/operations/outreach.py
  if draft_digest(message.draft_text) != expected_digest:
      raise ArtifactChanged("outreach/" + message_id + ".json")
  ```

  This is by design: editing `outreach/<message_id>.json` after approval but before execution
  changes what would be sent without a new review, so the send is refused rather than silently
  sending edited content under an approval that reviewed something else.

## 8. Error vocabulary

Every operation raises from a single hierarchy rooted at `OperationError`
(`careeros/operations/errors.py`). Operations never print and never call `sys.exit`; this is the
whole vocabulary a caller branches on.

| Exception | Raised when | Retry guidance |
|---|---|---|
| `EntityNotFound` | a job, person, company, or outreach message id does not resolve | not retryable without fixing the id |
| `PolicyBlocked` | the policy engine blocks the job (`.rule` names which rule) | not retryable without editing `config/policies.json` |
| `DraftFailed` | draft generation returned nothing | retry the propose call; likely an LLM-side transient failure |
| `MissingRecipient` | the person has no email on file at execute time | not retryable until `careeros people update <id> --email <address>` is run; the approval is untouched (still `approved`) so retrying `execute_outreach_send` after adding the address works without re-approving |
| `ApprovalNotGranted` | `require_state` finds the approval in a different state than the step needs (`.state` names the actual state) | retryable only by taking the correct action for that state — e.g. resolve first if `pending`, do nothing if already `executed` |
| `MalformedApproval` | a required payload key is missing (`.key` names it) | not retryable; the approval record itself is broken |
| `ArtifactChanged` | the draft's digest no longer matches what was approved (`.path` names the file) | not retryable as-is; re-propose so review covers the actual content |
| `SendFailed` | `send_email` raised | by the time this propagates to the caller, `mark_failed` has already run — the approval is `failed`, not `executed` (see §6). Do not retry against the same approval id; a retry requires a new propose |

**A boundary this hierarchy does not cover:** an unknown or malformed `approval_id` does not
surface as an `OperationError`. `Approval.load` raises a plain `FileNotFoundError` when the record
does not exist, and `LocalFilesystemStorage`'s path-escape guard raises a plain `ValueError` when
an id (used as a path segment) would resolve outside the workspace root:

```python
# careeros/storage/filesystem.py
def _resolve(self, path: str) -> Path:
    ...
    except ValueError:
        raise ValueError(f"Path '{path}' escapes workspace root")
```

Neither of those is an `OperationError`. A caller that only catches `OperationError` and lets
everything else propagate will see `FileNotFoundError` or `ValueError` for a bad id, not a
structured operations exception. This is a deliberate gap, not an oversight: the CLI never
supplies an arbitrary approval id (it only ever passes one it just received back from a propose
call), so a translation layer for a case only an external caller can reach was scope Phase 12a
declined. If you build a runtime that accepts an approval id from outside your own process —
resuming from a stored id, or accepting one over some external channel — catch
`FileNotFoundError` and `ValueError` alongside `OperationError`.

## 9. Recovering lost context

An agent session can lose its context — a conversation restart, a crashed process — after a
propose but before a resolve, with no record in memory of what was left open.
`careeros.operations.approvals.list_pending(storage)` rediscovers every `pending` approval, oldest
first, directly from `approvals/`:

```python
from careeros.operations.approvals import list_pending
from careeros.runtime.factory import resolve_storage

storage = resolve_storage()  # reads CAREEROS_WORKSPACE
for approval in list_pending(storage):
    print(approval.id, approval.action, approval.summary)
```

An unparseable record is skipped rather than raised on, so one corrupt file cannot hide every
other pending decision behind it.

## 10. Known limitations

- **A residual concurrency window.** `require_state` (`careeros/operations/approvals.py`) is a
  read-then-compare with no compare-and-swap, and there is no lock file over `approvals/`.
  `execute_outreach_send` calls `mark_executed` — which moves the approval out of `approved` to
  `executed` (durably so if the send then succeeds or the process dies before recording an
  outcome; overwritten to `failed` if `send_email` raises — see §6) — *before* calling
  `send_email`, specifically so that a crash between the two leaves a record no longer in
  `approved` state rather than one a second process could still execute against; that ordering
  closes the crash-then-duplicate-send window. It does not close the concurrency window: two
  processes racing `execute_outreach_send` against the same approval id could both read `state ==
  "approved"` before either has written `executed`, and both proceed to send. Do not run two
  executors against the same approval id concurrently; nothing in this layer prevents it.
- **`queue_only` is the only shipped out-of-process callback.** There is no notification, no
  expiry, and no locking. It gives a safe, deny-by-default answer to a synchronous
  `request_approval` call, and that is all it does. A runtime that wants genuine asynchronous
  approval — propose now, a human approves from another machine hours later — has the durable
  `Approval` record it needs (poll it with `list_pending`), but none of the machinery to be told
  when a decision lands, to expire a stale pending approval, or to prevent two callers from acting
  on the same one at once.
