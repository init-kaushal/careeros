# Agent integration

This document is the contract for driving CareerOS from a process that is not the
`careeros` CLI — an agent session (Claude Code or otherwise), a script, or a future
third runtime. It covers both approval-gated flows the operations layer exposes today:
outreach send (`careeros/operations/outreach.py`, Phase 12a, §1-§10) and job apply
(`careeros/operations/apply.py`, Phase 12b, §11).

Everything here is grounded in code that ships today: `careeros/operations/approvals.py`,
`careeros/operations/outreach.py`, `careeros/operations/apply.py`, `careeros/operations/errors.py`,
`careeros/operations/approval_queue.py`, `careeros/runtime/factory.py`, `careeros/runtime/base.py`,
and `careeros/core/models.py`. `tests/test_agent_integration.py` is the worked, literally
cross-process example for outreach that this document's outreach snippets are drawn from.
`tests/test_operations_apply.py` is the best source of truth for apply's behavior, but it exercises
`propose_apply`/`execute_apply` in a single process. §11's apply examples were run as two literal,
separate Python processes while writing this document, the same way `tests/test_agent_integration.py`
proves outreach — but that verification is not a committed, permanently-enforced test the way
outreach's is: nothing in the test suite will fail if a future change breaks apply's cross-process
contract the way `tests/test_agent_integration.py` would for outreach's.

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

**Every CLI command now honors this same three-tier precedence.** Every command module under
`careeros/cli/` — `apply_cmd`, `browse_cmd`, `browser_cmd`, `discover_and_apply_cmd`, `job_cmd`,
`outreach_cmd`, `research_cmd`, `resume_cmd`, and `workspace_cmd` — resolves its storage through
`factory.resolve_storage`, either directly or through a thin per-module wrapper (`job_cmd._get_storage`
and `workspace_cmd._get_storage` keep that name only for their `typer.Exit`-on-`WorkspaceNotConfigured`
handling; the resolution itself calls `resolve_storage`). `careeros/cli/portability.py`'s `careeros
export` resolves the same way. `careeros import` and `careeros onboard` are the two commands that do
not: each is workspace *creation*, not discovery — `import_workspace_cmd` takes a required `--dest`
and writes it straight into the global config after extracting the zip there, and `onboard` is the
command that writes that same config file in the first place. Neither has a prior workspace to
discover, so excluding both from `resolve_storage` is correct, not an oversight. There is no longer
a split-workspace hazard from exporting `CAREEROS_WORKSPACE` while a different path is saved in the
global config for every command that *does* discover rather than create — they all consult the same
explicit-path-then-env-var-then-config-file order, so they resolve to the same workspace.

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
- `request_approval` — calls the runtime's `approval_callback` (see §4). `outreach_cmd`, `apply_cmd`,
  and `discover_and_apply_cmd` all still gate synchronously within one process through this — a
  human at a terminal, or `AutomationPolicy`'s scheduled-run rules, deciding in the same process
  that proposed the action. The two-process flow §5 describes is a second, independent path that
  coexists with these, not a replacement for them.
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

`approval_callback` is required everywhere it appears — there is no default anywhere in this
list — but it is only **keyword-only** on `open_agent_runtime`:

```python
# careeros/runtime/factory.py
def open_agent_runtime(
    *,
    workspace_path: str | None = None,
    approval_callback: ApprovalCallback,
    session_id: str | None = None,
) -> ClaudeCodeRuntime:
```

`ClaudeCodeRuntime.__init__` and `open_claude_code_runtime` both take it as an ordinary
**positional** parameter (fourth and second, respectively) — there is no `*` before it in either
signature. Passing it by keyword to those two still works, since Python allows that for any
parameter that isn't keyword-only; the point is only that *omitting* the keyword and passing it
positionally also works for those two, unlike for `open_agent_runtime`. If you're integrating
against this module, prefer `open_agent_runtime` — it's the one this document's examples use below
— but do not assume the same call shape works verbatim against `ClaudeCodeRuntime` or
`open_claude_code_runtime`.

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

- `propose_outreach_send(runtime, job_id, person_id, *, model=None, action_label)` —
  drafts the message, saves it as an `OutreachMessage`, and opens a `pending` `Approval`.
- `resolve_approval(runtime, approval_id, result, *, action_label)` — records a human decision
  against a `pending` approval, moving it to `approved` or `declined`.
- `execute_outreach_send(runtime, approval_id, *, action_label)` — re-verifies the
  draft against the digest recorded at proposal time, marks the approval `executed`, and calls
  `send_email`.

`action_label` is required and keyword-only on every one of these — there is no default. Omitting
it is a `TypeError` at the call site, the same fail-loudly posture §4 describes for
`approval_callback`. An earlier version of this document (and an earlier version of the code)
defaulted it to `"outreach"`; that default was removed before Phase 12b shipped, on every function
that takes it, including `propose_apply` and `execute_apply` (§11).

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

`tests/test_agent_integration.py` verifies this sequence actually runs as two literal subprocesses,
with `send_email` patched so nothing real is sent.

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
are two more ways out of an open approval, both terminal. There is also an
**`approved -> superseded`** edge: a re-propose for the same `(action, entity_id)` invalidates the
older approval whether it is still `pending` or already `approved`-but-unexecuted (see §7). So
holding an `approved` id is not a guarantee it will still execute — a later propose can take it
away.

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

Payload keys for `send_outreach` (set by `propose_outreach_send`). Read at execute time means read
via `payload_value` by `execute_outreach_send` and/or `decline_outreach_send`; the rest is recorded
on the payload for audit only and read by neither:

- `message_id` — the `OutreachMessage` id the payload refers to; read at execute time by both
  `execute_outreach_send` and `decline_outreach_send`
- `job_id` — recorded for audit only; read by neither `execute_outreach_send` nor
  `decline_outreach_send`
- `person_id` — read at execute time by both `execute_outreach_send` and `decline_outreach_send`
- `draft_sha256` — `sha256` hex digest of the draft text at proposal time; read at execute time by
  `execute_outreach_send` only
- `subject` — read at execute time by `execute_outreach_send` only; the email subject line,
  computed once at proposal time from the `Job` as it read
  then. `execute_outreach_send` reads this back rather than recomputing it, so editing the job's
  title or company between approval and execution cannot change what goes out. It is metadata
  about the send, not drafted body content, so it belongs on the payload the same way `summary`
  does.

### 6.1 Activity events this flow emits

An integrator reading `activity/*.jsonl` directly (rather than through `list_pending` or an
`Approval` record) should not meet an undocumented `event_type`. This is the complete vocabulary
the outreach-send flow writes, so nothing else appears:

| `event_type` | Emitted by | When |
|---|---|---|
| `outreach_drafted` | `propose_outreach_send` | every successful draft, before any human review — including each regeneration |
| `approval_requested` | `open_approval` | every new `pending` approval is opened, including the one that follows a regeneration |
| `approval_superseded` | `open_approval` | a prior *open* (`pending` or `approved`-but-unexecuted) approval for the same `(action, entity_id)` is invalidated by a new propose call — see §7's "Re-proposing supersedes". The summary names the state it superseded from, so `approved` supersessions are greppable |
| `approval_granted` | `resolve_approval` | a `pending` approval is decided `approved` |
| `approval_declined` | `resolve_approval` | a `pending` approval is decided `declined` |
| `outreach_sent` | `execute_outreach_send` | `send_email` succeeds |
| `outreach_send_failed` | `execute_outreach_send` | `send_email` raises; the approval has already moved `executed -> failed` by the time this is logged |
| `outreach_send_declined` | `decline_outreach_send` | the domain-side bookkeeping for a declined approval — logged separately from `approval_declined`, which only records the decision itself |
| `policy_blocked` | `propose_outreach_send` | the policy engine blocks the job, logged *before* `PolicyBlocked` is raised, so the audit trail shows the block even though the caller sees an exception |

`approval_superseded` in particular is easy to miss if you only read `careeros/operations/outreach.py`:
it is emitted from inside `open_approval` in `careeros/operations/approvals.py`, not from the
outreach module, because superseding is generic to every action that goes through `open_approval`,
not specific to outreach.

## 7. The integrator's obligations

- **An approval is single-use against a sequential caller.** `execute_outreach_send` calls
  `mark_executed`, which moves the record out of `approved` to `executed`, before it calls
  `send_email`. Whether `send_email` then succeeds (record stays `executed`) or raises (record
  moves on to `failed` — see §6), the state is no longer `approved` either way, so a second,
  later `execute_*` call against the same id fails `require_state`'s check and raises
  `ApprovalNotGranted`. That refusal is what makes the record single-use in normal operation, but
  it is a sequential guarantee, not a concurrency one: `require_state` is a read-then-compare with
  no compare-and-swap, so it does not by itself exclude two processes calling `execute_*` against
  the same approval id at the same time — both can read `approved` before either writes past it.
  See §10 for that residual window. Do not run two executors against the same approval id
  concurrently; the integrator, not this layer, has to prevent that.
- **Only `approved` executes.** `execute_outreach_send` and `decline_outreach_send` both call
  `require_state` for the specific state they need (`approved`, `declined` respectively) and raise
  `ApprovalNotGranted` for anything else, including `pending`.
- **Re-proposing supersedes, including an approval you already hold.** Calling
  `propose_outreach_send` again for the same `job_id` and `person_id` finds every prior *open*
  approval for that action/entity pair — `pending` *and* `approved`-but-unexecuted, via
  `open_approval`'s scan of `list_by_state` — and moves each to `superseded` before opening the new
  one. A stale approval id from before a regeneration cannot later execute against content that has
  since been overwritten. **So do assume that approving, then re-proposing, invalidates the
  approval you were holding**: a subsequent `execute_*` against that id raises `ApprovalNotGranted`,
  and the fix is to decide the new approval, not to retry the old id.

  `approved`-but-unexecuted is a reachable state, not a theoretical one: `execute_*` performs its
  digest and recipient checks *before* `mark_executed`, deliberately, so those refusals leave the
  record retryable (see the `MissingRecipient` and `ArtifactChanged` notes in §8). Before this rule
  covered `approved`, such a record stayed decided-but-invisible — `list_pending` does not report
  it — and if a later propose happened to redraft byte-identical content, the stranded approval's
  digest still matched and executing it performed the action a *second* time. The digest binding
  alone does not close that case. Because `mark_executed` always precedes the external action,
  `approved`-and-not-executed is exactly the set of pre-attempt refusals, so nothing that was
  actually attempted is ever superseded.
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
| `WrongApprovalAction` | the approval id passed to `execute_apply` or `execute_outreach_send` names an approval opened for a *different* action (`.expected` and `.actual` name which) — checked before any state change, so a transposed id never consumes the approval it names | not retryable as-is; the caller passed the wrong approval id — find the right one, or re-propose the intended action |
| `ArtifactChanged` | the draft's digest (outreach) or one of the cover-letter/resume/profile digests (apply — see §11.3) no longer matches what was approved (`.path` names the file) | not retryable as-is; re-propose so review covers the actual content |
| `SendFailed` | `send_email` raised | by the time this propagates to the caller, `mark_failed` has already run — the approval is `failed`, not `executed` (see §6). Do not retry against the same approval id; a retry requires a new propose |
| `ResumeNotFound` | `propose_apply` found no resume in `resumes/versions/` — a **subclass of `EntityNotFound`**, so an existing catch of the parent still matches it | not retryable until a resume file exists (`careeros resume ingest`), then re-propose |
| `NoFillerAvailable` | `propose_apply` found no registered filler that can handle the job's URL (`.url` names it) — also a **subclass of `EntityNotFound`** | not retryable for that job as it stands; no filler exists yet for that ATS |
| `BoardSessionRequired` | `propose_apply` found the filler is `LinkedInFiller` and the user is not signed in to LinkedIn in the CareerOS browser profile (`.board` names the board) | run `careeros browser login --board linkedin`, then re-propose — the check re-runs on every propose call, including a regeneration |
| `FillIncomplete` | `execute_apply`'s filler returned `False` — the form did not submit | not retryable against the same approval id; `mark_executed` already consumed it and the approval is now `failed` (§11.4). Re-propose to get a fresh approval |
| `BrowserUnavailable` | Playwright is not installed, the CareerOS browser profile is locked (`.profile_busy` is `True`), or any other browser exception — raised by both `propose_apply` (the LinkedIn session check) and `execute_apply` (the fill itself) | `.profile_busy=True` is a whole-run condition — something else holds the browser profile lock; stopping the whole run rather than skipping one job is the caller's job, not this layer's (see `discover_and_apply_cmd.py`'s handling). Any other `BrowserUnavailable` raised by `execute_apply` already left the approval `failed` (`mark_executed` already ran); not retryable against that approval id — re-propose |

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
other pending decision behind it. `list_pending` is generic over `action` — it surfaces
`apply_to_job` approvals (§11) exactly the same way it surfaces `send_outreach` ones; there is
nothing apply-specific to call instead.

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
  `execute_apply` uses the identical `require_state` / `mark_executed` pattern, so the same
  concurrency window applies to it too. Apply additionally has two narrower, accepted
  duplicate-submission windows of its own, both in the same family as this one — a `BaseException`
  during browser teardown after a successful submit, and a failure in the post-submit job-save —
  recorded plainly in `DIVERGENCES.md` rather than here, since they are new-for-12b findings, not
  a restatement of this outreach-side limitation.
- **`queue_only` is the only shipped out-of-process callback.** There is no notification, no
  expiry, and no locking. It gives a safe, deny-by-default answer to a synchronous
  `request_approval` call, and that is all it does. A runtime that wants genuine asynchronous
  approval — propose now, a human approves from another machine hours later — has the durable
  `Approval` record it needs (poll it with `list_pending`), but none of the machinery to be told
  when a decision lands, to expire a stale pending approval, or to prevent two callers from acting
  on the same one at once.

## 11. Apply — `propose_apply` → `resolve_approval` → `execute_apply`

Phase 12b moved job apply through the same operations-layer treatment Phase 12a gave outreach:
`careeros/operations/apply.py` exposes `propose_apply` and `execute_apply`, both taking an
`AgentRuntime` and reading/writing only through `storage`, over the same durable `Approval` record
described in §6. Apply is drivable from an external agent session today, the same way outreach is
described in §1 — there is no longer any apply-specific caveat on that.

### 11.1 The sequence

- `propose_apply(runtime, job_id, *, model=None, action_label, summary=None, jd_text=None)` —
  selects a resume, checks policy, detects the ATS filler, drafts a cover letter, writes it to
  `applications/<job_id>/cover_letter.txt`, and opens a `pending` `Approval` with `action ==
  "apply_to_job"`. `summary` and `jd_text` are optional overrides for callers that word their own
  approval summary or supply their own view of the job description — `discover_and_apply_cmd.py`
  uses both; the interactive `apply` command uses neither.
- `resolve_approval(runtime, approval_id, result, *, action_label)` — the same function §5 and §7
  already describe for outreach; it is generic over `action` and works identically here.
- `execute_apply(runtime, approval_id, *, headless: bool, action_label)` — re-verifies three
  digests (§11.3), marks the approval `executed`, launches a browser, fills and submits the
  application, and on success advances the job to `stage="applied"`.

`action_label` is required and keyword-only on both `propose_apply` and `execute_apply`, exactly
as §5 now describes for the outreach functions — there is no default.

This is the worked example, adapted from `tests/test_operations_apply.py`, run as two separate
Python processes exactly like §5's outreach example. Both were run this way to verify this
document (see the task report for the exact commands); unlike `tests/test_agent_integration.py`
for outreach, there is no committed test that runs this specific pair of scripts as two literal
subprocesses.

**Process 1 — propose** (an agent session drafts and asks):

```python
from careeros.operations.apply import propose_apply
from careeros.runtime.factory import open_agent_runtime
from careeros.operations.approval_queue import queue_only

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-propose")
proposal = propose_apply(runtime, "acme-sre-abc1", action_label="agent")
print(proposal.approval_id, proposal.summary)
# -> surface proposal.summary and proposal.cover_letter to the human in conversation
```

This process can exit. Nothing about the pending decision lives in memory — it is all in
`approvals/<approval_id>.json` and `applications/<job_id>/cover_letter.txt`.

**Process 2 — resolve the human's answer, then execute** (same or a later agent session, once the
human has actually replied):

```python
from careeros.operations.approvals import resolve_approval
from careeros.operations.apply import execute_apply
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_agent_runtime
from careeros.operations.approval_queue import queue_only

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-execute")
resolve_approval(
    runtime, approval_id,
    ApprovalResult(approved=True, reason="user said yes in chat"),
    action_label="agent",
)
result = execute_apply(runtime, approval_id, headless=True, action_label="agent")
print(result.company, result.applied_at)
```

### 11.2 `headless`, and why this call is not quick

`execute_apply` always launches a real browser (`careeros.browser.driver.launch_browser`) and
drives it through a platform-specific filler (`GreenhouseFiller`, `LeverFiller`, `LinkedInFiller`,
or a generic fallback) that navigates to the job posting, fills the form, uploads the resume and
cover letter, and clicks submit. An agent runtime calling `execute_apply` must expect a
long-running, blocking call — this is not a quick storage read like `resolve_approval`. It is a
required keyword parameter with no default:

- `headless=True` is what `discover_and_apply_cmd.py` passes for unattended, scheduled runs — no
  window appears.
- `headless=False` is what the interactive `apply_cmd.py` passes — a real, visible Chrome window
  opens and drives itself. An agent session calling `execute_apply` with `headless=False` should
  expect that window to appear on whatever machine the process is running on, not just a delay.

### 11.3 The payload, and its three digests

Payload keys for `apply_to_job` (set by `propose_apply`). Unlike `send_outreach`'s payload (§6),
where `job_id` sits on the payload for audit only and is read by neither `execute_outreach_send`
nor `decline_outreach_send`, **every one of apply's eight payload keys is read at execute time** —
confirmed by grepping every `payload_value(approval, ...)` call in `execute_apply`; there is no
apply-side `decline_apply` function, so `resolve_approval`'s generic decline path is the only other
consumer, and it doesn't touch the payload at all:

| Payload key | What it is |
|---|---|
| `job_id` | the `Job` id the payload refers to |
| `job_url` | the URL the filler navigates to — bound at propose time, exactly like outreach's `subject` (§6), so editing the job's URL between approval and execution cannot change what gets navigated to |
| `cover_letter_storage_path` | the workspace-relative path the drafted cover letter was written to |
| `resume_storage_path` | the workspace-relative path of the resume chosen by `select_resume` |
| `filler_platform` | which registered filler (`GreenhouseFiller.platform`, etc.) was detected for `job_url` — `execute_apply` re-selects the filler by matching this string, and raises `MalformedApproval` if none matches, rather than silently picking a different filler than the one that was approved |
| `cover_letter_sha256` | `sha256` of the cover letter bytes on disk at proposal time |
| `resume_sha256` | `sha256` of the resume bytes on disk at proposal time |
| `profile_sha256` | `sha256` of `Profile.model_dump_json()` at proposal time |

**The digest set is cover letter, resume, and profile — three digests, not one.** Outreach binds
one digest (the draft). Apply binds three, because `filler.fill` reads three things from the
workspace at execute time: the cover letter and resume (uploaded as files) and the profile (used
to populate the form's name/email/other fields). `execute_apply` re-verifies all three against
what's on disk before consuming the approval:

```python
# careeros/operations/apply.py
cover_letter_bytes = runtime.storage.read(cl_storage_path)
if hashlib.sha256(cover_letter_bytes).hexdigest() != expected_cl_digest:
    raise ArtifactChanged(cl_storage_path)
if _digest_stored(runtime.storage, resume_storage_path) != expected_resume_digest:
    raise ArtifactChanged(resume_storage_path)

profile = Profile.load_or_empty(runtime.storage)
if _digest_text(profile.model_dump_json()) != expected_profile_digest:
    raise ArtifactChanged("profile/profile.json")
```

**Editing your profile between approving an application and executing it fails with
`ArtifactChanged`, by design.** This is not a bug to work around — it is the same guarantee
outreach's digest gives the draft, applied to the profile because the profile is transmitted
content here too: `filler.fill` reads it to populate the form. A profile edited after approval but
before execution would change what gets typed into the form without a new review, exactly as an
edited draft would change what gets sent. If you hit this, the fix is to re-propose (which
re-reads the current profile and binds a fresh digest to it), not to retry the same approval id.

`job_url` gets the same treatment as a direct value rather than a digest, for the same reason
`outreach`'s `subject` does (§6): it is computed once from the `Job` as read at propose time and
never re-derived from a fresh reload at execute time, so editing the job's URL between approval and
execution cannot change what gets navigated to. This is a deliberate correction to what an earlier
draft of the design spec called for — see the spec's own §6.3 correction note
(`docs/superpowers/specs/2026-09-22-phase12-second-runtime-design.md`). `docs/superpowers/DIVERGENCES.md`
records the sibling profile-digest binding described just above, not this one — it has no entry
for `job_url`.

### 11.4 Two behaviors that look like bugs but aren't

**`execute_apply` consumes the approval — `mark_executed` — before launching the browser.**
Exactly like outreach's `execute_outreach_send` (§6), this exists to defeat process death: a crash
between `mark_executed` and the browser call leaves the record `executed`, not `approved`, so a
retry cannot act on it a second time. The cost is the same one §6 names for outreach:

> finding `state == "executed"` on disk for an apply approval means the browser launch was
> attempted; it does not by itself mean the application was submitted or that the job's stage
> advanced. A crash between `mark_executed` and `filler.fill` returning leaves the approval
> `executed` and the job's `stage` still whatever it was before (`"saved"`, typically) — not
> `"applied"`. Check `Job.load(storage, job_id).stage` for the ground truth of whether the
> application actually went out; do not infer it from the approval's state alone, and never retry
> against that approval id — re-propose instead.

**A browser *teardown* failure after a successful submit is treated as success, not failure.** If
`filler.fill` returns `True` — the form was submitted — and then the browser context's teardown
(inside the `with launch_browser(...)` block, e.g. `context.close()`) raises, `execute_apply`:

- advances the job to `stage="applied"` (the same `_mark_applied` helper the ordinary success path
  calls),
- logs `job_applied` **and** a distinct `apply_teardown_failed` event, and
- **returns the `ApplyResult` normally rather than raising.**

Read the exception type name alone and this looks wrong — the browser call raised, so how is that
a success? The application genuinely went out (`filler.fill`'s return value is the source of
truth, and it said `True`); only the housekeeping after it failed. Advancing the stage anyway is
what stops `discover-and-apply`, which skips a job once its `applied_at` is set (equivalently, once
`_mark_applied` has run — stage and `applied_at` always advance together here), from re-proposing
and resubmitting the same application on its next scheduled run. `ApplyResult.teardown_failed` is
set `True` on exactly this path so a caller does not have to grep the activity log to notice: both
`apply_cmd` and `discover_and_apply_cmd` check it and print a yellow warning alongside the ordinary
success message, without changing their exit code. An integrator whose
error handling swallows every browser exception the same way (as `BrowserUnavailable`) will never
see this branch as an exception at all — it returns.

### 11.5 Activity events this flow emits

Same discipline as §6.1: an integrator reading `activity/*.jsonl` directly should not meet an
undocumented `event_type`. This table is what `careeros/operations/apply.py` itself emits, found by
grepping every `record_activity`/`new_event` call in that file, plus the shared approval-lifecycle
events from `careeros/operations/approvals.py` (§6.1):

| `event_type` | Emitted by | When |
|---|---|---|
| `policy_blocked` | `propose_apply` | the policy engine blocks the job, before any LLM call |
| `approval_requested` / `approval_superseded` / `approval_granted` / `approval_declined` | `careeros/operations/approvals.py` | same generic approval-lifecycle events §6.1 describes for outreach — apply uses the identical machinery |
| `job_applied` | `_mark_applied`, called from `execute_apply` | the application was submitted — on the ordinary success path, and on the teardown-failure-after-success path described in §11.4 |
| `apply_failed` | `_record_apply_failed`, called from `execute_apply` | a browser exception other than the teardown-after-success case marked the approval `failed` |
| `apply_incomplete` | `execute_apply` | `filler.fill` returned `False` — nothing conclusive happened; the approval is marked `failed` with detail `"FillIncomplete"` |
| `apply_teardown_failed` | `execute_apply` | the teardown-failure-after-success case in §11.4 — logged alongside `job_applied`, not instead of it |

**`apply_error` is not in this list because `apply.py` never emits it.** It is emitted by
`discover_and_apply_cmd.py`'s own exception handling around its calls to `propose_apply` and
`execute_apply` — caller-side bookkeeping for a refusal the operations layer itself already raised
but didn't log (e.g. `ArtifactChanged`, `ApprovalNotGranted`, a generic `BrowserUnavailable`),
exactly the same relationship §6.1 describes between `outreach_send_declined` and
`approval_declined`. `discover_and_apply_cmd.py` also logs several events entirely of its own —
`cover_letter_failed`, `no_filler_available`, `session_unauthorized`, `source_unavailable`,
`posting_unusable`, `job_added`, `job_merged`, `apply_outcome_unknown` — that belong to that
command's unattended-discovery loop, not to the `apply_to_job` approval flow this section
documents; read that module directly if you need its complete vocabulary.
`apply_outcome_unknown` is logged when `careeros.operations.approvals.has_executed_approval`
finds an `executed` `apply_to_job` approval for a job whose `applied_at` is still unset, and the
job is skipped rather than re-proposed — see `docs/superpowers/DIVERGENCES.md` for why that state
is reachable and why skipping, not resubmitting, is the safe choice.
