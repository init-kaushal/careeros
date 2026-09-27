# Getting Started with CareerOS

CareerOS keeps your career data in a directory you own. The framework is installed from this repo;
your workspace lives wherever you put it. Framework updates (`git pull`) never touch your data.

## Prerequisites

- Python 3.11 or later (`python3 --version`)
- Chromium — installed automatically when you first run `pip install playwright && playwright install chrome`
- An API key for your preferred LLM provider — used during onboarding for profile extraction, and
  again by job scoring, cover letter/outreach drafting, and company/people/compensation research.
  Use `CAREEROS_MODEL=ollama/...` if you want every one of those calls to stay on your machine.
  Supported via [LiteLLM](https://docs.litellm.ai/):
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
pip install playwright && playwright install chrome
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

### Optional: fallback models

`CAREEROS_FALLBACK_MODELS` accepts a comma-separated list of models tried in order when the primary
hits a rate limit (429) or provider error:

```bash
# Primary: NVIDIA NIM free tier; fallback: Gemini 2.0 Flash
export CAREEROS_MODEL=nvidia_nim/meta/llama-3.1-70b-instruct
export NVIDIA_NIM_API_KEY=nvapi-...
export CAREEROS_FALLBACK_MODELS=gemini/gemini-2.0-flash-exp
export GEMINI_API_KEY=AIza...
```

Fallbacks use LiteLLM's native retry logic — no configuration beyond the env var is needed.

## 3. Run the onboarding wizard

```bash
careeros onboard
```

The wizard walks you through six steps:

1. **Workspace location** — where to create your workspace directory (default: `~/my-career`)
2. **Resume file** — CareerOS scans `~/Desktop`, `~/Downloads`, and `~/Documents` and shows a
   numbered pick list. Choose a number, or type `0` to enter a path manually. Supported formats:
   **PDF**, Markdown (`.md`), and plain text (`.txt`).
   > Note: scanned/image-only PDFs are not supported — export a text-based PDF from Word, Google
   > Docs, or LaTeX instead. If pypdf can't extract any text it will tell you.
3. **Profile extraction** — CareerOS calls your LLM to extract name, title, years of experience,
   and a summary from the resume text. *(this may take up to a minute)*
4. **Confirmation** — the extracted profile is shown in full (name, title, years, summary snippet,
   verified skills). Confirm with `Y` to continue, or choose to pick a different file and re-extract.
5. **Preferences and goals** — target roles, remote preference, compensation floor, locations, and
   optional short/long-term goals.
6. **API job sources** — optional Greenhouse and Lever board slugs for API-based job discovery
   (no browser needed). Enter the slug from the board URL — e.g. `stripe` for
   `boards.greenhouse.io/stripe`. Browser-based boards (LinkedIn, Wellfound, Naukri) are set up
   separately with `careeros browser login`.
7. **Done** — your workspace is ready.

The whole process takes under a minute. All prompts support arrow keys, Home/End, and backspace for editing.

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

## 5. Update your configuration

To change preferences, goals, or API sources after onboarding:

```bash
careeros workspace configure
```

You'll see a menu:

```
What would you like to update?
  1  Job preferences (roles, remote, salary, locations)
  2  Career goals (short-term, long-term)
  3  API job sources (Greenhouse / Lever slugs)
  4  All of the above
```

Each section pre-fills your current values — edit only what you want to change. Arrow keys and backspace work in all prompts.

## 6. Sign in to your job boards

CareerOS drives a dedicated browser profile — separate from your everyday Chrome — so a scheduled
run never holds sessions for anything but the boards you authorized:

```bash
careeros browser login --board linkedin
careeros browser login --board indeed
careeros browser login --board wellfound
```

Each command opens a real browser window at that board's login page. Sign in as normal; CareerOS
detects the completed session and closes the window. Check what's authorized at any time:

```bash
careeros browser status
```

Re-run `login` whenever a session expires. You need an active session for every board you want to
browse, apply to, or research against.

## 7. Browse and score jobs

```bash
careeros browse --board linkedin
```

CareerOS searches LinkedIn (or another board) through the dedicated profile, scores each result
against your profile and goals, and shows them ranked. Save the ones you want to apply to:

```
[92] Senior SRE @ Acme Corp — San Francisco, CA
Save this job? [y/n/q]
```

Saved jobs land in `jobs/` in your workspace. List them:

```bash
careeros job list
careeros job show <id>
```

You can also add jobs from a direct URL or a company's ATS without browsing:

```bash
careeros job add --url https://boards.greenhouse.io/acme/jobs/12345
careeros job search --source greenhouse --company acme
careeros job search --source lever --company acme
```

## 8. Research a job

Before applying or reaching out, research the company and its people:

```bash
careeros research company --job <id>
careeros research people --job <id>
careeros research compensation --job <id>
```

`research people` browser-scrapes a person list and classifies each one (IC, EM, hiring manager,
recruiter). `research compensation` pulls salary data and gives an honest confidence rating.

Add a LinkedIn profile URL and email address to a person once you have them:

```bash
careeros people update <id> --linkedin-url https://www.linkedin.com/in/...
careeros people update <id> --email name@company.com
```

## 9. Apply to a job

```bash
careeros apply --job <id>
```

CareerOS generates a cover letter tailored to the role and your profile, shows it to you for
review, and fills the real application form only after you approve:

```
--- Cover letter draft ---
Dear Hiring Manager,
...

[a]ccept  [r]egenerate  [q]uit
> a

Submit application to Acme Corp? [y/n]: y
```

A browser window opens and fills the form. You watch it happen. CareerOS detects submission and
logs the event. An `Approval` record is kept under `approvals/` so the whole lifecycle — proposed,
approved, executed — has a durable trail.

To use a resume variant tailored to this specific job, see §12 below.

## 10. Set policies (optional)

Before running any automated commands, consider setting policies. CareerOS checks these before
proposing any application — a blocked job never reaches the approval step:

```bash
# Edit config/policies.json directly, or create it:
cat > ~/my-career/config/policies.json << 'EOF'
{
  "blocked_companies": ["Acme Corp"],
  "min_salary": 150000,
  "blocked_locations": ["New York"]
}
EOF
```

- `blocked_companies` — exact company name match (case-insensitive)
- `min_salary` — minimum `salary_min` on the job record; jobs without salary data are not blocked
- `blocked_locations` — substring match; `"New York"` blocks "New York, NY" and "New York City"

A policy block is permanent: it cannot be overridden with a flag or env var. Edit the file to
change it.

## 11. Run unattended discovery and apply

```bash
careeros discover-and-apply
```

Discovers jobs across your configured boards, scores them, and auto-applies to anything above
your threshold — capped per run and logged in full. Configure the automation policy in
`config.json`:

```json
{
  "automation_policy": {
    "auto_apply_min_score": 80,
    "max_auto_applies_per_run": 5,
    "boards": ["linkedin", "indeed"]
  }
}
```

Add API board sources (no browser required for discovery):

```bash
# config/sources.json
[
  {"source": "greenhouse", "company": "acme"},
  {"source": "lever", "company": "stripe"}
]
```

Run from cron or launchd for fully automated discovery. Every save, merge, policy block, and
application is logged to the activity trail.

## 12. Resume variants

Build a resume tailored to one specific job:

```bash
careeros resume variant --job <id>
```

Every body bullet in the variant is a verbatim span of your master resume, verified against it.
The model selects and orders; it never writes new text. The result is saved as a PDF at
`resumes/versions/<job_id>/resume.pdf`. `careeros apply` picks it up automatically.

To update your master resume and re-extract skills with evidence verification:

```bash
careeros resume ingest /path/to/updated-resume.pdf   # PDF, .md, or .txt
```

Skills without a verified quote from the resume text are dropped and named, not silently stored.

## 13. Outreach

Draft and send outreach to a researched person:

```bash
careeros outreach send --job <id> --person <id>
```

The flow mirrors `apply`: draft, review loop, approval, send. The email is sent only after you
approve. An `OutreachMessage` record is kept under `outreach/` with the send state, timestamps,
and cadence counters.

Once outreach is sent, manage the follow-up cadence:

```bash
# Propose follow-ups for relationships due for another touch (run from cron):
careeros outreach follow-up

# Review and send the drafted follow-ups:
careeros outreach review

# End a cadence permanently:
careeros outreach close --job <id> --person <id> --reason "Accepted offer elsewhere"
```

`outreach follow-up` never sends on its own — it only drafts and leaves pending approvals.
`outreach review` is the only path that sends.

## 14. LinkedIn connection requests

```bash
careeros outreach connect --job <id> --person <id>
```

Drafts a 300-character LinkedIn connection note, shows you the exact bytes alongside the profile
URL it will navigate, then sends the invitation only after approval. The browser runs **headful**
on purpose: you watch it happen on your own account.

CareerOS sends at most **one connection request to a person, ever, across every job**. A second
attempt for the same person is refused outright.

Requires the person to have a LinkedIn URL set:

```bash
careeros people update <id> --linkedin-url https://www.linkedin.com/in/...
```

---

## Workspace layout

```
~/my-career/
  manifest.json             entry point — schema version, careeros version, timestamps
  config.json               workspace-level settings (automation policy, etc.)
  config/
    policies.json           blocking rules: companies, salary floor, locations
    cadence_policy.json     follow-up cadence: intervals, max touches per relationship
    sources.json            API board sources for discover-and-apply (Greenhouse, Lever)
  profile/
    profile.json            your structured profile (name, title, summary, years exp)
    skills.json             evidence-backed skills, each with a verified quote + line
    goals.json              short- and long-term goals, non-negotiables
    preferences.json        target titles, locations, compensation, work arrangement
  resumes/
    master.md               your stored resume, read by `careeros resume ingest`
    versions/<job_id>/      per-job resume variants — PDF + variant.json sidecar
  jobs/<id>.json            one file per saved job
  approvals/<id>.json       one approval record per proposed action
  outreach/<id>.json        one outreach message per person+job pair
  connections/<id>.json     one connection-request record per person+job pair
  activity/
    YYYY-MM-DD.jsonl        append-only event log, one JSON object per line
  exports/                  zips created by `careeros export`
```

The workspace is self-contained. Version-control it, back it up, or copy it between machines
without touching the CareerOS install.

## Common questions

**Can I use a different workspace directory?**
Yes. Run `careeros onboard --workspace /path/to/dir` or just provide a custom path when the
wizard asks. CareerOS stores the active workspace path in `~/.config/careeros/config.json`.
Every command also honors `--workspace <path>` and the `CAREEROS_WORKSPACE` environment variable,
in that precedence order.

**Does CareerOS send my data anywhere?**
Your resume and job data are sent to your configured LLM provider for extraction, scoring, and
drafting (cover letters, outreach messages, research summaries) — never to a CareerOS-run server,
since there isn't one. `browse`, `apply`, `discover-and-apply`, and `research` drive Playwright
against a dedicated CareerOS browser profile rather than calling a scraping API. `outreach send`
and `outreach connect` are the only commands that reach a third party directly, and only after
you approve them.

**Can I run multiple workspaces?**
Yes — export one workspace, import it elsewhere, or just create a new one with `careeros onboard`.
Only one workspace is active at a time (tracked in `~/.config/careeros/config.json`).

**What is the approval workflow?**
Every irreversible action (apply, outreach send, follow-up send, LinkedIn connect) is gated by
an `Approval` record in `approvals/`. Interactive commands prompt you at the terminal. The
scheduled `discover-and-apply` command auto-approves based on your configured threshold, because
the approval already happened when you set the policy. An external agent session can drive the
same operations through `careeros/operations/` directly — see `docs/agent-integration.md` for
the full contract.

**What agents can read my workspace?**
Any agent that reads `manifest.json` and understands the workspace layout. The manifest schema is
versioned, and CareerOS validates it on open. See `docs/agent-integration.md` for the operations
layer contract that lets an external agent propose and execute actions cross-process.

---

## Testing the current build

**Automated tests (always run first):**

```bash
source .venv/bin/activate
pytest --ignore=tests/integration
```

All 1280 unit tests should pass in under 15 seconds.

**Key manual scenarios (automated tests cannot exercise these):**

| Scenario | Command | What to check |
|---|---|---|
| Onboard with PDF resume | `careeros onboard` | File picker shows `.pdf` files alongside `.md`/`.txt`. Profile confirmation displays name, title, years, summary, and skills. Re-pick works. |
| Onboard with scanned PDF | `careeros onboard`, select an image-only PDF | Error: "PDF contained no extractable text — may be scanned/image-only." Clean exit. |
| LLM fallback | Set `CAREEROS_MODEL` to a rate-limited provider, set `CAREEROS_FALLBACK_MODELS` to a working model, run any LLM-powered command | On 429, request retries with the fallback model transparently. |
| Browser isolation | `careeros browser login --board linkedin` | Browser opens to LinkedIn login, not your regular Chrome profile. Session persists across commands. |
| Policy block | Set `blocked_companies: ["Acme Corp"]` in `config/policies.json`, then `careeros apply --job <acme-id>` | Command exits immediately with a policy-blocked message. No cover letter drafted, no approval created. |
| Duplicate invite prevention | `careeros outreach connect` twice for the same person | Second attempt raises `ConnectionAlreadySent` before any approval is opened. |
| Apply approval flow | `careeros apply --job <id>` | Cover letter shown, `[a]ccept` advances to browser fill, approval in `approvals/` transitions pending → approved → executed. |
| Follow-up queue | `careeros outreach follow-up` after sending outreach | Drafts pending approvals under `approvals/`; `careeros outreach review` is the only path that sends. |
| Stage-based skip in discover-and-apply | Create a `jobs/<id>.json` with `"stage": "applied"` and no `applied_at` field, run `careeros discover-and-apply` | Job is skipped (not re-applied); `already_applied` is true. |
| Sighting accumulation | Save a job with `url: null`, re-discover it from the same board | Second observation appends a new `Sighting` rather than being silently dropped. |

**Integration tests (requires real credentials):**

```bash
# Requires ANTHROPIC_API_KEY, an active LinkedIn session, and a real workspace
pytest tests/integration/
```

The three items from ROADMAP.md that require manual verification beyond the above:
1. **Phase 12 exit**: a real outreach send from an agent session (needs `CAREEROS_SMTP_*` + real recipient)
2. **Phase 13a exit**: a relationship carried through a real follow-up on cadence (needs LLM creds + time)
3. **Phase 13b exit**: `careeros outreach connect` against a live LinkedIn session — and verifying the
   CSS selectors in `careeros/browser/connect/linkedin.py` against actual LinkedIn HTML, since
   they were written from static markup and have never been exercised against the live site
