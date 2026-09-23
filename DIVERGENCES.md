
## Phase 13a: a cron follow-up run re-logs its permanent failures every pass

`careeros outreach follow-up` skips a not-due relationship silently and by
design — a daily cron must not fill an append-only activity log with records
of nothing happening. Two cases escape that intent, because neither advances
`last_touched_at`, so the relationship stays due forever and is reconsidered
every run:

- a relationship whose job is **policy-blocked** logs one `policy_blocked`
  event per run (from inside `propose_follow_up`), and
- an **orphaned** `outreach/` record whose job, person or company no longer
  resolves logs one `follow_up_propose_error` per run (from the command).

Measured: one event per affected relationship per run, indefinitely. The run
summary also carries the same non-zero `Blocked:` or `Errors:` counter every
pass, with no way to distinguish a new failure from the same one for the
four-hundredth time.

Deliberately not fixed in Phase 13a, because every cheap fix is worse than the
problem. Pre-checking policy in the command would reintroduce exactly the
duplicated due-ness logic that Phase 13a consolidated into
`check_follow_up_due`. Writing `closed_reason` after N failures would mutate
the user's own relationship data on a technicality — a temporarily missing
company record would permanently close a relationship. Suppressing the event
by reading back the log would make a proposer depend on its own audit trail.

The cost half is already bounded: `max_follow_ups_per_run` caps paid drafting
attempts at 20 per run, and three consecutive drafting failures abort the run.
What remains is log growth and a stale counter, both low-harm. Revisit in
Phase 13b, most plausibly by giving `OutreachMessage` a field that records the
last refusal so a repeat can be recognised rather than re-derived.
