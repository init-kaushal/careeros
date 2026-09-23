# Phase 13a — Follow-Up Cadence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry a referral relationship from its first message through a tracked follow-up cadence, so the user does not hold the state — a scheduled command drafts due follow-ups into a queue of pending approvals, and a review command drains it.

**Architecture:** A third flow on the propose/execute machinery Phases 12a/12b built. `careeros outreach follow-up` runs on cron with a queue-only approval callback, so it drafts and records pending `Approval`s but sends nothing. `careeros outreach review` walks those pending approvals interactively and sends on approval. Cadence state lives on the existing `OutreachMessage` record; the schedule comes from a new `config/cadence_policy.json` that fails loudly when absent.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer + Rich (CLI only), litellm, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-phase13-outreach-expansion-design.md` — read §1 (what is declined and why), §4 (shared groundwork), §5 (this stage), and Global Constraints.

## Global Constraints

Copied from the spec. Every task's requirements implicitly include these.

- No credentials, tokens, or API keys in any workspace file, activity log, prompt string, or test fixture.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, `careeros/runtime/`, or `careeros/operations/`.
- Activity logs are append-only; `atomic_write` for writes.
- Strings built by `+` concatenation only — **no `.format()`, no f-strings with user data.**
- **No `rich`, `typer`, or `click` under `careeros/operations/`, and no printing, prompting, or process exit from it.** The AST guard in `tests/test_operations_purity.py` parses only that package, so anything the layer *calls* must also be print-free — that is how a `rich` print reached the layer in Phase 12b.
- **`execute_*` performs the external action and nothing else,** consumes the approval **before** acting, and digest-verifies everything it transmits.
- **An `Approval` is single-use.**
- **The cadence must be incapable of sending anything without a per-item approval.** No path in this stage auto-approves.
- **No test may send a real email or launch a real browser.** The autouse guards in `tests/conftest.py` cover both; per-test patches override them.
- Run `.venv/bin/python -m pytest -q` before each commit. Baseline: **862 passed, 7 skipped**.

## File Structure

**Create:** `careeros/operations/_shared.py`; `careeros/operations/follow_up.py`; `careeros/skills/follow_up_draft.py`; `tests/test_operations_follow_up.py`; `tests/test_follow_up_draft.py`; `tests/test_cadence_policy.py`.

**Modify:** `careeros/core/models.py` (cadence fields, `referral_state` Literal, `CadencePolicy`); `careeros/operations/errors.py` (three refusal types); `careeros/operations/approvals.py`, `outreach.py`, `apply.py` (point at `_shared`); `careeros/operations/outreach.py` (touch tracking in execute); `careeros/cli/outreach_cmd.py` (three new subcommands); `tests/test_operations_outreach.py`, `tests/test_operations_apply.py`, `tests/test_approvals.py`, `tests/test_outreach_cmd.py`, `tests/test_models.py`; `docs/agent-integration.md`, `docs/superpowers/DIVERGENCES.md`, `README.md`, `ROADMAP.md`.

**Out of scope for 13a** (13b's plan introduces them): `careeros/browser/connect/`, `careeros/operations/connect.py`, `careeros/skills/connection_note.py`, `ConnectionNotSent`, `max_connection_requests_per_run`, and the `careeros outreach connect` command. Do not add them.

---

## Part A — Shared groundwork (Tasks 1–2)

These land first: Task 2's touch tracking edits `execute_outreach_send`, and Task 1 moves the helper it uses.

### Task 1: Extract the duplicated operations helpers, and constrain `referral_state`

**Files:**
- Create: `careeros/operations/_shared.py`
- Modify: `careeros/operations/approvals.py`, `careeros/operations/outreach.py`, `careeros/operations/apply.py`, `careeros/core/models.py`
- Test: `tests/test_models.py`, plus the existing digest assertions in `tests/test_operations_outreach.py` and `tests/test_operations_apply.py`

**Interfaces:**
- Produces: `careeros.operations._shared.now() -> str`, `digest_text(text) -> str`, `digest_stored(storage, path) -> str`; `OutreachMessage.referral_state` typed as a `Literal`.

- [ ] **Step 1: Confirm the helpers are genuinely identical before merging them**

Run: `grep -n -A3 "def _digest_text\|def draft_digest\|def _digest_stored\|def _now" careeros/operations/*.py`

Phase 12's final review reported `apply.py`'s `_digest_text` and `outreach.py`'s `draft_digest` as character-identical, and `_now()` as existing four times. **Verify that rather than trusting it. If any body differs — a different encoding, a different hash, a different timestamp format — stop and report it**, because silently unifying two different digests would invalidate every approval written by the flow whose behaviour changed.

- [ ] **Step 2: Create the shared module**

```python
# careeros/operations/_shared.py
"""Helpers shared by every flow in this package.

Extracted when a third flow arrived. With two flows the duplication was
drift; with three a fourth author would have invented a fifth name for the
same digest, and the digests are load-bearing — an approval binds what it
transmits by comparing one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from careeros.storage.interface import StorageProvider


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def digest_stored(storage: StorageProvider, path: str) -> str:
    """sha256 of stored bytes, for an artifact handed onward by path."""
    return hashlib.sha256(storage.read(path)).hexdigest()
```

- [ ] **Step 3: Point all three existing modules at it**

Replace the local definitions in `approvals.py`, `outreach.py`, and `apply.py` with imports. Keep each call site's behaviour identical — this is a rename, not a change. Where a module reads a stored artifact once and both digests and uses those same bytes (Phase 12b did this deliberately in `execute_apply`, so that what is verified is what is transmitted), **preserve the single read** — call `hashlib` on the bytes you already have rather than reintroducing `digest_stored`, and leave the comment explaining why.

- [ ] **Step 4: Constrain `referral_state`**

In `careeros/core/models.py`, add `"referral_confirmed"` and `"closed"` and make it a `Literal`:

```python
    referral_state: Literal[
        "research", "referral_requested", "referral_confirmed", "closed"
    ] = "research"
```

Only the first two are ever written today, so this is additive for existing workspaces. Add a test in `tests/test_models.py` asserting a nonsense `referral_state` is rejected on `OutreachMessage` load — mirroring the `Approval.state` test Phase 12b added, which is the precedent for this.

- [ ] **Step 5: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: 862 passed + the one new model test, 7 skipped. The existing digest tests passing unchanged is the evidence the extraction was behaviour-preserving.

```bash
git add careeros/operations/ careeros/core/models.py tests/
git commit -m "refactor: extract the shared operations helpers and constrain referral_state"
```

---

### Task 2: Cadence state and the cadence policy

**Files:**
- Modify: `careeros/core/models.py`, `careeros/operations/outreach.py`
- Test: `tests/test_cadence_policy.py` (create), `tests/test_operations_outreach.py`

**Interfaces:**
- Produces: `OutreachMessage.last_touched_at`, `.touch_count`, `.closed_reason`; `CadencePolicy` with `save`/`load`; `execute_outreach_send` setting the first two.

- [ ] **Step 1: Write the failing tests**

`tests/test_cadence_policy.py`: bounds rejected outside range (`days_between_touches` 0 and 91, `max_touches` 0 and 11, `max_follow_ups_per_run` 0 and 21); a valid policy round-trips through `save`/`load`; and **`load` raises `FileNotFoundError` when the file is absent** rather than returning defaults — assert this explicitly, it is the property that stops a cadence starting on a schedule the user never chose.

Append to `tests/test_operations_outreach.py`: after a successful `execute_outreach_send`, the message's `last_touched_at` equals the result's `sent_at` and `touch_count` is 1; and sending a second time (after a fresh propose and approval) makes `touch_count` 2 while `last_touched_at` advances.

- [ ] **Step 2: Run to confirm they fail, then add the fields**

In `careeros/core/models.py`, on `OutreachMessage`:

```python
    last_touched_at: str | None = None
    touch_count: int = 0
    closed_reason: str | None = None
```

All defaulted, so existing records load unchanged.

`last_touched_at` is deliberately **not** derived from `sent_at`: Phase 12a made `sent_at` load-bearing for the "you already sent this" warning by keying prior-send detection off it, and overloading it to mean "most recent touch" would break that. It is also deliberately not derived from the activity log, which is an append-only audit artifact — making a scheduling decision depend on parsing it would couple cadence correctness to log format.

- [ ] **Step 3: Add `CadencePolicy`**

Follow `AutomationPolicy` in the same file exactly — `Field(ge=, le=)` bounds, `save` to `config/cadence_policy.json`, and a `load` that raises `FileNotFoundError` when absent:

```python
class CadencePolicy(BaseModel):
    days_between_touches: int = Field(ge=1, le=90)
    max_touches: int = Field(ge=1, le=10)
    max_follow_ups_per_run: int = Field(ge=1, le=20)
```

`max_touches` counts the initial message, not just follow-ups.

- [ ] **Step 4: Track touches in `execute_outreach_send`**

In the success path, alongside setting `send_state="sent"` and `sent_at`, also set `last_touched_at` to the same timestamp and increment `touch_count`. This is the one change 13a makes to an existing flow, and it is what lets a relationship begun before this phase enter the cadence.

- [ ] **Step 5: Suite and commit**

```bash
git add careeros/core/models.py careeros/operations/outreach.py tests/
git commit -m "feat: add cadence state to OutreachMessage and a CadencePolicy"
```

---

## Part B — The follow-up flow (Tasks 3–5)

### Task 3: The follow-up drafting skill

**Files:** Create `careeros/skills/follow_up_draft.py`, `tests/test_follow_up_draft.py`.

**Interfaces:** Produces `generate_follow_up_message(person, job, company, profile, goals, prior_text, touch_number, model=None) -> str`, returning `""` on failure.

- [ ] **Step 1: Write the failing tests**

Model them on the existing `tests/test_outreach_draft.py`: patch `litellm.completion`, assert the returned text is stripped, assert `""` on an exception and on empty content, and assert the model resolves from `CAREEROS_MODEL` when set.

Two assertions specific to this skill:
- **the prior message text appears inside the untrusted block**, not the system prompt
- **`touch_number` reaches the prompt**, so a second follow-up can be worded differently from a first

- [ ] **Step 2: Implement it, mirroring `outreach_draft.py`**

Same shape: role-based instructions, trusted context (profile, goals) in the system message, untrusted context wrapped by `wrap_untrusted` in the user message, `litellm.completion`, `return ""` on any exception.

The instruction should produce a **short** follow-up that references the prior message without repeating it, and does not re-pitch. Include `prior_text` and `touch_number` in the untrusted block.

**`prior_text` goes in the untrusted block even though we generated it.** It is LLM output derived from scraped, untrusted input, so it inherits that taint — wrapping it costs nothing and treating our own output as trusted is exactly how a prompt-injection boundary erodes.

- [ ] **Step 3: Suite and commit**

```bash
git add careeros/skills/follow_up_draft.py tests/test_follow_up_draft.py
git commit -m "feat: add the follow-up drafting skill"
```

---

### Task 4: The refusal error types and `propose_follow_up`

**Files:** Modify `careeros/operations/errors.py`; create `careeros/operations/follow_up.py`, `tests/test_operations_follow_up.py`.

**Interfaces:**
- Produces: `NotDueForFollowUp(message_id, days_since, days_required)`, `CadenceExhausted(message_id, touch_count, max_touches)`, `RelationshipClosed(message_id, reason)`; `ACTION = "send_follow_up"`; `propose_follow_up(runtime, job_id, person_id, *, model=None, action_label) -> FollowUpProposal`; and the frozen dataclass

```python
@dataclass(frozen=True)
class FollowUpProposal:
    approval_id: str
    message_id: str
    summary: str
    draft_text: str
    recipient_name: str
    recipient_email: str | None
    subject: str
    touch_number: int            # which touch this would be
    days_since_last_touch: int
```

`touch_number` and `days_since_last_touch` exist for the reviewer's benefit — a human deciding whether to send a third nudge after eleven days wants both numbers on screen, and neither is recoverable from the draft text.

- [ ] **Step 1: Add the three error types**

In `careeros/operations/errors.py`, following its convention of building the message in `__init__` and carrying structured attributes. All three subclass `OperationError`.

These are **refusals, not failures**: they mean "correctly declined to act". They must leave every record untouched — no approval opened, no draft written, no state changed — so a caller catching `OperationError` handles them without special-casing, and a scheduled run that skips a not-due relationship leaves nothing behind.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_operations_follow_up.py`, fixtures modelled on `tests/test_operations_outreach.py`'s (which already seed a job, company, person with an email, and profile). Seed an `OutreachMessage` with `sent_at`/`last_touched_at`/`touch_count` set, and a `CadencePolicy`.

Cover: a relationship not yet due raises `NotDueForFollowUp` with the right day counts **and writes nothing** (assert no approval file appeared); `touch_count >= max_touches` raises `CadenceExhausted`; `referral_state` of `referral_confirmed` or `closed` raises `RelationshipClosed`; `closed_reason` set raises `RelationshipClosed`; an absent `CadencePolicy` propagates `FileNotFoundError`; a missing entity raises `EntityNotFound`; a blocked company logs `policy_blocked` **and** raises `PolicyBlocked` with the draft asserted not attempted; an empty draft raises `DraftFailed`; and the happy path writes the draft onto the existing `OutreachMessage`, logs `follow_up_drafted`, and opens a pending approval whose payload is exactly `{message_id, job_id, person_id, draft_sha256, touch_number}`.

Also: re-proposing supersedes the prior pending approval (inherited from `open_approval`, but assert it — it is what stops a backlog accumulating for an un-reviewed relationship).

- [ ] **Step 3: Implement `propose_follow_up`**

Ordering, cheapest failures first, exactly as the sibling flows do:

1. load the `OutreachMessage`, person, job, company (→ `EntityNotFound`)
2. `CadencePolicy.load` (→ `FileNotFoundError` propagates)
3. refuse if terminal: `referral_state` in `{"referral_confirmed", "closed"}` or `closed_reason` set (→ `RelationshipClosed`)
4. refuse if `touch_count >= max_touches` (→ `CadenceExhausted`)
5. refuse if not due: `last_touched_at` newer than `days_between_touches` ago (→ `NotDueForFollowUp`). A message with `last_touched_at is None` has never been touched and is **not** a follow-up candidate — `propose_outreach_send` owns the first message
6. policy check — log `policy_blocked`, then raise
7. load profile and goals
8. `generate_follow_up_message` over `message.draft_text` as `prior_text` and `touch_count + 1` as `touch_number` (→ `DraftFailed`)
9. persist the new draft onto the same `OutreachMessage` (overwriting `draft_text`, leaving `sent_at`, `touch_count`, and `last_touched_at` alone), log `follow_up_drafted`
10. `open_approval` with the five payload keys

Steps 3–5 sit before the policy check and the LLM call so a refusal costs nothing.

- [ ] **Step 4: Suite and commit**

```bash
git add careeros/operations/errors.py careeros/operations/follow_up.py tests/test_operations_follow_up.py
git commit -m "feat: add propose_follow_up and the cadence refusal errors"
```

---

### Task 5: `execute_follow_up` and `decline_follow_up`

**Files:** Modify `careeros/operations/follow_up.py`; extend `tests/test_operations_follow_up.py`.

**Interfaces:** Produces `FollowUpResult(message_id, recipient_name, sent_at, touch_count)`; `execute_follow_up(runtime, approval_id, *, action_label)`; `decline_follow_up(runtime, approval_id, *, action_label)`.

- [ ] **Step 1: Write the failing tests**

Cover, for execute: refuses every non-`approved` state (parametrized over the module constants, including `EXECUTED`); refuses an approval whose `action` is not `send_follow_up`, raising before any state change (the cross-action guard Phase 12b added); `ArtifactChanged` when `draft_text` changed after approval; `MalformedApproval` on an emptied payload; `MissingRecipient` when the person has no email, leaving the approval `approved` and retryable; an SMTP failure marking both the message and the approval `failed` and logging `follow_up_send_failed` with the **exception type name only**; and the happy path sending, setting `last_touched_at` and incrementing `touch_count`, marking the approval `executed`, and logging `follow_up_sent`.

Plus the ordering proof, as both sibling flows have: patch `send_email` with a side effect that loads the approval from storage and asserts it is **already** `executed`. Then confirm it fails if the ordering were reversed — neuter `mark_executed` in a scratch script (not a committed file) and observe the failure. A test that passes either way proves nothing.

For decline, one test carries the whole subtlety: **declining advances `last_touched_at` but does not increment `touch_count`.** Assert both halves. Then assert the consequence directly — after a decline, `propose_follow_up` raises `NotDueForFollowUp` rather than immediately re-proposing the follow-up the user just declined.

- [ ] **Step 2: Implement both**

`execute_follow_up`, in this order: `require_state(APPROVED)` → action check → `payload_value` reads → load message/person/job → digest re-verify against `draft_sha256` (→ `ArtifactChanged`) → recipient check (→ `MissingRecipient`) → **`mark_executed`** → `send_email` inside `try` → on success set `send_state="sent"`, `sent_at`, `last_touched_at`, `touch_count + 1`, log `follow_up_sent`, return → on exception set the message `failed`, `mark_failed` with `type(exc).__name__`, log `follow_up_send_failed` with `status="failed"`, raise `SendFailed`.

The digest and recipient checks precede `mark_executed` so those refusals leave the approval retryable; the send follows it so a crash cannot yield a duplicate. Both rules are inherited, not invented — Phase 12a's final review established the second as a Critical finding.

`decline_follow_up`: `require_state(DECLINED)` → payload reads → load the message → set `send_state="declined"` **and `last_touched_at` to now** → log `follow_up_send_declined`.

Comment the `last_touched_at` advance at the site. Without it the relationship stays past-due and the next scheduled run re-proposes what the user just declined; with it, a decline defers by one cadence period. It does not increment `touch_count`, so a user who declines every time never exhausts `max_touches` — that is deliberate (each decline is a fresh choice to defer) and `careeros outreach close` is the off switch.

- [ ] **Step 3: Suite and commit**

```bash
git add careeros/operations/follow_up.py tests/test_operations_follow_up.py
git commit -m "feat: add execute_follow_up and decline_follow_up"
```

---

## Part C — Commands (Tasks 6–8)

### Task 6: `careeros outreach follow-up` — the scheduled proposer

**Files:** Modify `careeros/cli/outreach_cmd.py`; create `tests/test_follow_up_cmd.py`.

**Interfaces:** Consumes Tasks 2–4. Produces no new operations API.

- [ ] **Step 1: Write the failing tests**

Cover: an absent `CadencePolicy` exits 1 with a clear message; a due relationship gets a pending approval and **`send_email` is asserted not called** — this is the defining property of the command and the thing a regression would quietly break; `max_follow_ups_per_run` caps the number proposed; a not-due relationship is skipped silently with **no activity event** (a daily cron must not fill an append-only log with records of nothing happening); one relationship's `OperationError` is logged and the run continues to the next; and `--dry-run` lists what is due, proposes nothing, and writes no approval.

- [ ] **Step 2: Implement it**

`AutomationRuntime` for the identity stamp, but with the approval callback that only ever queues — `careeros.operations.approval_queue.queue_only`. The command calls `propose_follow_up` and **stops**. It must contain no call to `resolve_approval`, `execute_follow_up`, or `send_email`.

Loop shape, following `discover_and_apply_cmd`: load the policy, enumerate `outreach/` via `runtime.storage.list`, filter to due relationships, and for each up to the cap call `propose_follow_up` inside a per-relationship `try` that catches `OperationError`, logs its own event where the operations layer logs none, increments a counter, and continues. Print a run summary.

**Skip any relationship that already has a pending follow-up approval.** This is not an optimisation, it is a correctness fix the plan's own self-review turned up. Nothing sends until the user reviews, so an un-reviewed relationship's `last_touched_at` never advances and it stays permanently due — without this skip, a daily cron re-drafts it every single day, paying an LLM call each time, and the user eventually reviews a draft written days after the one they were first offered. `open_approval`'s supersede rule keeps the *approval* count at one but does nothing about the repeated drafting.

Build the skip from `list_pending(runtime.storage)`: collect the `entity_id`s of pending approvals whose `action` is `follow_up.ACTION`, and filter the due list against that set before proposing. No new helper is needed. Add a test asserting a second run proposes nothing new when a pending follow-up already exists, and asserting `generate_follow_up_message` was not called on that second run — the call count is what pins the cost property; an approval-count assertion alone would pass even if it re-drafted.

`--dry-run` takes the same enumeration and prints the due list — including `touch_count` and days elapsed, so a repeatedly-declined relationship is visible rather than becoming a silent recurring prompt.

- [ ] **Step 3: Suite and commit**

```bash
git add careeros/cli/outreach_cmd.py tests/test_follow_up_cmd.py
git commit -m "feat: add the scheduled careeros outreach follow-up command"
```

---

### Task 7: `careeros outreach review` — the interactive drainer

**Files:** Modify `careeros/cli/outreach_cmd.py`; extend `tests/test_follow_up_cmd.py`.

- [ ] **Step 1: Write the failing tests**

Cover: with no pending approvals it prints so and exits 0; a pending follow-up approval is shown and accepting sends it, marking the approval `executed`; declining marks it `declined` and advances `last_touched_at`; skipping leaves it `pending` for a later run; regenerating re-proposes (superseding the prior approval) and the **newly** drafted text is what sends; and it only ever touches approvals whose `action` is `send_follow_up`, leaving an unrelated pending `send_outreach` or `apply_to_job` approval alone.

That last one matters: `list_pending` returns every pending approval regardless of action, so filtering is the command's job.

- [ ] **Step 2: Implement it**

`LocalRuntime`. Call `list_pending(runtime.storage)`, filter to `action == follow_up.ACTION`, and for each: show the draft in a `Panel`, prompt accept / regenerate / decline / skip, then `resolve_approval` plus `execute_follow_up` or `decline_follow_up`. Reuse `MAX_REGENERATIONS`. Wrap each item so one failure does not abandon the rest of the queue, and print a summary.

Note where the displayed text comes from, because it differs from `outreach send`. There is no fresh proposal in hand here — the approval was written by an earlier process — so load the `OutreachMessage` named in the approval's payload and show its `draft_text`. Only the regenerate path calls `propose_follow_up`, and from that point the loop holds a real `FollowUpProposal` and should display and send *its* text, since re-proposing supersedes the approval being reviewed and the old `approval_id` is no longer the one to resolve.

Keep the review loop in the command body — a prior phase deliberately decided non-approval prompts stay direct `Prompt.ask` calls, and an agent driving the operations layer makes its own choice about re-proposing.

- [ ] **Step 3: Suite and commit**

```bash
git add careeros/cli/outreach_cmd.py tests/test_follow_up_cmd.py
git commit -m "feat: add careeros outreach review to drain the follow-up queue"
```

---

### Task 8: `careeros outreach close`

**Files:** Modify `careeros/cli/outreach_cmd.py`; extend `tests/test_outreach_cmd.py`.

- [ ] **Step 1: Write the failing tests**

Cover: closing sets `referral_state="closed"` and `closed_reason` to the given text and logs `cadence_closed`; a subsequent `propose_follow_up` raises `RelationshipClosed`; and closing a job/person pair with no `OutreachMessage` exits 1 with a clear message rather than a traceback.

- [ ] **Step 2: Implement it**

`careeros outreach close --job <id> --person <id> --reason <text>`, reusing `make_message_id` and following `mark_referral_requested`'s shape. Require `--reason`: a cadence that stopped for an unrecorded reason is exactly the state the user will not remember in three months, which is the problem this phase exists to solve.

- [ ] **Step 3: Suite and commit**

```bash
git add careeros/cli/outreach_cmd.py tests/test_outreach_cmd.py
git commit -m "feat: add careeros outreach close to end a cadence"
```

---

## Part D — Documentation (Task 9)

### Task 9: The record

**Files:** Modify `docs/agent-integration.md`, `docs/superpowers/DIVERGENCES.md`, `README.md`, `ROADMAP.md`.

- [ ] **Step 1: Extend the integration contract**

Add `send_follow_up` to `docs/agent-integration.md`: its six payload keys, marking which are read at execute time (verify by grepping `payload_value` call sites rather than guessing — Phase 12b's equivalent claim was wrong in the first draft), the propose → resolve → execute sequence with a runnable example, the three refusal error types and what each means for a retry, and the new activity events.

Explain the queue model explicitly, because it is the first flow that uses it: the scheduled command leaves pending approvals and sends nothing, `list_pending` is how an agent finds them, and an agent can drain the queue instead of `careeros outreach review`. Note that re-proposing supersedes, so an un-reviewed relationship accumulates one pending follow-up rather than a backlog.

Add the events to the CLI-emitted event list too — Phase 12b's fix wave missed exactly that and it became a finding.

- [ ] **Step 2: Record the decisions in `DIVERGENCES.md`**

- **The ToS decision**, with its reasoning: full LinkedIn automation, risk accepted by the workspace owner, and the double standard resolved by moving the line to *approved action versus unapproved volume* rather than reads-versus-writes — so Phases 3 and 7 need no retroactive review. Include what the decision does **not** license (spec §9). This is the entry the roadmap's scope-honesty note was waiting for.
- **Automated email discovery: declined, not deferred**, with the reasoning — no reliable non-guessing source exists for arbitrary individuals, and guessing means mailing strangers.
- **The system cannot detect a reply**, so `max_touches` guards the system's own blindness rather than being a preference knob.
- **A user who declines every follow-up is re-prompted indefinitely**, since a decline advances `last_touched_at` but not `touch_count`.
- `execute_outreach_send` now also sets `last_touched_at` and `touch_count` — a change to an existing flow's persisted record.

- [ ] **Step 3: README and ROADMAP**

README: the new commands, and that follow-ups are drafted on a schedule but never sent unreviewed.

ROADMAP: mark **13a shipped**, state what it built, and state that 13b (LinkedIn connection requests) remains. Keep Phase 12's manual exit-condition verification recorded as outstanding — 13a does not discharge it — and note that 13a's own exit condition needs an LLM credential for the drafting step.

- [ ] **Step 4: Verify every snippet runs, then commit**

Run each code block in `docs/agent-integration.md` against a scratch workspace with `send_email` patched. A snippet in an integration contract that does not run is worse than none.

```bash
git add docs/ README.md ROADMAP.md
git commit -m "docs: document the follow-up cadence and record the ToS decision"
```

---

## Exit Condition

Automated: `.venv/bin/python -m pytest -q` green, with `tests/test_follow_up_cmd.py` proving the scheduled command never sends, `tests/test_operations_follow_up.py` proving the consume-before-send ordering and the decline-advances-`last_touched_at` rule, and the existing outreach and apply suites unchanged by Task 1's extraction.

Outstanding after this plan, and not dischargeable by it: 13a's own manual verification needs an LLM credential for the drafting step, and Phase 12's manual exit condition remains as recorded.
