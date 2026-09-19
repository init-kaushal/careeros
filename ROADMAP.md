# CareerOS Roadmap

## Where we are

Eight phases shipped. See `README.md` for the full command reference; this is the one-line version:

1. **Workspace core** — onboarding, profile extraction, `StorageProvider` protocol, export/import
2. **Job pipeline** — job schema, LLM-assisted scoring against your profile
3. **Browser search** — `browse`, scraping LinkedIn/Indeed/Wellfound via your own session
4. **Auto-apply** — cover letter generation, platform-specific form fillers, approval-gated submit
5. **Agent interoperability** — `AgentRuntime` seam (`LocalRuntime`, `ClaudeCodeRuntime`) so approval logic is runtime-agnostic
6. **Automation** — `discover-and-apply` for unattended, scheduled runs with a score-threshold policy
7. **People + outreach** — company/people research, role-aware drafting, approval-gated email send
8. **Compensation research** — evidence-backed comp data with an honest confidence rating

The original design (`docs/superpowers/specs/2026-09-18-careeros-design.md`) sketched Phases 0-6 up front; what actually got built diverged from that numbering as real constraints surfaced (browser scraping replaced planned API connectors in Phase 3, People+Outreach moved from Phase 3 to Phase 7, compensation research moved from Phase 2 to Phase 8). That original doc also named several things under Phases 2, 4, and 5 that were never built when those phases shipped. This roadmap picks up exactly those gaps, plus the interoperability and outreach surface area intentionally deferred along the way.

Every phase below follows the same process the first eight did: brainstorming (questions, approach, design) → written spec → implementation plan → subagent-driven execution with task-level and whole-branch review. Nothing here starts implementation without going through that gate — this document scopes what each phase is, not how it gets built.

---

## Phase 9 — Job Source Connectors + Deduplication

**Why:** Every discovery path today (`browse`, `discover-and-apply`) goes through browser scraping, which is inherently fragile — LinkedIn/Indeed/Wellfound markup changes silently break `parse_listings`, and there's no dedup, so the same posting saved from two boards (or two runs) creates two separate `Job` records with no relationship between them.

**What it builds:**
- A `JobSource` connector Protocol, parallel to the existing `Scraper` Protocol but for API-based sources (not browser-driven)
- A first real connector: Greenhouse's public job board API (search-only, no auth required for public boards) — a second, non-scraping discovery path that's more reliable than the browser scraper for companies that use Greenhouse
- A deduplication engine: company + title + location + canonical-URL fingerprinting, so `browse`/`discover-and-apply` recognize a posting already saved (from any source) and update it instead of creating a duplicate

**Exit condition:** the same job posted on both LinkedIn and a Greenhouse-hosted board resolves to one `Job` record, not two; `careeros browse` and the Greenhouse connector both feed the same dedup path.

---

## Phase 10 — Deep Resume Intelligence + Resume Variants

**Why:** `onboard`'s profile extraction is a quick, one-shot pass — good enough to bootstrap a workspace, but every downstream skill (job scoring, cover letters, outreach drafts) is only as good as that first extraction. And `apply` fills every application with the same static resume file regardless of the job, when a tailored variant would score better with both ATS keyword matching and a human reader.

**What it builds:**
- A deep resume ingestion skill, distinct from `onboard`'s basic extraction — evidence-backed: each extracted skill carries a source (which resume, which line/section) and a date, not just a bare string
- Resume variant generation: given a job's description and the evidence-backed skill set, generate a job-tailored resume variant, stored under `resumes/versions/`, that `apply` can select instead of always using the same file
- A `careeros resume ingest` command (or extending `onboard`) to run deep ingestion against an existing or updated resume without re-running the whole onboarding wizard

**Exit condition:** `careeros apply --job <id>` picks a resume variant tailored to that job's description rather than always using the same file; `cat resumes/versions/<variant>.json` (or equivalent) shows which evidence backs which claim.

---

## Phase 11 — Policy Engine

**Why:** The master design spec always called for a deterministic `PolicyEngine` sitting in front of every action — "the LLM proposes, the PolicyEngine decides, prompt injection cannot override policy." It was deferred in Phase 5 and again in Phase 6, where automation shipped with exactly one lever (a numeric score threshold). There's no way today to say "never apply to companies on this list" or "never apply below this salary" as a hard, auditable rule — the score threshold is a soft proxy for all of that at once.

**What it builds:**
- A `PolicyEngine` with deterministic, non-LLM rule evaluation: hard requirements (blocked companies, minimum salary, visa sponsorship required, location constraints) checked before any `ActionProposal` reaches `request_approval`
- `config/policies.json` (already named in the original workspace layout, never populated) as the policy source of truth
- Wiring into both `apply` (interactive) and `discover-and-apply`/`AutomationRuntime` (unattended) — a policy block is not the same as a declined approval; it never reaches the approval step at all, and it's the one thing a user cannot override without changing the policy itself

**Exit condition:** a job that violates a configured policy (e.g., a blocked company) is never presented for approval and never auto-applied to, with an activity event recording the block and which rule fired.

---

## Phase 12 — Second Real AgentRuntime

**Why:** Phase 5 built the `AgentRuntime` seam and proved it with `LocalRuntime` plus a `ClaudeCodeRuntime` exercised only by an internal interop test — no external runtime has ever actually driven CareerOS through it. The interoperability claim is real in the sense that the interface exists and is exercised, but it's unproven against anything outside this codebase.

**What it builds:**
- A concrete second runtime usable from a real external context — most likely a `ClaudeCodeRuntime` actually wired up for use from an agent session (this one, or one like it) via a documented integration path, rather than only a test-harness stub
- A workspace discovery convenience for that runtime: given a workspace path, bootstrap a runtime instance in one call, matching how `open_local_runtime` already works for the CLI
- Documentation of the integration contract (what `approval_callback` needs to do, what `record_activity` guarantees) so a third runtime could be built without reading the source

**Exit condition:** an agent session outside the CLI (not a test) opens a real workspace, reads/writes it, and completes at least one approval-gated action (e.g., drafts and sends outreach) end to end.

---

## Phase 13 — Outreach Expansion

**Why:** Phase 7 deliberately scoped outreach down to email-only sending and dropped LinkedIn connection-request automation entirely — no LinkedIn write access, no automated email discovery. Those were the right calls for a first outreach phase, but "complete capabilities" means revisiting them now that the approval-gated send pattern is proven.

**What it builds:**
- LinkedIn connection-request drafting with the same review-and-approve pattern as email send, if a ToS-compliant automation path exists (this needs its own scoped investigation before design — LinkedIn's automation restrictions are stricter than a simple browser-fill, unlike job applications which are the user's own action on their own account)
- Full referral workflow automation beyond the current manual `mark-referral-requested`: tracking follow-up cadence, surfacing "it's been N days since you messaged this person" as a workspace query
- Revisiting automated email discovery only if a genuinely reliable, non-guessing public source is identified — otherwise this stays manual by design, not an oversight

**Exit condition:** `careeros outreach` can carry a referral relationship from first message through a tracked follow-up cadence without the user needing to remember state themselves; LinkedIn connection automation ships only if the ToS investigation clears it — otherwise this phase ships the referral-tracking half and documents why LinkedIn automation was declined.

---

## Sequencing

Phases 9-11 come first — they hardstrengthen the foundation (fewer duplicate jobs, better resume quality, real policy control) rather than adding new external-facing surface area. Phase 12 (second runtime) has no hard dependency on the others and could run in parallel with any of them. Phase 13 (LinkedIn outreach) comes last deliberately: it's the highest-risk phase (third-party ToS, irreversible external actions) and benefits most from every other phase's approval/policy infrastructure being in place first.

Each phase still starts with its own brainstorming session — this roadmap fixes what and why, not the how, which gets decided (and can change) when that phase's turn comes.
