# CareerOS Research Skill

**Trigger:** "research [company]" / "find people at [company]" / "who's the hiring manager at [company]" / "comp research for [role]"

Supports three research modes — run whichever the user asks for, or all three in sequence.

---

## STEP -1 — CHECK FOR EXISTING RESEARCH FIRST

Before opening any tab, check whether this company has already been researched for a *different* role — `jobs/discovered/*/company.md` and `jobs/discovered/*/people.md` are per-job files, but the company itself doesn't change between two roles at the same employer.

Search other job folders for a `company.md` (or `people.md`) whose `# [Company]` header matches. If found and dated within the last ~30 days:
> "Already researched [Company] on [date] for [other role] — reuse that, or refresh anyway?"

On reuse: copy the file into this job's folder as-is (update nothing except adding a note of which job it was reused from), append to `activity.md`, and skip the mode(s) it covers. On refresh, or if none found / stale: proceed with the mode below as normal.

This applies to Mode A and Mode C (company info and comp data don't change per-role); Mode B (people) is worth refreshing more often since headcount and hiring-manager assignments turn over faster — still check, but weight toward refreshing if it's been more than a couple of weeks.

---

## MODE A — COMPANY RESEARCH

### A1 — Open company page

1. `tabs_create_mcp` — new tab.
2. Navigate to the company's website (or LinkedIn company page if no direct URL in the job file).
3. Wait 3–4 seconds.

### A2 — Extract company info

Use `read_page` to extract:
- What the company does (1–2 sentences)
- Company size / stage (startup, scale-up, enterprise)
- Tech stack clues (job listings, engineering blog, about page)
- Recent news (funding, launches, layoffs)
- Engineering culture signals (eng blog posts, open source, conference talks)

### A3 — Save

Write `jobs/discovered/[job-slug]/company.md`:
```markdown
# [Company] — Research

**Website:** [url]
**Size / stage:** [info]
**What they do:** [description]

## Tech stack signals
[bullet list]

## Recent news
[bullet list with dates]

## Culture / engineering signals
[bullet list]

**Researched:** [date]
```

Append to `activity.md`:
```
[date] researched company [Company] for [Job Title]
```

---

## MODE B — PEOPLE RESEARCH

### B1 — LinkedIn people search

1. `tabs_create_mcp` — new tab.
2. Navigate to LinkedIn People Search for the company: `https://www.linkedin.com/search/results/people/?keywords=[company]&origin=GLOBAL_SEARCH_HEADER`
3. Filter by company if needed.

If not logged in:
> "You're not logged in to LinkedIn. Log in and say 'find people at [company]' again."
Stop.

### B1b — Prefer the company's own People tab (more reliable than keyword search)

A global people search like `keywords=recruiter OR "engineering manager" [company]` is not scoped to the company and returns unrelated recruiters. Instead:

1. Find the company's LinkedIn slug: `https://www.linkedin.com/search/results/companies/?keywords=[company]` and read the first matching `/company/[slug]/` link. Check size and location — names collide (several "Arcana", "Matters", "Different AI").
2. Open `https://www.linkedin.com/company/[slug]/people/?keywords=[ONE word]`. Multi-word queries often return nothing. Run `talent` (recruiters), `manager` (EMs), `engineering` (peers), and `India` or the city to see whether a local team exists.
3. Read with `get_page_text` (retry if it returns only the header — the list loads late). Profile URLs come from the page's `/in/[slug]` links.

**Who to target:** people whose headline says they are hiring ("Hiring engineering talent at…", "Talent Partner", "Technical Recruiter"), the engineering manager the role would report to, and a peer engineer for a referral. Founders and C-level are a **fallback only** — at anything above ~20 people they rarely answer cold messages.

### B2 — Identify relevant contacts

From search results, look for:
- **Hiring managers**: Engineering Manager, Director of Engineering, VP Engineering for relevant teams
- **Recruiters**: Technical Recruiter, Talent Acquisition
- **Peers**: Engineers with titles matching the target role

Capture for each person: name, title, LinkedIn URL.

Aim for 3–6 people. Don't scrape broadly — focus on people who can actually influence a hiring decision.

### B3 — Save

Write `jobs/discovered/[job-slug]/people.md`:
```markdown
# [Company] — People

| Name | Title | LinkedIn | Role |
|------|-------|----------|------|
| [name] | [title] | [url] | hiring-manager / recruiter / peer |

**Researched:** [date]
```

Append to `activity.md`:
```
[date] researched people at [Company]: found [N] contacts
```

---

## MODE C — COMPENSATION RESEARCH

### C1 — Check public sources

Open tabs for each source in sequence:

1. **Levels.fyi** — `https://www.levels.fyi/companies/[company-slug]/salaries/`
2. **Glassdoor** — search `[company] [role title] salary`
3. **LinkedIn Salary** — `https://www.linkedin.com/salary/[role]-at-[company]-salaries`

Use `read_page` to extract salary ranges for matching roles/levels.

### C2 — Summarise with confidence rating

Cross-reference the sources. Rate confidence:
- **High**: 3+ data points within 15% of each other
- **Medium**: 2 sources or wide spread
- **Low**: single source or sparse data

### C3 — Save

Append a `## Compensation` section to `jobs/discovered/[job-slug]/company.md` (create it if absent):
```markdown
## Compensation

| Source | Role | Level | Range |
|--------|------|-------|-------|
| Levels.fyi | [role] | [level] | [range] |
| Glassdoor | [role] | — | [range] |

**Confidence:** [High / Medium / Low]
**Notes:** [any caveats]
**Researched:** [date]
```

Append to `activity.md`:
```
[date] compensation research [Company — Title]: [range summary] ([confidence] confidence)
```

---

## NOTES

- Always create a new tab — never navigate existing ones.
- If LinkedIn blocks or prompts CAPTCHA: stop and tell the user.
- Keep research factual — don't invent or guess data points.
