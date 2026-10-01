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

### Q4b — Working abroad
> "Would you also consider roles outside your home country? If yes, which countries or regions (e.g. Singapore, Thailand, Vietnam, Germany, the Netherlands), and would you need the employer to sponsor your visa?"

If no: skip Q5b and leave `## Markets` as the home market only.
If yes: for each country or region, capture the country/city and whether sponsorship is **required**, **preferred**, or **not needed** (e.g. remote from home, or already eligible). Ask which one is the top priority.

### Q5 — Compensation floor
> "What's your minimum acceptable compensation? (annual **base**, in whatever currency you think in — this is for your home market; I'll ask about other markets next if you listed any)"

Capture: number + currency. Store it as a **base** figure — stock and bonuses are upside, not part of the floor.

### Q5b — Floors for other markets (only if Q4b listed markets)
> "For each market you named, what's the minimum annual base you'd accept, in that country's currency? If you don't know what's realistic, say 'help me benchmark' — I'll leave it blank and fill it in after the market research."

Capture one floor per market, or `TBD — benchmark`. Never guess or convert a floor silently from the home number; salaries, taxes and living costs differ too much.

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

**If Q4b listed other markets**, also read `boards-catalog.md` (in this skill's directory) and offer the boards for each market: *"For [market] I'd suggest [boards from the catalog]. Add them? (yes / pick / skip)"*. Instahyre and Naukri are India boards — don't offer them for other markets. Note each board's status in the catalog: prefer `checked` ones, and tell the user when a board is `unchecked` or `browser-only`.

### Q8 — LinkedIn search URL (only if LinkedIn selected)
> "For LinkedIn, I'll use a default search for Senior Software Engineer in your city. Should I use that, or do you have a specific search URL you prefer?"

If they say default: construct the URL from their location and target roles.
If they paste a URL: use it as-is.

### Q9 — Resume intake
> "Last one: do you have an existing resume? Paste the text, or give me a file path and I'll read it. I'll use it as the source of truth for `apply` and `prep` — no invented experience, just yours, tailored per job. (Say 'skip' if you don't have one handy — you can add it later by saying 'add my resume'.)"

If they paste text: capture it verbatim.
If they give a file path: read the file (.txt/.md directly; for .pdf/.docx, extract text if your runtime has a tool for it, otherwise ask them to paste the text instead).
If they say skip: note that no resume is on file yet.

### Q9b — Resume format (only if they didn't skip Q9)
> "Is your actual resume PDF built from a LaTeX project (e.g. Overleaf), or is plain text/Word close enough to how it should look?"

If they name an Overleaf (or other LaTeX) master project:
- Ask for the project URL if not already given.
- Capture it — this becomes the `## Resume format` master in `profile.md`, and `prep` will clone it per application instead of trying to reconstruct the visual format from scratch.
- Ask: "Want me to set up a couple of resume variants now — different emphases of your experience for different role types (e.g. platform/infra-heavy vs. backend/distributed-systems-heavy) — so `prep` can pick the closest match instead of tailoring from the same starting point every time? (yes / later)" If yes, this is a `prep`-adjacent task, not part of onboarding itself — note it in the finish message so the user can trigger it explicitly ("set up resume variants").

If plain text/Word is fine: skip — `prep` will use the markdown-only path.

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
- **Minimum compensation (base, home market):** [from Q5]
- **Remote preference:** [from Q4]
- **Target companies:** [from Q6]

## Markets
*(where you'd work and the terms for each; written from Q4b/Q5b. Home market first. The `browse` comp check uses the matching market's floor. Visa and salary rules are not stored here — they change, so the market research looks them up and dates them.)*

| Market | Min base (per year) | Sponsorship | Priority |
|--------|---------------------|-------------|----------|
| [home country/city] (home) | [from Q5] | not needed | [1/2/3] |
| [other market, e.g. Singapore] | [from Q5b, or TBD — benchmark] | [required / preferred / not needed] | [1/2/3] |

## Platform preferences
*(filled in as they come up — one-time consent choices on job boards/ATS platforms that would otherwise be asked on every application, e.g. LinkedIn's profile-sharing toggle)*

## Consent defaults
*(filled in as they come up — recurring consent-style questions on application forms: AI/data processing, marketing opt-ins, interview recording tools, etc. See the apply skill's STEP 3 for how these get read and applied.)*

## Known ATS platforms
*(filled in as they come up — per-vendor quirks worth knowing before the next application on the same ATS, e.g. "iCIMS: forms are inside an iframe, file uploads can't be automated, sessions can time out within a few minutes of inactivity." See the apply skill's STEP 2/3 for how these get read.)*

## Resume format
*(only if the user's resume PDF comes from a LaTeX/Overleaf master rather than plain text — see the onboard skill's Q9b)*
<!--
**Master:** [Overleaf project name] — [project URL] (never edit directly — always clone)

## Resume variants
- **[Variant name]** — [Overleaf project URL]
  Emphasis: [2-4 keywords/themes this variant leads with]
  Best for: [role types this fits]
  Projects section: [which of the user's projects this variant showcases]
(add more as they're built — each one follows the same clone-from-master pattern; see the prep skill's STEP 4B)
-->

## Scoring rubric (use when rating job listings 0–10)
| Signal | Weight |
|--------|--------|
| Role title matches target roles | High |
| Primary language(s) in stack | High |
| Distributed systems / platform / infra work | High |
| Location match or remote-friendly | Medium |
| Seniority level matches target | High |
| Company type matches preference | Medium |
| Compensation signals above the minimum for that listing's market | Medium |
| Listing is in a market from `## Markets` and its sponsorship need is met (offered, or not needed) | Medium |
| Sponsorship is required but the listing says none, or demands existing right to work | Negative — deprioritize |

## ── HISTORY (on demand; do not read past this line) ──
```

### 2. `boards.md`

Only include boards the user selected. For each:

```markdown
# Job Boards

## Browser-based (use Claude-in-Chrome with your real session)

Every board entry has a `**Market:**` line — the market from `## Markets` that board serves (use `home` for the home market). `browse` uses it to pick the right comp floor.

### linkedin  ← only if selected
- **Browse URL:** [URL from Q8, or constructed default]
- **Market:** home
- **Session:** Log in via your regular Chrome before browsing
- **Notes:** Adjust keywords in the URL to change what you see

### instahyre  ← only if selected
- **Browse URL:** `https://www.instahyre.com/candidate/opportunities/?matching=true`
- **Market:** home
- **Session:** Log in via your regular Chrome before browsing
- **Notes:** Shows personalized matched opportunities

### wellfound  ← only if selected
- **Browse URL:** `https://wellfound.com/jobs?role=software-engineer&location=[city]`
- **Market:** home
- **Session:** Log in if prompted

### naukri  ← only if selected
- **Browse URL:** `https://www.naukri.com/[role-slug]-jobs-in-[city]`
- **Market:** home
- **Session:** Log in if prompted

### Regional boards  ← only for markets from Q4b, copied from `boards-catalog.md`
For each, use the same fields (`Browse URL`, `Market`, `Session`, `Notes`) and set `**Market:**` to that market's name.

## Adding a new board
Add an entry here with the browse URL and a `**Market:**` line. Say "browse [board]" to use it.
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
> - **Markets:** [home market, plus any others from Q4b — edit `## Markets` in `profile.md` to change floors or sponsorship needs]
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
