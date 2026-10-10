# CareerOS Prep Skill

**Trigger:** "prep for [company]" / "tailor resume for [job]" / "prep my application for [job]" / "what should I highlight for [company]?"

Run this before applying. It produces a tailoring brief — a cheat-sheet of what to emphasise in your resume, cover letter, and any written answers.

---

## EVIDENCE GATE (mandatory)

**Applies to:** the tailored resume (STEP 4B) and everything in the tailoring brief that states something about the user, including the cover-letter angle (STEP 4). Check each before you show it.

Every claim in text you write for the user must trace to their career memory (`career/`). Before you show, send, paste or save any draft as final:

1. Save the final text after your last edit (after `humanize`, if you used it) to a file, or pass it on standard input with `-`.
2. Run `careeros check <file> --against <job-id> --record`. Leave out `--against` when the text is not for one job.
3. **Exit 0:** tell the user the result in these words, with the real count: "Evidence check passed: every checkable claim the checker could detect (numbers, years, durations, technologies, employers, schools, titles, certifications) is supported by your career memory. Prose was not evaluated, N supporting fact(s) are still only claimed, not confirmed, and a pass does not mean every claim in the draft was detected." Never call a draft "verified" or "true".
4. **Exit 1:** do not show the draft. For every finding, remove or rewrite the claim, or ask the user whether it is true. If they say it is, add it with `careeros memory add ... --quote "<their exact words>"` and run the check again. `CHK010` means the claims in one sentence do not come from a single fact: rewrite the sentence so it says only what one fact says, or ask. Never use `--allow` to get past a claim about the user's own history; it is only for names or terms that come from the job or from other people. If the only finding is CHK030 (the memory has no usable facts), tell the user to import their resume (see the memory skill) and stop.
5. **Exit 2:** a usage error (for example an unknown job id or an unreadable file) or a career memory that cannot be trusted. Tell the user, run `careeros validate`, and stop. If the `careeros` command is not found, treat it as exit 2: tell the user and do not show or send the draft.

Re-run the check after every edit. Never skip it and never ignore a failure.

---

## STEP 0 — LOAD CONTEXT

Read:
1. `profile.md` — full file (above and below the fold if needed — you need skills detail)
2. `resume.md` — if it doesn't exist, ask: "You don't have a resume on file yet — paste it or a file path and I'll save it, or say 'skip' for a tailoring brief without a full tailored resume." Proceed without it only if they say skip — in that case, skip STEP 4B below.
3. The job file from `jobs/discovered/` matching the user's mention
4. If the job's `Market:` is not home and `markets/[country].md` exists, read its **Local application conventions**. Adapt the brief and resume to them — for example trim to a one- or two-page norm, or note the expected language — reordering and trimming only what is true. If a photo, date of birth or similar is customary, that's the user's decision: ask, don't add it.

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

There are two paths here depending on whether `profile.md` has a `## Resume format` section. Check it first.

### Path 1 — no `## Resume format` on file (markdown only)

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

Tell the user: "Tailored resume drafted at `jobs/discovered/[job-slug]/resume.md` — review it before using; I only reordered and reworded what's already in your resume, nothing invented. If you maintain your real resume in Overleaf/LaTeX, say so and I'll set up `## Resume format` so future applications skip straight to a properly-formatted PDF instead of this markdown draft."

### Path 2 — `## Resume format` is on file (Overleaf/LaTeX master + variants)

Don't invent a PDF pipeline (no reportlab, no from-scratch HTML-to-PDF) — the user's actual formatting lives in their Overleaf project, and that's the only thing that will visually match what they expect. Building an approximation wastes a full round-trip when it inevitably doesn't match.

**1. Pick a starting variant.** Read `## Resume variants` from `profile.md`. Score the JD's must-haves/strong signals (from STEP 2) against each variant's `Emphasis` line — plain keyword overlap is enough, this doesn't need to be fancy. Recommend the closest match. If it's a close call between two variants, or there are no variants yet, ask the user instead of guessing. Note the pick (or the ask) in the tailoring brief.

**2. Clone the variant**, don't edit it in place. In Claude-in-Chrome:
   - Open the chosen variant's Overleaf project URL.
   - `File → Make a copy` → name it `[Variant]_[Company]_[RoleSlug]` → Copy.
   - Work only in the new copy from here on. The variant project itself stays a clean, reusable starting point for the next application.

**3. Tailor on top of the variant** — light edits only, using the fit ratings from STEP 3:
   - Trim 1–2 weaker bullets per role if the variant runs long, keep the "direct" fits
   - Swap the Projects entry for whichever of the user's projects most closely matches this JD, if a better one exists
   - Never invent content — same rule as Path 1, everything must trace back to `resume.md` or a project the user has actually shipped

   **Editing safely in Overleaf's CodeMirror editor** (learned the hard way — read this before touching the keyboard):
   - Never use `ctrl+f` — it doesn't open a find UI here and falls through to literal keystrokes being typed into the document. Use Overleaf's own left-rail "Project search" (magnifying glass icon) to locate text instead.
   - To replace a block of text: click at the start of the first line → `Home` → shift-click at the start of the line right after the block → `shift+Home` → screenshot to confirm exactly what's highlighted (no more, no less) → `Delete` → type the replacement.
   - **After every multi-line replacement, zoom into that region and re-read it before moving on.** A selection that looks complete in a screenshot can still leave a trailing fragment of the old text stitched onto the new paragraph (this happened silently once — "...resource requests.Won the Eka.Care..." — the recompiled PDF was the only place it was visible). Don't trust the selection screenshot alone; verify the actual resulting text.
   - Braces/backslashes are typed literally as part of LaTeX source — if the editor auto-closes a brace you didn't intend, it'll show up doubled; check for that too in the same re-read pass.

**4. Recompile and verify** before downloading:
   - Click Recompile, wait for it to finish.
   - Screenshot the preview. Confirm: page count matches what the user asked for (usually 1), no leftover/garbled text at any edit point, no orphaned section (e.g. a bullet list under the wrong heading).
   - Download the PDF (download icon next to Recompile).

**5. Save it where the application expects it.** The download lands in the user's Downloads folder — move it (not copy; this is the working file) to `jobs/discovered/[job-slug]/[Name]_Resume_[Company].pdf`. If a file already exists at that path from an earlier draft, overwrite it — that's expected, it's this job's working copy, not a record to preserve.

**6. Still write the markdown mirror** at `jobs/discovered/[job-slug]/resume.md` (same format as Path 1) with the exact content that ended up in the PDF — this is what `apply` and future keyword/diff lookups read, so it needs to match the PDF, not the pre-edit draft.

Tell the user: "Tailored resume built from your `[Variant]` Overleaf variant, saved as `[Name]_Resume_[Company].pdf` in the job folder — review it before uploading. [N] bullets trimmed, projects section set to [Project]."

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
