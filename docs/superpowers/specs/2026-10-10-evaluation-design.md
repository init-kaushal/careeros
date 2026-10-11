# Job evaluation (sub-project 3a) — design

**Date:** 2026-10-10
**Status:** drafted for review
**Builds on:** Foundation 0.3.0 (IDs, ledger, workspace lock, atomic writes, validation, state machine) and Career Memory 0.4.0 (`career/` facts, statuses, lexicon, the evidence check).
**Source:** `docs/IMPLEMENTATION_AUDIT.md` §10, sub-project 3 ("Evaluation, compensation, state machine and approvals"). That sub-project is split into three specs. This one is **evaluation** only. Structured compensation intelligence and the extended approval system follow as their own specs.

## 1. Goal

Replace the prose evaluations CareerOS has today (the 0–10 snippet score in `browse`, and the "map against profile" step in `prep`) with one **evaluation record per saved job** that:

- lists the job's requirements, each with a verbatim quote from the posting;
- links each requirement to the **career facts** that support it, derived by code from the facts' own content and status, never trusted from the agent's label;
- computes a coverage score, a fit score, a tier, a confidence level, the caps that applied, and a "what would change this" list, deterministically;
- is stamped so a change to the posting, the memory, the rubric or the rules makes it visibly stale;
- decides the tier (triage: should this job get more of your time) **and** feeds `prep` and the cover-letter flow (the same map, so two analyses of one job cannot disagree).

**Success looks like:**

1. For an invented job and memory, `careeros evaluate` produces the tier, scores and confidence that the tables in §6 predict, and re-running changes nothing.
2. A requirement is `covered` only when a fact that actually names it supports it; a technology mentioned in an unrelated fact does not count; "5+ years of Go" is `unverifiable`, never silently passed.
3. Disputed and retired facts never support anything; claimed-only support lowers confidence, not credit.
4. A posting that is silent about sponsorship or pay never triggers a cap; an established mismatch always does.
5. A hand-edited result is detected by `validate` and recorded in the ledger before it is replaced; a changed posting, memory, rubric, rules version or input half makes the record stale.
6. `prep` starts from a fresh evaluation; the evidence gate still governs every draft, and no verdict counts as proof of a resume claim.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| E1 | **One record per job** drives the tier and is reused by `prep`. | Two analyses of one job would disagree. |
| E2 | **The agent proposes, code verifies, a person decides.** The agent extracts requirements and judges fit signals, each with a verbatim quote. Code derives outcomes and scores. The tier is a recommendation; `transition` is a separate, explicit act. | Extraction needs judgement; everything after it can be reproducible. |
| E3 | **Two namespaces, one author each.** `agent:` is written only by the agent; `result:` and the body only by code. | Ownership is enforceable and checkable. |
| E4 | **Tamper evidence before repair.** `validate` (read-only) compares the stored result with the recomputation of its own stamped inputs and reports a mismatch. `evaluate` logs `evaluation.result_replaced` before it overwrites. | Silently overwriting a hand edit would destroy the evidence. |
| E5 | **A proposed link is not proof.** Outcomes come from the requirement's kind and term, the fact's own content and relationship, and its status. | Same principle as the evidence check. |
| E6 | **Status affects confidence, not credit.** `claimed` earns full credit with reduced confidence; `disputed` and `retired` never count. | Provenance is not truth (Career Memory D1). |
| E7 | **Unknown is not a mismatch.** A silent sponsorship or an undisclosed salary adds an action item; only an established mismatch caps the tier. | Silence is not a refusal. |
| E8 | **Unverifiable leaves the denominator.** Requirements the memory cannot decide (years with a technology) are listed as action items and lower confidence. | They are unknown, not failures. |
| E9 | **Freshness is reproducible.** Five documented hashes; any difference means stale. | A stale record must never read as current. |
| E10 | `browse` keeps its quick 0–10 snippet score. The evaluation is a separate, deeper step for saved jobs. | Snippets carry no full description. |
| E11 | Evaluation never changes job state and never touches memory. | State changes stay explicit and logged. |

## 3. Alternatives considered

- **A. Structured record plus a deterministic scorer (chosen).**
- **B. Prompt-only upgrade** (richer prose, a validator that checks shape only). Nothing verifies that a claimed match is real, which repeats today's gap. Rejected.
- **C. Fully automatic requirement extraction by keyword matching.** Deterministic, but it misses semantic requirements and would score confidently on the wrong things. Rejected.

## 4. Files and the record

Per job, in `jobs/discovered/<slug>/`:

- `jd.md`: the job description, saved **verbatim** by the agent. Quotes are verified against it and it is hashed for freshness.
- `evaluation.md`: YAML frontmatter with `agent:` and `result:`, plus a body rendered by code from `result:`.

### 4.1 Example (invented)

```yaml
---
id: evl_9k2m4q7xta
type: evaluation
schema: 1
job: job_3f8c2m1xqa
created_at: 2026-10-10T09:15:00Z
agent:
  updated_at: 2026-10-10T09:20:00Z
  requirements:
    - id: r1
      class: must                 # must | strong | nice
      kind: technology            # technology | title | certification | education | years | domain | other
      term: Kubernetes
      text: Production Kubernetes experience
      quote: "production Kubernetes experience"
    - id: r2
      class: must
      kind: years
      years: 5
      term: Go                    # present when the years are tied to a technology
      text: 5+ years of Go
      quote: "5+ years of Go"
    - id: r3
      class: strong
      kind: domain
      text: Owned an on-call rotation
      quote: "you will own the on-call rotation"
      links:
        - {fact: ach_h64tj5evkz, strength: adjacent, why: "led incident response at Globex"}
  fit:
    - {row: "Role title matches target roles", judgement: yes, quote: "Senior Platform Engineer"}
    - {row: "Location match or remote-friendly", judgement: partial, quote: "hybrid, Pune"}
  job_facts:
    sponsorship: {needed: no, listing: unknown}          # needed: yes|no (from profile Markets); listing: offered|not_offered|unknown
    compensation:
      range: {low: 3000000, high: 4200000, currency: INR, basis: base}
      floor: {amount: 3500000, currency: INR, basis: base}
      source: levels.fyi
      confidence: medium                                  # high | medium | low | unknown
      as_of: 2026-09
result:                                                   # code-written; never edit
  stamps: {jd: "9f2c41…", memory: "03ab7e…", rubric: "c81d90…", rules: "1", inputs: "5be2f6…"}
  requirements:
    - {id: r1, outcome: covered, facts: [exp_kkfe92m3zb, skl_a1b2c3d4e5], status_floor: claimed}
    - {id: r2, outcome: unverifiable, reason: "years with a specific technology are not derivable"}
    - {id: r3, outcome: adjacent, facts: [ach_h64tj5evkz], status_floor: claimed}
  coverage: 0.8
  fit: 0.8
  combined: 0.8
  caps: []
  tier: pursue
  confidence: low        # high, minus one step for claimed-only support, minus one for 37.5% unverifiable weight
  action_items:
    - "r2: cannot be derived from memory; ask the user, and add the fact with `careeros memory add` if true"
    - "r1: supported only by claimed facts (exp_kkfe92m3zb, skl_a1b2c3d4e5); ask the user to confirm them"
    - "r3: supported only by claimed facts (ach_h64tj5evkz); ask the user to confirm them"
---
```

### 4.2 Agent half (all fields validated)

- `requirements[]`: `id` (record-local, unique: `r1`, `r2`, ...), `class`, `kind`, `text`, `quote` (verbatim in `jd.md`), and for matchable kinds a `term` (technology name, title, certification or degree phrase the requirement names). A `years` requirement carries `years` (a number) and, when tied to a technology or domain, a `term`. Optional `links[]`: `{fact, strength: direct|adjacent, why}`.
- `fit[]`: one entry per **fit row** of the profile rubric, keyed by the exact row text: `judgement` (`yes | partial | no`) and a verbatim `quote` (or `quote: null` with `judgement: no` when the posting says nothing).
- `job_facts.sponsorship`: `needed` (from the matching `## Markets` row), `listing` (`offered | not_offered | unknown`), and a verbatim `quote` when `listing` is not `unknown`.
- `job_facts.compensation`: `range` (`low`, `high`, `currency`, `basis: base|total`), `floor` (`amount`, `currency`, `basis`) copied from the matching `## Markets` row, `source`, `confidence`, `as_of`. A posting with no figures says `range: null`. (Structured compensation is the next sub-project; here the agent supplies these exactly as `browse` records them today.)

The `evl_` ID, the job ID and `created_at` are written once by `careeros evaluate <job> --init` (§7), which creates the skeleton; the agent writes everything under `agent:` from then on.

### 4.3 Rubric parsing

The fit rows are the rows of the `## Scoring rubric` table in `profile.md` whose label does not mention compensation or sponsorship (those two are handled as caps, §6.4). Weight labels map `High = 3`, `Medium = 2`, `Low = 1`; a `Negative` row is never a fit row. If `profile.md` has no parseable rubric, the fit score is omitted (§6.2).

## 5. Deriving requirement outcomes

Code derives each outcome from the requirement, the facts, and their status. The agent's `links` and `strength` are inputs, never conclusions.

Only **active** facts (`claimed`, `confirmed`, `verified`) count. `disputed` and `retired` facts support nothing. Matching uses the Career Memory lexicon and normalisation, and **only the field that is relevant to the kind**:

| Kind | `covered` when | Otherwise |
|---|---|---|
| `technology` | `term` is a canonical technology in an active `skill`'s name, or in the `technologies` of an active experience, project or achievement | a valid agent link → `adjacent`; else `gap` |
| `title` | `term` equals (normalised) an active experience title or identity headline | link → `adjacent`; else `gap` |
| `certification` | `term` matches an active certification's name or alias | link → `adjacent`; else `gap` |
| `education` | the normalised words of `term` are all present in an active education fact's degree plus field | link → `adjacent`; else `gap` |
| `years` (no `term`) | `years` ≤ the whole years derived from the union of active dated experiences | `years` above that total → `gap` |
| `years` (with `term`) | never derivable | `unverifiable` |
| `domain`, `other` | never by code | a valid agent link → `adjacent`; else `gap` |

Rules that apply to every row:

- An agent link must name an existing, active fact in the same workspace; otherwise the record is invalid (`EVL005`).
- A technology mentioned somewhere else in memory does not establish an unrelated achievement, a duration, or a `domain` requirement.
- `facts` in the result lists the supporting fact IDs (at most five per requirement) and `status_floor` is the weakest status among them (`claimed` < `confirmed` < `verified`).
- A requirement whose kind cannot be interpreted confidently is recorded by the agent as `kind: other` and, if it cannot be linked, becomes a `gap`; an agent that cannot tell may mark it `kind: years` with a `term` to force `unverifiable`. The agent is never pushed to invent precision.

## 6. Scoring

All numbers are defaults in one versioned constant, `rules_version` (initially `1`), and are part of the freshness stamp. Arithmetic uses exact fractions; stored values are rounded to four decimals; thresholds are compared on the exact values.

### 6.1 Coverage (0–1)

Weights: `must` 3, `strong` 2, `nice` 1. Credit: `covered` 1, `adjacent` 0.5, `gap` 0. Requirements with outcome `unverifiable` are **removed from the denominator**. `coverage = Σ(credit × weight) ÷ Σ(weight)` over the rest. If nothing remains (no requirements, or all unverifiable) coverage is undefined and the tier is `unrated` with confidence `low`.

### 6.2 Fit (0–1)

Row weights from §4.3; judgement credit `yes` 1, `partial` 0.5, `no` 0. `fit = Σ(credit × weight) ÷ Σ(weight)` over the rows present. If the rubric is missing, fit is omitted, the combined score equals coverage, and confidence drops one step.

### 6.3 Combined score and tier

`combined = 0.65 × coverage + 0.35 × fit`.

| combined | tier |
|---|---|
| ≥ 0.70 | `pursue` |
| ≥ 0.50 | `stretch` |
| ≥ 0.30 | `weak` |
| < 0.30 | `skip` |

### 6.4 Caps

A cap applies only when its condition is **established**. The lowest applicable cap wins and every applied cap is listed with its reason.

| Established condition | Cap |
|---|---|
| `sponsorship.needed: yes` **and** `listing: not_offered` (with a verbatim quote) | at most `weak` |
| compensation `range.high` is below `floor.amount`, with equal `currency` and `basis`, and `confidence` is not `low` or `unknown` | at most `weak` |
| two or more `must` requirements are `gap` | at most `stretch` |

Not established, so **no cap**: `listing: unknown`; no `range`; a `currency` or `basis` mismatch; `confidence: low`. Each of these adds an action item ("ask about sponsorship before investing time"; "pay not disclosed, ask the recruiter").

### 6.5 Confidence

Start at `high`; step down once for each (floor `low`):

1. more than 50% of the weight of `covered` and `adjacent` requirements rests on `status_floor: claimed` facts;
2. more than 25% of must/strong weight is `unverifiable` (two steps above 50%);
3. a decision-relevant unknown remains (sponsorship needed but `unknown`; or compensation `range` null while a floor exists);
4. fewer than three requirements, or no `must` requirement (thin extraction);
5. the rubric is missing.

### 6.6 Action items ("what would change this")

Generated by code from the result, in a fixed order: unverifiable requirements (ask the user, add the fact with `careeros memory add` if true), `gap` musts, claimed-only support worth confirming, unknown sponsorship or pay. Each names the requirement or fact. No item is emitted for a sponsorship need that is verified `no`; an `unverified` need or floor always produces an item.

## 7. `careeros evaluate`

`careeros evaluate <job> [--init] [--json]` (a job ID, directory name or path).

- `--init` creates the skeleton `evaluation.md` (ID, job ID, `created_at`, empty `agent:`) and refuses if one exists. It writes no judgements.
- Without `--init`:
  1. Validate `jd.md` and the `agent:` half (§8). On failure: exit 1, write nothing, log one `evaluation.rejected` event per identical attempt (the failing codes in `reason`).
  2. Compute the result. Refuse (exit 2) when the career memory has errors.
  3. Under the workspace lock, write `result:` and the body atomically, then append `evaluation.completed` (entity `evl_…`, with tier, combined score, confidence; the job in `artifacts`). If the ledger append fails, the file is restored.
  4. If the inputs are unchanged and the result is identical, change nothing and log nothing.
  5. If the stored result differs from the recomputation of its own stamped inputs, log `evaluation.result_replaced` (reason `tampered`) before rewriting.
- It never changes job state and never writes to `career/`.
- Exit codes: 0 scored (any tier, including `unrated`); 1 invalid inputs; 2 usage error or a career memory that cannot be trusted. A stale result is reported but is not an error for `evaluate`, which is how it is refreshed.
- `--json` prints only JSON.

All `evaluation.*` ledger event types are reserved; only this command writes them.

## 8. Validation

Run by `evaluate` (§7) and by `careeros validate` for every job directory that has an `evaluation.md`.

| Code | Severity | Check |
|---|---|---|
| `EVL001` | error | unreadable file, invalid YAML, or a missing `agent:` / `result:` namespace |
| `EVL002` | error | missing or duplicate requirement IDs, or the record's `job` is not this job |
| `EVL003` | error | invalid `class`, `kind`, `judgement`, `strength`, `listing`, `basis` or `confidence`; a fit `row` that is not a rubric fit row |
| `EVL004` | error | a `quote` that is not found verbatim in `jd.md`, or `jd.md` missing |
| `EVL005` | error | a link to a fact that does not exist or is not active |
| `EVL006` | error | sponsorship `listing` other than `unknown` without a quote; compensation given without `source`, `confidence` and `as_of` |
| `EVL007` | error | the stored `result:` differs from the recomputation of its own stamped inputs (tamper); reported before any repair |
| `EVL008` | warning | stale: a stored stamp differs from today's input (posting, memory, rubric, rules, or the agent half) |
| `LED003` | error (extended) | a ledger entity with prefix `evl_` whose evaluation file is missing |

`validate` never writes. The `agent:` half is checked structurally; whether the agent extracted every requirement is a judgement the code cannot guarantee (§13).

## 9. Freshness

Five stamps are stored in `result.stamps`; the record is stale when any differs from the current value.

- **jd:** SHA-256 of `jd.md` after Unicode NFC normalisation, converting line endings to `\n`, collapsing runs of whitespace to one space and stripping the ends. Case and wording are kept, so a changed posting invalidates but reflowed formatting does not.
- **memory:** SHA-256 of the canonical JSON of every fact (including `retired` and `disputed`, sorted by id): `id`, `type`, `status`, the content fields for its kind, `parent`, and `supports`; timestamps and `confirmed_at` are excluded.
- **rubric:** SHA-256 of the canonical JSON of the parsed rubric fit rows and weights.
- **rules:** the `rules_version` string.
- **inputs:** SHA-256 of the canonical JSON of the `agent:` half (excluding `updated_at`).

Any change to memory marks every evaluation stale. That is intentional and cheap, because recomputing is deterministic and instant. A stale record is shown as stale by `validate`, by the `evaluate` skill and by `prep`; it is never presented as current.

## 10. Skills

- **New `evaluate` skill** (`.claude/skills/evaluate/SKILL.md`), triggered by "evaluate [job]" / "should I apply to [job]": save `jd.md` verbatim; run `careeros evaluate <job> --init` if needed; extract requirements with verbatim quotes and judge each fit row; record sponsorship and compensation as `browse` does; run `careeros evaluate`; fix and re-run on `EVL` errors; present the tier, scores, confidence, caps, gaps and action items; stop and ask before any `transition`. It moves a job to `EVALUATED` and then `SHORTLISTED` only with the user's yes, using `careeros transition`.
- **`prep`:** STEPs 2–3 now start from a fresh evaluation. If the record is missing or stale, `prep` runs the `evaluate` skill first. It leads the tailored resume and cover letter with `covered` requirements' facts, states `adjacent`, `gap` and `unverifiable` honestly, and never claims more than the facts say. The evidence gate (Career Memory) is unchanged and still checks every draft: a verdict is never proof of a claim.
- **`browse`:** unchanged; its closing line now suggests "say `evaluate <job>` for the deep check".
- `CLAUDE.md` routes the new trigger; `upgrade` delivers it. GPT Work is unchanged.

## 11. Ledger, IDs and integration

- ID prefix `evl` already exists; requirement IDs are record-local (`r1`, `r2`) because they are never referenced from the ledger.
- Reserved event types: `evaluation.completed`, `evaluation.rejected`, `evaluation.result_replaced`.
- `validate` gains `EVL001`-`EVL008`; `LED003` is extended to `evl_`.
- Every action logs, including failures and replacement (standing directive).

## 12. Testing

Failure paths first; all data invented.

- **Scoring:** fixture tables pin every number in §6: weights, credits, the `unverifiable` exclusion, tier boundaries at exactly 0.70, 0.50 and 0.30, `unrated`, missing rubric, each cap alone and combined (lowest wins, all listed), confidence steps.
- **Outcome derivation:** each row of §5, including: a technology named only in an unrelated fact does not cover; "5+ years of Go" is `unverifiable`; generic years against derived experience; a `direct` link on a `domain` requirement yields `adjacent`; `disputed` and `retired` facts never count; claimed-only support lowers confidence, not credit.
- **Unknown versus mismatch:** silent sponsorship, null range, currency or basis mismatch and `low` confidence never cap; established mismatches always do.
- **Validation:** every `EVL001`-`EVL006` case, including a non-verbatim quote, duplicate IDs, a link to another workspace's fact, a missing source.
- **Integrity:** `EVL007` for a hand-edited result (validate read-only; evaluate logs `result_replaced`); `EVL008` for a changed `jd.md`, a changed fact, rubric or rules version, and an edited `agent:` half; unchanged inputs write nothing; reflowed whitespace in `jd.md` does not invalidate.
- **Atomicity and guards:** a failed ledger append restores the file; the memory is refused when it has errors (exit 2); `evaluate` never changes job state or `career/`; `evaluation.*` types are refused by `ledger append`.
- **Skills:** wording tests that `prep` starts from a fresh evaluation, that the `evaluate` skill never transitions without asking, and that refresh delivers both.
- **End to end:** invent a job and memory, extract, evaluate, shortlist, change a fact, see the record go stale, re-evaluate.

## 13. Out of scope and known limits

- Structured compensation intelligence (the next spec); here the agent supplies the pay figures as it does today.
- Approvals beyond `APPLIED`, GPT Work parity, changes to `browse`'s quick score, bulk evaluation across many jobs.
- **Extraction is the agent's judgement.** Quotes make it reviewable, but code cannot guarantee that every requirement was found or read correctly. Thin extraction lowers confidence (§6.5).
- Defaults such as 0.65/0.35 and the cut-offs are tunable through `rules_version`; changing them marks old records stale.
- A whole-memory fingerprint over-invalidates; accepted.

## 14. Acceptance

On a copy of the real workspace (never the original): evaluate one saved job end to end, check the record validates, change nothing else. The committed record states counts, tier, exit codes and codes only, with no posting text, employer or figure. The real workspace's checksums are compared before and after.

## 15. Build order

1. Rules constants, canonical hashing and the freshness stamps.
2. The outcome derivation and the scorer.
3. Record parsing and validation (`EVL001`-`EVL008`).
4. The `evaluate` command, `--init`, ledger events, `validate` integration.
5. Skills (`evaluate`, `prep`, `browse` closing line), docs, acceptance.

## 16. Decisions approved after plan review (2026-10-10)

These amend sections 4, 6, 8 and 9. Where they differ from earlier text, **this section wins**.

### 16.1 Floor and sponsorship are sourced from the `## Markets` row

- The agent adds `job_facts.market`: the label of the matching row in the `## Markets` table of `profile.md`. The table keeps its existing free-text format; there is no schema migration.
- **Row existence.** The label is compared after NFC normalisation, whitespace collapsing and case folding. A `market` that names no row is an input error (`EVL006`).
- **Floor.** The row's "Min base" cell is *parseable* only when it contains exactly one numeric amount (using the same number scanner as the evidence check, including `k`, `lakh`, `crore` and `M` suffixes) and exactly one currency indicator (a symbol or ISO code). Anything else (`TBD — derive from home floor`, a range, two figures, no currency) is *unparseable*. The column is a base figure, so a parsed floor always has `basis: base`.
- **Sponsorship need.** The row's "Sponsorship" cell establishes the need only when it says exactly `required` (need = yes) or `not needed` (need = no), case-insensitively. `preferred`, anything else, or an empty cell leaves the need *unknown*. Nothing is inferred from nationality, location or any other field.
- **Verification states** recorded in the result for each of floor and need: `verified` (the row establishes it and the agent's value agrees), `unverified` (the row is unparseable/ambiguous; the agent's value is kept for display only), or `conflict` (the row establishes a value and the agent's differs).
- **A cap uses only verified inputs.** The compensation cap needs a verified floor (and equal currency and basis with the range, and range confidence not `low`/`unknown`). The sponsorship cap needs a verified need of `yes`, a listing of `not_offered` and a quote. An unverified or conflicting input never produces a cap. A conflict is never resolved in favour of the more favourable value; it adds an action item ("resolve the profile/agent mismatch") until the agent's value and the row agree.
- **Auditability.** The basis (§16.2) stores the market label and the exact source cell text used for floor and sponsorship.
- A conflict or an unverified input does not stop scoring (exit 0); it is reported as a result field, an action item and the validation warning `EVL011`.
- **Confidence step 3** is restated: a decision-relevant unknown remains when (need is `yes` or unknown and listing is `unknown`), or (the range is null and a verified floor exists).

### 16.2 A historical basis separate from the freshness stamps

- `result.stamps` (`jd`, `memory`, `rubric`, `rules`, `inputs`) answers "is this evaluation current?".
- `result.basis` answers "can the recorded result be reproduced?". It holds the exact normalised inputs of the pure scoring function: per requirement `{id, class, outcome, facts, status_floor}`; per fit row `{weight, judgement}`; the cap inputs (sponsorship need and listing with their verification states, quote presence, the compensation range, floor, confidence, and the source cell text); `requirement_count`; `rubric_state`; and the two versions `basis_version` and `rules_version`. A sibling `result.basis_sha256` is the hash of its canonical form.
- **Canonical form.** Keys sorted, UTF-8, `ensure_ascii=False`, `allow_nan=False`. Scoring inputs contain no floating-point values: weights are integers; any non-integer quantity is a string fraction such as `"1/2"`. A float or non-finite number in a hashed structure is an error.
- Historical scoring is recomputed from the stored basis with the constants of the `rules_version` the basis declares, never from today's memory or profile. A result that disagrees with that recomputation (scores, tier, caps, confidence, action items, rendered body) is **tampering** (`EVL007`). `validate` reports it and never repairs.

### 16.3 Versions that can no longer be recomputed

`basis_version` and `rules_version` are independent of the current inputs. A record whose `basis_version` or `rules_version` this CareerOS no longer supports (or does not yet know) **cannot be verified**: that is the warning `EVL010` ("cannot be verified: unsupported version"), never `EVL007`. The record is also reported stale when its rules differ. `evaluate` always recomputes from current inputs with the current version and so replaces such a record without calling it tampered.

### 16.4 File/ledger consistency: `EVL009`

- `evaluation.completed` carries `artifacts: ["sha256:<result hash>"]` where the result hash is the SHA-256 of the **canonical form of the committed `result:` mapping** (§16.2 rules; the 4-decimal score fields are converted to fixed-point strings before hashing, the body being a pure function of the result).
- **Which event belongs to a record.** Only existing event metadata is used. The relevant events are those with `type == "evaluation.completed"` and `entity ==` the record's `evl_` id, ordered by `seq` (the hash-chain order, so no timestamps are compared); the **expected event is the one with the highest `seq`**. Earlier completions from earlier evaluations, `evaluation.rejected` events (their entity is the job id) and `evaluation.result_replaced` events (always written in the same batch as a completion) are ignored. A failed attempt writes no completion (the ledger is restored), so the latest completion still describes the restored file.
- **A well-formed completion** has exactly one artifact of the form `sha256:` plus 64 lowercase hex digits, and exactly one artifact that is this record's job-relative `evaluation.md` path.
- **Unique association.** The `evl_` id is generated by `--init` against every existing evaluation id and every `evl_` ledger entity, so it is unique per record. If two evaluation files in the workspace carry the same id (for example a copied job directory), or the latest completion's path artifact names a different file than the record being checked (the record was moved or copied), the event **cannot be uniquely associated** with this record.
- `EVL009` (warning, read-only) distinguishes these cases in its message: **missing** (a record with a `result:` and no completion event for its id); **malformed** (the expected event lacks a single well-formed hash or path artifact); **mismatched** (the expected event's hash differs from the SHA-256 of the file's current result, including a file hand-reverted to an older result); **ambiguous** (duplicate id across files, or the path artifact names another file). A skeleton that was never evaluated, and a job that was never evaluated, produce nothing.
- Ordinary exceptions still restore the previous file (and the ledger is restored by the append). A hard kill between the file write and the ledger append can still leave the pair inconsistent; `EVL009` **detects** that window, it does not remove it. `evaluate` heals it on the next run by writing a fresh `evaluation.completed`.

### 16.5 Code table additions

| Code | Severity | Meaning |
|---|---|---|
| `EVL009` | warning | the evaluation file's result is missing its `evaluation.completed` event, differs from it, the event is malformed, or the event cannot be uniquely associated with this record (§16.4) |
| `EVL010` | warning | the record cannot be verified because its `basis_version` or `rules_version` is unsupported (§16.3) |
| `EVL011` | warning | a floor or sponsorship need is `unverified` or in `conflict` with the `## Markets` row (§16.1) |

### 16.6 Defaults retained

A `direct` link can upgrade a gap only to `adjacent`; a malformed or duplicate rubric omits the fit score and lowers confidence; a hand-edited generated body is tampering; `--init` logs no event; the release is `0.5.0`.
