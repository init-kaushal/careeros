# CareerOS

**Privacy-first, agent-portable career automation.**

Your resume, skills, and job data live in a directory you own — a plain folder of JSON files you can version-control, back up, or hand to any agent. No account, no cloud sync, no vendor lock-in.

---

## What it does

CareerOS covers the whole job-search loop:

| Step | Command |
|---|---|
| Create your workspace | `careeros onboard` |
| Update preferences / goals / sources | `careeros workspace configure` |
| Sign in to job boards | `careeros browser login --board linkedin` |
| Browse and score jobs | `careeros browse --board linkedin` |
| Research companies and people | `careeros research company --job <id>` |
| Apply with a tailored resume | `careeros apply --job <id>` |
| Send outreach | `careeros outreach send --job <id> --person <id>` |
| Follow-up cadence | `careeros outreach follow-up` |
| LinkedIn connection requests | `careeros outreach connect --job <id> --person <id>` |

Every irreversible action is gated by an approval: CareerOS drafts, you review, then it executes. The full trail is logged to an append-only activity file in your workspace.

---

## Privacy model

- **Your data never reaches a CareerOS server** — there isn't one. The workspace is a folder on your machine.
- **LLM calls go to whichever provider you configure.** Profile extraction, job scoring, cover letter drafting, and outreach drafting all send relevant data to that provider. Use `CAREEROS_MODEL=ollama/...` to keep every call local. Set `CAREEROS_FALLBACK_MODELS` to a comma-separated list of fallback models tried automatically on rate limits or provider errors.
- **Browser automation runs against a dedicated CareerOS profile** — not your personal Chrome. Sessions for job boards are isolated from your everyday browsing.

---

## Installation

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python -m venv .venv && source .venv/bin/activate
pip install -e .
pip install playwright && playwright install chrome
export ANTHROPIC_API_KEY=sk-ant-...   # or any LiteLLM-supported provider
careeros onboard
```

The onboard wizard scans `~/Desktop`, `~/Downloads`, and `~/Documents` for resume files (PDF, `.md`, `.txt`) and presents a numbered pick list. It calls your LLM to extract the profile, then shows you a confirmation table before saving anything.

Full walkthrough: [Getting Started](getting-started.md)

---

## For agent integrators

CareerOS exposes an `AgentRuntime` protocol so an external agent session can propose and execute approval-gated actions (apply, send outreach, follow-up, connect) cross-process against a real workspace. The approval state machine, error vocabulary, and a worked example are in the [Agent Integration guide](agent-integration.md).
