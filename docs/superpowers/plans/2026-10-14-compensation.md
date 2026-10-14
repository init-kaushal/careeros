# Structured Compensation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status: DRAFT FOR REVIEW. Plan not yet signed off. No implementation has started.** Same format as the evaluation plan: tasks give files, contracts, tests first and acceptance, with **no embedded implementation code**. Execution does not begin until the plan is signed off and an execution method is chosen.

**Goal:** Add one agent-owned compensation record per saved job (`jobs/discovered/<slug>/compensation.md`) of dated, kinded pay observations and recorded currency conversions; derive the effective range and its comparison with the verified `## Markets` floor by a pure function; make `careeros evaluate` read that derivation (basis/rules version 2) instead of a re-typed `job_facts.compensation`.

**Architecture:** A new package `careeros/core/compensation/` (record parsing and validation, pure derivation, a thin service) that depends only on the leaf modules `evaluation.canon` and `evaluation.markets`. `careeros comp` and a `CMP` section in `careeros validate` sit on top. The evaluation package gains basis/rules v2 (cap inputs hold the derivation), a `compensation` freshness stamp and an agent half without `job_facts.compensation`; version 1 records stay verifiable. Two skills change (`browse`, `evaluate`); docs; version 0.6.0.

**Tech Stack:** Python 3.11+, Typer, PyYAML, pytest, `fractions.Fraction`. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-10-14-compensation-design.md` (source of truth). Where this plan and the spec differ, the spec wins; every addition is marked **proposed** in section 8.

**Verification note:** nothing here has been executed. Signatures and rules come from reading the repository at `main` = `a457885` (0.5.0). Tests are the authority: if a value here disagrees with the spec, the spec wins, the discrepancy goes in the task report and the plan is corrected.

**Branch:** `compensation` (the spec is committed there). Do not merge or push without being asked. The user's real workspace (`~/Projects/job-search`) is never touched; acceptance uses an invented workspace.

---

## 1. Scope and non-goals

**In scope:** the record and its validation (`CMP001`-`CMP007`, `CMP100`); the pure derivation; `careeros comp [--init] [--json]`; `compensation.recorded` / `compensation.rejected` ledger events; evaluation basis/rules v2, the `compensation` stamp, removal of `job_facts.compensation` from new records, v1 compatibility; `browse` and `evaluate` skill changes including the migration step; docs; version 0.6.0; an invented-data end-to-end acceptance.

**Non-goals (separate specs):** live FX lookup, market floors/benchmarks, offer-stage compensation, tax/net pay, approvals, bulk recompute, GPT workspace parity, changing any non-compensation scoring constant, changing `prep`/`offer`/the evidence gate.

## 2. Existing interfaces this plan reuses (read at `a457885`)

| Area | Interface found | Used for |
|---|---|---|
| Canonical hashing | `evaluation.canon`: `canonical_json`, `sha256_hex`, `normalize_text`, `fixed4`, `exact` / `parse_exact` (Fraction ⇄ string), `result_sha256` | record canonical form, stamp, basis strings, verbatim-quote check |
| Markets | `evaluation.markets`: `parse(profile_text) -> Markets(rows, problems, ambiguous, unusable)`, `MarketRow(label, floor_cell, sponsorship_cell, floor, need)`, `Floor(amount, currency, basis)`, `norm_label`, `MarketError`, `FieldCheck`, `MarketCheck`, `verify_market`, `norm_currency`, `norm_basis` | the floor for a market label |
| Evaluation model | `evaluation.model`: `MarketBasis` (`base`/`total`), `SourceConfidence`, `Stamps(jd, memory, rubric, rules, inputs)`, `JobFacts` (still has `range_*`, `floor_*`, `source`, `comp_confidence`, `as_of`), `Cap`, `ActionItem`, `Tier`, `Confidence` | v2 changes |
| Rules registries | `evaluation.rules`: `RULES` / `BASIS_VERSIONS`, `RULES_VERSION = BASIS_VERSION = "1"`, `rules_for`, `basis_schema_for`, `UnsupportedVersion` (a `LookupError`), `Rules`, `RulesV1`, `BasisSchema(version, keys)` | add version "2"; keep "1" byte-for-byte |
| Scorer | `evaluation.score.score(basis)` dispatches on `basis_version`/`rules_version`; `_validate` hard-codes the v1 `cap_inputs` keys; `_compensation_cap`, `_confidence` (range-null unknown), `_action_items` (pay items), constants `REASON_*`/`ITEM_*` | v2 cap/confidence/items; v1 path unchanged |
| Basis builder | `evaluation.derive`: `Basis.complete(fit, facts, check, rubric)`, `_cap_inputs`, `_row_state`; `record.derive_basis`, `record.compute`, `record.assemble`, `record.stamps`, `STAMP_KEYS`, `Inputs`, `read_inputs`, `for_job`, `parse_agent`, `_job_facts`, `validate_agent` | wiring |
| Integrity | `evaluation.integrity.check(rec, inputs)`, `_freshness`, `_derivation`, `_self_consistency`, `market_issues`, `validate_workspace` | v1/v2 behaviour |
| Service | `evaluation.service.evaluate/init/_rejected/_all_records`: lock, validate, compute, `ledgercheck.verify`, `Transaction`, `ledger.append_events` | evaluate wiring |
| Ledger | `ledger.append_events`, `append_event`, `read_events`, `is_reserved`, `_NEEDS_REASON`; evaluation events use `artifacts: [rel, "sha256:<hex>"]`, `reason`, `entity` | `compensation.*` events follow the same convention |
| Workspace | `WorkspaceLock`, `ensure_writable`, `atomic_write_bytes`, `split_frontmatter`, `join_frontmatter`, `resolve_job`, `job_files`, `Transaction` | `comp` command |
| CLI | `cli/main.py` registers commands; `_util.resolve_root`, `fail`, `echo_json`, `default_actor`, `EXIT_*`; `cli/evaluate.py` is the command template | `careeros comp` |
| Validation | `validation.validate_workspace` already calls `integrity.validate_workspace`; `LED003` entity loop | add the `CMP` section |
| Tests | `conftest.py` (`clock`, session `career` fixture), `helpers.py` (`make_workspace`, `add_job`, `make_career`, `snapshot`), the evaluation fixtures in `tests/test_evaluation_command.py` | all test tasks; import-order subprocess pattern from the `fold` tests |

**Things that do not exist yet:** any `compensation.md`; the `CMP` codes; a clock-free date-window helper; basis/rules v2; the `compensation` stamp; a golden 0.5.0 evaluation fixture.

## 3. Task sequence and dependency graph

```
A record + validation ──> B pure derivation ──> C comp command + ledger + validate
                                                      │
D golden v1 fixture + rules/basis v2 + scorer ──> E evaluation wiring ──> F skills + docs + 0.6.0 ──> G e2e + acceptance
```

Strictly linear in the order A, B, C, D, E, F, G; each task ends green with one commit (plus fix commits) before the next starts. The 1334 existing tests stay green at every commit.

## 4. Global constraints (verbatim from the spec where it has them)

- No new dependencies; Python `>=3.11`; deterministic on 3.11 through 3.14; exact fractions, no floats in hashed data; no network, no clock, no randomness in the derivation.
- Record: amounts whole integers; `low >= 0`; `high >= low` when both present; a missing bound is `null`, never zero, `0` is valid; ids local-unique non-empty strings; ISO dates; closed enums; unknown keys are errors; `posted` needs a verbatim `quote` in `jd.md`; every observation needs `source` and `as_of`; `confidence` is the agent's view of the source, not proof.
- Normalisation: month ×12 first, then convert with the exact `(from, to)` conversion (no inverse, no chaining, no live lookup); keep the original amount, currency, period and source beside the converted figure; never compare base with total.
- Rate window: `0 <= obs.as_of - rate.as_of <= 30` days passes; a rate dated after the observation, or older than 30 days, makes it `FX_RATE_OUTSIDE_WINDOW`.
- Reasons: `FLOOR_UNVERIFIED`, `BASIS_MISMATCH`, `FX_CONVERSION_MISSING`, `FX_RATE_OUTSIDE_WINDOW`; a not-comparable observation never contributes to a comparison or a cap; a missing conversion keeps the record valid; the `market` label resolves in one fixed order (spec §5.1): `null` ⇒ floor unverified; ambiguous or unknown label ⇒ `CMP007` error; unusable table or a row with no machine-readable floor ⇒ `FLOOR_UNVERIFIED` (valid record); two conversions for one `(from, to)` pair are `CMP005`.
- Effective kind = highest of `posted > recruiter > estimate` present among all observations; none comparable ⇒ no effective range; effective range = union of the comparable observations of that kind; they must share a **common intersection** (closed intervals, touching counts, `null` is unbounded) or the kind is in **conflict**.
- Compensation cap (`weak`) only when: effective kind is `posted` or `recruiter`; every observation of that kind is comparable; no conflict; every one of them is `medium`/`high`; effective `high` not null and **strictly** below the floor amount; floor verified. Estimate-only never caps. No effective range with a floor present keeps today's unknown-compensation confidence deduction.
- `comp` never edits `compensation.md` and never repairs YAML; its only writes are `compensation.recorded` (dedupe against the latest **valid** event for that job; a missing/malformed prior means no baseline) and `compensation.rejected` (dedupe on raw-bytes hash plus sorted CMP code set); a failed append is reported and never claimed. `validate` stays read-only.
- Write scope: `validate` leaves every workspace file, ledger included, byte-identical. `comp` leaves `compensation.md` and every file except `ledger.jsonl` byte-identical; `ledger.jsonl` only gains the documented events (old bytes an exact prefix; appended events exactly the expected types and count, none on a duplicate or a failed append).
- Scores depend only on persisted data: staleness against today is a `CMP100` warning, never a score change.
- Version 1 evaluations stay verifiable (never `EVL007`, never `EVL010`) and report stale; re-evaluation is explicit; the scorer never writes files; the migration is a resumable four-step skill sequence (write, validate, remove, re-evaluate) that re-detects state on every run, never overwrites an existing `compensation.md`, and never removes the legacy block before a valid `compensation.md` exists.
- Surgical changes to existing code; the user's real workspace is never touched; committed acceptance records hold counts and codes only; the GPT workspace and the Career Memory evidence gate are unchanged.

## 5. Review focus (inputs most likely to bite, each pinned by a task test)

1. A 0.5.0 evaluation (golden fixture produced by the unmodified code) must still `validate` with no `EVL007` and report stale after the upgrade; `evaluate` on it refuses to run until the legacy `job_facts.compensation` block is migrated (Tasks D, E).
2. YAML hazards in `compensation.md`: unquoted float rates, dates parsed as `datetime.date`, boolean/float amounts, duplicate observation ids, tabs, a non-mapping file, a huge or deeply nested file must give stable `CMP` codes and never crash (Task A).
3. Foreign-currency posting with no conversion, or a conversion dated after the observation: valid record, not comparable, no cap, an action item (Tasks B, D).
4. Conflicting or open-ended ranges (`30–40`/`35–45`/`42–50`; `null` bounds) never crash and never mis-cap (Task B).
5. A ledger failure during `comp`, and re-running `comp` on an unchanged or still-invalid file, must neither edit the file nor duplicate or falsely claim events (Task C).

---

## 6. Tasks

### Task A — Record parsing and validation

**Goal and scope:** the data shapes and deterministic parse/validate of `compensation.md`. No derivation, no I/O beyond reading a path.

**Files (new):** `careeros/core/compensation/__init__.py` (empty docstring), `model.py`, `record.py`, `rules.py`; `tests/test_compensation_record.py`.

**Reuse:** `evaluation.canon`, `workspace.split_frontmatter` / `join_frontmatter`, `evaluation.model.MarketBasis`. Import only leaf modules: a subprocess test asserts that importing `careeros.core.compensation` does not import `evaluation.record`, `evaluation.integrity`, `evaluation.service` or `validation` (no cycle once evaluation imports compensation in Task E).

**Dependencies:** none.

**Contracts to define:**
- `rules.py`: constants `COMPENSATION_FILE = "compensation.md"`, `SCHEMA = 1`, `KINDS = ("posted", "recruiter", "estimate")` (highest first), `FX_WINDOW_DAYS = 30`, `OBSERVATION_STALE_DAYS = 90`.
- `model.py`: frozen dataclasses `Observation(id, kind, low, high, currency, basis, period, source, quote, as_of, confidence)`, `Conversion(from_currency, to_currency, rate, source, as_of)` (`rate` an exact `Fraction`), `CompRecord(market, observations, conversions, notes)`; `Issue`-compatible findings reuse `validation.Issue(severity, code, path, message, fix)`.
- `record.py`: `load(path, root) -> Loaded` (raw bytes, parsed frontmatter, `rel`; raises a `RecordError` with `.code` on unreadable/invalid frontmatter); `parse(loaded) -> tuple[CompRecord | None, list[Issue]]`; `validate(loaded, jd_text: str | None) -> list[Issue]` (adds `CMP004` using `canon.normalize_text` for the verbatim check); `canonical(record) -> dict` (observations sorted by id, conversions sorted by `(from, to)`, dates ISO strings, amounts ints, rates exact strings, `market` omitted when there are no observations); `stamp(record_or_none, raw_text_or_none) -> str` (absent file, skeleton and `observations: []` all hash to the same value; an unparseable file hashes its normalised raw text under an `invalid:` prefix so editing it still changes the stamp); `skeleton(now) -> bytes` (`market: null`, empty lists, a one-line body); `NO_FILE`/empty constants as needed.
- Code table implemented here: `CMP001` (structure), `CMP002` (types/enums/dates/empty text, `market` null with non-empty observations), `CMP003` (range invariants), `CMP004` (quote), `CMP005` (duplicate id / conversion pair), `CMP006` (missing `source`/`as_of`/`rate`, rate not a positive decimal string). `CMP007` and `CMP100` belong to Task C because they need the Markets table and today's date.
- Dates may arrive as YAML dates or strings; both normalise to the ISO string; a datetime with a time, a non-ISO string or anything else is `CMP002`.

**Tests first (`test_compensation_record.py`):**
- the spec §4 example parses; `observations: []` parses; absent, skeleton and empty record share one stamp; changing any observation field changes the stamp; key order and `market` text do not change it when there are no observations;
- range grid: `low=0` ok, `low=-1`, `high<low`, `low=null`, `high=null`, both null (valid, undisclosed), bool, float, string amount (`CMP003`);
- evidence by kind: `posted` without quote, with a quote absent from `jd.md`, with `jd.md` missing (`CMP004`); a reflowed/NFC-different quote still found; `recruiter`/`estimate` without a quote ok; missing `source` or `as_of` (`CMP006`); `confidence` outside the enum (`CMP002`);
- conversions: duplicate `(from, to)` even with equal rates (`CMP005`); unquoted float rate (`CMP006`, fix text says to quote the rate); zero, negative and non-numeric rate; inverse pair present (both legal, distinct pairs);
- ids: duplicate (`CMP005`), empty (`CMP002`), any non-empty string legal;
- YAML hazards: unknown top-level and nested keys, duplicate YAML keys (loaded with a loader that raises on duplicates, `CMP001`), tabs, non-mapping root, empty file, no frontmatter, unsupported `schema`, a 1 MB file and deep nesting (never a traceback);
- import-order subprocess test; every function pure and importable with no workspace.

**Acceptance:** all A tests green; no function touches the clock or the filesystem except `load`; 1334 + new tests green.

**Commit:** `Add the compensation record parser and validator`.

### Task B — Pure derivation

**Goal and scope:** `derive_compensation` and its JSON-safe basis form. Pure: no I/O, no clock, no markets parsing.

**Files (new):** `careeros/core/compensation/derive.py`; `tests/test_compensation_derive.py`.

**Dependencies:** A.

**Contracts to define:**
- `FloorInfo(amount: int | None, currency: str | None, basis: str | None, verified: bool)` (the caller builds it from the Markets row; `verified` false when `market` is null, the row has no machine-readable floor, or the table is unusable; an unknown or ambiguous label never reaches this function because Task C rejects it as `CMP007`).
- `derive_compensation(record: CompRecord, floor: FloorInfo, *, fx_window_days: int = FX_WINDOW_DAYS) -> Derivation` with `Derivation(observations: tuple[ObsResult, ...], status: "effective" | "none" | "conflict", kind: str | None, low: Fraction | None, high: Fraction | None, kind_complete: bool, kind_confident: bool, notes: tuple[Note, ...])`; `ObsResult(id, kind, original, annualised, converted, comparable, reason, confidence)` where `original` keeps amount/currency/period/source and `converted` is in the floor's currency.
- `Note(code, ref, detail)` with codes `estimate_only`, `conflict`, `incomparable` (detail = the `CMP` reason code or the kind).
- `to_basis(derivation) -> dict`: JSON-safe, exact strings via `canon.exact`, keys `status, kind, low, high, kind_complete, kind_confident, observations: [{id, kind, comparable, reason}], notes: [{code, ref, detail}]` (the shape stored in `cap_inputs.compensation`). Dates are never needed again after derivation, so they are not stored.
- Date arithmetic is on `datetime.date` differences only; `now` is never an input.

**Tests first (`test_compensation_derive.py`), all values by hand-written fractions:**
- order: a monthly figure ×12 then converted: `(low 250000 INR/month → ×12 = 3,000,000)`; USD→INR at `83.20` exact; originals preserved in the result;
- window: rate dated the same day, 30 days before (pass), 31 days before, one day after the observation (both `FX_RATE_OUTSIDE_WINDOW`);
- missing pair ⇒ `FX_CONVERSION_MISSING`; total vs base floor ⇒ `BASIS_MISMATCH`; floor not verified ⇒ every observation `FLOOR_UNVERIFIED`; when several reasons apply the first of `FLOOR_UNVERIFIED`, `BASIS_MISMATCH`, `FX_CONVERSION_MISSING`, `FX_RATE_OUTSIDE_WINDOW` is reported (tested with an observation that triggers all four);
- effective kind: posted + estimate ⇒ posted wins; posted present but incomparable and a comparable estimate ⇒ `none` (no fall-back) with an `incomparable` note; estimate only ⇒ `effective` with kind `estimate` and an `estimate_only` note; empty ⇒ `none`;
- union/conflict: the spec's three examples; touching ranges (30–40, 40–45 ⇒ union 30–45); the chain 30–40 / 35–45 / 42–50 ⇒ `conflict`; open sides: `30–open` + `40–45` ⇒ union `30–open`; `null`/`null` observation; a single observation; two identical;
- `kind_complete` false when any observation of the effective kind is incomparable even if others compute a range; `kind_confident` false when any is `low`;
- `to_basis` round-trips through `canonical_json` with no floats and is deterministic across observation order.

**Acceptance:** exhaustive small grid (kinds × comparability × confidence) agrees with a straightforward reference implementation written in the test; all green.

**Commit:** `Add the pure compensation derivation`.

### Task C — `careeros comp`, ledger events and validation

**Goal and scope:** the command, the two ledger events with the specified dedupe, and the `CMP` section of `validate`.

**Files (new):** `careeros/core/compensation/service.py`, `careeros/cli/comp.py`; `tests/test_compensation_command.py`. **Modify:** `careeros/cli/main.py` (register), `careeros/core/ledger.py` (reserve the `compensation.` prefix; add `compensation.rejected` to `_NEEDS_REASON`), `careeros/core/validation.py` (call `compensation.service.validate_workspace`, mirroring the evaluation call).

**Reuse:** the `evaluate` command and service as the structural template (lock, `ensure_writable`, resolve job, exit codes, `_rejected` pattern); `markets.parse` and `norm_label` for the floor.

**Dependencies:** A, B.

**Contracts to define:**
- `service.floor_for(markets, label) -> tuple[MarketRow | None, FloorInfo]` resolving the label in the spec's fixed order, the same order as `evaluation.markets.verify_market`: `None` ⇒ `(None, unverified)`; ambiguous label (`label in markets.ambiguous`) ⇒ `MarketError` mapped to `CMP007`; unknown label ⇒ `MarketError` mapped to `CMP007`; `markets.unusable` ⇒ `(row, unverified)`; `row.floor is None` ⇒ `(row, unverified)`; otherwise `(row, verified FloorInfo)`. The check applies whenever the label is non-null, even with no observations.
- `service.init(root, ref) -> str` (writes the skeleton atomically, refuses an existing file, no event).
- `service.run(root, ref, actor) -> Report(job_id, rel, record, derivation, issues, recorded: bool)` : validates, derives, and appends events; never writes the file. `recorded` is true only after a successful append.
- Event data: `compensation.recorded` `entity=job.id`, `artifacts=[rel, "sha256:<canonical stamp hash>"]`, `action` summarising the observation count; `compensation.rejected` `entity=job.id`, `reason="CMP001,CMP003"` (sorted, comma-joined), `artifacts=[rel, "sha256:<raw bytes hash>"]`.
- Dedupe helpers (pure, unit-tested): `latest_valid_recorded(events, job_id) -> str | None` (ignores malformed events, wrong entity, bad hash strings) and `is_duplicate_rejection(events, job_id, raw_hash, codes) -> bool` (the latest `compensation.*` event for the job is an identical rejection).
- CLI: `careeros comp <job> [--init] [--json]`; text output lists observations (original and converted), reasons, effective kind/range, conflicts, whether the compensation cap would fire, action-item notes, and any `CMP100` warnings; `--json` is one JSON object once the workspace is found; exits 0 valid, 1 invalid record / unknown market row / ledger failure / `--init` on an existing file, 2 usage or no workspace.
- `validate` codes added: `CMP001`-`CMP007` errors, `CMP100` warning (observation `as_of` older than `OBSERVATION_STALE_DAYS` relative to `now`); `validate_workspace(root, now)` reads `jd.md` per job and `profile.md` once; absent `compensation.md` produces nothing.

**Tests first (`test_compensation_command.py`):**
- happy path: `--init`, edit, `comp` ⇒ exit 0, one `compensation.recorded`; second run ⇒ no new event; edit ⇒ new event; revert to the earlier content after an intervening different valid record ⇒ a new event, because the baseline is the latest valid `recorded` event (the intervening one); revert immediately after a rejection with no intervening valid record ⇒ no new event (equal to the latest valid hash);
- invalid file: `comp` ⇒ exit 1, one `compensation.rejected`; same invalid file again ⇒ no new event; a different invalid file with the same codes ⇒ new event; the same file after it became valid and was recorded, then invalid again ⇒ rejected again; in every case `compensation.md` and every file except `ledger.jsonl` are byte-identical and `ledger.jsonl` gains exactly the expected events (use a test helper `assert_only_ledger_appended(before, after, expected_types)` built on `snapshot`: it checks the old ledger bytes are an exact prefix of the new and lists the appended event types);
- ledger failure (`monkeypatch` of `ledger.append_events`): the file is untouched, the output says nothing was recorded, exit 1, the next run succeeds; a corrupt prior event is treated as no baseline;
- `--init` refuses an existing file, writes no event, leaves the file byte-identical;
- market matrix, each case asserting the `comp` exit code, the `validate` finding, the observation reason and the events: `market: null` + empty record ⇒ valid, exit 0; `market: null` + observations ⇒ `CMP002`, exit 1; a label listed twice in `## Markets` ⇒ `CMP007`, exit 1, one `compensation.rejected` (also with an empty record); a label naming no row (and a profile with no Markets table at all) ⇒ `CMP007`, exit 1; a row whose floor cell is `TBD` ⇒ valid, observations `FLOOR_UNVERIFIED`, exit 0, one `compensation.recorded`; a profile with two `## Markets` headings and an existing label ⇒ valid, `FLOOR_UNVERIFIED`; a normal row ⇒ verified; `validate` gives exactly the same verdicts as `comp` for every case;
- `validate` read-only: `snapshot(root)` (ledger included) equal before and after for valid, invalid and `CMP100` workspaces; `CMP100` is a warning and changes no exit code without `--strict`; a workspace with no `compensation.md` and a legacy workspace are unchanged;
- `--json` purity and plain-text usage errors; reserved prefix test (`ledger append compensation.x` refused like the `evaluation.` prefix); `LED003` accepts `compensation.*` events for a job entity.

**Acceptance:** all green; `comp` never modifies `compensation.md` (hash equality asserted in every test) and the only other change it can make is the appended ledger events; no unrelated command output changes.

**Commit:** `Add the comp command, compensation ledger events and CMP validation`.

### Task D — Golden v1 fixture, rules/basis v2 and the v2 scorer

**Goal and scope:** (1) before touching any code, capture a complete 0.5.0 evaluation workspace as a golden fixture; (2) add rules version `"2"` and basis version `"2"` and teach the pure scorer the v2 `cap_inputs`, keeping v1 scoring byte-identical. No wiring into `evaluate` yet.

**Files:** `tests/fixtures/evaluation_v1/` (new: `profile.md`, `career/`, one job directory with `job.md`, `jd.md`, `evaluation.md`, a `ledger.jsonl` whose chain verifies), `tests/test_evaluation_v1_compat.py`, `tests/test_evaluation_score_v2.py`. **Modify:** `evaluation/rules.py`, `evaluation/score.py`.

**Dependencies:** B.

**Step order (mandatory):** first commit the fixture, generated with the **unmodified 0.5.0 code** through the real CLI on invented data, plus a test that loads it and asserts today's behaviour (validates with no `EVL007`, scores to the stored result, the ledger chain verifies). Commit it alone as `tests: golden 0.5.0 evaluation workspace`. Only then change code.

**Contracts to define:**
- `rules.py`: `RULES_VERSION = BASIS_VERSION = "2"` for new evaluations; `RULES` and `BASIS_VERSIONS` keep `"1"` unchanged and add `"2"`. `RulesV2` carries every v1 constant unchanged plus `fx_window_days = 30` (the compensation constants are versioned with the rules). `BasisSchema` for `"2"` has the same top-level keys as v1; the difference is the `cap_inputs` layout, documented in the docstring.
- v2 `cap_inputs`: `{market, need{value, verification, source_cell, row_state}, listing, quote, floor{amount, currency, basis, verification, source_cell, row_state}, compensation{...to_basis shape from Task B...}}`. `range`, `confidence` and `as_of` are gone; the floor is `verified` or `unverified` only (taken from the Markets row; no agent copy, so no `conflict` state).
- `score.py`: `_validate` dispatches the `cap_inputs` layout on `basis["basis_version"]`; `_compensation_cap` v2 implements the spec §5.3 rules from the stored values (parse the stored exact strings with `canon.parse_exact`; strict `<`); v1 functions are kept as is. `_confidence` v2: the unknown deduction applies when the floor is verified and `compensation.status != "effective"` (keeps today's behaviour for "range null"). `_action_items` v2: floor items as today; pay items from `compensation.notes` and from `status == "none"` with no observations (`ITEM_RANGE_UNDISCLOSED`, unchanged text). New pinned constants for the notes (`ITEM_PAY_ESTIMATE_ONLY`, `ITEM_PAY_CONFLICT`, `ITEM_PAY_INCOMPARABLE` templates naming the reason code) and a v2 cap reason that equals `REASON_COMPENSATION` when no conversion happened and names both the converted high and the floor currency otherwise (**proposed**, section 8).

**Tests first:**
- `test_evaluation_v1_compat.py` (runs against the golden fixture and survives the whole task): `score(stored_basis)` equals the stored result field by field; `integrity.check` on the unmodified fixture reports no tamper; `rules_for("1")` and `basis_schema_for("1")` unchanged; v1 constants byte-identical (assert the full constants table);
- `test_evaluation_score_v2.py`: **parity grid** over (high, floor, currency, basis, confidence) comparing the v1 scorer on a legacy `cap_inputs` with the v2 scorer on the equivalent derived `compensation` object for a single `posted`/`recruiter` figure: same cap presence and the same reason text when there is no conversion; then the spec cases: equality does not cap; null `high` never caps; estimate-only never caps; any low-confidence observation in the kind prevents the cap; conflict prevents the cap and adds the conflict item; floor unverified prevents the cap; an incomparable observation in the effective kind prevents the cap; `status none` with a verified floor lowers confidence exactly as `range null` did; malformed v2 `cap_inputs` ⇒ `ValueError`, unknown version ⇒ `UnsupportedVersion` first.
- Stored numbers: `compensation.high`/`low` exact strings round-trip; no floats in hashed data.

**Acceptance:** the whole existing suite and the golden-fixture tests green after the code change; `RulesV1` constants untouched (diff review); the parity grid has no mismatches.

**Commits:** `tests: golden 0.5.0 evaluation workspace`, then `Add evaluation rules and basis version 2 with the compensation scorer`.

### Task E — Evaluation wiring

**Goal and scope:** `evaluate` reads `compensation.md`; the agent half loses `job_facts.compensation` for new records; the `compensation` stamp; v1 records stay verifiable and report stale; explicit re-evaluation only.

**Files (modify):** `evaluation/model.py` (`Stamps` gains an optional `compensation`; `JobFacts` keeps the legacy fields only as long as the legacy parse needs them), `evaluation/record.py` (`STAMP_KEYS` by basis version, `stamps`, `Inputs` gains the parsed compensation record/raw text, `for_job`, `derive_basis`, `compute`, `assemble`, `_job_facts` / `validate_agent` rules for the legacy key and the market-equality check, `skeleton` unchanged), `evaluation/derive.py` (v2 `_cap_inputs` and `Basis.complete`, floor taken directly from the Markets row), `evaluation/markets.py` (a row-floor check that does not need an agent floor; `verify_market` itself unchanged for v1), `evaluation/integrity.py` (`_freshness` compares the stamp names present in the record; v2 derivation re-check; `market_issues` for both layouts), `evaluation/service.py` (map `CMP` errors to a rejection; no behaviour change for ledger/rollback logic), `tests/test_evaluation_*.py` (updated expectations only where the record format necessarily changed). New `tests/test_evaluation_compensation.py`.

**Dependencies:** A, B, C, D.

**Contracts to define:**
- Stamps: `STAMP_KEYS` for basis `"1"` stays five names; for `"2"` it is six (`... "inputs", "compensation"`). `compensation` stamp = `compensation.record.stamp(...)` of the job's current file. Every use of `STAMP_KEYS`/`Stamps` (grep list in the task report) is updated; v1 results are never re-keyed.
- Agent half rules: a record whose stored result is absent or has `basis_version == "2"` rejects `job_facts.compensation` with `EVL003` ("pay now lives in compensation.md; see the evaluate skill"); a record whose stored result has `basis_version == "1"` accepts the legacy block read-only (so it stays verifiable) but `careeros evaluate` refuses to run on it (`EVL003`, same message) until the block is removed. `job_facts.market` must equal `compensation.md`'s `market` when that file has observations, else `EVL006`.
- `derive_basis`: reads `inputs.compensation` (an absent file is the empty record), builds `FloorInfo` from the Markets row, calls `derive_compensation` with `rules.fx_window_days`, stores `to_basis` under `cap_inputs.compensation`, stamps `basis_version`/`rules_version` `"2"`. An invalid `compensation.md` (any `CMP` error) makes `compute`/`evaluate` raise a rejection carrying the `CMP` codes (exit 1, one deduped `evaluation.rejected`, nothing written).
- Integrity: a v1 result reports `basis` (and `rules`) stale, never tampered; `_derivation` stays skipped for stale-basis records; a v2 result whose stored `compensation` differs from the derivation of the current file is `EVL007` only when `compensation`-stamp is fresh and the others are fresh (same ordering rule as `inputs`). The `inputs` stamp is unchanged in meaning; confirm it still covers the Markets row cell (it does via `rubric_stamp(rubric, markets)`; add a test that a floor edit in `profile.md` marks the evaluation stale).
- Service: the `new != old` replacement logic is unchanged; a v1 → v2 re-evaluation after the legacy block is removed writes only `evaluation.completed` (stale-only, no `result_replaced`) because the file still matches the ledger hash.

**Tests first (`tests/test_evaluation_compensation.py` plus updates):**
- end-to-end evaluate on invented data: a `posted` figure below the verified floor ⇒ tier capped `weak` with the compensation cap; raise the figure above the floor in `compensation.md` ⇒ `validate` reports `EVL008` (stamp `compensation`), re-`evaluate` ⇒ no cap; `comp` and `validate` alone never change the evaluation file or events;
- empty record / absent file / skeleton ⇒ identical result and no staleness when a skeleton is created later; a changed `market` on an empty record does not stale it;
- foreign currency: no conversion ⇒ valid, no cap, `FX_CONVERSION_MISSING` item; add a conversion ⇒ stale then capped when below the floor; conversion dated after the observation ⇒ not comparable;
- conflicting posted figures ⇒ no cap and the conflict item; a `low`-confidence posted figure ⇒ no cap;
- golden v1 fixture: `validate` ⇒ `EVL008` (basis/rules) and **no `EVL007`/`EVL010`**; `evaluate` on it ⇒ exit 1 `EVL003` with the migration message and zero file/ledger changes except at most one deduped `evaluation.rejected`; after removing the legacy block by hand (simulating the skill) and adding a valid `compensation.md`, `evaluate` ⇒ exit 0, basis v2, only `evaluation.completed` appended, a second run `(unchanged)`;
- refusal message variants (spec §10): with no `compensation.md` the `EVL003` text says to migrate (see the `evaluate` skill); with an existing valid `compensation.md` it says to confirm the record covers the legacy block and then remove the block; with an existing invalid one it says to fix the `CMP` errors first; each variant tested, none writes anything but the deduped rejection;
- invalid `compensation.md` ⇒ `evaluate` exit 1 with `CMP` codes, one deduped rejection, evaluation file untouched; `job_facts.market` ≠ `compensation.md` market ⇒ `EVL006`;
- hand-edited v2 result and the ledger-mismatch behaviour from 0.5.0 still hold (re-run the existing tamper/stale/crash-window tests unchanged);
- `job_facts.compensation` present on a record with no result or a v2 result ⇒ `EVL003`.

**Acceptance:** the entire existing suite green with only the expectation edits listed in the report; golden-fixture and parity tests still green; no new dependency; `evaluate` still never writes outside `evaluation.md` and the ledger.

**Commit:** `Make evaluation read the compensation record (basis and rules version 2)`.

### Task F — Skills, docs and 0.6.0

**Goal and scope:** `browse` records pay in `compensation.md`; the `evaluate` skill stops asking for pay, reads the derivation and performs the safe migration; docs; version bump.

**Files (new):** `docs/compensation.md`, `tests/test_skills_compensation.py`. **Modify:** `careeros/templates/workspace/.claude/skills/browse/SKILL.md`, `careeros/templates/workspace/.claude/skills/evaluate/SKILL.md`, `careeros/templates/workspace/CLAUDE.md` (one routing line if needed), `docs/evaluation.md` (replace the pay-figure instructions and the stated limits), `mkdocs.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml` → `0.6.0`.

**Dependencies:** E.

**Contracts:**
- `browse`: at save time run `careeros comp <job> --init` and fill observations (kind, range, base/total, currency, period, source, `as_of`, confidence, and a verbatim quote for `posted`), or leave `observations: []`; existing comp-check rules stand ("unknown" is valid); one new line in the closing hint; the results table keeps its Comp column. No other browse text changes (a pin test compares the unchanged sections).
- `evaluate` skill: remove the pay-figure instructions; add: run `careeros comp <job>` and read the derivation; present reasons, conflicts, the cap outcome; never edit `compensation.md` beyond what the user's data says and never invent a conversion rate, source or date; use a conversion only if the user or a cited source gave one.
- **Migration section** with exactly the spec §10 rules: the four ordered steps (write, validate, remove, re-evaluate) each with its precondition; the verbatim `Migrated from evaluation.md (0.5.0)` provenance heading in the new file's notes; confirm-before-write and confirm-before-remove; ask for `kind` (suggest `estimate`; `posted` also needs a verbatim quote) and for `period`; never invent source/`as_of`/confidence/currency/basis; never overwrite an existing `compensation.md`; never remove the legacy block before a valid record exists; **the recovery-by-state table with all six rows** (including the fallback that rebuilds from the stored v1 `result.basis.cap_inputs` when the block is gone and no record exists); idempotent repeats.
- Docs: record format and fields, kinds and why, derivation order, the rate window, cap rules with the six edge cases, `CMP` codes, `comp` command and its two events and dedupe rules, evaluation v2 changes and the one-time stale after upgrade, the migration step, stated limits (spec §13). `mkdocs build --strict` passes.
- CHANGELOG `0.6.0`: run `careeros upgrade` to receive the updated skills; existing evaluations read as stale once and need an explicit re-evaluation; evaluation basis/rules version 2. README: add compensation to the overview. After bumping, refresh both venvs' installed metadata with `pip install -e ".[dev]"` (the version test reads installed metadata).

**Tests first (`test_skills_compensation.py`):** phrase pins for the browse `comp --init` step and the evaluate migration rules (never overwrite, ask for kind, no inventing, show-before-write, confirm-before-remove, re-evaluate only after validation, no scorer writes, the provenance heading, and each of the six recovery-table rows); prompt-level pins that neither skill instructs an unconditional `transition`; the prep Career Memory evidence-gate sha pin and the GPT-workspace sha table still pass; **a test extracts the evaluate skill's example `compensation.md` and runs `careeros comp` on it** (exit 0, expected derivation) so the example cannot drift; `scaffold(refresh=True)` delivers the updated skills; wheel/doc build checks left to Task G.

**Acceptance:** all green; `mkdocs build --strict` exit 0 (own exit status checked); docs numbers equal the code constants (a test reads `FX_WINDOW_DAYS` and `OBSERVATION_STALE_DAYS` and asserts the doc strings contain them).

**Commit:** `Add the compensation skills, docs and the 0.6.0 version bump`.

### Task G — End-to-end test and acceptance

**Goal and scope:** one end-to-end test on invented data, a disposable-workspace acceptance run, release checks.

**Files (new):** `tests/test_compensation_e2e.py`. **Modify:** `docs/END_TO_END_TEST.md` (a "## Structured compensation (0.6.0)" section).

**Dependencies:** F.

**Steps:** (a) create a job and `jd.md`, `comp --init`, record a `posted` figure below a verified floor, `evaluate` ⇒ `weak` via the compensation cap; (b) change the figure above the floor ⇒ `validate` shows `EVL008`, re-`evaluate` ⇒ cap gone, a second run `(unchanged)`; (c) foreign currency without then with a conversion; (d) a conflicting pair ⇒ no cap, an action item; (e) the golden v1 fixture ⇒ stale, refused with the migration message, migrated by hand following the skill steps, re-evaluated; **migration interruption**: repeat the migration from a fresh copy of the fixture, stopping after step 1 (file written, block present), after step 2, after step 3 (block removed, not yet evaluated), and in the state with the block removed and no `compensation.md` (rebuild from the stored v1 basis); each resumes by the recovery table to the same derivation as the uninterrupted run, `compensation.md` is never rewritten (sha256 equal after its first write) and no observation is duplicated; (f) `validate` leaves every workspace file, ledger included, byte-identical (sha256 table); an invalid or valid `comp` run leaves every file except `ledger.jsonl` byte-identical and `ledger.jsonl` only gains the expected events (old bytes an exact prefix, appended event types and count asserted); (g) `compensation.recorded`/`rejected` event counts and dedupe across the whole run; (h) isolation: `career/`, job status, `jd.md` untouched.

**Release checks to run and report truthfully (separate lines per Python version):** full suite on 3.14 and 3.11 (refresh both venv installs first; state if 3.11 is unavailable); `.venv/bin/python -m mkdocs build --strict` (its own exit status); wheel contains `careeros/core/compensation/*.py`, `cli/comp.py` and the updated skills and reports version 0.6.0; `git diff --check`; clean tree. The acceptance record holds commands, exit codes, tiers, reason/error codes, counts and checksum comparisons only, no real postings, employers or figures, and says what was not covered (skills not run live, no real posting). The real workspace is never read or copied.

**Acceptance:** all of the above recorded; any defect found stops the task with an exact reproduction; no earlier-task source is patched in this task.

**Commit:** `Add the compensation end-to-end test and the 0.6.0 acceptance record`.

---

## 7. Process and review

Subagent-driven if chosen: one implementer per task, an adversarial task reviewer, scoped re-reviews of fix rounds, a ledger of rulings, and a whole-branch review at the end (most capable model), as in the evaluation work. Guardrails carried from the evaluation work: D must not change any v1 constant; E must preserve rollback on ledger-append exceptions and the 0.5.0 tamper/stale/ledger-mismatch behaviour; C keeps `validate` read-only; G uses invented data in a disposable workspace.

## 8. Proposed additions beyond the spec (marked for approval)

1. **Task split:** the spec's task D (evaluation integration) is two tasks here (D scorer/versions and golden fixture; E wiring) so each is reviewable.
2. **Stamp equivalence:** absent file, skeleton and any `observations: []` record share one stamp (the spec says "a fixed constant for no file"); this avoids staling an evaluation just because a skeleton was created. `market` does not enter the stamp when there are no observations.
3. **Unparseable file stamp:** an invalid file hashes its normalised raw text under an `invalid:` prefix so editing it still changes the stamp.
4. **Skeleton:** `--init` writes `market: null`; `market` may be `null` only with no observations (otherwise `CMP002`), and a null market makes every observation `FLOOR_UNVERIFIED`.
5. **Cap reason text:** identical to 0.5.0 when no conversion occurred; names the converted high and the floor currency when one did.
6. **`cap_inputs` v2 layout:** the `compensation` sub-object holds status, kind, exact `low`/`high`, `kind_complete`, `kind_confident`, per-observation comparability and notes; `range`, `confidence` and `as_of` leave the layout.
7. **Event convention:** `compensation.*` events carry `artifacts: [rel, "sha256:<hex>"]` and `reason` like the evaluation events, instead of ad-hoc data keys.
8. **Legacy key rule:** the legacy `job_facts.compensation` block is accepted read-only on records whose stored result is basis v1 and rejected (`EVL003`) otherwise.
9. **Golden fixture:** a committed 0.5.0 workspace generated by the unmodified code is the back-compat anchor.
10. **Constants:** `fx_window_days` is versioned with rules v2; `OBSERVATION_STALE_DAYS` is a validation-only constant that never reaches the scorer.
11. **One market-label contract:** the order `null` / ambiguous / unknown / unusable / no machine-readable floor / verified (spec §5.1) is shared by `comp`, `validate` and `evaluate`; a wrong or ambiguous label is `CMP007` even with no observations.
12. **Write-scope wording:** "read-only" claims are split into `validate` (nothing changes, ledger included) and `comp` (only `ledger.jsonl` gains the documented events), and tests assert the prefix property.
13. **Resumable migration:** four steps with preconditions, a provenance note carrying the legacy block verbatim in the new file, a six-row recovery table (including rebuilding from the stored v1 basis), and distinct `EVL003` refusal messages.

## 9. Self-review (spec coverage)

Spec §4 (record) → A; §5 (derivation) → B and D (cap/confidence/items); §6 (kinds) → B; §7 (`comp`, events, dedupe, exits) → C; §8 (codes) → A (CMP001-006), C (CMP007, CMP100); §9 (evaluation integration, stamps, v1) → D, E; §10 (skills, migration) → F; §11 (docs/release) → F, G; §12 (tests) → distributed per task, cap grid and parity in D, e2e in G; §13 (limits) → docs in F; §14 (build order) → section 3 (D split noted in section 8); §15 (acceptance) → G. No placeholders; all names above are defined in the task that introduces them (`FloorInfo`, `Derivation`, `ObsResult`, `Note`, `to_basis`, `floor_for`, `latest_valid_recorded`, `is_duplicate_rejection`, `stamp`, `canonical`, `skeleton`, `RulesV2`).
