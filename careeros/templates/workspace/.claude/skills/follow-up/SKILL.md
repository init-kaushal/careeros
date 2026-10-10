# CareerOS Follow-up Skill

**Trigger:** "check follow-ups" / "who needs a follow-up?" / "follow up with [person/company]" / "follow-up queue"

---

## EVIDENCE GATE (mandatory)

**Applies to:** every follow-up message drafted in STEP 3 (LinkedIn, email and application). Check each before STEP 4 shows it.

Every claim in text you write for the user must trace to their career memory (`career/`). Before you show, send, paste or save any draft as final:

1. Save the final text after your last edit (after `humanize`, if you used it) to a file, or pass it on standard input with `-`.
2. Run `careeros check <file> --against <job-id> --record`. Leave out `--against` when the text is not for one job.
3. **Exit 0:** tell the user the result in these words, with the real count: "Evidence check passed: every checkable claim (numbers, years, technologies, employers, schools, titles, certifications) is supported by your career memory. It does not evaluate prose, and N supporting fact(s) are still only claimed, not confirmed." Never call a draft "verified" or "true".
4. **Exit 1:** do not show the draft. For every finding, remove or rewrite the claim, or ask the user whether it is true. If they say it is, add it with `careeros memory add ... --quote "<their exact words>"` and run the check again. `CHK010` means the claims in one sentence do not come from a single fact: rewrite the sentence so it says only what one fact says, or ask. Never use `--allow` to get past a claim about the user's own history; it is only for names or terms that come from the job or from other people.
5. **Exit 2:** the career memory is missing or broken. Tell the user, run `careeros validate`, and stop.

Re-run the check after every edit. Never skip it and never ignore a failure.

---

## STEP 0 — LOAD CONTEXT

Read `activity.md` in full (past the fold if necessary — follow-up depends on history).
Read `jobs/pipeline.md` above the fold.
Read `profile.md` above the fold.

---

## STEP 0.5 — CHECK FOR REPLIES (best-effort)

If this agent session has an email tool available (e.g. Gmail), search it for messages to/from each pending contact's email or the company's domain, since the date logged in `activity.md`. Use whatever it finds to mark an item as already replied-to before building the queue — that item isn't overdue, it just needs a status update instead (ask the user, then log the outcome to `activity.md`).

If no email tool is available in this session, skip this step and rely on `activity.md` alone, as before.

---

## STEP 1 — BUILD THE FOLLOW-UP QUEUE

Scan activity.md for these event types and compute days since each:

| Event | Follow-up due after |
|-------|---------------------|
| `sent LinkedIn invite → [Name]` (no "accepted" entry following it) | 7 days |
| `sent email outreach → [Name]` (no reply logged) | 5 days |
| `applied [Company]` (no interview / rejection logged) | 10 days |
| `[~] Applied` job with no status change | 14 days |
| Interview scheduled / completed (no offer / rejection logged) | 3 days after interview date |

Today's date is available via the environment. Compute "days since" for each event.

**Skip:**
- Items where a follow-up was already sent within the cooldown window
- Items marked closed / rejected / offer-accepted in activity.md or pipeline.md

---

## STEP 2 — SHOW THE QUEUE

Present a table:

| # | Who / What | Type | Last contact | Days overdue |
|---|-----------|------|-------------|-------------|
| 1 | Jane Doe @ Stripe | LinkedIn invite | 2024-01-10 | 3 days overdue |
| 2 | Application @ Notion | Applied | 2024-01-08 | 5 days overdue |

If the queue is empty:
> "Nothing is due for follow-up right now."
Stop.

Ask:
> "Draft follow-ups for which items? (e.g. 1 3, or 'all', or 'q' to skip)"

---

## STEP 3 — DRAFT FOLLOW-UPS

For each selected item, draft the appropriate message.

### LinkedIn follow-up (after unanswered invite)

A short, warm message to send after they accept (check if they've accepted first — navigate to their profile and look for "Message" button appearing instead of "Connect"):

> "Hey [name] — thanks for connecting. I'm exploring [role type] roles and noticed [specific thing about their work / company]. Would you be open to a quick chat?"

Keep under 300 characters if sending via LinkedIn message.

### Email follow-up

Reference the original email:
> "Subject: Re: [original subject]
>
> Hi [name],
>
> Just following up on my note from [N] days ago. [One sentence reiterating the ask]. Happy to keep it brief — would 20 minutes work this week?
>
> [name]"

### Application follow-up

A short note to the recruiter (if known from people.md) or via the company's application portal:

> "Hi [recruiter name] — I applied for [role] on [date] and wanted to express continued interest. Happy to answer any questions. [name]"

Run whichever message got drafted through the `humanize` skill before moving to STEP 4 — these templates are a starting point, and filled-in verbatim they read as a template.

---

## STEP 4 — REVIEW AND CONFIRM

Show each draft. For each one ask:
> "Send / use this draft? (yes / regenerate / skip)"

- **yes → LinkedIn message:** navigate to person's profile, click Message, paste, confirm before sending.
- **yes → email:** output the draft ready to paste (CareerOS does not send email).
- **regenerate:** redraft.
- **skip:** move to next item.

**Do not send any LinkedIn message without explicit per-item approval.**

---

## STEP 5 — LOG

After each action:

```
[date] follow-up [type] → [Name / Company]: [brief description]
```

For closed items (user says "close this cadence"):
```
[date] closed follow-up cadence → [Name / Company]: [reason]
```

---

## NOTES

- Check if a LinkedIn invite has been accepted before drafting a follow-up message — navigate to their profile briefly to check.
- If someone hasn't accepted a LinkedIn invite after 14+ days, it's usually better to close the cadence than send a cold message.
- Never send the same follow-up type twice to the same person without a new trigger event.
- Email reply-checking (STEP 0.5) is best-effort and depends on what's connected in the current session — it's never required, and its absence never blocks the queue.
