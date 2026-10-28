# Outreach and Follow-up State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status: DRAFT FOR REVIEW. Plan not yet signed off. No implementation has started.** Same format as the earlier plans: tasks give files, contracts, tests first and acceptance, with **no embedded implementation code**. Execution does not begin until the plan is signed off and an execution method is chosen.

**Goal:** Give CareerOS one deterministic answer to "who have I contacted, what happened, who is due a follow-up, how many invites are left this week and whom should I approach next": agent-written person and target-company records, a few reserved `outreach.*` ledger events for outcomes and target decisions, state derived from those events plus the approval events and validated record fields, read-only `careeros outreach` commands, and the weekly invite cap enforced at `approval begin`.

**Architecture:** A new package `careeros/core/outreach/` of small pure layers (rules and slugify, record parsing, person/target fold, queue/next/warm derivations) under a lock-protected service; a thin `careeros outreach` command group; a neutral pure module in the approvals package for the cap and for the cross-package event reads (so approvals never imports outreach); small, explicit changes to the approvals service (`propose`, `begin`); an `OUT` section in `careeros validate`; a new `targets` skill and changes to `outreach`, `follow-up` and `browse`; docs; version 0.8.0.

**Tech Stack:** Python 3.11+, Typer, PyYAML, pytest. **No new dependencies** (the AST dependency test from 0.7.0 guards this).

**Spec:** `docs/superpowers/specs/2026-10-19-outreach-state-design.md` (source of truth). Where this plan and the spec differ, the spec wins; every addition is marked **proposed** in section 8.

**Verification note:** nothing here has been executed. Signatures and rules come from reading the repository at `main` = `cf91e0e` (0.7.0). Tests are the authority: if a value here disagrees with the spec, the spec wins, the discrepancy goes in the task report and the plan is corrected.

**Branch:** `outreach` (the spec is committed there). Do not merge or push without being asked. The user's real workspace (`~/Projects/job-search`) is never touched; acceptance uses an invented workspace. If `gh`/`git push` fails with a bad token, the keyring login works with `env -u GH_TOKEN`.

---

## 1. Scope and non-goals

**In scope:** person and target records (`people/<per_id>.md`, `targets/<slug>.md`); `outreach.*` events; derived person state, target status, follow-up queue, weekly cap, next and warm contacts; `careeros outreach person|target|record|queue|cap|next|warm`; the identity unification with approvals; `person_closed` at `propose` and `begin`; `cap_reached` / `cap_config_invalid` at `begin`; `OUT001`-`OUT007`, `OUT100` in `careeros validate`; the `targets` skill and changes to `outreach`, `follow-up`, `browse`; docs; version 0.8.0; an invented-data end-to-end acceptance.

**Non-goals:** a GPT workspace counterpart; sending or tracking email; interview follow-ups; automatic reply detection; learning from outcomes; a dashboard; importing existing `activity.md` history; re-opening dropped targets. The feature is **not a security boundary** and must never be described as one.

## 2. Existing interfaces this plan reuses (read at `cf91e0e`)

| Area | Interface found | Used for |
|---|---|---|
| IDs | `careeros.core.ids`: `PREFIXES` (has `person` = `per`, `approval` = `apr`; no `target`), `new_id(kind, existing)` | add `"target": "tgt"` |
| Ledger | `ledger.append_events` (atomic batch, restores on failure), `read_events`, `verify_chain`, `is_reserved()` (prefixes `workspace.`, `memory.`, `evaluation.`, `compensation.`, `approval.`), `_NEEDS_REASON` (add `outreach.rejected`, `outreach.target_dropped`, `outreach.closed`) | `outreach.*` events |
| Approvals pure core | `approvals.item.canonical_profile_url`, `approvals.fold` (`fold_events`, `fold_all`, `ItemState` with `kind`, `target_id`, `url`, `state`, `last_ts`; `clock_floor`, `clock_skew_of`, `filter_events`, `parse_ts`, `is_expired`), `approvals.rules` constants, `approvals.model` | person state, cap, identity |
| Approvals service | `service.propose` (identity map `by_url` built from folded proposal events; `taken_people`), `service.begin` (the `_first(...)` check chain under the lock, `_refuse` writes the deduped `approval.rejected`, `_load`, `_chain_ok`, `_skew_message`), `service.validate_workspace`, refusal codes in `rules.REFUSAL_CODES` plus codes defined in `service.py` | the integration changes |
| Strict YAML | `compensation.record.strict_frontmatter` (public wrapper over the strict loader, used by `approvals.item`) | record parsing |
| Workspace | `WorkspaceLock`, `ensure_writable`, `atomic_write_bytes`, `split_frontmatter`/`join_frontmatter`, `job_files(root)`, `read_job(path)` (`Job` has `id`, `status`, `company`), `resolve_job`; `memory.store.Transaction` (file-first/event-second rollback); `core/fold.above_fold` (profile fold marker) | commands, jobs input, cap setting |
| State machine | `job.status_changed` events carry `entity`, `new_state`, `ts` | application follow-up (APPLIED age) |
| CLI | `cli/approval.py` and `cli/comp.py` (group structure, `_guard`, `--json`, exit codes, clock callable), `cli/_util.py`, `cli/main.py` | `cli/outreach.py` |
| Validation | `validation.validate_workspace` calls the compensation and approvals sections | add the `OUT` section |
| Tests | `conftest.py` (`clock`), `helpers.py` (`make_workspace`, `add_job`, `snapshot`, `assert_only_ledger_appended`), `tests/test_approvals_claims.py` (interactive seam, real-process races), `tests/test_approvals_e2e.py` (full-state snapshot, one clock source), `tests/test_cli_dependencies.py` | all test tasks |

**Things that do not exist yet:** `people/`, `targets/`, `tgt` IDs, `outreach.*` events, `slugify`, the cap function, the `OUT` codes, the `careeros outreach` commands, the `targets` skill.

## 3. Task sequence and dependency graph

```
A pure core 1 ──> B pure core 2 ──> C records + commands + identity + OUT validation ──> D outcomes + derived commands + approvals enforcement ──> E skills + docs + 0.8.0 ──> F e2e + acceptance
```

Strictly linear; each task ends green with one commit (plus fix commits) before the next. The 2651 existing tests stay green at every commit.

## 4. Global constraints (verbatim from the spec where it has them)

- No new dependencies; Python `>=3.11`; deterministic apart from the documented clock use; every derivation takes `now`; one clock read per operation, after the lock; the ledger clock floor (refuse `clock_skew` when `now` is earlier than the latest event of any type).
- Person record: `people/<per_id>.md`, frontmatter `schema, id, url, name, headline, company, company_slug, role (recruiter|hiring_manager|peer|other), market (optional), found_in [{kind: job|target, id}], found_at`; `id` and `url` immutable and identity-bearing (canonical URL via `approvals.item.canonical_profile_url`); no state in the record; `company_slug = slugify(company)` (lowercase, hyphenated, ASCII letters and digits).
- Target record: `targets/<slug>.md`, frontmatter `schema, id (tgt_), slug (unique), company, market, fit (0-10), sponsorship (offered|likely|unknown|not_offered|n/a), evidence [{source, read_on}]`; every sponsorship value except `n/a` needs at least one evidence entry with a non-empty `source` and a valid ISO `read_on`.
- Identity map = union of `approval.proposed` url elements and `outreach.person_added` events (ledger-authoritative; files only cross-check). One URL under two ids, or one id under two URLs, makes `person add` and `approval propose` fail closed with `identity_conflict`; never pick a winner.
- Events (reserved prefix `outreach.`): `person_added` (`artifacts=[rel, "sha256:<hex>", "url:<canonical>"]`), `target_added` (`artifacts=[rel, "sha256:<hex>"]`, slug in `action`), `target_approved`, `target_dropped` (needs a reason), `accepted`, `replied`, `closed` (needs a reason from `no_response|declined|not_a_fit|other`; `source` in `user|observed|email`), `rejected` (audit only; needs a reason; deduped; entity = an existing `per_`/`tgt_`; nothing appended for an unknown entity). Each outcome kind at most once per person (a repeat is `wrong_state`). Unknown `outreach.*` types are errors (`OUT005`). Rejections never change any state.
- Person state flags: `invited_at` = earliest `approval.consumed` of a `linkedin_invite` (a second consumed invite is `OUT005`); `messaged_at` = latest consumed `linkedin_message`; `accepted_at`, `replied_at`, `closed_at` from the single outcome events; `invite_in_flight`. Label, first match: `closed`, `replied`, `connected`, `messaged`, `invited`, `discovered`. Transitions: `accepted` needs `invited_at`, no `accepted_at`/`closed_at`; `replied` needs `invited_at` or `messaged_at`, no `closed_at`; `closed` needs no `closed_at`; `closed` is terminal.
- Rules constants (versioned rules module): `INVITE_FOLLOWUP_DAYS = 7`, `APPLICATION_FOLLOWUP_DAYS = 10`, `CAP_WINDOW_DAYS = 7`, `DEFAULT_WEEKLY_CAP = 15`, `TARGET_STALE_DAYS = 60`, `MAX_CONTACTS_PER_TARGET = 2`.
- Queue: invite due when `invited_at + 7 days <= now`, no accepted/replied/closed, no consumed `linkedin_message` after `invited_at` (one follow-up message per invite); pre-opening contacts (found only via targets, no saved job at that company) are never queued; application due when the job's latest status event is `APPLIED`, older than 10 days, with no later status change; closed people and terminal jobs never queued; email and interview follow-ups are explicitly not covered (the output says so).
- Cap: `count` = every invite item currently `in_flight` (any age) + invite items `consumed` whose `approval.consumed` ts is inside the rolling window (`now < ts + 7 days`); never counts an item twice; `approval.started` ts is never used; released/voided/expired/declined/superseded do not count. `cap` from `profile.md` above the fold, under `## Outreach`, exactly one line `Weekly invite cap: N` (leading list marker and bold allowed): absent = 15; present but invalid (not a whole number 0-1000, more than one such line, unparseable) = `cap_config_invalid` (refused in `cap`, `next` and `begin`; `OUT007` error); `0` is valid. `remaining = max(0, cap - count)`; output includes the date the oldest counted consumed invite leaves the window. One pure function serves `outreach cap` and `approval begin`.
- Target decisions: legal `candidate -> approved`, `candidate -> dropped`, `approved -> dropped`; everything else `wrong_state`; `dropped` terminal; a dropped slug cannot be re-added (`duplicate`). Displayed status, first match: `dropped`, `warm` (a linked person is connected/replied), `contacted` (a linked person has `invited_at`), `opening-seen` (a saved job has `slugify(company) == slug`), `approved`, `candidate`. Stale is a flag: `now - last_activity > 60 days` (strict), `last_activity` = latest of the target's `target_added`/`target_approved` ts, the ts of `person_added`/`approval.consumed`/`outreach.accepted`/`outreach.replied` of its linked people, and each evidence `read_on` (00:00 UTC); dropped targets never stale; stale targets stay eligible for `next` and are flagged.
- `next`: `discovered` people whose target is `approved`, or whose `found_in` job is saved and non-terminal; at most one `recruiter` and one `hiring_manager` per target; exclude anyone with a live approval item (`pending`, `approved` unexpired, `in_flight`; invite or message), a consumed invite, `invite_in_flight` or `closed`; limited to `remaining`; targets rank home market by fit descending, non-home by sponsorship tier (`offered`, `likely`, `unknown`; `not_offered` excluded) then fit descending; each result carries its reason. `warm`: `connected`/`replied` people at a company with a saved non-terminal job.
- Source-data honesty (spec 6.0): identity, what was sent, the cap and closed state come from the ledger only; target status, `next`, `warm` and pre-opening exclusion also use validated record fields (`found_in`, `role`, `company_slug`, `market`, `fit`, `sponsorship`, `slug`); a hand edit of those never changes identity, sends, the cap or closed state; `person link` edits only the record file.
- Approvals integration: `approval propose` and `approval begin` refuse an invite or message to a person with `outreach.closed` (`person_closed`); `begin` rechecks it under the workspace lock immediately before recording `approval.started`, for invite and message items; `begin` for an invite refuses `cap_reached` / `cap_config_invalid`; the identity map also reads `outreach.person_added`; approvals import nothing from the outreach package (shared event shapes live in `approvals/people.py`, the cap in `approvals/cap.py`).
- Commands: exit codes 0 ok, 1 refused, 2 usage/no workspace; `--json` is one JSON object once the workspace is found; state-changing commands take the lock, verify the chain (`ledger_corrupt`), read the clock once after the lock, write the file first and the event second in a transaction; refusal codes `not_found`, `wrong_state`, `duplicate`, `invalid_input`, `clock_skew`, `ledger_corrupt`, `identity_conflict`, `cap_config_invalid`, `person_closed`, `cap_reached`.
- **Atomic enforcement (binding).** In `approval begin` for an invite, the `person_closed` recheck, the `cap_config_invalid`/`cap_reached` check and the append of `approval.started` are ONE critical section under the workspace lock: the profile setting, the events and the folded states used for the decision are all read INSIDE the lock, and the claim is appended before the lock is released. A pure cap function is necessary but not sufficient: two processes must never both observe the last free slot. Exactly one of N concurrent `begin` calls proceeds when one slot remains (real-process test, Task D).
- **Record editing (binding).** The spec 6.0 split is tested field by field (Task C): `id` and `url` (person), `id` and `slug` (target) are immutable identity; `company`, `company_slug`, `role`, `found_in` (person) and `market`, `fit`, `sponsorship`, `evidence` (target) are editable and may change target status, `next`, `warm` and the queue exclusions; `name`, `headline`, `found_at`, `market` (person) and a target's `company` display name affect no derived output. No command or `validate`/`list`/`show` ever repairs or rewrites a record; only `person link` rewrites a record (its `found_in` only).
- **Refusal audit (binding).** A refused operation on an existing entity appends at most one deduped, audit-only `outreach.rejected` (or `approval.rejected` for approvals refusals) and changes no derived state: for every refusal path tested, the derived snapshot (person flags, target statuses, queue, cap, next, warm, approval states) is identical before and after, apart from the audit event itself.
- **`OUT007` (exact contract).** Condition: the above-the-fold `## Outreach` section of `profile.md` contains a `Weekly invite cap` setting that does not parse as exactly one line with a whole number from 0 to 1000 (non-numeric, negative, above 1000, fractional, empty value, or more than one such line). Severity: error. Path: `profile.md`. The message quotes the offending line(s) and the fix text says to write one line `Weekly invite cap: N` with a whole number 0-1000. No finding when the setting is absent (heading missing, or heading without the line), when it is valid (including `0` and list-marker/bold variants), or when the text sits below the fold. Positive and negative cases are listed in Task D tests.
- `careeros validate` stays entirely read-only (creates, modifies and removes nothing, `.careeros/` included); a workspace without `people/`, `targets/` or `outreach.*` events is unchanged.
- Surgical changes to existing code; the user's real workspace is never touched; committed acceptance records hold codes and counts only; the GPT workspace, `prep`, `offer`, the evaluate/compensation skills and the Career Memory evidence gate are unchanged.

## 5. Review focus (inputs most likely to bite, each pinned by a task test)

1. An `in_flight` invite older than 7 days, and an `in_flight -> consumed` transition: the cap must never undercount or double count (Tasks B, D).
2. A person closed after proposal or approval but before `begin`, for invites and messages, including a race between `record --closed` and `begin` (Task D).
3. Hand-edited `found_in`/`role`/ids/urls in records and deleted record files: identity, sends, the cap and closed state must not change (Tasks A, C).
4. An invalid or duplicated cap setting must fail closed everywhere the cap is used (Tasks B, D).
5. Identity unification across the approvals and outreach paths with URL variants, and a conflicting identity map failing closed (Task C).

---

## 6. Tasks

### Task A — Pure core 1: rules, slugify, records, person and target fold

**Goal and scope:** every pure contract the rest builds on. No ledger writes, no commands, no I/O beyond what is stated.

**Files (new):** `careeros/core/outreach/__init__.py` (docstring), `rules.py`, `slug.py`, `model.py`, `person.py`, `target.py`, `fold.py`; `careeros/core/approvals/people.py` (pure shared event shapes); `tests/test_outreach_core.py`. **Modify:** `careeros/core/ids.py` (add `"target": "tgt"` and the pinned prefix-count test).

**Dependencies:** none.

**Contracts to define:**
- `rules.py`: the constants of section 4, outcome and decision event type constants, role/sponsorship/closed-reason enums, refusal-code constants, the transition tables as data.
- `slug.py`: `slugify(text) -> str` (NFKC, lowercase, ASCII letters/digits separated by single hyphens, trimmed; empty result is `None` so callers refuse); deterministic and idempotent.
- `model.py`: frozen dataclasses `Person`, `Evidence`, `Target`, `FoundIn`, `PersonFlags`, `TargetView`.
- `person.py` / `target.py`: `parse(text, rel) -> (record | None, list[Issue])` with the strict loader (`compensation.record.strict_frontmatter`): alias ban, duplicate keys, unknown keys, closed enums, canonical `url` required (`approvals.item.canonical_profile_url` must return it unchanged), `company_slug == slugify(company)`, ISO dates, `fit` integer 0-10, sponsorship evidence rule; `to_bytes`; `OUT001` for every shape problem; hostile-input safe.
- `approvals/people.py`: `person_added_fields(rel, sha, url)` (the ONLY writer of the event shape), `person_urls(events) -> dict[url, id]` and `person_ids(events)` (parse the `url:` element; malformed ones become errors), `closed_people(events) -> set[str]` (types `outreach.closed`), `identity_problems(events) -> list[str]` (one URL under two ids, one id under two URLs, across proposal AND `person_added` events). Pure; imports only approvals modules.
- `fold.py`: `fold_people(events, now) -> dict[per_id, PersonFlags]` (flags and label of section 4; filters audit-only `outreach.rejected`; unknown `outreach.*` types and illegal sequences reported through a returned error list for `OUT005`), `apply_outcome(flags, outcome) -> flags | Refusal`, `apply_decision(status, decision) -> status | Refusal`, `fold_targets(events, people, jobs, now) -> dict[slug, TargetView]` (decision state, displayed status by the precedence, `stale` flag and `last_activity`). Inputs are plain data: `JobFacts(id, company, status, status_events)` built by callers.

**Tests first (`test_outreach_core.py`):**
- `slugify` grid (accents, case, punctuation, runs of separators, CJK ⇒ `None`, idempotence, determinism); records: the spec examples parse; every enum/shape/hostile-input case ⇒ `OUT001`; non-canonical url, mismatched `company_slug`, evidence rule for each sponsorship value, duplicate keys, aliases, huge input, deep nesting, lone surrogates; `to_bytes`/`parse` round trip;
- `approvals/people.py`: writer/parser round trip; malformed `url:` element; `identity_problems` for each conflict; `closed_people`; entity-less or malformed events reported, never silently dropped;
- fold: every outcome transition (legal and each refusal) against an independent table-driven simulator over 20,000 random sequences with `outreach.rejected` inserted at random positions (results unchanged); `invited_at` earliest and a second consumed invite reported; label precedence for every flag combination; outcome-once rule;
- target decisions: every pair of the transition table; precedence table for every combination of {dropped, warm, contacted, opening-seen, approved, candidate} conditions; stale: each qualifying event/evidence date, exactly 60 days (not stale) vs 60 days + 1 s (stale), dropped never stale, the creation event alone;
- import-isolation subprocess test: importing `careeros.core.outreach` does not import `validation`, `state_machine`, the CLI or `approvals.service`; importing `approvals.people` does not import `outreach`.

**Acceptance:** all pure; no clock reads inside functions (they take `now`); 2651 + new tests green.

**Commit:** `Add the outreach core: records, rules, person and target fold`.

### Task B — Pure core 2: cap, queue, next, warm

**Goal and scope:** the derivations over the Task A fold. Still pure.

**Files (new):** `careeros/core/approvals/cap.py`, `careeros/core/outreach/derive.py`; `tests/test_outreach_derive.py`.

**Dependencies:** A.

**Contracts to define:**
- `approvals/cap.py` (pure; imports only approvals modules): `parse_cap_setting(profile_text) -> CapSetting(value: int | None, problem: str | None)` (above the fold, the `## Outreach` heading, exactly one `Weekly invite cap: N` line, list marker/bold tolerated; absent ⇒ `value=None, problem=None`; invalid ⇒ `problem` set), `effective_cap(setting) -> int | refusal` (15 when absent), `invite_count(states, now, *, exclude=None) -> CapCount(in_flight: int, consumed_in_window: int, total, oldest_leaves: str | None)` implementing the section 4 rule from the folded approval states (`exclude` omits the item being begun), `remaining(cap, count)`.
- `outreach/derive.py`: `build_queue(people, flags, targets, jobs, now) -> Queue(items, uncovered_note)` (invite and application items with reasons and days overdue), `build_next(people, flags, targets, states, count_remaining, now) -> list[NextContact]` (ranking, eligibility, per-target limit, reasons, stale flag), `build_warm(people, flags, jobs, now) -> list[WarmContact]`. Inputs are plain data; output ordering is deterministic (documented total order).

**Tests first (`test_outreach_derive.py`):**
- cap: absent/present/invalid setting grid (`abc`, `-1`, `1001`, `1.5`, empty, two lines, heading without line, line without heading, below the fold, markup variants), `0` valid; `in_flight` invite older than 7 days still counts; `in_flight -> consumed` counted once, using the `approval.consumed` ts; released/voided/expired/declined/superseded not counted; exactly 7 days (`now == ts + 7d` ⇒ not counted) and 1 s inside; `exclude` semantics; `oldest_leaves` for consumed only; an independent brute-force reference over random approval event sequences;
- queue: exactly 7 and 10 days (`<=` boundary), one-follow-up-per-invite rule, pre-opening contacts excluded until a job exists at the company (via slug), closed people and terminal jobs excluded, application due only with no later status change, the explicit "email and interview not covered" note;
- next: eligibility exclusions one by one (each live approval state vs expired/declined/superseded/voided/consumed), the one-recruiter-one-manager limit, `remaining` limit, home vs non-home ranking, `not_offered` excluded, stale flagged but present, reasons text deterministic;
- warm: connected/replied at a company with a saved non-terminal job; terminal jobs excluded;
- determinism: shuffling input order never changes output.

**Acceptance:** exhaustive small-grid agreement with the independent reference; pure; all green.

**Commit:** `Add the outreach derivations: cap, queue, next and warm`.

### Task C — Records, commands and identity unification

**Goal and scope:** creating and reading records; the `outreach.*` events for records and target decisions; the approvals identity change; `OUT` validation.

**Files (new):** `careeros/core/outreach/service.py`, `careeros/cli/outreach.py`; `tests/test_outreach_records.py`. **Modify:** `careeros/cli/main.py` (register), `careeros/core/ledger.py` (reserve `outreach.`; `_NEEDS_REASON`), `careeros/core/validation.py` (call the outreach validation), `careeros/core/approvals/service.py` (`propose`: identity map also from `people.person_urls`, `identity_conflict` via `people.identity_problems`).

**Dependencies:** A, B.

**Contracts to define:**
- `service.person_add(root, *, url, name, company, role, headline, market, found_in, actor, clock) -> Added`: lock; chain check (`ledger_corrupt`); one clock read after the lock; floor check; canonicalise the URL; build the identity map from events (`identity_conflict` when inconsistent); allocate `ids.new_id("person", …)` or reuse; validate `found_in` targets (jobs via `resolve_job`, targets via records) and the record; file first (`Transaction.write`), then `outreach.person_added` through `people.person_added_fields`; rollback on any exception. Adding a person whose id already has a record is `duplicate`.
- `service.person_link(root, per_id, found_in)`: atomically rewrites ONLY the record file's `found_in` (no event; documented source data), validates references.
- `service.person_list/show`, `target_list/show` (read-only; fold states, never writes).
- `service.target_add(root, *, company, slug, market, fit, sponsorship, evidence, actor, clock)`: slug via `slugify` and uniqueness (`duplicate`, including dropped slugs); evidence rule; file then `outreach.target_added`.
- `service.target_decide(root, slug, decision, *, source, reason, actor, clock)`: `apply_decision` legality; `outreach.target_approved` / `outreach.target_dropped` (dropped needs a reason).
- Refusals of an existing entity append the deduped, audit-only `outreach.rejected` (dedupe: the entity's latest event of any type is an identical rejection); an unknown entity appends nothing. `validate_workspace(root, events, now)`: `OUT001`, `OUT002` (orphan file, warning), `OUT003` (person with approval events but no record, or an event whose record file is missing, warning), `OUT004` (record id/url disagree with its event, or identity inconsistency across events and files, error), `OUT005` (illegal outcome sequence / unknown `outreach.*` type / second consumed invite, error), `OUT006` (`found_in` names a missing job/target, warning), `OUT100` (stale target, info); read-only; a corrupt ledger line never crashes it.
- `approvals/service.propose` change is limited to the identity map and the conflict refusal; approvals tests stay green unchanged.

**Tests first (`test_outreach_records.py`):**
- `person add`/`target add` happy paths: file bytes, event shape, ids, `--json`, exit codes; URL variants (case, trailing slash, query, country subdomain, http) map to ONE id across `person add` and `approval propose` in both orders; reuse of an id proposed earlier; `found_in` validation; `person link` writes only the file and appends no event;
- target decision grid and refusal codes; duplicate slug including a dropped one; evidence rule;
- file-first/event-second: failing `append_events` leaves no file and no event; a crash window leaves an orphan (`OUT002`, never counted in state); a deleted record file keeps identity (events) and gives `OUT003`; hand-edited `id`/`url` ⇒ `OUT004`; hand-edited `found_in`/`role` changes target status and `next` input but not identity, sends, cap or closed state (both halves of spec 6.0);
- identity conflict: hand-crafted events (one URL, two ids) make `person add` and `approval propose` fail closed with `identity_conflict` and write nothing;
- `outreach.rejected` dedupe and audit-only property; reserved prefix refused for `ledger append outreach.x`; `_NEEDS_REASON` enforced;
- **record editing table (table-driven, every field of section 4):** for each person and target field, edit it by hand and assert exactly which outputs change (target status, `next`, `warm`, queue exclusion) and which never do (identity map, sends and approval states, cap count, closed state); editing `id`/`url`/`slug` is `OUT004`/`OUT001` and changes no identity; `validate`, `list`, `show` and every command leave an invalid or hand-edited record byte-identical (no silent repair); `person link` changes only `found_in`;
- **refusal audit:** for every refusal path of `person add`, `person link`, `target add|approve|drop` (duplicate, wrong_state, invalid_input, identity_conflict, clock_skew, ledger_corrupt) the derived snapshot is identical before and after and at most one deduped `outreach.rejected` is appended (none for an unknown entity);
- `validate`: each `OUT` code positive/negative; byte-identical snapshot (ledger and `.careeros/` included) before/after `validate`, `list`, `show`; a workspace without `people/`/`targets/` unchanged; existing approvals tests unchanged.

**Acceptance:** all green; approvals behaviour unchanged except the identity map and `identity_conflict`.

**Commit:** `Add outreach records, commands and identity unification`.

### Task D — Outcomes, derived commands and approvals enforcement

**Goal and scope:** `record`, the read-only derived commands, and the enforcement in approvals.

**Files (new):** `tests/test_outreach_outcomes.py`, `tests/test_outreach_enforcement.py`. **Modify:** `careeros/core/outreach/service.py` (record, queue, cap, next, warm), `careeros/cli/outreach.py`, `careeros/core/approvals/service.py` (`propose`: `person_closed`; `begin`: `person_closed` recheck under the lock immediately before recording `approval.started`, `cap_reached`, `cap_config_invalid`), `careeros/core/approvals/rules.py` (codes), `careeros/core/validation.py` (`OUT007`), approvals docs strings only if a code list is pinned by a test.

**Dependencies:** A, B, C.

**Contracts to define:**
- `service.record(root, per_id, outcome, *, source, reason, note, actor, clock)`: lock, chain check, one clock read, floor; `apply_outcome` legality; `outreach.accepted|replied|closed` (closed needs a reason); repeated outcome ⇒ `wrong_state`; refusals ⇒ deduped `outreach.rejected`.
- `service.queue/cap/next/warm(root, now=None)` read-only: build inputs (records via the strict parsers, jobs via `job_files`/`read_job`, status events from the ledger, approval states via `fold_all`, the cap from `profile.md` via `cap.parse_cap_setting`), call Task B functions; `cap`/`next` refuse `cap_config_invalid`; list/queue output includes the "email and interview follow-ups are not covered" note.
- `approvals/service.begin` additions (within the existing `_first(...)` chain, in this order after the existing checks): `person_closed` (recheck from the freshly read events under the lock, invite and message kinds), then for invites `cap_config_invalid` / `cap_reached` using `cap.invite_count(states, now, exclude=item_id)` and the profile setting. `propose` refuses a closed person. Refusal codes added to `rules.REFUSAL_CODES` with the pin test and the approvals docs updated by Task E.
- `OUT007` in `validate` exactly as in section 4 (error, path `profile.md`, quotes the offending line(s), fix text names the valid form); read-only.

**Tests first:**
- `record`: every outcome transition and refusal (accepted without invite, replied without invite/message, anything after closed, repeats), event shapes, sources, reasons, deduped rejections, `--json`, exit codes, clock floor, ledger failure injection, chain break ⇒ `ledger_corrupt`;
- queue/cap/next/warm commands over invented workspaces: output equals the Task B functions on the same data (end to end), `--json` purity, the 7-day advance via a single clock source, the explicit uncovered note;
- enforcement: `person_closed` at propose; **close after proposal and after approval ⇒ `begin` refuses** for an invite item and for a message item; `cap_reached` at the boundary (cap - 1 ok, cap refused); an `in_flight` invite older than 7 days blocks; `cap_config_invalid` variants block `begin` while other approvals commands keep working; `cap = 0` blocks;
- **real-process races** (barrier, widened window, bounded subprocess timeouts): two invite `begin`s at `cap - 1` ⇒ exactly one proceeds; `record --closed` racing `begin` ⇒ either `begin` wins and the close lands after, or `begin` refuses `person_closed`, never a claim on a closed person after the close event; each new lock is proven necessary by removing it in a scratch copy (bounded);
- **`OUT007` cases:** positive (each reported once, severity error, path `profile.md`, offending line quoted): `Weekly invite cap: abc`, `-1`, `1001`, `1.5`, an empty value, two cap lines; negative (no finding): no `profile.md`, no `## Outreach` heading, heading without the line, `Weekly invite cap: 0`, `15`, `1000`, `- **Weekly invite cap:** 20`, and an invalid line placed below the fold marker; `validate` stays byte-identical and exit codes follow the existing severity rules;
- **atomic enforcement:** the decision inputs (profile setting, events, folded states, closed set) are read inside the lock (a test that changes the profile or appends an event while `begin` waits for the lock proves the decision uses the fresh data), and the real-process race proves exactly one claim at `cap - 1`;
- **refusal audit:** each refusal (`person_closed`, `cap_reached`, `cap_config_invalid`, `wrong_state` outcomes, `ledger_corrupt`, `clock_skew`) leaves the derived snapshot identical and appends at most one deduped audit event;
- existing approvals tests unchanged and green; `validate` byte-identical proof extended.

**Acceptance:** all green; approvals still import nothing from the outreach package (subprocess import test).

**Commit:** `Add outreach outcomes, derived commands and the approvals enforcement`.

### Task E — Skills, docs and 0.8.0

**Goal and scope:** the `targets` skill and the `outreach`/`follow-up`/`browse` changes, docs, version bump.

**Files (new):** `careeros/templates/workspace/.claude/skills/targets/SKILL.md`, `docs/outreach.md`, `tests/test_skills_outreach.py`. **Modify:** `careeros/templates/workspace/.claude/skills/{outreach,follow-up,browse}/SKILL.md`, `careeros/templates/workspace/CLAUDE.md` (routing rows; layout lines `people/`, `targets/`), `mkdocs.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml` → `0.8.0` (then `.venv/bin/pip install -e ".[dev]" -q`), `docs/approvals.md` (the new refusal codes and the cap/closed rules).

**Dependencies:** D.

**Contracts:**
- `targets`: the old spec's steps 0-3 (seeds from the profile "Target companies" line and saved jobs scoring 7 or higher, sponsor registers, regional boards; at most 12 new candidates per market per run; fit and sponsorship from evidence only; one table per market in the ranking order), `outreach target add` with evidence, the user's approve/drop decisions recorded with `outreach target approve|drop --source user`, contact discovery with `research` Mode B1b stored via `outreach person add --found-in target:<slug>`, refresh of stale targets (update evidence `read_on`) before drafting; it never sends anything; unverified is acceptable, a guess is not.
- `outreach`: a pre-opening mode using `outreach next` and `outreach cap` (never draft beyond `remaining`; stop on `cap_config_invalid`), the old spec's note rules (300 characters at most, one clause on who the user is, one concrete real fact about the company or person, never claims an opening, never asks for a job or referral, never claims a relationship, local norms for non-home markets, `humanize` first), then the approvals flow unchanged; on "accepted"/"replied" from the user run `outreach record … --source user`; a refusal such as `person_closed`, `cap_reached` or `cap_config_invalid` ends the action and is relayed to the user.
- `follow-up`: the LinkedIn and application parts of the queue come from `careeros outreach queue`; warm contacts from `outreach warm`; email and interview follow-ups stay prose and the skill says so; `activity.md` is still appended to but no longer parsed for state.
- `browse`: before searching for contacts, `outreach person list` for the company slug; add newly found people with `person add --found-in job:<REF>`; the route question defaults to a warm contact.
- Docs: `docs/outreach.md` (records with invented examples, events, derived state and the 6.0 table, rules table with the constants, commands and exit codes, refusal codes, `OUT` codes with severities, cap enforcement and the fail-closed configuration, limits from spec section 13), README, CHANGELOG `0.8.0` (run `careeros upgrade` to receive the updated skills; the follow-up queue and weekly cap now come from recorded events; existing `activity.md` history is not imported), `mkdocs build --strict` passes.

**Tests first (`test_skills_outreach.py`):** wording pins for the note rules, the approvals flow, "never sends", the prose-only email/interview statement, stop-on-refusal, never run `approve`/`resolve`, no unconditional `careeros transition`; the numbers (7, 10, 15, 60, 12, 300) compared with code constants as sets; every command line in the skills parses with the real CLI; example `person add`/`target add` commands extracted and run on an invented workspace (exit 0); docs `OUT` table and refusal-code list compared with the code; sha pins (prep evidence gate, GPT workspace, evaluate/compensation skills) still pass; every wording pin mutation-checked by the implementer with short bounded runs.

**Acceptance:** all green; `mkdocs build --strict` exits 0 (own exit status); version 0.8.0 consistent.

**Commit:** `Add the outreach skills, docs and the 0.8.0 version bump`.

### Task F — End-to-end test and acceptance

**Files (new):** `tests/test_outreach_e2e.py`. **Modify:** `docs/END_TO_END_TEST.md` (a "## Outreach and follow-up state (0.8.0)" section).

**Dependencies:** E.

**Steps (invented data, real CLI, ONE clock source):** (a) add a target and two people (URL variants resolve to one id), approve the target; `next` ranks and limits to the remaining cap; (b) propose, approve, begin and done an invite (cap count 1); a second `begin` at the cap is refused `cap_reached`; (c) advance 7 days: the invite appears in `queue`; a leftover `in_flight` invite older than 7 days still counts; (d) `record --accepted` removes it from the queue; saving a job at the company makes `warm` list the person; (e) `record --closed` after an approval makes `begin` refuse `person_closed` and `propose` refuse it too; (f) an invalid cap setting fails closed everywhere; (g) a hand-edited `found_in` changes `next` but not identity, cap or closed state; (h) `validate`, `queue`, `cap`, `next`, `warm`, `list` and `show` leave the whole workspace byte-identical (snapshot including `ledger.jsonl` and `.careeros/`); (i) event counts per type, rejection dedupe, ledger chain verifies; (j) isolation: `career/`, other jobs, jd.md untouched; (k) one real-process race for the cap.

**Release checks to run and report truthfully, on the FINAL COMMITTED TREE (not the working directory):** commit first, record the commit SHA, then create a clean checkout of exactly that commit in a temporary directory (`git worktree add` or `git archive`) and run every check there: refresh both venvs from that checkout; full suite on Python 3.14 and on a FRESH Python 3.11 venv built from `/Users/kaushal/.local/bin/python3.11` with `pip install -e ".[dev]"` and **without click** (confirm `pip show click` fails; run from the repo); `.venv/bin/python -m mkdocs build --strict` (own exit status); wheel contains `careeros/core/outreach/*.py`, `approvals/people.py`, `approvals/cap.py`, `cli/outreach.py` and the skills, version 0.8.0; `git diff --check` (staged and against the merge base); `git status` clean; the report names the tested SHA. The acceptance record holds commands, exit codes, state names, codes and counts only (no real people, employers or profile URLs; invented placeholders), says what was not covered (skills not run live, no real LinkedIn, agent behaviour, a real pty approval) and never calls the feature a security boundary.

**Acceptance:** all recorded; any defect found stops the task with an exact reproduction; no earlier source is patched here.

**Commit:** `Add the outreach end-to-end test and the 0.8.0 acceptance record`.

---

## 7. Process and review

Subagent-driven if chosen, as for evaluation, compensation and approvals: one implementer per task, an adversarial task reviewer (independent simulators, mutation tables, real-process races where relevant), scoped re-reviews of fix rounds, a ledger of rulings, one whole-branch review on the most capable model. Guardrails: A and B stay pure and cycle-free (the import-isolation tests); C and D hold the lock for every state change and read the clock once after it; approvals never import outreach; D proves each new lock necessary by removal; E never lets a skill run `approve`/`resolve`; F uses invented data in a disposable workspace. **Commit gates (binding):** each task ends with its planned tests green, its adversarial review and any scoped re-reviews complete, and its ledger entry written BEFORE the next task is dispatched; a task with an open Critical or Important finding is not complete, and no later task starts on it. **Executor hygiene (learned in 0.7.0):** every command bounded (Python subprocess timeouts; full suite ~60 s with a 280 s limit), never `pkill`/`killall`/`pgrep -f`, no background loops, short mutation proofs (one scratch copy, one targeted test file, 120 s), and the fresh-venv 3.11 run without click for every release.

## 8. Proposed additions beyond the spec (marked for approval)

1. **Neutral modules in the approvals package:** `approvals/people.py` (shared `outreach.person_added` event shape, closed set, identity problems) and `approvals/cap.py` (the pure cap) so approvals never import outreach while sharing one definition with `careeros outreach`.
2. **Strict YAML loader reuse** through `compensation.record.strict_frontmatter`, as approvals did.
3. **`JobFacts` input struct** (`id, company, status, status_events`) built by callers so the derivations stay pure.
4. **`slugify` returns `None` for an empty result** (e.g. CJK-only names); callers refuse with `invalid_input` and the skill asks for an ASCII slug.
5. **Order of the new `begin` checks:** after the existing chain, `person_closed`, then the cap checks.
6. **Refusal codes** `person_closed`, `cap_reached`, `cap_config_invalid`, `identity_conflict` are added to the approvals refusal-code list and docs.

## 9. Self-review (spec coverage)

Spec §4 (records) → A, C; §5 (events) → A, C, D; §6.0 (derivation inputs) → A, B, C tests; §6.1-6.2 (rules, person state) → A; §6.3 (queue) → B, D; §6.4 (cap) → B, D; §6.5 (targets) → A, C; §6.6 (next, warm) → B, D; §7 (commands) → C, D; §8 (approvals integration) → C (identity), D (closed, cap); §9 (validation) → C (OUT001-006, 100), D (OUT007); §10 (skills) → E; §11 (docs/release) → E, F; §12 (tests) → distributed per task; §13 (limits) → E docs; §14 (build order) → section 3; §15 (acceptance) → F. No placeholders; names used later are defined where introduced (`slugify`, `Person`, `Target`, `PersonFlags`, `TargetView`, `JobFacts`, `fold_people`, `fold_targets`, `apply_outcome`, `apply_decision`, `parse_cap_setting`, `effective_cap`, `invite_count`, `build_queue`, `build_next`, `build_warm`, `person_added_fields`, `person_urls`, `closed_people`, `identity_problems`).
