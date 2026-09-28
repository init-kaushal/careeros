# Claude Code setup

Step-by-step guide for setting up and using CareerOS Cowork with Claude Code.

---

## Prerequisites

- CareerOS installed (`pip install -e .` from the repo)
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

Creates:

```
~/my-job-search/
├── CLAUDE.md
└── .claude/skills/
    ├── onboard/   browse/   track/
    ├── research/  prep/     apply/
    ├── outreach/  follow-up/
    ├── interview/ offer/
```

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

---

## Step 4 — Full workflow

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
Scans `activity.md` for overdue invites (7 days), email outreach (5 days), applications (10 days), and post-interview (3 days). Drafts per-item, confirms before each send.

### Interview prep
```
"I have an interview at Stripe on Friday"
"prep for my Stripe hiring manager round"
```
Looks up interviewers on LinkedIn, generates a question bank for the stage, drafts STAR stories from your profile, and writes `interview-prep.md`. After the interview, log how it went and Claude updates the pipeline.

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

## Keeping skills up to date

```bash
careeros init ~/my-job-search --refresh
```

Updates all `.claude/skills/` files. Your profile, boards, jobs, and activity log are never touched.

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
