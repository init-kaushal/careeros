# CareerOS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give CareerOS a tested Python core and thin CLI that make a workspace versioned, identifiable, validatable and auditable: stable IDs, a validator, an application state machine with atomic serialised transitions, an append-only hash-chained ledger, and safe migration and upgrade with verified backups.

**Architecture:** `careeros/core/` holds all logic (models, ids, versions, workspace IO and lock, ledger, state machine, validation, migration); `careeros/cli/` holds thin Typer wrappers. Markdown with YAML frontmatter stays canonical; `ledger.jsonl` is the machine audit log and `activity.md` stays the human log. Every mutating operation runs under one workspace lock and writes files by temp file, `fsync` and atomic rename.

**Tech Stack:** Python 3.11+, Typer, Rich, PyYAML (`safe_load` only), pytest, stdlib `dataclasses`, `fcntl`/`msvcrt`, `hashlib`, `json`.

**Spec:** `docs/superpowers/specs/2026-10-05-foundation-design.md` (revised 2026-10-06). Executors read it alongside this plan.

## Global Constraints

- Package version becomes **0.3.0**; `careeros.__version__` is read from package metadata (single source). `SCHEMA_VERSION = 1`.
- Timestamps everywhere (`created_at`, `updated_at`, `archived_at`, ledger `ts`, backup manifests) are **full UTC ISO-8601 with seconds and trailing `Z`**, produced only by `careeros.core.models.utc_now()`. Code must call it as `models.utc_now()` (module attribute) so tests can replace it.
- IDs are `<3-letter prefix>_<10 chars of lowercase Crockford base32>`; the prefix table has exactly **27** unique entries; this sub-project uses `job` (`job`) and `event` (`evt`).
- Status values are exactly the 17 names in the spec, in lifecycle order. `APPLIED` is reachable only from `APPROVAL_REQUIRED` and only when the latest approval decision since the job last entered `APPROVAL_REQUIRED` is `approved`.
- Ledger: `ledger.jsonl`, one JSON object per line, fields `id, seq, ts, type, actor, entity, prev_state, new_state, action, reason, approval, artifacts, source, prev`; `prev` is the SHA-256 hex of the previous line's bytes **excluding its trailing newline** (64 zeros for the first). Event `type` matches `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`. Actor matches `^(user|system|agent:[A-Za-z0-9._-]+)$`.
- Reserved event types that `careeros ledger append` must refuse: `application.approved`, `application.approval_denied`, `job.status_changed`, `job.status_corrected`, `job.transition_rejected`, `job.archived`, `job.unarchived`, `job.imported`, and anything starting `workspace.`.
- Concurrency: one workspace lock at `.careeros/lock`, exclusive across processes and threads, re-entrant within a thread, 10 s default timeout with the message "another careeros command is running in this workspace; try again in a moment".
- All user-file writes use `atomic_write_bytes` (temp file in the same directory, `fsync`, `os.replace`). Job body bytes are never altered except the `- **Status:**` display bullet by `transition`.
- Mutating commands refuse when the workspace schema or `framework_version` is newer than the installed CareerOS.
- Exit codes: 0 success; 1 operation failed or validation errors; 2 usage error or confirmation required.
- The real `~/Projects/job-search` workspace is **never modified**; all migration tests and acceptance steps use a temporary copy.
- Failure and decline paths are logged (rejected transitions, declined approvals, declined migrate/upgrade when a ledger exists); usage errors are not.
- Existing 31 tests keep passing unchanged. Baseline before this plan: **31 passed**. Commands run from `/Users/kaushal/Projects/careeros`; tests with `.venv/bin/python -m pytest -q`.
- Every commit message ends with a blank line then:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y`.

## Review Focus

1. **Crash between the job write and the ledger append** (process killed, not an exception): the job is in the new state but the ledger is behind. Expected: `validate` warns `LED005`, and retrying the same transition repairs the ledger with one `source: recovery` event and no state change. (Task 4, Task 5)
2. **Pipeline and job drifting apart inside one transition:** the job file is written but the pipeline write or ledger append fails. Expected: both files return to their original bytes. (Task 4)
3. **Migration run twice, or after the user decided but before the plan was applied:** a second run must be a no-op; a file changed between planning and applying must abort. (Task 6)
4. **A job directory deleted by hand after the ledger recorded it:** historical verification must report it with a fix that says to archive instead. (Task 5)
5. **Legacy data that does not fit the parser:** a heading without ` at `, a missing `URL` or `Discovered` bullet, an unknown status, a title that itself contains ` at `. Expected: reported per file, nothing written, titles split on the last ` at `. (Task 6)

---

### Task 1: Version, models and ID primitives

**Files:**
- Modify: `pyproject.toml` (version, dependency)
- Modify: `careeros/__init__.py`
- Create: `careeros/core/__init__.py` (empty), `careeros/core/ids.py`, `careeros/core/versions.py`, `careeros/core/models.py`
- Modify: `tests/conftest.py` (append a `clock` fixture)
- Test: `tests/test_core_primitives.py`

**Interfaces:**
- Produces:
  - `ids.PREFIXES: dict[str, str]`, `ids.new_id(kind: str, existing: Iterable[str] = ()) -> str`, `ids.is_valid_id(kind: str, value: object) -> bool`
  - `versions.SCHEMA_VERSION: int`, `versions.installed_version() -> str`, `versions.parse_version(value: str) -> tuple[int, int, int]` (raises `ValueError`), `versions.compare_versions(a: str, b: str) -> int` (-1, 0, 1)
  - `models.State` (str Enum, 17 members), `models.Approval` (str Enum), `models.utc_now() -> str`, `models.is_utc_timestamp(value: object) -> bool`, `models.date_to_utc(date_str: str) -> str`, `models.WorkspaceMeta`, `models.Issue`, `models.Job` (with `.slug`)
  - pytest fixture `clock` (replaces `models.utc_now` with a counter producing `2026-10-06T09:MM:SSZ`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_primitives.py`:

```python
import re
import tomllib
from pathlib import Path

import pytest

import careeros
from careeros.core import ids, models, versions
from careeros.core.models import State


def test_prefix_table_has_27_unique_three_letter_prefixes() -> None:
    assert len(ids.PREFIXES) == 27
    assert len(set(ids.PREFIXES.values())) == 27
    assert all(len(p) == 3 and p.isalpha() and p.islower() for p in ids.PREFIXES.values())
    assert ids.PREFIXES["job"] == "job"
    assert ids.PREFIXES["event"] == "evt"


def test_new_id_shape_and_validity() -> None:
    value = ids.new_id("job")
    assert re.fullmatch(r"job_[0-9a-hjkmnp-tv-z]{10}", value)
    assert ids.is_valid_id("job", value)
    assert not ids.is_valid_id("event", value)
    assert not ids.is_valid_id("job", "job_SHORT")
    assert not ids.is_valid_id("job", None)


def test_new_id_retries_on_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = iter([b"\x00" * 10, b"\x01" * 10])
    monkeypatch.setattr(ids.secrets, "token_bytes", lambda n: next(chunks))
    assert ids.new_id("job", existing={"job_0000000000"}) == "job_1111111111"


def test_new_id_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown entity kind"):
        ids.new_id("spaceship")


def test_parse_and_compare_versions() -> None:
    assert versions.parse_version("0.3.0") == (0, 3, 0)
    assert versions.parse_version("1.2.3.dev4") == (1, 2, 3)
    assert versions.compare_versions("0.3.0", "0.2.9") == 1
    assert versions.compare_versions("0.3.0", "0.3.0.dev1") == 0
    assert versions.compare_versions("0.2.0", "0.10.0") == -1


@pytest.mark.parametrize("bad", ["", "abc", "1.2", "v1.2.3"])
def test_parse_version_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        versions.parse_version(bad)


def test_utc_now_has_full_iso_shape() -> None:
    assert models.is_utc_timestamp(models.utc_now())


@pytest.mark.parametrize(
    "bad",
    ["2026-10-06", "2026-10-06T09:15:00", "2026-10-06T09:15:00+00:00", "2026-13-01T00:00:00Z", None, 5],
)
def test_is_utc_timestamp_rejects_other_shapes(bad: object) -> None:
    assert not models.is_utc_timestamp(bad)


def test_date_to_utc_makes_midnight_and_validates() -> None:
    assert models.date_to_utc("2026-10-01") == "2026-10-01T00:00:00Z"
    with pytest.raises(ValueError):
        models.date_to_utc("10/01/2026")


def test_state_enum_lists_17_states_in_lifecycle_order() -> None:
    assert [s.name for s in State] == [
        "DISCOVERED", "EVALUATED", "SHORTLISTED", "RESEARCHED", "PREPARING",
        "READY_TO_APPLY", "APPROVAL_REQUIRED", "APPLIED", "RECRUITER_REPLIED",
        "SCREEN", "TECHNICAL", "HM", "FINAL", "OFFER", "ACCEPTED", "REJECTED", "WITHDRAWN",
    ]


def test_installed_version_comes_from_metadata_and_matches_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert careeros.__version__ == pyproject["project"]["version"] == versions.installed_version()
    assert versions.SCHEMA_VERSION == 1


def test_clock_fixture_controls_utc_now(clock) -> None:
    first, second = models.utc_now(), models.utc_now()
    assert first == "2026-10-06T09:00:01Z"
    assert second == "2026-10-06T09:00:02Z"
```

Append to `tests/conftest.py`:

```python


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Replace models.utc_now with a deterministic counter, one second per call."""
    state = {"n": 0}

    def tick() -> str:
        state["n"] += 1
        n = state["n"]
        return f"2026-10-06T09:{n // 60:02d}:{n % 60:02d}Z"

    monkeypatch.setattr("careeros.core.models.utc_now", tick)
    return tick
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_core_primitives.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'careeros.core'`).

- [ ] **Step 3: Implement**

In `pyproject.toml` change `version = "0.2.0"` to `version = "0.3.0"` and add `"pyyaml>=6.0",` to `dependencies` so it reads:

```toml
dependencies = [
    "typer[all]>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
]
```

Replace the contents of `careeros/__init__.py` with:

```python
"""CareerOS: a local-first career workspace."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("careeros")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
```

Create empty `careeros/core/__init__.py`.

Create `careeros/core/ids.py`:

```python
"""Stable entity IDs: <prefix>_<10 lowercase Crockford base32 characters>."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lowercase, no i l o u

PREFIXES: dict[str, str] = {
    "profile": "prf",
    "experience": "exp",
    "achievement": "ach",
    "project": "prj",
    "skill": "skl",
    "education": "edu",
    "certification": "crt",
    "goal": "gol",
    "preference": "pre",
    "constraint": "con",
    "story": "sty",
    "evidence": "evd",
    "company": "cmp",
    "person": "per",
    "job": "job",
    "requirement": "req",
    "evaluation": "evl",
    "application": "app",
    "outreach": "otr",
    "followup": "flw",
    "interview": "int",
    "round": "rnd",
    "offer": "off",
    "document": "doc",
    "decision": "dec",
    "event": "evt",
    "gap": "gap",
}

_BODY = "[0-9a-hjkmnp-tv-z]{10}"


def new_id(kind: str, existing: Iterable[str] = ()) -> str:
    try:
        prefix = PREFIXES[kind]
    except KeyError:
        raise ValueError(f"unknown entity kind {kind!r}") from None
    taken = set(existing)
    for _ in range(100):
        body = "".join(_ALPHABET[b & 31] for b in secrets.token_bytes(10))
        candidate = f"{prefix}_{body}"
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not generate a unique id")


def is_valid_id(kind: str, value: object) -> bool:
    prefix = PREFIXES.get(kind)
    return bool(prefix) and isinstance(value, str) and re.fullmatch(f"{prefix}_{_BODY}", value) is not None
```

Create `careeros/core/versions.py`:

```python
"""Framework and schema version handling."""

from __future__ import annotations

import re

import careeros

SCHEMA_VERSION = 1

_NUMERIC = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def installed_version() -> str:
    return careeros.__version__


def parse_version(value: str) -> tuple[int, int, int]:
    match = _NUMERIC.match(str(value))
    if not match:
        raise ValueError(f"invalid version {value!r}: expected MAJOR.MINOR.PATCH")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def compare_versions(a: str, b: str) -> int:
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)
```

Create `careeros/core/models.py`:

```python
"""Core data shapes: states, approvals, workspace metadata, jobs, validation issues."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class State(str, Enum):
    DISCOVERED = "DISCOVERED"
    EVALUATED = "EVALUATED"
    SHORTLISTED = "SHORTLISTED"
    RESEARCHED = "RESEARCHED"
    PREPARING = "PREPARING"
    READY_TO_APPLY = "READY_TO_APPLY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPLIED = "APPLIED"
    RECRUITER_REPLIED = "RECRUITER_REPLIED"
    SCREEN = "SCREEN"
    TECHNICAL = "TECHNICAL"
    HM = "HM"
    FINAL = "FINAL"
    OFFER = "OFFER"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class Approval(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    APPROVED = "approved"
    DENIED = "denied"


_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TS_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now() -> str:
    """The only source of timestamps. Call as `models.utc_now()` so tests can replace it."""
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not _TS_SHAPE.match(value):
        return False
    try:
        datetime.strptime(value, _TS_FORMAT)
    except ValueError:
        return False
    return True


def date_to_utc(date_str: str) -> str:
    """Legacy date-only value to midnight UTC. Raises ValueError for anything but YYYY-MM-DD."""
    datetime.strptime(date_str, "%Y-%m-%d")
    return f"{date_str}T00:00:00Z"


@dataclass(frozen=True)
class WorkspaceMeta:
    schema_version: int
    framework_version: str
    created_at: str
    updated_at: str
    runtimes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    code: str
    path: str
    message: str
    fix: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class Job:
    id: str
    status: State
    company: str
    title: str
    url: str | None
    created_at: str
    updated_at: str
    path: Path
    archived: bool = False
    archived_at: str | None = None
    frontmatter: dict = field(default_factory=dict)
    body: str = ""

    @property
    def slug(self) -> str:
        return self.path.parent.name
```

Reinstall so package metadata reports 0.3.0: run `.venv/bin/python -m pip install -q -e .`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: the 31 baseline tests plus the new ones all pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml careeros/__init__.py careeros/core tests/conftest.py tests/test_core_primitives.py
git commit -m "core: add ids, versions, models and a single-source package version (0.3.0)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 2: Workspace IO: atomic writes, lock, frontmatter, metadata, job lookup

**Files:**
- Create: `careeros/core/workspace.py`
- Test: `tests/test_core_workspace.py`

**Interfaces:**
- Consumes: `models.WorkspaceMeta/Job/State/utc_now`, `versions.SCHEMA_VERSION/installed_version/parse_version/compare_versions`.
- Produces (all in `careeros.core.workspace`):
  - constants `META_REL`, `LOCK_REL`, `RECOVERY_REL`, `BACKUPS_REL`, `LEDGER_REL` (all `Path`); `JOB_KEYS` tuple
  - `WorkspaceError(Exception)`, `LockTimeout(WorkspaceError)`
  - `atomic_write_bytes(path: Path, data: bytes) -> None`
  - `class WorkspaceLock(root: Path, timeout: float = 10.0)` context manager (re-entrant per thread)
  - `split_frontmatter(text: str) -> tuple[dict | None, str]`, `join_frontmatter(data: dict, body: str) -> str`
  - `is_workspace_root(path: Path) -> bool`, `find_workspace(explicit=None, start=None) -> Path`
  - `load_meta(root: Path) -> WorkspaceMeta | None`, `save_meta(root: Path, meta: WorkspaceMeta) -> None`, `ensure_writable(root: Path) -> WorkspaceMeta | None`
  - `job_files(root: Path) -> list[Path]`, `read_job(path: Path) -> Job`, `resolve_job(root: Path, ref: str) -> Job`
  - `normalize_value(value)` (YAML datetime/date to canonical string)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_workspace.py`:

```python
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from careeros.core import versions
from careeros.core import workspace as ws
from careeros.core.models import State, WorkspaceMeta

TS = "2026-10-06T09:15:00Z"


def _meta(schema: int = 1, framework: str = "0.3.0") -> WorkspaceMeta:
    return WorkspaceMeta(schema, framework, TS, TS, ("claude",))


def _write_job(root: Path, slug: str, job_id: str = "job_aaaaaaaaaa", status: str = "DISCOVERED") -> Path:
    fm = {
        "id": job_id, "type": "job", "schema": 1, "status": status,
        "company": "Acme", "title": "Backend Engineer", "url": f"https://x.test/{slug}",
        "created_at": TS, "updated_at": TS,
    }
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ws.join_frontmatter(fm, "# Backend Engineer at Acme\n"), encoding="utf-8")
    return path


# --- atomic writes -----------------------------------------------------------

def test_atomic_write_creates_and_overwrites_without_leaving_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "a.txt"
    ws.atomic_write_bytes(target, b"one")
    assert target.read_bytes() == b"one"
    ws.atomic_write_bytes(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["a.txt"]


def test_atomic_write_preserves_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x")
    target.chmod(0o640)
    ws.atomic_write_bytes(target, b"y")
    assert target.stat().st_mode & 0o777 == 0o640


def test_failed_atomic_write_leaves_target_unchanged_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"original")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        ws.atomic_write_bytes(target, b"new")
    monkeypatch.undo()
    assert target.read_bytes() == b"original"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


# --- frontmatter --------------------------------------------------------------

def test_frontmatter_round_trip_keeps_body_byte_identical() -> None:
    body = "# Title at Co\n\n- **URL:** https://x.test/a?b=1\n---\nnot a fence in the body — ✓\n\ttabbed\n"
    data = {"id": "job_0000000000", "title": "Senior: Engineer (Go) — Platform", "created_at": TS, "archived": False}
    text = ws.join_frontmatter(data, body)
    parsed, rest = ws.split_frontmatter(text)
    assert text.startswith("---\n")
    assert rest == body
    assert parsed == data


def test_split_without_frontmatter_returns_none_and_full_text() -> None:
    assert ws.split_frontmatter("# Just a heading\n") == (None, "# Just a heading\n")


def test_split_invalid_yaml_and_non_mapping_raise() -> None:
    with pytest.raises(ws.WorkspaceError):
        ws.split_frontmatter("---\nkey: [unclosed\n---\nbody\n")
    with pytest.raises(ws.WorkspaceError, match="mapping"):
        ws.split_frontmatter("---\n- a\n- b\n---\nbody\n")


# --- metadata and the write guard ------------------------------------------------

def test_meta_round_trip_and_missing(tmp_path: Path) -> None:
    assert ws.load_meta(tmp_path) is None
    ws.save_meta(tmp_path, _meta())
    assert ws.load_meta(tmp_path) == _meta()
    assert (tmp_path / ".careeros" / "workspace.yaml").is_file()


def test_load_meta_rejects_bad_content(tmp_path: Path) -> None:
    path = tmp_path / ".careeros" / "workspace.yaml"
    path.parent.mkdir()
    path.write_text("schema_version: 1\n")
    with pytest.raises(ws.WorkspaceError, match="missing key"):
        ws.load_meta(tmp_path)
    path.write_text(f"schema_version: one\nframework_version: 0.3.0\ncreated_at: '{TS}'\nupdated_at: '{TS}'\n")
    with pytest.raises(ws.WorkspaceError, match="schema_version"):
        ws.load_meta(tmp_path)
    path.write_text(f"schema_version: 1\nframework_version: abc\ncreated_at: '{TS}'\nupdated_at: '{TS}'\n")
    with pytest.raises(ws.WorkspaceError, match="MAJOR.MINOR.PATCH"):
        ws.load_meta(tmp_path)


def test_ensure_writable_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "installed_version", lambda: "0.3.0")
    assert ws.ensure_writable(tmp_path) is None  # legacy workspace: no metadata, nothing to refuse
    ws.save_meta(tmp_path, _meta(framework="0.3.0"))
    assert ws.ensure_writable(tmp_path) is not None
    ws.save_meta(tmp_path, _meta(framework="0.2.0"))
    assert ws.ensure_writable(tmp_path) is not None  # older workspace: upgrade available, still writable
    ws.save_meta(tmp_path, _meta(framework="0.4.0"))
    with pytest.raises(ws.WorkspaceError, match="newer than the installed"):
        ws.ensure_writable(tmp_path)
    ws.save_meta(tmp_path, _meta(schema=2, framework="0.3.0"))
    with pytest.raises(ws.WorkspaceError, match="schema 2 is newer"):
        ws.ensure_writable(tmp_path)


# --- discovery -----------------------------------------------------------------

def test_find_workspace_explicit_parent_walk_and_failure(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "jobs").mkdir(parents=True)
    (root / "profile.md").write_text("# p\n")
    nested = root / "jobs" / "deep"
    nested.mkdir()
    assert ws.find_workspace(explicit=root) == root.resolve()
    assert ws.find_workspace(start=nested) == root.resolve()
    with pytest.raises(ws.WorkspaceError, match="not a CareerOS workspace"):
        ws.find_workspace(explicit=tmp_path / "elsewhere")
    with pytest.raises(ws.WorkspaceError, match="no CareerOS workspace found"):
        ws.find_workspace(start=tmp_path / "nothing-here")


def test_find_workspace_recognises_meta_file_alone(tmp_path: Path) -> None:
    ws.save_meta(tmp_path, _meta())
    assert ws.is_workspace_root(tmp_path)


# --- jobs ------------------------------------------------------------------------

def test_read_job_and_resolve_by_id_slug_and_path(tmp_path: Path) -> None:
    path = _write_job(tmp_path, "acme-backend")
    job = ws.read_job(path)
    assert job.id == "job_aaaaaaaaaa" and job.status is State.DISCOVERED and job.slug == "acme-backend"
    assert job.body == "# Backend Engineer at Acme\n"
    for ref in ("job_aaaaaaaaaa", "acme-backend", "jobs/discovered/acme-backend", str(path.parent)):
        assert ws.resolve_job(tmp_path, ref).path == path
    with pytest.raises(ws.WorkspaceError, match="no job matches"):
        ws.resolve_job(tmp_path, "nope")


def test_read_job_reports_missing_keys_bad_status_and_no_frontmatter(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs" / "discovered" / "legacy" / "job.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# T at C\n")
    with pytest.raises(ws.WorkspaceError, match="no frontmatter"):
        ws.read_job(legacy)
    partial = tmp_path / "jobs" / "discovered" / "partial" / "job.md"
    partial.parent.mkdir(parents=True)
    partial.write_text(ws.join_frontmatter({"id": "job_aaaaaaaaaa"}, "x\n"))
    with pytest.raises(ws.WorkspaceError, match="missing"):
        ws.read_job(partial)
    bad = _write_job(tmp_path, "bad", status="NONSENSE")
    with pytest.raises(ws.WorkspaceError, match="invalid status"):
        ws.read_job(bad)


# --- lock ------------------------------------------------------------------------

def test_lock_is_reentrant_within_a_thread(tmp_path: Path) -> None:
    with ws.WorkspaceLock(tmp_path, timeout=0.5):
        with ws.WorkspaceLock(tmp_path, timeout=0.5):
            pass
    with ws.WorkspaceLock(tmp_path, timeout=0.5):
        pass
    assert (tmp_path / ".careeros" / "lock").exists()


_HOLDER = """
import sys, time
from pathlib import Path
from careeros.core.workspace import WorkspaceLock
with WorkspaceLock(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def test_lock_excludes_another_process_and_times_out_with_clear_message(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(tmp_path), "2"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "locked"
        with pytest.raises(ws.LockTimeout, match="another careeros command is running"):
            with ws.WorkspaceLock(tmp_path, timeout=0.3):
                pass
    finally:
        proc.wait(timeout=10)
    with ws.WorkspaceLock(tmp_path, timeout=1):
        pass


def test_lock_serialises_threads_so_no_update_is_lost(tmp_path: Path) -> None:
    counter = tmp_path / "counter.txt"
    counter.write_text("0")

    def worker() -> None:
        for _ in range(20):
            with ws.WorkspaceLock(tmp_path, timeout=10):
                counter.write_text(str(int(counter.read_text()) + 1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter.read_text() == "80"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_core_workspace.py`
Expected: FAIL (`ImportError: cannot import name 'workspace'`).

- [ ] **Step 3: Implement `careeros/core/workspace.py`**

```python
"""Workspace discovery, metadata, frontmatter, atomic writes, the workspace lock, job lookup."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from careeros.core import versions
from careeros.core.models import Job, State, WorkspaceMeta

META_REL = Path(".careeros") / "workspace.yaml"
LOCK_REL = Path(".careeros") / "lock"
RECOVERY_REL = Path(".careeros") / "recovery"
BACKUPS_REL = Path(".careeros") / "backups"
LEDGER_REL = Path("ledger.jsonl")

JOB_KEYS = ("id", "type", "status", "company", "title", "created_at", "updated_at")


class WorkspaceError(Exception):
    """A problem with the workspace that the user can act on."""


class LockTimeout(WorkspaceError):
    """The workspace lock could not be acquired in time."""


# --- atomic writes ---------------------------------------------------------------

def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, fsync, then atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = path.stat().st_mode & 0o777
    else:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(path.parent)


# --- workspace lock --------------------------------------------------------------

try:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    _CONTENDED: tuple[type[BaseException], ...] = (BlockingIOError,)
except ImportError:  # Windows
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _CONTENDED = (OSError,)

_local = threading.local()


class WorkspaceLock:
    """Exclusive across processes and threads; re-entrant within a thread."""

    def __init__(self, root: Path, timeout: float = 10.0) -> None:
        self.root = Path(root)
        self.timeout = timeout
        self._path = self.root / LOCK_REL

    def _held(self) -> dict[str, list[int]]:
        if not hasattr(_local, "held"):
            _local.held = {}
        return _local.held

    def __enter__(self) -> "WorkspaceLock":
        key = str(self._path.resolve())
        held = self._held()
        if key in held:
            held[key][1] += 1
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _try_lock(fd)
                break
            except _CONTENDED:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(
                        "another careeros command is running in this workspace; try again in a moment"
                    ) from None
                time.sleep(0.05)
        held[key] = [fd, 1]
        return self

    def __exit__(self, *exc: object) -> bool:
        key = str(self._path.resolve())
        held = self._held()
        entry = held[key]
        entry[1] -= 1
        if entry[1] == 0:
            try:
                _unlock(entry[0])
            finally:
                os.close(entry[0])
                del held[key]
        return False


# --- frontmatter -----------------------------------------------------------------

_FENCE_LINE = re.compile(r"^---[ \t]*$", re.M)


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter dict, body). The body is exactly the text after the closing fence."""
    if not text.startswith("---\n"):
        return None, text
    match = _FENCE_LINE.search(text, 4)
    if not match:
        return None, text
    block = text[4:match.start()]
    rest = match.end()
    if text[rest:rest + 1] == "\n":
        rest += 1
    try:
        data = yaml.safe_load(block) if block.strip() else {}
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError("frontmatter must be a mapping of keys to values")
    return data, text[rest:]


def join_frontmatter(data: dict, body: str) -> str:
    dumped = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10_000
    )
    return f"---\n{dumped}---\n{body}"


def normalize_value(value: object) -> object:
    """YAML turns unquoted timestamps into datetime/date; bring them back to canonical strings."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    return value


# --- discovery, metadata, guard --------------------------------------------------

def is_workspace_root(path: Path) -> bool:
    path = Path(path)
    return (path / META_REL).is_file() or ((path / "profile.md").is_file() and (path / "jobs").is_dir())


def find_workspace(explicit: Path | str | None = None, start: Path | None = None) -> Path:
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not is_workspace_root(root):
            raise WorkspaceError(
                f"{root} is not a CareerOS workspace (no .careeros/workspace.yaml, and no profile.md with jobs/)"
            )
        return root
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if is_workspace_root(candidate):
            return candidate
    raise WorkspaceError("no CareerOS workspace found here or in any parent directory; use --workspace PATH")


def load_meta(root: Path) -> WorkspaceMeta | None:
    path = Path(root) / META_REL
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"{META_REL}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"{META_REL}: expected a mapping of keys to values")
    for key in ("schema_version", "framework_version", "created_at", "updated_at"):
        if key not in data:
            raise WorkspaceError(f"{META_REL}: missing key {key!r}")
    schema = data["schema_version"]
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise WorkspaceError(f"{META_REL}: schema_version must be an integer")
    framework = str(data["framework_version"])
    try:
        versions.parse_version(framework)
    except ValueError as exc:
        raise WorkspaceError(f"{META_REL}: {exc}") from exc
    return WorkspaceMeta(
        schema_version=schema,
        framework_version=framework,
        created_at=str(normalize_value(data["created_at"])),
        updated_at=str(normalize_value(data["updated_at"])),
        runtimes=tuple(str(r) for r in (data.get("runtimes") or ())),
    )


def save_meta(root: Path, meta: WorkspaceMeta) -> None:
    data = {
        "schema_version": meta.schema_version,
        "framework_version": meta.framework_version,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "runtimes": list(meta.runtimes),
    }
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    atomic_write_bytes(Path(root) / META_REL, text.encode("utf-8"))


def ensure_writable(root: Path) -> WorkspaceMeta | None:
    """Refuse to change a workspace that a newer CareerOS has touched. Legacy workspaces return None."""
    meta = load_meta(root)
    if meta is None:
        return None
    if meta.schema_version > versions.SCHEMA_VERSION:
        raise WorkspaceError(
            f"workspace schema {meta.schema_version} is newer than this CareerOS supports "
            f"({versions.SCHEMA_VERSION}); upgrade CareerOS before changing it"
        )
    installed = versions.installed_version()
    if versions.compare_versions(installed, meta.framework_version) < 0:
        raise WorkspaceError(
            f"workspace was last updated by CareerOS {meta.framework_version}, which is newer than "
            f"the installed {installed}; upgrade CareerOS before changing it"
        )
    return meta


# --- jobs ------------------------------------------------------------------------

def job_files(root: Path) -> list[Path]:
    base = Path(root) / "jobs" / "discovered"
    return sorted(base.glob("*/job.md")) if base.is_dir() else []


def read_job(path: Path) -> Job:
    path = Path(path)
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if fm is None:
        raise WorkspaceError(f"{path}: no frontmatter; run `careeros migrate`")
    fm = {k: normalize_value(v) for k, v in fm.items()}
    missing = [k for k in JOB_KEYS if k not in fm]
    if missing:
        raise WorkspaceError(f"{path}: frontmatter is missing {', '.join(missing)}")
    try:
        status = State(fm["status"])
    except ValueError:
        raise WorkspaceError(f"{path}: invalid status {fm['status']!r}") from None
    return Job(
        id=str(fm["id"]),
        status=status,
        company=str(fm["company"]),
        title=str(fm["title"]),
        url=fm.get("url"),
        created_at=str(fm["created_at"]),
        updated_at=str(fm["updated_at"]),
        path=path,
        archived=bool(fm.get("archived", False)),
        archived_at=fm.get("archived_at"),
        frontmatter=fm,
        body=body,
    )


def resolve_job(root: Path, ref: str) -> Job:
    """Find a job by ID, by directory slug, or by path."""
    root = Path(root)
    matches: list[Path] = []
    for path in job_files(root):
        if ref == path.parent.name:
            matches.append(path)
            continue
        try:
            if read_job(path).id == ref:
                matches.append(path)
        except WorkspaceError:
            continue  # un-migrated or broken files cannot be matched by ID
    if not matches:
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_dir() and (candidate / "job.md").is_file():
            matches.append(candidate / "job.md")
        elif candidate.is_file() and candidate.name == "job.md":
            matches.append(candidate)
    unique = list(dict.fromkeys(matches))
    if not unique:
        raise WorkspaceError(f"no job matches {ref!r}")
    if len(unique) > 1:
        raise WorkspaceError(f"{ref!r} matches more than one job; use the job ID")
    return read_job(unique[0])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: every test passes, old and new.

- [ ] **Step 5: Commit**

```bash
git add careeros/core/workspace.py tests/test_core_workspace.py
git commit -m "core: workspace IO with atomic writes, a re-entrant lock, frontmatter and metadata" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 3: Ledger

**Files:**
- Create: `careeros/core/ledger.py`
- Test: `tests/test_core_ledger.py`

**Interfaces:**
- Consumes: `ids.new_id`, `models.utc_now`, `models.Issue`, `workspace.LEDGER_REL/WorkspaceError/WorkspaceLock`.
- Produces (all in `careeros.core.ledger`):
  - `ZERO_HASH: str`, `FIELDS: tuple[str, ...]`, `RESERVED_TYPES: frozenset[str]`
  - `class LedgerError(WorkspaceError)`
  - `is_reserved(event_type: str) -> bool`
  - `append_events(root: Path, specs: list[dict]) -> list[dict]` (batch, single write, truncates back on failure)
  - `append_event(root: Path, *, type: str, actor: str, action: str, entity: str | None = None, prev_state: str | None = None, new_state: str | None = None, approval: str = "not_required", artifacts: Sequence[str] = (), source: str | None = None, reason: str | None = None) -> dict`
  - `read_events(root: Path) -> list[dict]`
  - `verify_chain(root: Path) -> list[Issue]` (codes `LED001`, `LED002`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_ledger.py`:

```python
import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from careeros.core import ledger


def _append(root: Path, n: int = 1, **overrides: object) -> list[dict]:
    out = []
    for i in range(n):
        spec = {"type": "note.added", "actor": "system", "action": f"event {i}"}
        spec.update(overrides)
        out.append(ledger.append_event(root, **spec))
    return out


def _rewrite_lines(root: Path, edit) -> None:
    path = root / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")


def test_append_creates_contiguous_seq_and_hash_chain(tmp_path: Path, clock) -> None:
    first, second = _append(tmp_path, 2)
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["prev"] == "0" * 64
    assert list(first)[:3] == ["id", "seq", "ts"]
    assert first["ts"] == "2026-10-06T09:00:01Z"
    assert first["id"].startswith("evt_")
    lines = (tmp_path / "ledger.jsonl").read_bytes().split(b"\n")
    assert lines[-1] == b""
    assert second["prev"] == hashlib.sha256(lines[0]).hexdigest()
    assert set(first) == set(ledger.FIELDS)


def test_verify_clean_and_missing_ledgers_have_no_issues(tmp_path: Path) -> None:
    assert ledger.verify_chain(tmp_path) == []
    _append(tmp_path, 3)
    assert ledger.verify_chain(tmp_path) == []


def test_editing_a_past_line_breaks_the_chain(tmp_path: Path) -> None:
    _append(tmp_path, 3)

    def edit(lines: list[str]) -> list[str]:
        event = json.loads(lines[0])
        event["action"] = "tampered"
        lines[0] = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return lines

    _rewrite_lines(tmp_path, edit)
    issues = ledger.verify_chain(tmp_path)
    assert [i.code for i in issues] == ["LED002"]
    assert "line 2" in issues[0].message


def test_deleting_or_reordering_lines_is_detected(tmp_path: Path) -> None:
    _append(tmp_path, 4)
    original = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")

    _rewrite_lines(tmp_path, lambda lines: [lines[0], lines[2], lines[3]])
    assert {i.code for i in ledger.verify_chain(tmp_path)} == {"LED002"}

    (tmp_path / "ledger.jsonl").write_text(original, encoding="utf-8")
    _rewrite_lines(tmp_path, lambda lines: [lines[0], lines[2], lines[1], lines[3]])
    assert {i.code for i in ledger.verify_chain(tmp_path)} == {"LED002"}


def test_torn_last_line_is_reported_and_refuses_further_appends(tmp_path: Path) -> None:
    _append(tmp_path, 2)
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(path.read_bytes() + b'{"id":"evt_partial')
    codes = [i.code for i in ledger.verify_chain(tmp_path)]
    assert "LED001" in codes
    with pytest.raises(ledger.LedgerError, match="partial line"):
        _append(tmp_path)


def test_invalid_json_and_missing_fields_are_reported(tmp_path: Path) -> None:
    (tmp_path / "ledger.jsonl").write_text('not json\n{"seq": 1}\n', encoding="utf-8")
    issues = ledger.verify_chain(tmp_path)
    assert issues and all(i.code == "LED001" for i in issues)
    assert any("line 1" in i.message for i in issues)


def test_batch_append_rolls_back_to_original_bytes_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _append(tmp_path, 1)
    path = tmp_path / "ledger.jsonl"
    before = path.read_bytes()

    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    spec = {"type": "note.added", "actor": "system", "action": "x"}
    with pytest.raises(OSError):
        ledger.append_events(tmp_path, [spec, spec])
    monkeypatch.undo()
    assert path.read_bytes() == before


def test_failed_first_append_leaves_no_ledger_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        _append(tmp_path)
    monkeypatch.undo()
    assert not (tmp_path / "ledger.jsonl").exists()


def test_reserved_types() -> None:
    for reserved in ("workspace.created", "job.status_changed", "application.approved", "job.imported"):
        assert ledger.is_reserved(reserved)
    assert not ledger.is_reserved("note.added")


@pytest.mark.parametrize(
    "spec,match",
    [
        ({"type": "BadType", "actor": "system", "action": "x"}, "type"),
        ({"type": "note.added", "actor": "robot", "action": "x"}, "actor"),
        ({"type": "note.added", "actor": "system", "action": ""}, "action"),
        ({"type": "note.added", "actor": "system", "action": "x", "approval": "maybe"}, "approval"),
        ({"type": "job.status_corrected", "actor": "user", "action": "x"}, "reason"),
        ({"type": "note.added", "actor": "system", "action": "x", "colour": "red"}, "unknown"),
    ],
)
def test_spec_validation(tmp_path: Path, spec: dict, match: str) -> None:
    with pytest.raises(ledger.LedgerError, match=match):
        ledger.append_events(tmp_path, [spec])


def test_read_events_reports_the_bad_line(tmp_path: Path) -> None:
    _append(tmp_path, 1)
    path = tmp_path / "ledger.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
    with pytest.raises(ledger.LedgerError, match="line 2"):
        ledger.read_events(tmp_path)


def test_concurrent_appends_keep_the_chain_intact(tmp_path: Path) -> None:
    def worker() -> None:
        for _ in range(10):
            ledger.append_event(tmp_path, type="note.added", actor="system", action="x")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e["seq"] for e in ledger.read_events(tmp_path)] == list(range(1, 61))
    assert ledger.verify_chain(tmp_path) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_core_ledger.py`
Expected: FAIL (`ImportError: cannot import name 'ledger'`).

- [ ] **Step 3: Implement `careeros/core/ledger.py`**

```python
"""Append-only, hash-chained activity ledger (ledger.jsonl)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from careeros.core import ids, models
from careeros.core.models import Issue
from careeros.core.workspace import LEDGER_REL, WorkspaceError, WorkspaceLock

ZERO_HASH = "0" * 64
APPROVALS = ("not_required", "required", "approved", "denied")
FIELDS = (
    "id", "seq", "ts", "type", "actor", "entity", "prev_state", "new_state",
    "action", "reason", "approval", "artifacts", "source", "prev",
)
RESERVED_TYPES = frozenset({
    "application.approved",
    "application.approval_denied",
    "job.status_changed",
    "job.status_corrected",
    "job.transition_rejected",
    "job.archived",
    "job.unarchived",
    "job.imported",
})
_SPEC_KEYS = {
    "type", "actor", "action", "entity", "prev_state", "new_state",
    "approval", "artifacts", "source", "reason",
}
_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_ACTOR_RE = re.compile(r"^(user|system|agent:[A-Za-z0-9._-]+)$")
_NEEDS_REASON = {"job.status_corrected", "job.transition_rejected"}


class LedgerError(WorkspaceError):
    """The ledger cannot be read or extended safely."""


def is_reserved(event_type: str) -> bool:
    return event_type in RESERVED_TYPES or event_type.startswith("workspace.")


def _line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _canon(event: dict) -> bytes:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _check_spec(spec: dict) -> None:
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        raise LedgerError(f"unknown ledger field(s): {', '.join(sorted(unknown))}")
    if not _TYPE_RE.match(str(spec.get("type", ""))):
        raise LedgerError(f"invalid event type {spec.get('type')!r}: use dotted lower-case names like 'note.added'")
    if not _ACTOR_RE.match(str(spec.get("actor", ""))):
        raise LedgerError(f"invalid actor {spec.get('actor')!r}: use 'user', 'system' or 'agent:<name>'")
    if not str(spec.get("action", "")).strip():
        raise LedgerError("action is required")
    if spec.get("approval", "not_required") not in APPROVALS:
        raise LedgerError(f"invalid approval {spec.get('approval')!r}: use one of {', '.join(APPROVALS)}")
    if spec["type"] in _NEEDS_REASON and not str(spec.get("reason") or "").strip():
        raise LedgerError(f"a reason is required for {spec['type']}")


def _read_lines(root: Path) -> list[bytes]:
    path = Path(root) / LEDGER_REL
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise LedgerError("ledger.jsonl ends with a partial line; run `careeros ledger verify`")
    return data[:-1].split(b"\n")


def read_events(root: Path) -> list[dict]:
    events = []
    for number, raw in enumerate(_read_lines(root), 1):
        try:
            events.append(json.loads(raw))
        except ValueError:
            raise LedgerError(f"ledger.jsonl line {number} is not valid JSON") from None
    return events


def append_events(root: Path, specs: list[dict]) -> list[dict]:
    """Append a batch in one write. On any failure the file is put back exactly as it was."""
    root = Path(root)
    for spec in specs:
        _check_spec(spec)
    with WorkspaceLock(root):
        path = root / LEDGER_REL
        lines = _read_lines(root)
        existed = path.exists()
        original_size = path.stat().st_size if existed else 0
        prev_hash = _line_hash(lines[-1]) if lines else ZERO_HASH
        seq = json.loads(lines[-1])["seq"] if lines else 0
        events: list[dict] = []
        buffer = bytearray()
        for spec in specs:
            seq += 1
            event = {
                "id": ids.new_id("event"),
                "seq": seq,
                "ts": models.utc_now(),
                "type": spec["type"],
                "actor": spec["actor"],
                "entity": spec.get("entity"),
                "prev_state": spec.get("prev_state"),
                "new_state": spec.get("new_state"),
                "action": spec["action"],
                "reason": spec.get("reason"),
                "approval": spec.get("approval", "not_required"),
                "artifacts": list(spec.get("artifacts") or ()),
                "source": spec.get("source"),
                "prev": prev_hash,
            }
            line = _canon(event)
            buffer += line + b"\n"
            prev_hash = _line_hash(line)
            events.append(event)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                view = memoryview(bytes(buffer))
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            _restore_size(path, original_size, existed)
            raise
        return events


def append_event(
    root: Path,
    *,
    type: str,
    actor: str,
    action: str,
    entity: str | None = None,
    prev_state: str | None = None,
    new_state: str | None = None,
    approval: str = "not_required",
    artifacts: Sequence[str] = (),
    source: str | None = None,
    reason: str | None = None,
) -> dict:
    spec = {
        "type": type, "actor": actor, "action": action, "entity": entity,
        "prev_state": prev_state, "new_state": new_state, "approval": approval,
        "artifacts": list(artifacts), "source": source, "reason": reason,
    }
    return append_events(root, [spec])[0]


def _restore_size(path: Path, size: int, existed: bool) -> None:
    if not existed:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    with open(path, "r+b") as handle:
        handle.truncate(size)


def verify_chain(root: Path) -> list[Issue]:
    """Structure, sequence and hash-chain checks. Entity and state checks live in validation."""
    path = Path(root) / LEDGER_REL
    if not path.exists():
        return []
    data = path.read_bytes()
    issues: list[Issue] = []
    if data and not data.endswith(b"\n"):
        issues.append(Issue(
            "error", "LED001", "ledger.jsonl",
            "the ledger ends with a partial line",
            "remove the partial last line, or restore ledger.jsonl from .careeros/backups or version control",
        ))
        lines = data.split(b"\n")
    else:
        lines = data[:-1].split(b"\n") if data else []
    prev_hash = ZERO_HASH
    expected_seq = 1
    for number, raw in enumerate(lines, 1):
        try:
            event = json.loads(raw)
        except ValueError:
            issues.append(Issue(
                "error", "LED001", "ledger.jsonl", f"line {number} is not valid JSON",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
            break
        if not isinstance(event, dict):
            issues.append(Issue("error", "LED001", "ledger.jsonl", f"line {number} is not a JSON object",
                                "restore ledger.jsonl from .careeros/backups or version control"))
            break
        missing = [f for f in FIELDS if f not in event]
        if missing:
            issues.append(Issue(
                "error", "LED001", "ledger.jsonl",
                f"line {number} is missing field(s): {', '.join(missing)}",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
        elif not _TYPE_RE.match(str(event["type"])):
            issues.append(Issue(
                "error", "LED001", "ledger.jsonl", f"line {number} has a malformed type {event['type']!r}",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
        seq = event.get("seq")
        if seq != expected_seq:
            issues.append(Issue(
                "error", "LED002", "ledger.jsonl",
                f"line {number}: expected seq {expected_seq}, found {seq}",
                "a line was removed, added or reordered; restore ledger.jsonl from .careeros/backups or version control",
            ))
        if event.get("prev") != prev_hash:
            issues.append(Issue(
                "error", "LED002", "ledger.jsonl",
                f"line {number}: hash chain broken (an earlier line was changed)",
                "restore ledger.jsonl from .careeros/backups or version control",
            ))
        prev_hash = _line_hash(raw)
        expected_seq = (seq if isinstance(seq, int) else expected_seq) + 1
    return issues
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add careeros/core/ledger.py tests/test_core_ledger.py
git commit -m "core: append-only hash-chained ledger with batch append and rollback" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 4: State machine, transitions, approvals, archive

**Files:**
- Create: `careeros/core/state_machine.py`, `tests/helpers.py`
- Test: `tests/test_core_state_machine.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces (in `careeros.core.state_machine`):
  - `PRE`, `POST` (tuples of `State`), `TERMINAL` (frozenset), `STATUS_BULLET` and `PIPE_ENTRY` (compiled regexes)
  - `is_legal(frm: State, to: State) -> bool`
  - `display_for(state: State) -> str`, `bullet_matches(bullet: str, state: State) -> bool`, `icon_for(state: State) -> str` (one character: `" "`, `"~"`, `"?"`, `"✓"`, `"x"`)
  - `approval_status(events: list[dict], job_id: str) -> str | None` (`"approved"`, `"denied"` or `None`)
  - `recorded_state(events: list[dict], job_id: str) -> str | None`
  - `update_pipeline_text(text: str, url: str, icon: str) -> str`
  - `class TransitionError(WorkspaceError)` with `.code`; `class RecoveryError(WorkspaceError)`
  - `@dataclass(frozen=True) TransitionResult(outcome: str, job_id: str, from_state: State, to_state: State, event: dict | None = None)` where `outcome` is one of `"changed"`, `"corrected"`, `"unchanged"`, `"repaired"`
  - `apply_transition(root, ref, to_state, *, actor, reason=None, force=False) -> TransitionResult`
  - `approve_job(root, ref, *, confirm: Callable[[str], bool]) -> dict` (keys `outcome` in `"approved"|"denied"|"unchanged"`, `event`, optional `decision`)
  - `archive_job(root, ref, *, actor, undo=False, reason=None) -> dict` (keys `outcome` in `"archived"|"unarchived"|"unchanged"`, `event`)
- Test helpers in `tests/helpers.py`: `make_workspace(root, framework_version=None) -> Path`, `add_job(root, slug, *, status=State.DISCOVERED, company="Acme", title="Backend Engineer", url=None, bullet="discovered", baseline=True, pipeline=True) -> dict` with keys `id`, `path`, `url`, `slug`.

- [ ] **Step 1: Create the test helpers**

Create `tests/helpers.py`:

```python
"""Builders for test workspaces."""

from __future__ import annotations

from pathlib import Path

from careeros.core import ids, ledger, versions
from careeros.core.models import State, WorkspaceMeta
from careeros.core.workspace import join_frontmatter, save_meta

TS = "2026-10-01T00:00:00Z"


def make_workspace(root: Path, framework_version: str | None = None) -> Path:
    """A schema-1 workspace with a profile, an activity log, an empty pipeline and no jobs."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.md").write_text("# Profile\n", encoding="utf-8")
    (root / "jobs" / "discovered").mkdir(parents=True, exist_ok=True)
    (root / "activity.md").write_text("# Activity Log\n", encoding="utf-8")
    (root / "jobs" / "pipeline.md").write_text("# Job Pipeline\n\n## Active\n\n", encoding="utf-8")
    save_meta(
        root,
        WorkspaceMeta(
            versions.SCHEMA_VERSION, framework_version or versions.installed_version(), TS, TS, ("claude",)
        ),
    )
    return root


def add_job(
    root: Path,
    slug: str,
    *,
    status: State = State.DISCOVERED,
    company: str = "Acme",
    title: str = "Backend Engineer",
    url: str | None = None,
    bullet: str = "discovered",
    baseline: bool = True,
    pipeline: bool = True,
) -> dict:
    url = url or f"https://example.com/jobs/{slug}"
    job_id = ids.new_id("job")
    frontmatter = {
        "id": job_id, "type": "job", "schema": 1, "status": status.value,
        "company": company, "title": title, "url": url, "created_at": TS, "updated_at": TS,
    }
    body = f"# {title} at {company}\n\n- **URL:** {url}\n- **Status:** {bullet}\n\n## Notes\nhello\n"
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(join_frontmatter(frontmatter, body), encoding="utf-8")
    if pipeline:
        with (root / "jobs" / "pipeline.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [ ] **{company}** — {title} · Remote · Score 8 · 2026-10-01 · {url}\n")
    if baseline:
        ledger.append_event(
            root, type="job.imported", actor="system", entity=job_id, new_state=status.value,
            action=f"imported {company}", source="migration",
        )
    return {"id": job_id, "path": path, "url": url, "slug": slug}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_core_state_machine.py`:

```python
import itertools
from pathlib import Path

import pytest
from helpers import add_job, make_workspace

from careeros.core import ledger, versions
from careeros.core import state_machine as sm
from careeros.core import workspace as ws
from careeros.core.models import State

PRE = [State.DISCOVERED, State.EVALUATED, State.SHORTLISTED, State.RESEARCHED, State.PREPARING, State.READY_TO_APPLY]
POST = [State.APPLIED, State.RECRUITER_REPLIED, State.SCREEN, State.TECHNICAL, State.HM, State.FINAL, State.OFFER]


def _legal_pairs() -> set[tuple[State, State]]:
    """The spec's transition table, written out independently of the implementation."""
    legal: set[tuple[State, State]] = set()
    for i, a in enumerate(PRE):
        legal.update((a, b) for b in PRE[i + 1:])
    legal.add((State.READY_TO_APPLY, State.APPROVAL_REQUIRED))
    legal.add((State.APPROVAL_REQUIRED, State.APPLIED))
    for i, a in enumerate(POST):
        legal.update((a, b) for b in POST[i + 1:])
        legal.add((a, State.REJECTED))
    legal.add((State.OFFER, State.ACCEPTED))
    for a in PRE + [State.APPROVAL_REQUIRED] + POST:
        legal.add((a, State.WITHDRAWN))
    return legal


def test_is_legal_matches_the_spec_table_for_every_pair() -> None:
    legal = _legal_pairs()
    wrong = [(f.name, t.name) for f, t in itertools.product(State, State) if sm.is_legal(f, t) != ((f, t) in legal)]
    assert wrong == []


def test_display_icon_and_bullet_helpers() -> None:
    assert sm.display_for(State.DISCOVERED) == "discovered"
    assert sm.display_for(State.SCREEN) == "interview"
    assert sm.display_for(State.APPROVAL_REQUIRED) == "approval-required"
    assert sm.display_for(State.WITHDRAWN) == "closed"
    assert sm.bullet_matches("declined", State.WITHDRAWN)
    assert sm.bullet_matches("Interview", State.TECHNICAL)
    assert not sm.bullet_matches("applied", State.DISCOVERED)
    assert [sm.icon_for(s) for s in (State.DISCOVERED, State.APPROVAL_REQUIRED, State.APPLIED, State.HM, State.OFFER, State.ACCEPTED, State.REJECTED)] == [
        " ", " ", "~", "?", "✓", "✓", "x",
    ]


@pytest.fixture
def workspace(tmp_path: Path, clock) -> Path:
    return make_workspace(tmp_path / "ws")


def _events(root: Path) -> list[dict]:
    return ledger.read_events(root)


def test_legal_transition_updates_job_bullet_pipeline_and_ledger(workspace: Path) -> None:
    job = add_job(workspace, "acme-backend", status=State.APPLIED, bullet="applied")
    before = job["path"].read_text(encoding="utf-8")
    result = sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user")
    assert result.outcome == "changed"
    reread = ws.read_job(job["path"])
    assert reread.status is State.SCREEN
    assert reread.updated_at != "2026-10-01T00:00:00Z"
    assert "- **Status:** interview" in job["path"].read_text(encoding="utf-8")
    assert reread.body == before.split("---\n", 2)[2].replace("- **Status:** applied", "- **Status:** interview")
    assert (workspace / "jobs" / "pipeline.md").read_text(encoding="utf-8").count("- [?] **Acme**") == 1
    event = _events(workspace)[-1]
    assert (event["type"], event["prev_state"], event["new_state"], event["entity"], event["actor"]) == (
        "job.status_changed", "APPLIED", "SCREEN", job["id"], "user",
    )


def test_transition_accepts_slug_and_leaves_other_jobs_alone(workspace: Path) -> None:
    one = add_job(workspace, "one")
    two = add_job(workspace, "two")
    sm.apply_transition(workspace, "one", State.EVALUATED, actor="user")
    assert ws.read_job(one["path"]).status is State.EVALUATED
    assert ws.read_job(two["path"]).status is State.DISCOVERED


def test_repeating_a_transition_is_a_no_op(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    snapshot = (job["path"].read_bytes(), len(_events(workspace)))
    again = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert again.outcome == "unchanged"
    assert (job["path"].read_bytes(), len(_events(workspace))) == snapshot


def test_retry_after_a_crash_between_job_write_and_ledger_append_repairs_the_ledger_once(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    frontmatter, body = ws.split_frontmatter(job["path"].read_text(encoding="utf-8"))
    frontmatter["status"] = "EVALUATED"  # the process died after this write and before the ledger append
    job["path"].write_text(ws.join_frontmatter(frontmatter, body), encoding="utf-8")
    result = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert result.outcome == "repaired"
    event = _events(workspace)[-1]
    assert (event["source"], event["prev_state"], event["new_state"]) == ("recovery", "DISCOVERED", "EVALUATED")
    count = len(_events(workspace))
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "unchanged"
    assert len(_events(workspace)) == count


def test_illegal_transition_is_rejected_unchanged_and_logged_once(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    before = job["path"].read_bytes()
    for _ in range(2):
        with pytest.raises(sm.TransitionError) as exc:
            sm.apply_transition(workspace, job["id"], State.OFFER, actor="agent:claude")
        assert exc.value.code == "illegal_transition"
    assert job["path"].read_bytes() == before
    rejected = [e for e in _events(workspace) if e["type"] == "job.transition_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"].startswith("illegal_transition")
    assert (rejected[0]["prev_state"], rejected[0]["new_state"]) == ("DISCOVERED", "OFFER")


def test_applied_needs_a_recorded_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    with pytest.raises(sm.TransitionError, match="careeros approve") as exc:
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="agent:claude")
    assert exc.value.code == "approval_required"
    sm.approve_job(workspace, job["id"], confirm=lambda prompt: True)
    assert sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user").outcome == "changed"


def test_latest_approval_decision_wins_and_resets_on_reentry(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: False)["outcome"] == "denied"
    with pytest.raises(sm.TransitionError):
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user")
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: True)["outcome"] == "approved"
    sm.apply_transition(workspace, job["id"], State.READY_TO_APPLY, actor="user", force=True, reason="needs another look")
    sm.apply_transition(workspace, job["id"], State.APPROVAL_REQUIRED, actor="user")
    assert sm.approval_status(_events(workspace), job["id"]) is None
    with pytest.raises(sm.TransitionError):
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user")


def test_approve_is_idempotent_and_records_user_actor(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    first = sm.approve_job(workspace, job["id"], confirm=lambda p: True)
    event = first["event"]
    assert (event["type"], event["actor"], event["approval"], event["source"]) == (
        "application.approved", "user", "approved", "tty",
    )
    count = len(_events(workspace))

    def must_not_ask(prompt: str) -> bool:
        raise AssertionError("already approved; the user must not be asked again")

    again = sm.approve_job(workspace, job["id"], confirm=must_not_ask)
    assert again["outcome"] == "unchanged"
    assert len(_events(workspace)) == count


def test_repeated_denial_logs_once(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: False)["outcome"] == "denied"
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: False)["outcome"] == "unchanged"
    assert [e["type"] for e in _events(workspace)].count("application.approval_denied") == 1


def test_approve_requires_the_job_to_be_awaiting_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    with pytest.raises(sm.TransitionError) as exc:
        sm.approve_job(workspace, job["id"], confirm=lambda p: True)
    assert exc.value.code == "not_awaiting_approval"


def test_approve_does_not_hold_the_lock_while_the_user_decides_and_detects_changes(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")

    def withdraw_meanwhile(prompt: str) -> bool:
        # would deadlock or time out if approve held the workspace lock during the prompt
        sm.apply_transition(workspace, job["id"], State.WITHDRAWN, actor="user")
        return True

    with pytest.raises(sm.TransitionError) as exc:
        sm.approve_job(workspace, job["id"], confirm=withdraw_meanwhile)
    assert exc.value.code == "not_awaiting_approval"


def test_force_requires_a_reason(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.SCREEN, bullet="interview")
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user", force=True)
    assert exc.value.code == "reason_required"


def test_force_records_a_correction_not_a_normal_change(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.SCREEN, bullet="interview")
    result = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user", force=True, reason="entered by mistake")
    assert result.outcome == "corrected"
    event = _events(workspace)[-1]
    assert (event["type"], event["prev_state"], event["new_state"], event["actor"], event["reason"]) == (
        "job.status_corrected", "SCREEN", "EVALUATED", "user", "entered by mistake",
    )
    assert ws.read_job(job["path"]).status is State.EVALUATED


def test_force_on_an_already_legal_move_is_a_normal_transition(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    result = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user", force=True, reason="n/a")
    assert result.outcome == "changed"
    assert _events(workspace)[-1]["type"] == "job.status_changed"


def test_force_cannot_leave_a_terminal_state(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.WITHDRAWN, bullet="closed")
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.PREPARING, actor="user", force=True, reason="changed my mind")
    assert exc.value.code == "terminal"


def test_force_into_applied_still_needs_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.PREPARING, bullet="preparing")
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user", force=True, reason="applied by email")
    assert exc.value.code == "approval_required"


def test_failed_ledger_append_leaves_job_and_pipeline_byte_identical(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = add_job(workspace, "acme", status=State.APPLIED, bullet="applied")  # pipeline line shows "[ ]", so SCREEN rewrites it
    pipeline = workspace / "jobs" / "pipeline.md"
    before = (job["path"].read_bytes(), pipeline.read_bytes())

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ledger, "append_event", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user")
    assert (job["path"].read_bytes(), pipeline.read_bytes()) == before
    assert not [p for p in workspace.rglob("*") if p.name.endswith(".tmp")]


def test_failed_restore_saves_the_original_under_recovery(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = add_job(workspace, "acme")  # DISCOVERED -> EVALUATED leaves the pipeline icon alone: one file written
    original = job["path"].read_bytes()
    calls = {"n": 0}
    real = sm.atomic_write_bytes

    def flaky(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("restore failed")
        real(path, data)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ledger, "append_event", boom)
    monkeypatch.setattr(sm, "atomic_write_bytes", flaky)
    with pytest.raises(sm.RecoveryError, match="saved at"):
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    saved = list((workspace / ".careeros" / "recovery").iterdir())
    assert len(saved) == 1 and saved[0].read_bytes() == original


def test_workspace_newer_than_installed_is_refused_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock
) -> None:
    root = make_workspace(tmp_path / "ws", framework_version="9.0.0")
    job = add_job(root, "acme")
    before = job["path"].read_bytes()
    with pytest.raises(ws.WorkspaceError, match="newer than the installed"):
        sm.apply_transition(root, job["id"], State.EVALUATED, actor="user")
    assert job["path"].read_bytes() == before


def test_archive_flags_the_job_blocks_transitions_and_is_idempotent(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    first = sm.archive_job(workspace, job["id"], actor="user", reason="not interested")
    assert first["outcome"] == "archived"
    reread = ws.read_job(job["path"])
    assert reread.archived and reread.archived_at
    assert _events(workspace)[-1]["type"] == "job.archived"
    assert _events(workspace)[-1]["reason"] == "not interested"
    count = len(_events(workspace))
    assert sm.archive_job(workspace, job["id"], actor="user")["outcome"] == "unchanged"
    assert len(_events(workspace)) == count
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert exc.value.code == "archived"
    undone = sm.archive_job(workspace, job["id"], actor="user", undo=True)
    assert undone["outcome"] == "unarchived"
    again = ws.read_job(job["path"])
    assert not again.archived and "archived_at" not in again.frontmatter
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "changed"


def test_archive_keeps_directory_and_body_in_place(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    body_before = ws.read_job(job["path"]).body
    sm.archive_job(workspace, job["id"], actor="user")
    assert job["path"].exists()
    assert ws.read_job(job["path"]).body == body_before
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_core_state_machine.py`
Expected: FAIL (`ImportError: cannot import name 'state_machine'`).

- [ ] **Step 4: Implement `careeros/core/state_machine.py`**

```python
"""Application state machine and the operations that change a job: transition, approve, archive."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from careeros.core import ledger, models
from careeros.core.models import Job, State
from careeros.core.workspace import (
    RECOVERY_REL,
    WorkspaceError,
    WorkspaceLock,
    atomic_write_bytes,
    ensure_writable,
    join_frontmatter,
    normalize_value,
    resolve_job,
    split_frontmatter,
)

PRE = (
    State.DISCOVERED, State.EVALUATED, State.SHORTLISTED,
    State.RESEARCHED, State.PREPARING, State.READY_TO_APPLY,
)
POST = (
    State.APPLIED, State.RECRUITER_REPLIED, State.SCREEN,
    State.TECHNICAL, State.HM, State.FINAL, State.OFFER,
)
TERMINAL = frozenset({State.ACCEPTED, State.REJECTED, State.WITHDRAWN})

STATUS_BULLET = re.compile(r"^(- \*\*Status:\*\* ?)(.*)$", re.M)
PIPE_ENTRY = re.compile(r"^(- \[)(.)(\] .*?)(https?://\S+)([ \t]*)$", re.M)

_DISPLAY = {
    State.DISCOVERED: "discovered",
    State.APPLIED: "applied",
    State.RECRUITER_REPLIED: "interview",
    State.SCREEN: "interview",
    State.TECHNICAL: "interview",
    State.HM: "interview",
    State.FINAL: "interview",
    State.OFFER: "offer",
    State.ACCEPTED: "accepted",
    State.REJECTED: "rejected",
    State.WITHDRAWN: "closed",
}
_ALIASES = {State.WITHDRAWN: {"closed", "declined", "withdrawn"}}
_STATUS_EVENTS = {"job.imported", "job.status_changed", "job.status_corrected"}


class TransitionError(WorkspaceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RecoveryError(WorkspaceError):
    """A failed write could not be undone; the original content was saved for recovery."""


@dataclass(frozen=True)
class TransitionResult:
    outcome: str  # "changed" | "corrected" | "unchanged" | "repaired"
    job_id: str
    from_state: State
    to_state: State
    event: dict | None = None


# --- rules ------------------------------------------------------------------------

def is_legal(frm: State, to: State) -> bool:
    if frm in TERMINAL or frm == to:
        return False
    if to == State.WITHDRAWN:
        return True
    if to == State.REJECTED:
        return frm in POST
    if to == State.ACCEPTED:
        return frm == State.OFFER
    if to == State.APPROVAL_REQUIRED:
        return frm == State.READY_TO_APPLY
    if to == State.APPLIED:
        return frm == State.APPROVAL_REQUIRED
    if frm in PRE and to in PRE:
        return PRE.index(to) > PRE.index(frm)
    if frm in POST and to in POST:
        return POST.index(to) > POST.index(frm)
    return False


def display_for(state: State) -> str:
    return _DISPLAY.get(state, state.value.lower().replace("_", "-"))


def bullet_matches(bullet: str, state: State) -> bool:
    value = bullet.strip().lower()
    return value in {display_for(state), state.value.lower(), *_ALIASES.get(state, set())}


def icon_for(state: State) -> str:
    if state in PRE or state == State.APPROVAL_REQUIRED:
        return " "
    if state == State.APPLIED:
        return "~"
    if state in (State.RECRUITER_REPLIED, State.SCREEN, State.TECHNICAL, State.HM, State.FINAL):
        return "?"
    if state in (State.OFFER, State.ACCEPTED):
        return "✓"
    return "x"


def approval_status(events: list[dict], job_id: str) -> str | None:
    """Latest approval decision since the job last entered APPROVAL_REQUIRED."""
    status: str | None = None
    for event in events:
        if event.get("entity") != job_id:
            continue
        kind = event.get("type")
        if kind in ("job.status_changed", "job.status_corrected") and event.get("new_state") == State.APPROVAL_REQUIRED.value:
            status = None
        elif kind == "application.approved":
            status = "approved"
        elif kind == "application.approval_denied":
            status = "denied"
    return status


def recorded_state(events: list[dict], job_id: str) -> str | None:
    state: str | None = None
    for event in events:
        if event.get("entity") == job_id and event.get("type") in _STATUS_EVENTS and event.get("new_state"):
            state = event["new_state"]
    return state


def update_pipeline_text(text: str, url: str, icon: str) -> str:
    target = url.rstrip("/")

    def replace(match: re.Match[str]) -> str:
        if match.group(4).rstrip("/") == target:
            return f"{match.group(1)}{icon}{match.group(3)}{match.group(4)}{match.group(5)}"
        return match.group(0)

    return PIPE_ENTRY.sub(replace, text)


# --- writing helpers --------------------------------------------------------------

def _set_status_bullet(body: str, display: str) -> str:
    return STATUS_BULLET.sub(lambda m: f"{m.group(1)}{display}", body, count=1)


def _restore(root: Path, originals: dict[Path, bytes], written: list[Path], job_id: str) -> None:
    first_failure: RecoveryError | None = None
    for path in written:
        try:
            atomic_write_bytes(path, originals[path])
        except OSError as exc:
            stamp = models.utc_now().replace(":", "")
            recovery = Path(root) / RECOVERY_REL / f"{stamp}-{job_id}-{path.name}"
            recovery.parent.mkdir(parents=True, exist_ok=True)
            recovery.write_bytes(originals[path])
            if first_failure is None:
                first_failure = RecoveryError(
                    f"could not restore {path}; its original content is saved at {recovery}"
                )
                first_failure.__cause__ = exc
    if first_failure is not None:
        raise first_failure


def _reject(root: Path, job: Job, to_state: State, actor: str, code: str, message: str, events: list[dict]) -> None:
    reason = f"{code}: {message}"
    last = next((e for e in reversed(events) if e.get("entity") == job.id), None)
    duplicate = (
        last is not None
        and last.get("type") == "job.transition_rejected"
        and last.get("new_state") == to_state.value
        and last.get("reason") == reason
    )
    if not duplicate:
        ledger.append_event(
            root, type="job.transition_rejected", actor=actor, entity=job.id,
            prev_state=job.status.value, new_state=to_state.value,
            action=f"rejected {job.status.value} → {to_state.value}", reason=reason, source="cli",
        )
    raise TransitionError(code, message)


# --- transition -------------------------------------------------------------------

def apply_transition(
    root: Path,
    ref: str,
    to_state: State | str,
    *,
    actor: str,
    reason: str | None = None,
    force: bool = False,
) -> TransitionResult:
    root = Path(root)
    to_state = State(to_state)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        events = ledger.read_events(root)
        current = job.status
        recorded = recorded_state(events, job.id)
        if job.archived:
            _reject(root, job, to_state, actor, "archived",
                    "the job is archived; run `careeros archive <job> --undo` first", events)
        if current == to_state:
            if recorded is not None and recorded != current.value:
                event = ledger.append_event(
                    root, type="job.status_changed", actor=actor, entity=job.id,
                    prev_state=recorded, new_state=current.value,
                    action=f"ledger repaired: the job file already shows {current.value}", source="recovery",
                )
                return TransitionResult("repaired", job.id, current, to_state, event)
            return TransitionResult("unchanged", job.id, current, to_state)

        corrected = False
        if not is_legal(current, to_state):
            if not force:
                _reject(root, job, to_state, actor, "illegal_transition",
                        f"{current.value} → {to_state.value} is not allowed", events)
            if not (reason or "").strip():
                raise TransitionError("reason_required", "--force needs --reason explaining the correction")
            if current in TERMINAL:
                _reject(root, job, to_state, actor, "terminal",
                        f"{current.value} is terminal and cannot be changed", events)
            corrected = True
        if to_state == State.APPLIED and approval_status(events, job.id) != "approved":
            _reject(root, job, to_state, actor, "approval_required",
                    "approval required — ask the user to run `careeros approve <job>` themselves", events)
        return _write_transition(root, job, to_state, actor=actor, reason=reason, corrected=corrected)


def _write_transition(root: Path, job: Job, to_state: State, *, actor: str, reason: str | None, corrected: bool) -> TransitionResult:
    originals: dict[Path, bytes] = {job.path: job.path.read_bytes()}
    frontmatter, body = split_frontmatter(originals[job.path].decode("utf-8"))
    frontmatter = {k: normalize_value(v) for k, v in (frontmatter or {}).items()}
    frontmatter["status"] = to_state.value
    frontmatter["updated_at"] = models.utc_now()
    new_texts: dict[Path, str] = {job.path: join_frontmatter(frontmatter, _set_status_bullet(body, display_for(to_state)))}
    pipeline = root / "jobs" / "pipeline.md"
    if pipeline.is_file() and job.url:
        pipeline_text = pipeline.read_bytes().decode("utf-8")
        updated = update_pipeline_text(pipeline_text, job.url, icon_for(to_state))
        if updated != pipeline_text:
            originals[pipeline] = pipeline_text.encode("utf-8")
            new_texts[pipeline] = updated
    written: list[Path] = []
    try:
        for path, text in new_texts.items():
            atomic_write_bytes(path, text.encode("utf-8"))
            written.append(path)
        event = ledger.append_event(
            root,
            type="job.status_corrected" if corrected else "job.status_changed",
            actor=actor, entity=job.id, prev_state=job.status.value, new_state=to_state.value,
            action=f"{job.company} — {job.title}: {job.status.value} → {to_state.value}",
            reason=reason if corrected else None, source="cli",
        )
    except BaseException:
        _restore(root, originals, written, job.id)
        raise
    return TransitionResult("corrected" if corrected else "changed", job.id, job.status, to_state, event)


# --- approvals --------------------------------------------------------------------

def _require_awaiting_approval(job: Job) -> None:
    if job.archived:
        raise TransitionError("archived", "the job is archived")
    if job.status != State.APPROVAL_REQUIRED:
        raise TransitionError("not_awaiting_approval", f"the job is {job.status.value}, not APPROVAL_REQUIRED")


def approve_job(root: Path, ref: str, *, confirm: Callable[[str], bool]) -> dict:
    """Record the user's approval or denial. The lock is not held while the user decides."""
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        _require_awaiting_approval(job)
        events = ledger.read_events(root)
        current = approval_status(events, job.id)
        if current == "approved":
            latest = [e for e in events if e.get("entity") == job.id and e.get("type") == "application.approved"][-1]
            return {"outcome": "unchanged", "decision": "approved", "event": latest}
        company, title = job.company, job.title
    decision = bool(confirm(f"Approve submitting the application for {company} — {title}?"))
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        _require_awaiting_approval(job)
        if approval_status(ledger.read_events(root), job.id) != current:
            raise TransitionError("state_changed", "the approval state changed while you were deciding; run the command again")
        if decision:
            event = ledger.append_event(
                root, type="application.approved", actor="user", entity=job.id, approval="approved",
                action=f"user approved the application for {company} — {title}", source="tty",
            )
            return {"outcome": "approved", "event": event}
        if current == "denied":
            return {"outcome": "unchanged", "decision": "denied"}
        event = ledger.append_event(
            root, type="application.approval_denied", actor="user", entity=job.id, approval="denied",
            action=f"user declined to approve the application for {company} — {title}", source="tty",
        )
        return {"outcome": "denied", "event": event}


# --- archive ----------------------------------------------------------------------

def archive_job(root: Path, ref: str, *, actor: str, undo: bool = False, reason: str | None = None) -> dict:
    root = Path(root)
    with WorkspaceLock(root):
        ensure_writable(root)
        job = resolve_job(root, ref)
        target = not undo
        if job.archived == target:
            return {"outcome": "unchanged"}
        original = job.path.read_bytes()
        frontmatter, body = split_frontmatter(original.decode("utf-8"))
        frontmatter = {k: normalize_value(v) for k, v in (frontmatter or {}).items()}
        now = models.utc_now()
        if target:
            frontmatter["archived"] = True
            frontmatter["archived_at"] = now
        else:
            frontmatter.pop("archived", None)
            frontmatter.pop("archived_at", None)
        frontmatter["updated_at"] = now
        atomic_write_bytes(job.path, join_frontmatter(frontmatter, body).encode("utf-8"))
        try:
            event = ledger.append_event(
                root, type="job.archived" if target else "job.unarchived", actor=actor, entity=job.id,
                action=f"{'archived' if target else 'unarchived'} {job.company} — {job.title}",
                reason=reason, source="cli",
            )
        except BaseException:
            _restore(root, {job.path: original}, [job.path], job.id)
            raise
        return {"outcome": "archived" if target else "unarchived", "event": event}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass. If `test_legal_transition_updates_job_bullet_pipeline_and_ledger` fails on the body comparison, print `before.split("---\n", 2)[2]` and `reread.body`: only the status bullet may differ.

- [ ] **Step 6: Commit**

```bash
git add careeros/core/state_machine.py tests/helpers.py tests/test_core_state_machine.py
git commit -m "core: state machine with atomic transitions, approvals, corrections and archive" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 5: Validation

**Files:**
- Create: `careeros/core/validation.py`
- Test: `tests/test_core_validation.py`

**Interfaces:**
- Consumes: `models.Issue/State`, `versions`, `ids.is_valid_id`, `ledger.verify_chain`, `state_machine.PIPE_ENTRY/STATUS_BULLET/bullet_matches/icon_for/is_legal/recorded_state`, `workspace.load_meta/job_files/split_frontmatter/normalize_value/JOB_KEYS`.
- Produces: `validation.validate_workspace(root: Path) -> list[Issue]` (rule codes exactly as in spec section 9.2).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_validation.py`:

```python
import json
import shutil
from pathlib import Path

import pytest
from helpers import add_job, make_workspace

from careeros.core import ledger, versions
from careeros.core import workspace as ws
from careeros.core.models import Issue, State, WorkspaceMeta
from careeros.core.validation import validate_workspace

TS = "2026-10-01T00:00:00Z"


@pytest.fixture
def root(tmp_path: Path, clock) -> Path:
    return make_workspace(tmp_path / "ws")


def _find(issues: list[Issue], code: str) -> Issue:
    matches = [i for i in issues if i.code == code]
    assert matches, f"expected {code}, got {[i.code for i in issues]}"
    assert matches[0].fix.strip(), f"{code} has no fix text"
    return matches[0]


def test_a_healthy_workspace_has_no_issues(root: Path) -> None:
    add_job(root, "one")
    add_job(root, "two", status=State.APPLIED, bullet="applied")
    pipeline = root / "jobs" / "pipeline.md"
    pipeline.write_text(pipeline.read_text().replace("- [ ] **Acme** — Backend Engineer · Remote · Score 8 · 2026-10-01 · https://example.com/jobs/two",
                                                     "- [~] **Acme** — Backend Engineer · Remote · Score 8 · 2026-10-01 · https://example.com/jobs/two"))
    assert validate_workspace(root) == []


def test_str001_missing_profile_and_jobs(root: Path) -> None:
    (root / "profile.md").unlink()
    shutil.rmtree(root / "jobs")
    issues = validate_workspace(root)
    assert [i.severity for i in issues if i.code == "STR001"] == ["error", "error"]
    _find(issues, "STR001")


def test_str002_missing_activity_and_pipeline_are_warnings(root: Path) -> None:
    (root / "activity.md").unlink()
    (root / "jobs" / "pipeline.md").unlink()
    issues = validate_workspace(root)
    assert [i.severity for i in issues if i.code == "STR002"] == ["warning", "warning"]


def test_ws001_legacy_workspace_without_metadata(root: Path) -> None:
    (root / ".careeros" / "workspace.yaml").unlink()
    issue = _find(validate_workspace(root), "WS001")
    assert issue.severity == "error" and "careeros migrate" in issue.fix


def test_ws002_schema_newer_than_supported(root: Path) -> None:
    ws.save_meta(root, WorkspaceMeta(2, versions.installed_version(), TS, TS, ()))
    assert _find(validate_workspace(root), "WS002").severity == "error"


def test_ws003_unreadable_metadata(root: Path) -> None:
    (root / ".careeros" / "workspace.yaml").write_text("schema_version: [")
    issues = validate_workspace(root)
    assert _find(issues, "WS003").severity == "error"
    assert "WS001" not in [i.code for i in issues]


def test_ws004_and_ws005_framework_version_relations(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "installed_version", lambda: "0.4.0")
    ws.save_meta(root, WorkspaceMeta(1, "0.3.0", TS, TS, ()))
    older = _find(validate_workspace(root), "WS004")
    assert older.severity == "warning" and "careeros upgrade" in older.fix
    ws.save_meta(root, WorkspaceMeta(1, "9.0.0", TS, TS, ()))
    newer = _find(validate_workspace(root), "WS005")
    assert newer.severity == "error"


def test_job001_legacy_job_file_without_frontmatter(root: Path) -> None:
    path = root / "jobs" / "discovered" / "legacy" / "job.md"
    path.parent.mkdir(parents=True)
    path.write_text("# T at C\n- **URL:** https://x.test\n")
    issue = _find(validate_workspace(root), "JOB001")
    assert issue.severity == "warning" and "careeros migrate" in issue.fix


def test_job002_missing_key_and_unreadable_frontmatter(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    del fm["company"]
    job["path"].write_text(ws.join_frontmatter(fm, body))
    assert "company" in _find(validate_workspace(root), "JOB002").message
    job["path"].write_text("---\nkey: [oops\n---\nbody\n")
    assert _find(validate_workspace(root), "JOB002").severity == "error"


def test_job003_invalid_and_duplicate_ids(root: Path) -> None:
    one = add_job(root, "one")
    two = add_job(root, "two")
    fm, body = ws.split_frontmatter(two["path"].read_text())
    fm["id"] = one["id"]
    two["path"].write_text(ws.join_frontmatter(fm, body))
    assert "also used" in _find(validate_workspace(root), "JOB003").message
    fm["id"] = "job_BAD"
    two["path"].write_text(ws.join_frontmatter(fm, body))
    assert "not a valid job id" in _find(validate_workspace(root), "JOB003").message


def test_job004_invalid_status(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["status"] = "NONSENSE"
    job["path"].write_text(ws.join_frontmatter(fm, body))
    assert _find(validate_workspace(root), "JOB004").severity == "error"


def test_job005_status_line_disagrees_with_frontmatter(root: Path) -> None:
    add_job(root, "one", status=State.DISCOVERED, bullet="applied")
    issue = _find(validate_workspace(root), "JOB005")
    assert issue.severity == "warning" and "careeros transition" in issue.fix


def test_job006_timestamp_must_be_full_utc(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["created_at"] = "2026-10-01"
    job["path"].write_text(ws.join_frontmatter(fm, body))
    assert _find(validate_workspace(root), "JOB006").severity == "error"


def test_pipeline_rules(root: Path) -> None:
    add_job(root, "one", status=State.APPLIED, bullet="applied")  # entry still shows "[ ]"
    add_job(root, "two", pipeline=False)
    archived = add_job(root, "three", pipeline=False)
    from careeros.core import state_machine as sm
    sm.archive_job(root, archived["id"], actor="user")
    with (root / "jobs" / "pipeline.md").open("a") as handle:
        handle.write("- [ ] **Ghost** — Nobody · Remote · Score 1 · 2026-10-01 · https://example.com/ghost\n")
    issues = validate_workspace(root)
    assert _find(issues, "PIPE001").severity == "warning"
    assert [i.path for i in issues if i.code == "PIPE002"] == ["jobs/discovered/two/job.md"]
    assert "[~]" in _find(issues, "PIPE003").message


def test_pipeline_history_below_the_fold_is_ignored(root: Path) -> None:
    add_job(root, "one")
    with (root / "jobs" / "pipeline.md").open("a") as handle:
        handle.write("\n## -- HISTORY (on demand; do not read past this line) --\n"
                     "- [x] **Old** — Thing · Remote · Score 1 · 2026-01-01 · https://example.com/old\n")
    assert "PIPE001" not in [i.code for i in validate_workspace(root)]


def test_led001_and_led002_come_from_the_chain_check(root: Path) -> None:
    add_job(root, "one")
    path = root / "ledger.jsonl"
    path.write_text(path.read_text() + "garbage\n")
    assert _find(validate_workspace(root), "LED001").severity == "error"
    path.write_text(path.read_text().replace('"seq":1', '"seq":7', 1))
    assert _find(validate_workspace(root), "LED002").severity == "error"


def test_led003_deleted_job_is_reported_with_archive_advice(root: Path) -> None:
    job = add_job(root, "one")
    shutil.rmtree(job["path"].parent)
    issue = _find(validate_workspace(root), "LED003")
    assert issue.severity == "error" and "archive" in issue.fix and job["id"] in issue.message
    assert len([i for i in validate_workspace(root) if i.code == "LED003"]) == 1


def test_archived_job_keeps_ledger_references_valid(root: Path) -> None:
    from careeros.core import state_machine as sm
    job = add_job(root, "one")
    sm.archive_job(root, job["id"], actor="user")
    assert "LED003" not in [i.code for i in validate_workspace(root)]


def test_led004_illegal_logged_change_but_corrections_are_allowed(root: Path) -> None:
    job = add_job(root, "one")
    ledger.append_event(root, type="job.status_changed", actor="agent:claude", entity=job["id"],
                        prev_state="DISCOVERED", new_state="OFFER", action="bogus")
    assert _find(validate_workspace(root), "LED004").severity == "error"


def test_led004_not_raised_for_forced_corrections(root: Path) -> None:
    job = add_job(root, "one", status=State.SCREEN, bullet="interview")
    ledger.append_event(root, type="job.status_corrected", actor="user", entity=job["id"],
                        prev_state="SCREEN", new_state="DISCOVERED", action="fix", reason="entered by mistake")
    codes = [i.code for i in validate_workspace(root)]
    assert "LED004" not in codes


def test_led005_job_status_not_reflected_in_ledger(root: Path) -> None:
    job = add_job(root, "one")
    fm, body = ws.split_frontmatter(job["path"].read_text())
    fm["status"] = "EVALUATED"  # changed by hand, or a crash before the ledger append
    job["path"].write_text(ws.join_frontmatter(fm, body))
    issue = _find(validate_workspace(root), "LED005")
    assert issue.severity == "warning" and "careeros transition" in issue.fix


def test_every_issue_serialises(root: Path) -> None:
    (root / "profile.md").unlink()
    issue = validate_workspace(root)[0]
    assert set(issue.to_dict()) == {"severity", "code", "path", "message", "fix"}
    json.dumps(issue.to_dict())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_core_validation.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'careeros.core.validation'`).

- [ ] **Step 3: Implement `careeros/core/validation.py`**

```python
"""Deterministic workspace validation. Read-only: it never changes a file."""

from __future__ import annotations

import json
from pathlib import Path

from careeros.core import ids, ledger, models, versions
from careeros.core.models import Issue, State
from careeros.core.state_machine import (
    PIPE_ENTRY,
    STATUS_BULLET,
    bullet_matches,
    icon_for,
    is_legal,
    recorded_state,
)
from careeros.core.workspace import (
    JOB_KEYS,
    WorkspaceError,
    job_files,
    load_meta,
    normalize_value,
    split_frontmatter,
)

_FOLD_MARKER = "do not read past this line"
_RESTORE_LEDGER = "restore ledger.jsonl from .careeros/backups or version control"


def _above_fold(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if _FOLD_MARKER in line:
            break
        kept.append(line)
    return "".join(kept)


def _lenient_events(root: Path) -> list[dict]:
    path = root / "ledger.jsonl"
    events: list[dict] = []
    if not path.exists():
        return events
    for raw in path.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def validate_workspace(root: Path) -> list[Issue]:
    root = Path(root)
    issues: list[Issue] = []

    def add(severity: str, code: str, path: str, message: str, fix: str) -> None:
        issues.append(Issue(severity, code, path, message, fix))

    # --- structure ---
    if not (root / "profile.md").is_file():
        add("error", "STR001", "profile.md", "profile.md is missing", "run the onboarding skill, or create profile.md")
    if not (root / "jobs").is_dir():
        add("error", "STR001", "jobs/", "the jobs/ directory is missing", "create jobs/discovered/, or restore jobs/ from a backup")
    for rel in ("activity.md", "jobs/pipeline.md"):
        if not (root / rel).is_file():
            add("warning", "STR002", rel, f"{rel} is missing", f"create {rel}, or run the skill that writes it")

    # --- workspace metadata and versions ---
    meta_path = ".careeros/workspace.yaml"
    installed = versions.installed_version()
    meta = None
    meta_broken = False
    try:
        meta = load_meta(root)
    except WorkspaceError as exc:
        meta_broken = True
        add("error", "WS003", meta_path, str(exc), "fix the file, or restore it from .careeros/backups")
    if meta is None and not meta_broken:
        add("error", "WS001", meta_path, "no workspace metadata: this is a legacy (schema 0) workspace", "run `careeros migrate`")
    if meta is not None:
        if meta.schema_version > versions.SCHEMA_VERSION:
            add("error", "WS002", meta_path,
                f"workspace schema {meta.schema_version} is newer than this CareerOS supports ({versions.SCHEMA_VERSION})",
                "upgrade CareerOS before changing this workspace")
        relation = versions.compare_versions(installed, meta.framework_version)
        if relation > 0:
            add("warning", "WS004", meta_path,
                f"CareerOS {installed} is installed; the workspace was last updated by {meta.framework_version}",
                "run `careeros upgrade`")
        elif relation < 0:
            add("error", "WS005", meta_path,
                f"the workspace was last updated by CareerOS {meta.framework_version}, newer than the installed {installed}",
                "upgrade CareerOS before changing this workspace")

    # --- jobs ---
    jobs: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}
    all_ids: set[str] = set()
    for path in job_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except WorkspaceError as exc:
            add("error", "JOB002", rel, f"the frontmatter could not be read: {exc}", "fix the YAML between the --- lines")
            continue
        if frontmatter is None:
            add("warning", "JOB001", rel, "no frontmatter (legacy job file)", "run `careeros migrate`")
            continue
        fm = {k: normalize_value(v) for k, v in frontmatter.items()}
        missing = [k for k in JOB_KEYS if k not in fm]
        if missing:
            add("error", "JOB002", rel, f"frontmatter is missing: {', '.join(missing)}", "add the missing key(s) between the --- lines")
        job_id = fm.get("id")
        if job_id is not None:
            all_ids.add(str(job_id))
        valid_id = False
        if "id" in fm:
            if not ids.is_valid_id("job", job_id):
                add("error", "JOB003", rel, f"id {job_id!r} is not a valid job id", "use the form job_ followed by 10 lowercase base32 characters")
            elif job_id in seen_ids:
                add("error", "JOB003", rel, f"id {job_id} is also used by {seen_ids[job_id]}", "give one of the two jobs a new id")
            else:
                seen_ids[job_id] = rel
                valid_id = True
        state: State | None = None
        if "status" in fm:
            try:
                state = State(fm["status"])
            except ValueError:
                add("error", "JOB004", rel, f"status {fm['status']!r} is not a valid state",
                    "use one of: " + ", ".join(s.value for s in State))
        for key in ("created_at", "updated_at", "archived_at"):
            if key in fm and not models.is_utc_timestamp(fm[key]):
                add("error", "JOB006", rel, f"{key} {fm[key]!r} is not a UTC ISO-8601 timestamp", "use the form 2026-10-06T09:15:00Z")
        bullet = STATUS_BULLET.search(body)
        if state is not None and bullet and not bullet_matches(bullet.group(2), state):
            add("warning", "JOB005", rel,
                f"the Status line says {bullet.group(2).strip()!r} but the frontmatter status is {state.value}",
                f"edit the Status line, or run `careeros transition {job_id} --to <STATE>` (add --force --reason \"...\" for a correction) so file, pipeline and ledger agree")
        if valid_id:
            jobs[str(job_id)] = {
                "path": rel,
                "state": state,
                "url": (fm.get("url") or "").rstrip("/"),
                "archived": bool(fm.get("archived", False)),
            }

    # --- pipeline ---
    pipeline = root / "jobs" / "pipeline.md"
    if pipeline.is_file() and jobs:
        entries: dict[str, str] = {}
        for match in PIPE_ENTRY.finditer(_above_fold(pipeline.read_text(encoding="utf-8"))):
            entries.setdefault(match.group(4).rstrip("/"), match.group(2))
        by_url = {info["url"]: info for info in jobs.values() if info["url"]}
        for url, icon in entries.items():
            info = by_url.get(url)
            if info is None:
                add("warning", "PIPE001", "jobs/pipeline.md", f"pipeline entry {url} matches no job",
                    "remove the line, or save the job with the browse skill")
            elif info["state"] is not None and icon != icon_for(info["state"]):
                add("warning", "PIPE003", "jobs/pipeline.md",
                    f"the pipeline shows [{icon}] for {url} but the job is {info['state'].value}, which is [{icon_for(info['state'])}]",
                    "change the icon, or run `careeros transition` so file, pipeline and ledger agree")
        for url, info in by_url.items():
            if url not in entries and not info["archived"]:
                add("warning", "PIPE002", info["path"], "the job has no line in jobs/pipeline.md",
                    "add a pipeline line for it, or archive the job with `careeros archive`")

    # --- ledger ---
    issues.extend(ledger.verify_chain(root))
    events = _lenient_events(root)
    reported: set[str] = set()
    for event in events:
        entity = event.get("entity")
        if isinstance(entity, str) and entity.startswith("job_") and entity not in all_ids and entity not in reported:
            reported.add(entity)
            add("error", "LED003", "ledger.jsonl",
                f"event {event.get('seq')} refers to {entity}, but no job file has that id",
                "restore the job directory from .careeros/backups or version control; archive jobs with `careeros archive` instead of deleting them")
    for event in events:
        if event.get("type") != "job.status_changed":
            continue
        try:
            before, after = State(event.get("prev_state")), State(event.get("new_state"))
        except ValueError:
            continue
        if not is_legal(before, after):
            add("error", "LED004", "ledger.jsonl",
                f"event {event.get('seq')} moved {event.get('entity')} from {before.value} to {after.value}, which the state machine forbids",
                "record rule-breaking changes with `careeros transition --force --reason \"...\"`, which writes a job.status_corrected event")
    for job_id, info in jobs.items():
        recorded = recorded_state(events, job_id)
        if info["state"] is not None and recorded is not None and recorded != info["state"].value:
            add("warning", "LED005", info["path"],
                f"the job shows {info['state'].value} but its latest ledger event recorded {recorded}",
                f"run `careeros transition {job_id} --to {info['state'].value}` to bring the ledger up to date")
    return issues
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass. The healthy-workspace test is the strictest: if it reports any issue, fix the implementation, not the test.

- [ ] **Step 5: Commit**

```bash
git add careeros/core/validation.py tests/test_core_validation.py
git commit -m "core: deterministic workspace validation with actionable fixes" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 6: Migration and upgrade

**Files:**
- Create: `careeros/core/migration.py`
- Modify: `tests/helpers.py` (append the legacy builder)
- Test: `tests/test_core_migration.py`

**Interfaces:**
- Consumes: Tasks 1–5, plus `careeros.workspace.scaffold._RUNTIME_CONFIG` (framework file selection per runtime: keys `templates_dir`, `sentinel`, `framework_prefixes`).
- Produces (in `careeros.core.migration`):
  - `LEGACY_STATUS: dict[str, State]`
  - `class MigrationError(WorkspaceError)`
  - `@dataclass JobChange(path, rel, original, new_text, job_id, status, legacy_status)`
  - `@dataclass MigrationPlan(root, source_schema, target_schema, meta_to_write, job_changes, errors, notes)` with `.empty`
  - `@dataclass OperationResult(status, backup, jobs, manifest)` where `status` is `"complete"` or `"noop"`
  - `plan_migration(root) -> MigrationPlan`, `run_migration(root, plan) -> OperationResult`
  - `@dataclass UpgradeItem(rel, status, src, dst, before)`, `@dataclass UpgradePlan(root, items, meta, schema_behind)` with `.pending`, `.framework_stale`, `.noop`
  - `plan_upgrade(root) -> UpgradePlan`, `run_upgrade(root, plan) -> OperationResult`
  - `find_incomplete_operations(root) -> list[Path]`

- [ ] **Step 1: Extend the helpers with a legacy workspace builder**

Append to `tests/helpers.py`:

```python


# --- legacy (schema 0) workspaces ---------------------------------------------------

LEGACY_JOBS = [
    # slug, title, company, legacy status, pipeline icon the legacy skills would show
    ("acme-backend-engineer", "Backend Engineer", "Acme", "discovered", " "),
    ("globex-senior-engineer-at-scale", "Senior Engineer at Scale", "Globex", "discovered", " "),
    ("initech-platform-engineer", "Platform Engineer", "Initech", "applied", "~"),
    ("hooli-staff-engineer", "Staff Engineer", "Hooli", "interview", "?"),
    ("umbrella-sde-3", "SDE-3", "Umbrella", "offer", "✓"),
    ("wayne-principal-engineer", "Principal Engineer", "Wayne", "accepted", "✓"),
    ("stark-engineer", "Engineer", "Stark", "declined", "x"),
    ("oscorp-engineer", "Engineer", "Oscorp", "closed", "x"),
]


def legacy_job_text(title: str, company: str, url: str, status: str, discovered: str = "2026-10-01") -> str:
    return (
        f"# {title} at {company}\n\n"
        f"- **Board:** LinkedIn\n- **Location:** Bengaluru\n- **Market:** home\n"
        f"- **URL:** {url}\n- **Score:** 8\n- **Discovered:** {discovered}\n- **Status:** {status}\n\n"
        f"## Notes\nSnippet text — with unicode ✓\n"
    )


def make_legacy_workspace(root: Path) -> None:
    """A schema-0 workspace shaped like the real one: no metadata, no frontmatter, no ledger."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.md").write_text("# Kaushal's Career Profile\n\n## Markets\n| Market | Min base |\n", encoding="utf-8")
    (root / "boards.md").write_text("# Job Boards\n\n### linkedin\n- **Market:** home\n", encoding="utf-8")
    (root / "resume.md").write_text("# Resume\n\n## Education\n", encoding="utf-8")
    (root / "activity.md").write_text(
        "# Activity Log\n\n<!-- append-only; newest entries at top -->\n2026-09-29 careeros update: variants\n",
        encoding="utf-8",
    )
    (root / "resume-variants").mkdir()
    (root / "resume-variants" / "Resume.pdf").write_bytes(b"%PDF-1.4\n%fixture \x00\xff\n")
    (root / "markets").mkdir()
    (root / "markets" / "singapore.md").write_text("# Singapore — Market Notes\n", encoding="utf-8")
    pipeline = ["# Job Pipeline", "", "## Active", ""]
    for slug, title, company, status, icon in LEGACY_JOBS:
        url = f"https://example.com/jobs/{slug}"
        job_dir = root / "jobs" / "discovered" / slug
        job_dir.mkdir(parents=True)
        (job_dir / "job.md").write_text(legacy_job_text(title, company, url, status), encoding="utf-8")
        pipeline.append(f"- [{icon}] **{company}** — {title} · Bengaluru · Score 8 · 2026-10-01 · {url}")
    (root / "jobs" / "discovered" / "acme-backend-engineer" / "people.md").write_text("# Hiring contacts\n", encoding="utf-8")
    (root / "jobs" / "pipeline.md").write_text("\n".join(pipeline) + "\n", encoding="utf-8")


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file in the workspace except CareerOS's own bookkeeping, keyed by relative path."""
    skip = {".careeros", "ledger.jsonl"}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).parts[0] not in skip
    }
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_core_migration.py`:

```python
import hashlib
import json
from pathlib import Path

import pytest
from helpers import LEGACY_JOBS, legacy_job_text, make_legacy_workspace, snapshot

from careeros.core import ids, ledger, versions
from careeros.core import migration as mig
from careeros.core import workspace as ws
from careeros.core.models import State, WorkspaceMeta
from careeros.core.validation import validate_workspace
from careeros.workspace.scaffold import scaffold

TS = "2026-10-01T00:00:00Z"


@pytest.fixture
def legacy(tmp_path: Path, clock) -> Path:
    root = tmp_path / "ws"
    make_legacy_workspace(root)
    return root


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _add_legacy_job(root: Path, slug: str, text: str) -> Path:
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- planning --------------------------------------------------------------------------

def test_plan_covers_every_job_and_maps_legacy_statuses(legacy: Path) -> None:
    plan = mig.plan_migration(legacy)
    assert plan.errors == [] and plan.source_schema == 0 and plan.target_schema == 1
    assert plan.meta_to_write is not None and plan.meta_to_write.schema_version == 1
    assert plan.meta_to_write.framework_version == versions.installed_version()
    assert len(plan.job_changes) == len(LEGACY_JOBS) == 8
    mapped = {c.legacy_status: c.status for c in plan.job_changes}
    assert mapped == {
        "discovered": State.DISCOVERED, "applied": State.APPLIED, "interview": State.SCREEN, "offer": State.OFFER,
        "accepted": State.ACCEPTED, "declined": State.WITHDRAWN, "closed": State.WITHDRAWN,
    }
    assert any("interview" in note and "SCREEN" in note for note in plan.notes)
    assert any("midnight UTC" in note for note in plan.notes)
    assert len({c.job_id for c in plan.job_changes}) == 8 and all(ids.is_valid_id("job", c.job_id) for c in plan.job_changes)


def test_title_containing_at_splits_on_the_last_at(legacy: Path) -> None:
    plan = mig.plan_migration(legacy)
    change = next(c for c in plan.job_changes if "globex" in c.rel)
    fm, _ = ws.split_frontmatter(change.new_text)
    assert (fm["title"], fm["company"]) == ("Senior Engineer at Scale", "Globex")


def test_planning_writes_nothing(legacy: Path) -> None:
    before = snapshot(legacy)
    mig.plan_migration(legacy)
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros").exists() and not (legacy / "ledger.jsonl").exists()


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("# No separator here\n- **URL:** https://x.test\n- **Discovered:** 2026-10-01\n- **Status:** discovered\n", "Title at Company"),
        ("# T at C\n- **Discovered:** 2026-10-01\n- **Status:** discovered\n", "URL"),
        ("# T at C\n- **URL:** https://x.test\n- **Status:** discovered\n", "Discovered"),
        ("# T at C\n- **URL:** https://x.test\n- **Discovered:** soon\n- **Status:** discovered\n", "YYYY-MM-DD"),
        ("# T at C\n- **URL:** https://x.test\n- **Discovered:** 2026-10-01\n- **Status:** wibble\n", "unknown Status"),
        ("- **URL:** https://x.test\n", "heading"),
    ],
)
def test_each_unparseable_job_is_reported_by_file(legacy: Path, text: str, fragment: str) -> None:
    _add_legacy_job(legacy, "broken-job", text)
    plan = mig.plan_migration(legacy)
    assert any("jobs/discovered/broken-job/job.md" in e and fragment in e for e in plan.errors), plan.errors


# --- running ---------------------------------------------------------------------------

def test_migrate_prepends_frontmatter_and_changes_nothing_else(legacy: Path) -> None:
    before = snapshot(legacy)
    result = mig.run_migration(legacy, mig.plan_migration(legacy))
    assert result.status == "complete" and result.jobs == 8
    after = snapshot(legacy)
    assert set(after) == set(before)
    for rel, original in before.items():
        if rel.endswith("/job.md"):
            fm, body = ws.split_frontmatter(after[rel].decode("utf-8"))
            assert body.encode("utf-8") == original  # the old file is the new body, byte for byte
            assert ids.is_valid_id("job", fm["id"]) and fm["type"] == "job" and fm["schema"] == 1
            assert fm["created_at"] == "2026-10-01T00:00:00Z" and fm["created_at_precision"] == "date"
            assert fm["updated_at"].startswith("2026-10-06T09:")
        else:
            assert after[rel] == original, rel


def test_migrate_writes_metadata_and_a_valid_ledger(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    meta = ws.load_meta(legacy)
    assert meta is not None and meta.schema_version == 1 and meta.framework_version == versions.installed_version()
    events = ledger.read_events(legacy)
    assert [e["type"] for e in events].count("job.imported") == 8
    assert events[0]["type"] == "workspace.migrated"
    imported = {e["entity"]: e["new_state"] for e in events if e["type"] == "job.imported"}
    assert sorted(imported.values()).count("WITHDRAWN") == 2
    assert ledger.verify_chain(legacy) == []


def test_migrated_workspace_validates_with_no_errors_or_warnings(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    assert validate_workspace(legacy) == []


def test_backup_holds_originals_and_manifest_records_hashes(legacy: Path) -> None:
    before = snapshot(legacy)
    result = mig.run_migration(legacy, mig.plan_migration(legacy))
    manifest = json.loads((result.backup / "manifest.json").read_text())
    assert manifest["status"] == "complete" and manifest["kind"] == "migrate"
    assert (manifest["source_schema"], manifest["target_schema"]) == (0, 1)
    assert manifest["tool_version"] == versions.installed_version()
    by_path = {f["path"]: f for f in manifest["files"]}
    for rel, original in before.items():
        if rel.endswith("/job.md"):
            record = by_path[rel]
            assert record["existed"] is True
            assert record["before_sha256"] == _sha(original)
            assert (result.backup / rel).read_bytes() == original
            assert record["after_sha256"] == _sha((legacy / rel).read_bytes())
    assert by_path[".careeros/workspace.yaml"]["existed"] is False
    assert by_path[".careeros/workspace.yaml"]["after_sha256"] == _sha((legacy / ".careeros" / "workspace.yaml").read_bytes())
    assert by_path["ledger.jsonl"]["existed"] is False


def test_migration_is_idempotent(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    after_first = snapshot(legacy)
    ledger_bytes = (legacy / "ledger.jsonl").read_bytes()
    plan = mig.plan_migration(legacy)
    assert plan.empty and not plan.errors
    assert mig.run_migration(legacy, plan).status == "noop"
    assert snapshot(legacy) == after_first and (legacy / "ledger.jsonl").read_bytes() == ledger_bytes
    assert len(list((legacy / ".careeros" / "backups").iterdir())) == 1


def test_a_second_run_adopts_only_new_legacy_jobs(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    ids_before = {p.parent.name: ws.read_job(p).id for p in ws.job_files(legacy)}
    _add_legacy_job(legacy, "newco-engineer", legacy_job_text("Engineer", "NewCo", "https://example.com/jobs/newco", "discovered"))
    plan = mig.plan_migration(legacy)
    assert plan.meta_to_write is None and [c.rel for c in plan.job_changes] == ["jobs/discovered/newco-engineer/job.md"]
    result = mig.run_migration(legacy, plan)
    assert result.status == "complete" and (result.backup / "ledger.jsonl").exists()
    assert {p.parent.name: ws.read_job(p).id for p in ws.job_files(legacy) if p.parent.name in ids_before} == ids_before
    assert ledger.verify_chain(legacy) == []
    assert [e["type"] for e in ledger.read_events(legacy)][-2:] == ["workspace.migrated", "job.imported"]


def test_unknown_status_aborts_with_nothing_written(legacy: Path) -> None:
    _add_legacy_job(legacy, "weird", legacy_job_text("Engineer", "Weird", "https://example.com/weird", "wibble"))
    before = snapshot(legacy)
    plan = mig.plan_migration(legacy)
    assert plan.errors
    with pytest.raises(mig.MigrationError):
        mig.run_migration(legacy, plan)
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros").exists() and not (legacy / "ledger.jsonl").exists()


def test_a_file_changed_after_planning_aborts_before_anything_is_written(legacy: Path) -> None:
    plan = mig.plan_migration(legacy)
    victim = legacy / "jobs" / "discovered" / "acme-backend-engineer" / "job.md"
    victim.write_text(victim.read_text() + "edited meanwhile\n")
    before = snapshot(legacy)
    with pytest.raises(mig.MigrationError, match="changed since the plan"):
        mig.run_migration(legacy, plan)
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros" / "backups").exists()


def test_backup_verification_failure_aborts_before_modifying_the_workspace(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = mig.plan_migration(legacy)
    real = mig.atomic_write_bytes

    def corrupting(path: Path, data: bytes) -> None:
        if "backups" in Path(path).parts and Path(path).name == "job.md":
            data = data + b"corrupt"
        real(path, data)

    monkeypatch.setattr(mig, "atomic_write_bytes", corrupting)
    before = snapshot(legacy)
    with pytest.raises(mig.MigrationError, match="backup verification failed"):
        mig.run_migration(legacy, plan)
    monkeypatch.undo()
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros" / "workspace.yaml").exists()


def test_failure_while_applying_rolls_everything_back(legacy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = snapshot(legacy)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(mig.ledger, "append_events", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        mig.run_migration(legacy, mig.plan_migration(legacy))
    monkeypatch.undo()
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros" / "workspace.yaml").exists()
    assert not (legacy / "ledger.jsonl").exists()
    manifests = list((legacy / ".careeros" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1 and json.loads(manifests[0].read_text())["status"] == "rolled_back"
    assert mig.find_incomplete_operations(legacy) == []


def test_find_incomplete_operations_reports_pending_manifests(legacy: Path) -> None:
    backup = legacy / ".careeros" / "backups" / "20261006T090000Z-migrate"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text(json.dumps({"status": "pending", "files": []}))
    assert mig.find_incomplete_operations(legacy) == [backup]


def test_migrate_refuses_a_workspace_from_the_future(legacy: Path) -> None:
    ws.save_meta(legacy, WorkspaceMeta(2, versions.installed_version(), TS, TS, ()))
    with pytest.raises(ws.WorkspaceError, match="newer than this CareerOS supports"):
        mig.plan_migration(legacy)


# --- upgrade ---------------------------------------------------------------------------

def _claude_workspace(tmp_path: Path, framework: str = "0.1.0") -> Path:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    (root / "profile.md").write_text("# my profile\n", encoding="utf-8")
    ws.save_meta(root, WorkspaceMeta(1, framework, TS, TS, ("claude",)))
    ledger.append_event(root, type="workspace.created", actor="system", action="created")
    return root


def test_upgrade_plan_reports_new_changed_and_unchanged(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path)
    (root / ".claude" / "skills" / "browse" / "SKILL.md").write_text("customised")
    (root / ".claude" / "skills" / "track" / "SKILL.md").unlink()
    plan = mig.plan_upgrade(root)
    status = {i.rel: i.status for i in plan.items}
    assert status[".claude/skills/browse/SKILL.md"] == "changed"
    assert status[".claude/skills/track/SKILL.md"] == "new"
    assert status["CLAUDE.md"] == "unchanged"
    assert plan.framework_stale and not plan.noop and not plan.schema_behind


def test_upgrade_backs_up_refreshes_and_keeps_user_data(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path)
    skill = root / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("customised")
    user_files = {rel: data for rel, data in snapshot(root).items() if not rel.startswith(".claude/") and rel != "CLAUDE.md"}
    result = mig.run_upgrade(root, mig.plan_upgrade(root))
    assert result.status == "complete"
    assert skill.read_text() != "customised"
    assert (result.backup / ".claude" / "skills" / "browse" / "SKILL.md").read_text() == "customised"
    manifest = json.loads((result.backup / "manifest.json").read_text())
    assert manifest["kind"] == "upgrade" and manifest["status"] == "complete"
    record = next(f for f in manifest["files"] if f["path"] == ".claude/skills/browse/SKILL.md")
    assert record["before_sha256"] == _sha(b"customised") and record["after_sha256"] == _sha(skill.read_bytes())
    assert ws.load_meta(root).framework_version == versions.installed_version()
    assert ledger.read_events(root)[-1]["type"] == "workspace.upgraded"
    assert ledger.verify_chain(root) == []
    for rel, data in user_files.items():
        assert (root / rel).read_bytes() == data, rel


def test_upgrade_is_a_no_op_when_everything_is_current(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path, framework=versions.installed_version())
    plan = mig.plan_upgrade(root)
    assert plan.noop
    assert mig.run_upgrade(root, plan).status == "noop"


def test_upgrade_on_a_legacy_workspace_refreshes_files_but_creates_no_metadata(tmp_path: Path, clock) -> None:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    (root / ".claude" / "skills" / "browse" / "SKILL.md").write_text("old")
    plan = mig.plan_upgrade(root)
    assert plan.schema_behind
    mig.run_upgrade(root, plan)
    assert (root / ".claude" / "skills" / "browse" / "SKILL.md").read_text() != "old"
    assert not (root / ".careeros" / "workspace.yaml").exists() and not (root / "ledger.jsonl").exists()


def test_upgrade_refuses_a_workspace_from_the_future(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path, framework="9.0.0")
    with pytest.raises(ws.WorkspaceError, match="newer than the installed"):
        mig.run_upgrade(root, mig.plan_upgrade(root))


def test_upgrade_failure_rolls_back_files_and_metadata(tmp_path: Path, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _claude_workspace(tmp_path)
    skill = root / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("customised")
    meta_before = (root / ".careeros" / "workspace.yaml").read_bytes()
    before = snapshot(root)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(mig.ledger, "append_event", boom)
    with pytest.raises(OSError):
        mig.run_upgrade(root, mig.plan_upgrade(root))
    monkeypatch.undo()
    assert snapshot(root) == before
    assert (root / ".careeros" / "workspace.yaml").read_bytes() == meta_before
    assert mig.find_incomplete_operations(root) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_core_migration.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'careeros.core.migration'`).

- [ ] **Step 4: Implement `careeros/core/migration.py`**

```python
"""Backups with manifests, schema migration and framework upgrade."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from careeros.core import ids, ledger, models, versions
from careeros.core.models import State, WorkspaceMeta
from careeros.core.workspace import (
    BACKUPS_REL,
    LEDGER_REL,
    META_REL,
    WorkspaceError,
    WorkspaceLock,
    atomic_write_bytes,
    ensure_writable,
    job_files,
    join_frontmatter,
    load_meta,
    save_meta,
    split_frontmatter,
)
from careeros.workspace import scaffold as _scaffold

LEGACY_STATUS: dict[str, State] = {
    "discovered": State.DISCOVERED,
    "applied": State.APPLIED,
    "interview": State.SCREEN,
    "offer": State.OFFER,
    "accepted": State.ACCEPTED,
    "declined": State.WITHDRAWN,
    "closed": State.WITHDRAWN,
    "rejected": State.REJECTED,
}


class MigrationError(WorkspaceError):
    """A migration or upgrade could not be completed safely."""


@dataclass
class JobChange:
    path: Path
    rel: str
    original: bytes
    new_text: str
    job_id: str
    status: State
    legacy_status: str


@dataclass
class MigrationPlan:
    root: Path
    source_schema: int
    target_schema: int
    meta_to_write: WorkspaceMeta | None
    job_changes: list[JobChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.meta_to_write is None and not self.job_changes


@dataclass
class OperationResult:
    status: str  # "complete" | "noop"
    backup: Path | None
    jobs: int
    manifest: dict | None


# --- helpers ----------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _new_backup_dir(root: Path, kind: str) -> Path:
    stamp = models.utc_now().replace("-", "").replace(":", "")
    base = root / BACKUPS_REL
    candidate = base / f"{stamp}-{kind}"
    n = 2
    while candidate.exists():
        candidate = base / f"{stamp}-{kind}-{n}"
        n += 1
    candidate.mkdir(parents=True)
    return candidate


def _write_manifest(backup: Path, manifest: dict) -> None:
    atomic_write_bytes(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))


def _backup_files(backup: Path, items: list[tuple[str, bytes]]) -> None:
    for rel, data in items:
        destination = backup / rel
        atomic_write_bytes(destination, data)
        if _sha(destination.read_bytes()) != _sha(data):
            raise MigrationError(f"backup verification failed for {rel}; nothing was changed")


def _detect_runtimes(root: Path) -> tuple[str, ...]:
    return tuple(name for name, cfg in _scaffold._RUNTIME_CONFIG.items() if (root / cfg["sentinel"]).exists())


def find_incomplete_operations(root: Path) -> list[Path]:
    base = Path(root) / BACKUPS_REL
    pending: list[Path] = []
    if base.is_dir():
        for manifest in sorted(base.glob("*/manifest.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except ValueError:
                pending.append(manifest.parent)
                continue
            if data.get("status") == "pending":
                pending.append(manifest.parent)
    return pending


# --- legacy parsing ----------------------------------------------------------------------

_HEADING = re.compile(r"^# (.+?)[ \t]*$", re.M)


def _bullet(text: str, name: str) -> str | None:
    match = re.search(rf"^- \*\*{re.escape(name)}:\*\*[ \t]*(.*?)[ \t]*$", text, re.M)
    return match.group(1) if match and match.group(1) else None


def _parse_legacy(text: str) -> tuple[str, str, str, str, str]:
    heading = _HEADING.search(text)
    if not heading:
        raise ValueError("no '# Title at Company' heading")
    title, separator, company = heading.group(1).rpartition(" at ")
    if not separator or not title.strip() or not company.strip():
        raise ValueError(f"heading {heading.group(1)!r} does not look like '# Title at Company'")
    url = _bullet(text, "URL")
    if not url:
        raise ValueError("missing the '- **URL:**' line")
    discovered = _bullet(text, "Discovered")
    if not discovered:
        raise ValueError("missing the '- **Discovered:**' line")
    date_match = re.match(r"\d{4}-\d{2}-\d{2}", discovered)
    if not date_match:
        raise ValueError(f"Discovered {discovered!r} does not start with YYYY-MM-DD")
    try:
        models.date_to_utc(date_match.group(0))
    except ValueError:
        raise ValueError(f"Discovered {discovered!r} is not a real date") from None
    status = _bullet(text, "Status")
    if not status:
        raise ValueError("missing the '- **Status:**' line")
    return title.strip(), company.strip(), url, date_match.group(0), status.strip().lower()


# --- migration ---------------------------------------------------------------------------

def plan_migration(root: Path) -> MigrationPlan:
    """Compute everything in memory. Nothing is written."""
    root = Path(root)
    meta = load_meta(root)
    source = 0 if meta is None else meta.schema_version
    if source > versions.SCHEMA_VERSION:
        raise WorkspaceError(
            f"workspace schema {source} is newer than this CareerOS supports ({versions.SCHEMA_VERSION}); "
            "upgrade CareerOS first"
        )
    now = models.utc_now()
    plan = MigrationPlan(root, source, versions.SCHEMA_VERSION, None)
    if meta is None:
        plan.meta_to_write = WorkspaceMeta(
            versions.SCHEMA_VERSION, versions.installed_version(), now, now, _detect_runtimes(root)
        )
    existing_ids: set[str] = set()
    legacy: list[Path] = []
    for path in job_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            frontmatter, _ = split_frontmatter(path.read_bytes().decode("utf-8"))
        except (WorkspaceError, UnicodeDecodeError) as exc:
            plan.errors.append(f"{rel}: cannot read the file ({exc})")
            continue
        if frontmatter is None:
            legacy.append(path)
        elif frontmatter.get("id"):
            existing_ids.add(str(frontmatter["id"]))
    interview: list[str] = []
    for path in legacy:
        rel = path.relative_to(root).as_posix()
        original = path.read_bytes()
        text = original.decode("utf-8")
        try:
            title, company, url, discovered, legacy_status = _parse_legacy(text)
        except ValueError as exc:
            plan.errors.append(f"{rel}: {exc}")
            continue
        state = LEGACY_STATUS.get(legacy_status)
        if state is None:
            plan.errors.append(
                f"{rel}: unknown Status {legacy_status!r} (known: {', '.join(sorted(LEGACY_STATUS))})"
            )
            continue
        job_id = ids.new_id("job", existing_ids)
        existing_ids.add(job_id)
        frontmatter = {
            "id": job_id,
            "type": "job",
            "schema": versions.SCHEMA_VERSION,
            "status": state.value,
            "company": company,
            "title": title,
            "url": url,
            "created_at": models.date_to_utc(discovered),
            "created_at_precision": "date",
            "updated_at": now,
        }
        plan.job_changes.append(
            JobChange(path, rel, original, join_frontmatter(frontmatter, text), job_id, state, legacy_status)
        )
        if legacy_status == "interview":
            interview.append(rel)
    if interview:
        plan.notes.append(
            f"{len(interview)} job(s) had Status 'interview' and are mapped to SCREEN because the old field "
            f"does not record the stage; correct them with `careeros transition --force --reason ...`: "
            + ", ".join(interview)
        )
    if plan.job_changes:
        plan.notes.append(
            f"{len(plan.job_changes)} job(s) have date-only creation times; they are stored as midnight UTC "
            "with created_at_precision: date"
        )
    return plan


def _verify_unchanged(changes: list[JobChange]) -> None:
    for change in changes:
        if _sha(change.path.read_bytes()) != _sha(change.original):
            raise MigrationError(f"{change.rel} changed since the plan was made; run the command again")


def run_migration(root: Path, plan: MigrationPlan) -> OperationResult:
    root = Path(root)
    if plan.errors:
        raise MigrationError("; ".join(plan.errors))
    ensure_writable(root)
    if plan.empty:
        return OperationResult("noop", None, 0, None)
    with WorkspaceLock(root):
        ensure_writable(root)
        _verify_unchanged(plan.job_changes)
        ledger_path = root / LEDGER_REL
        ledger_before = ledger_path.read_bytes() if ledger_path.exists() else None
        backup = _new_backup_dir(root, "migrate")
        backup_items = [(c.rel, c.original) for c in plan.job_changes]
        if ledger_before is not None:
            backup_items.append((LEDGER_REL.as_posix(), ledger_before))
        _backup_files(backup, backup_items)
        files: list[dict] = [
            {"path": c.rel, "existed": True, "before_sha256": _sha(c.original), "after_sha256": None}
            for c in plan.job_changes
        ]
        if plan.meta_to_write is not None:
            files.append({"path": META_REL.as_posix(), "existed": False, "before_sha256": None, "after_sha256": None})
        files.append({
            "path": LEDGER_REL.as_posix(),
            "existed": ledger_before is not None,
            "before_sha256": _sha(ledger_before) if ledger_before is not None else None,
            "after_sha256": None,
        })
        manifest = {
            "version": 1,
            "kind": "migrate",
            "created_at": models.utc_now(),
            "tool_version": versions.installed_version(),
            "source_schema": plan.source_schema,
            "target_schema": plan.target_schema,
            "status": "pending",
            "files": files,
        }
        _write_manifest(backup, manifest)
        written: list[JobChange] = []
        meta_created = False
        try:
            if plan.meta_to_write is not None:
                save_meta(root, plan.meta_to_write)
                meta_created = True
            for change in plan.job_changes:
                atomic_write_bytes(change.path, change.new_text.encode("utf-8"))
                written.append(change)
            backup_rel = backup.relative_to(root).as_posix()
            specs = [{
                "type": "workspace.migrated", "actor": "system", "source": "migration",
                "action": f"migrated workspace from schema {plan.source_schema} to {plan.target_schema}: "
                          f"{len(plan.job_changes)} job(s) given ids",
                "artifacts": [f"{backup_rel}/manifest.json"],
            }]
            for change in plan.job_changes:
                specs.append({
                    "type": "job.imported", "actor": "system", "source": "migration",
                    "entity": change.job_id, "new_state": change.status.value,
                    "action": f"imported legacy job {change.rel} as {change.status.value}",
                    "artifacts": [change.rel],
                })
            ledger.append_events(root, specs)
        except BaseException:
            for change in written:
                atomic_write_bytes(change.path, change.original)
            if meta_created:
                (root / META_REL).unlink(missing_ok=True)
            manifest["status"] = "rolled_back"
            _write_manifest(backup, manifest)
            raise
        for record in files:
            current = root / record["path"]
            record["after_sha256"] = _sha(current.read_bytes()) if current.exists() else None
        manifest["status"] = "complete"
        _write_manifest(backup, manifest)
        return OperationResult("complete", backup, len(plan.job_changes), manifest)


# --- upgrade -----------------------------------------------------------------------------

@dataclass
class UpgradeItem:
    rel: str
    status: str  # "new" | "changed" | "unchanged"
    src: Path
    dst: Path
    before: bytes | None


@dataclass
class UpgradePlan:
    root: Path
    items: list[UpgradeItem]
    meta: WorkspaceMeta | None
    schema_behind: bool

    @property
    def pending(self) -> list[UpgradeItem]:
        return [i for i in self.items if i.status != "unchanged"]

    @property
    def framework_stale(self) -> bool:
        return self.meta is not None and versions.compare_versions(versions.installed_version(), self.meta.framework_version) > 0

    @property
    def noop(self) -> bool:
        return not self.pending and not self.framework_stale


def plan_upgrade(root: Path) -> UpgradePlan:
    root = Path(root)
    meta = load_meta(root)
    items: list[UpgradeItem] = []
    for _runtime, cfg in _scaffold._RUNTIME_CONFIG.items():
        sentinel = cfg["sentinel"]
        if not (root / sentinel).exists():
            continue
        templates = cfg["templates_dir"]
        for src in sorted(templates.rglob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(templates).as_posix()
            if not (rel == sentinel or any(rel.startswith(p) for p in cfg["framework_prefixes"])):
                continue
            dst = root / rel
            if not dst.exists():
                items.append(UpgradeItem(rel, "new", src, dst, None))
            else:
                current = dst.read_bytes()
                items.append(UpgradeItem(rel, "unchanged" if current == src.read_bytes() else "changed", src, dst, current))
    schema_behind = meta is None or meta.schema_version < versions.SCHEMA_VERSION
    return UpgradePlan(root, items, meta, schema_behind)


def run_upgrade(root: Path, plan: UpgradePlan) -> OperationResult:
    root = Path(root)
    ensure_writable(root)
    if plan.noop:
        return OperationResult("noop", None, 0, None)
    with WorkspaceLock(root):
        meta = ensure_writable(root)
        for item in plan.pending:
            if item.status == "changed" and item.dst.read_bytes() != item.before:
                raise MigrationError(f"{item.rel} changed since the plan was made; run the command again")
        meta_path = root / META_REL
        meta_before = meta_path.read_bytes() if meta is not None else None
        backup = _new_backup_dir(root, "upgrade")
        _backup_files(backup, [(i.rel, i.before) for i in plan.pending if i.status == "changed" and i.before is not None])
        files: list[dict] = [
            {
                "path": i.rel,
                "existed": i.before is not None,
                "before_sha256": _sha(i.before) if i.before is not None else None,
                "after_sha256": None,
            }
            for i in plan.pending
        ]
        if meta_before is not None:
            _backup_files(backup, [(META_REL.as_posix(), meta_before)])
            files.append({"path": META_REL.as_posix(), "existed": True, "before_sha256": _sha(meta_before), "after_sha256": None})
        manifest = {
            "version": 1,
            "kind": "upgrade",
            "created_at": models.utc_now(),
            "tool_version": versions.installed_version(),
            "source_schema": meta.schema_version if meta else 0,
            "target_schema": meta.schema_version if meta else 0,
            "status": "pending",
            "files": files,
        }
        _write_manifest(backup, manifest)
        written: list[UpgradeItem] = []
        try:
            for item in plan.pending:
                atomic_write_bytes(item.dst, item.src.read_bytes())
                written.append(item)
            if meta is not None:
                now = models.utc_now()
                save_meta(root, WorkspaceMeta(
                    meta.schema_version, versions.installed_version(), meta.created_at, now, meta.runtimes
                ))
                if (root / LEDGER_REL).exists():
                    changed = len(plan.pending)
                    ledger.append_event(
                        root, type="workspace.upgraded", actor="system", source="upgrade",
                        action=f"upgraded framework files to CareerOS {versions.installed_version()}: {changed} file(s) written",
                        artifacts=[f"{backup.relative_to(root).as_posix()}/manifest.json"],
                    )
        except BaseException:
            for item in written:
                if item.before is None:
                    item.dst.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(item.dst, item.before)
            if meta_before is not None:
                atomic_write_bytes(meta_path, meta_before)
            manifest["status"] = "rolled_back"
            _write_manifest(backup, manifest)
            raise
        for record in files:
            current = root / record["path"]
            record["after_sha256"] = _sha(current.read_bytes()) if current.exists() else None
        manifest["status"] = "complete"
        _write_manifest(backup, manifest)
        return OperationResult("complete", backup, len(plan.pending), manifest)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass. Common causes if not: the pipeline icons in the legacy builder must match the mapped states (the "validates with no errors or warnings" test checks this), and `ledger.append_event` must be called through the `ledger` module so the monkeypatches in the rollback tests take effect.

- [ ] **Step 6: Commit**

```bash
git add careeros/core/migration.py tests/helpers.py tests/test_core_migration.py
git commit -m "core: verified backups with manifests, idempotent migration and upgrade with rollback" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 7: CLI commands

**Files:**
- Create: `careeros/cli/_util.py`, `careeros/cli/doctor.py`, `careeros/cli/status.py`, `careeros/cli/validate.py`, `careeros/cli/ledger.py`, `careeros/cli/transition.py`, `careeros/cli/approve.py`, `careeros/cli/archive.py`, `careeros/cli/migrate.py`, `careeros/cli/upgrade.py`
- Modify: `careeros/cli/main.py`, `careeros/cli/init_cmd.py`, `tests/helpers.py` (one builder)
- Test: `tests/test_cli_foundation.py`

**Interfaces:**
- Consumes: all of `careeros.core`.
- Produces: the commands in the spec's CLI table. Exit codes: 0 success, 1 failure or validation errors (or a declined confirmation, or a denied approval), 2 usage error or confirmation required. Every command takes `--workspace/-w PATH` (env `CAREEROS_WORKSPACE`; otherwise discovery upward from the current directory). `_util.is_interactive()` is the single terminal check; tests replace it.

- [ ] **Step 1: Add one more helper**

Append to `tests/helpers.py`:

```python


def make_claude_workspace(root: Path, framework: str = "0.1.0") -> Path:
    """A scaffolded Claude workspace with metadata at an older framework version and a ledger."""
    from careeros.workspace.scaffold import scaffold

    scaffold(root, runtime="claude")
    (root / "profile.md").write_text("# my profile\n", encoding="utf-8")
    save_meta(root, WorkspaceMeta(versions.SCHEMA_VERSION, framework, TS, TS, ("claude",)))
    ledger.append_event(root, type="workspace.created", actor="system", action="created")
    return root
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_cli_foundation.py`:

```python
import json
from pathlib import Path

import pytest
from helpers import (
    add_job, legacy_job_text, make_claude_workspace, make_legacy_workspace, make_workspace, snapshot,
)
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger, versions
from careeros.core import workspace as ws
from careeros.core.models import State, WorkspaceMeta

runner = CliRunner()
TS = "2026-10-01T00:00:00Z"


def run(root: Path | None, *args: str, input: str | None = None, env: dict | None = None):
    argv = list(args)
    if root is not None:
        argv += ["--workspace", str(root)]
    return runner.invoke(app, argv, input=input, env=env)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_workspace(tmp_path / "ws")


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_util, "is_interactive", lambda: True)


def _events(root: Path) -> list[dict]:
    return ledger.read_events(root)


# --- workspace resolution ----------------------------------------------------------------

def test_workspace_from_env_var(root: Path) -> None:
    result = run(None, "status", "--json", env={"CAREEROS_WORKSPACE": str(root)})
    assert result.exit_code == 0
    assert json.loads(result.stdout)["workspace"] == str(root.resolve())


def test_no_workspace_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = run(None, "status")
    assert result.exit_code == 2 and "no CareerOS workspace found" in result.output


# --- validate ----------------------------------------------------------------------------

def test_validate_clean_and_json(root: Path) -> None:
    add_job(root, "one")
    result = run(root, "validate")
    assert result.exit_code == 0 and "0 error(s), 0 warning(s)" in result.output
    assert json.loads(run(root, "validate", "--json").stdout) == {"errors": 0, "warnings": 0, "issues": []}


def test_validate_legacy_workspace_fails_with_actionable_fix(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    result = run(legacy, "validate")
    assert result.exit_code == 1 and "WS001" in result.output and "careeros migrate" in result.output


def test_validate_strict_turns_warnings_into_failure(root: Path) -> None:
    add_job(root, "one", pipeline=False)
    assert run(root, "validate").exit_code == 0
    assert run(root, "validate", "--strict").exit_code == 1


# --- status and doctor -------------------------------------------------------------------

def test_status_json_summarises_jobs_events_and_versions(root: Path) -> None:
    add_job(root, "one")
    add_job(root, "two", status=State.APPLIED, bullet="applied")
    data = json.loads(run(root, "status", "--json").stdout)
    assert data["jobs"] == {"DISCOVERED": 1, "APPLIED": 1}
    assert (data["schema_version"], data["migrate_needed"], data["upgrade_available"], data["workspace_newer"]) == (1, False, False, False)
    assert [e["type"] for e in data["recent_events"]] == ["job.imported", "job.imported"]


def test_status_on_a_legacy_workspace_says_migrate_is_needed(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    data = json.loads(run(legacy, "status", "--json").stdout)
    assert data["schema_version"] == 0 and data["migrate_needed"] is True and data["unmigrated_jobs"] == 8


def test_doctor_healthy_workspace_exits_zero_and_warns_about_git(root: Path) -> None:
    assert run(root, "doctor").exit_code == 0
    (root / ".git").mkdir()
    result = run(root, "doctor")
    assert result.exit_code == 0 and "git repository" in result.output


def test_doctor_fails_on_a_legacy_workspace_and_reports_stale_framework_files(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    result = run(legacy, "doctor")
    assert result.exit_code == 1 and "careeros migrate" in result.output
    claude = make_claude_workspace(tmp_path / "claude")
    (claude / ".claude" / "skills" / "browse" / "SKILL.md").write_text("old")
    out = run(claude, "doctor").output
    assert "careeros upgrade" in out


def test_doctor_reports_a_pending_manifest(root: Path) -> None:
    backup = root / ".careeros" / "backups" / "20261006T090000Z-migrate"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text(json.dumps({"status": "pending", "files": []}))
    result = run(root, "doctor")
    assert result.exit_code == 1 and "pending" in result.output


# --- ledger ------------------------------------------------------------------------------

def test_ledger_append_list_and_verify(root: Path) -> None:
    result = run(root, "ledger", "append", "--type", "note.added", "--action", "called the recruiter",
                 "--actor", "user", "--entity", "job_aaaaaaaaaa", "--artifact", "notes.md")
    assert result.exit_code == 0 and "appended event 1" in result.output
    listed = json.loads(run(root, "ledger", "list", "--json").stdout)
    assert listed[0]["type"] == "note.added" and listed[0]["artifacts"] == ["notes.md"]
    assert json.loads(run(root, "ledger", "list", "--entity", "job_zzzzzzzzzz", "--json").stdout) == []
    assert run(root, "ledger", "verify").exit_code == 0


def test_ledger_append_refuses_reserved_types(root: Path) -> None:
    result = run(root, "ledger", "append", "--type", "application.approved", "--action", "sneaky", "--actor", "agent:claude")
    assert result.exit_code == 2 and "reserved" in result.output
    assert not (root / "ledger.jsonl").exists()


def test_ledger_verify_fails_after_tampering(root: Path) -> None:
    for i in range(3):
        run(root, "ledger", "append", "--type", "note.added", "--action", f"note {i}", "--actor", "user")
    path = root / "ledger.jsonl"
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace("note 0", "note X")
    path.write_text("\n".join(lines) + "\n")
    result = run(root, "ledger", "verify")
    assert result.exit_code == 1 and "LED002" in result.output


def test_ledger_append_refuses_a_workspace_from_the_future(tmp_path: Path) -> None:
    future = make_workspace(tmp_path / "ws", framework_version="9.0.0")
    result = run(future, "ledger", "append", "--type", "note.added", "--action", "x", "--actor", "user")
    assert result.exit_code == 1 and "newer than the installed" in result.output


# --- transition, approve, archive ---------------------------------------------------------

def test_transition_changes_state_and_is_idempotent(root: Path) -> None:
    job = add_job(root, "one")
    first = run(root, "transition", job["id"], "--to", "evaluated", "--actor", "user")
    assert first.exit_code == 0 and "DISCOVERED → EVALUATED" in first.output
    again = run(root, "transition", "one", "--to", "EVALUATED")
    assert again.exit_code == 0 and "nothing to do" in again.output
    assert [e["type"] for e in _events(root)].count("job.status_changed") == 1


def test_illegal_transition_exits_1_and_is_logged(root: Path) -> None:
    job = add_job(root, "one")
    result = run(root, "transition", job["id"], "--to", "OFFER", "--actor", "agent:claude")
    assert result.exit_code == 1 and "illegal_transition" in result.output
    assert _events(root)[-1]["type"] == "job.transition_rejected"


def test_unknown_state_is_a_usage_error(root: Path) -> None:
    job = add_job(root, "one")
    assert run(root, "transition", job["id"], "--to", "BANANA").exit_code == 2


def test_force_without_reason_fails(root: Path) -> None:
    job = add_job(root, "one", status=State.SCREEN, bullet="interview")
    result = run(root, "transition", job["id"], "--to", "EVALUATED", "--force", "--actor", "user")
    assert result.exit_code == 1 and "reason_required" in result.output
    ok = run(root, "transition", job["id"], "--to", "EVALUATED", "--force", "--reason", "mis-click", "--actor", "user")
    assert ok.exit_code == 0 and "corrected" in ok.output


def test_approve_needs_a_terminal(root: Path) -> None:
    job = add_job(root, "one", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    result = run(root, "approve", job["id"])
    assert result.exit_code == 2 and "interactive terminal" in result.output and "Ask the user" in result.output
    assert [e["type"] for e in _events(root)] == ["job.imported"]


def test_approve_records_the_users_decision(root: Path, interactive: None) -> None:
    job = add_job(root, "one", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    assert run(root, "transition", job["id"], "--to", "APPLIED", "--actor", "agent:claude").exit_code == 1
    approved = run(root, "approve", job["id"], input="y\n")
    assert approved.exit_code == 0
    event = _events(root)[-1]
    assert (event["type"], event["actor"], event["approval"]) == ("application.approved", "user", "approved")
    assert run(root, "transition", job["id"], "--to", "APPLIED", "--actor", "agent:claude").exit_code == 0


def test_declined_approval_is_recorded_and_exits_1(root: Path, interactive: None) -> None:
    job = add_job(root, "one", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    result = run(root, "approve", job["id"], input="n\n")
    assert result.exit_code == 1
    assert _events(root)[-1]["type"] == "application.approval_denied"


def test_archive_and_undo(root: Path) -> None:
    job = add_job(root, "one")
    assert run(root, "archive", job["id"], "--reason", "not a fit", "--actor", "user").exit_code == 0
    assert ws.read_job(job["path"]).archived
    assert run(root, "archive", job["id"], "--undo", "--actor", "user").exit_code == 0
    assert not ws.read_job(job["path"]).archived


# --- migrate -----------------------------------------------------------------------------

def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    before = snapshot(legacy)
    result = run(legacy, "migrate", "--dry-run")
    assert result.exit_code == 0 and "Dry run" in result.output and "8 job file(s)" in result.output
    assert snapshot(legacy) == before and not (legacy / ".careeros").exists()


def test_migrate_without_a_terminal_or_yes_exits_2_and_changes_nothing(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    before = snapshot(legacy)
    result = run(legacy, "migrate")
    assert result.exit_code == 2 and "--yes" in result.output
    assert snapshot(legacy) == before and not (legacy / ".careeros" / "workspace.yaml").exists()


def test_migrate_yes_applies_then_validates_and_a_second_run_is_a_no_op(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    result = run(legacy, "migrate", "--yes")
    assert result.exit_code == 0 and "Migrated 8 job(s)" in result.output and "backups" in result.output
    assert run(legacy, "validate").exit_code == 0
    again = run(legacy, "migrate", "--yes")
    assert again.exit_code == 0 and "already current" in again.output


def test_migrate_reports_unparseable_jobs_and_exits_1(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    broken = legacy / "jobs" / "discovered" / "broken" / "job.md"
    broken.parent.mkdir()
    broken.write_text(legacy_job_text("Engineer", "Weird", "https://x.test", "wibble"))
    before = snapshot(legacy)
    result = run(legacy, "migrate", "--yes")
    assert result.exit_code == 1 and "unknown Status" in result.output and "nothing was changed" in result.output
    assert snapshot(legacy) == before


def test_declined_migration_is_logged_when_a_ledger_exists(tmp_path: Path, interactive: None) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    assert run(legacy, "migrate", "--yes").exit_code == 0
    extra = legacy / "jobs" / "discovered" / "newco" / "job.md"
    extra.parent.mkdir()
    extra.write_text(legacy_job_text("Engineer", "NewCo", "https://x.test/newco", "discovered"))
    before = extra.read_bytes()
    result = run(legacy, "migrate", input="n\n")
    assert result.exit_code == 1 and "Cancelled" in result.output
    assert extra.read_bytes() == before
    assert _events(legacy)[-1]["type"] == "workspace.migrate_declined"


# --- upgrade -----------------------------------------------------------------------------

def test_upgrade_needs_confirmation_then_refreshes_and_reports_current(tmp_path: Path) -> None:
    claude = make_claude_workspace(tmp_path / "ws")
    skill = claude / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("customised")
    assert run(claude, "upgrade").exit_code == 2 and skill.read_text() == "customised"
    result = run(claude, "upgrade", "--yes")
    assert result.exit_code == 0 and "Upgraded" in result.output and skill.read_text() != "customised"
    assert ws.load_meta(claude).framework_version == versions.installed_version()
    assert "already current" in run(claude, "upgrade", "--yes").output


def test_declined_upgrade_is_logged(tmp_path: Path, interactive: None) -> None:
    claude = make_claude_workspace(tmp_path / "ws")
    (claude / ".claude" / "skills" / "browse" / "SKILL.md").write_text("customised")
    result = run(claude, "upgrade", input="n\n")
    assert result.exit_code == 1 and "Cancelled" in result.output
    assert _events(claude)[-1]["type"] == "workspace.upgrade_declined"
    assert (claude / ".claude" / "skills" / "browse" / "SKILL.md").read_text() == "customised"


def test_upgrade_on_a_legacy_workspace_points_to_migrate(tmp_path: Path) -> None:
    from careeros.workspace.scaffold import scaffold

    legacy = tmp_path / "ws"
    scaffold(legacy, runtime="claude")
    (legacy / ".claude" / "skills" / "browse" / "SKILL.md").write_text("old")
    result = run(legacy, "upgrade", "--yes")
    assert result.exit_code == 0 and "careeros migrate" in result.output


# --- init --------------------------------------------------------------------------------

def test_init_new_workspace_writes_metadata_and_a_created_event(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    assert runner.invoke(app, ["init", str(target)]).exit_code == 0
    meta = ws.load_meta(target)
    assert (meta.schema_version, meta.framework_version, meta.runtimes) == (1, versions.installed_version(), ("claude",))
    assert [e["type"] for e in ledger.read_events(target)] == ["workspace.created"]


def test_init_second_runtime_extends_metadata_without_a_second_created_event(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    assert runner.invoke(app, ["init", str(target), "--runtime", "gpt"]).exit_code == 0
    assert ws.load_meta(target).runtimes == ("claude", "gpt")
    assert [e["type"] for e in ledger.read_events(target)] == ["workspace.created"]


def test_init_into_a_legacy_directory_does_not_invent_metadata(tmp_path: Path) -> None:
    legacy = tmp_path / "ws"
    make_legacy_workspace(legacy)
    assert runner.invoke(app, ["init", str(legacy), "--runtime", "gpt"]).exit_code == 0
    assert ws.load_meta(legacy) is None and not (legacy / "ledger.jsonl").exists()


def test_init_refresh_updates_framework_version_and_logs_an_upgrade(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    meta = ws.load_meta(target)
    ws.save_meta(target, WorkspaceMeta(meta.schema_version, "0.1.0", meta.created_at, meta.updated_at, meta.runtimes))
    assert runner.invoke(app, ["init", str(target), "--refresh"]).exit_code == 0
    assert ws.load_meta(target).framework_version == versions.installed_version()
    assert ledger.read_events(target)[-1]["type"] == "workspace.upgraded"


def test_init_refresh_refuses_a_workspace_from_the_future(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", str(target)])
    meta = ws.load_meta(target)
    ws.save_meta(target, WorkspaceMeta(meta.schema_version, "9.0.0", meta.created_at, meta.updated_at, meta.runtimes))
    skill = target / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("corrupted")
    result = runner.invoke(app, ["init", str(target), "--refresh"])
    assert result.exit_code == 1 and "newer than the installed" in result.output
    assert skill.read_text() == "corrupted"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_cli_foundation.py`
Expected: FAIL (the new commands do not exist: `No such command 'status'` and friends).

- [ ] **Step 4: Implement the CLI**

Create `careeros/cli/_util.py`:

```python
"""Shared CLI helpers: workspace resolution, exit codes, JSON output, confirmation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn, Optional

import typer

from careeros.core.workspace import LEDGER_REL, WorkspaceError, ensure_writable, find_workspace

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

WorkspaceOption = typer.Option(
    None,
    "--workspace",
    "-w",
    envvar="CAREEROS_WORKSPACE",
    help="Workspace directory (default: search upward from the current directory)",
)


def fail(message: str, code: int = EXIT_FAIL) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


def resolve_root(workspace: Optional[Path]) -> Path:
    try:
        return find_workspace(workspace)
    except WorkspaceError as exc:
        fail(str(exc), EXIT_USAGE)


def echo_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def default_actor() -> str:
    return "user" if is_interactive() else "agent:cli"


def confirm_or_exit(prompt: str, *, assume_yes: bool) -> bool:
    """True when the user (or --yes) agrees. Exits 2 when there is no terminal and no --yes."""
    if assume_yes:
        return True
    if not is_interactive():
        typer.echo(
            "This changes your workspace and needs confirmation. Review the plan above, then run it in a "
            "terminal, or add --yes.",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    return typer.confirm(prompt, default=False)


def log_decline(root: Path, event_type: str, action: str) -> None:
    """Record a declined confirmation when the workspace already has a ledger. Never raises."""
    from careeros.core import ledger

    if not (root / LEDGER_REL).exists():
        return
    try:
        ensure_writable(root)
        ledger.append_event(root, type=event_type, actor="user", action=action, source="cli")
    except (WorkspaceError, OSError):
        pass
```

Create `careeros/cli/validate.py`:

```python
"""careeros validate."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core.validation import validate_workspace


def validate_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Check the workspace structure, versions, jobs, pipeline and ledger. Changes nothing."""
    root = _util.resolve_root(workspace)
    issues = validate_workspace(root)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    if as_json:
        _util.echo_json({"errors": len(errors), "warnings": len(warnings), "issues": [i.to_dict() for i in issues]})
    else:
        for issue in issues:
            typer.echo(f"{issue.severity.upper():<7} {issue.code}  {issue.path}")
            typer.echo(f"        {issue.message}")
            typer.echo(f"        fix: {issue.fix}")
        typer.echo(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    raise typer.Exit(_util.EXIT_FAIL if errors or (strict and warnings) else _util.EXIT_OK)
```

Create `careeros/cli/status.py`:

```python
"""careeros status."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ledger, versions
from careeros.core.workspace import WorkspaceError, job_files, load_meta, read_job


def status_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """One-screen summary of versions, jobs and recent activity. Changes nothing."""
    root = _util.resolve_root(workspace)
    try:
        meta = load_meta(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    installed = versions.installed_version()
    counts: Counter[str] = Counter()
    archived = 0
    unmigrated = 0
    for path in job_files(root):
        try:
            job = read_job(path)
        except WorkspaceError:
            unmigrated += 1
            continue
        if job.archived:
            archived += 1
        else:
            counts[job.status.value] += 1
    ledger_note = None
    try:
        events = ledger.read_events(root)
    except WorkspaceError as exc:
        events, ledger_note = [], str(exc)
    relation = versions.compare_versions(installed, meta.framework_version) if meta else 0
    newer = bool(meta and (relation < 0 or meta.schema_version > versions.SCHEMA_VERSION))
    data = {
        "workspace": str(root),
        "installed_version": installed,
        "framework_version": meta.framework_version if meta else None,
        "schema_version": meta.schema_version if meta else 0,
        "jobs": dict(counts),
        "archived": archived,
        "unmigrated_jobs": unmigrated,
        "recent_events": [
            {"seq": e["seq"], "ts": e["ts"], "type": e["type"], "entity": e["entity"], "action": e["action"]}
            for e in events[-5:]
        ],
        "ledger_note": ledger_note,
        "migrate_needed": meta is None or unmigrated > 0 or meta.schema_version < versions.SCHEMA_VERSION,
        "upgrade_available": bool(meta and relation > 0),
        "workspace_newer": newer,
    }
    if as_json:
        _util.echo_json(data)
        return
    typer.echo(f"Workspace   {root}")
    typer.echo(f"CareerOS    {installed}   workspace framework {data['framework_version'] or '—'}   schema {data['schema_version']}")
    typer.echo("Jobs        " + (", ".join(f"{state} {n}" for state, n in counts.items()) or "none") + f"   archived {archived}")
    if unmigrated:
        typer.echo(f"            {unmigrated} job file(s) have no frontmatter yet")
    typer.echo("Recent      " + ("(no events)" if not events else ""))
    for event in data["recent_events"]:
        typer.echo(f"  {event['seq']:>4}  {event['ts']}  {event['type']}  {event['action']}")
    if ledger_note:
        typer.echo(f"Ledger      {ledger_note}")
    if data["workspace_newer"]:
        typer.echo("Next        this workspace was last changed by a newer CareerOS: upgrade CareerOS")
    if data["migrate_needed"]:
        typer.echo("Next        run `careeros migrate` to add versions and ids")
    if data["upgrade_available"]:
        typer.echo("Next        run `careeros upgrade` to refresh skill files")
```

Create `careeros/cli/doctor.py`:

```python
"""careeros doctor."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import migration, versions
from careeros.core.validation import validate_workspace
from careeros.core.workspace import WorkspaceError, load_meta


def _inside_git_repo(root: Path) -> bool:
    return any((p / ".git").exists() for p in (root, *root.parents))


def doctor_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Check the environment and the workspace. Changes nothing."""
    root = _util.resolve_root(workspace)
    checks: list[dict[str, str]] = []

    def add(level: str, name: str, message: str) -> None:
        checks.append({"level": level, "name": name, "message": message})

    installed = versions.installed_version()
    py = sys.version_info
    add("ok" if py >= (3, 11) else "error", "python", f"Python {py.major}.{py.minor}.{py.micro}" + ("" if py >= (3, 11) else " (CareerOS needs 3.11+)"))
    add("ok", "careeros", f"CareerOS {installed}")
    add("ok", "workspace", str(root))

    try:
        meta = load_meta(root)
    except WorkspaceError as exc:
        add("error", "metadata", str(exc))
    else:
        if meta is None:
            add("warn", "metadata", "legacy workspace (schema 0, no .careeros/workspace.yaml): run `careeros migrate`")
        else:
            add("ok", "metadata", f"schema {meta.schema_version}, framework {meta.framework_version}")
            if meta.schema_version > versions.SCHEMA_VERSION:
                add("error", "schema", f"schema {meta.schema_version} is newer than this CareerOS supports ({versions.SCHEMA_VERSION}); upgrade CareerOS")
            relation = versions.compare_versions(installed, meta.framework_version)
            if relation > 0:
                add("warn", "framework", f"upgrade available: the workspace is at {meta.framework_version}, installed is {installed}; run `careeros upgrade`")
            elif relation < 0:
                add("error", "framework", f"the workspace was last updated by a newer CareerOS ({meta.framework_version}); upgrade CareerOS before changing it")
            else:
                add("ok", "framework", "workspace framework version matches the installed CareerOS")

    try:
        plan = migration.plan_upgrade(root)
    except WorkspaceError as exc:
        add("error", "framework files", str(exc))
    else:
        pending = plan.pending
        if pending:
            add("warn", "framework files", f"{len(pending)} skill/entry file(s) differ from the installed templates; run `careeros upgrade`")
        else:
            add("ok", "framework files", "skill and entry files match the installed templates")

    issues = validate_workspace(root)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    if errors:
        add("error", "validation", f"{len(errors)} error(s), {len(warnings)} warning(s); run `careeros validate` for details")
    elif warnings:
        add("warn", "validation", f"{len(warnings)} warning(s); run `careeros validate` for details")
    else:
        add("ok", "validation", "no issues")

    for path in migration.find_incomplete_operations(root):
        add("error", "incomplete operation", f"{path} has a manifest still marked pending; restore from that backup, then run the command again")

    if _inside_git_repo(root):
        add("warn", "privacy", "this workspace is inside a git repository; it holds your resume, contacts and compensation, so keep any remote private")

    if as_json:
        _util.echo_json(checks)
    else:
        for check in checks:
            typer.echo(f"{check['level'].upper():<6} {check['name']:<20} {check['message']}")
    raise typer.Exit(_util.EXIT_FAIL if any(c["level"] == "error" for c in checks) else _util.EXIT_OK)
```

Create `careeros/cli/ledger.py`:

```python
"""careeros ledger: read and append the audit ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ledger as core_ledger
from careeros.core.workspace import WorkspaceError, ensure_writable

ledger_app = typer.Typer(help="Read and extend the append-only audit ledger (ledger.jsonl).", no_args_is_help=True)


@ledger_app.command("append")
def append_cmd(
    event_type: str = typer.Option(..., "--type", help="Dotted event type, for example note.added"),
    action: str = typer.Option(..., "--action", help="One sentence describing what happened"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name> (default: user at a terminal, else agent:cli)"),
    entity: Optional[str] = typer.Option(None, "--entity", help="ID of the affected entity"),
    prev_state: Optional[str] = typer.Option(None, "--prev-state"),
    new_state: Optional[str] = typer.Option(None, "--new-state"),
    approval: str = typer.Option("not_required", "--approval", help="not_required, required, approved or denied"),
    artifact: Optional[list[str]] = typer.Option(None, "--artifact", help="Workspace-relative path; repeatable"),
    source: Optional[str] = typer.Option(None, "--source"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Append one event. Reserved types are written only by their own commands."""
    root = _util.resolve_root(workspace)
    if core_ledger.is_reserved(event_type):
        _util.fail(
            f"{event_type} is reserved: only its own command writes it (transition, approve, archive, migrate, upgrade)",
            _util.EXIT_USAGE,
        )
    try:
        ensure_writable(root)
        event = core_ledger.append_event(
            root, type=event_type, actor=actor or _util.default_actor(), action=action, entity=entity,
            prev_state=prev_state, new_state=new_state, approval=approval, artifacts=artifact or (),
            source=source, reason=reason,
        )
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo(f"appended event {event['seq']} ({event['id']})")


@ledger_app.command("list")
def list_cmd(
    entity: Optional[str] = typer.Option(None, "--entity", help="Only events for this entity ID"),
    since: Optional[str] = typer.Option(None, "--since", help="Only events at or after this UTC timestamp"),
    type_prefix: Optional[str] = typer.Option(None, "--type", help="Only event types starting with this text"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List ledger events."""
    root = _util.resolve_root(workspace)
    try:
        events = core_ledger.read_events(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    selected = [
        e for e in events
        if (not entity or e["entity"] == entity)
        and (not since or e["ts"] >= since)
        and (not type_prefix or e["type"].startswith(type_prefix))
    ]
    if as_json:
        _util.echo_json(selected)
        return
    for e in selected:
        typer.echo(f"{e['seq']:>5}  {e['ts']}  {e['type']:<28} {e['entity'] or '-':<16} {e['action']}")


@ledger_app.command("verify")
def verify_cmd(workspace: Optional[Path] = _util.WorkspaceOption) -> None:
    """Check the ledger's structure, sequence numbers and hash chain."""
    root = _util.resolve_root(workspace)
    issues = core_ledger.verify_chain(root)
    if issues:
        for issue in issues:
            typer.echo(f"{issue.code}  {issue.message}")
            typer.echo(f"        fix: {issue.fix}")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"ledger OK ({len(core_ledger.read_events(root))} events)")
```

Create `careeros/cli/transition.py`:

```python
"""careeros transition."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import state_machine as sm
from careeros.core.models import State
from careeros.core.workspace import WorkspaceError


def transition_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    to: str = typer.Option(..., "--to", help="Target state, for example EVALUATED"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    force: bool = typer.Option(False, "--force", help="Record a correction the rules would forbid (needs --reason)"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Why a forced correction is needed"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Move a job to a new state, atomically, and record it in the ledger."""
    root = _util.resolve_root(workspace)
    try:
        target = State(to.strip().upper())
    except ValueError:
        _util.fail(f"unknown state {to!r}; choose one of: {', '.join(s.value for s in State)}", _util.EXIT_USAGE)
    try:
        result = sm.apply_transition(
            root, job, target, actor=actor or _util.default_actor(), reason=reason, force=force
        )
    except sm.TransitionError as exc:
        _util.fail(f"{exc} [{exc.code}]")
    except WorkspaceError as exc:
        _util.fail(str(exc))
    messages = {
        "changed": f"{result.from_state.value} → {result.to_state.value}",
        "corrected": f"corrected {result.from_state.value} → {result.to_state.value} (recorded as a correction)",
        "unchanged": f"already {result.to_state.value}; nothing to do",
        "repaired": f"already {result.to_state.value}; the ledger was behind and has been repaired",
    }
    typer.echo(messages[result.outcome])
```

Create `careeros/cli/approve.py`:

```python
"""careeros approve: record the user's own decision to submit an application."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import state_machine as sm
from careeros.core.workspace import WorkspaceError


def approve_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Approve (or decline) submitting an application. Needs an interactive terminal.

    This is a human-confirmation guard against accidental execution, not a security boundary.
    """
    root = _util.resolve_root(workspace)
    if not _util.is_interactive():
        typer.echo(
            "careeros approve records YOUR decision and needs an interactive terminal. "
            "Ask the user to run it themselves (in Claude Code: `! careeros approve <job>`).",
            err=True,
        )
        raise typer.Exit(_util.EXIT_USAGE)
    try:
        result = sm.approve_job(root, job, confirm=lambda prompt: typer.confirm(prompt, default=False))
    except sm.TransitionError as exc:
        _util.fail(f"{exc} [{exc.code}]")
    except WorkspaceError as exc:
        _util.fail(str(exc))
    outcome = result["outcome"]
    if outcome == "approved":
        typer.echo("Approved. The application may now move to APPLIED.")
    elif outcome == "unchanged" and result.get("decision") == "approved":
        typer.echo(f"Already approved at {result['event']['ts']}.")
    elif outcome == "unchanged":
        typer.echo("Already declined; nothing was recorded again.")
        raise typer.Exit(_util.EXIT_FAIL)
    else:
        typer.echo("Declined. The application stays at APPROVAL_REQUIRED.")
        raise typer.Exit(_util.EXIT_FAIL)
```

Create `careeros/cli/archive.py`:

```python
"""careeros archive."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import state_machine as sm
from careeros.core.workspace import WorkspaceError


def archive_cmd(
    job: str = typer.Argument(..., help="Job ID, directory name or path"),
    undo: bool = typer.Option(False, "--undo", help="Unarchive the job"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    actor: Optional[str] = typer.Option(None, "--actor", help="user, system or agent:<name>"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Archive a job instead of deleting it, so the ledger's history stays verifiable."""
    root = _util.resolve_root(workspace)
    try:
        result = sm.archive_job(root, job, actor=actor or _util.default_actor(), undo=undo, reason=reason)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo({"archived": "archived", "unarchived": "unarchived", "unchanged": "nothing to do"}[result["outcome"]])
```

Create `careeros/cli/migrate.py`:

```python
"""careeros migrate."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import migration
from careeros.core.workspace import WorkspaceError


def _print_plan(plan: migration.MigrationPlan) -> None:
    typer.echo(f"Migration plan: schema {plan.source_schema} → {plan.target_schema}")
    if plan.meta_to_write is not None:
        typer.echo("  - create .careeros/workspace.yaml")
    if plan.job_changes:
        typer.echo(f"  - add frontmatter and a stable id to {len(plan.job_changes)} job file(s):")
        for change in plan.job_changes:
            typer.echo(f"      {change.rel}  →  {change.job_id}  {change.status.value}")
    for note in plan.notes:
        typer.echo(f"  note: {note}")
    for error in plan.errors:
        typer.echo(f"  ERROR: {error}")


def migrate_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and change nothing"),
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (after you have reviewed the plan)"),
) -> None:
    """Add versions, ids and frontmatter to a workspace. Makes a verified backup first."""
    root = _util.resolve_root(workspace)
    try:
        plan = migration.plan_migration(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    _print_plan(plan)
    if plan.errors:
        _util.fail("nothing was changed; fix the files above and run again")
    if plan.empty:
        typer.echo("Nothing to migrate: the workspace is already current.")
        raise typer.Exit(_util.EXIT_OK)
    if dry_run:
        typer.echo("Dry run: nothing was changed.")
        raise typer.Exit(_util.EXIT_OK)
    if not _util.confirm_or_exit("Apply this migration? A verified backup is made first.", assume_yes=yes):
        _util.log_decline(root, "workspace.migrate_declined", "user declined the migration")
        typer.echo("Cancelled. Nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    try:
        result = migration.run_migration(root, plan)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo(f"Migrated {result.jobs} job(s). Backup and manifest: {result.backup.relative_to(root)}")
```

Create `careeros/cli/upgrade.py`:

```python
"""careeros upgrade."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import migration
from careeros.core.workspace import WorkspaceError


def upgrade_cmd(
    workspace: Optional[Path] = _util.WorkspaceOption,
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (after you have reviewed the plan)"),
) -> None:
    """Refresh skill files and the entry file from the installed CareerOS. Makes a verified backup first."""
    root = _util.resolve_root(workspace)
    try:
        plan = migration.plan_upgrade(root)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    if plan.noop:
        typer.echo("Framework files are already current.")
        _schema_hint(plan)
        raise typer.Exit(_util.EXIT_OK)
    typer.echo("Upgrade plan:")
    for item in plan.pending:
        typer.echo(f"  {item.status:<8} {item.rel}")
    if plan.meta is not None and plan.framework_stale:
        typer.echo(f"  workspace framework version {plan.meta.framework_version} → installed")
    if not _util.confirm_or_exit("Apply this upgrade? Changed files are backed up first.", assume_yes=yes):
        _util.log_decline(root, "workspace.upgrade_declined", "user declined the upgrade")
        typer.echo("Cancelled. Nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    try:
        result = migration.run_upgrade(root, plan)
    except WorkspaceError as exc:
        _util.fail(str(exc))
    typer.echo(f"Upgraded {result.jobs} file(s). Backup and manifest: {result.backup.relative_to(root)}")
    _schema_hint(plan)


def _schema_hint(plan: migration.UpgradePlan) -> None:
    if plan.schema_behind:
        typer.echo("Your workspace data is not on the current schema yet. Run `careeros migrate`.")
```

Replace the contents of `careeros/cli/main.py` with:

```python
import typer

from careeros.cli.approve import approve_cmd
from careeros.cli.archive import archive_cmd
from careeros.cli.doctor import doctor_cmd
from careeros.cli.init_cmd import init_cmd
from careeros.cli.ledger import ledger_app
from careeros.cli.migrate import migrate_cmd
from careeros.cli.status import status_cmd
from careeros.cli.transition import transition_cmd
from careeros.cli.upgrade import upgrade_cmd
from careeros.cli.validate import validate_cmd

app = typer.Typer(name="careeros", help="CareerOS — scaffold and maintain your job-search workspace.")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


app.command("init")(init_cmd)
app.command("doctor")(doctor_cmd)
app.command("status")(status_cmd)
app.command("validate")(validate_cmd)
app.command("transition")(transition_cmd)
app.command("approve")(approve_cmd)
app.command("archive")(archive_cmd)
app.command("migrate")(migrate_cmd)
app.command("upgrade")(upgrade_cmd)
app.add_typer(ledger_app, name="ledger")

if __name__ == "__main__":
    app()
```

Edit `careeros/cli/init_cmd.py` with this script (it asserts each anchor exists once):

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path

p = Path("careeros/cli/init_cmd.py")
s = p.read_text()

def sub(old, new):
    global s
    assert s.count(old) == 1, (old, s.count(old))
    s = s.replace(old, new)

sub("from careeros.workspace.scaffold import scaffold\n",
    '''from careeros.core import ledger, models, versions
from careeros.core.models import WorkspaceMeta
from careeros.core.workspace import LEDGER_REL, WorkspaceError, ensure_writable, load_meta, save_meta
from careeros.workspace.scaffold import scaffold


def _record_init(target: Path, runtime: str, *, refresh: bool, was_empty: bool) -> None:
    """Keep .careeros/workspace.yaml and the ledger in step with what init just did."""
    meta = load_meta(target)
    now = models.utc_now()
    installed = versions.installed_version()
    if meta is None:
        if refresh or not was_empty:
            return  # a legacy workspace: `careeros migrate` adopts it, init must not pretend it is current
        save_meta(target, WorkspaceMeta(versions.SCHEMA_VERSION, installed, now, now, (runtime,)))
        ledger.append_event(
            target, type="workspace.created", actor="system", source="cli",
            action=f"workspace created for runtime {runtime}",
        )
        return
    runtimes = tuple(dict.fromkeys((*meta.runtimes, runtime)))
    if refresh:
        save_meta(target, WorkspaceMeta(meta.schema_version, installed, meta.created_at, now, runtimes))
        if (target / LEDGER_REL).exists():
            ledger.append_event(
                target, type="workspace.upgraded", actor="system", source="init --refresh",
                action=f"refreshed framework files to CareerOS {installed}",
            )
    elif runtime not in meta.runtimes:
        save_meta(target, WorkspaceMeta(meta.schema_version, meta.framework_version, meta.created_at, now, runtimes))
''')

sub("    target = Path(path).expanduser().resolve()\n\n    try:\n        written = scaffold(",
    '''    target = Path(path).expanduser().resolve()
    was_empty = not target.exists() or not any(target.iterdir())

    if refresh:
        try:
            ensure_writable(target)  # refuse before touching any file
        except WorkspaceError as exc:
            rprint(f"[red]{exc}[/red]")
            raise typer.Exit(1)

    try:
        written = scaffold(''')

sub("    if refresh:\n        rprint(",
    '''    try:
        _record_init(target, runtime, refresh=refresh, was_empty=was_empty)
    except (WorkspaceError, OSError) as exc:
        rprint(f"[yellow]Workspace files were written, but the metadata could not be updated: {exc}[/yellow]")

    if refresh:
        rprint(''')
p.write_text(s)
print("init_cmd.py updated")
EOF
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, including the 31 original ones. If a Typer or Click version difference breaks `result.stdout` JSON parsing, print `result.output` and `result.stdout` once and adjust only the tests' use of those attributes.

- [ ] **Step 6: Commit**

```bash
git add careeros/cli tests/helpers.py tests/test_cli_foundation.py
git commit -m "cli: doctor, status, validate, ledger, transition, approve, archive, migrate, upgrade" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

### Task 8: End-to-end test, CI, changelog, docs, and the acceptance run on a copy

**Files:**
- Create: `tests/test_foundation_e2e.py`, `.github/workflows/tests.yml`, `CHANGELOG.md`, `docs/foundation.md`, `docs/END_TO_END_TEST.md`
- Modify: `mkdocs.yml`, `README.md`, `ROADMAP.md`, `docs/index.md`, `docs/IMPLEMENTATION_AUDIT.md`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: the end-to-end test, CI, and the documentation that describes only what exists.

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_foundation_e2e.py`:

```python
"""One job from a legacy workspace to an offer, through the CLI, with an audit at every step."""

import json
from pathlib import Path

import pytest
from helpers import make_legacy_workspace
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger
from careeros.core import workspace as ws
from careeros.core.models import State

runner = CliRunner()


def run(root: Path, *args: str, input: str | None = None):
    return runner.invoke(app, [*args, "--workspace", str(root)], input=input)


def test_legacy_workspace_to_offer_with_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ws"
    make_legacy_workspace(root)

    # 1. migrate, then the workspace validates with nothing to report
    assert run(root, "migrate", "--yes").exit_code == 0
    assert run(root, "validate", "--strict").exit_code == 0

    job_path = root / "jobs" / "discovered" / "acme-backend-engineer" / "job.md"
    job_id = ws.read_job(job_path).id

    # 2. walk the pre-application stages
    for state in ("EVALUATED", "SHORTLISTED", "RESEARCHED", "PREPARING", "READY_TO_APPLY", "APPROVAL_REQUIRED"):
        result = run(root, "transition", job_id, "--to", state, "--actor", "agent:claude")
        assert result.exit_code == 0, result.output

    # 3. an agent cannot jump to APPLIED, and the refusal is on the record
    refused = run(root, "transition", job_id, "--to", "APPLIED", "--actor", "agent:claude")
    assert refused.exit_code == 1 and "careeros approve" in refused.output
    assert ledger.read_events(root)[-1]["type"] == "job.transition_rejected"

    # 4. without a terminal the approval command refuses; with one, the user's decision is recorded
    assert run(root, "approve", job_id).exit_code == 2
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    assert run(root, "approve", job_id, input="y\n").exit_code == 0
    monkeypatch.undo()
    assert run(root, "transition", job_id, "--to", "APPLIED", "--actor", "agent:claude").exit_code == 0

    # 5. the interview stages and the offer; a retry changes nothing
    for state in ("RECRUITER_REPLIED", "SCREEN", "TECHNICAL", "HM", "FINAL", "OFFER"):
        assert run(root, "transition", job_id, "--to", state, "--actor", "agent:claude").exit_code == 0
    events_before_retry = len(ledger.read_events(root))
    assert "nothing to do" in run(root, "transition", job_id, "--to", "OFFER").output
    assert len(ledger.read_events(root)) == events_before_retry

    # 6. file, pipeline and ledger all agree, and the chain verifies
    assert ws.read_job(job_path).status is State.OFFER
    assert "- **Status:** offer" in job_path.read_text()
    assert "- [✓] **Acme**" in (root / "jobs" / "pipeline.md").read_text()
    assert run(root, "validate", "--strict").exit_code == 0
    assert run(root, "ledger", "verify").exit_code == 0
    status = json.loads(run(root, "status", "--json").stdout)
    assert status["jobs"]["OFFER"] == 2  # the walked job and the legacy job that was already at OFFER

    # 7. editing history is detected
    path = root / "ledger.jsonl"
    lines = path.read_text().splitlines()
    lines[3] = lines[3].replace("job.imported", "job.imported ")
    path.write_text("\n".join(lines) + "\n")
    assert run(root, "ledger", "verify").exit_code == 1
```

Run: `.venv/bin/python -m pytest -q tests/test_foundation_e2e.py`
Expected: PASS. If the final tamper step does not fail verification, the edited line was not part of the JSON structure; edit a character inside a quoted value of line 4 instead.

- [ ] **Step 2: Add CI**

Create `.github/workflows/tests.yml`:

```yaml
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pytest -q
```

- [ ] **Step 3: Write the changelog**

Create `CHANGELOG.md`:

```markdown
# Changelog

## 0.3.0 — Foundation

### Added
- `careeros doctor`, `status`, `validate`, `ledger` (`append`, `list`, `verify`), `transition`, `approve`, `archive`, `migrate` and `upgrade`.
- A tested core library (`careeros/core/`): stable entity IDs, workspace metadata (`.careeros/workspace.yaml`) with schema and framework versions, an application state machine, an append-only hash-chained ledger (`ledger.jsonl`), a deterministic validator and verified backups with manifests.
- Atomic, serialised writes: one workspace lock, temp file + `fsync` + atomic rename, and rollback when a ledger append fails.
- `migrate` adds frontmatter and IDs to existing job files without changing their bodies, backs up first, and can be re-run safely.
- A test workflow in CI (it previously only built the docs).

### Changed
- `careeros.__version__` is read from package metadata. It said `0.1.0` while `pyproject.toml` said `0.2.0`.
- `careeros init` writes workspace metadata and a `workspace.created` ledger event for new workspaces; `init --refresh` records the framework version it applied.
- New dependency: PyYAML (`safe_load` only).

### Notes
- Skills are unchanged and still edit `Status:` lines and append to `activity.md`. `careeros validate` reports the resulting drift as warnings and says how to fix each; `careeros migrate` adopts jobs saved since the last migration.
- `activity.md` stays the human-readable log. `ledger.jsonl` is the machine and audit log.
- `careeros approve` is a human-confirmation guard, not a security boundary.

## 0.2.0

- Replaced the v0.1 Playwright CLI with the Cowork workspace scaffold (`careeros init`) and markdown skills for Claude Code and GPT Work.

## 0.1.0

- Initial CLI with browser automation, apply, outreach and research commands.
```

- [ ] **Step 4: Write the documentation page**

Create `docs/foundation.md`:

````markdown
# Workspace health, versions and audit

CareerOS keeps your career data in plain markdown you own. Version 0.3.0 adds a small layer underneath that makes the workspace **versioned, identifiable and auditable** without moving your data anywhere.

## What it adds to a workspace

| File | What it is |
|---|---|
| `.careeros/workspace.yaml` | schema version, the framework version last applied, runtimes |
| `ledger.jsonl` | an append-only record of every change made through the commands below, each line chained to the one before it |
| `jobs/discovered/[job]/job.md` frontmatter | a stable `id`, a `status`, and timestamps at the top of each job file; the rest of the file is untouched |
| `.careeros/backups/` | a copy of every file a migration or upgrade changes, with a `manifest.json` of SHA-256 hashes |

`activity.md` stays your human-readable log. The ledger is the machine and audit log.

## Commands

| Command | What it does |
|---|---|
| `careeros doctor` | environment and workspace health, stale skill files, incomplete operations, a warning if the workspace is inside a git repository |
| `careeros status` | versions, jobs by state, the last five ledger events, whether migrate or upgrade is needed |
| `careeros validate [--strict] [--json]` | checks structure, versions, job files, the pipeline and the ledger, and says how to fix each finding |
| `careeros transition <job> --to STATE` | moves a job along the state machine, updating the job file, the pipeline icon and the ledger together |
| `careeros approve <job>` | records your decision to submit an application; needs an interactive terminal |
| `careeros archive <job> [--undo]` | archives a job instead of deleting it |
| `careeros ledger append / list / verify` | add a note event, read events, check the hash chain |
| `careeros migrate [--dry-run] [--yes]` | adds versions and IDs to an existing workspace, with a verified backup |
| `careeros upgrade [--yes]` | refreshes skill files and the entry file, with a verified backup |

All commands accept `--workspace PATH` or the `CAREEROS_WORKSPACE` environment variable; otherwise they look upward from the current directory.

## The job lifecycle

```text
DISCOVERED → EVALUATED → SHORTLISTED → RESEARCHED → PREPARING → READY_TO_APPLY
→ APPROVAL_REQUIRED → APPLIED → RECRUITER_REPLIED → SCREEN → TECHNICAL → HM → FINAL → OFFER
→ ACCEPTED | REJECTED | WITHDRAWN
```

Early stages may skip forward but never move back. `APPLIED` is reachable only from `APPROVAL_REQUIRED`, and only after you have run `careeros approve`. Corrections use `transition --force --reason "..."` and are recorded as `job.status_corrected`, never as a normal move.

## Safety guarantees

- Changes to a job file, the pipeline and the ledger happen together or not at all. If the ledger cannot be written, the files are put back byte for byte.
- Files are written through a temporary file and an atomic rename, under a workspace lock, so two commands cannot interleave.
- Migration and upgrade back up every file they change, verify each copy, write a manifest, and roll back if anything fails.
- Retrying a transition, an approval or an archive does not create a second event.
- A workspace last updated by a newer CareerOS is read-only until you upgrade CareerOS.

## What it does not guarantee

- **Approval is not a security boundary.** `careeros approve` asks a person to confirm at a terminal. It stops accidental or automatic approval. An agent with unrestricted shell access could still allocate a terminal and answer the prompt; the ledger would show it afterwards, but nothing prevents it.
- **The ledger is tamper-evident, not tamper-proof.** Editing or deleting a past line is detected by `ledger verify`. Rewriting the whole file consistently is not.
- **Skills have not been updated yet.** They still edit the `Status:` line and append to `activity.md`, so `careeros validate` will report drift warnings after they run. Each warning says how to resolve it.

## Migrating an existing workspace

```bash
careeros migrate --dry-run      # see what would change; nothing is written
careeros migrate                # review the plan, confirm; a verified backup is made first
careeros validate               # should report no errors
```

Migration only adds: frontmatter is placed above each job file, and nothing else in your workspace is modified. Jobs that the old format cannot describe (no `URL`, no `Discovered` date, an unknown `Status`) are listed by file and **nothing is written** until you fix them.

Jobs saved by skills after a migration have no ID yet. Run `careeros migrate` again to adopt them.

## Troubleshooting

- **"another careeros command is running in this workspace"** means the workspace lock is held. Wait a moment and retry.
- **`WS005` or "newer than the installed"** means a newer CareerOS last updated this workspace. Upgrade CareerOS.
- **`LED003`** means a job directory that the ledger mentions was deleted. Restore it from `.careeros/backups/` or version control; archive jobs instead of deleting them.
- **A manifest marked `pending`** (shown by `careeros doctor`) means an operation was interrupted. Restore the listed files from that backup folder, then run the command again.
````

Apply the remaining documentation edits with this checked script (each anchor must exist exactly once; if one has drifted, open the file, adjust the anchor, and do not skip the edit):

````bash
.venv/bin/python - <<'EOF'
from pathlib import Path


def sub(path, old, new):
    p = Path(path)
    s = p.read_text()
    assert s.count(old) == 1, (path, old, s.count(old))
    p.write_text(s.replace(old, new))


sub("mkdocs.yml", "  - Getting Started: getting-started.md\n",
    "  - Getting Started: getting-started.md\n  - Workspace health and audit: foundation.md\n")

sub("docs/index.md",
    "| `careeros init <path> --refresh` | Update skill files and the entry file (old copy saved as `.bak`) without touching user data |\n",
    "| `careeros init <path> --refresh` | Update skill files and the entry file (old copy saved as `.bak`) without touching user data |\n"
    "| `careeros doctor` / `status` / `validate` | check environment and workspace health, summarise the workspace, validate it |\n"
    "| `careeros transition` / `approve` / `archive` | move a job along its lifecycle, record your approval, archive instead of deleting |\n"
    "| `careeros migrate` / `upgrade` | add versions and IDs to an existing workspace; refresh skill files, both with verified backups |\n"
    "| `careeros ledger append / list / verify` | read and extend the audit ledger |\n")

sub("README.md", "## Requirements\n", """## Workspace health, versions and audit

```bash
careeros doctor      # environment and workspace health
careeros status      # versions, jobs by state, recent activity
careeros validate    # structure, ids, pipeline and ledger checks, each with a fix
careeros migrate     # add versions and ids to an existing workspace (backs up first)
careeros upgrade     # refresh skill files (backs up first)
```

Every change made through these commands is recorded in an append-only, hash-chained `ledger.jsonl`; your markdown stays the source of truth. See `docs/foundation.md` for the job lifecycle, the guarantees and their limits.

## Requirements
""")

sub("ROADMAP.md", "## Next\n", """## Foundation (v0.3.0, done)

A tested core underneath the workspace: stable IDs, workspace and framework versions, an application state machine, an append-only hash-chained ledger, deterministic validation, and `migrate` / `upgrade` with verified backups. Design: `docs/superpowers/specs/2026-10-05-foundation-design.md`. What it deliberately does not do yet: skills still edit `Status:` lines and `activity.md` directly (so `validate` reports drift warnings), and approval is a confirmation prompt, not a security boundary.

The wider plan is in `docs/IMPLEMENTATION_AUDIT.md` section 10: evidence and career memory next, then evaluation and the full approval system, workflow completion, learning, runtime adapters, and the dashboard.

## Next
""")

sub("ROADMAP.md",
    "**3. `careeros upgrade`.** Detect stale skill files across multiple workspaces and offer a\nbatch refresh. Depends on versioning.",
    "**3. Batch upgrade.** `careeros upgrade` now handles one workspace (diff preview, verified backup,\nmanifest). Detecting stale workspaces across several directories and refreshing them in one go is\nstill open.")

with open("docs/IMPLEMENTATION_AUDIT.md", "a") as handle:
    handle.write("""
## Update after Foundation (0.3.0)

Resolved from section 5: CI now runs the tests (item 2); the activity log's ordering ambiguity is addressed by a separate machine ledger while `activity.md` stays free text (item 3, partly); workspace, framework and schema versions exist (item 4); a `CHANGELOG.md` exists (item 7); `careeros.__version__` matches `pyproject.toml`. Still open: skills do not use the new commands, there is no evidence model, no prompt-injection handling, GPT parity and runtime adapters, and the stale `tests/integration/__init__.py` docstring.
""")
print("docs updated")
EOF
````

- [ ] **Step 5: Run everything**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m mkdocs build --strict >/dev/null 2>&1; echo "mkdocs strict exit: $?"
git status --short
```
Expected: all tests pass; `mkdocs strict exit: 0`; only the files listed in this task are changed.

- [ ] **Step 6: Acceptance run on a copy of the real workspace**

The real `~/Projects/job-search` must stay untouched. Work only on a copy and record exactly what happens.

```bash
REAL=~/Projects/job-search
ACC=$(mktemp -d)/job-search-copy
.venv/bin/python - <<EOF
import hashlib, pathlib, json
root = pathlib.Path("$REAL")
snap = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}
pathlib.Path("/tmp/real-before.json").write_text(json.dumps(snap))
print(len(snap), "files in the real workspace")
EOF
cp -R "$REAL" "$ACC" && echo "copy at $ACC"
.venv/bin/careeros validate -w "$ACC"; echo "exit $?"
.venv/bin/careeros migrate -w "$ACC" --dry-run; echo "exit $?"
.venv/bin/careeros migrate -w "$ACC" --yes; echo "exit $?"
.venv/bin/careeros validate -w "$ACC"; echo "exit $?"
.venv/bin/careeros status -w "$ACC"
.venv/bin/careeros doctor -w "$ACC"; echo "exit $?"
.venv/bin/careeros ledger verify -w "$ACC"; echo "exit $?"
```

Then check the bodies byte for byte and that the real workspace did not change:

```bash
.venv/bin/python - <<EOF
import hashlib, json, pathlib
from careeros.core.workspace import split_frontmatter
real, copy = pathlib.Path("$REAL"), pathlib.Path("$ACC")
bad = []
jobs = sorted((real / "jobs" / "discovered").glob("*/job.md"))
for original in jobs:
    migrated = copy / original.relative_to(real)
    _, body = split_frontmatter(migrated.read_text(encoding="utf-8"))
    if body.encode("utf-8") != original.read_bytes():
        bad.append(str(original))
print(len(jobs), "job files checked;", "bodies differ:", bad or "none")
before = json.loads(pathlib.Path("/tmp/real-before.json").read_text())
after = {str(p.relative_to(real)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(real.rglob("*")) if p.is_file()}
print("real workspace unchanged:", before == after)
EOF
```

Then exercise the state machine and the audit on the copy (pick any job slug from `ls "$ACC/jobs/discovered"`):

```bash
JOB=$(ls "$ACC/jobs/discovered" | head -1)
.venv/bin/careeros transition "$JOB" --to OFFER -w "$ACC"; echo "exit $?"            # expect: illegal_transition
.venv/bin/careeros transition "$JOB" --to EVALUATED -w "$ACC"; echo "exit $?"        # expect: DISCOVERED → EVALUATED
.venv/bin/careeros transition "$JOB" --to EVALUATED -w "$ACC"; echo "exit $?"        # expect: nothing to do
.venv/bin/careeros transition "$JOB" --to APPLIED -w "$ACC"; echo "exit $?"           # expect: illegal_transition (APPLIED only from APPROVAL_REQUIRED)
.venv/bin/careeros ledger list -w "$ACC"
cp "$ACC/ledger.jsonl" /tmp/ledger.good
sed -i.bak '2s/imported/IMPORTED/' "$ACC/ledger.jsonl"
.venv/bin/careeros ledger verify -w "$ACC"; echo "exit $?"                              # expect: LED002 and exit 1
cp /tmp/ledger.good "$ACC/ledger.jsonl"
.venv/bin/careeros ledger verify -w "$ACC"; echo "exit $?"                              # expect: OK
```

If `migrate` lists errors for real job files, the legacy parser is stricter than the real data. Fix it from the actual files (not by guessing), add the shape as a case in `tests/test_core_migration.py`, run the suite, and repeat this step.

- [ ] **Step 7: Record the acceptance run**

Create `docs/END_TO_END_TEST.md` containing: the date, the git commit, the exact commands from Step 6 as run, and **the real output of each**, plus the byte-identical-body result and the real-workspace-unchanged result. Do not paraphrase outputs and do not record anything that was not run. State plainly what this run did not cover: the browser-driven skills (browse, apply, outreach), because they cannot be exercised by this suite, and the real workspace itself, which was not migrated.

- [ ] **Step 8: Commit**

```bash
git add tests/test_foundation_e2e.py .github/workflows/tests.yml CHANGELOG.md docs mkdocs.yml README.md ROADMAP.md
git commit -m "foundation: end-to-end test, CI, changelog, docs and acceptance record" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
```

---

## After the plan

- Do **not** migrate `~/Projects/job-search` as part of this work. When you are ready, run `careeros migrate --dry-run` there, read the plan, then `careeros migrate`.
- Update the standing-directive memory (`feedback-careeros-logging.md`): it refers to code that no longer exists (`AgentRuntime.record_activity`); the equivalent today is `careeros ledger` and the core ledger functions.
- Next sub-project (not started): evidence and Career Memory, then teaching the skills to call these commands.
