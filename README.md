# CareerOS

Scaffold a markdown-native job-search workspace for Claude Code or ChatGPT Projects.

**[→ init-kaushal.github.io/careeros](https://init-kaushal.github.io/careeros/)** — setup guides and docs.

## What it does

One command scaffolds a workspace. On the first session the agent interviews you to build your profile and scoring rubric. After that, you chat your way through the entire job-search lifecycle:

| Phase | Example |
|-------|---------|
| **Discovery** | "browse linkedin" · "show pipeline" |
| **Research** | "research Stripe" · "find the hiring manager at Stripe" · "comp research for staff engineer" |
| **Application** | "prep my application for Stripe" · "apply to Stripe" |
| **Outreach** | "connect with Jane Doe at Stripe" · "draft outreach email to the recruiter" |
| **Follow-up** | "check follow-ups" · "who needs a follow-up?" |
| **Interviews** | "I have an interview at Stripe on Friday" · "prep for my HM round" |
| **Offers** | "I got an offer from Stripe" · "help me negotiate" · "compare my Stripe and Notion offers" |

Your data — profile, jobs, activity log — lives in a plain-text directory you own. No account. No cloud sync.

## Quickstart

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python3.11 -m venv .venv && source .venv/bin/activate   # requires Python 3.11+; check with `python3 --version`
pip install -e .

# Claude Code (recommended)
careeros init ~/my-job-search
# then open ~/my-job-search in Claude Code

# ChatGPT Projects
careeros init ~/my-job-search --runtime gpt
# then paste AGENTS.md into ChatGPT Project Instructions
```

## Runtimes

| | Claude Code | GPT Work |
|---|---|---|
| **Command** | `careeros init ~/my-job-search` | `careeros init ~/my-job-search --runtime gpt` |
| **Browse method** | Real Chrome via Claude-in-Chrome | GPT web search + paste for authenticated boards |
| **File writes** | Direct | Code blocks for user to apply |
| **LinkedIn / Instahyre** | Full (real logged-in session) | Paste page content into chat |

## Workspace layout

```
profile.md              — career profile + scoring rubric
boards.md               — boards and search URLs
jobs/
  pipeline.md           — active pipeline
  discovered/
    YYYY-MM-DD-co-title.md    — job listing
    [job-slug]/
      company.md        — company info + comp research
      people.md         — hiring manager, recruiter, peers
      prep.md           — tailoring brief (keywords, bullets)
      interview-prep.md — question bank + STAR stories
      offer.md          — offer details + negotiation notes
activity.md             — append-only action log
CLAUDE.md / AGENTS.md   — agent entry point (framework-owned)
.claude/skills/         — skill files (framework-owned)
```

Framework files are always refreshable — user data is never touched:

```bash
careeros init ~/my-job-search --refresh
```

## Workspace health, versions and audit

```bash
careeros doctor      # environment and workspace health
careeros status      # versions, jobs by state, recent activity
careeros validate    # structure, ids, pipeline and ledger checks, each with a fix
careeros migrate     # add versions and ids to an existing workspace (backs up first)
careeros upgrade     # refresh skill files (backs up first)
```

Every change made through these commands is recorded in an append-only, hash-chained `ledger.jsonl`; your markdown stays the source of truth. See `docs/foundation.md` for the job lifecycle, the guarantees and their limits.

## Requirements

- Python 3.11+
- For Claude Code: Claude Code CLI + Claude-in-Chrome browser extension
- For GPT Work: ChatGPT account with Projects access (Plus or Team)

## Development

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Troubleshooting

**`careeros: bad interpreter: ... No such file or directory`**

Your `.venv` is a symlink to a specific Python binary. If that Python gets removed or replaced later (e.g. `brew upgrade python` bumps the default `python@3.x`), the venv breaks. Recreate it:

```bash
rm -rf .venv
python3.11 -m venv .venv && source .venv/bin/activate   # any Python 3.11+ works
pip install -e .
```

## License

MIT — see [LICENSE](LICENSE).
