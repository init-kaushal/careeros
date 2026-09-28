# CareerOS Onboarding Skill

**When to run:** `profile.md` does not exist — run the full interview before any other skill. Also run just the RESUME INTAKE section on its own whenever the user says "add my resume" / "update my resume" / "here's my resume", even if `profile.md` already exists.

---

## Overview

Walk the user through a short setup interview. Ask one question at a time. Write all workspace files at the end.

Do not overwhelm — keep it conversational. The whole flow should feel like a 5-minute chat, not a form.

---

## THE INTERVIEW

Ask these questions, one at a time, in order. Wait for an answer before asking the next.

### Q1 — Role and experience
> "Let's get your workspace set up. First: what's your current role and how many years of experience do you have?"

Capture: job title, years of experience.

### Q2 — Tech stack
> "What's your primary tech stack? (Languages, frameworks, infra — whatever you spend most of your time on)"

Capture: languages, frameworks, infra tools.

### Q3 — Target roles
> "What roles are you targeting? (e.g. Senior SWE, SDE-2, Staff Engineer — or just describe what you're looking for)"

Capture: target titles.

### Q4 — Location
> "Where are you based, and what's your work arrangement preference? (remote / hybrid / in-office / open)"

Capture: city/country, remote preference.

### Q5 — Compensation floor
> "What's your minimum acceptable compensation? (annual, in whatever currency you think in)"

Capture: number + currency.

### Q6 — Target companies
> "Any specific companies or types of companies you're targeting? (e.g. MNCs, Series B startups, product companies)"

Capture: company preferences.

### Q7 — Boards to enable
> "Which job boards do you want to use? I'll set them all up.
>
> 1. LinkedIn
> 2. Instahyre
> 3. Wellfound (AngelList)
> 4. Naukri
>
> Which ones? (say 'all' or pick numbers)"

Capture: selected boards.

### Q8 — LinkedIn search URL (only if LinkedIn selected)
> "For LinkedIn, I'll use a default search for Senior Software Engineer in your city. Should I use that, or do you have a specific search URL you prefer?"

If they say default: construct the URL from their location and target roles.
If they paste a URL: use it as-is.

### Q9 — Resume intake
> "Last one: do you have an existing resume? Paste the text, or give me a file path and I'll read it. I'll use it as the source of truth for `apply` and `prep` — no invented experience, just yours, tailored per job. (Say 'skip' if you don't have one handy — you can add it later by saying 'add my resume'.)"

If they paste text: capture it verbatim.
If they give a file path: read the file (.txt/.md directly; for .pdf/.docx, extract text if your runtime has a tool for it, otherwise ask them to paste the text instead).
If they say skip: note that no resume is on file yet.

---

## WRITE THE FILES

After Q8, say: "Got it — setting up your workspace now."

Then write all files in one go:

### 1. `profile.md`

```markdown
# [Name or "Your"] Career Profile

**Role:** [current title]
**Experience:** [N] years
**Location:** [city, country]
**Contact:** (add your email if you'd like)

## Target Roles
[list each target title on its own line]

## Summary
[2-3 sentence description of the user based on their answers — write it for them, they can edit]

## Core Skills
**Languages:** [from Q2]
**Infrastructure:** [from Q2]
**Other:** [from Q2]

## Preferences
- **Minimum compensation:** [from Q5]
- **Remote preference:** [from Q4]
- **Target companies:** [from Q6]

## Platform preferences
*(filled in as they come up — one-time consent choices on job boards/ATS platforms that would otherwise be asked on every application, e.g. LinkedIn's profile-sharing toggle)*

## Consent defaults
*(filled in as they come up — recurring consent-style questions on application forms: AI/data processing, marketing opt-ins, interview recording tools, etc. See the apply skill's STEP 3 for how these get read and applied.)*

## Scoring rubric (use when rating job listings 0–10)
| Signal | Weight |
|--------|--------|
| Role title matches target roles | High |
| Primary language(s) in stack | High |
| Distributed systems / platform / infra work | High |
| Location match or remote-friendly | Medium |
| Seniority level matches target | High |
| Company type matches preference | Medium |
| Compensation signals above minimum | Medium |

## ── HISTORY (on demand; do not read past this line) ──
```

### 2. `boards.md`

Only include boards the user selected. For each:

```markdown
# Job Boards

## Browser-based (use Claude-in-Chrome with your real session)

### linkedin  ← only if selected
- **Browse URL:** [URL from Q8, or constructed default]
- **Session:** Log in via your regular Chrome before browsing
- **Notes:** Adjust keywords in the URL to change what you see

### instahyre  ← only if selected
- **Browse URL:** `https://www.instahyre.com/candidate/opportunities/?matching=true`
- **Session:** Log in via your regular Chrome before browsing
- **Notes:** Shows personalized matched opportunities

### wellfound  ← only if selected
- **Browse URL:** `https://wellfound.com/jobs?role=software-engineer&location=[city]`
- **Session:** Log in if prompted

### naukri  ← only if selected
- **Browse URL:** `https://www.naukri.com/[role-slug]-jobs-in-[city]`
- **Session:** Log in if prompted

## Adding a new board
Add an entry here with the browse URL. Say "browse [board]" to use it.
```

### 3. `jobs/pipeline.md`

```markdown
# Job Pipeline

## Active

*No jobs tracked yet. Say "browse [first board]" to start.*

## ── HISTORY (on demand; do not read past this line) ──
```

### 4. `activity.md`

```markdown
# Activity Log

<!-- append-only; newest entries at top -->

[today's date] onboarding complete — workspace initialized
```

### 5. `resume.md` (only if the user provided one in Q9)

Write their resume verbatim, as given:

```markdown
# Resume — [Name]

<!-- Source of truth for tailored resumes and applications. Update anytime by saying "add my resume" or "update my resume". -->

[pasted or extracted resume text, unedited]
```

If they skipped Q9, don't create this file — `prep` and `apply` will ask for it when needed.

### 6. Create directory `jobs/discovered/` (empty, just the directory)

---

## FINISH

After writing all files, say:

> "You're all set. Your workspace is ready at a glance:
>
> - **Profile:** `profile.md` — edit anytime to adjust your scoring rubric or preferences
> - **Boards configured:** [list the boards they chose]
> - **Resume:** [`resume.md` saved — I'll use it for tailored resumes and applications. / not on file yet — say "add my resume" anytime.]
> - **Pipeline:** `jobs/pipeline.md` — tracks everything you save
>
> To start: say **'browse [board]'** and I'll open it in Chrome, pull listings, score them against your profile, and save what you want to keep."

---

## RESUME-ONLY INTAKE (profile.md already exists)

Triggered by: "add my resume" / "update my resume" / "here's my resume".

1. Ask Q9 verbatim (above).
2. Write/overwrite `resume.md` as in step 5 above.
3. Append to `activity.md`:
   ```
   [date] resume added/updated on file
   ```
4. Say: "Got it — saved to `resume.md`. I'll use this for tailored resumes and applications from now on."
