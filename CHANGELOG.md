# Changelog

## 0.8.0 — Outreach and follow-up state

### Added

- `careeros outreach person add | link | list | show` and `careeros outreach target add | approve | drop | list | show`: person records (`people/<per_id>.md`) and target-company records (`targets/<slug>.md`), each with a stable id. A person gets one `per_` id whether they were added here or proposed for an invite, whichever came first.
- `careeros outreach record <per_id> --accepted | --replied | --closed`: the outcomes you tell the agent about, recorded as `outreach.*` ledger events (`outreach.accepted`, `outreach.replied`, `outreach.closed`, plus `outreach.person_added`, `outreach.target_added`, `outreach.target_approved`, `outreach.target_dropped` and the audit-only `outreach.rejected`). The type prefix `outreach.*` is reserved and `tgt_` ids are accepted in the ledger.
- `careeros outreach queue | cap | next | warm`: read-only views derived from those events and the approval events. Exit 0 ok, 1 refused, 2 usage or no workspace.
- `careeros validate` reports `OUT001`-`OUT007` and `OUT100`.
- The `targets` skill (build, rank and refresh target companies, record your approve and drop decisions, store contacts) and a pre-opening mode (Mode C) in the `outreach` skill.
- `docs/outreach.md`.

### Changed

- The follow-up queue and the weekly invite cap now come from recorded events, not from reading `activity.md`. `activity.md` is still appended to as a readable log, but invites and applications logged only in `activity.md` are not imported; invites recorded with `careeros approval` (0.7.0) do count against the cap and appear in the queue.
- `careeros approval begin` refuses an invite beyond the weekly cap (`cap_reached`; 15 unless `profile.md` sets `Weekly invite cap: N` under `## Outreach`) and any invite or message to a closed person (`person_closed`); an invalid cap setting is refused, not guessed (`cap_config_invalid`). `careeros approval propose` refuses a closed person and an inconsistent identity map (`identity_conflict`). The cap and the closed check are made inside the same lock as the claim.
- The `follow-up` skill reads `careeros outreach queue` and `careeros outreach warm`; email and interview follow-ups stay prose. The `browse` skill checks the people already recorded for a company, records the people it finds, and defaults the route question to a warm contact.
- Upgrading: run `careeros upgrade` to receive the updated skills. This is not a security boundary; see `docs/outreach.md` for the limits.
- The skill-only design `docs/superpowers/specs/2026-10-05-target-company-outreach-design.md`, which was never implemented, is superseded by this release.

## 0.7.0 — Approvals

### Added

- `careeros approval propose | list | show | begin | done | resolve`: LinkedIn invitations, LinkedIn messages and the application Submit are recorded as exact, content-bound, single-use items (`approvals/<apr_id>.md` plus `approval.*` ledger events). Items expire 24 hours after approval; an interrupted action stays `in_flight` until you `resolve` it. Exit 0 ok, 1 refused, 2 usage or non-interactive.
- `careeros approve` now also decides one item (`careeros approve <apr_id>`) or the whole queue of pending items (no argument). `careeros approve <job>` works as before.
- `careeros validate` reports `APR001`-`APR008` and `APR100`; the type prefix `approval.*` is reserved and `apr_` ids are accepted in the ledger.
- `docs/approvals.md`.

### Changed

- A job that has an `application_submit` item reaches `APPLIED` only when no submit item is unresolved and at least one is consumed (`submission_unresolved`, `submission_not_consumed`). A job with no such item behaves as before.
- Upgrading: run `careeros upgrade` to receive the updated skills. Skills: outreach, follow-up and apply now use recorded approvals: they propose the exact text, wait for your `careeros approve`, then `begin`, send and `done`. This is not a security boundary; see `docs/approvals.md` for the limits.

## 0.6.0 — Structured compensation

### Added

- `compensation.md` per saved job: dated pay observations (`posted`, `recruiter`, `estimate`) with range, base or total, currency, period, source, `as_of`, confidence and a verbatim quote for `posted`, plus recorded currency conversions. `careeros comp <job>` validates it and shows the derivation (`--init` writes the skeleton, `--json` is machine-readable). Exit 0 valid, 1 invalid record, ledger failure or `--init` on an existing file, 2 usage. New ledger events `compensation.recorded` and `compensation.rejected`; the type prefix `compensation.*` is reserved.
- `careeros validate` reports `CMP001`-`CMP007` (errors) and `CMP100` (warning, an observation older than 90 days) for every `compensation.md`.
- The `browse` skill records pay in `compensation.md` when it saves a job; the `evaluate` skill reads the derivation and migrates a 0.5.0 evaluation in four resumable steps.
- `docs/compensation.md`.

### Changed

- Evaluation basis and rules are now version 2: pay comes only from `compensation.md`, the floor from the verified `## Markets` row by code, and a sixth freshness stamp (`compensation`) covers the record. `job_facts.compensation` is removed from the agent half (`EVL003`), and `job_facts.market` must equal the record's `market` (`EVL006`).
- Upgrading: run `careeros upgrade` to receive the updated skills. Existing evaluations read as stale once (`EVL008`, never tampered) and need an explicit re-evaluation; a 0.5.0 record that still has the pay block is refused by `careeros evaluate` until the `evaluate` skill has migrated it.

### Known limits

- Observations and rates are supplied by the agent, so a mistaken source is not detectable; a verbatim quote proves the text is in `jd.md`, not that it is pay. A rate dated after the observation reads as not comparable. The cap needs a verified `## Markets` floor. Out of scope: live exchange rates, market benchmarks, offer-stage pay, tax modelling and the GPT workspace.

## 0.5.0 — Job evaluation

### Added
- `careeros evaluate <job>`: scores a saved job against your career memory from the agent's recorded judgements. `--init` creates `jobs/discovered/[job-slug]/evaluation.md`; `--json` prints machine-readable output. Exit 0 scored or unchanged, 1 the record or posting cannot be scored (evaluation.md is unchanged; an `evaluation.rejected` event is logged for invalid inputs), 2 usage or a career memory with errors.
- The result: a tier (`pursue`, `stretch`, `weak`, `skip`, `unrated`), coverage, fit and combined scores, confidence, sponsorship, pay and unmet-`must` caps, and action items. Outcomes (`covered`, `adjacent`, `gap`, `unverifiable`) are derived from your active facts by code, never by the agent; every judgement carries a verbatim quote from `jd.md`.
- Freshness stamps and integrity checks: `validate` reports `EVL001`-`EVL011` (stale `EVL008`, tampered `EVL007`, file and ledger disagreement `EVL009`). New ledger events `evaluation.completed`, `evaluation.rejected` and `evaluation.result_replaced`; the type prefix `evaluation.*` is reserved.
- An `evaluate` skill for Claude Code, a fresh-evaluation check in `prep` (its EVIDENCE GATE is unchanged), a closing hint in `browse` and a routing row in `CLAUDE.md`. GPT Work is unchanged.
- `docs/evaluation.md`.

### Notes
- Run `careeros upgrade` in existing workspaces to receive the `evaluate` skill.
- Evaluations need a `jd.md`; the `evaluate` skill saves the posting there.
- The file is written before its ledger event. A crash between the two is detected by `EVL009` and repaired by running `careeros evaluate` again; the window is accepted, not eliminated.
- Known limits: requirement extraction is the agent's judgement, and the memory stamp covers the whole memory, so any fact change makes every evaluation stale (see `docs/evaluation.md`).

## 0.4.0 — Career memory and the evidence check

### Added
- `careeros memory` (`import`, `add`, `update`, `confirm`, `verify`, `dispute`, `retire`, `list`, `show`, `status`) and a `career/` folder of provenance-tracked facts. Facts have a status (`claimed`, `confirmed`, `verified`, `disputed`, `retired`) that is separate from their source, and imports only ever create `claimed` facts.
- `careeros memory import`: a deterministic parse of `resume.md` with a reviewable diff. It never overwrites a confirmed fact, never touches facts you added or edited, marks facts whose line left the resume as stale instead of deleting them, backs up every file it changes, and rolls back if the ledger append fails.
- `careeros check`: compares the numbers, years, durations, technologies, employers, schools, titles and certifications in a draft with your facts, requires the claims in one sentence to come from a single fact, and reports what it did not evaluate. Exit 0 means every detected claim is supported, not that the draft is true.
- A mandatory EVIDENCE GATE in the `prep`, `apply`, `outreach`, `follow-up`, `humanize` and `interview` skills, and a `memory` skill. GPT Work is unchanged.
- `validate` reports `MEM001`-`MEM008` for the memory and `LED003` for memory ids the ledger mentions but no file has. The ledger type prefix `memory.*` is now reserved. `check --record` writes `draft.checked`, which is not reserved.

### Changed
- The verified-backup helpers moved to `careeros/core/backup.py`; migration behaviour is unchanged.

### Notes
- Run `careeros upgrade` in existing workspaces to receive the new skills, then `careeros memory import` to build your career memory (until then every gated draft fails with CHK030).
- Known limits: the checker cannot see claims outside its detection rules (see `docs/career-memory.md`), a person's name raises `CHK009` unless allowed or added as an identity, and the importer understands the `resume.md` layout documented there.

## 0.3.0 — Foundation

### Added
- `careeros doctor`, `status`, `validate`, `ledger` (`append`, `list`, `verify`), `transition`, `approve`, `archive`, `migrate` and `upgrade`.
- A tested core library (`careeros/core/`): stable entity IDs, workspace metadata (`.careeros/workspace.yaml`) with schema and framework versions, an application state machine, an append-only hash-chained ledger (`ledger.jsonl`), a deterministic validator and verified backups with manifests.
- Atomic, serialised writes: one workspace lock, temp file + `fsync` + atomic rename, and rollback when a ledger append fails.
- `migrate` adds frontmatter and IDs to existing job files without changing their bodies, backs up first, and can be re-run safely.
- A test workflow in CI (it previously only built the docs).

### Changed
- `careeros.__version__` is read from package metadata. It said `0.1.0` while `pyproject.toml` said `0.2.0`.
- `careeros init` writes workspace metadata and a `workspace.created` ledger event for new workspaces; `init --refresh` records the framework version it applied.
- New dependency: PyYAML (`safe_load` only).

### Fixed (after review)
- `transition` refuses to move on from a job file whose status disagrees with the ledger (`history_mismatch`) instead of recording a move that skips the difference; the existing repair/correction path reconciles it first, with its legality, terminal and approval rules intact.
- `migrate` now updates existing metadata whose schema is older than the current one (it previously only created missing metadata), keeps job frontmatter and metadata on the same schema, no longer treats a metadata-only migration as a no-op, and restores the old metadata on rollback.

### Notes
- Skills are unchanged and still edit `Status:` lines and append to `activity.md`. `careeros validate` reports the resulting drift as warnings and says how to fix each; `careeros migrate` adopts jobs saved since the last migration.
- `activity.md` stays the human-readable log. `ledger.jsonl` is the machine and audit log.
- `careeros approve` is a human-confirmation guard, not a security boundary.
- Known limitations: a lock timeout exits 1; a process killed during `archive` can leave an unrecorded archive; `migrate` records the installed framework version without refreshing skill files, so run `careeros upgrade` after migrating.

## 0.2.0

- Replaced the v0.1 Playwright CLI with the Cowork workspace scaffold (`careeros init`) and markdown skills for Claude Code and GPT Work.

## 0.1.0

- Initial CLI with browser automation, apply, outreach and research commands.
