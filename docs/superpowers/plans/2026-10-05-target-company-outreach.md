# Target-company outreach (pre-opening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `targets` skill and a pre-opening mode in `outreach` so the user can build a ranked target-company list across all researched markets and introduce themselves to recruiters and engineering managers before any role opens.

**Architecture:** The feature is skill and template text delivered through the existing `scaffold` and `--refresh` mechanisms; there are no Python source changes. A new `targets` skill owns the list (`targets.md`, `targets/[slug]/people.md`); `outreach` gains Mode C for sending introductions; `browse` and `follow-up` get small hooks that reuse the contacts. Behaviour is pinned by text-assertion tests in `tests/test_scaffold.py`, the repo's existing style.

**Tech Stack:** Markdown skill files under `careeros/templates/`, pytest, mkdocs (strict build).

**Spec:** `docs/superpowers/specs/2026-10-05-target-company-outreach-design.md`

## Global Constraints

- Weekly invite cap defaults to **15**, read from `## Outreach` → `- **Weekly invite cap:** N` in `profile.md`; the cap counts **all** `sent LinkedIn invite →` lines in the last 7 days.
- At most **12 new** candidates per market per run.
- A target not touched for more than **60 days** is shown as stale.
- Introduction notes are **300 characters maximum**, pass through the `humanize` skill, never imply an opening exists, never ask for a job or referral, never claim a relationship.
- Sponsorship values are exactly `offered`, `likely`, `unknown`, `not offered` (and `n/a` for the home market); every value except `n/a` needs a source and a date.
- Status lifecycle is exactly `candidate` → `approved` → `contacted` → `warm` → `opening-seen`, with `dropped` from any state.
- Ranking: home market by fit; non-home markets by sponsorship tier (`offered`, `likely`, `unknown`) then fit.
- Nothing is sent without the user's explicit yes, per note. The `targets` skill never sends anything.
- At most two contacts per company (one recruiter or talent partner, one engineering manager).
- GPT runtime gets the list-building half only (no contact discovery, no outreach).
- No Python source changes. Every commit message ends with:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y` (two lines, after a blank line).
- All paths below are relative to `/Users/kaushal/Projects/careeros`. Run tests with `.venv/bin/python -m pytest -q`. Baseline before this plan: **31 passed**.

## Review Focus

1. **Company slug mismatch:** a saved job's company ("Atlassian Pty Ltd", "Atlassian") does not match the `targets.md` slug, so `browse` silently misses the existing contacts. Expected: the slug rule is explicit and a near-match is shown to the user, not guessed. (Task 3)
2. **Cap miscounting:** only pre-opening invites counted, or a missing `## Outreach` section treated as zero or unlimited. Expected: all invites in the last 7 days count; missing section means 15. (Task 2)
3. **First run with nothing to build from:** no `markets/` files, or no `targets.md` yet. Expected: home-market-only list with a clear message, and `targets.md` created. (Task 1)
4. **LinkedIn unreadable or logged out** during contact search or sending. Expected: stop, never log in for the user, no status or log changes. (Tasks 1 and 2)
5. **Unsponsorable or duplicate targets:** a `not offered` company in a sponsorship-required market shown as a candidate; the same person invited twice via two companies. Expected: the company goes to `## Dropped`; the duplicate rule skips the person. (Tasks 1 and 2)

---

### Task 1: `targets` skill (Claude) + routing + refresh delivery

**Files:**
- Create: `careeros/templates/workspace/.claude/skills/targets/SKILL.md`
- Modify: `careeros/templates/workspace/CLAUDE.md` (Outreach table and Workspace layout)
- Test: `tests/test_scaffold.py` (append)

**Interfaces:**
- Consumes: `scaffold(path, refresh=False, runtime="claude") -> list[str]` from `careeros.workspace.scaffold`.
- Produces: skill file path `.claude/skills/targets/SKILL.md`; workspace file formats `targets.md` and `targets/[slug]/people.md` that Tasks 2 and 3 read; the test helper `_read_skill(ws, runtime, name)` used by Tasks 2–4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
def _read_skill(ws: Path, runtime: str, name: str) -> str:
    prefix = ".claude/skills/" if runtime == "claude" else ".gpt/skills/"
    return (ws / prefix / name / "SKILL.md").read_text()


def test_targets_skill_ships_with_core_rules(tmp_workspace: Path) -> None:
    written = scaffold(tmp_workspace, runtime="claude")
    assert ".claude/skills/targets/SKILL.md" in written
    text = _read_skill(tmp_workspace, "claude", "targets")
    for needle in [
        "12 new",
        "60 days",
        "`offered`",
        "`likely`",
        "`unknown`",
        "`not offered`",
        "never sends anything",
        "targets/[slug]/people.md",
        "lowercase, hyphenated",
    ]:
        assert needle in text, needle


def test_targets_skill_handles_empty_first_run(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "targets")
    assert "No markets researched yet" in text
    assert "create it in STEP 3" in text


def test_targets_skill_stops_when_linkedin_unreadable(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "targets")
    assert "LinkedIn cannot be read" in text
    assert "do not log in on the user's behalf" in text


def test_targets_skill_drops_unsponsorable_companies(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "targets")
    assert "straight to `## Dropped`" in text
    assert "sponsorship is required" in text


def test_claude_entrypoint_routes_to_targets_and_lists_layout(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = (tmp_workspace / "CLAUDE.md").read_text()
    assert "targets/SKILL.md" in text
    assert "build target companies" in text
    assert "targets.md" in text
    assert "targets/" in text


def test_refresh_delivers_targets_skill(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    skill = tmp_workspace / ".claude" / "skills" / "targets" / "SKILL.md"
    skill.unlink()
    refreshed = scaffold(tmp_workspace, refresh=True, runtime="claude")
    assert skill.exists()
    assert ".claude/skills/targets/SKILL.md" in refreshed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_scaffold.py -k "targets"`
Expected: FAIL (the skill file does not exist yet; `FileNotFoundError` / assertion failures).

- [ ] **Step 3: Create the skill file**

Create `careeros/templates/workspace/.claude/skills/targets/SKILL.md` with exactly this content:

````markdown
# CareerOS Targets Skill

**Trigger:** "build target companies" / "find targets for [market]" / "show targets" / "refresh targets"

Builds and maintains a ranked list of companies the user wants to be known at **before** any role is open, and finds hiring contacts there. This skill never sends anything — introductions go through the `outreach` skill (Mode C).

---

## STEP 0 — LOAD CONTEXT

Read:
1. `profile.md` — target roles, stack, excluded roles, the "Target companies" line, and the `## Markets` table.
2. Every file in `markets/`. If `markets/` is missing or empty, build the list for the home market only and say: "No markets researched yet — run 'research [country] market' to add other countries."
3. `boards.md` and `.claude/skills/onboard/boards-catalog.md` — the boards each market uses.
4. `targets.md` if it exists. If it does not, create it in STEP 3 using the format below.
5. `jobs/discovered/*/job.md` — companies and scores of saved jobs.

**"show targets":** print `targets.md` grouped by market. Mark any row whose `Last touched` is more than 60 days old as **stale** and offer "refresh targets". Stop.

**"refresh targets":** for every row in `targets.md` that is not dropped, re-check the sponsorship evidence (STEP 2) and the contacts (STEP 4), update `Last touched`, then show the table.

**"find targets for [market]":** run STEPS 1–5 for that market only. **"build target companies":** run them for every market.

---

## STEP 1 — GATHER CANDIDATES

Per market, gather up to **12 new** candidates that are not already in `targets.md` (including `## Dropped`):

1. **Seeds:** companies in the "Target companies" line of `profile.md`, and companies of saved jobs scoring 7 or higher.
2. **Sponsor register:** if the market file names a register of licensed sponsors, companies on it that hire for the user's target roles.
3. **Regional boards:** companies appearing on that market's boards for the user's target roles.
4. **Levels.fyi** company lists filtered to the country, when readable. If `WebFetch` returns an empty page, open it in a new browser tab and use `get_page_text`.

Skip any company whose only visible roles are in the user's excluded role families.

---

## STEP 2 — EVIDENCE AND SCORING

- **Fit (0–10):** the same rubric `browse` uses (role, stack, seniority) plus a pay check against the market floor in `## Markets`. Unknown pay is flagged as unknown, never guessed.
- **Sponsorship** (non-home markets; the home market is `n/a`). Grade from evidence only, and record the source and the date you read it:
  - `offered` — a listing or careers page states visa or relocation support.
  - `likely` — the company is on the market's sponsor register, or is a large multinational with visa-supporting listings on record.
  - `unknown` — nothing found either way. This is the common case. Do not upgrade it.
  - `not offered` — a listing or page states no sponsorship. If the `## Markets` row says sponsorship is required, move the company straight to `## Dropped` with the reason instead of listing it.
- **Ranking:** home market sorted by fit, highest first. Non-home markets sorted by sponsorship tier (`offered`, then `likely`, then `unknown`), then by fit, highest first.

Unverified is an acceptable value. A guess is not.

---

## STEP 3 — PRESENT AND APPROVE

Show one table per market in the ranking above:

| # | Company | Slug | Fit | Sponsorship | Evidence (source, date) |
|---|---------|------|-----|-------------|-------------------------|

The **Slug** is the company name in lowercase, hyphenated, with no legal suffix (for example `atlassian`). It is the join key with `jobs/discovered/` and `targets/`, so show it and let the user correct it.

Ask:
> "Approve which? (e.g. 'approve 1 2 4, drop 3', or 'q' to stop)"

Write `targets.md` (create it if absent) in this format:

```markdown
# Target Companies

**Updated:** [date]

| Company | Slug | Market | Fit | Sponsorship | Evidence (source, date) | Status | Contacts | Last touched |
|---------|------|--------|-----|-------------|-------------------------|--------|----------|--------------|
| [Company] | [slug] | [market] | [0-10] | [offered / likely / unknown / n/a] | [source, date] | approved | 0 | [date] |

## Dropped
| Company | Market | Reason | Date |
|---------|--------|--------|------|
```

Approved rows get status `approved`. Dropped rows move under `## Dropped` with a reason. Append to `activity.md` for each approved company:
```
[date] target added: [Company] ([Market]) — fit [N], sponsorship [value]
```

---

## STEP 4 — FIND CONTACTS FOR APPROVED TARGETS

For each approved target, run the `research` skill's contact discovery (Mode B1b: the company's LinkedIn People tab, single-keyword queries) and write `targets/[slug]/people.md`:

```markdown
# Hiring contacts — [Company], searched [date]

| Name | Headline | Location | Role |
|------|----------|----------|------|
| [name] | [headline] | [location] | recruiter / hiring-manager / peer |
```

Look for recruiters or talent partners whose headline says they are hiring, the engineering manager the user's role would report to, and one peer engineer. Founders and C-level only as a fallback. Record public profile URLs and roles only. Put the number of contacts found in the `Contacts` column of `targets.md`.

If LinkedIn cannot be read or the user is logged out, keep the target as `approved`, say "contact search pending", and stop the contact step — do not log in on the user's behalf.

---

## STEP 5 — HAND OFF

Update `Last touched` and `Updated` in `targets.md`. Finish with:
> "Targets saved. Say 'reach out to my targets' to draft introductions — nothing is sent until you approve each note."

---

## NOTES

- Always open a fresh browser tab and close the tabs you opened.
- Never send an invite, message or email from this skill.
- Status lifecycle: `candidate` → `approved` → `contacted` → `warm` → `opening-seen`; `dropped` from any state. `outreach` and `browse` advance statuses after `approved`.
- Re-run "refresh targets" when rows go stale (more than 60 days).
````

- [ ] **Step 4: Route the entry file**

In `careeros/templates/workspace/CLAUDE.md`, replace this line in the Outreach table:

```
| "check follow-ups" / "who needs a follow-up?" / "follow-up queue" | `.claude/skills/follow-up/SKILL.md` |
```

with:

```
| "check follow-ups" / "who needs a follow-up?" / "follow-up queue" | `.claude/skills/follow-up/SKILL.md` |
| "build target companies" / "find targets for [market]" / "show targets" / "refresh targets" | `.claude/skills/targets/SKILL.md` |
| "reach out to my targets" / "pre-opening outreach" / "introduce myself at [company]" | `.claude/skills/outreach/SKILL.md` (Mode C) |
```

Then in the Workspace layout block, replace:

```
markets/
  [country].md          — visa routes, your eligibility, typical pay, local application norms (dated, cited)
```

with:

```
markets/
  [country].md          — visa routes, your eligibility, typical pay, local application norms (dated, cited)
targets.md              — ranked target companies by market, with sponsorship evidence and status
targets/
  [company-slug]/
    people.md           — hiring contacts found before any opening exists
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (31 baseline + 6 new = 37).

- [ ] **Step 6: Commit**

```bash
git add careeros/templates/workspace/.claude/skills/targets/SKILL.md careeros/templates/workspace/CLAUDE.md tests/test_scaffold.py
git commit -m "targets: add skill that builds a ranked pre-opening target-company list" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 2: `outreach` Mode C (pre-opening introduction)

**Files:**
- Modify: `careeros/templates/workspace/.claude/skills/outreach/SKILL.md`
- Test: `tests/test_scaffold.py` (append)

**Interfaces:**
- Consumes: `_read_skill(ws, runtime, name)` from Task 1; the `targets.md` and `targets/[slug]/people.md` formats from Task 1.
- Produces: activity log line `[date] sent LinkedIn invite → [Name] ([Title] at [Company]) (pre-opening) · note: …` that Task 3's `follow-up` rule matches on the literal `(pre-opening)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
def test_outreach_has_pre_opening_mode_with_guardrails(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "outreach")
    for needle in [
        "three outreach modes",
        "MODE C — PRE-OPENING INTRODUCTION",
        "reach out to my targets",
        "(pre-opening)",
        "300 characters",
        "Never imply an opening exists",
        "Never ask for a job",
        "at most two contacts",
        "Do not click Send until the user says yes",
        "humanize",
    ]:
        assert needle in text, needle


def test_outreach_cap_counts_all_invites_and_defaults_to_15(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "outreach")
    assert "Weekly invite cap" in text
    assert "all invites" in text
    assert "last 7 days" in text
    assert "**15**" in text
    assert "if the section or line is missing" in text


def test_outreach_pre_opening_dedupes_and_stops_when_logged_out(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "outreach")
    assert "already in `activity.md`" in text
    assert "one invite per person, ever" in text
    assert "do not log in on the user's behalf" in text
    assert "do not change any status" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_scaffold.py -k "outreach"`
Expected: FAIL (the needles are not in the file yet).

- [ ] **Step 3: Edit the outreach skill**

In `careeros/templates/workspace/.claude/skills/outreach/SKILL.md`:

1. Replace the trigger line

```
**Trigger:** "connect with [person]" / "send LinkedIn invite to [person]" / "draft outreach to [person]" / "send email to [person]"
```

with

```
**Trigger:** "connect with [person]" / "send LinkedIn invite to [person]" / "draft outreach to [person]" / "send email to [person]" / "reach out to my targets" / "pre-opening outreach" / "introduce myself at [company]"
```

2. Replace `Supports two outreach modes.` with `Supports three outreach modes.`

3. Insert the following block immediately before the line `## NOTES`, keeping the `---` separator above it:

```markdown
## MODE C — PRE-OPENING INTRODUCTION

Use this when the user wants to be known at a target company **before** a role is open. Contacts come from `targets/[slug]/people.md` (built by the `targets` skill), not from a saved job.

### C1 — Check the weekly cap

Count the lines in `activity.md` matching `sent LinkedIn invite →` from the last 7 days. Count **all invites**, role-based and pre-opening, because LinkedIn's limits apply to the whole account. Read the cap from the `## Outreach` section of `profile.md` (`- **Weekly invite cap:** N`); if the section or line is missing, the cap is **15**.

If the cap is reached, say so with the date the oldest counted invite ages out, and stop.

### C2 — Pick contacts

Read `targets.md`. If it is missing or has no approved companies, say "No approved targets — run 'build target companies' first." and stop.

Take companies with status `approved` (or `contacted` / `warm` that still have uninvited contacts), in table order. From each company's `targets/[slug]/people.md` pick **at most two contacts** — one recruiter or talent partner and one engineering manager. Skip anyone already in `activity.md` under Mode A's duplicate rule (one invite per person, ever), even if they appear under two companies. Stop picking when the remaining cap is used up.

### C3 — Draft each note

300 characters maximum. Run it through the `humanize` skill first. The note:
- says who the user is in one clause (role, years of experience, one real strength from `profile.md`);
- mentions one concrete thing about the company or the person's work, from `company.md`, the person's public profile or the company's public pages;
- says the user would like to stay connected or learn about the team;
- follows the local norms in `markets/[country].md` for a non-home market.

Never imply an opening exists. Never ask for a job or a referral. Never claim a relationship that does not exist.

Show each draft with its count and the cap status:
> "[Note text] — [N]/300 characters · invites this week: [used]/[cap]"

Ask: "Send this note, or regenerate?"

### C4 — Send

Use steps 1–10 of Mode A's step A4 exactly as written. Do not click Send until the user says yes. If LinkedIn cannot be read or the user is logged out, stop — do not log in on the user's behalf and do not change any status or log line.

### C5 — Log and update status

Append to `activity.md`:
```
[date] sent LinkedIn invite → [Name] ([Title] at [Company]) (pre-opening) · note: [first 60 chars of note]…
```
In `targets.md`, set the company's status to `contacted` (unless it is already `warm` or `opening-seen`) and update `Contacts` and `Last touched`.

When the user later says a person accepted, append `[date] LinkedIn invite accepted → [Name]` and set the company's status to `warm`.

---

```

4. In the `## NOTES` list, add this bullet at the end:

```
- Pre-opening introductions (Mode C) never mention a role and never ask for one; the user approves every note before it is sent.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (40).

- [ ] **Step 5: Commit**

```bash
git add careeros/templates/workspace/.claude/skills/outreach/SKILL.md tests/test_scaffold.py
git commit -m "outreach: add pre-opening introduction mode with weekly cap" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 3: `browse` and `follow-up` hooks

**Files:**
- Modify: `careeros/templates/workspace/.claude/skills/browse/SKILL.md` (STEP 5 D and E)
- Modify: `careeros/templates/workspace/.claude/skills/follow-up/SKILL.md` (Skip list, STEP 2)
- Test: `tests/test_scaffold.py` (append)

**Interfaces:**
- Consumes: `_read_skill`; the `targets.md` / `targets/[slug]/people.md` formats (Task 1); the literal `(pre-opening)` log marker (Task 2).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
def test_browse_reuses_target_contacts_and_matches_slug_carefully(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "browse")
    for needle in [
        "targets/[company-slug]/people.md",
        "Reused from targets/",
        "opening-seen",
        "lowercase, hyphenated",
        "ask which company is meant",
        "reach-out-first via your existing contact",
    ]:
        assert needle in text, needle


def test_follow_up_ignores_pre_opening_invites_and_lists_warm_contacts(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="claude")
    text = _read_skill(tmp_workspace, "claude", "follow-up")
    assert "(pre-opening)" in text
    assert "never nagged at 7 days" in text
    assert "Warm contacts at companies you're applying to" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_scaffold.py -k "browse_reuses or follow_up_ignores"`
Expected: FAIL.

- [ ] **Step 3: Edit `browse`**

In `careeros/templates/workspace/.claude/skills/browse/SKILL.md`, replace the exact text

```
**D. Find hiring contacts for every saved job — always, not on request.** Run the `research` skill's contact discovery
```

with

```
**D. Find hiring contacts for every saved job — always, not on request.** **First check targets:** compute the company slug (the company name in lowercase, hyphenated, no legal suffix) and look for `targets/[company-slug]/people.md`. If it exists, copy it to `jobs/discovered/[job-slug]/people.md` with the first line "Reused from targets/[company-slug], searched [date]", skip the new LinkedIn search for that company, set the company's status in `targets.md` to `opening-seen`, and append `[date] target opening seen: [Company] — [Title]` to `activity.md`. If the slug is close to but not exactly a `targets.md` row (a legal suffix, a different spelling), show both and ask which company is meant instead of guessing. Otherwise run the `research` skill's contact discovery
```

Then replace the exact sentence (end of step E)

```
Never send a message or submit an application without the user's go-ahead.
```

with

```
Never send a message or submit an application without the user's go-ahead.

**Targets default:** if the saved job's company is a target with status `contacted` or `warm`, make the default for that job `reach-out-first via your existing contact` and name the contact from `targets/[company-slug]/people.md` — for example: "You already invited [Name] at [Company] — message them about this role first?"
```

- [ ] **Step 4: Edit `follow-up`**

In `careeros/templates/workspace/.claude/skills/follow-up/SKILL.md`, replace the exact line

```
- Items marked closed / rejected / offer-accepted in activity.md or pipeline.md
```

with

```
- Items marked closed / rejected / offer-accepted in activity.md or pipeline.md
- LinkedIn invites whose activity line contains `(pre-opening)` — these are introductions sent before any role existed, so they are never nagged at 7 days. They are surfaced as warm contacts instead (STEP 2).
```

Then replace the exact text

```
Present a table:

| # | Who / What | Type |
```

with

```
**Warm contacts first.** Read `targets.md` (skip this if it is missing). For every company whose status is `contacted`, `warm` or `opening-seen` and that also has an Active job in `jobs/pipeline.md`, list the contacts invited under `(pre-opening)` in `activity.md`, under the heading "Warm contacts at companies you're applying to", with the job and the invite date. These are for awareness; they do not enter the overdue queue.

Present a table:

| # | Who / What | Type |
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (42).

- [ ] **Step 6: Commit**

```bash
git add careeros/templates/workspace/.claude/skills/browse/SKILL.md careeros/templates/workspace/.claude/skills/follow-up/SKILL.md tests/test_scaffold.py
git commit -m "browse, follow-up: reuse target contacts and stop nagging pre-opening invites" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 4: GPT `targets` skill (list-building only)

**Files:**
- Create: `careeros/templates/gpt-workspace/.gpt/skills/targets/SKILL.md`
- Modify: `careeros/templates/gpt-workspace/AGENTS.md`
- Test: `tests/test_scaffold.py` (append)

**Interfaces:**
- Consumes: `_read_skill` (Task 1); the `targets.md` format (Task 1).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
def test_gpt_targets_skill_builds_list_only(tmp_workspace: Path) -> None:
    written = scaffold(tmp_workspace, runtime="gpt")
    assert ".gpt/skills/targets/SKILL.md" in written
    text = _read_skill(tmp_workspace, "gpt", "targets")
    for needle in [
        "web search",
        "code block",
        "12 new",
        "60 days",
        "never sends anything",
        "`not offered`",
        "straight to `## Dropped`",
        "pastes",
    ]:
        assert needle in text, needle


def test_gpt_entrypoint_routes_to_targets(tmp_workspace: Path) -> None:
    scaffold(tmp_workspace, runtime="gpt")
    text = (tmp_workspace / "AGENTS.md").read_text()
    assert "targets/SKILL.md" in text
    assert "targets.md" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_scaffold.py -k "gpt_targets or gpt_entrypoint_routes_to_targets"`
Expected: FAIL.

- [ ] **Step 3: Create the GPT skill**

Create `careeros/templates/gpt-workspace/.gpt/skills/targets/SKILL.md` with exactly this content:

````markdown
# Skill: Targets

## When to run

When the user says "build target companies", "find targets for [market]", "show targets", or "refresh targets".

Builds a ranked list of companies the user wants to be known at **before** any role is open. This skill never sends anything and cannot browse LinkedIn, so it builds the list only; the user does any outreach themselves.

---

## STEP 0 — Load context

From the uploaded files read: `profile.md` (target roles, stack, excluded roles, the "Target companies" line, `## Markets`), every `markets/[country].md`, `boards.md`, `targets.md` if uploaded, and the companies in `jobs/discovered/`. If no `markets/` files are uploaded, build the list for the home market only and say: "No markets researched yet — run 'research [country] market' to add other countries."

**"show targets":** print `targets.md` grouped by market and mark any row whose `Last touched` is more than 60 days old as **stale**. Stop.

## STEP 1 — Gather candidates

Per market, gather up to **12 new** candidates not already in `targets.md`: seeds (the "Target companies" line and saved jobs scoring 7 or higher), companies on the market's sponsor register if the market file names one, and companies hiring for the user's target roles on that market's boards. Use web search. Skip companies whose only visible roles are in the user's excluded role families.

## STEP 2 — Evidence and scoring

- **Fit (0–10):** role, stack and seniority against the profile rubric, plus a pay check against the market floor. Unknown pay is flagged as unknown, never guessed.
- **Sponsorship** (non-home markets; the home market is `n/a`), from evidence only, with source and the date you read it:
  - `offered` — a listing or careers page states visa or relocation support.
  - `likely` — the company is on the sponsor register, or is a large multinational with visa-supporting listings on record.
  - `unknown` — nothing found either way. Do not upgrade it.
  - `not offered` — a listing or page states no sponsorship. If the `## Markets` row says sponsorship is required, move the company straight to `## Dropped` with the reason instead of listing it.
- **Ranking:** home market by fit, highest first. Non-home markets by sponsorship tier (`offered`, `likely`, `unknown`), then fit.

Unverified is acceptable. A guess is not.

## STEP 3 — Present and approve

Show one table per market (Company, Slug, Fit, Sponsorship, Evidence). The slug is the company name in lowercase, hyphenated, with no legal suffix. Ask: "Approve which? (e.g. 'approve 1 2 4, drop 3')".

Then output the complete `targets.md` as a code block for the user to save:

~~~
**`targets.md`** — save this file to your workspace:
```markdown
# Target Companies

**Updated:** [date]

| Company | Slug | Market | Fit | Sponsorship | Evidence (source, date) | Status | Contacts | Last touched |
|---------|------|--------|-----|-------------|-------------------------|--------|----------|--------------|

## Dropped
| Company | Market | Reason | Date |
|---------|--------|--------|------|
```
~~~

Approved rows have status `approved`; dropped rows go under `## Dropped`. Also output the activity lines to append to `activity.md`:
```
[date] target added: [Company] ([Market]) — fit [N], sponsorship [value]
```

## STEP 4 — Contacts (optional)

You cannot browse LinkedIn. If the user pastes a company's LinkedIn People-tab results, pick recruiters or talent partners who say they are hiring, the engineering manager the role would report to, and one peer, and output `targets/[slug]/people.md` as a code block. Otherwise say contact search needs Claude Code.

Finish with: "Targets built. Introductions need Claude Code's `outreach` skill, or you can send them yourself."
````

- [ ] **Step 4: Route the GPT entry file**

In `careeros/templates/gpt-workspace/AGENTS.md`, after the table row

```
| "research [country] market" / "can I work in [country]?" / "visa options for [country]" | `.gpt/skills/market/SKILL.md` |
```

add

```
| "build target companies" / "find targets for [market]" / "show targets" | `.gpt/skills/targets/SKILL.md` |
```

and in the Workspace layout block, after the line `markets/            — one .md per country: visa routes, eligibility, typical pay, local norms`, add

```
targets.md          — ranked target companies by market, with sponsorship evidence and status
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (44).

- [ ] **Step 6: Commit**

```bash
git add careeros/templates/gpt-workspace/.gpt/skills/targets/SKILL.md careeros/templates/gpt-workspace/AGENTS.md tests/test_scaffold.py
git commit -m "gpt: add targets skill that builds the target-company list" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 5: Docs and full verification

**Files:**
- Modify: `docs/index.md`, `docs/cowork/claude-code.md`, `docs/cowork/chatgpt.md`

**Interfaces:**
- Consumes: the finished skills from Tasks 1–4.
- Produces: user-facing docs; nothing later depends on them.

- [ ] **Step 1: Apply the doc edits with a checked script**

Run this exactly (it asserts each anchor exists once, so a drifted doc fails loudly instead of silently skipping):

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path

def sub(path, old, new):
    p = Path(path)
    s = p.read_text()
    assert s.count(old) == 1, (path, old, s.count(old))
    p.write_text(s.replace(old, new))

# docs/index.md: add a bullet after the markets bullet (single long line)
p = Path("docs/index.md")
lines = p.read_text().split("\n")
idx = [i for i, l in enumerate(lines) if "It's research to support your decision, not legal advice." in l]
assert len(idx) == 1, idx
lines.insert(idx[0] + 1, "- **It can introduce you before a role opens.** The `targets` skill (\"build target companies\") builds a ranked list across every market you've researched, sponsorship-friendly companies first for markets where you need a visa, and finds recruiters and engineering managers there. `outreach` then drafts short introductions (\"reach out to my targets\") within a weekly invite cap, 15 by default. Introductions never mention a role or ask for one, and you approve every note before it is sent. Later, when `browse` saves a job at a target company, your existing contacts are reused. GPT Work builds the list only; sending needs Claude Code.")
p.write_text("\n".join(lines))

# docs/cowork/claude-code.md
sub("docs/cowork/claude-code.md", "    ├── market/\n", "    ├── market/\n    ├── targets/\n")
sub("docs/cowork/claude-code.md", "### Outreach\n", """### Target companies (before any opening)

```
"build target companies"
"find targets for Singapore"
"show targets"
"reach out to my targets"
```

`targets` builds a ranked list of companies across every market with a research file. Non-home markets rank sponsorship-friendly companies first, graded from evidence with a source and date; `unknown` is a valid grade. It finds recruiters and engineering managers on each company's LinkedIn People tab and saves them to `targets/[company]/people.md`. It sends nothing.

"reach out to my targets" drafts a short introduction (300 characters at most) for up to two contacts per company, shows your invites used this week against the cap (15 by default, set under `## Outreach` in `profile.md`), and sends only after you approve each note. Introductions never mention a role and never ask for a job. Targets not touched for 60 days are marked stale.

### Outreach
""")

# docs/cowork/chatgpt.md
sub("docs/cowork/chatgpt.md", "GPT Work ships four skills (onboard, browse, track, market).", "GPT Work ships five skills (onboard, browse, track, market, targets).")
sub("docs/cowork/chatgpt.md", "    ├── market/\n", "    ├── market/\n    ├── targets/\n")
sub("docs/cowork/chatgpt.md", "- `.gpt/skills/market/SKILL.md`\n", "- `.gpt/skills/market/SKILL.md`\n- `.gpt/skills/targets/SKILL.md`\n")
sub("docs/cowork/chatgpt.md", "| Company / comp research | ✅ (web search, outputs research notes) |\n", "| Company / comp research | ✅ (web search, outputs research notes) |\n| Target-company list (pre-opening) | ✅ list only (web search, outputs targets.md); sending introductions needs Claude Code |\n")
print("docs updated")
EOF
```

Expected output: `docs updated`. If an assertion fails, open the named doc, find the drifted line, and adjust the anchor in the script; do not skip it.

- [ ] **Step 2: Full verification**

Run each and check the result:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m mkdocs build --strict >/dev/null 2>&1; echo "mkdocs strict exit: $?"
T=$(mktemp -d)/ws && .venv/bin/careeros init $T >/dev/null && ls $T/.claude/skills/targets/SKILL.md && grep -c "targets" $T/CLAUDE.md
T2=$(mktemp -d)/ws && .venv/bin/careeros init $T2 --runtime gpt >/dev/null && ls $T2/.gpt/skills/targets/SKILL.md
```

Expected: pytest passes (44); `mkdocs strict exit: 0`; both `ls` commands print the skill path; the `grep -c` count is at least 3.

- [ ] **Step 3: Commit**

```bash
git add docs/index.md docs/cowork/claude-code.md docs/cowork/chatgpt.md
git commit -m "docs: describe target-company outreach" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

## After the plan

The browser steps cannot be unit-tested. After merging, run one supervised pass in the real workspace: refresh it (`careeros init ~/Projects/job-search --refresh`), say "build target companies" for one market, approve a single company, and check `targets.md`, `targets/[slug]/people.md` and a drafted (unsent) introduction before sending anything.
