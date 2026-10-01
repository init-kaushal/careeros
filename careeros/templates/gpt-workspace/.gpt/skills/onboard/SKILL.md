# Skill: Onboard

## When to run

Run this skill when `profile.md` does not exist in the project files.

---

## Protocol

Say:

> "Welcome to CareerOS! I'll ask you 8 quick questions to set up your workspace. This takes about 3 minutes."

Then ask these questions **one at a time**, waiting for each answer:

1. **Role and experience** — "What's your current role, and how many years of experience do you have?"
2. **Tech stack** — "What are your primary skills and technologies? (e.g. Python, React, AWS, product management)"
3. **Target roles** — "What role titles are you targeting? (e.g. Senior Backend Engineer, Staff Engineer, Engineering Manager)"
4. **Location** — "Where are you based, and are you open to remote / hybrid / relocation? If you'd consider other countries, which ones, and would you need visa sponsorship?"
5. **Compensation floor** — "What's your minimum acceptable annual base salary, in your local currency? If you named other countries, give a minimum base for each in that country's currency — or just skip it and I'll derive each one from your home floor (FX, tax and cost of living) during market research, which you can override."
6. **Target companies** — "Any specific companies you want to target? (optional — skip if not sure yet)"
7. **Job boards** — "Which job boards do you use? (LinkedIn, Instahyre, Wellfound, Naukri, or others)" If they named other markets in Q4, suggest the boards for those markets from `boards-catalog.md` in this skill's folder. Instahyre and Naukri are India boards; don't suggest them for other markets.
8. **LinkedIn profile URL** — "What's your LinkedIn profile URL? (used for company research and outreach)"

After collecting all answers, say: "Great — writing your workspace files now. Copy and save each one."

---

## Output files

Output each file as a complete fenced code block. The user saves each one to their workspace directory.

### profile.md

```markdown
**`profile.md`** — save to your workspace root:

~~~markdown
# Profile

## Identity
- **Name:** [from answers]
- **Current role:** [from Q1]
- **Experience:** [from Q1]
- **LinkedIn:** [from Q8]

## Skills and stack
[from Q2 — bullet list]

## Target roles
[from Q3 — bullet list]

## Location and work mode
[from Q4]

## Compensation floor (base, home market)
[from Q5]

## Markets

| Market | Min base (per year) | Sponsorship | Priority |
|--------|---------------------|-------------|----------|
| [home] (home) | [from Q5] | not needed | 1 |
| [other market from Q4, if any] | [per-market floor, or TBD — derive from home floor] | [required / preferred / not needed] | [2/3] |

Visa and salary rules are not stored here — they change. Look them up and date them when needed.

## Target companies
[from Q6 — bullet list, or "Not specified"]

## Scoring rubric

Score each job 0–10:

| Criterion | Weight | Notes |
|-----------|--------|-------|
| Role title match | 3 | Exact match = 3, adjacent = 1-2, off = 0 |
| Stack overlap | 3 | Each matching tech = 0.5, max 3 |
| Company type / culture fit | 2 | Based on target companies and stated preferences |
| Location / remote | 1 | Matches preference = 1, partial = 0.5 |
| Seniority level | 1 | Right level = 1, one off = 0.5 |
| Market and sponsorship | modifier | Listing is in a market from the table and its sponsorship need is met: no change. Sponsorship required but the listing says none: −2 |

Threshold: save jobs scoring ≥ 6.

## ── HISTORY (on demand; do not read past this line) ──
~~~
```

### boards.md

```markdown
**`boards.md`** — save to your workspace root:

~~~markdown
# Boards

[For each board from Q7:]
## [Board name]
- **URL:** [board URL]
- **Search queries:** [2-3 suggested queries based on target roles]
- **Notes:** [any board-specific notes, e.g. "requires login — paste page content into chat"]
~~~
```

### jobs/pipeline.md

```markdown
**`jobs/pipeline.md`** — save to jobs/ folder (create it if it doesn't exist):

~~~markdown
# Pipeline

| Status | Job | Company | Score | Added |
|--------|-----|---------|-------|-------|
| — | — | — | — | — |

**Status icons:** `[ ]` Discovered · `[~]` Applied · `[?]` Interview · `[✓]` Offer · `[x]` Closed

## ── HISTORY (on demand; do not read past this line) ──
~~~
```

### activity.md

```markdown
**`activity.md`** — save to your workspace root:

~~~markdown
# Activity log

[YYYY-MM-DD] Workspace created via onboarding.
~~~
```

---

## Finish message

After outputting all files, say:

> "Your workspace is set up. Here's how to use it:
>
> - **Find jobs:** say 'browse linkedin' (or any board) — I'll use web search and show you scored listings.
> - **Track pipeline:** say 'show pipeline' to see your job status table.
> - **Other countries:** if you named any, say 'research [country] market' for visa routes, eligibility and typical pay.
> - **Update status:** say 'mark [job] as applied' and I'll output the updated pipeline.md.
>
> Note: since I can't write files directly, you'll need to save each output I give you. I'll always show complete file contents so you can copy-paste them."
