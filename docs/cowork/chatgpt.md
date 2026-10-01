# GPT Work setup (ChatGPT Projects)

Step-by-step guide for setting up CareerOS Cowork with ChatGPT Projects.

---

## Prerequisites

- CareerOS installed (`pip install -e .` from the repo)
- ChatGPT Plus or Team (Projects access required)

---

## Step 1 — Scaffold the GPT workspace

```bash
careeros init ~/my-job-search --runtime gpt
```

Creates:

```
~/my-job-search/
├── AGENTS.md
└── .gpt/skills/
    ├── onboard/   browse/   track/
    ├── market/
```

> **Note:** GPT Work ships four skills (onboard, browse, track, market). The full skill set — apply, research, outreach, follow-up, prep, interview, offer — requires Claude Code, which has direct file access and real browser control via Claude-in-Chrome. See the [capability comparison](index.md#choose-your-runtime) for details.

---

## Step 2 — Create a ChatGPT Project

1. Go to [chatgpt.com](https://chatgpt.com) → **Projects** → **New Project**
2. Name it (e.g. "Job Search")

---

## Step 3 — Set Project Instructions

1. Open `~/my-job-search/AGENTS.md` in a text editor
2. Copy all contents
3. In the project: gear icon → **Customize project** → paste into **Project Instructions**
4. Save

---

## Step 4 — Upload skill files

In the project, click **Add files** and upload:
- `.gpt/skills/onboard/SKILL.md`
- `.gpt/skills/browse/SKILL.md`
- `.gpt/skills/track/SKILL.md`
- `.gpt/skills/market/SKILL.md`
- `.gpt/skills/onboard/boards-catalog.md`

---

## Step 5 — Onboarding

Start a chat. GPT detects no `profile.md` and runs the onboarding interview. After 8 questions it outputs each file as a fenced code block — copy each one and save to `~/my-job-search/`.

Upload `profile.md` to the project once it exists (GPT needs to read it each session).

---

## What GPT Work can do

| Capability | GPT Work |
|-----------|---------|
| Onboarding interview | ✅ (outputs files as code blocks) |
| Browse public boards (Wellfound, Indeed) | ✅ (web search) |
| Browse LinkedIn / Instahyre | Paste page content into chat |
| Track pipeline | ✅ (outputs updated pipeline.md) |
| Company / comp research | ✅ (web search, outputs research notes) |
| Apply to jobs | ❌ (no browser control) |
| LinkedIn outreach | ❌ (no browser control) |
| Email outreach drafting | ✅ (outputs draft) |
| Interview prep | ✅ (outputs interview-prep.md) |
| Offer evaluation | ✅ (outputs offer.md) |

For apply, LinkedIn outreach, form filling, and the full automated workflow: use [Claude Code](claude-code.md).

---

## Keeping skills up to date

```bash
careeros init ~/my-job-search --runtime gpt --refresh
```

Re-upload the updated files from `.gpt/skills/` to your ChatGPT Project.

---

## FAQ

**Q: Can I use GPT Work with Cursor or Codex?**
Yes. Both respect `AGENTS.md`. Add `.gpt/skills/` to your project and they'll load automatically.

**Q: Can I switch to Claude Code later?**
Yes. Run `careeros init ~/my-job-search` (no `--runtime gpt`) to add the Claude skeleton. Your `profile.md`, `boards.md`, `jobs/`, and `activity.md` are untouched.
