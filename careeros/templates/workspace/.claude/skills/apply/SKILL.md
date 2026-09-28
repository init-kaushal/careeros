# CareerOS Apply Skill

**Trigger:** "apply to [job]" / "apply for [company]" / "fill application for [job]"

---

## STEP 0 — IDENTIFY THE JOB

Read the job file from `jobs/discovered/`. Match the user's mention to a file by company name or title.

If ambiguous, show a numbered list and ask which one.

Read `profile.md` above the fold — you'll need name, email, phone, location, and the scoring notes.

---

## STEP 1 — DRAFT COVER LETTER

Draft a cover letter tailored to this job. Structure:
- Opening: why this role + company specifically (1 sentence)
- Body: 2–3 paragraphs linking profile skills to the job's visible requirements
- Close: brief, no clichés

Show the draft. Ask:
> "Cover letter looks good? (yes / regenerate / skip cover letter)"

On "regenerate": redraft with different emphasis. On "skip": proceed without one.

---

## STEP 2 — OPEN THE APPLICATION

1. `tabs_context_mcp` — check what's open.
2. `tabs_create_mcp` — new tab.
3. `navigate` to the job URL from the job file.
4. Wait 3–4 seconds.

If redirected to login:
> "You're not logged in to [board]. Log in and say 'apply to [job]' again."
Stop.

Look for an "Apply" or "Easy Apply" button. Click it.

---

## STEP 3 — SURVEY THE FORM

Before filling anything, read the full form using `read_page`. List every field and its current value.

Show the user what you see:
> "I can see these fields: [name, email, phone, ...]. I'll fill from your profile. Any fields you want to handle yourself?"

Wait for their reply.

---

## STEP 4 — FILL THE FORM

Fill each field from `profile.md`. Use `form_input` for text fields.

**LinkedIn Easy Apply multi-page forms**: fill one page at a time, click "Next", re-read the form, repeat.

**Typical field mapping from profile.md:**
- First / Last name → name
- Email → email
- Phone → phone
- Location → current location
- Years of experience → experience
- LinkedIn URL → linkedin field
- Resume upload → if a tailored resume exists at `jobs/discovered/[job-slug]/resume.md` (from the prep skill), tell the user it's ready to upload from there; otherwise skip and note it for the user to handle manually
- Cover letter → paste the drafted letter

**For questions you can't answer from profile** (e.g. "Do you require visa sponsorship?", "Expected salary"):
Stop and ask the user for each one individually before proceeding.

---

## STEP 5 — REVIEW BEFORE SUBMIT

Take a screenshot with `computer` and show it.

Say:
> "Ready to submit. Here's what's filled in: [field summary]. Should I submit?"

**Do not click Submit until the user says yes.** This is irreversible.

---

## STEP 6 — SUBMIT AND LOG

On user approval:
1. Click Submit / Finish.
2. Confirm submission confirmation page appeared.

Update the job file — change `Status: discovered` → `Status: applied` and add an Applied date.

Update `jobs/pipeline.md`: change `- [ ]` → `- [~]` for this job.

Append to `activity.md`:
```
[date] applied [Company — Title] via [board] · cover letter: [yes/no]
```

---

## NOTES

- Never click Submit without explicit user approval — this is an irreversible external action.
- If the form has a resume upload field, flag it: "There's a resume upload field — please upload your resume manually, then tell me when done."
- If the application was already submitted (confirmation page already shown), log it and stop rather than trying to resubmit.
- Close the tab after a successful submission.
