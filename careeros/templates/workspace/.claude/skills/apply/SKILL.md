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

**LinkedIn "Share your profile?" modal**: clicking an external "Apply ↗" button on LinkedIn (jobs marked "Responses managed off LinkedIn") often triggers a profile-sharing consent modal first. Check `profile.md` for a `## Platform preferences` line about this:
- If a preference is already recorded, apply it silently and continue — don't ask again.
- If none is recorded, ask once: "LinkedIn wants to share your profile with the employer before continuing to their site — leave that on? (yes / no / just this once)". On "yes" or "no" (not "just this once"), write the answer under `## Platform preferences` in `profile.md` so future applications don't ask again.

**External ATS redirects** (iCIMS, Workday, Greenhouse, Lever, etc.): the job may open the employer's own applicant-tracking site in a new tab/domain. Treat that ATS the same as the board itself for the rest of this flow — the same login-wall and consequential-action rules below apply there too.

**Session timeouts**: ATS candidate sessions can expire from inactivity faster than you'd expect (observed on iCIMS: a several-minute gap while doing other work was enough to silently bounce the tab back to the start of the form, logged out). If you're going to pause mid-application to do unrelated work, either finish the current page first or expect to redo the login step afterward — re-screenshot before assuming a previously-filled page is still in the state you left it.

---

## STEP 3 — SURVEY THE FORM

Before filling anything, read the full form using `read_page`. List every field and its current value.

**Embedded forms**: some ATS platforms (iCIMS is a known one) render the real form inside an `<iframe>`, not the top-level document. If `read_page`/`form_input` find no usable fields (or only something unrelated like a language picker), check for an iframe and read/act inside `iframe.contentDocument` via `javascript_tool` instead.

**Mid-form login walls**: a login/password prompt can appear partway through a multi-step form, not just on the first page — e.g. after you enter an email, the ATS may recognize it as an existing candidate account and ask for a password before continuing. Treat this exactly like the login check in STEP 2:
> "This ATS wants you to log in to an existing account for [email] before continuing — log in yourself in the open tab, then tell me and I'll pick up from there."
Name the exact tab and field so it's a one-click job if they have a password manager extension (Bitwarden, 1Password, Keychain): "it's the password field on the tab now showing [ATS domain] — your password manager's autofill icon should pick it up."
**Never type a password, trigger a password reset, or use a browser-saved/autofilled password on the user's behalf — even if the browser shows one filled in.** Stop and wait for the user to confirm they're logged in.

**Returning-candidate pre-fill**: if the form already shows values (name, phone, resume, cover letter, past consent answers, etc.) from an earlier application, don't overwrite them from `profile.md` — verify they still match and flag anything that looks stale (an old resume filename, a generic cover letter that isn't the one tailored for this job) instead of silently replacing it.

**Consent / policy questions** (AI processing of your data, interview recording/transcription tools, EEO/voluntary disclosures, sponsorship): check `profile.md` for a `## Consent defaults` section first.
- If the question matches a recorded default, apply it silently and note what you set in the summary you show before filling — don't ask again.
- If it's a marketing/communications opt-in ("send me job alerts", "join our talent community") with no recorded default, opt out unless the ATS requires opting in to proceed with the application — in which case opt in and flag it: "had to opt in to marketing emails to continue — you can unsubscribe later."
- Anything else with no recorded default: ask, every time, and offer to save the answer to `## Consent defaults` if it's the kind of question likely to repeat.

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
- Never enter a password, complete a password reset, or authenticate into any account on the user's behalf — always hand that back to the user, on any site, at any step.
- If the form has a resume upload field, flag it: "There's a resume upload field — please upload your resume manually, then tell me when done."
- If the application was already submitted (confirmation page already shown), log it and stop rather than trying to resubmit.
- Close the tab after a successful submission.
