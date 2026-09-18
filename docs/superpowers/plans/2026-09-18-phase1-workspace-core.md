# CareerOS Phase 1 — Workspace + Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the CareerOS workspace foundation: a portable, user-owned local directory that any agent can open, with a CLI for onboarding, profile extraction, export/import, and auditable activity logging.

**Architecture:** Two-directory model — the `careeros/` Python package holds all code; the user's workspace is a separate directory created at onboard time. All workspace I/O goes through `StorageProvider`. The LLM (Anthropic SDK) is used only for profile extraction during onboard. Everything else is deterministic.

**Tech Stack:** Python 3.11+, typer[all] (CLI), pydantic v2 (models), anthropic SDK (profile extraction), pytest (tests), hatchling (build).

**Spec:** `docs/superpowers/specs/2026-09-18-careeros-design.md`

## Global Constraints

- Python >= 3.11 required; use `|` union syntax, not `Optional[]`
- All workspace I/O through `StorageProvider` — no direct `open()`, `Path.read_bytes()`, or `os.*` calls in `careeros/core/`, `careeros/workspace/`, or `careeros/skills/`
- Activity logs are append-only; no event is ever edited or deleted
- No credentials, tokens, or API keys in any workspace file, activity log, test fixture, or prompt string
- `atomic_write` must use write-to-temp-then-rename (never write directly to final path)
- Pydantic models use `model_dump_json()` / `model_validate_json()` (v2 API)
- LLM model for extraction: `"claude-haiku-4-5-20251001"`
- CLI entry point: `careeros` (registered in `pyproject.toml` `[project.scripts]`)

---

## File Map

```
careeros/                           ← git repo root
├── careeros/
│   ├── __init__.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── interface.py            ← StorageProvider Protocol
│   │   └── filesystem.py           ← LocalFilesystemStorage
│   ├── workspace/
│   │   ├── __init__.py
│   │   ├── manifest.py             ← Manifest dataclass, version constants, compatibility check
│   │   ├── manager.py              ← WorkspaceContext, init_workspace(), open_workspace()
│   │   └── migrations/
│   │       ├── __init__.py         ← MigrationRunner, MIGRATIONS list, register decorator
│   │       └── m001_initial.py     ← seeds profile/, resumes/, config/, activity/, .careeros/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py               ← Profile, Preferences, Skill, Skills, Goals (pydantic)
│   │   └── activity.py             ← ActivityEvent dataclass, ActivityLogger
│   ├── config.py                   ← GlobalConfig (~/.config/careeros/config.json)
│   ├── skills/
│   │   ├── __init__.py
│   │   └── profile_extract.py      ← extract_basic_profile(resume_text, client) → (Profile, Skills)
│   └── cli/
│       ├── __init__.py
│       ├── main.py                 ← typer app, registers all commands
│       ├── onboard.py              ← onboard_cmd wizard
│       ├── workspace_cmd.py        ← workspace_app (status, validate subcommands)
│       └── portability.py          ← export_cmd, import_workspace_cmd
├── tests/
│   ├── conftest.py                 ← tmp_workspace fixture, SAMPLE_RESUME constant
│   ├── test_storage.py
│   ├── test_manifest.py
│   ├── test_migrations.py
│   ├── test_models.py
│   ├── test_activity.py
│   ├── test_config.py
│   ├── test_profile_extract.py
│   ├── test_onboard.py
│   ├── test_workspace_cmd.py
│   └── test_portability.py
├── pyproject.toml
└── README.md
```

---

### Task 1: Package Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `careeros/__init__.py`
- Create: `careeros/storage/__init__.py`
- Create: `careeros/workspace/__init__.py`
- Create: `careeros/workspace/migrations/__init__.py` (empty stub for now)
- Create: `careeros/core/__init__.py`
- Create: `careeros/skills/__init__.py`
- Create: `careeros/cli/__init__.py`
- Create: `tests/conftest.py`
- Create: `README.md`

**Interfaces:**
- Produces: `careeros.cli.main:app` entry point (smoke-tested)

- [ ] **Step 1: Write the smoke test**

Create `tests/test_smoke.py`:
```python
def test_package_imports():
    import careeros
    assert careeros.__version__ == "0.1.0"

def test_cli_help():
    from typer.testing import CliRunner
    from careeros.cli.main import app
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "careeros" in result.output.lower()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/kaushal/Projects/careeros
pytest tests/test_smoke.py -v
```
Expected: `ModuleNotFoundError` — package doesn't exist yet.

- [ ] **Step 3: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "careeros"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer[all]>=0.12",
    "anthropic>=0.34",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.14",
]

[project.scripts]
careeros = "careeros.cli.main:app"

[tool.hatch.build.targets.wheel]
packages = ["careeros"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Create package stubs**

`careeros/__init__.py`:
```python
__version__ = "0.1.0"
```

`careeros/storage/__init__.py` — empty  
`careeros/workspace/__init__.py` — empty  
`careeros/workspace/migrations/__init__.py` — empty (Task 4 fills this)  
`careeros/core/__init__.py` — empty  
`careeros/skills/__init__.py` — empty  
`careeros/cli/__init__.py` — empty  

`careeros/cli/main.py`:
```python
import typer

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")

if __name__ == "__main__":
    app()
```

`tests/conftest.py`:
```python
import pytest

SAMPLE_RESUME = """
Alice Johnson
Senior Site Reliability Engineer | San Francisco, CA
alice@example.com

EXPERIENCE
Site Reliability Engineer — MegaCorp (2018–present, 6 years)
  - Built distributed monitoring platform handling 1M events/sec
  - Reduced MTTR by 40% through improved alerting and runbooks

Software Engineer — StartupXYZ (2016–2018)
  - Built microservices in Go and Python

SKILLS
Python, Go, Kubernetes, Terraform, AWS, Prometheus, Grafana, Linux

EDUCATION
BS Computer Science, UC Berkeley, 2016
"""
```

`README.md`:
```markdown
# CareerOS

Privacy-first, agent-portable career automation platform.

## Setup

```bash
pip install -e ".[dev]"
```

## Usage

```bash
careeros onboard
careeros workspace status
careeros export
```

## Workspace

Your career data lives in a directory you control, separate from this repo.
Run `git pull` here to get framework updates without touching your data.
```

- [ ] **Step 5: Install and run test**

```bash
pip install -e ".[dev]"
pytest tests/test_smoke.py -v
```
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml careeros/ tests/ README.md
git commit -m "feat: scaffold careeros package with typer CLI entry point"
```

---

### Task 2: StorageProvider Interface + LocalFilesystemStorage

**Files:**
- Create: `careeros/storage/interface.py`
- Create: `careeros/storage/filesystem.py`
- Create: `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `LocalFilesystemStorage(root: str)` — implements all 7 StorageProvider methods
  - `StorageProvider` Protocol (for type annotations elsewhere)

- [ ] **Step 1: Write failing tests**

`tests/test_storage.py`:
```python
import pytest
from careeros.storage.filesystem import LocalFilesystemStorage


def test_write_and_read(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("foo/bar.txt", b"hello")
    assert storage.read("foo/bar.txt") == b"hello"


def test_write_creates_parent_dirs(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("a/b/c/file.txt", b"data")
    assert (tmp_path / "a" / "b" / "c" / "file.txt").exists()


def test_exists_missing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert not storage.exists("missing.txt")


def test_exists_present(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("present.txt", b"x")
    assert storage.exists("present.txt")


def test_delete(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("file.txt", b"x")
    storage.delete("file.txt")
    assert not storage.exists("file.txt")


def test_list_returns_files_under_prefix(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("profile/profile.json", b"{}")
    storage.write("profile/skills.json", b"{}")
    storage.write("activity/log.jsonl", b"")
    result = storage.list("profile/")
    assert set(result) == {"profile/profile.json", "profile/skills.json"}


def test_list_missing_prefix_returns_empty(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert storage.list("nonexistent/") == []


def test_append_creates_file(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.append("activity/test.jsonl", b'{"event":"test"}\n')
    assert storage.read("activity/test.jsonl") == b'{"event":"test"}\n'


def test_append_adds_to_existing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.append("log.jsonl", b"line1\n")
    storage.append("log.jsonl", b"line2\n")
    assert storage.read("log.jsonl") == b"line1\nline2\n"


def test_atomic_write_is_readable_after(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("data.json", b'{"key":"value"}')
    assert storage.read("data.json") == b'{"key":"value"}'


def test_atomic_write_replaces_existing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("data.json", b"old")
    storage.atomic_write("data.json", b"new")
    assert storage.read("data.json") == b"new"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_storage.py -v
```
Expected: `ImportError` — `careeros.storage.filesystem` not found.

- [ ] **Step 3: Create StorageProvider Protocol**

`careeros/storage/interface.py`:
```python
from typing import Protocol


class StorageProvider(Protocol):
    def read(self, path: str) -> bytes: ...
    def write(self, path: str, data: bytes) -> None: ...
    def atomic_write(self, path: str, data: bytes) -> None: ...
    def exists(self, path: str) -> bool: ...
    def delete(self, path: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def append(self, path: str, data: bytes) -> None: ...
```

- [ ] **Step 4: Create LocalFilesystemStorage**

`careeros/storage/filesystem.py`:
```python
import os
import tempfile
from pathlib import Path


class LocalFilesystemStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _resolve(self, path: str) -> Path:
        return self._root / path

    def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    def atomic_write(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=full.parent)
        try:
            os.write(fd, data)
            os.close(fd)
            os.replace(tmp, str(full))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            os.unlink(tmp)
            raise

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def delete(self, path: str) -> None:
        self._resolve(path).unlink()

    def list(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        return [
            str(p.relative_to(self._root))
            for p in base.rglob("*")
            if p.is_file()
        ]

    def append(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "ab") as f:
            f.write(data)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_storage.py -v
```
Expected: all 11 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add careeros/storage/ tests/test_storage.py
git commit -m "feat: add StorageProvider protocol and LocalFilesystemStorage"
```

---

### Task 3: Workspace Manifest

**Files:**
- Create: `careeros/workspace/manifest.py`
- Create: `tests/test_manifest.py`

**Interfaces:**
- Produces:
  - `Manifest` dataclass with `to_json() -> str`, `from_json(data: str) -> Manifest`, `create_new(storage_type: str) -> Manifest`
  - `CAREEROS_VERSION: str = "0.1.0"`
  - `SUPPORTED_SCHEMA_VERSION: str = "1"`
  - `check_schema_compatibility(manifest: Manifest) -> None` — raises `UnsupportedSchemaVersion` if workspace is newer than package supports
  - `UnsupportedSchemaVersion(Exception)`

- [ ] **Step 1: Write failing tests**

`tests/test_manifest.py`:
```python
import json
import pytest
from careeros.workspace.manifest import (
    Manifest,
    CAREEROS_VERSION,
    SUPPORTED_SCHEMA_VERSION,
    check_schema_compatibility,
    UnsupportedSchemaVersion,
)


def test_create_new_has_expected_fields():
    m = Manifest.create_new()
    assert m.careeros_version == CAREEROS_VERSION
    assert m.schema_version == SUPPORTED_SCHEMA_VERSION
    assert m.storage_type == "local"
    assert m.migrations_applied == []


def test_create_new_generates_unique_ids():
    m1 = Manifest.create_new()
    m2 = Manifest.create_new()
    assert m1.workspace_id != m2.workspace_id


def test_to_json_is_valid_json():
    m = Manifest.create_new()
    data = json.loads(m.to_json())
    assert data["schema_version"] == SUPPORTED_SCHEMA_VERSION


def test_round_trip():
    m = Manifest.create_new()
    m.migrations_applied = ["001_initial"]
    restored = Manifest.from_json(m.to_json())
    assert restored.workspace_id == m.workspace_id
    assert restored.migrations_applied == ["001_initial"]


def test_compatible_schema_does_not_raise():
    m = Manifest.create_new()
    check_schema_compatibility(m)  # must not raise


def test_newer_schema_raises():
    m = Manifest.create_new()
    m.schema_version = "999"
    with pytest.raises(UnsupportedSchemaVersion, match="999"):
        check_schema_compatibility(m)


def test_no_pii_in_manifest_fields():
    m = Manifest.create_new()
    data = json.loads(m.to_json())
    assert "name" not in data
    assert "email" not in data
    assert "resume" not in data
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_manifest.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement manifest.py**

`careeros/workspace/manifest.py`:
```python
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import uuid

CAREEROS_VERSION = "0.1.0"
SUPPORTED_SCHEMA_VERSION = "1"


class UnsupportedSchemaVersion(Exception):
    pass


@dataclass
class Manifest:
    careeros_version: str
    schema_version: str
    workspace_id: str
    created_at: str
    storage_type: str
    migrations_applied: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> "Manifest":
        obj = json.loads(data)
        return cls(**obj)

    @classmethod
    def create_new(cls, storage_type: str = "local") -> "Manifest":
        return cls(
            careeros_version=CAREEROS_VERSION,
            schema_version=SUPPORTED_SCHEMA_VERSION,
            workspace_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            storage_type=storage_type,
        )


def check_schema_compatibility(manifest: Manifest) -> None:
    if int(manifest.schema_version) > int(SUPPORTED_SCHEMA_VERSION):
        raise UnsupportedSchemaVersion(
            f"Workspace schema version {manifest.schema_version} is newer than "
            f"this CareerOS supports ({SUPPORTED_SCHEMA_VERSION}). Upgrade CareerOS."
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_manifest.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add careeros/workspace/manifest.py tests/test_manifest.py
git commit -m "feat: add Manifest dataclass with schema version compatibility check"
```

---

### Task 4: Migration Runner + m001_initial

**Files:**
- Modify: `careeros/workspace/migrations/__init__.py`
- Create: `careeros/workspace/migrations/m001_initial.py`
- Create: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `StorageProvider` from `careeros.storage.interface`
- Produces:
  - `MIGRATIONS: list[tuple[str, MigrationFn]]` — ordered list of all registered migrations
  - `register(name: str)` decorator — adds migration to MIGRATIONS
  - `run_pending(storage: StorageProvider, applied: list[str]) -> list[str]` — runs unapplied migrations, returns names of newly applied ones
  - Side effect of `m001_initial`: creates `profile/.keep`, `resumes/versions/.keep`, `config/.keep`, `activity/.keep`, `.careeros/migrations/.keep`, `config/sources.json`, `config/policies.json`, `config/storage.json`

- [ ] **Step 1: Write failing tests**

`tests/test_migrations.py`:
```python
import json
import pytest
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.migrations import run_pending, MIGRATIONS


def test_run_pending_runs_unapplied(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    newly = run_pending(storage, applied=[])
    assert "001_initial" in newly


def test_run_pending_skips_applied(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    # run again — should apply nothing new
    newly = run_pending(storage, applied=["001_initial"])
    assert newly == []


def test_m001_creates_profile_dir(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("profile/.keep")


def test_m001_creates_resumes_versions(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("resumes/versions/.keep")


def test_m001_creates_activity_dir(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("activity/.keep")


def test_m001_creates_sources_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("config/sources.json")
    data = json.loads(storage.read("config/sources.json"))
    assert "sources" in data


def test_m001_creates_policies_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    assert storage.exists("config/policies.json")
    data = json.loads(storage.read("config/policies.json"))
    assert data["approval_required"] is True


def test_m001_idempotent(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    run_pending(storage, applied=[])
    run_pending(storage, applied=[])  # running m001 twice must not error
    assert storage.exists("profile/.keep")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_migrations.py -v
```
Expected: `ImportError` (MIGRATIONS not defined yet).

- [ ] **Step 3: Implement migration runner**

`careeros/workspace/migrations/__init__.py`:
```python
from typing import Callable
from careeros.storage.interface import StorageProvider

MigrationFn = Callable[[StorageProvider], None]

MIGRATIONS: list[tuple[str, MigrationFn]] = []


def register(name: str):
    def decorator(fn: MigrationFn) -> MigrationFn:
        MIGRATIONS.append((name, fn))
        return fn
    return decorator


def run_pending(storage: StorageProvider, applied: list[str]) -> list[str]:
    newly_applied: list[str] = []
    for mig_name, fn in MIGRATIONS:
        if mig_name not in applied:
            fn(storage)
            newly_applied.append(mig_name)
    return newly_applied


# Must be last — imports trigger @register decorators; register must be defined first
from careeros.workspace.migrations import m001_initial  # noqa: F401, E402
```

- [ ] **Step 4: Implement m001_initial**

`careeros/workspace/migrations/m001_initial.py`:
```python
import json
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("001_initial")
def m001_initial(storage: StorageProvider) -> None:
    keep_dirs = [
        "profile/.keep",
        "resumes/versions/.keep",
        "config/.keep",
        "activity/.keep",
        ".careeros/migrations/.keep",
    ]
    for path in keep_dirs:
        if not storage.exists(path):
            storage.write(path, b"")

    if not storage.exists("config/sources.json"):
        storage.write(
            "config/sources.json",
            json.dumps({"sources": []}, indent=2).encode(),
        )

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

    if not storage.exists("config/storage.json"):
        storage.write(
            "config/storage.json",
            json.dumps({"backend": "local"}, indent=2).encode(),
        )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_migrations.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add careeros/workspace/migrations/ tests/test_migrations.py
git commit -m "feat: add migration runner and m001_initial workspace scaffold"
```

---

### Task 5: Workspace Manager

**Files:**
- Create: `careeros/workspace/manager.py`
- Create: `tests/test_workspace_manager.py`
- Modify: `tests/conftest.py` — add `tmp_workspace` fixture

**Interfaces:**
- Consumes: `LocalFilesystemStorage`, `Manifest`, `check_schema_compatibility`, `run_pending`
- Produces:
  - `WorkspaceContext(manifest: Manifest, storage: StorageProvider)` dataclass
  - `MANIFEST_PATH: str = "manifest.json"`
  - `init_workspace(storage: StorageProvider) -> WorkspaceContext` — raises `FileExistsError` if manifest already exists
  - `open_workspace(storage: StorageProvider) -> WorkspaceContext` — raises `FileNotFoundError` if no manifest; raises `UnsupportedSchemaVersion` if schema too new; runs pending migrations

- [ ] **Step 1: Write failing tests**

`tests/test_workspace_manager.py`:
```python
import pytest
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, open_workspace
from careeros.workspace.manifest import SUPPORTED_SCHEMA_VERSION, UnsupportedSchemaVersion


def test_init_creates_manifest(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    assert storage.exists("manifest.json")
    assert ctx.manifest.schema_version == SUPPORTED_SCHEMA_VERSION


def test_init_runs_migrations(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    assert storage.exists("profile/.keep")
    assert storage.exists("config/sources.json")


def test_init_records_migrations_in_manifest(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    assert "001_initial" in ctx.manifest.migrations_applied


def test_init_twice_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileExistsError, match="already exists"):
        init_workspace(storage)


def test_open_reads_manifest(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx1 = init_workspace(storage)
    ctx2 = open_workspace(storage)
    assert ctx1.manifest.workspace_id == ctx2.manifest.workspace_id


def test_open_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        open_workspace(storage)


def test_open_too_new_schema_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    # Simulate a workspace created by a future CareerOS version
    import json
    from dataclasses import asdict
    manifest_data = asdict(ctx.manifest)
    manifest_data["schema_version"] = "999"
    storage.atomic_write("manifest.json", json.dumps(manifest_data, indent=2).encode())
    with pytest.raises(UnsupportedSchemaVersion):
        open_workspace(storage)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_workspace_manager.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement workspace manager**

`careeros/workspace/manager.py`:
```python
from dataclasses import dataclass
from careeros.storage.interface import StorageProvider
from careeros.workspace.manifest import Manifest, check_schema_compatibility
from careeros.workspace.migrations import run_pending

MANIFEST_PATH = "manifest.json"


@dataclass
class WorkspaceContext:
    manifest: Manifest
    storage: StorageProvider


def init_workspace(storage: StorageProvider) -> WorkspaceContext:
    if storage.exists(MANIFEST_PATH):
        raise FileExistsError(
            "Workspace already exists at this path. Use 'careeros workspace status' to inspect it."
        )
    manifest = Manifest.create_new()
    newly = run_pending(storage, manifest.migrations_applied)
    manifest.migrations_applied.extend(newly)
    storage.atomic_write(MANIFEST_PATH, manifest.to_json().encode())
    return WorkspaceContext(manifest=manifest, storage=storage)


def open_workspace(storage: StorageProvider) -> WorkspaceContext:
    if not storage.exists(MANIFEST_PATH):
        raise FileNotFoundError(
            "No CareerOS workspace found. Run 'careeros onboard' to create one."
        )
    manifest = Manifest.from_json(storage.read(MANIFEST_PATH).decode())
    check_schema_compatibility(manifest)
    newly = run_pending(storage, manifest.migrations_applied)
    if newly:
        manifest.migrations_applied.extend(newly)
        storage.atomic_write(MANIFEST_PATH, manifest.to_json().encode())
    return WorkspaceContext(manifest=manifest, storage=storage)
```

- [ ] **Step 4: Add tmp_workspace fixture to conftest.py**

Append to `tests/conftest.py`:
```python
import pytest
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, WorkspaceContext


@pytest.fixture
def tmp_workspace(tmp_path) -> WorkspaceContext:
    storage = LocalFilesystemStorage(str(tmp_path))
    return init_workspace(storage)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_workspace_manager.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 6: Run full suite to check for regressions**

```bash
pytest -v
```
Expected: all prior tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add careeros/workspace/manager.py tests/test_workspace_manager.py tests/conftest.py
git commit -m "feat: add WorkspaceContext, init_workspace, open_workspace"
```

---

### Task 6: Core Models

**Files:**
- Create: `careeros/core/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: `StorageProvider`
- Produces:
  - `Profile(name, email, title, years_of_experience, location, summary)` — pydantic BaseModel
    - `save(storage) -> None` — atomic_write to `profile/profile.json`
    - `load(storage) -> Profile` — raises if file missing
    - `load_or_empty(storage) -> Profile`
  - `Skill(name, category, level, years, source, last_used)` — pydantic BaseModel
  - `Skills(skills: list[Skill])` — pydantic BaseModel
    - `save(storage) -> None` — atomic_write to `profile/skills.json`
    - `load_or_empty(storage) -> Skills`
  - `Preferences(target_roles, seniority, locations, remote_preference, industries, target_companies, minimum_compensation, compensation_currency, employment_types, visa_sponsorship_required, notice_period_days)` — pydantic BaseModel
    - `save(storage) -> None` — atomic_write to `profile/preferences.json`
    - `load_or_empty(storage) -> Preferences`
  - `Goals(short_term, long_term, non_negotiables, open_to)` — pydantic BaseModel
    - `save(storage) -> None` — atomic_write to `profile/goals.json`
    - `load_or_empty(storage) -> Goals`

- [ ] **Step 1: Write failing tests**

`tests/test_models.py`:
```python
import pytest
from careeros.core.models import Profile, Skill, Skills, Preferences, Goals


def test_profile_save_and_load(tmp_workspace):
    storage = tmp_workspace.storage
    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    profile.save(storage)
    loaded = Profile.load(storage)
    assert loaded.name == "Alice Johnson"
    assert loaded.title == "Senior SRE"
    assert loaded.years_of_experience == 8


def test_profile_load_missing_raises(tmp_workspace):
    with pytest.raises(FileNotFoundError):
        Profile.load(tmp_workspace.storage)


def test_profile_load_or_empty_returns_defaults(tmp_workspace):
    profile = Profile.load_or_empty(tmp_workspace.storage)
    assert profile.name == ""
    assert profile.email is None


def test_profile_optional_fields_default_none(tmp_workspace):
    profile = Profile(name="Bob")
    profile.save(tmp_workspace.storage)
    loaded = Profile.load(tmp_workspace.storage)
    assert loaded.email is None
    assert loaded.title is None


def test_skills_save_and_load(tmp_workspace):
    storage = tmp_workspace.storage
    skills = Skills(skills=[Skill(name="Python", level="expert", source="resume")])
    skills.save(storage)
    loaded = Skills.load_or_empty(storage)
    assert len(loaded.skills) == 1
    assert loaded.skills[0].name == "Python"
    assert loaded.skills[0].source == "resume"


def test_skills_load_or_empty_when_missing(tmp_workspace):
    result = Skills.load_or_empty(tmp_workspace.storage)
    assert result.skills == []


def test_preferences_round_trip(tmp_workspace):
    storage = tmp_workspace.storage
    prefs = Preferences(
        target_roles=["SRE", "Platform Engineer"],
        minimum_compensation=150000,
        remote_preference="remote",
    )
    prefs.save(storage)
    loaded = Preferences.load_or_empty(storage)
    assert loaded.target_roles == ["SRE", "Platform Engineer"]
    assert loaded.minimum_compensation == 150000
    assert loaded.remote_preference == "remote"


def test_preferences_defaults(tmp_workspace):
    prefs = Preferences.load_or_empty(tmp_workspace.storage)
    assert prefs.target_roles == []
    assert prefs.visa_sponsorship_required is False
    assert prefs.compensation_currency == "USD"


def test_goals_round_trip(tmp_workspace):
    storage = tmp_workspace.storage
    goals = Goals(
        short_term=["Get a staff role"],
        non_negotiables=["No on-call"],
    )
    goals.save(storage)
    loaded = Goals.load_or_empty(storage)
    assert loaded.short_term == ["Get a staff role"]
    assert loaded.non_negotiables == ["No on-call"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_models.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement models.py**

`careeros/core/models.py`:
```python
from pydantic import BaseModel
from careeros.storage.interface import StorageProvider


class Profile(BaseModel):
    name: str = ""
    email: str | None = None
    title: str | None = None
    years_of_experience: int | None = None
    location: str | None = None
    summary: str | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/profile.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load(cls, storage: StorageProvider) -> "Profile":
        if not storage.exists("profile/profile.json"):
            raise FileNotFoundError("profile/profile.json not found in workspace")
        return cls.model_validate_json(storage.read("profile/profile.json").decode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Profile":
        if not storage.exists("profile/profile.json"):
            return cls()
        return cls.load(storage)


class Skill(BaseModel):
    name: str
    category: str | None = None
    level: str | None = None
    years: int | None = None
    source: str | None = None
    last_used: str | None = None


class Skills(BaseModel):
    skills: list[Skill] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/skills.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Skills":
        if not storage.exists("profile/skills.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/skills.json").decode())


class Preferences(BaseModel):
    target_roles: list[str] = []
    seniority: str | None = None
    locations: list[str] = []
    remote_preference: str | None = None
    industries: list[str] = []
    target_companies: list[str] = []
    minimum_compensation: int | None = None
    compensation_currency: str = "USD"
    employment_types: list[str] = []
    visa_sponsorship_required: bool = False
    notice_period_days: int | None = None

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/preferences.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Preferences":
        if not storage.exists("profile/preferences.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/preferences.json").decode())


class Goals(BaseModel):
    short_term: list[str] = []
    long_term: list[str] = []
    non_negotiables: list[str] = []
    open_to: list[str] = []

    def save(self, storage: StorageProvider) -> None:
        storage.atomic_write("profile/goals.json", self.model_dump_json(indent=2).encode())

    @classmethod
    def load_or_empty(cls, storage: StorageProvider) -> "Goals":
        if not storage.exists("profile/goals.json"):
            return cls()
        return cls.model_validate_json(storage.read("profile/goals.json").decode())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_models.py -v
```
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add careeros/core/models.py tests/test_models.py
git commit -m "feat: add Profile, Skills, Preferences, Goals pydantic models"
```

---

### Task 7: ActivityLogger

**Files:**
- Create: `careeros/core/activity.py`
- Create: `tests/test_activity.py`

**Interfaces:**
- Consumes: `StorageProvider`
- Produces:
  - `ActivityEvent(event_type, action, status, summary, agent_runtime, session_id, timestamp, entity_type, entity_id, reason)` dataclass
  - `ActivityLogger(storage: StorageProvider, session_id: str | None)`
    - `session_id: str` — auto-generated UUID if not provided
    - `log(event: ActivityEvent) -> None` — appends JSON line to `activity/YYYY-MM-DD.jsonl`
    - `new_event(event_type, action, summary, status, agent_runtime, **kwargs) -> ActivityEvent` — convenience factory

- [ ] **Step 1: Write failing tests**

`tests/test_activity.py`:
```python
import json
import pytest
from datetime import datetime, timezone
from careeros.core.activity import ActivityEvent, ActivityLogger


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_logger_writes_jsonl(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    event = logger.new_event("workspace_created", "init", "Workspace initialized")
    logger.log(event)

    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    lines = [l for l in content.strip().split("\n") if l]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "workspace_created"
    assert parsed["status"] == "success"
    assert parsed["session_id"] == logger.session_id


def test_multiple_events_appended(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    for i in range(3):
        logger.log(logger.new_event("test_event", "test", f"Event {i}"))

    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    lines = [l for l in content.strip().split("\n") if l]
    assert len(lines) == 3


def test_custom_session_id(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage, session_id="my-session")
    event = logger.new_event("test", "test", "summary")
    logger.log(event)

    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    parsed = json.loads(content.strip())
    assert parsed["session_id"] == "my-session"


def test_event_has_iso_timestamp(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    event = logger.new_event("test", "test", "summary")
    # timestamp must be a valid ISO-8601 string
    datetime.fromisoformat(event.timestamp)


def test_no_secret_fields_in_event():
    event = ActivityEvent(
        event_type="test", action="test", summary="summary",
        agent_runtime="local", session_id="s1", status="success",
    )
    serialized = json.dumps(
        {f: getattr(event, f) for f in event.__dataclass_fields__}
    )
    for forbidden in ("password", "token", "api_key", "secret", "credential"):
        assert forbidden not in serialized


def test_entity_fields_optional(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    event = logger.new_event(
        "job_matched", "match", "Matched job at Acme",
        entity_type="job", entity_id="job-123"
    )
    logger.log(event)
    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    parsed = json.loads(content.strip())
    assert parsed["entity_type"] == "job"
    assert parsed["entity_id"] == "job-123"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_activity.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement activity.py**

`careeros/core/activity.py`:
```python
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import uuid
from careeros.storage.interface import StorageProvider


@dataclass
class ActivityEvent:
    event_type: str
    action: str
    status: str
    summary: str
    agent_runtime: str
    session_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entity_type: str | None = None
    entity_id: str | None = None
    reason: str | None = None


class ActivityLogger:
    def __init__(self, storage: StorageProvider, session_id: str | None = None) -> None:
        self._storage = storage
        self.session_id = session_id or str(uuid.uuid4())

    def log(self, event: ActivityEvent) -> None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = f"activity/{date}.jsonl"
        line = json.dumps(asdict(event)) + "\n"
        self._storage.append(path, line.encode())

    def new_event(
        self,
        event_type: str,
        action: str,
        summary: str,
        status: str = "success",
        agent_runtime: str = "local",
        **kwargs,
    ) -> ActivityEvent:
        return ActivityEvent(
            event_type=event_type,
            action=action,
            summary=summary,
            status=status,
            agent_runtime=agent_runtime,
            session_id=self.session_id,
            **kwargs,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_activity.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add careeros/core/activity.py tests/test_activity.py
git commit -m "feat: add ActivityEvent dataclass and ActivityLogger"
```

---

### Task 8: GlobalConfig

**Files:**
- Create: `careeros/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `CONFIG_PATH: Path` — `~/.config/careeros/config.json`
  - `GlobalConfig(workspace_path: str | None)` — pydantic BaseModel
    - `save() -> None` — writes to CONFIG_PATH; creates parent dirs
    - `load() -> GlobalConfig` (classmethod) — returns `GlobalConfig()` if file missing

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:
```python
import pytest
from pathlib import Path
from careeros.config import GlobalConfig


def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    config = GlobalConfig(workspace_path="/my/career")
    config.save()
    loaded = GlobalConfig.load()
    assert loaded.workspace_path == "/my/career"


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "nonexistent" / "config.json")
    config = GlobalConfig.load()
    assert config.workspace_path is None


def test_save_creates_parent_dirs(tmp_path, monkeypatch):
    nested = tmp_path / "a" / "b" / "config.json"
    monkeypatch.setattr("careeros.config.CONFIG_PATH", nested)
    GlobalConfig(workspace_path="/ws").save()
    assert nested.exists()


def test_overwrite_updates_value(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    GlobalConfig(workspace_path="/first").save()
    GlobalConfig(workspace_path="/second").save()
    loaded = GlobalConfig.load()
    assert loaded.workspace_path == "/second"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_config.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement config.py**

`careeros/config.py`:
```python
from pathlib import Path
from pydantic import BaseModel

CONFIG_PATH = Path.home() / ".config" / "careeros" / "config.json"


class GlobalConfig(BaseModel):
    workspace_path: str | None = None

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls) -> "GlobalConfig":
        if not CONFIG_PATH.exists():
            return cls()
        return cls.model_validate_json(CONFIG_PATH.read_text())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_config.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add careeros/config.py tests/test_config.py
git commit -m "feat: add GlobalConfig for workspace path persistence"
```

---

### Task 9: Profile Extraction Skill

**Files:**
- Create: `careeros/skills/profile_extract.py`
- Create: `tests/test_profile_extract.py`

**Interfaces:**
- Consumes: `anthropic.Anthropic` client (injected), `Profile`, `Skill`, `Skills`
- Produces:
  - `extract_basic_profile(resume_text: str, client: anthropic.Anthropic | None = None) -> tuple[Profile, Skills]`
    - If `client` is None, creates `anthropic.Anthropic()` (reads `ANTHROPIC_API_KEY` from env)
    - Makes 2 API calls: one for profile fields, one for skills list
    - Model: `"claude-haiku-4-5-20251001"`, max_tokens=512 (profile) and 1024 (skills)
    - Returns `(Profile, Skills)` parsed from LLM JSON responses

- [ ] **Step 1: Write failing tests**

`tests/test_profile_extract.py`:
```python
import pytest
from unittest.mock import MagicMock
from careeros.skills.profile_extract import extract_basic_profile
from careeros.core.models import Profile, Skills


def _make_mock_client(profile_json: str, skills_json: str) -> MagicMock:
    client = MagicMock()
    profile_msg = MagicMock()
    profile_msg.content = [MagicMock(text=profile_json)]
    skills_msg = MagicMock()
    skills_msg.content = [MagicMock(text=skills_json)]
    client.messages.create.side_effect = [profile_msg, skills_msg]
    return client


def test_returns_profile_and_skills():
    profile_json = '{"name":"Alice Johnson","title":"Senior SRE","years_of_experience":8,"location":"San Francisco, CA","email":"alice@example.com","summary":"SRE with 8 years."}'
    skills_json = '{"skills":[{"name":"Python","level":"expert","source":"resume"},{"name":"Kubernetes","level":"advanced","source":"resume"}]}'
    client = _make_mock_client(profile_json, skills_json)

    profile, skills = extract_basic_profile("dummy resume", client=client)

    assert profile.name == "Alice Johnson"
    assert profile.years_of_experience == 8
    assert profile.title == "Senior SRE"
    assert len(skills.skills) == 2
    assert skills.skills[0].name == "Python"
    assert skills.skills[0].level == "expert"


def test_handles_null_fields():
    profile_json = '{"name":"Bob","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)

    profile, skills = extract_basic_profile("short bio", client=client)

    assert profile.name == "Bob"
    assert profile.title is None
    assert skills.skills == []


def test_makes_exactly_two_api_calls():
    profile_json = '{"name":"Charlie","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)

    extract_basic_profile("resume text", client=client)

    assert client.messages.create.call_count == 2


def test_uses_haiku_model():
    profile_json = '{"name":"Dana","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)

    extract_basic_profile("resume text", client=client)

    calls = client.messages.create.call_args_list
    for call in calls:
        assert call.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_resume_text_included_in_prompt():
    profile_json = '{"name":"Eve","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)
    resume = "Eve Smith\nStaff Engineer with 12 years"

    extract_basic_profile(resume, client=client)

    first_call_messages = client.messages.create.call_args_list[0].kwargs["messages"]
    assert resume in first_call_messages[0]["content"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_profile_extract.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement profile_extract.py**

`careeros/skills/profile_extract.py`:
```python
import json
import anthropic
from careeros.core.models import Profile, Skill, Skills

EXTRACTION_MODEL = "claude-haiku-4-5-20251001"

# Instruction-only templates — resume text is concatenated, never interpolated,
# so braces in user content cannot cause KeyError or prompt injection via formatting.
_PROFILE_INSTRUCTIONS = """\
Extract the following fields from this resume as JSON.
Return ONLY valid JSON — no markdown, no explanation.
Use null for any field not found.

{
  "name": "<full name>",
  "email": "<email or null>",
  "title": "<current or most recent job title or null>",
  "years_of_experience": <total years as integer or null>,
  "location": "<city, state/country or null>",
  "summary": "<1-2 sentence professional summary or null>"
}

Resume:
"""

_SKILLS_INSTRUCTIONS = """\
Extract the top technical skills from this resume as JSON.
Return ONLY valid JSON — no markdown, no explanation.
Limit to 20 most prominent skills.

{"skills": [{"name": "<skill name>", "level": "<beginner|intermediate|advanced|expert or null>", "source": "resume"}]}

Resume:
"""


def extract_basic_profile(
    resume_text: str,
    client: anthropic.Anthropic | None = None,
) -> tuple[Profile, Skills]:
    if client is None:
        client = anthropic.Anthropic()

    profile_resp = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": _PROFILE_INSTRUCTIONS + resume_text}],
    )
    profile_data = json.loads(profile_resp.content[0].text)
    profile = Profile(**{k: v for k, v in profile_data.items() if v is not None})

    skills_resp = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": _SKILLS_INSTRUCTIONS + resume_text}],
    )
    skills_data = json.loads(skills_resp.content[0].text)
    skills = Skills(skills=[Skill(**s) for s in skills_data.get("skills", [])])

    return profile, skills
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_profile_extract.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add careeros/skills/profile_extract.py tests/test_profile_extract.py
git commit -m "feat: add profile extraction skill using Anthropic Haiku"
```

---

### Task 10: Onboard Wizard + CLI Wiring

**Files:**
- Create: `careeros/cli/onboard.py`
- Modify: `careeros/cli/main.py` — register onboard command
- Create: `tests/test_onboard.py`

**Interfaces:**
- Consumes: `LocalFilesystemStorage`, `init_workspace`, `ActivityLogger`, `Profile`, `Skills`, `Preferences`, `Goals`, `extract_basic_profile`, `GlobalConfig`
- Produces:
  - `onboard_cmd(workspace: str | None)` typer command — registered as `careeros onboard`
  - Side effects: creates workspace directory, seeds all profile files, writes `~/.config/careeros/config.json`, logs `workspace_created` / `resume_imported` / `profile_extracted` / `onboard_complete` activity events
  - `careeros/cli/workspace_cmd.py` stub (empty `workspace_app`) — created here so main.py can import it

- [ ] **Step 1: Write failing tests**

`tests/test_onboard.py`:
```python
import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch
from careeros.cli.main import app
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace
from datetime import datetime, timezone


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture
def resume_file(tmp_path):
    f = tmp_path / "resume.md"
    f.write_text("Alice Johnson\nSenior SRE\n8 years")
    return f


@pytest.fixture
def mock_extraction():
    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    skills = Skills(skills=[])
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=(profile, skills)):
        yield


def _run_onboard(runner, tmp_path, resume_file, ws_name="workspace"):
    ws_path = str(tmp_path / ws_name)
    # Input sequence: workspace path, resume path, confirm profile (y),
    # roles (blank), remote (any), comp (blank), locations (blank),
    # sources (greenhouse), goals (n)
    user_input = f"{ws_path}\n{resume_file}\ny\n\nany\n\n\ngreenhouse\nn\n"
    return runner.invoke(app, ["onboard"], input=user_input), ws_path


def test_onboard_creates_manifest(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    result, ws_path = _run_onboard(runner, tmp_path, resume_file)
    assert result.exit_code == 0, result.output
    assert (Path(ws_path) / "manifest.json").exists()


def test_onboard_writes_profile(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    storage = LocalFilesystemStorage(ws_path)
    profile = Profile.load(storage)
    assert profile.name == "Alice Johnson"


def test_onboard_writes_global_config(tmp_path, resume_file, mock_extraction, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("careeros.config.CONFIG_PATH", config_path)
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["workspace_path"] == ws_path


def test_onboard_logs_activity_events(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    storage = LocalFilesystemStorage(ws_path)
    log = storage.read(f"activity/{_today()}.jsonl").decode()
    event_types = [json.loads(l)["event_type"] for l in log.strip().split("\n") if l]
    assert "workspace_created" in event_types
    assert "resume_imported" in event_types
    assert "onboard_complete" in event_types


def test_onboard_missing_resume_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    ws_path = str(tmp_path / "ws")
    result = runner.invoke(app, ["onboard"], input=f"{ws_path}\n/nonexistent/resume.md\n")
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_onboard.py -v
```
Expected: `ImportError` or command not found.

- [ ] **Step 3: Create workspace_cmd stub so main.py can import it**

`careeros/cli/workspace_cmd.py` (stub only — Task 11 fills this):
```python
import typer

workspace_app = typer.Typer(name="workspace", help="Manage your CareerOS workspace.")
```

- [ ] **Step 4: Create onboard.py**

`careeros/cli/onboard.py`:
```python
from pathlib import Path
import typer
from rich import print as rprint
from rich.prompt import Confirm, Prompt

from careeros.config import GlobalConfig
from careeros.core.activity import ActivityLogger
from careeros.core.models import Goals, Preferences, Profile, Skills
from careeros.skills.profile_extract import extract_basic_profile
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace
import json


def onboard_cmd(
    workspace: str = typer.Option(None, "--workspace", "-w", help="Path for new workspace"),
) -> None:
    rprint("[bold]Welcome to CareerOS[/bold]")
    rprint("Let's set up your workspace.\n")

    # Step 1: workspace path
    ws_path = workspace or Prompt.ask(
        "Where should I store your workspace?",
        default=str(Path.home() / "career"),
    )
    ws_path = str(Path(ws_path).expanduser())

    storage = LocalFilesystemStorage(ws_path)
    ctx = init_workspace(storage)
    logger = ActivityLogger(ctx.storage)
    logger.log(logger.new_event("workspace_created", "init", f"Workspace initialized at {ws_path}"))
    rprint(f"\n[green]Workspace created at {ws_path}[/green]")

    # Step 2: resume
    resume_path_str = Prompt.ask("\nPath to your resume (Markdown or plain text)")
    resume_file = Path(resume_path_str).expanduser()
    if not resume_file.exists():
        rprint(f"[red]File not found: {resume_file}[/red]")
        raise typer.Exit(1)

    resume_text = resume_file.read_text()
    storage.atomic_write("resumes/master.md", resume_text.encode())
    logger.log(logger.new_event("resume_imported", "import", f"Resume imported from {resume_path_str}", entity_type="resume"))

    # Step 3: profile extraction
    rprint("\nExtracting profile from resume...")
    try:
        profile, skills = extract_basic_profile(resume_text)
    except Exception as e:
        rprint(f"[yellow]Extraction failed ({e}). Starting with empty profile.[/yellow]")
        profile, skills = Profile(), Skills()

    rprint("\n[bold]Extracted profile:[/bold]")
    rprint(f"  Name:       {profile.name or '(not found)'}")
    rprint(f"  Title:      {profile.title or '(not found)'}")
    rprint(f"  Experience: {profile.years_of_experience or '?'} years")
    rprint(f"  Skills:     {len(skills.skills)} found")

    if not Confirm.ask("\nDoes this look right?", default=True):
        rprint("[yellow]Edit profile/profile.json in your workspace to correct it.[/yellow]")

    profile.save(storage)
    skills.save(storage)
    logger.log(logger.new_event("profile_extracted", "extract", f"Profile extracted: {profile.name}", entity_type="profile"))

    # Step 4: preferences
    rprint("\n[bold]Job Preferences[/bold]")
    roles_raw = Prompt.ask("Target roles (comma-separated, or press enter to skip)", default="")
    target_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    remote_pref = Prompt.ask(
        "Remote preference", choices=["remote", "hybrid", "onsite", "any"], default="any"
    )

    comp_raw = Prompt.ask("Minimum annual compensation in USD (or press enter to skip)", default="")
    min_comp = int(comp_raw) if comp_raw.strip().isdigit() else None

    locs_raw = Prompt.ask("Preferred locations (comma-separated, or press enter to skip)", default="")
    locations = [l.strip() for l in locs_raw.split(",") if l.strip()]

    prefs = Preferences(
        target_roles=target_roles,
        remote_preference=remote_pref,
        minimum_compensation=min_comp,
        locations=locations,
    )
    prefs.save(storage)

    # Step 5: job sources
    rprint("\n[bold]Job Sources[/bold]")
    rprint("Which sources may CareerOS search? Available: greenhouse, linkedin, lever, naukri")
    sources_raw = Prompt.ask("Sources (comma-separated)", default="greenhouse")
    sources = [
        {"source": s.strip(), "mode": "SEARCH_ONLY"}
        for s in sources_raw.split(",")
        if s.strip()
    ]
    storage.atomic_write("config/sources.json", json.dumps({"sources": sources}, indent=2).encode())

    # Step 6: goals (optional)
    goals = Goals()
    if Confirm.ask("\nWould you like to set career goals now?", default=False):
        st_raw = Prompt.ask("Short-term goals (comma-separated)", default="")
        lt_raw = Prompt.ask("Long-term goals (comma-separated)", default="")
        goals = Goals(
            short_term=[g.strip() for g in st_raw.split(",") if g.strip()],
            long_term=[g.strip() for g in lt_raw.split(",") if g.strip()],
        )
    goals.save(storage)

    # Save global config
    GlobalConfig(workspace_path=ws_path).save()
    logger.log(logger.new_event("onboard_complete", "onboard", "Onboarding complete"))

    rprint(f"\n[bold green]CareerOS ready.[/bold green]")
    rprint(f"Workspace: {ws_path}")
    rprint("Run [bold]careeros workspace status[/bold] to see your profile summary.")
```

- [ ] **Step 5: Wire up main.py**

`careeros/cli/main.py`:
```python
import typer
from careeros.cli.onboard import onboard_cmd
from careeros.cli.workspace_cmd import workspace_app

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")

if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_onboard.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/ tests/test_onboard.py
git commit -m "feat: add onboard wizard CLI command with profile extraction and activity logging"
```

---

### Task 11: Workspace CLI Commands (status, validate)

**Files:**
- Modify: `careeros/cli/workspace_cmd.py` — implement status and validate
- Create: `tests/test_workspace_cmd.py`

**Interfaces:**
- Consumes: `LocalFilesystemStorage`, `open_workspace`, `Profile`, `Skills`, `GlobalConfig`
- Produces:
  - `careeros workspace status [--workspace PATH]` — prints workspace ID, created date, schema version, name, title, skill count, last activity event
  - `careeros workspace validate [--workspace PATH]` — checks manifest parseable + compatible, required paths exist, last 10 activity log lines parse as JSON; exits 1 on any failure

- [ ] **Step 1: Write failing tests**

`tests/test_workspace_cmd.py`:
```python
import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from careeros.cli.main import app
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


@pytest.fixture
def seeded_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    ctx = init_workspace(storage)
    Profile(name="Alice", title="SRE").save(storage)
    Skills(skills=[]).save(storage)
    return tmp_path, ctx


def test_status_exits_zero(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "status", "--workspace", str(ws_path)])
    assert result.exit_code == 0


def test_status_shows_name(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "status", "--workspace", str(ws_path)])
    assert "Alice" in result.output


def test_status_shows_schema_version(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "status", "--workspace", str(ws_path)])
    assert "v1" in result.output


def test_validate_passes_fresh_workspace(seeded_workspace):
    ws_path, _ = seeded_workspace
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "validate", "--workspace", str(ws_path)])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_validate_fails_missing_manifest(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "validate", "--workspace", str(tmp_path)])
    assert result.exit_code != 0


def test_validate_fails_bad_activity_line(seeded_workspace):
    ws_path, _ = seeded_workspace
    from datetime import datetime, timezone
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = ws_path / "activity" / f"{date}.jsonl"
    log_path.parent.mkdir(exist_ok=True)
    log_path.write_text("not valid json\n")
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "validate", "--workspace", str(ws_path)])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_workspace_cmd.py -v
```
Expected: tests fail because workspace_cmd only has a stub.

- [ ] **Step 3: Implement workspace_cmd.py**

`careeros/cli/workspace_cmd.py`:
```python
import json
import typer
from rich import print as rprint
from careeros.config import GlobalConfig
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace
from careeros.workspace.manifest import Manifest, check_schema_compatibility, UnsupportedSchemaVersion

workspace_app = typer.Typer(name="workspace", help="Manage your CareerOS workspace.")


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


@workspace_app.command("status")
def status_cmd(workspace: str = typer.Option(None, "--workspace", help="Workspace path")) -> None:
    storage = _get_storage(workspace)
    ctx = open_workspace(storage)
    profile = Profile.load_or_empty(storage)
    skills = Skills.load_or_empty(storage)

    rprint("[bold]CareerOS Workspace[/bold]")
    rprint(f"  ID:      {ctx.manifest.workspace_id}")
    rprint(f"  Created: {ctx.manifest.created_at[:10]}")
    rprint(f"  Schema:  v{ctx.manifest.schema_version}")
    rprint(f"  Name:    {profile.name or '(not set)'}")
    rprint(f"  Title:   {profile.title or '(not set)'}")
    rprint(f"  Skills:  {len(skills.skills)} recorded")

    activity_files = sorted(p for p in storage.list("activity/") if p.endswith(".jsonl"))
    if activity_files:
        last_content = storage.read(activity_files[-1]).decode()
        lines = [l for l in last_content.strip().split("\n") if l]
        if lines:
            last = json.loads(lines[-1])
            rprint(f"  Last:    {last['timestamp'][:19]} — {last['summary']}")
    else:
        rprint("  Last:    (no activity yet)")


@workspace_app.command("validate")
def validate_cmd(workspace: str = typer.Option(None, "--workspace", help="Workspace path")) -> None:
    storage = _get_storage(workspace)
    errors: list[str] = []

    if not storage.exists("manifest.json"):
        rprint("[red]FAIL[/red] manifest.json missing")
        raise typer.Exit(1)

    try:
        manifest = Manifest.from_json(storage.read("manifest.json").decode())
        check_schema_compatibility(manifest)
        rprint(f"[green]OK[/green]   manifest.json (schema v{manifest.schema_version})")
    except UnsupportedSchemaVersion as e:
        errors.append(str(e))
        rprint(f"[red]FAIL[/red] manifest.json: {e}")
    except Exception as e:
        errors.append(str(e))
        rprint(f"[red]FAIL[/red] manifest.json parse error: {e}")

    required_paths = [
        "profile/.keep",
        "resumes/versions/.keep",
        "activity/.keep",
        "config/sources.json",
        "config/policies.json",
    ]
    for path in required_paths:
        if storage.exists(path):
            rprint(f"[green]OK[/green]   {path}")
        else:
            errors.append(f"missing: {path}")
            rprint(f"[red]FAIL[/red] missing: {path}")

    activity_files = sorted(p for p in storage.list("activity/") if p.endswith(".jsonl"))
    for log_path in activity_files[-1:]:
        content = storage.read(log_path).decode()
        lines = [l for l in content.strip().split("\n") if l]
        bad_lines = 0
        for line in lines[-10:]:
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"bad activity line: {e}")
                bad_lines += 1
        if bad_lines:
            rprint(f"[red]FAIL[/red] {log_path}: {bad_lines} unparseable line(s)")
        else:
            rprint(f"[green]OK[/green]   {log_path} ({len(lines)} events)")

    if not errors:
        rprint("\n[bold green]Workspace is valid.[/bold green]")
    else:
        rprint(f"\n[bold red]{len(errors)} error(s) found.[/bold red]")
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_workspace_cmd.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add careeros/cli/workspace_cmd.py tests/test_workspace_cmd.py
git commit -m "feat: add workspace status and validate CLI commands"
```

---

### Task 12: Export and Import

**Files:**
- Create: `careeros/cli/portability.py`
- Modify: `careeros/cli/main.py` — register export and import commands
- Create: `tests/test_portability.py`

**Interfaces:**
- Consumes: `GlobalConfig`, `LocalFilesystemStorage`, `open_workspace`
- Produces:
  - `export_cmd(output: str | None, workspace: str | None)` — registered as `careeros export`
    - Zips all workspace files except `.careeros/migrations/` internals
    - Default output filename: `<workspace_dir_name>-YYYY-MM-DD.zip` in cwd
  - `import_workspace_cmd(source: str, dest: str)` — registered as `careeros import`
    - Extracts zip to dest (dest must not exist)
    - Calls `open_workspace()` to validate the restored workspace
    - `import` is a Python keyword; use `app.command("import")(import_workspace_cmd)`

- [ ] **Step 1: Write failing tests**

`tests/test_portability.py`:
```python
import json
import zipfile
import pytest
from pathlib import Path
from typer.testing import CliRunner
from careeros.cli.main import app
from careeros.config import GlobalConfig
from careeros.core.models import Profile
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, open_workspace


@pytest.fixture
def workspace_with_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    ws_path = str(tmp_path / "my-career")
    storage = LocalFilesystemStorage(ws_path)
    ctx = init_workspace(storage)
    Profile(name="Test User", title="Engineer").save(storage)
    GlobalConfig(workspace_path=ws_path).save()
    return tmp_path, ws_path, ctx.manifest.workspace_id


def test_export_creates_zip(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    result = runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])
    assert result.exit_code == 0, result.output
    assert Path(output).exists()


def test_export_zip_contains_manifest(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])
    with zipfile.ZipFile(output) as zf:
        assert "manifest.json" in zf.namelist()


def test_export_zip_contains_profile(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])
    with zipfile.ZipFile(output) as zf:
        assert "profile/profile.json" in zf.namelist()


def test_import_restores_workspace(workspace_with_profile):
    tmp_path, ws_path, original_id = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])

    new_dest = str(tmp_path / "restored")
    result = runner.invoke(app, ["import", output, "--dest", new_dest])
    assert result.exit_code == 0, result.output

    restored_storage = LocalFilesystemStorage(new_dest)
    ctx = open_workspace(restored_storage)
    assert ctx.manifest.workspace_id == original_id


def test_import_preserves_profile(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])

    new_dest = str(tmp_path / "restored")
    runner.invoke(app, ["import", output, "--dest", new_dest])
    storage = LocalFilesystemStorage(new_dest)
    profile = Profile.load(storage)
    assert profile.name == "Test User"


def test_import_nonexistent_zip_fails(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["import", "/nonexistent.zip", "--dest", str(tmp_path / "d")])
    assert result.exit_code != 0


def test_import_existing_dest_fails(workspace_with_profile):
    tmp_path, ws_path, _ = workspace_with_profile
    output = str(tmp_path / "backup.zip")
    runner = CliRunner()
    runner.invoke(app, ["export", "--output", output, "--workspace", ws_path])

    existing_dest = tmp_path / "existing"
    existing_dest.mkdir()
    result = runner.invoke(app, ["import", output, "--dest", str(existing_dest)])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_portability.py -v
```
Expected: `ImportError` or command not found.

- [ ] **Step 3: Implement portability.py**

`careeros/cli/portability.py`:
```python
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich import print as rprint

from careeros.config import GlobalConfig
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace


def export_cmd(
    output: str = typer.Option(None, "--output", "-o", help="Output zip path"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    config = GlobalConfig.load()
    ws_path = workspace or config.workspace_path
    if not ws_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    ws_root = Path(ws_path).expanduser()

    if not output:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output = str(Path.cwd() / f"{ws_root.name}-{date}.zip")

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in ws_root.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(ws_root)
            # skip internal migration receipts but keep .careeros dir structure
            if ".careeros/migrations" in str(rel) and str(rel) != ".careeros/migrations/.keep":
                continue
            zf.write(file_path, rel)

    rprint(f"[green]Exported workspace to {output}[/green]")


def import_workspace_cmd(
    source: str = typer.Argument(..., help="Path to workspace zip"),
    dest: str = typer.Option(..., "--dest", "-d", help="Destination directory (must not exist)"),
) -> None:
    source_path = Path(source).expanduser()
    if not source_path.exists():
        rprint(f"[red]File not found: {source}[/red]")
        raise typer.Exit(1)

    dest_path = Path(dest).expanduser()
    if dest_path.exists():
        rprint(f"[red]Destination already exists: {dest_path}[/red]")
        raise typer.Exit(1)

    dest_path.mkdir(parents=True)
    with zipfile.ZipFile(source_path, "r") as zf:
        zf.extractall(dest_path)

    storage = LocalFilesystemStorage(str(dest_path))
    try:
        open_workspace(storage)
    except Exception as e:
        rprint(f"[red]Restored workspace failed validation: {e}[/red]")
        raise typer.Exit(1)

    rprint(f"[green]Workspace imported to {dest_path}[/green]")
    rprint("Run [bold]careeros workspace validate[/bold] to confirm.")
```

- [ ] **Step 4: Register commands in main.py**

`careeros/cli/main.py`:
```python
import typer
from careeros.cli.onboard import onboard_cmd
from careeros.cli.portability import export_cmd, import_workspace_cmd
from careeros.cli.workspace_cmd import workspace_app

app = typer.Typer(name="careeros", help="CareerOS — your career, your data.")
app.command("onboard")(onboard_cmd)
app.add_typer(workspace_app, name="workspace")
app.command("export")(export_cmd)
app.command("import")(import_workspace_cmd)

if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_portability.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest -v
```
Expected: all tests across all files PASS.

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/portability.py careeros/cli/main.py tests/test_portability.py
git commit -m "feat: add export and import commands for workspace portability"
```

---

## Exit Condition Verification

After all 12 tasks are complete, verify the Phase 1 exit condition manually:

```bash
# Install the package
pip install -e ".[dev]"

# Run onboard (needs ANTHROPIC_API_KEY set)
export ANTHROPIC_API_KEY=<your-key>
careeros onboard

# Inspect the workspace without CareerOS
cat ~/career/profile/profile.json
cat ~/career/manifest.json

# Check status
careeros workspace status

# Validate workspace integrity
careeros workspace validate

# Export the workspace
careeros export --output ~/career-backup.zip

# Verify the zip
python -c "import zipfile; z = zipfile.ZipFile('~/career-backup.zip'); print(z.namelist())"

# Run full test suite
pytest -v
```

All commands should succeed. `profile/profile.json` must contain real extracted data (name, title, years of experience). `manifest.json` must be human-readable. The test suite must report zero failures.
