# Career memory and the evidence check

CareerOS keeps what it may say about you in one place: your **career memory**, a folder of small markdown files in `career/`. Every resume, cover letter, outreach note, follow-up and interview answer the agent drafts is checked against it with `careeros check` before you see it.

The aim is narrow and testable: **a draft should not state a number, employer, school, title, certification, technology, year or duration that your memory does not support.**

## What the check guarantees, and what it does not

A pass means: *every claim the checker was able to detect is supported by your career memory.* It does **not** mean the draft is true, that every claim in it was detected, or that your memory is correct.

- **Provenance is not truth.** A fact imported from your own resume is `claimed`: it records where it came from, not that anyone verified it. Nothing upgrades a status automatically. Only you can confirm a fact, at a terminal.
- **It checks entities and figures, not sentences.** Numbers, years, durations, technologies, employers, schools, titles and certifications are matched against your facts. Whether the prose around them is accurate is not evaluated.
- **Claims in one sentence must come from one fact.** "Reduced AWS costs by 35% using Kafka" passes only if a single fact holds the 35%, AWS and Kafka together. If each appears in a different fact, the sentence is flagged for review (`CHK010`).
- **It lists what it did not examine.** Every run prints a *Not evaluated* section: the sentences in which nothing was detected, and the standing categories it cannot assess.

### What stays undetectable

Whether prose is true; wording and meaning; responsibilities, scope and team-size wording ("a large team"); qualitative outcomes ("improved reliability"); a lowercase or sentence-initial unknown name with no cue; technologies in neither the bundled lexicon nor your memory; numbers written as words ("a dozen"); and languages other than English.

Smaller limits of the detection rules:

- Soft-wrapped lines of a paragraph are joined only when the previous line has no closing punctuation and the next starts with a lowercase letter or a digit. A capitalised continuation is checked as a separate sentence.
- Capitalised words that appear in the text of your active achievements, projects, education and experience are treated as known, so a name made only of such words does not raise `CHK009`. Employer, school and title cues still apply.
- A list of technologies is exempt from the one-fact rule only in a skills-style section or line, or after a cue such as "experience with", "proficient in", "such as", "including", "Stack:" or "Technologies:", and only for the technologies that follow the cue.

## The workflow

1. **Import** your resume: `careeros memory import` shows a plan; `careeros memory import --apply` makes it. Your `resume.md` is only read, never changed.
2. **Confirm** the facts that matter: `careeros memory confirm <id>` (a person must run this, at a terminal; in Claude Code type `! careeros memory confirm <id>`).
3. **Draft** with the agent as usual. Each drafting skill runs `careeros check` on its final text and fixes or asks about anything it flags.
4. **Keep it current**: when `resume.md` changes, `careeros memory status` says so; import again to review the differences.

## What is in `career/`

| Folder | Holds |
|---|---|
| `identity.md` | your name and the titles you may call yourself |
| `experiences/` | one file per employer and title, with dates and technologies |
| `achievements/` | one file per resume bullet, with its numbers and technologies |
| `projects/`, `skills/`, `education/`, `certifications/` | what they say |
| `preferences/`, `goals/`, `constraints/` | things you want; not checked against drafts |
| `evidence/` | documents or links that back one or more facts |
| `sources.yaml` | each imported file and its hash |
| `lexicon.yaml` | optional: technologies and aliases you add to the bundled vocabulary |

Each file has frontmatter (`id`, `status`, `origin`, `source`, timestamps) and a heading for people to read. Nothing is ever deleted: retiring a fact keeps the file, so the ledger's references stay valid.

### Statuses

| Status | Meaning | Supports claims |
|---|---|---|
| `claimed` | stated by you (in your resume or in chat); not reviewed | yes |
| `confirmed` | you reviewed it and affirmed it | yes |
| `verified` | confirmed, and backed by an `evidence` record | yes |
| `disputed` | you say it is wrong | no |
| `retired` | superseded or no longer applies | no |

`confirm` and `verify` always need an interactive terminal. `update`, `dispute` and `retire` need one unless the fact is `claimed`. Without a terminal they exit 2 and change nothing. Editing any fact that is not `claimed` resets it to `claimed`. A `retired` fact cannot be changed at all.

## Commands

| Command | What it does |
|---|---|
| `careeros memory import [--source FILE] [--apply] [--yes]` | Read `resume.md` (or another file), show a deterministic plan, and with `--apply` write it. Backs up every file it changes. |
| `careeros memory add --kind K --text "..." --set key=value --quote "your words"` | Add a fact you stated. Kinds: `experience`, `achievement`, `project`, `skill`, `education`, `certification`, `preference`, `goal`, `constraint`, `identity`, `evidence`. |
| `careeros memory update ID --text "..."` / `--set key=value` | Change a fact. Needs a terminal unless the fact is `claimed`; resets a changed fact to `claimed`; refused for a retired fact. |
| `careeros memory confirm ID` / `verify ID --evidence EVD` | Record your own decision. Interactive terminal only (exit 2 without one). |
| `careeros memory dispute ID --reason "..."` / `retire ID --reason "..."` | Mark a fact wrong or no longer applicable. Needs a terminal unless the fact is `claimed`. |
| `careeros memory list [--kind K] [--status S]` / `show ID` / `status` | Read the memory. `--json` is available. |
| `careeros check PATH` (or `-` for standard input) | Check a draft. `--against JOB`, `--allow TERM`, `--require-confirmed`, `--record`, `--json`. |

Every change is recorded in `ledger.jsonl` (`memory.*` events, written only by these commands). `careeros check --record` adds a `draft.checked` event carrying the hash of the exact text that was checked.

### Importing `resume.md`

The importer is deterministic and understands this layout:

```markdown
## Experience
### Employer | Title
City - Month YYYY - Present
- A bullet becomes an achievement.
- Tech: Go, Python, Kubernetes      <- becomes the experience's technologies

## Education
**School, City** - Month YYYY
B.Tech. in Computer Science | CGPA: 8.1/10

## Projects
**Project name (stack)** - YYYY
One line describing it.

## Skills
**Category:** Go, Python, AWS (Lambda, EC2)

## Certifications
- Certified Kubernetes Administrator, 2023
```

Sections it does not know, and lines before the first `##`, are reported as *not imported*. A heading it cannot read (for example an experience heading without a `|`) is an error and **nothing is written**. So is an experience without a date range on the first line under its heading, or a school without a date after its name and a degree line under it. Re-importing:

- never overwrites a `confirmed` or `verified` fact: it lists it under *needs review* with the proposed change;
- never touches facts you added or edited by hand;
- never re-creates a fact you retired;
- marks a fact `stale` when its line has left the resume (it is not deleted, and still supports claims until you retire it).

## `careeros check`

Exit codes: **0** every detected claim is supported and nothing needs review; **1** at least one finding; **2** a usage error or a career memory that cannot be trusted (run `careeros validate`).

| Code | Meaning |
|---|---|
| `CHK001`-`CHK007` | a number, year, technology, employer, school, title or certification is not in your memory |
| `CHK008` | a duration your dated experience does not support, or years with a specific technology (never derivable) |
| `CHK009` | a name-shaped phrase the checker does not recognise |
| `CHK010` | claims that are each supported but not by one fact together, or an allowed term combined with other claims |
| `CHK020` | supported only by `claimed` facts (a failure with `--require-confirmed`) |
| `CHK030` | the memory has no usable facts (a failure) |
| `CHK031` | an informational note that never changes the exit code: a source file changed since it was imported, or a supporting fact is stale (its source line is gone from the resume) |

### `--allow` and `--against`

`--against JOB` lets a cover letter name the company and title it applies to. Working there still needs an experience record. `--allow TERM` lets a draft mention something that comes from the job post or from another person (a technology the team uses, a name); it is listed as an **unverified mention** and is never evidence. In a sentence with any other claim, an allowed term cannot stand in for a missing fact: the sentence is flagged. This holds for names too: an allowed name beside any other claim is flagged `CHK010`.

`--against` never excuses a self-description such as "I am a Staff Engineer" or a claim of employment. A duration stated next to an employer is compared with that employer's own dated experience. An achievement lends its parent experience's employer, title and dates only while that experience is active.

### Common false positives

The checker prefers a false alarm to a missed fabrication. A person's name or a place it does not know raises `CHK009`: pass it with `--allow "Name"`, or add yourself with `careeros memory add --kind identity --set name="Your Name" --quote "..."` so your own name is known. A name that only appears in the text of your own active facts is already known. Add real technologies you use as skills so they are recognised and supported.

## Memory validation

`careeros validate` reports problems in `career/`: `MEM001` unreadable file (including a `career/sources.yaml` or `career/lexicon.yaml` that cannot be read or has the wrong shape), `MEM002` missing key or bad or duplicate id, `MEM003` dangling reference, `MEM004` invalid status, origin or source, or a date the check cannot read (an experience `start` must be `YYYY-MM`, an experience `end` `YYYY-MM` or `present`, an education `end` `YYYY-MM`, a certification `year` a four-digit year), `MEM005` confirmed without a decision or verified without evidence, `MEM006` source changed since import, `MEM007` source file missing, `MEM008` stale fact, and `LED003` when a fact the ledger mentions has no file.

A broken `career/lexicon.yaml` (your own technology, certification or vocabulary additions) is reported by `validate` as `MEM001`, and `careeros check` and every memory command then refuse with exit 2 until it is fixed.

Evidence cannot be retired or disputed while it is the only active evidence for a `verified` fact; dispute or re-verify that fact first.

## GPT Work

The evidence gate is Claude Code only for now: GPT Work cannot run local commands, so its skills are unchanged and its drafts are not checked.
