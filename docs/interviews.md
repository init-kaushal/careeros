# Interview rounds and debriefs

CareerOS keeps one reliable answer to "which interviews are coming up, which ones happened, and which still need a debrief". The agent writes a **round record** for each interview through `careeros interview`; whether a round was held, cancelled, rescheduled or debriefed is a reserved `interview.*` ledger event; everything else is **derived** from those events and the validated records. Nothing about state is stored in a file, and nothing parses `activity.md` for it.

This is not a security boundary. It records and reports; it does not confine the agent. See [Limits](#limits).

```bash
careeros interview upcoming          # scheduled rounds in the next 14 days, soonest first, with each job's prep status
careeros interview needs-debrief     # held rounds still waiting for a debrief, and past rounds that need an update
careeros interview round list        # every round with its label, incomplete ones included
careeros validate                    # the INT checks, with the rest of the workspace
```

The `interview` skill records each round as you describe it, reads `upcoming` to say what is due, records a held or cancelled round only on your say-so, and asks the debrief one question at a time, writing only what you said. The `follow-up` skill lists `needs-debrief` and sends nothing. Run `careeros upgrade` to receive them. Rounds start empty: existing `interview-prep.md` and `activity.md` history is not imported.

## Records

A round record is YAML frontmatter, read with the strict loader (no aliases, anchors, merge keys, duplicate keys or unknown keys), then an optional free-text body that nothing parses. The example is invented.

### Round: `rounds/<rnd_id>.md`

```markdown
---
schema: 1
id: rnd_xxxxxxxxxx
job: job_xxxxxxxxxx
stage: hiring_manager
format: video
scheduled_for: 2026-11-12
interviewers:
  - {name: "Sam Example", title: "Engineering Manager"}
  - {name: "Alex Example", title: ""}
debrief:
  rating: mixed
  questions_asked:
    - "Tell me about a migration you led."
  do_differently:
    - "Open with the outcome, then the detail."
  next_step: "Recruiter to confirm the panel"
  next_step_date: 2026-11-19
---
Notes (ignored by code).
```

- `schema` is `1`. `id` is a `rnd_` id, equals the file stem, and is immutable. `job` is the id of an existing job and is immutable.
- `stage` is `recruiter_screen`, `hiring_manager`, `technical`, `system_design`, `panel`, `final` or `other`. `format` is `phone`, `video`, `onsite`, `take_home` or `other`.
- `scheduled_for` is an ISO date (`YYYY-MM-DD`) or a UTC datetime (`YYYY-MM-DDTHH:MM:SSZ`).
- `interviewers` holds at most 10 mappings with exactly `name` and `title`; `title` may be empty.
- `debrief` is optional and written by `careeros interview debrief`: `rating` is `strong`, `mixed` or `weak`; `questions_asked` and `do_differently` each hold at most 10 strings; `next_step` is a string; `next_step_date` is an optional ISO date. The rating is your own words mapped to one of the three values; no code and no skill invents one.
- Every string is at most 500 characters, with no control, bidi or invisible characters (the same checks as outreach records).
- Any problem makes the record invalid (`INT001`). An invalid record feeds nothing and is never repaired. The record carries **no state**.

## Events

The type prefix `interview.` is reserved: `careeros ledger append` refuses it. Each event is what you told CareerOS, not checked against anything else.

| Event | Entity | Written by | Meaning |
|---|---|---|---|
| `interview.round_added` | `rnd_` | `round add` | the record exists; `artifacts` carry the file, its hash, `job:<job id>` and `when:<scheduled_for>` (the date the ledger recorded) |
| `interview.held` | `rnd_` | `round held` | the round took place; `source` `user` or `observed` |
| `interview.cancelled` | `rnd_` | `round cancel` | the round was cancelled; needs a reason; `source` `user` or `observed` |
| `interview.rescheduled` | `rnd_` | `round reschedule` | `artifacts` carry `when:<new scheduled_for>`; the record's `scheduled_for` is rewritten first, in the same operation |
| `interview.debriefed` | `rnd_` | `debrief` | `artifacts` carry the record's file and hash at that moment (an audit trail, not a check) |
| `interview.rejected` | an existing `rnd_` | any refused command on an existing round | audit only; needs a reason; deduplicated on the round's latest event; never changes any state |

An unknown `interview.*` type is `INT005`. A malformed event (a missing or unsupported `source`, a `cancelled` event without a reason, a bad entity id, a missing or invalid `job:` or `when:` element on `round_added`, a missing or invalid `when:` element on `rescheduled`) is reported as `INT005` and is not applied: it changes no flag, label or view, a round that has only malformed events gets no flags entry, and a later valid event of the same kind still counts. The commands always write well-formed events, so only a hand edit or a foreign writer can produce one.

## What each result is derived from

| Result | Derived from |
|---|---|
| Round identity, which events happened, `held`, `cancelled`, `debriefed` | the ledger only |
| `scheduled_for`, `stage`, `format`, interviewers, the debrief contents | the validated record (editable source data) |
| `needs_update`, upcoming, overdue | events plus the record's `scheduled_for` |
| Prep status | the job's `interview-prep.md` plus `draft.checked` events |
| A round whose record is unusable | identity, label and event history from the ledger; every record field unavailable (see [Incomplete rounds](#incomplete-rounds)) |

A hand edit of a record can change what the views display. It never changes a round's identity, whether it was held or cancelled, or whether a debrief event happened. An orphan record (no `round_added` event) or one whose `id` or `job` disagrees with its event feeds nothing.

## Derived state

**Flags** come from events: `added_at`, `held_at`, `cancelled_at`, `debriefed_at`, `cancel_reason`. The **label** is the first match of `cancelled`, `debriefed`, `held`, `scheduled`.

**Transitions.** `held` needs `scheduled`; `cancelled` needs `scheduled`; `rescheduled` needs `scheduled` and may happen any number of times; `debriefed` needs `held` and no earlier `debriefed`. Each of `held`, `cancelled` and `debriefed` happens once at most. `cancelled` is final: a cancelled round is never reopened (add a new round). Anything else is refused `wrong_state`.

**Flags derived with the clock.** `needs_debrief` is the label `held`. `needs_update` is the label `scheduled` with the record's `scheduled_for` in the past: a date-only value is overdue once the clock is at or after the start of the next UTC day; a datetime is overdue once the clock is after it.

**Prep status** of a round, by its job: `none` if the job has no `interview-prep.md`; `checked` if the file's current text hashes (the same way `careeros check` hashes a draft) to the `sha256` of a `draft.checked` event whose entity is the job and whose action records a pass; otherwise `drafted`. The prep file is read as a regular file, never through a symlink, and only up to 1 MiB; anything else is `none`. Editing the file after its check makes it `drafted` again. Only a passing check of the saved file itself counts: `careeros check jobs/discovered/<job-slug>/interview-prep.md --against <job> --record`. The interview skill's evidence gate checks the STAR outlines and drafted answers before the file is written, not the saved file, so following the skill alone leaves the round `drafted`.

**`careeros interview upcoming`** lists the rounds labelled `scheduled` that are not `needs_update` and whose `scheduled_for` is within the chosen number of days of now, inclusive (default 14 days; `--days` takes a whole number from 0 to 3650, a command-line bound and not a rule), soonest first, then by id. Each row carries the job, stage, format, date, interviewers and prep status.

**`careeros interview needs-debrief`** has two groups. `needs_debrief` holds the held rounds, oldest `held_at` first. `needs_update` holds the overdue scheduled rounds, oldest `scheduled_for` first; rounds on a job in a terminal status (`ACCEPTED`, `REJECTED`, `WITHDRAWN`) are left out of this group, since they are stale by definition. A held round on a terminal job still appears in `needs_debrief`: the lessons are still worth capturing.

Orderings are total and do not depend on the order of the input. No output is capped or truncated: `upcoming`, `needs-debrief` and `round list` return every matching round, however many, so an old held interview never drops out of the debrief queue; the only window is the `--days` value you choose. Rows carry `incomplete` and `date_unrecorded` where they apply. `upcoming` and `needs-debrief` only read. They create no file, event or lock file.

## Incomplete rounds

A round is **incomplete** when it has a valid `round_added` event but no usable record: the file is missing (`INT003`), invalid (`INT001`) or disagrees with its event (`INT004`). It is never silently recreated or repaired.

- Kept from the ledger: the id, the job (from the `job:` element), `added_at`, the label, `held_at`, `cancelled_at`, `debriefed_at`, `cancel_reason` and the event history. Unavailable: the stage, format, `scheduled_for`, interviewers and debrief (`null`), with `record` set to `missing`, `invalid` or `mismatched`.
- `round list` includes it (`incomplete: true`). `round show` returns its identity, label, flags and history, with the unavailable fields `null` and the `record` status.
- `upcoming` excludes it, since it has no date. In `needs-debrief`, the `needs_update` group excludes it (that group depends on the date), and the `needs_debrief` group includes a held incomplete round, flagged, because that group is decided by events alone.
- `round held` and `round cancel` still work (they write events only). `round reschedule` and `debrief` are refused `record_unavailable`: there is nothing to write into.
- Recovery is yours: restore the file from a backup or version control, hand-create `rounds/<id>.md` with the same `id` and `job` as its event (the recorded `sha256` is an audit trail, not a check), or add a new round.

## Commands

Every command takes `--workspace` and `--json` (one JSON object once the workspace is found).

| Command | Does |
|---|---|
| `careeros interview round add --job <job> --stage <stage> --format <format> --when <date>` | write the record, then `interview.round_added`; add `--interviewer "Name:Title"` once per interviewer |
| `careeros interview round list` / `careeros interview round show <rnd_id>` | list every round (add `--job <job>` for one job) or show one with its history (read-only) |
| `careeros interview round held <rnd_id> --source <user\|observed>` | `interview.held` (`--source` defaults to `user`) |
| `careeros interview round cancel <rnd_id> --reason "<why>" --source <user\|observed>` | `interview.cancelled` |
| `careeros interview round reschedule <rnd_id> --when <date>` | rewrite `scheduled_for`, then `interview.rescheduled` |
| `careeros interview debrief <rnd_id> --rating <strong\|mixed\|weak> --next-step "<what happens next>"` | write the debrief block, then `interview.debriefed`; add `--asked "<question>"` and `--improve "<what to change>"` once per item and `--next-date <date>` if there is one |
| `careeros interview upcoming` | the scheduled rounds ahead, with prep status (read-only; `--days N`) |
| `careeros interview needs-debrief` | the held rounds without a debrief and the overdue scheduled rounds (read-only) |

For example:

```bash
careeros interview round add --job example-corp-platform --stage technical --format video --when 2026-11-12T15:00:00Z --interviewer "Sam Example:Engineering Manager"
```

State-changing commands take the workspace lock, verify the ledger's hash chain, read the clock once after the lock, refuse a clock earlier than the latest event of any type ([the clock floor](approvals.md#the-clock-floor)), write the file first and the event second in one transaction, and roll both back on any failure. `round add` refuses a job in a terminal status (`wrong_state`) and a live (not cancelled) round with the same job, stage and date (`duplicate`). `round held`, `round cancel` and `debrief` may be recorded after the job reached a terminal status: a debrief after a rejection is often the most useful one. `debrief` is refused unless the round is `held`. A refusal on an existing round appends at most one deduplicated `interview.rejected` and changes no derived state; a refusal on an unknown round appends nothing.

**Exit codes:** 0 ok; 1 refused (the code is printed, and in `--json` is the `code` field), or a ledger or disk failure; 2 usage, or no workspace.

## Refusal codes

| Code | Meaning |
|---|---|
| `not_found` | no such round or job |
| `wrong_state` | the transition is not legal (a second `held`, `held` after `cancelled`, `debrief` before `held`), or `round add` on a job in a terminal status |
| `duplicate` | a live round with the same job, stage and `scheduled_for` already exists |
| `invalid_input` | a malformed value: a bad enum or date, an empty `--reason`, too many interviewers or debrief items, a string too long or with control characters (a missing required option is a usage error, exit 2) |
| `record_unavailable` | the round is incomplete (its record is missing, invalid or mismatched), so `round reschedule` and `debrief` have nothing to write into |
| `clock_skew` | the clock is earlier than the latest event of any type |
| `ledger_corrupt` | the ledger's hash chain does not verify |

## Rules

A versioned rules module holds the numbers. Changing any of them is a rules version bump. The last three bound inputs (record and command validation) and nothing else.

| Constant | Value | Meaning |
|---|---|---|
| `UPCOMING_DEFAULT_DAYS` | 14 | how far ahead `upcoming` looks by default |
| `MAX_INTERVIEWERS` | 10 | interviewers per round |
| `MAX_DEBRIEF_ITEMS` | 10 | entries in `questions_asked` and in `do_differently` |
| `MAX_TEXT` | 500 | characters in any string of a record |

## Interrupted updates

Rollback covers an operation that raises; it does not cover a process that is killed. The file is replaced atomically and the event is appended atomically, and the file is written first, so a death between them leaves the new file and no event.

- **`round reschedule`:** the record holds the new date and the ledger last recorded the old one. The views use the record's date, `INT008` reports the difference, and `round show` and view rows carry `date_unrecorded: true`. To finish it, re-run `careeros interview round reschedule <rnd_id> --when <date>` with the intended date: it is allowed even though the record already holds that date, rewrites identical bytes and appends the event. A hand edit of `scheduled_for` looks the same and has the same warning and remedy.
- **`debrief`:** the record holds a debrief block but there is no `debriefed` event. The round stays `held`, so it stays in `needs-debrief`. No view and no `round show` treats the unrecorded block as a debrief (`debrief_recorded: false`), and `INT009` reports it. To finish it, re-run `careeros interview debrief <rnd_id>` with the same answers; it replaces the unrecorded block.
- **`round add`:** an orphan record (`INT002`), which feeds nothing; a retry creates a new round.
- **`round held`, `round cancel`:** one event and no file; nothing to interrupt.

The workspace lock is released when its process dies, so the next command does not wait. Nothing repairs anything automatically, and `careeros validate` only reports.

## Validation codes

`careeros validate` is read-only: it creates, changes and removes nothing. A workspace without `rounds/` and without `interview.*` events is unchanged. A corrupt ledger line never crashes the section, and record files are read as regular files, never through symlinks, with the same size bounds as the outreach records.

| Code | Severity | Meaning |
|---|---|---|
| `INT001` | error | a round record is unreadable or has the wrong shape: an unknown key, a bad enum or date, malformed `interviewers` or `debrief`, control characters |
| `INT002` | warning | an orphan record file with no `round_added` event; it feeds nothing |
| `INT003` | warning | a round with a `round_added` event whose record file is missing: the round is incomplete; restore the file or hand-create a record with the same `id` and `job` |
| `INT004` | error | a record's `id` or `job` disagrees with its event, or one round id appears under two jobs |
| `INT005` | error | an illegal sequence (a second `held`, `held` after `cancelled`, `debriefed` before `held`), an unknown `interview.*` type, or a malformed event (reported, never applied) |
| `INT006` | warning | the round's `job` names no saved job |
| `INT007` | warning | status consistency: a round is `held` or `scheduled` while its job is still before the interview stages (`DISCOVERED` through `APPLIED`, including `APPROVAL_REQUIRED`), or a round is still `scheduled` or `needs_update` after its job reached a terminal status |
| `INT008` | warning | a complete round's record `scheduled_for` differs from the date its latest `round_added` or `rescheduled` event recorded (an interrupted reschedule or a hand edit); re-run `round reschedule` with the intended date |
| `INT009` | warning | a complete round's record has a `debrief` block but the round has no `debriefed` event (an interrupted debrief or a hand edit); re-run `debrief` |
| `INT100` | info | a round is `needs_update` |

`INT007` names the command you may want: `careeros transition` for a job before the interview stages, and `round cancel` or `round held` for a terminal job (`careeros transition` refuses a terminal job). Nothing is ever run for you. The stage is deliberately not compared with the job's exact status: interviews do not always follow the pipeline's order.

## Limits

- Out of scope: a reusable story bank, offer tracking, "waiting on a response" and thank-you reminders, email follow-ups, linking interviewers to outreach person records, a GPT workspace counterpart (the GPT workspace is unchanged), learning from debrief ratings, and importing existing `interview-prep.md` or `activity.md` history (it stays a readable log that nothing parses).
- The skills are pinned by their wording only and were not run live.
- This is not a security boundary.
- Truncating the tail of `ledger.jsonl` cannot be detected by a hash chain.
- The clock floor is fail-closed: a clock behind the latest event refuses every state-changing command (`clock_skew`) until it catches up.
- Debrief ratings are your own self-report and are not verified. `held` and `cancelled` are what you say happened.
- An interrupted update is reported (`INT008`, `INT009`) and recovered by re-running the command, never repaired automatically.
- A held round whose record is gone stays in the debrief queue until the record is restored.
- A duplicate round means the same job, stage and exact `scheduled_for` text: a date-only value and a datetime on the same day are both accepted.
- Prep counts as `checked` if any passing check of the same text exists, even if a later check of that text failed.
- Rewriting a record drops YAML comments in its frontmatter and normalises body line endings.
- An interviewer name cannot contain a colon: `--interviewer "Name:Title"` splits at the first colon.
- A malformed `interview.*` event is reported (`INT005`) and not applied, so the views may show a round as if that event had never been written.
- No view or list is truncated, so a workspace with many rounds prints them all.
- The interview skill's STEP 6 still edits the job file's `Status:` line and the pipeline icon by hand, as before, which may produce `JOB005` or `PIPE003` warnings. Rounds themselves never change a job's status.
