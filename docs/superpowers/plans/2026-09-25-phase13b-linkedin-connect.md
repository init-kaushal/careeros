# Phase 13b — LinkedIn Connection Requests

Implements §6 of `docs/superpowers/specs/2026-09-23-phase13-outreach-expansion-design.md`.
13a (the follow-up cadence) shipped; this is the second and final stage of Phase 13.

**Baseline:** `main` at `83889bc`, suite `1047 passed, 7 skipped`. Every task must leave the suite
green and must not regress that count.

---

## Pre-flight: what I verified against the code before writing this

Recorded because six task briefs in 13a contained a claim about the codebase that turned out to be
false, and every implementer that stopped to report one was right to.

**Confirmed true — rely on these:**

- `Person.linkedin_url: str | None = None` already exists (`careeros/core/models.py:310`).
- `BoardSessionRequired(board)`, `BrowserUnavailable(detail, *, profile_busy=False)` and
  `FillIncomplete` all exist in `careeros/operations/errors.py`. `ConnectionNotSent` is the new
  one, and `FillIncomplete` is its shape precedent.
- `launch_browser(headless: bool = False)` — the default is already **headful**, which is what §6.4
  requires. No new parameter is needed; just do not pass `headless=True`.
- The session-check pattern is `check_board_sessions(["linkedin"])`, and it raises
  `BrowserProfileBusy`, which must be caught and re-raised as
  `BrowserUnavailable(str(exc), profile_busy=True)`. `propose_apply` (`careeros/operations/apply.py`
  around line 130) is the exact precedent — copy its shape.
- `CadencePolicy` has exactly three required fields today, so adding a fourth **with a default** is
  additive and cannot break a `cadence_policy.json` written during 13a.
- `Filler` (`careeros/browser/fillers/base.py`) is a `Protocol` whose `fill` takes
  `(page, job, profile, cover_letter_text, cover_letter_path, resume_path)`. The spec is right that
  a connector's signature differs enough that reusing it would distort things — do not subclass or
  implement `Filler`.

**Must be created — none of these exist:** `ConnectionRequest` model, `ConnectionNotSent`,
`careeros/browser/connect/`, `careeros/skills/connection_note.py`,
`careeros/operations/connect.py`, the `careeros outreach connect` command.

### Four gaps in the spec, and how this plan resolves them

- **P1 — `careeros people update` cannot set `linkedin_url`.** The spec has
  `propose_connection_request` raise an `EntityNotFound` that names `careeros people update` as the
  remedy, but that command takes only `--email` (`careeros/cli/outreach_cmd.py:979`). Worse,
  `linkedin_url` is populated in exactly one place — `careeros research people`, via the
  people-search scraper — so a person the user added by hand has **no path at all** to a
  `linkedin_url` and 13b would be permanently unusable for them. **Ruling: add
  `--linkedin-url` to `people update` in Task 1.** Without it the error message is a dead end.

- **P2 — `max_connection_requests_per_run` cannot bind as specified.** §6.5 justifies the cap as
  stopping "an enthusiastic session [burning] through the user's allowance in one run", but §6.4
  specifies a single-person interactive command (`--job <id> --person <id>`). One invocation sends
  at most one request, so any cap above 1 never binds and the restraint is decorative. **Ruling:
  add the field exactly as the spec commits — additive, defaulted — and enforce it honestly
  per-invocation, but do NOT silently redefine it as a daily quota.** Record in `DIVERGENCES.md`
  that it is forward-looking: it will bind when a batch or scheduled connect command exists, and
  does not today. Inventing a rolling 24-hour window read off the activity log would be a
  unilateral redesign of a user-facing restraint, and inflating a decorative cap into a claim of
  protection is worse than recording the truth.

- **P3 — the spec omits duplicate-request protection, which is the actual harm.** A second
  connection request is **visible to the recipient**, and §6.3 itself says "correctness cannot rest
  on the platform's idempotence". Nothing in the spec stops proposing a second request to a person
  who already has one sent. **Ruling: `propose_connection_request` refuses when a
  `ConnectionRequest` for that person is already in a sent state.** This is the protection P2's cap
  was reaching for and the one that matters, since it guards a human's perception rather than a
  quota.

- **P4 — `ConnectionRequest`'s fields are undefined.** **Ruling: mirror `OutreachMessage`** — an id
  derived from `(job_id, person_id)` via the existing `make_message_id` slug helper, plus
  `job_id`, `person_id`, `linkedin_url`, `note_text`, `send_state`, `sent_at`. Stored under
  `connections/<id>.json`. Mirroring an existing record keeps `list`-and-slice enumeration, the
  `ValueError`-covers-`ValidationError` load guard, and the digest-binding pattern all identical.

### Carried forward from 13a — these are not optional

- **The note is displayed for review and then transmitted**, so it is exactly the case 13a's worst
  bug fell into. Render it with `verbatim`/`verbatim_panel_args` from `careeros/cli/_display.py`.
  A raw string in a `Panel` has Rich delete bracketed spans from the display while the real bytes
  go out, and a closing-tag-shaped span raises `MarkupError`.
- **Patch at the module that binds the name.** `careeros/operations/connect.py` will do
  `from careeros.browser.connect import LinkedInConnector` (or similar) at import, so tests must
  patch `careeros.operations.connect.<name>`, never the origin module. A vacuous
  `assert_not_called` against the wrong target shipped on this branch once already.
- **Do not patch `Prompt.ask` with a `Mock` when testing prompt behaviour** — `default=` and
  `choices=` never execute under a Mock, which let four safety-relevant regressions ship green in
  13a. Use `CliRunner(input=...)`.
- **Mutation-test every new test, and assert the anchor is unique** before trusting a kill. Two
  13a mutations silently patched the wrong command because their anchor text appeared twice.
- `careeros/operations/` is purity-enforced, and the AST guard only parses *that* package — so
  **`LinkedInConnector` must not print**, because the operations layer calls it. This is called out
  explicitly in the spec's global constraints and was a real 12b defect.

---

## Part A — Groundwork

### Task 1: The record, the error, the cap, and the missing flag

**Files:** `careeros/core/models.py`, `careeros/operations/errors.py`,
`careeros/cli/outreach_cmd.py`; `tests/test_models.py`, `tests/test_cadence_policy.py`,
`tests/test_outreach_cmd.py`.

- [ ] **Step 1: Write the failing tests**

Cover: a `ConnectionRequest` round-trips through `save`/`load`; its id derivation matches
`make_message_id`'s slugging so an unsafe `person_id` cannot escape the `connections/` prefix;
`CadencePolicy` still loads from a 13a-era file with only three fields and reports the new field's
default; the new field rejects out-of-range values; `ConnectionNotSent` subclasses `OperationError`;
and `people update --linkedin-url` sets the field, leaves `email` untouched when not passed, and
vice versa.

That last pair matters — `update` currently requires `--email`, so making both optional must not
turn a no-flag invocation into a silent no-op that reports success.

- [ ] **Step 2: Implement**

`ConnectionRequest` per P4. `ConnectionNotSent(OperationError)` — the spec gives it no arguments,
and `FillIncomplete` is the precedent for a bare one. `max_connection_requests_per_run: int =
Field(default=..., ge=1, le=20)` — pick the default deliberately and say why in a comment; LinkedIn's
own weekly limits are the context, and this cap is self-imposed restraint, not evasion. Add
`--linkedin-url` to `people update`, making both flags optional but requiring at least one.

- [ ] **Step 3: Suite and commit**

---

## Part B — The writer and the drafter

### Task 2: `LinkedInConnector`

**Files:** new `careeros/browser/connect/__init__.py`, `careeros/browser/connect/linkedin.py`;
new `tests/test_linkedin_connector.py`.

- [ ] **Step 1: Write the failing tests**

`can_handle` is a pure URL check with no browser — true for LinkedIn profile URLs, false for
anything else, deterministic, and it must not touch the network. `send_connection_request` returns
`False` (not raises) for each ordinary outcome: already connected, request already pending, no
Connect button present. It returns `True` only on a completed send. Drive it with a mock `Page`.

**Every test in this file uses a mock `Page`. None may construct a real browser** — `tests/conftest.py`
has an autouse guard, but do not rely on it as the only barrier.

- [ ] **Step 2: Implement**

Per §6.1's signature exactly. `False` for ordinary non-completion, mapping to `ConnectionNotSent`
the way a filler's `False` maps to `FillIncomplete`. **No printing** — see the purity note above.
Note that a connection note has a character cap enforced in Task 3; the connector should not
silently truncate either.

- [ ] **Step 3: Suite and commit**

### Task 3: `generate_connection_note`

**Files:** new `careeros/skills/connection_note.py`; new `tests/test_connection_note.py`.

- [ ] **Step 1: Write the failing tests**

A note over the cap raises rather than truncating — silently truncating would transmit a sentence
that stops mid-word. An empty or whitespace-only model response is a failure, not a valid note.
The scraped/untrusted inputs (the job description, the person's title and company) go through
`wrap_untrusted` and land in the **user** message, while the instruction stays in the system
message — `careeros/skills/follow_up_draft.py` is the precedent; follow it.

Assert the prompt-injection boundary directly, the way `tests/test_follow_up_draft.py` does: it is
the one property of a drafting skill a reviewer cannot eyeball.

- [ ] **Step 2: Implement**

`generate_connection_note(person, job, company, profile, goals, model=None) -> str`. Define the cap
as a named module constant (300) rather than a literal, and validate against it. Raising is the
caller's `DraftFailed`; decide whether this module raises `DraftFailed` itself or returns `""` for
the operations layer to convert, match whichever `follow_up_draft.py` does, and say which in the
report.

- [ ] **Step 3: Suite and commit**

---

## Part C — The operations pair

### Task 4: `propose_connection_request`

**Files:** new `careeros/operations/connect.py`; new `tests/test_operations_connect.py`.

- [ ] **Step 1: Write the failing tests**

Cover, in this order of importance: a missing `person.linkedin_url` raises `EntityNotFound` naming
`careeros people update --linkedin-url` (the flag Task 1 adds — the message must name something that
works); **the LinkedIn session is checked BEFORE the note is drafted**, so a signed-out user never
pays for an LLM call — assert the drafter was never called, not merely that the error was raised;
a locked profile surfaces as `BrowserUnavailable(profile_busy=True)`; a policy-blocked job raises
`PolicyBlocked` and logs `policy_blocked` before raising; **a person who already has a sent
`ConnectionRequest` is refused** (P3); the persisted record and the approval payload's
`note_sha256` agree; and re-proposing supersedes the prior open approval.

- [ ] **Step 2: Implement**

`ACTION = "send_connection_request"`. Payload is exactly the four keys §6.3 names: `person_id`,
`job_id`, `linkedin_url`, `note_sha256`. Order of operations is load → require `linkedin_url` →
policy → **session** → draft → validate → persist record → `open_approval`. The session check
before drafting is 12b's lesson and the spec calls it out by name.

- [ ] **Step 3: Suite and commit**

### Task 5: `execute_connection_request` and `decline_connection_request`

**Files:** `careeros/operations/connect.py`; `tests/test_operations_connect.py`.

- [ ] **Step 1: Write the failing tests**

The ordering test is the important one and it must be empirical: **`mark_executed` is called before
`launch_browser`**, proven by neutering `mark_executed` and watching the test fail, not by reading
the source. 13a's equivalent test was verified this way and it caught a reversal.

Also cover: an approval from another flow raises `WrongApprovalAction`; a changed `note_text`
raises `ArtifactChanged`; **a changed `person.linkedin_url` raises `ArtifactChanged`** — this is the
one that stops a request reaching a different human being, and §6.3 calls it load-bearing;
`send_connection_request` returning `False` raises `ConnectionNotSent`, leaves the approval `failed`
and the record untouched; a decline records the decision and does not open a browser; and a
cross-action approval id is refused by `decline_connection_request` too, which 13a had to fix
retroactively in both its decline functions.

- [ ] **Step 2: Implement**

`require_state(APPROVED)` → action check → payload reads → note digest re-verify →
`linkedin_url` re-verify → `mark_executed` → `launch_browser` (headful) → send. `ConnectionNotSent`
leaves the approval `failed`. Follow `execute_follow_up`'s shape, including that the refusals which
precede `mark_executed` leave the approval `approved` and retryable.

**Check whether a closed or terminal relationship should block a connection request**, the way
13a's `_require_live_relationship` blocks a follow-up send. Decide it deliberately and say what you
chose — do not leave it unconsidered, because that exact gap was a real 13a defect: the check
existed on the propose path only and a stranded approval mailed a closed relationship.

- [ ] **Step 3: Suite and commit**

---

## Part D — The command

### Task 6: `careeros outreach connect`

**Files:** `careeros/cli/outreach_cmd.py`; new `tests/test_connect_cmd.py`.

- [ ] **Step 1: Write the failing tests**

Cover: the happy path sends exactly one request and marks the approval `executed`; **the note shown
on screen is byte-identical to the note transmitted, asserted with a bracket-containing note** (the
13a carry-forward above); a `[/b]`-shaped note does not crash the command; declining resolves the
approval `declined` and opens no browser; quitting declines rather than leaving the approval
pending (Phase 12's finding); regenerating re-proposes and the **new** note is what sends; and the
command never touches a pending approval belonging to another action.

Drive at least one test through the real prompt with `CliRunner(input=...)` so `default=` and
`choices=` actually execute.

- [ ] **Step 2: Implement**

Interactive, `LocalRuntime`, headful. Review loop modelled on `send`/`review`, reusing
`MAX_REGENERATIONS`. Use `verbatim_panel_args` for the note panel and its title. Distinct
`action_label` so the audit trail separates this entry point from the others.

- [ ] **Step 3: Suite and commit**

---

## Part E — The record

### Task 7: Documentation

**Files:** `docs/agent-integration.md`, `docs/superpowers/DIVERGENCES.md`, `README.md`,
`ROADMAP.md`.

- [ ] **Step 1: Extend the integration contract**

A new section for `send_connection_request`, mirroring §12's structure: the propose → resolve →
execute sequence with a **runnable** example, its four payload keys marked by whether
`execute_connection_request` actually reads them — **verify by grepping `payload_value` call sites,
not by inference; that claim was wrong in Phase 12b's first draft and §12's six-keys-four-read
split was only correct because it was grepped** — the refusal types and what each means for a retry,
and every new activity event. Add the four 13b events to the CLI-emitted list too.

State plainly that this flow performs an **irreversible action visible to another person**, which
none of the three existing flows do to the same degree: an email can be ignored, a connection
request appears in someone's notifications.

- [ ] **Step 2: `DIVERGENCES.md`**

Record P2 (the cap is forward-looking and does not bind a single-person command), P1 (13b had to add
`people update --linkedin-url`, because the spec's remedy named a flag that did not exist and
hand-added people had no path to the field at all), and any judgement call the tasks logged.

- [ ] **Step 3: README and ROADMAP**

README: the new command, and that a connection request is approval-gated and visible to the
recipient. ROADMAP: mark **13b shipped** and **Phase 13 complete**, and move the pending-work
section's 13b entry out. **Keep both outstanding manual verifications recorded** — Phase 12's and
13a's — and add 13b's own, which needs an authorized LinkedIn session in the isolated profile that
this environment does not have.

- [ ] **Step 4: Verify every new snippet runs, then commit**

Run each new code block against a scratch workspace with `LinkedInConnector` and `launch_browser`
patched. Extract the snippets from the rendered markdown rather than retyping them, so a snippet
that drifts from the document cannot pass — that is how §12's two were verified.

---

## Exit Condition

**Automated:** suite green, with `tests/test_operations_connect.py` proving the
`mark_executed`-before-`launch_browser` ordering and the `linkedin_url` re-verification,
`tests/test_connect_cmd.py` proving the displayed note is the transmitted note, and
`tests/test_linkedin_connector.py` proving no test constructs a real browser.

**Manual, and not dischargeable by a green suite:** `careeros outreach connect --job <id> --person
<id>` drafts a note within LinkedIn's limit, gates it on approval, and sends it through the isolated
profile, with `approvals/<id>.json` recording the bound `linkedin_url` and note digest. This needs
an authorized LinkedIn session in the CareerOS profile, which this environment does not have.

Phase 12's and Phase 13a's manual exit conditions remain outstanding and are **not** discharged by
this phase.
