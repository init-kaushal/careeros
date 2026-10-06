# CareerOS Foundation (sub-project 1) — design

**Date:** 2026-10-05, revised 2026-10-06 after review
**Status:** approved with nine review changes, all incorporated below
**Baseline:** `docs/IMPLEMENTATION_AUDIT.md` (commit `64a22a4`)
**Scope note:** the request that started this sub-project was cut off mid-sentence at "expose `career…`". This spec assumes the remainder covers the ledger, the remaining CLI commands and migration, as in the original Career OS brief. Anything beyond that is out of scope until the user says otherwise.

## 1. Goal

Give CareerOS a small, tested Python core that makes the workspace **versioned, identifiable, validatable and auditable**, with Markdown and YAML staying the canonical, user-owned data. Concretely:

- every workspace and every job carries versions and a stable ID;
- a deterministic validator can say what is wrong and how to fix it;
- application status moves only along a defined state machine, atomically;
- every change made through the core is recorded in an append-only, tamper-evident ledger;
- existing workspaces migrate safely, with a verified backup, and never lose user data.

**Success looks like:** `careeros migrate` on a **copy** of the real `~/Projects/job-search` workspace (33 jobs, all `discovered`) adds versions and IDs, leaves every body byte-identical, writes a verified backup with a manifest, and afterwards `careeros validate` reports no errors; `careeros transition` rejects an illegal move and refuses `APPLIED` without a recorded approval; a failed ledger append leaves the job byte-identical; `careeros ledger verify` fails if any past line is edited.

**The real workspace is not modified by this sub-project.** All migration testing and the acceptance workflow run against a temporary copy. Migrating the real workspace happens only when the user asks for it afterwards.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Structure | Core library first (`careeros/core/`), CLI second (`careeros/cli/`), CLI is a thin wrapper | lets any shell-capable agent call the same logic |
| Canonical data | Markdown with YAML frontmatter; plain-text sidecars | user-owned, diffable, exportable |
| YAML dependency | add `pyyaml>=6.0`, `safe_load` only | the standard library has no YAML; hand-rolling a parser is riskier than one small dependency |
| Models | stdlib `dataclasses` and enums, no pydantic | v0.2 removed pydantic on purpose; the shapes are small |
| Database | none | markdown plus an append-only text ledger is enough |
| Ledger format | JSON Lines, one event per line, with a sequence number and hash chain | human-readable, appendable, and a rewrite becomes detectable |
| Concurrency | one **workspace lock** (cross-process, re-entrant within a process) around every mutating operation | the ledger lock alone cannot protect a read-validate-write-append sequence |
| File writes | temp file in the same directory, `fsync`, atomic rename | a crash never leaves a half-written file |
| Status source of truth | `status` in `job.md` frontmatter; the legacy `- **Status:**` bullet stays as a display line, kept in sync by `transition` and checked for drift | one authoritative field |
| Application entity | not separate yet; one application per job, state lives on the job | the workspace already models it that way |
| Entities in scope | workspace metadata, Job, ActivityEvent; an ID prefix table for all 27 entities in the brief | later sub-projects reuse consistent prefixes |
| Approvals | the state machine requires a recorded approval before `APPLIED`; a minimal `careeros approve` exists so `APPLIED` is reachable | the guard cannot be added later without breaking the state machine; the full approval system stays in sub-project 3 |
| Skills | **not rewritten** in this sub-project | that is workflow work (sub-projects 3 and 4) |
| Human log vs audit log | `activity.md` stays the free-text human log; `ledger.jsonl` is the machine and audit log | reconciling them is deferred |

## 3. Layout

```text
careeros/
  __init__.py            # __version__ read from package metadata (single source of truth)
  core/
    models.py            # State, Approval, WorkspaceMeta, Job, Issue; UTC timestamp helpers
    ids.py               # PREFIXES table, new_id(), is_valid_id()
    versions.py          # SCHEMA_VERSION, installed_version(), compare helpers
    workspace.py         # discovery, meta, frontmatter, atomic writes, WorkspaceLock, job lookup
    validation.py        # rule-based validator returning Issue records
    ledger.py            # append, batch append, read, verify (hash chain)
    state_machine.py     # legal transitions, guards, approval status, transition/approve/archive
    migration.py         # backup with manifest, migrate and upgrade plans and runners
  cli/
    main.py              # registers all commands (existing `init` kept)
    _util.py             # workspace resolution, exit codes, JSON helper
    doctor.py  status.py  validate.py  ledger.py
    transition.py  approve.py  archive.py  migrate.py  upgrade.py
tests/
  helpers.py             # builder for a realistic legacy (schema 0) workspace
  test_core_*.py  test_cli_*.py  test_foundation_e2e.py
.github/workflows/tests.yml
CHANGELOG.md
```

`careeros/__init__.py` currently says `0.1.0` while `pyproject.toml` says `0.2.0`. This sub-project removes the duplicate: `__version__` is read with `importlib.metadata.version("careeros")`, and the package version becomes **0.3.0**.

## 4. Versions and timestamps

### 4.1 Workspace metadata

`.careeros/workspace.yaml`:

```yaml
schema_version: 1
framework_version: 0.3.0
created_at: 2026-10-06T09:15:00Z
updated_at: 2026-10-06T09:15:00Z
runtimes: [claude]
```

### 4.2 Version semantics

Three numbers are kept distinct:

| Name | Meaning | Source |
|---|---|---|
| **Installed version** | the CareerOS package that is running now | package metadata |
| **Workspace `framework_version`** | the framework version **last applied** to this workspace by `init`, `upgrade` or `migrate` | `.careeros/workspace.yaml` |
| **`schema_version`** | the shape of the user data | `.careeros/workspace.yaml`; `0` means a legacy workspace with no metadata file |

Comparison uses the numeric `MAJOR.MINOR.PATCH` prefix; any suffix after it (for example `.dev1`) is ignored for ordering. A value that does not start with three numbers is invalid (`WS003`).

| Relation | Meaning | Behaviour |
|---|---|---|
| installed > workspace | upgrade available | `WS004` warning; `doctor` and `status` say "run `careeros upgrade`"; writes still allowed |
| installed == workspace | current | nothing |
| installed < workspace | the workspace was last touched by a newer CareerOS | `WS005` **error**; every mutating command refuses to run; read-only commands still work |
| schema newer than the installed core supports | same idea for data shape | `WS002` **error**; mutating commands refuse |

### 4.3 Timestamps

All `created_at`, `updated_at`, `archived_at` and ledger `ts` values are **full UTC ISO-8601 with seconds and a trailing `Z`**, for example `2026-10-06T09:15:00Z`. One helper, `models.utc_now()`, produces them, and tests replace that single function to control time. A malformed timestamp is `JOB006`.

Legacy migration data has only a date (the `- **Discovered:**` bullet). Migration writes it as midnight UTC of that day and adds `created_at_precision: date` to the frontmatter, so the file is honest that the time of day is unknown. The migration report lists how many jobs got date-precision timestamps.

## 5. IDs

`ids.py` holds a prefix table with one three-letter prefix for each of the 27 entities listed in the brief (CareerProfile, Experience, Achievement, Project, Skill, Education, Certification, CareerGoal, Preference, Constraint, CareerStory, Evidence, Company, Person, Job, JobRequirement, JobEvaluation, Application, Outreach, FollowUp, Interview, InterviewRound, Offer, Document, Decision, ActivityEvent, LearningGap), so later sub-projects stay consistent. This sub-project uses `job` and `evt`.

Format: `<prefix>_<10 characters of lowercase Crockford base32>` from `secrets`, for example `job_7k3m9q2xta`. `new_id(kind, existing=())` retries on collision. `is_valid_id(kind, value)` checks prefix and shape. IDs are never derived from mutable fields and never reused.

## 6. Job records

`jobs/discovered/[slug]/job.md` gains YAML frontmatter at the very top. The existing body, including the `- **Field:** value` bullets, is preserved byte for byte:

```markdown
---
id: job_7k3m9q2xta
type: job
schema: 1
status: DISCOVERED
company: Adyen
title: Senior Platform Engineer (Distributed Data Stores)
url: https://job-boards.greenhouse.io/adyen/jobs/7720026
created_at: 2026-10-01T00:00:00Z
created_at_precision: date
updated_at: 2026-10-06T09:15:00Z
---
# Senior Platform Engineer (Distributed Data Stores) at Adyen

- **Board:** ...
```

- `company` and `title` come from the `# [Title] at [Company]` heading, splitting on the **last** ` at `. If the heading does not match, migration reports the file instead of guessing.
- `url` comes from the `- **URL:**` bullet and `created_at` from `- **Discovered:**`. If either is missing, migration reports the file and changes nothing.
- Optional keys: `archived: true` and `archived_at` (section 7.2).
- Bullets stay as the human-readable display. `validate` compares the `Status:` bullet to the frontmatter and reports `JOB005` on a mismatch.

**Legacy status mapping** (migration only):

| Legacy `Status:` bullet | `status` |
|---|---|
| `discovered` | `DISCOVERED` |
| `applied` | `APPLIED` |
| `interview` | `SCREEN` |
| `offer` | `OFFER` |
| `accepted` | `ACCEPTED` |
| `declined`, `closed` | `WITHDRAWN` |
| `rejected` | `REJECTED` |

`interview` maps to `SCREEN` because the legacy field does not record the stage; the migration report lists every such job. Any other value is an error that aborts the whole migration before anything is written.

## 7. Jobs over time

### 7.1 State machine

```text
DISCOVERED → EVALUATED → SHORTLISTED → RESEARCHED → PREPARING → READY_TO_APPLY
→ APPROVAL_REQUIRED → APPLIED → RECRUITER_REPLIED → SCREEN → TECHNICAL → HM → FINAL → OFFER
→ ACCEPTED | REJECTED | WITHDRAWN
```

1. **Pre-application stages** (`DISCOVERED` … `READY_TO_APPLY`): forward to any later pre-application stage, never backward.
2. `READY_TO_APPLY` → `APPROVAL_REQUIRED` is the only way into `APPROVAL_REQUIRED`.
3. `APPROVAL_REQUIRED` → `APPLIED` is the only way into `APPLIED`, and it needs an approval (rule 6).
4. **Post-application stages** (`APPLIED` … `OFFER`): forward to any later stage, never backward.
5. `ACCEPTED` only from `OFFER`. `REJECTED` from `APPLIED` onward. `WITHDRAWN` from any non-terminal state.
6. **Approval guard:** moving to `APPLIED` requires that the **latest approval decision since the job last entered `APPROVAL_REQUIRED`** is `approved`. Otherwise: "approval required — ask the user to run `careeros approve <job>` themselves".
7. `ACCEPTED`, `REJECTED` and `WITHDRAWN` are terminal.

### 7.2 Corrections, archive, lifecycle

**Forced corrections.** `transition --force --reason "…"` is a different operation from a normal move. It may make a move the rules forbid, with three exceptions: it cannot leave a terminal state, it cannot move into `APPLIED` unless the approval guard is satisfied, and `--reason` must be non-empty. It never records `job.status_changed`. It records **`job.status_corrected`** with `prev_state`, `new_state`, `actor` and `reason`, and the validator treats only that event type as a legitimate rule-breaking change.

**Entities referenced by the ledger are never physically deleted.** They may be **archived**. `careeros archive <job>` sets `archived: true` and `archived_at` in the frontmatter, leaves the directory and files exactly where they are, and records `job.archived`; `careeros archive <job> --undo` clears the flags and records `job.unarchived`. Archived jobs accept no transitions until unarchived, are excluded from `PIPE002`, and are otherwise validated normally. If a job directory is deleted anyway, `LED003` reports it as an error whose fix says "restore it from `.careeros/backups/` or version control; archive instead of deleting". This keeps historical ledger verification intact.

### 7.3 Atomic, serialised transitions

`apply_transition` is the only function that changes `status`. Everything runs under the **workspace lock** (section 9.1):

1. refuse if the workspace is newer than the installed CareerOS (section 4.2);
2. read the job and, if present, `jobs/pipeline.md`, keeping the original bytes;
3. validate the move (rules above) and compute the new texts: frontmatter `status` and `updated_at`, the `- **Status:**` display bullet, and the pipeline icon on the line whose URL matches the job;
4. write the job file, then the pipeline file, each via **temp file, `fsync`, atomic rename**;
5. append the ledger event;
6. **if the ledger append fails**, restore every file written in step 4 to its original bytes, again atomically, and re-raise. If a restore itself fails, the original bytes are saved under `.careeros/recovery/` and the error names that path.

Result: the job file and pipeline either both change with a ledger event, or none of them change. A test proves a failed ledger append leaves the job byte-identical.

### 7.4 Idempotency

Retrying must not create duplicate logical events or change state twice.

- `transition --to S` when the job is **already** in `S`: no state change and no new event. If the ledger's latest recorded state for that job differs (a process died between steps 4 and 5), the transition **repairs the ledger**: it appends one `job.status_changed` event with `source: recovery` and reports "ledger repaired". Migration records a baseline `job.imported` event per job (section 10), so this check has something to compare with.
- `approve <job>` when an approval is already the latest decision for the current stage: no new event, reports the original approval time.
- `archive <job>` on an already-archived job: no change, no event.
- A rejected transition is logged once: an identical rejection (same job, same target, same reason) immediately after a previous identical one is not logged again.

### 7.5 Approval semantics

`careeros approve <job>` records `application.approved` with `actor: user`, the timestamp, `approval: approved` and `source: tty`. If the user declines the prompt it records `application.approval_denied` with `approval: denied`. A later `approve` can reverse a denial; the **latest** decision for the stage wins.

The terminal check is a **human-confirmation guard against accidental execution**, not a security boundary. See section 13 for the explicit limitation. To make the guard meaningful, `ledger append` refuses the reserved event types (`application.approved`, `application.approval_denied`, `job.status_changed`, `job.status_corrected`, `job.archived`, `job.unarchived`, `job.imported`, `workspace.*`); only the dedicated commands write them.

## 8. Ledger

`ledger.jsonl` at the workspace root, UTF-8, one JSON object per line, never rewritten. Fields:

| Field | Meaning |
|---|---|
| `id` | `evt_…` |
| `seq` | 1-based, contiguous |
| `ts` | UTC timestamp (section 4.3) |
| `type` | dotted name, see the table below |
| `actor` | `user`, `agent:<runtime>`, or `system` |
| `entity` | the affected entity ID, or `null` |
| `prev_state`, `new_state` | for status changes, else `null` |
| `action` | one human-readable sentence |
| `reason` | required for `job.status_corrected` and `job.transition_rejected`, else `null` |
| `approval` | `not_required`, `required`, `approved` or `denied` |
| `artifacts` | list of workspace-relative paths |
| `source` | provenance: `tty`, `cli`, `recovery`, `migration`, or free text |
| `prev` | SHA-256 (hex) of the previous line's bytes **excluding its trailing newline**; the first event uses 64 zeros |

Event types written in Foundation:

| Type | When | Notes |
|---|---|---|
| `workspace.created` | `careeros init` on a new workspace | |
| `workspace.migrated` | a migration completed | names the backup directory and counts |
| `workspace.migrate_declined`, `workspace.upgrade_declined` | the user declined the confirmation | only when a ledger already exists |
| `workspace.upgraded` | `upgrade` (or `init --refresh` on a schema-1 workspace) completed | |
| `job.imported` | migration gave a job its first ID | baseline state, one per job |
| `job.status_changed` | a normal transition, or a ledger repair | |
| `job.status_corrected` | a forced correction | carries `reason` |
| `job.transition_rejected` | a move failed a rule (illegal, missing approval, archived) | no state change; carries `reason` |
| `application.approved`, `application.approval_denied` | `approve` | `actor: user` |
| `job.archived`, `job.unarchived` | `archive` | |

Failure and decline paths are logged on purpose, following the standing CareerOS rule that every action, including refused and declined ones, leaves a trail. Usage errors (bad arguments, no terminal, lock timeout) happen before any decision and are not logged.

**Locking and appends.** Appends run under the workspace lock. `append_events` writes a batch in a single call, `fsync`s, and on any failure truncates the file back to its original length. An append refuses to extend a file that does not end with a newline (a torn last line); `verify` reports it.

`verify` re-reads the file and checks: valid JSON, required fields, contiguous `seq`, unbroken `prev` chain, and a known `type`. The validator adds entity and state checks (section 9).

**Honest limit.** The chain makes tampering **detectable**, not impossible. Someone who rewrites the whole file consistently from some point onward can produce a valid chain; the ledger is an audit aid, not a security boundary.

## 9. Validation and locking

### 9.1 Workspace lock

`.careeros/lock`, held with an exclusive OS file lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). It is **re-entrant within a thread**, so a locked `transition` can call the ledger append without deadlocking, exclusive across threads and processes, and times out after 10 seconds with "another careeros command is running in this workspace".

### 9.2 Rules

`validation.py` returns `Issue(severity, code, path, message, fix)` records. `error` fails the command; `warning` does not unless `--strict`.

| Code | Severity | Check |
|---|---|---|
| `WS001` | error | `.careeros/workspace.yaml` missing (legacy workspace); fix: `careeros migrate` |
| `WS002` | error | `schema_version` newer than the installed core supports |
| `WS003` | error | workspace metadata unreadable, missing keys, or an invalid version string |
| `WS004` | warning | installed version newer than the workspace `framework_version`; fix: `careeros upgrade` |
| `WS005` | error | installed version older than the workspace `framework_version` |
| `STR001` | error | `profile.md` or `jobs/` missing |
| `STR002` | warning | `activity.md` or `jobs/pipeline.md` missing |
| `JOB001` | warning | `job.md` has no frontmatter; fix: `careeros migrate` |
| `JOB002` | error | required frontmatter key missing (`id`, `type`, `status`, `company`, `title`, `created_at`, `updated_at`) |
| `JOB003` | error | `id` has the wrong prefix or shape, or is duplicated |
| `JOB004` | error | `status` is not a valid state |
| `JOB005` | warning | `Status:` bullet disagrees with the frontmatter; fix: `careeros transition` or edit the bullet |
| `JOB006` | error | a timestamp is not full UTC ISO-8601 |
| `PIPE001` | warning | pipeline line whose URL matches no job |
| `PIPE002` | warning | active job (not archived) with no pipeline line |
| `PIPE003` | warning | pipeline icon inconsistent with the job's state |
| `LED001` | error | ledger line unparseable, missing fields, unknown type, or a torn last line |
| `LED002` | error | `seq` gap or broken hash chain |
| `LED003` | error | event references an entity ID with no file (deleted; archive instead) |
| `LED004` | error | a ledger status change the state machine forbids and that is not a `job.status_corrected` |
| `LED005` | warning | a job's status is not the one its latest ledger event recorded; fix: `careeros transition --to <status>` repairs the ledger |

Every issue names the file and says what to do. Output is human-readable by default and machine-readable with `--json`. Exit code 0 means no errors, 1 means errors, 2 means usage error.

## 10. Migration and upgrade

### 10.1 `careeros migrate`

A registry maps `from_version → (to_version, function)`; Foundation registers `0 → 1`. Migration is **idempotent**: running it again also gives new, un-migrated jobs (created later by skills that do not yet write frontmatter) their frontmatter, ID and baseline event.

1. **Plan** everything in memory: which files change, which are reported, which IDs will be assigned, and each existing file's SHA-256. If any file hits a hard error, print all errors and **write nothing**.
2. **Show** the plan. In a terminal, ask for confirmation. Without one, require `--yes`, otherwise print the plan and exit 2. `--dry-run` prints the plan and stops. A declined confirmation is recorded as `workspace.migrate_declined` if a ledger exists.
3. **Back up** every existing file that will change into `.careeros/backups/<UTC timestamp>-migrate/`, preserving relative paths.
4. **Verify the backup:** re-read each original, confirm its hash still equals the plan's `before_sha256` (nothing changed while the user was deciding), and confirm each backup copy's SHA-256 equals the original's. Any mismatch aborts before the workspace is modified.
5. Write `manifest.json` with `status: "pending"` and the **before** hashes.
6. **Apply** under the workspace lock: write `.careeros/workspace.yaml`, then the job frontmatter, each atomically.
7. **Append** one `workspace.migrated` event and one `job.imported` event per job in a single batch.
8. Rewrite `manifest.json` with `status: "complete"` and the **after** hashes.
9. If step 6 or 7 fails, restore every changed file from the backup, remove files the migration created, truncate the ledger back, mark the manifest `rolled_back`, and report the error.

`manifest.json`:

```json
{
  "version": 1,
  "kind": "migrate",
  "created_at": "2026-10-06T09:15:00Z",
  "tool_version": "0.3.0",
  "source_schema": 0,
  "target_schema": 1,
  "status": "complete",
  "files": [
    {"path": "jobs/discovered/adyen-.../job.md", "existed": true,
     "before_sha256": "…", "after_sha256": "…"},
    {"path": ".careeros/workspace.yaml", "existed": false,
     "before_sha256": null, "after_sha256": "…"}
  ]
}
```

`doctor` reports any manifest left in `pending` as an incomplete operation and names the backup to restore from.

User data is only ever **added to**: frontmatter is prepended; nothing in a body, `profile.md`, `boards.md`, `activity.md`, `resume.md`, `markets/`, PDFs or `people.md` is modified.

### 10.2 `careeros upgrade`

Selects the same framework files as the existing scaffold (skill files and the entry file, per runtime) but backs up differently:

1. Compare each framework file in the workspace with the installed templates and list `new`, `changed`, `unchanged`.
2. Show the list and ask for confirmation (same terminal rule as `migrate`). A declined confirmation is recorded as `workspace.upgrade_declined` if a ledger exists.
3. Back up changed files to `.careeros/backups/<timestamp>-upgrade/` with a `manifest.json`, verified as in 10.1 step 4, then write the new files atomically.
4. Update `framework_version` in `workspace.yaml` and append `workspace.upgraded` (only if the workspace has metadata and a ledger).
5. If the schema is behind, say so and point to `migrate`.

`init --refresh` keeps its current behaviour (including the `.bak` of the entry file) and, on a schema-1 workspace, also updates `framework_version` and appends `workspace.upgraded`.

## 11. CLI

Thin wrappers over `core`. All accept `--workspace PATH` (or `CAREEROS_WORKSPACE`, or discovery by walking up from the current directory to the first `.careeros/workspace.yaml`, or a legacy `profile.md` plus `jobs/`). `--json` is available where output is data.

| Command | Does |
|---|---|
| `careeros doctor` | environment and workspace health: Python and CareerOS versions, workspace and schema versions and their relation (section 4.2), stale framework files, validation summary, incomplete migrations, and a warning if the workspace is inside a git repository (it holds personal data; keep any remote private). Read-only |
| `careeros status` | one-screen summary: versions, jobs by state, archived count, last five ledger events, whether migrate or upgrade is needed. Read-only |
| `careeros validate [--strict] [--json]` | section 9.2 |
| `careeros ledger append --type … --entity … --actor … --action …` | appends one non-reserved event |
| `careeros ledger list [--entity ID] [--since TS] [--type PREFIX] [--json]` | reads the ledger |
| `careeros ledger verify` | chain checks (`LED001`, `LED002`) |
| `careeros transition <job> --to STATE [--actor …] [--force --reason …]` | `<job>` is an ID, a slug or a directory path |
| `careeros approve <job>` | section 7.5. Requires an interactive terminal; without one it exits 2 and tells the agent to ask the user to run it themselves (in Claude Code, `! careeros approve …`) |
| `careeros archive <job> [--undo] [--reason …]` | section 7.2 |
| `careeros migrate [--dry-run] [--yes]` | section 10.1 |
| `careeros upgrade [--yes]` | section 10.2 |
| `careeros init` | unchanged, plus writing `workspace.yaml` and a `workspace.created` event for new workspaces |

Exit codes: 0 success, 1 the operation failed or validation found errors, 2 usage error or confirmation required.

## 12. Out of scope

- Evidence model, Career Memory tree, fabrication validator (sub-project 2).
- Job evaluation, compensation intelligence, the full approval system, dashboard, outreach engine, interview and offer systems (later sub-projects). Foundation ships only the `APPLIED` guard and the minimal `approve`.
- Rewriting any skill. Until sub-projects 3 and 4, skills keep editing bullets and appending to `activity.md`, so `validate` will report `JOB005`, `PIPE*` and `LED005` warnings after skills run. That is intended and each warning says how to fix it.
- Reconciling `activity.md` with the ledger. `activity.md` stays the human-readable log; `ledger.jsonl` is the machine and audit log.
- Company, Person and Application as stored entities.
- GPT Work: the core is runtime-neutral, but ChatGPT Projects cannot run commands, so its workflow does not change.
- Cleaning up `tests/integration/__init__.py` (stale docstring from v0.1; noted in the audit).
- Migrating the real `~/Projects/job-search` workspace.

## 13. Risks and limits

- **The approval prompt is not a security boundary.** It is a human-confirmation and accidental-execution guard. It stops an agent from approving by habit and makes the required human step explicit and recorded as `actor: user`. An agent with unrestricted shell access could still allocate its own terminal and answer the prompt. The ledger makes that auditable after the fact; it does not prevent it. Real prevention would need an out-of-band channel the agent cannot reach, which is not in this sub-project.
- **Frontmatter and bullets can drift** because skills still write bullets. Warnings, not errors, keep the workspace usable meanwhile.
- **Migration touches user files** (additively). The all-or-nothing plan, confirmation, verified backup, manifest, rollback and the byte-identical-body test are the mitigations.
- **Windows locking** uses `msvcrt` and is not exercised by the test suite here.
- **PyYAML is a new dependency.** `safe_load` only; version floor 6.0.
- **Crash between file write and ledger append** is repaired by retrying the same transition (section 7.4) and is flagged meanwhile by `LED005`.

## 14. Testing

Test-first, per module:

- **ids:** prefix table has 27 unique prefixes, format, collision retry, `is_valid_id`.
- **versions and timestamps:** compare rules, suffix ignored, invalid versions, `utc_now` shape, malformed timestamps.
- **workspace IO:** atomic write leaves no temp file and preserves mode; an exception during write leaves the target unchanged; frontmatter round trip keeps the body byte-identical; lock excludes a second process, is re-entrant, times out with the documented message, and two threads cannot lose updates.
- **state machine:** an exhaustive table test over every (from, to) state pair against the rules; the approval guard; forced corrections including the three forbidden cases; archive blocks transitions.
- **atomicity:** a failed ledger append leaves the job **and** the pipeline byte-identical; a failed restore writes a recovery file.
- **idempotency:** repeating a transition, an approval and an archive creates no second event; a simulated crash (job written, ledger not) is repaired by retrying; identical rejections are logged once.
- **ledger:** append, contiguous `seq`, hash chain, tamper detection (edit, delete, reorder, truncated last line), batch append truncates on failure, reserved types refused by `ledger append`.
- **validation:** one case per rule code, asserting code, severity and a non-empty fix.
- **migration:** a realistic legacy workspace built by `tests/helpers.py` (eight jobs covering every legacy status, a pipeline, a market file, `activity.md`, a PDF). Bodies byte-identical; backup copies match originals and `manifest.json` has correct before and after hashes; an altered original after planning aborts; an unknown status aborts with nothing written; a failure in the middle rolls back completely; a second run is a no-op; untouched files stay untouched; `--dry-run` writes nothing; legacy dates become midnight UTC with `created_at_precision: date`.
- **upgrade:** user data survives, changed framework files are backed up with a manifest, `framework_version` is updated, a workspace newer than the installed version is refused.
- **CLI:** each command through Typer's `CliRunner`: exit codes, `--json`, `--workspace`, `approve` refusing without a terminal, `ledger append` refusing reserved types, declined confirmations logged.
- **End to end:** scaffold, add legacy jobs, `migrate`, `validate`, walk one job to `OFFER` with approval, `ledger verify`, tamper with the ledger, confirm `verify` fails.
- **Compatibility:** the existing 31 tests keep passing unchanged.

CI: `.github/workflows/tests.yml` runs `pytest` on push and pull request (the audit found CI ran no tests). `CHANGELOG.md` is added with a 0.3.0 entry.

## 15. Acceptance

All steps run against a **temporary copy** of `~/Projects/job-search`; the real workspace is not modified.

1. The full suite passes locally and in CI.
2. On the copy: `migrate --dry-run`, then `migrate --yes`, then `validate` reports no errors; every `job.md` body is byte-identical to before; the backup and a correct `manifest.json` exist.
3. `transition` rejects an illegal move, rejects `APPLIED` without approval, records legal moves in the ledger, and a repeated transition adds no event.
4. A failed ledger append leaves the job byte-identical.
5. `ledger verify` fails after a past line is edited.
6. `careeros doctor` and `status` run on a fresh and a migrated workspace.
7. `docs/END_TO_END_TEST.md` records exactly what was run and its results.
8. `README.md`, `ROADMAP.md` and `CHANGELOG.md` describe only what exists.
