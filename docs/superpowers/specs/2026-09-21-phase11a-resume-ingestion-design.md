# Phase 11a — Evidence-Backed Resume Ingestion Design

## Why

`onboard` extracts skills with a single prompt that returns bare strings:

```python
{"skills": [{"name": "<skill name>", "level": "...", "source": "resume"}]}
```

`source` is the literal word `"resume"` for every skill, and `last_used` is never
populated at all. Nothing connects an extracted skill to anything in the resume, so
nothing can distinguish a skill the candidate actually has from one the model inferred,
guessed, or invented. Every downstream consumer — job scoring, cover letters, outreach
drafting — treats that list as fact.

That is a different risk from the one Phase 9b closed. Prompt injection is an attacker
putting words in the model's mouth; this is the model putting words in the *user's* mouth,
and the output goes to employers under the user's name.

## Scope

This is **Phase 11a**. The ROADMAP's Phase 11 contained two subsystems with different
dependency and risk profiles, and they are split:

- **11a (this spec):** evidence-backed deep ingestion and `careeros resume ingest`. No new
  dependency, no generated document. Improves job scoring, cover letters, and outreach
  immediately, since all three read the skill set today.
- **11b (next):** job-tailored resume variant generation. Deferred because it requires a
  decision 11a does not: `apply` hands a file path to `set_input_files()`, so a "variant"
  must be an uploadable `.pdf`/`.docx`, and the project has no renderer
  (`typer`, `rich`, `litellm`, `pydantic`, `playwright`). That question deserves its own
  spec rather than riding along with an extraction task.

**In:**

- An `Evidence` record and a `verify_quote` function — pure, no I/O
- `Skill.evidence`, nullable, no migration
- `ingest_resume()` — one LLM call, then deterministic verification
- `careeros resume ingest [PATH]`
- `onboard` switches to the same extractor for skills

**Out (rejected, with reasons — so they are not relitigated):**

- **Recorded citations instead of verified ones.** Storing whatever source the model
  reports is simpler and captures skills implied across a resume rather than quotable from
  one line. Rejected: the citation would be an LLM claim about an LLM extraction, so a
  fabricated skill arrives with a fabricated citation indistinguishable from a real one.
- **A second, unverified tier.** Keeping inferred-but-unquotable skills in a separate
  lower-confidence list loses nothing. Rejected: it recreates the ambiguity verification
  exists to remove, and the confidence field gets ignored by the first consumer that finds
  it inconvenient.
- **Chunk-first attribution.** Splitting the resume into sections and extracting per
  section makes the source structural rather than claimed. Rejected: one LLM call per
  section, coarser evidence (section, not line), and it still cannot catch a skill invented
  *within* a correctly-attributed section — the likelier failure.
- **A separate citation pass.** Extract, then ask the model to quote each skill. Rejected:
  doubles the cost and buys nothing, since the citing call fabricates as easily as the
  extracting one and the deterministic check is still required underneath.
- **Fuzzy or partial quote matching.** Rejected explicitly — see *The verifier*.
- **Multi-resume accumulation.** `resumes/master.md` is the master design spec's declared
  source of truth (§4.2). One resume, replaced on re-ingest.
- **PDF/DOCX parsing for input.** `master.md` is Markdown and `onboard` already rejects
  binaries.
- **A skill taxonomy.** Whatever `category` the model returns is stored as-is.

## Mechanism

### The evidence record and the verifier

New module `careeros/skills/resume_evidence.py` — pure, no I/O, no LLM, no storage:

```python
@dataclass(frozen=True)
class Evidence:
    quote: str        # verbatim from the source
    line: int         # 1-indexed line the quote was found on
    source_file: str  # "resumes/master.md"


MAX_QUOTE_CHARS = 200


def verify_quote(quote: str, source_text: str) -> int | None:
    """Return the 1-indexed line where `quote` occurs, or None."""
```

**Normalization is whitespace collapsing and case-folding, and nothing else.** No fuzzy
matching, no partial matching, no stemming, no regex. Phase 10 learned this twice at real
cost: the normalization that makes matching forgiving is exactly what admits false matches.
Here a false match is a fabricated skill wearing a citation identical to a real one.

Two guards that would otherwise let verification pass while meaning nothing:

1. **Plain containment, never a regex.** Resumes are full of `C++`, `.NET`, and
   `(2018–present)`. Compiled as a pattern, those either raise or match wrongly.
2. **A quote longer than `MAX_QUOTE_CHARS` is rejected as evidence.** An unbounded quote
   verifies trivially — return the whole resume and every skill "checks out". Evidence that
   cites everything cites nothing.

A quote occurring more than once resolves to its first line.

`Skill` gains one field in `careeros/core/models.py`:

```python
    evidence: Evidence | None = None
```

Nullable with a default, so every existing `profile/skills.json` loads unchanged and no
migration is required — the same property Phase 10's `sightings` relied on.

**A caveat worth stating rather than burying.** Nullability is what avoids the migration,
but it means a consumer cannot infer from the type that evidence exists. The guarantee
comes from the *writer* — after this phase there is exactly one extractor and it drops
unverified skills — not from the schema. That is convention, not structure, the same
caveat the `JobStore` seam carries. A required field would force a migration over a field
nothing reads yet; that trade is deliberate, not overlooked.

`Skill.source` and `Skill.last_used` already exist and finally mean what their names say:
`source` becomes the source file, `last_used` the inferred recency.

### The extractor

New module `careeros/skills/resume_ingest.py`:

```python
@dataclass(frozen=True)
class IngestResult:
    skills: Skills            # verified only
    dropped: tuple[str, ...]  # names whose quote could not be located


def ingest_resume(
    resume_text: str, source_file: str, model: str | None = None
) -> IngestResult: ...
```

One LLM call in the house shape established by Phase 9b: instructions in a `system`
message, the resume wrapped in `wrap_untrusted()` in a `user` message. A resume is
user-supplied but not necessarily user-*authored* — `resume ingest <path>` will read any
file it is pointed at, including one that arrived by email. Treating it as untrusted costs
one function call.

(Note that `profile_extract.py` was explicitly carved out of Phase 9b and still
concatenates resume text unwrapped for the *profile* half. That half is unchanged here;
this spec does not silently fix it, and it remains a known carve-out.)

The model returns candidates carrying `name`, `quote`, and an inferred `last_used`. Each
candidate passes through `verify_quote`. Verified candidates become `Skill` records with
populated `Evidence`; the rest land in `IngestResult.dropped`.

Candidates are deduplicated by normalized name, keeping the first verified occurrence.

**Input is capped at 4000 characters**, the value every other skill module uses. Each such
module declares its own `_CONTENT_CAP`/`_JD_CAP` constant rather than sharing one, so
`resume_ingest.py` declares `_CONTENT_CAP = 4000` locally, matching the house pattern. The
cap is inherited in spirit, not added by this phase — `extract_basic_profile` was already
given one during the Phase 9 sweep.

The consequence is worth stating plainly: content past the cap cannot be cited, so it
cannot produce skills. A very long CV loses its tail.

**`last_used` is inferred and deliberately unverified.** A date is an inference across
lines — "Kubernetes" quoted under `SRE — MegaCorp (2018–present)` implies current use —
not a quotable string co-located with the skill. It is a soft ranking input, never a
factual claim submitted to an employer. The quote remains the hard guarantee.

### The command

New module `careeros/cli/resume_cmd.py`, registered as `careeros resume`:

- `careeros resume ingest` re-ingests `resumes/master.md`.
- `careeros resume ingest <PATH>` copies that file to `resumes/master.md` first, so exactly
  one source of truth persists, then ingests it.

It writes `profile/skills.json`, logs a `resume_ingested` activity event carrying the
verified and dropped counts, and prints both — including the dropped names. A user should
see what the guarantee cost them rather than silently receiving a thinner profile.

**The command requires a workspace**, following the rule Phase 9c established: a workspace
is required exactly when writing to the activity log. This command writes both the skill
file and an audit event, so it resolves storage through the same `_get_storage` helper
every other CLI module defines locally, and exits 1 with the standard
"Run 'careeros onboard' first" message when none is configured.

### Onboard

`onboard` calls `ingest_resume` for skills. `extract_basic_profile` keeps its profile half
(name, title, years, location, summary) and loses its skills half — that half is not skill
evidence and nothing in this phase improves it. The function ends up honest about what it
does.

That is a **signature change**, not just an internal one:

```python
def extract_basic_profile(resume_text: str, model: str | None = None) -> Profile
```

It previously returned `tuple[Profile, Skills]`. `onboard` is its only caller in
`careeros/`, but `tests/test_profile_extract.py` and `tests/test_onboard.py` both unpack
the tuple and must be updated. Its second LLM call — the skills prompt — is deleted
outright rather than left unused, along with `_SKILLS_INSTRUCTIONS`.

Onboarding's sequence becomes: write `resumes/master.md`, call `extract_basic_profile` for
the profile, call `ingest_resume(resume_text, "resumes/master.md")` for the skills, then
display both. Two LLM calls, as today.

Consequence, accepted: a resume that verifies poorly produces a sparse profile at first
run. That is the honest outcome of the guarantee, and `resume ingest` is the remedy — but
it will be some user's first experience of the tool.

### Failure handling

Deliberately asymmetric. Onboarding must never be blocked by extraction quality.

| Condition | `careeros resume ingest` | `onboard` |
|---|---|---|
| LLM call fails or returns unparseable JSON | Report, exit 1 | Warn, continue with an empty skill set |
| Zero skills verified | Report dropped names, exit 1 | Warn loudly, continue |
| File missing, unreadable, or non-UTF-8 | Exit 1 naming the path | Already handled |

`ingest_resume` itself never raises; it returns an empty `IngestResult` on any LLM or parse
failure, matching the sentinel pattern every other skill in this codebase uses and the
behaviour `extract_basic_profile` already has. The command turns an empty result into a
non-zero exit; onboarding does not.

## Testing

All tests offline; no network. `litellm.completion` is mocked at the boundary. The suite is
at 496 passed, 6 skipped before this phase.

- `tests/test_resume_evidence.py` — the centre of gravity, and pure, so it is cheap to be
  thorough:
  - an exact quote resolves to its 1-indexed line; a quote on line 1 returns 1, not 0
  - whitespace differences and case differences still match
  - a quote absent from the source returns `None`
  - **a quote containing regex metacharacters (`C++`, `.NET`, `(2018–present)`) matches
    literally** — the guard against pattern compilation
  - **a quote longer than `MAX_QUOTE_CHARS` is rejected even when it genuinely occurs** —
    the guard against citing the whole document
  - a quote occurring twice returns the first line
  - empty quote and empty source both return `None`
  - `Evidence` is frozen
- `tests/test_resume_ingest.py`:
  - a model response whose quotes all verify produces that many skills and no drops
  - **a fabricated skill — one whose quote does not appear in the resume — is dropped, and
    its name appears in `dropped`.** This is the test the phase exists for
  - a mix of real and fabricated candidates keeps exactly the real ones
  - `last_used` is populated from the model without verification
  - duplicate names are collapsed, keeping the first verified occurrence
  - input is truncated at `_CONTENT_CAP`, so a skill quotable only past the cap is dropped
  - an LLM exception and an unparseable response each yield an empty `IngestResult` rather
    than raising
  - the call uses a `system` + wrapped-`user` message pair, not a single concatenated
    prompt
- `tests/test_resume_cmd.py`:
  - `ingest` with no argument reads `resumes/master.md`
  - `ingest <PATH>` copies the file to `master.md` and ingests the new content
  - `profile/skills.json` is written with evidence populated
  - a `resume_ingested` activity event records verified and dropped counts
  - zero verified skills exits non-zero and names the dropped skills
  - a missing path exits 1 naming the path
- `tests/test_onboard.py` — onboarding completes with a sparse skill set when nothing
  verifies, rather than failing; the profile half is still populated.
- `tests/test_job_model.py` — a `skills.json` written before this phase, with no `evidence`
  key on any skill, still loads.

## Migration note

**No workspace migration.** `Skill.evidence` defaults to `None`, so existing
`profile/skills.json` files load unchanged. Their skills simply carry no evidence until the
user runs `careeros resume ingest`, at which point the file is replaced wholesale by
verified entries.

`resumes/master.md` is unchanged in format and location. Workspaces created before this
phase need no action.
