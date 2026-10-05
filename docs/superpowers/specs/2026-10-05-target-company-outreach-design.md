# Target-company outreach (pre-opening) — design

**Date:** 2026-10-05
**Status:** approved in conversation, pending written-spec review
**Runtimes:** Claude Cowork (full), GPT Work (list-building only)

## 1. Goal

Today CareerOS reaches recruiters and engineering managers only after `browse` saves a job. This
design reverses the order for a chosen set of companies: build a ranked target-company list, find
hiring contacts there, and send short introduction notes **before** any role is open, so the user is
a known name when a matching opening appears.

**Success looks like:**

- The user can say "build target companies" and get a ranked, evidenced list covering every market
  that has a `markets/*.md` file.
- The user can say "reach out to my targets" and get drafted introduction notes, approved and sent
  one at a time, within a weekly invite cap.
- When `browse` later saves a job at a target company, the existing contacts are reused and the route
  question defaults to messaging them.

## 2. Decisions made with the user

| Decision | Choice |
|---|---|
| Market scope | Every market with a research file in `markets/`, tagged by market |
| Ranking | Home market by fit. Non-home markets sponsorship-friendly first, then fit |
| Architecture | New `targets` skill + new pre-opening mode in `outreach` + small hooks in `browse` and `follow-up` |
| Weekly invite cap | 15 (setting in `profile.md`, default when absent) |
| New candidates per market per run | at most 12 |
| Staleness | a target not touched for 60 days is shown as stale and offered a refresh |
| First message | an introduction. It never claims an opening exists and never asks for a job |
| Sending | the user approves every note, as today |

## 3. Data model

All new files live in the user's workspace, never in the repo templates.

### 3.1 `targets.md` (workspace root)

```markdown
# Target Companies

**Updated:** [date]

| Company | Slug | Market | Fit | Sponsorship | Evidence (source, date) | Status | Contacts | Last touched |
|---------|------|--------|-----|-------------|-------------------------|--------|----------|--------------|
| Atlassian | atlassian | Australia | 8 | likely | sponsor register hit — [url], 2026-10-05 | approved | 0 | 2026-10-05 |

## Dropped
| Company | Market | Reason | Date |
|---------|--------|--------|------|
```

- **Sponsorship** is one of `offered`, `likely`, `unknown`, `not offered`, or `n/a` for the home
  market. Every value except `n/a` needs an evidence entry with a source and the date it was read.
- **Status** lifecycle: `candidate` → `approved` → `contacted` → `warm` → `opening-seen`. `dropped`
  can happen from any state and moves the row under `## Dropped`.
- **Slug** is the lowercase, hyphenated company name. It is the join key with
  `jobs/discovered/[company-slug]-[title-slug]/` and `targets/[slug]/`.

### 3.2 `targets/[slug]/people.md`

Same format as the `people.md` that `research` already writes into job folders (name, headline,
location, role as recruiter / hiring-manager / peer, public profile URL), plus a `Searched:` date.
Using the same format lets `outreach` and `browse` read it without translation.

### 3.3 `profile.md`: optional `## Outreach` section

```markdown
## Outreach
- **Weekly invite cap:** 15
```

If the section or the line is absent, the cap is 15. Onboarding does not ask for it.

### 3.4 `activity.md` lines

```
[date] target added: [Company] ([Market]) — fit [N], sponsorship [value]
[date] sent LinkedIn invite → [Name] ([Title] at [Company]) (pre-opening) · note: [first 60 chars]…
[date] LinkedIn invite accepted → [Name]
[date] target opening seen: [Company] — [Title]
```

## 4. `targets` skill (new)

**Triggers:** "build target companies", "find targets for [market]", "show targets", "refresh targets".

### Step 0: Load context

Read `profile.md` (target roles, stack, excluded roles, the "Target companies" line, `## Markets`),
every `markets/*.md`, `boards-catalog.md`, `targets.md` if present, and the companies in
`jobs/discovered/*/job.md`.

### Step 1: Candidate generation, per market

For each market in `markets/`, gather up to **12 new** candidates not already in `targets.md`:

1. **Seeds:** companies named in `profile.md`'s "Target companies" line, and companies of saved jobs
   scoring 7 or higher (home and non-home).
2. **Sponsor register:** if the market file names a register of licensed sponsors, companies found
   there that hire for the user's roles.
3. **Regional boards:** companies appearing on the market's boards from `boards-catalog.md` for the
   user's target roles.
4. **Levels.fyi company lists** filtered to the country, when readable.

Skip any company whose only visible roles are in the profile's excluded role families.

### Step 2: Evidence and scoring

- **Fit (0–10)** uses the same rubric as `browse` (role, stack, seniority) and a pay check against the
  market floor in `## Markets`. Unknown pay is flagged, not guessed.
- **Sponsorship (non-home markets)** is graded from evidence only:
  - `offered`: a listing or careers page states visa or relocation support.
  - `likely`: the company appears on the market's sponsor register, or a large multinational with
    visa-supporting listings on record.
  - `unknown`: nothing found either way (the common case).
  - `not offered`: a listing or page states no sponsorship. In a market where the `## Markets` row
    says sponsorship is required, these go straight to `## Dropped` with the reason.
- **Ranking:** home market sorted by fit descending. Non-home markets sorted by sponsorship tier
  (`offered`, then `likely`, then `unknown`), then fit descending. Never fill in a tier from memory.

### Step 3: Present and approve

Show one table per market, in the ranking above. The user approves or drops rows (for example
"approve 1 2 4, drop 3"). Approved rows become `approved`, dropped rows move to `## Dropped`.

### Step 4: Find contacts for approved targets

For each approved target, run `research` Mode B1b (the company's LinkedIn People tab, single-keyword
queries) and write `targets/[slug]/people.md`: recruiters or talent partners whose headline says they
are hiring, the engineering manager the user's role would report to, and one peer engineer.
Founders and C-level only as a fallback. Record the number of contacts found in `targets.md`. If
LinkedIn cannot be read, keep the target and report that the contact search is pending.

### Step 5: Hand off

Append the `target added` activity lines, update `targets.md`, and finish by saying:
"Say 'reach out to my targets' to draft introductions." The skill never sends anything.

### Staleness

"Show targets" marks any row whose `Last touched` is more than 60 days old as `stale` and offers
"refresh targets", which re-checks the sponsorship evidence and contacts and updates the date.

## 5. `outreach`: new Mode C, pre-opening introduction

**New triggers:** "reach out to my targets", "pre-opening outreach", "introduce myself at [company]".

### C1: Check the cap

Count lines matching `sent LinkedIn invite →` in `activity.md` over the last 7 days. This counts
**all** invites, role-based and pre-opening, because LinkedIn's limits apply to the account. Read the
cap from `profile.md`'s `## Outreach` section, default 15. If the cap is reached, say so with the
date the oldest counted invite ages out, and stop.

### C2: Pick contacts

Take `approved` targets in rank order. From each target's `people.md` pick at most **two** contacts
(one recruiter or talent partner, one engineering manager). Skip anyone already in `activity.md`
under Mode A's duplicate rule. Stop when the cap's remaining invites are used.

### C3: Draft

One note per contact, **300 characters maximum**, run through the `humanize` skill first. The note:

- says who the user is in one clause (role, years, one real strength from `profile.md`);
- mentions one concrete thing about the company or the person's work, taken from `company.md`, the
  person's public profile, or the company's public pages;
- says the user would like to stay connected or learn about the team;
- **never** says or implies an opening exists, **never** asks for a job or referral, and **never**
  claims a prior relationship;
- for a non-home market, follows the local norms in `markets/[country].md`.

Show each draft with its character count and the cap status, for example "[N]/300 · invites this week: 6/15".

### C4: Send

Use Mode A's step A4 mechanics unchanged: new tab, open the profile, Connect (or More → Connect),
Add a note, paste the approved text, show the filled modal, and **do not click Send until the user
says yes**.

### C5: Log and status

Append the `(pre-opening)` invite line to `activity.md`. Set the target's status to `contacted` and
update `Contacts` and `Last touched`. When the user later says a person accepted, append the
`invite accepted` line and set the status to `warm`.

## 6. `browse` hook

In STEP 5D (contact discovery for each saved job), before searching LinkedIn:

1. Look for `targets/[company-slug]/people.md` where the slug matches the saved job's company.
2. If it exists, **copy** it to `jobs/discovered/[job-slug]/people.md` with a first line
   "Reused from targets/[slug], searched [date]", skip the new search, and set the target's status to
   `opening-seen`. Append the `target opening seen` activity line.
3. In STEP 5E (route question), if the target is `contacted` or `warm`, change the default for that
   job to "reach out first via your existing contact" and name the contact.

Companies with no target entry behave exactly as today.

## 7. `follow-up` change

In the follow-up queue's LinkedIn-invite rule, ignore invites whose activity line contains
`(pre-opening)`. They are not nagged at 7 days. Instead, when a job is saved at a company with a
`warm` or `contacted` target, the follow-up queue lists those contacts under "Warm contacts at
companies you're applying to".

## 8. Guardrails

- Nothing is sent without the user's explicit yes, for every invite.
- The user's own logged-in LinkedIn session is used; the agent never logs in or handles passwords.
- Contacts are recorded as public profile URLs and roles only.
- The weekly cap is enforced from the log before drafting, not after.
- Sponsorship statements are truthful. If a contact asks, the answer is that sponsorship is needed.
- No claim of an opening, a referral, or a relationship that does not exist.
- Targets are shown stale after 60 days.
- Skills never invent evidence. Unverified is an acceptable value; a guess is not.

## 9. GPT runtime

GPT Work cannot drive a browser, so it gets the **list-building half only**. A new `.gpt/skills/targets`
skill builds and ranks `targets.md` using web search and the market files, outputs the file as a code
block for the user to save, and skips Step 4 contact discovery (it offers to take pasted LinkedIn
People-tab results instead). `AGENTS.md` lists the trigger. There is no GPT outreach mode, since GPT
has no `outreach` skill.

## 10. Files changed

**New**
- `careeros/templates/workspace/.claude/skills/targets/SKILL.md`
- `careeros/templates/gpt-workspace/.gpt/skills/targets/SKILL.md`

**Edited**
- `careeros/templates/workspace/.claude/skills/outreach/SKILL.md` (Mode C and trigger line)
- `careeros/templates/workspace/.claude/skills/browse/SKILL.md` (steps 5D and 5E)
- `careeros/templates/workspace/.claude/skills/follow-up/SKILL.md` (queue rule)
- `careeros/templates/workspace/CLAUDE.md` (routing rows, layout: `targets.md`, `targets/`)
- `careeros/templates/gpt-workspace/AGENTS.md` (trigger row)
- `docs/index.md`, `docs/cowork/claude-code.md`, `docs/cowork/chatgpt.md`
- `tests/test_scaffold.py`

No Python code changes. The feature is entirely skill and template text, delivered through the
existing scaffold and `--refresh` mechanisms.

## 11. Testing

Following the existing style in `tests/test_scaffold.py`:

- Both runtimes scaffold a `targets` skill file.
- The Claude `CLAUDE.md` template contains the targets triggers and lists `targets.md` and `targets/`.
- `outreach/SKILL.md` contains "pre-opening", "Weekly invite cap", the default of 15, and the rule
  that notes never ask for a job.
- `browse/SKILL.md` references `targets/` in the contact-discovery step.
- `follow-up/SKILL.md` references `(pre-opening)`.
- `targets/SKILL.md` contains the 12-candidate limit, the 60-day staleness rule, the sponsorship
  grading values and the "never sends" rule.
- `--refresh` delivers the new skills to an existing workspace.
- Full `pytest` run and `mkdocs build --strict` pass.

Skill behaviour itself (browser steps) cannot be unit-tested; it is verified by one supervised run
against a real workspace after implementation.

## 12. Out of scope

- Detecting connection acceptance automatically (the user reports it).
- Messages after a connection is accepted.
- Pre-opening email outreach.
- GPT outreach.
- Scheduling or unattended runs.
- Asking for the weekly cap during onboarding.

## 13. Risks and limits

- LinkedIn may flag outreach volume. The cap and per-send approval mitigate this but cannot
  guarantee against it.
- Reply rates to unsolicited introductions are unknown. This design improves readiness, not response.
- `unknown` will be the most common sponsorship value, so non-home ranking will often fall back to
  fit within the `unknown` tier.
- Sponsor-register and board data go stale; the 60-day refresh addresses this.
- Slug collisions between similarly named companies are possible. The skill shows the slug in the
  table so the user can catch them.
