# Career Memory and evidence (sub-project 2) — design

**Date:** 2026-10-10
**Status:** drafted for review
**Builds on:** CareerOS Foundation 0.3.0 (merged): IDs, ledger, workspace lock, atomic writes, validation, backups and manifests.
**Source brief:** the Career OS brief, sections 4 (Career Memory) and 5 (Evidence system), plus seven design decisions the user locked in (section 2).

## 1. Goal

The agent should only state career facts that trace to a source, and a deterministic check should catch drafts that say more than the source supports.

- Facts live in a structured `career/` tree of markdown files with frontmatter. Every fact carries its **provenance** (where it came from) and a **status** (how far anyone has vouched for it). These are separate things.
- `careeros memory import` turns an existing `resume.md` into facts, reviewably and repeatably, without ever overwriting confirmed facts.
- `careeros check <draft>` examines a draft's checkable claims against that memory and fails closed on anything unsupported or whose relationships the memory does not represent.
- Every drafting skill must run the check on its final draft and may not show or send a draft that has not passed.

**Success looks like:**

1. On a copy of the real workspace, `memory import` produces facts from `resume.md` with no change to `resume.md`, and a second import is a no-op.
2. `check` exits non-zero for a draft containing an invented metric, employer, school, certification, technology, duration or an unrepresented relationship, and exits zero for a faithful paraphrase of a supported achievement.
3. A fact imported from your own resume is reported as *supported by a claimed fact*, never as verified.
4. Every change to the memory is in the ledger, with backup and rollback for imports.
5. Each drafting skill's text requires the check after its final edit, and tests assert that.

## 2. Decisions

The user's seven locked decisions come first.

| # | Decision | Where it lands |
|---|---|---|
| D1 | **Provenance is not truth.** Importing a fact from `resume.md` makes it `claimed`, with its source and a confidence preserved. Nothing upgrades a status automatically. | §5, §6.4 |
| D2 | **"Supported" is defined narrowly.** Exact numbers, years, employers, titles, schools, certifications and technologies are checked against structured facts. The checker flags unsupported entities and metrics; it never claims a whole sentence is true. | §8.1, §8.6 |
| D3 | **Compound claims are handled conservatively.** Atoms in one sentence must co-occur in a single memory record; each atom being supported somewhere is not enough. Otherwise the sentence is flagged `review`. | §8.4 |
| D4 | **The check is mandatory in every drafting skill** (`prep`, `apply`, `outreach`, `follow-up`, `humanize`, `interview`), on the final draft after all edits. Failures must be fixed or confirmed with you, never ignored. | §12 |
| D5 | **The guarantee stays narrow.** A pass means "supported by the current career memory under these rules". The output separates confirmed support from claimed-only support. | §8.6 |
| D6 | **Imports and edits are reviewable.** Re-import produces a deterministic diff, never overwrites confirmed facts, never deletes manual facts, and is audited with backup and rollback. | §6, §7 |
| D7 | **Failure paths are tested**, not just examples, including keyword-stuffing and malformed memory. | §14 |

Further design decisions:

| Decision | Choice | Why |
|---|---|---|
| Source of truth for what the agent may claim | `career/` | `resume.md` and `profile.md` stay untouched inputs; the importer reads them |
| Checking is deterministic | No model judgment, no similarity scores | the same draft and memory always give the same answer, and it is testable |
| Relationship representation | One record = one place where atoms co-occur | no separate relation graph to maintain; an achievement record already says "these things go together" |
| Confirming a fact | TTY-guarded `careeros memory confirm` | same pattern as `approve`: a human-confirmation guard, not a security boundary |
| New dependencies | none | PyYAML and the standard library are enough |
| Skills | wired in this sub-project, last in build order | a check nothing calls is only advisory |

## 3. Alternatives considered

- **A. One structured file per fact (chosen).** Readable, diffable, each fact has its own ID, status and source; the ledger can refer to it. Cost: many small files.
- **B. One big `career.yaml`.** Fewer files, but every edit rewrites the whole thing, diffs are noisy, and per-fact provenance is awkward.
- **C. Model-judged verification** ("does this sentence follow from the resume?"). Handles paraphrase, but it is non-deterministic, cannot be tested for failure paths, and a model that invents facts can also approve them. Rejected.

## 4. The `career/` tree

```text
career/
  identity.md                  # id: prf_…   name, location, headline(s)
  goals/<id>.md                # gol_…
  preferences/<id>.md          # pre_…       "no fintech roles"
  constraints/<id>.md          # con_…
  experiences/<id>.md          # exp_…       one per employer + title
  achievements/<id>.md         # ach_…       one per claim (a resume bullet)
  projects/<id>.md             # prj_…
  skills/<id>.md               # skl_…
  education/<id>.md            # edu_…
  certifications/<id>.md       # crt_…
  evidence/<id>.md             # evd_…       independent support for a fact
  sources.yaml                 # imported source files and their hashes
  lexicon.yaml                 # optional: the user's additions to the bundled lexicon
```

Out of this sub-project: `stories/`, `decisions/`, `learning/`, `compensation.md`, `writing-style.md` (later phases).

### 4.1 Common frontmatter

```yaml
id: ach_7k3m9q2xta
type: achievement            # experience | achievement | project | skill | education | certification
                             # | preference | goal | constraint | identity | evidence
schema: 1
status: claimed              # claimed | confirmed | verified | disputed | retired
origin: imported             # imported | manual
source:                      # where this fact came from (required)
  kind: resume               # resume | user_statement | document
  path: resume.md            # for resume/document
  line: 21                   # 1-based line in path, when applicable
  sha256: "…"                # sha256 of the source line (resume) or file (document)
  quote: "…"                 # required for user_statement: the user's own words
created_at: 2026-10-10T09:15:00Z
updated_at: 2026-10-10T09:15:00Z
confirmed_at: null           # set by confirm
```

Every file starts with `---` frontmatter and then a `# Heading` for human readers; the heading is not parsed. Timestamps follow the Foundation rule (full UTC ISO-8601 from `models.utc_now()`).

### 4.2 Kind-specific fields

| Kind | Fields |
|---|---|
| experience | `employer`, `title`, `start` (`YYYY-MM`), `end` (`YYYY-MM` or `present`), `technologies` (list, optional), `location` (optional) |
| achievement | `parent` (an `exp_…` or `prj_…` ID, or `null` with `section: summary`), `text` (the claim as written), `metrics` (list of `{raw, value, unit, plus}`), `technologies` (list) |
| project | `name`, `text`, `start`/`end` (optional), `metrics`, `technologies` |
| skill | `name`, `category` (optional) |
| education | `school`, `degree`, `field` (optional), `end` (`YYYY-MM`), `metrics` (e.g. a grade) |
| certification | `name`, `issuer` (optional), `year` |
| preference / goal / constraint | `text` (preferences are not checked against drafts) |
| identity | `name`, `location` (optional), `headlines` (list of titles the user may call themselves) |
| evidence | `kind` (`document` or `link`), `path` or `url`, `supports` (list of fact IDs), `note` |

`metrics` entries are produced by the importer and `memory add` (not typed by hand): `raw` is the text (`"35%"`, `"1M+"`), `value` the normalized number, `unit` one of `percent`, `count`, `currency:<code>`, `multiplier`, `score`, and `plus` a boolean for an "at least" suffix.

## 5. Statuses and provenance

Two independent axes:

| Axis | Values | Meaning |
|---|---|---|
| **status** (how far it is vouched for) | `claimed` | stated in a document or conversation by the user; not reviewed as a structured fact |
| | `confirmed` | the user reviewed the structured fact and affirmed it |
| | `verified` | independent evidence the user supplied backs it (`evidence` record) |
| | `disputed` | the user says it is wrong |
| | `retired` | superseded or no longer to be used |
| **source** (where it came from) | `resume`, `user_statement`, `document` | recorded forever; never changed by a status change |

Rules:

1. The importer and `memory add` create `claimed` facts only. Only `confirm` and `verify` raise a status, and both need an interactive terminal.
2. `disputed` and `retired` facts never support any claim.
3. `verified` requires at least one existing `evidence` record in `supports`; `validate` checks it.
4. A status change never edits the `source` block. A change to the *content* of a fact resets nothing silently: editing a `confirmed` or `verified` fact needs an interactive terminal and sets it back to `claimed` (it must be confirmed again).
5. **Origin protects manual facts:** `origin: manual` facts are never touched by an import.

## 6. Importing `resume.md`

`careeros memory import [--source PATH] [--dry-run | --apply]`. The default source is `resume.md` in the workspace root. The source file is read, never written.

### 6.1 Grammar (v1)

The importer is deterministic and understands this structure, which is what `resume.md` has today:

- `##` headings name sections, case-insensitively: `Summary`, `Experience`, `Education`, `Projects`, `Skills`, `Certifications`. Unknown sections are reported and skipped (listed, never silently dropped).
- **Experience:** `### Employer | Title` (the only accepted separator in v1), followed by an optional date line containing a range such as `Jan 2024 - Present`, `2021 – 2023` or `03/2020 - 06/2022` (month names or numbers, `-`, `–` or `—`, `Present`), then bullets (`-` or `*`) and/or prose lines. Every bullet or prose line becomes an `achievement` under that experience.
- **Summary:** each sentence becomes an `achievement` with `parent: null`, `section: summary`.
- **Education:** a line `**School** - Month YYYY` (or `**School**, Month YYYY`), followed by a degree line `Degree in Field | Detail`; the end date and any grade metric are extracted.
- **Projects:** each `### Name` heading or top-level bullet starts a project; its text and sub-bullets become the project text and achievements.
- **Skills:** each line is `Category: a, b, c` (bold optional) or a plain comma list; every item becomes a `skill`.
- Lines the grammar cannot place **inside a recognized section** become achievements of that section's entity (nothing is dropped). A line that breaks a section's structure (an experience heading without a `|`) is a hard error listing the line number and what was expected; **nothing is written** when there is any hard error.

Metric extraction (`35%`, `$2.4M`, `1M+`, `3x`, `7.7/10`, `5,000`) and technology extraction (from the lexicon, §9) run on each achievement and project text and fill `metrics` and `technologies`.

### 6.2 Identity of imported facts

Each imported fact stores `source.path`, `source.line` and `source.sha256` (hash of the exact source line, whitespace-normalized). Its match key for re-import is `(section, parent employer or project, normalized text hash)`.

### 6.3 The diff (deterministic)

Comparing the source to existing imported facts yields, in stable order (section order in the file, then line number):

| Result | Meaning |
|---|---|
| `unchanged` | a fact with the same key and text hash exists |
| `added` | a source line with no matching fact |
| `changed` | an existing imported fact in the same section and parent whose text is replaced by a new line with token overlap (Jaccard over lowercase word sets) of at least 0.6; ties break by file order |
| `removed` | an existing imported fact whose line is no longer in the source |

Identical inputs always produce byte-identical diff output. `--dry-run` (the default) prints the diff and writes nothing.

### 6.4 Applying the diff (`--apply`)

| Result | Fact status `claimed` | Fact status `confirmed` / `verified` | `origin: manual` |
|---|---|---|---|
| `unchanged` | nothing | nothing | not considered |
| `added` | create a `claimed` fact | — | — |
| `changed` | update text, facets and source; stays `claimed` | **not modified**; listed under "needs review" with the proposed new text | never touched |
| `removed` | mark `stale: true` (source line missing); not deleted | mark `stale: true`; not deleted | never touched |

Stale facts still support claims (they are real facts the user once stated) but `check` and `validate` report them, and the user decides to `retire` them. `--apply` needs `--yes` when there is no terminal, exactly like `migrate`.

### 6.5 Safety

- Runs under the workspace lock; all file writes are atomic.
- **Backup and manifest:** every existing file the import changes (`career/…` files and `career/sources.yaml`) is copied to `.careeros/backups/<UTC timestamp>-memory-import/` with a verified `manifest.json`, using the Foundation backup code (extracted to `careeros/core/backup.py` with no behaviour change).
- **Rollback:** if any write or the ledger append fails, changed files are restored, created files are removed, and the manifest is marked `rolled_back`.
- **Ledger:** one `memory.imported` event (counts per result, the backup folder, the source hash) plus one `memory.fact_added` or `memory.fact_updated` event per affected fact, appended in a single batch.
- `career/sources.yaml` records each imported source path, its whole-file sha256, the import time and the framework version; an import where the source hash equals the recorded one is an immediate no-op.

## 7. Memory operations

All of these run under the workspace lock, write atomically, append to the ledger, and restore files if the append fails (the Foundation `transition` pattern).

| Command | Effect | Needs an interactive terminal |
|---|---|---|
| `memory add --kind K …` | create a `manual`, `claimed` fact. `--quote "…"` is required: the user's own words, stored as the source (`user_statement`). Metrics and technologies are extracted from `--text`. | no |
| `memory add --kind evidence --supports ID… (--path P \| --url U) --note "…"` | create an `evidence` record (a document in the workspace or a link) that backs one or more facts; this is what `verify` needs | no |
| `memory update ID …` | change a fact's content | **yes** if its status is `confirmed` or `verified` (it resets to `claimed`); otherwise no |
| `memory confirm ID` | `claimed` → `confirmed`, sets `confirmed_at` | **yes** |
| `memory verify ID --evidence EVD` | `confirmed` → `verified` | **yes** |
| `memory dispute ID --reason …` | `disputed` | no for `claimed`; yes otherwise |
| `memory retire ID --reason …` | `retired`; the file stays, so ledger references stay valid | no for `claimed`; yes otherwise |
| `memory list [--kind K] [--status S]` / `show ID` | read | no |
| `memory status` | counts by kind and status, stale imports, unconfirmed share | no |

Ledger events (reserved: only these commands write `memory.*`): `memory.imported`, `memory.fact_added` (also used for evidence records), `memory.fact_updated`, `memory.fact_confirmed`, `memory.fact_verified`, `memory.fact_disputed`, `memory.fact_retired`. Each carries the fact ID as `entity`, the actor, and a `reason` where one is required. Retiring replaces deleting: **a fact referenced by the ledger is never physically deleted**, the same rule as jobs.

## 8. The checker

`careeros check PATH|- [--against JOB] [--allow TERM …] [--require-confirmed] [--record] [--json]`. `-` reads the draft from standard input.

### 8.1 What "supported" means

A draft is checked for **atoms**, the checkable claims in v1:

| Atom | Detected by | Supported when |
|---|---|---|
| `number` | a standalone numeric expression (`35%`, `1M+`, `$2.4M`, `3x`, `5,000`) that is not a year, not part of a URL/email/phone, not a version attached to a lexicon term, not list numbering | its normalized value and unit equal a `metrics` entry of a supporting record (defined below) |
| `year` | a four-digit year 1950–2100 | it lies within the date range of some experience, project, education or certification record |
| `duration` | `N years` / `N+ years` / `N-year` | for a generic claim: N is at most the whole years covered by the union of experience date ranges (derived, never stored). For a duration tied to a technology or skill (`5 years of Go`): never derivable, always `review` |
| `technology` | a lexicon term or alias (§9) | it appears among the skills, technologies of a record, or aliases of those |
| `employer` | a proper name after an employment cue (below) | it matches an experience `employer` (legal suffixes ignored) |
| `school` | a proper name after an education cue | it matches an education `school` |
| `certification` | a certification lexicon term, or a proper name after `certified`/`certification` | it matches a certification `name` |
| `title` | a role phrase after a self-description cue (below) | it equals an experience `title` or an identity `headline` (normalized) |

Cues are enumerated, not guessed:
- employment: `(worked|working|employed|interned|served|experience|role|position|tenure|time) … (at|for|with) NAME`, `(joined|left) NAME`, `ROLE-PHRASE (at|@) NAME`
- education: `(studied|graduated|degree|B.Tech|M.Tech|Bachelor|Master|PhD|MBA|diploma) … (at|from) NAME`
- self-description: `(as|I am|I'm|I was|was) (a|an|the) ROLE-PHRASE`

`NAME` is one to four capitalized tokens (letters, digits, `&`, `.`, `-`), optionally followed by a legal suffix. `ROLE-PHRASE` is up to four words ending in a role noun from the lexicon.

**Supporting records** are those with status `claimed`, `confirmed` or `verified`. `disputed` and `retired` records support nothing.

What the checker deliberately does **not** do: judge whether prose is true, compare wording or meaning, detect claims outside these atoms, or recognize technologies that are in neither the lexicon nor the memory. It reports how many sentences held no checkable claim, so coverage is visible.

### 8.2 Normalization

Case-folded, punctuation-collapsed, `&` = `and`, legal suffixes (`Inc`, `Ltd`, `LLC`, `Corp`, `GmbH`, `Pvt`, …) dropped for organization names; technology aliases mapped to canonical names through the lexicon (`k8s` → Kubernetes, `golang` → Go, `Postgres` → PostgreSQL); numbers normalized (`k`, `M`, `B`, `million`, `billion`, `%`/`percent`, thousands separators, a leading `~`/`about` ignored).

**Numbers match by equality** of normalized value and unit. The `+` qualifier is directional: a draft `+` needs a memory `+` for the same value; a memory `+` supports a draft without one. `2M` does not match a memory `1M+`.

### 8.3 Context terms

`--against JOB` makes the job's company and title *known names* for this check, so cover letters can name the employer they apply to. It does **not** add technologies or numbers from the job. A term that must be allowed anyway (a technology the company uses, a number from the job post) is passed with `--allow TERM`; each allowed term is listed in the output as an **unverified mention**, never as support, so a person can see exactly what was waved through.

### 8.4 Compound claims

Split the draft into sentences (sentence punctuation, newlines, bullets). For each sentence with atoms:

1. **Unsupported atom → `unsupported`.** Any atom with no supporting record fails with its specific code.
2. **Relationship check → `review`.** If the sentence has two or more distinct atoms, a single supporting **record** (an achievement, project or experience, with facets inherited from its parent experience: employer, title, date range) must contain **all** of them. If every atom is supported individually but no single record holds them together, the sentence fails as `review: relationship not represented`, and the finding names which atoms were found in which records.
3. **List exemption.** Technology (and skill) enumerations are exempt from step 2 among themselves: a line under a `Skills`/`Technologies`/`Tech stack` heading, or technologies in a list after `experience with`, `proficient in`, `skilled in`, `familiar with`, `such as`, `including`, `stack:` or `technologies:`. Each is still checked individually. An enumeration that also contains a number, employer, school or title is not exempt.

Why this works: an achievement record such as *"Reduced AWS costs by 35% using Kafka-based batching"* holds the metric and both technologies together, so a faithful restatement passes; "Reduced costs 35% with Kafka" built from a 35% in one achievement and Kafka in a skills list fails.

### 8.5 Output and exit codes

Each finding has a code, the sentence, the atom, a reason and a fix:

| Code | Meaning | Class |
|---|---|---|
| `CHK001` | number not in memory | unsupported |
| `CHK002` | year outside every dated record | unsupported |
| `CHK003` | technology not in memory | unsupported |
| `CHK004` | employer not in memory | unsupported |
| `CHK005` | school not in memory | unsupported |
| `CHK006` | title not in memory | unsupported |
| `CHK007` | certification not in memory | unsupported |
| `CHK008` | duration not derivable or above the derived total | unsupported / review |
| `CHK010` | atoms supported separately but not together | review |
| `CHK020` | supported only by `claimed` facts | info (a failure with `--require-confirmed`) |
| `CHK030` | career memory is empty or has no usable facts | fail closed |
| `CHK031` | a source file changed since it was imported (stale import) | warning |

Exit codes: **0** every checkable claim is supported (`CHK020`/`CHK031` may be printed); **1** at least one `unsupported`, `review` or `CHK030` finding (or `CHK020` with `--require-confirmed`); **2** usage error or invalid career memory (malformed files: the checker will not run against memory it cannot trust). `--json` prints the same facts as machine-readable data (`findings`, `supported_by_claimed`, `allowed_mentions`, `sentences_without_claims`, `memory_stale`) with nothing else on standard output.

`--record` appends a non-reserved `draft.checked` ledger event with the draft's sha256, the entity (`--against`), the verdict and the finding counts, so the audit trail shows which exact text passed.

### 8.6 The guarantee, stated once

Every successful run prints: *"Passed: every checkable claim (numbers, years, durations, technologies, employers, schools, titles, certifications) is supported by your career memory. Prose was not evaluated, and N supporting fact(s) are claimed rather than confirmed."* Documentation and skills use that wording and never say "verified" or "true" about a draft.

## 9. Lexicon

A bundled data file (`careeros/data/lexicon.yaml`, included in the package) holds: technology names with aliases and a `case_sensitive` flag for ambiguous short names (`Go`, `R`, `C`, `D`); role nouns; organization legal suffixes; certification names and abbreviations. The user may extend it with `career/lexicon.yaml` (additions only; removing a bundled entry is not supported). Skills present in memory are added to the vocabulary automatically, so a technology you list is recognized even if the bundle has never heard of it.

## 10. Validation integration

`careeros validate` gains memory rules (read-only):

| Code | Severity | Check |
|---|---|---|
| `MEM001` | error | a `career/` file is unreadable or its frontmatter is invalid |
| `MEM002` | error | missing required key, wrong ID prefix, or duplicate ID |
| `MEM003` | error | a `parent`, `supports` or `evidence` reference points to nothing |
| `MEM004` | error | invalid `status`, `origin` or `kind`, or a missing `source` |
| `MEM005` | error | `verified` without an existing evidence record, or `confirmed` without `confirmed_at` |
| `MEM006` | warning | a source file differs from its recorded hash (stale import) |
| `MEM007` | warning | an imported fact's source path no longer exists |
| `MEM008` | warning | a fact flagged `stale` |
| `LED003` | error (extended) | any ledger entity ID with a known prefix (`exp_`, `ach_`, `prj_`, …) whose file is missing; the fix says to retire, not delete |

## 11. CLI summary

New: `careeros memory import|add|update|confirm|verify|dispute|retire|list|show|status` and `careeros check`. All accept `--workspace`/`-w` and the `CAREEROS_WORKSPACE` variable. Exit codes follow Foundation (0 success, 1 failure or findings, 2 usage). Commands that only the user should run (`confirm`, `verify`, edits to confirmed facts) refuse without an interactive terminal and print how the user can run them (in Claude Code: `! careeros memory confirm <id>`). Every mutating command refuses a workspace newer than the installed CareerOS.

## 12. Skill integration

**The evidence gate**, one standard block added to each drafting skill and asserted by tests:

1. After the final edit to a draft (after `humanize`, after any rewrite) save the final text, then run `careeros check <draft> --against <job> --record` (text on standard input with `-` is fine).
2. If it exits non-zero, for every finding either **remove or rewrite** the claim, or **ask the user** whether the fact is true. If the user says yes, add it with `careeros memory add … --quote "<their words>"` and re-run. A `review` finding means rewriting the sentence so its atoms come from one record, or asking.
3. Re-run after every change. **Never show, send, paste or save a draft as final that has not exited 0.**
4. Tell the user the result in the standard wording (§8.6), including how many supporting facts are only claimed.

Skills and the text each gates: `prep` (the tailored resume and cover-letter angle), `apply` (the cover letter and every free-text application answer), `outreach` (LinkedIn notes and emails), `follow-up` (all three message templates), `humanize` (it rewrites text, so it must be followed by the gate and must not add facts), `interview` (STAR outlines and answers).

Also: a new `memory` skill (`.claude/skills/memory/SKILL.md`) with triggers such as "import my resume", "remember that …", "add this achievement", "what do you know about me", "confirm …", routed from `CLAUDE.md`; and `onboard` runs `memory import` after taking the resume.

GPT Work cannot run commands. This sub-project does not change the GPT skills, and the docs say that the evidence gate is Claude Code only for now.

## 13. Out of scope

- Interview stories, debriefs, decisions, learning, compensation, writing style (later phases).
- Job evaluation and requirement-to-evidence mapping (the evaluation phase will consume this memory).
- Semantic or model-based checking of prose.
- Detecting claims outside the atoms in §8.1.
- GPT Work parity.
- Migrating or editing the user's real workspace: acceptance runs on a copy.

## 14. Testing

Test-first, failure paths first. All tests use temporary workspaces.

- **Checker, unsupported atoms:** an invented metric, year, employer (each cue pattern), school, certification, technology (canonical name and alias), title, and a technology duration. Each exits 1 with the right code.
- **Checker, relationships:** a number and a technology each present but in different records → `review`; the same atoms in one achievement → pass; employer and metric from different experiences → `review`; a keyword present only in a skills list cannot back a metric claim; list exemption passes for plain enumerations and does not apply when a number is in the line.
- **Checker, paraphrase:** a reworded but atom-identical restatement of a supported achievement passes.
- **Status semantics:** claimed-only support passes and is reported; `--require-confirmed` fails it; `disputed` and `retired` facts support nothing; stale-source warning; empty memory fails closed (`CHK030`).
- **Normalization:** `35%`/`35 percent`, `1M`/`1,000,000`, the directional `+` rule, `k8s`/Kubernetes, `golang`/Go, case-sensitive `Go` versus the verb, versions attached to technologies, URL and phone digits ignored, year ranges.
- **Malformed and missing:** invalid YAML, missing keys, bad IDs, duplicate IDs, dangling references, a fact with no source, a source file that no longer exists: `check` exits 2 and `validate` reports the right `MEM` code.
- **Importer:** every grammar case; the real-shaped fixture; unknown sections reported; a hard error writes nothing; determinism (two runs give identical output); each diff result; `confirmed` facts never modified and listed for review; manual facts untouched; removed lines become `stale`, not deleted; second import is a no-op; stale-source detection.
- **Atomicity:** import and each memory command restore every file when the ledger append fails; the manifest is `rolled_back`; the ledger chain stays valid.
- **Guards:** `confirm`, `verify` and edits of confirmed facts refuse without a terminal; reserved `memory.*` types refused by `ledger append`; a workspace newer than the installed version is refused.
- **Ledger:** every operation produces its event(s); `--record` writes `draft.checked` with the draft hash.
- **Skills:** the evidence-gate block and its key sentences appear in each of the six drafting skills; the `memory` skill and its `CLAUDE.md` routing exist; refresh delivers them.
- **End to end:** workspace with a `resume.md` → import → confirm one fact → faithful draft passes → tampered draft fails with the expected findings → retire a fact → the draft that used it now fails.
- Existing 230 tests keep passing; CI and `mkdocs build --strict` stay green.

## 15. Risks and limits

- **Recall is bounded.** A claim phrased outside the enumerated cues, or a technology unknown to the lexicon and the memory, is not detected. The output reports how much it examined; the docs say so plainly.
- **Precision costs.** Conservative rules produce false positives (a generic self-description, a number quoted from a job post). The escape hatches are visible: add the fact to the memory, or `--allow` a term, which shows up as an unverified mention.
- **Heuristic parsing of `resume.md`** covers the structure it has today; other resume layouts need grammar additions, and unrecognized structure is reported, not guessed.
- **`claimed` is not `true`.** A fact that came from your own resume can be wrong; the checker only ensures drafts do not exceed it.
- **Terminal guard is not a security boundary,** as for `approve`: an agent with unrestricted shell access could answer the prompt. The ledger records who did what.

## 16. Acceptance

On a temporary copy of the real workspace (the real one is never modified; committed records contain counts and placeholder names only):

1. `memory import --dry-run` then `--apply` produces facts from `resume.md`; `resume.md` is byte-identical; a second import is a no-op; the backup and manifest verify.
2. A faithful draft built from the memory passes; drafts with an invented metric, employer, technology and an unrepresented relationship each fail with the right code and exit 1.
3. The ledger contains the import and fact events and `ledger verify` passes; `validate` shows no new errors.
4. The full suite passes locally and on Python 3.11; `mkdocs build --strict` passes.
5. `docs/END_TO_END_TEST.md` is extended with this run.

## 17. Build order

1. Extract backup helpers into `core/backup.py` (no behaviour change); career file model and CRUD.
2. Lexicon and normalization.
3. Importer: parser, diff, apply, rollback.
4. Checker core, relationships and output.
5. CLI: `memory …` and `check`; ledger prefix and validation rules.
6. Skill integration: the evidence gate in six skills, the `memory` skill, routing, docs, acceptance.
