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
