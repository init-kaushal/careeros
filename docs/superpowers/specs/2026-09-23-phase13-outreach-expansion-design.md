# Phase 13 — Outreach Expansion — Design Spec

**Date:** 2026-09-23
**Status:** Approved for implementation planning
**Author:** Kaushal + Claude

---

## 1. What This Is

Phase 7 scoped outreach down to email-only sending and dropped LinkedIn connection-request
automation entirely. Phase 13 revisits both, plus the referral workflow that `mark-referral-requested`
currently leaves as a manual note.

This phase builds two flows on the propose/execute machinery Phase 12 established:

- **Follow-up cadence** — a scheduled `careeros outreach follow-up` that finds relationships past
  due, drafts a follow-up, and sends it through the approval gate. This is what makes the roadmap's
  exit condition true: carrying a referral relationship through follow-ups *without the user
  holding the state in their head*.
- **LinkedIn connection requests** — drafting and sending a connection note, automated, through the
  same gate.

That makes four flows on one mechanism (outreach send, job apply, follow-up, connection request).
Three is where an abstraction usually reveals whether it generalised or was shaped around its first
caller; four is the real test, and this phase is where the answer shows up.

**Deliberately out of scope** (declined, not deferred):

- **Automated email discovery. Declined outright.** The roadmap said to revisit it only if a
  genuinely reliable, non-guessing public source were identified. There isn't one for arbitrary
  individuals. Every practical option is either pattern-guessing (`first.last@company.com`) or a
  paid aggregator of scraped data. Guessing means sending real email to an address that may belong
  to a stranger who never applied for anything — the failure mode is worse than the inconvenience
  it saves. Email addresses stay manual, entered by the user via `careeros people update`, by
  design rather than by omission.
- **Reading replies.** Nothing in this phase reads an inbox or a LinkedIn message thread, so the
  system *cannot know* whether someone answered. This shapes the cadence design (§5.4): the stop
  condition must be a user action plus a hard cap, never an inferred "they replied".
- **Bulk or unattended-unapproved sending.** Every message and every connection request passes a
  per-item approval. There is no path that sends more than one thing per approval.
- **Anything designed to evade platform controls** — no fingerprint spoofing, no rate-limit
  circumvention, no detection avoidance. See §9.

---

## 2. The ToS Decision, and the Double Standard It Resolves

The roadmap flagged that Phase 13's own text gated LinkedIn *connection-request automation* on a
ToS investigation, while Phases 3 and 7 already ship LinkedIn job scraping and people-search
scraping with no such investigation. It said this phase's design session must settle that
explicitly rather than inherit an unexamined double standard.

**Settled: full automation, with the risk accepted by the workspace owner.** Kaushal chose this
deliberately, having been shown the alternatives — draft-only with a human paste, declining writes
entirely, or investigating first. The reasoning recorded on his behalf: it is his own account, the
tool acts only on requests he approves individually, and the scraping already shipped sits on the
same footing, so gating writes while shipping reads was the inconsistency rather than the caution.

**The double standard is resolved by removing it, not by re-drawing it.** Phases 3 and 7 need no
retroactive review: the line this project now holds is not reads-versus-writes, it is
*automating an action the user approved* versus *automating volume the user did not*. Both the
existing scraping and the new connection requests sit on the approved side. What stays off-limits
is in §9, and it is a narrower and more defensible line than "no writes".

This goes in `DIVERGENCES.md` as a recorded decision with its reasoning, so a future reader finds a
choice rather than a silence.

---

## 3. Staging

Two stages, in the manner of 9a/9b/9c, 11a/11b, and 12a/12b:

- **13a — the follow-up cadence.** New models and cadence state, `config/cadence_policy.json`, the
  `propose_follow_up`/`execute_follow_up` pair, the `careeros outreach follow-up` command, and
  `careeros outreach close`. No third-party write beyond an email this project already sends.
  Independently shippable, and it satisfies the roadmap's exit condition on its own.
- **13b — LinkedIn connection requests.** The `LinkedInConnector`, the
  `propose_connection_request`/`execute_connection_request` pair, and the `careeros outreach connect`
  command.

13a first for two reasons. It is the lower-risk half, so the cadence substrate is proven before the
first automated third-party write lands on top of it. And if the ToS position is ever revisited,
13a still stands.

---

## 4. Shared Groundwork

### 4.1 Extract the duplicated operations helpers

Phase 12's final review recorded as a Minor that `careeros/operations/apply.py` and
`outreach.py` each carry a character-identical text digest helper under different names
(`_digest_text`, `draft_digest`), and that `_now()` exists four times across the layer. With two
flows that was drift; with four it is a liability, because a fourth author will invent a fifth name.

13a adds `careeros/operations/_shared.py` with `now()`, `digest_text(text)`, and
`digest_stored(storage, path)`, and points all four flows at it. Behaviour-preserving: the digest
algorithm and the timestamp format are unchanged, and the existing digest tests pin that.

### 4.2 Constrain `OutreachMessage.referral_state`

It is currently a bare `str` defaulting to `"research"`, with `"referral_requested"` the only other
value ever written. Phase 12 established that a state field with a known vocabulary should be a
`Literal` so a corrupt or hand-edited record fails loudly on load rather than flowing through.

13a makes it `Literal["research", "referral_requested", "referral_confirmed", "closed"]`. The two
new values are the cadence's terminal states (§5.4). Existing workspaces hold only the first two, so
this is additive.

---

## 5. Stage 13a — Follow-Up Cadence

### 5.1 New cadence state on `OutreachMessage`

Three fields, all additive with defaults so existing records load unchanged:

```python
    last_touched_at: str | None = None   # when we last sent anything to this person
    touch_count: int = 0                 # messages sent, initial included
    closed_reason: str | None = None      # why the cadence stopped, when it has
```

`last_touched_at` is deliberately not derived from `sent_at`. `sent_at` records the *initial* send
and Phase 12 made it load-bearing for the "you already sent this" warning; overloading it to mean
"most recent touch" would break that. And it is deliberately not derived from the activity log
either: the log is an append-only audit artifact, and making a scheduling decision depend on
parsing it would couple cadence correctness to log format stability.

`execute_outreach_send` and `execute_follow_up` both set `last_touched_at` and increment
`touch_count`. That is the one change 13a makes to an existing flow, and it is what lets a
relationship that began before this phase still enter the cadence.

### 5.2 `config/cadence_policy.json`

Following `AutomationPolicy` exactly — validated bounds, and a **loud absence** rather than a
default, because a silently-defaulted cadence would start messaging people on a schedule the user
never chose:

```python
class CadencePolicy(BaseModel):
    days_between_touches: int = Field(ge=1, le=90)
    max_touches: int = Field(ge=1, le=10)
    max_follow_ups_per_run: int = Field(ge=1, le=20)

    def save(self, storage: StorageProvider) -> None: ...

    @classmethod
    def load(cls, storage: StorageProvider) -> "CadencePolicy":
        # Raises FileNotFoundError when absent, matching AutomationPolicy.
```

`max_touches` counts the initial message. `max_follow_ups_per_run` caps a single scheduled run the
way `max_auto_applies_per_run` caps auto-apply.

### 5.3 The operations pair

`careeros/operations/follow_up.py`, mirroring `outreach.py`'s shape:

```python
ACTION = "send_follow_up"

@dataclass(frozen=True)
class FollowUpProposal:
    approval_id: str
    message_id: str
    summary: str
    draft_text: str
    recipient_name: str
    recipient_email: str | None
    subject: str
    touch_number: int          # which touch this would be, for the reviewer's benefit
    days_since_last_touch: int

@dataclass(frozen=True)
class FollowUpResult:
    message_id: str
    recipient_name: str
    sent_at: str
    touch_count: int
```

`propose_follow_up(runtime, job_id, person_id, *, model=None, action_label)`: loads the
`OutreachMessage`, person, job, and company (→ `EntityNotFound`); refuses a relationship that is not
due or is already terminal (→ `NotDueForFollowUp`, `CadenceExhausted`, `RelationshipClosed`); runs
the policy check the way outreach does (→ `PolicyBlocked`); drafts via a new
`generate_follow_up_message` skill; persists the draft onto the same `OutreachMessage` record; then
`open_approval` with `payload = {message_id, job_id, person_id, draft_sha256, touch_number}`.

`execute_follow_up(runtime, approval_id, *, action_label)`: `require_state(APPROVED)`, action check,
payload reads, digest re-verification, recipient check, **then `mark_executed` before
`send_email`** — the ordering Phase 12's final review established as Critical — then sets
`last_touched_at`, increments `touch_count`, logs `follow_up_sent`.

`decline_follow_up` is the symmetric counterpart, and it does something the outreach decline does
not: declining a follow-up must **advance `last_touched_at`**, or the relationship stays past-due
and the next scheduled run re-proposes the same follow-up the user just declined. A declined
follow-up therefore counts as a touch for scheduling purposes but not for `touch_count`. That
asymmetry is the single subtlest thing in 13a and needs its own test.

It has a consequence worth stating: because a decline does not increment `touch_count`, a user who
declines every follow-up never exhausts `max_touches` and will see the relationship re-proposed
every `days_between_touches` indefinitely. That is defensible — each decline is a fresh choice to
defer rather than to stop — and `careeros outreach close` is the explicit off switch. But it means
the cap guards against the system's blindness, not against a user who keeps saying "not yet", and
the `--dry-run` output should make a repeatedly-declined relationship visible so it does not become
a silent recurring prompt.

### 5.4 Stop conditions, and what the system cannot know

The cadence stops on any of:

1. `touch_count >= policy.max_touches` — the hard cap.
2. `referral_state` in `{"referral_confirmed", "closed"}` — set by the user.
3. `closed_reason` set — set by `careeros outreach close`.

It does **not** stop on the person replying, because nothing here reads a reply. That is stated in
§1 as a non-goal and it is the reason condition 1 exists: without a cap, a tool that cannot detect
an answer will keep nagging someone who already answered. `max_touches` is the guard against the
system's own blindness, not a preference knob.

`careeros outreach close --job <id> --person <id> --reason <text>` is the user's way to end a
cadence, recording the reason. `mark-referral-requested` keeps working unchanged and now also means
"this relationship is live", which the cadence reads.

### 5.5 The command

**The scheduled command proposes; it does not send.** This is the one place the spec's first draft
contradicted itself, and the contradiction is worth recording because resolving it produced a better
design. That draft had the command auto-approve through `AutomationRuntime`, the way
`discover-and-apply` does — which would mean LLM-drafted email going to real people with no human
ever reading it, flatly against §1 and §9's promise of a per-item approval. `discover-and-apply`'s
justification for auto-approval ("the decision happened once, when the threshold was set") does not
transfer: a wrong application costs the user an application, while a wrong message costs someone
else's inbox and the user's standing with a person they want a referral from.

So the cadence splits across the propose/execute boundary Phase 12 built, which is precisely what
that boundary is for:

`careeros outreach follow-up` — cron-driven, `ClaudeCodeRuntime`-style queue-only approval
(`queue_only`), never prompts, never sends:

1. `CadencePolicy.load` (→ a clear error if absent, like `discover-and-apply`)
2. enumerate `outreach/` for relationships that are due: `last_touched_at` older than
   `days_between_touches`, `touch_count < max_touches`, not terminal
3. for each, up to `max_follow_ups_per_run`: `propose_follow_up`, leaving a **pending `Approval`**
4. per-relationship `except OperationError` logging its own event and continuing — one
   relationship's failure must not kill the run, the property Phase 12b established for
   `discover-and-apply`

`careeros outreach review` — interactive, `LocalRuntime`. Walks the pending follow-up approvals
`list_pending` reports, shows each draft, and takes accept / regenerate / decline / skip per item,
then `resolve_approval` and `execute_follow_up` on accept.

This is what makes the roadmap's exit condition true in the intended sense. The user does not hold
the state: the cron holds it, as a queue of drafted, digest-bound, still-unsent follow-ups. They
drain it when they choose, and an external agent session can drain it instead through the documented
contract — `list_pending` exists for exactly this and until now had no caller.

`careeros outreach follow-up --dry-run` lists what is due and proposes nothing — worth having
because the first thing anyone wants from a scheduled messaging tool is to see what it *would* do
before letting it draft.

One consequence to state rather than discover: because a pending approval is superseded by the next
propose for the same entity, a relationship left un-reviewed for several cadence periods accumulates
one pending follow-up, not a backlog of them. That is the desired behaviour and it falls out of
`open_approval`'s existing supersede rule rather than needing new logic.

---

## 6. Stage 13b — LinkedIn Connection Requests

### 6.1 Where the writer lives

Existing LinkedIn code is read-only: `careeros/browser/scrapers/` parse HTML from a `Page`. The
closest analogue to a page-driving *writer* is `careeros/browser/fillers/`, which take a `Page` and
fill and submit a form. A connection sender is a writer, not a scraper, but its signature differs
enough from a filler's that reusing the `Filler` protocol would distort it.

New package `careeros/browser/connect/` with `LinkedInConnector`:

```python
class LinkedInConnector:
    platform = "LinkedIn"

    def can_handle(self, linkedin_url: str) -> bool: ...
    def send_connection_request(self, page: Page, linkedin_url: str, note: str) -> bool: ...
```

`send_connection_request` returns `False` rather than raising when the request cannot be completed
for an ordinary reason — already connected, request already pending, no Connect button on the page.
Those are expected outcomes, not errors, and `False` maps to `ConnectionNotSent` the way a filler's
`False` maps to `FillIncomplete`.

### 6.2 The note is its own drafting skill

LinkedIn caps a connection note at roughly 300 characters. An email-shaped draft does not fit, and
truncating one produces something worse than text written to the limit. So
`careeros/skills/connection_note.py` with `generate_connection_note(person, job, company, profile,
goals, model=None) -> str`, prompted for the limit and **validated against it** — a note over the
cap is a `DraftFailed`, not a silent truncation, because silently truncating would send a sentence
that stops mid-word.

### 6.3 The operations pair

`careeros/operations/connect.py`, `ACTION = "send_connection_request"`.

`propose_connection_request(runtime, job_id, person_id, *, model=None, action_label)`: loads
entities; requires `person.linkedin_url` (→ `EntityNotFound` naming `careeros people update`);
policy check; **checks the LinkedIn board session before drafting** (→ `BoardSessionRequired`,
reusing Phase 12b's type and its lesson — a signed-out user should not pay for an LLM call first);
drafts and validates the note; persists a `ConnectionRequest` record; `open_approval` with
`payload = {person_id, job_id, linkedin_url, note_sha256}`.

**`linkedin_url` in the payload is load-bearing, more than `job_url` was for apply.** If the
`Person` record's URL changes between approval and execution, the request goes to *a different human
being*. Binding it means execute sends to the profile that was approved, and a changed URL is an
`ArtifactChanged` refusal rather than a message to a stranger.

`execute_connection_request(runtime, approval_id, *, action_label)`: `require_state(APPROVED)`,
action check, payload reads, note digest re-verification, **`mark_executed` before
`launch_browser`** — same ordering as apply, and for a sharper reason: a duplicate connection
request is visible to the recipient. LinkedIn would likely reject the second one as already-pending,
but correctness cannot rest on the platform's idempotence.

`ConnectionNotSent` leaves the approval `failed` and the record untouched. A locked browser profile
surfaces as `BrowserUnavailable(profile_busy=True)`, reusing the flag Phase 12b introduced.

### 6.4 The command

`careeros outreach connect --job <id> --person <id>` — interactive, `LocalRuntime`, headful so the
user can see what the browser does on their account the first time. Review loop for the note,
approval gate, execute. The quit path resolves the approval as declined, per Phase 12's finding.

### 6.5 Self-imposed restraint

`CadencePolicy` gains `max_connection_requests_per_run` in 13b — **with a default**, unlike its
other three fields. Those are deliberately required so an absent policy fails loudly rather than
starting a cadence the user never chose, but adding a *required* field in 13b would make every
`cadence_policy.json` written during 13a fail to load. A default keeps 13b additive; the loud-absence
property still holds for the file as a whole.

LinkedIn enforces its own weekly limits; this cap is not an attempt to work around them. It is the opposite — a cap the tool imposes on
itself so an enthusiastic session cannot burn through the user's allowance in one run. Declining to
build evasion (§9) and declining to build volume are the same decision.

---

## 7. New Error Types

In `careeros/operations/errors.py`, following its convention of building the message in `__init__`:

- `NotDueForFollowUp(message_id, days_since, days_required)` — 13a
- `CadenceExhausted(message_id, touch_count, max_touches)` — 13a
- `RelationshipClosed(message_id, reason)` — 13a
- `ConnectionNotSent` — 13b

All four subclass `OperationError`. The first three are refusals rather than failures: they mean
"correctly declined to act", so they leave the approval untouched and a caller that catches
`OperationError` handles them without special-casing.

---

## 8. Activity Events

13a: `follow_up_drafted`, `follow_up_sent`, `follow_up_send_failed`, `follow_up_send_declined`,
`cadence_closed`.

No event for the not-due case. A daily cron would otherwise append one line per not-yet-due
relationship per day to an append-only log — the log would be dominated by records of nothing
having happened, which is how an audit trail becomes unreadable. Not-due relationships surface in
`--dry-run` output, which is ephemeral by design.
13b: `connection_note_drafted`, `connection_request_sent`, `connection_request_failed`,
`connection_request_declined`.

Every one is documented in `docs/agent-integration.md` when it lands, per the discipline Phase 12
established — an integrator should never meet an undocumented `event_type`.

---

## 9. What This Phase Will Not Build

Recorded here because the ToS decision in §2 makes the boundary the important thing, and a decision
to automate is only defensible alongside an explicit statement of what it does not license:

- **No evasion of platform controls.** No browser-fingerprint spoofing, no user-agent rotation to
  look like a different client, no proxy rotation, no timing randomisation whose purpose is to
  appear human, and no attempt to detect or circumvent rate limiting. The isolated profile exists
  for session hygiene (Phase 9c), not disguise.
- **No unapproved volume.** One approval authorises exactly one message or one connection request.
  Both scheduled commands cap per run, and the caps are the user's own restraint rather than a
  limit-avoidance mechanism.
- **No scraping expansion.** This phase adds a write to LinkedIn; it reads nothing new.
- **No sending to anyone the user did not enter.** Combined with declining email discovery (§1),
  every recipient is a person the user researched and recorded themselves.

---

## Global Constraints

Inherited from Phase 12, plus two specific to this phase:

- No credentials, tokens, or API keys in any workspace file, activity log, or test fixture.
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*`
  in `careeros/core/`, `careeros/workspace/`, `careeros/skills/`, `careeros/sources/`,
  `careeros/browser/`, `careeros/runtime/`, or `careeros/operations/`.
- Activity logs are append-only; `atomic_write` for writes.
- Strings built by `+` concatenation only — no `.format()`, no f-strings with user data.
- **No `rich`, `typer`, or `click` under `careeros/operations/`, and no printing, prompting, or
  process exit from it.** Note Phase 12b's lesson: the AST guard only parses that package, so
  anything the layer *calls* must also be print-free. `LinkedInConnector` must not print.
- **`execute_*` performs the external action and nothing else,** consuming the approval before
  acting, and every artifact it transmits is digest-bound. For 13b that explicitly includes the
  target `linkedin_url`.
- **An `Approval` is single-use.**
- **No test may send a real email, launch a real browser, or contact LinkedIn.** The autouse guards
  in `tests/conftest.py` cover the first two; 13b's tests patch `LinkedInConnector` and
  `launch_browser`.
- **The cadence must be incapable of sending anything without a per-item approval.**

---

## Exit Condition

**13a:** `careeros outreach follow-up` carries a relationship from its initial message through at
least one follow-up on the configured cadence, stopping at `max_touches` without user intervention
and at `careeros outreach close` with one. A relationship whose follow-up the user declines is not
re-proposed on the next run.

**13b:** `careeros outreach connect --job <id> --person <id>` drafts a note within LinkedIn's limit,
gates it on approval, and sends it through the isolated profile, with `approvals/<id>.json` recording
the bound `linkedin_url` and note digest.

**The manual half, as with Phase 12, is not dischargeable by a green suite** and requires
credentials this environment does not have: an LLM credential for drafting, and an authorized
LinkedIn session in the isolated profile for 13b. State it outstanding rather than implying
otherwise.
