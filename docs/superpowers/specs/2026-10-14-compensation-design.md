# Structured compensation (sub-project 3b) — design

**Date:** 2026-10-14
**Status:** drafted for review
**Builds on:** Foundation 0.3.0, Career Memory 0.4.0, Evaluation 0.5.0 (`careeros evaluate`, the evaluation record, basis/rules versions, freshness stamps, `## Markets` parsing).
**Source:** `docs/IMPLEMENTATION_AUDIT.md` §10, sub-project 3. Evaluation (3a) shipped in 0.5.0 with the agent supplying pay figures "as `browse` records them today". This spec is **per-job compensation facts** only. Market floors/benchmarks, offer-stage compensation and the extended approval system are separate later specs.

## 1. Goal

Today a job's pay is prose in `browse` output and, inside an evaluation, a block the agent re-types into `evaluation.md` (`job_facts.compensation`). The same figure can be typed differently per evaluation, carries only an agent-claimed confidence, and cannot be reused by anything else.

This sub-project adds **one compensation record per saved job** (`jobs/discovered/<slug>/compensation.md`) that:

- holds dated **observations** of different trust kinds (`posted`, `recruiter`, `estimate`), each with range, base/total, currency, period, source, `as_of` and confidence;
- holds the **currency conversions** the agent recorded (direction, rate, source, date);
- is validated deterministically (`careeros validate`, `careeros comp`);
- is turned into one **derivation** by a pure function (annualise, convert, pick the effective range, compare with the verified `## Markets` floor);
- is the **only** source of pay for `careeros evaluate`, whose compensation cap then rests on that derivation, not on a re-typed block.

## 2. Decisions (approved in brainstorming, 2026-10-14)

1. **Scope:** per-job pay facts first. Market floors/benchmarks and offer-stage compensation are out.
2. **Ownership:** `compensation.md` is the single source of pay for a job; evaluation reads it. `job_facts.compensation` leaves the evaluation agent half.
3. **Model:** a list of observations with a `kind`; trust differs by kind (§6).
4. **Currency:** the agent records dated conversions in the record; code converts with exact fractions; nothing is fetched or inferred (§5).
5. **Architecture:** the file is **agent-owned**; there is **no code-owned result** stored in it. A pure function derives everything; `careeros comp` and `careeros evaluate` both call it. (No tamper/stale layer for compensation; the evaluation record's basis stores the derived values so results stay reproducible.)
6. **Invariant:** comparability and caps depend only on the persisted observations, conversions, the verified floor and the versioned rules, never on the current date or a live rate source. Age against today is an informational warning (§8).

## 3. Alternatives considered

- **Mirror evaluation (agent half + code-owned result with tamper/stale/ledger checks).** Rejected: it stores derived data that can be hand-edited or go stale, and that layer was the costliest part of evaluation to get right.
- **Fold into the evaluation package (no separate command).** Rejected: pay could not be validated or shown without a full evaluation, so `browse` and later `offer` could not reuse it.
- **One figure per job.** Rejected: a posted range and a third-party guess would overwrite each other and carry only the agent-claimed confidence.
- **Same-currency-only comparison.** Rejected: most non-home postings would never reach the cap.

## 4. The record

`jobs/discovered/<slug>/compensation.md`: YAML frontmatter, optional free notes below. Code never rewrites it.

```yaml
---
schema: 1
market: "India (home)"          # exact ## Markets row label; must equal the evaluation's job_facts.market
observations:
  - id: c1
    kind: posted                # posted | recruiter | estimate
    low: 3000000
    high: 4500000               # either bound may be null (undisclosed side); never read as zero
    currency: INR
    basis: base                 # base | total
    period: year                # year | month
    source: "posting text"
    quote: "verbatim line from jd.md"    # required for posted
    as_of: 2026-10-12
    confidence: high            # high | medium | low
conversions:                    # only for a currency that differs from the floor's
  - {from: USD, to: INR, rate: "83.20", source: "...", as_of: 2026-10-10}
---
Free notes (ignored by code).
```

Field rules:

- **Amounts** are whole integers in whole currency units (ten lakh is written `1000000`). `low >= 0`; `high >= low` when both are present. A missing bound is written `null`, never omitted and never read as zero. An explicit `0` is valid. Amounts are in the declared `currency`, `basis` and `period`.
- **IDs** are any non-empty string, unique within the record (local only, no prescribed format).
- **Dates** are ISO `YYYY-MM-DD`. **Enums** are closed sets. Unknown keys anywhere are an error.
- **Evidence by kind.** Every observation needs a non-empty `source` and a valid `as_of`. `posted` also needs a `quote` found verbatim (after the evaluation's text normalisation) in `jd.md`. `recruiter` and `estimate` need no quote. `confidence` is the agent's assessment of how reliable the source is; nothing treats it as proof that the numbers are right.
- **Conversions:** `from`/`to` are currency codes, `rate` a positive decimal string, `source` non-empty, `as_of` a valid date. Two conversions for the same `(from, to)` pair are an error even if the rates match.
- **Empty record:** `observations: []` is valid and honest ("no pay information"). A job with **no** `compensation.md` is treated as the empty record.
- **Creation:** `careeros comp <job> --init` writes the skeleton and refuses if the file exists. `browse` and the `evaluate` skill fill it.

## 5. Derivation (pure function)

`derive_compensation(record, floor, jd_text_available, rules) → Derivation` has no I/O, no clock and no randomness. All arithmetic uses exact fractions; stored numbers use the existing `exact()` string form.

### 5.1 Per observation

1. **Annualise:** `period: month` multiplies both bounds by 12. Applied first.
2. **Convert** when the observation's currency differs from the floor's: use the single conversion whose `(from, to)` is exactly (observation currency → floor currency). No inverse rates, no chaining through a third currency, no live rate. Converted = annualised × rate.
3. The derivation always keeps the **original** amount, currency, period and source next to the converted figure.
4. Each observation is **comparable** or carries a stable reason code (it then never contributes to a comparison or a cap):

| Reason | When |
|---|---|
| `FLOOR_UNVERIFIED` | the `## Markets` row is missing, unparseable, ambiguous or the table is unusable |
| `BASIS_MISMATCH` | the observation's `basis` differs from the floor's (total pay against a base floor); never compared as equivalent |
| `FX_CONVERSION_MISSING` | the currencies differ and no `(from, to)` conversion exists |
| `FX_RATE_OUTSIDE_WINDOW` | the conversion's `as_of` is after the observation's `as_of`, or more than 30 days before it (`0 <= obs.as_of - rate.as_of <= 30` days passes) |

A missing conversion is an information gap, not a schema error: the record stays valid.

### 5.2 Effective range

- The **effective kind** is the highest kind present among *all* observations (`posted` over `recruiter` over `estimate`), whether or not they are comparable. If none of that kind is comparable there is **no effective range** (no silent fall-back to a lower kind).
- Lower kinds are shown as context and never override a higher one.
- Among the **comparable** observations of the effective kind, the effective range is their **union**: lowest `low`, highest `high`, a `null` bound making that side open (unbounded).
- **Consistency:** the comparable observations of the effective kind must share a **common intersection** (closed intervals; ranges that touch at one point intersect; an open side is unbounded). If the three-way (n-way) intersection is empty, the kind is in **conflict**: no effective range, and an action item. Example: `30–40`, `35–45`, `42–50` is a conflict because the first and last do not meet, even though neighbours do.
- A valid empty record, an all-incomparable effective kind or a conflict all give **no effective range**.

### 5.3 The compensation cap

Evaluation's existing `compensation` cap (ceiling `weak`) fires only when **all** hold:

- the effective kind is `posted` or `recruiter`;
- **every** observation of the effective kind is comparable, so a not-comparable one blocks the cap;
- no conflict, and every observation of that kind has confidence `medium` or `high` (any `low` prevents the cap);
- the effective `high` is not null and is strictly below the floor amount (equality does not cap; a null upper bound never caps);
- the floor is verified.

The union uses the highest high, so the cap fires only if even the best case misses the floor. An estimate-only range never caps; it adds the action item "pay is estimated only". A conflict adds "resolve conflicting `<kind>` pay figures". A not-comparable observation adds an action item naming its reason code.

### 5.4 Unknown stays unknown

With a floor present and **no effective range** (no observations, all incomparable, or a conflict) the evaluation keeps today's unknown-compensation confidence deduction (§6.5 of the evaluation design). An empty record is never evidence that pay is below the floor.

## 6. Why kinds differ

`posted` is the employer's own words, so it needs a verbatim quote. `recruiter` is a person's statement with a `source` and date. `estimate` is a third-party guess (Levels.fyi, Glassdoor): useful context, never enough on its own to cap. The ranking is fixed by the rules version, not by the agent's confidence.

## 7. `careeros comp <job> [--init] [--json]`

- Resolves the job, reads `compensation.md`, validates it (§8), reads the `## Markets` floor for the record's `market` (reusing evaluation's market parsing), derives, and prints the derivation: observations with original and converted figures, reason codes, effective kind/range, conflicts, cap eligibility and action items. `--json` prints the same as one JSON object once the workspace is found (usage errors before that print plain text on stderr).
- **It never edits `compensation.md`**, and a successful derivation never repairs or normalises malformed YAML.
- **Its only writes are ledger events** (reserved prefix `compensation.`):
  - `compensation.recorded` `{file_sha256, observations}` when the record validates and its canonical hash differs from the latest **valid** `compensation.recorded` event **for that job**. A missing or malformed prior event counts as no baseline.
  - `compensation.rejected` (needs a reason) when the record is invalid, carrying the sorted CMP codes and the SHA-256 of the file's raw bytes. It is deduplicated on exactly (raw-bytes hash, sorted code set), so a changed invalid file is never suppressed by an earlier rejection.
  - If the ledger append fails the command reports it and never claims the event was recorded.
- **Exit codes:** 0 valid; 1 invalid record, an unknown `market` row, or ledger failure; 2 usage / no workspace.
- `--init` refuses if the file exists (exit 1) and never writes an event.

## 8. Validation

`careeros validate` stays read-only. It adds, for every `jobs/*/*/compensation.md` that exists:

| Code | Severity | Meaning |
|---|---|---|
| `CMP001` | error | unreadable file, no/invalid frontmatter or YAML, not a mapping, unsupported `schema`, unknown key |
| `CMP002` | error | wrong type, unsupported enum, bad ISO date, empty required text |
| `CMP003` | error | range invariant: non-integer or boolean amount, `low < 0`, `high < low` |
| `CMP004` | error | a `posted` quote is missing, or not found verbatim in `jd.md`, or `jd.md` is absent |
| `CMP005` | error | duplicate observation `id`, or two conversions for the same `(from, to)` pair |
| `CMP006` | error | missing `source` or `as_of` (observation or conversion), or missing/invalid `rate` |
| `CMP007` | error | `market` names no row of the Markets table, or an ambiguous one |
| `CMP100` | warning | an observation's `as_of` is older than the freshness threshold (rules constant, 90 days) relative to today; informational, never changes a score |

Malformed records produce these stable codes without modifying any file. Final code numbering is fixed in the plan.

## 9. Evaluation integration (versioned)

- **Agent half:** `job_facts.compensation` is removed (the key is rejected by `EVL003` for new records). `job_facts.market` stays and must equal `compensation.md`'s `market` when that file has observations, otherwise `EVL006`. Sponsorship stays in the agent half.
- **Floor:** taken directly from the verified `## Markets` row by code (no agent copy, so the floor "conflict" state disappears). Row unparseable ⇒ unverified.
- **Basis:** `basis_version` and `rules_version` go to `2`. The basis stores the derivation inputs and outputs needed to reproduce the cap: the effective kind and range (exact strings), comparability and reason codes, conflict and confidence flags, and the floor with its source cell and row state. The scorer stays pure and reads only the basis.
- **Stamps:** a new `compensation` stamp, the SHA-256 of the canonical parsed record (a fixed constant for "no file"). The existing `inputs` stamp keeps covering everything else that feeds the cap (the plan must confirm it still covers the Markets row cell). A changed figure makes the evaluation `stale`.
- **Version 1 records** stay verifiable (never tamper, never `EVL010`): they report `stale` (EVL008) because their versions are older. Their historical basis is recomputed with the version 1 rules, which remain in the code.
- **Re-evaluation is explicit.** `validate` and `comp` never re-evaluate anything. `careeros evaluate <job>` on a version 1 record needs a v2 agent half; the skill guides the migration (§10).

## 10. Skills

- **`browse`:** when it saves a job it also runs `careeros comp <job> --init` and records the pay it gathered as observations (kind, source, as_of, confidence), or leaves `observations: []` when it found nothing. Its comp-check rules (a source and a confidence for every figure; "unknown" is a valid answer) stand. The results table keeps its Comp column.
- **`evaluate`:** no longer asks for pay. It runs `careeros comp <job>`, reads the derivation, presents comparability reasons, conflicts and the cap outcome from the evaluation, and performs the **migration** below when it finds a version 1 record.
- **Migration (skill step, never part of the scorer; the scorer never writes files).**
  - Only for a version 1 evaluation whose agent half has `job_facts.compensation`, in a job with **no** `compensation.md`. It never overwrites an existing `compensation.md`.
  - It maps `range`, `currency`, `basis`, `source` and `as_of` to one observation and preserves the original figures and provenance. Version 1 recorded no `kind` and no `period`: it asks the user for the kind (suggesting `estimate`; `posted` also needs a verbatim quote) and for the period if it cannot tell. It never invents a missing source, `as_of`, confidence, currency or basis; anything it cannot map is left unresolved and asked about.
  - It is visible: it shows the proposed file and writes it only after the user agrees, then removes the old block from the agent half and re-evaluates **only after** the new record validates.
  - **Idempotent:** a repeat run finds the existing `compensation.md` and does nothing; unchanged inputs after a successful migration are a `(unchanged)` evaluation.
- `prep`, `offer`, the GPT workspace and the Career Memory evidence gate are unchanged.

## 11. Docs and release

`docs/compensation.md` (record, kinds, derivation, cap, FX window, codes, commands, migration, limits), README, CHANGELOG ("0.6.0: run `careeros upgrade` to receive the updated skills; existing evaluations read as stale once and need an explicit re-evaluation"), mkdocs nav, version `0.6.0`.

## 12. Testing

Written first (RED), pure-function tests need no workspace:

- **Range invariants:** `low >= 0`, `high >= low`, `null` bounds never read as zero, explicit `0` valid, bools and floats rejected.
- **Normalisation order:** month ×12 first, then conversion, exact fractions, originals preserved.
- **Conversions:** exact-pair selection; duplicate pair is an error even with equal rates; missing pair ⇒ not comparable with `FX_CONVERSION_MISSING`; rate dated after the observation, one day inside and one day outside the 30-day window.
- **Overlap/union/conflict:** the three examples (`30–40`+`35–45` union `30–45`; `30–35`+`40–45` conflict; `30–open`+`40–45` union `30–open`), points that touch, and the **chain** `30–40`/`35–45`/`42–50` (conflict).
- **Effective kind:** highest present kind wins even when incomparable (no fall-back); estimates never override.
- **Cap grid:** parity with today's single-figure behaviour (one `posted`/`recruiter` figure below a verified floor, medium/high confidence ⇒ the same `weak` cap as 0.5.0), plus: equality does not cap; null `high` never caps; estimate-only never caps; any `low` confidence in the kind prevents the cap; a conflict prevents the cap and adds an action item; missing or unverified floor prevents the cap; an incomparable observation in the kind prevents the cap.
- **Unknown stays unknown:** empty record, all-incomparable and conflict keep today's unknown deduction.
- **Ledger:** `recorded` dedupes against the latest valid event for that job (malformed/missing prior ⇒ appended); `rejected` dedupes on (raw hash, sorted codes) and a changed invalid file is recorded again; failed append reports and claims nothing.
- **Read-only:** `validate` and invalid `comp` runs leave every workspace file byte-identical; `comp` never edits or normalises `compensation.md`.
- **Evaluation:** basis v2 reproduces the cap; the `compensation` stamp marks an evaluation stale; v1 records stay verifiable and report stale, never tamper; `job_facts.compensation` rejected in new records; market mismatch `EVL006`.
- **Skills:** wording tests pin the migration rules (never overwrite, ask for kind, idempotent, no scorer writes) and the unchanged evidence gate.
- **End to end** on invented data, and an **acceptance run in a disposable workspace** recording commands, exit codes and codes/counts only (no real postings, employers or figures; the real workspace is never touched).

## 13. Out of scope and known limits

- **Out of scope:** live FX lookup, market floors/benchmarks, offer-stage compensation (equity/bonus/negotiation), tax and net-pay modelling, approvals (next sub-project), bulk recompute across jobs, GPT workspace parity.
- **Known limits:** observations and rates are agent-supplied, so a mistaken or dishonest source is not detectable; a verbatim quote proves the text exists in `jd.md`, not that it is pay; conversion is directional per record; a rate dated after the observation reads as not comparable (the safe side; the agent re-dates it); the cap still depends on a verified `## Markets` floor.

## 14. Build order

1. **A — primitives and record:** parse and validate `compensation.md` (CMP codes), range/enum/date rules, conversions; the `rules` constants.
2. **B — derivation (pure):** annualise, convert, comparability, effective range, conflict, cap eligibility and action items; cap grid and parity tests.
3. **C — `careeros comp` and ledger:** command, `--init`, events with dedupe, `validate` integration, reserved prefix.
4. **D — evaluation integration:** agent half change, floor from the Markets row, basis/rules v2 and the `compensation` stamp, v1 compatibility.
5. **E — skills, docs, version 0.6.0.**
6. **F — end-to-end test, acceptance run, release checks.**

Each task is one commit (plus fix commits), reviewed before the next.

## 15. Acceptance

A disposable invented workspace: create a job and `jd.md`, `comp --init`, record a `posted` figure below a verified floor, `evaluate` ⇒ `weak` via the compensation cap; change the figure above the floor ⇒ stale, re-evaluate ⇒ cap gone; foreign-currency figure with and without a conversion; a conflicting pair ⇒ no cap and an action item; a version 1 record ⇒ stale, migrated through the skill steps; `validate` and invalid `comp` leave the workspace byte-identical. Full suite green on Python 3.14 and 3.11, `mkdocs build --strict`, wheel contents, `git diff --check`.
