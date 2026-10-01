# Skill: Market

## When to run

When the user says "research [country] market", "can I work in [country]?", "visa options for [country]", or "refresh [country] market".

Researches what it takes to work in another country — visa routes, whether the user qualifies, what pay looks like, and local application norms. **Do not answer from memory:** visa rules and salary thresholds change, so every figure must come from a source you search for in this conversation.

> This is research to help the user decide and prepare, **not legal or immigration advice**. Say so once in the final summary and tell them to confirm with the official authority or a qualified adviser before acting.

---

## STEP 0 — Load context

From `profile.md` and `resume.md` (if uploaded) note: years of experience, role and seniority, degree and field, the `## Markets` row for this country (minimum base, sponsorship need), and languages. Ask only for what is missing and needed. Never invent a qualification.

If a `markets/[country-slug].md` file is uploaded and its `Refresh after` date is in the future, say so and ask whether to reuse or refresh.

---

## STEP 1 — Visa routes (official sources first)

Use web search. Prefer, in order: the government's immigration or labour authority site; official employer-side pages (sponsor registers, work-pass applications); reputable secondary sources only to fill gaps (and lower the confidence).

For each route a software engineer could use, capture: name and purpose, **salary threshold** (amount, currency, period), core requirements, whether the **employer must sponsor**, processing time and costs if stated, and the **source URL with the date you read it**. If two sources disagree, show both and say which is official. If you cannot read a source, mark the item **unverified** — never fill it in.

## STEP 2 — Eligibility check

| Route | Requirement | Your situation | Status |
|-------|-------------|----------------|--------|

`meets` only when the profile or resume states it; `unclear` when it depends on something not on file (say what); `does not meet` when it clearly fails. Do not round up.

## STEP 3 — Compensation and cost

Find typical **base** pay for the user's role and level in the target city (Levels.fyi, Glassdoor's local site, local job boards). Keep the local currency; if you convert, state the rate and date. Give range, base vs total, source, and confidence. Compare with the user's floor for this market. If it says `TBD`, **derive the floor yourself** from the home floor and record it (the user can override): (1) FX from two sources with date; (2) home take-home at the home floor after tax, minus a rough home living cost = absolute yearly savings; (3) same lifestyle's cost in the target city (Numbeo comparison, rent and basket separately); (4) required net = destination living cost + same savings in converted terms; (5) gross up with destination progressive tax and mandatory contributions; (6) floor = max(that figure, visa-route salary threshold), rounded, target about 10% higher; (7) write the floor into `## Markets` and the full reasoning into the market file, labelled approximate. Add a rough, clearly approximate tax and cost-of-living note, and check typical pay against the route thresholds.

## STEP 4 — Employer side

Name any official register of licensed or recognised sponsors, which employer types usually sponsor, and phrases to look for in listings ("relocation package", "visa support", "must already have the right to work").

## STEP 5 — Local application conventions

CV length and format, whether a photo/date of birth is customary, expected language, cover-letter expectations, how salary and notice period are stated, dominant platforms. Mark each `norm` or `varies`. The user decides on personal details.

---

## Output file

Output the file as a fenced block for the user to save:

````markdown
**`markets/[country-slug].md`** — save to a `markets/` folder in your workspace:

~~~markdown
# [Country] — Market Notes

**Researched:** [date]
**Refresh after:** [date + 90 days]
**Overall confidence:** [High / Medium / Low]
**Note:** Research to support a decision, not legal or immigration advice.

## Visa routes
| Route | For | Salary threshold | Key requirements | Employer must sponsor? | Source (read on [date]) |
|-------|-----|------------------|------------------|------------------------|--------------------------|

## Your eligibility (as of [date])
| Route | Requirement | Your situation | Status |
|-------|-------------|----------------|--------|

**To confirm:** [what the user must verify or obtain]

## Compensation
| City | Role / level | Base range | Currency | Source | Confidence |
|------|--------------|------------|----------|--------|------------|

**Your floor for this market:** [from profile, or "proposed: … (not yet confirmed)"]
**Tax and cost of living (approximate):** [short note]

## Employer sponsorship signals
[...]

## Local application conventions
[...]

## Open questions
[...]
~~~
````

Also output a one-line addition for `activity.md`, and the updated `profile.md` `## Markets` table only if the user confirmed a proposed floor.

Finish with a few sentences: viable routes, main eligibility gaps, how typical pay compares to the floor, and the next step (for example "browse [local board]").
