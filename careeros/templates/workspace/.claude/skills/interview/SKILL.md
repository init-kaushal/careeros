# CareerOS Interview Skill

**Trigger:** "prep for [company] interview" / "interview prep for [job]" / "I have an interview at [company]" / "prepare me for [company]"

---

## EVIDENCE GATE (mandatory)

**Applies to:** the STAR story outlines (STEP 3) and any answer you draft for the user (STEP 5). Check them before you write the interview prep file.

Every claim in text you write for the user must trace to their career memory (`career/`). Before you show, send, paste or save any draft as final:

1. Save the final text after your last edit (after `humanize`, if you used it) to a file, or pass it on standard input with `-`.
2. Run `careeros check <file> --against <job-id> --record`. Leave out `--against` when the text is not for one job.
3. **Exit 0:** tell the user the result in these words, with the real count: "Evidence check passed: every checkable claim (numbers, years, technologies, employers, schools, titles, certifications) is supported by your career memory. It does not evaluate prose, and N supporting fact(s) are still only claimed, not confirmed." Never call a draft "verified" or "true".
4. **Exit 1:** do not show the draft. For every finding, remove or rewrite the claim, or ask the user whether it is true. If they say it is, add it with `careeros memory add ... --quote "<their exact words>"` and run the check again. `CHK010` means the claims in one sentence do not come from a single fact: rewrite the sentence so it says only what one fact says, or ask. Never use `--allow` to get past a claim about the user's own history; it is only for names or terms that come from the job or from other people.
5. **Exit 2:** the career memory is missing or broken. Tell the user, run `careeros validate`, and stop. If the `careeros` command is not found, treat it as exit 2: tell the user and do not show or send the draft.

Re-run the check after every edit. Never skip it and never ignore a failure.

---

## STEP 0 — LOAD CONTEXT

Read:
1. `profile.md` — full file (you need skills and experience detail for STAR stories)
2. Job file from `jobs/discovered/`
3. `jobs/discovered/[job-slug]/company.md` if it exists
4. `jobs/discovered/[job-slug]/people.md` if it exists
5. `jobs/discovered/[job-slug]/prep.md` if it exists

Ask the user:
> "What stage is this interview? (e.g. recruiter screen, hiring manager, technical, system design, final loop, panel) — and do you know who you're interviewing with?"

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

Append to `activity.md`:
```
[date] interview prep [Company — Title] · stage: [type] · interviewers: [names if known]
```

Update the job file status to `interview` if not already:
- Change `Status: applied` → `Status: interview` in the job file
- Update `jobs/pipeline.md`: `[~]` → `[?]`

---

## NOTES

- STAR stories must use real experience from profile.md — never invent.
- "Why [company]?" requires specific research — don't answer it generically.
- If the user has already done prep, ask what stage they're at and skip to the relevant section.
- After the interview: ask for a brief debrief ("How did it go?") and log it to activity.md.
