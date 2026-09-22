# Phase 12 — Second Real AgentRuntime — Design Spec

**Date:** 2026-09-22
**Status:** Approved for implementation planning
**Author:** Kaushal + Claude

---

## 1. What This Is

The roadmap frames Phase 12 as "build a second real `AgentRuntime`." Exploration
found that framing is wrong about where the gap is.

`ClaudeCodeRuntime` already exists (`careeros/runtime/claude_code.py`), takes an
injected `approval_callback`, stamps `agent_runtime`/`session_id` onto every logged
event, and `open_claude_code_runtime` already bootstraps it through the same
`open_workspace` path the CLI uses. It satisfies the `AgentRuntime` Protocol
completely. Nothing about it needs to be built.

What is missing is anything for it to drive. Every business flow in CareerOS lives
inside a Typer command body that

1. hardcodes `open_local_runtime(...)`, so the seam only ever holds a `LocalRuntime`,
2. interleaves `rprint` / `Prompt.ask` / `typer.Exit` with domain logic, and
3. returns nothing — outcomes are printed, not returned.

`outreach send` (`careeros/cli/outreach_cmd.py:63-205`) is 140 lines mixing the policy
check, LLM drafting, a Rich regenerate loop, the approval gate, and the SMTP send.
An external runtime cannot reach that logic at any granularity. So the phase's exit
condition — an agent session outside the CLI completing an approval-gated action — is
not blocked on a runtime. It is blocked on the absence of a **runtime-agnostic
operations layer**, and on the fact that CareerOS approval cannot survive a process
boundary.

This phase builds both, for the two approval-gated external actions (outreach send,
job apply), and documents the resulting integration contract.

**Deliberately out of scope** (deferred, not forgotten):

- Extracting any flow other than outreach send and job apply. `browse`, `research`,
  `resume`, and `onboard` stay CLI-only. They have no approval gate and no external
  irreversible action, so they do not exercise anything this phase is proving.
- A read/query API for an agent to discover job and person IDs. An agent reads
  `jobs/`, `people/`, and `profile/` through `runtime.storage` or `read_workspace`,
  which the Protocol already supports. A curated read surface overlaps the never-built
  `careeros jobs` / `careeros history` commands (`DIVERGENCES.md`) and belongs with
  those, not here.
- Converging the five duplicated `_get_storage` helpers across the CLI onto the new
  factory discovery function. Only the two in-scope commands are rewired; see §10.
- MCP, HTTP, or any network-facing transport. The integration is a documented Python
  entry point invoked from a shell.
- Asynchronous multi-party approval (one agent proposes, a different human approves
  hours later on another machine). The record this phase writes would support it, but
  nothing here implements notification, expiry, or locking.

---

## 2. The Process-Boundary Problem

The target client is a **shell-driven agent** — Claude Code, invoking Python through
Bash. Each invocation is a fresh, short-lived process.

This breaks `request_approval` as a mid-flow call. A synchronous callback cannot
round-trip to a human, because the human is reachable only through the agent's
conversation channel, which lives in the *parent* process that spawned the Python.
The callback's only honest behavior is to record the proposal and stop.

So an approval-gated flow must split at the gate, and the artifact that was approved
must survive the split. That second requirement is not a nicety. The naive fix —
"abort at the gate, then call the whole operation again with the decision" —
regenerates the LLM draft on the second call, which means the user approves cover
letter A and the resumed process submits cover letter B. Avoiding that class of bug is
the reason for the shape below.

**The shape:** each flow becomes two functions over a persisted approval record.

```text
propose_*(runtime, ...) -> Proposal        # load, validate, policy-check, draft,
                                           # persist artifacts, write pending Approval
resolve_approval(runtime, id, result)      # record the human's decision
execute_*(runtime, approval_id) -> Result  # perform ONLY the external action
```

Every caller runs the same three steps and differs only in where step 2 happens:

| Caller | Runtime | Step 2 |
|---|---|---|
| `careeros outreach send`, `careeros apply` | `LocalRuntime` | `runtime.request_approval` → Rich `Confirm`, same process, unchanged UX |
| `careeros discover-and-apply` | `AutomationRuntime` | `runtime.request_approval` → auto-approve per policy, same process |
| Shell-driven agent | `ClaudeCodeRuntime` | process exits with the proposal; agent asks its human; a **new** process resolves and executes |

`request_approval` stays exactly as it is, and the Protocol gains no new methods. It
gains exactly one declaration: `agent_runtime_name: str`, which all three runtimes
already define as a class attribute and which `record_activity`'s documented
"stamps the runtime's identity" guarantee already depends on. `resolve_approval` needs
to read it to record *who* decided, so the Protocol should stop omitting it.

The two-call split is what makes that seam usable *across* a process boundary, and it
is what lets one operation be driven by three different runtimes without the operation
knowing which one it has.

---

## 3. Workspace Layout

One new directory, created by one new migration following `m002_applications.py`:

```python
# careeros/workspace/migrations/m003_approvals.py
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("003_approvals")
def m003_approvals(storage: StorageProvider) -> None:
    if not storage.exists("approvals/.keep"):
        storage.write("approvals/.keep", b"")
```

Registered by appending an import to `careeros/workspace/migrations/__init__.py` below
the existing two. `open_workspace` already runs pending migrations and rewrites the
manifest, so existing workspaces acquire `approvals/` on next open with no user action.
`SUPPORTED_SCHEMA_VERSION` stays `"1"` — this adds a directory, it does not change the
meaning of any existing file.

---

## 4. The `Approval` Record

`Approval` goes in `careeros/core/models.py` beside the other Pydantic models, with the
same `save`/`load` shape they all use.

```python
class Approval(BaseModel):
    id: str
    action: str                      # "send_outreach" | "apply_to_job"
    summary: str                     # the same text an ActionProposal would carry
    state: str = "pending"           # see the state machine below
    entity_type: str | None = None
    entity_id: str | None = None
    payload: dict[str, str] = Field(default_factory=dict)
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None    # the deciding runtime's agent_runtime_name
    reason: str | None = None        # from ApprovalResult.reason
    executed_at: str | None = None
    detail: str | None = None        # failure detail when state == "failed"

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("approvals/" + self.id + ".json",
                             self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, approval_id: str) -> "Approval":
        path = "approvals/" + approval_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Approval " + repr(approval_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())
```

### 4.1 State machine

```text
pending ──approve──> approved ──execute──> executed
   │                    │
   │                    └──execute fails──> failed
   ├──decline──> declined
   └──superseded by a new propose──> superseded
```

`executed`, `declined`, `failed`, and `superseded` are all terminal. `execute_*`
requires exactly `approved`; anything else raises `ApprovalNotGranted` carrying the
state it actually found. This is what makes double-execution impossible rather than
merely unlikely — the concern `outreach_cmd.py:132` already warns about in-process
("an outreach email was already sent on …"), now held across processes.

### 4.2 `payload` is deliberately `dict[str, str]`

The payload carries only what `execute_*` needs to find its inputs — identifiers and
workspace-relative paths, all strings. Keeping it an open string map is what makes
`approvals/<id>.json` a *contract* a third runtime can read without importing CareerOS.
Required keys per action are fixed by §6 and validated by each `execute_*`, which
raises `MalformedApproval` on a missing key rather than `KeyError`.

Two rules the payload must obey:

- **No drafted content.** The outreach draft lives in `OutreachMessage.draft_text`; the
  cover letter lives at `applications/<job_id>/cover_letter.txt`. The payload holds
  paths and IDs, never a copy, so there is exactly one source of truth and nothing to
  drift.
- **No absolute paths.** The workspace is portable by design (Phase 1 export/import),
  so an absolute path recorded on one machine is wrong on another. Paths are stored
  workspace-relative and passed through `storage.resolve()` at execute time.

### 4.3 Binding the approval to the artifacts

Two independent mechanisms, because they close different holes:

- **Superseding.** `propose_*` marks any existing `pending` approval with the same
  `(action, entity_id)` as `superseded` before writing its own. This is what makes
  "regenerate" safe: re-proposing invalidates the older pending approval, so a stale
  approval ID cannot later execute against a freshly overwritten cover letter.
- **Digest binding.** The payload records the sha256 of every artifact that will be
  transmitted — `draft_sha256` for outreach, `cover_letter_sha256` and `resume_sha256`
  for apply. `execute_*` recomputes and raises `ArtifactChanged` on mismatch. This
  catches what superseding cannot: an out-of-band edit rather than a re-propose. A
  hand-edited `outreach/<id>.json`, or `careeros resume variant --job <id>` run from
  another terminal between propose and execute, both leave a pending approval pointing
  at content it never described.

Digesting stored bytes is already the idiom here (`resume_select._digest`,
`ResumeVariant.pdf_sha256`), so this reuses an established pattern rather than
inventing one.

### 4.4 IDs

`careeros/core/ids.py` gains:

```python
def make_approval_id(action: str, entity_id: str | None) -> str:
    parts = [p for p in [_slugify(action)[:20], _slugify(entity_id or "")[:30]] if p]
    return "-".join(parts) + "-" + secrets.token_hex(3)
```

Same slugify-then-random-suffix construction as `make_compensation_id`. The random
suffix matters: two proposals for the same job must not collide, because the older one
has to remain readable in its `superseded` state for the audit trail.

Both components are slugified before becoming a path segment, for the same reason
`outreach_cmd._message_id` does it (`outreach_cmd.py:34-41`) — an arbitrary `entity_id`
must never reach `storage._resolve()` as a raw path component.

---

## 5. Approval Lifecycle — `careeros/operations/approvals.py`

The `Approval` *model* lives in `careeros/core/models.py` with every other model. Its
lifecycle functions live in the operations layer (§6) rather than in `core/`, for two
reasons: they take an `AgentRuntime`, and nothing in `core/` does — `PolicyEngine` takes
a config, `job_store` takes a storage — and they raise the error types defined in
`careeros/operations/errors.py`, which a module in `core/` importing from `operations/`
would invert.

```python
def open_approval(runtime, action, summary, payload, *,
                  entity_type=None, entity_id=None, action_label) -> Approval
def resolve_approval(runtime, approval_id, result: ApprovalResult, *, action_label) -> Approval
def mark_executed(runtime, approval_id) -> Approval
def mark_failed(runtime, approval_id, detail) -> Approval
def require_state(storage, approval_id, expected) -> Approval
def payload_value(approval, key) -> str
def list_pending(storage) -> list[Approval]
```

`mark_executed` and `mark_failed` take no `action_label` because they log nothing — the
calling flow logs its own domain event (`outreach_sent`, `outreach_send_failed`) and
these only advance the record. `require_state` is the single state guard both
`execute_*` and `decline_*` go through, raising `ApprovalNotGranted` with the state it
actually found; `payload_value` raises `MalformedApproval` rather than `KeyError` on a
missing or empty key.

- `open_approval` supersedes prior pendings for the same `(action, entity_id)`, writes
  the new record, and logs `approval_requested`.
- `resolve_approval` refuses anything not in `pending` (raises `ApprovalNotGranted`),
  stamps `decided_at` and `decided_by=runtime.agent_runtime_name`, copies
  `ApprovalResult.reason` onto the record, and logs `approval_granted` or
  `approval_declined` **with `ActivityEvent.reason` populated from that same reason**.
  This is the first code in the project to populate that field, closing a
  `DIVERGENCES.md` row as a side effect of needing it here.
- `list_pending` enumerates `storage.list("approvals/")`, skips `.keep`, and is how an
  agent discovers what is awaiting a decision after a context loss.

Every one of these logs through `runtime.record_activity`, so the runtime stamps
identity and the audit trail shows which runtime decided what.

`action_label` is threaded through because the activity log's `action` field names the
surface that drove the flow — `"apply"` from the CLI, `"discover-and-apply"` from the
scheduled run, `"agent"` from an external session. `agent_runtime` already distinguishes
the runtime; `action` distinguishes the entry point, and today's code sets it
per-command (`apply_cmd.py:82` vs `discover_and_apply_cmd.py:231`). Preserving that
distinction is why it is a parameter and not a constant.

---

## 6. The Operations Layer — `careeros/operations/`

```text
careeros/operations/
├── __init__.py
├── errors.py          ← the exception hierarchy below
├── approvals.py       ← the §5 lifecycle: open / resolve / mark / load / list
├── approval_queue.py  ← queue_only, the shipped out-of-process callback
├── outreach.py        ← propose / execute / decline_outreach_send
└── apply.py           ← propose_apply, execute_apply
```

**Hard constraint:** nothing under `careeros/operations/` may import `rich`, `typer`,
or `click`, and no function in it may prompt, print, or exit. Presentation and
interaction are the caller's job. This is enforced by a test (§9), not by convention,
because the whole point of the layer is that a non-CLI caller can use it.

### 6.1 Errors — `careeros/operations/errors.py`

```python
class OperationError(Exception): ...           # base
class EntityNotFound(OperationError): ...      # job / person / company missing
class PolicyBlocked(OperationError): rule      # already logged before raising
class DraftFailed(OperationError): ...         # LLM returned nothing
class MissingRecipient(OperationError): ...    # person has no email on file
class BoardSessionRequired(OperationError): board
class ApprovalNotGranted(OperationError): state
class MalformedApproval(OperationError): key
class ArtifactChanged(OperationError): path
class SendFailed(OperationError): ...          # SMTP raised
class FillIncomplete(OperationError): ...      # filler returned False
class BrowserUnavailable(OperationError): ...  # Playwright missing / profile busy
```

Typed exceptions rather than a result object with an error string: the CLI needs to map
each to its existing red message and exit code, and an agent needs to distinguish "fix
your config and retry" from "this job is blocked forever." A result-with-error would
make both callers string-match.

`PolicyBlocked` is raised **after** the operation logs the `policy_blocked` activity
event, so all three callers produce an identical audit trail. Today the CLI and
`discover-and-apply` each log their own, which is exactly the drift this removes.

### 6.2 Outreach — `careeros/operations/outreach.py`

```python
@dataclass(frozen=True)
class OutreachProposal:
    approval_id: str
    message_id: str
    draft_text: str
    recipient_name: str
    recipient_email: str | None
    subject: str
    already_sent_at: str | None   # prior send; caller must surface this

@dataclass(frozen=True)
class OutreachResult:
    message_id: str
    recipient_name: str
    sent_at: str
```

`propose_outreach_send(runtime, job_id, person_id, *, model=None, action_label)`:
loads job/person/company (→ `EntityNotFound`), runs the policy check (logs, then →
`PolicyBlocked`), loads profile and goals, calls `generate_outreach_message` (→
`DraftFailed`), reads any prior `OutreachMessage` to carry `referral_state` forward and
detect a prior send, saves the `OutreachMessage`, logs `outreach_drafted`, and calls
`open_approval` with
`payload = {message_id, job_id, person_id, draft_sha256}`. Returns the proposal.

Prior-send detection reads `existing.sent_at`, not `existing.send_state`, and the
re-drafted message carries that `sent_at` forward while its `send_state` returns to
`"drafted"`. This matters because propose now writes the message *before* the review
loop rather than after it: keying off `send_state` would make the first propose erase
the evidence of an earlier send, and the "you already sent this" warning would vanish
on regeneration. `sent_at` is only ever set by a real send, so its presence is the
durable fact.

Regeneration is just calling this again — the new call supersedes the prior pending
approval and overwrites the draft. No separate entry point, no loop inside the
operation.

`execute_outreach_send(runtime, approval_id)`: loads the approval, requires `approved`,
loads the `OutreachMessage` named in the payload, requires the person to have an email
(→ `MissingRecipient`), sends via `send_email`, and on success sets `send_state="sent"`
/ `sent_at`, marks the approval `executed`, and logs `outreach_sent`. On `send_email`
raising: sets `send_state="failed"`, marks the approval `failed` with the exception type
as detail, logs `outreach_send_failed` with `status="failed"`, and raises `SendFailed`.

`decline_outreach_send(runtime, approval_id)` is the symmetric counterpart: it sets
`send_state="declined"` and logs `outreach_send_declined`. It lives in this module
rather than inside `resolve_approval` because `approvals.py` is deliberately
action-agnostic — it knows about approval states and nothing about outreach messages,
job applications, or whatever a later flow adds. Dispatching per-action side effects
from inside it would make the generic lifecycle a registry of every flow. Callers
branch on the decision they already hold: `execute_*` on approve, `decline_*` on
decline. Apply needs no counterpart, because declining an application changes no domain
state beyond the approval record itself, which `resolve_approval` has already written.

The draft text is never re-generated and never copied into the approval: it is read back
from `OutreachMessage.draft_text`. The bytes that were reviewed are the bytes that send.

### 6.3 Apply — `careeros/operations/apply.py`

```python
@dataclass(frozen=True)
class ApplyProposal:
    approval_id: str
    job_id: str
    company: str
    title: str
    cover_letter: str
    cover_letter_storage_path: str
    resume: ResumeChoice           # carries tailored / stale_master / variant
    filler_platform: str

@dataclass(frozen=True)
class ApplyResult:
    job_id: str
    company: str
    title: str
    applied_at: str
```

`propose_apply(runtime, job_id, *, model=None, action_label)` preserves the current
ordering in `apply_cmd.py:59-178` exactly, because that ordering is deliberate and
commented — resume lookup before profile load so failure is fast, policy check before
any LLM spend:

1. load job (→ `EntityNotFound`); require `job.url` (→ `EntityNotFound` with a message
   naming `careeros job update`)
2. `select_resume` (→ `EntityNotFound` if no resume at all)
3. policy check — log, then → `PolicyBlocked`
4. load profile / skills / goals
5. `generate_cover_letter` over `(job.description or "")[:4000]` (→ `DraftFailed`)
6. write `applications/<job_id>/cover_letter.txt`
7. detect the filler (→ `EntityNotFound` if none can handle the URL)
8. if the filler is `LinkedInFiller`, check the board session and raise
   `BoardSessionRequired("linkedin")` if absent — the check stays *before* the approval,
   as it is today
9. `open_approval` with `payload = {job_id, cover_letter_storage_path,
   resume_storage_path, filler_platform, cover_letter_sha256, resume_sha256}`

Step 8 is the one behavioral relocation: `require_board_session`
(`careeros/cli/preflight.py:10`) prints and raises `typer.Exit`, which an operation must
not do. It keeps its three other callers (`browse_cmd`, `research_cmd`) unchanged; only
apply's use of it becomes the raised error, and `apply_cmd` catches it and prints the
identical message. `BrowserProfileBusy` during that check surfaces as
`BrowserUnavailable`.

The proposal returns `ResumeChoice` whole rather than a rendered string, so the tailored
/ `stale_master` / missing-sidecar messaging at `apply_cmd.py:105-127` stays presentation
and each caller words it for its own audience. That messaging carries real semantics —
whether the upload is tailored and whether its provenance is trustworthy — so it must
cross the boundary as structured data, not prose.

`execute_apply(runtime, approval_id, *, headless: bool)`: loads the approval, requires
`approved`, validates payload keys, re-verifies both digests (→ `ArtifactChanged`),
resolves the two workspace paths to absolute, reloads the job and profile, launches the
browser at the requested headlessness, and fills. On success: updates the job to
`stage="applied"` with `applied_at`, marks the approval `executed`, logs `job_applied`.
On `filler.fill` returning `False`: marks the approval `failed`, leaves the stage
untouched, raises `FillIncomplete`. `ImportError` and `BrowserProfileBusy` become
`BrowserUnavailable`; any other browser exception marks the approval `failed` and raises
`BrowserUnavailable` with the original message — preserving today's guarantee that a
browser error never advances the stage.

`headless` is a parameter rather than a constant because the CLI runs headful
(`apply_cmd.py:185`) and `discover-and-apply` runs headless
(`discover_and_apply_cmd.py:300`). It is the only behavioral difference between those two
call sites, which is precisely why they can now share one implementation.

---

## 7. Callers

### 7.1 `careeros/cli/apply_cmd.py` and `careeros/cli/outreach_cmd.py`

Both command bodies become: resolve storage → open runtime → `propose_*` → present the
draft and run the existing regenerate loop (re-calling `propose_*` on "r") →
`runtime.request_approval` → `resolve_approval` → `execute_*` → print the result.
Wrapped in one `except OperationError` block that maps each error type to the red
message and exit code it produces today. **No user-visible behavior changes** —
identical prompts, identical messages, identical exit codes.

The regenerate loops stay in the command bodies. Phase 5 deliberately left non-approval
prompts as direct `Prompt.ask` calls (§7 of that spec), and this keeps that decision:
the loop is a presentation concern, and an agent makes its own choice about whether to
re-propose.

### 7.2 `careeros/cli/discover_and_apply_cmd.py`

Lines ~205-320 are a near-duplicate of the apply flow — its own `select_resume`, policy
check, `generate_cover_letter`, filler detection, `request_approval`, `launch_browser`,
and stage update. It is rewired onto `propose_apply` / `resolve_approval` /
`execute_apply(headless=True)` with `action_label="discover-and-apply"`, deleting the
duplicate.

This is in scope because leaving it would mean shipping a *third* copy of apply
semantics that can silently drift from the extracted one, and because routing the
scheduled path through the same operation is what proves the layer is genuinely
runtime-agnostic: the same code then runs under `LocalRuntime`, `AutomationRuntime`, and
`ClaudeCodeRuntime`. Its per-job `try/except` becomes `except OperationError`, which
also makes the `no_filler_available` and policy-block events come from one place instead
of two.

Its behavior is preserved, including the `max_auto_applies_per_run` cap, the
`auto_apply_min_score` threshold, and the `already_applied` skip — all of which live
above the extracted section and are untouched.

### 7.3 The shell-driven agent

```python
import json
from careeros.operations.approval_queue import queue_only
from careeros.operations.approvals import resolve_approval
from careeros.operations.outreach import execute_outreach_send, propose_outreach_send
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_agent_runtime

# Process 1 — propose
rt = open_agent_runtime(approval_callback=queue_only)
p = propose_outreach_send(rt, "acme-eng-aaa1", "acme-jane-doe", action_label="agent")
print(json.dumps({"approval_id": p.approval_id, "draft": p.draft_text,
                  "to": p.recipient_email, "already_sent_at": p.already_sent_at}))

# ... agent shows the draft to its human and gets a decision ...

# Process 2 — resolve and execute
rt = open_agent_runtime(approval_callback=queue_only)
resolve_approval(rt, approval_id,
                 ApprovalResult(approved=True, reason="user said yes in chat"),
                 action_label="agent")
r = execute_outreach_send(rt, approval_id)
```

---

## 8. Runtime Bootstrap — `careeros/runtime/factory.py`

Two additions.

**`resolve_storage(workspace_path: str | None) -> LocalFilesystemStorage`** implements
the three-tier discovery the master spec §3 specified and the project never fully built:
explicit path → `CAREEROS_WORKSPACE` environment variable → `GlobalConfig`. It raises
`WorkspaceNotConfigured` instead of printing and exiting.

The env-var tier is a `DIVERGENCES.md` row, and it lands here because it is the tier a
shell-driven agent actually needs: the agent exports `CAREEROS_WORKSPACE` once and every
subprocess it spawns finds the same workspace without threading a `--workspace` flag
through every call.

**`open_agent_runtime(*, workspace_path=None, approval_callback, session_id=None) -> ClaudeCodeRuntime`**
is the roadmap's "workspace discovery convenience": path or discovery → storage →
`open_workspace` → runtime, in one call. `approval_callback` stays required and
keyword-only, preserving the Phase 5 constraint that construction fails loudly rather
than silently auto-denying.

`open_claude_code_runtime` is kept as-is for callers that build their own storage.

**`careeros/operations/approval_queue.py`** ships `queue_only`, the batteries-included
callback for the shell case. It returns
`ApprovalResult(approved=False, reason="deferred to out-of-process approval")`. It
exists because a shell-driven runtime's decision path goes through `resolve_approval`,
never through the callback — so the callback's job is to be a safe, deny-by-default
answer for any *other* `request_approval` call the runtime might encounter. Shipping it
stops every integrator from inventing their own, and the deny-by-default posture is
deliberate.

---

## 9. Testing

New:

- `tests/test_approvals.py` — the `Approval` model and lifecycle: each legal transition,
  every illegal one raising `ApprovalNotGranted` with the found state, superseding on
  re-propose, `list_pending` skipping `.keep`, and `ActivityEvent.reason` populated from
  `ApprovalResult.reason`.
- `tests/test_operations_outreach.py` — propose: entity missing, policy block (event
  logged *and* raised), draft failure, `referral_state` carried forward,
  `already_sent_at` surfaced. Execute: refuses every non-`approved` state, missing
  recipient, SMTP failure marking both the message and the approval failed, success
  marking both sent and executed.
- `tests/test_operations_apply.py` — propose: no URL, no resume, policy block, no
  filler, `BoardSessionRequired`, draft failure, digests recorded. Execute: refuses
  non-`approved`, `ArtifactChanged` when the cover letter or resume PDF changes after
  approval, `FillIncomplete` leaving the stage untouched, browser errors leaving the
  stage untouched, success updating the stage.
- `tests/test_operations_purity.py` — scans `careeros/operations/` for imports of
  `rich`, `typer`, and `click`, and for `print(`. The constraint is the layer's reason to
  exist, so it is asserted rather than trusted.
- `tests/test_agent_integration.py` — the regression guard for the exit condition: two
  separate `subprocess` invocations against one `tmp_path` workspace discovered via
  `CAREEROS_WORKSPACE`, the first proposing and the second resolving and executing, with
  `send_email` patched at the boundary. Asserts the approval reached `executed`, and that
  the activity log carries `agent_runtime="claude_code"` on the `approval_granted` and
  `outreach_sent` events.
- `tests/test_migrations.py` — extended for `003_approvals`, including that an existing
  workspace acquires `approvals/` on `open_workspace`.

Updated: `test_apply_cmd.py`, `test_outreach_cmd.py`, and
`test_discover_and_apply_cmd.py` keep asserting the same user-visible output and exit
codes, with mocks moving from the skills to the operations. Their job in this phase is to
prove the refactor changed nothing observable.

---

## 10. Recorded Deferrals

Added to `DIVERGENCES.md` as deliberate, so a later review finds a decision rather than
drift:

- `browse_cmd`, `research_cmd`, `onboard`, and `resume_cmd` keep their private
  `_get_storage` helpers, now duplicating logic that also lives in
  `factory.resolve_storage`. Converging them is mechanical but touches four command
  modules and their test suites, none of which this phase otherwise opens.
- `queue_only` is the only shipped approval callback for out-of-process use. A runtime
  wanting genuine asynchronous approval (propose now, approve on another machine later)
  has the record it needs but no notification, expiry, or locking.

Rows this phase **closes**, each because the work needed it rather than as a side quest:
the `approvals/` queue for an external runtime; `ActivityEvent.reason` never being
populated; and the `CAREEROS_WORKSPACE` discovery tier.

---

## 11. Documentation

`docs/agent-integration.md` — the roadmap's third bullet, written so a third runtime can
be built without reading the source:

- the `AgentRuntime` Protocol, member by member, and what each guarantees
- what `approval_callback` must do, and when it is and is not consulted
- what `record_activity` guarantees: append-only, runtime-stamped identity, and that it
  overwrites `agent_runtime`/`session_id` unconditionally
- the `approvals/<id>.json` schema, its state machine, and the payload keys per action
- the propose → resolve → execute sequence, with the worked two-process example
- workspace discovery precedence
- the digest and superseding rules, stated as the integrator's obligations

`README.md` gains an agent-integration section pointing at it; `ROADMAP.md` marks Phase
12 shipped with the caveats from §10.

---

## 12. Staging

This is more surface than one review can hold well, so it should be planned as two
stages in the manner of 9a/9b/9c and 11a/11b — each independently shippable, each
ending in a working tree:

- **12a — the mechanism, proved on outreach.** The migration, the `Approval` model,
  `careeros/operations/` with `errors.py`, `approvals.py`, `approval_queue.py`, and
  `outreach.py`, the factory additions, the `outreach send` rewire, and
  `docs/agent-integration.md`. The phase exit condition is reachable at the end of 12a,
  since it is an outreach send.
- **12b — generalizing to apply.** `operations/apply.py`, the `apply_cmd` rewire, and
  the `discover-and-apply` de-duplication. This is what demonstrates the layer fits a
  second, much less pure flow — browser, file upload, resume provenance — rather than
  having been shaped around one.

Splitting here also front-loads the risk: if the propose/execute shape is wrong, 12a
surfaces it on the cheaper flow.

---

## Global Constraints

Inherited unchanged from the Phase 5 spec, plus three specific to this phase:

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture,
  or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`,
  or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`,
  `careeros/sources/`, `careeros/browser/`, `careeros/runtime/`, or the new
  `careeros/operations/`
- Activity logs are append-only; no event is ever edited or deleted
- `atomic_write` must use write-to-temp-then-rename
- Prompts and activity summaries are built by string concatenation only — no `.format()`
  or f-strings with user data
- **No `rich`, `typer`, or `click` import anywhere under `careeros/operations/`, and no
  printing, prompting, or process exit from it**
- **`execute_*` performs the external action and nothing else.** It never drafts, never
  calls an LLM, and never re-derives an artifact. Everything it transmits was written
  before the approval and is digest-verified against it.
- **An `Approval` is single-use.** Only `approved` may execute, and executing moves it to
  a terminal state, so no approval can authorize two external actions.

---

## Exit Condition

The roadmap's condition is that an agent session outside the CLI — not a test — opens a
real workspace, reads and writes it, and completes one approval-gated action end to end.

Concretely: a Claude Code session, driving Python through Bash against the user's real
workspace discovered via `CAREEROS_WORKSPACE`, proposes an outreach send, surfaces the
draft in conversation, records the user's actual decision through `resolve_approval`, and
executes the send in a second, separate process. Verified by `approvals/<id>.json`
reaching `state: "executed"` and the activity log showing `approval_granted` and
`outreach_sent` with `agent_runtime: "claude_code"`, a populated `reason`, and two
distinct `session_id`s from the same run.

The send must be real — the user's own email address through their configured SMTP —
because a patched mailer is what the automated test in §9 already covers, and the point
of this verification is that the external action actually happened. This requires the
`CAREEROS_SMTP_*` variables to be configured, and a `Person` record carrying the user's
own address as the recipient.
