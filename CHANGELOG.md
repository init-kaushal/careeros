# Changelog

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
