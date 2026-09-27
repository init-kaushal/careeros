# Getting Started

## Install

```bash
git clone https://github.com/init-kaushal/careeros
cd careeros
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Pick your runtime

CareerOS supports two agent runtimes. See [Cowork → Overview](cowork/index.md) for a full comparison.

### Claude Code (recommended)

Best for LinkedIn, Instahyre, and any authenticated board — uses your real Chrome session.

**Prerequisites:** Claude Code CLI + [Claude-in-Chrome](https://chromewebstore.google.com/detail/claude-in-chrome) extension.

```bash
careeros init ~/my-job-search
```

Open `~/my-job-search` in Claude Code. Claude detects the fresh workspace and runs the onboarding interview automatically.

Full guide: [Cowork → Claude Code](cowork/claude-code.md)

---

### GPT Work (ChatGPT Projects)

Best for Wellfound, Indeed, and public boards. LinkedIn and Instahyre require pasting page content.

**Prerequisites:** ChatGPT account with Projects (Plus or Team).

```bash
careeros init ~/my-job-search --runtime gpt
```

Paste `AGENTS.md` into your ChatGPT Project Instructions. Upload the skill files from `.gpt/skills/`.

Full guide: [Cowork → GPT Work](cowork/chatgpt.md)

---

## Keeping skills up to date

After upgrading CareerOS, refresh skill files without touching your data:

```bash
careeros init ~/my-job-search --refresh                    # Claude
careeros init ~/my-job-search --runtime gpt --refresh     # GPT
```

Your `profile.md`, `boards.md`, `jobs/`, and `activity.md` are never touched.
