# Changelog

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
