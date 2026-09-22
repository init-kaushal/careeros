# Phase 12b — Apply Operations + Follow-Up Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the job-apply flow into the operations layer as a propose/execute pair over the same durable `Approval` record, de-duplicate the copy of it living in `discover-and-apply`, and close the follow-ups Phase 12a recorded.

**Architecture:** Same shape Phase 12a established for outreach: `propose_apply` loads, policy-checks, drafts, persists, and opens a pending `Approval` carrying digests of everything that will be transmitted; `execute_apply` consumes the approval, re-verifies those digests, then performs only the external action (launching a browser and filling a form). Three callers — `apply_cmd` (`LocalRuntime`, headful), `discover-and-apply` (`AutomationRuntime`, headless), and any external agent (`ClaudeCodeRuntime`) — drive one implementation.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer + Rich (CLI only), Playwright (browser fillers), pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-phase12-second-runtime-design.md` — §6.3 (apply operations), §7.2 (discover-and-apply), §10 (deferrals), §12 (staging).

## Global Constraints

Copied from the spec's Global Constraints. Every task's requirements implicitly include these.

- No credentials, tokens, or API keys in any workspace file, activity log, or test fixture.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, `careeros/runtime/`, or `careeros/operations/`. (Workspace *discovery* via `os.environ` in `runtime/factory.py` is the standing carve-out.)
- Activity logs are append-only; no event is ever edited or deleted.
- `atomic_write` for writes (write-to-temp-then-rename).
- Strings built by `+` concatenation only — **no `.format()` and no f-strings with user data.**
- **No `rich`, `typer`, or `click` import anywhere under `careeros/operations/`, and no printing, prompting, or process exit from it.** An AST guard in `tests/test_operations_purity.py` enforces this and auto-extends to new modules.
- **`execute_*` performs the external action and nothing else.** It never drafts, never calls an LLM, and never re-derives an artifact. Everything it transmits was written before the approval and is digest-verified against it.
- **An `Approval` is single-use.** Only `approved` may execute; executing advances it to a terminal state.
- A browser error must never advance a job's `stage`.
- Run `.venv/bin/python -m pytest -q` before each commit. Baseline: **788 passed, 7 skipped**.

## Deviations from the spec, and why

The spec's §6.3 predates two things learned during 12a's final review. All three deviations below are deliberate; record them in `DIVERGENCES.md` in Task 9.

1. **The spec says `headless` is "the only behavioral difference" between `apply_cmd` and `discover-and-apply`. That is false.** They also differ in approval-summary content, JD truncation, per-job activity events, the `job_applied` summary text, and locked-profile handling. Tasks 5–7 handle each explicitly rather than assuming one parameter covers it.
2. **The spec has `execute_apply` "reload the job and profile".** That re-derives transmitted content: `filler.fill(page, job, profile, ...)` populates form fields from `profile`, and navigates to `job.url`. This is the same defect the final review found in outreach's subject line. Tasks 4–5 digest-bind the profile and record the approved `job_url` in the payload.
3. **The board-session check moves ahead of the cover-letter generation.** The spec orders it at step 8, after the draft — which is what `apply_cmd` does today, so a user who is not signed in to LinkedIn pays for an LLM call before being told to sign in. Task 4 checks the session at step 5 instead. The consequence, accepted: because the CLI's review loop re-calls `propose_apply` to regenerate, the check now runs once per regeneration (up to six times) rather than once, each opening the isolated browser profile. That is the right trade — re-checking also catches a session that expired during a long review, which today's single check cannot, and each repeated check is now preceded by nothing expensive.

## File Structure

**Create:** `careeros/operations/apply.py`; `tests/test_operations_apply.py`.

**Modify:** `careeros/operations/errors.py` (three new error types); `careeros/operations/approvals.py` (follow-ups); `careeros/operations/outreach.py` (follow-ups); `careeros/core/models.py` (`Literal` state); `careeros/core/ids.py`, `careeros/core/job_id.py`, `careeros/cli/research_cmd.py` (slugify convergence); `careeros/cli/apply_cmd.py`, `careeros/cli/discover_and_apply_cmd.py` (rewire); six CLI modules (`_get_storage` convergence); `tests/conftest.py`, `tests/test_operations_purity.py`, `tests/test_approvals.py`, `tests/test_operations_outreach.py`, `tests/test_apply_cmd.py`, `tests/test_discover_and_apply_cmd.py`; `docs/agent-integration.md`, `docs/superpowers/DIVERGENCES.md`, `README.md`, `ROADMAP.md`.

---

## Part A — Follow-up closure (Tasks 1–3)

These land first: Task 2 settles `action_label`'s signature before Task 4 writes new operations against it.

### Task 1: Documentation and docstring accuracy follow-ups

**Files:** Modify `careeros/operations/approvals.py`, `docs/agent-integration.md`, `tests/conftest.py`, `tests/test_operations_outreach.py`, `docs/superpowers/DIVERGENCES.md`.

**Interfaces:** Consumes nothing. Produces no API change — documentation text only.

- [ ] **Step 1: Fix the five recorded inaccuracies**

Each was found by a reviewer and deferred. Verify each against the code before writing.

1. `careeros/operations/approvals.py` — the sentence claiming "a dead process leaves the record executed or failed, never approved" is unscoped: a process dying *before* `mark_executed` (during the digest check) leaves it `approved`, which is correct and intended. Scope the sentence to a death *after* `mark_executed`.
2. `docs/agent-integration.md` — the garbled double negative around "cannot change what goes out with no re-review". The same claim is phrased cleanly in `careeros/operations/outreach.py`'s `execute_outreach_send` docstring; match it.
3. `tests/test_operations_outreach.py` — the subject-binding test's docstring says the subject "is digest-bound the same way the draft body is". It is not hashed; it is stored verbatim on the payload, which is a *stronger* guarantee. Correct the wording.
4. `docs/agent-integration.md` — the `send_outreach` payload-key list is headed "read by `execute_outreach_send` via `payload_value`", but `job_id` is read by neither `execute_outreach_send` nor `decline_outreach_send`. Mark which keys are read at execute time and which are recorded for audit only.
5. `tests/conftest.py` — the SMTP guard docstring claims it is "independent of import style, so it cannot be bypassed". True only because `careeros/mailer.py` does `import smtplib` with late attribute lookup; a module doing `from smtplib import SMTP` would bind before the fixture patches. Soften to state that condition.

- [ ] **Step 2: Add the `portability.py` omission to the deferral entry**

`docs/superpowers/DIVERGENCES.md`'s "New deferrals" entry enumerates eight modules with a private `_get_storage`. `careeros/cli/portability.py` (`careeros export`) also ignores `CAREEROS_WORKSPACE` but resolves inline with no such helper, so it is absent from that list while the `CAREEROS_WORKSPACE` row above it (correctly) mentions it. Reconcile: note that `export` also ignores the variable and resolves inline.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: 788 passed, 7 skipped — unchanged.

```bash
git add careeros/operations/approvals.py docs/agent-integration.md tests/conftest.py \
        tests/test_operations_outreach.py docs/superpowers/DIVERGENCES.md
git commit -m "docs: close the accuracy follow-ups Phase 12a recorded"
```

---

### Task 2: Hardening follow-ups in the operations layer

**Files:** Modify `careeros/core/models.py`, `careeros/operations/approvals.py`, `careeros/operations/outreach.py`, `careeros/operations/errors.py`, `careeros/cli/outreach_cmd.py`, `tests/test_operations_purity.py`, `tests/test_approvals.py`, `tests/test_operations_outreach.py`.

**Interfaces:**
- Produces: `Approval.state` typed as a `Literal`; `action_label` required (no default) on the three outreach operations; `list_pending` catching a narrow exception set; the purity guard catching bare `exit(...)`; `decline_outreach_send` no longer requiring the `Person` record; `MissingRecipient` carrying its own remediation text.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_approvals.py`:

```python
class TestApprovalStateIsConstrained:
    def test_an_unknown_state_is_rejected_on_load(self, tmp_path):
        import pytest
        from pydantic import ValidationError
        storage = _storage(tmp_path)
        storage.atomic_write(
            "approvals/bad.json",
            b'{"id":"bad","action":"send_outreach","summary":"s",'
            b'"state":"banana","created_at":"2026-09-22T00:00:00+00:00"}',
        )
        with pytest.raises(ValidationError):
            Approval.load(storage, "bad")


class TestListPendingExceptionNarrowing:
    def test_a_corrupt_record_is_skipped_but_an_unexpected_error_is_not_swallowed(self, tmp_path, monkeypatch):
        import pytest
        runtime = _runtime(tmp_path)
        pending = _open(runtime)
        runtime.storage.atomic_write("approvals/garbage.json", b"{not json")
        assert [a.id for a in list_pending(runtime.storage)] == [pending.id]

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(Approval, "load", classmethod(boom))
        with pytest.raises(RuntimeError):
            list_pending(runtime.storage)
```

Append to `tests/test_operations_purity.py`:

```python
@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_bare_exit_calls(path):
    """`from sys import exit` then a bare `exit(1)` is still a process exit.

    The sys.exit check only sees attribute access on `sys`; this catches the
    imported-name form, which the previous guard let through.
    """
    tree = ast.parse(path.read_text())
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "exit" not in called, path.name + " calls a bare exit()"
```

Append to `tests/test_operations_outreach.py`:

```python
class TestDeclineDoesNotNeedThePerson:
    def test_declines_with_the_person_record_deleted(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        resolve_approval(runtime, proposal.approval_id,
                         ApprovalResult(approved=False), action_label="outreach")
        runtime.storage.delete("people/" + PERSON_ID + ".json")
        decline_outreach_send(runtime, proposal.approval_id, action_label="outreach")
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).send_state == "declined"
        assert "outreach_send_declined" in _log(runtime.storage)


class TestActionLabelIsRequired:
    def test_propose_requires_action_label(self, tmp_path):
        import pytest
        runtime = _runtime(tmp_path)
        with pytest.raises(TypeError):
            propose_outreach_send(runtime, JOB_ID, PERSON_ID)
```

- [ ] **Step 2: Run to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_approvals.py tests/test_operations_purity.py tests/test_operations_outreach.py -q`
Expected: the five new tests fail — `banana` currently validates, `list_pending` swallows the `RuntimeError`, no bare-exit guard exists, decline raises `EntityNotFound`, and `action_label` has a default.

- [ ] **Step 3: Constrain `Approval.state`**

In `careeros/core/models.py`, add `from typing import Literal` to the imports and change the field:

```python
    state: Literal["pending", "approved", "declined", "superseded", "executed", "failed"] = "pending"
```

Nothing rejected a nonsense state on load before; the lifecycle guarded transitions but not the stored value.

- [ ] **Step 4: Narrow `list_pending`'s exception set**

In `careeros/operations/approvals.py`, replace the bare `except Exception: continue` with `except (ValueError, FileNotFoundError, ValidationError): continue`, importing `ValidationError` from `pydantic`. Keep the comment explaining that one corrupt record must not hide every other pending decision, and extend it to say that an unexpected error is deliberately *not* swallowed, because a future field rename would otherwise make every record silently invisible.

- [ ] **Step 5: Make `action_label` required on the three outreach operations**

In `careeros/operations/outreach.py`, remove the `= "outreach"` default from `action_label` on `propose_outreach_send`, `execute_outreach_send`, and `decline_outreach_send`. The spec's §6.2 signature has no default, and a caller that forgets it on one of three calls silently labels part of its own audit trail as CLI-driven. `careeros/cli/outreach_cmd.py` already passes it on `resolve_approval`; add it to every operations call there too.

- [ ] **Step 6: Drop the `Person` requirement from the decline path**

`decline_outreach_send` loads the person solely to name them in its activity summary. A missing `people/<id>.json` therefore turns pure bookkeeping into `EntityNotFound`. Load the message only, and build the summary from the person id when the record is unavailable — a decline must always be recordable. Keep using the person's name when the record *is* present.

- [ ] **Step 7: Move `MissingRecipient`'s remediation text into the error**

`careeros/operations/errors.py` builds next-step advice into `PolicyBlocked` and `ArtifactChanged`, but `MissingRecipient`'s `careeros people update` instruction lives in the CLI. Move it into the error so an agent gets the same guidance, and simplify `careeros/cli/outreach_cmd.py`'s handler to print the message. **Keep the rendered text identical** — `tests/test_outreach_cmd.py` asserts on it.

- [ ] **Step 8: Run the tests and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: pass, with the new tests green and no existing assertion changed.

```bash
git add careeros/core/models.py careeros/operations/ careeros/cli/outreach_cmd.py tests/
git commit -m "fix: harden the approval lifecycle and make action_label explicit"
```

---

### Task 3: Converge the remaining `slugify` copies

**Files:** Modify `careeros/core/job_id.py`, `careeros/cli/research_cmd.py`. Test: `tests/test_ids.py`, `tests/test_job_id.py` if present, `tests/test_research_cmd.py`.

**Interfaces:** Consumes `careeros.core.ids.slugify` (made public in Phase 12a). Produces no new API.

- [ ] **Step 1: Verify the copies are identical**

Run: `grep -n -A4 "def _slugify" careeros/core/job_id.py careeros/cli/research_cmd.py`
Confirm both bodies match `careeros/core/ids.py`'s `slugify` character for character. **If either differs, stop and report it** — a silent behavior change in ID generation would be far worse than a duplicated helper.

- [ ] **Step 2: Replace both with the shared helper**

Import `slugify` from `careeros.core.ids` in each and delete the local definitions. Phase 12a already did this for `outreach_cmd`; copy that pattern.

- [ ] **Step 3: Run the affected suites and commit**

Run: `.venv/bin/python -m pytest tests/test_ids.py tests/test_job_cmd.py tests/test_research_cmd.py tests/test_dedup.py -q`
Then: `.venv/bin/python -m pytest -q`

```bash
git add careeros/core/job_id.py careeros/cli/research_cmd.py
git commit -m "refactor: converge the last two slugify copies onto core.ids"
```

---

## Part B — Phase 12b apply operations (Tasks 4–7)

### Task 4: Apply errors and `propose_apply`

**Files:** Create `careeros/operations/apply.py`. Modify `careeros/operations/errors.py`. Test: `tests/test_operations_apply.py`.

**Interfaces:**
- Consumes: `open_approval`, `payload_value`, `require_state`, `mark_executed`, `mark_failed`, `APPROVED` from `careeros.operations.approvals`; `EntityNotFound`, `PolicyBlocked`, `DraftFailed`, `ArtifactChanged` from `careeros.operations.errors`; `select_resume(storage, job_id) -> ResumeChoice | None` from `careeros.core.resume_select` (`ResumeChoice` has `path` absolute, `storage_path` relative, `tailored`, `variant`, `stale_master`); `generate_cover_letter(jd_text, profile, skills, goals, model=None) -> str` from `careeros.skills.cover_letter`; `PolicyEngine`; `check_board_sessions([board]) -> dict[str, bool]` from `careeros.browser.session`; `BrowserProfileBusy` from `careeros.browser.driver`; the four fillers.
- Produces: `ACTION = "apply_to_job"`; `BoardSessionRequired(board)`, `FillIncomplete`, `BrowserUnavailable(profile_busy: bool)` in `errors.py`; `ApplyProposal`, `ApplyResult`, `propose_apply(runtime, job_id, *, model=None, action_label, summary=None, jd_text=None) -> ApplyProposal`.

- [ ] **Step 1: Add the three error types**

In `careeros/operations/errors.py`, following the existing convention of building the message in `__init__`:

```python
class BoardSessionRequired(OperationError):
    def __init__(self, board: str) -> None:
        super().__init__(
            "Not signed in to " + board
            + ". Run: careeros browser login --board " + board
        )
        self.board = board


class FillIncomplete(OperationError):
    pass


class BrowserUnavailable(OperationError):
    def __init__(self, detail: str, *, profile_busy: bool = False) -> None:
        super().__init__(detail)
        # A locked browser profile is a whole-run condition, not a per-job
        # failure: discover-and-apply must stop rather than pay for a cover
        # letter on every remaining job only to fail identically at launch.
        # Carried as a flag so that decision stays with the caller and no
        # browser exception leaks through the operations boundary.
        self.profile_busy = profile_busy
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_operations_apply.py`. Model the fixtures on `tests/test_operations_apply`'s sibling `tests/test_operations_outreach.py` and on `tests/test_apply_cmd.py`'s workspace setup (which already seeds a job, a profile, and a resume under `resumes/versions/`). Cover, for propose:

- a job with no `url` raises `EntityNotFound` and the message names `careeros job update`
- no resume at all raises `EntityNotFound`
- a blocked company logs `policy_blocked` **and** raises `PolicyBlocked`, with `generate_cover_letter` asserted not called
- an empty cover letter raises `DraftFailed`
- no filler for the URL raises `EntityNotFound`
- a LinkedIn URL with no authorized session raises `BoardSessionRequired` with `.board == "linkedin"`
- `BrowserProfileBusy` during the session check raises `BrowserUnavailable` with `profile_busy is True`
- the happy path writes `applications/<job_id>/cover_letter.txt`, opens a `pending` approval, and records all seven payload keys
- `propose_apply` called twice supersedes the first approval
- an explicit `summary=` overrides the default approval summary
- an explicit `jd_text=` is used in preference to `job.description`

Assert the payload keys exactly: `job_id`, `job_url`, `cover_letter_storage_path`, `resume_storage_path`, `filler_platform`, `cover_letter_sha256`, `resume_sha256`, `profile_sha256`.

- [ ] **Step 3: Run to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_operations_apply.py -q`
Expected: `ModuleNotFoundError: No module named 'careeros.operations.apply'`.

- [ ] **Step 4: Implement `propose_apply`**

Create `careeros/operations/apply.py`. Preserve `apply_cmd.py`'s current ordering exactly — it is deliberate and commented. Key points:

```python
ACTION = "apply_to_job"

FILLERS = [GreenhouseFiller(), LeverFiller(), LinkedInFiller(), GenericFiller()]


@dataclass(frozen=True)
class ApplyProposal:
    approval_id: str
    job_id: str
    company: str
    title: str
    summary: str
    cover_letter: str
    cover_letter_storage_path: str
    resume: ResumeChoice
    filler_platform: str


@dataclass(frozen=True)
class ApplyResult:
    job_id: str
    company: str
    title: str
    resume_storage_path: str
    applied_at: str
```

Order inside `propose_apply` — everything that can fail cheaply fails before the LLM call, which is why the sequence is what it is:

1. load job (→ `EntityNotFound`); require `job.url` (→ `EntityNotFound` naming `careeros job update`)
2. `select_resume` (→ `EntityNotFound` when `None`)
3. policy check — log `policy_blocked`, then raise `PolicyBlocked`
4. detect the filler from `job.url` (→ `EntityNotFound` when none can handle it)
5. if the filler is a `LinkedInFiller`, check the session via `check_board_sessions(["linkedin"])`, raising `BoardSessionRequired("linkedin")` when unauthorized and `BrowserUnavailable(str(exc), profile_busy=True)` on `BrowserProfileBusy`
6. load profile / skills / goals
7. `generate_cover_letter` over `jd_text if jd_text is not None else (job.description or "")[:4000]` (→ `DraftFailed`)
8. write `applications/<job_id>/cover_letter.txt`
9. `open_approval` with the eight payload keys

Steps 4 and 5 sit **ahead** of the cover-letter generation, which is Deviation 3 above — today's `apply_cmd` generates the letter first and only then discovers the user is not signed in.

Default summary, matching today's CLI prompt text:

```python
    default_summary = (
        "About to fill the " + filler.platform + " application for "
        + job.company + " — " + job.title + ". Proceed?"
    )
```

The payload records **workspace-relative** paths plus three digests:

```python
        {
            "job_id": job_id,
            "job_url": job.url,
            "cover_letter_storage_path": cl_storage_path,
            "resume_storage_path": resume_choice.storage_path,
            "filler_platform": filler.platform,
            "cover_letter_sha256": _digest_stored(runtime.storage, cl_storage_path),
            "resume_sha256": _digest_stored(runtime.storage, resume_choice.storage_path),
            "profile_sha256": _digest_text(profile.model_dump_json()),
        }
```

`job_url` and `profile_sha256` are the deviation from the spec described at the top of this plan: `filler.fill` navigates to the URL and populates form fields from the profile, so both are transmitted content and must be bound to the approval rather than re-derived at send time. Add a comment saying exactly that.

Write `_digest_stored(storage, path)` (sha256 of the stored bytes) and `_digest_text(text)` helpers; do not import outreach's private digest helper.

- [ ] **Step 5: Run the tests and commit**

Run: `.venv/bin/python -m pytest tests/test_operations_apply.py tests/test_operations_purity.py -q`
Then: `.venv/bin/python -m pytest -q`

```bash
git add careeros/operations/apply.py careeros/operations/errors.py tests/test_operations_apply.py
git commit -m "feat: add propose_apply and the apply error types"
```

---

### Task 5: `execute_apply`

**Files:** Modify `careeros/operations/apply.py`. Test: `tests/test_operations_apply.py`.

**Interfaces:** Produces `execute_apply(runtime, approval_id, *, headless: bool, action_label) -> ApplyResult`.

- [ ] **Step 1: Write the failing tests**

Cover: refuses every non-`approved` state (parametrized, including `executed`); refuses a second execution with the filler asserted called once; `ArtifactChanged` when the cover letter changes after approval; `ArtifactChanged` when the resume PDF changes; `ArtifactChanged` when the profile changes; `MalformedApproval` on an emptied payload; `FillIncomplete` when `filler.fill` returns `False`, with the job's `stage` asserted **unchanged**; `BrowserUnavailable` on `ImportError` and on `BrowserProfileBusy` (the latter with `profile_busy is True`), both leaving the stage unchanged; a generic browser exception marking the approval `failed` and leaving the stage unchanged; and the happy path setting `stage="applied"` with `applied_at`, marking the approval `executed`, and logging `job_applied` whose summary names the resume that was uploaded.

Also add the ordering test, mirroring the one that guards outreach: patch `launch_browser` with a side effect that loads the approval from storage and asserts it is already `executed`, proving consumption precedes the external action.

Patch `careeros.operations.apply.launch_browser` and use a `MagicMock` filler; do not drive a real browser.

- [ ] **Step 2: Run to confirm failure, then implement**

`execute_apply` order — consumption must precede the fill, exactly as outreach's does:

1. `require_state(runtime.storage, approval_id, APPROVED)`
2. read all payload keys via `payload_value`
3. re-verify all three digests → `ArtifactChanged(path)` naming the artifact that changed
4. reload the job; resolve the two storage paths to absolute via `runtime.storage.resolve`
5. `mark_executed(runtime, approval_id)`
6. `launch_browser(headless=headless)` and `filler.fill(...)`, inside `try`
7. on success: update the job to `stage="applied"` with `applied_at`/`updated_at`, log `job_applied` including the resume storage path, return `ApplyResult`
8. on `filler.fill` returning `False`: `mark_failed`, log `apply_incomplete` with `status="failed"`, leave the stage untouched, raise `FillIncomplete`
9. `except ImportError` → `BrowserUnavailable` naming the `playwright install` remedy; `except BrowserProfileBusy` → `BrowserUnavailable(..., profile_busy=True)`; `except Exception` → `mark_failed` then `BrowserUnavailable`

Resolve the filler from `filler_platform` by matching against `FILLERS`; raise `MalformedApproval` if no filler matches the recorded platform.

Steps 3 and 4 precede `mark_executed` so a digest mismatch or a missing job leaves the approval `approved` and retryable, matching outreach's treatment of `ArtifactChanged` and `MissingRecipient`.

- [ ] **Step 3: Run the tests and commit**

Run: `.venv/bin/python -m pytest tests/test_operations_apply.py -q` then the full suite.

```bash
git add careeros/operations/apply.py tests/test_operations_apply.py
git commit -m "feat: add execute_apply"
```

---

### Task 6: Rewire `apply_cmd`

**Files:** Modify `careeros/cli/apply_cmd.py`, `tests/test_apply_cmd.py`.

**Interfaces:** Consumes Task 4 and 5. Produces no new API; `apply_cmd.py` loses `_get_storage` and `_now`.

- [ ] **Step 1: Move the test patch targets, watch them fail**

`careeros.cli.apply_cmd.generate_cover_letter` → `careeros.operations.apply.generate_cover_letter`; `careeros.cli.apply_cmd.launch_browser` → `careeros.operations.apply.launch_browser`; `careeros.cli.apply_cmd.select_resume` → `careeros.operations.apply.select_resume`. Leave `careeros.cli.apply_cmd.Prompt.ask` and `careeros.runtime.local.Confirm.ask` alone — the cover-letter review loop and the approval prompt both stay in the command body.

**Do not change any assertion.** If one cannot pass, stop and report it: those assertions are the only proof this refactor preserves behavior. (Phase 12a hit exactly this and the escalation was correct.)

- [ ] **Step 2: Rewire the command**

Body becomes: `resolve_storage` → `open_local_runtime` → `propose_apply(..., action_label="apply")` → print the resume-provenance messaging from `proposal.resume` (keep the existing wording at the current `apply_cmd.py:105-127` verbatim) → the existing cover-letter review loop, re-calling `propose_apply` on "r" → `runtime.request_approval(ActionProposal(action=ACTION, summary=proposal.summary, ...))` → `resolve_approval` → `execute_apply(..., headless=False, action_label="apply")` → print success.

Map each error to the message and exit code it produces today, catching the specific types before the generic `OperationError`: `BoardSessionRequired` prints the `careeros browser login` line, `BrowserUnavailable` prints its detail, `FillIncomplete` prints the "Form fill incomplete" warning and exits 1.

Use `resolve_storage` rather than a private `_get_storage`, picking up the `CAREEROS_WORKSPACE` tier — this is two of the eight modules the Phase 12a deferral named, and the deferral says this rewire is the natural place.

On the quit path, resolve the approval as declined (the lesson from 12a: a "q" must not leave a live `pending` approval that a later process could execute).

- [ ] **Step 3: Full suite and commit**

```bash
git add careeros/cli/apply_cmd.py tests/test_apply_cmd.py
git commit -m "refactor: route apply through the operations layer"
```

---

### Task 7: De-duplicate `discover-and-apply`

**Files:** Modify `careeros/cli/discover_and_apply_cmd.py`, `tests/test_discover_and_apply_cmd.py`.

**Interfaces:** Consumes Tasks 4–5. Produces no new API.

**This is the task with the real behavior decisions.** The spec was wrong that `headless` is the only difference; handle each explicitly.

- [ ] **Step 1: Rewire the apply section**

Replace the duplicated block (currently `discover_and_apply_cmd.py:213-320`) with `propose_apply` / `resolve_approval` / `execute_apply(headless=True)`, `action_label="discover-and-apply"`, passing:

- `summary=` the existing score-and-threshold text, so the approval record keeps it
- nothing for `jd_text` — see Step 2

Keep above the extracted section, untouched: the `max_auto_applies_per_run` cap, the `auto_apply_min_score` threshold, the `already_applied` skip, and the `job.applied_at` re-read (a pre-filter, not part of applying).

Keep the unattended resume-provenance wording — it differs from the CLI's deliberately and `proposal.resume` carries the structured data for it.

Convert the per-job failure handling to `except OperationError`, preserving each existing event: `DraftFailed` → `cover_letter_failed`; `EntityNotFound` from filler detection → `no_filler_available`; `FillIncomplete` → the `apply_incomplete` event already logged by `execute_apply`, so do not log it twice; `PolicyBlocked` → already logged inside `propose_apply`, so only increment `blocked_count`.

`BrowserUnavailable` with `profile_busy is True` must still stop the whole run, printing the same red message — that is what the flag exists for. Other `BrowserUnavailable` values are per-job failures.

- [ ] **Step 2: Record two deliberate behavior changes**

Both follow from sharing one implementation, and both need a `DIVERGENCES.md` note in Task 9:

1. **JD truncation.** This command currently passes the full `p["jd_text"]` to `generate_cover_letter`, while `apply_cmd` caps it at 4000 characters. Because line ~171 already persists that text into `job.description` before the apply loop, letting `propose_apply` derive it applies the 4000-character cap here too. Adopt the cap: it is the deliberate prompt budget, and two different caps for one operation is exactly the drift being removed. For JDs over 4000 characters this changes the generated cover letter.
2. **The `job_applied` summary.** Today this command's event text embeds the score and threshold plus the resume path; `execute_apply` logs a canonical summary naming company, title, and resume. The score is not lost — it is in the approval's `summary`, which `approval_requested` and `approval_granted` both carry for the same `entity_id` — so the trail as a whole keeps it. Do not add a summary override for this.

- [ ] **Step 3: Full suite and commit**

`tests/test_discover_and_apply_cmd.py` must keep asserting the same counts, caps, and skip behavior. Update patch targets to `careeros.operations.apply.*`.

```bash
git add careeros/cli/discover_and_apply_cmd.py tests/test_discover_and_apply_cmd.py
git commit -m "refactor: de-duplicate the apply flow in discover-and-apply"
```

---

## Part C — Convergence and documentation (Tasks 8–9)

### Task 8: Converge the remaining `_get_storage` helpers

**Files:** Modify `careeros/cli/browse_cmd.py`, `browser_cmd.py`, `job_cmd.py`, `research_cmd.py`, `resume_cmd.py`, `workspace_cmd.py`, `portability.py`, and their test suites.

**Interfaces:** Consumes `resolve_storage` and `WorkspaceNotConfigured` from `careeros.runtime.factory`.

- [ ] **Step 1: Confirm the inventory**

Run: `grep -ln "def _get_storage" careeros/cli/*.py`
After Task 6 this should list six modules (`apply_cmd` and `discover_and_apply_cmd` having converged). `portability.py` resolves inline with no helper and also needs converting. **If the count differs, report it before proceeding** — an earlier phase had this number wrong twice.

- [ ] **Step 2: Replace each with `resolve_storage`**

Each private helper prints and raises `typer.Exit`; `resolve_storage` raises `WorkspaceNotConfigured`. Follow the pattern Phase 12a established in `outreach_cmd.py`: a small module-local `_open_runtime`/`_storage` wrapper that catches `WorkspaceNotConfigured` and `FileNotFoundError` and prints the existing message. **Preserve each module's current message and exit code exactly** — several test suites assert on them.

This makes `CAREEROS_WORKSPACE` uniform across the CLI, which is what the Phase 12a deferral and the warning now in `docs/agent-integration.md` were waiting on.

- [ ] **Step 3: Full suite and commit**

```bash
git add careeros/cli/ tests/
git commit -m "refactor: converge every CLI command onto factory.resolve_storage"
```

---

### Task 9: Documentation and the honest record

**Files:** Modify `docs/agent-integration.md`, `docs/superpowers/DIVERGENCES.md`, `README.md`, `ROADMAP.md`, and the spec.

- [ ] **Step 1: Extend the integration contract with apply**

Add the `apply_to_job` action to `docs/agent-integration.md`: its eight payload keys (marking which are read at execute time), the `propose_apply` → `resolve_approval` → `execute_apply` sequence with a runnable example, the `headless` parameter, and the three new error types with what each means for a retry. Note that `execute_apply` launches a browser, so an agent must expect a long-running call and a visible window when `headless=False`.

Document the digest set explicitly: cover letter, resume, **and profile** — and that a profile edit between approval and execution fails with `ArtifactChanged` by design, because the profile populates the form fields.

- [ ] **Step 2: Update `DIVERGENCES.md`**

Close what 12b closes and record what it changes:

- The `_get_storage` deferral: **closed** by Tasks 6 and 8; `CAREEROS_WORKSPACE` is now honored by every command.
- The `CAREEROS_WORKSPACE` row: now **fully** closed — remove the "partly" qualification and the split-workspace hazard, since it no longer exists.
- **New:** `discover-and-apply` now caps JD text at 4000 characters for cover-letter generation, where it previously passed the full text.
- **New:** its `job_applied` event summary no longer embeds the score and threshold; those live in the approval summary carried by `approval_requested`/`approval_granted`.
- **New:** apply is now digest-bound on the profile, so editing your profile between approving an application and executing it fails with `ArtifactChanged`.
- Correct the spec's §6.3 claim that `headless` is the only difference between the two call sites, and its instruction that `execute_apply` reload the profile.

- [ ] **Step 3: Update `README.md` and `ROADMAP.md`**

README: the agent-integration section currently says apply "is not yet" drivable — it now is. Remove the split-workspace warning.

ROADMAP: mark Phase 12 **shipped** (both halves). State what 12b built, and keep the manual exit-condition verification recorded as outstanding with what it requires.

- [ ] **Step 4: Verify every snippet runs, then commit**

Run each code block in `docs/agent-integration.md` against a scratch workspace with `send_email` and `launch_browser` patched. A snippet in an integration contract that does not run is worse than none.

```bash
git add docs/ README.md ROADMAP.md
git commit -m "docs: document apply in the integration contract and close the 12b record"
```

---

## Exit Condition

Automated: `.venv/bin/python -m pytest -q` green, with `tests/test_operations_apply.py` proving the consume-before-fill ordering and all three digest bindings, and `tests/test_apply_cmd.py` / `tests/test_discover_and_apply_cmd.py` proving the rewire changed nothing observable beyond the two recorded behavior changes.

Still outstanding after this plan, and not dischargeable by it: the phase's **manual** exit condition — a real outreach send from an agent session against a real workspace, requiring `CAREEROS_SMTP_*` configured and a `Person` record holding the user's own address.
