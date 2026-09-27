# Cowork — Agent-native job search

Cowork turns Claude or ChatGPT into an interactive job search assistant that browses, scores, and tracks jobs — all inside a plain-text workspace you own.

---

## How it works

You run one command to scaffold a workspace directory. On the first session, the agent interviews you to build your profile and scoring rubric. After that, you chat: "browse linkedin", "show pipeline", "mark Stripe as applied."

No account. No cloud sync. The workspace is a folder on your machine — version-control it, back it up, or hand it to any agent.

---

## Choose your runtime

| | Claude Code | GPT Work |
|---|---|---|
| **Entry point** | `CLAUDE.md` | `AGENTS.md` |
| **Scaffold command** | `careeros init ~/my-job-search` | `careeros init ~/my-job-search --runtime gpt` |
| **Browse method** | Real Chrome via Claude-in-Chrome extension | GPT web search (public boards) or paste content |
| **File writes** | Claude writes files directly | GPT outputs code blocks; you apply them |
| **Works with** | Claude Code CLI, Claude.ai Projects | ChatGPT Projects, OpenAI Codex, Cursor |
| **LinkedIn / Instahyre** | Full support (real logged-in session) | Paste page content into chat |

**Recommendation:** use Claude Code if you have access — the real Chrome integration means LinkedIn and other authenticated boards work without any extra steps.

---

## Workspace layout

Both runtimes use the same file layout:

```
profile.md          — career profile + scoring rubric
boards.md           — configured boards and search queries
jobs/
  pipeline.md       — active pipeline
  discovered/       — one .md file per saved job
activity.md         — append-only action log
CLAUDE.md or AGENTS.md  — agent entry point (framework-owned)
.claude/skills/ or .gpt/skills/  — skill files (framework-owned)
```

User data (profile, boards, jobs, activity) is never touched by `--refresh`. Framework files (CLAUDE.md / AGENTS.md, skill files) are always refreshable.

---

## Step-by-step guides

- [Claude Code setup](claude-code.md)
- [GPT Work setup (ChatGPT Projects)](chatgpt.md)
