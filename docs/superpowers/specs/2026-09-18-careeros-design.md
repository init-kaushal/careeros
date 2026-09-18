# CareerOS — Design Spec

**Date:** 2026-09-18  
**Status:** Approved for implementation planning  
**Author:** Kaushal + Claude

---

## 1. What This Is

CareerOS is a privacy-first, agent-portable career automation platform. It helps a user build a structured career profile, discover and match jobs, research companies and people, prepare personalized outreach, and manage applications — all while keeping the user's data entirely under their own control.

CareerOS is not a SaaS. It is a framework (installable package) that operates on a user-owned workspace directory. The framework can be updated independently of the user's data.

---

## 2. Core Principles

1. **User owns the data.** Career data lives in a directory the user controls. CareerOS never requires a central backend.
2. **Framework and workspace are separate.** Code in the package; data in the workspace. A `git pull` on the framework never touches user data.
3. **Agent is replaceable.** The same workspace can be opened by Claude, ChatGPT Work, Hermes, or a local agent without migration.
4. **Storage is replaceable.** Phase 1 ships local filesystem only. The interface makes other backends possible without rewriting core logic.
5. **Human in control of side effects.** Research is autonomous. Sending messages, submitting applications, or any external action requires explicit user approval.
6. **Deterministic policy, not LLM policy.** The LLM proposes. The PolicyEngine decides. Prompt injection cannot override policy.
7. **Auditable by design.** Every meaningful state transition writes an append-only activity event. A user must be able to answer "why did CareerOS do that?" for any action.

---

## 3. Two-Directory Model

Inspired by Trellis:

```text
~/Projects/careeros/        ← framework repo (code, skills, schemas, prompts)
                              git pull here to get updates
                              pip install -e . makes `careeros` CLI available

~/my-career/                ← workspace (user-owned data, chosen at onboard)
                              never touched by a git pull
                              schema migrations handle format upgrades
                              readable without CareerOS (plain JSON/Markdown)
```

The `careeros` CLI discovers the workspace via, in order of precedence:
1. `--workspace <path>` flag
2. `CAREEROS_WORKSPACE` environment variable
3. `~/.config/careeros/config.json` (written once at onboard)

The workspace contains **only data**. No framework code, no prompts, no skills are ever copied into the workspace.

---

## 4. Workspace Contract

### 4.1 Manifest

Every workspace has a `manifest.json` at its root. This is the entry point for any agent runtime.

```json
{
  "careeros_version": "0.1.0",
  "schema_version": "1",
  "workspace_id": "<uuid-v4>",
  "created_at": "<ISO-8601>",
  "storage_type": "local",
  "migrations_applied": ["001_initial"]
}
```

Rules:
- `schema_version` is an integer string. CareerOS refuses to open a workspace with a newer schema than it supports.
- `workspace_id` is set once at creation and never changed.
- No secrets, no PII, no credentials in the manifest.

### 4.2 Directory Layout (Phase 1 scope)

```text
<workspace>/
├── manifest.json
├── profile/
│   ├── profile.json          ← identity, seniority, bio
│   ├── preferences.json      ← roles, geo, comp, remote, visa, notice period
│   ├── skills.json           ← evidence-backed, each skill has source + date
│   └── goals.json            ← short/long term, non-negotiables
├── resumes/
│   ├── master.md             ← source of truth resume (Markdown)
│   └── versions/             ← generated variants, one file per variant
├── config/
│   ├── sources.json          ← whitelisted job sources + allowed modes
│   ├── policies.json         ← hard and soft matching rules
│   └── storage.json          ← backend config (Phase 1: local path only)
├── activity/
│   └── YYYY-MM-DD.jsonl      ← append-only, one JSON object per line
└── .careeros/
    └── migrations/           ← applied migration receipts (one file per migration)
```

Later phases add: `jobs/`, `companies/`, `people/`, `compensation/`, `outreach/`, `applications/`, `approvals/`.

### 4.3 Activity Log Schema

Every event in `activity/YYYY-MM-DD.jsonl`:

```json
{
  "timestamp": "<ISO-8601>",
  "event_type": "<string>",
  "entity_type": "<string|null>",
  "entity_id": "<string|null>",
  "action": "<string>",
  "status": "success|failed|blocked",
  "summary": "<string>",
  "reason": "<string|null>",
  "agent_runtime": "<string>",
  "session_id": "<string>"
}
```

Invariants:
- Activity files are **append-only**. No event is ever edited or deleted.
- No credentials, tokens, or secrets in any field.
- No PII beyond what is needed for audit (e.g., job ID is fine; full resume text is not).

---

## 5. Core Abstractions

### 5.1 StorageProvider

All workspace I/O goes through this interface. Nothing in CareerOS core touches the filesystem directly.

```python
class StorageProvider(Protocol):
    def read(self, path: str) -> bytes: ...
    def write(self, path: str, data: bytes) -> None: ...
    def atomic_write(self, path: str, data: bytes) -> None: ...
    def exists(self, path: str) -> bool: ...
    def delete(self, path: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def append(self, path: str, data: bytes) -> None: ...
```

Phase 1 implementation: `LocalFilesystemStorage`.  
`atomic_write` uses write-to-temp-then-rename to be safe under concurrent access.  
Future implementations (Google Drive, OneDrive, S3) are interface-compatible; no core logic changes.

### 5.2 ActivityLogger

Wraps `StorageProvider.append`. Enforces the event schema. Write-only — never reads back.

```python
class ActivityLogger:
    def log(self, event: ActivityEvent) -> None: ...
```

### 5.3 PolicyEngine

Deterministic. No LLM involvement. The LLM never holds a reference to this.

```python
class PolicyEngine:
    def check_action(self, action: ActionProposal) -> PolicyResult: ...
    def check_job(self, job: Job, preferences: Preferences) -> MatchResult: ...
    def is_source_allowed(self, source: str, mode: str) -> bool: ...
```

If `PolicyResult.decision == BLOCKED`, the action never reaches the executor. User approval cannot override a policy block — only a policy change can.

### 5.4 AgentRuntime

The seam between CareerOS and any LLM framework. Phase 1 ships `LocalRuntime` (runs in-process for CLI). Claude/Hermes/ChatGPT adapters come in Phase 5.

```python
class AgentRuntime(Protocol):
    def read_workspace(self, path: str) -> str: ...
    def write_workspace(self, path: str, content: str) -> None: ...
    def request_approval(self, proposal: ActionProposal) -> ApprovalResult: ...
    def record_activity(self, event: ActivityEvent) -> None: ...
```

The LLM reasons through the AgentRuntime. It never accesses StorageProvider or PolicyEngine directly.

---

## 6. Security Boundaries

### Credentials

Never stored in:
- Any workspace file
- Activity logs
- Prompts or skill files
- Git

Use OS credential store (macOS Keychain, etc.) or environment variables. A `CredentialProvider` interface wraps this in Phase 3+ when connectors need real auth.

### External content

Job descriptions, LinkedIn profiles, web pages: treated as **untrusted data**, not instructions. They pass through a sanitization layer before entering any CareerOS reasoning. They cannot modify policies, permissions, or approval requirements.

### Policy bypass

The LLM cannot bypass the PolicyEngine. The action flow is always:

```text
LLM proposes → PolicyEngine checks → ApprovalEngine (if needed) → ActionExecutor → External
```

---

## 7. Phased Implementation Plan

### Phase 0 — Repository Inspection ✓ Done
Findings: greenfield project, Trellis two-directory model as the architecture pattern, Python + local filesystem only for Phase 1.

### Phase 1 — Workspace + Core
Deliverables:
- `careeros/` Python package with `pyproject.toml` and `typer` CLI
- `LocalFilesystemStorage` implementation
- Workspace init: create directory, write manifest, seed empty dirs
- Profile, preferences, skills, goals schemas (JSON)
- `ActivityLogger` writing append-only JSONL
- Schema migration runner
- `/onboard` wizard: workspace path → resume upload → basic profile extraction (name, title, YoE, top skills via LLM) → preferences → sources → approval policy
- `/workspace` commands: init, status, validate
- `/export` and `/import`: zip workspace, restore from zip

**Exit condition:** user runs `careeros onboard`, can `cat profile/profile.json` and see real extracted data, runs `careeros export` and gets a valid zip.

### Phase 2 — Career Intelligence
- Deep resume ingestion (PDF/Markdown → evidence-backed extraction via LLM; distinct from onboard's basic extraction)
- Evidence-backed skills with source + date tags
- Job schema, `JobSource` connector interface
- First connector: Greenhouse (search only)
- Deduplication engine (company + title + location + canonical URL fingerprint)
- Job matching: hard requirements deterministic, soft requirements LLM-assisted, explanations stored
- Compensation research skill + evidence storage (source, date, geo, role, seniority, currency, base/bonus/equity, confidence)

**Exit condition:** `careeros discover` returns matched jobs with stored match explanations; `cat jobs/shortlisted/<id>.json` shows full match reasoning.

### Phase 3 — People + Outreach
- Company research skill
- People research (public sources only; hiring managers, EMs, recruiters)
- Role-aware message drafting (IC vs EM vs recruiter strategies)
- Referral workflow state machine (research → connect → engage → referral request)
- Approval queue: every outreach action blocked until explicit approval
- Communication connector interface (LinkedIn, email); drafting only, no send without approval

**Exit condition:** `careeros outreach --job <id>` drafts messages to `outreach/`, records approval request, blocks on send until approved.

### Phase 4 — Applications
- Full application pipeline: discover → verify → match → research → prepare → select resume → generate answers → review → approve → submit
- Resume variant generation per job
- Application connectors (Greenhouse first)
- Application tracking in `jobs/applications/`

**Exit condition:** `careeros apply --job <id>` walks the pipeline, blocks at approval gate, stores all artifacts in workspace.

### Phase 5 — Agent Interoperability
- `AgentRuntime` adapter implementations: Claude/Cowork, ChatGPT Work, Hermes, Generic
- Workspace discovery protocol (agent reads `manifest.json`, bootstraps)
- Workspace handoff between runtimes with no data loss

**Exit condition:** same workspace opened in two different agent runtimes; both read/write correctly, activity log shows both sessions.

### Phase 6 — Automation
- Scheduled discovery, follow-up reminders, opportunity monitoring
- User-configurable automation policies
- All policies and approvals still enforced under automation

**Exit condition:** `careeros schedule --daily-discover` runs unattended and writes to workspace without manual intervention; approvals still required for any outreach or application.

---

## 8. CLI Command Reference (target, not all Phase 1)

```text
careeros onboard              # create workspace, import resume, set preferences
careeros workspace status     # show workspace health and last activity
careeros workspace validate   # check schema version, integrity
careeros export               # zip workspace to file
careeros import <file>        # restore workspace from zip

careeros discover             # Phase 2: find and match jobs
careeros jobs                 # Phase 2: list shortlisted jobs
careeros research --job <id>  # Phase 2+3: research job, company, people
careeros outreach --job <id>  # Phase 3: draft and queue outreach
careeros apply --job <id>     # Phase 4: run application pipeline
careeros approvals            # Phase 3+4: review pending approvals
careeros history              # show activity log
careeros settings             # manage config and policies
```

---

## 9. What This Is Not

- Not a centralized SaaS with a user database
- Not tightly coupled to Claude or any specific LLM
- Not a system that takes external actions without user approval
- Not a system where the LLM can override policy
- Not a system that stores credentials alongside career data
- Not a system that copies framework files into the user's workspace
