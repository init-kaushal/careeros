# Phase 10 — Job Source Connectors + Deduplication Design

## Why

Four code paths create `Job` records. Exactly one of them deduplicates:

| Path | Dedup today |
|---|---|
| `cli/discover_and_apply_cmd.py:148` | exact case-insensitive `(company, title)` stopgap |
| `cli/browse_cmd.py:154` (interactive discovery) | none |
| `cli/job_cmd.py:326` (`job search`, the ATS API path) | none |
| `cli/job_cmd.py:111` (`job add`, manual) | none |

The stopgap landed in Phase 9a to stop the most damaging case — a scheduled run
re-submitting a real application to the same employer on every tick — and Phase 9c's
review closed a hole in it. But it covers one path, matches titles exactly, and lives
inline in the command that happens to need it. The same posting saved from `browse` and
from `discover-and-apply` still produces two records, and a posting re-listed as "Senior
Site Reliability Engineer" after being saved as "Senior SRE" still produces two.

Separately, every discovery path is browser scraping, which breaks silently when markup
changes. `careeros/sources/ats.py` already contains working public-API fetchers for
Greenhouse *and* Lever — they are simply not behind a Protocol and are reachable only from
a manual per-company command, never from the unattended run that would benefit most from a
path that cannot be broken by a markup change.

## Scope

**In:**

- A `JobSource` Protocol and a shared `Posting` record, parallel to the existing `Scraper`
  Protocol
- Greenhouse and Lever `JobSource` implementations over the existing fetch functions
- A deduplication engine: canonical-URL match, else normalized company/title/location match
- A `JobStore` creation seam that all four creation paths route through
- Enrich-on-merge with a `sightings` record of where a posting has been seen
- `config/sources.json` extended with board entries, and wired into `discover-and-apply`

**Out (rejected, with reasons — so they are not relitigated):**

- **LLM-based title matching.** Would catch the most variants. Rejected on principle:
  Phase 9's entire thesis is "deterministic policy, not LLM policy" (master spec §2.6), and
  letting a model decide whether to submit a second real application reintroduces exactly
  the class of problem 9a and 9b closed.
- **Token-set similarity with a tuned threshold.** Catches reordered and padded titles, but
  the threshold has no principled value, and a false positive collapses two genuinely
  different roles into one record — meaning the second is silently never applied to, with
  no artifact showing it happened. Deterministic normalization errs the other way, toward a
  visible duplicate.
- **A similarity matcher behind a config flag.** Defers the decision rather than making it,
  and still requires picking a default.
- **One unified Protocol with scrapers adapted onto it.** Conceptually tidier, but scrapers
  need a live Playwright `Page` for the duration of a fetch and API sources need no browser
  at all. The adapter would exist only to hide that mismatch, at the cost of refactoring
  working, recently-reviewed scraper code.
- **A separate `careeros sources sync` command.** Smallest blast radius, but the ROADMAP's
  stated value is a non-scraping discovery path *because* scraping is fragile, and a path
  unattended runs cannot use does not deliver that. It also recreates two dedup entry points.
- **`browse --source greenhouse --board stripe`.** `browse --board` already means a browser
  board (`linkedin|indeed|wellfound|url`); overloading it is a trap. The interactive API
  path already exists as `job search`.
- **Retry or backoff on a failed fetch.** `_get_json` already has a 10s timeout and typed
  errors; a scheduled run retries on its next tick; retrying into a rate limit is how a
  board starts refusing you.
- **A migration for `config/sources.json`.** See *Migration note*.
- **A source registry or plugin system.** A dict literal, as with Phase 9c's `BOARDS`, is
  the proven house pattern.

## Mechanism

### The `Posting` record and the `JobSource` Protocol

New module `careeros/sources/base.py`:

```python
@dataclass(frozen=True)
class Posting:
    source: str              # "greenhouse" | "lever" | "linkedin" | ...
    title: str
    company: str
    url: str                 # absolute, always
    location: str | None = None
    description: str | None = None
    source_id: str | None = None


class JobSource(Protocol):
    name: str
    def fetch(self, board: str) -> list[Posting]: ...
```

`Posting` is a frozen dataclass rather than a dict, matching Phase 9c's `Board` pattern and
for the same reason: the two current producers disagree silently. Scrapers emit
`source_board/title/company/location/url`; `ats.py` emits `source_id/title/url/location/
description` with **no company**. A typed record turns that mismatch into a construction
error instead of a `KeyError` three layers downstream.

**The company gap.** Greenhouse's `/boards/{slug}/jobs` response never returns the company
name — it is implied by the slug requested. Deriving it (`"stripe"` → `"Stripe"`) would be
a guess, and `company` is half the dedup fingerprint, so a derived name that differs
between sources defeats cross-source dedup entirely. The `sources.json` entry therefore
carries it explicitly.

**Module layout** keeps transport separate from the Protocol:

| Module | Responsibility |
|---|---|
| `careeros/sources/ats.py` (existing) | HTTP + JSON transport, `ATSFetchError`, HTML stripping |
| `careeros/sources/base.py` (new) | `Posting`, `JobSource`. No I/O |
| `careeros/sources/greenhouse.py`, `lever.py` (new) | Thin `JobSource` implementations |

`Scraper` is untouched. A single `posting_from_scrape(dict) -> Posting` adapter lives with
the scrapers and is the only surviving use of the old dict shape — one call site to delete
when those dicts are eventually retired.

`careeros/sources/base.py` also holds `job_from_posting(posting: Posting, now: str) -> Job`,
the one place a `Posting` becomes a `Job`. It assigns the id via the existing
`make_job_id(company, title)` and sets `stage="saved"`, `created_at`/`updated_at` to `now`,
and `sightings=[]`. Note that `make_job_id` is random-suffixed and therefore **not** an
identity: two calls for the same posting produce different ids. That is precisely why
identity is decided by `is_same_posting` against stored records rather than by the id, and
why `JobStore.save_new` must run before the id is ever persisted.

Lever ships alongside Greenhouse because `fetch_lever` already works; the marginal cost is
a second five-line class.

### Deduplication

New module `careeros/core/dedup.py` — pure functions, no I/O:

```python
def canonical_url(url: str) -> str: ...
def normalize_title(title: str) -> str: ...
def normalize_location(loc: str | None) -> str | None: ...
def is_same_posting(candidate: Job, existing: Job) -> bool: ...
```

**Two-tier match, in order:**

1. **Canonical URL equality.** Two records pointing at the same posting *are* the same
   posting, however the title is worded.
2. **Normalized company + title + location.**

`canonical_url` lowercases scheme and host, drops the fragment, strips a trailing slash, and
removes a **denylist** of tracking parameters (`utm_*`, `gh_src`, `trk`, `refId`,
`originalSubdomain`). Deliberately a denylist, not "strip all query parameters": some ATS
links carry the job ID in a query parameter, and blanket-stripping would collapse every
posting on such a board into a single record — the catastrophic direction.

`normalize_title` lowercases, strips punctuation, collapses whitespace, then expands a
curated abbreviation map on **whole tokens only** (so "engineering" is never mangled):

```python
_ABBREVIATIONS = {
    "sr": "senior", "jr": "junior", "eng": "engineer",
    "sre": "site reliability engineer", "swe": "software engineer",
    "mgr": "manager", "dev": "developer",
}
```

Every miss is one map entry away from being fixed.

**Location is a wildcard when absent.** Location varies across boards more than titles do —
the same job is "Remote" on one board and "San Francisco, CA" on another. A null or empty
location matches anything, so a scraper sighting without a location merges into a Greenhouse
record that has one. Two records that both carry *differently worded* non-empty locations
stay separate.

This is a real, accepted limitation: "Remote" vs "San Francisco, CA" for one job will
duplicate. It is the recoverable direction — a visible duplicate you can delete, rather than
two distinct roles silently collapsed into one you then never apply to.

**No stored fingerprint field.** `is_same_posting` computes from existing fields at check
time. A stored fingerprint would be silently invalidated by every change to the
normalization map, and a stale fingerprint is worse than none because it looks
authoritative. The cost is an O(n) scan of `Job.list_all()` per check — negligible for a
personal job list, and `discover-and-apply` already loads all jobs for its current stopgap.

### The `JobStore` creation seam

New module `careeros/core/job_store.py`:

```python
@dataclass(frozen=True)
class SaveOutcome:
    job: Job
    created: bool                      # False => merged into an existing record
    enriched: tuple[str, ...]          # field names the merge actually filled


class JobStore:
    def __init__(self, storage: StorageProvider) -> None: ...
    def save_new(self, job: Job, *, force: bool = False) -> SaveOutcome: ...
```

One method, one check. Discovery paths build a `Job` from a `Posting` via
`job_from_posting()`; `job add` builds one from prompts. Both then call `save_new`.

**Creation only.** `apply_cmd`, `job update`, and `job note` update a known job and keep
calling `job.save(storage)` directly — routing them through dedup would be nonsense, since
a job always matches itself.

**Merge behaviour.** On a match, `save_new` enriches: for each of `location`, `description`,
`salary_min`, `salary_max`, `remote`, `requirements`, `source_id`, if the existing value is
"absent" and the incoming one is not, fill it. A present value is never overwritten.
"Absent" is type-specific and must be explicit, because the naive truthiness test is wrong
for two of these fields: `None` for the optionals, `[]` for `requirements`, and `None`
**only** for `remote` — `remote=False` is a real answer and must not be treated as missing.
Likewise `salary_min=0` is a present value.
`stage`, `applied_at`, and `notes` are not members of the enrichable set at all —
structurally excluded rather than guarded by a conditional a later edit can weaken.
`updated_at` bumps only when something actually changed.

**Sightings.** `Job` gains `sightings: list[Sighting] = []`. `Sighting` is a pydantic
`BaseModel` (not a dataclass) declared alongside `Job` in `careeros/core/models.py`, so it
serialises through the existing `model_dump_json` path with no custom encoder:

```python
class Sighting(BaseModel):
    source: str
    url: str | None = None
    seen_at: str
```

Pydantic's default means every existing job JSON loads unchanged. On the first merge into a record whose `sightings` is empty, the store seeds the
list with that record's own `source`/`url`/`created_at` before appending the new sighting,
so the list is complete from the first merge onward without backfilling every workspace.

**`force`** is wired only to a new `careeros job add --force`. Discovery paths never set it:
an unattended run must not be able to talk itself into a duplicate.

**Replacing the stopgap.** `discover_and_apply_cmd`'s inline `(company, title)` block is
deleted. Its `already_applied` check becomes `outcome.job.applied_at is not None`, reading
the returned record rather than recomputing. Its `Duplicates: N` summary segment survives,
counting `not outcome.created`.

**Residual risk, named.** The seam is enforced by convention, not structure: nothing
physically prevents a future path from calling `job.save()` with a fresh id. Having
`JobStore` assign the id would make bypass impossible, but `Job.id` is required, so that
means an optional-id model or a builder method — both cost more clarity than they buy.
Mitigation: one test per creation path asserting it routes through `JobStore`, and a
docstring on `Job.save` pointing at `JobStore.save_new` for new records. Honest mitigation,
not a guarantee.

### Configuration and CLI wiring

`config/sources.json` entries gain `board` and `company`:

```json
{"sources": [{"source": "greenhouse", "board": "stripe", "company": "Stripe", "mode": "SEARCH_ONLY"}]}
```

| Path | Change |
|---|---|
| `job search` | Routes through `JobStore`; gains dedup and a `Duplicates: N` line. The interactive API path. No new flags. |
| `discover-and-apply` | After the board loop, iterates configured `sources.json` entries, fetches via `JobSource`, and feeds the same accumulator the scrapers feed — one scoring, policy, dedup, and apply pipeline |
| `browse` | Unchanged. Browser-only |
| `job add` | Routes through `JobStore`; gains `--force` |
| `onboard` | Source prompt drops `naukri` (never had code behind it) and asks for board slugs |
| `workspace validate` | Reports `sources.json` entries lacking a `board` as "inert (no board slug)" |

### Failure handling

Deliberately mirrors Phase 9c's session pre-flight, so the two discovery halves behave alike:

| Condition | Behaviour |
|---|---|
| Bad board slug (`ATSFetchError("not_found")`) | Skip that source, log `source_unavailable` at `status="failed"`, name it in the summary |
| Network or HTTP failure | Same — skip, log, report |
| Every board unauthorized **and** every source unavailable | Exit non-zero, rather than a healthy-looking row of zeros |

The summary line gains exactly one segment: `Unavailable sources: greenhouse:stripe`. It
already carries six counters; adding several more would make the cron log unreadable.

## Testing

All tests offline; no network, no real browser. The suite is at 409 passed, 6 skipped
before this phase.

- `tests/test_sources_base.py` — `Posting` is frozen and fully populated;
  `posting_from_scrape` maps every scraper dict field and raises on a missing `company`
  rather than defaulting it.
- `tests/test_greenhouse_source.py`, `tests/test_lever_source.py` — `fetch` returns
  `Posting` records with absolute URLs and the configured company name; `ATSFetchError`
  propagates rather than being swallowed; a malformed posting is skipped without aborting
  the batch. `urllib` is mocked at the transport boundary.
- `tests/test_dedup.py` — the centre of gravity for this phase:
  - `canonical_url` strips fragments, trailing slashes, and each denylisted tracking
    parameter, while **preserving** a non-denylisted query parameter (the catastrophic
    case: two distinct postings whose IDs live in the query string must not collapse)
  - `normalize_title` expands each abbreviation on whole tokens only, and leaves
    "engineering" intact — the mangling case, asserted explicitly
  - "Senior SRE" and "Senior Site Reliability Engineer" match; "Senior Engineer" and
    "Staff Engineer" do not
  - a null location matches any location; two differently-worded non-empty locations do not
    match — the accepted limitation, pinned so it cannot regress silently into a false
    positive
  - URL-tier match wins even when titles differ entirely
- `tests/test_job_store.py` — `save_new` creates when no match exists; merges when one does;
  enriches only null/empty fields and never overwrites a populated one; **never** alters
  `stage`, `applied_at`, or `notes` even when the incoming job carries different values;
  seeds `sightings` from the existing record on first merge; `force=True` creates a
  duplicate; `updated_at` is unchanged when nothing was enriched. Two absence tests that a
  truthiness-based implementation would fail: an existing `remote=False` is **not**
  overwritten by an incoming `remote=True`, and an existing `salary_min=0` is **not**
  overwritten.
- `tests/test_job_cmd.py`, `tests/test_browse_cmd.py`,
  `tests/test_discover_and_apply_cmd.py` — one test per creation path asserting it routes
  through `JobStore` and that a second identical discovery produces no new record.
- `tests/test_discover_and_apply_cmd.py` — an unavailable source is skipped and logged at
  `status="failed"`; the summary names it; all-boards-unauthorized **and**
  all-sources-unavailable exits non-zero; a Greenhouse posting and a LinkedIn posting for
  the same job resolve to one record (the ROADMAP's exit condition, asserted directly).
- `tests/test_workspace_cmd.py` — `validate` reports a legacy `sources.json` entry as inert.

## Migration note

**No workspace migration.** `Job.sightings` defaults to `[]`, so existing job JSON loads
unchanged. Legacy `config/sources.json` entries lack a `board` and are skipped by the
reader — they are inert now exactly as they have been since Phase 1, when the file was first
written and never read. Rewriting them would silently discard user configuration for no
benefit; surfacing them through `workspace validate` makes the state visible instead.

`onboard`'s source prompt changes for new workspaces only. Existing users who want API
discovery edit `config/sources.json` directly, as they already do for
`config/automation_policy.json`.
