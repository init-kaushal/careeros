# Workspace health, versions and audit

CareerOS keeps your career data in plain markdown you own. Version 0.3.0 adds a small layer underneath that makes the workspace **versioned, identifiable and auditable** without moving your data anywhere.

## What it adds to a workspace

| File | What it is |
|---|---|
| `.careeros/workspace.yaml` | schema version, the framework version last applied, runtimes |
| `ledger.jsonl` | an append-only record of every change made through the commands below, each line chained to the one before it |
| `jobs/discovered/[job]/job.md` frontmatter | a stable `id`, a `status`, and timestamps at the top of each job file; the rest of the file is untouched |
| `.careeros/backups/` | a copy of every file a migration or upgrade changes, with a `manifest.json` of SHA-256 hashes |

`activity.md` stays your human-readable log. The ledger is the machine and audit log.

## Commands

| Command | What it does |
|---|---|
| `careeros doctor` | environment and workspace health, stale skill files, incomplete operations, a warning if the workspace is inside a git repository |
| `careeros status` | versions, jobs by state, the last five ledger events, whether migrate or upgrade is needed |
| `careeros validate [--strict] [--json]` | checks structure, versions, job files, the pipeline and the ledger, and says how to fix each finding |
| `careeros transition <job> --to STATE` | moves a job along the state machine, updating the job file, the pipeline icon and the ledger together |
| `careeros approve <job>` | records your decision to submit an application; needs an interactive terminal |
| `careeros archive <job> [--undo]` | archives a job instead of deleting it |
| `careeros ledger append / list / verify` | add a note event, read events, check the hash chain |
| `careeros migrate [--dry-run] [--yes]` | adds versions and IDs to an existing workspace, with a verified backup |
| `careeros upgrade [--yes]` | refreshes skill files and the entry file, with a verified backup |

All commands accept `--workspace PATH` or the `CAREEROS_WORKSPACE` environment variable; otherwise they look upward from the current directory.

## The job lifecycle

```text
DISCOVERED → EVALUATED → SHORTLISTED → RESEARCHED → PREPARING → READY_TO_APPLY
→ APPROVAL_REQUIRED → APPLIED → RECRUITER_REPLIED → SCREEN → TECHNICAL → HM → FINAL → OFFER
→ ACCEPTED | REJECTED | WITHDRAWN
```

Early stages may skip forward but never move back. `APPLIED` is reachable only from `APPROVAL_REQUIRED`, and only after you have run `careeros approve`. Corrections use `transition --force --reason "..."` and are recorded as `job.status_corrected`, never as a normal move.

## Safety guarantees

- Changes to a job file, the pipeline and the ledger happen together or not at all. If the ledger cannot be written, the files are put back byte for byte.
- Files are written through a temporary file and an atomic rename, under a workspace lock, so two commands cannot interleave.
- Migration and upgrade back up every file they change, verify each copy, write a manifest, and roll back if anything fails.
- Retrying a transition, an approval or an archive does not create a second event.
- A workspace last updated by a newer CareerOS is read-only until you upgrade CareerOS.

## What it does not guarantee

- **Approval is not a security boundary.** `careeros approve` asks a person to confirm at a terminal. It stops accidental or automatic approval. An agent with unrestricted shell access could still allocate a terminal and answer the prompt; the ledger would show it afterwards, but nothing prevents it.
- **The ledger is tamper-evident, not tamper-proof.** Editing or deleting a past line is detected by `ledger verify`. Rewriting the whole file consistently is not.
- **Skills have not been updated yet.** They still edit the `Status:` line and append to `activity.md`, so `careeros validate` will report drift warnings after they run. Each warning says how to resolve it.

## Migrating an existing workspace

```bash
careeros migrate --dry-run      # see what would change; nothing is written
careeros migrate                # review the plan, confirm; a verified backup is made first
careeros validate               # should report no errors
```

Migration only adds: frontmatter is placed above each job file, and nothing else in your workspace is modified. Jobs that the old format cannot describe (no `URL`, no `Discovered` date, an unknown `Status`) are listed by file and **nothing is written** until you fix them.

Jobs saved by skills after a migration have no ID yet. Run `careeros migrate` again to adopt them.

## Troubleshooting

- **"another careeros command is running in this workspace"** means the workspace lock is held. Wait a moment and retry.
- **`WS005` or "newer than the installed"** means a newer CareerOS last updated this workspace. Upgrade CareerOS.
- **`LED003`** means a job directory that the ledger mentions was deleted. Restore it from `.careeros/backups/` or version control; archive jobs instead of deleting them.
- **A manifest marked `pending`** (shown by `careeros doctor`) means an operation was interrupted. Restore the listed files from that backup folder, then run the command again.
- **`careeros doctor` reports errors right after `careeros init`** because onboarding has not run yet: `profile.md` is created by the onboarding skill, and `STR001`/`STR002` report the missing files. Open the workspace in Claude Code (or ChatGPT Projects) and run onboarding; the errors clear once `profile.md` and `jobs/` exist.
