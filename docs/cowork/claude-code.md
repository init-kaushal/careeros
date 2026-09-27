# Claude Code setup

Step-by-step guide for setting up a CareerOS Cowork workspace with Claude Code.

---

## Prerequisites

- CareerOS installed (`pip install -e .` from the repo)
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code` or via the Mac app)
- Chrome with the **Claude-in-Chrome** extension installed

### Install Claude-in-Chrome

Claude-in-Chrome lets Claude control your real Chrome browser — your existing logged-in sessions, no automation detection.

1. Go to the [Claude-in-Chrome extension page](https://chromewebstore.google.com/detail/claude-in-chrome)
2. Click **Add to Chrome**
3. The extension runs silently in the background; no configuration needed

---

## Step 1 — Scaffold the workspace

```bash
careeros init ~/my-job-search
```

This copies the CareerOS templates into `~/my-job-search/`:

```
~/my-job-search/
├── CLAUDE.md                         ← agent entry point
└── .claude/
    └── skills/
        ├── onboard/SKILL.md
        ├── browse/SKILL.md
        └── track/SKILL.md
```

No user data is written yet — the agent does that during onboarding.

---

## Step 2 — Open the workspace in Claude Code

```bash
# Option A: open in Claude Code CLI
claude ~/my-job-search

# Option B: Claude.ai Projects
# Go to claude.ai → Projects → New Project
# Set the working directory to ~/my-job-search
```

---

## Step 3 — First-run onboarding

Claude reads `CLAUDE.md` at session start. Since `profile.md` doesn't exist yet, it automatically loads the onboard skill and runs an 8-question interview:

1. Current role and years of experience
2. Primary skills and tech stack
3. Target role titles
4. Location and remote preference
5. Minimum compensation (total package)
6. Target companies (optional)
7. Job boards to use
8. LinkedIn profile URL

After the interview, Claude writes these files directly to your workspace:

- `profile.md` — your profile with a scoring rubric
- `boards.md` — configured boards with search queries
- `jobs/pipeline.md` — empty pipeline table
- `activity.md` — initial log entry
- `jobs/discovered/` — directory for saved jobs

---

## Step 4 — Browse jobs

Say: **"browse linkedin"** (or any board from your `boards.md`)

Claude will:
1. Open a new Chrome tab via Claude-in-Chrome
2. Navigate to the board using your existing logged-in session
3. Extract listings from the page
4. Score each listing 0–10 against your rubric
5. Show you a markdown table of scored results
6. Ask which jobs to save

For jobs you select, Claude writes files to `jobs/discovered/` and updates `jobs/pipeline.md`.

### Supported boards

| Board | Method |
|-------|--------|
| LinkedIn | Chrome extension (real session, full access) |
| Instahyre | Chrome extension + network request interception |
| Wellfound | Chrome extension |
| Indeed | Chrome extension |
| Naukri | Chrome extension |

---

## Step 5 — Track your pipeline

Say: **"show pipeline"** or **"track"**

Claude shows your pipeline with status icons:
- `[ ]` Discovered
- `[~]` Applied
- `[?]` Interview
- `[✓]` Offer
- `[x]` Closed

To update: "mark Stripe as applied" — Claude updates `pipeline.md` and logs to `activity.md`.

---

## Keeping skills up to date

When you upgrade CareerOS, refresh skill files without touching your data:

```bash
careeros init ~/my-job-search --refresh
```

This updates `.claude/skills/` from the latest templates. Your `profile.md`, `boards.md`, `jobs/`, and `activity.md` are never touched.

---

## FAQ

**Q: Why use my real Chrome instead of a dedicated browser profile?**

A real logged-in session bypasses automation detection entirely. LinkedIn and Instahyre block headless browsers and Playwright within seconds; your real session works because it looks exactly like you browsing normally.

**Q: Does CareerOS send my data anywhere?**

No CareerOS server exists. LLM calls go to Anthropic (or whichever provider you configure). Your workspace files stay on your machine.

**Q: Can I version-control the workspace?**

Yes. `git init ~/my-job-search` works well. Consider adding `jobs/discovered/` to a `.gitignore` if the files contain salary information you prefer not to commit.
