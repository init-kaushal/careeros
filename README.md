# CareerOS

Privacy-first, agent-portable career automation platform.
Your resume, skills, and career data live in a directory you own — not in any cloud.

**[→ init-kaushal.github.io/careeros](https://init-kaushal.github.io/careeros/)** — project overview and getting started guide.

## Quickstart

```bash
# Install (Python 3.11+)
git clone https://github.com/init-kaushal/careeros
cd careeros
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Set your API key for whichever provider you want to use:
export ANTHROPIC_API_KEY=sk-ant-...   # Claude (default)
export OPENAI_API_KEY=sk-...          # OpenAI
# Ollama needs no key — just run `ollama serve` locally

# Optional: choose a model (default is claude-haiku-4-5-20251001)
export CAREEROS_MODEL=gpt-4o-mini       # OpenAI
export CAREEROS_MODEL=ollama/llama3.2   # Ollama (free, local)

# Run the onboarding wizard
careeros onboard
```

The wizard asks where to create your workspace, then extracts your profile from your resume using
whichever model you configured. Your data is written locally — nothing leaves your machine after onboarding.

## What it does

CareerOS covers the whole job-search loop — discovery, scoring, applying, researching people,
outreach, and compensation evidence — with every action gated by an approval seam and logged to
an append-only audit trail.

**Workspace**
- **`careeros onboard`** — interactive wizard that creates a workspace, extracts a structured
  profile from your resume via LLM, and writes it to `profile/profile.json`.
- **`careeros workspace status` / `workspace validate`** — workspace path, schema version,
  profile/skills/goals summary, activity counts; validate checks schema and data integrity.
- **`careeros export` / `careeros import <path>`** — zip your entire workspace, or restore one,
  in a format any agent runtime that speaks the CareerOS manifest can read.

**Job discovery + scoring**
- **`careeros job add / list / show / update / note / search`** — manage saved jobs directly.
- **`careeros browse --board <linkedin|indeed|wellfound|url>`** — browser-driven job search using
  your own logged-in session, LLM-scored against your profile, save the ones you want.

**Applying**
- **`careeros apply --job <id>`** — generates a role-specific cover letter (with an
  accept/regenerate/quit review loop), then fills the real application form in your browser via
  a platform-specific filler (Greenhouse, Lever, LinkedIn Easy Apply, or a generic fallback) —
  gated behind an explicit approval prompt before anything is submitted.
- **`careeros discover-and-apply`** — the unattended version: run from cron/launchd, it discovers
  jobs across your configured boards and auto-applies to anything scoring above a threshold you
  set, capped per run, with a full activity trail for every save and every apply.

**People + outreach**
- **`careeros research company / people --job <id>`** — browser-driven research on a job's company
  and the people there (role-classified: IC, EM, hiring manager, recruiter), LLM-extracted into
  structured records.
- **`careeros research compensation --job <id>`** — evidence-backed compensation data pulled from
  a public salary source, with an honest confidence rating rather than a guessed number.
- **`careeros outreach send --job <id> --person <id>`** — drafts a role-aware outreach message,
  same review loop as `apply`, then sends it by email only after explicit approval — the one place
  in CareerOS that makes a real, irreversible external action.
- **`careeros people update <id> --email <address>`** — CareerOS never guesses an email address;
  add one manually once you've found it.

**Automation seam**
Every command above routes through an `AgentRuntime` interface rather than talking to storage or
prompting the user directly — `LocalRuntime` blocks on a real terminal prompt for interactive use,
`AutomationRuntime` auto-approves for scheduled runs (because the approval already happened when
you set the policy threshold), and the same interface is designed for a future agent-embedded
runtime to plug in without any command code changing.

## Workspace layout

CareerOS uses a two-directory model: the framework (this repo) and your workspace (a directory you own).

```
~/my-career/                  ← your workspace, never touched by framework updates
  manifest.json               ← entry point for any agent runtime
  config.json                 ← workspace-level settings
  profile/
    profile.json              ← structured profile data (skills, goals, preferences)
  activity/
    2026-01-15.jsonl          ← append-only activity log, one event per line
  exports/                    ← created by `careeros export`
```

The workspace is self-contained. You can copy it, version-control it, or hand it to
another tool without touching CareerOS.

## Agent portability

Every workspace has a `manifest.json` that any agent runtime can read:

```json
{
  "schema_version": 1,
  "careeros_version": "0.1.0",
  "created_at": "2026-01-15T10:00:00Z"
}
```

Any tool that reads the manifest can discover the workspace structure and work with
your career data without needing CareerOS installed.

## Requirements

- Python 3.11+
- An API key for your preferred LLM provider — used for profile extraction, job scoring, cover
  letter and outreach drafting, and company/people/compensation research. Supported providers
  (via [LiteLLM](https://docs.litellm.ai/)): Anthropic, OpenAI, Ollama (free, local), Groq,
  Mistral, Google, and 100+ more.
- [Playwright](https://playwright.dev/) with Chrome, for `browse`, `apply`, `discover-and-apply`,
  and `research` — these drive your own logged-in browser session rather than an API, so there's
  no separate account to connect. `pip install playwright && playwright install chrome`.
- SMTP credentials (`CAREEROS_SMTP_HOST/PORT/USER/PASSWORD` env vars) only if you use
  `careeros outreach send` — read from the environment at send time, never written to your
  workspace.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run fully offline — no API calls, no network, no real browser. 285 tests, every
LLM/browser/SMTP call mocked at the boundary.

## Status

Eight phases shipped, in order:

1. **Workspace core** — onboarding, profile extraction, `StorageProvider` protocol, export/import
2. **Job pipeline** — job schema, LLM-assisted scoring against your profile
3. **Browser search** — `browse`, scraping LinkedIn/Indeed/Wellfound via your own session
4. **Auto-apply** — cover letter generation, platform-specific form fillers, approval-gated submit
5. **Agent interoperability** — `AgentRuntime` seam (`LocalRuntime`, and the interface a future
   agent-embedded runtime plugs into) so every command's approval logic is runtime-agnostic
6. **Automation** — `discover-and-apply` for unattended, scheduled runs with a score-threshold policy
7. **People + outreach** — company/people research, role-aware drafting, approval-gated email send
8. **Compensation research** — evidence-backed comp data with an honest confidence rating

All workspace I/O goes through the `StorageProvider` protocol, so storage backends can be
swapped without touching business logic. Every meaningful action — both outcomes of any
approval decision, not just the success path — writes to the append-only activity log.

## License

MIT — see [LICENSE](LICENSE).
