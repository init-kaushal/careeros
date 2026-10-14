# CareerOS Evaluate Skill

**Trigger:** "evaluate [job]" / "should I apply to [job]?" / "how well do I fit [job]?" / "score [job]" / "is [job] worth it?"

Judges one saved job against the user's career memory and writes the result to `jobs/discovered/[job-slug]/evaluation.md`. You do the reading: you pull the requirements out of the posting and record how the posting answers the user's scoring rubric. The command `careeros evaluate` does the judging: it matches each requirement against the career memory, scores, applies the sponsorship and pay caps, and writes the result. You never score anything yourself.

---

## RULES

- Every quote is **verbatim** from `jd.md`. Capitalisation and wording must match; only the spacing and line breaks may differ.
- Judge every fit row of the scoring rubric, even when the posting says nothing about it (`judgement: "no"` and `quote: null`).
- Record sponsorship and pay exactly as the `browse` skill does: what the posting shows, the source, a confidence and an `as_of` date. Never guess a figure or fill a field to make a cap go away.
- Never edit `result:` or the body of `evaluation.md`. They are written by `careeros evaluate`; a hand edit is reported as tampering (EVL007).
- Say a requirement is met only as far as its outcome allows, and never say a requirement is met on `adjacent`, `gap` or `unverifiable`. `covered` means the career memory has a matching fact. `adjacent` means related experience, not a match. `gap` and `unverifiable` mean the memory does not show it.
- Never add or change a career fact from this skill, and never edit `career/`. To record something the user tells you, use the `memory` skill.
- `careeros evaluate` changes only `evaluation.md` (and the ledger). It never changes the job's state.
- Ask the user before any state change (`transition`); see STEP 8.

---

## STEP 0 — LOAD CONTEXT

1. Find the job folder `jobs/discovered/[job-slug]/` and read `job.md` (market, URL, status).
2. Read `profile.md`: the `## Markets` table and the `## Scoring rubric` table. The market row for this job is the one named by the job's `Market:` line (`home` means the row marked `(home)`).
3. If the folder already has `evaluation.md`, read it before starting over. Fix the `agent:` half in place; do not create a second record.

If the profile has no `## Scoring rubric`, evaluate anyway and tell the user the fit score will be missing until it has one.

---

## STEP 1 — SAVE THE POSTING

Save the posting verbatim to `jd.md` (`jobs/discovered/[job-slug]/jd.md`): the whole description as the user would read it, without summarising, translating or tidying. If `jd.md` is missing, open the job URL, read the full description and save it. Every quote below is checked against this file, so save it before you read it for requirements.

If the posting changes later, save the new text over `jd.md` and run the steps again. A changed posting makes the old evaluation stale.

---

## STEP 2 — CREATE THE RECORD

Run `careeros evaluate <job> --init`. It creates `jobs/discovered/[job-slug]/evaluation.md` with an empty `agent:` half and a fresh id. It refuses when the file already exists; edit the existing one instead. Everything you write goes under `agent:`.

---

## STEP 3 — EXTRACT THE REQUIREMENTS

Read `jd.md` and list **every** requirement, not only the ones the user clearly meets. Split a line that asks for several things into several requirements. Fewer than three requirements lowers the result's confidence, so a thin list is reported as thin.

Each entry under `agent.requirements`:

| Field | What to write |
|---|---|
| `id` | `r1`, `r2`, ... each used once |
| `class` | `must` (required, minimum qualification), `strong` (emphasised, repeated, central to the day-to-day) or `nice` (bonus, preferred, plus) |
| `kind` | `technology`, `title`, `certification`, `education`, `years`, `domain` or `other` |
| `term` | the thing to look for in the memory: the technology name, title, certification or degree. Leave it out for `domain` and `other` |
| `years` | a whole number, for `kind: years` only |
| `text` | the requirement in your own words |
| `quote` | the **verbatim** words from `jd.md` that state it |
| `links` | optional, see below |

How the command reads them:

- `technology`, `title`, `certification` and `education` are `covered` only when an active fact matches `term`. Without a `term` nothing can match.
- `years` with no `term` is checked against the whole years of dated experience in the memory. `years` with a `term` ("5+ years of Go") cannot be derived from dates, so it comes back `unverifiable` and becomes an action item for the user. That is expected; do not drop the `term` to avoid it.
- `domain` and `other` are never `covered`.
- **Links.** If you know a fact that relates to a requirement but does not match it, add `links: [{fact, strength, why}]` with the fact id (`careeros memory list`, `careeros memory show <id>`), a `strength` of `direct` or `adjacent`, and a short `why`. A link is a pointer, not a verdict: it can only turn a `gap` into `adjacent`. It never makes a requirement `covered`. Link only to active facts (not `disputed` or `retired`, and not an achievement whose experience or project is inactive); anything else is EVL005.

---

## STEP 4 — JUDGE THE FIT ROWS

For **every** fit row of the profile's `## Scoring rubric`, add an entry under `agent.fit`. A row whose label mentions compensation or sponsorship, or whose weight is `Negative`, is not a fit row: pay and sponsorship are handled by the caps from STEP 5, so do not judge those rows (a judgement naming one is EVL003). If a weight is not High, Medium or Low, the rubric is reported as invalid and the fit score is left out; tell the user to correct the table.

- `row`: the row's label, copied exactly.
- `judgement`: `"yes"`, `"partial"` or `"no"`. Put quotes around `"yes"` and `"no"` in YAML; unquoted `yes` and `no` are tolerated but are YAML booleans, and quoting avoids the confusion.
- `quote`: a verbatim quote from `jd.md` that supports the judgement. If the posting says nothing about the row, write `judgement: "no"` and `quote: null`. A `yes` or `partial` without a quote is rejected.

A fit row you skip is not scored. Judge them all.

---

## STEP 5 — RECORD THE JOB FACTS

Under `agent.job_facts`:

- `job_facts.market` (written `market:` under `job_facts:`): the **exact** label of the profile's `## Markets` row for this job, including a `(home)` suffix where the table has one. An unknown label is EVL006.
- `sponsorship`:
  - `needed`: `"yes"` or `"no"`, copied from the market row (`required` is `"yes"`, `not needed` is `"no"`). If the row says anything else, leave it out.
  - `listing`: `offered`, `not_offered` or `unknown` (the common case: the posting is silent). Anything but `unknown` needs `quote`, a verbatim phrase from `jd.md` (EVL006 if missing).
- `compensation`:
  - `range`: `low`, `high`, `currency` and `basis` (`base` or `total`) as the posting or your research shows it. Leave it out if the pay is not disclosed; do not estimate.
  - `floor`: `amount`, `currency` and `basis: base`, copied from the market row's minimum. If the row's floor is `TBD` or not a single amount with a currency, leave `floor` out and tell the user the market floor needs setting (the `market` skill's floor derivation).
  - `source`, `confidence` (`high`, `medium`, `low` or `unknown`) and `as_of` (for example `2026-09`): sources, confidence and `as_of` are required whenever `compensation` is present. Use the sources and confidence scale from the `browse` skill's comp check; state `unknown` rather than guessing.

The command compares your floor and need with the market row. A mismatch is recorded as a conflict and shows up in the action items; it is never resolved in your favour.

---

## STEP 6 — RUN IT

Run `careeros evaluate <job>` (add `--json` if you want to read the fields).

- **Exit 0:** the result was written. Continue to STEP 7. Running it again with nothing changed prints `(unchanged)` and writes nothing.
- **Exit 1:** on exit 1 the record or the posting could not be scored and evaluation.md is unchanged (an invalid-input rejection is logged in the ledger). Read the `EVL` codes in the message, fix the `agent:` half (see the table below), and run it again; never edit `result:` to get around an error.
- **Exit 2:** a usage problem (unknown job, no workspace) or a career memory with errors. Run `careeros validate`, show the user what it says, and stop.

| Code | Meaning | Fix |
|---|---|---|
| EVL001 | `evaluation.md` cannot be read: bad YAML, no header, no `agent:` | repair the file, or move it aside and run `--init` again |
| EVL002 | a requirement has no id or repeats one; the record belongs to another job, or the agent half lists no requirements | give each requirement its own id (`r1`, `r2`, ...); an empty `requirements` list is also EVL002, so extract the requirements first |
| EVL003 | a required text field is missing, a field has the wrong type or is not an allowed value, a `fit` row is not in the rubric, or a `fit` row is judged twice | use the values in the tables above; copy fit labels exactly |
| EVL004 | a quote is not found in `jd.md`, a required quote is missing, or `jd.md` is missing | copy the words from `jd.md`; save `jd.md` first |
| EVL005 | a link names a fact that is missing or not active | find the right id with `careeros memory list`, or remove the link |
| EVL006 | `market` is not a `## Markets` row, a floor or need has no market, or a pay or sponsorship field is incomplete | copy the row label; give `source`, `confidence`, `as_of` and any needed quote |
| EVL007 | someone edited `result:` or the body | do not edit them; re-running replaces the stored result and logs it as `evaluation.result_replaced` (reasons: `tampered`, `stale`, `ledger_mismatch`) |
| EVL008 | the evaluation is stale: `jd.md`, the career memory, the rubric or Markets table, or your judgements changed since it was scored | re-read `jd.md`, check the judgements still hold, and run `careeros evaluate <job>` again |
| EVL009 | the file and the ledger disagree (a run was interrupted, or the file was copied or restored) | re-running heals it; for a copied file, delete the copy and `--init` a fresh one |

EVL010 and EVL011 are warnings from `careeros validate`: EVL010 means the record was scored by a version of CareerOS this one cannot verify (re-run to refresh it); EVL011 lists a floor or sponsorship need that the market row could not confirm. Tell the user about them; neither stops you.

A **stale** record (EVL008) is not an error in your judgements, it is a notice that the inputs moved. Re-run after checking.

---

## STEP 7 — PRESENT

Show the user, from the command output and `evaluation.md`, the tier, scores, confidence, caps, gaps and action items:

- the **tier** (`pursue`, `stretch`, `weak`, `skip` or `unrated`), the combined, coverage and fit scores, and the **confidence** (`high`, `medium` or `low`), saying plainly when confidence is low and why;
- the **caps**, if any: a sponsorship or pay cap, or a cap for unmet `must` requirements, and the tier it limits the job to;
- a table of the requirements with the outcome of each (`covered`, `adjacent`, `gap`, `unverifiable`) and the fact ids behind it;
- the **gaps** and **action items** in the file's own words (what the user could confirm, add or check to change the result).

Say what each outcome supports and no more. `adjacent` is related experience, not a match. Where an action item says a fact is only claimed, offer the `memory` skill ("run `! careeros memory confirm <id>` to affirm it"); do not add or change facts yourself.

The score is a triage tool, not proof of anything. It does not prove a resume claim, and it never replaces the Career Memory EVIDENCE GATE: every resume, cover letter or message you draft later is still checked with `careeros check`.

---

## STEP 8 — OFFER THE NEXT STEP

Ask the user what they want to do: prep and tailor (the `prep` skill), look at the gaps first, or drop the job. Evaluating does not move the job anywhere.

Only if the user says yes to recording the evaluation in the pipeline, run `careeros transition <job> --to EVALUATED`. Only if the user says they want to pursue it, run `careeros transition <job> --to SHORTLISTED`. If they want to drop it, ask what to record before running anything. Never run a transition because the tier looks good or bad.

---

## COMPLETE EXAMPLE

A worked evaluation with invented data. The fact ids (`ach_incident`, `skl_kubernetes`) are placeholders; use real ids from `careeros memory list`.

**profile.md (the parts the evaluation reads)**
```markdown
# Example Career Profile

## Markets
| Market | Min base (per year) | Sponsorship | Priority |
|--------|---------------------|-------------|----------|
| Atlantis (home) | ₹35,00,000 | not needed | 1 |
| Singapore | SGD 8000 | required | 2 |

## Scoring rubric (use when rating job listings 0–10)
| Signal | Weight |
|--------|--------|
| Role title matches target roles | High |
| Primary language(s) in stack | High |
| Location match or remote-friendly | Medium |
| Compensation signals above the minimum for that listing's market | Medium |
```

(The compensation row is not a fit row: it is covered by the pay cap, so the example judges the other three.)

**jd.md**
```markdown
# Senior Platform Engineer

Example Corp is hiring a Senior Platform Engineer.

Requirements:
- production Kubernetes experience
- 3+ years of professional software engineering
- you will own the on-call rotation
- Rust is a bonus

Location: hybrid, Pune.
Visa sponsorship is not available for this role.
```

**evaluation.md agent half**
```yaml
agent:
  updated_at: "2026-10-10T09:20:00Z"
  requirements:
    - id: r1
      class: must
      kind: technology
      term: Kubernetes
      text: Production Kubernetes experience
      quote: production Kubernetes experience
    - id: r2
      class: must
      kind: years
      years: 3
      text: At least three years of professional software engineering
      quote: 3+ years of professional software engineering
    - id: r3
      class: strong
      kind: domain
      text: Owns an on-call rotation
      quote: you will own the on-call rotation
      links:
        - fact: ach_incident
          strength: adjacent
          why: led incident response with alerting
    - id: r4
      class: nice
      kind: technology
      term: Rust
      text: Rust
      quote: Rust is a bonus
  fit:
    - row: Role title matches target roles
      judgement: "yes"
      quote: Senior Platform Engineer
    - row: Primary language(s) in stack
      judgement: "no"
      quote: null
    - row: Location match or remote-friendly
      judgement: partial
      quote: hybrid, Pune
  job_facts:
    market: Atlantis (home)
    sponsorship:
      needed: "no"
      listing: not_offered
      quote: Visa sponsorship is not available
    compensation:
      range: {low: 3000000, high: 4200000, currency: INR, basis: base}
      floor: {amount: 3500000, currency: INR, basis: base}
      source: posting
      confidence: medium
      as_of: "2026-09"
```

Paste it under `agent:` in the file `--init` created (replace the empty `agent: {}`; the `id`, `type`, `schema`, `job` and `created_at` lines stay), then run `careeros evaluate <job>`. Here `r1` comes back `covered` (the memory has a Kubernetes skill), `r2` `covered` (the dated experience adds up to at least three years), `r3` `adjacent` (linked, not matched) and `r4` `gap`; the unmet `nice` requirement does not trigger a cap.

---

## NOTES

- Extraction is your judgement. Two careful readers can class a line differently; keep the quote exact so the user can see what you relied on.
- Evaluations need a `jd.md`. A job without a saved posting cannot be evaluated.
- If the user later changes the career memory (confirms a fact, adds one, imports a resume), the evaluation becomes stale (EVL008). Re-run `careeros evaluate <job>`; the judgements you recorded are reused.
- Log nothing by hand: the command records the evaluation in the ledger itself. You may add a line to `activity.md`: `[date] evaluated [Company — Title] → [tier]`.
