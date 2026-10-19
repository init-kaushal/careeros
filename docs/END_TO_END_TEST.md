# End-to-end acceptance record (Foundation 0.3.0)

This records a run of the Foundation commands against a **copy** of a real, in-use job-search workspace. The workspace holds personal data, so this page records commands, exit codes and counts only. Company names, job titles, URLs and file names are deliberately left out; where a job must be named it is called "job-1".

- Date: 2026-10-09
- Git commit under test: `582ae48` (branch `foundation-0.3.0`, plus the uncommitted end-to-end test, CI, changelog and docs of this task)
- Workspace: 94 files before the run, including 33 job files under `jobs/discovered/`, all in the legacy format (no frontmatter, no ledger, no workspace metadata)
- Method: the workspace was copied with `cp -R`; every `careeros` command ran with `-w` pointing at the copy. A SHA-256 snapshot of every file in the real workspace was taken before the copy and compared after the last command.

## Commands and results

| # | Command (on the copy) | Exit | Result |
|---|---|---|---|
| 1 | `careeros validate` | 1 | 1 error (`WS001`, no workspace metadata) and 33 warnings (`JOB001`, one per job file: no frontmatter, legacy job file; each says to run `careeros migrate`) |
| 2 | `careeros migrate --dry-run` | 0 | The plan listed `create .careeros/workspace.yaml` and "add frontmatter and a stable id to 33 job file(s)", each mapped to `DISCOVERED`, plus a note that 33 jobs have date-only creation times. No parse errors. "Dry run: nothing was changed." |
| 3 | `careeros migrate --yes` | 0 | "Migrated 33 job(s)." A backup and manifest were written under `.careeros/backups/` |
| 4 | `careeros validate` | 0 | 0 errors, 0 warnings |
| 5 | `careeros status` | 0 | Jobs: `DISCOVERED` 33, archived 0; 5 recent events shown, all `job.imported`; workspace framework 0.3.0, schema 1 |
| 6 | `careeros doctor` | 0 | All checks `OK` (python, careeros, workspace, metadata, framework, framework files, validation) |
| 7 | `careeros ledger verify` | 0 | "ledger OK (34 events)": 1 `workspace.migrated` plus 33 `job.imported` |

## Bodies and the real workspace

- Byte-identical bodies: 33 job files checked, **0 bodies differ**. The text below the added frontmatter is byte for byte the original file.
- Real workspace unchanged: **True**. The SHA-256 snapshot of all 94 files taken before the copy equals the snapshot taken after the last command.

## State machine and audit on the copy

Using the first job directory in alphabetical order ("job-1"):

| Command | Exit | Result |
|---|---|---|
| `careeros transition job-1 --to OFFER` | 1 | `illegal_transition` |
| `careeros transition job-1 --to EVALUATED` | 0 | `DISCOVERED → EVALUATED` |
| `careeros transition job-1 --to EVALUATED` (repeat) | 0 | "nothing to do"; no new event |
| `careeros transition job-1 --to APPLIED` | 1 | `illegal_transition` (`APPLIED` is reachable only from `APPROVAL_REQUIRED`) |
| `careeros ledger list` | 0 | 37 events: the 34 above (1 `workspace.migrated`, 33 `job.imported`), one `job.status_changed`, and two `job.transition_rejected` |
| edit one word on line 2 of `ledger.jsonl`, then `careeros ledger verify` | 1 | reported `LED001` and `LED002` (`LED001` means the edited line's structure or type was invalid; `LED002` means the hash chain or sequence broke) |
| restore the saved `ledger.jsonl`, then `careeros ledger verify` | 0 | "ledger OK (37 events)" |

## What this run did not cover

- The browser-driven skills (browse, apply, outreach): this suite cannot exercise them.
- The real workspace itself: it was **not** migrated. Only a copy was.
- `approve` was not run on the copy, because it needs an interactive terminal; it is covered by `tests/test_foundation_e2e.py` with a synthetic workspace.
- These spec acceptance steps are covered by the automated tests only, not by the run on the copy: the approval-guard refusal and repeated-transition check (step 3), the failed-ledger-append byte-identical check (step 4), and doctor/status on a fresh workspace (step 6, partly).

## Career memory (0.4.0)

- Date: 2026-10-10
- Commit under test: 272040d (the Task 8 documentation commit follows it)
- Method: the real workspace was copied (`cp -R`); SHA-256 checksums of all 94 files of the real workspace (excluding `.git`) were taken before and after; the copy was migrated first (the real workspace is schema 0), and every command below ran with `-w` on the copy. The faithful draft is the verbatim text of one imported achievement; the four tampered drafts are generated from the copy's own memory by a throwaway script that is not committed.

| Command | Exit | Observed |
|---|---|---|
| `migrate --yes` (copy) | 0 | migrated |
| `memory import` (dry run) | 0 | 55 added, 0 changed, 0 removed, 0 unchanged, 0 need review; one "not imported" note (preamble) |
| `memory import --apply --yes` | 0 | same summary |
| `memory import --apply --yes` (second) | 0 | "Nothing to import" |
| `resume.md` copy vs original | n/a | identical |
| `memory status` | 0 | 55 facts, all claimed: 13 achievement, 1 education, 3 experience, 1 project, 37 skill |
| `validate` | 0 | 0 errors, 0 warnings |
| `ledger verify` | 0 | 90 events OK |
| `check` faithful draft | 0 | no findings |
| `check` first 5 imported achievements, verbatim | 0 each | 5 of 5 pass |
| `check` invented metric | 1 | CHK001 |
| `check` invented employer | 1 | CHK004 |
| `check` invented technology | 1 | CHK003 |
| `check` unrepresented relationship | 1 | CHK010 |
| Real workspace checksums before/after | n/a | identical (94 files) |

An earlier run of this acceptance on the same workspace failed the faithful draft with two CHK009 findings (capitalised words that appear verbatim in the fact's own text were treated as unknown names); that was fixed in 272040d and the run above is after the fix.

Not covered: the skills cannot be run live here; they are covered by the wording tests only. `memory confirm` and `retire` need an interactive terminal and were not run on the copy; they are covered by `tests/test_memory_e2e.py` with a synthetic workspace. The real workspace itself was not migrated or imported; only a copy was.

## Job evaluation (0.5.0)

- Date: 2026-10-11
- Commit under test: `c403250` (the end-to-end test and this record are added by the commit that follows it)
- Method: a brand-new invented workspace was generated in a temporary directory with `careeros init`; five invented memory facts were added with `careeros memory add`; one invented job with an invented `jd.md` and an invented profile (markets and rubric) was written by a throwaway script that is not committed. The agent half was filled by the script, since the skills cannot be run live. Every command ran the installed `careeros` with `-w` on that workspace. No real workspace was read, copied or touched, and no real posting, employer, person or figure was used. For each read-only command, a SHA-256 of every file in the workspace (including `ledger.jsonl` and `.careeros/`) was taken before and after.

| # | Command | Exit | Result | Files before/after |
|---|---|---|---|---|
| 1 | `validate` (before any evaluation) | 0 | no `EVL` finding | identical |
| 2 | `evaluate <job> --init` | 0 | skeleton written, no event | n/a |
| 3 | `evaluate <job>` (agent half filled) | 0 | tier `pursue`, combined 0.8, confidence `low`, 0 caps, 3 action items; one `evaluation.completed` | n/a |
| 4 | `evaluate <job>` (again) | 0 | same result, no new event | identical |
| 5 | `validate` | 0 | no `EVL` finding | identical |
| 6 | hand-edit `result.tier`, then `validate` | 1 | `EVL007` (error) and `EVL009` (warning, the logged hash no longer matches) | identical |
| 7 | `evaluate <job>` | 0 | `pursue`, 0.8, `low` restored; one `evaluation.result_replaced` and one `evaluation.completed` | n/a |
| 8 | `validate` | 0 | no `EVL` finding | identical |
| 9 | change `jd.md` wording, then `validate` | 0 | `EVL008` (warning) | identical |
| 10 | `evaluate <job>` | 0 | `pursue`, 0.8, `low`; one `evaluation.completed` | n/a |
| 11 | `validate` | 0 | no `EVL` finding | identical |
| 12 | `ledger verify` | 0 | chain OK | identical |

- Evaluation events in order: `evaluation.completed`, `evaluation.result_replaced`, `evaluation.completed`, `evaluation.completed`. The job status stayed `DISCOVERED`.
- Every `validate` also showed one `STR002` warning for a workspace file that the invented workspace never created. It is unrelated to evaluation.
- Real workspace: not touched.
- Automated tests: Python 3.14: 1329 passed (150 s); Python 3.11: 1329 passed (146 s). `tests/test_evaluation_e2e.py` walks the same flow plus every staleness cause on its own (`jd`, `memory`, `rubric`, `rules`, `inputs`), a ledger failure, the crash window (`EVL009`, healed by a re-run) and the delivered skills.

Not covered: the `evaluate`, `prep` and `browse` skills cannot be run live here; they are covered by the wording tests only. A real posting and a real workspace were not used, so the quality of an agent's judgements on real text is untested.

## Structured compensation (0.6.0)

- Date: 2026-10-15
- Commit under test: `47c1281` (the end-to-end test and this record are added by the commit that follows it)
- Method: a brand-new invented workspace was generated in a temporary directory with `careeros init`; an invented career memory, an invented profile (markets table with a verified floor), one invented job with an invented `jd.md` and a valid agent half were written by a throwaway script that is not committed. `compensation.md` was written by the script, since the skills cannot be run live. Every command ran the installed `careeros` with `-w` on that workspace. A second brand-new workspace was a copy of the committed golden 0.5.0 fixture. No real workspace was read, copied or touched, and no real posting, employer, person or figure was used. For each read-only command, a SHA-256 of every file in the workspace (including `ledger.jsonl` and `.careeros/`) was taken before and after. (That manual run's checksums covered `.careeros/`; the automated snapshot helper in `tests/helpers.py` skips `.careeros` and `ledger.jsonl`, and the automated tests compare the ledger separately.)

| # | Command (after the step shown) | Exit | Result | Files before/after |
|---|---|---|---|---|
| 1 | `validate` (no `compensation.md`) | 0 | no `EVL` or `CMP` finding | identical |
| 2 | `comp <job> --init` | 0 | skeleton written, no event | n/a |
| 3 | `comp <job>` (posted figure below the verified floor) | 0 | one `compensation.recorded` | n/a |
| 4 | `evaluate <job>` | 0 | tier `weak`, cap `compensation` | n/a |
| 5 | `validate` | 0 | no `EVL` finding | identical |
| 6 | raise the figure above the floor, then `validate` | 0 | `EVL008` (warning), no `EVL007` | identical |
| 7 | `comp <job>` | 0 | one `compensation.recorded` | n/a |
| 8 | `evaluate <job>` | 0 | tier `pursue`, 0 caps | n/a |
| 9 | `evaluate <job>` (again) | 0 | `(unchanged)`, no new event | n/a |
| 10 | `validate` | 0 | no `EVL` finding | identical |
| 11-12 | foreign currency, no conversion: `comp`, `evaluate` | 0, 0 | 0 caps, `FX_CONVERSION_MISSING` action item | n/a |
| 13-14 | add an in-window conversion: `comp`, `evaluate` | 0, 0 | tier `weak`, cap `compensation` | n/a |
| 15-16 | conversion dated after the observation: `comp`, `evaluate` | 0, 0 | 0 caps, `FX_RATE_OUTSIDE_WINDOW` action item | n/a |
| 17-18 | conflicting recruiter pair: `comp`, `evaluate` | 0, 0 | 0 caps, resolve-conflict action item | n/a |
| 19 | `comp <job>` (low above high) | 1 | `CMP003`; one `compensation.rejected` | n/a |
| 20 | `comp <job>` (same invalid file) | 1 | `CMP003`; no new event (deduped) | n/a |
| 21 | `validate` | 1 | `CMP003`, `EVL008`, `STR002` | identical |
| 22 | `ledger verify` | 0 | chain OK (31 events) | n/a |

- Events across the run: 6 `compensation.recorded`, 1 `compensation.rejected`, 6 `evaluation.completed`. No evaluation event came from `comp` or `validate`.
- Every `validate` also showed one `STR002` warning for a workspace file that the invented workspace never created. It is unrelated to compensation.
- Golden 0.5.0 record (copy of the committed fixture), by hand following the skill steps:

| # | Command | Exit | Result | Files before/after |
|---|---|---|---|---|
| 1 | `validate` | 0 | `EVL008` (stale), no `EVL007`, no `EVL010` | identical |
| 2 | `evaluate <job>` | 1 | `EVL003`, the migration wording; nothing changed but one `evaluation.rejected` | n/a |
| 3 | write `compensation.md`, then `comp <job>` | 0 | one `compensation.recorded` | n/a |
| 4 | remove the legacy block, then `evaluate <job>` | 0 | version 2, tier `weak`, cap `compensation` (same cap as 0.5.0) | n/a |
| 5 | `evaluate <job>` (again) | 0 | `(unchanged)` | n/a |
| 6 | `validate` | 0 | no `EVL` finding | identical |

- Migration interruptions (automated, in `tests/test_compensation_e2e.py`): stopped after step 1, in the empty-skeleton state, after step 3, and with the block removed and no `compensation.md` (rebuilt from the stored version 1 range). Each resumed to the same tier, caps, stamps, action items and `cap_inputs` as the uninterrupted run; a written `compensation.md` was never rewritten (SHA-256 equal after its first full write) and no observation was duplicated.
- Real workspace: not touched.
- Automated tests: Python 3.14: 1795 passed (30 s); Python 3.11: 1795 passed (30 s). `tests/test_compensation_e2e.py` walks the same flow in one test (cap, stale, uncap, `(unchanged)`, foreign currency, conflict, rejection dedupe, write scope with a SHA-256 table, event counts, isolation of `career/`, the job file and `jd.md`) plus the migration matrix above.

- Release checks: `mkdocs build --strict` exit 0; wheel `careeros-0.6.0` contains `careeros/core/compensation/{__init__,derive,model,record,rules,service}.py`, `careeros/cli/comp.py` and the browse and evaluate skills; `git diff --check` exit 0.
- The Python 3.11 run (3.11.16) used a freshly created venv, because the existing one had no pip or pytest.

Not covered: the `browse` and `evaluate` skills cannot be run live here; they are covered by the wording tests only, and the migration steps were followed by a script, not by an agent. No real posting and no real workspace were used, so the quality of an agent's observations, quotes and migration answers on real text is untested.

## Extended approvals (0.7.0)

- Date: 2026-10-19
- Commit under test: `3fb0a7b` (the end-to-end test and this record are added by the commit that follows it)
- Method: `tests/test_approvals_e2e.py` drives the real CLI (`careeros approval ...`, `approve`, `transition`, `validate`) on brand-new invented workspaces in a temporary directory, with invented people, invented jobs and placeholder message text only. One settable clock supplies both the commands' `now` and the ledger stamps. `approve` and `resolve` need an interactive terminal; the walk plays it by patching the interactive check (the seam the other approval tests use), and separate tests run real subprocesses with closed or piped stdin. No real workspace was read, copied or touched.

| # | Step | Exit | Result |
|---|---|---|---|
| 1 | `approval propose` (invite), `approve`, `approval begin`, `approval done` | 0 each | states `pending`, `approved`, `in_flight`, `consumed` |
| 2 | `approval propose` (invite, same person, 5 URL spellings: same, other case, trailing slash, country subdomain, query) | 1 each | `invite_conflict` |
| 3 | `approval propose` (message, URL variant) | 0 | same `per_` id as the invite |
| 4 | `approve`, then `approval begin` at exactly 24 h | 0, 1 | `expired` |
| 5 | `approval begin` again at 24 h | 1 | `expired`; no second `approval.rejected` (deduped) |
| 6 | `approval begin` with the clock set back; `approval propose` with the clock set back | 1, 1 | `clock_skew`; the item is not revived |
| 7 | submit with a 2-file manifest: `approve`, edit a file, `approval begin` | 0, 1 | `manifest_changed` |
| 8 | re-propose, `approve`, `approval begin` | 0 | old item `superseded`, new item `in_flight` |
| 9 | `approval propose` (submit, same job) | 1 | `in_flight_conflict` |
| 10 | `validate`, `approval list`, `approval list --json`, `approval show` | 0 each | workspace identical (see below) |
| 11 | `transition --to APPLIED` | 1 | `submission_unresolved` |
| 12 | `approval resolve --sent`, then `transition --to APPLIED` | 0, 0 | `consumed`, then the transition succeeds |
| 13 | second job: approve, `begin`, edit a manifest file, `resolve --not-sent` | 0 | `voided` |
| 14 | `transition --to APPLIED` on that job | 1 | `submission_not_consumed` |
| 15 | `approval show` and the `approve` queue on a draft with an escape sequence, a bidi override and a zero-width character | 0 | shown as `\x1b`, `\u202e`, `\u200b`; no raw character in the output; queue `0 approved, 2 declined, 0 skipped` |
| 16 | real subprocess `approve`, stdin closed, and stdin piped | 2, 2 | `not_interactive`; item still `pending` |
| 17 | real subprocess `approval resolve --sent`, no terminal | 2 | item still `in_flight` |
| 18 | two real processes racing `approval begin` on one approved item | 0, 1 | exactly one `approval.started`; the loser `wrong_state` |

- Read-only commands: before and after `validate`, `validate --json`, `approval list`, `approval list --json` and `approval show`, the path list and the bytes and modification time of every path (including `ledger.jsonl` and `.careeros/`) were compared: identical, nothing created, changed or removed.
- Events across the walk: 7 `approval.proposed`, 5 `approval.approved`, 2 `approval.declined`, 1 `approval.superseded`, 3 `approval.started`, 2 `approval.consumed`, 1 `approval.voided`, 3 `approval.rejected` (`expired`, `clock_skew`, `manifest_changed`; no identical repeat). Refusals of a new proposal and of `transition` append no `approval.rejected`. The ledger hash chain verifies.
- Isolation: `career/`, the other job and every `jd.md` are byte-identical after the walk; the other job has no event beyond its import.
- Automated tests: Python 3.14: 2620 passed (51 s); Python 3.11.16: 2620 passed (50 s). The focused approvals files (e2e, claims, state machine, propose) alone: 329 passed on 3.14.
- Python 3.11 venv: `python3.11` (3.11.16, a fresh venv created for the run, without click), `pip install -e ".[dev]"`, and `pip show click` reports that click is not installed. The first fresh 3.11 run found that `careeros/cli/approve.py` imported `click`, which is not a declared dependency and which the current typer no longer installs, so the CLI could not be imported. That was fixed in `3fb0a7b` and is now guarded by `tests/test_cli_dependencies.py`.
- Release checks: `mkdocs build --strict` exit 0; wheel `careeros-0.7.0` contains `careeros/core/approvals/{__init__,fold,item,manifest,model,render,rules,service}.py`, `careeros/cli/approval.py` and the `outreach`, `follow-up` and `apply` skills; `git diff --check` exit 0.

Not covered: the `outreach`, `follow-up` and `apply` skills were not run live (their wording is pinned by tests only); no real LinkedIn and no real ATS were used; the behaviour of an agent following the skills is untested; a real pty approval was not exercised (the interactive check was patched, and the non-interactive refusals were run through real processes). This feature is a recorded, content-bound decision, not a security boundary.
