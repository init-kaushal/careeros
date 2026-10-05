# CareerOS Foundation (sub-project 1) — design

**Date:** 2026-10-05
**Status:** drafted for review
**Baseline:** `docs/IMPLEMENTATION_AUDIT.md` (commit `64a22a4`)
**Scope note:** the request that started this sub-project was cut off mid-sentence at "expose `career…`". This spec assumes the remainder covers the ledger, the remaining CLI commands, and migration, as listed in the original Career OS brief. Anything beyond that is out of scope until the user says otherwise.

## 1. Goal

Give CareerOS a small, tested Python core that makes the workspace **versioned, identifiable, validatable and auditable**, with Markdown and YAML staying the canonical, user-owned data. Concretely:

- every workspace and every job carries versions and a stable ID;
- a deterministic validator can say what is wrong and how to fix it;
- application status moves only along a defined state machine;
- every change made through the core is recorded in an append-only, tamper-evident ledger;
- existing workspaces migrate safely, with a backup, and never lose user data.

**Success looks like:** `careeros migrate` on the real `~/Projects/job-search` workspace (33 jobs, all `discovered`) adds versions and IDs, leaves every body byte-identical, writes a backup, and afterwards `careeros validate` reports no errors; `careeros transition` rejects an illegal move and refuses `APPLIED` without a recorded approval; `careeros ledger verify` fails if any past line is edited.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Structure | Core library first (`careeros/core/`), CLI second (`careeros/cli/`), CLI is a thin wrapper | the user's instruction; lets any shell-capable agent call the same logic |
| Canonical data | Markdown with YAML frontmatter; plain-text sidecars | user-owned, diffable, exportable |
| YAML dependency | add `pyyaml>=6.0`, use `safe_load` only | the standard library has no YAML; hand-rolling a parser is riskier than one small dependency |
| Models | stdlib `dataclasses`, no pydantic | v0.2 removed pydantic on purpose; the shapes are small |
| Database | none | markdown plus an append-only text ledger is enough for Foundation |
| Ledger format | JSON Lines, one event per line, with a sequence number and hash chain | human-readable, appendable, and a rewrite becomes detectable |
| Status source of truth after migration | the `status` field in `job.md` frontmatter | one authoritative field; the legacy `- **Status:**` bullet is kept as a display line and checked for drift |
| Application entity | not separate yet; one application per job, state lives on the job | the workspace already models it that way |
| Entities in scope | workspace metadata, Job, ActivityEvent; an ID prefix table for all 27 entities in the brief | later sub-projects reuse consistent prefixes without schema churn |
| Approvals | the state machine **requires** a recorded approval before `APPLIED`; a minimal TTY-gated `careeros approve` exists so `APPLIED` is reachable | the guard cannot be added later without breaking the state machine; the full approval system stays in sub-project 3 |
| Skills | **not rewritten** to call the CLI in this sub-project | that is workflow work (sub-project 3 and 4); until then `validate` reports drift as warnings |
| `activity.md` | left untouched | reconciling the human log with the ledger is deferred; see section 12 |

## 3. Layout

```text
careeros/
  __init__.py            # __version__ read from package metadata (single source of truth)
  core/
    models.py            # State, Approval, WorkspaceMeta, Job, ActivityEvent dataclasses
    ids.py               # PREFIXES table, new_id(), is_valid_id()
    versions.py          # framework version, SCHEMA_VERSION, compare helpers
    workspace.py         # find/load/save workspace, meta file, job discovery, frontmatter IO
    validation.py        # rule-based validator returning Issue records
    ledger.py            # append, read, verify (hash chain)
    state_machine.py     # legal transitions, guards, apply_transition()
    migration.py         # backup + registry of schema migrations
  cli/
    main.py              # registers all commands (existing `init` kept)
    doctor.py  status.py  validate.py  ledger.py
    transition.py  approve.py  migrate.py  upgrade.py
tests/
  fixtures/legacy_workspace/   # realistic schema-0 workspace
  test_core_*.py  test_cli_*.py  test_foundation_e2e.py
.github/workflows/tests.yml
CHANGELOG.md
```

`careeros/__init__.py` currently says `0.1.0` while `pyproject.toml` says `0.2.0`. This sub-project removes the duplicate: `__version__` is read with `importlib.metadata.version("careeros")`, and the package version becomes **0.3.0**.

## 4. Versions

Three independent numbers, all recorded in `.careeros/workspace.yaml`:

```yaml
schema_version: 1            # shape of user data; changed only by a migration
framework_version: 0.3.0     # framework that last refreshed this workspace's skill files
created_at: 2026-10-05
updated_at: 2026-10-05
runtimes: [claude]
```

- **Framework version** = the installed `careeros` package version.
- **Schema version** = `SCHEMA_VERSION` constant in `versions.py`. Schema `0` means a legacy workspace with no `.careeros/workspace.yaml`. This sub-project defines schema `1`.
- A workspace whose `schema_version` is **higher** than the installed core supports is reported as an error ("workspace is newer than this CareerOS; upgrade the tool") and no command that writes will touch it.

`careeros init` for a new workspace also writes `.careeros/workspace.yaml` (schema 1). `init --refresh` keeps its current behaviour and additionally updates `framework_version` when the file exists.

## 5. IDs

`ids.py` holds a prefix table with one three-letter prefix for each of the 27 entities listed in the brief (CareerProfile, Experience, Achievement, Project, Skill, Education, Certification, CareerGoal, Preference, Constraint, CareerStory, Evidence, Company, Person, Job, JobRequirement, JobEvaluation, Application, Outreach, FollowUp, Interview, InterviewRound, Offer, Document, Decision, ActivityEvent, LearningGap), so later sub-projects stay consistent. The `job` and `evt` prefixes are used in this sub-project.

Format: `<prefix>_<10 characters of lowercase Crockford base32>` from `secrets`, for example `job_7k3m9q2xta`. `new_id(kind, existing=())` retries on collision with `existing`. `is_valid_id(kind, value)` checks prefix and shape. IDs are never reused and never derived from mutable fields; once written to a file they are stable.

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
created_at: 2026-10-01
updated_at: 2026-10-05
---
# Senior Platform Engineer (Distributed Data Stores) at Adyen

- **Board:** ...
```

- `company` and `title` are parsed from the `# [Title] at [Company]` heading (the format `browse` writes). If the heading does not match, migration reports the file instead of guessing.
- `url` comes from the `- **URL:**` bullet. `created_at` comes from the `- **Discovered:**` bullet. If either is absent, migration reports it and leaves the file unchanged.
- `updated_at` is set to the migration date.
- Bullets stay in the file as the human-readable display. `validate` compares the `Status:` bullet to the frontmatter and reports `STATE_DRIFT` if they disagree.

**Legacy status mapping** (used only by migration):

| Legacy `Status:` bullet | `status` |
|---|---|
| `discovered` | `DISCOVERED` |
| `applied` | `APPLIED` |
| `interview` | `SCREEN` |
| `offer` | `OFFER` |
| `accepted` | `ACCEPTED` |
| `declined`, `closed` | `WITHDRAWN` |
| `rejected` | `REJECTED` |

`interview` maps to `SCREEN` because the legacy field does not record the stage; migration lists every such job so the user can correct it. Any other value is an error that aborts the whole migration before anything is written.

## 7. State machine

`state_machine.py` defines `State` and the legal transitions.

```text
DISCOVERED → EVALUATED → SHORTLISTED → RESEARCHED → PREPARING → READY_TO_APPLY
→ APPROVAL_REQUIRED → APPLIED → RECRUITER_REPLIED → SCREEN → TECHNICAL → HM → FINAL → OFFER
→ ACCEPTED | REJECTED | WITHDRAWN
```

Rules:

1. **Pre-application stages** (`DISCOVERED` … `READY_TO_APPLY`): a job may move forward to any later pre-application stage (real work skips steps), never backward.
2. `READY_TO_APPLY` → `APPROVAL_REQUIRED` is the only way into `APPROVAL_REQUIRED`.
3. `APPROVAL_REQUIRED` → `APPLIED` is the only way into `APPLIED`, and it needs a recorded approval (rule 6).
4. **Post-application stages** (`APPLIED` … `OFFER`): forward to any later stage, never backward.
5. `ACCEPTED` is reachable only from `OFFER`. `REJECTED` is reachable from `APPLIED` onward (a company decision). `WITHDRAWN` is reachable from any non-terminal state (the user's decision, including skipping a job).
6. **Approval guard:** moving to `APPLIED` requires a ledger event for that job with `approval: approved`, dated after the job entered `APPROVAL_REQUIRED`. Without one the transition fails with "approval required — run `careeros approve <job>` yourself".
7. `ACCEPTED`, `REJECTED` and `WITHDRAWN` are terminal.
8. **Corrections:** `--force --reason "…"` allows any move except out of a terminal state or into `APPLIED` without approval, and records a `job.status_corrected` event so history is never silently rewritten.

`apply_transition(workspace, job, to_state, *, actor, reason=None, force=False)` is the only function that changes `status`. It validates, rewrites the frontmatter `status` and `updated_at`, updates the display bullet (`- **Status:** …`, lower-case legacy spelling), appends the ledger event, and does all three or none: if the ledger append fails, the file is not changed.

## 8. Ledger

`ledger.jsonl` at the workspace root, UTF-8, one JSON object per line, never rewritten. Fields:

| Field | Meaning |
|---|---|
| `id` | `evt_…` |
| `seq` | 1-based, contiguous |
| `ts` | UTC ISO 8601 timestamp |
| `type` | dotted name, for example `job.status_changed`, `application.approved`, `workspace.migrated` |
| `actor` | `user`, `agent:<runtime>`, or `system` |
| `entity` | the affected entity ID, or `null` |
| `prev_state`, `new_state` | for status changes, else `null` |
| `action` | one human-readable sentence |
| `approval` | `not_required`, `required`, `approved` or `denied` |
| `artifacts` | list of workspace-relative paths |
| `source` | optional provenance string |
| `prev` | SHA-256 of the previous line's exact bytes (the first event uses 64 zeros) |

`verify` re-reads the file and checks: valid JSON, required fields, contiguous `seq`, unbroken `prev` chain, `entity` IDs that exist, and that every status change in the ledger is a legal transition (forced ones carry a correcting type). Editing or deleting any past line breaks the chain and `verify` fails. Appends take an exclusive file lock so two processes cannot interleave.

Honest limit: the chain makes tampering **detectable**, not impossible. Someone who rewrites the whole file consistently from some point onward can produce a valid chain; the ledger is an audit aid, not a security boundary.

## 9. Validation

`validation.py` returns `Issue(severity, code, path, message, fix)` records. `error` fails the command; `warning` does not unless `--strict`. Rules:

| Code | Severity | Check |
|---|---|---|
| `WS001` | error | `.careeros/workspace.yaml` missing (legacy workspace) when schema 1 is expected; fix: `careeros migrate` |
| `WS002` | error | `schema_version` newer than the installed core supports |
| `WS003` | error | workspace metadata not valid YAML or missing required keys |
| `WS004` | warning | `framework_version` older than the installed framework; fix: `careeros upgrade` |
| `STR001` | error | `profile.md` or `jobs/` missing |
| `STR002` | warning | `activity.md` or `jobs/pipeline.md` missing |
| `JOB001` | warning | `job.md` has no frontmatter; fix: `careeros migrate` |
| `JOB002` | error | required frontmatter key missing (`id`, `type`, `status`, `company`, `title`, `created_at`) |
| `JOB003` | error | `id` has the wrong prefix or shape, or is duplicated across jobs |
| `JOB004` | error | `status` is not a valid state |
| `JOB005` | warning | `Status:` bullet disagrees with the frontmatter (`STATE_DRIFT`); fix: run `careeros transition` or edit the bullet |
| `PIPE001` | warning | pipeline line whose URL matches no job |
| `PIPE002` | warning | job with no pipeline line |
| `PIPE003` | warning | pipeline icon inconsistent with the job's state (`[ ]` early states, `[~]` `APPLIED`, `[?]` interview stages, `[✓]` `OFFER`/`ACCEPTED`, `[x]` `REJECTED`/`WITHDRAWN`) |
| `LED001` | error | ledger line unparseable or missing required fields |
| `LED002` | error | `seq` gap or broken hash chain |
| `LED003` | error | event references an entity ID that does not exist |
| `LED004` | error | a ledger status change that the state machine would not allow and that is not a recorded correction |

Every issue message names the file and says what to do. Output is human-readable by default and machine-readable with `--json`. Exit code is 0 with no errors, 1 with errors, 2 for usage errors.

## 10. Migration and upgrade

### `careeros migrate`

A registry maps `from_version → (to_version, function)`. Foundation registers `0 → 1`. The migration is **idempotent**: re-running it later also gives new, un-migrated jobs (created by skills that do not yet write frontmatter) their frontmatter and IDs.

Order of operations:

1. Compute the whole plan in memory (which files change, which are reported). If any file hits a hard error (unknown legacy status, unparseable heading), print all errors and **write nothing**.
2. Show the plan. In a terminal, ask for confirmation. Without a terminal, require `--yes`; otherwise print the plan and exit 2. `--dry-run` prints the plan and stops.
3. Copy every file that will change into `.careeros/backups/<UTC timestamp>/`, preserving relative paths.
4. Write `.careeros/workspace.yaml`, then the job frontmatter.
5. Append a `workspace.migrated` ledger event (creating the ledger if absent) naming the backup directory and counts.

User data is only ever **added to**: frontmatter is prepended, nothing in a body, `profile.md`, `boards.md`, `activity.md`, `resume.md`, `markets/`, PDFs or `people.md` is modified.

### `careeros upgrade`

1. Compare each framework file in the workspace (skill files, the entry file) with the installed templates and list `new`, `changed`, `unchanged`.
2. Show the list and ask for confirmation (same terminal rule as `migrate`).
3. Back up changed files to `.careeros/backups/<timestamp>/`, then refresh them using the existing `scaffold(refresh=True)` logic.
4. Update `framework_version` in `workspace.yaml` and append a `workspace.upgraded` event.
5. If the schema is behind, say so and offer to run `migrate`.

Framework files are exactly those the current scaffold already treats as framework-owned (skill files and the entry file). Nothing else is touched.

## 11. CLI

Thin wrappers over `core`. All accept `--workspace PATH` (or `CAREEROS_WORKSPACE`, or discovery by walking up from the current directory to the first `.careeros/workspace.yaml`, or a legacy `profile.md` plus `jobs/`). All accept `--json` where output is data.

| Command | Does |
|---|---|
| `careeros doctor` | environment and workspace health: Python and `careeros` versions, workspace and schema versions, stale framework files, a validation summary, and a warning if the workspace is inside a git repository (it holds personal data; keep any remote private). Read-only |
| `careeros status` | one-screen summary: versions, jobs by state, last five ledger events, whether migrate or upgrade is needed. Read-only |
| `careeros validate [--strict] [--json]` | runs section 9 |
| `careeros ledger append --type … --entity … --actor … --action … [--prev-state … --new-state … --approval … --artifact …]` | appends one event |
| `careeros ledger list [--entity ID] [--since DATE] [--type PREFIX] [--json]` | reads the ledger |
| `careeros ledger verify` | chain and reference checks (LED001–LED004) |
| `careeros transition <job> --to STATE [--actor …] [--force --reason …]` | `<job>` is an ID, a slug or a directory path |
| `careeros approve <job>` | records `application.approved` for a job in `APPROVAL_REQUIRED`. **Requires an interactive terminal** and asks the user to confirm; without one it exits 2 and tells the agent to ask the user to run it themselves (in Claude Code, `! careeros approve …`). Defence in depth only: an agent with unrestricted shell access can still run commands in a terminal it allocates, which section 13 states plainly |
| `careeros migrate [--dry-run] [--yes]` | section 10 |
| `careeros upgrade [--yes]` | section 10 |
| `careeros init` | unchanged, plus writing `workspace.yaml` for new workspaces |

## 12. Out of scope

- Evidence model, Career Memory tree, fabrication validator (sub-project 2).
- Job evaluation, compensation intelligence, full approval system (sub-project 3). Foundation ships only the `APPLIED` guard and the minimal `approve`.
- Rewriting any skill to call these commands. Until sub-projects 3 and 4, skills keep editing bullets and appending to `activity.md`, so `validate` will report `STATE_DRIFT` and `PIPE*` warnings after skills run. That is intended and each warning says how to fix it.
- Reconciling `activity.md` with the ledger. `activity.md` stays a free-text human log; the ledger records events made through the core. Two logs coexist until sub-project 3.
- Company, Person and Application as stored entities.
- GPT Work: the core is runtime-neutral, but ChatGPT Projects cannot run commands, so its workflow does not change.
- Cleaning up `tests/integration/__init__.py` (stale docstring from v0.1; noted in the audit).

## 13. Risks and limits

- **Approval is not a security boundary.** The TTY check stops accidental or lazy agent approvals and makes the required step explicit. A determined agent with shell access could still work around it. The ledger records who ran what, so it is auditable after the fact, not preventable.
- **Frontmatter and bullets can drift** because skills still write bullets. Warnings, not errors, keep the workspace usable meanwhile.
- **Migration touches user files** (additively). The all-or-nothing plan, the confirmation, the backup and the byte-identical-body test are the mitigations.
- **PyYAML is a new dependency.** `safe_load` only; version floor 6.0.
- **Concurrent writers.** The ledger append lock prevents interleaving; two simultaneous `transition` calls on the same job are serialised by that lock but the second may fail validation because the state moved, which is correct.

## 14. Testing

Test-first, per module:

- **ids:** prefix table complete, format, collision retry, `is_valid_id`.
- **versions:** compare, newer-workspace detection.
- **state_machine:** an exhaustive table test over every (from, to) state pair against the rules above; guard tests for `APPLIED`; `--force` cases including forbidden ones; atomicity (ledger failure leaves the file unchanged).
- **ledger:** append, contiguous `seq`, hash chain, tamper detection (edit, delete, reorder), concurrent append, entity reference check.
- **validation:** one fixture per rule code, asserting code, severity and a non-empty fix.
- **migration:** the `legacy_workspace` fixture (built from the real shapes: 6–8 jobs, all legacy statuses, a pipeline, markets, activity.md). Assertions: bodies byte-identical, backup contains the originals, idempotent on a second run, unknown status aborts with nothing written, `activity.md`/`profile.md`/`boards.md`/PDFs untouched, `--dry-run` writes nothing.
- **upgrade:** user data survives, framework files backed up and refreshed, `framework_version` updated.
- **CLI:** each command through Typer's `CliRunner`, including exit codes, `--json`, `--workspace`, and `approve` refusing without a TTY.
- **End to end** (`test_foundation_e2e.py`): scaffold a workspace, add legacy jobs, `migrate`, `validate`, walk one job through the whole state machine to `OFFER` with approval, `ledger verify`, tamper with the ledger, confirm `verify` fails.
- **Compatibility:** the existing 31 tests keep passing unchanged.

CI: `.github/workflows/tests.yml` runs `pytest` on push and pull request (the audit found CI ran no tests). `CHANGELOG.md` is added with a 0.3.0 entry.

## 15. Acceptance

1. The full suite passes locally and in CI.
2. On a **copy** of `~/Projects/job-search`: `migrate --dry-run`, then `migrate --yes`, then `validate` reports no errors; every `job.md` body is byte-identical to before; the backup exists.
3. `transition` rejects an illegal move, rejects `APPLIED` without approval, and records legal moves in the ledger.
4. `ledger verify` fails after a past line is edited.
5. `careeros doctor` and `status` run on both a fresh and a migrated workspace.
6. `docs/END_TO_END_TEST.md` records exactly what was run and its results.
7. `README.md`, `ROADMAP.md` and `CHANGELOG.md` describe only what exists.
