# Job evaluation

`careeros evaluate` tells you how well a saved job fits your career memory, and why. The agent reads the posting and records what it says, and the job's pay comes from its [compensation record](compensation.md); the command matches that against your facts, scores it and writes the result. The score is a triage tool. It is not a promise about the job and it is not evidence for anything you write: the [evidence check](career-memory.md) still governs every draft.

## How it works

1. **Save the posting.** The `evaluate` skill saves the description verbatim to `jobs/discovered/[job-slug]/jd.md`. Evaluations need a `jd.md`.
2. **Create the record.** `careeros evaluate <job> --init` writes `jobs/discovered/[job-slug]/evaluation.md` with a fresh id (`evl_...`) and an empty `agent:` half.
3. **The agent fills in `agent:`** (what it must supply is listed below).
4. **Score.** `careeros evaluate <job>` validates the agent half, matches every requirement against your active facts, scores, applies the caps, writes `result:` and the body, and records an `evaluation.completed` event in the ledger. Running it again with nothing changed writes nothing and prints `(unchanged)`.

The command changes only `evaluation.md` and the ledger. It never changes a job's state, `jd.md` or `career/`. Moving a job on is a separate step (`careeros transition`) that the skill takes only after asking you.

```bash
careeros evaluate example-corp-platform --init
careeros evaluate example-corp-platform            # exit 0: scored
careeros evaluate example-corp-platform --json     # the same, machine-readable
```

## The record

`evaluation.md` is a markdown file with YAML frontmatter.

| Key | Written by | Meaning |
|---|---|---|
| `id`, `type: evaluation`, `schema: 1`, `job`, `created_at` | `--init` | identity; `job` is the id in the job's `job.md` |
| `agent:` | the agent | the judgements below, plus `updated_at` |
| `result:` | the command | tier, scores, confidence, caps, action items, per-requirement outcomes, freshness stamps and the exact `basis` that was scored |

The body below the frontmatter is rendered from `result:` and nothing else: tier and scores, a requirements table, the caps and "what would change this". Do not edit `result:` or the body. A changed value is reported as tampering (`EVL007`) and the next `careeros evaluate` replaces it. The replacement is logged as an `evaluation.result_replaced` event, written together with the new `evaluation.completed`, whenever the old result was tampered with, was from an unsupported version, or no longer matches the ledger's last completion. Its reason lists `tampered`, `stale` and `ledger_mismatch` (comma-joined, in that order; `ledger_mismatch` means the file differs from the result the ledger last recorded and is not added when `tampered` already says so). A result that is merely stale, or a file whose completion event is missing after an interrupted run, is rewritten with a plain `evaluation.completed` and no replacement event.

### What the agent must supply

Under `agent:`:

- **`requirements`**: every requirement in the posting, each with an `id` (`r1`, `r2`, ...), a `class` (`must`, `strong`, `nice`), a `kind` (`technology`, `title`, `certification`, `education`, `years`, `domain`, `other`), `text`, a verbatim `quote` from `jd.md`, a `term` (the technology, title, certification or degree to look for) and, for `years`, `years`. Optional `links` point at related facts: `{fact, strength, why}`.
- **`fit`**: a judgement (`"yes"`, `"partial"`, `"no"`) with a verbatim `quote` (or `quote: null` with `"no"` when the posting is silent) for every fit row of the profile's `## Scoring rubric`. Rows whose label mentions compensation or sponsorship, and rows weighted `Negative`, are not fit rows.
- **`job_facts`**: `market` (the exact label of the profile's `## Markets` row, including a `(home)` suffix) and `sponsorship` (`needed`, `listing`, `quote`).

Pay is not part of the agent half. `careeros evaluate` reads it from the job's `compensation.md` (see [Job compensation](compensation.md)) and takes the floor from the verified `## Markets` row by code. The 0.5.0 `job_facts.compensation` block is rejected with `EVL003`, and `job_facts.market` must name the same row as `compensation.md`'s `market` once that file has observations (`EVL006`). A job without a `compensation.md` has no pay information, which is not evidence that pay is low.
Quote `"yes"` and `"no"` in YAML. Unquoted `yes` and `no` are read as booleans; the command turns them back into the words, but quoting avoids the confusion. The `evaluate` skill has a complete worked example, and a test runs that example through the command so it cannot drift from the code.

Every quote must appear in `jd.md` after whitespace is normalised: line breaks and repeated spaces do not matter, capitalisation and wording do.

## How a requirement becomes an outcome

The command, not the agent, decides each outcome from your active facts (`claimed`, `confirmed` or `verified`; `disputed` and `retired` facts support nothing).

| Outcome | Meaning |
|---|---|
| `covered` | an active fact matches the requirement |
| `adjacent` | no match, but the agent linked a related active fact |
| `gap` | no match and no link |
| `unverifiable` | cannot be derived from the memory (for example "5+ years of Go": dates do not say how long you used Go) |

- `technology`, `title`, `certification` and `education` match their `term` against the facts' technologies, titles, certifications or degree and field. Technologies compare as exact canonical strings (`C`, `C++` and `C#` differ).
- `years` without a `term` is compared with the whole years of dated, active experience (overlaps counted once). With a `term` it is `unverifiable`.
- `domain` and `other` are never `covered`.
- A link is an input, not a conclusion: it can turn a `gap` into `adjacent`, never into `covered`. A link to a missing, disputed or retired fact, or to an achievement whose experience is inactive, is `EVL005`.

## Scoring

Rules version 2 (version 1 differs only in how pay was supplied). All arithmetic uses exact fractions; the stored scores are rounded to four decimals for display only.

| Requirement class | Weight |
|---|---|
| `must` | 3 |
| `strong` | 2 |
| `nice` | 1 |

| Outcome | Credit |
|---|---|
| `covered` | 1 |
| `adjacent` | 1/2 |
| `gap` | 0 |
| `unverifiable` | left out of coverage |

| Fit row weight | Weight | Judgement | Credit |
|---|---|---|---|
| High | 3 | `yes` | 1 |
| Medium | 2 | `partial` | 1/2 |
| Low | 1 | `no` | 0 |

**Coverage** is the weighted share of credit over the requirements that are not `unverifiable`. **Fit** is the weighted share over the judged fit rows. **Combined** is 65% coverage plus 35% fit; if there is no fit (no usable rubric, or no judged row) the combined score is the coverage.

| Combined | Tier |
|---|---|
| 0.7 or more | `pursue` |
| 0.5 to under 0.7 | `stretch` |
| 0.3 to under 0.5 | `weak` |
| under 0.3 | `skip` |
| no requirement can be scored | `unrated` (no combined score, confidence `low`) |

### Caps

A cap limits the tier; it never raises one. The lowest ceiling wins. The sponsorship and compensation caps use verified inputs only; the must-gaps cap uses the requirement outcomes.

| Cap | Ceiling | When |
|---|---|---|
| `sponsorship` | `weak` | the profile's Markets row says sponsorship is `required`, the agent says so, and the posting does not offer it (with a verbatim quote) |
| `compensation` | `weak` | a verified floor exists and the effective range from `compensation.md` (a `posted` or `recruiter` kind, every figure of that kind comparable with the floor, none with `low` confidence, no conflict) has its top strictly below it; the figures are converted with a recorded, dated rate when the currency differs. Estimates never cap |
| `must_gaps` | `stretch` | two or more `must` requirements are `gap` |

The sponsorship need is **verified** only when the agent's value agrees with the one machine-readable value in the `## Markets` row (`required` or `not needed`). A disagreement is a **conflict** and is reported, never resolved in the job's favour. The pay floor is read from the row by code: an amount with one currency is verified, anything else is **unverified**, which means no cap and an action item. Derivation, comparability and conflicts are described in [Job compensation](compensation.md).

### Confidence

Confidence starts at `high` and drops one step for each of these (never below `low`):

- more than half of the weight of the supported requirements is claimed-only (a requirement counts as claimed-only when the weakest of its supporting facts, its status floor, is `claimed`);
- more than a quarter of the `must` and `strong` weight is `unverifiable` (two steps if more than half);
- the sponsorship listing is unknown while the need is not established as `no`, or the range is not disclosed while a verified floor exists;
- fewer than three requirements, or no `must` requirement;
- there is no fit score.

### Action items

`result.action_items` lists what would change the result, in a fixed order: unverifiable requirements, `must` gaps, requirements whose weakest supporting fact is only claimed, sponsorship questions, then pay questions (conflict, unverified, range not disclosed, currency or basis mismatch, low source confidence). Each tells you the next step, usually to add or confirm a fact with the `memory` skill.

## Freshness: stale versus tampered

`result.stamps` holds six hashes of the inputs the result was computed from.

| Stamp | Covers |
|---|---|
| `jd` | `jd.md`, after whitespace normalisation |
| `memory` | every fact (retired and disputed included) without bookkeeping fields, the effective lexicon, and the whole years of experience and contributing experiences derived from today's date |
| `rubric` | the fit rows and weights, and the `## Markets` rows |
| `rules` | the scoring rules version |
| `inputs` | the `agent:` half without `updated_at` |
| `compensation` | the canonical parsed `compensation.md` (a fixed value when there is no file) |

- **Stale** (`EVL008`, a warning) means an input changed after the result was computed, for example you confirmed a fact, edited `jd.md` or changed a figure in `compensation.md`. Evaluations written by 0.5.0 (basis and rules version 1) read as stale once after upgrading; they are never reported as tampered. Re-evaluate explicitly; if the old record still has the pay block, the `evaluate` skill migrates it first (see [Job compensation](compensation.md)). Nothing is wrong with the file. Run `careeros evaluate <job>` to refresh it. The `prep` skill does this check before it uses an evaluation.
- **Tampered** (`EVL007`, an error) means the stored result is not what the command would have produced from its own stored basis: a score, the tier, the caps, the body or the basis was edited. Re-running replaces the result and logs the replacement (`evaluation.result_replaced`).

The two are reported separately and can both be present.

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | scored, replaced or unchanged |
| 1 | the record or the posting cannot be scored (`EVL001`-`EVL006`, `EVL009` for a duplicated id), or the write failed; evaluation.md is unchanged. A rejection for invalid input is logged once as `evaluation.rejected`; a missing file, `--init` on an existing file and a write failure log nothing |
| 2 | a usage problem: unknown job, no workspace, or a career memory with errors (run `careeros validate`) |

`--json` prints a single JSON object on stdout once the workspace is found; for exit 1 it has `error` and `issues`. Usage errors before that (no workspace, bad arguments) print plain text on stderr.

## Codes

`careeros validate` runs these checks on every `evaluation.md`; `careeros evaluate` reports `EVL001`-`EVL006` and `EVL009` itself.

| Code | Severity | Meaning |
|---|---|---|
| `EVL001` | error | the file cannot be read, has bad YAML, a wrong header or no `agent:` |
| `EVL002` | error | a requirement has no id or repeats one, the record's `job` is not this job, or a record with a `result:` lists no requirements |
| `EVL003` | error | a required text field is missing, a field has the wrong type, an unknown key or a value outside its enum; a `fit` row is not a fit row of the rubric or is judged more than once; the removed `job_facts.compensation` block (on a 0.5.0 record, `evaluate` names whether `compensation.md` is missing, has errors, is empty or is valid) |
| `EVL004` | error | a quote is missing or not found in `jd.md`, or `jd.md` is missing |
| `EVL005` | error | a link names a fact that does not exist or is not active |
| `EVL006` | error | `market` does not name exactly one `## Markets` row, differs from `compensation.md`'s `market`, or a sponsorship field is incomplete |
| `EVL007` | error | the stored result or body was changed (tampered) |
| `EVL008` | warning | stale: an input changed since the result was computed |
| `EVL009` | warning | the file and the ledger disagree (see below) |
| `EVL010` | warning | scored under a version of the rules or basis this CareerOS cannot verify |
| `EVL011` | warning | a floor or sponsorship need is unverified or in conflict with the Markets row |

A `CMP` error in `compensation.md` also stops `careeros evaluate` (exit 1); see [Job compensation](compensation.md) for the codes.

### File and ledger (EVL009)

The file is written first and the `evaluation.completed` event second, under the workspace lock. If the process dies between the two, the file has no matching event, which `validate` reports as `EVL009`. Running `careeros evaluate <job>` again repairs it. This crash window is accepted and detected, not eliminated. `EVL009` is also raised when a restored or copied file does not match the ledger and when two records share an id (delete the copy and run `--init` for a fresh id).

## Limits

- **Extraction is judgement.** Which lines are requirements, their class and kind, and the fit judgements come from the agent reading prose. Quotes prove the words are in the posting, not that the reading is right. Two careful readers can score the same posting differently.
- **The memory stamp covers the whole memory.** Any change to any fact, including one unrelated to the job, makes every evaluation stale. This over-invalidates on purpose: it is cheap to re-run, and a narrower stamp could miss a change that matters.
- **Only the facts matter.** The score is as good as `career/`. Claimed facts count, but they lower confidence and become action items.
- **The Markets table is read conservatively.** A floor is read only from a cell with one amount and one currency; anything else is unverified.
- **Pay is only as good as its record.** Observations and rates are supplied by the agent; see the limits in [Job compensation](compensation.md).
- **Tamper checks have gaps.** A record under an unsupported rules version cannot be verified (`EVL010`), and `EVL009` against the ledger is the backstop.
- **The verdict is not evidence.** A `covered` requirement says your memory has a matching fact. It does not make a sentence in a resume true, and drafts are still checked by `careeros check`.
- **Claude Code only.** GPT Work cannot run local commands, so it has no `evaluate` skill.

## Getting the skill

Run `careeros upgrade` in an existing workspace to receive the updated `evaluate` and `browse` skills (it backs up first).
