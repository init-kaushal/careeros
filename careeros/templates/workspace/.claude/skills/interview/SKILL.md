# CareerOS Interview Skill

**Trigger:** "prep for [company] interview" / "interview prep for [job]" / "I have an interview at [company]" / "prepare me for [company]" / "debrief my [company] interview" / "what interviews are coming up?"

---

## EVIDENCE GATE (mandatory)

**Applies to:** the STAR story outlines (STEP 3) and any answer you draft for the user (STEP 5). Check them before you write the interview prep file.

Every claim in text you write for the user must trace to their career memory (`career/`). Before you show, send, paste or save any draft as final:

1. Save the final text after your last edit (after `humanize`, if you used it) to a file, or pass it on standard input with `-`.
2. Run `careeros check <file> --against <job-id> --record`. Leave out `--against` when the text is not for one job.
3. **Exit 0:** tell the user the result in these words, with the real count: "Evidence check passed: every checkable claim the checker could detect (numbers, years, durations, technologies, employers, schools, titles, certifications) is supported by your career memory. Prose was not evaluated, N supporting fact(s) are still only claimed, not confirmed, and a pass does not mean every claim in the draft was detected." Never call a draft "verified" or "true".
4. **Exit 1:** do not show the draft. For every finding, remove or rewrite the claim, or ask the user whether it is true. If they say it is, add it with `careeros memory add ... --quote "<their exact words>"` and run the check again. `CHK010` means the claims in one sentence do not come from a single fact: rewrite the sentence so it says only what one fact says, or ask. Never use `--allow` to get past a claim about the user's own history; it is only for names or terms that come from the job or from other people. If the only finding is CHK030 (the memory has no usable facts), tell the user to import their resume (see the memory skill) and stop.
5. **Exit 2:** a usage error (for example an unknown job id or an unreadable file) or a career memory that cannot be trusted. Tell the user, run `careeros validate`, and stop. If the `careeros` command is not found, treat it as exit 2: tell the user and do not show or send the draft.

Re-run the check after every edit. Never skip it and never ignore a failure.

---

## STEP 0 — LOAD CONTEXT

**Shortcuts.** If the user only asks what is coming up, run `careeros interview upcoming`, tell them what it lists, and stop there. If the user only wants to debrief a round, take the round id from `careeros interview round list --job <job-slug> --json` and go straight to STEP 7.

Read:
1. `profile.md` — full file (you need skills and experience detail for STAR stories)
2. Job file from `jobs/discovered/`
3. `jobs/discovered/[job-slug]/company.md` if it exists
4. `jobs/discovered/[job-slug]/people.md` if it exists
5. `jobs/discovered/[job-slug]/prep.md` if it exists

Ask the user:
> "What stage is this interview? (e.g. recruiter screen, hiring manager, technical, system design, final loop, panel) — and do you know who you're interviewing with?"

---

## STEP 0.5 — RECORD THE ROUND

Rounds are recorded with `careeros interview`, not in `activity.md`. Do this as soon as the user has told you the stage and the date.

1. Run `careeros interview round list --job <job-slug> --json`. If a listed round has the same stage and date as the one the user describes, it is already recorded: do not add it. If the user says the round is already recorded, do not add it again: use that round's id. Never add a round twice for the same job, stage and date.
2. Otherwise record it with the stage and interviewers exactly as the user gave them. For example:

```bash
careeros interview round add --job example-corp-platform --stage hiring_manager --format video --when 2026-11-12 --interviewer "Sam Example:Engineering Manager"
```

   `--stage` is one of `recruiter_screen`, `hiring_manager`, `technical`, `system_design`, `panel`, `final` or `other`; `--format` is `phone`, `video`, `onsite`, `take_home` or `other`; `--when` is the date (`YYYY-MM-DD`) or a UTC time (`YYYY-MM-DDTHH:MM:SSZ`). If the stage the user gave does not clearly match one of them, ask which it is. Add one `--interviewer "Name:Title"` for each person the user named, at most 10; a name cannot contain a colon, and the title may be left empty when it is not known. If the date is not known yet, ask for it; never guess one.
3. Run `careeros interview upcoming` and tell the user what is due in the next 14 days, with each round's prep status (`none`, `drafted` or `checked`).

A refusal ends that action and is relayed to the user: if a `careeros interview` command refuses (exit 1), for example `duplicate`, `wrong_state`, `not_found`, `invalid_input` or `record_unavailable`, stop that action, tell the user the code and what it said, and do not retry it or work around it.

---

## STEP 1 — RESEARCH INTERVIEWERS (if names provided)

For each interviewer:
1. `tabs_create_mcp` — new tab
2. Navigate to their LinkedIn profile
3. Extract: current role, past companies, tenure, any public posts or articles, shared background with you

Add to or create `jobs/discovered/[job-slug]/people.md` with an `## Interviewers` section.

Close the tab after each.

---

## STEP 2 — BUILD QUESTION BANK

Generate likely questions based on:
- The job title and level (IC vs. lead, seniority)
- The JD's must-haves and strong signals
- The interview stage (recruiter screen vs. technical vs. behavioural loop)

### Behavioural questions (all stages)
Standard bank adjusted for the role:
- "Tell me about yourself" — always
- "Why [company]?" — always
- "Tell me about a time you [led a project / resolved a conflict / made a technical decision with incomplete info / failed and recovered]"
- "What does [their repeated culture value from the JD] mean to you in practice?"

### Technical questions (engineering roles)
Based on the JD's stack signals:
- Core concepts for each must-have technology
- System design scope (if senior/staff: broad distributed systems; mid: service design; junior: component design)
- Coding round: typical pattern for the company if known (Leetcode medium, domain-specific, take-home)

### Role-specific questions
Manager / PM / design — generate appropriate domain questions.

---

## STEP 3 — DRAFT STAR STORY OUTLINES

For each behavioural question, draft a STAR outline using your actual experience from profile.md:

```
Question: Tell me about a time you led a project with competing priorities.

Situation: [context from your profile — team size, timeline, stakes]
Task: [what you owned]
Action: [3 specific things you did]
Result: [outcome — quantify where possible]
```

Draft 4–6 stories covering: leadership, conflict/ambiguity, technical decision, failure/learning, cross-functional work, impact at scale.

Show them and ask which to refine.

---

## STEP 4 — COMPANY REFRESHER

If `company.md` exists, extract a 3-bullet "day-of refresher":
- What they do and who their customers are
- One recent thing (funding, product launch, news)
- One culture/engineering signal to reference

If `company.md` doesn't exist, ask:
> "Want me to run company research first? It'll strengthen your 'Why [company]?' answer."

---

## STEP 5 — WRITE INTERVIEW PREP FILE

Write `jobs/discovered/[job-slug]/interview-prep.md`:

```markdown
# Interview Prep — [Title] at [Company]

**Stage:** [recruiter screen / technical / loop]
**Date:** [if known]
**Interviewers:** [names + roles]

## Company refresher
- [what they do]
- [recent news]
- [culture signal]

## Interviewers
[name] — [title], [N] years at company, [notable background]

## Question bank
### Behavioural
- [question]
- [question]

### Technical / Role-specific
- [question]
- [question]

## STAR stories
### [Story title]
**Q:** [question it answers]
**S:** [situation]
**T:** [task]
**A:** [actions]
**R:** [result]

## Your questions to ask them
- [thoughtful question about the team / role / challenges]
- [question that signals you've done your homework]
- [question about growth / success criteria]
```

---

## STEP 6 — LOG

The round itself is recorded with `careeros interview` (STEP 0.5 and STEP 7); `activity.md` is a readable log, not the record of the round. Append to `activity.md`:
```
[date] interview prep [Company — Title] · stage: [type] · interviewers: [names if known]
```

Update the job file status to `interview` if not already:
- Change `Status: applied` → `Status: interview` in the job file
- Update `jobs/pipeline.md`: `[~]` → `[?]`

---

## STEP 7 — AFTER THE ROUND

Take the round id from `careeros interview round list --job <job-slug> --json`. `careeros interview needs-debrief` lists the held rounds still waiting for a debrief and the scheduled rounds whose date has passed.

1. **Held, cancelled or moved, on the user's say-so only.** Ask whether the round took place. Only if the user says it took place, run `careeros interview round held <rnd_id> --source user`. Only if the user says it was cancelled, run `careeros interview round cancel <rnd_id> --reason "<the user's reason>" --source user`. If the user says it moved, run `careeros interview round reschedule <rnd_id> --when <new date>` with the date they give. Never record any of these from a guess, a calendar or the date alone.
2. **Debrief, one question at a time.** For a held round, ask each of these on its own and wait for the answer before the next:
   - "How did it go, in your own words?"
   - "What questions were you asked?"
   - "What would you do differently next time?"
   - "What is the next step, and is there a date for it?"

   Map the first answer to `strong`, `mixed` or `weak` only when the user's words clearly say which; otherwise ask the user to choose one. Never write a rating the user did not give. Record only what the user said, in their words:

   `careeros interview debrief <rnd_id> --rating <strong|mixed|weak> --asked "<a question they were asked>" --improve "<something to do differently>" --next-step "<what happens next>" --next-date <YYYY-MM-DD>`

   Repeat `--asked` and `--improve` once per item, at most 10 of each, and leave out any the user did not give. Each answer is at most 500 characters. If `debrief` is refused `invalid_input` because an answer is too long, ask the user once to shorten it, then run the command again with their shorter words. `--next-step` is required: if the user does not know it, record what they said (for example "waiting to hear back"). Leave out `--next-date` when there is no date. `debrief` is refused unless the round is `held`.
3. If `careeros validate` reports `INT008` or `INT009` (an update that was interrupted), re-run the same `round reschedule` or `debrief` command with the values the user gave; never edit the record by hand to fix it. `careeros interview round show <rnd_id>` does not print these codes: it shows the same facts as `date_unrecorded` ("date not recorded") and `unrecorded_debrief`. `INT008` is reported only for a scheduled round and `INT009` only for a held round, the only labels where the command can be re-run; on any other round the difference is kept as history and needs nothing.

A refusal ends that action and is relayed to the user, as in STEP 0.5; the only exception is the one re-ask for an answer that is too long.

---

## NOTES

- STAR stories must use real experience from profile.md — never invent.
- "Why [company]?" requires specific research — don't answer it generically.
- If the user has already done prep, ask what stage they're at and skip to the relevant section.
- After the interview: run STEP 7 to record what happened and the debrief.
