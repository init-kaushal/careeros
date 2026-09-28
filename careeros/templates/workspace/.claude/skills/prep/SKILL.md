# CareerOS Prep Skill

**Trigger:** "prep for [company]" / "tailor resume for [job]" / "prep my application for [job]" / "what should I highlight for [company]?"

Run this before applying. It produces a tailoring brief — a cheat-sheet of what to emphasise in your resume, cover letter, and any written answers.

---

## STEP 0 — LOAD CONTEXT

Read:
1. `profile.md` — full file (above and below the fold if needed — you need skills detail)
2. `resume.md` — if it doesn't exist, ask: "You don't have a resume on file yet — paste it or a file path and I'll save it, or say 'skip' for a tailoring brief without a full tailored resume." Proceed without it only if they say skip — in that case, skip STEP 4B below.
3. The job file from `jobs/discovered/` matching the user's mention

If the job file exists but has no JD text, navigate to the job URL and extract the full description:
1. `tabs_create_mcp` — new tab
2. `navigate` to the job URL
3. `read_page` — extract the full job description text
4. Close the tab

---

## STEP 2 — ANALYSE THE JOB

From the JD, extract:

**Must-haves** — hard requirements (years of exp, specific tech, certifications)
**Strong signals** — repeated words, phrases, and concepts (these are the ATS keywords)
**Nice-to-haves** — "bonus" / "preferred" items
**Company signals** — culture cues ("move fast", "ownership", "customer obsession")
**Role signals** — IC vs. lead, scope, who you'd work with

---

## STEP 3 — MAP AGAINST PROFILE

For each must-have and strong signal:
- Find the closest match in your profile (skills, experience, projects)
- Rate the fit: **direct** / **adjacent** / **gap**

Gaps to flag honestly — don't paper over them, but note if they can be addressed with context (e.g. "used Kafka in adjacent tech X").

---

## STEP 4 — PRODUCE THE TAILORING BRIEF

Write `jobs/discovered/[job-slug]/prep.md`:

```markdown
# Application Prep — [Title] at [Company]

## Must-haves coverage

| Requirement | Your match | Fit |
|-------------|-----------|-----|
| [req] | [your experience/skill] | direct / adjacent / gap |

## Keywords to include
[comma-separated ATS keywords found in the JD]

## Bullets to lead with
Based on the JD signals, these experiences from your profile are strongest:
1. [specific experience from profile — quote the relevant line]
2. [...]
3. [...]

## Gaps to address
- [gap]: [suggested framing — or "skip if no honest angle"]

## Cover letter angle
[One-sentence framing for the cover letter opening — why this role, why now, why you]

## Things to research before the interview
- [specific technology / product / recent news worth knowing]
```

---

## STEP 4B — DRAFT TAILORED RESUME (skip if no `resume.md`)

Using `resume.md` as the only source of content — never invent experience, skills, or numbers that aren't already there — produce a version tailored to this job:

- Reorder bullets so the strongest matches (the "direct" fits from STEP 3) lead each section
- Rework phrasing to mirror the JD's exact keywords, only where it's honestly accurate
- Trim or de-emphasise sections least relevant to this role
- Keep every fact traceable back to `resume.md` — if you can't find it there, don't add it here either; that's a STEP 3 gap, not something to paper over

Write `jobs/discovered/[job-slug]/resume.md`:

```markdown
# Resume — [Name] — tailored for [Title] at [Company]

<!-- Generated from resume.md for this application. Review before using. -->

[tailored resume content, same format as resume.md]
```

Tell the user: "Tailored resume drafted at `jobs/discovered/[job-slug]/resume.md` — review it before using; I only reordered and reworded what's already in your resume, nothing invented. Say the word if you want it converted to PDF or Word."

---

## STEP 5 — UPDATE PIPELINE

If the job's status is still `discovered`, ask:
> "Ready to apply now, or just prepping for later?"

On "apply now" → hand off to the apply skill.

Append to `activity.md`:
```
[date] prepped application for [Company — Title]
```

---

## NOTES

- Don't invent experience that isn't in profile.md or resume.md. Flag gaps honestly — in STEP 4B too, not just the brief.
- ATS keyword matching matters — use the JD's exact phrasing where it's accurate.
- The brief is for the user to use, not for Claude to auto-fill — don't jump to applying without being asked.
