# Spec → Implementation Divergences

A running log of things a design spec (master or phase-level) committed to that were never
built, and were never recorded as a deliberate "deliberately out of scope" deferral at the time.
This is distinct from each phase spec's own honest non-goals list — those are decisions; this is
a gap-tracking mechanism so future phase reviews catch drift before it compounds.

Populated from the 2026-09-20 external review. Update this file whenever a future review, or a
phase's own retrospective, finds another one — and once a gap fully closes, move its row out of
the open table below and into the "Closed" section, noting which phase closed it, rather than
deleting it: the point is to preserve the history of what was open when, not just the current
state.

| Spec commitment | Source | Status |
|---|---|---|
| Workspace discovery via `--workspace` flag → `CAREEROS_WORKSPACE` env var → config file (three tiers) | Master §3 | **Partly closed by Phase 12a.** The env var tier is now implemented in `careeros/runtime/factory.py`'s `resolve_storage`, and honored by `careeros outreach send`, `careeros outreach mark-referral-requested`, `careeros people update`, and `open_agent_runtime`. `browse_cmd`, `browser_cmd`, `job_cmd`, `apply_cmd`, `research_cmd`, `discover_and_apply_cmd`, `resume_cmd`, and `workspace_cmd` still each carry their own private `_get_storage` helper (flag → config file only) that does not read `CAREEROS_WORKSPACE` — see the new entry below. `careeros/cli/portability.py` (`careeros export`) does not read it either — it resolves the same flag-then-config-file precedence inline, with no private helper of its own. **The hazard this splits between:** a user or agent who follows `docs/agent-integration.md`'s advice to export `CAREEROS_WORKSPACE` while also having a workspace path saved in the global config (from `careeros onboard`) gets the three converged commands writing to one workspace tree and every other command writing to another — outreach history in one tree, applications and job data in the other, with no error or warning. Until convergence lands, pass an explicit workspace path everywhere, or keep the env var and the configured path pointed at the same directory. |
| Activity events carry a `reason` field ("why did CareerOS do that?") | Master §4.3 | **Partly closed by Phase 12a.** `careeros/operations/approvals.py`'s `resolve_approval` now populates it on `approval_granted` and `approval_declined` events, sourced from `ApprovalResult.reason`. That source is itself populated on every path that produces one today: `LocalRuntime.request_approval` now supplies a short reason ("approved/declined at a terminal confirmation prompt") rather than leaving it `None`, so the CLI path carries a real reason too, not just automation/external-agent callers that already supplied one. Every other event type still leaves `reason` `None`. |
| Activity event `status: "blocked"` | Master §4.3 | Never emitted — there's no policy engine yet to produce a blocked outcome (tracked as Phase 9) |
| `cat jobs/shortlisted/<id>.json` shows full match reasoning | Master §7, Phase 2 exit condition | Score and reasoning are computed and displayed at browse time, then discarded — `Job` has no field to persist either one |
| `careeros history` — a reader for the activity log | Master §8 | Never built. The audit trail exists (append-only, faithfully written) but has no command to read it back; `workspace status` shows only the most recent line |
| `careeros approvals`, `careeros settings`, `careeros discover`, `careeros jobs` | Master §8 | Not built as named — some functionality is covered under different command names (`job`, `browse`), some isn't covered at all (`approvals`, `settings`) |
| `CredentialProvider` interface for real external auth | Master §6, "Phase 3+" | Not built — `careeros/mailer.py` reads `os.environ` directly instead |
| `profile/skills.json` — "evidence-backed, each skill has source + date" | Master §4.2 | Partial: the `Skill` model has both fields, but `profile_extract` only ever sets `source="resume"` and never sets `last_used` |
| `discover-and-apply` is idempotent | Phase 6 spec §2 | False as shipped — `make_job_id` includes a random suffix and there's no dedup, so re-running against the same postings creates new `Job` records and can re-submit real applications. Asserted as a property, never tested. Gated pending Phase 9/10 (see `ROADMAP.md`) |
| Filler failure inside `discover-and-apply` logs "the same outcome `apply_cmd` does" | Phase 6 spec §6.5 | Not implemented as specified — the failure path was a bare `except Exception` with no activity event, fixed as part of addressing the 2026-09-20 review (see git history around that date for the fix commit) |

## Closed

- **Outreach approval "blocks on send until approved" via an `approvals/` queue** (Master §7,
  Phase 3 exit condition) — **closed by Phase 12a.** A durable `Approval` record
  (`careeros/core/models.py`) now exists under `approvals/`, written synchronously by whichever
  process proposes the action, and `careeros.operations.approvals.list_pending` is the poll
  surface a future external runtime uses to rediscover open decisions. No notification and no
  expiry exist — a caller has to poll `list_pending` itself; nothing pushes a decision or ages one
  out.

## New deferrals recorded by Phase 12a

- `browse_cmd`, `browser_cmd`, `job_cmd`, `apply_cmd`, `research_cmd`, `discover_and_apply_cmd`,
  `resume_cmd`, and `workspace_cmd` each keep a private `_get_storage` helper that duplicates logic
  also living in `careeros.runtime.factory.resolve_storage`, and none of them read
  `CAREEROS_WORKSPACE`. A deliberate deferral: converging them is mechanical but touches eight
  command modules and their test suites, none of which Phase 12a otherwise opens. `outreach_cmd`
  is the one worked example of the target shape: Phase 12a removed its `_get_storage` in favor of
  `factory.resolve_storage`, so whoever converges the rest has a live pattern to copy. Two of the
  eight — `apply_cmd` and `discover_and_apply_cmd` — are rewired onto the operations layer by
  Phase 12b regardless, so that rewiring is the natural place to also pick up
  `factory.resolve_storage`; this deferral shrinks to six on its own once 12b lands, and there is
  no reason to converge those two here first.
- `queue_only` (`careeros/operations/approval_queue.py`) is the only shipped approval callback for
  out-of-process use, and it only ever denies. A runtime wanting genuine asynchronous approval —
  propose now, a human approves on another machine hours later — has the durable `Approval` record
  it needs, but no notification, expiry, or locking around it.
- The residual concurrency window in `careeros.operations.approvals.require_state`: it is a
  read-then-compare with no compare-and-swap and no lock file over `approvals/`. Two processes
  racing `execute_outreach_send` against the same approval id could both read the `approved` state
  before either writes `executed`, and both proceed to send. `execute_outreach_send` now calls
  `mark_executed` before calling `send_email`, which closes the narrower window where a crash
  mid-send left an approval that a second process could still execute — but it does not close the
  concurrent-race window.
- **The `outreach_send_approved` activity event is no longer emitted by any code path.** It has
  been replaced by `approval_requested` (logged when the approval is opened) plus
  `approval_granted` (logged when it is decided) — together these carry strictly more information
  than the old event did: the old trail never recorded that a decision was *sought*, and
  `approval_granted` carries the decision's `reason`. Any log tooling keyed on the literal string
  `outreach_send_approved` will find it absent going forward.
- **`outreach_drafted` is now logged once per draft rather than once per send.**
  `propose_outreach_send` persists the drafted `OutreachMessage` and logs `outreach_drafted` before
  any human review happens, so a user who regenerates the same outreach N times produces N + 1
  `outreach_drafted` events where the previous inline command logged exactly 1 (at send time).
  Accepted as more truthful — each one genuinely was a draft — but it changes the activity log's
  shape for anyone counting drafts against sends.
- **Quitting `careeros outreach send` at the review prompt now persists workspace state where it
  previously persisted none.** Because `propose_outreach_send` already wrote the draft and opened
  a `pending` `Approval` before the review loop runs, answering "q" leaves behind an
  `outreach/<message_id>.json`, an `outreach_drafted` activity event, and — after the fix that
  resolves this path the same way the declined path already did — a resolved (`declined`) approval
  and an `outreach_send_declined` event. Before this branch, quitting the old inline command exited
  before anything was written at all. This is a real change in what a quit leaves on disk, alongside
  the two activity-log shape changes recorded above.
