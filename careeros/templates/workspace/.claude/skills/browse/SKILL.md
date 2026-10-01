# CareerOS Browse Skill

**Trigger:** "browse [board]" / "find me jobs on [board]" / "search [board]"

---

## STEP 0 — PREPARE

Read these files before touching the browser:
1. `profile.md` — above the fold only. Internalize target roles, stack, scoring rubric, and the **comp floor** (minimum compensation). You will check every shortlisted job against it in STEP 3.
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

**For Instahyre** (React SPA — no `<a>` job links, and `get_page_text` often returns only a popup):

1. Call `read_network_requests` once *first* — tracking only starts at the first call — then reload the page and call it again with `urlPattern: "instahyre.com/api"`.
2. It returns request **URLs only, never response bodies**. Find the `candidate_opportunities/candidate_matching?...limit=30&offset=0` URL.
3. Fetch that URL from inside the logged-in page with `javascript_tool` (read-only GET, same origin, `credentials: 'include'`) and keep it on `window.__opps`:
   ```javascript
   const r = await fetch('/api/v1/candidate_opportunities/candidate_matching?...&limit=30&offset=0', {credentials:'include'}).then(r => r.json());
   window.__opps = r.objects;
   ```
4. Useful fields: `employer.company_name`, `employer.employee_count`, `job.title`, `job.locations`, `job.keywords`, `job.opportunity_url` (prefix `https://www.instahyre.com`). Ignore Instahyre's own `score` — most jobs sit at a flat 4.5.
5. `javascript_tool` output is cut off at roughly 600 characters. Print compact rows (`company|title|location`) in slices of ~10–15.
6. A "change your job search status" modal may cover the page. Both buttons change an account setting — do not click either. Press Escape or ignore it; the API fetch works regardless.

---

## STEP 3 — SCORE

Score each listing 0–10 using the rubric in profile.md:
- 8–10: Strong match
- 5–7: Partial match
- 1–4: Weak match
- 0: Clearly wrong (drop it)

### Comp check (required — do not skip)

List data (title, company, location) cannot tell you whether a job clears the comp floor. After the first-pass score, take the **top 10–12** and look up compensation for the role, level and city **before presenting**. Never present a table with comp "unchecked" — if you could not find data, say **unknown** and name what you tried.

Sources, in order:
1. **Levels.fyi** — `levels.fyi/companies/[company]/salaries/software-engineer`. `WebFetch` often returns an empty page here; open it in a browser tab and use `get_page_text` instead of giving up.
2. **AmbitionBox / Glassdoor / Weekday** — `[company] [title] salary`. Self-reported and skewed low; treat as a floor, often fixed pay only.
3. **LeetCode Discuss "compensation" posts** and **Blind** — individual offers; note the year (they go stale).
4. The listing's own page (Instahyre/Wellfound/LinkedIn job page often shows a band).

Record per job: the range, whether it is fixed or total comp, the source, and a confidence (high / medium / low / unknown). Then compare with the comp floor in profile.md — like with like: if the floor is a base figure, compare base to base, and treat stock and bonuses as upside — and adjust the score: clearly below the floor → cap at 6 and say why; borderline → keep and flag; unknown → keep, flag, and note the gap. Early-stage startups usually have no public data — mark them unknown rather than guessing.

---

## STEP 4 — PRESENT

Show a markdown table sorted by score descending:

| # | Score | Company | Title | Location | Comp (source, confidence) | URL |
|---|-------|---------|-------|----------|---------------------------|-----|

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
- **Chrome connection flakiness:** "tab not in group" / "couldn't determine which page" errors often alternate — retry the same call once or twice. If it keeps failing, run `list_connected_browsers`: another Chromium browser with the extension installed (Arc, Brave) can steal the connection, especially if it is the OS default browser. Disable the extension there and log in to the board in the browser you want driven. A `navigate` with no `tabId` can land in a different profile than the one you read from.
- Close every tab you opened before finishing.
- Dismiss premium popups before reading: look for "No thanks" / "Continue" links.
- Never navigate the user's existing tabs.
