# Skill: Browse

## When to run

When user says "browse [board]", "find jobs on [board]", or similar.

---

## STEP 0 — Load context

Read `profile.md` above the fold and `boards.md`.

Extract:
- scoring rubric
- comp floor (minimum compensation)
- target roles (for search queries)
- board config for the requested board

---

## STEP 1 — Determine browse method

| Board | Method |
|-------|--------|
| Wellfound, Indeed, Naukri | Web search (built-in) |
| LinkedIn, Instahyre | Requires login — ask user to paste content |

### For boards with web search

Use your web search tool to find job listings. Suggested queries:
- `site:[board domain] [target role] [location]`
- `"[target role]" "[key skill]" site:[board domain]`

Search 2-3 queries and aggregate results. Aim for 10–20 listings.

### For authenticated boards (LinkedIn, Instahyre)

Say:

> "LinkedIn requires login to show full listings. Here's how to get the jobs:
> 1. Go to LinkedIn Jobs and search for **[target role queries from boards.md]**
> 2. Select all listings on the page (Ctrl+A or Cmd+A in the results area)
> 3. Copy and paste the page text into this chat
>
> I'll score and summarize them for you."

Wait for the user to paste the content, then parse it.

---

## STEP 2 — Score listings

For each listing, score 0–10 using the rubric from `profile.md`.

Extract per listing:
- title
- company
- location
- brief description (1 sentence)
- URL (if available)
- score

Drop listings scoring < 6.

### Comp check (required)

For the top 10 listings, search for compensation for that role, level and city (Levels.fyi, AmbitionBox, Glassdoor, LeetCode Discuss compensation posts) and compare it with the comp floor in `profile.md`. Never present comp as "unchecked" — if there is no data, write "unknown" and say what you searched. Self-reported sites skew low and often show fixed pay only; say so. A role clearly below the floor caps at 6; borderline stays but is flagged.

For a board that serves a non-home market, use that market's floor from the `## Markets` table in `profile.md` (its `Market:` line in `boards.md` says which), keep the currency local, and compare base to base.

### Sponsorship check (listings outside the home market, or when `## Markets` says sponsorship is required)

Classify each listing as **offered** ("visa sponsorship", "relocation package/support"), **not offered** ("must have the right to work", "no sponsorship", "citizens only") or **unknown** (says nothing — the common case; don't treat silence as yes or no), with a short phrase as evidence. If sponsorship is required and the listing is not offered, cap the score at 3. Unknown stays but is flagged: "ask the recruiter early". If `markets/[country].md` exists, compare any stated pay with its route thresholds and flag a likely mismatch. If it doesn't exist, suggest "research [country] market" first.

---

## STEP 3 — Present results

Show a markdown table sorted by score descending:

```
| # | Score | Title | Company | Location | Comp (source, confidence) | Sponsor | URL |
|---|-------|-------|---------|----------|---------------------------|---------|-----|
| 1 | 8.5   | Senior Backend Engineer | Stripe | Remote | ₹55–70L (Levels.fyi, medium) | n/a | [link] |
...
```

Then ask: "Which jobs would you like to save? Reply with the numbers (e.g. '1, 3, 5') or 'all'."

---

## STEP 4 — Save selected jobs

For each job the user selects, output two code blocks:

### jobs/discovered/[company-slug]-[role-slug].md

```markdown
**`jobs/discovered/[company-slug]-[role-slug].md`** — save to jobs/discovered/:

~~~markdown
# [Title] @ [Company]

- **URL:** [url]
- **Location:** [location]
- **Market:** [home / market name]
- **Sponsorship:** [offered / not offered / unknown]
- **Score:** [score]/10
- **Added:** [date]

## Why it scored [score]
[2-3 bullet points from rubric]

## Notes
[blank — user fills in]
~~~
```

### Updated pipeline.md

After all selected jobs, output the complete updated `pipeline.md` with new rows appended:

```markdown
**`jobs/pipeline.md`** — replace your existing file:

~~~markdown
[full pipeline table with new rows added as [ ] Discovered]
~~~
```

### Updated activity.md

```markdown
**`activity.md`** — append these lines to your existing file:

~~~
[YYYY-MM-DD] Browsed [board]: found [N] listings, saved [M] jobs.
[YYYY-MM-DD] Saved: [Title] @ [Company] (score [X]/10)
...
~~~
```

---

## Limitations note

> **Note:** Web search finds publicly indexed listings. LinkedIn and authenticated boards require you to paste page content since I can't log into sites on your behalf.
