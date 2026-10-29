# Approvals

LinkedIn invitations, LinkedIn messages and the final Submit of an application each need a recorded decision. The agent *proposes* the exact action. Only you, in an interactive terminal, can approve or decline it, and you see the exact content when you do. The approval is bound to a hash of the action, the target and the content, so an edited draft needs a new decision. It is single use, it expires, and everything is a ledger event: the current state is derived from the events, never stored.

**This is not a security boundary.** An agent with browser tools can still click without calling `begin`. What it gives you is a recorded, content-bound, single-use decision, refusal in every code path CareerOS controls, and a tamper-evident trail. See [Limits](#limits).

The `outreach` (invites), `follow-up` (LinkedIn messages) and `apply` skills use it. CareerOS does not send email, so email is outside it. Run `careeros upgrade` to receive the updated skills.

```bash
careeros approval propose --kind linkedin_invite --person-url https://www.linkedin.com/in/alex-example --label "Alex Example, Example Corp" --content-file note.txt
careeros approve                       # you, in a terminal: review every pending item, approve / decline / skip
careeros approval show apr_xxxxxxxxxx  # the exact content, hash prefix and derived state
careeros approval begin apr_xxxxxxxxxx # the agent, immediately before acting
careeros approval done apr_xxxxxxxxxx  # the agent, once the action is confirmed
```

## The item

Each item is one file, `approvals/<apr_id>.md`: YAML frontmatter plus the exact content as the body. `propose` writes it once and it is never edited. This example is invented.

```markdown
---
schema: 1
id: apr_xxxxxxxxxx
kind: linkedin_invite
target:
  id: per_xxxxxxxxxx
  channel: linkedin
  label: "Alex Example, Example Corp"
  url: "https://www.linkedin.com/in/alex-example"
created_at: 2026-10-15T09:00:00Z
---
Hi Alex, I saw Example Corp's platform work and would like to connect.
```

| Field | Meaning |
|---|---|
| `kind` | `linkedin_invite`, `linkedin_message` or `application_submit` |
| `target` | who or what it is for: `id`, `channel`, `label` and, for invites and messages only, the canonical profile `url` |
| `manifest` | `application_submit` only: the final documents as `{path, sha256}`, workspace-relative regular files. Code computes the hashes |
| body | the exact message, or for `application_submit` the exact answers text. Line endings are normalised to LF and nothing else is changed |

The label must be plain text. Control characters, bidi controls and invisible (default-ignorable) characters are refused, including the emoji presentation selector U+FE0F anywhere in the label. An ordinary emoji such as a smiley is accepted; keep labels to ordinary words.

The **action hash** covers the kind, the target (id, channel, label and url), the exact content and the sorted manifest. It is stored in the `approval.proposed` event. A changed draft is a new item with a new id; `propose` never overwrites a file or reuses an id. Editing an item file after proposing is `APR004`.

### Person identity

Approvals keep no person records of their own (the optional `people/<per_id>.md` records belong to the outreach feature and are never needed here). A person is identified by the canonical LinkedIn profile URL: `https://www.linkedin.com/in/<slug>`, scheme forced to `https`, host `linkedin.com` or `<country>.linkedin.com` mapped to `www.linkedin.com`, the slug percent-decoded, NFKC-normalised and lowercased, and the query, fragment and trailing slash dropped. Another host, a path other than `/in/<slug>`, credentials or a port are refused. The first proposal for a canonical URL allocates a `per_` id; every later proposal for the same URL reuses it, so the one-invite-per-person and in-flight rules hold however the agent re-proposes.

Two assumptions sit behind this: LinkedIn slugs are case-insensitive, and NFKC-equivalent spellings are the same profile. If either is wrong for a profile, two spellings would count as two people.

Two further assumptions: one person reachable through a member-id URL (`/in/ACoAA...`) and through a vanity slug, or whose slug was renamed, counts as two people, so a second invite to them is allowed. Member ids are case-sensitive on LinkedIn but are lower-cased here, so two member ids that differ only in case would count as one person.

For an application, the target is the job (`job_` id), which must exist.

## Lifecycle

States: `pending`, `approved`, `in_flight`, `consumed`, `declined`, `expired`, `superseded`, `voided`.

| Event | Written by | Meaning |
|---|---|---|
| `approval.proposed` | the agent (`propose`) | the item exists, state `pending` |
| `approval.approved` / `approval.declined` | you, interactive (`approve`) | the decision; actor `user`, source `tty` |
| `approval.superseded` | `propose`, in the new proposal's batch | an earlier pending or approved item of the same kind and target is void |
| `approval.started` | the agent (`begin`) | the claim; state `in_flight` |
| `approval.consumed` | the agent (`done`) or you (`resolve --sent`) | the action happened |
| `approval.released` | you (`resolve --not-sent`) | the claim is released; nothing was sent |
| `approval.voided` | you (`resolve --not-sent`, failed revalidation) | terminal; carries a reason |
| `approval.rejected` | any command that was refused | an audit record of the attempt; carries a reason; deduped |

| Current state | Operation | Result |
|---|---|---|
| `pending` | approve / decline | `approved` / `declined` |
| `pending`, `approved` | a newer proposal for the same kind and target | `superseded` |
| `in_flight` | a newer proposal for the same kind and target | the proposal is refused (`in_flight_conflict`); the in-flight item is untouched |
| `approved` | `begin` | `in_flight` |
| `approved` | expiry reached | `expired` (computed, no event) |
| `in_flight` | `done` or `resolve --sent` | `consumed` |
| `in_flight` | `resolve --not-sent` | `approved` (released), `expired` or `voided` |
| `consumed`, `declined`, `expired`, `superseded`, `voided` | any reuse | refused |

A declined item is never re-opened; a new decision needs a new proposal. `in_flight` never times out, because an uncertain outcome needs a human.

**Unresolved** means `pending`, `approved` and not expired, or `in_flight`. The other states are resolved.

### Expiry

An approval is valid while `now < approved_at + 24 hours`; at exactly 24 hours the item is `expired`. `approved_at` is the ledger's stamp on the `approval.approved` event, written when your decision is recorded, so the 24 hours start at recording, not at the proposal. Each operation reads the clock once and uses that value for its checks; the event timestamps it writes are read after that single clock read. The clock is wall-clock time, not monotonic across restarts. Only `pending` items older than 7 days are flagged (`APR100`); they are never closed automatically.

### The clock floor

Every operation that changes state (`propose`, `approve`, `begin`, `done`, `resolve`) refuses `clock_skew` when the clock is earlier than the item's approval, than the item's latest event, or than the **clock floor**: the latest timestamp of any event of any type in the ledger (equal is allowed). `approval list` and `approval show` only flag a skewed clock; they refuse nothing. A backward jump never extends or bypasses any other check. The remedy is to fix the system clock; if the latest event is future-dated by a wrong clock, restore `ledger.jsonl` from `.careeros/backups`. Because any event raises the floor, a stray future-dated event fails closed.

## Commands

| Command | Who | What it does |
|---|---|---|
| `careeros approval propose --kind K (--person-url URL \| --job REF) [--label L] --content-file F [--manifest M]...` | agent | validates the target and manifest, writes `approvals/<id>.md`, then appends `approval.proposed` (and `approval.superseded` for each replaced item) in one batch |
| `careeros approval list [--status S] [--json]` | anyone | the items with their derived state; changes nothing |
| `careeros approval show <id> [--json]` | anyone | the exact content (control characters, ANSI escapes, bidi overrides, zero-width and other invisible characters are shown as visible escapes), its length, the first 12 hex characters of the action hash and the state; changes nothing |
| `careeros approve` | you | the queue: every pending item is shown in full with approve / decline / skip |
| `careeros approve <apr_id>` | you | decide one item |
| `careeros approve <job>` | you | the original job-level approval, unchanged |
| `careeros approval begin <id>` | agent | claims an approved item immediately before acting (`approval.started`). With `--json` it returns the `content` and the `manifest` that were checked under the lock at claim time: the agent sends that text, not the output of `show` (which is unlocked and runs after the claim) |
| `careeros approval done <id> [--note T]` | agent | records that the claimed action happened (`approval.consumed`); never changes the job |
| `careeros approval resolve <id> --sent \| --not-sent` | you | records your account of an `in_flight` item |

On screen (`show`, the `approve` queue), the content is a block: every content line is prefixed with the gutter `| `, and the block ends with `+-- end of content (N characters, hash xxxxxxxxxxxx)`. Content cannot forge that closing line or a header line, because each of its own lines carries the gutter; a line of dashes or `hash:` inside the content stays visibly inside the block.

`approve` and `resolve` need an interactive terminal; in Claude Code type `! careeros approve`. A non-interactive run is refused (`not_interactive`), and a closed stdin or stdout counts as non-interactive. For an `application_submit` item, your approval also records the job-level approval, in the same batch.

**Exit codes:** `0` ok; `1` refused (a refusal code, a broken ledger, a lock timeout or a disk failure); `2` usage, no workspace, or a non-interactive terminal where an interactive one is required. Add `--json` to `propose`, `list`, `show`, `begin`, `done` and `resolve` for machine-readable output; a refusal prints `{"error": ..., "code": ...}`.

Under the workspace lock, `begin` re-folds the state and requires the item to be `approved`, unexpired and free of `clock_skew`; recomputes the action hash from the file; for `application_submit`, revalidates and re-hashes every manifest file and requires the job to be at `APPROVAL_REQUIRED` and not archived, with its job-level approval recorded; and requires that no other item with the same kind and target is `in_flight`. For an invite it also requires that no invite to the same person is `consumed`; for a submission, that no submission for the job is `consumed`. Two racing `begin` calls have exactly one winner. Deciding on a submit item needs the same job state. Proposing is allowed at any time.

## Refusal codes

Stable codes used by `approval.rejected` reasons and command output.

| Code | Meaning |
|---|---|
| `not_found` | no item has this id (an item exists only once its `approval.proposed` event is recorded). Nothing is appended |
| `wrong_state` | the operation is not legal for the item's current state, which is named |
| `expired` | the 24 hours have passed |
| `clock_skew` | the clock is earlier than the item's approval, its latest event or the ledger's clock floor |
| `content_missing` | the item file is missing |
| `hash_mismatch` | the item file no longer matches what was proposed |
| `manifest_invalid` | a manifest path is absolute, uses `..`, is empty, contains a control character, resolves outside the workspace (a symlink), does not exist (including a file deleted after approval), cannot be read, is not a regular file, or is listed twice |
| `manifest_changed` | a manifest file exists but its content differs from the proposal |
| `invite_conflict` | an invite to this person is already sent |
| `already_submitted` | a submission for this job is already consumed |
| `in_flight_conflict` | an item of the same kind and target is `in_flight` |
| `submission_unresolved` | a submit item for the job is unresolved, so `APPLIED` is refused |
| `submission_not_consumed` | no submit item for the job is consumed, so `APPLIED` is refused |
| `not_interactive` | the command needs an interactive terminal |
| `ledger_corrupt` | the ledger's hash chain is broken; nothing is written |
| `job_not_awaiting_approval` | a submit item's job is not at `APPROVAL_REQUIRED`, is archived, or (for `begin`) lacks its job-level approval |
| `identity_conflict` | the person identity map in the ledger is inconsistent (one profile URL under two person ids, or one id under two URLs, across proposals and `outreach.person_added` events), or an `outreach.person_added` event or a person proposal has no readable profile URL: a person proposal is refused and nothing is written until `ledger.jsonl` is restored |
| `invalid_input` | the proposal itself is not valid: an unparseable URL, an empty label or content, or a value the item cannot store |

A refusal of an existing item appends an audit-only, deduped `approval.rejected`. A refusal of an id that names no item appends nothing.

### Problem codes in list and show

`approval list` and `approval show` (text and `--json`) report why an item is not usable in `problems`. Besides the refusal codes `content_missing`, `hash_mismatch` and `clock_skew`, three codes exist only here:

| Code | Meaning |
|---|---|
| `item_invalid` | the item file cannot be parsed as an approval item |
| `event_mismatch` | the proposal event and the item file disagree (the file path, kind, target or profile URL) |
| `sequence_error` | the item's `approval.*` events break the lifecycle, or an unknown `approval.*` event type names it |

### Audit-only rejections

`approval.rejected` (and the job-level `job.transition_rejected`) never change a state. The fold, `approved_at`, supersession, every conflict check, the `APPLIED` gate and the `APR005` sequence check ignore them. Only the deduplication reads them, to skip an identical repeat. Like any event they can raise the clock floor, which fails closed.

## The APPLIED invariant

A job with no `application_submit` item behaves exactly as before: `APPLIED` needs `careeros approve <job>` and nothing else. Once a job has any `application_submit` item, `careeros transition <job> --to APPLIED` also requires that **no submit item for the job is unresolved and at least one is `consumed`**. There is no notion of a "latest" item: an unresolved item blocks the transition whichever is newest, and a consumed item never lets an unresolved sibling through.

- Refusal order: a job with a pending submit item and no job-level approval is refused `approval_required` first (the message says that the user decides a submit item with `careeros approve <apr_id>`), then `submission_unresolved`.
- The gate trusts approval events, so for a job that has submit items it first verifies the ledger's hash chain, on both the normal path and the repair path, and with `--force`. A broken chain is `ledger_corrupt`: nothing is appended (not even a rejection), and the message says to restore `ledger.jsonl` from `.careeros/backups`. Jobs without submit items are not checked.
- `submission_unresolved` names each unresolved item and its state, and for an `in_flight` item points at `careeros approval resolve`. It takes precedence.
- `submission_not_consumed` means only declined, expired, superseded or voided items exist.

`done` and `resolve` never change the job. The apply skill's order is: propose, you approve, `begin`, click Submit, confirmation page, `done`, `careeros transition`. A crash between `done` and the transition leaves the job at `APPROVAL_REQUIRED`; the transition simply runs again. It never resubmits.

Forced transitions (`--force`) are gated too, by design. If you applied outside CareerOS on a job that has submit items, settle the items first: make sure the job is at `APPROVAL_REQUIRED` (deciding and `begin` need it), propose an `application_submit` for the job, approve it, run `careeros approval begin`, then record what happened with `careeros approval resolve <id> --sent`; the transition then succeeds. A job that never had a submit item needs none of this.

The apply skill no longer writes an "Applied date" into the job file; the ledger and `activity.md` are the record of when it happened.

## Recovery

| Situation | What happens |
|---|---|
| The action succeeded but the process died before `done`, or the confirmation page was already shown | The item stays `in_flight`. `begin` is refused. Either the agent runs `done` (while it still has the session) or you run `resolve --sent`; both record it as sent |
| The outcome is uncertain (button not found, confirmation not seen) | The agent stops and never retries. You run `resolve --sent` or `resolve --not-sent` |
| `resolve --sent` | Appends `approval.consumed` (actor `user`, source `tty`) |
| `resolve --not-sent` | Revalidates the content hash, expiry with the current clock, the manifest hashes (submissions) and invite uniqueness. All pass: `approval.released` (back to `approved`). Only expiry fails: released, and the state reads `expired`. Anything else: `approval.voided` with the reason, and a new proposal is required |
| `approval.voided` | Terminal. The old approval is never silently reused |
| Crash between the file and the proposal event | An orphan file: `APR002`, never actionable |
| A proposal event but the file is gone | `APR003`, never actionable |

Correctness does not rely on exception handlers: every crash state is safe by ordering (file first, event second).

### Ledger integrity

Every operation that changes state verifies the ledger's hash chain first; `approval list` and `approval show` do not verify it (they exit 1 only on a corrupt line). A broken chain is `ledger_corrupt` and nothing is written. `careeros validate` stays entirely read-only: it creates, modifies and removes nothing, including `.careeros/`.

## Validation codes

`careeros validate` reports these for every approval item.

| Code | Severity | Meaning |
|---|---|---|
| `APR001` | error | item file unreadable, invalid frontmatter, unknown key, bad kind or target shape |
| `APR002` | warning | orphan file: no `approval.proposed` event |
| `APR003` | error | a non-terminal item's file is missing |
| `APR004` | error | content hash differs from the proposal event |
| `APR005` | error | illegal event sequence (for example `consumed` without `started`), or an unknown `approval.*` event type |
| `APR006` | error | a job target does not exist, or the person identity is inconsistent (one canonical URL under two `per_` ids, or one `per_` id under two URLs) |
| `APR007` | warning | an `approved` or `in_flight` submit item whose manifest file is missing or changed |
| `APR008` | warning | an item is `in_flight`: run `careeros approval resolve` |
| `APR100` | info | a `pending` item older than 7 days |

## Limits

- **This is not a security boundary.** An agent with browser tools can click without calling `begin`. CareerOS records and checks; it does not confine.
- The interactive check is TTY-based and can be faked by a process that allocates a pty.
- The hash binds what the user saw, not what the browser finally sends, and the recipient on the LinkedIn page is not verified.
- Truncating the tail of `ledger.jsonl` cannot be detected by a hash chain, and it can revive an expired approval (a test pins this). Keep the `.careeros/backups` copies.
- An `APR005` history blocks even `resolve`, so a stuck `in_flight` submit item blocks `APPLIED` until the ledger is restored.
- A label containing an emoji presentation selector is refused.
- A closed stdin or stdout counts as non-interactive.
- Expiry is wall-clock based.
- There is no general job queue and no distributed transactions.
- There is no GPT-workspace parity: the GPT workspace stays guided.
- CareerOS does not send email, and offers (accept, decline, counter, sign) are out of scope.
- The skills' behaviour cannot be exercised in CI: their wording is pinned by tests and the CLI is tested.
