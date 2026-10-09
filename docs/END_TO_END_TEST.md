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
