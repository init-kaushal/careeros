# Implementation audit

**Date:** 2026-10-05
**Repository state:** `main` at `8712758` (v0.2.0)
**Purpose:** a factual baseline before the architecture work described in the Career OS brief. Everything below was counted or grepped from the repository and from the one real workspace (`~/Projects/job-search`) that exists; where something is inferred, it says so.

**Method and limits.** File sizes, test counts, tool-name frequencies and the data shapes in the real workspace were measured this session. The bodies of `browse`, `apply`, `prep`, `research`, `market`, `outreach`, `follow-up` and `targets` (spec only) were read in full this session. `onboard`, `interview`, `offer`, `track` and `humanize` were counted and grepped but not re-read line by line here, so findings about them rest on greps plus earlier reading. No skill was executed in this audit.

## 1. Current architecture

CareerOS today is a **scaffolder plus a library of prompts**. Nearly all behaviour lives in markdown.

| Layer | What it is | Size |
|---|---|---|
| Python | `careeros init` only: copies templates into a directory, `--refresh` re-copies skill files and the entry file (backing up the entry file as `.bak`) | 161 lines across 3 files (`cli/main.py` 16, `cli/init_cmd.py` 66, `workspace/scaffold.py` 79) |
| Claude templates | 12 skills under `.claude/skills/` + `CLAUDE.md` + a boards catalog | about 1,930 lines of markdown |
| GPT templates | 4 skills under `.gpt/skills/` + `AGENTS.md` + a boards catalog | about 585 lines |
| Tests | 31 tests: scaffold behaviour, CLI, and text assertions on skill files | `tests/test_scaffold.py` 161 lines, `tests/test_init_cmd.py` 53 |
| Docs | mkdocs-material site, strict build | 8 pages plus the new spec and plan |
| CI | one workflow that builds and deploys the docs | **runs no tests** |

Runtime dependencies: `typer`, `rich`. There is no network code in the Python package, no telemetry, no state beyond the files `init` copies.

The agent (Claude Code or ChatGPT) is the only executor. Skills are instructions the agent follows; nothing validates what the agent does.

## 2. Current data model

There is no schema. The workspace is free-form markdown with conventions.

| Concept | Where it lives | Structure | ID | Timestamps | Provenance |
|---|---|---|---|---|---|
| Profile | `profile.md` | 12 headed sections, one fold marker | none | none (dates appear inside prose) | none |
| Resume | `resume.md`, `resume-variants/*.pdf`, `## Resume variants` in `profile.md` | prose | none | none | none |
| Job | `jobs/discovered/[company]-[title]/job.md` | a bullet list of `- **Field:** value` | the directory slug | `Discovered:` as text | `Board:` and `URL:` only |
| Contacts | `jobs/discovered/[slug]/people.md` | markdown table | none | `searched` date in a heading | the search source in prose |
| Pipeline | `jobs/pipeline.md` | checkbox list, 5 states (`[ ]` `[~]` `[?]` `[✓]` `[x]`) | none | date inside each line | none |
| Activity | `activity.md` | free-text lines | none | a date at line start, in two formats | none |
| Markets | `markets/[country].md` | prose tables, dated, with sources | none | `Researched` / `Refresh after` | source URL and read date per figure |
| Evidence, stories, achievements, offers, interviews | partly `interview-prep.md`, `offer.md` per job | prose | none | none | none |

Measured in the real workspace: 33 jobs, each with 7 required bullet fields (URL, Status, Score, Location, Discovered, Board) and 3 optional ones (Market, Sponsorship, Route on 20 of 33); 27 `people.md`; 1 `prep.md`; 1 `company.md`; 1 tailored `resume.md`; `activity.md` 36 lines; **every one of the 33 jobs has `Status: discovered`**.

## 3. Current workflows

| Skill | Claude lines | GPT | Runs a browser | Has a hard stop before irreversible action |
|---|---|---|---|---|
| onboard | 285 | yes | no | n/a |
| browse | 164 | yes | yes | no irreversible action |
| research | 167 | no | yes | no |
| market | 170 | yes | yes (fallback) | no |
| prep | 159 | no | yes (Overleaf) | no |
| apply | 150 | no | yes | yes: never submits without a yes |
| outreach | 130 | no | yes | yes: never sends without a yes |
| follow-up | 128 | no | yes | yes, per message |
| interview | 152 | no | yes | no |
| offer | 188 | no | no | never signs or accepts |
| track | 37 | yes | no | n/a |
| humanize | 49 | no | no | n/a |

Per the roadmap, `browse`, `apply`, `prep` and `research` have run against real jobs. The roadmap says outreach, follow-up, track, humanize and the variant flow have not run end to end. In the real workspace no application or LinkedIn invite is logged, so `apply` and `outreach` have not completed a real submission or send there.

The roadmap is partly stale: it says nothing in the markets work has run against a real market, but the workspace holds seven market files dated 2026-10-01 and 20 jobs with a Market field.

## 4. Implemented versus asked for

Status key: **none** nothing exists; **prompt** exists only as instructions an agent may follow, with no code or test enforcing it; **partial** some enforcement; **done** enforced and tested.

| Capability in the brief | Status | Evidence |
|---|---|---|
| Canonical data model with stable IDs and timestamps | none | no IDs outside directory slugs |
| Structured Career Memory (`career/…` tree) | none | one `profile.md` and one `resume.md` |
| Evidence and provenance system | prompt | "never invent" appears in `prep`, `interview`, `market`, `outreach`; no claim-to-source mapping, no validator |
| Fabrication detection (numbers, employers, titles, technologies) | none | no code, no tests |
| Deterministic job evaluation | prompt | `browse` scores 0–10 by rubric in prose; no recommendation tiers, no confidence, no "what would change this" |
| Compensation intelligence with provenance | partial | `browse` comp check and `market` floors cite sources and confidence in prose; nothing structured |
| Application state machine | none | five checkbox states, no transition validation |
| Append-only activity ledger | prompt | the file says both "append-only" and "newest entries at top"; skills append in about 16 different free-text shapes |
| Explicit approval states | prompt | stop-before-submit/send rules on 11 matching lines across the skills, all prose |
| Outreach engine and contact ranking | partial | `research` finds contacts; no ranking, no per-persona strategy, no state tracking beyond log lines |
| Interview story bank and debrief | partial | `interview` builds STAR outlines per job; no reusable bank, no debrief step |
| Outcome analytics, resume experiments, learning loop, career graph | none | no code, no data to compute from |
| Runtime abstraction | partial | two hardcoded runtimes selected by a dict in `scaffold.py`; no capability model |
| `doctor`, `status`, `upgrade`, `migrate`, `validate`, `export` | none | only `init` exists; no framework, workspace or schema version |
| Local dashboard, "what should I do next" | none | |
| Prompt-injection defence | none | no template contains any instruction to treat listings, pages or emails as untrusted |
| Zero-friction onboarding | partial | conversational onboarding exists, 285 lines; install is clone plus venv plus pip; not published to PyPI or npm |

## 5. Technical debt

1. **Stale test package docstring.** `tests/integration/__init__.py` documents live-browser and live-apply tests, an SMTP guard and a `careeros browser login` command that were deleted in v0.2. The package holds no tests.
2. **CI runs no tests.** The only workflow builds docs. A regression in `scaffold.py` or in a skill's required text would not fail any check.
3. **Activity log contradicts itself.** `activity.md` is headed "append-only; newest entries at top" while the skills say "append". Entries use `[date] …` in templates and bare ISO dates in practice.
4. **No versions anywhere.** No framework version in `CLAUDE.md` or `AGENTS.md`, no workspace version file, no schema version. `--refresh` cannot tell whether a workspace is current.
5. **Duplicated content across runtimes.** The boards catalog is byte-identical in both runtimes. The GPT `onboard` and `market` skills restate the Claude ones with adaptations; they will drift.
6. **Hardcoded runtime paths.** `.claude/skills/...` and `.gpt/skills/...` appear throughout skill text and tests rather than in one place.
7. **No changelog.** Version `0.2.0` has no `CHANGELOG`; history lives in commit messages.
8. **Roadmap out of date** (see section 3).
9. **No lint or type configuration** in `pyproject.toml`; dev dependencies are `pytest` only.
10. **Venv fragility.** The repo's own `.venv` points at a Python that has been removed more than once (documented in the troubleshooting section).

## 6. UX problems

- Install requires cloning, creating a venv on a specific Python minor version and `pip install -e .`. There is no one-line entry point.
- The user must know which skill to trigger by phrase. `CLAUDE.md` carries the trigger table, which works for Claude Code but is invisible in a fresh ChatGPT project until the user pastes `AGENTS.md`.
- GPT users are asked to save code blocks by hand for every file the agent would write.
- The pipeline view has five states and every real job still shows `discovered`; there is no signal of what to do next.
- Onboarding does not collect age, nationality or degree, so each market run asks again (found earlier in this project).

## 7. Portability problems

- **Claude-in-Chrome tool names are baked into the skill text.** Counts across the 12 Claude skills: `navigate` 14, `tabs_create_mcp` 8, `read_page` 7, `javascript_tool` 4, `get_page_text` 4, `WebFetch` 3, `form_input` 3, `tabs_context_mcp` 2, `computer` 2, `read_network_requests` 1, `list_connected_browsers` 1. A different runtime must rewrite those skills.
- **GPT has 4 of 12 skills.** `apply`, `prep`, `research`, `outreach`, `follow-up`, `interview`, `offer` and `humanize` are Claude-only.
- **No adapters for Codex, Gemini, OpenCode or Cursor.** `AGENTS.md` is the only cross-tool entry point, and the GPT template targets ChatGPT Projects rather than a shell-capable agent.
- **A GPT Project cannot run a CLI.** Any design that makes the agent shell out to a validator or ledger writer works for Claude Code and Codex-style runtimes but not for ChatGPT Projects.

## 8. Security and privacy

**Holds today**
- No network code and no telemetry in the Python package.
- All data is local files the user owns; `--refresh` does not touch profile, boards, pipeline, jobs or activity.
- Hard stops are written for submit, send, passwords and native file pickers.

**Gaps**
- **No prompt-injection handling.** A grep for untrusted-content or injection language across all templates finds nothing. Job descriptions, company pages and LinkedIn profiles are read by skills that can also send invites and fill forms, with only the approval prose between them.
- **Approvals are advisory.** Every gate is an instruction, not a mechanism. Nothing records an approval or checks one before an action.
- **No workspace `.gitignore`.** None of the 20 template files is a `.gitignore`. A user who runs `git init` in a workspace can commit their resume, contacts and compensation figures.
- **The workspace holds personal data in plain text:** resume, recruiter names and profile URLs, compensation floors, visa status. The docs say it stays local; they do not yet say what the agent's model provider sees.
- **Fabrication controls are prose.** Rules against invented facts exist in `prep` and `interview` and nowhere is a claim checked against a source.

## 9. Missing tests

- Skill behaviour (browser flows, approvals) cannot be tested in CI; only the presence of required text is tested.
- No tests for: migration, versioning, state transitions, ledger format, evidence validation, fabrication, prompt injection, duplicate jobs, data isolation, end-to-end lifecycle, runtime capability detection.
- `--refresh` is tested for skill files and the entry file; it is not tested against a workspace containing real user data.
- No fixtures for postings, resumes, interviews, offers or hostile inputs.

## 10. Recommended migration path

This is too large for one specification. The brief's own seven phases are the right boundaries, with one prerequisite decision.

**Prerequisite decision: where enforcement lives.** The brief asks for deterministic validation, a state machine, approvals and a ledger. Today there is no code to host them. The simplest design that works for shell-capable runtimes is a small Python core that the agent invokes as commands (`careeros validate`, `careeros ledger append`, `careeros transition`), with markdown remaining canonical. That design does not work for ChatGPT Projects, which cannot run commands; for it the same rules would stay in instructions and be marked "guided". The capability matrix in the brief should record exactly that difference.

**Proposed sub-projects, each with its own spec, plan and tests**

1. **Foundation:** framework, workspace and schema versions; stable IDs; the ledger format and writer; `doctor`, `status`, `upgrade`, `migrate`, `validate`; migration of the real workspace with a backup. This also fixes debt items 3, 4 and 7.
2. **Evidence and Career Memory:** the `career/` tree, the evidence model and the fabrication validator, with adversarial fixtures.
3. **Evaluation, compensation, state machine and approvals.**
4. **Workflow completion:** outreach ranking and state, follow-ups, interview story bank and debrief, offers.
5. **Learning:** outcome analytics, resume experiments, derived graph, next-best-action.
6. **Runtime adapters and the capability matrix,** including the injection defences in skill text.
7. **Dashboard, onboarding polish, release engineering** (CI that runs tests, changelog, publishing).

**Already in flight and independent of the above:** the target-company outreach feature (spec and plan committed, not implemented). It would slot into sub-project 4.

## 11. What the test suite can and cannot prove

The brief's final acceptance asks for a full lifecycle run, including discovery, outreach and application. The parts that drive a real browser against LinkedIn, ATS sites or Overleaf cannot be verified by an automated suite and will not be claimed as verified by one. They need a supervised run in a real workspace, recorded in `docs/END_TO_END_TEST.md` with what ran and what did not. Runtime adapters for tools not installed here (Gemini, OpenCode, Cursor) can be specified and unit-tested for their file output, but their live behaviour cannot be confirmed from this repository.
