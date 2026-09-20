# Phase 9a Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, non-LLM `PolicyEngine` that blocks `apply`, `discover-and-apply`'s auto-apply, and `outreach send` from ever proposing an `ActionProposal` for a job that violates a user-configured rule (blocked company, minimum salary, blocked location).

**Architecture:** A `PolicyConfig` Pydantic model (parallel to the existing `AutomationPolicy`) persists rules to `config/policies.json`. A `PolicyEngine` class wraps a loaded `PolicyConfig` with one method, `check_job(job) -> PolicyResult`, evaluated in a fixed rule order. Three CLI commands each construct a `PolicyEngine` from the workspace's `PolicyConfig` and call `check_job` before any LLM call or `ActionProposal` construction for that job.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Typer's `CliRunner` for CLI tests.

**Spec:** `docs/superpowers/specs/2026-09-20-phase9a-policy-engine-design.md`

## Global Constraints

- No f-strings with user data in activity-log summaries or prompts — string concatenation only (project-wide convention; see `careeros/cli/job_cmd.py` for the established pattern).
- All new/changed `StorageProvider`-backed models follow the existing `save`/`load` classmethod pattern (see `AutomationPolicy` in `careeros/core/models.py`).
- A policy block is unconditional: no flag or environment variable overrides it in any of the three call sites. Only editing `config/policies.json` changes the outcome.
- `PolicyEngine` takes no dependency on `StorageProvider`, `AgentRuntime`, or any LLM call — it is constructed from an already-loaded `PolicyConfig` and never re-reads storage itself.
- A malformed `config/policies.json` must raise, not silently degrade to "no rules enforced."
- Missing data (`job.salary_min is None`, `job.location is None`) never causes a rule to block — a rule only fires when both the policy and the job have a value to compare.

---

### Task 1: `PolicyConfig` model and `PolicyEngine`

**Files:**
- Modify: `careeros/core/models.py` (add `PolicyConfig` class after `AutomationPolicy`, which currently ends at line 161, right before `class Company(BaseModel):` at line 164)
- Create: `careeros/core/policy_engine.py`
- Test: `tests/test_policy_engine.py`

**Interfaces:**
- Produces: `PolicyConfig` (Pydantic model, fields `blocked_companies: list[str]`, `min_salary: int | None`, `blocked_locations: list[str]`, all defaulting to empty/`None`; classmethod `load(storage: StorageProvider) -> PolicyConfig`; instance method `save(self, storage: StorageProvider) -> None`)
- Produces: `PolicyResult` (dataclass, fields `blocked: bool`, `rule: str | None = None`)
- Produces: `PolicyEngine` (class, constructor `__init__(self, config: PolicyConfig)`, method `check_job(self, job: Job) -> PolicyResult`)
- Consumes: `Job` from `careeros.core.models` (existing — fields `company: str`, `salary_min: int | None`, `location: str | None` are the ones read)
- Consumes: `StorageProvider` from `careeros.storage.interface` (existing Protocol)

- [ ] **Step 1: Write the failing tests for `PolicyConfig` persistence**

Create `tests/test_policy_engine.py`:

```python
from datetime import datetime, timezone

import pytest

from careeros.core.models import Job, PolicyConfig
from careeros.core.policy_engine import PolicyEngine
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _make_job(company="Acme", salary_min=None, location=None):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-abc1", source="browse", company=company, title="Senior SRE",
        salary_min=salary_min, location=location, stage="saved",
        created_at=now, updated_at=now,
    )


class TestPolicyConfigPersistence:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        config = PolicyConfig.load(storage)
        assert config.blocked_companies == []
        assert config.min_salary is None
        assert config.blocked_locations == []

    def test_save_and_load_round_trip(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        init_workspace(storage)
        config = PolicyConfig(blocked_companies=["Bad Co"], min_salary=150000, blocked_locations=["Antarctica"])
        config.save(storage)
        loaded = PolicyConfig.load(storage)
        assert loaded.blocked_companies == ["Bad Co"]
        assert loaded.min_salary == 150000
        assert loaded.blocked_locations == ["Antarctica"]

    def test_load_malformed_json_raises(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        init_workspace(storage)
        storage.atomic_write("config/policies.json", b"not valid json")
        with pytest.raises(Exception):
            PolicyConfig.load(storage)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_policy_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'PolicyConfig' from 'careeros.core.models'` (or similar — the class doesn't exist yet)

- [ ] **Step 3: Add `PolicyConfig` to `careeros/core/models.py`**

Find this in `careeros/core/models.py` (the end of `AutomationPolicy`, immediately before `class Company(BaseModel):`):

```python
    @classmethod
    def load(cls, storage: StorageProvider) -> "AutomationPolicy":
        if not storage.exists("config/automation_policy.json"):
            raise FileNotFoundError("config/automation_policy.json not found in workspace")
        return cls.model_validate_json(storage.read("config/automation_policy.json").decode())


class Company(BaseModel):
```

Replace it with:

```python
    @classmethod
    def load(cls, storage: StorageProvider) -> "AutomationPolicy":
        if not storage.exists("config/automation_policy.json"):
            raise FileNotFoundError("config/automation_policy.json not found in workspace")
        return cls.model_validate_json(storage.read("config/automation_policy.json").decode())


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
        return cls.model_validate_json(storage.read("config/policies.json").decode())


class Company(BaseModel):
```

- [ ] **Step 4: Run tests to verify the persistence tests pass**

Run: `pytest tests/test_policy_engine.py -v`
Expected: The three `TestPolicyConfigPersistence` tests PASS; any `PolicyEngine`-related tests (added next) still fail on import.

- [ ] **Step 5: Write the failing tests for `PolicyEngine.check_job`**

Append to `tests/test_policy_engine.py`:

```python
class TestPolicyEngineBlockedCompanies:
    def test_blocks_case_insensitive_company_match(self):
        engine = PolicyEngine(PolicyConfig(blocked_companies=["acme"]))
        result = engine.check_job(_make_job(company="Acme"))
        assert result.blocked is True
        assert result.rule == "blocked_company:acme"

    def test_does_not_block_different_company(self):
        engine = PolicyEngine(PolicyConfig(blocked_companies=["Acme"]))
        result = engine.check_job(_make_job(company="Beta"))
        assert result.blocked is False
        assert result.rule is None


class TestPolicyEngineMinSalary:
    def test_blocks_salary_below_floor(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=100000))
        assert result.blocked is True
        assert result.rule == "min_salary"

    def test_does_not_block_salary_at_floor(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=150000))
        assert result.blocked is False

    def test_does_not_block_salary_above_floor(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=200000))
        assert result.blocked is False

    def test_does_not_block_when_job_salary_missing(self):
        engine = PolicyEngine(PolicyConfig(min_salary=150000))
        result = engine.check_job(_make_job(salary_min=None))
        assert result.blocked is False

    def test_does_not_block_when_no_min_salary_configured(self):
        engine = PolicyEngine(PolicyConfig())
        result = engine.check_job(_make_job(salary_min=1))
        assert result.blocked is False


class TestPolicyEngineBlockedLocations:
    def test_blocks_substring_match_case_insensitive(self):
        engine = PolicyEngine(PolicyConfig(blocked_locations=["antarctica"]))
        result = engine.check_job(_make_job(location="Remote, Antarctica"))
        assert result.blocked is True
        assert result.rule == "blocked_location:antarctica"

    def test_does_not_block_when_location_missing(self):
        engine = PolicyEngine(PolicyConfig(blocked_locations=["Antarctica"]))
        result = engine.check_job(_make_job(location=None))
        assert result.blocked is False

    def test_does_not_block_non_matching_location(self):
        engine = PolicyEngine(PolicyConfig(blocked_locations=["Antarctica"]))
        result = engine.check_job(_make_job(location="San Francisco, CA"))
        assert result.blocked is False


class TestPolicyEngineNoRules:
    def test_no_rules_configured_never_blocks(self):
        engine = PolicyEngine(PolicyConfig())
        result = engine.check_job(_make_job(company="Anything", salary_min=1, location="Anywhere"))
        assert result.blocked is False
        assert result.rule is None


class TestPolicyEngineRuleOrdering:
    def test_blocked_company_checked_before_salary_and_location(self):
        engine = PolicyEngine(PolicyConfig(
            blocked_companies=["Acme"], min_salary=150000, blocked_locations=["SF"],
        ))
        result = engine.check_job(_make_job(company="Acme", salary_min=1, location="SF"))
        assert result.rule == "blocked_company:Acme"

    def test_min_salary_checked_before_location_when_company_not_blocked(self):
        engine = PolicyEngine(PolicyConfig(
            blocked_companies=["Other"], min_salary=150000, blocked_locations=["SF"],
        ))
        result = engine.check_job(_make_job(company="Acme", salary_min=1, location="SF"))
        assert result.rule == "min_salary"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_policy_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.core.policy_engine'`

- [ ] **Step 7: Create `careeros/core/policy_engine.py`**

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

- [ ] **Step 8: Run all tests in the file to verify they pass**

Run: `pytest tests/test_policy_engine.py -v`
Expected: All tests PASS (3 persistence + 13 engine tests = 16 total)

- [ ] **Step 9: Run the full test suite to check for regressions**

Run: `pytest`
Expected: All previously-passing tests still pass; new tests pass.

- [ ] **Step 10: Commit**

```bash
git add careeros/core/models.py careeros/core/policy_engine.py tests/test_policy_engine.py
git commit -m "feat: add PolicyConfig model and PolicyEngine (Phase 9a)

Deterministic, non-LLM rule engine per docs/superpowers/specs/2026-09-20-phase9a-policy-engine-design.md.
Not yet wired into any CLI command -- that's Tasks 3-5."
```

---

### Task 2: Update `m001_initial` migration seed

**Files:**
- Modify: `careeros/workspace/migrations/m001_initial.py`
- Test: `tests/test_migrations.py:47-52` (existing test, update assertion)

**Interfaces:**
- Consumes: nothing new (this task only changes the JSON content a migration writes)

- [ ] **Step 1: Update the failing assertion first**

In `tests/test_migrations.py`, find:

```python
def test_m001_creates_policies_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("config/policies.json")
    data = json.loads(storage.read("config/policies.json"))
    assert data["approval_required"] is True
```

Replace with:

```python
def test_m001_creates_policies_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("config/policies.json")
    data = json.loads(storage.read("config/policies.json"))
    assert data == {"blocked_companies": [], "min_salary": None, "blocked_locations": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py::test_m001_creates_policies_json -v`
Expected: FAIL — `data["approval_required"]` no longer matches (`KeyError` or assertion mismatch against the old seed still in `m001_initial.py`)

- [ ] **Step 3: Update the migration seed**

In `careeros/workspace/migrations/m001_initial.py`, find:

```python
    if not storage.exists("config/policies.json"):
        storage.write(
            "config/policies.json",
            json.dumps(
                {
                    "hard_requirements": {},
                    "soft_requirements": {},
                    "approval_required": True,
                },
                indent=2,
            ).encode(),
        )
```

Replace with:

```python
    if not storage.exists("config/policies.json"):
        storage.write(
            "config/policies.json",
            json.dumps(
                {
                    "blocked_companies": [],
                    "min_salary": None,
                    "blocked_locations": [],
                },
                indent=2,
            ).encode(),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_migrations.py::test_m001_creates_policies_json -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest`
Expected: All tests pass. (A workspace with the OLD seed shape loads fine under the new `PolicyConfig` per Task 1's `load` — Pydantic v2 ignores unknown keys and every new field has a default — so no other test depending on the old shape should break. `tests/test_workspace_cmd.py`'s `workspace validate` check only asserts `config/policies.json` *exists*, never its shape, so it's unaffected.)

- [ ] **Step 6: Commit**

```bash
git add careeros/workspace/migrations/m001_initial.py tests/test_migrations.py
git commit -m "feat: seed config/policies.json with the real PolicyConfig schema

The old seed (hard_requirements/soft_requirements/approval_required)
was never read by any code. New workspaces now get the schema
PolicyConfig actually understands. Backward-compatible: Pydantic v2
ignores unknown keys and every PolicyConfig field defaults, so a
workspace created before this change still loads as an unblocked,
all-defaults config -- no new migration number needed."
```

---

### Task 3: Wire `PolicyEngine` into `apply_cmd.py`

**Files:**
- Modify: `careeros/cli/apply_cmd.py`
- Test: `tests/test_apply_cmd.py`

**Interfaces:**
- Consumes: `PolicyConfig`, `PolicyEngine`, `PolicyResult` from Task 1 (`careeros.core.models.PolicyConfig`, `careeros.core.policy_engine.PolicyEngine`)
- Produces: nothing new for later tasks (each CLI wiring task is independent of the others)

- [ ] **Step 1: Fix the shared test fixture so existing tests keep passing**

In `tests/test_apply_cmd.py`, find:

```python
def _mock_runtime(tmp_path, resume_filename="resume.pdf", approved=True):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    storage.exists.return_value = True
    runtime = MagicMock()
    runtime.storage = storage
    runtime.request_approval.return_value = ApprovalResult(approved=approved)
    return runtime
```

Replace with:

```python
def _mock_runtime(tmp_path, resume_filename="resume.pdf", approved=True):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    storage.exists.return_value = True
    storage.read.return_value = b"{}"
    runtime = MagicMock()
    runtime.storage = storage
    runtime.request_approval.return_value = ApprovalResult(approved=approved)
    return runtime
```

(`PolicyConfig.model_validate_json(b"{}")` parses to an all-defaults, unblocked config — every existing test that reaches the new policy-check line keeps passing without individual edits. `test_no_resume_exits_1` builds its own separate mock and exits before the resume check, so it never reaches the policy-check line and needs no change.)

Also update the import line near the top of the file:

```python
from careeros.core.models import Goals, Job, Profile, Skills
```

to:

```python
from careeros.core.models import Goals, Job, PolicyConfig, Profile, Skills
```

- [ ] **Step 2: Write the failing tests for policy-blocked behavior**

In `tests/test_apply_cmd.py`, add these two tests inside `class TestApplyCmdFailurePaths:` (after `test_no_resume_exits_1`):

```python
    def test_policy_blocked_company_exits_1_without_requesting_approval(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig(blocked_companies=["Acme"])):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])
        assert result.exit_code == 1
        assert "blocked by policy" in result.output.lower()
        runtime.request_approval.assert_not_called()

    def test_policy_blocked_logs_policy_blocked_event(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        job = _make_job()
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig(blocked_companies=["Acme"])):
            runner.invoke(apply_app, ["acme-sre-abc1"])
        runtime.record_activity.assert_called_once()
        event_args = runtime.new_event.call_args
        assert event_args[0][0] == "policy_blocked"
```

(`_make_job()` defaults to `company="Acme"`, matching the blocked-company rule.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_apply_cmd.py -v`
Expected: The two new tests FAIL (job proceeds past where policy should block it, since the check doesn't exist yet — likely fails on `runtime.request_approval.assert_not_called()` since approval IS requested today, or the command completes with exit_code 0/1 for unrelated reasons rather than the expected policy-blocked message).

- [ ] **Step 4: Add the policy check to `apply_cmd.py`**

Add the import near the top of `careeros/cli/apply_cmd.py`:

```python
from careeros.core.models import Goals, Job, Profile, Skills
```

becomes:

```python
from careeros.core.models import Goals, Job, PolicyConfig, Profile, Skills
```

and:

```python
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_local_runtime
```

becomes:

```python
from careeros.core.policy_engine import PolicyEngine
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_local_runtime
```

Then find:

```python
    resume_file = resume_entries[-1]
    resume_path = runtime.storage.resolve(resume_file)

    # Load profile data (after resume check so early failure avoids unnecessary I/O)
    profile = Profile.load_or_empty(runtime.storage)
```

Replace with:

```python
    resume_file = resume_entries[-1]
    resume_path = runtime.storage.resolve(resume_file)

    # Policy check (before any LLM call is spent preparing this application)
    policy_engine = PolicyEngine(PolicyConfig.load(runtime.storage))
    policy_result = policy_engine.check_job(job)
    if policy_result.blocked:
        runtime.record_activity(runtime.new_event(
            "policy_blocked", "apply",
            "Blocked by policy (" + policy_result.rule + "): " + job.company + " — " + job.title,
            status="failed", entity_type="job", entity_id=job_id,
        ))
        rprint("[red]Blocked by policy (" + policy_result.rule + "). Edit config/policies.json to change this.[/red]")
        raise typer.Exit(1)

    # Load profile data (after resume check so early failure avoids unnecessary I/O)
    profile = Profile.load_or_empty(runtime.storage)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_apply_cmd.py -v`
Expected: All tests PASS, including the two new ones.

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `pytest`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/apply_cmd.py tests/test_apply_cmd.py
git commit -m "feat: enforce PolicyEngine in apply_cmd (Phase 9a)

A job matching a policy rule (blocked company, salary floor, blocked
location) never reaches request_approval -- it exits with a
policy_blocked activity event and a red error naming the rule.
Nothing overrides this except editing config/policies.json."
```

---

### Task 4: Wire `PolicyEngine` into `discover_and_apply_cmd.py`

**Files:**
- Modify: `careeros/cli/discover_and_apply_cmd.py`
- Test: `tests/test_discover_and_apply_cmd.py`

**Interfaces:**
- Consumes: `PolicyConfig`, `PolicyEngine` from Task 1
- Produces: a `Blocked: N` term in the command's printed summary line (informational only — nothing downstream consumes this)

- [ ] **Step 1: Write the failing test for policy-blocked behavior**

In `tests/test_discover_and_apply_cmd.py`, update the import line:

```python
from careeros.core.models import AutomationPolicy, Job, Profile, Skill, Skills
```

to:

```python
from careeros.core.models import AutomationPolicy, Job, PolicyConfig, Profile, Skill, Skills
```

Then add this test inside `class TestDiscoverAndApplyCmd:` (after `test_duplicate_saved_job_reuses_existing_id_instead_of_creating_new_one`):

```python
    def test_policy_blocked_company_skipped_and_counted(self, tmp_path):
        policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"])
        ws_path = _setup_workspace(tmp_path, policy=policy)
        storage = LocalFilesystemStorage(ws_path)
        PolicyConfig(blocked_companies=["Acme"]).save(storage)
        mock_filler = MagicMock()
        mock_filler.can_handle.return_value = True

        with patch("careeros.cli.discover_and_apply_cmd.SCRAPERS", {"linkedin": MagicMock(search=MagicMock(return_value=[_posting()]))}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.fetch_jd_text", return_value="JD text"), \
             patch("careeros.cli.discover_and_apply_cmd.score_job", return_value={"score": 95, "reasoning": "great"}), \
             patch("careeros.cli.discover_and_apply_cmd.generate_cover_letter") as mock_gen, \
             patch("careeros.cli.discover_and_apply_cmd.FILLERS", [mock_filler]):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path, "--i-accept-the-risk"])

        assert result.exit_code == 0
        mock_gen.assert_not_called()
        mock_filler.fill.assert_not_called()
        assert "Blocked: 1" in result.output
        jobs = Job.list_all(storage)
        assert jobs[0].stage == "saved"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "policy_blocked" in log_content
```

(`_posting()` defaults to `company="Acme"`, matching the blocked-company rule. `storage` here is a real `LocalFilesystemStorage`, since `_setup_workspace` already created one at `ws_path` via `init_workspace`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_discover_and_apply_cmd.py::TestDiscoverAndApplyCmd::test_policy_blocked_company_skipped_and_counted -v`
Expected: FAIL — `mock_gen.assert_not_called()` fails (cover letter generation currently proceeds unconditionally) or `"Blocked: 1" in result.output` fails (no such term exists in the summary line yet).

- [ ] **Step 3: Add the policy check to `discover_and_apply_cmd.py`**

Update the import line near the top of `careeros/cli/discover_and_apply_cmd.py`:

```python
from careeros.core.models import AutomationPolicy, Goals, Job, Profile, Skills
```

to:

```python
from careeros.core.models import AutomationPolicy, Goals, Job, PolicyConfig, Profile, Skills
```

and:

```python
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_automation_runtime
```

to:

```python
from careeros.core.policy_engine import PolicyEngine
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_automation_runtime
```

Then find:

```python
    applied_count = 0
    skipped_count = 0
    for p in eligible:
        if applied_count >= policy.max_auto_applies_per_run:
            break
        if resume_path is None:
            rprint("[yellow]No resume found — skipping auto-apply for " + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue

        job_id = p["job_id"]
        cover_letter = generate_cover_letter(p["jd_text"], profile, skills, goals)
```

Replace with:

```python
    policy_engine = PolicyEngine(PolicyConfig.load(runtime.storage))
    applied_count = 0
    skipped_count = 0
    blocked_count = 0
    for p in eligible:
        if applied_count >= policy.max_auto_applies_per_run:
            break
        if resume_path is None:
            rprint("[yellow]No resume found — skipping auto-apply for " + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue

        job_id = p["job_id"]
        job = Job.load(runtime.storage, job_id)
        policy_result = policy_engine.check_job(job)
        if policy_result.blocked:
            runtime.record_activity(runtime.new_event(
                "policy_blocked", "discover-and-apply",
                "Blocked by policy (" + policy_result.rule + "): " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            blocked_count += 1
            continue

        cover_letter = generate_cover_letter(p["jd_text"], profile, skills, goals)
```

(`policy_engine` is named to avoid colliding with the existing `policy` variable, which holds the loaded `AutomationPolicy`. The re-`Job.load` here is a cheap local read; the later `job = Job.load(runtime.storage, job_id)` right after approval, further down the function, still runs and simply reloads the same record — this plan does not remove it.)

Finally, find the summary print:

```python
    rprint(
        "Discovered: " + str(saved_count) + ", Duplicates: " + str(duplicate_count)
        + ", Auto-applied: " + str(applied_count) + ", Skipped: " + str(skipped_count)
    )
```

Replace with:

```python
    rprint(
        "Discovered: " + str(saved_count) + ", Duplicates: " + str(duplicate_count)
        + ", Blocked: " + str(blocked_count)
        + ", Auto-applied: " + str(applied_count) + ", Skipped: " + str(skipped_count)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_discover_and_apply_cmd.py -v`
Expected: All tests PASS, including the new one.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/discover_and_apply_cmd.py tests/test_discover_and_apply_cmd.py
git commit -m "feat: enforce PolicyEngine in discover-and-apply (Phase 9a)

A job matching a policy rule is skipped before cover-letter generation
(no LLM call spent) and before any ActionProposal is built. The
summary line now reports Blocked: N alongside the existing counts."
```

---

### Task 5: Wire `PolicyEngine` into `outreach_cmd.py`

**Files:**
- Modify: `careeros/cli/outreach_cmd.py`
- Test: `tests/test_outreach_cmd.py`

**Interfaces:**
- Consumes: `PolicyConfig`, `PolicyEngine` from Task 1

- [ ] **Step 1: Write the failing test for policy-blocked behavior**

In `tests/test_outreach_cmd.py`, update the import line:

```python
from careeros.core.models import Company, Job, OutreachMessage, Person, Profile
```

to:

```python
from careeros.core.models import Company, Job, OutreachMessage, Person, PolicyConfig, Profile
```

Then add this test inside `class TestOutreachSend:` (after `test_resend_preserves_existing_referral_state`):

```python
    def test_policy_blocked_company_exits_1_and_does_not_send(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(storage)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message") as mock_gen, \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        assert "blocked by policy" in result.output.lower()
        mock_gen.assert_not_called()
        mock_send.assert_not_called()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "policy_blocked" in log_content
```

(`_setup_workspace` creates a `Job` with `company="Acme Corp"`, matching the blocked-company rule.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_outreach_cmd.py::TestOutreachSend::test_policy_blocked_company_exits_1_and_does_not_send -v`
Expected: FAIL — `mock_gen.assert_not_called()` fails (draft generation currently proceeds unconditionally).

- [ ] **Step 3: Add the policy check to `outreach_cmd.py`**

Update the import lines near the top of `careeros/cli/outreach_cmd.py`:

```python
from careeros.core.models import Company, Goals, Job, OutreachMessage, Person, Profile
from careeros.mailer import send_email
```

to:

```python
from careeros.core.models import Company, Goals, Job, OutreachMessage, Person, PolicyConfig, Profile
from careeros.core.policy_engine import PolicyEngine
from careeros.mailer import send_email
```

Then find:

```python
    try:
        job_obj = Job.load(runtime.storage, job)
        person_obj = Person.load(runtime.storage, person)
        company_obj = Company.load(runtime.storage, person_obj.company_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job, person, or company not found.[/red]")
        raise typer.Exit(1)

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)
```

Replace with:

```python
    try:
        job_obj = Job.load(runtime.storage, job)
        person_obj = Person.load(runtime.storage, person)
        company_obj = Company.load(runtime.storage, person_obj.company_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job, person, or company not found.[/red]")
        raise typer.Exit(1)

    policy_engine = PolicyEngine(PolicyConfig.load(runtime.storage))
    policy_result = policy_engine.check_job(job_obj)
    if policy_result.blocked:
        runtime.record_activity(runtime.new_event(
            "policy_blocked", "outreach",
            "Blocked by policy (" + policy_result.rule + "): " + job_obj.company + " — " + job_obj.title,
            status="failed", entity_type="job", entity_id=job_obj.id,
        ))
        rprint("[red]Blocked by policy (" + policy_result.rule + "). Edit config/policies.json to change this.[/red]")
        raise typer.Exit(1)

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_outreach_cmd.py -v`
Expected: All tests PASS, including the new one.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/outreach_cmd.py tests/test_outreach_cmd.py
git commit -m "feat: enforce PolicyEngine in outreach send (Phase 9a)

A job matching a policy rule (e.g. its company is blocked) never gets
an outreach draft generated or an ActionProposal built -- outreach
send exits with a policy_blocked activity event before any LLM call.

This completes Phase 9a. Policy Engine now gates every irreversible
external action CareerOS can take. Phase 9b (content sanitization)
and Phase 9c (browser isolation) are next per ROADMAP.md before the
discover-and-apply opt-in gate can be lifted."
```
