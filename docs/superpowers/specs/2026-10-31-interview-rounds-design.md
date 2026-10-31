# Interview rounds and debriefs (sub-project 4b) — design

**Date:** 2026-10-31
**Status:** drafted for review
**Builds on:** Foundation 0.3.0 (IDs, ledger, workspace lock, validation, state machine), Career Memory 0.4.0 (`careeros check`, the `draft.checked` event), Outreach and follow-up state 0.8.0 (the record + reserved-event + derived-state pattern this reuses).
**Source:** `docs/IMPLEMENTATION_AUDIT.md` §10, sub-project 4 ("Workflow completion: … interview story bank and debrief, offers"). This spec covers only **interview rounds and debriefs**. The story bank and offer tracking are separate later specs.

## 1. Goal

The `interview` skill builds a prep file for one interview and logs a line in `activity.md`. Nothing records which rounds were scheduled, which happened, which were cancelled, or what was learned afterwards, so "what do I have to prepare this week?" and "which interviews have I not debriefed?" are answered by an agent re-reading prose.

This sub-project adds:

- agent-written, validated **round records** (`rounds/<rnd_id>.md`), each with an optional light **debrief**;
- a few reserved **`interview.*` ledger events** for what the user tells the agent (a round was added, held, cancelled, rescheduled, debriefed);
- **state derived deterministically** from those events plus validated record fields, never stored;
- read-only **`careeros interview` views** (`upcoming`, `needs-debrief`) and the commands that record rounds and debriefs;
- an `interview` skill that records rounds and debriefs through those commands.

## 2. Decisions (approved in brainstorming, 2026-10-31)

1. **Scope:** interview rounds and debriefs only. Not the story bank, not offers.
2. **Status link:** rounds are separate from the job's status. The job status changes only through `careeros transition`, as today. `validate` warns when the two disagree (INT007). Recording a round never changes a job.
3. **Views:** (a) upcoming rounds with prep status; (b) held rounds with no debrief. Not included: "waiting on a response" and "thank-you due".
4. **Interviewers:** `{name, title}` entries on the round. No link to outreach person records.
5. **Model:** the approvals/outreach pattern: a record file per round, reserved events, derived state. A **light structured debrief**: rating, questions asked, what to do differently, next step and an optional date.
6. **Prose logs** (`activity.md`, `interview-prep.md`) stay as readable files the skills write; nothing parses them for round state. The one exception is the prep status, which reads the prep file's hash (§6.4).

## 3. Alternatives considered

- **One `interviews.md` per job (edited by hand, validated).** Simplest to read, but no stable round ids, no append-only history, and state would be read from file contents. Rejected for the same reason the audit flagged log-based state as fragile.
- **Events only, no record files.** No drift and nothing to hand-edit, but nothing a person or the existing skills can read. Rejected.
- **Recording a round moves the job status, or status derived from rounds.** Rejected: a recorded round could change the pipeline, or the state machine and pipeline view would have to change. Rounds stay separate with an advisory check.

## 4. Records

### 4.1 Round record: `rounds/<rnd_id>.md`

YAML frontmatter, parsed with the strict loader (no aliases, anchors, merge keys, duplicate keys or unknown keys), then an optional free-text body that nothing parses.

| Key | Rule |
|---|---|
| `schema` | `1` |
| `id` | `rnd_` id (the `round` kind already exists in `careeros/core/ids.py`); **immutable**, must equal the file stem |
| `job` | the id of an existing job; **immutable** |
| `stage` | `recruiter_screen`, `hiring_manager`, `technical`, `system_design`, `panel`, `final`, `other` |
| `format` | `phone`, `video`, `onsite`, `take_home`, `other` |
| `scheduled_for` | an ISO date `YYYY-MM-DD` or a UTC datetime `YYYY-MM-DDTHH:MM:SSZ` |
| `interviewers` | a list of 0 to `MAX_INTERVIEWERS` (10) mappings `{name, title}`; each string at most `MAX_TEXT` (500) characters; `title` may be empty |
| `debrief` | optional mapping (§4.2) |

All strings: no control, bidi or invisible characters (the same checks as outreach records). Any problem makes the record invalid (`INT001`); an invalid record feeds nothing and is never repaired.

### 4.2 Debrief block

`rating` (`strong`, `mixed`, `weak`), `questions_asked` (list of at most 10 strings), `do_differently` (list of at most 10 strings), `next_step` (string), `next_step_date` (optional ISO date). Written by `careeros interview debrief`. The rating is the user's own words mapped to the enum; the skill never invents one (§9).

## 5. Events (reserved prefix `interview.`)

Written only by the commands in §7, with `source` of `user` or `observed` where it applies. Unknown `interview.*` types are errors (`INT005`).

| Type | Entity | Notes |
|---|---|---|
| `interview.round_added` | `rnd_` id | `artifacts = [rel, "sha256:<hex>"]`; action names the stage, job id and date for humans |
| `interview.held` | `rnd_` id | the round took place; `source` `user` or `observed` |
| `interview.cancelled` | `rnd_` id | needs a reason; `source` `user` or `observed` |
| `interview.rescheduled` | `rnd_` id | action names the new date; the record's `scheduled_for` is rewritten in the same operation |
| `interview.debriefed` | `rnd_` id | `artifacts = [rel, "sha256:<hex>"]` of the record at that moment (an audit trail, not a check) |
| `interview.rejected` | existing `rnd_` id | audit-only, deduped, needs a reason; never changes state (same rules as `outreach.rejected`) |

A malformed event (missing or unsupported `source`, a missing reason on `cancelled`, a bad entity id) is reported as `INT005` and **is not applied** to derived state.

## 6. Derived state (pure fold; takes `now`; nothing stored)

### 6.0 What each result is derived from

| Result | Derived from |
|---|---|
| Round identity, which events happened, `held`, `cancelled`, `debriefed` | the ledger only |
| `scheduled_for`, `stage`, `format`, interviewers, the debrief contents | the validated record (editable source data) |
| `needs_update`, "upcoming", "overdue" | events plus the record's `scheduled_for` |
| Prep status | the job's prep file plus `draft.checked` events (§6.4) |

A hand edit of a record can change what the views display. It never changes a round's identity, whether it was held or cancelled, or whether a debrief event happened. An orphan record (no `round_added` event) or one whose `id` or `job` disagrees with its event feeds nothing.

### 6.1 Rules (a versioned rules module)

`UPCOMING_DEFAULT_DAYS = 14`, `MAX_INTERVIEWERS = 10`, `MAX_LIST = 10`, `MAX_TEXT = 500`. Changing a constant is a rules version bump.

### 6.2 Round state

Flags from events: `added_at`, `held_at`, `cancelled_at`, `debriefed_at`, `cancel_reason`. Label, first match: `cancelled`, `debriefed`, `held`, `scheduled`.

Legal transitions (otherwise `wrong_state`): `held` needs `scheduled`; `cancelled` needs `scheduled`; `rescheduled` needs `scheduled` (any number of times); `debriefed` needs `held` and no earlier `debriefed`. Each of `held`, `cancelled`, `debriefed` happens at most once. `cancelled` is terminal; a cancelled round is never reopened (add a new round).

### 6.3 Flags derived with `now`

- `needs_debrief`: label `held`.
- `needs_update`: label `scheduled` and the record's `scheduled_for` is in the past. A date-only value is overdue once `now` is at or after the start of the next UTC day; a datetime is overdue once `now` is after it.

### 6.4 Prep status and the two views

**Prep status** of a round (by its job): `none` if the job has no `interview-prep.md`; `checked` if the file's current text hashes (the same way `careeros check` hashes a draft) to the `sha256` of a `draft.checked` event whose entity is the job and whose action records a pass; otherwise `drafted`. The prep file is read as a regular file, not through a symlink, and at most 1 MiB; anything else is `none`. The producer's action text is shared as a constant between `careeros check` and this reader, and a test pins them together.

**`interview upcoming [--days N]`** (default `UPCOMING_DEFAULT_DAYS`): rounds labelled `scheduled` that are not `needs_update` and whose `scheduled_for` is within `N` days of `now` (inclusive), soonest first, then by id. Each row carries the job, stage, date, interviewers and prep status.

**`interview needs-debrief`**: two groups. `needs_debrief` (held rounds), oldest `held_at` first. `needs_update` (overdue scheduled rounds), oldest `scheduled_for` first; rounds on a job in a terminal status (`ACCEPTED`, `REJECTED`, `WITHDRAWN`) are left out of this group, since they are stale by definition. A held round on a terminal job still appears in `needs_debrief`: the lessons are still worth capturing.

Orderings are total and independent of input order.

## 7. Commands (`careeros interview`)

State-changing commands take the workspace lock, check the chain (`ledger_corrupt`), read the clock **once** after the lock, refuse `clock_skew` when `now` is earlier than the latest event of any type, write the file first and the event second inside a transaction, and roll both back on any failure. A refusal on an existing round appends at most one deduped `interview.rejected`; a refusal on an unknown entity appends nothing. `--json` prints one object. Exit codes: 0 ok, 1 refused, 2 usage error or no workspace. Refusal codes: `not_found`, `wrong_state`, `duplicate`, `invalid_input`, `clock_skew`, `ledger_corrupt`.

| Command | Effect |
|---|---|
| `round add --job <ref> --stage … --format … --when <date> [--interviewer "Name:Title" …]` | writes the record, then `round_added`. Refuses a job in a terminal status (`wrong_state`) and a live round with the same job, stage and date (`duplicate`) |
| `round list [--job <ref>] [--json]`, `round show <rnd_id>` | read-only |
| `round held <rnd_id> [--source user\|observed]` | `interview.held` |
| `round cancel <rnd_id> --reason … [--source …]` | `interview.cancelled` |
| `round reschedule <rnd_id> --when <date>` | rewrites `scheduled_for`, then `interview.rescheduled` |
| `debrief <rnd_id> --rating … [--asked …]… [--improve …]… --next-step … [--next-date …]` | writes the debrief block, then `interview.debriefed`. Refuses unless the round is `held` |
| `upcoming [--days N]`, `needs-debrief` | the read-only views of §6.4; they create no file, event or lock file |

`round held`, `cancel` and `debrief` may be recorded after the job reached a terminal status (a debrief after a rejection is the most valuable one). Only `round add` refuses a terminal job.

## 8. Validation (`careeros validate`, read-only)

A workspace with no `rounds/` directory and no `interview.*` events is unchanged. A corrupt ledger line never crashes the section. Record files are read as regular files, never through symlinks, with the same size bounds and hostile-file handling as the outreach records.

| Code | Severity | Condition |
|---|---|---|
| `INT001` | error | Round record shape: unknown key, bad enum, bad date, malformed `interviewers` or `debrief`, control characters |
| `INT002` | warning | Orphan record file with no `round_added` event; it feeds nothing |
| `INT003` | warning | A round event whose record file is missing; identity is kept from the events |
| `INT004` | error | The record's `id` or `job` disagrees with its event, or one id appears under two jobs |
| `INT005` | error | Illegal sequence (second `held`, `held` after `cancelled`, `debriefed` before `held`, …), an unknown `interview.*` type, or a malformed event |
| `INT006` | warning | The round's `job` names no saved job |
| `INT007` | warning | Status consistency: a round is `held` or `scheduled` while its job's status is before the interview stages (`DISCOVERED` through `APPLIED`, including `APPROVAL_REQUIRED`), or a round is still `scheduled` or `needs_update` after the job reached a terminal status. The message names the `careeros transition` the user may want. Nothing is ever run for them. The stage is deliberately not compared with the exact status: interviews do not always follow the pipeline's order |
| `INT100` | info | A round is `needs_update` |

## 9. Skills

- **`interview`:** after STEP 0 a new step records the round with `interview round add` (stage and interviewers as the user gives them; a round the user says already exists is not added twice). The skill reads `interview upcoming` to state what is due. A new debrief step after a round asks the rating and the other fields one at a time and records only what the user said; it never writes a rating the user did not give. The skill runs `round held` and `round cancel` only on the user's say-so. STEP 6 no longer treats the `activity.md` line as the only record of the round. A refusal ends that action and is relayed to the user. The evidence gate (`careeros check`) and the prep file format are unchanged.
- **`follow-up`:** one short step reads `interview needs-debrief` and lists those rounds. It sends nothing.
- **Unchanged:** `prep`, `offer`, the evaluation and compensation skills, the approvals flow and the GPT workspace. No skill text claims a security boundary or runs `approve`, `resolve` or an unconditional `careeros transition`.

## 10. Docs and release

`docs/interviews.md` (records with invented examples, events, derived state and the §6.0 table, constants, commands and exit codes, refusal codes, `INT` codes with severities, limits), workspace `CLAUDE.md` routing and layout (`rounds/`), README, CHANGELOG `0.9.0` (run `careeros upgrade` to receive the updated skills; rounds start empty; existing `interview-prep.md` and `activity.md` history is not imported), mkdocs nav, version `0.9.0`. `mkdocs build --strict` passes.

## 11. Testing

- **Pure core:** record parsing and the fold against an independently written reference model over random event sequences (including audit-only rejections inserted at random, which must change nothing, and malformed events, which must not apply); the transition table; the date rules for `needs_update` at the exact boundary (date-only and datetime); ordering independent of input order; hostile-input corpora for the record parser; an import-isolation test.
- **Records and commands:** file-first/event-second crash safety by injection, orphans and id mismatches feeding nothing, the record-editing table (which edits change a view and which never change identity), refusal audit (derived state identical before and after, at most one deduped `interview.rejected`), duplicate-round refusal, terminal-job rules, one clock read after the lock, the clock floor, and real-process races (two `held` or two `debrief` on one round: exactly one succeeds).
- **Views:** equal to the pure functions on the same data; read-only proof by a whole-workspace byte-identical snapshot (ledger and `.careeros/` included, no lock file); prep status for `none` / `drafted` / `checked`, including the file edited after its check (back to `drafted`), a symlinked prep file and an oversized one; the shared action-text constant pinned between `careeros check` and the reader.
- **Validation:** each `INT` code positive and negative; a hostile ledger and hostile record files never crash it or write anything.
- **Skills:** wording pins, with constants compared to code and every command line parsed against the real CLI; the existing sha pins for untouched skills pass unchanged.
- **End to end:** invented data, one clock: add rounds, hold one, debrief it, cancel one, reschedule one, view `upcoming` and `needs-debrief` across a clock advance, a status inconsistency (`INT007`), a refusal, and one real-process race.
- **Release checks** on the final committed tree: the full suite on Python 3.14 and on a fresh Python 3.11 **without click**, `mkdocs build --strict`, wheel contents and version, `git diff --check` against the merge base, a clean `git status`.

## 12. Out of scope and known limits

- **Out of scope:** the reusable story bank, offer tracking, "waiting on a response" and thank-you reminders, email follow-ups, linking interviewers to outreach person records, a GPT workspace counterpart, learning from debrief ratings, importing existing `interview-prep.md` or `activity.md` history.
- **Limits, stated in the docs:** skills are pinned by wording only and were not run live; the feature is **not a security boundary**; ledger tail truncation is undetectable (the existing `verify_chain` limit); the clock floor is fail-closed; debrief ratings are the user's self-report and are not verified; `held` and `cancelled` are what the user says happened.

## 13. Build order

A. **Pure core:** rules, record parsing, the round fold, the views' derivations (including the date rules and prep-status logic over plain data).
B. **Records and commands:** `round add | list | show | held | cancel | reschedule`, `debrief`, the reserved prefix and reason rules, the `interview` command group.
C. **Views and validation:** `upcoming`, `needs-debrief`, the prep-status reader and its shared constant, the `INT` section in `validate`.
D. **Skills, docs and 0.9.0.**
E. **End-to-end test, acceptance record and release checks.**

Each task ends with its tests, its adversarial review and any re-reviews complete before the next begins.

## 14. Acceptance

All of §11 passes; the end-to-end record in `docs/END_TO_END_TEST.md` states the commands, exit codes, states and counts that ran, what was not covered (skills not run live, no real interview data, agent behaviour), and never calls the feature a security boundary; the release checks pass on the final committed tree; the user's real workspace is never touched.
