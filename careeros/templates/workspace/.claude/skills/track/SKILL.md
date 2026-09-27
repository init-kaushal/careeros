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

If user says "mark [company] as applied" or similar:

1. Update the checkbox in `jobs/pipeline.md`.
2. Update `Status:` in the job's file under `jobs/discovered/`.
3. Append to `activity.md`:
   ```
   [date] status: [Company] — [Title] → [new status]
   ```
