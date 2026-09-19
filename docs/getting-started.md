# Getting Started with CareerOS

CareerOS keeps your career data in a directory you own. The framework is installed from this repo;
your workspace lives wherever you put it. Framework updates (`git pull`) never touch your data.

## Prerequisites

- Python 3.11 or later (`python3 --version`)
- An API key for your preferred LLM provider — used only during onboarding for profile extraction.
  Everything else works offline. Supported via [LiteLLM](https://docs.litellm.ai/):
  - **Anthropic Claude** (default): `ANTHROPIC_API_KEY`
  - **OpenAI**: `OPENAI_API_KEY` + `CAREEROS_MODEL=gpt-4o-mini`
  - **Ollama** (free, local): no key — just run `ollama serve` + `CAREEROS_MODEL=ollama/llama3.2`
  - **Groq, Mistral, Google, and 100+ more**: see [LiteLLM providers](https://docs.litellm.ai/docs/providers)

## 1. Install

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
```

Verify the install:

```bash
careeros --help
```

## 2. Set your API key and model

```bash
# Anthropic Claude (default — no CAREEROS_MODEL needed)
export ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
export OPENAI_API_KEY=sk-...
export CAREEROS_MODEL=gpt-4o-mini

# Ollama (free, runs locally — no key needed)
# Start Ollama first: `ollama serve` and `ollama pull llama3.2`
export CAREEROS_MODEL=ollama/llama3.2
```

Add the relevant lines to your shell profile (`~/.zshrc`, `~/.bashrc`) so you don't need to set them each session.

## 3. Run the onboarding wizard

```bash
careeros onboard
```

The wizard walks you through five steps:

1. **Workspace location** — where to create your workspace directory (default: `~/my-career`)
2. **Your name** — used in the profile
3. **Your resume** — paste plain text, a LinkedIn export, or any text describing your background.
   Leave blank to skip profile extraction and fill in the profile manually later.
4. **Profile extraction** — CareerOS calls Claude Haiku to parse your text into structured data:
   skills, job titles, years of experience, and goals.
5. **Done** — your workspace is ready.

The whole process takes under a minute. If you skip the resume step, you can run `careeros onboard`
again at any time to extract your profile.

## 4. Check your workspace

```bash
careeros workspace status
```

Sample output:

```
Workspace:  /Users/you/my-career
Version:    schema v1 (careeros 0.1.0)
Profile:    Jane Smith · Software Engineer
Skills:     12 skills
Goals:      3 goals
Activity:   2 events today
```

## 5. Inspect your profile

```bash
cat ~/my-career/profile/profile.json
```

The profile is plain JSON. You can edit it directly — CareerOS reads it fresh on every command.

## 6. Export your workspace

```bash
careeros export
```

This creates a zip file at `~/my-career/exports/careeros-export-<timestamp>.zip`. The zip contains
your entire workspace and can be imported into any CareerOS installation or handed to an agent
runtime that speaks the workspace manifest format.

## 7. Import a workspace

```bash
careeros import ~/Downloads/careeros-export-2026-01-15.zip
```

CareerOS unpacks the zip to a new directory and makes it your active workspace.

---

## Workspace layout

```
~/my-career/
  manifest.json         entry point — schema version, careeros version, timestamps
  config.json           workspace-level settings
  profile/
    profile.json        your structured profile (skills, experience, goals)
  activity/
    2026-01-15.jsonl    append-only event log, one JSON object per line
  exports/              zips created by `careeros export`
```

The workspace is self-contained. Version-control it, back it up, or copy it between machines
without touching the CareerOS install.

## Common questions

**Can I use a different workspace directory?**
Yes. Run `careeros onboard --workspace /path/to/dir` or just provide a custom path when the
wizard asks. CareerOS stores the active workspace path in `~/.config/careeros/config.json`.

**Does CareerOS send my data anywhere?**
Your resume and job data are sent to your configured LLM provider for extraction, scoring, and
drafting (cover letters, outreach messages, research summaries) — never to a CareerOS-run server,
since there isn't one. `browse`, `apply`, `discover-and-apply`, and `research` drive Playwright
against your own logged-in Chrome session rather than calling a scraping API. `outreach send` is
the only command that reaches a third party directly, by SMTP, and only after you approve it.

**Can I run multiple workspaces?**
Yes — export one workspace, import it elsewhere, or just create a new one with `careeros onboard`.
Only one workspace is active at a time (tracked in `~/.config/careeros/config.json`).

**What agents can read my workspace?**
Any agent that reads `manifest.json` and understands the workspace layout. The manifest schema
is versioned, and CareerOS validates it on open. Future versions will maintain backwards
compatibility.

## Beyond onboarding

Once your workspace exists, CareerOS covers the rest of the job search:

- `careeros browse --board linkedin` — search and score jobs through your own browser session
- `careeros apply --job <id>` — generate a cover letter and fill the real application form
- `careeros discover-and-apply` — run unattended from cron, auto-applying above a score threshold
- `careeros research company --job <id>` / `research people --job <id>` / `research compensation --job <id>`
  — browser-driven, LLM-extracted research on a job's company, its people, and market comp
- `careeros outreach send --job <id> --person <id>` — draft and send outreach, gated by approval
- `careeros job add / list / show / update / note / search` — manage saved jobs directly

Run `careeros --help` or `careeros <command> --help` for the full option list on any of these.
