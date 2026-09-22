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
whichever model you configured. Your data is always written to a directory you control, never to a
CareerOS-run server — but onboarding is not the only place your data reaches a third party: job
scoring, cover letter and outreach drafting, and company/people/compensation research all send
relevant profile and job data to whichever LLM provider you configured. Run everything through
`CAREEROS_MODEL=ollama/...` if you want zero data leaving your machine at any point.

## What it does

CareerOS covers the whole job-search loop — discovery, scoring, applying, researching people,
outreach, and compensation evidence — with every action gated by an approval seam and logged to
an append-only audit trail.

**Workspace**
- **`careeros onboard`** — interactive wizard that creates a workspace, extracts a
  structured profile from your resume via LLM, and writes it to `profile/profile.json`.
  Skills are extracted separately and kept only when backed by a verified quote.
- **`careeros resume ingest [path]`** — re-extract your skills from your resume, keeping
  only those backed by a verbatim quote that is verified to appear in the file. Skills the
  model could not evidence are dropped and named, rather than silently stored. If nothing
  verifies — including on an API or network failure — your existing `profile/skills.json`
  is left untouched and the command exits non-zero, so a bad run is always safe to retry.
  Pass a path to replace your stored master resume, but only once ingestion succeeds.
- **`careeros resume variant --job <id>`** — build a resume tailored to one job. Every
  body bullet is a verbatim span of your master resume, verified against it: the model
  selects and orders, it never writes new text, and any span it cannot copy exactly —
  including one stitched together across a paragraph break — is dropped and named. The
  contact header (name, title, location, email) comes from your stored profile, not from
  the master. Stored as `resumes/versions/<job_id>/resume.pdf` with a `variant.json`
  sidecar recording which line of `resumes/master.md` backs each body bullet, the header
  as rendered, and checksums of both the PDF and the master it was built from — so a
  variant whose master has since changed is flagged rather than trusted. `apply` uses it
  automatically.
- **`careeros workspace status` / `workspace validate`** — workspace path, schema version,
  profile/skills/goals summary, activity counts; validate checks schema and data integrity.
- **`careeros export` / `careeros import <path>`** — zip your entire workspace, or restore one,
  in a format any agent runtime that speaks the CareerOS manifest can read.

**Job discovery + scoring**
- **`careeros browser login --board <linkedin|indeed|wellfound>`** — sign in to a job board in
  the isolated CareerOS profile. Opens a real browser window; CareerOS detects the completed
  sign-in and closes it. Your credentials are never seen or stored by CareerOS.
- **`careeros browser status`** — which boards are currently authorized, and where the profile lives.
- **`careeros job add / list / show / update / note / search`** — manage saved jobs directly.
- **`careeros browse --board <linkedin|indeed|wellfound|url>`** — browser-driven job search using
  the dedicated CareerOS profile, LLM-scored against your profile, save the ones you want.
- **`careeros job search --source <greenhouse|lever> --company <slug>`** — search a company's
  public ATS board directly, no browser required. Deduplicates against jobs you already have.

**Applying**
- **`careeros apply --job <id>`** — generates a role-specific cover letter (with an
  accept/regenerate/quit review loop), then fills the real application form in your browser via
  a platform-specific filler (Greenhouse, Lever, LinkedIn Easy Apply, or a generic fallback) —
  gated behind an explicit approval prompt before anything is submitted.
- **`careeros discover-and-apply`** — the unattended version: run from cron/launchd, it discovers
  jobs across your configured boards and auto-applies to anything scoring above a threshold you
  set, capped per run, with a full activity trail for every save and every apply.
  It also polls any API boards configured in `config/sources.json`, so discovery does not
  depend on browser scraping alone.

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

**Agent integration**
`careeros outreach send`'s propose/decide/execute steps live in `careeros/operations/outreach.py`
over a durable `Approval` record under `approvals/`, so one process can propose a send and a
different, later process can carry out the human's decision and execute it. That is what lets an
agent session that is not the CLI drive CareerOS: it can open the workspace via
`CAREEROS_WORKSPACE`, propose an outreach send, hold a conversation about it, and execute the send
in a later call, all without a live connection back to the first process. See
[docs/agent-integration.md](docs/agent-integration.md) for the full contract — the `AgentRuntime`
Protocol, the approval schema, the error vocabulary, and a runnable two-process example. **Outreach
send is drivable from an external agent session today; job apply is not yet** — that is Phase 12b.

**Only `careeros outreach send`, `careeros outreach mark-referral-requested`, and `careeros people
update` honor `CAREEROS_WORKSPACE`.** Every other command still resolves its workspace from an
explicit `--workspace` flag or the config file saved by `careeros onboard`, never from the
environment variable. Exporting `CAREEROS_WORKSPACE` while a different workspace is configured
splits your data across two trees. Pass an explicit workspace path, or keep the two in sync, until
this converges — see `docs/agent-integration.md` and `DIVERGENCES.md`.

## Workspace layout

CareerOS uses a two-directory model: the framework (this repo) and your workspace (a directory you own).

```
~/my-career/                  ← your workspace, never touched by framework updates
  manifest.json               ← entry point for any agent runtime
  config.json                 ← workspace-level settings
  profile/
    profile.json              ← name, title, location, years of experience, summary
    skills.json               ← evidence-backed skills, each with a verified quote + line
    goals.json                ← short- and long-term goals, non-negotiables, what you're open to
    preferences.json          ← target titles, locations, compensation, work arrangement
  resumes/
    master.md                 ← your stored resume, read by `careeros resume ingest`
    versions/
      my-resume.pdf           ← your own files; used when a job has no tailored variant
      <job_id>/
        resume.pdf            ← job-tailored variant, uploaded by `careeros apply`
        variant.json          ← which line of master.md backs each bullet, plus the header
  activity/
    2026-01-15.jsonl          ← append-only activity log, one event per line
  exports/                    ← created by `careeros export`
```

The workspace is self-contained. You can copy it, version-control it, or hand it to
another tool without touching CareerOS.

`config/sources.json` lists API job boards to poll:

```json
{"sources": [{"source": "greenhouse", "board": "stripe", "company": "Stripe", "mode": "SEARCH_ONLY"}]}
```

`board` is the company's slug on that ATS; `company` is the display name used for
deduplication. Entries without a `board` are inert — `careeros workspace validate` reports them.

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
  and `research`. These drive a **dedicated CareerOS browser profile**, not your everyday Chrome
  profile — so an unattended run never holds your banking or email sessions. Sign in to each board
  once with `careeros browser login --board <name>`.
  `pip install playwright && playwright install chrome`. `pip install` does not install
  either browser — `careeros resume variant` renders PDFs with Playwright's bundled
  Chromium rather than Chrome, so it needs its own `playwright install chromium`.
- SMTP credentials (`CAREEROS_SMTP_HOST/PORT/USER/PASSWORD` env vars) only if you use
  `careeros outreach send` — read from the environment at send time, never written to your
  workspace.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run fully offline — no API calls, no network, no real browser. Every
LLM/browser/SMTP call mocked at the boundary.

## Status

Eleven phases shipped, in order, plus the first half of a twelfth:

1. **Workspace core** — onboarding, profile extraction, `StorageProvider` protocol, export/import
2. **Job pipeline** — job schema, LLM-assisted scoring against your profile
3. **Browser search** — `browse`, scraping LinkedIn/Indeed/Wellfound via a dedicated CareerOS profile
4. **Auto-apply** — cover letter generation, platform-specific form fillers, approval-gated submit
5. **Agent interoperability** — `AgentRuntime` seam (`LocalRuntime`, and a `ClaudeCodeRuntime`
   exercised so far only by an internal test — see Phase 12 in `ROADMAP.md`) so every command's
   approval logic is runtime-agnostic
6. **Automation** — `discover-and-apply` for unattended, scheduled runs with a score-threshold policy
7. **People + outreach** — company/people research, role-aware drafting, approval-gated email send
8. **Compensation research** — evidence-backed comp data with an honest confidence rating
9. **Policy engine, content sanitization, browser isolation** — deterministic pre-approval policy,
   untrusted-content delimiters, and a dedicated browser profile
10. **Job source connectors + deduplication** — `JobSource` connectors for Greenhouse and Lever,
    and a dedup engine behind a single `JobStore` creation seam that all four job-creation paths
    route through
11. **Deep resume intelligence + resume variants** — evidence-backed skill extraction
    (`careeros resume ingest`) where every stored skill carries a verified quote from the source
    resume, plus `careeros resume variant` to render a job-tailored resume whose body is verbatim
    spans of the master resume, with a self-validating sidecar recording provenance for every
    bullet and header

Phase 12a has also shipped: the outreach-send flow now runs through a runtime-agnostic
`careeros/operations/` layer over a durable `Approval` record, so an agent session outside the CLI
can propose a send in one process and a human's decision execute it in another — see "Agent
integration" above. Job apply is not yet rewired onto that layer; that is Phase 12b.

All workspace I/O goes through the `StorageProvider` protocol, so storage backends can be
swapped without touching business logic. Every meaningful action — both outcomes of any
approval decision, not just the success path — writes to the append-only activity log.

See [ROADMAP.md](ROADMAP.md) for what's planned next: apply support in the operations layer
(`careeros/operations/apply.py`, the `apply_cmd` rewire, and `discover-and-apply`
de-duplication — Phase 12b) and then outreach expansion (Phase 13).

## License

MIT — see [LICENSE](LICENSE).
