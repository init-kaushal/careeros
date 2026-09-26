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
  same review loop as `apply`, then sends it by email only after explicit approval. One of several
  commands that take a real, irreversible external action — the others being `careeros apply`,
  `careeros discover-and-apply`, `careeros outreach review` and `careeros outreach connect`, each
  gated on an approval the same way.
- **`careeros people update <id> --email <address>`** — CareerOS never guesses an email address;
  add one manually once you've found it. This is a deliberate permanent refusal, not a missing
  feature: every way to obtain a stranger's work address automatically is pattern-guessing, and a
  wrong guess means mailing someone who never entered this system.

**Follow-up cadence**
- **`careeros outreach follow-up`** — the scheduled proposer, meant for cron. Finds every
  relationship due for another touch under `config/cadence_policy.json`, drafts one, and stops.
  **It never sends.** Every draft is left as a pending approval, so nothing reaches a human's
  inbox that you did not read first. `--dry-run` shows what it would draft without drafting it.
- **`careeros outreach review`** — drains that queue interactively: shows each draft, then accept,
  regenerate, decline, or skip. Accepting is the only path that sends.
- **`careeros outreach close --job <id> --person <id> --reason <text>`** — ends a cadence for good.
  `--reason` is required, because a cadence that stopped for an unrecorded reason is exactly what
  you will not remember in three months. Declining a follow-up only defers it by one interval;
  closing is the actual off switch.

**LinkedIn connection requests**
- **`careeros outreach connect --job <id> --person <id>`** — drafts a LinkedIn connection note
  (300 characters, LinkedIn's own limit), shows you the exact bytes alongside the profile URL it
  will navigate, and sends the invitation only after you approve it. The browser runs **headful**
  on purpose: you watch it happen on your own account.

  This is the one command in CareerOS whose result **another person sees and nobody can recall**.
  An unwanted email can be ignored or deleted; a connection request appears in the recipient's
  notifications the moment it goes out. So it is approval-gated per request, and CareerOS sends at
  most **one connection request to a person, ever, across every job** — a second one is refused
  outright rather than left to LinkedIn to deduplicate.
- **`careeros people update <id> --linkedin-url <url>`** — sets the profile URL this flow needs.
  Worth knowing about for a person you added by hand: the field is otherwise only ever written by
  `careeros research people`, so without this flag a hand-added person had no path to it at all.

**Automation seam**
Every command above routes through an `AgentRuntime` interface rather than talking to storage or
prompting the user directly — `LocalRuntime` blocks on a real terminal prompt for interactive use,
`AutomationRuntime` auto-approves for scheduled runs (because the approval already happened when
you set the policy threshold), and the same interface is designed for a future agent-embedded
runtime to plug in without any command code changing.

**Agent integration**
Both `careeros outreach send` and `careeros apply`'s propose/decide/execute steps live in
`careeros/operations/` (`outreach.py` and `apply.py`) over a durable `Approval` record under
`approvals/`, so one process can propose a send or an application and a different, later process
can carry out the human's decision and execute it. That is what lets an agent session that is not
the CLI drive CareerOS: it can open the workspace via `CAREEROS_WORKSPACE`, propose an outreach
send or a job application, hold a conversation about it, and execute the action in a later call,
all without a live connection back to the first process. See
[docs/agent-integration.md](docs/agent-integration.md) for the full contract — the `AgentRuntime`
Protocol, the approval schema, the error vocabulary, and a runnable two-process example for each
flow. **Outreach send, job apply, follow-ups and LinkedIn connection requests are all drivable from an
external agent session today.** Apply's cross-process contract (§11 of that document), the
follow-up contract (§12) and the connection-request contract (§13) have not been proven with a
committed test that runs the propose and execute halves as two literal subprocesses, as outreach's
has; all three sections' snippets were run that way by hand to verify them.

Follow-ups are the first flow built around a *queue*, and §12 documents it as a first-class entry
point rather than a CLI detail: the scheduled command leaves pending approvals behind and
`list_pending` is how any caller finds them, so an agent can drain that queue instead of
`careeros outreach review` with no loss of function.

Every CLI command honors the explicit `--workspace` flag, then `CAREEROS_WORKSPACE`, then the config
file saved by `careeros onboard`, in that precedence order — see `docs/agent-integration.md` §2.

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

## License

MIT — see [LICENSE](LICENSE).
