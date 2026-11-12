# CareerOS Track Skill

**Trigger:** "track" / "show pipeline" / "what's my status" / "pipeline"

---

## STEP 0 — READ

Read `jobs/pipeline.md` above the fold (stop at `── HISTORY`).

---

## STEP 1 — PRESENT

Show active jobs as a table:

| Status | Company | Title | Score | Board | Date |
|--------|---------|-------|-------|-------|------|

Status icons: `[ ]` Discovered · `[~]` Applied · `[?]` Interview · `[✓]` Offer · `[x]` Closed

Summarize: "N jobs in pipeline. M applied, P in interview."

If pipeline is empty: "No jobs tracked yet. Say 'browse [board]' to start."

---

## STEP 2 — UPDATE (if user asks)

A job's status changes only through the transition command; never edit the job file's Status line or `jobs/pipeline.md` by hand. If `careeros` is not found or a command refuses, tell the user the code and what it said and stop; do not use `--force` and do not edit the files instead.

If the user says "mark [company] as [status]" or similar:

1. Read the job's current state with `careeros status`. If the job has no id yet (`careeros validate` reports `JOB001`), run `careeros migrate --dry-run` and show the user the plan; only if they say yes, run `careeros migrate --yes`.
2. Map what the user said to a state: discovered `DISCOVERED`, evaluated `EVALUATED`, shortlisted `SHORTLISTED`, an interview `RECRUITER_REPLIED`, `SCREEN`, `TECHNICAL`, `HM` or `FINAL`, an offer `OFFER`, offer accepted `ACCEPTED`, rejected `REJECTED`, withdrawn, closed or declined `WITHDRAWN`. If it is not clear which, ask.
3. Ask the user to confirm the move, naming the old and the new state. Only if the user says yes, run `careeros transition <job-slug> --to <STATE>`. States only move forward.
4. `APPLIED` is reachable only from `APPROVAL_REQUIRED`, and only after the user has approved the job themselves with `careeros approve <job-slug>` (an interactive terminal; in Claude Code they type it after `! `). That holds when the user applied outside CareerOS too. Ask the user whether to move the job forward one state at a time (`SHORTLISTED`, `RESEARCHED`, `PREPARING`, `READY_TO_APPLY`, `APPROVAL_REQUIRED`), confirm each move, and stop at `APPROVAL_REQUIRED`: tell the user to run `careeros approve <job-slug>`, and only after they have, ask again before moving to `APPLIED`. Never run `--force` yourself.
5. Append to `activity.md`:
   ```
   [date] status: [Company] — [Title] → [new status]
   ```
