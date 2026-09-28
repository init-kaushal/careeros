# CareerOS

Scaffold a markdown-native job-search workspace for Claude Code or ChatGPT Projects.

**[→ init-kaushal.github.io/careeros](https://init-kaushal.github.io/careeros/)** — setup guides and docs.

## What it does

One command scaffolds a workspace directory with an agent entry point and skills. On the first session, the agent interviews you to build your profile and scoring rubric. After that, you chat: "browse linkedin", "show pipeline", "mark Stripe as applied."

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
profile.md          — career profile + scoring rubric
boards.md           — configured boards and search queries
jobs/
  pipeline.md       — active pipeline
  discovered/       — one .md per saved job
activity.md         — append-only action log
CLAUDE.md           — Claude Code entry point (framework-owned)
AGENTS.md           — GPT Work entry point (framework-owned)
```

User data is never touched by `--refresh`. Framework files are always refreshable:

```bash
careeros init ~/my-job-search --refresh
```

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
