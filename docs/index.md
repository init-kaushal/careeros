# CareerOS

**Privacy-first, agent-portable career automation.**

Your resume, skills, and job data live in a directory you own — plain files you can version-control, back up, or hand to any agent. No account, no cloud sync, no vendor lock-in.

---

## Two ways to use CareerOS

### Cowork workspace (recommended for job discovery)

A markdown-native workspace for Claude Code / Claude.ai. Claude uses your real Chrome browser session via Claude-in-Chrome — no automation detection, works on any job board including React SPAs like Instahyre.

```bash
careeros init ~/my-job-search
# then open ~/my-job-search in Claude Code and say "browse linkedin"
```

One command scaffolds the workspace. Claude interviews you once to build your profile, then browses, scores, and saves jobs in chat.

Full setup: [Getting Started → Cowork workspace](getting-started.md#cowork-workspace-recommended)

### CLI (for apply, outreach, and full automation)

The CLI handles the full job-search lifecycle after you have jobs in your pipeline: applying, researching companies and people, sending outreach, follow-up cadence, and LinkedIn connection requests.

```bash
careeros onboard              # create your CLI workspace (resume → profile)
careeros apply --job <id>     # cover letter → approval → form fill
careeros outreach send --job <id> --person <id>
careeros outreach connect --job <id> --person <id>
```

Every irreversible action is approval-gated: CareerOS drafts, you review, then it executes. The full trail is logged to an append-only activity file.

Full walkthrough: [Getting Started → CLI setup](getting-started.md#cli-setup)

---

## Full command reference

| Step | Command |
|---|---|
| **Scaffold Cowork workspace** | `careeros init ~/my-job-search` |
| **Refresh Cowork skills** | `careeros init ~/my-job-search --refresh` |
| Create CLI workspace | `careeros onboard` |
| Configure a job board | `careeros board setup linkedin` |
| List boards and their status | `careeros board list` |
| Update preferences / goals | `careeros workspace configure` |
| Browse and score jobs (CLI) | `careeros browse --board linkedin` |
| Research companies and people | `careeros research company --job <id>` |
| Apply with a tailored resume | `careeros apply --job <id>` |
| Send outreach | `careeros outreach send --job <id> --person <id>` |
| Follow-up cadence | `careeros outreach follow-up` |
| LinkedIn connection requests | `careeros outreach connect --job <id> --person <id>` |

---

## Privacy model

- **Your data never reaches a CareerOS server** — there isn't one. Both the Cowork workspace and the CLI workspace are folders on your machine.
- **LLM calls go to whichever provider you configure.** Profile extraction, job scoring, cover letter drafting, and outreach drafting all send relevant data to that provider. Use `CAREEROS_MODEL=ollama/...` to keep every call local.
- **Cowork browsing uses your real Chrome** via the Claude-in-Chrome browser extension — your existing logged-in sessions, no separate browser profile needed.
- **CLI browser automation runs against a dedicated CareerOS profile** — not your personal Chrome. Used for form-filling during `apply` and `outreach connect`.

---

## Installation

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python -m venv .venv && source .venv/bin/activate
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # or any LiteLLM-supported provider
```

For the Cowork workflow, no additional dependencies are needed — just the Claude-in-Chrome browser extension in your Chrome.

For CLI apply/outreach (form-filling):
```bash
pip install playwright && playwright install chrome
```

Full walkthrough: [Getting Started](getting-started.md)

---

## For agent integrators

CareerOS exposes an `AgentRuntime` protocol so an external agent session can propose and execute approval-gated actions (apply, send outreach, follow-up, connect) cross-process against a real workspace. The approval state machine, error vocabulary, and a worked example are in the [Agent Integration guide](agent-integration.md).
