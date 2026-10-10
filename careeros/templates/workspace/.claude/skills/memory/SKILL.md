# CareerOS Memory Skill

**Trigger:** "import my resume" / "update my career memory" / "remember that [fact]" / "add this achievement" / "what do you know about me?" / "confirm [fact]" / "I no longer [fact]"

Your career memory is the set of facts in `career/` that every draft is checked against. Nothing you write for the user may claim more than these facts say. Each fact has a **status** (how far it is vouched for) and a **source** (where it came from); a fact imported from the user's own resume is only `claimed` until the user confirms it.

| Status | Meaning |
|---|---|
| `claimed` | Stated by the user (in their resume or in chat). Not yet reviewed as a structured fact. |
| `confirmed` | The user reviewed it and affirmed it. Only the user can do this, at a terminal. |
| `verified` | Confirmed, and backed by evidence the user supplied. |
| `disputed` / `retired` | Wrong, or no longer applicable. Supports nothing. |

---

## IMPORT THE RESUME

1. Run `careeros memory import` (a dry run). Show the user the plan: what would be added, changed or marked stale, anything it could not place, and anything that needs their review.
2. If the plan reports an error (for example a heading it cannot read), show the message and the line, and help the user fix `resume.md`. Nothing is written until the plan has no errors.
3. Ask: "Import these N facts?" Only after the user says yes, run `careeros memory import --apply --yes`.
4. Tell the user: the facts start as **claimed**, `resume.md` was not changed, and a backup of every file the import touched is in `.careeros/backups/`. Offer to walk through confirming the important ones (step "CONFIRM" below).
5. Re-run the same flow whenever `resume.md` changes (`careeros memory status` shows when it is out of date). The import never overwrites a confirmed fact and never deletes anything: a removed line marks its fact stale.

## ADD A FACT

When the user tells you something about their career that is not in the memory ("I also led the migration at Acme"):

1. Ask for the details you need (employer, dates, the number, the technologies) and for the claim **in their own words**.
2. Run `careeros memory add --kind achievement --set parent=<experience id> --text "<the claim>" --quote "<their exact words>"` (see `careeros memory add --help` for other kinds: `experience`, `project`, `skill`, `education`, `certification`, `preference`, `goal`, `constraint`, `evidence`).
3. Find ids with `careeros memory list --kind experience`.
4. Never add a fact the user did not state. Never paraphrase the quote.

## CONFIRM, VERIFY, DISPUTE, RETIRE

- **Confirm / verify** record the user's own decision. You cannot run them: they need an interactive terminal. Tell the user: "Run `! careeros memory confirm <id>` to affirm this one." Never try to pipe an answer into them.
- **Dispute** (the fact is wrong) and **retire** (no longer applies) need a `--reason` in the user's words: `careeros memory dispute <id> --reason "..."`.
- To change a fact: `careeros memory update <id> --text "..."`. Changing a confirmed fact resets it to `claimed`.

## SHOW WHAT YOU KNOW

- `careeros memory status` — counts by kind and status, stale imports, how much is still only claimed.
- `careeros memory list [--kind K] [--status S]` and `careeros memory show <id>`.
- Say plainly which facts are only claimed. Do not describe a claimed fact as verified.

## NOTES

- Every change is recorded in `ledger.jsonl`; nothing is deleted.
- If any command reports that the memory has errors, run `careeros validate`, show the user what it says, and stop until it is fixed.
