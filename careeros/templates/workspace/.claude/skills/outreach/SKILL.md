# CareerOS Outreach Skill

**Trigger:** "connect with [person]" / "send LinkedIn invite to [person]" / "draft outreach to [person]" / "send email to [person]"

Supports two outreach modes.

---

## MODE A — LINKEDIN CONNECTION REQUEST

### A1 — Find the person

Read `jobs/discovered/[job-slug]/people.md` to get the person's LinkedIn URL.

If no URL is on file, ask:
> "I don't have a LinkedIn URL for [name]. Can you paste it?"

### A2 — Check for duplicate

Read `activity.md`. If there's an existing entry `sent LinkedIn invite → [name]`, say:
> "A LinkedIn connection request was already sent to [name] on [date]. Sending another risks being flagged as spam. Are you sure?"

Stop unless user explicitly confirms.

### A3 — Draft the connection note

LinkedIn limit: **300 characters** (strict).

Draft a note that:
- Names the specific role at the company
- References one concrete thing about their work or the company (from `company.md` or their profile)
- Is human, not salesy
- Does NOT ask for a job in the first message

Run it through the `humanize` skill first — a 300-character note has no room for a stock phrase, and "is human, not salesy" above is exactly what that skill checks for.

Show the draft with character count:
> "[Note text] — [N]/300 characters"

Ask:
> "Send this note, or regenerate?"

On "regenerate": redraft with different angle. Repeat until approved.

### A4 — Send the invitation

1. `tabs_create_mcp` — new tab.
2. `navigate` to the person's LinkedIn URL.
3. Wait 3–4 seconds.
4. Find the "Connect" button. If it shows "Follow" instead, look for "More" → "Connect".
5. Click Connect.
6. If a modal appears with "Add a note" — click it.
7. Paste the approved note using `form_input`.
8. Show the filled modal with `computer`.

Say:
> "Ready to send this invite to [name]. Confirm?"

**Do not click Send until the user says yes.**

9. On approval: click "Send invitation" / "Send".
10. Confirm success (button disappears or confirmation message).

### A5 — Log

Append to `activity.md`:
```
[date] sent LinkedIn invite → [Name] ([Title] at [Company]) · note: [first 60 chars of note]…
```

---

## MODE B — EMAIL OUTREACH DRAFT

### B1 — Find context

Read the job file and `jobs/discovered/[job-slug]/people.md` for the person's role and any notes.

### B2 — Draft the email

Draft a short, human outreach email:
- Subject line (clear and specific, no clickbait)
- Body: 3–5 sentences max
  - Who you are + one specific credential
  - Why this company / role specifically (tie to company.md if available)
  - Concrete ask (30-min call / intro / referral — pick one)
- Sign-off with name from profile.md

Run it through the `humanize` skill before showing it.

Show the full draft including subject line.

Ask:
> "Use this draft, or regenerate?"

### B3 — Deliver the draft

CareerOS does not send email directly. Output the final approved email ready to paste:

```
To: [email if known, otherwise "—"]
Subject: [subject]

[body]
```

If the person's email is not on file, note that and suggest:
> "To find their email: check their LinkedIn profile, GitHub, or the company's team page. Once you have it, you can paste this draft directly."

### B4 — Log

Append to `activity.md`:
```
[date] drafted email outreach → [Name] ([Title] at [Company]) · subject: "[subject]"
```

If the user confirms they sent it, also log:
```
[date] sent email outreach → [Name] at [Company]
```

---

## NOTES

- LinkedIn connection notes: 300 characters hard limit. Always show count.
- One LinkedIn connection request per person, ever. Check activity.md before sending.
- Never guess or fabricate an email address.
- Email drafts are for the user to send — CareerOS does not send email autonomously.
