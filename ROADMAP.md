# CareerOS Roadmap

## Current state

CareerOS is a workspace scaffolder for agent-native job search. `careeros init` creates a
markdown workspace that Claude Code or ChatGPT reads at session start; agents browse, score,
and track jobs via chat rather than CLI commands.

Two runtimes are supported:
- **Claude Code** — real Chrome via Claude-in-Chrome, direct file writes
- **GPT Work** — ChatGPT Projects with web search and code-block outputs

## What's next

**Near term**
- `careeros init --runtime cursor` — Cursor / OpenAI Codex variant (AGENTS.md already works; needs a dedicated skill set tuned for Cursor's tool access)
- Skill versioning — `AGENTS.md` / `CLAUDE.md` expose their version so `careeros init --refresh` can diff what changed
- `careeros upgrade` — auto-detect stale skill files across multiple workspaces and offer a batch refresh

**Later**
- Naukri skill for GPT Work (currently web-search only; Naukri's job API is public)
- Outreach skill — draft and track connection messages inside the Cowork workspace, no separate CLI needed
- Apply skill — fill forms via Claude-in-Chrome from within the workspace chat, approval-gated

## What was removed

The previous CareerOS (v0.1) included a full CLI for apply, outreach, research, and follow-up cadence, backed by Playwright browser automation and LLM skills. That codebase was replaced by Cowork in v0.2 — the Playwright approach was brittle against React SPAs and automation detection, and the approval-gated CLI was harder to use than chatting with an agent that has browser access. The old code is in git history (`c6a88ce` and earlier).
