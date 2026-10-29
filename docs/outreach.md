# Outreach and follow-up state

CareerOS keeps one reliable answer to "who have I contacted, what happened, who is due a follow-up, how many invites are left this week, and whom should I approach next". The agent writes **person records** and **target-company records** through `careeros outreach`; outcomes and decisions are a few reserved `outreach.*` ledger events; everything else is **derived** from those events, the approval events and a few validated record fields. Nothing about state is stored in a file, and nothing parses `activity.md` for it.

This is not a security boundary. It records and reports; it does not confine the agent. See [Limits](#limits).

```bash
careeros outreach target add --company "Example Corp" --slug example-corp --market Australia --fit 8 --sponsorship likely --evidence "sponsor register entry (placeholder):2026-10-19"
careeros outreach person add --url https://www.linkedin.com/in/alex-example --name "Alex Example" --company "Example Corp" --role recruiter --found-in target:example-corp
careeros outreach next      # whom to invite next, limited to the invites left under the weekly cap
careeros outreach cap       # the cap, what counts against it, and what remains
careeros outreach queue     # LinkedIn invites and applications due for a follow-up
careeros outreach warm      # people who accepted or replied at a company where you have a saved job
```

The `targets` skill builds and ranks the target list, the `outreach` skill drafts and sends through [approvals](approvals.md), `follow-up` reads `queue` and `warm`, and `browse` records the people it finds. Run `careeros upgrade` to receive them. Nothing in `careeros outreach` sends anything.

## Records

Both records are YAML frontmatter plus free notes that code ignores. The examples are invented.

### Person: `people/<per_id>.md`

```markdown
---
schema: 1
id: per_xxxxxxxxxx
url: "https://www.linkedin.com/in/alex-example"
name: "Alex Example"
headline: "Engineering Manager, Platform"
company: "Example Corp"
company_slug: example-corp
role: hiring_manager
market: "Australia"
found_in:
  - {kind: target, id: tgt_xxxxxxxxxx}
  - {kind: job, id: job_xxxxxxxxxx}
found_at: 2026-10-19
---
Notes (ignored by code).
```

- `role` is `recruiter`, `hiring_manager`, `peer` or `other`. `market` is optional and is the exact label of a `## Markets` row.
- `id` and `url` are immutable and identity-bearing. The `url` is the canonical profile URL, the same function [approvals](approvals.md) uses, so URL variants (case, trailing slash, query, country subdomain, `http`) are one person. The id is a random id mapped to the canonical URL, and it is reused when the same person is proposed for an invite or added here, in either order.
- `company_slug` is `slugify(company)`: lower case, ASCII letters and digits separated by single hyphens. It is the join key with targets and with saved jobs.
- The record carries **no state**. `found_in` names where the person was found: a target by `id`, a job by `id`.

### Target: `targets/<slug>.md`

```markdown
---
schema: 1
id: tgt_xxxxxxxxxx
slug: example-corp
company: "Example Corp"
market: "Australia"
fit: 8
sponsorship: likely
evidence:
  - {source: "sponsor register entry (placeholder)", read_on: 2026-10-19}
---
Notes.
```

- `fit` is a whole number from 0 to 10, the same rubric as `browse`. `sponsorship` is `offered`, `likely`, `unknown`, `not_offered` or `n/a` (the home market).
- Every `sponsorship` value except `n/a` needs at least one `evidence` entry with a non-empty `source` and a valid `read_on` date. Unverified is an acceptable value; a guess is not.
- `slug` is unique for ever: a dropped slug cannot be added again (`duplicate`).

## Events

The type prefix `outreach.` is reserved: `careeros ledger append` refuses it. Each event is a fact reported to CareerOS, not checked against LinkedIn.

| Event | Entity | Written by | Meaning |
|---|---|---|---|
| `outreach.person_added` | `per_` | `person add` | the record exists; carries the file, its hash and `url:<canonical>` |
| `outreach.target_added` | `tgt_` | `target add` | the record exists; the slug is in `action` |
| `outreach.target_approved` | `tgt_` | `target approve` | the user's decision, with a `source` |
| `outreach.target_dropped` | `tgt_` | `target drop` | the user's decision, with a `source` and a reason |
| `outreach.accepted` | `per_` | `record --accepted` | the person accepted the invite |
| `outreach.replied` | `per_` | `record --replied` | the person replied |
| `outreach.closed` | `per_` | `record --closed` | no further invite or message; carries a reason (`no_response`, `declined`, `not_a_fit`, `other`) |
| `outreach.rejected` | `per_` / `tgt_` | any refused command on an existing entity | audit only; needs a reason; deduplicated; never changes any state |

An outcome has a `source` of `user`, `observed` or `email` and an optional note. Each outcome kind is recorded at most once per person: a repeat is refused `wrong_state`, so the derived timestamps are unambiguous. An unknown `outreach.*` type is `OUT005`.

## What each result is derived from

| Result | Inputs |
|---|---|
| identity (the `per_` id for a URL), what was sent, invite and message items and their states, the cap count, `closed`, outcome flags | ledger events only (approval events and `outreach.*` events) |
| target status, `next`, `warm`, queue exclusions for pre-opening contacts | ledger events plus validated record fields: `found_in`, `role`, `company_slug` and `market` on people; `fit`, `sponsorship` and `slug` on targets; the saved jobs' company |

So a hand edit of `found_in` or `role` can change which target looks contacted or who qualifies for `next`, but it can never change identity, what was sent, the cap, or whether a person is closed. This is deliberate: relationships and descriptive facts are source data, not audited status. `careeros validate` checks their shape (`OUT001`, `OUT006`); `person link` rewrites only the `found_in` of the record file and appends no event.

## Derived state

**Person.** The label is the first match of: `closed`, `replied`, `connected` (accepted), `messaged`, `invited`, `discovered` (a record and no events). It comes from these flags, folded from the person's events and from the approval events of their invite and message items:

- `invited_at`: the earliest `approval.consumed` of a `linkedin_invite`;
- `messaged_at`: the latest consumed `linkedin_message`;
- `accepted_at`, `replied_at`, `closed_at`: the single outcome event of each kind;
- `invite_in_flight`: an invite item is `in_flight`.

`accepted` needs `invited_at`; `replied` needs `invited_at` or `messaged_at`; `closed` needs nothing; none is allowed after `closed`, and `closed` is final. A refused transition is `wrong_state`.

**Target.** The decisions are `candidate` (added), `approved` and `dropped`; the legal moves are candidate to approved, candidate to dropped and approved to dropped. Anything else is `wrong_state`. The displayed status is the first match of: `dropped`; `warm` (a linked person is `connected` or `replied`); `contacted` (a linked person has `invited_at`); `opening-seen` (a saved job's company slug equals the target's slug); `approved`; `candidate`. A person who accepted or replied and was then closed is `closed`, so the target shows `contacted`, not `warm`. A target is flagged **stale** (a flag, not a status) when more than 60 days have passed since its last activity: its added or approved event, the person-added, consumed, accepted and replied events of its linked people, and each evidence `read_on` date. A dropped target is never stale. A stale target stays eligible for `next` and is flagged everywhere; the `targets` skill refreshes its evidence before the `outreach` skill drafts to its contacts.

**Queue (`outreach queue`).** A LinkedIn invite is due when `invited_at` is at least 7 days ago, with no accepted, replied or closed outcome and no consumed message after the invite: at most one follow-up message per invite, and further follow-up needs a new trigger (accepted or replied). An application is due when the job's latest status event is `APPLIED`, at least 10 days old, with no later status change. Closed people and closed or terminal jobs are never queued. A person found only through a target, with no saved job at that company, is not queued; they appear in `warm` once such a job exists. Email and interview follow-ups are not covered: CareerOS sends and records no email, and the command's output says so.

**Next and warm.** `next` lists `discovered` people whose target is `approved` (or whose `found_in` job is saved and not terminal), at most one `recruiter` and one `hiring_manager` per target, excluding anyone with a live approval item (pending, approved and not expired, or in flight, for an invite or a message), a consumed invite, an invite in flight or a closed person, limited to the invites remaining under the cap. Targets rank by fit in the home market, and in other markets by sponsorship tier (`offered`, `likely`, `unknown`; `not_offered` is excluded) and then fit. Each result carries its reason. `warm` lists `connected` or `replied` people at a company with a saved, non-terminal job.

## Rules

A versioned rules module holds the numbers. Changing any of them is a rules version bump.

| Constant | Value | Meaning |
|---|---|---|
| `INVITE_FOLLOWUP_DAYS` | 7 | days after an invite before it is due for a follow-up |
| `APPLICATION_FOLLOWUP_DAYS` | 10 | days after `APPLIED` before an application is due for a follow-up |
| `CAP_WINDOW_DAYS` | 7 | the rolling window of the weekly invite cap |
| `DEFAULT_WEEKLY_CAP` | 15 | the cap when `profile.md` does not set one |
| `TARGET_STALE_DAYS` | 60 | days without activity before a target is flagged stale |
| `MAX_CONTACTS_PER_TARGET` | 2 | at most one `recruiter` and one `hiring_manager` per target in `next` |

## Weekly invite cap

The count is every invite item currently `in_flight`, whatever its age (an invite whose outcome is unknown may have been sent; only an explicit `resolve` removes it from the count), plus invite items `consumed` whose `approval.consumed` event is inside the rolling window (`now` is earlier than the event time plus 7 days). An item is in exactly one state at a time, so the same invite never counts twice, and the `approval.started` timestamp is never used. Released, voided, expired, declined and superseded items do not count. `remaining` is the cap minus the count, never below 0, and the output gives the date the oldest counted consumed invite leaves the window.

The cap is read from `profile.md`, above the fold, under a heading `## Outreach`, from exactly one line `Weekly invite cap: N` (a leading list marker and bold markup are allowed):

```markdown
## Outreach

- Weekly invite cap: 12
```

No heading, or the heading without the line, means the default. A present but invalid setting (not a whole number from 0 to 1000, more than one such line, or an unparseable value) is a configuration error: `careeros outreach cap`, `careeros outreach next` and `careeros approval begin` for an invite refuse with `cap_config_invalid`, and `careeros validate` reports `OUT007`. A typo in the value never silently changes enforcement (it is `cap_config_invalid`). A line that does not start with the exact label `Weekly invite cap`, or that is not under an exact `## Outreach` heading, is not a setting and the default applies; a misspelled label, a misspelled heading, a table row or a blockquote line is therefore not seen. `0` is valid and blocks all invites.

**Where it is enforced.** `careeros approval begin` for an invite checks the cap, and for an invite or a message checks that the person is not closed, in the same critical section, under the workspace lock, as it records `approval.started`: the profile setting, the events and the folded states used for the decision are all read inside the lock. Two processes cannot both take the last free slot. `careeros approval propose` also refuses a closed person. The only override is raising the cap in `profile.md`. `outreach cap` and `begin` use one shared count function. `approval begin` applies the [ledger clock floor](approvals.md#the-clock-floor); the read-only `outreach cap` and `outreach next` use the same count without it, so a clock behind the ledger can only over-count, never under-count.

## Commands

Every command takes `--workspace` and `--json` (one JSON object once the workspace is found).

| Command | Does |
|---|---|
| `careeros outreach person add --url URL --name N --company C --role R [--headline H] [--market M] [--found-in job:REF\|target:SLUG ...]` | write a person record and `outreach.person_added` |
| `careeros outreach person list` / `careeros outreach person show PER_ID` | list or show people with their derived label (read-only) |
| `careeros outreach person link PER_ID --found-in job:REF\|target:SLUG ...` | rewrite only the record's `found_in`; no event |
| `careeros outreach target add --company C --slug S --market M --fit N --sponsorship V [--evidence SOURCE:YYYY-MM-DD ...]` | write a target record and `outreach.target_added` |
| `careeros outreach target approve SLUG` / `careeros outreach target drop SLUG --reason R` | record the user's decision (`--source user\|observed\|email`, default `user`) |
| `careeros outreach target list` / `careeros outreach target show SLUG` | list or show targets with status, stale flag and people (read-only) |
| `careeros outreach record PER_ID --accepted\|--replied\|--closed [--reason R] [--source S] [--note T]` | record one outcome |
| `careeros outreach queue` | the follow-ups due (read-only) |
| `careeros outreach cap` | the cap, the count and what remains (read-only) |
| `careeros outreach next` | whom to invite next (read-only) |
| `careeros outreach warm` | warm contacts (read-only) |

State-changing commands take the workspace lock, verify the ledger's hash chain, read the clock once after the lock, write the file first and the event second in one transaction, and use the same clock floor as approvals. A refusal of an existing entity appends one deduplicated `outreach.rejected`; an unknown entity appends nothing.

**Exit codes:** 0 ok; 1 refused (the code is printed, and in `--json` is the `code` field), or a ledger or disk failure; 2 usage, or no workspace.

## Refusal codes

| Code | Meaning |
|---|---|
| `not_found` | no such person, target or job |
| `wrong_state` | the transition is not legal (an outcome out of order or repeated, a decision on a dropped target) |
| `duplicate` | the person or the slug already exists (including a dropped slug) |
| `invalid_input` | a malformed or missing argument or record field |
| `clock_skew` | the clock is earlier than the latest event of any type |
| `ledger_corrupt` | the ledger's hash chain does not verify |
| `identity_conflict` | the identity map is inconsistent (one URL under two ids, or one id under two URLs); `person add` and `approval propose` fail closed and never pick a winner |
| `cap_config_invalid` | `profile.md` holds an invalid `Weekly invite cap` setting |
| `person_closed` | the person was closed: `approval propose` and `approval begin` refuse an invite or message to them |
| `cap_reached` | `approval begin` for an invite: the weekly cap is already met |

The last four are also approval refusal codes; see [approvals](approvals.md#refusal-codes).

## Validation codes

`careeros validate` checks the records and events. It is read-only and creates, changes and removes nothing.

| Code | Severity | Meaning |
|---|---|---|
| `OUT001` | error | a person or target record is unreadable, has invalid frontmatter, an unknown key, a bad enum or shape, a non-canonical `url`, or a `sponsorship` other than `n/a` without evidence |
| `OUT002` | warning | an orphan record file (no `person_added` or `target_added` event) |
| `OUT003` | warning | a person with approval events but no record, or an event whose record file is missing for an entity that still has live state |
| `OUT004` | error | a record's `id` or `url` disagrees with its event, or one URL under two ids or one id under two URLs |
| `OUT005` | error | an illegal outcome sequence (such as `accepted` with no invite, or an outcome after `closed`), a second consumed invite for one person, or an unknown `outreach.*` event type |
| `OUT006` | warning | `found_in` names a job or target that does not exist |
| `OUT007` | error | `profile.md` holds an invalid weekly cap setting |
| `OUT100` | info | a target is stale |

A missing record file for an entity with events is a warning, because state needs only the events. A workspace without `people/`, `targets/` or `outreach.*` events is unchanged.

## Limits

- Out of scope: a GPT workspace counterpart (the GPT workspace is unchanged), sending or tracking email, interview follow-ups, automatic reply detection, learning from outcomes, a dashboard, and importing existing `activity.md` history (it stays a readable log that nothing parses).
- `found_in`, `role` and the other descriptive record fields are editable source data that influence target status and `next` without an audit event.
- A dropped target cannot be re-opened; revisiting a dropped company is out of scope.
- Outcomes and target decisions are reported facts, not verified against LinkedIn. Fit and sponsorship are agent-supplied evidence.
- The cap counts only invites recorded in CareerOS: an invite sent outside it is invisible.
- An invalid person record (`OUT001`) feeds nothing: that contact loses its pre-opening exclusion and is queued like any contact with approval events.
- A company slug match is a heuristic (`slugify`).
- Truncating the tail of `ledger.jsonl` cannot be detected by a hash chain.
- This is not a security boundary.
