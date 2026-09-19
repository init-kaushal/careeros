# Phase 7 — People + Outreach — Design Spec

**Date:** 2026-09-19
**Status:** Approved for implementation planning
**Author:** Kaushal + Claude

---

## 1. What This Is

The master design spec (`docs/superpowers/specs/2026-09-18-careeros-design.md`, §7) originally scoped this as "Phase 3 — People + Outreach," but the team built browser-driven job search instead when that phase came up, and it was never revisited. This spec picks up that thread now that CareerOS has a working apply pipeline (Phase 4), an `AgentRuntime` approval seam (Phase 5), and unattended automation (Phase 6) to build on.

CareerOS gains the ability to research a job's company and the people at it (browser-driven, public sources only), draft a role-aware outreach message, and — after explicit approval through the same `AgentRuntime.request_approval` gate `apply_cmd` already uses — actually send it via email (SMTP). A lightweight referral-workflow state machine tracks progress from research through a referral request.

**Deliberately scoped down from the master spec's original ask:**
- **No LinkedIn connection-request automation.** The master spec's state machine was `research → connect → engage → referral_request`; `connect` implied automating a LinkedIn connection request, which this spec does not build (no LinkedIn write access, only read-only public-page scraping). The state machine here is `research → engage → referral_requested`.
- **No LinkedIn sending.** Only email (SMTP) sending is built. LinkedIn messaging requires either the LinkedIn API (paid, restricted access) or automating message-send through the browser session (a materially different risk profile than filling a job application form — this is unsolicited outreach to a third party, not the user's own application). Deferred.
- **No automated email discovery.** `Person.email` is populated only when a public source lists one directly, or the user adds it manually. CareerOS never guesses an email pattern (e.g., `firstname.lastname@company.com`).
- **No `CredentialProvider`/OS-keychain integration.** SMTP credentials come from environment variables only, matching the existing `CAREEROS_MODEL` pattern for LLM config — not the master spec's longer-term keychain plan.

---

## 2. Architecture

Three new models in `careeros/core/models.py` (`Company`, `Person`, `OutreachMessage`), two new browser scrapers under `careeros/browser/scrapers/` (`CompanyPageScraper`, `PeopleSearchScraper`) implementing the existing `Scraper` Protocol, three new skills (`company_research.py`, `people_research.py`, `outreach_draft.py`) following the browser-fetch-then-LLM-extract pattern already used by `job_score.py`, a new `careeros/mailer.py` for SMTP sending (stdlib only), and four new CLI commands: `careeros research company`, `careeros research people`, `careeros outreach`, `careeros outreach mark-referral-requested` (plus a small `careeros people update` for manually adding an email). Every new command routes through `AgentRuntime` exactly as `browse_cmd`/`apply_cmd` already do — `LocalRuntime` for interactive use, with the send step gated by `request_approval`.

---

## 3. Activity Logging

Every action produces an activity event via the existing `AgentRuntime.record_activity`/`new_event` mechanism — no new logging path, only new `event_type` values on the same append-only log. This phase widens the existing convention: prior commands (`apply_cmd`) only log the success path; outreach logs both outcomes of any decision point, since an audit trail for an action with a real external side effect (an email leaving the system) must be able to answer "was this attempted and denied," not just "did it succeed."

| Event type | When | Entity |
|---|---|---|
| `company_researched` | After `research company` completes | company |
| `people_researched` | Once per person found by `research people` | person |
| `outreach_drafted` | After a message is generated | outreach_message |
| `outreach_send_approved` | `request_approval` returns approved | outreach_message |
| `outreach_send_declined` | `request_approval` returns declined | outreach_message |
| `outreach_sent` | SMTP send succeeds | outreach_message (summary includes recipient email — necessary for audit; never the message body) |
| `outreach_send_failed` | SMTP error (auth, connection, missing config) | outreach_message |
| `referral_requested` | `mark-referral-requested` runs | outreach_message |

This logging-completeness principle — every meaningful action, both outcomes of any approval/decision point — applies to all future CareerOS work, not only this phase.

---

## 4. Models

Appended to `careeros/core/models.py`, following the same `save(storage)`/`load(storage)` pattern already used by `Job` (a strict `load` that raises `FileNotFoundError`, not `Profile`'s singleton `load_or_empty` — these three are ID-keyed records like `Job`, not singleton workspace state, so there's no sensible "empty" default to fall back to):

```python
class Company(BaseModel):
    id: str
    name: str
    url: str | None = None
    industry: str | None = None
    size: str | None = None
    notes: str | None = None
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("companies/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, company_id: str) -> "Company":
        path = "companies/" + company_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Company " + repr(company_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class Person(BaseModel):
    id: str
    company_id: str
    name: str
    role_category: str  # "ic" | "em" | "recruiter" | "hiring_manager"
    title: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    researched_at: str

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("people/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, person_id: str) -> "Person":
        path = "people/" + person_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("Person " + repr(person_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())


class OutreachMessage(BaseModel):
    id: str
    job_id: str
    person_id: str
    draft_text: str
    send_state: str = "drafted"        # drafted -> approved -> sent | declined | failed
    referral_state: str = "research"   # research -> engage -> referral_requested
    created_at: str
    sent_at: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("outreach/" + self.id + ".json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider, message_id: str) -> "OutreachMessage":
        path = "outreach/" + message_id + ".json"
        if not storage.exists(path):
            raise FileNotFoundError("OutreachMessage " + repr(message_id) + " not found")
        return cls.model_validate_json(storage.read(path).decode())
```

`role_category`, `send_state`, `referral_state` are plain strings, not enums — matching how `Job.stage` already works. IDs follow the existing `make_job_id`-style pattern (a new `make_person_id`/`make_company_id` helper in `careeros/core/job_id.py`, or a shared generic ID helper — decided at plan time).

---

## 5. Scrapers + Research Skills

New files under `careeros/browser/scrapers/`: `company_page.py` (`CompanyPageScraper`) and `people_search.py` (`PeopleSearchScraper`), both implementing the existing `Scraper` Protocol (`careeros/browser/scrapers/base.py`):

```python
class Scraper(Protocol):
    source_board: str
    def search(self, page: Page, query: str, limit: int) -> list[dict]: ...
    def parse_listings(self, html: str) -> list[dict]: ...
```

`CompanyPageScraper.search` navigates to the company's LinkedIn company page (or website, if no LinkedIn URL is known), returns raw listing dicts with page content. `PeopleSearchScraper.search` navigates to LinkedIn people search scoped to the company name, returns raw listing dicts per person found (name, title, profile URL — no email, per §1's scoping).

New skills `careeros/skills/company_research.py` (`extract_company_info(page_content: str) -> dict`) and `careeros/skills/people_research.py` (`classify_person_role(name: str, title: str) -> str`, returning one of `"ic"`/`"em"`/`"recruiter"`/`"hiring_manager"`) follow `job_score.py`'s exact pattern: an LLM call with a fixed instruction string plus concatenated content, JSON-parsed response, graceful fallback on parse failure.

---

## 6. Outreach Drafting + CLI Flow

`careeros/skills/outreach_draft.py`:

```python
def generate_outreach_message(
    person: Person, job: Job, company: Company, profile: Profile, goals: Goals, model: str | None = None,
) -> str:
```

Role-aware: the instruction string varies by `person.role_category` (an IC gets a peer-to-peer framing, an EM/hiring-manager gets a "here's why I'm a fit for your team" framing, a recruiter gets a direct "I'm interested in this role" framing) — three fixed instruction-string variants selected by a lookup, not a single generic prompt. Returns `""` on failure, matching `generate_cover_letter`'s contract.

CLI flow for `careeros outreach --job <id> --person <id>`:
1. Load `Job`, `Person`, `Company` (missing any → exit 1)
2. Generate draft via `generate_outreach_message(...)`; empty result → exit 1
3. Review loop: identical Accept/Regenerate/Quit pattern to `apply_cmd`'s cover-letter loop, same `MAX_REGENERATIONS`
4. Save `OutreachMessage(send_state="drafted")`, log `outreach_drafted`
5. `runtime.request_approval(ActionProposal(action="send_outreach", summary="Send outreach email to " + person.name + " (" + (person.email or "no email on file") + ") re: " + job.company + " — " + job.title + "?", ...))` — note the parentheses around `(person.email or "no email on file")`: without them, Python's `+`/`or` precedence means `"(" + person.email or "..."` evaluates the `or` before the trailing `+ ")"` ever applies, silently dropping the closing paren whenever `person.email` is truthy
6. Declined → `send_state="declined"`, log `outreach_send_declined`, exit 0
7. Approved but `person.email is None` → log `outreach_send_approved` (the approval itself happened), then `rprint` a clear error telling the user to run `careeros people update <id> --email <address>` and retry — does NOT log `outreach_send_failed` (nothing was attempted; this is a precondition failure, not a send failure) — exit 1
8. Approved with email present → log `outreach_send_approved`, call `careeros.mailer.send_email(person.email, subject, draft_text)`; success → `send_state="sent"`, `sent_at` set, log `outreach_sent`; `smtplib` exception → `send_state="failed"`, log `outreach_send_failed` with the exception type/message (never credentials) in the summary, exit 1

`careeros outreach mark-referral-requested --job <id> --person <id>` loads the `OutreachMessage` for that job/person pair, sets `referral_state="referral_requested"`, saves, logs `referral_requested`. Pure local state transition, no network action, no approval gate needed (it's not an external side effect).

`careeros people update <id> --email <address>` loads the `Person`, sets `email`, saves. No approval gate (adding a fact to a local record isn't an external action).

---

## 7. Mailer

```python
# careeros/mailer.py
from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText


def send_email(to_address: str, subject: str, body: str) -> None:
    host = os.environ["CAREEROS_SMTP_HOST"]
    port = int(os.environ["CAREEROS_SMTP_PORT"])
    user = os.environ["CAREEROS_SMTP_USER"]
    password = os.environ["CAREEROS_SMTP_PASSWORD"]

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to_address

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_address], message.as_string())
```

Stdlib only (`smtplib`, `email.mime.text`) — no new dependency. `send_email` is the one function in the codebase permitted to read `os.environ` directly for secrets, exempted from the "no credentials in workspace files" constraint because credentials never touch the workspace — they're read from the process environment at send time and never written anywhere. Missing env vars raise `KeyError`, caught by the CLI and logged as `outreach_send_failed` — never silently skipped or defaulted.

---

## 8. Testing

- Model round-trip tests for `Company`/`Person`/`OutreachMessage` (`tests/test_company_person_outreach_models.py`), matching existing `Job`/`Profile` test patterns
- `tests/test_company_research.py` / `tests/test_people_research.py` — LLM extraction/classification logic, mocked LLM response, same shape as `tests/test_job_score.py`
- `tests/test_outreach_draft.py` — role-aware prompt construction, one test per `role_category` variant, mocked LLM response, plus the empty-string-on-failure contract
- `tests/test_mailer.py` — `send_email` with a mocked `smtplib.SMTP` (success path asserts `starttls`/`login`/`sendmail` called with correct args), missing-env-var case (asserts `KeyError`), SMTP exception case
- `tests/test_research_cmd.py` — `research company`/`research people` CLI flow, mocked scraper + LLM, real filesystem storage (Phase 6 precedent), activity events asserted
- `tests/test_outreach_cmd.py` — full flow: draft → review → approve → send (mocked mailer) logs `outreach_sent`; declined path logs `outreach_send_declined` and does not call the mailer; missing-email-on-approval path logs `outreach_send_approved` but not `outreach_send_failed`, exits 1, mailer not called; SMTP failure path logs `outreach_send_failed`; `mark-referral-requested` sets state and logs `referral_requested`

---

## Global Constraints

- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string — SMTP credentials are the sole codebase exception, read from environment variables in `careeros/mailer.py` only, never persisted
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`, `careeros/browser/`, or `careeros/runtime/` (`careeros/mailer.py` and `careeros/cli/` are exempt for the specific, narrow case of reading SMTP env vars and calling `smtplib`)
- Activity logs are append-only; no event is ever edited or deleted
- Every meaningful action — both outcomes of any approval/decision point, not just the success path — produces an activity event; this is a standing principle for all future CareerOS work
- Activity summaries and `ActionProposal.summary` built via string concatenation only — no `.format()` or f-strings with user data
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id` with the runtime's own identity
- CareerOS never guesses an email address from a name/company pattern — `Person.email` is populated only from a public source that lists one, or manual entry
- `send_email` never logs the message body, only recipient address and subject-level metadata

---

## Exit Condition

`careeros research company --job <id>` and `careeros research people --job <id>` populate `companies/` and `people/` with real, browser-fetched, LLM-extracted data. `careeros outreach --job <id> --person <id>` drafts a role-aware message, blocks on `request_approval` exactly as `apply_cmd` does, and — once approved with a known email — actually sends via SMTP, with a complete activity trail covering every decision point (drafted, approved/declined, sent/failed). `careeros outreach mark-referral-requested` advances the referral state machine as a pure local action requiring no approval.
