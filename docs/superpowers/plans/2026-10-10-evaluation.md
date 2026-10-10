# Job Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status: DRAFT FOR REVIEW. Not approved. No implementation has started.** Section 9 lists the decisions that need approval before Task A begins.

**Goal:** Add one deterministic evaluation record per saved job (`jobs/discovered/<slug>/evaluation.md`): the agent proposes requirements and fit judgements with verbatim quotes; code derives outcomes from career facts, scores, caps, tiers and confidence, stamps the result for freshness, and detects tampering.

**Architecture:** A new package `careeros/core/evaluation/` of pure, separately testable layers: canonical hashing and rules → rubric parsing and requirement-outcome derivation (against Career Memory) → scoring → record parsing, validation, rendering and integrity checks → the `evaluate` service (lock, atomic write, ledger). A thin `careeros evaluate` command and a `validate` section sit on top. A new `evaluate` skill and small edits to `prep`/`browse` deliver it to agents.

**Tech Stack:** Python 3.11+, Typer, PyYAML, pytest, `fractions.Fraction` for exact arithmetic. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-10-10-evaluation-design.md` (the source of truth for requirements). Where this plan and the spec differ, the spec wins; every place this plan adds to it is marked **proposed** in section 9.

**Verification note:** unlike the Career Memory plan, **nothing in this plan has been executed**. The signatures, rules and test values below come from reading the current repository and from hand arithmetic. Implementers must treat the tests as the authority: if a listed test value or rule here disagrees with the spec, the spec wins, and the discrepancy goes in the task report and the plan is corrected.

**Branch:** `evaluation` (the spec is committed there). Do not merge or push without being asked. The user's real workspace (`~/Projects/job-search`) is never touched; acceptance uses an invented workspace.

---

## 1. Scope and non-goals

**In scope:** the evaluation record and its validation; `careeros evaluate [--init] [--json]`; `EVL001`-`EVL008` and the `LED003` extension in `careeros validate`; reserved `evaluation.*` ledger events; the `evaluate` skill; `prep` starting from a fresh evaluation; docs, version bump, an invented-data end-to-end acceptance.

**Non-goals (separate specs):** structured compensation intelligence (the agent supplies pay figures as `browse` records them today); approvals beyond `APPLIED`; a dashboard; bulk evaluation across jobs; outreach automation; GPT Work parity; changing `browse`'s quick snippet score (its only change is one closing hint line); changing any scoring constant in the spec.

## 2. Existing interfaces this plan reuses (from reading the repository at `main` = `f290369`)

| Area | Interface found | Used for |
|---|---|---|
| IDs | `careeros.core.ids`: `PREFIXES["evaluation"] == "evl"` and `["requirement"] == "req"` already exist; `new_id(kind, existing)`, `is_valid_id(kind, value)` | the `evl_` id; requirement ids stay record-local (`r1`) |
| Ledger | `ledger.append_events(root, specs)` (one atomic batch; on failure the ledger file is restored to its previous size), `append_event`, `read_events`, `is_reserved()` (set `RESERVED_TYPES` plus prefixes `("workspace.", "memory.")`), `_NEEDS_REASON` (types that must carry a `reason`), `verify_chain` | `evaluation.*` events; reserve the prefix |
| Reject dedupe | `state_machine._reject` appends `job.transition_rejected` only if the previous event for the entity is not an identical rejection | the pattern for `evaluation.rejected` |
| Workspace | `WorkspaceLock` (re-entrant, per workspace), `ensure_writable`, `atomic_write_bytes`, `split_frontmatter`, `join_frontmatter`, `normalize_value`, `resolve_job(root, ref)` (id, slug or path), `read_job`, `job_files` | the command and validation |
| Memory | `memory.store.load_career(root) -> Career(facts, issues)` with `.errors`, `.by_id()`, `.active()`; `Fact(fm, body, path)` with `.id .kind .status .get()`; `ACTIVE = ("claimed","confirmed","verified")`; `store.Transaction` (remember originals, `rollback()`); `store.lexicon_for(root, career)`; `lexicon.canonical_tech`; `normalize.norm_text/norm_org` | outcome derivation, atomic write with rollback |
| Derived experience years | `memory.check.Memory(career, lexicon, now)` computes `.months`, the union of active dated experience intervals (also `.employer_months`), and `_title_variants()` | the generic-years rule and title matching; reuse by calling `Memory`, do not reimplement |
| Validation | `validation.validate_workspace(root)` extends an `issues` list with `Issue(severity, code, path, message, fix)`; `_above_fold(text)` cuts a markdown file at the `do not read past this line` marker; `LED003` loop over ledger entities; `store.load_career(root).issues` already included | the `EVL` section and the `evl_` branch of `LED003` |
| CLI | `cli/main.py` registers commands; `_util.resolve_root`, `fail`, `echo_json`, `default_actor`, `EXIT_OK/FAIL/USAGE` | `careeros evaluate` |
| Skills | `careeros/templates/workspace/.claude/skills/*/SKILL.md`, `CLAUDE.md` routing; delivered by `scaffold(refresh=True)` and `careeros upgrade` (no code change needed to deliver a new skill) | the `evaluate` skill |
| Tests | `conftest.py`: `clock` fixture, session-built `career` fixture (copy per test); `helpers.py`: `make_workspace`, `add_job`, `make_career`, `snapshot`; failure injection by `monkeypatch.setattr(<module>.ledger, "append_events", boom)`; wording tests in `test_skills_evidence.py`; refresh delivery in `test_scaffold.py` | all test tasks |

**Things that do not exist yet** (the design depends on them; the plan creates them): any canonical-JSON hashing helper (only `ledger._canon` for events and `store.sha256_hex`); any parser for the `profile.md` rubric or `## Markets` table; any `jd.md` convention (a repository-wide search finds none; `browse` saves the listing snippet inside `job.md`); any `evaluation` record.

**Observed `profile.md` shape** (from the `onboard` template): `## Scoring rubric (use when rating job listings 0–10)` is a markdown table `| Signal | Weight |` with weights `High | Medium | Negative — deprioritize`, located above the fold. `## Markets` is a table `| Market | Min base (per year) | Sponsorship | Priority |` whose floor cell may be a number with a currency or the text `TBD — derive from home floor`, and whose sponsorship cell is `required | preferred | not needed`.

## 3. Task sequence and dependency graph

```
A primitives ──> B rubric + outcome derivation ──> C scoring ──> D record + validation + render ──> E command + ledger + validate ──> F skills + docs ──> G acceptance
```

Strictly linear: each task's tests run green before the next starts. Every task ends with an independently testable deliverable and one commit. The existing 591 tests stay green at every commit.

## 4. Global constraints (verbatim from the spec where it has them)

- No new dependencies; Python `>=3.11`; deterministic: no model, network or similarity score. Same inputs, same result, on 3.11 through 3.14.
- Scoring constants: weights must 3 / strong 2 / nice 1; credits covered 1 / adjacent 0.5 / gap 0; fit weights High 3 / Medium 2 / Low 1; fit credits yes 1 / partial 0.5 / no 0; `combined = 0.65 × coverage + 0.35 × fit`; tiers `≥ 0.70 pursue`, `≥ 0.50 stretch`, `≥ 0.30 weak`, else `skip`. Arithmetic in exact fractions; thresholds compared on exact values; stored values rounded to four decimals.
- Unverifiable requirements leave the coverage denominator. Caps apply only when their condition is **established**; the lowest cap wins and all applied caps are listed. Unknown sponsorship or pay is never a cap.
- Only `claimed | confirmed | verified` facts count; `disputed` and `retired` never support. Status lowers confidence, never credit.
- `agent:` is written only by the agent (and `--init` writes the header once); `result:` and the body only by code. `evaluate` never changes job state and never writes to `career/`.
- `validate` is read-only. Every `evaluation.*` ledger type is reserved. Every action, including rejection and replacement, is logged.
- Freshness stamps: `jd`, `memory`, `rubric`, `rules`, `inputs`; a stale record is never presented as current.
- Surgical changes to existing code; the user's real workspace is never touched; committed acceptance records hold counts and codes only.

## 5. Review focus (inputs most likely to bite, each pinned by a task test)

1. A `direct` agent link on a requirement the code cannot verify must not produce `covered` (Task B).
2. A result edited by hand **and** inputs changed since: both reported, in the right order, with the right ledger reason (Tasks D, E).
3. A rubric table with duplicate, unknown-weight or reordered rows must degrade safely, not crash or mis-score (Task B).
4. A ledger failure after the file write must leave the old file and no event (Task E).
5. A posting reflowed (whitespace/line endings) must stay fresh; one reworded must go stale (Tasks A, D).

---

## 6. Tasks

### Task A — Contracts and deterministic primitives

**Goal and scope:** the data shapes and pure helpers every later task imports. No filesystem, no ledger.

**Files (new):** `careeros/core/evaluation/__init__.py`, `rules.py`, `canon.py`, `model.py`; tests `tests/test_evaluation_primitives.py`.

**Reuse:** `ids.is_valid_id("evaluation", …)`; Python stdlib only.

**Dependencies:** none.

**Contracts to define:**

- `rules.py`: `RULES_VERSION = "1"` and a frozen `RulesV1` holding every constant of section 4 as `Fraction`s (`COVERAGE_SHARE = Fraction(65,100)`, `FIT_SHARE = Fraction(35,100)`, tier cut-offs `7/10, 1/2, 3/10`, class weights, credits, fit weights, confidence thresholds `1/2`, `1/4`, the minimum requirement count `3`). `RULES: dict[str, Rules]` registry keyed by version string; `rules_for(version)` raises `UnknownRulesVersion` for a version this CareerOS does not know (a record written by a newer CareerOS). Old versions stay in the registry so a stored result can still be recomputed.
- `canon.py`: `canonical_json(obj) -> bytes` — UTF-8, `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`; mappings sorted by key; lists keep order; `None` is kept (`null`); **floats are forbidden in hashed structures** (raise `TypeError`): exact values are carried as strings (`"13/20"`) or integers, so hashes cannot differ across Python versions or float repr. `sha256_hex(bytes)`. `normalize_text(s)` for the JD stamp: NFC, `\r\n`/`\r` → `\n`, whitespace runs → one space, strip; **case kept**.
- `model.py`: frozen dataclasses/enums `Requirement`, `Link`, `FitJudgement`, `JobFacts`, `Outcome` (`covered|adjacent|gap|unverifiable`), `Tier` (`pursue|stretch|weak|skip|unrated`), `Confidence` (`high|medium|low`), `Cap`, `ActionItem`; `Stamps(jd, memory, rubric, rules, inputs)`.

**Tests first (`test_evaluation_primitives.py`):**

- `canonical_json` is independent of dict insertion order and nested ordering; a float raises `TypeError`; `None` and empty list/dict round-trip; non-ASCII is not escaped; two equal structures built in different orders hash identically.
- `normalize_text`: CRLF, CR and LF give the same hash; `"a  b\n\n c"` equals `"a b c"`; NFC vs NFD input (`"é"` composed vs decomposed) hash equal; changing a letter's case changes the hash; leading/trailing whitespace ignored.
- `rules_for("1")` returns the constants; `rules_for("99")` raises `UnknownRulesVersion`; every constant is a `Fraction`.
- Enum values equal the spec strings exactly (guards a typo changing a stored value).

**Acceptance:** all primitives pure and importable with no workspace; a table test asserts each constant equals the spec's number.

**Failure/rollback:** none (no writes).

### Task B — Rubric parsing and requirement outcome derivation

**Goal and scope:** (1) read the fit rows from `profile.md`; (2) derive each requirement's outcome and supporting facts from Career Memory, per spec §5; (3) produce the **basis**, the memory-derived data the scorer needs (see clarification 6).

**Files (new):** `careeros/core/evaluation/rubric.py`, `derive.py`; tests `tests/test_evaluation_derive.py`.

**Reuse:** `store.load_career`, `Career.active()`, `store.lexicon_for`, `lexicon.canonical_tech`, `normalize.norm_text`, `check.Memory(...)` for `.months` and title variants, `validation._above_fold` (promote to a shared helper only if importing a private name is unacceptable; otherwise import it).

**Dependencies:** Task A.

**Contracts:**

- `rubric.parse(profile_text) -> Rubric(rows: tuple[FitRow], problems: tuple[str])`; `FitRow(label, weight: int)`. Parse only the table under the `## Scoring rubric` heading, above the fold. Weight words map High 3 / Medium 2 / Low 1. Excluded from fit rows: any row whose label mentions compensation or sponsorship (case-insensitive) and any row with a `Negative…` weight. **Malformed input degrades, never crashes:** no heading or no table → `rows=()`, problem `"rubric missing"`; a row with an unknown weight word, an empty label, or a duplicate label (after normalisation) is dropped and recorded in `problems`; if any duplicate or unknown row is dropped the rubric is flagged `invalid` and fit is omitted entirely (an ambiguous rubric must not silently half-score). Row identity for the agent's `fit[].row` is the **normalised label** (NFC, whitespace-collapsed, casefolded).
- `derive.derive(requirements, career, lexicon, now) -> Derived` returning, per requirement: `outcome`, `facts` (≤ 5 ids, sorted), `status_floor` (`claimed < confirmed < verified`, or `None`), `reason` for `unverifiable`, plus the `Basis` (see Task D). Implements the spec §5 table exactly:
  - technology: canonical match of `term` against active `skill.name`, `technologies` of active experience/project/achievement; **no other field**;
  - title: normalised equality with an active experience title (including `check` title variants) or identity headline;
  - certification: active certification name/alias (lexicon `certs`);
  - education: every normalised word of `term` present in an active education fact's `degree` + `field`;
  - years without `term`: `years ≤ Memory.months // 12` → `covered` (facts = the active experience ids), above it → `gap`; with `term` → `unverifiable`;
  - domain/other: never `covered`; a valid link → `adjacent`, else `gap`.
  - A valid link (must name an existing **active** fact) upgrades `gap → adjacent` for every kind; it can never produce `covered`; strength `direct` vs `adjacent` is recorded but **does not change the outcome** (clarification 3).
  - Invalid link (missing fact, or `disputed`/`retired`) raises `DeriveError(code="EVL005")` carrying the requirement id.

**Tests first (`test_evaluation_derive.py`, invented memory via the `career` fixture plus targeted `ops.add_fact` calls):**

- technology covered by a skill; covered by an achievement's `technologies`; **not** covered when the term appears only in an unrelated achievement's *text* (not in `technologies`); not covered by a `retired` skill; not covered by a `disputed` achievement; covered by a `claimed` fact with `status_floor == "claimed"`, and by a `confirmed` one with `status_floor == "confirmed"`.
- years: `years: 3` with the career's derived 4 years → covered; `years: 10` → gap; `years: 5` + `term: Go` → unverifiable with a reason; a duration spanning two overlapping experiences uses the union (overlap counted once).
- title/cert/education covered and not-covered cases; education with partial words stays `gap`.
- links: a `direct` link on a `domain` requirement → `adjacent`; a `direct` link on a technology the memory lacks → `adjacent`, not `covered`; a link to a retired fact → `EVL005`; a link to a nonexistent id → `EVL005`; a link to a fact from a different workspace id format is just "nonexistent".
- rubric: the template table parses to six fit rows with weights 3,3,3,2,3,2 and excludes the compensation, sponsorship and Negative rows; table with a duplicate label → `invalid`, fit omitted; unknown weight word → row dropped and flagged invalid; no rubric → rows empty with problem; rubric below the fold is ignored; label comparison ignores case and extra whitespace.

**Acceptance:** each spec §5 row has a passing and a failing test; no outcome is ever `covered` without a deterministic match.

**Failure/rollback:** pure; errors are exceptions with codes; nothing written.

### Task C — Scoring, caps, confidence and action items

**Goal and scope:** a pure function from the **basis** to the result numbers, tier, caps, confidence and action items.

**Files (new):** `careeros/core/evaluation/score.py`; tests `tests/test_evaluation_score.py`.

**Reuse:** `rules.RULES`, `model` types, `fractions.Fraction`.

**Dependencies:** Task B (the `Basis` shape).

**Contract:** `score(basis, rules) -> ScoreResult(coverage: Fraction|None, fit: Fraction|None, combined: Fraction|None, tier, caps, confidence, action_items)`. Spec §6 exactly:

- coverage over non-`unverifiable` requirements; undefined → tier `unrated`, confidence `low`;
- fit over rows present; rubric missing/invalid → fit `None`, `combined = coverage`, one confidence step down;
- tier from the **exact** `combined`; rounding happens only when serialising (`round(float(x), 4)`) and is never used to choose a tier;
- caps: sponsorship (needed yes + listing not_offered + a quote present); compensation (`range.high < floor.amount` with equal currency **and** basis, confidence not `low`/`unknown`); two or more `must` gaps → `stretch`; lowest wins, every applied cap listed with its reason;
- confidence steps from spec §6.5 (claimed-only share > 1/2; unverifiable share of must/strong weight > 1/4, two steps above 1/2; decision-relevant unknown; fewer than three requirements or no `must`; rubric missing/invalid), floor `low`;
- action items in the fixed order of spec §6.6, each naming the requirement/fact.

**Tests first (`test_evaluation_score.py`), hand-computed values:**

- Spec worked example: requirements must covered (3), must unverifiable, strong adjacent (2×½), fit rows `yes`×High(3) and `partial`×Medium(2) → coverage `4/5`, fit `4/5`, combined `4/5`, tier `pursue`; claimed-only support and the 37.5% unverifiable share each cost a step → `low`.
- Coverage-only boundaries (rubric missing, weights 3,3,2,1,1 = 10): credits 7 → combined exactly `7/10` → `pursue`; credits 5 → `1/2` → `stretch`; credits 3 → `3/10` → `weak`; plus a direct unit test of `tier_for` at `Fraction(7,10)`, `Fraction(69999,100000)`, `Fraction(1,2)`, `Fraction(49999,100000)`, `Fraction(3,10)`, `Fraction(29999,100000)`, `0`.
- A case whose rounded four-decimal value would cross a boundary but whose exact value does not (e.g. combined `Fraction(69999,100000)` rounds to `0.7`): tier is `stretch`, proving rounding is not used.
- `unrated`: no requirements; all unverifiable.
- Weights: `must`/`strong`/`nice` contribute 3/2/1; adjacent half credit; gap zero; unverifiable removed (coverage with and without an unverifiable item is identical).
- Caps: sponsorship cap alone (combined 0.9 → `weak`); comp cap alone; two must gaps → `stretch`; all three together → `weak` with three entries; lowest wins; a cap never raises a tier (combined 0.2 with a `stretch` cap stays `skip`).
- **Established vs unknown:** `listing: unknown`, null range, currency mismatch, basis mismatch, `confidence: low`, `needed: no` each produce **no cap** and the right action item; the established versions of the same inputs cap.
- Confidence: each of the five step triggers alone; two steps for unverifiable > 1/2; stacking floors at `low`; thin extraction (2 requirements; no must).
- Determinism: the same basis scored twice, and with its requirement list reordered, gives equal results.

**Acceptance:** every number in spec §6 is pinned by a test; no float arithmetic in the scorer (a test greps the module for `float(` outside the serialiser, or asserts types are `Fraction`).

**Failure/rollback:** pure.

### Task D — Record parsing, rendering, integrity and validation

**Goal and scope:** read and write the record file's structure; render the body; compute stamps; check every `EVL` rule; separate stale from tampered.

**Files (new):** `careeros/core/evaluation/record.py`, `integrity.py`; tests `tests/test_evaluation_record.py`.

**Reuse:** `split_frontmatter/join_frontmatter/normalize_value`, `ids.is_valid_id`, Tasks A-C, `store.load_career`, `Career.by_id()`.

**Dependencies:** Tasks A-C.

**Contracts:**

- `record.load(path) -> Record` (parse; `EVL001` on unreadable/invalid YAML/missing namespaces); `record.skeleton(job_id, now) -> bytes` for `--init`.
- `record.validate_agent(record, jd_text, career, rubric) -> list[Issue]` implements `EVL002`-`EVL006` per spec §8: duplicate/missing requirement ids; `job` ≠ this job; enums; fit `row` not a fit row of the rubric (normalised comparison); `quote` found in `jd.md` after the **same** normalisation used for the JD stamp (substring of `normalize_text(jd)`; quotes are normalised the same way); links to missing/inactive facts; sponsorship quote required when `listing != unknown`; compensation requires `source`, `confidence`, `as_of`.
- `record.stamps(jd_text, career, rubric, rules_version, agent) -> Stamps` per spec §9: memory = hash of the canonical JSON of every fact (id, type, status, kind content fields, `parent`, `supports`; timestamps and `confirmed_at` excluded; **numbers inside facts such as `metrics.value` are carried as decimal strings** to satisfy the no-float rule), rubric = hash of the parsed fit rows, inputs = hash of the `agent:` half without `updated_at`.
- **Basis** (clarification 6): `result.basis` holds the exact inputs of the pure scorer: per requirement `{id, class, outcome, facts, status_floor}`, per fit row `{weight, judgement}`, the sponsorship and compensation inputs the cap logic reads, `requirement_count`, `rubric_state`, and `rules_version`. `result.stamps` includes `basis` (hash of the basis).
- `record.render_body(result) -> str`: a fixed template (tier, scores, confidence, a requirements table, caps, action items) containing no timestamp or randomness; the stored body must equal `render_body(stored_result)` byte for byte (clarification 9).
- `integrity.check(record, current_inputs) -> IntegrityReport(stale: tuple[str], tampered: tuple[str])`, evaluated **in this order** (clarification 7):
  1. **Structure** (`EVL001`-`EVL006` on the agent half; failures stop here).
  2. **Self-consistency (tamper):** `hash(stored basis) == stamps.basis`; `score(stored basis, rules_for(stamps.rules)) == stored scores/tier/caps/confidence/action_items`; `render_body(stored result) == stored body`. Any failure → `EVL007` (message names which of basis, scores or body). This uses only stored data, so it works even when current memory has changed.
  3. **Freshness:** compare each stamp with the current value: `jd`, `memory`, `rubric`, `rules` (`rules != RULES_VERSION` current), `inputs` → `EVL008` listing the differing stamps.
  4. **Derivation check (only when `memory`, `rubric` and `inputs` stamps are all current):** `derive(current agent, current memory) == stored basis`, otherwise `EVL007` (the stored basis itself was altered).
  Both reports can be non-empty at once; they are reported separately.
- `EVL008` for an **unknown newer** rules version is escalated to an error with the message "written by a newer CareerOS".

**Tests first (`test_evaluation_record.py`):**

- Each of `EVL001`-`EVL006` has a failing fixture (list in spec §8, plus: quote differing only by case → fails; quote spanning a reflowed line break → passes; fit row text with different case/spacing → passes; unknown rubric row → `EVL003`; `listing: not_offered` without a quote → `EVL006`; compensation missing `as_of` → `EVL006`).
- Freshness: change `jd.md` wording → stale `jd`; reflow `jd.md` whitespace/line endings → still fresh; add/retire/dispute/confirm a fact → stale `memory`; change a rubric weight → stale `rubric`; bump the rules version (monkeypatch `RULES_VERSION`) → stale `rules`; edit the `agent:` half → stale `inputs`; edit only `agent.updated_at` → still fresh; changing a fact's `updated_at` → still fresh.
- Tamper: flip `result.tier`; change a score; delete a cap; edit the body; edit `result.basis` → each `EVL007` with the right message. Tamper with all stamps current → `EVL007` from the derivation check. Result edited **and** `jd.md` changed → both `EVL007` and `EVL008` reported, `tampered` detected using the stored basis even though current memory differs.
- Unknown stored rules version → error; older known version recomputes with its own constants (use a second registered test version).
- `render_body` is deterministic and contains no timestamps; golden-file test for a fixed result.
- Canonicalization determinism: the stamp for a fixed invented memory equals a hard-coded expected hash (catches accidental changes to the canonical form).

**Acceptance:** staleness and tampering are distinguished and independently reportable; validation never writes.

**Failure/rollback:** pure/read-only.

### Task E — The `evaluate` command, ledger events and `validate` integration

**Goal and scope:** the transactional service, the CLI, the reserved events, and the `validate` section.

**Files (new):** `careeros/core/evaluation/service.py`, `careeros/cli/evaluate.py`; tests `tests/test_evaluation_command.py`. **Modify:** `careeros/cli/main.py`, `careeros/core/ledger.py`, `careeros/core/validation.py`.

**Reuse:** `WorkspaceLock`, `ensure_writable`, `resolve_job`, `store.load_career`, `store.Transaction`, `ledger.append_events`, the `_reject` dedupe pattern, `_util` helpers.

**Dependencies:** Tasks A-D.

**Ledger changes:** reserve the prefix `evaluation.` in `is_reserved`; add `evaluation.rejected` to `_NEEDS_REASON` (a rejection must say which codes). Events: `evaluation.completed` (entity `evl_…`; `new_state` = tier; `action` = tier, combined score, confidence; `artifacts` = the job directory-relative path of `evaluation.md` and `sha256:<result hash>`), `evaluation.rejected` (entity the job id), `evaluation.result_replaced` (entity `evl_…`, `reason` ∈ `tampered`, `stale`, `tampered,stale`).

**Service flow (`evaluate(root, ref, actor)`), all under `WorkspaceLock`:**

1. `ensure_writable`; resolve the job; load career — if `Career.errors` raise (CLI exit 2).
2. Read `jd.md` and the record; run `validate_agent`. Any issue → append one `evaluation.rejected` (deduped against the entity's last event) and raise `InvalidInputs` (exit 1). Nothing else is written.
3. Derive, score, build the new `result:`, render the body.
4. Decide the events: if the new `result`+body equal the stored ones byte for byte → **no-op** (no write, no event). Otherwise run `integrity.check` on the stored record: if it reports tampering, the batch begins with `evaluation.result_replaced`; then `evaluation.completed`.
5. Write: `Transaction().write(evaluation.md, new_bytes)`, then `ledger.append_events(root, specs)` (both events in **one** batch); on any exception `Transaction.rollback()` restores the previous file and re-raise. The `result_replaced` event and the replacement therefore commit together or not at all.
6. Return the summary for the CLI.

`--init`: if `evaluation.md` exists, exit 1; else write the skeleton (`evl_` id, `job`, `created_at`, empty `agent:`) under the same lock and append `evaluation.completed`? **No** — nothing was scored; `--init` appends no event (a skeleton carries no judgement). It refuses when the job is missing.

**CLI:** `careeros evaluate <job> [--init] [--json]`; exit 0 scored (any tier incl. `unrated`), 1 invalid inputs or existing-file `--init`, 2 usage / untrustworthy memory / workspace newer than CareerOS. `--json` prints only JSON (the result summary, or the list of issues on failure).

**validate integration:** for each job directory that has `evaluation.md`, call `record.load` → `validate_agent` → `integrity.check` and add `Issue`s for `EVL001`-`EVL008` (`EVL007` error, `EVL008` warning); extend the `LED003` entity loop with prefix `evl_` against the set of evaluation ids found on disk. Read-only.

**Tests first (`test_evaluation_command.py`):**

- Happy path: init, fill the agent half (helper builds an invented job + `jd.md` + record), evaluate → result matches the hand-computed tier/scores; one `evaluation.completed` event with the tier; ledger chain valid.
- Idempotence: second run → byte-identical file, no new event, exit 0.
- Rejections: each `EVL001`-`EVL006` input → exit 1, file unchanged, one `evaluation.rejected` with the codes; the identical attempt twice → one event.
- Memory with errors → exit 2, nothing written; workspace newer than installed → refused; job missing → exit 2.
- **Ledger failure:** `monkeypatch.setattr(service.ledger, "append_events", boom)` → previous `evaluation.md` restored byte for byte, no events, and a re-run succeeds. Same failure on `--init` leaves no file.
- **Tamper then recompute:** hand-edit `result.tier` → `validate` reports `EVL007` and writes nothing; `evaluate` writes `result_replaced(reason=tampered)` then `completed` in one batch, the file is restored to the correct result, `validate` is clean afterwards.
- **Tamper + stale:** edit the result, then change a fact → `evaluate` logs reason `tampered,stale`.
- **Stale only:** change a fact → `validate` warns `EVL008`; `evaluate` writes `completed` with **no** `result_replaced`.
- `evaluate` never changes the job's `status` frontmatter nor any file under `career/` (snapshot compare) and never changes `jd.md`.
- `ledger append --type evaluation.completed` is refused (reserved); `LED003` fires when an evaluation file is deleted but its entity is in the ledger.
- `--json` output is pure JSON on success and on failure.

**Acceptance:** all guarantees of spec §7 hold; failure injection proves no half-written state in the file/ledger pair (see clarification 8 for the one residual window).

**Failure/rollback:** described above; the file is restored, the ledger is restored by `append_events`.

### Task F — Skills, delivery and documentation

**Goal and scope:** the `evaluate` skill; `prep` and `browse` edits; `CLAUDE.md` routing; docs; version bump.

**Files (new):** `careeros/templates/workspace/.claude/skills/evaluate/SKILL.md`, `docs/evaluation.md`, `tests/test_skills_evaluation.py`. **Modify:** `prep/SKILL.md`, `browse/SKILL.md`, `careeros/templates/workspace/CLAUDE.md`, `mkdocs.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml` (0.5.0), `docs/career-memory.md` only to cross-link.

**Reuse:** the skill-patch style used for the evidence gate (a one-off script that asserts each anchor occurs exactly once); `scaffold(refresh=True)`/`upgrade` for delivery.

**Dependencies:** Task E.

**Skill rules to encode (pinned by wording tests):**

- `evaluate`: save `jd.md` verbatim; `careeros evaluate <job> --init`; extract requirements with verbatim quotes, `class`, `kind`, `term`/`years`; judge **every** fit row with a quote (or `quote: null`, `no`); record sponsorship and compensation exactly as `browse` does (sources, confidence, `as_of`); run `careeros evaluate`; on exit 1 fix the named `EVL` codes and re-run, never edit `result:`; present tier, scores, confidence, caps, gaps and action items; **ask before any `transition`**; never claim a requirement is met beyond `covered`/`adjacent`; never add or alter memory (point to the `memory` skill instead).
- `prep`: STEPs 2-3 start by checking for a fresh evaluation (`careeros validate` shows `EVL008`, or run `careeros evaluate` and read the exit); missing or stale → run the `evaluate` skill first; lead with `covered` requirements' facts, state `adjacent`/`gap`/`unverifiable` honestly; the Career Memory evidence gate still runs on every draft and the verdict is never proof.
- `browse`: one closing hint line only.
- `CLAUDE.md`: a routing row for "evaluate [job]" / "should I apply to [job]".

**Tests first (`test_skills_evaluation.py`):** the phrases above exist in `evaluate` and `prep`; `prep` still contains the evidence-gate heading; `browse` changed by exactly the hint line (compare against the previous text with the line removed); the `evaluate` skill never contains an unconditional `transition` instruction (it must appear only after an "ask" sentence); `CLAUDE.md` routes the trigger; `scaffold(refresh=True)` delivers `evaluate/SKILL.md` to an existing workspace; `gpt-workspace` unchanged.

**Docs (`docs/evaluation.md`):** the record format, outcome meanings, scoring tables, caps, confidence, the freshness stamps, tamper vs stale, exit codes, `EVL` codes, what the agent must supply, and the stated limits (extraction is judgement; whole-memory fingerprint over-invalidates). Strict docs build must pass.

**Acceptance:** wording tests green; `mkdocs build --strict` exit 0; wheel contains the new skill; version `0.5.0`; CHANGELOG notes "run `careeros upgrade` to receive the `evaluate` skill".

**Failure/rollback:** none (text files); the patch script asserts anchors so a mismatch fails loudly.

### Task G — Acceptance and release readiness

**Goal and scope:** an automated invented-data end-to-end test plus a recorded manual acceptance on a disposable workspace.

**Files (new):** `tests/test_evaluation_e2e.py`; modify `docs/END_TO_END_TEST.md` (append a section with counts and codes only).

**Dependencies:** Tasks A-F.

**Automated test steps (all invented data, `clock` fixture, one test):**

1. Build a workspace, an invented memory, a job with `jd.md`; `evaluate --init`.
2. Write the agent half; `evaluate` → assert tier, scores, confidence, caps and action items equal the hand-computed expectations.
3. Re-run → no file change, no new event.
4. Hand-edit `result.tier` → `validate` reports `EVL007` and the workspace is unchanged; `evaluate` logs `result_replaced`, restores correctness; `validate` clean.
5. Independently change `jd.md` wording, a fact's status, a rubric weight, the rules version (monkeypatch) and the `agent:` half; assert `EVL008` names exactly that stamp each time, then re-evaluate to clear it.
6. Inject a ledger failure → file restored, no event.
7. Assert no change to `career/`, job `status`, or `jd.md` across the whole flow.
8. Run `scaffold(refresh=True)` and assert `prep` contains the fresh-evaluation rule and the evidence gate.

**Manual acceptance (disposable workspace only, never `~/Projects/job-search`):** repeat the flow with `careeros` on a freshly generated invented workspace; record in `docs/END_TO_END_TEST.md`: date, commit, counts, tier, exit codes, validation codes. No posting text, employer names, personal data or compensation figures.

**Release checks to run and report truthfully:** full suite on Python 3.14 and 3.11; `mkdocs build --strict`; build the wheel and verify `evaluate/SKILL.md` and the `evaluation` package are inside; `git diff --check`; confirm the real workspace's checksums are unchanged if (and only if) it was ever used — it is not used in this task, so state "not touched".

**Acceptance:** the final checklist (section 10) all green.

---

## 7. Risks and failure handling

- **Extraction is judgement.** Mitigated by verbatim quotes, thin-extraction confidence steps and documentation; not eliminable.
- **Float and canonicalisation drift across Python versions.** Mitigated by forbidding floats in hashed data, a golden-hash test, and running the suite on 3.11 and 3.14.
- **Over-invalidation.** The whole-memory fingerprint marks every record stale after any memory change; accepted by the spec (re-evaluating is instant).
- **File/ledger crash window.** See clarification 8.
- **Rubric free-text brittleness.** The rubric is user-edited prose; parsing degrades safely (omit fit, lower confidence, say why). A structured rubric would be a later change.
- **A new convention (`jd.md`).** Existing jobs have none; evaluation requires it, and the skill creates it. Jobs without it are simply not evaluated (no error unless an `evaluation.md` exists).
- **Review-driven rework** (as in the Career Memory work): expect fix rounds on derivation and scoring; keep those two layers pure so fixes stay local.

## 8. Rollback of the feature itself

All new behaviour is additive: new package, new command, new skill, new optional files per job. Removing `evaluation.md` files and the package restores the previous behaviour; no existing data format changes. The only edits to existing behaviour are the reserved `evaluation.` prefix, one `_NEEDS_REASON` entry, the `validate` section, and prose edits in two skills.

## 9. Design clarifications and unresolved decisions

Legend: **R** resolved by existing code/design · **P** proposed implementation decision · **Q** unresolved, needs your approval.

| # | Topic | Status | Resolution or question |
|---|---|---|---|
| 1 | Requirement IDs | **R** | `r1`, `r2`… are evaluation-local. No link, ledger event or other file references them; ledger events reference only the `evl_…` id and the job id. IDs only need uniqueness inside one record (`EVL002`). |
| 2 | Claimed-only support | **R** | Outcome derivation treats `claimed`, `confirmed` and `verified` identically for credit; `status_floor` is carried into the basis and only the confidence rule (>½ of covered/adjacent weight claimed-only) reads it. Tested in B and C. |
| 3 | Direct vs adjacent links | **P** | A link is valid only if it names an existing **active** fact (else `EVL005`). A link can upgrade `gap` to `adjacent` and nothing more; `strength` is recorded for reviewers but does not change the outcome. A `direct` label cannot force `covered` because `covered` is reserved for deterministic matches (§5 of the spec). |
| 4 | Rubric parsing | **P** | Specified in Task B: parse only the table under `## Scoring rubric`, above the fold; unknown weight word, empty label or duplicate label drops the row and marks the rubric `invalid`, which omits fit and lowers confidence; the reason is recorded in the result and shown by `validate` as part of `EVL008`'s message when it matters. No new error code. |
| 5 | Floor and sponsorship sourcing | **Q** | The spec has the agent copy the floor and `needed` from the matching `## Markets` row. Code could cross-check them, but the Markets cells are free text (`TBD — derive…`, a number with a symbol). **Recommendation:** add one agent field `job_facts.market` naming the Markets row, validate (as `EVL006`) that the row exists, and where the row's floor cell parses as a number+currency require the agent's `floor` to equal it (a conflict is `EVL006`); an unparsable cell is accepted with an action item "floor not machine-readable". Alternative: trust the agent entirely (no `market` field). Please choose. |
| 6 | Recomputing history | **Q** (recommended **P**) | The spec says `validate` recomputes "from the inputs represented by the stored stamps", but a hash cannot be re-derived once memory or the rubric changed. **Recommendation:** persist `result.basis` — exactly the inputs of the pure scorer (per-requirement outcome/facts/status floor, fit rows with weights and judgements, the cap inputs, counts, rubric state, rules version) — and stamp its hash. The scorer is a pure function of the basis, so `validate` can verify scores, tier, caps, confidence, actions and body against the stored basis at any time, and verify the basis itself against current inputs whenever the memory/rubric/inputs stamps are current. This adds one field to the spec's record and one stamp (`basis`). Needs your approval because it extends the spec's stamp list. |
| 7 | Tamper vs stale | **P** | Order in Task D: structure → self-consistency of the stored result against its own basis (tamper) → freshness stamps (stale) → derivation check only when all inputs stamps are current. A difference is never assumed to be tampering: a result consistent with its own basis but with stale stamps is simply stale. If both apply both are reported; `evaluate` logs `result_replaced` with reason `tampered`, `stale` or `tampered,stale`, then `completed`, in one batch. |
| 8 | File/ledger atomicity | **Q** | Strongest guarantee the existing APIs give: write the file, append the ledger batch, and on any exception restore the file (`Transaction`) while `append_events` restores the ledger — the pair commits together or neither changes **for any raised exception**. A hard kill between the file write and the ledger append (power loss, SIGKILL) can leave a new file with no event; the existing `transition` and memory operations have the same window. **Recommendation:** accept it, but make it detectable: put `sha256:<result hash>` in `evaluation.completed.artifacts` and add warning `EVL009` "the evaluation file differs from the last `evaluation.completed` event for this record". This adds one warning code beyond the spec's list. Alternatives: accept silently (spec as written), or write the ledger first (leaves an event claiming a result the file lacks). Please choose. |
| 9 | Body ownership | **P** | The body is a pure function of `result` (`render_body`, fixed template, no timestamps). `validate` recomputes it and any difference is `EVL007` (message names "body"). Hand edits to the body are therefore tamper, and `evaluate` replaces it. |
| 10 | Rules version | **P** | `rules.py` keeps every released version in `RULES`; the stored `stamps.rules` selects the constants for recomputation (so old results stay verifiable) and a difference from the current `RULES_VERSION` is `EVL008`. A stored version this build does not know is an error ("written by a newer CareerOS"). |
| 11 | `evaluation.rejected` needs a reason | **P** | Add the type to `ledger._NEEDS_REASON` (the reason carries the failing `EVL` codes). |
| 12 | `--init` and events | **P** | `--init` writes only a header skeleton and appends no event (nothing has been judged). |
| 13 | Jobs without `jd.md` | **P** | Not an error by itself. `EVL004` fires only when an `evaluation.md` exists and its quotes cannot be checked. |
| 14 | Version | **P** | Release as `0.5.0` in Task F. |

**Decisions I need from you before Task A:** items **5**, **6** and **8** (each has a recommendation). Everything marked **P** will be implemented as written unless you object.

## 10. Final acceptance checklist

- [ ] Section 9 items 5, 6 and 8 decided and the spec amended for any chosen extension (`basis` stamp; `market` field; `EVL009`).
- [ ] Tasks A-G complete, each with a passing focused test file and a commit; every task independently reviewed.
- [ ] Full suite green on Python 3.14 and 3.11 (591 existing + the new tests); no existing test weakened.
- [ ] `careeros validate` clean on a workspace with a fresh evaluation; reports `EVL007` on a tampered one and `EVL008` on a stale one; never writes.
- [ ] `evaluate` idempotent; rejection logged once per identical attempt; ledger-failure injection leaves the file and ledger unchanged.
- [ ] `evaluate` demonstrably leaves `career/`, job `status` and `jd.md` untouched.
- [ ] Skill wording tests green; `prep` keeps the evidence gate; no unconditional `transition`; GPT workspace untouched; refresh delivers `evaluate`.
- [ ] `mkdocs build --strict` exit 0; wheel contains the package and the new skill; `git diff --check` clean.
- [ ] Acceptance record in `docs/END_TO_END_TEST.md` states counts, tier, exit codes and codes only; the real workspace was not touched.
- [ ] PR opened for review; not merged by the implementer.
