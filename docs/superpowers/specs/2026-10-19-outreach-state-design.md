# Outreach and follow-up state (sub-project 4a) — design

**Date:** 2026-10-19
**Status:** drafted for review
**Builds on:** Foundation 0.3.0 (IDs, ledger, workspace lock, validation, state machine), Evaluation 0.5.0, Compensation 0.6.0, Extended approvals 0.7.0 (recorded invites and messages, canonical profile-URL person identity).
**Source:** `docs/IMPLEMENTATION_AUDIT.md` §10, sub-project 4 ("Workflow completion: outreach ranking and state, follow-ups, …"). This spec also **absorbs** `docs/superpowers/specs/2026-10-05-target-company-outreach-design.md` (committed, never implemented), replacing its prose-only data model with the records and derived state below. The rest of that spec's behaviour (ranking rules, note rules, guardrails) is kept.

## 1. Goal

Today the follow-up queue and the weekly invite cap are computed by an agent re-reading `activity.md` prose. Approvals made every invite and message a recorded ledger event with a stable person identity, but outcomes (accepted, replied, closed) and contact facts (who the person is, where they were found) are still prose.

This sub-project gives CareerOS one reliable answer to "who have I contacted, what happened, who is due a follow-up, how many invites are left this week, and whom should I approach next":

- agent-written, validated **person records** and **target-company records**;
- a small set of reserved **`outreach.*` ledger events** for outcomes and target decisions;
- **state derived deterministically** from those events plus the approval events and a small set of validated record fields (§6.0), never stored in a file;
- read-only **`careeros outreach` commands** for the queue, the cap, the next contacts and warm contacts, used by the `outreach`, `follow-up` and `browse` skills and by a new `targets` skill;
- the **weekly invite cap enforced where sending happens** (`approval begin`).

## 2. Decisions (approved in brainstorming, 2026-10-19)

1. **Scope:** one spec, two slices: the state layer (people, outcomes, queue, cap) and the target-company list with pre-opening outreach built on it.
2. **Contacts:** a person record per person (`people/<per_id>.md`); state is derived from the ledger and never stored in the record.
3. **Architecture:** records + event-derived state (the approvals pattern). No code-owned status fields, so no tamper or stale layer. Safety-relevant facts (person identity, what was sent, the cap, closed people) come only from the ledger; descriptive record fields (§6.0) are editable source data, validated but not audited.
4. **Outcome events** (`accepted`, `replied`, `closed`) may be recorded by the agent: they are facts reported by the user or observed, they only affect follow-up timing, and they are not outward actions.
5. **Cap:** enforced at `approval begin`, with one pure function shared with `careeros outreach cap`.
6. **Prose logs** (`activity.md`) stay as a readable log the skills append to, but nothing parses them for state.
7. **GPT workspace:** unchanged (stays "guided"); the old spec's GPT list-building half is dropped for now.

## 3. Alternatives considered

- **Code-owned status in each record** (like `evaluation.md`): rejected, it brings back the tamper/stale/ledger-mismatch machinery that cost the most in evaluation.
- **A read-only helper that parses `activity.md`:** rejected, it keeps fragile prose parsing and cannot know person identity or outcomes.
- **Events only, no person records:** rejected, contacts that were found but never contacted would have no state and ranking would stay agent parsing of prose.
- **Targets as skill text only (the old spec as written):** rejected, the cap and due dates would stay agent judgement.

## 4. Records

### 4.1 Person record: `people/<per_id>.md`

YAML frontmatter plus free notes below; written by the agent through `careeros outreach person add` (and editable by hand).

```yaml
---
schema: 1
id: per_xxxxxxxxxx
url: "https://www.linkedin.com/in/alex-example"   # canonical form, same function as approvals
name: "Alex Example"
headline: "Engineering Manager, Platform"
company: "Example Corp"
company_slug: example-corp
role: hiring_manager            # recruiter | hiring_manager | peer | other
market: "India (home)"          # optional: exact ## Markets row label
found_in:                       # where this person was found
  - {kind: target, id: tgt_xxxxxxxxxx}
  - {kind: job, id: job_xxxxxxxxxx}
found_at: 2026-10-19
---
Notes (ignored by code).
```

- `id` and `url` are **immutable and identity-bearing**. The other fields can be edited; `validate` checks their shape.
- The record carries **no state**. `found_in`, `role`, `company_slug` and `market` are **editable source data** (see §6.0): they are validated (`OUT001`, `OUT006`) but a hand edit does not append an event. `person link` edits only the record file (atomically) and is a convenience for that edit.
- IDs are allocated from the canonical profile URL exactly as approvals do (`item.canonical_profile_url`), and **reused** for the same URL. `outreach person add` writes the file, then appends `outreach.person_added` carrying the URL; the **identity map is the union of `approval.proposed` url elements and `outreach.person_added` events**, so a person added here and later proposed for an invite (or the reverse) gets one `per_` ID. Identity stays ledger-authoritative; files are a cross-check.
- `company_slug` is `slugify(company)`: lowercase, hyphenated, ASCII letters and digits. It is the join key with targets and with jobs (`slugify(job.company)`).

### 4.2 Target record: `targets/<slug>.md`

```yaml
---
schema: 1
id: tgt_xxxxxxxxxx
slug: example-corp
company: "Example Corp"
market: "Australia"                  # a markets/<country>.md exists for it
fit: 8                               # 0-10, same rubric as browse
sponsorship: likely                  # offered | likely | unknown | not_offered | n/a
evidence:                            # required for every sponsorship value except n/a
  - {source: "sponsor register entry (placeholder)", read_on: 2026-10-19}
---
Notes.
```

- `slug` is unique; `target add` refuses a duplicate.
- `sponsorship: n/a` is for the home market. Every other value needs at least one `evidence` entry with a non-empty `source` and a valid `read_on` date (unverified is an acceptable value; a guess is not).

## 5. Events (reserved prefix `outreach.`)

| Event | Entity | Written by | Meaning |
|---|---|---|---|
| `outreach.person_added` | `per_` | `person add` | record exists; carries `artifacts=[rel, "sha256:<hex>", "url:<canonical>"]` |
| `outreach.target_added` | `tgt_` | `target add` | record exists; `artifacts=[rel, "sha256:<hex>"]`, `action` carries the slug |
| `outreach.target_approved` / `outreach.target_dropped` | `tgt_` | `target approve|drop` | the user's decision, recorded with a `source` and `reason` for a drop |
| `outreach.accepted` / `outreach.replied` / `outreach.closed` | `per_` | `record` | outcome; `source` is `user`, `observed` or `email`, plus an optional note; `closed` carries a reason (`no_response`, `declined`, `not_a_fit`, `other`) |
| `outreach.rejected` | `per_` / `tgt_` | any refused command on an existing entity | audit only; needs a reason; deduped like `approval.rejected`; never changes any state |

All outcome and decision events are **facts reported to CareerOS**, not verified against LinkedIn. Each outcome kind is recorded **at most once per person** (`accepted`, `replied`, `closed`); a repeat is refused `wrong_state`, so the derived `*_at` timestamps are unambiguous. Unknown `outreach.*` types are errors (`OUT005`).

## 6. Derived state (pure fold; takes `now`; nothing stored)

### 6.0 What each result is derived from

| Result | Inputs |
|---|---|
| identity (`per_` id for a URL), what was sent, invite/message items and their states, the cap count, `closed`, outcome flags | **ledger events only** (approval events, `outreach.*` events) |
| target status, `next`, `warm`, queue exclusions for pre-opening contacts | ledger events **plus validated record fields**: `found_in`, `role`, `company_slug`, `market` on people and `fit`, `sponsorship`, `slug` on targets, and the saved jobs' company |

So a hand edit of `found_in` or `role` can change which target looks contacted or who qualifies for `next`, but it can never change identity, what was sent, the cap, or whether a person is closed. This is deliberate: relationships and descriptive facts are source data, not audited status. `validate` checks them (`OUT001`, `OUT006`).

### 6.1 Rules (a versioned rules module; changing any constant is a rules version bump)

`INVITE_FOLLOWUP_DAYS = 7`, `APPLICATION_FOLLOWUP_DAYS = 10`, `CAP_WINDOW_DAYS = 7`, `DEFAULT_WEEKLY_CAP = 15`, `TARGET_STALE_DAYS = 60`, `MAX_CONTACTS_PER_TARGET = 2` (at most one `recruiter` and one `hiring_manager`).

### 6.2 Person state

Flags are folded from the person's events (outreach events and the approval events of their `linkedin_invite` / `linkedin_message` items, matched through the ledger-recorded person ID):

- `invited_at`: `ts` of the **earliest** `approval.consumed` of a `linkedin_invite` (approvals allow at most one consumed invite per person; a second one in a hand-crafted ledger is `OUT005`, and `invited_at` stays the earliest);
- `messaged_at`: `ts` of the latest consumed `linkedin_message`;
- `accepted_at`, `replied_at`, `closed_at` from the single outcome event of each kind (§5);
- `invite_in_flight`: an invite item is `in_flight`.

Label (first match): `closed`, `replied`, `connected` (accepted), `messaged`, `invited`, `discovered` (record only).

Outcome transitions: `accepted` needs `invited_at` and no `accepted_at`/`closed_at`; `replied` needs `invited_at` or `messaged_at`, no `closed_at`; `closed` needs no `closed_at`. A refused transition is `wrong_state`. `closed` is terminal.

### 6.3 Follow-up queue (`outreach queue`)

- **LinkedIn invite:** due when `invited_at + 7 days <= now`, no `accepted_at`, `replied_at` or `closed_at`, and no consumed `linkedin_message` after `invited_at` (**at most one follow-up message per invite**; further follow-up needs a new trigger: accepted or replied).
- **Pre-opening contacts are not queued:** a person whose `found_in` contains only targets, with no saved job at that company, never appears. They surface in `warm` once a job at that company exists.
- **Application:** a job whose latest status event is `APPLIED` and older than 10 days, with no later status change, is due.
- Closed people and closed or terminal jobs are never queued.
- **Email and interview follow-ups are not covered** (prose today; CareerOS sends no email). The command's output says so.

### 6.4 Weekly cap (`outreach cap`)

`count` = (a) **every invite item currently `in_flight`, whatever its age** (an invite whose outcome is unknown may have been sent; only an explicit `resolve` removes it from the count) **plus** (b) invite items `consumed` whose `approval.consumed` event `ts` is inside the rolling window (`now < ts + 7 days`). An invite item is in exactly one state at a time, so the same invite never counts twice: when `in_flight` becomes `consumed` it moves from (a) to (b) using the `approval.consumed` timestamp (the `approval.started` timestamp is never used). `released`, `voided`, `expired`, `declined` and `superseded` items do not count.

`cap` comes from `profile.md`, above the fold, under a heading `## Outreach`, from exactly one line `Weekly invite cap: N` (a leading list marker and bold markup are allowed). **Absent** (no heading, or the heading without the line) means 15. **Present but invalid** (not a whole number from 0 to 1000, more than one such line, or an unparseable value) is a configuration error: `outreach cap`, `outreach next` and `approval begin` for an invite refuse with `cap_config_invalid` and say what is wrong, and `validate` reports `OUT007` (error). A typo never silently changes enforcement. `0` is valid and blocks all invites.

`remaining = max(0, cap - count)`. The output also gives the date the oldest counted *consumed* invite leaves the window (`in_flight` invites have none: they leave the count only when resolved). One pure function computes this for `outreach cap` and for `approval begin`. All computations use one clock read and the ledger clock floor.

### 6.5 Target status

Decisions (events): `candidate` (added), `approved`, `dropped`. Legal transitions: `candidate -> approved`, `candidate -> dropped`, `approved -> dropped`. Anything else is refused `wrong_state`: approving an approved or dropped target, dropping a dropped target, approving after a drop. **`dropped` is terminal in this version** (a dropped slug cannot be re-added: `duplicate`); revisiting a dropped company is out of scope and noted as a limit.

Displayed status, first match wins:
1. `dropped`;
2. `warm`: a person linked to this target (`found_in`) is `connected` or `replied`;
3. `contacted`: a linked person has `invited_at`;
4. `opening-seen`: a saved job has `slugify(company) == slug`;
5. `approved`;
6. `candidate`.

**Stale** (a flag, not a status): `now - last_activity > 60 days` (strict), where `last_activity` is the latest of: the target's `target_added` / `target_approved` event `ts`; the `ts` of `person_added`, `approval.consumed`, `outreach.accepted` and `outreach.replied` events of its linked people; and each evidence `read_on` date (as 00:00 UTC). `dropped` targets are never stale. A stale target is **flagged in every output and remains eligible** for `next`; the `targets` skill must refresh its evidence (update `read_on`) before drafting to its contacts.

### 6.6 Next contacts (`outreach next`) and warm contacts (`outreach warm`)

- `next`: `discovered` people whose target is `approved`, or whose `found_in` job is saved and not terminal, at most one `recruiter` and one `hiring_manager` per target, excluding anyone with a **live approval item** (an item of either invite or message kind that is `pending`, `approved` and not expired, or `in_flight`; expired, declined, superseded, voided and consumed items are not live), a consumed invite, `invite_in_flight` or `closed`; limited to `remaining` invites. Stale targets stay eligible and are flagged (§6.5). Targets rank: home market by `fit` descending; non-home by sponsorship tier (`offered`, `likely`, `unknown`; `not_offered` is excluded) then `fit` descending. Each result carries its reason.
- `warm`: people who are `connected` or `replied` at a company that has a saved, non-terminal job.

## 7. Commands (`careeros outreach`)

Exit codes: 0 ok; 1 refused; 2 usage / no workspace. `--json` prints one JSON object once the workspace is found.

- `person add --url URL --name N --company C --role R [--headline H] [--market M] [--found-in job:REF|target:SLUG ...]`, `person list|show`, `person link <per_id> --found-in …`.
- `target add --company C --slug S --market M --fit N --sponsorship V [--evidence SOURCE:DATE ...]`, `target approve|drop <slug> [--source S] [--reason R]`, `target list|show`.
- `record <per_id> --accepted | --replied | --closed [--reason R] [--source user|observed|email] [--note T]`.
- Read-only: `queue`, `cap`, `next`, `warm`.

State-changing commands take the workspace lock, verify the hash chain (`ledger_corrupt`), read the clock once after the lock, write the file first and the event second in a transaction, and refuse with stable codes (`not_found`, `wrong_state`, `duplicate`, `invalid_input`, `clock_skew`, `ledger_corrupt`, `identity_conflict`, `cap_config_invalid`). `identity_conflict` is raised by `person add` (and by `approval propose`) when the identity map built from events is inconsistent (one canonical URL under two `per_` ids, or one id under two URLs): the command fails closed until the ledger is restored; it never picks a winner. A refusal of an existing entity appends a deduped `outreach.rejected`; an unknown entity appends nothing. They use the same clock floor as approvals (refuse when `now` is earlier than the latest event of any type).

## 8. Integration with approvals (small, explicit)

- The person-ID lookup in `approval propose` also reads `outreach.person_added` events (one identity map).
- `approval propose` **and `approval begin`** refuse an invite or message to a person with an `outreach.closed` event (`person_closed`). `begin` rechecks it **under the workspace lock, immediately before recording `approval.started`**, for both invite and message items, so a person closed after proposal or approval can no longer be claimed. Both read event types directly and import nothing from the outreach package.
- `approval begin` for a `linkedin_invite` refuses `cap_reached` when the count (§6.4: all other `in_flight` invites plus consumed invites in the rolling window) already meets the cap, and `cap_config_invalid` when `profile.md` holds an invalid cap setting; the only override is raising the cap in `profile.md`.
- Approvals keep working without any outreach records; a person with approval events and no record is an `OUT003` warning.

## 9. Validation (`careeros validate`, read-only)

| Code | Severity | Meaning |
|---|---|---|
| `OUT001` | error | person or target record unreadable, invalid frontmatter, unknown key, bad enum or shape, non-canonical `url`, `sponsorship` other than `n/a` without evidence |
| `OUT002` | warning | orphan record file (no `person_added` / `target_added` event) |
| `OUT003` | warning | a person with approval events but no record; or an event whose record file is missing for a person/target that still has live state |
| `OUT004` | error | record `id`/`url` disagrees with its event, or one URL under two ids / one id under two URLs (events and files considered together with approval events) |
| `OUT005` | error | illegal outcome sequence (`accepted` with no invite, an outcome after `closed`, …) or an unknown `outreach.*` event type |
| `OUT006` | warning | `found_in` names a job or target that does not exist |
| `OUT007` | error | `profile.md` holds an invalid weekly cap setting (§6.4) |
| `OUT100` | info | a target is stale (60 days) |

A missing record file for an entity with events is `OUT003` (warning) because state needs only the events; a workspace without `people/`, `targets/` or `outreach.*` events is unchanged.

## 10. Skills

- **New `targets` skill:** builds and ranks candidates (the old spec's steps 0–3: the "Target companies" profile line, saved jobs scoring 7 or higher, sponsor registers, regional boards; at most 12 new candidates per market per run; fit and sponsorship graded from evidence only), creates `target add` records, shows one table per market, records your approve/drop decisions, finds contacts for approved targets with the existing `research` Mode B1b and stores them with `person add --found-in target:<slug>`. It never sends anything.
- **`outreach`:** a pre-opening mode uses `outreach next` and `outreach cap`; the note rules from the old spec are kept (300 characters at most, one clause on who the user is, one concrete real fact about the company or person, never claims an opening, never asks for a job or referral, never claims a relationship, local norms for non-home markets); sending goes through the approvals flow unchanged. When you say someone accepted or replied, it runs `outreach record … --source user`.
- **`follow-up`:** the LinkedIn and application parts of the queue come from `outreach queue`; warm contacts from `outreach warm`; email and interview follow-ups stay prose and the skill says so.
- **`browse`:** contact discovery first checks existing people for the company (`outreach person list`), adds newly found people with `person add --found-in job:<REF>`, and defaults the route question to a warm contact.
- `CLAUDE.md` routing rows and layout lines (`people/`, `targets/`); the GPT workspace and the Career Memory evidence gate are unchanged.

## 11. Docs and release

`docs/outreach.md` (records, events, derived state, rules table, commands, `OUT` codes, cap enforcement, limits), README, CHANGELOG ("0.8.0: run `careeros upgrade` to receive the updated skills; the follow-up queue and weekly cap now come from recorded events; existing `activity.md` history is not imported"), mkdocs nav, version `0.8.0`.

## 12. Testing

Written first (RED); pure fold and derivation tests need no workspace:

- **Fold:** outcome transitions and labels against an independent simulator over random event sequences; audit-only rejections never change a result (property test); unknown `outreach.*` types are errors.
- **Queue / cap / next / warm:** grids at the boundaries (exactly 7 days, 10 days, the rolling window edge, `in_flight` counting toward the cap, the one-follow-up-per-invite rule, pre-opening contacts not queued, closed people excluded, ranking order for home and non-home, `not_offered` excluded, two-contacts-per-target limit).
- **Cap semantics:** an `in_flight` invite older than 7 days still counts and blocks `begin`; `in_flight -> consumed` never counts twice and uses the `approval.consumed` ts; `resolve --not-sent` (released) removes it; absent setting = 15, every invalid form (`abc`, `-1`, `1001`, two lines, empty value) fails closed with `cap_config_invalid` in `cap`, `next` and `begin`, and `0` blocks all invites.
- **Closed after approval:** propose, approve, then `record --closed`: `begin` refuses `person_closed` for an invite and a message item, with the close event recorded after approval and inside a race with `begin` (real processes: exactly one outcome, never a claim on a closed person).
- **Targets:** every legal and illegal decision transition and its refusal code; the status precedence table (each pair of overlapping conditions); stale by each qualifying event and evidence date, strictness at exactly 60 days; stale targets flagged and still eligible in `next`.
- **Source data:** hand-editing `found_in`/`role` changes target status and `next` but never identity, the cap, closed state or what was sent (tests for both halves of the §6.0 table); `OUT001`/`OUT006` catch bad edits; `person link` writes only the record.
- **Identity:** a person added here and proposed in approvals (and the reverse) share one `per_` id; URL variants map to one id; deleting or editing a record file cannot change identity; a conflicting identity map makes `person add` and `approval propose` fail closed (`identity_conflict`).
- **Approvals integration:** `person_closed` on propose; `cap_reached` on `begin` with a **real-process race** (two invites begun at `cap - 1`: exactly one proceeds); the cap value read from `profile.md` and the default.
- **Commands:** every refusal code, idempotence-hostile `record`, file-first/event-second rollback and crash windows (orphan `OUT002`), `--json` purity, exit codes, deduped `outreach.rejected`, clock floor.
- **Validation:** each `OUT` code with a positive and a negative case; `validate` leaves the whole workspace byte-identical (ledger and `.careeros/` included).
- **Skills/docs:** wording tests pin the note rules, the approvals flow, "never sends", the prose-only email/interview statement, and the numbers (7, 10, 15, 60) against the code constants; every command line in the skills parses with the real CLI (a test); mutation-checked.
- **Packaging:** the dependency AST test and a **fresh Python 3.11 venv without click** run, as for 0.7.0.
- **End to end** on invented data, and an **acceptance run in a disposable workspace** (codes and counts only; the real workspace is never touched).

## 13. Out of scope and known limits

- **Out of scope:** a GPT workspace counterpart, sending or tracking email, interview follow-ups, automatic reply detection, learning from outcomes (audit sub-project 5), a dashboard, importing existing `activity.md` history.
- **Known limits:** `found_in`, `role` and the other descriptive record fields are editable source data that influence target status and `next` without an audit event; a dropped target cannot be re-opened; outcomes and target decisions are reported facts, not verified against LinkedIn; fit and sponsorship are agent-supplied evidence; the cap counts only invites recorded in CareerOS (invites sent outside it are invisible); a company slug match is a heuristic (`slugify`); ledger tail truncation cannot be detected by a hash chain; this is not a security boundary.

## 14. Build order

1. **A — pure core:** rules, record parsing and validation, `slugify`, the person/target fold, queue, cap, next, warm.
2. **B — records and commands:** `person` and `target` commands, the `outreach.*` events and reserved prefix, identity unification with approvals, `OUT` validation.
3. **C — outcomes and approvals integration:** `record`, `queue|cap|next|warm` commands, `person_closed` and `cap_reached`.
4. **D — skills, docs, version 0.8.0.**
5. **E — end-to-end test, acceptance run, release checks (3.14 and a fresh 3.11 without click).**

Each task is one commit (plus fix commits), reviewed before the next.

## 15. Acceptance

A disposable invented workspace: add a target and two people (URL variants resolve to one id); approve the target; `next` ranks and limits to the cap; propose, approve, begin and complete an invite (cap count 1); a second begin at the cap is refused `cap_reached`; the clock advances 7 days and the invite appears in `queue`; `record --accepted` removes it and `warm` lists the person once a job at the company is saved; an invite left `in_flight` for more than 7 days still counts toward the cap until it is resolved; `record --closed` after an approval makes `begin` refuse `person_closed`, and `propose` refuses it too; `validate` leaves the whole workspace byte-identical. Full suite green on Python 3.14 and a fresh 3.11 without click, `mkdocs build --strict`, wheel contents, `git diff --check`.
