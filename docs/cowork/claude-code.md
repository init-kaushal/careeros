# Claude Code setup

Step-by-step guide for setting up and using CareerOS Cowork with Claude Code.

---

## Prerequisites

- CareerOS installed in a virtual environment (`pip install -e .` from the repo). Claude Code must be able to run `careeros` in that workspace, so start it from a shell where `careeros doctor` runs
- Claude Code CLI or Claude.ai with Projects
- Chrome with the **Claude-in-Chrome** extension

### Install Claude-in-Chrome

1. Install from the [Chrome Web Store](https://chromewebstore.google.com/detail/claude-in-chrome)
2. No configuration needed — it runs in the background

---

## Step 1 — Scaffold the workspace

```bash
careeros init ~/my-job-search
```

Creates `CLAUDE.md`, the skills under `.claude/skills/` (onboard, browse, track, research, market, evaluate, prep, apply, outreach, follow-up, interview, offer, memory, humanize, targets), `ledger.jsonl` and `.careeros/`. Until onboarding writes `profile.md`, `careeros doctor` and `careeros validate` report the missing `profile.md`, `jobs/`, `activity.md` and `jobs/pipeline.md`; that is expected on a new workspace.

---

## Step 2 — Open in Claude Code

```bash
claude ~/my-job-search
```

Or in Claude.ai: Projects → New Project → set working directory to `~/my-job-search`.

---

## Step 3 — Onboarding (first session only)

Claude detects that `profile.md` is missing and runs the onboarding interview automatically — 8 questions covering your role, stack, targets, location, compensation floor, and boards. It then writes:

- `profile.md` — profile + scoring rubric
- `boards.md` — boards with search URLs
- `jobs/pipeline.md` — empty pipeline
- `activity.md` — initial log entry
- `career/` — if you give it your resume, it runs `careeros memory import` so that every draft can be checked against facts you confirmed

---

## Step 4 — Full workflow

### Evaluate
```
"evaluate Stripe"
```
Scores a saved job against your career memory (`careeros evaluate`) and shows the fit, the gaps and the pay cap. See [Job evaluation](../evaluation.md).

### Find jobs
```
"browse linkedin"
"find senior backend roles on wellfound"
```
Claude opens a new Chrome tab using your real logged-in session, extracts listings, scores them against your rubric, and saves selected ones to `jobs/discovered/`.

### Research
```
"research Stripe"
"find the hiring manager at Stripe"
"comp research for staff engineer at Stripe"
```
Produces `jobs/discovered/[job]/company.md` and `people.md`.

### Prep your application
```
"prep my application for Stripe"
```
Reads the JD, maps your profile against it, and writes a tailoring brief to `prep.md` — ATS keywords, which bullets to lead with, cover letter angle.

### Apply
```
"apply to Stripe"
```
Claude drafts a cover letter → surveys the form → fills fields from your profile → shows you a preview → waits for your explicit approval → submits. Resume upload fields are flagged for you to handle.

### Outreach
```
"connect with the engineering manager at Stripe"
"draft an outreach email to Jane Doe"
```
For LinkedIn: drafts a ≤300-character note → navigates to their profile → shows you the filled invite → waits for approval → sends.
For email: produces a draft you send from your own client.

### Follow-ups
```
"check follow-ups"
"who needs a follow-up?"
```
LinkedIn invites (7 days) and applications (10 days) come from recorded events via `careeros outreach queue`; warm contacts from `careeros outreach warm`. Email outreach (5 days) and post-interview (3 days) follow-ups stay prose and come from `activity.md`. Drafts per-item, confirms before each send.

### Interview prep
```
"I have an interview at Stripe on Friday"
"prep for my Stripe hiring manager round"
```
Records the round (`careeros interview round add`), looks up interviewers, generates a question bank for the stage, drafts STAR stories from your profile, and writes `interview-prep.md`. After the round, tell Claude whether it took place and how it went; it records `held`, `cancelled` or a new date, and your debrief, only from what you say. `careeros interview upcoming` and `careeros interview needs-debrief` list what is due. See [Interview rounds](../interviews.md).

### Offer evaluation and negotiation
```
"I got an offer from Stripe"
"help me negotiate with Stripe"
"compare my Stripe and Notion offers"
```
Records the full offer (base, equity, bonus, benefits), calculates Year 1 and steady-state total comp, benchmarks against comp research, identifies negotiation levers, and drafts the negotiation email with a specific ask.

### Track pipeline
```
"show pipeline"
"mark Stripe as applied"
```

| Icon | Status |
|------|--------|
| `[ ]` | Discovered |
| `[~]` | Applied |
| `[?]` | Interview |
| `[✓]` | Offer / Accepted |
| `[x]` | Closed / Declined |

---

## Commands only you run

Some commands record your own decision, so they need an interactive terminal and the agent cannot run them. In Claude Code, type them after `! `:

| Command | When |
|---|---|
| `careeros approve <job>` or `<apr_id>` | before an application is submitted or an outward message is sent |
| `careeros memory confirm <id>` / `verify` | to affirm a fact in your career memory |
| `careeros approval resolve <item>` | to settle a submission that was interrupted |

The agent moves jobs between states only with `careeros transition`, and asks you before each move.

## Keeping the workspace healthy

```bash
careeros status      # versions, jobs by state, recent events
careeros validate    # problems in the workspace, with a fix for each
careeros doctor      # environment and workspace checks
careeros ledger verify
```

Every change is recorded in `ledger.jsonl`, a hash chain. `careeros validate` reports problems; the usual fixes:

- **A new job has no id (`JOB001`):** run `careeros migrate`. The `browse` skill does this after saving.
- **A command was interrupted:** run the same command again; `validate` names the record that needs it.
- **`ledger verify` fails:** restore `ledger.jsonl` from `.careeros/backups/` or version control, then run `careeros validate`. Do not edit the ledger.
- **Something was changed by hand:** `validate` reports it; do not repair files by hand, ask the agent to re-run the step, or restore from a backup.

Backups are made before `migrate`, `upgrade` and `memory import --apply`. Keeping the workspace in git as well is a good idea.

## Keeping skills up to date

```bash
careeros upgrade
```

Shows what changes, backs up the changed files first, and asks before applying. Your profile, boards, jobs, memory and ledger are never touched. (`careeros init ~/my-job-search --refresh` refreshes the same files without showing a plan.)

---

## FAQ

**Q: Why use my real Chrome instead of a separate browser profile?**
Your logged-in session bypasses automation detection on LinkedIn, Instahyre, and other SPAs. A dedicated profile would require re-login and triggers bot detection within seconds.

**Q: Does CareerOS send my data anywhere?**
No server. LLM calls go to Anthropic. Workspace files stay on your machine.

**Q: Can I version-control the workspace?**
Yes. `git init ~/my-job-search` works well. Consider gitignoring `jobs/discovered/` if it contains salary information you prefer not to commit.

**Q: What if a form has a resume upload?**
Claude flags it and asks you to upload manually. It continues filling all other fields while you handle the upload.
