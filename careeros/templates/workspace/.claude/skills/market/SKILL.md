# CareerOS Market Skill

**Trigger:** "research [country] market" / "can I work in [country]?" / "visa options for [country]" / "refresh [country] market" / "market research [country]"

Researches what it takes to work in another country — visa routes, whether the user qualifies, what the pay looks like, and local application norms — and saves a dated, cited file per country. **Nothing here is answered from memory:** visa rules and salary thresholds change, so every figure comes from a source fetched during this run.

> This is research to help you decide and prepare, **not legal or immigration advice**. Confirm anything you act on with the official immigration authority or a qualified adviser. Say this once in the final summary.

---

## STEP -1 — CHECK FOR EXISTING RESEARCH

Look for `markets/[country-slug].md` (lowercase, hyphenated country name).

- **Exists and `Refresh after` is in the future:** say "Already researched [Country] on [date] — reuse it or refresh?" and stop unless the user wants a refresh.
- **Exists but past `Refresh after`, or missing:** proceed.

---

## STEP 0 — LOAD CONTEXT

Read `profile.md` (whole file) and `resume.md` if present. Capture what eligibility depends on:
- years of experience, current/target role, seniority
- degree and field (from `resume.md` education, or ask)
- the `## Markets` row for this country: minimum base, sponsorship need, priority
- languages (ask if a route or local convention depends on it)

Ask only for what is missing and needed. Never invent a qualification.

---

## STEP 1 — VISA ROUTES (official sources first)

Find the routes a software engineer could use. Search in this order and prefer the earliest:
1. **The government's immigration / labour authority** (the official `.gov` / `.gov.xx` / EU site).
2. **Official employer-side pages** (sponsor registers, work-pass applications).
3. **Reputable secondary sources** (law firms, relocation guides) — only to fill gaps, and mark confidence lower.

`WebFetch` can fail or be blocked on some official sites. If it does, open the page in a new browser tab (`tabs_create_mcp` → `navigate` → `get_page_text`). If a page cannot be read at all, say so and mark that item **unverified** — do not fill it in.

For each plausible route capture:
- name and what it is for (skilled worker, tech talent, intra-company, job-seeker, long-term/high-skill, etc.)
- **salary threshold** (amount, currency, per month/year) and what it applies to
- core requirements (degree, years of experience, language, job offer needed?)
- whether the **employer must sponsor** or the candidate can apply independently
- typical processing time and key costs, if stated
- **source URL and the date you read it**

Every number needs a source. If two sources disagree, show both and say which is official.

---

## STEP 2 — ELIGIBILITY CHECK

For each route, compare against the profile:

| Route | Requirement | Your situation | Status |
|-------|-------------|----------------|--------|
| [route] | [e.g. degree in a relevant field] | [from profile/resume] | meets / unclear / does not meet |

Status rules: `meets` only when the profile or resume states it; `unclear` when it depends on something not on file (say what); `does not meet` when it clearly fails. Do not round up. List what the user would need to confirm or obtain.

---

## STEP 3 — COMPENSATION AND COST

Find typical **base** pay for the user's role and level in the target city:
- Levels.fyi (filter to the country/city), Glassdoor's local site, and the local job boards' salary pages. Open in a browser tab if `WebFetch` returns an empty page.
- Keep the **local currency**. If you convert, state the rate and date.
- Record the range, whether it is base or total, source, and confidence (high: 3+ sources close together; medium; low: one or sparse).

Then:
**Derive the floor (do this when the row says `TBD`; don't wait to be asked).** Base it on the home-market floor in the `## Markets` table:
1. **FX:** current rate from at least two sources (state them and the date).
2. **Home net and savings:** estimate take-home at the home floor (home income-tax rules, current regime), subtract a rough single-person home living cost (user's own figures if given in profile, otherwise Numbeo-style estimate) to get absolute yearly savings.
3. **Destination living cost:** compare the same lifestyle in the target city (Numbeo cost-of-living comparison: rent and everyday basket separately). Use a city-centre vs outside-centre sensitivity if rent dominates.
4. **Required net** = destination living cost + the same absolute savings (converted at FX).
5. **Gross up** with the destination's progressive income tax and mandatory social contributions (check whether the visa holder pays them). Result = minimum monthly/yearly base.
6. **Sanity-check** against the visa-route salary thresholds and typical market pay from above. Floor = max(derived figure, route threshold), rounded to a clean number; set a target about 10% above.
7. **Record** the floor in the `## Markets` row and the full reasoning (inputs, assumptions, sensitivities) in the market file. Tell the user the number and that they can override it. Label tax figures approximate.
- Add a short, rough note on income tax and cost of living, clearly labelled as approximate. If net pay matters to the decision, say what would be needed to compute it properly.
- Check the route thresholds from STEP 1 against this range: is the typical pay above them?

---

## STEP 4 — EMPLOYER SIDE

Note how to tell whether an employer can sponsor:
- Does this country publish a **register of licensed / recognised sponsors** or similar? If yes, name it and link it.
- Which employer types usually sponsor (multinationals, scale-ups) and which usually cannot (very small companies).
- Phrases that appear in listings (for example "relocation package", "visa support", "must already have the right to work").

---

## STEP 5 — LOCAL APPLICATION CONVENTIONS

Capture what differs from the user's home market, from a reliable source:
- CV length and format norms; whether a photo, date of birth or nationality is customary
- language expected for the CV and for interviews
- cover letter expectations
- how salary expectations and notice periods are usually stated
- anything platform-specific (the dominant job board or professional network)

Mark each as `norm` (widely expected) or `varies`. These are guidance for `prep` and `apply`; the user decides on personal details.

---

## STEP 6 — SAVE

Create the `markets/` directory if it does not exist. Write `markets/[country-slug].md`:

```markdown
# [Country] — Market Notes

**Researched:** [date]
**Refresh after:** [date + 90 days]
**Overall confidence:** [High / Medium / Low]
**Note:** Research to support a decision, not legal or immigration advice.

## Visa routes
| Route | For | Salary threshold | Key requirements | Employer must sponsor? | Source (read on [date]) |
|-------|-----|------------------|------------------|------------------------|--------------------------|

## Your eligibility (as of [date], against profile.md)
| Route | Requirement | Your situation | Status |
|-------|-------------|----------------|--------|

**To confirm:** [anything the user must verify or obtain]

## Compensation
| City | Role / level | Base range | Currency | Source | Confidence |
|------|--------------|------------|----------|--------|------------|

**Your floor for this market:** [from profile.md, or "proposed: … (not yet confirmed)"]
**Vs. route thresholds:** [typical pay above/below each threshold]
**Tax and cost of living (approximate):** [short note]

## Employer sponsorship signals
[register link, which employers usually sponsor, phrases to look for]

## Local application conventions
- CV: [length / format / personal details — norm or varies]
- Language: [...]
- Cover letter: [...]
- Salary expectation / notice period: [...]
- Platforms: [...]

## Open questions
[unverified items and what would settle them]
```

Append to `activity.md`:
```
[date] market research [Country]: [N] routes, eligibility [summary], typical base [range] ([confidence] confidence)
```

---

## FINISH

Summarise in a few sentences: the routes that look viable, the main eligibility gaps, how typical pay compares to the user's floor, and what to do next (for example "browse [local board]"). Mention that this is research, not legal advice. If a floor was proposed, ask for confirmation to update `## Markets`.

---

## NOTES

- Always open a fresh browser tab; close tabs you opened.
- Never submit anything or create an account on a government or immigration site.
- Keep it factual: unverified is an acceptable answer; a guess is not.
- Re-run when the file passes its `Refresh after` date, or when the user hears of a rule change.
