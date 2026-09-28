# CareerOS

Scaffold a markdown-native job-search workspace for Claude Code or ChatGPT Projects.

Your profile, jobs, and activity log live in a plain-text directory you own — version-control it, back it up, hand it to any agent. No account. No cloud sync.

---

## How it works

One command scaffolds a workspace. On the first session, the agent interviews you to build your profile, scoring rubric, and (optionally) your resume. After that, you chat: "browse linkedin", "research Stripe", "prep for Stripe", "apply to Stripe", "connect with [recruiter]", "check follow-ups", "I have an interview at Stripe", "I got an offer from Stripe", "show pipeline" — covering discovery through offer, not just search.

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

## Scope — what this is and isn't

CareerOS drives the whole job-search workflow — discovery, company/people research, resume tailoring, applying, outreach, follow-ups, interview prep, and offer negotiation — but it's a copilot you run, not a bot that job-hunts unattended:

- **It never submits anything without asking first.** Applications, LinkedIn messages, and emails are always shown to you for a yes before anything irreversible happens.
- **It doesn't generate your resume from nothing.** Give it a starting resume (at onboarding, or anytime by saying "add my resume") and it tailors copies per job — reordering and rewording what's already true, never inventing experience.
- **Browsing is scoped to boards in `boards.md`.** LinkedIn, Instahyre, Wellfound, and Naukri are set up by default; add any other board by giving its URL the first time you say "browse [board]".
- **It doesn't run on a schedule by itself.** Every skill fires from a chat trigger. If you want "browse linkedin" to run automatically (say, daily), wire that up with your agent runtime's own scheduling feature — CareerOS doesn't ship one.
- **Follow-up tracking is mostly log-based.** It checks your email for replies when an email tool is available in your session, but otherwise relies on what's recorded in `activity.md`.

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
