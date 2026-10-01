# CareerOS Roadmap

## Current state

CareerOS is a workspace scaffolder for agent-native job search. `careeros init` creates a
markdown workspace that Claude Code or ChatGPT reads at session start; agents browse, score,
research, apply, and track jobs via chat rather than CLI commands.

Two runtimes are supported, at different levels of completeness:
- **Claude Code** — full skill set: browse, research, prep, apply, outreach, follow-up,
  interview, offer, track, onboard, humanize. Real Chrome via Claude-in-Chrome, direct file writes.
- **GPT Work** — ChatGPT Projects with web search and code-block outputs. Only browse,
  onboard, and track exist so far.

Recent additions: resume variants cloned from an Overleaf master (prep), per-job directories
shared by every skill, company/people research caching, ATS quirk memory (iframes, mid-form
login walls, session timeouts, file-picker hand-off), saved consent defaults, and a humanize
pass over drafted cover letters, outreach, and follow-ups.

## Next

**1. Live-test what's unproven.** Browse, apply, prep and research have run against real
jobs. Outreach, follow-up, track, the humanize pass, and the resume-variant flow end to end
have not. Run the full loop on a real job and fix whatever breaks. Real friction has
produced the best fixes so far.

**2. Skill versioning.** `AGENTS.md` / `CLAUDE.md` expose a version so `careeros init --refresh`
can show what changed. Today every skill change reaches existing workspaces only through a
blind refresh, and that gets worse as the skill set grows.

**3. `careeros upgrade`.** Detect stale skill files across multiple workspaces and offer a
batch refresh. Depends on versioning.

**4. GPT Work parity.** Port research, prep, apply, outreach, follow-up, interview, offer,
and humanize to `.gpt/skills/`, adapted to what ChatGPT Projects can actually do (no
browser control, so apply and outreach become guided drafting rather than form-filling).
Until then, the docs should say plainly which skills are Claude-only.

## Later

- **Cursor / Codex runtime** — `careeros init --runtime cursor`. `AGENTS.md` already works;
  needs a skill set tuned to Cursor's tool access.
- **Naukri skill for GPT Work** — currently web-search only; Naukri's job API is public.
- **Unattended runs** — nothing in CareerOS runs on a schedule today; every skill fires from
  a chat trigger. Scheduled browsing or inbox checks would come from the host runtime's own
  scheduler, not from CareerOS itself. Authenticated email/calendar access via an
  integration layer (rather than browser automation) is a possible follow-on.
- **Better ATS coverage** — add vendor quirks (Workday, Greenhouse, Lever) as they are met
  in real applications, not speculatively.

## Not planned

- A standalone background agent or desktop app. CareerOS stays stateless and
  runtime-agnostic: markdown skills plus plain-text data, no server, no account.
- Bypassing the hard stops: never entering passwords, never automating native file pickers,
  never submitting without an explicit yes.

## What was removed

The previous CareerOS (v0.1) included a full CLI for apply, outreach, research, and follow-up
cadence, backed by Playwright browser automation and LLM skills. That codebase was replaced by
Cowork in v0.2 — the Playwright approach was brittle against React SPAs and automation
detection, and the approval-gated CLI was harder to use than chatting with an agent that has
browser access. The old code is in git history (`c6a88ce` and earlier).
