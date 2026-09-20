# Phase 9a — Policy Engine Design

## Why

An external review (2026-09-20) found that `discover-and-apply` — the only command that takes unattended, irreversible, external action — has no deterministic safety boundary in front of it. The master design spec (`docs/superpowers/specs/2026-09-18-careeros-design.md`, §5.3, §6) named a `PolicyEngine` as *the* backstop that keeps an LLM-produced decision from being the last word on an irreversible action: "The LLM proposes. The PolicyEngine decides. Prompt injection cannot override policy." That engine was deferred in Phase 5, deferred again in Phase 6 — the exact phase that shipped `discover-and-apply`. The substitute reasoning at the time ("the human decision happens once, when the user sets the score threshold") only holds if the score itself is trustworthy, and a job description with an embedded instruction can already influence that score (see the companion Phase 9b sanitization spec).

Phase 9 was scoped in `ROADMAP.md` as three independent sub-projects — PolicyEngine, content sanitization, browser isolation — built in sequence, with the `discover-and-apply` opt-in gate lifted only once all three land. This spec covers the first: PolicyEngine.

`config/policies.json` has existed since Phase 1's `m001_initial` migration but has never been read by any code. This phase makes it real.

## Scope

**In scope:**
- `PolicyConfig` — the schema for `config/policies.json`: blocked companies, minimum salary, blocked locations.
- `PolicyEngine.check_job(job)` — deterministic, non-LLM evaluation of a `Job` against the loaded `PolicyConfig`.
- Wiring `check_job` into the three places an `ActionProposal` for an irreversible action gets constructed: `apply_cmd.py`, `discover_and_apply_cmd.py`, `outreach_cmd.py`.
- Updating `m001_initial`'s seed content for `config/policies.json` to the new schema.

**Explicitly out of scope for this phase:**
- **Visa sponsorship rule.** Named as a v1 rule type in `ROADMAP.md`, but `Job` has no `visa_sponsorship` field and no skill extracts one from posting text today. Building a rule with nothing to check against would be a placeholder. Deferred until a real extraction step exists — tracked here, not silently dropped.
- **`is_source_allowed(source, mode)`** — a board-level gate named in the master spec's `PolicyEngine` sketch (§5.3). Nothing today needs to disable a whole board; adding it now would be speculative.
- **Removing the `discover-and-apply` gate.** That happens only after Phase 9b (content sanitization) and Phase 9c (browser isolation) also land, per `ROADMAP.md`'s "why" for Phase 9.
- **A `careeros policy` CLI subcommand.** Rules are authored by editing `config/policies.json` directly, matching how `config/automation_policy.json` works today with no dedicated CLI.

## Data model

Replace `config/policies.json`'s current (never-consumed) shape:

```json
{
  "hard_requirements": {},
  "soft_requirements": {},
  "approval_required": true
}
```

with:

```json
{
  "blocked_companies": [],
  "min_salary": null,
  "blocked_locations": []
}
```

- `blocked_companies: list[str]` — case-insensitive exact match against `Job.company`.
- `min_salary: int | None` — `Job.salary_min` must be `>= min_salary` to pass. A job with `salary_min is None` passes through unconditionally: most scraped postings omit salary, and treating "unknown" as "blocked" would make auto-apply unusable for the majority of real postings. This rule only fires when both the policy and the job have a number to compare.
- `blocked_locations: list[str]` — case-insensitive substring match against `Job.location` (e.g. a blocked value of `"Antarctica"` matches a job location of `"Remote, Antarctica"`). A job with `location is None` cannot match any blocked-location rule (nothing to search).

`PolicyConfig` (`careeros/core/models.py`, alongside `AutomationPolicy`):

```python
class PolicyConfig(BaseModel):
    blocked_companies: list[str] = []
    min_salary: int | None = None
    blocked_locations: list[str] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("config/policies.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "PolicyConfig":
        if not storage.exists("config/policies.json"):
            return cls()
        return cls.model_validate_json(storage.read("config/policies.json"))
```

Because Pydantic v2's default `extra` behavior is `"ignore"` and every new field has a default, a workspace whose `config/policies.json` still holds the old vestigial shape (`hard_requirements`, etc.) loads cleanly as an all-defaults `PolicyConfig` — no migration bump needed. `m001_initial`'s seed content is updated directly to the new shape so newly created workspaces see the real schema from the start.

A malformed `config/policies.json` (present but not valid JSON, or a value of the wrong type — e.g. `min_salary: "a lot"`) raises during `model_validate_json`. This is intentional: a broken policy file must stop the command loudly (exit non-zero, red error message) rather than silently degrade to "no rules enforced." This matches the existing pattern for `AutomationPolicy` load failures elsewhere in the CLI.

## PolicyEngine

`careeros/core/policy_engine.py` (new file):

```python
from dataclasses import dataclass
from careeros.core.models import Job, PolicyConfig


@dataclass
class PolicyResult:
    blocked: bool
    rule: str | None = None


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self._config = config

    def check_job(self, job: Job) -> PolicyResult:
        for company in self._config.blocked_companies:
            if job.company.strip().lower() == company.strip().lower():
                return PolicyResult(blocked=True, rule="blocked_company:" + company)

        if self._config.min_salary is not None and job.salary_min is not None:
            if job.salary_min < self._config.min_salary:
                return PolicyResult(blocked=True, rule="min_salary")

        if job.location:
            location_lower = job.location.lower()
            for blocked in self._config.blocked_locations:
                if blocked.strip().lower() in location_lower:
                    return PolicyResult(blocked=True, rule="blocked_location:" + blocked)

        return PolicyResult(blocked=False)
```

Rules are checked in a fixed order (companies, salary, locations); the first rule that fires is the one reported. This ordering has no safety significance — any one blocking rule is sufficient to block the action — it only determines which rule name appears in the activity log when more than one would have fired.

`PolicyEngine` takes no dependency on `StorageProvider`, `AgentRuntime`, or any LLM call. It is constructed once per command invocation from an already-loaded `PolicyConfig` and never re-reads storage — consistent with "deterministic, no LLM involvement" from the master spec.

## Enforcement points

`check_job` is called before any `ActionProposal` for the job is constructed, and before any LLM call whose only purpose is to prepare that action (cover letter, outreach draft) — a blocked job never causes work to be attempted, not just never causes approval to be asked.

**`careeros/cli/apply_cmd.py`** — immediately after `job = Job.load(runtime.storage, job_id)` (~line 59), before jd_text/cover-letter generation:

```python
policy_engine = PolicyEngine(PolicyConfig.load(runtime.storage))
result = policy_engine.check_job(job)
if result.blocked:
    runtime.record_activity(runtime.new_event(
        "policy_blocked", "apply", "Blocked by policy (" + result.rule + "): "
        + job.company + " — " + job.title,
        status="failed", entity_type="job", entity_id=job_id,
    ))
    rprint("[red]Blocked by policy (" + result.rule + "). Edit config/policies.json to change this.[/red]")
    raise typer.Exit(1)
```

**`careeros/cli/discover_and_apply_cmd.py`** — top of the `for p in eligible:` loop (~line 178), before `generate_cover_letter`:

```python
job_id = p["job_id"]
job = Job.load(runtime.storage, job_id)
result = policy_engine.check_job(job)
if result.blocked:
    runtime.record_activity(runtime.new_event(
        "policy_blocked", "discover-and-apply", "Blocked by policy (" + result.rule + "): "
        + p["company"] + " — " + p["title"],
        status="failed", entity_type="job", entity_id=job_id,
    ))
    blocked_count += 1
    continue
```

`policy_engine` (named to avoid colliding with the existing `policy` variable, which already holds the loaded `AutomationPolicy`) is constructed once, right after `AutomationPolicy.load(runtime.storage)` near the top of the command, from `PolicyEngine(PolicyConfig.load(runtime.storage))`. A new `blocked_count = 0` counter is initialized alongside `applied_count`/`skipped_count`, and the final summary line becomes:

```
Discovered: N, Duplicates: N, Blocked: N, Auto-applied: N, Skipped: N
```

**`careeros/cli/outreach_cmd.py`** — immediately after `job_obj = Job.load(runtime.storage, job)` (~line 76), before drafting:

```python
policy_engine = PolicyEngine(PolicyConfig.load(runtime.storage))
result = policy_engine.check_job(job_obj)
if result.blocked:
    runtime.record_activity(runtime.new_event(
        "policy_blocked", "outreach", "Blocked by policy (" + result.rule + "): "
        + job_obj.company + " — " + job_obj.title,
        status="failed", entity_type="job", entity_id=job_obj.id,
    ))
    rprint("[red]Blocked by policy (" + result.rule + "). Edit config/policies.json to change this.[/red]")
    raise typer.Exit(1)
```

In every call site, a block is unconditional: there is no flag or environment variable to override it (unlike `discover-and-apply`'s `--i-accept-the-risk` gate, which is the user knowingly opting into a documented, temporary gap — a policy block is the user's own configured rule firing as designed). The only way to change the outcome is to edit `config/policies.json` and re-run.

## Testing

**`tests/test_policy_engine.py`** (new):
- `PolicyConfig.load` returns all-defaults when the file doesn't exist.
- `PolicyConfig.load` parses a populated file correctly; `save` round-trips.
- `PolicyConfig.load` raises on malformed JSON.
- `check_job` blocks on a case-insensitive company match; does not block a different company.
- `check_job` blocks on salary below the floor; does not block salary at/above the floor; does not block when `job.salary_min` is `None`; does not block when no `min_salary` policy is configured.
- `check_job` blocks on a location substring match (case-insensitive); does not block when `job.location` is `None`; does not block a non-matching location.
- `check_job` returns `blocked=False, rule=None` when no rules are configured at all.
- When multiple rules would fire, the reported `rule` is the first one checked (blocked_companies, then min_salary, then blocked_locations) — pin the exact ordering so this behavior is a documented, tested guarantee rather than incidental.

**Integration tests** (extending `tests/test_apply_cmd.py`, `tests/test_discover_and_apply_cmd.py`, `tests/test_outreach_cmd.py`):
- A job matching a blocked-company rule never reaches `runtime.request_approval` (assert the mock was not called) and logs a `policy_blocked` event naming the rule.
- `discover_and_apply_cmd`'s summary line reports the correct `Blocked: N` count, and a blocked job does not count toward `Skipped` or `Auto-applied`.
- An unblocked job proceeds through the existing approval flow unchanged (regression check — this phase must not alter behavior for the non-blocked path).

## Migration note

`careeros/workspace/migrations/m001_initial.py`'s `config/policies.json` seed changes from the old vestigial shape to `{"blocked_companies": [], "min_salary": null, "blocked_locations": []}`. `tests/test_migrations.py::test_m001_creates_policies_json` is updated to assert the new keys instead of the old ones. No new migration number is needed — see the Data model section above for why the schema change is backward-compatible with already-created workspaces.
