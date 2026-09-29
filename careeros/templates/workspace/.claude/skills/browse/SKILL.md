# CareerOS Browse Skill

**Trigger:** "browse [board]" / "find me jobs on [board]" / "search [board]"

---

## STEP 0 — PREPARE

Read these files before touching the browser:
1. `profile.md` — above the fold only. Internalize target roles, stack, scoring rubric.
2. `boards.md` — find the entry for the requested board. Get the browse URL.

If the board is not in boards.md, ask the user for the URL and add it before proceeding.

---

## STEP 1 — OPEN THE BOARD

1. `tabs_context_mcp` — see what's open.
2. `tabs_create_mcp` — open a fresh tab.
3. `navigate` to the board's browse URL from boards.md.
4. Wait 3–4 seconds for JS-heavy pages to settle.

If redirected to login:
> "You're not logged in to [board] in Chrome. Log in and say 'browse [board]' again."
Stop — do not proceed without a live session.

---

## STEP 2 — EXTRACT LISTINGS

Use `read_page` (accessibility tree) or `javascript_tool` to extract job cards.

For each listing capture: title, company, location, url, snippet (visible tech/level text).

Aim for 15–25 listings. If the page has infinite scroll, scroll once:
```javascript
window.scrollTo(0, document.body.scrollHeight);
```
Wait 2s, read again.

**If 0 results from `read_page`**, try direct DOM extraction:
```javascript
Array.from(document.querySelectorAll('a[href]'))
  .filter(a => /\/(job|career|position|opportunit)/i.test(a.href))
  .map(a => ({href: a.href, text: a.innerText.trim()}))
  .slice(0, 30)
```

**For Instahyre** (React SPA — no `<a>` job links): use `read_network_requests` to capture the JSON API response that feeds the job cards, then parse job data from it.

---

## STEP 3 — SCORE

Score each listing 0–10 using the rubric in profile.md:
- 8–10: Strong match
- 5–7: Partial match
- 1–4: Weak match
- 0: Clearly wrong (drop it)

---

## STEP 4 — PRESENT

Show a markdown table sorted by score descending:

| # | Score | Company | Title | Location | URL |
|---|-------|---------|-------|----------|-----|

Then ask:
> "Pick jobs to save (e.g. 1 3 5), or 'q' to skip."

---

## STEP 5 — SAVE

Every job gets its own directory — `prep`, `apply`, `outreach`, and `track` all write and read files (`prep.md`, `resume.md`, `company.md`, `people.md`, tailored resume/cover-letter PDFs) alongside the job record, so a flat file per job doesn't hold them. Use the same slug everywhere: `[company-slug]-[title-slug]` (lowercase, hyphenated, no date — the date lives inside the file).

For each selected job:

**A.** Create `jobs/discovered/[company-slug]-[title-slug]/job.md`:
```markdown
# [Title] at [Company]

- **Board:** [board]
- **Location:** [location]
- **URL:** [url]
- **Score:** [score]
- **Discovered:** [date]
- **Status:** discovered

## Notes
[snippet]
```

**B.** Append to `jobs/pipeline.md` above the fold under `## Active`:
```
- [ ] **[Company]** — [Title] · [location] · Score [N] · [date] · [url]
```

**C.** Append to `activity.md`:
```
[date] browse [board] → saved [N] jobs: [Company1 — Title1], ...
```

---

## NOTES

- Always create a new tab — never navigate an existing one.
- Dismiss premium popups before reading: look for "No thanks" / "Continue" links.
- Never navigate the user's existing tabs.
