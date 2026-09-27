# Skill: Track

## When to run

When user says "show pipeline", "track", "status", or asks to update a job's status.

---

## STEP 1 — Read pipeline

Read `jobs/pipeline.md` above the fold (stop at the `── HISTORY` line).

---

## STEP 2 — Show status table

Display the pipeline with status icons:

```
| Status | Job | Company | Score | Added |
|--------|-----|---------|-------|-------|
| [ ]    | Senior Backend Engineer | Stripe | 8.5 | 2024-01-15 |
| [~]    | Staff Engineer | Notion | 7.0 | 2024-01-12 |
```

**Status icons:**
- `[ ]` Discovered
- `[~]` Applied
- `[?]` Interview
- `[✓]` Offer
- `[x]` Closed

Show a summary line: "**N jobs total — X discovered, Y applied, Z interviews, W offers**"

---

## STEP 3 — Handle update requests

If the user asks to update a status (e.g. "mark Stripe as applied", "move Notion to interview"):

1. Confirm: "Updating Stripe → Applied. Correct?"
2. On confirmation, output two blocks:

### Updated pipeline.md

```markdown
**`jobs/pipeline.md`** — replace your existing file:

~~~markdown
[full pipeline table with updated row]

## ── HISTORY (on demand; do not read past this line) ──
~~~
```

### Activity log entry

```markdown
**`activity.md`** — append this line to your existing file:

~~~
[YYYY-MM-DD] [Title] @ [Company]: status updated to [new status].
~~~
```

---

## Notes

- Always output complete file contents, never just the changed row
- If the user asks for details on a specific job, read `jobs/discovered/[job-file].md`
