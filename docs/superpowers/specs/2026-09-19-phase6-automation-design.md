# Phase 6 — Automation — Design Spec

**Date:** 2026-09-19
**Status:** Approved for implementation planning
**Author:** Kaushal + Claude

---

## 1. What This Is

The master design spec (`docs/superpowers/specs/2026-09-18-careeros-design.md`, §7) defines Phase 6 as "Automation": scheduled discovery, follow-up reminders, opportunity monitoring, user-configurable automation policies, with the exit condition `careeros schedule --daily-discover` running unattended and writing to the workspace without manual intervention, while "approvals still required for any outreach or application."

In practice, the master spec's Phase 3 ("People + Outreach") was never built — there is no outreach subsystem, no follow-up mechanism, and no `PolicyEngine` (deferred in Phase 5). This spec scopes Phase 6 to what the built system can actually support: unattended, scheduled **discovery** (the existing `browse` pipeline) plus unattended **auto-apply** for jobs that clear a user-configured score threshold, using the existing `apply` pipeline. "Approval" for unattended auto-apply is the user's own pre-configured threshold, resolved by a new `AutomationRuntime` — consistent with the master spec's "approvals still enforced" language, since the human decision happens once, in advance, when the user sets the threshold, rather than per-application in real time.

**Deliberately out of scope for this phase** (deferred, not forgotten):
- Follow-up reminders, opportunity monitoring, outreach automation — no outreach subsystem exists to automate
- A `PolicyEngine` beyond the single numeric threshold this phase introduces
- A built-in scheduler/daemon — the user's own cron/launchd triggers a one-shot CLI command; CareerOS manages no process lifecycle
- Interactive automation policy configuration (`careeros automation configure` or similar) — the user edits `config/automation_policy.json` directly, same treatment as `config/sources.json` today
- Cover-letter regeneration/review for auto-applied jobs — unattended runs use the first generated cover letter or skip the job

---

## 2. Architecture

New CLI command `careeros discover-and-apply` (`careeros/cli/discover_and_apply_cmd.py`) — one-shot, idempotent: for each board, run the same discover→score pipeline `browse_cmd` uses (headless forced `True`, no interactive prompts), save every matched job, then auto-apply to jobs scoring at or above a configured threshold using the same fill pipeline `apply_cmd` uses (single-shot cover letter, no review loop). A new `careeros/runtime/automation.py` adds `AutomationRuntime` — a third `AgentRuntime` implementation (alongside `LocalRuntime` and `ClaudeCodeRuntime` from Phase 5) whose `request_approval` always auto-approves, because the threshold check already happened before any `ActionProposal` is constructed. The user schedules `careeros discover-and-apply` themselves via their OS's cron/launchd; CareerOS adds no scheduler or daemon.

---

## 3. Automation Policy

New workspace file `config/automation_policy.json`:

```json
{
  "auto_apply_min_score": 90,
  "max_auto_applies_per_run": 5,
  "boards": ["linkedin", "indeed"]
}
```

- `auto_apply_min_score` (int) — jobs scoring below this are discovered and saved but never auto-applied. This is the score-threshold-as-approval policy: the human decision is made once, in advance, by setting this number.
- `max_auto_applies_per_run` (int) — a hard safety cap on the whole invocation (not per-board), independent of the master spec's ask. Guards against a scoring bug or a flooded job board causing far more auto-submissions than intended with nobody watching. Once hit, remaining eligible jobs are saved but not applied, and the run reports them as skipped.
- `boards` (list[str]) — boards iterated when `discover_and_apply_cmd` runs with no `--board` flag; `--board` overrides for a single-board run.

No file at `config/automation_policy.json` → the command exits 1 with a message telling the user to create one. No silent defaults for a command that can submit real applications.

---

## 4. AutomationRuntime

```python
# careeros/runtime/automation.py
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

`request_approval` always returns approved because the score-threshold check happens in `discover_and_apply_cmd` *before* any `ActionProposal` is constructed — a job below threshold never reaches the apply step, so `AutomationRuntime.request_approval` is only ever called for jobs the policy already cleared. Every resulting `job_applied` activity event carries `agent_runtime: "automation"`; the score and threshold that justified the auto-apply are embedded directly in the activity summary string (e.g., `"Auto-applied (score 94 >= threshold 90) to Acme — Senior SRE"`), since `ApprovalResult` itself is not persisted — only `ActivityEvent` is, per the existing design from Phase 5.

`careeros/runtime/factory.py` gains a third function:

```python
def open_automation_runtime(storage: StorageProvider, session_id: str | None = None) -> AutomationRuntime:
    ctx = open_workspace(storage)
    return AutomationRuntime(storage, ctx, session_id or uuid.uuid4().hex)
```

---

## 5. Cover Letter Generation (No Review Loop)

`apply_cmd`'s cover-letter step has an interactive review loop (`Prompt.ask` for Accept/Regenerate/Quit) that cannot run unattended — there is no stdin in a cron job. `discover_and_apply_cmd` calls `generate_cover_letter(...)` exactly once per auto-applied job and uses the result directly. If generation fails (returns `""`), that job is skipped — logged as a `cover_letter_failed` activity event, not applied — rather than blocking the whole run or silently submitting with a blank cover letter. This is a real quality trade-off against `apply_cmd`'s reviewed letters, accepted deliberately: it is the only option with nobody watching, and skipping preserves the "never submit something broken" invariant over "always submit something."

---

## 6. CLI Command Flow

```python
# careeros/cli/discover_and_apply_cmd.py
@discover_and_apply_app.command()
def discover_and_apply_cmd(
    board: str = typer.Option(None, "--board", help="Single board to run (overrides policy's board list)"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
```

Flow is strictly two-phase: every board is fully discovered and saved before any auto-apply is attempted. This makes "highest score first" in phase two meaningful — it ranks across the combined results of every board in this run, not within one board at a time.

1. Load `config/automation_policy.json` via `runtime.storage`. Missing → `rprint` error, `typer.Exit(1)`.
2. Construct one `AutomationRuntime` for the whole invocation via `open_automation_runtime(_get_storage(workspace))`. One `session_id` covers every board and every job in this run — matches the "one CLI invocation = one session" convention from Phase 5.
3. Determine the board list: `[board]` if `--board` given, else the policy's `boards` list.
4. **Discovery phase (all boards):** for each board in turn, launch browser with `headless=True` (always — never interactive), run the same scraper/scoring pipeline `browse_cmd` uses (`SCRAPERS[board].search`, `fetch_jd_text`, `score_job`), save every matched job via `job.save(runtime.storage)` and `runtime.record_activity(runtime.new_event("job_added", ...))` — identical to `browse_cmd`, no threshold gate on saving. Accumulate all newly-saved jobs (across all boards) into one in-memory list with their scores.
5. **Auto-apply phase (combined, once):** from that accumulated list, take jobs scoring `>= auto_apply_min_score`, sorted by score descending. Walk that sorted list; while the running auto-apply count for this invocation is below `max_auto_applies_per_run`: run the same resume-discovery + single-shot cover-letter + filler pipeline `apply_cmd` uses, call `runtime.request_approval(ActionProposal(...))` (always approved, see §4), then `filler.fill(...)`. On success: update job stage, log `job_applied`, increment the auto-apply count. On cover-letter failure: log `cover_letter_failed`, skip — does not count against the cap. On filler failure: log the same "form fill incomplete" outcome `apply_cmd` does, skip — also does not count against the cap (nothing was actually submitted). Once the cap is reached, stop walking the list — remaining eligible jobs stay at `stage: "saved"` for the next run or manual `careeros apply`.
6. Print a final summary line for cron log capture: `"Discovered: {N}, Auto-applied: {M}, Skipped: {K}"`, where `K` is the combined count of cover-letter failures and filler-fill failures (both are "attempted but not submitted," distinct from jobs simply left below threshold or past the cap, which are not counted as "skipped" — they were never attempted).

---

## 7. Testing

- `tests/test_automation_runtime.py` — unit tests mirroring `LocalRuntime`'s existing test shape: `request_approval` always returns `ApprovalResult(approved=True, reason=...)` regardless of the proposal; `record_activity` stamps `agent_runtime="automation"` and the given `session_id`; `read_workspace`/`write_workspace`/`new_event` behave identically to `LocalRuntime`/`ClaudeCodeRuntime`.
- `tests/test_runtime_factory.py` (extended) — `open_automation_runtime` bootstraps an existing workspace and raises `FileNotFoundError` on a missing manifest, matching the other two factory functions' tests.
- `tests/test_discover_and_apply_cmd.py`:
  - Missing `config/automation_policy.json` → exit 1
  - A job scoring below `auto_apply_min_score` is saved but `filler.fill` is never called for it
  - A job scoring at/above threshold is auto-applied (filler called, stage updated, `job_applied` logged with `agent_runtime: "automation"`)
  - `max_auto_applies_per_run` stops further auto-applies once hit, even with more eligible jobs remaining (they stay at `stage: "saved"`)
  - A cover-letter generation failure (`generate_cover_letter` returns `""`) skips that job (`cover_letter_failed` logged, stage not updated) without crashing the run or affecting other jobs
  - `launch_browser` is always called with `headless=True`

---

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/`
- Activity logs are append-only; no event is ever edited or deleted
- `atomic_write` must use write-to-temp-then-rename (never write directly to final path)
- Activity summaries (including score/threshold text) are built via string concatenation only — no `.format()` or f-strings with user data
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id` with the runtime's own identity
- `max_auto_applies_per_run` is enforced in `discover_and_apply_cmd`, not inside `AutomationRuntime` — the runtime resolves one proposal at a time and holds no run-level state
- `discover_and_apply_cmd` always launches the browser with `headless=True` — never interactive, since there is no terminal to interact with in a scheduled run

---

## Exit Condition

`careeros discover-and-apply` run from cron (or manually, with no terminal interaction required) discovers jobs on the configured boards, saves all matches, auto-applies to every job scoring at or above the configured threshold up to the run's cap, and writes a complete activity trail (`agent_runtime: "automation"`) for every save and every apply — with zero prompts, zero blocking calls, and a clean exit even when cover-letter generation fails for some jobs.
