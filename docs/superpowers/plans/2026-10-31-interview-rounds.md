# Interview Rounds and Debriefs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status: DRAFT FOR REVIEW. No implementation has started.** Same format as the earlier plans: tasks give files, contracts, tests first and acceptance, with **no embedded implementation code**. Execution does not begin until the plan is signed off and an execution method is chosen.

**Goal:** Give CareerOS one deterministic answer to "what interviews do I have coming up, how prepared am I, and which rounds have I not debriefed": agent-written round records with a light debrief, a few reserved `interview.*` ledger events, state derived from those events plus validated record fields, read-only `careeros interview` views, and an `interview` skill that records rounds and debriefs through the commands.

**Architecture:** A new package `careeros/core/interview/` of small pure layers (rules, record parsing, round fold, view derivations) under a lock-protected service; a thin `careeros interview` command group; one small neutral change to `careeros check` so its pass wording is a shared constant; an `INT` section in `careeros validate`; changes to the `interview` and `follow-up` skills; docs; version 0.9.0. The pattern is the one outreach (0.8.0) and approvals (0.7.0) established; the interview package imports nothing from the outreach package.

**Tech Stack:** Python 3.11+, Typer, PyYAML, pytest. **No new dependencies** (the AST dependency test guards this).

**Spec:** `docs/superpowers/specs/2026-10-31-interview-rounds-design.md` (source of truth). Where this plan and the spec differ, the spec wins; every addition is marked **proposed** in section 8.

**Verification note:** nothing here has been executed. Names and rules come from reading the repository at `main` = `659f661` (0.8.0). Tests are the authority: if a value here disagrees with the spec, the spec wins, the discrepancy goes in the task report and the plan is corrected.

**Branch:** `interviews` (the spec is committed there). Do not merge or push without being asked. The user's real workspace (`~/Projects/job-search`) is never touched; acceptance uses an invented workspace. If `gh`/`git push` fails with a bad token, the keyring login works with `env -u GH_TOKEN`.

---

## 1. Scope and non-goals

**In scope:** round records (`rounds/<rnd_id>.md`) with an optional debrief block; `interview.*` events; derived round state (`scheduled`, `held`, `debriefed`, `cancelled`, `needs_debrief`, `needs_update`); `careeros interview round add|list|show|held|cancel|reschedule`, `debrief`, and the read-only `upcoming` and `needs-debrief` views with prep status; `INT001`-`INT007` and `INT100` in `careeros validate`; changes to the `interview` and `follow-up` skills; docs; version 0.9.0; an invented-data end-to-end acceptance.

**Non-goals:** the story bank; offer tracking; "waiting on a response" and thank-you reminders; email follow-ups; linking interviewers to outreach person records; a GPT workspace counterpart; learning from ratings; importing existing `interview-prep.md` or `activity.md` history. Rounds never change a job's status. The feature is **not a security boundary** and must never be described as one.

## 2. Existing interfaces this plan reuses (read at `659f661`)

| Area | Interface found | Used for |
|---|---|---|
| IDs | `careeros.core.ids`: `PREFIXES` already has `round` = `rnd`; `new_id(kind, existing)`; `is_valid_id(kind, value)` | round ids; no change to ids |
| Ledger | `ledger.append_events` (atomic batch), `read_events`, `verify_chain`, `RESERVED_TYPES`, `is_reserved()` (prefix tuple), `_NEEDS_REASON`, `_ACTOR_RE` | add the `interview.` prefix and reasons |
| Approvals neutral helpers | `approvals.fold`: `clock_floor`, `clock_skewed`, `parse_ts`; `approvals.render.needs_escape` | clock floor and text hygiene (the interview package imports these, not outreach) |
| Strict YAML | `compensation.record.strict_frontmatter` (used by the outreach parsers) | record parsing |
| Outreach pattern (read, mirror, do NOT import) | `outreach/_common.py` (bounded `load`, `keys_problems`, `text_value`, `date_value`, `schema_problem`), `outreach/service.py` (`_scan` bounded record reader, `_Op`/`_begin`/`_refuse`/`_skew`/`_write_then_append`, `Loaded`, `load`, `validate_workspace`), `outreach/fold.py` (events fold with `SequenceError`, malformed events reported and NOT applied), `cli/outreach.py` | the interview package re-implements the small plumbing it needs (section 8, item 1) |
| Workspace | `WorkspaceLock`, `ensure_writable`, `atomic_write_bytes`, `job_files(root)`, `read_job(path)`, `resolve_job(root, ref)`; `memory.store.Transaction` (file-first/event-second rollback) | commands, jobs input |
| State machine | `models.State`, `state_machine.TERMINAL`, the pre-interview and terminal sets; `job.status_changed` events | `INT007`, terminal-job rules |
| Evidence check | `cli/check.py`: `draft.checked` event with `entity` = job id, `artifacts = ["sha256:<hex of the draft text>"]`, action `checked draft: passed|failed (…)`; `_read_draft` | prep status; a shared constant for the action text |
| CLI | `cli/main.py` (`add_typer`), `cli/outreach.py` (group structure, `--json`, exit codes, clock callable), `cli/_util.py` | `cli/interview.py` |
| Validation | `validation.validate_workspace` calls `outreach_service.validate_workspace(root, events, now)` near line 208 and reads the clock once at the top | add the interview section the same way |
| Tests | `conftest.py` (`clock`), `tests/helpers.py` (`make_workspace`, `add_job`, `snapshot`, `full_state`, `Cmd`, `race_many`, `Probe`, `OutreachW`), `tests/test_outreach_*.py`, `tests/test_cli_dependencies.py`, `tests/test_no_bidi_controls.py` | all test tasks |

**Things that do not exist yet:** `rounds/`, `interview.*` events, the `careeros interview` group, `INT` codes, the shared `draft.checked` wording constant, `docs/interviews.md`.

## 3. Task sequence and dependency graph

```
A pure core ──> B records + commands ──> C views + prep status + validation ──> D skills + docs + 0.9.0 ──> E e2e + acceptance + release checks
```

Strictly linear; each task ends green with one commit (plus fix commits) before the next. The 3,635 existing tests stay green at every commit.

## 4. Global constraints (verbatim from the spec where it has them)

- No new dependencies; Python `>=3.11`; deterministic apart from the documented clock use; every derivation takes `now`; one clock read per operation, after the lock; the ledger clock floor (`clock_skew` when `now` is earlier than the latest event of any type).
- Round record: `rounds/<rnd_id>.md`, YAML frontmatter via the strict loader (no aliases, anchors, merge keys, duplicate keys or unknown keys), body optional and unparsed. Keys: `schema` (1), `id` (`rnd_`, immutable, equals the file stem), `job` (an existing job id, immutable), `stage` (`recruiter_screen`, `hiring_manager`, `technical`, `system_design`, `panel`, `final`, `other`), `format` (`phone`, `video`, `onsite`, `take_home`, `other`), `scheduled_for` (an ISO date `YYYY-MM-DD` or UTC datetime `YYYY-MM-DDTHH:MM:SSZ`), `interviewers` (0 to `MAX_INTERVIEWERS` mappings `{name, title}`, `title` may be empty), optional `debrief`. All strings at most `MAX_TEXT` (500) characters with no control, bidi or invisible characters. Any problem makes the record invalid (`INT001`); an invalid record feeds nothing and is never repaired.
- Debrief block: `rating` (`strong`, `mixed`, `weak`), `questions_asked` (at most 10 strings), `do_differently` (at most 10 strings), `next_step` (string), `next_step_date` (optional ISO date). The rating is the user's own words mapped to the enum; no code or skill invents one.
- Events (reserved prefix `interview.`): `round_added` (`artifacts = [rel, "sha256:<hex>"]`), `held` and `cancelled` (`source` `user` or `observed`; `cancelled` needs a reason), `rescheduled` (action names the new date; the record's `scheduled_for` is rewritten in the same operation), `debriefed` (`artifacts = [rel, "sha256:<hex>"]`, audit only), `rejected` (audit only, deduped on the entity's latest event of any type, needs a reason, entity must be an existing `rnd_` id, nothing appended for an unknown entity). Unknown `interview.*` types are errors (`INT005`). **A malformed event (missing or unsupported `source`, a missing reason on `cancelled`, a bad entity id) is reported as `INT005` and is NOT applied to derived state, and a round that has only malformed events gets no flags entry.**
- Round state: flags `added_at`, `held_at`, `cancelled_at`, `debriefed_at`, `cancel_reason`; label first match `cancelled`, `debriefed`, `held`, `scheduled`. Legal transitions (else `wrong_state`): `held` needs `scheduled`; `cancelled` needs `scheduled`; `rescheduled` needs `scheduled` (any number of times); `debriefed` needs `held` and no earlier `debriefed`; each of `held`, `cancelled`, `debriefed` at most once; `cancelled` is terminal. `needs_debrief` = label `held`. `needs_update` = label `scheduled` and the record's `scheduled_for` is in the past: a date-only value is overdue once `now` is at or after the start of the next UTC day; a datetime is overdue once `now` is after it.
- Derived-from table (spec 6.0): round identity, which events happened, `held`, `cancelled`, `debriefed` come from the ledger only; `scheduled_for`, `stage`, `format`, interviewers and debrief contents come from the validated record (editable source data); an orphan record or one whose `id` or `job` disagrees with its event feeds nothing; a hand edit never changes identity or what happened and nothing repairs a record.
- Rules constants (versioned rules module): `UPCOMING_DEFAULT_DAYS = 14`, `MAX_INTERVIEWERS = 10`, `MAX_LIST = 10`, `MAX_TEXT = 500`.
- Prep status of a round (by its job): `none` if the job has no `interview-prep.md`; `checked` if the file's current text hashes (the same way `careeros check` hashes a draft) to the `sha256` of a `draft.checked` event whose entity is the job and whose action records a pass; otherwise `drafted`. The prep file is read as a regular file, never through a symlink, at most 1 MiB; anything else is `none`. The action text is a constant shared between `careeros check` and this reader, pinned by a test.
- Views: `interview upcoming [--days N]` (default 14): rounds labelled `scheduled`, not `needs_update`, with `scheduled_for` within `N` days of `now` inclusive, soonest first then by id. `interview needs-debrief`: group `needs_debrief` (held rounds, oldest `held_at` first) and group `needs_update` (overdue scheduled rounds, oldest `scheduled_for` first, rounds on a job in a terminal status left out of this group; a held round on a terminal job still appears in `needs_debrief`). Orderings are total and independent of input order. Views create no file, event or lock file.
- Commands: state-changing commands take the lock, check the chain (`ledger_corrupt`), read the clock once after the lock, refuse `clock_skew`, write the file first and the event second in a transaction and roll back both on any failure. `round add` refuses a job in a terminal status (`wrong_state`) and a live (non-cancelled) round with the same job, stage and date (`duplicate`); `held`, `cancel` and `debrief` are allowed after the job reached a terminal status; `debrief` is refused unless the round is `held`. Refusal codes: `not_found`, `wrong_state`, `duplicate`, `invalid_input`, `clock_skew`, `ledger_corrupt`. Exit codes 0 ok, 1 refused, 2 usage error or no workspace; `--json` prints one object. A refusal on an existing round appends at most one deduped `interview.rejected` and changes no derived state.
- `INT` codes (read-only; a corrupt ledger line never crashes the section; record files read as regular files, never through symlinks, with outreach-equivalent size bounds): `INT001` error record shape; `INT002` warning orphan record; `INT003` warning event whose record file is missing; `INT004` error record `id`/`job` disagree with the event or one id under two jobs; `INT005` error illegal sequence / unknown `interview.*` type / malformed event; `INT006` warning the round's `job` names no saved job; `INT007` warning status consistency (a round `held` or `scheduled` while its job's status is `DISCOVERED` through `APPLIED` including `APPROVAL_REQUIRED`, or a round still `scheduled`/`needs_update` after the job reached a terminal status; the message names the `careeros transition` the user may want and nothing is ever run); `INT100` info a round is `needs_update`. A workspace without `rounds/` and without `interview.*` events is unchanged. `careeros validate` stays entirely read-only.
- Surgical changes to existing code; the user's real workspace is never touched; committed acceptance records hold codes and counts only; skills, `prep`, `offer`, the evaluation and compensation skills, the approvals flow, the Career Memory evidence gate and the GPT workspace are unchanged except where section 6 Task D says; the feature is not a security boundary.

## 5. Review focus (inputs most likely to bite, each pinned by a task test)

1. An orphan or mismatched round record (crash window, hand-copied file, id or job edited) must feed no view or state, while still being reported (Tasks A, B, C).
2. A malformed `interview.*` event must be reported but never applied, and must not create a flags entry; a later valid event of the same kind applies normally (Task A).
3. Hostile files: a symlinked, FIFO, oversized or non-UTF-8 `rounds/*.md` or `interview-prep.md` must never hang or crash `upcoming`, `needs-debrief`, `validate` or any command, and a hostile prep file counts as `none` (Tasks B, C).
4. Date boundaries: date-only vs datetime `scheduled_for` at exactly the overdue instant and one second either side; a hand-edited invalid date; `--days` boundary inclusive (Tasks A, C).
5. Prep status exactness: the file edited after its check goes back to `drafted`; a failed check, a check for another job, or a check of different text never counts; the hash is computed exactly as `careeros check` does (newlines, BOM, trailing whitespace) (Task C).

---

## 6. Tasks

### Task A — Pure core: rules, record, fold, derivations

**Goal and scope:** every pure contract the rest builds on. No ledger writes, no commands, no filesystem, no clock reads (functions take `now`).

**Files (new):** `careeros/core/interview/__init__.py` (docstring), `rules.py`, `_common.py`, `model.py`, `round.py`, `fold.py`, `derive.py`; `tests/test_interview_core.py`.

**Dependencies:** none.

**Contracts to define:**
- `rules.py`: the constants of section 4, enums (`STAGES`, `FORMATS`, `RATINGS`, `SOURCES = ("user", "observed")`), event type constants (`EV_ROUND_ADDED`, `EV_HELD`, `EV_CANCELLED`, `EV_RESCHEDULED`, `EV_DEBRIEFED`, `EV_REJECTED`, `EVENT_PREFIX`, `AUDIT_ONLY_TYPES`), code constants (`INT001`..`INT007`, `INT100`), refusal-code constants, the transition table as data.
- `_common.py`: the small record validators the parser needs (bounded `load` using `compensation.record.strict_frontmatter`, `keys_problems`, `schema_problem`, `text_value` with the control/bidi/invisible check, `date_value`, a `when_value` that accepts a date or UTC datetime), mirroring `outreach/_common.py` (section 8, item 1).
- `model.py`: frozen dataclasses `Interviewer`, `Debrief`, `Round`, `RoundFlags`, `JobRef(id, status, terminal)`.
- `round.py`: `parse(text, rel) -> (Round | None, list[Issue])` (alias ban, duplicate/unknown keys, closed enums, `id` equals the file stem, `INT001` for every shape problem, hostile-input safe, never raises), `to_bytes(round) -> bytes`, `with_debrief(round, debrief)`, `with_when(round, when)`, `parse_when(value) -> datetime | None`.
- `fold.py`: `fold_rounds(events, now) -> RoundsFold(flags, errors)` (filters audit-only events, reports unknown `interview.*` types, illegal sequences and malformed events through a returned error list of `SequenceError`, never applies a malformed event, creates no flags entry for a round with only malformed events), `apply_event(flags, etype, ts) -> RoundFlags | Refusal`, `round_added_fields(rel, sha)` (the only writer of that event shape), `rejected_fields(...)`.
- `derive.py`: `needs_update(round, flags, now) -> bool`, `build_upcoming(rounds, flags, jobs, prep, now, days) -> list[UpcomingItem]`, `build_needs_debrief(rounds, flags, jobs, now) -> NeedsDebrief(needs_debrief, needs_update)`, `status_issues(rounds, flags, jobs, now) -> list[(code, round_id, message)]` for `INT007`/`INT100`. Inputs are plain data; output ordering is a documented total order.

**Tests first (`test_interview_core.py`):**
- record parser: the spec example parses; every enum/shape/hostile-input case gives `INT001` (aliases, merge keys, duplicate and unknown keys, huge ints, NaN, 100k-deep nesting, lone surrogates, NUL, wrong types for every field, 5 MB body, CRLF, BOM); `id` not equal to the file stem; control/bidi characters in names; bad dates (`2026-02-30`, wrong forms, datetime without `Z`, offsets); `to_bytes`/`parse` round trip;
- fold: every legal transition and every refusal against an independent table-driven simulator over 20,000+ random sequences with audit-only `interview.rejected` inserted at random (results unchanged) and malformed events inserted at random (reported, not applied, no flags entry, a later valid event applies; a malformed event does not use up the once-only slot); label precedence for every flag combination; `rescheduled` any number of times only while `scheduled`; `cancelled` terminal;
- derivations: `needs_update` for date-only (exactly the start of the next UTC day, one second before and after) and datetime (exactly the instant, one second before and after); `upcoming` window inclusive at `N` days and one second beyond; `needs-debrief` ordering and the terminal-job exclusion in the `needs_update` group only; ordering independent of shuffled input; `INT007` cases (pre-interview statuses including `APPROVAL_REQUIRED`, terminal statuses, the interview stages produce nothing);
- import-isolation subprocess test: importing `careeros.core.interview` does not import `validation`, `state_machine`, the CLI, `approvals.service` or anything from `careeros.core.outreach`.

**Acceptance:** all pure; no clock reads inside functions; 3,635 + new tests green.

**Commit:** `Add the interview core: records, rules, round fold and derivations`.

### Task B — Records and commands

**Goal and scope:** creating and recording rounds and debriefs; the `interview.*` events; the reserved prefix.

**Files (new):** `careeros/core/interview/service.py`, `careeros/cli/interview.py`; `tests/test_interview_records.py`. **Modify:** `careeros/cli/main.py` (register `interview_app` as `interview`), `careeros/core/ledger.py` (reserve the `interview.` prefix in `is_reserved`; add `interview.rejected` and `interview.cancelled` to `_NEEDS_REASON`; update any pinned test of those sets).

**Dependencies:** A.

**Contracts to define:**
- `service.load(root, now=None, events=None) -> Loaded`: a bounded, symlink-refusing record scan of `rounds/*.md` (the `_scan` pattern), events, `fold_rounds`; a round feeds state only when its id has a `round_added` event, the file stem equals the id and the record's `job` equals the job named by that event's action/entity data (orphans and mismatches feed nothing and stay reported).
- Operations, each under `WorkspaceLock`, with chain check, one clock read after the lock, floor check, file first/event second in a transaction with rollback: `round_add(root, *, job, stage, format, when, interviewers, actor, clock)`, `round_held`, `round_cancel`, `round_reschedule`, `debrief`; read-only `round_list`, `round_show`. Refusals append the deduped audit-only `interview.rejected` for an existing round, nothing for an unknown id. `--actor` validated before any write.
- `cli/interview.py`: the `interview` group with `round add|list|show|held|cancel|reschedule` and `debrief`; `--json`; exit codes 0/1/2; `--interviewer "Name:Title"` repeatable; `--asked` and `--improve` repeatable.

**Tests first (`test_interview_records.py`):**
- happy paths: file bytes, event shape, ids, `--json`, exit codes; `--job` resolved through `resolve_job`; terminal-job refusal for `add` and success for `held`/`cancel`/`debrief`; duplicate live round refused (and allowed again after the first is cancelled); `debrief` refused unless `held`; `reschedule` rewrites the date and appends one event; `held` twice refused;
- file-first/event-second: failing `append_events` leaves no file and no event; a failing file write leaves no event; `reschedule` rollback restores the old date; a crash window leaves an orphan reported as `INT002` and counted nowhere; a deleted record keeps identity (`INT003`); hand-edited `id`/`job` gives `INT004` and feeds nothing; the record-editing table: for each field, edit it by hand and assert exactly which view output changes and that identity, held/cancelled/debriefed state and event history never change;
- refusal audit: every refusal path leaves the derived snapshot identical and appends at most one deduped `interview.rejected` (loop the same refusal 20 times: one event), none for an unknown id; reserved prefix refused for `ledger append interview.x`; `_NEEDS_REASON` enforced;
- clock: a counting injected clock is read exactly once per operation, after the lock; the floor refuses when `now` precedes the latest event of any type; broken chain gives `ledger_corrupt` before the clock read;
- concurrency (real processes, barrier, bounded timeouts): two `held` on one round, exactly one succeeds; two `debrief` on one held round, exactly one; two `round add` of the same job/stage/date, exactly one. Prove each lock necessary by removal in a scratch copy (bounded);
- hostile record files (symlink, directory named like a record, FIFO, non-UTF-8, 5 MB, binary, YAML `!!python` tag, anchor bomb) never crash or hang any command and are reported;
- approvals and outreach tests unchanged and green; the interview package imports nothing from outreach (subprocess test).

**Acceptance:** all green; existing behaviour unchanged except the reserved prefix.

**Commit:** `Add interview rounds, debriefs and the interview commands`.

### Task C — Views, prep status and validation

**Goal and scope:** the two read-only views, the prep-status reader with its shared constant, and the `INT` section in `validate`.

**Files (new):** `careeros/core/draft_check.py` (the shared wording constants), `careeros/core/interview/prep.py`; `tests/test_interview_views.py`. **Modify:** `careeros/cli/check.py` (use the constants in place of the string literals; behaviour and output unchanged), `careeros/core/interview/service.py` (`upcoming`, `needs_debrief`, `validate_workspace`), `careeros/cli/interview.py` (`upcoming`, `needs-debrief`), `careeros/core/validation.py` (call the interview validation next to the outreach call, using the one `now`).

**Dependencies:** A, B.

**Contracts to define:**
- `draft_check.py`: the constants for the `draft.checked` pass/fail action prefixes; `cli/check.py` builds its action text from them, and the prep reader matches with them.
- `prep.py`: `prep_status(root, job_dir, job_id, events) -> "none" | "drafted" | "checked"`: opens `interview-prep.md` without following symlinks and without blocking, requires a regular file, reads at most 1 MiB, decodes UTF-8, hashes exactly as `careeros check` does (read `_read_draft` and reuse its text normalisation), and matches a `draft.checked` event for the job whose artifact hash equals it and whose action starts with the pass constant. Everything else non-`none` is `drafted`; any hostile file is `none`.
- `service.upcoming(root, days=14, now=None)` and `service.needs_debrief(root, now=None)`: build plain-data inputs once with one `now` (records via the strict parser feeding only valid, event-matched rounds; jobs via `job_files`/`read_job`; prep status per job), call the Task A derivations, create no file, event or lock file.
- `validate_workspace(root, events, now)`: `INT001`..`INT007`, `INT100` exactly as in section 4, read-only, never raises.

**Tests first (`test_interview_views.py`):**
- views equal the pure functions on the same data end to end through the CLI (text and `--json`), with one `now`; a whole-workspace byte-identical snapshot (ledger and `.careeros/` included, mtimes, no lock file created) after every view and `round list|show`; the clock advance moves a round from `upcoming` to `needs_update` exactly at the boundary; `--days` boundary; terminal-job rules in both groups;
- prep status: `none`, `drafted`, `checked` for a real `careeros check <file> --against <job> --record` run; editing the file afterwards returns `drafted`; a failed check, a check of another job and a check of different text never give `checked`; trailing newline, CRLF and BOM variants hash exactly as `check` does (assert by calling the real `check` on the same bytes); symlinked, FIFO (in a subprocess with a timeout), directory, oversized (1 MiB boundary) and non-UTF-8 prep files are `none` and never hang `upcoming`; the shared constants pin the producer to the reader (changing the wording in `cli/check.py` only makes the test fail); `careeros check` output and exit codes unchanged (the existing check tests pass untouched);
- `validate`: each `INT` code positive and negative (including every `INT007` case and the no-finding cases), exact severities, a hostile ledger and hostile records never crash it or write, byte-identical snapshot before and after; a workspace without `rounds/` or `interview.*` events gives exactly the same findings as before; the clock is read once per `validate` call; orphan and mismatched records feed no view but are reported;
- existing tests unchanged and green.

**Acceptance:** all green; `careeros check` behaviour byte-identical; views read-only.

**Commit:** `Add the interview views, prep status and INT validation`.

### Task D — Skills, docs and 0.9.0

**Goal and scope:** the `interview` and `follow-up` skill changes, docs, version bump.

**Files (new):** `docs/interviews.md`, `tests/test_skills_interview.py`. **Modify:** `careeros/templates/workspace/.claude/skills/interview/SKILL.md`, `careeros/templates/workspace/.claude/skills/follow-up/SKILL.md`, `careeros/templates/workspace/CLAUDE.md` (routing row and layout line `rounds/`), `mkdocs.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml` -> `0.9.0` (then `.venv/bin/pip install -e ".[dev]" -q`), and every existing test that pins version `0.8.0` or the `0.8.0` CHANGELOG heading (report each edit; weaken nothing).

**Dependencies:** C.

**Contracts:**
- `interview` skill: after STEP 0 a step records the round with `careeros interview round add` (stage and interviewers as the user gives them; never add a round twice for the same job, stage and date); it reads `careeros interview upcoming` to say what is due; a debrief step after a round asks the rating and the other fields one at a time and records only what the user said, never inventing a rating; `round held` and `round cancel` only on the user's say-so; a refusal ends that action and is relayed to the user; STEP 6 no longer treats the `activity.md` line as the only record of the round; the evidence gate and the prep file format are unchanged.
- `follow-up` skill: one short step reads `careeros interview needs-debrief` and lists those rounds; it sends nothing.
- No skill text claims a security boundary, runs `approve` or `resolve`, or contains an unconditional `careeros transition`. The sha pins for untouched skills (prep evidence gate, GPT workspace, evaluate, compensation, offer) must pass UNCHANGED; the two skills changed here get anchored additions in the style of the earlier browse pins if a sha test covers them.
- Docs: `docs/interviews.md` (records with invented examples, events, derived state with the spec 6.0 table, constants, commands and exit codes, refusal codes, `INT` codes with severities, prep status, limits from spec section 12 including that malformed events are reported but not applied); README; CHANGELOG `0.9.0` (run `careeros upgrade` to receive the updated skills; rounds start empty; existing `interview-prep.md` and `activity.md` history is not imported); mkdocs nav; `mkdocs build --strict` passes.

**Tests first (`test_skills_interview.py`):** wording pins for each sentence above; constants (`14`, `10`, `500`) compared with code as sets; every command line in the skills and docs parsed against the real CLI (including `--source user` values, which click does not check); docs `INT` table and refusal-code list compared with code; the doc statement that views create no files; a test that no docs page says `activity.md` drives round tracking; every pin mutation-checked by the implementer with short bounded runs; sha pins still pass.

**Acceptance:** all green; `mkdocs build --strict` exits 0 (own exit status, output outside the repo); version `0.9.0` consistent.

**Commit:** `Add the interview skills, docs and the 0.9.0 version bump`.

### Task E — End-to-end test, acceptance record and release checks

**Files (new):** `tests/test_interview_e2e.py`. **Modify:** `docs/END_TO_END_TEST.md` (a "## Interview rounds and debriefs (0.9.0)" section).

**Dependencies:** D.

**Steps (invented data, real CLI, ONE settable clock except the race step which uses the real clock):** (a) add a job and two rounds (a screen in 3 days, a technical in 10 days); `upcoming` lists both soonest first with prep `none`, then a prep file is drafted (`drafted`) and checked (`checked`); (b) advance the clock past the first round: it leaves `upcoming` and appears in `needs-debrief` under `needs_update`; record `held`: it moves to `needs_debrief`; (c) `debrief` it, it leaves `needs-debrief`; a second debrief is refused `wrong_state`; (d) reschedule the second round and cancel a third; a cancelled round is not reopenable; (e) duplicate round refused; terminal job: `add` refused, `held`/`debrief` allowed after the job is moved to `REJECTED`; (f) `validate` shows `INT007` when a round is held on a job still at `APPLIED`, and `INT100` for a `needs_update` round, with exit codes; (g) a hand-edited `scheduled_for` changes `upcoming` but not identity or event history; an orphan record and a mismatched record feed nothing; (h) a malformed `interview.held` event hand-appended to the ledger is `INT005` and changes nothing; (i) `upcoming`, `needs-debrief`, `round list|show` and `validate` leave the whole workspace byte-identical (ledger and `.careeros/` included); (j) event counts per type, rejection dedupe, the ledger chain verifies; (k) isolation: `career/`, other jobs and `jd.md` untouched; (l) one real-process race: two `held` on one round, exactly one succeeds.

**Release checks to run and report truthfully, on the FINAL COMMITTED TREE (not the working directory):** commit first, record the commit SHA, create a clean checkout of exactly that commit in a temporary directory outside the repo (`git worktree add`, removed afterwards) and run everything there: fresh venvs; the full suite on Python 3.14; the full suite in a FRESH Python 3.11 venv built from `/Users/kaushal/.local/bin/python3.11` with `pip install -e ".[dev]"` and **without click** (confirm `pip show click` fails); `mkdocs build --strict` with its own exit status and output outside the repo (the repo `.venv` interpreter is acceptable because the fresh venvs' dev extra has no mkdocs; say so); a built wheel listing `careeros/core/interview/*.py`, `core/draft_check.py`, `cli/interview.py` and the version `0.9.0`; `git diff --check` (and against the merge base); a clean `git status`. The report names the tested SHA. The controller re-runs these on the final tree after any final fix wave. The acceptance record holds commands, exit codes, state names, codes and counts only (no real companies or people), says what was not covered (skills not run live, no real interview data, agent behaviour) and never calls the feature a security boundary.

**Acceptance:** all recorded; any defect found stops the task with an exact reproduction; no earlier source is patched here.

**Commit:** `Add the interview end-to-end test and the 0.9.0 acceptance record`.

---

## 7. Process and review

Subagent-driven if chosen, as for evaluation, compensation, approvals and outreach: one implementer per task, an adversarial task reviewer (independent reference models, mutation tables, real-process races where relevant), scoped re-reviews of fix rounds, a ledger of rulings, one whole-branch review on the most capable model available, then ONE fix wave and a re-run of the release checks on the final tree. **Commit gates (binding):** each task ends with its tests green, its adversarial review and any scoped re-reviews complete and its ledger entry written BEFORE the next task is dispatched; a task with an open Critical or Important finding is not complete. **Executor hygiene (learned in 0.7.0 and 0.8.0):** every command bounded (Python subprocess timeouts; the full suite takes about 90 s, allow 290 s); never `pkill`/`killall`/`pgrep -f`; no background loops; short mutation proofs (one scratch copy, one targeted test file, 120 s); commit early once green (API outages have interrupted agents mid-task); run the whole suite AFTER any amend; no raw bidi or zero-width characters in any file (escapes in test sources); the fresh-venv 3.11 run without click for every release; ledger lines written with single-quoted `printf` (backticks in double quotes execute).

## 8. Proposed additions beyond the spec (marked for approval)

1. **The interview package duplicates a small record-validation helper set** (`_common.py`, about 60 lines mirroring `outreach/_common.py`) instead of importing outreach's private module or refactoring 0.8.0 code into a shared module. Reason: the spec requires the new package to import nothing from outreach and changing shipped modules is outside this feature. Cost if wrong: a later refactor into one shared module.
2. **`careeros/core/draft_check.py`** holds the `draft.checked` wording constants and `cli/check.py` uses them: the spec requires a shared constant between producer and reader but does not name the module.
3. **A `JobRef(id, status, terminal)` input struct** for the derivations so they stay pure.
4. **`interview.cancelled` joins `interview.rejected` in `_NEEDS_REASON`** (the spec requires a reason on `cancelled`; the ledger enforces it for outreach the same way).
5. **`round list` accepts an optional `--job`** filter (the spec's table lists it) and is read-only.

## 9. Self-review (spec coverage)

Spec §4 (records, debrief) -> A (parse), B (write); §5 (events) -> A (shapes, malformed handling), B (writers, reserved prefix), C (validate); §6.0-6.3 (derivation inputs, rules, state, flags) -> A; §6.4 (prep status, views) -> A (derivations), C (prep reader, views, shared constant); §7 (commands) -> B (state-changing, `round list|show`), C (views); §8 (validation) -> C; §9 (skills) -> D; §10 (docs/release) -> D, E; §11 (testing) -> distributed per task; §12 (limits) -> D docs; §13 (build order) -> section 3; §14 (acceptance) -> E. No placeholders; names used later are defined where introduced (`Round`, `RoundFlags`, `Debrief`, `Interviewer`, `JobRef`, `fold_rounds`, `RoundsFold`, `apply_event`, `round_added_fields`, `needs_update`, `build_upcoming`, `build_needs_debrief`, `status_issues`, `prep_status`, `Loaded`, `load`, `round_add`, `round_held`, `round_cancel`, `round_reschedule`, `debrief`, `upcoming`, `needs_debrief`, `validate_workspace`).
