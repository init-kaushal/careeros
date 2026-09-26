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
- **Workspace discovery via `--workspace` flag → `CAREEROS_WORKSPACE` env var → config file (three
  tiers)** (Master §3) — **fully closed by Phase 12b** (Tasks 6 and 8), after Phase 12a closed only
  the env-var tier's implementation. Every command module under `careeros/cli/` now routes through
  `factory.resolve_storage`: `apply_cmd`, `browse_cmd`, `browser_cmd`, `discover_and_apply_cmd`,
  `outreach_cmd`, `research_cmd`, `resume_cmd` call it directly; `job_cmd` and `workspace_cmd` call
  it through a same-named `_get_storage` wrapper kept only for its `typer.Exit`-on-
  `WorkspaceNotConfigured` handling; `careeros/cli/portability.py`'s `careeros export` calls it
  inline. `careeros onboard` and `careeros import` are the two exceptions, and correctly so: both
  are workspace *creation*, not discovery — `onboard` is the command that writes the global config
  file the third tier reads, and `import_workspace_cmd` takes a required `--dest` and writes that
  same config itself after extracting a zip there, rather than consulting any of the three tiers.
  Neither has a prior workspace to discover. The split-workspace hazard this used to carry —
  exporting `CAREEROS_WORKSPACE` while a different path was saved in the global config sent some
  commands to one workspace tree and the rest to another, silently — no longer exists among the
  commands that discover a workspace rather than create one: they all consult the same three tiers
  in the same order, so they agree with each other. A user with `CAREEROS_WORKSPACE` exported who
  runs `onboard` (or `import`) still creates a workspace and writes it to config while every
  discovering command then prefers the env var over that new config entry — that is not a bug in
  this closure, since neither command claims to discover, but it means "always agree" would
  overclaim if stated without that scope.

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
  no reason to converge those two here first. `careeros/cli/portability.py` (`careeros export`)
  shares the same divergence — it also ignores `CAREEROS_WORKSPACE` — but it is not one of the
  eight above because it has no private `_get_storage` helper to converge: it resolves the
  flag-then-config-file precedence inline in `export_cmd`. It is deferred for the same reason as
  the eight, and picking it up alongside them (or alongside `factory.resolve_storage` directly) is
  the natural fix.

  **Closed by Phase 12b.** See the `Closed` section above — every remaining command module and
  `portability.py` now route through `factory.resolve_storage`, and the `_get_storage` name that
  survives on `job_cmd` and `workspace_cmd` is a thin wrapper over it, not a separate resolution
  path.
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

## New deferrals and behavior changes recorded by Phase 12b

- **`discover-and-apply` now caps the JD text handed to cover-letter generation at 4000
  characters**, where it previously passed the full text. This is only a cap, not a staleness
  regression: an earlier fix round in this same plan (`ba8273d` and its ancestors) already ensured
  the text being capped is this run's freshly-fetched JD, not a stored, possibly-stale
  `job.description` — see `discover_and_apply_cmd.py`'s `propose_apply(... jd_text=p["jd_text"][:4000])`
  call and its adjacent comment. The cap itself matches what `apply_cmd`'s interactive path has
  always applied via `propose_apply`'s own default (`(job.description or "")[:_JD_CAP]`,
  `_JD_CAP = 4000` in `careeros/operations/apply.py`) — this closes a divergence between the two
  callers, it does not introduce a new one, but a cover letter for a JD longer than 4000 characters
  will now draft from a truncated view either way.
- **`discover-and-apply`'s `job_applied` event summary no longer embeds the score and threshold
  that triggered the auto-apply.** Before this refactor, the event logged directly by the old
  inline command's success branch read "Auto-applied (score N >= threshold M) to Company — Title".
  The shared `_mark_applied` helper both `apply_cmd` and `discover_and_apply_cmd` now call through
  `execute_apply` logs only "Applied to Company — Title with <resume path>". The score and
  threshold are not lost — they live in the approval's `summary` (built by
  `discover_and_apply_cmd.py` before calling `propose_apply`), which is carried verbatim by the
  `approval_requested` event when the approval opens and again, prefixed "Approved: ", by
  `approval_granted` when it is decided — both logged against the same `entity_id` (the `job_id`)
  as `job_applied`. Reading them together, not `job_applied` alone, reconstructs what the old
  single event said in one line.
- **A successful `careeros apply` now writes three activity events where it wrote one.** Before
  this refactor, `apply_cmd`'s inline flow logged exactly `job_applied` on success — no `Approval`
  record existed to log a request or a grant against. Now `propose_apply` logs
  `approval_requested`, `resolve_approval` logs `approval_granted`, and `execute_apply`'s
  `_mark_applied` logs `job_applied` — the same three-event shape Phase 12a already gave outreach,
  extended to apply.
- **Apply is now digest-bound on the profile, in addition to the cover letter and resume.**
  Editing `profile/profile.json` between approving an application and executing it now fails with
  `ArtifactChanged` — by design, not a bug: `filler.fill` reads the profile to populate the
  application form's fields, so an edited profile between approval and execution would change what
  gets typed into the form without a new review, exactly as an edited draft would change what an
  approved outreach send actually says. See `docs/agent-integration.md` §11.3.
- **`careeros export`'s default zip filename now derives from the fully resolved workspace path**,
  where it previously derived from the path as given (only `~` expanded, symlinks left alone).
  `export_cmd` now builds `ws_root` from `storage.resolve(".")`, which is
  `LocalFilesystemStorage`'s own `Path(...).resolve()` — the same call that follows symlinks for
  every other storage operation — rather than `Path(ws_path).expanduser()`. A `--workspace` (or
  configured, or `CAREEROS_WORKSPACE`) path that is itself a symlink now produces a default zip
  filename from the real target directory's name, not the symlink's own name. Zip contents are
  unaffected; this changes only the default output filename when `--output` is omitted.
- **Two residual duplicate-submission windows in `execute_apply` itself, neither fixed at the
  source, in the same family as the concurrency window already recorded above for outreach.**
  Both were parked pending the final whole-branch review, which dissented from an in-function fix
  (an `except BaseException` handler risks masking a real interrupt) and instead closed the
  practical resubmission risk one layer up, at `discover_and_apply_cmd`'s eligibility check: a new
  `careeros.operations.approvals.has_executed_approval` helper lets it skip a job whose
  `apply_to_job` approval is `executed` while `applied_at` is still `None` — logging a distinct
  `apply_outcome_unknown` event rather than silently excluding the job forever. That closes the
  resubmission risk for the *scheduled* path for both windows below. It does nothing for `apply_cmd`
  (interactive): there is no equivalent scan there, so a human re-running `careeros apply <job_id>`
  after either window fires could still resubmit. Neither window is the case
  `ApplyResult.teardown_failed` warns about — that field is only set on the ordinary `Exception`
  teardown failure that already returns normally (§11.4's "looks like a bug but isn't"), not on
  either window below, both of which end in an uncaught propagation with no `ApplyResult` returned
  at all.

  **Be precise about what `executed` + `applied_at is None` actually implies.** Windows 1 and 2
  below are both post-submit: `filler.fill` already returned `True` before either one fires. But
  the identical durable state — `executed`, `applied_at` still `None` — is also reached by an
  interrupt anywhere *after* `mark_executed` and *before* a submission ever happened at all: a
  Ctrl-C or `SIGTERM` during browser launch or mid-fill, with nothing sent. That case is at least
  as likely as the two below, and it is the one where `has_executed_approval`'s permanent skip
  costs a legitimate, never-submitted application rather than merely avoiding a duplicate one. The
  durable record cannot distinguish the two — skipping is still correct, since it trades a
  possibly-missed legitimate application for certainly avoiding a duplicate real submission to a
  real employer — but an operator triaging `apply_outcome_unknown` should not assume the
  application went out.
  1. A `BaseException` — a `KeyboardInterrupt` from Ctrl-C, a `SystemExit` raised by a signal
     handler installed for `SIGTERM`, or any other exception that is not an `Exception` subclass —
     landing during browser context teardown, after
     `filler.fill` already returned `True`, is caught by nothing in `execute_apply`: the function's
     only exception handlers catch `ImportError`, `BrowserProfileBusy`, and `Exception`, none of
     which match a bare `BaseException`. It propagates out of `execute_apply` uncaught, leaving the
     approval `executed` (`mark_executed` already ran before the browser was launched) and the
     job's `applied_at` unset (`_mark_applied` never ran). Neither `apply_cmd` nor
     `discover_and_apply_cmd` catches a bare `BaseException` either, so it propagates out of the CLI
     the same way it does out of `execute_apply` — a bare traceback in both, not a caught-and-logged
     failure.
  2. A failure inside `_mark_applied` itself — the job-save or its `record_activity` call, on the
     line immediately following the `try`/`except` block that wraps the browser launch and fill —
     is not caught by that block either, because the call sits after it, not inside it. It escapes
     `execute_apply` with the identical result: approval `executed`, job `applied_at` unset. The two
     CLI callers do not handle this identically: `discover_and_apply_cmd`'s per-job loop has a bare
     `except Exception` that logs a durable `apply_error` activity event before continuing to the
     next job, so this window does leave a trace in the unattended path. `apply_cmd` has no
     equivalent `except Exception` — only `BoardSessionRequired`, `BrowserUnavailable`,
     `FillIncomplete`, and `OperationError` are caught — so the same failure surfaces as a bare,
     unlogged traceback to the interactive user instead. This one is pre-existing, not new to Phase
     12b: the pre-refactor `apply_cmd.py` had the same unguarded `job.save(...)` /
     `record_activity(...)` pair after a successful `filler.fill`, with no surrounding
     `try`/`except` there either — the refactor moved this code into `careeros/operations/apply.py`
     without changing that structural property.
- **A crash inside `resolve_approval` itself, between the `Approval.save` that moves a record to
  `approved` and the `record_activity` call that logs `approval_granted`, leaves a durable
  `approved` record with no matching log entry for the decision that produced it.** Parked during
  Task 7 for the final whole-branch review, which confirmed it is inert rather than a duplicate-
  submission path — but the reasoning that makes it inert is stronger than "nothing currently
  enumerates and re-executes `approved` records", which was the first-pass justification. The real
  guard is the digest binding: `approved` is consumed only via an explicit approval id at
  `execute_apply`'s and `execute_outreach_send`'s call sites, `make_approval_id` appends a random
  suffix so the *next* `propose_apply`/`propose_outreach_send` for the same job mints a fresh id
  rather than reusing this one — and that next propose call also overwrites the cover letter (or
  draft) at the same workspace-relative path. So the stale approval's `cover_letter_sha256` (or
  `draft_sha256`) usually no longer matches what is actually on disk, and executing it would raise
  `ArtifactChanged`, not silently send stale content. Pre-existing, not new to Phase 12b or 12a — a
  crash in this same gap left the same shape of record before either refactor.

  **Amended in Phase 13a:** the digest argument above was load-bearing and it had a hole. A redraft
  that comes back byte-identical to the stale one leaves the digest matching, so executing the
  stranded `approved` id performed the action a second time — reproduced end to end as a duplicate
  follow-up email. `open_approval` now supersedes `approved`-but-unexecuted records as well as
  `pending` ones, so the guard no longer rests on the redraft differing.


## New deferrals and decisions recorded by Phase 13a

These are decisions, not drift — recorded here because the phase spec commits to stating what a
decision does *not* license, and because three of them are properties a future reviewer would
otherwise read as bugs.

- **The LinkedIn Terms-of-Service decision: full automation, risk accepted by the workspace
  owner.** Phase 13b sends connection requests through the isolated browser profile without a
  human in the loop for the browser step itself (the *message* is still per-item approved). This
  was decided explicitly by the owner of the account being automated, which is the only party
  whose account is at risk. The apparent double standard with earlier phases — that Phases 3 and 7
  already read LinkedIn — is resolved by moving the line rather than by grandfathering: the
  relevant boundary is **approved action versus unapproved volume**, not reads versus writes. A
  scripted read and a scripted write are the same kind of act; what makes an act defensible is
  that a human authorised *that* act. On that reading Phases 3 and 7 need no retroactive review,
  and this phase needs per-item approval, which it has.

  What the decision explicitly does **not** license (spec §9), recorded because a decision to
  automate is only defensible alongside its limits:
  - **No evasion of platform controls.** No browser-fingerprint spoofing, no user-agent rotation,
    no proxy rotation, no timing randomisation intended to appear human, and no attempt to detect
    or circumvent rate limiting. The isolated profile exists for session hygiene, not disguise.
  - **No unapproved volume.** One approval authorises exactly one message or one connection
    request. The per-run caps are the user's own restraint, not a limit-avoidance mechanism.
  - **No scraping expansion.** The phase adds a write; it reads nothing new.
  - **No sending to anyone the user did not enter.** Combined with declining email discovery
    below, every recipient is a person the user researched and recorded themselves.

- **Automated email-address discovery: declined, not deferred.** There is no reliable
  non-guessing source for an arbitrary individual's work address. Every available technique is
  pattern-guessing against a domain, and a guess that is wrong means mailing a stranger — an
  unrecoverable act performed on someone who never entered this system. `MissingRecipient` is
  therefore a permanent, deliberate refusal rather than a missing feature: `execute_outreach_send`
  and `execute_follow_up` both stop when `person.email` is absent, and the user supplies the
  address or nothing is sent. This is recorded as *declined* so a future phase does not read it as
  an open gap and implement it.

- **The system cannot detect a reply.** Nothing in CareerOS reads an inbox, so it cannot know that
  a person already answered. `max_touches` therefore guards the system's **own blindness**, not a
  stylistic preference about persistence: it is the bound on how many times CareerOS will write to
  someone whose reply it structurally cannot see. Read as a preference knob it looks arbitrary and
  tunable; read correctly it is the only thing preventing an unbounded one-sided thread. Raising it
  raises exactly that risk.

- **A user who declines every follow-up is re-prompted indefinitely.** `decline_follow_up` advances
  `last_touched_at` but deliberately does **not** increment `touch_count`, because a decline is not
  a touch — nothing was sent. The consequence is that declining defers by one `days_between_touches`
  period rather than counting toward `max_touches`, so a relationship the user keeps declining is
  re-proposed forever. This is why `careeros outreach close` exists, and it is the honest trade: the
  alternative, counting a decline as a touch, would let a user exhaust a cadence without a single
  message being sent.

- **Behaviour change to an existing flow: `execute_outreach_send` now also writes `last_touched_at`
  and `touch_count`.** The initial-outreach path previously set only `sent_at` and `send_state`.
  It now seeds the two cadence fields so a relationship becomes eligible for follow-up on the same
  schedule whether its first message was sent before or after this phase. Records written by an
  earlier version are handled by a read-time fallback rather than a migration
  (`last_touched_at or sent_at`, and `touch_count or (1 if sent_at else 0)` — see
  `check_follow_up_due`), so no stored file is rewritten and a legacy relationship is not excluded
  from the cadence forever.

- **A cron follow-up run re-logs its permanent failures every pass.**

`careeros outreach follow-up` skips a not-due relationship silently and by
design — a daily cron must not fill an append-only activity log with records
of nothing happening. Two cases escape that intent, because neither advances
`last_touched_at`, so the relationship stays due forever and is reconsidered
every run:

- a relationship whose job is **policy-blocked** logs one `policy_blocked`
  event per run (from inside `propose_follow_up`), and
- an **orphaned** `outreach/` record whose job, person or company no longer
  resolves logs one `follow_up_propose_error` per run (from the command).

Measured: one event per affected relationship per run, indefinitely. The run
summary also carries the same non-zero `Blocked:` or `Errors:` counter every
pass, with no way to distinguish a new failure from the same one for the
four-hundredth time.

Deliberately not fixed in Phase 13a, because every cheap fix is worse than the
problem. Pre-checking policy in the command would reintroduce exactly the
duplicated due-ness logic that Phase 13a consolidated into
`check_follow_up_due`. Writing `closed_reason` after N failures would mutate
the user's own relationship data on a technicality — a temporarily missing
company record would permanently close a relationship. Suppressing the event
by reading back the log would make a proposer depend on its own audit trail.

The cost half is already bounded: `max_follow_ups_per_run` caps paid drafting
attempts at 20 per run, and three consecutive drafting failures abort the run.
What remains is log growth and a stale counter, both low-harm. Revisit in
Phase 13b, most plausibly by giving `OutreachMessage` a field that records the
last refusal so a repeat can be recognised rather than re-derived.

- **Four smaller Phase 13a deferrals, recorded so they are not rediscovered as bugs.**
  - *A non-`OperationError` still aborts a scheduled follow-up run.* The enumeration loop in
    `outreach follow-up` guards per record against `OperationError` and against
    `MalformedTouchTimestamp`, but an `OSError` from a workspace write (disk full, permissions)
    escapes and ends the run. Mid-batch state stays coherent — earlier relationships keep their
    pending approvals, and the interrupted one is redrafted next run because `last_touched_at`
    never moved — so this is a robustness gap, not a correctness one. Not fixed because a
    disk-level failure ending a cron run is defensible, where a hand-edited timestamp doing it
    (which *was* fixed) is not.
  - *A crashed `careeros outreach send` can leave a `pending` `send_outreach` approval that no
    interactive command drains.* `outreach review` deliberately filters to `send_follow_up`, and
    `outreach send` always proposes fresh rather than resuming. The record is inert — a `pending`
    approval cannot execute — so this is queue clutter rather than a send risk, but an integrator
    reading `list_pending` will see it and should not assume every pending approval is actionable.
  - *`careeros outreach close` overwrites a `referral_confirmed` `referral_state`.* Closing a
    relationship whose referral was confirmed sets `referral_state="closed"`, so the record no
    longer shows the referral happened; the `cadence_closed` event names the prior state, so the
    append-only log is the only place it survives. Nothing in `careeros/` reads
    `referral_confirmed` except the terminal-state set, so no behaviour depends on it today — but
    a future "which relationships produced referrals?" query would have to read the log, not the
    records.
  - *`careeros/cli/job_cmd.py`'s detail panel still interprets Rich markup.* Phase 13a fixed this
    class of bug in the two places where the displayed text is also *transmitted* (the follow-up
    draft and the cover letter), because there the reviewer must see the exact bytes. `job_cmd`'s
    panel is read-only and its markup is intentional, so it needs per-field
    `rich.markup.escape` rather than the verbatim treatment, and a bracketed span in scraped job
    data can still be dropped from that display or raise `MarkupError`.


## New deferrals and decisions recorded by Phase 13b

13b implements §6 of `docs/superpowers/specs/2026-09-23-phase13-outreach-expansion-design.md`.
Four of the entries below are places where the shipped code is deliberately **not** what that
section says, and every one of them is recorded rather than quietly reconciled.

- **`max_connection_requests_per_run` is forward-looking and does not bind today.** Spec §6.5
  commits the field and justifies it as stopping "an enthusiastic session [burning] through the
  user's allowance in one run". But §6.4 specifies a *single-person interactive* command
  (`careeros outreach connect --job <id> --person <id>`), and one invocation sends at most one
  request — so any cap above 1 can never bind, and the restraint as specified is decorative.

  What shipped is the field exactly as the spec commits it: `CadencePolicy` gains
  `max_connection_requests_per_run: int = Field(default=5, ge=1, le=20)`
  (`careeros/core/models.py:270`), additive and defaulted so no `cadence_policy.json` written
  during 13a fails to load. It is **read nowhere in `careeros/`** — verified by grep, whose only
  other hits are `tests/test_cadence_policy.py` (four tests covering the default, the round trip
  and both validation bounds), this file, and the plan and spec prose. It will bind when a batch or
  scheduled connect command exists, and there is no such command today.

  Recorded as forward-looking rather than redefined. The tempting alternative — reading the
  activity log for a rolling 24-hour quota — would be a unilateral redesign of a user-facing
  restraint into something the spec never described, and inflating a decorative cap into a claim
  of protection is worse than recording the truth. The protection the cap was reaching for is
  instead provided by the duplicate refusal below, which guards a human's perception rather than a
  quota.

- **13b had to add `careeros people update --linkedin-url`, which the spec assumed existed.**
  Spec §6.3 has `propose_connection_request` raise an `EntityNotFound` naming `careeros people
  update` as the remedy, but that command took only `--email`. Worse, `linkedin_url` was populated
  in exactly **one** place — `careeros research people`, via the people-search scraper — so a
  person the user added by hand had no path at all to the field, and every LinkedIn-facing flow was
  permanently closed to them. An error message naming a flag that does not parse is a dead end, so
  the flag was added in Task 1 and the error text now uses its real spelling.

  Related, and a second divergence from the spec's single-refusal picture: the URL is **normalized
  in the operations layer**, not at either writer. `people update --linkedin-url` stores whatever
  the user pasted (a scheme-less `www.linkedin.com/in/jane` is the common case) and `research
  people` stores the scraper's raw href (which on a search page can be root-relative, `/in/jane`),
  so a *researched* person can be as non-canonical as a hand-entered one and normalizing at one
  writer would leave the other. `canonical_linkedin_url` therefore runs at the moment the URL
  becomes load-bearing, and a present-but-unusable URL is the same `EntityNotFound` with the same
  remedy rather than a second error type.

- **`ConnectionAlreadySent` is a new error type beyond spec §7**, which lists only
  `ConnectionNotSent` for 13b. The spec omits duplicate-request protection entirely, which is the
  actual harm this phase can do: a second connection request is *visible in the recipient's
  notifications* and cannot be recalled, and §6.3 itself says "correctness cannot rest on the
  platform's idempotence". The refusal means **one request per human being, ever, across every
  job** — it enumerates `connections/` and keys on `sent_at` rather than on the `(job_id,
  person_id)` record id, because the recipient sees one notification whichever job prompted it, and
  `send_state` is rewritten by a regeneration while `sent_at` is only ever written by a real send.
  It is raised from **both** `propose_connection_request` and `execute_connection_request`.

  Reusing `ConnectionNotSent` was rejected: that type means *the page* declined to complete the
  request, while this one means *careeros* declined to offer it. They mean opposite things to a
  retry — "possibly try again" versus "never again for this person" — and collapsing them would
  force callers to string-match to tell them apart.

  **Known consequence, accepted:** `ConnectionAlreadySent` raised from `execute` leaves the
  approval `approved` forever. The refusal fires before `mark_executed` (correctly — nothing was
  attempted), but it is permanent, so no retry can ever consume that record. Closely related to the
  stranded-approval clutter 13a recorded for a crashed `outreach send`, though one state further on
  — that one strands a `pending` record, this one strands an `approved` one. It is inert either
  way, because the only thing that can act on it is an executor that will hit the same refusal.
  Documented in `docs/agent-integration.md` §13.4 so an integrator enumerating approvals expects
  the shape rather than retrying it.

- **`connection_request_teardown_failed` is a fifth activity event beyond spec §8's four**
  (`connection_note_drafted`, `connection_request_sent`, `connection_request_failed`,
  `connection_request_declined`). Added in Task 5 after finding that a browser-teardown failure
  *after* the connector had already returned `True` would otherwise be recorded as a plain failure
  — which leaves `sent_at` unset, and `sent_at` is the fact the duplicate refusal reads. The next
  `propose` would then draft a **second invitation to somebody who can already see the first one**.
  So the send is recorded as a send, `ConnectionResult.teardown_failed` carries the anomaly to the
  caller, and the anomaly gets its own durable event rather than being hidden inside the success
  one. `ApplyResult.teardown_failed` is the precedent and `careeros outreach connect` warns on it
  rather than printing unqualified success.

- **`CadencePolicy` is never loaded by the connect flow.** `careeros/operations/connect.py` does
  not import it, and `careeros outreach connect` does not read it. This is deliberate and follows
  from the first entry: the only 13b-relevant field in that file is a cap that cannot bind a
  one-person propose, so requiring `config/cadence_policy.json` to exist before a connection
  request could be drafted would make an unrelated 13a config file a hard precondition for an
  unrelated 13b command. The loud-absence property 13a's three required fields give that file still
  holds for the *cadence*; it was never meant to gate LinkedIn.

- **The LinkedIn selectors are unverified against live HTML, and this is not dischargeable by a
  test.** `careeros/browser/connect/linkedin.py`'s class names (`.pv-top-card-v2-ctas`,
  `.dist-value`, `.distance-badge`, `textarea#custom-message`) were written from LinkedIn's markup
  as known at the time, **not** from an observation made during the phase. The behavioural tests
  dispatch on the module's own selector constants, so they prove the control flow and prove nothing
  about the selector strings. Verifying them needs a real authorized session, which makes this
  inherently manual — it is recorded here rather than papered over with a test that would only
  restate the constants.

  Two selector-level safety properties *are* pinned statically, and both are load-bearing rather
  than stylistic:

  - **Every profile-state probe is scoped to the top card** (`_TOP_CARD`, and `_scoped()` which
    distributes it across the selector list). A LinkedIn profile page also renders "People also
    viewed" / "More profiles for you" cards, each carrying its own Connect button and degree badge,
    so an unscoped `button:has-text("Connect")` can resolve to a **different human being** — the
    one failure mode in this file that no later check could catch, since the invitation would go
    out correctly formed to the wrong person. The invite modal's own selectors (`_ADD_NOTE_SELECTOR`,
    `_NOTE_SELECTOR`, `_SEND_SELECTOR`) are deliberately *unscoped*, because the modal renders in a
    portal outside `<main>`.
  - **The send control uses `:text-is("Send")`, never `:has-text("Send")`.** The latter also matches
    **"Send without a note"**, which sits right beside the confirm button in the invite modal —
    clicking it transmits the invitation with the reviewed note silently discarded, which is the
    one outcome a review gate exists to prevent.

  The most markup-dependent selector is the already-connected probe (the `1st` degree badge). When
  it rots it fails **safe**: a connected profile has no Connect button either, so the connector
  returns `False` and the caller sees `ConnectionNotSent` rather than a misdirected invitation.

- **Found in 13b, pre-existing, and FIXED in 13b (`46be03c`): `LocalRuntime.request_approval`
  handed the approval summary to `Confirm.ask` as a raw `str`.** Kept here because this file
  preserves what was open when, not just what is open now — and because the quieter of the two
  failure modes is worth remembering.

  Rich parses console markup in a raw string, and an approval summary is built from scraped data:
  a person's name, a company, a job title. So a closing-tag-shaped span (`Jane [/b] Doe`) aborted
  the confirmation prompt with `MarkupError` before the user could answer — and, worse, an
  ordinary span was silently *deleted*, so a person recorded as `Jane [dim]Doe` produced a prompt
  asking about "Jane Doe" and the user approved a summary that did not match the record. On the
  approval gate specifically, showing the user something other than what is being approved
  defeats the point of the gate. It affected `careeros outreach send` and `careeros apply` as well
  as 13b's new command.

  This was the last instance of the defect class Phase 13a fixed on the follow-up review panel and
  the cover-letter panel. Fixed with `rich.text.Text`, not by importing `careeros/cli/_display.py`'s
  `verbatim()`, because `careeros/runtime/` must not depend on `careeros/cli/`.

  **Recorded also as a process note.** Task 6 found the defect and correctly declined to fix it —
  the file was outside its scope and the blast radius covered two shipped commands. It was fixed
  immediately afterwards, before Task 7 ran. Task 7 then carried the "not fixed" claim forward
  from Task 6's report without re-checking it against the code, and this entry originally shipped
  saying the defect was live. A report is a snapshot of the moment it was written; a later task
  reading one has to re-verify, exactly as it would re-verify a claim in a brief.
