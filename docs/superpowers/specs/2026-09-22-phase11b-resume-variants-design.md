# Phase 11b — Job-Tailored Resume Variants

**Date:** 2026-09-22
**Status:** Approved, ready for implementation planning
**Builds on:** Phase 11a (evidence-backed resume ingestion), shipped at `1b7e66a`

## Why

`apply` uploads the same resume to every application. The selection is
`sorted(storage.list("resumes/versions/"))[-1]` — the last filename
alphabetically, chosen without reference to the job. A tailored resume reads
better to a human and matches an ATS keyword filter more often, and the
material to tailor from already exists: Phase 11a made every stored skill carry
an `Evidence` record whose quote was deterministically located in the resume.

Phase 11b was split out of Phase 11 because it needed a decision 11a did not:
`apply` hands a filesystem path to the ATS form uploader, so a variant has to be
an uploadable `.pdf`, and the project had no document renderer. That question is
settled here.

## Scope

### In

- A pure selection skill that, given a job description and the master resume,
  returns an ordered set of **verbatim** spans of that resume, each verified by
  `verify_quote`.
- A `ResumeVariant` model and a `variant.json` sidecar recording which line of
  `resumes/master.md` backs every line of the rendered document.
- An HTML renderer (pure) and a PDF renderer (Chromium) producing an uploadable
  `resume.pdf`.
- `careeros resume variant --job <id>` to generate and store a variant.
- Explicit, job-keyed resume selection shared by `apply` and
  `discover-and-apply`, replacing the duplicated alphabetical scan.
- The approval screen stating which resume is being uploaded and whether it is
  tailored.

### Out

Each of the following is a phase of its own if wanted, and none is required by
the exit condition:

- **`.docx` output.** `.pdf` satisfies the uploader's extension allowlist.
- **Page-count control and pagination tuning.** Chromium paginates; we do not
  try to guarantee a one-page result.
- **A reusable variant library with fuzzy job matching.** Per-job generation
  makes selection an exact lookup. A library would need a matching subsystem
  and would deliver a weaker guarantee.
- **ATS keyword scoring.** Measuring a variant's keyword coverage is a
  different feature from producing one.
- **Editing `master.md` from a variant.** The master is the source of truth and
  this phase only reads it.
- **Rephrasing resume content for a job's vocabulary.** Rejected on the merits;
  see "Rejected alternatives" below.

### Rejected alternatives

- **Rephrasing bullets with tracked provenance.** Reads best and matches
  keywords hardest, but nothing rewritten is verbatim-verifiable, so the
  deterministic guard disappears and the audit trail degrades into the model's
  own assertion about its own output — precisely what 11a was built to prevent.
- **A generated summary paragraph alongside verbatim bullets.** Would leave one
  section of the document carrying a weaker guarantee than the rest, requiring
  the sidecar to distinguish them and the renderer to visibly mark the
  difference. Deferred rather than refused: it is additive later.
- **A new document dependency** (`reportlab`, `python-docx`, `weasyprint`).
  Chromium is already in the dependency tree; `weasyprint` in particular drags
  in native system libraries that complicate installation.
- **No renderer, tailoring by selection among user-supplied files.** Does not
  meet the roadmap's "generate a job-tailored resume variant" — it only chooses
  among documents the user already wrote.
- **`apply` generating a variant on demand.** Adds an LLM call and a Chromium
  render to an already-interactive flow, and `discover-and-apply` runs
  unattended — it would silently generate and upload documents the user never
  saw.
- **Variants stored flat in `resumes/versions/`.** `StorageProvider.list` is
  `rglob`-based, so this would make one job's variant the alphabetical winner
  for every other job.

## Mechanism

### Storage layout

```
resumes/
  master.md                      # unchanged; 11a's ingest source
  versions/
    my-resume-2026.pdf           # user-supplied files: the untailored fallback pool
    <job_id>/
      resume.pdf                 # the tailored variant apply uploads
      variant.json               # evidence sidecar
```

The roadmap's exit condition names `resumes/versions/` and this preserves it.
Per-job subdirectories keep tailored output out of the fallback pool.

### Selection

`careeros/core/resume_select.py` exposes
`select_resume(storage, job_id) -> ResumeChoice`, where `ResumeChoice` carries
the absolute path, a `tailored: bool`, and the `ResumeVariant | None`. Order:

1. `resumes/versions/<job_id>/resume.pdf` exists → use it, `tailored=True`.
2. Otherwise, **top-level files only** in `resumes/versions/` whose name ends
   in `.pdf` or `.docx`, `sorted()[-1]`, `tailored=False`.
3. Otherwise, no resume: the caller errors as it does today.

Step 2's top-level filter is load-bearing and fixes a latent defect present
before this phase: because `list` is recursive, any subdirectory under
`versions/` already pollutes the alphabetical scan.

`apply_cmd.py` and `discover_and_apply_cmd.py` both call this, replacing the
block duplicated at `apply_cmd.py:71` and `discover_and_apply_cmd.py:208`, and
the `_RESUME_EXTENSIONS` constant duplicated at `apply_cmd.py:30` and
`discover_and_apply_cmd.py:28`.

### Generation

One LLM call. Instructions in the `system` message; the job description and the
master resume in the `user` message, each passed through `wrap_untrusted()`.
The JD is scraped content and the resume is a file from disk — 11a treats both
as untrusted and that does not change.

The model returns only a selection:

```json
{"sections": [{"heading": "Experience", "quotes": ["...", "..."]}]}
```

Three deterministic guards run on that output, in order:

1. **Heading allowlist.** `Summary`, `Experience`, `Skills`, `Education`,
   `Projects`. A heading is rendered text on the document, so free-text
   headings are a fabrication surface like any other — nothing otherwise stops
   a model emitting "Perfect Match For This Role". A section whose heading is
   not in the list is dropped and named.
2. **`verify_quote`, unchanged.** A quote that returns a line becomes an
   `Evidence` record; a quote that returns `None` is dropped and named.
3. **Duplicate collapse**, matched on the same normalized form `verify_quote`
   uses. 11a's I3 finding was exactly this mismatch, reporting one skill as
   both stored and dropped.

The contact header — name, email, title, location — is read from `profile.json`
and rendered deterministically. The model never writes the part of the document
that identifies the user.

`skills` is passed as a hint, not a source. It supplies the names of skills
already carrying verified `Evidence`, so the selector can favour spans that
evidence a skill the job description asks for. It can never contribute text:
every quote must still be a verbatim span of the capped master resume, and
`skills.json` is derived from that same file. A variant generated with an empty
`skills.json` is therefore valid, just less well prioritised.

`select_variant_content(jd_text, master_text, skills, model=None)` carries the
same never-raises contract as 11a's `ingest_resume`, **with the adversarial
tests written as part of this phase rather than deferred.** 11a shipped a
Critical because `{"skills": null}` escaped its per-candidate guard and crashed
a caller that trusted that contract; the deferred minor "no test exercises the
unguarded crash paths" was where it lived. Required cases: null payload,
payload not a dict, `sections` absent or not a list, a section that is not a
dict, `quotes` not a list, a quote that is not a string, a heading that is not
a string, and non-JSON model output.

### Constants

- `_MASTER_CAP = 8000`. 11a caps ingest content at 4000, roughly one page; a
  two-page resume would have its tail invisible to the selector. As in 11a,
  **verification runs against exactly the capped text the model saw**, never the
  full file.
- `MAX_QUOTE_CHARS = 200` is reused from `resume_evidence.py` unchanged, which
  means a bullet longer than 200 characters cannot enter a variant. This is a
  deliberate trade — inheriting 11a's tested code path rather than
  parameterizing it — and a **known limitation**: such a bullet is dropped and
  named, and the remedy is shortening it in `master.md`.

### Rendering

`careeros/render/resume_html.py` is pure:
`build_resume_html(variant, profile) -> str`. Every quote and every profile
field passes through `html.escape()`; verbatim resume text becomes markup here,
and a resume containing `C++ & <legacy> tooling` must not produce broken
output. The template uses inline CSS with **no external asset references**, so
rendering never touches the network.

`careeros/render/resume_pdf.py` is deliberately thin:
`render_pdf(html, out_path)` runs `chromium.launch(headless=True)`,
`set_content(html)`, `page.pdf(format="Letter", ...)`, close.

- **Never `launch_persistent_context`.** That is the profile Phase 9c isolated;
  rendering through it would contend with a live browsing session and
  reintroduce `BrowserProfileBusy`.
- **`set_content` only, never `goto`.** No URL is loaded.
- **Missing Chromium raises a typed `RendererUnavailable`** carrying the
  `playwright install chromium` hint. Playwright browsers are not installed by
  `pip install`, so this is the expected first-run path, not an edge case, and
  must not surface as a raw Playwright traceback.

The rendered PDF carries no provenance annotations. Line numbers and dropped
lists live in the sidecar; the document an employer sees is clean.

Splitting HTML generation from the Chromium call is a testability decision:
browser-dependent tests are `integration`-marked and skipped by default, so a
combined unit would leave all layout logic untested.

### The `variant` command

`careeros resume variant --job <id>`:

1. Requires a workspace and an existing job record.
2. Reads `resumes/master.md`; a missing master is a clear error naming
   `careeros resume ingest`.
3. Calls `select_variant_content`.
4. Writes `resumes/versions/<job_id>/resume.pdf`, **then** `variant.json`.
5. Prints sections, entries kept, every dropped quote and heading, and the
   output paths.
6. Records one activity event on every path, success or failure.

Write order matters: selection keys off the PDF, so a torn write degrades to
"variant works, audit missing" rather than "sidecar promises a file that is not
there." Two non-atomic writes cannot be made atomic without a staging
directory, which this phase does not add.

Running the command again for a job that already has a variant regenerates it,
overwriting both files. Regeneration is not a merge and keeps no history — the
master resume is the source of truth and a variant is reproducible from it. The
overwrite happens only on the success path: per the failure table, an LLM
error, zero verified spans, or a missing Chromium all leave an existing variant
exactly as it was.

### Failure table

| Condition | Behaviour |
|---|---|
| No workspace | Error naming `careeros onboard`; exit 1 |
| Job id unknown | Error; exit 1 |
| `resumes/master.md` missing | Error naming `careeros resume ingest`; exit 1 |
| LLM call fails | Report the error distinctly from "nothing verified"; write nothing; exit 1 |
| Zero spans verified | Refuse to write a variant at all — an empty resume is worse than an untailored one; leave any existing variant untouched; exit 1 |
| Chromium missing | `RendererUnavailable` with the install hint; write nothing; exit 1 |
| Some quotes dropped | Write the variant; name every dropped quote and heading |
| `apply` finds a variant | Use it; state that it is tailored to this job |
| `apply` finds none | Use the fallback; state plainly that it is **not** tailored |

The distinction between "the LLM call failed" and "nothing verified" is
required, not optional: 11a shipped an Important because those two were
indistinguishable to the user, so a bad API key read as a resume problem.

## Testing

| File | Covers |
|---|---|
| `tests/test_resume_variant.py` | Selection, all three guards, dropped reporting, and the full adversarial-payload totality suite |
| `tests/test_resume_html.py` | Escaping, heading allowlist rendering, contact header sourced from `Profile`, absence of external asset references |
| `tests/test_resume_pdf.py` | `RendererUnavailable` when Chromium is absent (mocked); a real render, `integration`-marked |
| `tests/test_resume_select.py` | Exact lookup, top-level-only fallback including the subdirectory-pollution regression, no-resume error |
| `tests/test_resume_cmd.py` | The new subcommand: happy path, zero-verified refusal, LLM error, render failure, activity events on every path |
| `tests/test_apply_cmd.py`, `tests/test_discover_and_apply_cmd.py` | Tailored variant used when present; explicit untailored notice when absent |

No test may assert only that a call did not raise.

## Migration

None. `ResumeVariant` is a new model in a new file, directories are created on
demand, and existing `resumes/versions/*.pdf` keep working through the
fallback. No existing stored file changes shape.

## Exit condition

`careeros apply --job <id>` uploads a resume tailored to that job rather than
the alphabetically-last file, and states which it used.
`cat resumes/versions/<job_id>/variant.json` shows which line of
`resumes/master.md` backs every line of the rendered document.
