# CareerOS

Scaffold a markdown-native job-search workspace for Claude Code or ChatGPT Projects.

Your profile, jobs, and activity log live in a plain-text directory you own — version-control it, back it up, hand it to any agent. No account. No cloud sync.

---

## How it works

One command scaffolds a workspace. On the first session, the agent interviews you to build your profile and scoring rubric. After that, you chat: "browse linkedin", "show pipeline", "mark Stripe as applied."

```bash
careeros init ~/my-job-search                # Claude Code (recommended)
careeros init ~/my-job-search --runtime gpt  # ChatGPT Projects
```

Full setup: [Getting Started](getting-started.md) · [Cowork guides](cowork/index.md)

---

## Runtimes

| | Claude Code | GPT Work |
|---|---|---|
| **Command** | `careeros init ~/my-job-search` | `careeros init ~/my-job-search --runtime gpt` |
| **Browse method** | Real Chrome via Claude-in-Chrome | GPT web search + paste for authenticated boards |
| **File writes** | Direct | Code blocks for user to apply |
| **LinkedIn / Instahyre** | Full (real logged-in session) | Paste page content into chat |

---

## Command reference

| Command | What it does |
|---|---|
| `careeros init <path>` | Scaffold a Claude Cowork workspace |
| `careeros init <path> --runtime gpt` | Scaffold a GPT Work workspace |
| `careeros init <path> --refresh` | Update skill files without touching user data |

---

## Privacy

Your data never reaches a CareerOS server — there isn't one. The workspace is a folder on your machine. LLM calls go to whichever provider your agent runtime uses (Anthropic for Claude Code, OpenAI for GPT Work).

---

## Installation

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python3.11 -m venv .venv && source .venv/bin/activate   # requires Python 3.11+
pip install -e .
```

See [Getting Started → Troubleshooting](getting-started.md#troubleshooting) if `careeros` stops working after a Python upgrade.
