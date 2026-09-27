# Getting Started with CareerOS

CareerOS keeps your career data in a directory you own. The framework is installed from this repo; your workspace lives wherever you put it. Framework updates (`git pull`) never touch your data.

---

## Prerequisites

- Python 3.11 or later (`python3 --version`)
- An API key for your preferred LLM provider — used during onboarding for profile extraction, and again by job scoring, cover letter/outreach drafting, and company/people/compensation research. Use `CAREEROS_MODEL=ollama/...` if you want every one of those calls to stay on your machine. Supported via [LiteLLM](https://docs.litellm.ai/):
  - **Anthropic Claude** (default): `ANTHROPIC_API_KEY`
  - **OpenAI**: `OPENAI_API_KEY` + `CAREEROS_MODEL=gpt-4o-mini`
  - **Ollama** (free, local): no key — just run `ollama serve` + `CAREEROS_MODEL=ollama/llama3.2`
  - **Groq, Mistral, Google, and 100+ more**: see [LiteLLM providers](https://docs.litellm.ai/docs/providers)

---

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

> **Every new terminal session:** you must activate the venv before using `careeros`:
> ```bash
> source ~/Projects/careeros/.venv/bin/activate
> ```
> To avoid this, add the venv's `bin` directory to your PATH permanently in `~/.zshrc`:
> ```bash
> export PATH="$HOME/Projects/careeros/.venv/bin:$PATH"
> ```
> Then `source ~/.zshrc` once and `careeros` will be available in every terminal.

---

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

`CAREEROS_FALLBACK_MODELS` accepts a comma-separated list of models tried in order when the primary hits a rate limit (429) or provider error:

```bash
export CAREEROS_MODEL=gemini/gemini-3.5-flash-lite
export GEMINI_API_KEY=AIza...
export CAREEROS_FALLBACK_MODELS=nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b
export NVIDIA_NIM_API_KEY=nvapi-...
```

---

## Cowork workspace (recommended) {#cowork-workspace-recommended}

The Cowork workspace is how most job discovery works in CareerOS. You talk to Claude in chat — "browse linkedin", "show my pipeline" — and Claude uses your real Chrome browser sessions via the [Claude-in-Chrome](https://github.com/anthropics/claude-in-chrome) browser extension. No Playwright, no dedicated browser profile, no automation detection issues.

### Set up the workspace

```bash
careeros init ~/my-job-search
```

This creates a scaffold at `~/my-job-search/`:

```
~/my-job-search/
  CLAUDE.md                    entry point: detection logic, skill routing
  .claude/skills/
    onboard/SKILL.md           first-run interview protocol
    browse/SKILL.md            browse → score → save protocol
    track/SKILL.md             pipeline status protocol
```

### Open it in Claude Code

```bash
cd ~/my-job-search
claude   # or open the folder as a project in Claude.ai
```

Claude reads `CLAUDE.md`, detects that `profile.md` is missing, and immediately runs the onboarding interview. It asks 8 questions one at a time:

1. Current role and years of experience
2. Primary tech stack
3. Target roles
4. Location and work arrangement preference
5. Minimum compensation
6. Target company types
7. Which job boards to use (LinkedIn, Instahyre, Wellfound, Naukri)
8. LinkedIn search URL (or Claude constructs a default)

At the end it writes `profile.md`, `boards.md`, `jobs/pipeline.md`, and `activity.md` — your workspace is ready.

### Browse for jobs

Say in chat:

> "browse linkedin"

Claude will:
1. Read your `profile.md` (above the fold — your scoring rubric)
2. Read `boards.md` to get the LinkedIn browse URL
3. Open a new Chrome tab and navigate to that URL using your existing LinkedIn session
4. Extract listings from the live page
5. Score each one against your rubric
6. Present a table: pick which ones to save

Saved jobs land in `jobs/discovered/` and appear in `jobs/pipeline.md`.

### Track your pipeline

> "show my pipeline"

Claude reads `jobs/pipeline.md` and shows a status table. To update a status:

> "mark Stripe as applied"

### Keeping skills up to date

After a CareerOS upgrade, refresh the skill files in your workspace without touching your profile or pipeline:

```bash
careeros init ~/my-job-search --refresh
```

---

## CLI setup {#cli-setup}

The CLI is how you handle everything after job discovery: applying, researching companies and people, sending outreach, following up, and LinkedIn connection requests. You need Playwright for the browser-filling features:

```bash
pip install playwright && playwright install chrome
```

### Run the onboarding wizard

```bash
careeros onboard
```

The wizard walks you through six steps:

1. **Workspace location** — where to create your workspace directory (default: `~/my-career`)
2. **Resume file** — enter the full path to your resume. Supported formats: **PDF**, Markdown (`.md`), and plain text (`.txt`).
   > Note: scanned/image-only PDFs are not supported — export a text-based PDF from Word, Google Docs, or LaTeX instead.
3. **Profile extraction** — CareerOS calls your LLM to extract name, title, years of experience, and a summary from the resume text. *(this may take up to a minute)*
4. **Confirmation** — the extracted profile is shown in full. Confirm with `Y` to continue.
5. **Preferences and goals** — target roles, remote preference, compensation floor, locations, and optional short/long-term goals.
6. **Job boards** — which boards to register: `linkedin`, `wellfound`, `indeed`, `greenhouse`, `lever`

### Check your workspace

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

### Set up job boards

After onboarding, configure each board you registered:

```bash
careeros board setup linkedin      # opens a browser window → sign in → done
careeros board setup wellfound     # same
careeros board setup greenhouse    # asks for the board URL → saves it
careeros board setup lever         # same
```

**Browser boards** (linkedin, wellfound, indeed) — CareerOS opens the board's login page in a dedicated browser profile, separate from your everyday Chrome. Sign in as normal; CareerOS detects the session and closes the window automatically.

**API boards** (greenhouse, lever) — you will be prompted for the board URL, e.g. `https://boards.greenhouse.io/stripe`. No browser needed; jobs are fetched directly from the ATS API.

```bash
careeros board list    # check which boards are ready
```

### Update your configuration

```bash
careeros workspace configure
```

Menu options:
1. Job preferences (roles, remote, salary, locations)
2. Career goals (short-term, long-term)
3. API job sources (Greenhouse / Lever slugs)
4. All of the above

---

## Browse and score jobs

**Recommended:** use the [Cowork workspace](#cowork-workspace-recommended) — say "browse linkedin" in chat. Claude uses your real browser session; works on all boards including React SPAs.

**CLI alternative** (linkedin, wellfound, indeed only):

```bash
careeros browse --board linkedin
```

CareerOS searches through the dedicated CareerOS browser profile, scores each result against your profile and goals, and shows them ranked:

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

---

## Research a job

Before applying or reaching out, research the company and its people:

```bash
careeros research company --job <id>
careeros research people --job <id>
careeros research compensation --job <id>
```

`research people` browser-scrapes a person list and classifies each one (IC, EM, hiring manager, recruiter). `research compensation` pulls salary data and gives an honest confidence rating.

Add a LinkedIn profile URL and email address to a person once you have them:

```bash
careeros people update <id> --linkedin-url https://www.linkedin.com/in/...
careeros people update <id> --email name@company.com
```

---

## Apply to a job

```bash
careeros apply --job <id>
```

CareerOS generates a cover letter tailored to the role and your profile, shows it to you for review, and fills the real application form only after you approve:

```
--- Cover letter draft ---
Dear Hiring Manager,
...

[a]ccept  [r]egenerate  [q]uit
> a

Submit application to Acme Corp? [y/n]: y
```

A browser window opens and fills the form. You watch it happen. CareerOS detects submission and logs the event. An `Approval` record is kept under `approvals/` so the whole lifecycle — proposed, approved, executed — has a durable trail.

To use a resume variant tailored to this specific job, see [Resume variants](#resume-variants) below.

---

## Set policies (optional)

Before running any automated commands, consider setting policies. CareerOS checks these before proposing any application — a blocked job never reaches the approval step:

```bash
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
- `blocked_locations` — substring match

A policy block is permanent: it cannot be overridden with a flag or env var. Edit the file to change it.

---

## Run unattended discovery and apply

```bash
careeros discover-and-apply
```

Discovers jobs across your configured boards, scores them, and auto-applies to anything above your threshold — capped per run and logged in full. Configure the automation policy in `config.json`:

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

Run from cron or launchd for fully automated discovery.

---

## Resume variants {#resume-variants}

Build a resume tailored to one specific job:

```bash
careeros resume variant --job <id>
```

Every body bullet in the variant is a verbatim span of your master resume, verified against it. The model selects and orders; it never writes new text. The result is saved as a PDF at `resumes/versions/<job_id>/resume.pdf`. `careeros apply` picks it up automatically.

To update your master resume and re-extract skills with evidence verification:

```bash
careeros resume ingest /path/to/updated-resume.pdf   # PDF, .md, or .txt
```

Skills without a verified quote from the resume text are dropped and named, not silently stored.

---

## Outreach

Draft and send outreach to a researched person:

```bash
careeros outreach send --job <id> --person <id>
```

The flow mirrors `apply`: draft, review loop, approval, send. The email is sent only after you approve.

Once outreach is sent, manage the follow-up cadence:

```bash
careeros outreach follow-up    # propose follow-ups for relationships due (run from cron)
careeros outreach review       # review and send the drafted follow-ups
careeros outreach close --job <id> --person <id> --reason "Accepted offer elsewhere"
```

`outreach follow-up` never sends on its own — it only drafts and leaves pending approvals. `outreach review` is the only path that sends.

---

## LinkedIn connection requests

```bash
careeros outreach connect --job <id> --person <id>
```

Drafts a 300-character LinkedIn connection note, shows you the exact bytes alongside the profile URL it will navigate, then sends the invitation only after approval. The browser runs **headful** on purpose: you watch it happen on your own account.

CareerOS sends at most **one connection request to a person, ever, across every job**. A second attempt for the same person is refused outright.

Requires the person to have a LinkedIn URL set:

```bash
careeros people update <id> --linkedin-url https://www.linkedin.com/in/...
```

---

## Workspace layout

**Cowork workspace** (`~/my-job-search/`):

```
CLAUDE.md                entry point — detection and skill routing
profile.md               career profile, target roles, scoring rubric (fold file)
boards.md                configured boards and browse URLs
jobs/
  pipeline.md            active pipeline (fold file)
  discovered/            one .md per saved job
activity.md              append-only action log
.claude/skills/          onboard, browse, track skill protocols
```

**CLI workspace** (`~/my-career/`):

```
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

Both workspaces are self-contained. Version-control them, back them up, or copy them between machines without touching the CareerOS install.

---

## Common questions

**Can I use a different CLI workspace directory?**
Yes. Run `careeros onboard --workspace /path/to/dir` or just provide a custom path when the wizard asks. CareerOS stores the active workspace path in `~/.config/careeros/config.json`. Every command also honors `--workspace <path>` and the `CAREEROS_WORKSPACE` environment variable, in that precedence order.

**Does CareerOS send my data anywhere?**
Your resume and job data are sent to your configured LLM provider for extraction, scoring, and drafting (cover letters, outreach messages, research summaries) — never to a CareerOS-run server, since there isn't one. `browse` (CLI), `apply`, `discover-and-apply`, and `research` drive Playwright against a dedicated CareerOS browser profile rather than calling a scraping API. `outreach send` and `outreach connect` are the only commands that reach a third party directly, and only after you approve them.

**Why does Cowork use my real Chrome instead of Playwright?**
The Cowork workflow uses the Claude-in-Chrome extension to interact with your existing logged-in browser sessions. This avoids automation detection (a real problem on LinkedIn), works on React SPAs like Instahyre that have no `<a href>` job links, and means you never need to maintain a separate browser profile or re-authenticate. The trade-off: you need Claude Code or Claude.ai open and the extension installed.

**What agents can read my workspace?**
Any agent that reads `manifest.json` (CLI workspace) or `CLAUDE.md` (Cowork workspace) and understands the layout. The CLI manifest schema is versioned, and CareerOS validates it on open. See `docs/agent-integration.md` for the operations layer contract that lets an external agent propose and execute actions cross-process.

---

## Testing the current build

**Automated tests (always run first):**

```bash
source .venv/bin/activate
pytest --ignore=tests/integration
```

All unit tests should pass in under 15 seconds.

**Key manual scenarios (automated tests cannot exercise these):**

| Scenario | Command | What to check |
|---|---|---|
| Scaffold Cowork workspace | `careeros init /tmp/test-ws` | CLAUDE.md and three skill files created. `careeros init /tmp/test-ws` a second time fails with "already exists". |
| Refresh skills | `careeros init /tmp/test-ws --refresh` | Skill files updated; no other files touched. |
| Onboard with PDF resume | `careeros onboard` | Enter path to a `.pdf` resume. Profile confirmation displays name, title, years, summary, and skills. Re-pick works. |
| Onboard with scanned PDF | `careeros onboard`, select an image-only PDF | Error: "PDF contained no extractable text — may be scanned/image-only." Clean exit. |
| LLM fallback | Set `CAREEROS_MODEL` to a rate-limited provider, set `CAREEROS_FALLBACK_MODELS` to a working model, run any LLM-powered command | On 429, request retries with the fallback model transparently. |
| Board setup (browser) | `careeros board setup linkedin` | Browser opens to LinkedIn login in the dedicated CareerOS profile. Session persists; `careeros board list` shows it as ready. |
| Board setup (API) | `careeros board setup greenhouse` | Prompts for board URL, parses slug, appends to sources.json. `careeros board list` shows it as ready. |
| Browse blocked without setup | `careeros browse --board linkedin` (no session) | Exits with "linkedin is not set up. Run: careeros board setup linkedin". Browser never opens. |
| Policy block | Set `blocked_companies: ["Acme Corp"]` in `config/policies.json`, then `careeros apply --job <acme-id>` | Command exits immediately with a policy-blocked message. |
| Duplicate invite prevention | `careeros outreach connect` twice for the same person | Second attempt raises `ConnectionAlreadySent` before any approval is opened. |
| Apply approval flow | `careeros apply --job <id>` | Cover letter shown, `[a]ccept` advances to browser fill, approval in `approvals/` transitions pending → approved → executed. |
| Follow-up queue | `careeros outreach follow-up` after sending outreach | Drafts pending approvals under `approvals/`; `careeros outreach review` is the only path that sends. |

**Integration tests (requires real credentials):**

```bash
pytest tests/integration/
```

The items from ROADMAP.md that require manual verification beyond the above:
1. **Phase 12 exit**: a real outreach send from an agent session (needs `CAREEROS_SMTP_*` + real recipient)
2. **Phase 13a exit**: a relationship carried through a real follow-up on cadence (needs LLM creds + time)
3. **Phase 13b exit**: `careeros outreach connect` against a live LinkedIn session — verifying the CSS selectors in `careeros/browser/connect/linkedin.py` against actual LinkedIn HTML
