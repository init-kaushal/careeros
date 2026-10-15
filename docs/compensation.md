# Job compensation

Each saved job can carry one compensation record, `jobs/discovered/[job-slug]/compensation.md`. It holds the pay figures the agent found, each with its kind, source, date and confidence, and the currency conversions the agent was given. `careeros comp` checks it and shows what it means. `careeros evaluate` reads it as the only source of a job's pay, so the pay cap rests on recorded facts and not on a figure re-typed into each evaluation.

Pay is a fact about the job, so the file is agent-owned: the `browse` skill fills it when it saves a job, and the `evaluate` skill reads it. Code never rewrites it. Nothing is fetched or inferred: no live exchange rate, no guessed figure.

```bash
careeros comp example-corp-platform --init   # write the skeleton (refuses if the file exists)
careeros comp example-corp-platform          # exit 0: valid; shows the derivation
careeros comp example-corp-platform --json   # the same, machine-readable
```

## The record

`compensation.md` is YAML frontmatter followed by free notes that code ignores. This example is invented.

```markdown
---
schema: 1
market: "Atlantis (home)"
observations:
  - id: c1
    kind: posted
    low: 3000000
    high: 4200000
    currency: INR
    basis: base
    period: year
    source: "posting text"
    quote: "INR 3,000,000 to 4,200,000 per year"
    as_of: 2026-10-10
    confidence: high
  - id: c2
    kind: estimate
    low: 2800000
    high: null
    currency: INR
    basis: total
    period: year
    source: "salary survey"
    as_of: 2026-09-20
    confidence: low
conversions: []
---
Free notes.
```

| Field | Meaning |
|---|---|
| `schema` | `1` |
| `market` | the exact label of the profile's `## Markets` row (including a `(home)` suffix), or `null` only when `observations` is empty; it must equal the evaluation's `job_facts.market` once there are observations |
| `observations` | the pay figures, possibly `[]` |
| `conversions` | dated exchange rates, `{from, to, rate, source, as_of}`, only for a currency that differs from the floor's |

Each observation:

| Field | Rule |
|---|---|
| `id` | any non-empty text, unique within the file |
| `kind` | `posted`, `recruiter` or `estimate` |
| `low`, `high` | whole numbers in whole currency units (ten lakh is `1000000`). `low` is zero or more and `high` is at least `low` when both are given. An undisclosed side is written `null`, never omitted and never read as zero; an explicit `0` is valid |
| `currency` | a currency code such as `INR` |
| `basis` | `base` or `total` |
| `period` | `year` or `month` |
| `source` | where the figure came from; required |
| `quote` | required for `posted`: words copied verbatim from `jd.md` (whitespace and line breaks may differ) |
| `as_of` | the date the figure was seen, `YYYY-MM-DD` |
| `confidence` | `high`, `medium` or `low`: the agent's view of how reliable the source is. Nothing treats it as proof that the numbers are right |

A conversion's `rate` is a positive decimal written as a quoted string (`"83.20"`). Two conversions for the same `from` and `to` pair are an error even when the rates match. Unknown keys anywhere are an error. `observations: []` is valid and honest: no pay information. A job with no `compensation.md` is treated as the same empty record.

## Kinds

| Kind | What it is | Evidence |
|---|---|---|
| `posted` | the employer's own words | a `quote` found verbatim in `jd.md` |
| `recruiter` | a person's statement | a `source` and a date |
| `estimate` | a third-party guess (Levels.fyi, Glassdoor) | a `source` and a date |

The ranking is fixed by the rules, not by the agent's confidence. An estimate is useful context and is never enough on its own to cap a job.

## How the figures are derived

`careeros comp` and `careeros evaluate` run the same pure function. It uses exact fractions, never the current date and never a live rate. For each observation it works in this order:

1. **Annualise.** `period: month` multiplies both bounds by 12.
2. **Convert.** When the currency differs from the floor's, use the one conversion whose `from` and `to` are exactly the observation's currency and the floor's. No inverse rates, no chaining through a third currency.
3. The original amount, currency, period and source are always kept beside the converted figure.

Each observation is then comparable or carries one reason code (the first that applies, in the order of this table), and a not-comparable observation never contributes to a comparison or a cap:

| Reason | When |
|---|---|
| `FLOOR_UNVERIFIED` | the record's `market` row has no machine-readable floor (for example `TBD`), or the Markets table is unusable |
| `BASIS_MISMATCH` | the observation's `basis` differs from the floor's (total pay against a base floor) |
| `FX_CONVERSION_MISSING` | the currencies differ and there is no matching conversion |
| `FX_RATE_OUTSIDE_WINDOW` | the conversion is dated after the observation or more than 30 days before it |

A missing conversion is an information gap and the record stays valid.

### The rate window

A conversion applies only if it is dated no later than the observation and no more than 30 days before it (`0 <= observation.as_of - rate.as_of <= 30` days passes). A rate dated after the observation reads as not comparable, which is the safe side: record a rate dated near the observation. The window makes the result depend only on the stored dates, never on today.

### The effective range

- The **effective kind** is the highest kind present among all observations (`posted`, then `recruiter`, then `estimate`), comparable or not. If none of that kind is comparable there is no effective range, and the evaluation does not fall back to a lower kind.
- Among the comparable observations of that kind the effective range is their union: the lowest `low` and the highest `high`, where a `null` bound leaves that side open.
- Those observations must share a common intersection (closed intervals; ranges that touch at one point intersect; an open side is unbounded). If they do not, the kind is in **conflict**: there is no effective range and an action item asks you to resolve it. For example `30–40`, `35–45` and `42–50` conflict, because the first and last never meet even though neighbours do.
- An empty record, an all-incomparable effective kind and a conflict all give no effective range. Unknown stays unknown: the evaluation keeps its unknown-pay confidence deduction, and an empty record is never evidence that pay is below the floor.

## The compensation cap

The evaluation's `compensation` cap (ceiling `weak`) fires only when all of these hold:

- the effective kind is `posted` or `recruiter`;
- every observation of that kind is comparable, and none has `low` confidence;
- the figures do not conflict;
- the floor is verified;
- the effective `high` is not `null` and is strictly below the floor.

Because the union uses the highest top, the cap fires only if even the best case misses the floor. The edge cases:

1. **Equality does not cap.** A top equal to the floor is not below it.
2. **An open top never caps.** A `null` `high` is unbounded.
3. **An estimate-only range never caps.** It adds the action item "pay is estimated only".
4. **Any `low` confidence in the effective kind prevents the cap.**
5. **A conflict prevents the cap** and adds an action item.
6. **An incomparable observation of the effective kind, or an unverified floor, prevents the cap**, and an action item names the reason code.

## Validation codes

`careeros validate` reports these for every `compensation.md` and never changes a file.

| Code | Severity | Meaning |
|---|---|---|
| `CMP001` | error | unreadable file, no or invalid frontmatter or YAML, unsupported `schema`, a missing or unknown key |
| `CMP002` | error | wrong type, unsupported enum value, bad ISO date, empty required text, or `market: null` while there are observations |
| `CMP003` | error | range rule broken: a missing bound, a non-integer or boolean amount, `low` below zero, `high` below `low` |
| `CMP004` | error | a `posted` quote is missing, not found verbatim in `jd.md`, or `jd.md` is absent |
| `CMP005` | error | a duplicate observation `id`, or two conversions for one pair |
| `CMP006` | error | a missing `source` or `as_of`, or a missing or invalid `rate` |
| `CMP007` | error | a non-null `market` names no row of the Markets table, or a row listed more than once |
| `CMP100` | warning | an observation's `as_of` is more than 90 days old; informational, it never changes a score |

A `market` of `null` is valid only with `observations: []`. A Markets row without a machine-readable floor and an unusable table are not errors: the floor is unverified and the observations are simply not compared.

## The `comp` command

`careeros comp <job> [--init] [--json]` reads `compensation.md`, validates it, reads the floor of the record's `market` from `profile.md`, derives, and prints the observations (original and converted figures, reason codes), the effective kind and range, conflicts, whether the cap would fire and the action items.

It never edits `compensation.md`. Its only writes are ledger events:

- `compensation.recorded`, when the record is valid and its canonical hash differs from the latest valid `compensation.recorded` event for that job. It carries `artifacts: [path, "sha256:<canonical record stamp>"]` and an action with the observation count. A missing or malformed earlier event counts as no baseline, so the event is appended.
- `compensation.rejected`, when the record is invalid. It carries the sorted `CMP` codes and the SHA-256 of the file's bytes, and is skipped only when the job's latest `compensation.*` event is a rejection with exactly that pair, so a changed invalid file is recorded again. A job with no `compensation.md` appends nothing.

If the ledger cannot be written the command says so and never claims the event was recorded.

| Exit | Meaning |
|---|---|
| 0 | the record is valid |
| 1 | the record is invalid (including a `market` that is unknown or ambiguous), `--init` found an existing file, or the ledger failed |
| 2 | a usage problem or no workspace |

`--init` refuses when the file exists and never writes an event. `--json` prints one JSON object once the workspace is found.

## Evaluation version 2

Evaluations now read pay from `compensation.md`, and the evaluation basis and rules are version 2.

- `job_facts.compensation` is gone from the agent half and is rejected (`EVL003`). `job_facts.market` must equal the record's `market` once the file has observations, otherwise `EVL006`.
- The floor comes from the verified `## Markets` row by code, so there is no agent copy that could conflict with it.
- The basis stores the effective kind and range, the comparability and reason codes, the conflict and confidence flags and the floor with its source cell, so a result stays reproducible.
- A sixth freshness stamp, `compensation`, hashes the parsed record. A changed figure makes the evaluation stale (`EVL008`).
- **One-time stale after upgrade.** Evaluations written by 0.5.0 are version 1. They stay verifiable and are never reported as tampered, but they are read once as stale because their versions are older. Re-evaluation is explicit: `validate` and `comp` never re-evaluate anything, and `careeros evaluate` on a version 1 record that still has the pay block is refused until it is migrated.

## Migrating a 0.5.0 evaluation

The `evaluate` skill performs the migration; no code writes a file for it. It applies to a version 1 evaluation whose agent half still has `job_facts.compensation`, and is four ordered steps:

1. **Write** `compensation.md` with `careeros comp <job> --init` (only when none exists) from the legacy block. Version 1 recorded no `kind` and no `period`, so the skill asks for them (suggesting `estimate`; `posted` needs a verbatim quote). It never invents a source, an `as_of`, a confidence, a currency or a basis, shows the proposed file and writes it only after you agree. The notes of the new file keep the legacy block verbatim under the heading `Migrated from evaluation.md (0.5.0)`.
2. **Validate** with `careeros comp <job>`; on a `CMP` error it stops and asks you to fix the file.
3. **Remove** the legacy block, only when the record validates, and only after you confirm the record covers it.
4. **Re-evaluate** with `careeros evaluate <job>`.

The skill re-detects the state from the files on every run and performs only the steps whose precondition holds, so an interruption is recovered by running it again. It never overwrites an existing `compensation.md` and never removes the block before a valid record exists. If the block is already gone but the old version 1 result is still stored, it can rebuild the observation from the result's stored range, currency, basis and `as_of`. The refusal from `careeros evaluate` names the situation: no `compensation.md` yet, a file with errors, a valid file with no observations, or a valid file that needs your confirmation.

## Limits

- **Observations and rates are supplied by the agent.** A mistaken or dishonest source is not detectable.
- **A verbatim quote proves the text exists in `jd.md`, not that it is pay.**
- **Conversion is directional per record.** There are no inverse rates and no chaining.
- **A rate dated after the observation reads as not comparable.** That is the safe side; ask for a rate dated near the observation.
- **The cap depends on a verified `## Markets` floor.** Without one nothing is capped.
- **Some forgeries are caught only by the ledger check.** A forged compensation stamp, a basis forged while a stamp is genuinely stale, a downgrade to a version 1 result, and a basis that cannot be derived while `compensation.md` has an error are caught only by `EVL009`, and `careeros evaluate` heals them. The pay record has no tamper layer of its own.
- **Out of scope:** live exchange rates, market floors and benchmarks, offer-stage pay (equity, bonus, negotiation), tax and net-pay modelling, and the GPT Work workspace.

Run `careeros upgrade` in an existing workspace to receive the updated `browse` and `evaluate` skills (it backs up first).
