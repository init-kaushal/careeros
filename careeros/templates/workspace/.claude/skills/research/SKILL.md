# CareerOS Research Skill

**Trigger:** "research [company]" / "find people at [company]" / "who's the hiring manager at [company]" / "comp research for [role]"

Supports three research modes — run whichever the user asks for, or all three in sequence.

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
