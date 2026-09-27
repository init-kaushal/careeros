# GPT Work setup (ChatGPT Projects)

Step-by-step guide for setting up a CareerOS Cowork workspace with ChatGPT Projects.

---

## Prerequisites

- CareerOS installed (`pip install -e .` from the repo)
- A ChatGPT account with **Projects** access (ChatGPT Plus or Team)

---

## Step 1 — Scaffold the GPT workspace

```bash
careeros init ~/my-job-search --runtime gpt
```

This creates:

```
~/my-job-search/
├── AGENTS.md                         ← agent entry point + project instructions
└── .gpt/
    └── skills/
        ├── onboard/SKILL.md
        ├── browse/SKILL.md
        └── track/SKILL.md
```

---

## Step 2 — Create a ChatGPT Project

1. Go to [chatgpt.com](https://chatgpt.com)
2. Click **Projects** in the left sidebar → **New Project**
3. Name it something like "Job Search" or "CareerOS"

---

## Step 3 — Set up Project Instructions

Project Instructions are loaded at the start of every conversation in the project — this is where `AGENTS.md` goes.

1. Inside your project, click the gear icon or **"Customize project"**
2. Open `~/my-job-search/AGENTS.md` in a text editor
3. Copy the full contents
4. Paste into the **Project Instructions** field
5. Save

---

## Step 4 — Upload skill files

ChatGPT Projects lets you attach files that GPT can read in every conversation.

Upload these files from your workspace:
- `.gpt/skills/onboard/SKILL.md`
- `.gpt/skills/browse/SKILL.md`
- `.gpt/skills/track/SKILL.md`

In the project, click **Add files** (or the paperclip icon) and upload each one.

---

## Step 5 — First-run onboarding

Start a new conversation in your project. GPT reads `AGENTS.md` (from Project Instructions) and, since `profile.md` doesn't exist yet, runs the onboard skill — an 8-question interview:

1. Current role and years of experience
2. Primary skills and tech stack
3. Target role titles
4. Location and remote preference
5. Minimum compensation (total package)
6. Target companies (optional)
7. Job boards to use
8. LinkedIn profile URL

After the interview, GPT **outputs file contents as fenced code blocks** — it cannot write files directly. Save each one to your `~/my-job-search/` directory:

- `profile.md`
- `boards.md`
- `jobs/pipeline.md`
- `activity.md`

Create `jobs/discovered/` as an empty directory.

---

## Step 6 — Upload profile to project

Once `profile.md` exists, upload it to the ChatGPT Project so GPT can read your rubric in future sessions:

1. In your project, click **Add files**
2. Upload `profile.md`

Re-upload this file whenever you update your profile.

---

## Step 7 — Browse jobs

Say: **"browse wellfound"** (or any public board — see limitations below)

GPT will:
1. Use its built-in web search to find listings on the board
2. Score each listing 0–10 against your rubric
3. Show you a markdown table
4. Ask which jobs to save

For each job you select, GPT outputs complete file contents as code blocks — copy each one and save to your workspace.

### Board limitations

| Board | GPT Work |
|-------|---------|
| Wellfound, Indeed, Naukri | Web search (works well) |
| LinkedIn | Requires you to paste page content |
| Instahyre | Requires you to paste page content |

**For LinkedIn:** search LinkedIn Jobs yourself, select all text on the results page (Cmd+A / Ctrl+A), copy, paste into the chat. GPT will parse and score the listings.

---

## Step 8 — Track your pipeline

Say: **"show pipeline"**

GPT shows your pipeline table with status icons. To update a status, say "mark Stripe as applied" — GPT outputs the complete updated `pipeline.md` and an `activity.md` entry for you to save.

---

## Keeping skills up to date

When you upgrade CareerOS:

```bash
careeros init ~/my-job-search --runtime gpt --refresh
```

This updates `.gpt/skills/` in your local workspace. Re-upload the updated skill files to your ChatGPT Project to pick up the changes.

---

## Key difference from Claude Code

GPT Work has one important constraint: **GPT cannot write files directly**. Every file write produces a code block you apply manually. This is by design — ChatGPT Projects don't have file system access.

For full automation (Claude writes files, browses with your real Chrome session, no manual copy-paste), use the [Claude Code setup](claude-code.md) instead.

---

## FAQ

**Q: Can I use GPT Work with Cursor or OpenAI Codex instead of ChatGPT?**

Yes. Both tools respect `AGENTS.md` as the project entry point. Add `.gpt/skills/` to your repo and Codex/Cursor will read the skill files automatically. The same code-block output pattern applies.

**Q: Does CareerOS send my data anywhere?**

No CareerOS server exists. LLM calls go to OpenAI. Your workspace files stay on your machine until you upload them to a ChatGPT Project — at that point they're stored by OpenAI per their data retention policy.

**Q: Can I switch from GPT Work to Claude Code later?**

Yes — the workspace file layout is identical. Run `careeros init ~/my-job-search` (without `--runtime gpt`) to add the Claude skeleton alongside your existing data. Your `profile.md`, `boards.md`, `jobs/`, and `activity.md` are untouched.
