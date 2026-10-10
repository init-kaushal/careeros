# Career Memory and Evidence Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give CareerOS a structured, provenance-tracked career memory (`career/`), a deterministic importer for `resume.md`, and `careeros check`, an evidence check that every drafting skill must pass before showing a draft.

**Architecture:** A new package `careeros/core/memory/` (lexicon, normalisation, facet extraction, store, operations, importer, checker) built on the Foundation primitives (ids, ledger, workspace lock, atomic writes, validation, verified backups). Two new CLI surfaces (`careeros memory ...`, `careeros check`) sit on top. The six Claude Code drafting skills gain a mandatory "EVIDENCE GATE" section and a new `memory` skill is added.

**Tech Stack:** Python 3.11+, Typer, PyYAML, pytest. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-10-10-career-memory-evidence-design.md` (approved). Task 1 appends the small clarifications that surfaced while planning.

**Branch:** `career-memory` (the spec is already committed there). Do not merge to `main` and do not push without being asked.

## How this plan was verified

Every code block below was written first in a throwaway copy of the repository **outside this repo** and run against the tests in this plan: 456 tests pass there (the 230 existing plus 226 new), on Python 3.14. Python 3.11 was not run there, so Task 8 runs it. The code blocks are therefore the intended implementation, not pseudocode.

If a listed test fails for a reason you can trace to a defect in this plan's code, fix the code, never weaken the test, and say so in your report. If a test fails because the environment differs (a library version, a path), report it instead of working around it.

## Global Constraints

- Python `>=3.11`; **no new dependencies** (PyYAML, Typer and the standard library only).
- Checking is deterministic: no model, no network, no similarity scores. The same draft and memory always give the same answer.
- Fact IDs use the existing prefixes in `careeros/core/ids.py`: `exp`, `ach`, `prj`, `skl`, `edu`, `crt`, `gol`, `pre`, `con`, `evd`, and `prf` for the identity. `utc_now()` from `careeros.core.models` is the only timestamp source.
- Statuses are `claimed | confirmed | verified | disputed | retired`. The importer and `memory add` create `claimed` facts only. `confirm`, `verify`, and editing/disputing/retiring a non-claimed fact need an interactive terminal (the same human-confirmation guard as `careeros approve`, not a security boundary).
- Exit codes: 0 success, 1 failure or findings, 2 usage error or a career memory that cannot be trusted. `careeros check` exits 0 only when every **detected** claim is supported and nothing needs review.
- Every `memory.*` ledger event type is **reserved** (written only by the memory commands). `draft.checked` is not reserved. Every state-changing action and every decline of a confirmation is logged (standing directive).
- A fact the ledger mentions is never physically deleted: retire it. The user's real workspace (`~/Projects/job-search`) is never modified; acceptance runs on a copy.
- The pass wording is exactly: "Passed: all N detected checkable claims (numbers, years, durations, technologies, employers, schools, titles, certifications) are supported by your career memory; M sentence(s) had nothing the checker can evaluate and K supporting fact(s) are claimed rather than confirmed. A pass does not mean every claim in the draft was detected."
- Surgical changes to existing code: the 230 existing tests keep passing at every commit, and migration behaviour is unchanged.

## Review Focus

Inputs the spec implies that are most likely to bite a real user, each pinned by a named test:

1. **A resume layout the importer cannot place** must be a clear error that writes nothing, never a silent drop: `test_structure_the_grammar_cannot_place_is_a_hard_error`, `test_a_hard_error_writes_nothing`, and the "Not imported" report (Task 5).
2. **A fabricated name that merely begins like a real one** ("Acme Systems" vs "Acme Corp") must not ride on the real employer: `test_a_longer_name_that_starts_like_a_known_one_is_not_the_known_one` (Task 4).
3. **An allowed term used as a shortcut around the evidence gate** must stay an unverified mention: `test_an_allowed_technology_cannot_back_an_unsupported_achievement`, `test_allowing_a_number_does_not_make_it_supported` (Task 4).
4. **Markdown, links, code fences, Windows line endings and non-ASCII text** in a draft or resume: `test_markdown_is_stripped_before_checking`, `test_windows_line_endings_are_handled`, `test_non_ascii_text_and_names_round_trip`, `test_windows_line_endings_in_the_resume_are_handled` (Tasks 4 and 5).
5. **Empty input fails closed**: an empty memory (`CHK030`) and an empty draft (exit 2, not a pass): `test_an_empty_memory_fails_closed`, `test_an_empty_draft_is_a_usage_error_not_a_pass` (Tasks 4 and 6).

---

## File Structure

New:

| Path | Responsibility |
|---|---|
| `careeros/core/backup.py` | Verified backup folders and manifests (extracted from migration, behaviour unchanged) |
| `careeros/data/lexicon.yaml` | Bundled vocabulary: technologies, role words, certifications, stoplist |
| `careeros/core/memory/lexicon.py` | Load the lexicon, find technologies in text |
| `careeros/core/memory/normalize.py` | Name normalisation; extract numbers, years, durations |
| `careeros/core/memory/facets.py` | Technologies and metrics a piece of text claims |
| `careeros/core/memory/store.py` | Fact files, loading and validation (`MEM001`-`MEM008`), sources, transactions |
| `careeros/core/memory/ops.py` | add / update / confirm / verify / dispute / retire |
| `careeros/core/memory/check.py` | The evidence check |
| `careeros/core/memory/importer.py` | Parse `resume.md`, diff, apply |
| `careeros/cli/memory.py`, `careeros/cli/check.py` | The commands |
| `.claude/skills/memory/SKILL.md` (under `careeros/templates/workspace/`) | The memory skill |
| `docs/career-memory.md` | User documentation |
| `tests/test_core_backup.py`, `test_memory_normalize.py`, `test_memory_store.py`, `test_memory_check.py`, `test_memory_importer.py`, `test_cli_memory.py`, `test_skills_evidence.py`, `test_memory_e2e.py`, `tests/fixtures/resume_sample.md` | Tests |

Modified: `careeros/core/migration.py` (use the extracted helpers), `careeros/core/ledger.py` (reserve `memory.*`), `careeros/core/validation.py` (memory rules, `LED003` for memory ids), `careeros/cli/main.py`, six skills + `onboard` + `CLAUDE.md` in `careeros/templates/workspace/`, `tests/helpers.py`, `tests/conftest.py`, `mkdocs.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml`, `docs/END_TO_END_TEST.md`, the spec (an appendix).

Task order is dependency order: backup, vocabulary, store and operations, checker, importer, CLI, skills, docs and acceptance.

---
### Task 1: Extract the backup helpers and record the spec clarifications

**Files:**
- Create: `careeros/core/backup.py`, `tests/test_core_backup.py`
- Modify: `careeros/core/migration.py`, `docs/superpowers/specs/2026-10-10-career-memory-evidence-design.md`

**Interfaces:**
- Produces (`careeros/core/backup.py`): `class BackupError(WorkspaceError)`; `sha256(data: bytes) -> str`; `new_backup_dir(root: Path, kind: str) -> Path`; `write_manifest(backup: Path, manifest: dict, write=atomic_write_bytes) -> None`; `backup_files(backup: Path, items: list[tuple[str, bytes]], write=atomic_write_bytes) -> None` (raises `BackupError("backup verification failed for <rel>; nothing was changed")`).
- Consumes: `careeros.core.models.utc_now`, `careeros.core.workspace.{BACKUPS_REL, WorkspaceError, atomic_write_bytes}`.
- `migration.py` keeps its private `_sha`, `_new_backup_dir`, `_write_manifest`, `_backup_files` as thin wrappers. They pass **migration's own** `atomic_write_bytes` so the existing tests that monkeypatch `mig.atomic_write_bytes` still intercept backup writes, and `_backup_files` re-raises `MigrationError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_core_backup.py`:

````python
"""Verified backups and manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from careeros.core import backup


def test_new_backup_dirs_never_collide(tmp_path: Path, clock) -> None:
    first = backup.new_backup_dir(tmp_path, "kind")
    second = backup.new_backup_dir(tmp_path, "kind")
    assert first != second and first.is_dir() and second.is_dir()


def test_backup_files_copies_and_verifies(tmp_path: Path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    backup.backup_files(folder, [("a/b.txt", b"hello")])
    assert (folder / "a" / "b.txt").read_bytes() == b"hello"


def test_a_corrupted_copy_is_caught(tmp_path: Path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    from careeros.core.workspace import atomic_write_bytes

    def corrupting(path: Path, data: bytes) -> None:
        atomic_write_bytes(path, data + b"x")

    with pytest.raises(backup.BackupError, match="backup verification failed for a.txt"):
        backup.backup_files(folder, [("a.txt", b"hello")], write=corrupting)


def test_manifest_is_written_as_json(tmp_path: Path) -> None:
    backup.write_manifest(tmp_path, {"status": "pending"})
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"status": "pending"}
    assert backup.sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
````

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_core_backup.py -q`
Expected: collection error, `ImportError: cannot import name 'backup' from 'careeros.core'`.

- [ ] **Step 3: Create `careeros/core/backup.py`**

````python
"""Verified backups and manifests, shared by every operation that rewrites workspace files."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from careeros.core import models
from careeros.core.workspace import BACKUPS_REL, WorkspaceError, atomic_write_bytes


class BackupError(WorkspaceError):
    """A backup could not be written or verified."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_backup_dir(root: Path, kind: str) -> Path:
    stamp = models.utc_now().replace("-", "").replace(":", "")
    base = Path(root) / BACKUPS_REL
    candidate = base / f"{stamp}-{kind}"
    n = 2
    while candidate.exists():
        candidate = base / f"{stamp}-{kind}-{n}"
        n += 1
    candidate.mkdir(parents=True)
    return candidate


def write_manifest(backup: Path, manifest: dict, write: Callable[[Path, bytes], None] = atomic_write_bytes) -> None:
    write(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))


def backup_files(
    backup: Path, items: list[tuple[str, bytes]], write: Callable[[Path, bytes], None] = atomic_write_bytes
) -> None:
    """Copy each (relative path, bytes) into the backup folder and read it back to prove it is intact."""
    for rel, data in items:
        destination = backup / rel
        write(destination, data)
        if sha256(destination.read_bytes()) != sha256(data):
            raise BackupError(f"backup verification failed for {rel}; nothing was changed")
````

- [ ] **Step 4: Make `migration.py` use it, with no behaviour change**

In `careeros/core/migration.py`, delete the line `import hashlib` (the only user was `_sha`), and replace the import line `from careeros.core import ids, ledger, models, versions` with:

````python
from careeros.core import backup as backup_helpers
from careeros.core import ids, ledger, models, versions
````

Replace this block (from `def _sha` up to, not including, `def _detect_runtimes`):

````python
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
````

with:

````python
def _sha(data: bytes) -> str:
    return backup_helpers.sha256(data)


def _new_backup_dir(root: Path, kind: str) -> Path:
    return backup_helpers.new_backup_dir(root, kind)


def _write_manifest(backup: Path, manifest: dict) -> None:
    backup_helpers.write_manifest(backup, manifest, write=atomic_write_bytes)


def _backup_files(backup: Path, items: list[tuple[str, bytes]]) -> None:
    try:
        backup_helpers.backup_files(backup, items, write=atomic_write_bytes)
    except backup_helpers.BackupError as exc:
        raise MigrationError(str(exc)) from exc
````

- [ ] **Step 5: Append the spec clarifications**

Append this section to the end of `docs/superpowers/specs/2026-10-10-career-memory-evidence-design.md`:

````markdown

## 18. Clarifications made while writing the plan (2026-10-10)

These came from reading the real `resume.md` layout and from building the code. They narrow or complete the approved text; none weakens a guarantee.

1. **§6.1 importer grammar follows the real layout.** Under an experience, a line starting `Tech:`, `Technologies:`, `Tech stack:` or `Stack:` fills that experience's `technologies` and is not an achievement. A project starts at a `### Name` heading or a `**Name**` line; its first prose line is its text and later bullets are achievements. A school is `**School, City** - Month YYYY` followed by a degree line `Degree | detail`. Skills accept `**Category:** a, b` and a parenthesised group (`AWS (Lambda, EC2)` makes three skills). Lines before the first `##` are reported as `preamble`, never dropped silently. A repeated line is imported once and reported.
2. **§4.1 optional keys.** Imported facts carry `import_key` (the importer's match key); a fact whose source line has left the resume carries `stale: true`; achievements carry `section`.
3. **§6.4 no-op rule.** An import is a no-op only when the source hash is unchanged **and** the number of facts awaiting review is unchanged, so a pending review is never hidden.
4. **§7 events.** Declining a confirmation prompt is logged as `memory.declined`. Every event type starting `memory.` is reserved. In the CLI, `memory add --kind evidence` infers the evidence kind (`document` from `--set path=...`, otherwise `link`).
5. **§8.1 detection details.** Certification aliases from the lexicon and the certifications in memory are matched anywhere. A known employer or school followed by another capitalised word is a different name ("Acme Systems" is not "Acme Corp"). Greeting and sign-off lines are not scanned for unknown names. The employer cues use "at" and "for", not "with" ("worked with Zorbix" is a collaboration, so it raises `CHK009`, not `CHK004`). A resume-style header `Name | Role` is checked as employer and title.
6. **§8.5 empty draft.** An empty draft is a usage error (exit 2), not a pass.
````

- [ ] **Step 6: Run everything**

Run: `.venv/bin/python -m pytest -q`
Expected: `234 passed` (the 230 existing tests, unchanged, plus 4 new).

- [ ] **Step 7: Commit**

````bash
git add careeros/core/backup.py careeros/core/migration.py tests/test_core_backup.py docs/superpowers/specs/2026-10-10-career-memory-evidence-design.md
git commit -m "core: extract verified backup helpers; record spec clarifications" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 2: Vocabulary, normalisation and facet extraction

**Files:**
- Create: `careeros/data/lexicon.yaml`, `careeros/core/memory/__init__.py`, `careeros/core/memory/lexicon.py`, `careeros/core/memory/normalize.py`, `careeros/core/memory/facets.py`, `tests/test_memory_normalize.py`

**Interfaces:**
- Produces (`lexicon.py`): `LexiconError`; `TechSpan(canonical, start, end)`; `Lexicon` with `.find_techs(text) -> list[TechSpan]`, `.canonical_tech(name) -> str | None`, `.with_extra_techs(names) -> Lexicon`, and attributes `role_nouns`, `seniority`, `title_words`, `org_suffixes`, `stoplist`, `certs` (lower alias to canonical), `cert_aliases` (list of `(alias, canonical)`); `load_lexicon(root: Path | None = None) -> Lexicon` (bundled plus `<root>/career/lexicon.yaml` additions); `BUNDLED` (path of the bundled file).
- Produces (`normalize.py`): `norm_text(s)`, `norm_org(s, suffixes)`, `metric_key(value, unit) -> (float, str)`, `split_sentences(line) -> list[str]`, `scan(text, skip=None) -> Scan(numbers, years, durations)` with `NumberAtom(raw, value, unit, plus, start, end).metric()`, `YearAtom(year, start, end)`, `DurationAtom(years, plus, start, end)`.
- Produces (`facets.py`): `analyze(text, lexicon) -> Analysis(techs, scan, version_spans)`; `derive_facets(text, lexicon) -> {"metrics": [...], "technologies": [...]}`.
- Consumes: `careeros.core.workspace.WorkspaceError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_normalize.py`:

````python
"""Number, year and duration extraction, name normalisation and the lexicon."""

from __future__ import annotations

from pathlib import Path

import pytest

from careeros.core.memory import facets
from careeros.core.memory.lexicon import LexiconError, load_lexicon
from careeros.core.memory.normalize import norm_org, norm_text, scan, split_sentences

LX = load_lexicon()


def numbers(text: str) -> list[tuple[float, str, bool]]:
    return [(n.value, n.unit, n.plus) for n in scan(text).numbers]


@pytest.mark.parametrize("text,expected", [
    ("35%", [(35.0, "percent", False)]),
    ("35 percent", [(35.0, "percent", False)]),
    ("99.95% uptime", [(99.95, "percent", False)]),
    ("1M+ users", [(1_000_000.0, "count", True)]),
    ("5,000 requests", [(5000.0, "count", False)]),
    ("1,00,000 users", [(100000.0, "count", False)]),
    ("$2.4M ARR", [(2_400_000.0, "currency:USD", False)]),
    ("₹5 crore", [(50_000_000.0, "currency:INR", False)]),
    ("10x faster", [(10.0, "multiplier", False)]),
    ("CGPA 7.7/10", [(0.77, "score", False)]),
    ("40+ services", [(40.0, "count", True)]),
    ("2 million events", [(2_000_000.0, "count", False)]),
])
def test_numbers(text: str, expected: list) -> None:
    assert numbers(text) == expected


@pytest.mark.parametrize("text", [
    "S3 and EC2 and p99", "Call +91 80032 93018", "https://example.com/2031/45", "a@b.com 12345678",
    "on-call 24/7", "v1.2.3 released", "6/2022", "a 4-year degree", "3 years ago",
])
def test_things_that_are_not_claims_are_not_numbers(text: str) -> None:
    assert numbers(text) == []


def test_years_and_ranges() -> None:
    found = scan("Jan 2024 - Present, 2021–2023, since 2019, and 2000 users")
    assert [y.year for y in found.years] == [2024, 2021, 2023, 2019]
    assert [(n.value, n.unit) for n in found.numbers] == [(2000.0, "count")]


def test_durations() -> None:
    found = scan("7+ years of experience, a 5-year plan and 3 years ago")
    assert [(d.years, d.plus) for d in found.durations] == [(7, True), (5, False)]


def test_versions_after_a_technology_are_not_numbers() -> None:
    analysis = facets.analyze("Used Python 3.11 and Go 1.22 for 3x speedups", LX)
    assert [(n.value, n.unit) for n in analysis.scan.numbers] == [(3.0, "multiplier")]
    assert [t.canonical for t in analysis.techs] == ["Python", "Go"]


def test_derive_facets() -> None:
    derived = facets.derive_facets("Reduced AWS costs by 35% using Kafka and k8s.", LX)
    assert derived["technologies"] == ["AWS", "Kafka", "Kubernetes"]
    assert derived["metrics"] == [{"raw": "35%", "value": 35.0, "unit": "percent", "plus": False}]


def test_name_normalisation() -> None:
    assert norm_text("Procter & Gamble") == "procter and gamble"
    assert norm_org("Eka Care Pvt. Ltd.", LX.org_suffixes) == "eka care"
    assert norm_org("Ltd", LX.org_suffixes) == "ltd"
    assert split_sentences("Built X. Cut 3.5% with Node.js. Done!") == ["Built X.", "Cut 3.5% with Node.js.", "Done!"]


@pytest.mark.parametrize("text,expected", [
    ("Used golang and k8s with Postgres", ["Go", "Kubernetes", "PostgreSQL"]),
    ("C++, C# and C", ["C++", "C#", "C"]),
    ("Node.js beats Node", ["Node.js", "Node.js"]),
    ("I will go ahead; Rust is fun; rust never sleeps", ["Rust"]),
    ("R rules", ["R"]),
    ("Ran spark jobs on Spark", ["Spark"]),
])
def test_technology_recognition(text: str, expected: list[str]) -> None:
    assert [t.canonical for t in LX.find_techs(text)] == expected


def test_user_additions_extend_the_lexicon(tmp_path: Path) -> None:
    (tmp_path / "career").mkdir()
    (tmp_path / "career" / "lexicon.yaml").write_text("technologies:\n  - {name: Terragrunt, aliases: [tg]}\n", encoding="utf-8")
    lexicon = load_lexicon(tmp_path)
    assert [t.canonical for t in lexicon.find_techs("We use tg daily")] == ["Terragrunt"]
    assert lexicon.with_extra_techs(["Quasar"]).canonical_tech("quasar") == "Quasar"


def test_a_broken_user_lexicon_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "career").mkdir()
    (tmp_path / "career" / "lexicon.yaml").write_text("technologies:\n  - {aliases: [x]}\n", encoding="utf-8")
    with pytest.raises(LexiconError, match="needs a name"):
        load_lexicon(tmp_path)
    (tmp_path / "career" / "lexicon.yaml").write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(LexiconError, match="mapping"):
        load_lexicon(tmp_path)
````

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_memory_normalize.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'careeros.core.memory'`.

- [ ] **Step 3: Create the bundled vocabulary**

Create `careeros/data/lexicon.yaml` (the wheel build already includes every file under the `careeros` package):

````yaml
# Bundled vocabulary for `careeros check` and `careeros memory import`.
# Add your own terms in career/lexicon.yaml (additions only).

# name: canonical spelling. aliases: other spellings. case_sensitive: aliases that must match exactly
# (short or ordinary-looking words such as Go, R, Rust, Spark).
technologies:
  - {name: Python}
  - {name: Go, aliases: [golang], case_sensitive: [Go]}
  - {name: Java}
  - {name: JavaScript, aliases: [Javascript]}
  - {name: TypeScript, aliases: [Typescript]}
  - {name: C++, aliases: [cpp]}
  - {name: C#, aliases: [csharp]}
  - {name: C, case_sensitive: [C]}
  - {name: R, case_sensitive: [R]}
  - {name: Rust, case_sensitive: [Rust]}
  - {name: Swift, case_sensitive: [Swift]}
  - {name: Kotlin}
  - {name: Scala}
  - {name: Ruby}
  - {name: PHP}
  - {name: Perl}
  - {name: Elixir}
  - {name: Erlang}
  - {name: Bash, aliases: [shell scripting]}
  - {name: SQL}
  - {name: Node.js, aliases: [Node, NodeJS, Nodejs]}
  - {name: React, aliases: [ReactJS, React.js]}
  - {name: Angular}
  - {name: Vue, aliases: [Vue.js, VueJS]}
  - {name: Next.js, aliases: [NextJS]}
  - {name: Django}
  - {name: Flask, case_sensitive: [Flask]}
  - {name: FastAPI}
  - {name: Spring Boot, aliases: [Spring]}
  - {name: Rails, aliases: [Ruby on Rails]}
  - {name: .NET, aliases: [dotnet]}
  - {name: gRPC}
  - {name: GraphQL}
  - {name: Protobuf, aliases: [Protocol Buffers, protobufs]}
  - {name: Kubernetes, aliases: [k8s, kube]}
  - {name: Docker}
  - {name: Helm}
  - {name: Istio}
  - {name: Envoy, case_sensitive: [Envoy]}
  - {name: Cilium}
  - {name: Calico, case_sensitive: [Calico]}
  - {name: OpenShift}
  - {name: Rancher}
  - {name: Nomad, case_sensitive: [Nomad]}
  - {name: Terraform}
  - {name: Pulumi}
  - {name: Ansible}
  - {name: Chef, case_sensitive: [Chef]}
  - {name: Puppet, case_sensitive: [Puppet]}
  - {name: Packer, case_sensitive: [Packer]}
  - {name: Crossplane}
  - {name: ArgoCD, aliases: [Argo CD, Argo]}
  - {name: Flux, case_sensitive: [Flux]}
  - {name: Jenkins}
  - {name: GitHub Actions}
  - {name: GitLab CI}
  - {name: CircleCI}
  - {name: TeamCity}
  - {name: Git, case_sensitive: [Git]}
  - {name: GitHub}
  - {name: GitLab}
  - {name: Bitbucket}
  - {name: Linux}
  - {name: Nginx, aliases: [NGINX]}
  - {name: HAProxy}
  - {name: AWS, aliases: [Amazon Web Services]}
  - {name: GCP, aliases: [Google Cloud, Google Cloud Platform]}
  - {name: Azure}
  - {name: EC2}
  - {name: S3, aliases: [Amazon S3]}
  - {name: EKS}
  - {name: ECS}
  - {name: RDS}
  - {name: SQS}
  - {name: SNS}
  - {name: Lambda, aliases: [AWS Lambda], case_sensitive: [Lambda]}
  - {name: DynamoDB}
  - {name: CloudFormation}
  - {name: CloudWatch}
  - {name: BigQuery}
  - {name: PostgreSQL, aliases: [Postgres, psql]}
  - {name: MySQL}
  - {name: MariaDB}
  - {name: SQLite}
  - {name: MongoDB, aliases: [Mongo]}
  - {name: Redis}
  - {name: Memcached}
  - {name: Cassandra}
  - {name: Elasticsearch, aliases: [Elastic Search, OpenSearch]}
  - {name: Kibana}
  - {name: Logstash}
  - {name: Snowflake}
  - {name: Kafka, aliases: [Apache Kafka]}
  - {name: RabbitMQ}
  - {name: NATS, case_sensitive: [NATS]}
  - {name: IBM MQ, aliases: [MQ], case_sensitive: [MQ]}
  - {name: Flink, aliases: [Apache Flink]}
  - {name: Spark, aliases: [Apache Spark, PySpark], case_sensitive: [Spark]}
  - {name: Hadoop}
  - {name: Airflow, aliases: [Apache Airflow]}
  - {name: Prometheus}
  - {name: Grafana}
  - {name: Loki, case_sensitive: [Loki]}
  - {name: Thanos}
  - {name: Jaeger}
  - {name: OpenTelemetry, aliases: [otel, OTel]}
  - {name: Datadog}
  - {name: Splunk}
  - {name: New Relic}
  - {name: PagerDuty}
  - {name: Sentry, case_sensitive: [Sentry]}
  - {name: Fluentd}
  - {name: Vault, aliases: [HashiCorp Vault], case_sensitive: [Vault]}
  - {name: Consul, case_sensitive: [Consul]}
  - {name: PyTorch}
  - {name: TensorFlow}
  - {name: NumPy}
  - {name: Jira}
  - {name: Confluence}

role_nouns: [engineer, developer, programmer, architect, manager, director, consultant, analyst, scientist, administrator, specialist, officer, designer, lead, intern, founder, cofounder, president, owner, partner]
# A capitalized run containing one of these words (plus a role noun) is treated as a job title even without a cue.
seniority: [senior, sr, staff, principal, lead, head, director, vp, vice, chief, junior, jr, associate, distinguished, fellow, intern, cto, ceo, cio, coo, cfo]
# Words that make a run a title on their own, even with no role noun (Head of Platform, VP Engineering).
title_words: [head, vp, vice, chief, cto, ceo, cio, coo, cfo]
org_suffixes: [inc, incorporated, ltd, limited, llc, llp, lp, corp, corporation, co, company, gmbh, ag, plc, pvt, private, pte, sa, bv, nv, oy, ab, srl]

certifications:
  - {name: AWS Certified Solutions Architect, aliases: [AWS CSA, AWS Solutions Architect]}
  - {name: AWS Certified Developer}
  - {name: AWS Certified SysOps Administrator}
  - {name: AWS Certified DevOps Engineer}
  - {name: Certified Kubernetes Administrator, aliases: [CKA]}
  - {name: Certified Kubernetes Application Developer, aliases: [CKAD]}
  - {name: Certified Kubernetes Security Specialist, aliases: [CKS]}
  - {name: CISSP}
  - {name: PMP}
  - {name: OSCP}
  - {name: CCNA}
  - {name: ITIL}
  - {name: HashiCorp Certified Terraform Associate, aliases: [Terraform Associate]}
  - {name: Google Professional Cloud Architect}
  - {name: Azure Administrator Associate, aliases: [AZ-104]}
  - {name: CompTIA Security+}
  - {name: Certified Scrum Master, aliases: [CSM]}

# Capitalized words that are not names of employers, schools or titles. Compared case-insensitively.
stoplist:
  - I
  - Dear
  - Hi
  - Hello
  - Hey
  - Thanks
  - Thank
  - Regards
  - Best
  - Sincerely
  - Cheers
  - Yours
  - Kind
  - Warm
  - Monday
  - Tuesday
  - Wednesday
  - Thursday
  - Friday
  - Saturday
  - Sunday
  - January
  - February
  - March
  - April
  - May
  - June
  - July
  - August
  - September
  - October
  - November
  - December
  - Jan
  - Feb
  - Mar
  - Apr
  - Jun
  - Jul
  - Aug
  - Sep
  - Sept
  - Oct
  - Nov
  - Dec
  - Present
  - Summary
  - Profile
  - Experience
  - Education
  - Projects
  - Project
  - Skills
  - Certifications
  - Contact
  - Resume
  - Objective
  - Awards
  - Publications
  - Languages
  - Interests
  - References
  - Technologies
  - Achievements
  - Responsibilities
  - English
  - Hindi
  - French
  - German
  - Spanish
  - Mandarin
  - Japanese
  - India
  - Singapore
  - Germany
  - Canada
  - Australia
  - Netherlands
  - Ireland
  - Sweden
  - Switzerland
  - Japan
  - China
  - France
  - Spain
  - UAE
  - UK
  - USA
  - US
  - EU
  - London
  - Berlin
  - Bengaluru
  - Bangalore
  - Mumbai
  - Delhi
  - Hyderabad
  - Pune
  - Chennai
  - Dubai
  - Toronto
  - Sydney
  - Amsterdam
  - Dublin
  - Stockholm
  - Zurich
  - Paris
  - Tokyo
  - Remote
  - API
  - APIs
  - CI
  - CD
  - SLA
  - SLO
  - SLI
  - SLAs
  - SLOs
  - SRE
  - SaaS
  - PaaS
  - IaaS
  - SDK
  - UI
  - UX
  - AI
  - ML
  - LLM
  - LLMs
  - QA
  - KPI
  - KPIs
  - OKR
  - OKRs
  - ETL
  - CLI
  - REST
  - HTTP
  - HTTPS
  - JSON
  - YAML
  - TCP
  - UDP
  - DNS
  - TLS
  - SSL
  - VPC
  - IAM
  - RBAC
  - GitOps
  - DevOps
  - DevSecOps
  - MLOps
  - FinOps
  - IaC
  - TDD
  - CPU
  - GPU
  - RAM
  - OS
  - IT
  - HR
  - PR
  - PRs
  - MVP
  - POC
  - B2B
  - B2C
  - CTO
  - CEO
  - VP
````

Create `careeros/core/memory/__init__.py`:

````python
"""Career memory: structured, provenance-tracked facts and the evidence check that guards drafts."""
````

Create `careeros/core/memory/lexicon.py`:

````python
"""The vocabulary the importer and the checker recognise: technologies, role words, certifications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from careeros.core.workspace import WorkspaceError

BUNDLED = Path(__file__).resolve().parents[2] / "data" / "lexicon.yaml"
_TECH_KEYS = {"name", "aliases", "case_sensitive"}


class LexiconError(WorkspaceError):
    """A lexicon file cannot be used."""


@dataclass(frozen=True)
class TechSpan:
    canonical: str
    start: int
    end: int


class Lexicon:
    def __init__(self, data: dict) -> None:
        self.role_nouns = frozenset(w.lower() for w in data.get("role_nouns", ()))
        self.seniority = frozenset(w.lower() for w in data.get("seniority", ()))
        self.title_words = frozenset(w.lower() for w in data.get("title_words", ()))
        self.org_suffixes = frozenset(w.lower() for w in data.get("org_suffixes", ()))
        self.stoplist = frozenset(w.lower() for w in data.get("stoplist", ()))
        self._tech_defs: list[dict] = list(data.get("technologies", ()))
        self._cert_defs: list[dict] = list(data.get("certifications", ()))
        self._data = data
        self._fold: dict[str, str] = {}      # lower-cased alias -> canonical (case-insensitive aliases)
        self._exact: dict[str, str] = {}     # alias -> canonical (aliases that must match exactly)
        for entry in self._tech_defs:
            canonical = entry["name"]
            sensitive = set(entry.get("case_sensitive", ()))
            for alias in [canonical, *entry.get("aliases", ())]:
                if alias in sensitive:
                    self._exact[alias] = canonical
                else:
                    self._fold[alias.lower()] = canonical
        self.certs: dict[str, str] = {}      # lower-cased alias -> canonical
        self.cert_aliases: list[tuple[str, str]] = []  # (alias as written, canonical)
        for entry in self._cert_defs:
            for alias in [entry["name"], *entry.get("aliases", ())]:
                self.certs[alias.lower()] = entry["name"]
                self.cert_aliases.append((alias, entry["name"]))
        self._fold_re = self._compile(sorted(self._fold, key=len, reverse=True), re.IGNORECASE)
        self._exact_re = self._compile(sorted(self._exact, key=len, reverse=True), 0)

    @staticmethod
    def _compile(aliases: list[str], flags: int) -> re.Pattern | None:
        if not aliases:
            return None
        body = "|".join(re.escape(a) for a in aliases)
        return re.compile(rf"(?<![A-Za-z0-9_])(?:{body})(?![A-Za-z0-9_])", flags)

    def canonical_tech(self, name: str) -> str | None:
        name = name.strip()
        return self._exact.get(name) or self._fold.get(name.lower())

    def find_techs(self, text: str) -> list[TechSpan]:
        found: list[TechSpan] = []
        for pattern, lookup in ((self._fold_re, lambda s: self._fold[s.lower()]), (self._exact_re, lambda s: self._exact[s])):
            if pattern is None:
                continue
            for match in pattern.finditer(text):
                found.append(TechSpan(lookup(match.group(0)), match.start(), match.end()))
        found.sort(key=lambda s: (s.start, -(s.end - s.start)))
        result: list[TechSpan] = []
        for span in found:
            if result and span.start < result[-1].end:
                continue  # overlaps a longer or earlier match
            result.append(span)
        return result

    def with_extra_techs(self, names: list[str]) -> "Lexicon":
        known = {n.lower() for n in self._fold} | set(self._exact)
        extra = [{"name": n} for n in dict.fromkeys(names) if n and n.lower() not in known]
        if not extra:
            return self
        data = dict(self._data)
        data["technologies"] = [*self._tech_defs, *extra]
        return Lexicon(data)


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise LexiconError(f"{path.name} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise LexiconError(f"{path.name} must be a mapping")
    return data


def _check_tech(entry: object, where: str) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise LexiconError(f"{where}: every technology needs a name")
    unknown = set(entry) - _TECH_KEYS
    if unknown:
        raise LexiconError(f"{where}: unknown key(s) {', '.join(sorted(unknown))} for {entry['name']}")


def load_lexicon(root: Path | None = None) -> Lexicon:
    """The bundled lexicon plus the additions in <workspace>/career/lexicon.yaml, if present."""
    data = _read(BUNDLED)
    if root is not None:
        extra_path = Path(root) / "career" / "lexicon.yaml"
        if extra_path.is_file():
            extra = _read(extra_path)
            for entry in extra.get("technologies", ()):
                _check_tech(entry, "career/lexicon.yaml")
            for key in ("technologies", "certifications"):
                data[key] = [*data.get(key, ()), *extra.get(key, ())]
            for key in ("role_nouns", "seniority", "title_words", "org_suffixes", "stoplist"):
                data[key] = [*data.get(key, ()), *extra.get(key, ())]
    return Lexicon(data)
````

Create `careeros/core/memory/normalize.py`:

````python
"""Deterministic text normalisation and number/year/duration extraction. No models, no network."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCALE = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
    "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7,
}
_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_SCALE_WORDS = r"k|mm|m|bn|b|thousand|million|billion|lakhs?|crores?"
_YEAR = re.compile(r"^(19[5-9]\d|20\d\d|2100)$")
_YEAR_CONNECTORS = {
    "to", "and", "through", "until", "till", "as", "when", "while", "i", "we", "he", "she", "they", "it",
    "at", "in", "on", "for", "with", "my", "the", "a", "an", "or", "by", "from", "was", "is",
}
_MASK = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|\+\d[\d\s().-]{7,}\d")
_VERSION = re.compile(r"\bv?\d+(?:\.\d+){2,}\b")
_DURATION = re.compile(r"(?P<n>\d+)(?P<plus>\+)?[\s-]{0,2}years?\b", re.I)
_NOT_EXPERIENCE = re.compile(r"\s+(?:old|ago|degree|program|course|bachelor|master)\b", re.I)
_NUMTXT = r"\d+(?:,\d{2,3})*(?:\.\d+)?(?!\d)"
_NUMBER = re.compile(
    r"(?P<cur>[$€£₹])\s?(?P<cnum>" + _NUMTXT + r")\s?(?P<cscale>" + _SCALE_WORDS + r")?(?![A-Za-z])(?P<cplus>\+)?"
    r"|(?<![\d/])(?P<snum>\d+(?:\.\d+)?)\s?/\s?(?P<sden>\d+(?:\.\d+)?)(?![\d/])"
    r"|(?P<pnum>" + _NUMTXT + r")\s?(?P<pct>%|percent\b|per\s?cent\b)"
    r"|(?P<mnum>\d+(?:\.\d+)?)\s?[x×](?![A-Za-z0-9])"
    r"|(?<![\w.+])(?P<num>" + _NUMTXT + r")\s?(?P<scale>" + _SCALE_WORDS + r")?(?![A-Za-z0-9])(?P<plus>\+)?",
    re.I,
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True)
class NumberAtom:
    raw: str
    value: float
    unit: str       # percent | count | multiplier | score | currency:<code>
    plus: bool
    start: int
    end: int

    def metric(self) -> dict:
        return {"raw": self.raw, "value": self.value, "unit": self.unit, "plus": self.plus}


@dataclass(frozen=True)
class YearAtom:
    year: int
    start: int
    end: int


@dataclass(frozen=True)
class DurationAtom:
    years: int
    plus: bool
    start: int
    end: int


@dataclass
class Scan:
    numbers: list[NumberAtom]
    years: list[YearAtom]
    durations: list[DurationAtom]


def norm_text(value: str) -> str:
    """Case-folded, punctuation-free, '&' = 'and', single-spaced."""
    value = value.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def norm_org(value: str, suffixes: frozenset[str]) -> str:
    tokens = norm_text(value).split()
    while len(tokens) > 1 and tokens[-1] in suffixes:
        tokens.pop()
    return " ".join(tokens)


def metric_key(value: float, unit: str) -> tuple[float, str]:
    return (round(float(value), 6), unit)


def split_sentences(line: str) -> list[str]:
    return [part.strip() for part in SENTENCE_END.split(line) if part.strip()]


def _to_float(text: str) -> float:
    return float(text.replace(",", ""))


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


def scan(text: str, skip: list[tuple[int, int]] | None = None) -> Scan:
    """Find numbers, years and durations. `skip` lists spans (such as technology versions) to ignore."""
    blocked: list[tuple[int, int]] = list(skip or ())
    blocked.extend(m.span() for m in _MASK.finditer(text))
    blocked.extend(m.span() for m in _VERSION.finditer(text))
    result = Scan([], [], [])
    for m in _DURATION.finditer(text):
        if _overlaps(m.start(), m.end(), blocked):
            continue
        blocked.append(m.span())
        if not _NOT_EXPERIENCE.match(text[m.end():]):
            result.durations.append(DurationAtom(int(m.group("n")), bool(m.group("plus")), m.start(), m.end()))
    for m in _NUMBER.finditer(text):
        if _overlaps(m.start(), m.end(), blocked):
            continue
        raw = m.group(0).strip()
        if m.group("cur"):
            value = _to_float(m.group("cnum")) * _SCALE.get((m.group("cscale") or "").lower(), 1)
            atom = NumberAtom(raw, value, f"currency:{_CURRENCY[m.group('cur')]}", bool(m.group("cplus")), m.start(), m.end())
        elif m.group("snum"):
            num, den = float(m.group("snum")), float(m.group("sden"))
            if den == 0 or num > den or den > 1000:
                continue
            atom = NumberAtom(raw, num / den, "score", False, m.start(), m.end())
        elif m.group("pnum"):
            atom = NumberAtom(raw, _to_float(m.group("pnum")), "percent", False, m.start(), m.end())
        elif m.group("mnum"):
            atom = NumberAtom(raw, float(m.group("mnum")), "multiplier", False, m.start(), m.end())
        else:
            digits = m.group("num")
            if "," not in digits and "." not in digits and len(digits) >= 7:
                continue  # phone numbers, ids
            scale = (m.group("scale") or "").lower()
            if not scale and _YEAR.match(digits):
                follower = re.match(r"\s+([a-z]+)", text[m.end():])
                if not follower or follower.group(1) in _YEAR_CONNECTORS:
                    result.years.append(YearAtom(int(digits), m.start(), m.end()))
                    continue
            atom = NumberAtom(raw, _to_float(digits) * _SCALE.get(scale, 1), "count", bool(m.group("plus")), m.start(), m.end())
        result.numbers.append(atom)
    return result
````

Create `careeros/core/memory/facets.py`:

````python
"""What a piece of text claims: its technologies and its numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from careeros.core.memory.lexicon import Lexicon, TechSpan
from careeros.core.memory.normalize import Scan, scan

_VERSION_AFTER_TECH = re.compile(r"\s?v?\d+(?:\.\d+)*(?![\w%])")


@dataclass
class Analysis:
    techs: list[TechSpan]
    scan: Scan
    version_spans: list[tuple[int, int]]


def analyze(text: str, lexicon: Lexicon) -> Analysis:
    techs = lexicon.find_techs(text)
    versions: list[tuple[int, int]] = []
    for span in techs:
        match = _VERSION_AFTER_TECH.match(text, span.end)
        if match and match.end() > match.start():
            versions.append((span.end, match.end()))
    return Analysis(techs, scan(text, skip=versions), versions)


def derive_facets(text: str, lexicon: Lexicon) -> dict:
    """The `metrics` and `technologies` fields for a fact whose claim is `text`."""
    analysis = analyze(text, lexicon)
    return {
        "metrics": [n.metric() for n in analysis.scan.numbers],
        "technologies": list(dict.fromkeys(t.canonical for t in analysis.techs)),
    }
````

- [ ] **Step 4: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/test_memory_normalize.py -q`
Expected: `34 passed`.

- [ ] **Step 5: Run everything, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `268 passed`.

````bash
git add careeros/data/lexicon.yaml careeros/core/memory/ tests/test_memory_normalize.py
git commit -m "memory: bundled lexicon, number/year/duration extraction, facet derivation" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 3: The memory store, operations and validation

**Files:**
- Create: `careeros/core/memory/store.py`, `careeros/core/memory/ops.py`, `tests/test_memory_store.py`
- Modify: `careeros/core/ledger.py`, `careeros/core/validation.py`, `tests/helpers.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: Task 2 (`lexicon`, `facets`), `careeros.core.{ids, ledger, models}`, `careeros.core.workspace.{WorkspaceLock, ensure_writable, atomic_write_bytes, split_frontmatter, join_frontmatter, normalize_value}`.
- Produces (`store.py`): constants `CAREER`, `SOURCES_REL`, `KIND_DIR`, `KINDS`, `ID_KIND`, `STATUSES`, `ACTIVE`, `REQUIRED`, `MEMORY_ID_PREFIXES`; `Fact(fm, body, path)` with `.id .kind .status .get(key, default)`; `Career(facts, issues)` with `.errors`, `.by_id()`, `.active()`; `sha256_hex`, `fact_path`, `heading`, `new_frontmatter`, `render`, `new_fact`, `known_ids`, `load_sources`, `render_sources`, `load_career(root) -> Career` (reports `MEM001`-`MEM008`), `lexicon_for(root, career)`, and `Transaction` (`write(path, data)`, `rollback()`).
- Produces (`ops.py`): `MemoryOpError(WorkspaceError)`; `open_career(root)`; `event_spec(...)`; `add_fact(root, kind, fields, *, quote=None, actor="user", source=None, origin="manual") -> Fact`; `update_fact(root, id, changes, *, actor, confirm) -> Fact | None`; `confirm_fact`, `verify_fact(root, id, evidence_id, ...)`, `dispute_fact(root, id, reason, ...)`, `retire_fact(root, id, reason, ...)`, each `-> Fact | None` (`None` means the user declined; the decline is logged as `memory.declined`). `confirm` is a callable `str -> bool`; passing `None` where a person is required raises `MemoryOpError` mentioning "interactive terminal".
- Produces (tests): `helpers.make_career(root) -> dict[str, str]` (an invented memory, ids by short name) and the shared `career` fixture in `conftest.py` returning `(root, ids)`; the template is built once per session and copied per test.

- [ ] **Step 1: Add the test helpers and the shared fixture**

Append to `tests/helpers.py`:

````python
# --- career memory ---------------------------------------------------------------------

def make_career(root: Path) -> dict[str, str]:
    """A small, invented career memory (every fact `claimed`). Returns the ids by short name."""
    from careeros.core.memory import ops

    quote = "stated by the user in the test"

    def add(kind: str, **fields: object) -> str:
        return ops.add_fact(root, kind, fields, quote=quote).id

    made: dict[str, str] = {}
    made["identity"] = add("identity", name="Jordan Example", headlines=["Backend Engineer", "Platform Engineer"])
    made["acme"] = add("experience", employer="Acme Corp", title="Senior Software Engineer", start="2025-01", end="present",
                       technologies=["Go", "Python", "Kubernetes", "AWS", "Kafka", "Terraform"])
    made["globex"] = add("experience", employer="Globex Systems Pvt Ltd", title="Software Engineer 2", start="2022-03",
                         end="2024-12", technologies=["PostgreSQL", "Prometheus", "Grafana"])
    made["k8s"] = add("achievement", parent=made["acme"], text="Led a Kubernetes migration for 40+ services, cutting deploy time by 60%.")
    made["costs"] = add("achievement", parent=made["acme"], text="Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.")
    made["platform"] = add("achievement", parent=made["acme"], text="Built an internal developer platform in Go used by 120 engineers.")
    made["pg"] = add("achievement", parent=made["globex"], text="Designed a multi-region PostgreSQL setup with 99.95% availability.")
    made["incident"] = add("achievement", parent=made["globex"],
                           text="Cut incident response time from 45 minutes to 12 minutes with Prometheus and Grafana alerts.")
    for name in ("Go", "Python", "SQL", "Terraform", "Kubernetes"):
        made[f"skill_{name.lower()}"] = add("skill", name=name)
    made["edu"] = add("education", school="Example Institute of Technology, Jabalpur", degree="B.Tech. in Computer Science",
                      end="2020-06", text="CGPA 8.1/10")
    made["cka"] = add("certification", name="Certified Kubernetes Administrator", year="2023")
    made["kubewatch"] = add("project", name="kubewatch", text="Open-source tool that streams Kubernetes events, used by 300+ teams.")
    return made
````

Append to `tests/conftest.py`:

````python
@pytest.fixture(scope="session")
def _career_template(tmp_path_factory: pytest.TempPathFactory):
    """Build the invented career memory once; each test gets its own copy."""
    from helpers import make_career, make_workspace

    root = make_workspace(tmp_path_factory.mktemp("career-template") / "ws")
    return root, make_career(root)


@pytest.fixture
def career(_career_template, tmp_path: Path, clock):
    """(workspace, ids by short name): a workspace holding a small invented career memory."""
    import shutil

    template, ids = _career_template
    root = tmp_path / "ws"
    shutil.copytree(template, root)
    return root, dict(ids)
````

- [ ] **Step 2: Write the failing test**

Create `tests/test_memory_store.py`:

````python
"""Career memory files and operations: validation rules, status semantics, guards, rollback."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import snapshot

from careeros.core import ledger
from careeros.core.memory import ops, store
from careeros.core.memory.ops import MemoryOpError
from careeros.core.validation import validate_workspace
from careeros.core.workspace import join_frontmatter, split_frontmatter

YES = lambda prompt: True  # noqa: E731
NO = lambda prompt: False  # noqa: E731


def codes(root: Path) -> list[str]:
    return sorted({i.code for i in store.load_career(root).issues})


def rewrite(path: Path, **changes: object) -> None:
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is KeyError:
            fm.pop(key, None)
        else:
            fm[key] = value
    path.write_text(join_frontmatter(fm, body), encoding="utf-8")


def events(root: Path, prefix: str = "memory.") -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith(prefix)]


# --- a healthy memory ----------------------------------------------------------------------

def test_a_fresh_memory_is_clean_and_every_fact_is_claimed_and_manual(career) -> None:
    root, ids = career
    loaded = store.load_career(root)
    assert loaded.issues == [] and len(loaded.facts) == len(ids)
    assert {f.status for f in loaded.facts} == {"claimed"} and {f.get("origin") for f in loaded.facts} == {"manual"}
    fact = loaded.by_id()[ids["costs"]]
    assert fact.get("source") == {"kind": "user_statement", "quote": "stated by the user in the test"}
    assert fact.get("parent") == ids["acme"] and fact.get("technologies") == ["AWS", "Kafka"]
    assert validate_workspace(root) == []


def test_facts_are_ordinary_markdown_files_a_person_can_read(career) -> None:
    root, ids = career
    text = store.load_career(root).by_id()[ids["costs"]].path.read_text(encoding="utf-8")
    assert text.startswith("---\nid: ach_") and text.rstrip().endswith("# Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.")


# --- validation rules ----------------------------------------------------------------------

def test_unreadable_files_are_reported_not_crashed_on(career) -> None:
    root, ids = career
    path = store.load_career(root).by_id()[ids["costs"]].path
    path.write_text("no frontmatter here\n", encoding="utf-8")
    assert "MEM001" in codes(root)
    path.write_text("---\nid: [unclosed\n---\n", encoding="utf-8")
    assert "MEM001" in codes(root)
    path.write_bytes(b"---\nid: \xff\xfe\n---\n")
    assert "MEM001" in codes(root)


@pytest.mark.parametrize("change,code", [
    ({"parent": KeyError}, "MEM002"),
    ({"text": KeyError}, "MEM002"),
    ({"id": "ach_NOTVALID"}, "MEM002"),
    ({"status": "great"}, "MEM004"),
    ({"origin": "robot"}, "MEM004"),
    ({"source": KeyError}, "MEM002"),
    ({"source": {"kind": "rumour"}}, "MEM004"),
    ({"source": {"kind": "user_statement"}}, "MEM004"),
    ({"source": {"kind": "resume"}}, "MEM004"),
    ({"created_at": "yesterday"}, "MEM004"),
    ({"parent": "exp_0000000000"}, "MEM003"),
    ({"status": "confirmed"}, "MEM005"),
    ({"status": "verified", "confirmed_at": "2026-10-01T00:00:00Z"}, "MEM005"),
])
def test_validation_rules(career, change: dict, code: str) -> None:
    root, ids = career
    rewrite(store.load_career(root).by_id()[ids["costs"]].path, **change)
    assert code in codes(root), codes(root)
    assert any(i.code == code for i in validate_workspace(root))


def test_duplicate_ids_and_misnamed_files_are_errors(career) -> None:
    root, ids = career
    by_id = store.load_career(root).by_id()
    other = by_id[ids["platform"]].path
    rewrite(other, id=ids["costs"])
    assert "MEM002" in codes(root)


def test_experience_dates_must_be_year_month_or_present(career) -> None:
    root, ids = career
    rewrite(store.load_career(root).by_id()[ids["acme"]].path, start="January 2025")
    assert "MEM004" in codes(root)


def test_a_deleted_memory_file_breaks_the_ledger_reference(career) -> None:
    root, ids = career
    store.load_career(root).by_id()[ids["pg"]].path.unlink()
    issues = [i for i in validate_workspace(root) if i.code == "LED003"]
    assert issues and ids["pg"] in issues[0].message and "retire" in issues[0].fix


# --- adding --------------------------------------------------------------------------------

def test_adding_a_fact_needs_the_users_own_words(career) -> None:
    root, ids = career
    with pytest.raises(MemoryOpError, match="--quote"):
        ops.add_fact(root, "skill", {"name": "Rust"}, quote="  ")
    with pytest.raises(MemoryOpError, match="needs"):
        ops.add_fact(root, "experience", {"employer": "X"}, quote="mine")
    with pytest.raises(MemoryOpError, match="parent"):
        ops.add_fact(root, "achievement", {"text": "Did a thing"}, quote="mine")
    with pytest.raises(MemoryOpError, match="not an experience or project"):
        ops.add_fact(root, "achievement", {"parent": ids["pg"], "text": "x"}, quote="mine")
    with pytest.raises(MemoryOpError, match="already exists"):
        ops.add_fact(root, "identity", {"name": "Someone"}, quote="mine")
    with pytest.raises(MemoryOpError, match="YYYY-MM"):
        ops.add_fact(root, "experience", {"employer": "X", "title": "Y", "start": "2020", "end": "present"}, quote="mine")


def test_adding_derives_metrics_and_technologies_and_logs_the_event(career) -> None:
    root, ids = career
    fact = ops.add_fact(root, "achievement", {"parent": ids["acme"], "text": "Cut p99 latency 3x in Rust services."},
                        quote="said so", actor="agent:claude")
    assert fact.get("metrics")[0]["unit"] == "multiplier" and fact.get("technologies") == ["Rust"]
    event = events(root)[-1]
    assert event["type"] == "memory.fact_added" and event["entity"] == fact.id and event["actor"] == "agent:claude"
    assert event["new_state"] == "claimed" and ledger.verify_chain(root) == []


def test_evidence_records_back_facts_and_validate(career) -> None:
    root, ids = career
    (root / "proof.pdf").write_bytes(b"%PDF-1.4 invented")
    evidence = ops.add_fact(root, "evidence", {"kind": "document", "path": "proof.pdf", "supports": [ids["costs"]],
                                               "note": "Cost report from the finance team"})
    assert evidence.get("source") == {"kind": "document", "path": "proof.pdf"}
    with pytest.raises(MemoryOpError, match="supports"):
        ops.add_fact(root, "evidence", {"kind": "link", "url": "https://example.com", "supports": ["ach_zzzzzzzzzz"], "note": "x"})
    assert store.load_career(root).issues == []


# --- status changes ------------------------------------------------------------------------

def test_confirming_needs_a_person_and_records_their_decision(career) -> None:
    root, ids = career
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.confirm_fact(root, ids["costs"])
    assert ops.confirm_fact(root, ids["costs"], confirm=NO) is None
    assert store.load_career(root).by_id()[ids["costs"]].status == "claimed"
    assert events(root)[-1]["type"] == "memory.declined"
    fact = ops.confirm_fact(root, ids["costs"], confirm=YES, actor="user")
    assert fact.status == "confirmed" and fact.get("confirmed_at")
    assert events(root)[-1]["type"] == "memory.fact_confirmed" and events(root)[-1]["prev_state"] == "claimed"
    with pytest.raises(MemoryOpError, match="needs it to be claimed"):
        ops.confirm_fact(root, ids["costs"], confirm=YES)


def test_verifying_needs_evidence_that_lists_the_fact(career) -> None:
    root, ids = career
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    evidence = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://example.com/report", "supports": [ids["k8s"]], "note": "report"})
    with pytest.raises(MemoryOpError, match="does not list"):
        ops.verify_fact(root, ids["costs"], evidence.id, confirm=YES)
    with pytest.raises(MemoryOpError, match="not an active evidence"):
        ops.verify_fact(root, ids["costs"], ids["k8s"], confirm=YES)
    good = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://example.com/r2", "supports": [ids["costs"]], "note": "report 2"})
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.verify_fact(root, ids["costs"], good.id)
    fact = ops.verify_fact(root, ids["costs"], good.id, confirm=YES)
    assert fact.status == "verified" and events(root)[-1]["artifacts"] == [good.id]
    assert store.load_career(root).issues == []
    ops.retire_fact(root, good.id, "link died", confirm=YES)
    assert "MEM005" in codes(root)  # verified, but its evidence is retired


def test_changing_a_confirmed_fact_resets_it_to_claimed_after_confirmation(career) -> None:
    root, ids = career
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.update_fact(root, ids["costs"], {"text": "Reduced AWS costs by 36%."})
    assert ops.update_fact(root, ids["costs"], {"text": "Reduced AWS costs by 36%."}, confirm=NO) is None
    assert "35%" in store.load_career(root).by_id()[ids["costs"]].get("text")
    updated = ops.update_fact(root, ids["costs"], {"text": "Reduced AWS costs by 36%."}, confirm=YES)
    assert updated.status == "claimed" and updated.get("confirmed_at") is None and updated.get("origin") == "manual"
    assert updated.get("metrics")[0]["value"] == 36.0
    assert events(root)[-1]["type"] == "memory.fact_updated" and events(root)[-1]["prev_state"] == "confirmed"


def test_updating_a_claimed_fact_needs_no_terminal_but_only_known_fields(career) -> None:
    root, ids = career
    assert ops.update_fact(root, ids["platform"], {"text": "Built a developer platform in Go."}).status == "claimed"
    with pytest.raises(MemoryOpError, match="can change"):
        ops.update_fact(root, ids["platform"], {"parent": ids["globex"]})
    with pytest.raises(MemoryOpError, match="no fact"):
        ops.update_fact(root, "ach_zzzzzzzzzz", {"text": "x"})


def test_disputing_and_retiring_need_a_reason_and_keep_the_file(career) -> None:
    root, ids = career
    with pytest.raises(MemoryOpError, match="reason"):
        ops.dispute_fact(root, ids["costs"], " ")
    fact = ops.dispute_fact(root, ids["costs"], "the real number was lower")
    assert fact.status == "disputed" and events(root)[-1]["reason"] == "the real number was lower"
    path = fact.path
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.retire_fact(root, ids["costs"], "superseded")
    ops.retire_fact(root, ids["costs"], "superseded", confirm=YES)
    assert path.exists() and store.load_career(root).by_id()[ids["costs"]].status == "retired"
    with pytest.raises(MemoryOpError, match="retired"):
        ops.update_fact(root, ids["costs"], {"text": "x"})
    assert validate_workspace(root) == []  # a retired fact still resolves its ledger references


def test_disputing_a_confirmed_fact_needs_a_person(career) -> None:
    root, ids = career
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.dispute_fact(root, ids["costs"], "wrong")
    assert ops.dispute_fact(root, ids["costs"], "wrong", confirm=YES).status == "disputed"


# --- atomicity and the workspace guards ------------------------------------------------------

def test_a_failed_ledger_append_puts_every_file_back(career, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ids = career

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ops.ledger, "append_events", boom)
    before = snapshot(root)
    for attempt in (
        lambda: ops.add_fact(root, "skill", {"name": "Rust"}, quote="mine"),
        lambda: ops.confirm_fact(root, ids["costs"], confirm=YES),
        lambda: ops.update_fact(root, ids["platform"], {"text": "Changed."}),
        lambda: ops.retire_fact(root, ids["pg"], "gone"),
    ):
        with pytest.raises(OSError, match="ledger disk full"):
            attempt()
        assert snapshot(root) == before
    monkeypatch.undo()
    assert ledger.verify_chain(root) == []


def test_a_workspace_from_a_newer_careeros_is_refused(career) -> None:
    root, ids = career
    from careeros.core.models import WorkspaceMeta
    from careeros.core.workspace import WorkspaceError, save_meta

    save_meta(root, WorkspaceMeta(99, "9.9.9", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", ("claude",)))
    with pytest.raises(WorkspaceError, match="newer"):
        ops.add_fact(root, "skill", {"name": "Rust"}, quote="mine")


def test_operations_refuse_to_build_on_a_broken_memory(career) -> None:
    root, ids = career
    rewrite(store.load_career(root).by_id()[ids["costs"]].path, status="great")
    with pytest.raises(MemoryOpError, match="MEM004"):
        ops.add_fact(root, "skill", {"name": "Rust"}, quote="mine")


def test_memory_event_types_are_reserved_for_the_memory_commands() -> None:
    assert ledger.is_reserved("memory.fact_confirmed") and ledger.is_reserved("memory.imported")
    assert not ledger.is_reserved("draft.checked")
````

- [ ] **Step 3: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_memory_store.py -q`
Expected: errors, `ModuleNotFoundError: No module named 'careeros.core.memory.store'` (or `ops`).

- [ ] **Step 4: Create `careeros/core/memory/store.py`**

````python
"""Career memory on disk: one markdown file with frontmatter per fact, plus sources.yaml."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from careeros.core import ids
from careeros.core.models import Issue, is_utc_timestamp
from careeros.core.workspace import (
    WorkspaceError, atomic_write_bytes, join_frontmatter, normalize_value, split_frontmatter,
)

CAREER = Path("career")
SOURCES_REL = CAREER / "sources.yaml"
KIND_DIR = {
    "experience": "experiences", "achievement": "achievements", "project": "projects", "skill": "skills",
    "education": "education", "certification": "certifications", "preference": "preferences",
    "goal": "goals", "constraint": "constraints", "evidence": "evidence",
}
KINDS = (*KIND_DIR, "identity")
ID_KIND = {kind: kind for kind in KINDS} | {"identity": "profile"}
STATUSES = ("claimed", "confirmed", "verified", "disputed", "retired")
ACTIVE = ("claimed", "confirmed", "verified")
ORIGINS = ("imported", "manual")
SOURCE_KINDS = ("resume", "user_statement", "document")
REQUIRED = {
    "experience": ("employer", "title", "start", "end"),
    "achievement": ("parent", "text"),
    "project": ("name", "text"),
    "skill": ("name",),
    "education": ("school", "degree"),
    "certification": ("name",),
    "preference": ("text",), "goal": ("text",), "constraint": ("text",),
    "identity": ("name",),
    "evidence": ("kind", "supports", "note"),
}
COMMON = ("id", "type", "schema", "status", "origin", "source", "created_at", "updated_at")
MEMORY_ID_PREFIXES = tuple(f"{ids.PREFIXES[ID_KIND[k]]}_" for k in KINDS)
_DATE = re.compile(r"^(\d{4}-(0[1-9]|1[0-2])|present)$")
_NULLABLE = {"parent"}


@dataclass
class Fact:
    fm: dict
    body: str
    path: Path

    @property
    def id(self) -> str:
        return str(self.fm.get("id"))

    @property
    def kind(self) -> str:
        return str(self.fm.get("type"))

    @property
    def status(self) -> str:
        return str(self.fm.get("status"))

    def get(self, key: str, default: object = None) -> object:
        return self.fm.get(key, default)


@dataclass
class Career:
    facts: list[Fact] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def active(self) -> list[Fact]:
        return [f for f in self.facts if f.status in ACTIVE]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fact_path(root: Path, kind: str, fact_id: str) -> Path:
    if kind == "identity":
        return Path(root) / CAREER / "identity.md"
    return Path(root) / CAREER / KIND_DIR[kind] / f"{fact_id}.md"


def heading(kind: str, fm: dict) -> str:
    text = {
        "experience": lambda: f"{fm.get('title')} — {fm.get('employer')}",
        "education": lambda: f"{fm.get('degree')} — {fm.get('school')}",
        "project": lambda: str(fm.get("name")),
        "skill": lambda: str(fm.get("name")),
        "certification": lambda: str(fm.get("name")),
        "identity": lambda: str(fm.get("name")),
        "evidence": lambda: str(fm.get("note")),
    }.get(kind, lambda: str(fm.get("text")))()
    return " ".join(text.split())[:100]


def new_frontmatter(kind: str, fact_id: str, fields: dict, source: dict, origin: str, now: str) -> dict:
    fm: dict = {
        "id": fact_id, "type": kind, "schema": 1, "status": "claimed", "origin": origin,
        **fields, "source": source, "created_at": now, "updated_at": now, "confirmed_at": None,
    }
    return fm


def render(fact: Fact) -> bytes:
    body = f"# {heading(fact.kind, fact.fm)}\n"
    return join_frontmatter(fact.fm, body).encode("utf-8")


def new_fact(root: Path, kind: str, fm: dict) -> Fact:
    return Fact(fm, "", fact_path(root, kind, str(fm["id"])))


def _files(root: Path) -> list[Path]:
    base = Path(root) / CAREER
    paths = [base / "identity.md"] if (base / "identity.md").is_file() else []
    for directory in KIND_DIR.values():
        paths.extend(sorted((base / directory).glob("*.md")))
    return paths


def known_ids(root: Path) -> set[str]:
    """Every memory id that has a file, read leniently (broken files still count by file name)."""
    found: set[str] = set()
    for path in _files(root):
        found.add(path.stem)
        try:
            fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, WorkspaceError):
            continue
        if fm and isinstance(fm.get("id"), str):
            found.add(fm["id"])
    return found


def load_sources(root: Path) -> dict[str, dict]:
    path = Path(root) / SOURCES_REL
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("sources", [])
        return {str(e["path"]): dict(e) for e in entries}
    except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
        raise WorkspaceError(f"career/sources.yaml could not be read: {exc}") from exc


def render_sources(entries: dict[str, dict]) -> bytes:
    data = {"sources": [entries[key] for key in sorted(entries)]}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000).encode("utf-8")


def load_career(root: Path) -> Career:
    """Read every fact and report problems (MEM001-MEM008). Errors mean the memory cannot be trusted."""
    root = Path(root)
    career = Career()

    def add(severity: str, code: str, path: str, message: str, fix: str) -> None:
        career.issues.append(Issue(severity, code, path, message, fix))

    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        try:
            fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, WorkspaceError) as exc:
            add("error", "MEM001", rel, f"cannot be read: {exc}", "fix the file, or restore it from .careeros/backups or version control")
            continue
        if fm is None:
            add("error", "MEM001", rel, "has no frontmatter", "restore the --- frontmatter block, or restore the file from version control")
            continue
        career.facts.append(Fact({k: normalize_value(v) for k, v in fm.items()}, body, path))
    _validate(root, career, add)
    return career


def _validate(root: Path, career: Career, add) -> None:
    by_id: dict[str, Fact] = {}
    for fact in career.facts:
        rel = fact.path.relative_to(root).as_posix()
        fm = fact.fm
        kind = fm.get("type")
        if kind not in KINDS:
            add("error", "MEM004", rel, f"type {kind!r} is not a memory kind", "use one of: " + ", ".join(KINDS))
            continue
        missing = [k for k in (*COMMON, *REQUIRED[kind]) if k not in fm or (fm[k] is None and k not in _NULLABLE)]
        if missing:
            add("error", "MEM002", rel, f"missing: {', '.join(missing)}", "add the missing key(s) to the frontmatter")
        fact_id = fm.get("id")
        if not ids.is_valid_id(ID_KIND[kind], fact_id):
            add("error", "MEM002", rel, f"id {fact_id!r} is not a valid {kind} id", f"use {ids.PREFIXES[ID_KIND[kind]]}_ followed by 10 lowercase base32 characters")
        elif fact_id in by_id:
            add("error", "MEM002", rel, f"id {fact_id} is also used by {by_id[fact_id].path.relative_to(root).as_posix()}", "give one of the two facts a new id")
        else:
            by_id[str(fact_id)] = fact
            if kind != "identity" and fact.path.stem != fact_id:
                add("error", "MEM002", rel, f"the file name does not match its id {fact_id}", "rename the file to <id>.md")
        if fm.get("status") not in STATUSES:
            add("error", "MEM004", rel, f"status {fm.get('status')!r} is invalid", "use one of: " + ", ".join(STATUSES))
        if fm.get("origin") not in ORIGINS:
            add("error", "MEM004", rel, f"origin {fm.get('origin')!r} is invalid", "use imported or manual")
        for key in ("created_at", "updated_at", "confirmed_at"):
            if fm.get(key) is not None and not is_utc_timestamp(fm.get(key)):
                add("error", "MEM004", rel, f"{key} {fm.get(key)!r} is not a UTC ISO-8601 timestamp", "use the form 2026-10-06T09:15:00Z")
        source = fm.get("source")
        if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
            add("error", "MEM004", rel, "source is missing or has an invalid kind", "give the fact a source: resume, user_statement or document")
        elif source["kind"] == "user_statement" and not str(source.get("quote") or "").strip():
            add("error", "MEM004", rel, "a user_statement source needs the user's own words in quote", "add the quote")
        elif source["kind"] != "user_statement" and not source.get("path"):
            add("error", "MEM004", rel, f"a {source['kind']} source needs a path", "add the path")
        for key in ("start", "end"):
            if kind == "experience" and key in fm and not _DATE.match(str(fm[key])):
                add("error", "MEM004", rel, f"{key} {fm[key]!r} must be YYYY-MM or present", "use a value like 2024-01")
        if fm.get("status") in ("confirmed", "verified") and not fm.get("confirmed_at"):
            add("error", "MEM005", rel, f"{fm.get('status')} but confirmed_at is empty", "confirm the fact again with `careeros memory confirm`")
    for fact in career.facts:
        rel = fact.path.relative_to(root).as_posix()
        fm = fact.fm
        kind = fm.get("type")
        if kind == "achievement" and fm.get("parent") is not None and fm.get("parent") not in by_id:
            add("error", "MEM003", rel, f"parent {fm.get('parent')} does not exist", "restore the parent fact, or retire this one")
        if kind == "evidence":
            for target in fm.get("supports") or ():
                if target not in by_id:
                    add("error", "MEM003", rel, f"supports {target}, which does not exist", "restore that fact, or retire this evidence")
        if fm.get("status") == "verified":
            backed = any(
                e.kind == "evidence" and e.status in ACTIVE and fact.id in (e.fm.get("supports") or ())
                for e in career.facts
            )
            if not backed:
                add("error", "MEM005", rel, "verified, but no active evidence record supports it", "add evidence with `careeros memory add --kind evidence`, or dispute the fact")
        source = fm.get("source")
        if isinstance(source, dict) and fm.get("origin") == "imported" and source.get("kind") in ("resume", "document"):
            if source.get("path") and not (root / str(source["path"])).is_file():
                add("warning", "MEM007", rel, f"its source {source['path']} no longer exists", "restore the file, or retire the fact")
        if fm.get("stale"):
            add("warning", "MEM008", rel, "its source line is gone from the imported file", "retire the fact with `careeros memory retire`, or restore the line")
    try:
        sources = load_sources(root)
    except WorkspaceError as exc:
        add("error", "MEM001", SOURCES_REL.as_posix(), str(exc), "fix the file, or restore it from .careeros/backups")
        return
    for key, entry in sources.items():
        target = root / key
        if target.is_file() and sha256_hex(target.read_bytes()) != entry.get("sha256"):
            add("warning", "MEM006", key, f"{key} changed since it was imported", "run `careeros memory import` to review the differences")


class Transaction:
    """Remember what files looked like before, so a failed operation can put every one back."""

    def __init__(self) -> None:
        self._saved: dict[Path, bytes | None] = {}

    def write(self, path: Path, data: bytes) -> None:
        if path not in self._saved:
            self._saved[path] = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, data)

    def rollback(self) -> None:
        for path, original in reversed(list(self._saved.items())):
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, original)


def lexicon_for(root: Path, career: Career):
    """The bundled lexicon plus the user's additions and every technology named anywhere in the memory."""
    from careeros.core.memory.lexicon import load_lexicon

    names: list[str] = []
    for fact in career.facts:
        if fact.kind == "skill":
            names.append(str(fact.get("name") or ""))
        names.extend(str(t) for t in (fact.get("technologies") or ()) if isinstance(t, str))
    return load_lexicon(root).with_extra_techs(names)
````

- [ ] **Step 5: Create `careeros/core/memory/ops.py`**

````python
"""Changing the career memory: add, update, confirm, verify, dispute and retire facts.

Every operation runs under the workspace lock, writes atomically, appends its ledger event, and puts
the files back if the ledger append fails.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from careeros.core import ids, ledger, models
from careeros.core.memory import facets, store
from careeros.core.memory.store import ACTIVE, Career, Fact, Transaction
from careeros.core.workspace import WorkspaceError, WorkspaceLock, ensure_writable

Confirm = Callable[[str], bool]
EDITABLE = {
    "experience": ("employer", "title", "start", "end", "technologies", "location"),
    "achievement": ("text",),
    "project": ("name", "text", "technologies"),
    "skill": ("name", "category"),
    "education": ("school", "degree", "field", "end"),
    "certification": ("name", "issuer", "year"),
    "preference": ("text",), "goal": ("text",), "constraint": ("text",),
    "identity": ("name", "location", "headlines"),
    "evidence": ("note",),
}
_LIST_FIELDS = {"technologies", "headlines", "supports"}
_DATE = re.compile(r"^(\d{4}-(0[1-9]|1[0-2])|present)$")


class MemoryOpError(WorkspaceError):
    """The memory cannot be changed that way."""


def open_career(root: Path) -> Career:
    ensure_writable(root)
    career = store.load_career(root)
    if career.errors:
        first = career.errors[0]
        raise MemoryOpError(f"the career memory has errors ({first.code} {first.path}: {first.message}); run `careeros validate`")
    return career


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(v) for v in (value or ())]


def _prepare(kind: str, fields: dict, career: Career, lexicon) -> dict:
    fields = dict(fields)
    for key in _LIST_FIELDS & set(fields):
        fields[key] = _as_list(fields[key])
    if kind == "experience":
        fields.setdefault("technologies", [])
    required = store.REQUIRED[kind]
    missing = [k for k in required if k not in fields or (fields[k] in (None, "", []) and k != "parent")]
    if missing:
        raise MemoryOpError(f"a {kind} needs: {', '.join(missing)}")
    for key in ("start", "end"):
        if kind == "experience" and not _DATE.match(str(fields[key])):
            raise MemoryOpError(f"{key} must be YYYY-MM or present, not {fields[key]!r}")
    if kind in ("achievement", "project", "education") and fields.get("text"):
        fields.update(facets.derive_facets(str(fields["text"]), lexicon))
    if kind == "achievement":
        parent = fields.get("parent")
        if parent is None:
            if fields.get("section") != "summary":
                raise MemoryOpError("an achievement needs a parent experience or project (--set parent=<id>)")
        elif parent not in career.by_id() or career.by_id()[parent].kind not in ("experience", "project"):
            raise MemoryOpError(f"parent {parent} is not an experience or project in the memory")
    if kind == "evidence":
        if fields["kind"] not in ("document", "link"):
            raise MemoryOpError("evidence kind must be document or link")
        if not (fields.get("path") or fields.get("url")):
            raise MemoryOpError("evidence needs a --set path=... or --set url=...")
        for target in fields["supports"]:
            if target not in career.by_id():
                raise MemoryOpError(f"supports {target}, which is not in the memory")
    return fields


def event_spec(fact_id: str, event_type: str, action: str, *, actor: str, prev: str | None = None,
           new: str | None = None, reason: str | None = None, artifacts: list[str] | None = None) -> dict:
    spec = {"type": event_type, "actor": actor, "entity": fact_id, "prev_state": prev, "new_state": new,
            "action": action, "source": "cli"}
    if reason:
        spec["reason"] = reason
    if artifacts:
        spec["artifacts"] = artifacts
    return spec


def _commit(root: Path, tx: Transaction, specs: list[dict]) -> None:
    try:
        ledger.append_events(root, specs)
    except BaseException:
        tx.rollback()
        raise


def add_fact(root: Path, kind: str, fields: dict, *, quote: str | None = None, actor: str = "user",
             source: dict | None = None, origin: str = "manual") -> Fact:
    if kind not in store.KINDS:
        raise MemoryOpError(f"unknown kind {kind!r}; use one of: {', '.join(store.KINDS)}")
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        if kind == "identity" and any(f.kind == "identity" for f in career.facts):
            raise MemoryOpError("an identity already exists; change it with `careeros memory update`")
        fields = _prepare(kind, fields, career, store.lexicon_for(root, career))
        if source is None:
            if kind == "evidence":
                source = ({"kind": "document", "path": fields["path"]} if fields.get("path")
                          else {"kind": "user_statement", "quote": fields["note"]})
            elif quote and quote.strip():
                source = {"kind": "user_statement", "quote": quote.strip()}
            else:
                raise MemoryOpError("--quote is required: give the user's own words for this fact")
        fact_id = ids.new_id(store.ID_KIND[kind], career.by_id())
        fm = store.new_frontmatter(kind, fact_id, fields, source, origin, models.utc_now())
        fact = store.new_fact(root, kind, fm)
        tx = Transaction()
        tx.write(fact.path, store.render(fact))
        _commit(root, tx, [event_spec(fact_id, "memory.fact_added", f"added {kind}: {store.heading(kind, fm)}",
                                  actor=actor, new="claimed")])
        return fact


def _find(career: Career, fact_id: str) -> Fact:
    fact = career.by_id().get(fact_id)
    if fact is None:
        raise MemoryOpError(f"no fact with id {fact_id}")
    return fact


def _decline(root: Path, fact: Fact, what: str, actor: str) -> None:
    ledger.append_events(root, [event_spec(fact.id, "memory.declined", f"declined to {what} {fact.id}", actor=actor)])


def _need_confirm(fact: Fact, what: str, confirm: Confirm | None) -> Confirm:
    if confirm is None:
        raise MemoryOpError(f"{what} {fact.id} records the user's own decision and needs an interactive terminal")
    return confirm


def update_fact(root: Path, fact_id: str, changes: dict, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        fact = _find(career, fact_id)
        if fact.status == "retired":
            raise MemoryOpError(f"{fact_id} is retired and cannot be changed")
        unknown = set(changes) - set(EDITABLE[fact.kind])
        if unknown or not changes:
            raise MemoryOpError(f"a {fact.kind} can change: {', '.join(EDITABLE[fact.kind])}")
        prev = fact.status
        if prev in ("confirmed", "verified"):
            ask = _need_confirm(fact, "changing", confirm)
            if not ask(f"{fact_id} is {prev}. Changing it resets it to claimed so you review it again. Continue?"):
                _decline(root, fact, "change", actor)
                return None
        fm = dict(fact.fm)
        for key, value in changes.items():
            fm[key] = _as_list(value) if key in _LIST_FIELDS else value
        if fact.kind == "experience":
            for key in ("start", "end"):
                if not _DATE.match(str(fm[key])):
                    raise MemoryOpError(f"{key} must be YYYY-MM or present, not {fm[key]!r}")
        if "text" in changes and fact.kind in ("achievement", "project"):
            fm.update(facets.derive_facets(str(fm["text"]), store.lexicon_for(root, career)))
        fm["origin"] = "manual"
        fm["status"], fm["confirmed_at"], fm["updated_at"] = "claimed", None, models.utc_now()
        updated = Fact(fm, fact.body, fact.path)
        tx = Transaction()
        tx.write(updated.path, store.render(updated))
        _commit(root, tx, [event_spec(fact_id, "memory.fact_updated", f"updated {fact.kind}: {', '.join(changes)}",
                                  actor=actor, prev=prev, new="claimed")])
        return updated


def _set_status(root: Path, fact_id: str, new: str, event_type: str, *, actor: str, allowed: tuple[str, ...],
                confirm: Confirm | None, needs_human: bool, reason: str | None = None,
                artifacts: list[str] | None = None, check=None) -> Fact | None:
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        fact = _find(career, fact_id)
        if fact.status not in allowed:
            raise MemoryOpError(f"{fact_id} is {fact.status}; this needs it to be {' or '.join(allowed)}")
        if check:
            check(career, fact)
        prev = fact.status
        if needs_human or prev != "claimed":
            ask = _need_confirm(fact, new, confirm)
            if not ask(f"Mark {fact_id} ({store.heading(fact.kind, fact.fm)}) as {new}?"):
                _decline(root, fact, new, actor)
                return None
        fm = dict(fact.fm)
        fm["status"], fm["updated_at"] = new, models.utc_now()
        if new in ("confirmed", "verified"):
            fm["confirmed_at"] = fm.get("confirmed_at") or fm["updated_at"]
        if new in ("disputed", "retired"):
            fm["confirmed_at"] = None
        updated = Fact(fm, fact.body, fact.path)
        tx = Transaction()
        tx.write(updated.path, store.render(updated))
        _commit(root, tx, [event_spec(fact_id, event_type, f"{new} {fact_id}", actor=actor, prev=prev, new=new,
                                  reason=reason, artifacts=artifacts)])
        return updated


def confirm_fact(root: Path, fact_id: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    return _set_status(root, fact_id, "confirmed", "memory.fact_confirmed", actor=actor, allowed=("claimed",),
                       confirm=confirm, needs_human=True)


def verify_fact(root: Path, fact_id: str, evidence_id: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    def check(career: Career, fact: Fact) -> None:
        evidence = career.by_id().get(evidence_id)
        if evidence is None or evidence.kind != "evidence" or evidence.status not in ACTIVE:
            raise MemoryOpError(f"{evidence_id} is not an active evidence record")
        if fact.id not in (evidence.get("supports") or ()):
            raise MemoryOpError(f"{evidence_id} does not list {fact.id} under supports")

    return _set_status(root, fact_id, "verified", "memory.fact_verified", actor=actor, allowed=("confirmed",),
                       confirm=confirm, needs_human=True, artifacts=[evidence_id], check=check)


def dispute_fact(root: Path, fact_id: str, reason: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    if not reason.strip():
        raise MemoryOpError("a reason is required to dispute a fact")
    return _set_status(root, fact_id, "disputed", "memory.fact_disputed", actor=actor,
                       allowed=("claimed", "confirmed", "verified"), confirm=confirm, needs_human=False, reason=reason.strip())


def retire_fact(root: Path, fact_id: str, reason: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    if not reason.strip():
        raise MemoryOpError("a reason is required to retire a fact")
    return _set_status(root, fact_id, "retired", "memory.fact_retired", actor=actor,
                       allowed=("claimed", "confirmed", "verified", "disputed"), confirm=confirm,
                       needs_human=False, reason=reason.strip())
````

- [ ] **Step 6: Reserve the `memory.` event types**

In `careeros/core/ledger.py`, replace:

````python
    return event_type in RESERVED_TYPES or event_type.startswith("workspace.")
````

with:

````python
    return event_type in RESERVED_TYPES or event_type.startswith(("workspace.", "memory."))
````

- [ ] **Step 7: Report memory problems from `validate`**

In `careeros/core/validation.py`:

1. Replace `from careeros.core.models import Issue, State` with:

````python
from careeros.core.memory import store
from careeros.core.models import Issue, State
````

2. Replace `    # --- ledger ---\n    issues.extend(ledger.verify_chain(root))` with:

````python
    # --- career memory ---
    issues.extend(store.load_career(root).issues)

    # --- ledger ---
    issues.extend(ledger.verify_chain(root))
````

3. Replace this block:

````python
    reported: set[str] = set()
    for event in events:
        entity = event.get("entity")
        if isinstance(entity, str) and entity.startswith("job_") and entity not in all_ids and entity not in reported:
            reported.add(entity)
            add("error", "LED003", "ledger.jsonl",
                f"event {event.get('seq')} refers to {entity}, but no job file has that id",
                "restore the job directory from .careeros/backups or version control; archive jobs with `careeros archive` instead of deleting them")
````

with:

````python
    reported: set[str] = set()
    memory_ids = store.known_ids(root)
    for event in events:
        entity = event.get("entity")
        if not isinstance(entity, str) or entity in reported:
            continue
        if entity.startswith("job_") and entity not in all_ids:
            reported.add(entity)
            add("error", "LED003", "ledger.jsonl",
                f"event {event.get('seq')} refers to {entity}, but no job file has that id",
                "restore the job directory from .careeros/backups or version control; archive jobs with `careeros archive` instead of deleting them")
        elif entity.startswith(store.MEMORY_ID_PREFIXES) and entity not in memory_ids:
            reported.add(entity)
            add("error", "LED003", "ledger.jsonl",
                f"event {event.get('seq')} refers to {entity}, but no career file has that id",
                "restore the file from .careeros/backups or version control; retire facts with `careeros memory retire` instead of deleting them")
````

- [ ] **Step 8: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/test_memory_store.py -q`
Expected: `32 passed`.

- [ ] **Step 9: Run everything, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `300 passed`. (The existing `LED003` job test must still pass; the job branch of the rewritten check behaves as before.)

````bash
git add careeros/core/memory/store.py careeros/core/memory/ops.py careeros/core/ledger.py careeros/core/validation.py tests/helpers.py tests/conftest.py tests/test_memory_store.py
git commit -m "memory: fact store, operations, validation rules and reserved event types" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 4: The evidence check

**Files:**
- Create: `careeros/core/memory/check.py`, `tests/test_memory_check.py`

**Interfaces:**
- Consumes: Tasks 2-3 (`facets.analyze`, `lexicon`, `normalize`, `store.load_career`, `store.lexicon_for`, `Career`, `Fact`), the `career` fixture.
- Produces (`check.py`): `run_check(root, draft, *, allow=None, against=None, require_confirmed=False, career=None) -> CheckResult`, where `against` is `(company, title)`. `CheckResult` has `supported` (list of dicts with `sentence, kind, claim, facts, claimed_only`), `review_required` (`Finding(code, sentence, atom, reason, fix)`), `not_evaluated` (sentences), `info`, `allowed_mentions`, `claimed_support`, `memory_stale`, `.ok`, `.to_dict()`. Constants `NOT_EVALUATED_CATEGORIES`, `FIX`, `CODES`. The caller is responsible for refusing a memory with errors (Task 6 does).

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_check.py`:

````python
"""The evidence check: what passes, what fails, and why. All data is invented."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import make_workspace

from careeros.core.memory import check, ops


def codes(result) -> list[str]:
    return [f.code for f in result.review_required]


def run(root: Path, draft: str, **kwargs):
    return check.run_check(root, draft, **kwargs)


# --- supported claims -----------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "Reduced AWS costs by 35% using Kafka-based pipelines at Acme Corp.",
    "Cut AWS spend 35% by moving batch workloads to Kafka pipelines.",
    "Led a Kubernetes migration for 40+ services.",
    "Led a Kubernetes migration for 40 services, cutting deploy time by 60 percent.",
    "Designed a multi-region Postgres setup with 99.95% availability.",
    "Built an internal developer platform in golang used by 120 engineers.",
    "Cut incident response time from 45 minutes to 12 minutes.",
    "I have 4 years of experience.",
    "From 2022 to 2024 I worked at Globex Systems.",
    "I graduated from Example Institute of Technology, Jabalpur in 2020.",
    "I hold the Certified Kubernetes Administrator certification.",
    "Experience with Go, Python and k8s.",
    "I work as a Backend Engineer.",
    "Dear Hiring Manager,\nBest regards,\nJordan Example",
])
def test_faithful_drafts_pass(career, draft: str) -> None:
    root, _ = career
    result = run(root, draft)
    assert result.ok, [(f.code, f.atom, f.reason) for f in result.review_required]


def test_a_pass_reports_that_support_is_only_claimed_and_lists_what_was_not_evaluated(career) -> None:
    root, _ = career
    result = run(root, "Reduced AWS costs by 35% using Kafka.\nI enjoy mentoring junior colleagues.")
    assert result.ok
    assert result.claimed_support > 0 and all(s["claimed_only"] for s in result.supported)
    assert result.not_evaluated == ["I enjoy mentoring junior colleagues."]
    assert "whether prose is true" in result.to_dict()["not_evaluated"]["categories"]


# --- unsupported claims ----------------------------------------------------------------------

@pytest.mark.parametrize("draft,code", [
    ("Reduced AWS costs by 50% using Kafka.", "CHK001"),
    ("Served 2M users.", "CHK001"),
    ("Led a Kubernetes migration for 50+ services.", "CHK001"),
    ("Cut AWS spend by 35 users.", "CHK001"),
    ("I joined the team in 2019.", "CHK002"),
    ("I built it with Rust.", "CHK003"),
    ("We used Flink and Kafka.", "CHK003"),
    ("I worked at FakeCorp for a while.", "CHK004"),
    ("My experience at FakeCorp taught me patience.", "CHK004"),
    ("I joined FakeCorp after Acme Corp.", "CHK004"),
    ("Staff Engineer at FakeCorp, I led the team.", "CHK004"),
    ("I graduated from Fake University.", "CHK005"),
    ("I studied computer science at Imaginary College.", "CHK005"),
    ("As Director of Engineering, I led platform teams.", "CHK006"),
    ("I am a Staff Engineer.", "CHK006"),
    ("I was Head of Platform for two teams.", "CHK006"),
    ("I am the CTO of a startup.", "CHK006"),
    ("I am an AWS Certified Solutions Architect.", "CHK007"),
    ("I am certified in Magic Beans.", "CHK007"),
    ("I have 10+ years of experience.", "CHK008"),
])
def test_unsupported_claims_fail_with_the_right_code(career, draft: str, code: str) -> None:
    root, _ = career
    result = run(root, draft)
    assert not result.ok
    assert code in codes(result), [(f.code, f.atom) for f in result.review_required]


def test_a_technology_duration_is_never_derivable(career) -> None:
    root, _ = career
    result = run(root, "I have 3 years of Go experience.")
    assert codes(result) == ["CHK008"]


def test_a_year_range_longer_than_the_dated_experience_fails(career) -> None:
    root, _ = career
    assert codes(run(root, "I have 5 years of experience.")) == ["CHK008"]


# --- compound claims -------------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "I reduced costs by 60% using Kafka.",                 # 60% and Kafka each exist, in different facts
    "I cut costs by 35% with Terraform.",                  # Terraform is only a skill; 35% lives in another fact
    "At Globex Systems I cut deploy time by 60%.",         # employer from one experience, metric from another
    "Experience with Go and 35% cost savings.",            # a number in the line removes the list exemption
    "As a Staff Engineer I cut costs by 35%.",
])
def test_claims_that_are_only_supported_separately_need_review(career, draft: str) -> None:
    root, _ = career
    result = run(root, draft)
    assert "CHK010" in codes(result) or "CHK006" in codes(result), codes(result)
    assert not result.ok


def test_keywords_scattered_across_the_memory_cannot_carry_a_made_up_sentence(career) -> None:
    root, _ = career
    result = run(root, "I led Kubernetes and Kafka migrations that cut costs by 60% and 35% at Globex Systems.")
    assert not result.ok


def test_a_relationship_inside_one_fact_passes_including_inherited_employer(career) -> None:
    root, _ = career
    assert run(root, "At Acme Corp I reduced AWS costs by 35%.").ok
    assert run(root, "At Globex Systems I designed a PostgreSQL setup with 99.95% availability.").ok


def test_a_known_employer_without_any_cue_still_takes_part_in_the_relationship_check(career) -> None:
    root, _ = career
    assert not run(root, "My time at Globex Systems taught me to cut deploy time by 60%.").ok


def test_technology_enumerations_are_exempt_among_themselves_but_not_with_other_claims(career) -> None:
    root, _ = career
    assert run(root, "Technologies: Go, Python, PostgreSQL and Grafana.").ok
    assert run(root, "## Skills\n\nGo, Kafka, PostgreSQL").ok
    assert not run(root, "Built a service in Go and PostgreSQL for 500 users.").ok


# --- detection without cues ------------------------------------------------------------------

@pytest.mark.parametrize("draft,code", [
    ("My experience at FakeCorp taught me a lot.", "CHK004"),
    ("As Director of Engineering, I led platform teams.", "CHK006"),
    ("I graduated from Fake University.", "CHK005"),
    ("Zorbix Labs shaped how I work.", "CHK009"),
    ("I really admired working with Zorbix Labs.", "CHK009"),
    ("We shipped it together with Quantum Widgets.", "CHK009"),
    ("Senior Staff Engineer", "CHK006"),
    ("FakeCorp | Principal Engineer", "CHK004"),
])
def test_names_are_caught_without_the_usual_phrasing(career, draft: str, code: str) -> None:
    root, _ = career
    assert code in codes(run(root, draft)), codes(run(root, draft))


def test_a_pipe_header_in_a_resume_draft_is_checked_as_employer_and_title(career) -> None:
    root, _ = career
    assert run(root, "### Acme Corp | Senior Software Engineer\nBengaluru - January 2025 - Present").ok
    assert codes(run(root, "### Acme Corp | Principal Engineer")) == ["CHK006"]


def test_ordinary_capitalisation_is_not_mistaken_for_a_company(career) -> None:
    root, _ = career
    assert run(root, "Thanks for your time on Monday. I live in Bengaluru, India and speak English.").ok
    assert run(root, "Dear Priya Sharma,\nI admire your work.").ok


# --- --allow and --against ------------------------------------------------------------------

def test_an_allowed_term_alone_in_a_sentence_is_an_unverified_mention(career) -> None:
    root, _ = career
    result = run(root, "Your team runs Flink at scale, which excites me.", allow=["Flink"])
    assert result.ok and result.allowed_mentions == ["Flink"] and not result.supported


def test_an_allowed_technology_cannot_back_an_unsupported_achievement(career) -> None:
    root, _ = career
    without = run(root, "I used Kafka to reduce costs by 35% with Flink.", allow=["Flink"])
    assert codes(without) == ["CHK010"]
    result = run(root, "I used Flink to reduce costs by 35%.", allow=["Flink"])
    assert codes(result) == ["CHK010"] and "Flink" in result.review_required[0].atom
    assert not result.supported


def test_allowing_a_number_does_not_make_it_supported(career) -> None:
    root, _ = career
    result = run(root, "Reduced AWS costs by 50%.", allow=["50%"])
    assert codes(result) == ["CHK010"]


def test_allowing_a_name_only_silences_it_when_nothing_else_is_claimed(career) -> None:
    root, _ = career
    assert run(root, "I spoke with Zorbix Labs.", allow=["Zorbix Labs"]).ok
    assert not run(root, "I worked at FakeCorp and cut costs by 35%.", allow=["FakeCorp"]).ok


def test_the_company_applied_to_may_be_named_but_working_there_still_needs_a_record(career) -> None:
    root, _ = career
    against = ("Initech", "Backend Engineer")
    assert run(root, "I am excited about the Backend Engineer role at Initech.", against=against).ok
    assert run(root, "I would love to join Initech.", against=against).ok
    assert codes(run(root, "I worked at Initech last year.", against=against)) == ["CHK004"]


# --- status semantics ------------------------------------------------------------------------

def test_require_confirmed_fails_claims_backed_only_by_claimed_facts(career) -> None:
    root, ids = career
    draft = "Reduced AWS costs by 35% using Kafka."
    assert run(root, draft).ok
    assert "CHK020" in codes(run(root, draft, require_confirmed=True))
    ops.confirm_fact(root, ids["costs"], confirm=lambda p: True)
    ops.confirm_fact(root, ids["acme"], confirm=lambda p: True)
    result = run(root, draft, require_confirmed=True)
    assert result.ok and not any(s["claimed_only"] for s in result.supported if s["claim"] in ("35%", "Kafka"))


def test_disputed_and_retired_facts_support_nothing(career) -> None:
    root, ids = career
    draft = "Reduced AWS costs by 35% using Kafka."
    ops.dispute_fact(root, ids["costs"], "this number was wrong")
    assert "CHK001" in codes(run(root, draft))
    ops.retire_fact(root, ids["platform"], "no longer used")
    assert not run(root, "Built an internal developer platform in Go used by 120 engineers.").ok


def test_an_empty_memory_fails_closed(tmp_path: Path, clock) -> None:
    root = make_workspace(tmp_path / "ws")
    assert codes(run(root, "Anything at all.")) == ["CHK030"]
    ops.add_fact(root, "preference", {"text": "no fintech roles"}, quote="I prefer no fintech")
    assert codes(run(root, "Anything at all.")) == ["CHK030"]


# --- normalisation --------------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "Cut costs by 35 percent using Kafka and AWS.",
    "Reduced AWS costs by 35% using Kafka.",
    "Served 300+ teams.",
    "Served 300 teams.",
    "Used Python 3.11 and Go 1.22 daily.",
    "See https://example.com/2031/45 or call +91 80032 93018.",
    "1. Reduced AWS costs by 35% using Kafka.",
])
def test_numbers_and_versions_normalise(career, draft: str) -> None:
    root, _ = career
    assert run(root, draft).ok, [(f.code, f.atom) for f in run(root, draft).review_required]


@pytest.mark.parametrize("draft", ["Served 3000 teams.", "Served 301+ teams.", "Cut costs by 35 users."])
def test_numbers_match_by_equality_and_the_plus_rule_is_directional(career, draft: str) -> None:
    root, _ = career
    assert "CHK001" in codes(run(root, draft))


def test_json_shape_has_the_three_sections(career) -> None:
    root, _ = career
    data = run(root, "Reduced AWS costs by 50%.\nHello there friend.").to_dict()
    assert set(data) >= {"ok", "supported", "review_required", "not_evaluated", "allowed_mentions", "supported_by_claimed"}
    assert data["ok"] is False and data["not_evaluated"]["sentences"] == ["Hello there friend."]


# --- names that merely begin like a known one -------------------------------------------------

@pytest.mark.parametrize("draft,codes_expected", [
    ("I worked at Acme Systems for a year.", {"CHK004"}),
    ("I worked at Acme Corp Labs.", {"CHK004", "CHK009"}),
    ("I studied at Example Institute of Technology Bangalore.", {"CHK005", "CHK009"}),
])
def test_a_longer_name_that_starts_like_a_known_one_is_not_the_known_one(career, draft: str, codes_expected: set) -> None:
    root, _ = career
    assert set(codes(run(root, draft))) & codes_expected, codes(run(root, draft))


# --- markdown, line endings and unusual text ----------------------------------------------

def test_markdown_is_stripped_before_checking(career) -> None:
    root, _ = career
    draft = (
        "# Resume\n\n## Experience\n\n### Acme Corp | Senior Software Engineer\nBengaluru - January 2025 - Present\n\n"
        "- **Reduced** AWS costs by 35% using [Kafka](https://example.com/kafka)-based pipelines.\n"
        "```\nnot a claim: 99% of Rust\n```\n<!-- Rust 77% hidden -->\n---\n"
    )
    assert run(root, draft).ok


def test_windows_line_endings_are_handled(career) -> None:
    root, _ = career
    assert run(root, "Reduced AWS costs by 35% using Kafka.\r\nBuilt it in Go for 120 engineers.\r\n").ok
    assert not run(root, "Reduced AWS costs by 35% using Kafka.\r\nServed 9 users.\r\n").ok


def test_non_ascii_text_and_names_round_trip(tmp_path: Path, clock) -> None:
    root = make_workspace(tmp_path / "ws")
    exp = ops.add_fact(root, "experience", {"employer": "Café Systèmes", "title": "Ingénieur", "start": "2021-01", "end": "2022-12"}, quote="à moi")
    ops.add_fact(root, "achievement", {"parent": exp.id, "text": "Réduit les coûts de ₹5 crore avec Kafka."}, quote="à moi")
    assert run(root, "J'ai réduit les coûts de ₹5 crore avec Kafka chez Café Systèmes.").ok
    assert not run(root, "J'ai réduit les coûts de ₹6 crore avec Kafka.").ok


def test_a_very_long_draft_is_checked_in_reasonable_time(career) -> None:
    root, _ = career
    import time

    start = time.monotonic()
    result = run(root, "\n".join(["Reduced AWS costs by 35% using Kafka-based pipelines."] * 400))
    assert result.ok and time.monotonic() - start < 20
````

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_memory_check.py -q`
Expected: collection error, `ImportError: cannot import name 'check' from 'careeros.core.memory'`.

- [ ] **Step 3: Create `careeros/core/memory/check.py`**

````python
"""The evidence check: do the checkable claims in a draft trace to the career memory?

Deterministic: no model judges prose and nothing leaves the machine. A pass means every *detected* claim
(numbers, years, durations, technologies, employers, schools, titles, certifications) is supported by the
memory. It never means every claim in the draft was detected; the result lists what was not evaluated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from careeros.core import models
from careeros.core.memory import facets, store
from careeros.core.memory.lexicon import Lexicon
from careeros.core.memory.normalize import metric_key, norm_org, norm_text, scan, split_sentences
from careeros.core.memory.store import Career, Fact

CODES = {
    "number": "CHK001", "year": "CHK002", "technology": "CHK003", "employer": "CHK004", "school": "CHK005",
    "title": "CHK006", "certification": "CHK007", "duration": "CHK008", "name": "CHK009",
}
FIX = {
    "CHK001": "remove the figure or reword without it; if it is true, add it with `careeros memory add`",
    "CHK002": "remove the year, or add the dated fact to your memory",
    "CHK003": "remove the technology, or add it as a skill or inside a real achievement if it is true",
    "CHK004": "remove the employer, or add the experience with `careeros memory add --kind experience`",
    "CHK005": "remove the school, or add the education with `careeros memory add --kind education`",
    "CHK006": "use a title from your experience records, or add the role if it is true",
    "CHK007": "remove the certification, or add it with `careeros memory add --kind certification`",
    "CHK008": "state a number of years your dated experience supports, or drop the figure",
    "CHK009": "remove or fix the name; if it is real and not a claim about you, pass --allow \"<name>\"",
    "CHK010": "rewrite the sentence so its claims come from one fact, or ask the user and add the fact",
    "CHK020": "confirm the fact with `careeros memory confirm <id>` (a person must do this)",
    "CHK030": "import your resume with `careeros memory import`, or add facts with `careeros memory add`",
    "CHK031": "run `careeros memory import` to review what changed",
}
NOT_EVALUATED_CATEGORIES = [
    "whether prose is true", "wording and meaning", "responsibilities, scope and team-size wording",
    "qualitative outcomes (improved, faster, reliable)", "a lowercase or sentence-initial unknown name with no cue",
    "technologies in neither the lexicon nor your memory", "numbers written as words", "languages other than English",
]
LIST_SECTIONS = {"skills", "technologies", "tech stack", "technical skills", "tools", "stack", "technical"}
_LIST_CUE = re.compile(
    r"\b(?:experience (?:with|in)|proficient (?:in|with)|skilled (?:in|with)|familiar (?:with|in)|such as|including|"
    r"stack|technologies|tech|tools)\s*:?\s", re.I)
_LIST_PREFIX = re.compile(r"^(?:skills|technologies|tech stack|technical skills|tools|stack|tech)\s*:", re.I)
_GREETING = re.compile(r"^(?:dear|hi|hello|hey|greetings|thanks|thank you|regards|best|sincerely|cheers|yours)\b", re.I)
_NAME = r"(?-i:[A-Z][\w&'’-]*)(?:\s+(?:(?-i:[A-Z][\w&'’-]*)|of|&))*"
_EMPLOYER_CUES = [
    re.compile(rf"\b(?:worked|working|employed|interned|served|volunteered)\b(?:\s+\w+){{0,3}}?\s+(?:at|for)\s+(?P<n>{_NAME})", re.I),
    re.compile(rf"\b(?:experience|time|tenure|years|stint)\s+(?:\w+\s+){{0,2}}?at\s+(?P<n>{_NAME})", re.I),
    re.compile(rf"\b(?:joined|left|leaving)\s+(?P<n>{_NAME})", re.I),
]
_SCHOOL_CUE = re.compile(
    r"\b(?:studied|studying|graduated|graduate|degree|bachelor(?:'s)?|master(?:'s)?|b\.?tech|m\.?tech|b\.?sc|m\.?sc|phd|mba|diploma)\b"
    rf"[^.;\n]*?\b(?:at|from)\s+(?P<n>{_NAME})", re.I)
_CERT_CUE = re.compile(rf"\b(?:certified|certification|certificate)\b(?:\s+(?:in|as|for|by|from))?\s+(?P<n>{_NAME})", re.I)
_TITLE_AT = re.compile(rf"(?P<t>{_NAME})\s+(?:at|@)\s+(?P<n>{_NAME})")
_PIPE = re.compile(r"^\s*(?:#{1,6}\s*)?(?P<e>[^|]+?)\s*\|\s*(?P<t>[^|]+?)\s*$")
_FILLER = re.compile(
    r"^(?:\s+(?:of|professional|hands-on|industry|commercial|practical|experience|in|with|using|working|work|"
    r"building|writing|developing))*\s+$|^\s*$", re.I)
_TOKEN = re.compile(r"(?-i:[A-Z][\w&'’-]*)")
_WORDS = re.compile(r"[\w'’&-]+")


@dataclass
class Atom:
    kind: str
    raw: str
    key: object
    start: int
    end: int
    status: str = "supported"        # supported | unsupported | review | allowed
    code: str = ""
    reason: str = ""
    holders: tuple[str, ...] = ()
    holder_status: tuple[str, ...] = ()


@dataclass
class Finding:
    code: str
    sentence: str
    atom: str
    reason: str
    fix: str

    def to_dict(self) -> dict:
        return {"code": self.code, "sentence": self.sentence, "atom": self.atom, "reason": self.reason, "fix": self.fix}


@dataclass
class CheckResult:
    supported: list[dict] = field(default_factory=list)
    review_required: list[Finding] = field(default_factory=list)
    not_evaluated: list[str] = field(default_factory=list)
    info: list[Finding] = field(default_factory=list)
    allowed_mentions: list[str] = field(default_factory=list)
    claimed_support: int = 0
    memory_stale: bool = False

    @property
    def ok(self) -> bool:
        return not self.review_required

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "supported": self.supported,
            "review_required": [f.to_dict() for f in self.review_required],
            "not_evaluated": {"sentences": self.not_evaluated, "categories": NOT_EVALUATED_CATEGORIES},
            "info": [f.to_dict() for f in self.info],
            "allowed_mentions": self.allowed_mentions,
            "supported_by_claimed": self.claimed_support,
            "memory_stale": self.memory_stale,
        }


@dataclass
class Record:
    """One place where claims co-occur: an achievement, project, experience, education, certification or skill."""

    id: str
    kind: str
    status: str
    techs: frozenset = frozenset()
    metrics: tuple = ()
    employer: str | None = None
    titles: frozenset = frozenset()
    school: str | None = None
    certs: frozenset = frozenset()
    span: tuple[int, int] | None = None
    points: frozenset = frozenset()


def _year_month(value: str, now_year: int, now_month: int) -> int:
    if value == "present":
        return now_year * 12 + now_month
    year, month = value.split("-")
    return int(year) * 12 + int(month)


def _title_variants(title: str) -> set[str]:
    head = re.split(r"[,(|–—-]\s", title, maxsplit=1)[0]
    return {v for v in (norm_text(title), norm_text(head)) if v}


def _strip_suffix_words(name: str, suffixes: frozenset[str]) -> str:
    words = name.split()
    while len(words) > 1 and words[-1].lower().strip(".,") in suffixes:
        words.pop()
    return " ".join(words)


def _word_re(text: str) -> re.Pattern:
    return re.compile(rf"(?<![\w]){re.escape(text)}(?![\w])", re.I)


def _overlaps(start: int, end: int, used: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in used)


class Memory:
    """The active facts, indexed for lookup."""

    def __init__(self, career: Career, lexicon: Lexicon, now: str) -> None:
        self.lx = lexicon
        self.now_year, self.now_month = int(now[:4]), int(now[5:7])
        self.records: list[Record] = []
        self.employers: dict[str, str] = {}   # display form -> normalised key
        self.schools: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.cert_names: list[str] = []
        self.known_words: set[str] = set()
        by_id = career.by_id()
        active = career.active()
        intervals: list[tuple[int, int]] = []
        for fact in active:
            self._collect_words(fact)
            record = self._record(fact, by_id)
            if record is None:
                continue
            self.records.append(record)
            if fact.kind == "experience":
                intervals.append((_year_month(str(fact.get("start")), self.now_year, self.now_month),
                                  _year_month(str(fact.get("end")), self.now_year, self.now_month)))
        self.months = self._union(intervals)
        self.has_content = any(
            f.kind in ("experience", "achievement", "project", "skill", "education", "certification") for f in active)

    @staticmethod
    def _union(intervals: list[tuple[int, int]]) -> int:
        total, last_end = 0, None
        for start, end in sorted(intervals):
            if last_end is None or start > last_end:
                total += max(0, end - start)
                last_end = end
            elif end > last_end:
                total += end - last_end
                last_end = end
        return total

    def _collect_words(self, fact: Fact) -> None:
        for key in ("employer", "school", "degree", "field", "location", "name", "category", "issuer", "title"):
            value = fact.get(key)
            if isinstance(value, str):
                self.known_words.update(w.lower() for w in _WORDS.findall(value))
        for headline in fact.get("headlines") or ():
            self.known_words.update(w.lower() for w in _WORDS.findall(str(headline)))

    def _register_name(self, table: dict[str, str], name: str) -> None:
        key = norm_org(name, self.lx.org_suffixes)
        table[name] = key
        table[_strip_suffix_words(name, self.lx.org_suffixes)] = key

    def _register_title(self, title: str) -> None:
        head = re.split(r"[,(|–—-]\s", title, maxsplit=1)[0].strip()
        for display in (title, head):
            if display:
                self.titles[display] = norm_text(display)

    def _record(self, fact: Fact, by_id: dict[str, Fact]) -> Record | None:
        lx, kind = self.lx, fact.kind
        techs = {lx.canonical_tech(t) or t for t in (fact.get("technologies") or ()) if isinstance(t, str)}
        metrics = tuple((*metric_key(m["value"], m["unit"]), bool(m.get("plus"))) for m in (fact.get("metrics") or ()))
        rec = Record(fact.id, kind, fact.status, frozenset(techs), metrics)
        if kind == "skill":
            name = str(fact.get("name"))
            rec.techs = frozenset({lx.canonical_tech(name) or name})
        elif kind in ("experience", "achievement"):
            source = fact if kind == "experience" else by_id.get(str(fact.get("parent")))
            if source is None or source.kind != "experience":
                return rec if kind == "achievement" else None
            employer, title = str(source.get("employer")), str(source.get("title"))
            end = str(source.get("end"))
            rec.employer = norm_org(employer, lx.org_suffixes)
            rec.titles = frozenset(_title_variants(title))
            rec.span = (int(str(source.get("start"))[:4]), self.now_year if end == "present" else int(end[:4]))
            if kind == "experience":
                self._register_name(self.employers, employer)
                self._register_title(title)
        elif kind == "project":
            pass
        elif kind == "education":
            school = str(fact.get("school"))
            rec.school = norm_org(school, lx.org_suffixes)
            self._register_name(self.schools, school)
            if fact.get("end"):
                rec.points = frozenset({int(str(fact.get("end"))[:4])})
        elif kind == "certification":
            name = str(fact.get("name"))
            rec.certs = frozenset({(lx.certs.get(name.lower()) or name).lower()})
            self.cert_names.append(name)
            if fact.get("year"):
                rec.points = frozenset({int(str(fact.get("year"))[:4])})
        elif kind == "identity":
            rec.titles = frozenset(v for h in (fact.get("headlines") or ()) for v in _title_variants(str(h)))
            for headline in fact.get("headlines") or ():
                self._register_title(str(headline))
        else:
            return None
        return rec

    def holds(self, record: Record, atom: Atom) -> bool:
        kind, key = atom.kind, atom.key
        if kind == "technology":
            return key in record.techs
        if kind == "number":
            value, unit, plus = key
            return any(m[0] == value and m[1] == unit and (m[2] or not plus) for m in record.metrics)
        if kind == "year":
            return bool(record.span and record.span[0] <= key <= record.span[1]) or key in record.points
        if kind == "employer":
            return record.employer == key
        if kind == "school":
            return record.school == key
        if kind == "title":
            return key in record.titles
        if kind == "certification":
            return key in record.certs
        return False

    def holders(self, atom: Atom) -> list[Record]:
        return [r for r in self.records if self.holds(r, atom)]


class Checker:
    def __init__(self, memory: Memory, allow: list[str], against: tuple[str, str] | None) -> None:
        lx = self.lx = memory.lx
        self.m = memory
        self.allow_norm = {norm_text(t) for t in allow}
        self.allow_words = {w.lower() for t in allow for w in _WORDS.findall(t)}
        self.allow_numbers = {metric_key(n.value, n.unit) for t in allow for n in scan(t).numbers}
        self.allow_techs = {c for t in allow if (c := lx.canonical_tech(t))}
        self.against_names = [n for n in (against or ()) if n]
        self.against_title_keys = {norm_text(against[1])} if against and against[1] else set()
        self.against_company_key = norm_org(against[0], lx.org_suffixes) if against and against[0] else None
        seniority = "|".join(sorted(map(re.escape, lx.seniority), key=len, reverse=True))
        roles = "|".join(sorted(map(re.escape, lx.role_nouns), key=len, reverse=True))
        self._sen_run = re.compile(
            rf"\b(?:(?:{seniority})\.?\s+)+(?:[A-Za-z][\w&+/-]*\s+){{0,3}}?(?:{roles})\b"
            rf"(?:\s+(?:of|for|in)\s+[A-Za-z][\w&-]*(?:\s+[A-Za-z][\w&-]*){{0,2}})?", re.I)
        self._head_of = re.compile(
            r"\b(?:head|chief|director|manager|vice president)\s+(?:of|for)\s+(?-i:[A-Z])[\w&-]*"
            r"(?:\s+(?-i:[A-Z])[\w&-]*){0,2}", re.I)
        self._acronym = re.compile(r"\b(?:VP|CTO|CEO|CIO|COO|CFO)\b(?:\s+(?:of|for)\s+[A-Za-z][\w&-]*(?:\s+[A-Za-z][\w&-]*){0,2})?")
        self._cue_role = re.compile(
            r"(?:\bas\s+(?:an?\s+|the\s+)?|\bI(?:'m| am| was)\s+(?:an?\s+|the\s+)?|\bwas\s+(?:an?\s+|the\s+)?)"
            rf"(?P<t>(?:[\w&+/.-]+\s+){{0,3}}(?:{roles}))\b", re.I)
        self._known_titles = sorted(memory.titles, key=len, reverse=True)

    # ------------------------------------------------------------ detection
    def detect(self, text: str) -> list[Atom]:
        atoms: list[Atom] = []
        used: list[tuple[int, int]] = []
        analysis = facets.analyze(text, self.lx)

        def add(atom: Atom) -> None:
            atoms.append(atom)
            used.append((atom.start, atom.end))

        def free(start: int, end: int) -> bool:
            return not _overlaps(start, end, used)

        for d in analysis.scan.durations:
            tied = any(
                t.start >= d.end and t.start - d.end <= 40 and _FILLER.match(text[d.end:t.start])
                for t in analysis.techs)
            add(Atom("duration", text[d.start:d.end], (d.years, d.plus, tied), d.start, d.end))
        self._certifications(text, add, free)
        self._titles(text, add, free)
        for t in analysis.techs:
            if free(t.start, t.end):
                add(Atom("technology", text[t.start:t.end], t.canonical, t.start, t.end))
        used.extend(analysis.version_spans)
        for n in analysis.scan.numbers:
            if free(n.start, n.end):
                add(Atom("number", n.raw, (*metric_key(n.value, n.unit), n.plus), n.start, n.end))
        for y in analysis.scan.years:
            if free(y.start, y.end):
                add(Atom("year", text[y.start:y.end], y.year, y.start, y.end))
        self._known_names(text, add, free)
        self._cues(text, add, free)
        for name in self.against_names:
            for match in _word_re(name).finditer(text):
                if free(match.start(), match.end()):
                    used.append(match.span())
        if not _GREETING.match(text):
            self._residual(text, add, free)
        atoms.sort(key=lambda a: a.start)
        return atoms

    def _certifications(self, text: str, add, free) -> None:
        for alias, canonical in sorted(self.lx.cert_aliases, key=lambda p: len(p[0]), reverse=True):
            pattern = _word_re(alias) if len(alias) > 6 else re.compile(rf"(?<![\w]){re.escape(alias)}(?![\w])")
            for match in pattern.finditer(text):
                if free(match.start(), match.end()):
                    add(Atom("certification", match.group(0), canonical.lower(), match.start(), match.end()))
        for name in sorted(self.m.cert_names, key=len, reverse=True):
            key = (self.lx.certs.get(name.lower()) or name).lower()
            for match in _word_re(name).finditer(text):
                if free(match.start(), match.end()):
                    add(Atom("certification", match.group(0), key, match.start(), match.end()))

    def _has_role(self, text: str) -> bool:
        words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
        return bool(words & (self.lx.role_nouns | self.lx.title_words))

    def _titles(self, text: str, add, free) -> None:
        pipe = _PIPE.match(text)
        if pipe and (self._has_role(pipe.group("t")) or norm_text(pipe.group("t")) in set(self.m.titles.values())):
            if free(pipe.start("e"), pipe.end("e")):
                self._employer_atom(pipe.group("e").strip(), pipe.start("e"), pipe.end("e"), add)
            if free(pipe.start("t"), pipe.end("t")):
                self._title_atom(pipe.group("t"), pipe.start("t"), pipe.end("t"), add)
            return
        for pattern in (self._sen_run, self._head_of, self._acronym):
            for match in pattern.finditer(text):
                if free(match.start(), match.end()):
                    self._title_atom(match.group(0), match.start(), match.end(), add)
        for match in self._cue_role.finditer(text):
            if free(match.start("t"), match.end("t")):
                self._title_atom(match.group("t"), match.start("t"), match.end("t"), add)
        for display in self._known_titles:
            for match in _word_re(display).finditer(text):
                if free(match.start(), match.end()):
                    self._title_atom(match.group(0), match.start(), match.end(), add)

    def _title_atom(self, raw: str, start: int, end: int, add) -> None:
        raw = raw.strip().rstrip(".,;:")
        key = norm_text(raw)
        kind = "context" if key in self.against_title_keys else "title"
        add(Atom(kind, raw, key, start, start + len(raw)))

    def _employer_atom(self, raw: str, start: int, end: int, add) -> None:
        key = norm_org(raw, self.lx.org_suffixes)
        kind = "context" if self.against_company_key and key == self.against_company_key else "employer"
        add(Atom(kind, raw, key, start, end))

    def _known_names(self, text: str, add, free) -> None:
        for kind, table in (("employer", self.m.employers), ("school", self.m.schools)):
            for display in sorted(table, key=len, reverse=True):
                for match in _word_re(display).finditer(text):
                    if free(match.start(), match.end()) and not self._continues_name(text, match.end()):
                        add(Atom(kind, match.group(0), table[display], match.start(), match.end()))

    def _continues_name(self, text: str, end: int) -> bool:
        """'Acme Systems' is not 'Acme': a known name followed by another capitalised word is a different name."""
        follower = re.match(r"\s+([A-Z][\w&'’-]*)", text[end:])
        if not follower:
            return False
        word = follower.group(1).lower()
        return word not in self.lx.stoplist and word not in self.lx.org_suffixes

    @staticmethod
    def _trim(name: str) -> str:
        words = name.split()
        while words and words[-1].lower() in ("of", "&"):
            words.pop()
        return " ".join(words)

    def _cues(self, text: str, add, free) -> None:
        for pattern in _EMPLOYER_CUES:
            for match in pattern.finditer(text):
                name, start = self._trim(match.group("n")), match.start("n")
                if name and free(start, start + len(name)):
                    add(Atom("employer", name, norm_org(name, self.lx.org_suffixes), start, start + len(name)))
        for match in _SCHOOL_CUE.finditer(text):
            name, start = self._trim(match.group("n")), match.start("n")
            if name and free(start, start + len(name)):
                add(Atom("school", name, norm_org(name, self.lx.org_suffixes), start, start + len(name)))
        for match in _CERT_CUE.finditer(text):
            name, start = self._trim(match.group("n")), match.start("n")
            if name and free(start, start + len(name)):
                add(Atom("certification", name, name.lower(), start, start + len(name)))
        for match in _TITLE_AT.finditer(text):
            title, company, start = self._trim(match.group("t")), self._trim(match.group("n")), match.start("n")
            if self._has_role(title) and free(start, start + len(company)):
                self._employer_atom(company, start, start + len(company), add)

    def _residual(self, text: str, add, free) -> None:
        """Capitalised spans that nothing else explained: possible employers, schools or people."""
        runs: list[list[re.Match]] = []
        for token in _TOKEN.finditer(text):
            if not free(token.start(), token.end()):
                runs.append([])
                continue
            word = token.group(0).lower()
            if word in self.lx.stoplist or word in self.m.known_words or word in self.allow_words:
                runs.append([])
            elif runs and runs[-1] and text[runs[-1][-1].end():token.start()] == " ":
                runs[-1].append(token)
            else:
                runs.append([token])
        for run in runs:
            if run and text[:run[0].start()].strip(" #*-•>\t") == "":
                run = run[1:]  # sentence-initial capital: not evidence of a name
            if run:
                raw = text[run[0].start():run[-1].end()]
                add(Atom("name", raw, norm_text(raw), run[0].start(), run[-1].end()))

    # ------------------------------------------------------------ judgement
    def _allowed(self, atom: Atom) -> bool:
        if atom.kind == "technology":
            return atom.key in self.allow_techs or norm_text(atom.raw) in self.allow_norm
        if atom.kind == "number":
            return (atom.key[0], atom.key[1]) in self.allow_numbers
        if atom.kind == "name":
            return atom.key in self.allow_norm or {w.lower() for w in _WORDS.findall(atom.raw)} <= self.allow_words
        return atom.key in self.allow_norm or norm_text(atom.raw) in self.allow_norm

    def _support(self, atom: Atom) -> None:
        if atom.kind == "name":
            if self._allowed(atom):
                atom.status = "allowed"
            else:
                atom.status, atom.code = "review", "CHK009"
                atom.reason = f"{atom.raw!r} looks like a name, but it is not in your memory"
            return
        if atom.kind == "duration":
            years, _plus, tied = atom.key
            derived = self.m.months // 12
            if tied:
                atom.status, atom.code = "review", "CHK008"
                atom.reason = f"{atom.raw}: years with a specific technology cannot be derived from your dated experience"
            elif self.m.months == 0 or years > derived:
                atom.status, atom.code = "unsupported", "CHK008"
                atom.reason = f"{atom.raw}: your dated experience covers {derived} year(s)"
            return
        holders = self.m.holders(atom)
        if holders:
            atom.holders = tuple(r.id for r in holders)
            atom.holder_status = tuple(r.status for r in holders)
        elif self._allowed(atom):
            atom.status = "allowed"
        else:
            atom.status, atom.code = "unsupported", CODES[atom.kind]
            atom.reason = f"{atom.raw!r} is not supported by any fact in your memory"

    def _list_exempt(self, sentence: str, atoms: list[Atom], list_line: bool) -> bool:
        if any(a.kind != "technology" for a in atoms):
            return False
        return list_line or bool(_LIST_CUE.search(sentence))

    def judge(self, sentence: str, list_line: bool, result: CheckResult, require_confirmed: bool) -> None:
        atoms = [a for a in self.detect(sentence) if a.kind != "context"]
        if not atoms:
            result.not_evaluated.append(sentence)
            return
        for atom in atoms:
            self._support(atom)
        findings = [Finding(a.code, sentence, a.raw, a.reason, FIX[a.code]) for a in atoms if a.status in ("unsupported", "review")]
        if not findings:
            findings = self._relationship(sentence, atoms, list_line)
        result.review_required.extend(findings)
        if findings:
            return
        for atom in atoms:
            if atom.status == "allowed":
                result.allowed_mentions.append(atom.raw)
            elif atom.status == "supported":
                claimed_only = bool(atom.holder_status) and all(s == "claimed" for s in atom.holder_status)
                result.supported.append({
                    "sentence": sentence, "kind": atom.kind, "claim": atom.raw,
                    "facts": list(atom.holders), "claimed_only": claimed_only,
                })
                if claimed_only:
                    result.claimed_support += 1
                    if require_confirmed:
                        result.review_required.append(
                            Finding("CHK020", sentence, atom.raw, "supported only by claimed facts", FIX["CHK020"]))

    def _relationship(self, sentence: str, atoms: list[Atom], list_line: bool) -> list[Finding]:
        """Claims in one sentence must come from one fact; an allowed term can never supply that fact."""
        related = [a for a in atoms if a.kind not in ("duration", "name") and a.status in ("supported", "allowed")]
        if len(related) < 2 or self._list_exempt(sentence, related, list_line):
            return []
        supported = [a for a in related if a.status == "supported"]
        allowed = [a for a in related if a.status == "allowed"]
        if allowed and supported:
            names = ", ".join(a.raw for a in allowed)
            return [Finding("CHK010", sentence, names,
                            f"{names} was allowed, but no fact in your memory ties it to the other claims in this sentence",
                            FIX["CHK010"])]
        if not supported:
            return []
        holders = [r for r in self.m.records if all(self.m.holds(r, a) for a in supported)]
        if not holders:
            detail = "; ".join(f"{a.raw} ({', '.join(a.holders) or 'no fact'})" for a in supported)
            return [Finding("CHK010", sentence, ", ".join(a.raw for a in supported),
                            f"each claim is supported on its own, but no single fact holds them together: {detail}",
                            FIX["CHK010"])]
        for atom in supported:
            atom.holders = tuple(r.id for r in holders)
            atom.holder_status = tuple(r.status for r in holders)
        return []


def _prepare_lines(draft: str) -> list[tuple[str, bool]]:
    """Each content line of the draft with markdown stripped, and whether it sits in a skills-style section."""
    draft = re.sub(r"<!--.*?-->", "", draft, flags=re.S)
    out: list[tuple[str, bool]] = []
    section, in_fence = "", False
    for raw in draft.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip() or re.fullmatch(r"\s*([-=_*]\s*){3,}", line):
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        bold_only = re.match(r"^\s*\*\*([^*]+?)\*\*:?\s*$", line)
        if bold_only or (heading and not _PIPE.match(line)):
            section = norm_text((heading or bold_only).group(1))
            continue
        if _PIPE.match(line):
            text = line.strip()
        else:
            text = re.sub(r"^\s*(?:#{1,6}\s+|[-*+•]\s+|\d+[.)]\s+)", "", line)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("**", "").replace("`", "").strip()
        if text:
            out.append((text, section in LIST_SECTIONS))
    return out


def run_check(
    root: Path,
    draft: str,
    *,
    allow: list[str] | None = None,
    against: tuple[str, str] | None = None,
    require_confirmed: bool = False,
    career: Career | None = None,
) -> CheckResult:
    """Check a draft against the career memory. The caller must make sure the memory has no errors."""
    root = Path(root)
    career = career or store.load_career(root)
    memory = Memory(career, store.lexicon_for(root, career), models.utc_now())
    result = CheckResult()
    result.memory_stale = any(i.code == "MEM006" for i in career.issues)
    if result.memory_stale:
        result.info.append(Finding("CHK031", "", "", "a source file changed since it was imported", FIX["CHK031"]))
    if not memory.has_content:
        result.review_required.append(Finding("CHK030", "", "", "the career memory has no usable facts", FIX["CHK030"]))
        return result
    checker = Checker(memory, allow or [], against)
    for line, in_list in _prepare_lines(draft):
        list_line = in_list or bool(_LIST_PREFIX.match(line))
        for sentence in split_sentences(line):
            checker.judge(sentence, list_line, result, require_confirmed)
    return result
````

- [ ] **Step 4: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/test_memory_check.py -q`
Expected: `82 passed`.

- [ ] **Step 5: Run everything, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `382 passed`.

````bash
git add careeros/core/memory/check.py tests/test_memory_check.py
git commit -m "memory: evidence check with layered detection and single-fact relationships" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 5: The resume importer

**Files:**
- Create: `careeros/core/memory/importer.py`, `tests/test_memory_importer.py`, `tests/fixtures/resume_sample.md`

**Interfaces:**
- Consumes: Tasks 1-4 (`backup`, `store`, `ops.{MemoryOpError, event_spec, open_career}`, `facets`, `normalize`, `check` in the tests).
- Produces (`importer.py`): `parse_resume(text, lexicon) -> ParseResult(candidates, skipped, errors)`; `plan_import(root, source_rel="resume.md") -> ImportPlan` (read-only; `.changes`, `.skipped`, `.errors`, `.count(result)`, `.pending_review`, `.actionable`); `apply_import(root, source_rel="resume.md", *, actor="user") -> ImportResult(status, plan, backup)` where `status` is `"complete"` or `"noop"`. `Change` has `.result` (`unchanged|added|changed|removed`), `.action`, `.candidate`, `.fact`, `.label`.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/resume_sample.md` (invented data):

````markdown
# Resume - Jordan Example

<!-- Source of truth for tailored resumes. Invented data for tests. -->

jordan@example.com | +91 90000 00000 | linkedin.com/in/jordan-example

## Summary

Backend and platform engineer with 4 years of experience building reliable distributed systems.

## Experience

### Acme Corp | Senior Software Engineer
Bengaluru - January 2025 - Present

- Led a Kubernetes migration for 40+ services, cutting deploy time by 60%.
- Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.
- Built an internal developer platform in Go used by 120 engineers.
- Tech: Go, Python, Kubernetes, AWS, Kafka, Terraform

### Globex Systems Pvt Ltd | Software Engineer 2
Pune - March 2022 - December 2024

- Designed a multi-region PostgreSQL setup with 99.95% availability.
- Cut incident response time from 45 minutes to 12 minutes with Prometheus and Grafana alerts.
- Tech: PostgreSQL, Prometheus, Grafana

## Education

**Example Institute of Technology, Jabalpur** - Jun 2020
B.Tech. in Computer Science | CGPA: 8.1/10

## Projects

**kubewatch - Cluster event notifier (Go, Kubernetes)** - 2024
Open-source tool that streams Kubernetes events, used by 300+ teams.

## Skills

**Languages:** Go, Python, SQL
**Cloud & Infra:** AWS (Lambda, EC2, S3), Terraform, Kubernetes
**Data:** PostgreSQL, Kafka

## Certifications

- Certified Kubernetes Administrator, 2023

## Awards

- Hackathon winner, 2021
````

- [ ] **Step 2: Write the failing test**

Create `tests/test_memory_importer.py`:

````python
"""Importing resume.md: parse, diff, apply, protect confirmed and manual facts, roll back."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import make_workspace, snapshot

from careeros.core import ledger
from careeros.core.memory import check, importer, ops, store
from careeros.core.memory.lexicon import load_lexicon
from careeros.core.memory.ops import MemoryOpError

FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"


@pytest.fixture
def ws(tmp_path: Path, clock) -> Path:
    root = make_workspace(tmp_path / "ws")
    shutil.copy(FIXTURE, root / "resume.md")
    return root


def edit_resume(root: Path, old: str, new: str) -> None:
    path = root / "resume.md"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def facts_of(root: Path, kind: str) -> list:
    return [f for f in store.load_career(root).facts if f.kind == kind]


def parse(text: str):
    return importer.parse_resume(text, load_lexicon())


# --- parsing -------------------------------------------------------------------------------

def test_the_fixture_parses_into_every_kind_and_reports_what_it_skipped() -> None:
    result = parse(FIXTURE.read_text(encoding="utf-8"))
    assert result.errors == []
    kinds = [c.kind for c in result.candidates]
    assert kinds.count("experience") == 2 and kinds.count("education") == 1 and kinds.count("project") == 1
    assert kinds.count("certification") == 1 and kinds.count("skill") == 11
    assert any("preamble" in s for s in result.skipped) and any("Awards" in s for s in result.skipped)


def test_experience_dates_technologies_and_bullets() -> None:
    result = parse(FIXTURE.read_text(encoding="utf-8"))
    acme = next(c for c in result.candidates if c.kind == "experience" and c.fields["employer"] == "Acme Corp")
    assert acme.fields["start"] == "2025-01" and acme.fields["end"] == "present"
    assert acme.fields["technologies"] == ["Go", "Python", "Kubernetes", "AWS", "Kafka", "Terraform"]
    bullets = [c for c in result.candidates if c.kind == "achievement" and c.parent_key == acme.key]
    assert len(bullets) == 3 and not any("Tech:" in b.fields["text"] for b in bullets)
    reduced = next(b for b in bullets if "35%" in b.fields["text"])
    assert reduced.fields["metrics"][0]["unit"] == "percent" and reduced.fields["technologies"] == ["AWS", "Kafka"]


@pytest.mark.parametrize("line,expected", [
    ("Bengaluru - January 2025 - Present", ("2025-01", "present")),
    ("03/2020 - 06/2022", ("2020-03", "2022-06")),
    ("2021 – 2023", ("2021-01", "2023-12")),
    ("Remote | Sept 2019 — Mar 2021", ("2019-09", "2021-03")),
])
def test_date_ranges(line: str, expected: tuple[str, str]) -> None:
    text = f"## Experience\n\n### Acme | Engineer\n{line}\n\n- Did a thing.\n"
    cand = next(c for c in parse(text).candidates if c.kind == "experience")
    assert (cand.fields["start"], cand.fields["end"]) == expected


def test_education_projects_skills_and_certifications() -> None:
    result = parse(FIXTURE.read_text(encoding="utf-8"))
    edu = next(c for c in result.candidates if c.kind == "education")
    assert edu.fields["school"] == "Example Institute of Technology, Jabalpur" and edu.fields["end"] == "2020-06"
    assert edu.fields["degree"] == "B.Tech. in Computer Science" and edu.fields["metrics"][0]["unit"] == "score"
    project = next(c for c in result.candidates if c.kind == "project")
    assert project.fields["technologies"] == ["Go", "Kubernetes"] and "300+" in project.fields["text"]
    skills = {c.fields["name"]: c.fields["category"] for c in result.candidates if c.kind == "skill"}
    assert skills["Lambda"] == "Cloud & Infra" and skills["Go"] == "Languages" and skills["Kafka"] == "Data"
    cert = next(c for c in result.candidates if c.kind == "certification")
    assert cert.fields == {"name": "Certified Kubernetes Administrator", "year": "2023"}


@pytest.mark.parametrize("text,fragment", [
    ("## Experience\n\n### Acme Engineer\nJan 2024 - Present\n- x\n", "expected '### Employer | Title'"),
    ("## Experience\n\n- a bullet with no heading\n", "expected '### Employer | Title'"),
    ("## Experience\n\n### Acme | Engineer\n\n- A bullet but no dates.\n", "no date range"),
    ("## Education\n\n**Some School**\nB.Sc.\n", "needs a date"),
    ("## Education\n\n**Some School** - Jun 2020\n", "no degree line"),
])
def test_structure_the_grammar_cannot_place_is_a_hard_error(text: str, fragment: str) -> None:
    assert any(fragment in e for e in parse(text).errors)


def test_a_hard_error_writes_nothing(ws: Path) -> None:
    (ws / "resume.md").write_text("## Experience\n\n### Acme Engineer\n- no pipe\n", encoding="utf-8")
    before = snapshot(ws)
    with pytest.raises(MemoryOpError, match="expected '### Employer | Title'"):
        importer.apply_import(ws)
    assert snapshot(ws) == before and not (ws / "career").exists()


def test_a_missing_source_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(MemoryOpError, match="does not exist"):
        importer.plan_import(make_workspace(tmp_path / "ws"))


# --- import --------------------------------------------------------------------------------

def test_importing_creates_claimed_facts_with_sources_and_leaves_the_resume_alone(ws: Path) -> None:
    resume = (ws / "resume.md").read_bytes()
    result = importer.apply_import(ws)
    assert result.status == "complete" and (ws / "resume.md").read_bytes() == resume
    career = store.load_career(ws)
    assert career.issues == []
    assert {f.status for f in career.facts} == {"claimed"} and {f.get("origin") for f in career.facts} == {"imported"}
    cost = next(f for f in career.facts if "35%" in str(f.get("text")))
    assert cost.get("source")["kind"] == "resume" and cost.get("source")["path"] == "resume.md"
    assert cost.get("source")["line"] > 0 and len(cost.get("source")["sha256"]) == 64
    assert cost.get("parent") in career.by_id() and career.by_id()[cost.get("parent")].kind == "experience"
    summary = next(f for f in career.facts if f.get("section") == "summary")
    assert summary.get("parent") is None
    sources = store.load_sources(ws)
    assert sources["resume.md"]["sha256"] == store.sha256_hex(resume)


def test_the_import_is_backed_up_manifested_and_recorded_in_the_ledger(ws: Path) -> None:
    result = importer.apply_import(ws)
    manifest = json.loads((result.backup / "manifest.json").read_text())
    assert manifest["status"] == "complete" and manifest["kind"] == "memory-import"
    paths = {f["path"] for f in manifest["files"]}
    assert "career/sources.yaml" in paths and len(paths) > 20
    assert all(f["after_sha256"] for f in manifest["files"] if f["existed"] is False)
    events = ledger.read_events(ws)
    types = [e["type"] for e in events]
    assert types.count("memory.fact_added") == len(store.load_career(ws).facts)
    assert types[-1] == "memory.imported" and "manifest.json" in events[-1]["artifacts"][0]
    assert ledger.verify_chain(ws) == []


def test_importing_twice_is_a_no_op(ws: Path) -> None:
    importer.apply_import(ws)
    before, events = snapshot(ws), ledger.read_events(ws)
    backups = list((ws / ".careeros" / "backups").iterdir())
    assert importer.apply_import(ws).status == "noop"
    assert snapshot(ws) == before and ledger.read_events(ws) == events
    assert list((ws / ".careeros" / "backups").iterdir()) == backups


def test_the_diff_is_deterministic_and_a_dry_run_writes_nothing(ws: Path) -> None:
    before = snapshot(ws)
    one = [(c.result, c.action, c.label) for c in importer.plan_import(ws).changes]
    two = [(c.result, c.action, c.label) for c in importer.plan_import(ws).changes]
    assert one == two and snapshot(ws) == before and not (ws / "career").exists()


def test_a_changed_line_updates_a_claimed_fact_in_place(ws: Path) -> None:
    importer.apply_import(ws)
    fact_id = next(f.id for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    edit_resume(ws, "Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.",
                "Reduced AWS costs by 38% by moving batch jobs to Kafka-based pipelines.")
    plan = importer.plan_import(ws)
    assert [(c.result, c.action) for c in plan.changes if c.result != "unchanged"] == [("changed", "update")]
    importer.apply_import(ws)
    updated = store.load_career(ws).by_id()[fact_id]
    assert "38%" in updated.get("text") and updated.get("metrics")[0]["value"] == 38.0 and updated.status == "claimed"
    assert len(facts_of(ws, "achievement")) == 6  # not removed + added


def test_a_confirmed_fact_is_never_overwritten_and_is_listed_for_review(ws: Path) -> None:
    importer.apply_import(ws)
    fact = next(f for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    ops.confirm_fact(ws, fact.id, confirm=lambda p: True)
    original = store.load_career(ws).by_id()[fact.id].path.read_bytes()
    edit_resume(ws, "Reduced AWS costs by 35%", "Reduced AWS costs by 50%")
    plan = importer.plan_import(ws)
    assert plan.pending_review == 1
    result = importer.apply_import(ws)
    assert result.status == "complete" and fact.path.read_bytes() == original
    assert "35%" in store.load_career(ws).by_id()[fact.id].get("text")
    assert importer.apply_import(ws).status == "noop"  # reviewed state is recorded...
    assert importer.plan_import(ws).pending_review == 1  # ...but the review item stays visible
    assert not check.run_check(ws, "Reduced AWS costs by 50% using Kafka.").ok


def test_a_line_removed_from_the_resume_marks_its_fact_stale_and_keeps_it(ws: Path) -> None:
    importer.apply_import(ws)
    edit_resume(ws, "- Built an internal developer platform in Go used by 120 engineers.\n", "")
    result = importer.apply_import(ws)
    assert result.plan.count("removed") == 1
    stale = [f for f in store.load_career(ws).facts if f.get("stale")]
    assert len(stale) == 1 and "120 engineers" in stale[0].get("text") and stale[0].status == "claimed"
    assert any(i.code == "MEM008" for i in store.load_career(ws).issues)
    assert check.run_check(ws, "Built an internal developer platform in Go used by 120 engineers.").ok
    edit_resume(ws, "- Reduced AWS costs", "- Built an internal developer platform in Go used by 120 engineers.\n- Reduced AWS costs")
    importer.apply_import(ws)
    assert not any(f.get("stale") for f in store.load_career(ws).facts)


def test_manual_facts_and_edited_imported_facts_are_never_touched(ws: Path) -> None:
    importer.apply_import(ws)
    manual = ops.add_fact(ws, "preference", {"text": "no fintech roles"}, quote="I would rather avoid fintech")
    edited = next(f for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    ops.update_fact(ws, edited.id, {"text": "Reduced AWS costs by 35% (my wording)."})
    before = {p: p.read_bytes() for p in (manual.path, store.load_career(ws).by_id()[edited.id].path)}
    edit_resume(ws, "Reduced AWS costs by 35% by moving", "Reduced AWS costs by 41% by moving")
    importer.apply_import(ws)
    assert {p: p.read_bytes() for p in before} == before


def test_a_reworded_line_is_matched_as_a_change_not_a_removal_plus_addition(ws: Path) -> None:
    importer.apply_import(ws)
    edit_resume(ws, "Led a Kubernetes migration for 40+ services, cutting deploy time by 60%.",
                "Led a Kubernetes migration for 40+ services, cutting deployment time by 60%.")
    plan = importer.plan_import(ws)
    changed = [c for c in plan.changes if c.result != "unchanged"]
    assert [(c.result, c.action) for c in changed] == [("changed", "update")]


def test_a_changed_resume_makes_the_memory_stale_until_it_is_imported_again(ws: Path) -> None:
    importer.apply_import(ws)
    assert not any(i.code == "MEM006" for i in store.load_career(ws).issues)
    edit_resume(ws, "Jordan Example", "Jordan Example Jr")
    assert any(i.code == "MEM006" for i in store.load_career(ws).issues)
    result = check.run_check(ws, "Reduced AWS costs by 35% using Kafka.")
    assert result.memory_stale and [f.code for f in result.info] == ["CHK031"]
    importer.apply_import(ws)
    assert not any(i.code == "MEM006" for i in store.load_career(ws).issues)


def test_a_missing_source_file_is_reported_by_validation(ws: Path) -> None:
    importer.apply_import(ws)
    (ws / "resume.md").unlink()
    assert any(i.code == "MEM007" for i in store.load_career(ws).issues)


# --- atomicity -----------------------------------------------------------------------------

def test_a_ledger_failure_during_import_restores_every_file(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = snapshot(ws)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(importer.ledger, "append_events", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        importer.apply_import(ws)
    monkeypatch.undo()
    assert snapshot(ws) == before
    manifests = list((ws / ".careeros" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1 and json.loads(manifests[0].read_text())["status"] == "rolled_back"
    assert not ledger.read_events(ws) and importer.apply_import(ws).status == "complete"


def test_a_failure_part_way_through_the_writes_restores_changed_files(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer.apply_import(ws)
    edit_resume(ws, "Reduced AWS costs by 35%", "Reduced AWS costs by 36%")
    edit_resume(ws, "Cut incident response time from 45 minutes", "Cut incident response time from 50 minutes")
    before = snapshot(ws)
    real, calls = store.atomic_write_bytes, {"n": 0}

    def flaky(path: Path, data: bytes) -> None:
        if "career" in Path(path).parts and "achievements" in Path(path).parts:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(store, "atomic_write_bytes", flaky)
    with pytest.raises(OSError, match="disk full"):
        importer.apply_import(ws)
    monkeypatch.undo()
    assert snapshot(ws) == before and ledger.verify_chain(ws) == []


def test_a_repeated_line_is_imported_once_and_reported() -> None:
    text = "## Experience\n\n### Acme | Engineer\nJan 2024 - Present\n\n- Built a thing.\n- Built a thing.\n"
    result = parse(text)
    assert [c.kind for c in result.candidates].count("achievement") == 1
    assert any("duplicate" in s for s in result.skipped)


def test_windows_line_endings_in_the_resume_are_handled() -> None:
    text = FIXTURE.read_text(encoding="utf-8").replace("\n", "\r\n")
    assert parse(text).errors == [] and len(parse(text).candidates) == len(parse(FIXTURE.read_text(encoding="utf-8")).candidates)
````

- [ ] **Step 3: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_memory_importer.py -q`
Expected: collection error, `ImportError: cannot import name 'importer' from 'careeros.core.memory'`.

- [ ] **Step 4: Create `careeros/core/memory/importer.py`**

````python
"""Import resume.md into the career memory: a deterministic parse, a reviewable diff, an atomic apply.

The importer only ever creates `claimed` facts. It never overwrites a confirmed fact, never touches a
manual one, and never deletes anything: a line that disappeared from the resume marks its fact `stale`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from careeros.core import backup as backup_helpers
from careeros.core import ids, ledger, models, versions
from careeros.core.memory import facets, store
from careeros.core.memory.lexicon import Lexicon
from careeros.core.memory.normalize import norm_text, split_sentences
from careeros.core.memory.ops import MemoryOpError, event_spec, open_career
from careeros.core.memory.store import Fact, Transaction
from careeros.core.workspace import WorkspaceLock

SECTION_NAMES = {
    "summary": ("summary", "profile", "about", "about me", "objective"),
    "experience": ("experience", "work experience", "professional experience", "employment", "employment history"),
    "education": ("education",),
    "projects": ("projects", "personal projects", "open source"),
    "skills": ("skills", "technical skills", "technologies"),
    "certifications": ("certifications", "certificates", "certification"),
}
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_POINT = (r"(?:(?P<{p}mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(?P<{p}y>\d{{4}})"
          r"|(?P<{p}m>\d{{1,2}})/(?P<{p}yy>\d{{4}})|(?P<{p}yr>\d{{4}}))")
_RANGE = re.compile(
    _POINT.format(p="a") + r"\s*[-–—]\s*(?:" + _POINT.format(p="b") + r"|(?P<present>present|current|now)\b)", re.I)
_TECH_LINE = re.compile(r"^(?:tech(?:nologies)?(?:\s+stack)?|stack)\s*:\s*(.+)$", re.I)
_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*[-–—,:]?\s*(.*)$")
_CATEGORY = re.compile(r"^(?:\*\*(?P<b>.+?)\*\*:?|(?P<p>[A-Za-z][\w &/+.-]{0,40}):)\s+(?P<items>.+)$")
_MIN_OVERLAP = 0.6


@dataclass
class Candidate:
    kind: str
    key: str
    fields: dict
    line: int
    line_sha: str
    parent_key: str | None = None


@dataclass
class ParseResult:
    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _point_ym(match: re.Match, prefix: str, edge: str) -> str:
    if match.group(prefix + "mon"):
        return f"{int(match.group(prefix + 'y')):04d}-{_MONTHS[match.group(prefix + 'mon').lower()[:3]]:02d}"
    if match.group(prefix + "m"):
        return f"{int(match.group(prefix + 'yy')):04d}-{int(match.group(prefix + 'm')):02d}"
    year = match.group(prefix + "yr")
    return f"{year}-01" if edge == "start" else f"{year}-12"


def _date_range(line: str) -> tuple[str, str] | None:
    match = _RANGE.search(line)
    if not match:
        return None
    end = "present" if match.group("present") else _point_ym(match, "b", "end")
    return _point_ym(match, "a", "start"), end


def _last_point(text: str) -> str | None:
    found = list(re.finditer(_POINT.format(p="e"), text, re.I))
    return _point_ym(found[-1], "e", "end") if found else None


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return [p for p in parts if p]


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*[-*+•]\s+", "", line).strip()


def _sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _hash12(text: str) -> str:
    return hashlib.sha1(norm_text(text).encode("utf-8")).hexdigest()[:12]


def _section_of(title: str) -> str | None:
    folded = norm_text(title)
    return next((kind for kind, names in SECTION_NAMES.items() if folded in names), None)


class _Parser:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lx = lexicon
        self.out = ParseResult()
        self.keys: set[str] = set()
        self.exp: Candidate | None = None
        self.edu: Candidate | None = None
        self.prj: Candidate | None = None
        self.prj_text_set = False
        self.dated = False

    def add(self, kind: str, key: str, fields: dict, n: int, raw: str, parent_key: str | None = None) -> Candidate | None:
        if key in self.keys:
            self.out.skipped.append(f"line {n}: duplicate of an earlier line, ignored")
            return None
        self.keys.add(key)
        cand = Candidate(kind, key, fields, n, _sha(raw), parent_key)
        self.out.candidates.append(cand)
        return cand

    def achievement(self, text: str, n: int, raw: str, section: str, parent: Candidate | None) -> None:
        fields = {"text": text, **facets.derive_facets(text, self.lx), "section": section}
        parent_key = parent.key if parent else None
        self.add("achievement", f"ach|{parent_key or 'summary'}|{_hash12(text)}", fields, n, raw, parent_key)

    def finish_experience(self) -> None:
        if self.exp is not None and not self.dated:
            self.out.errors.append(
                f"line {self.exp.line}: experience {self.exp.fields['employer']!r} has no date range "
                f"(expected a line such as 'Jan 2024 - Present')")
        self.exp, self.dated = None, False

    def finish_education(self) -> None:
        if self.edu is not None and "degree" not in self.edu.fields:
            self.out.errors.append(f"line {self.edu.line}: education {self.edu.fields['school']!r} has no degree line under it")
        self.edu = None

    def feed(self, section: str, n: int, line: str) -> None:
        getattr(self, f"_{section}")(n, line)

    def _summary(self, n: int, line: str) -> None:
        for sentence in split_sentences(_strip_bullet(line)):
            self.achievement(sentence, n, sentence, "summary", None)

    def _experience(self, n: int, line: str) -> None:
        heading = re.match(r"^###\s+(.+)$", line)
        if heading:
            self.finish_experience()
            if "|" not in heading.group(1):
                self.out.errors.append(f"line {n}: expected '### Employer | Title', found {line.strip()!r}")
                return
            employer, title = (part.strip() for part in heading.group(1).split("|", 1))
            fields = {"employer": employer, "title": title, "technologies": []}
            self.exp = self.add("experience", f"exp|{norm_text(employer)}|{norm_text(title)}", fields, n, line)
            return
        if self.exp is None:
            self.out.errors.append(f"line {n}: expected '### Employer | Title' before this line, found {line.strip()!r}")
            return
        if not self.dated:
            dates = _date_range(line)
            if dates:
                self.exp.fields["start"], self.exp.fields["end"] = dates
                self.dated = True
                return
        text = _strip_bullet(line)
        tech = _TECH_LINE.match(text)
        if tech:
            names = _split_top_level(tech.group(1))
            self.exp.fields["technologies"] = [self.lx.canonical_tech(name) or name for name in names]
            return
        self.achievement(text, n, text, "experience", self.exp)

    def _education(self, n: int, line: str) -> None:
        bold = _BOLD.match(line.strip())
        if bold:
            self.finish_education()
            school, tail = bold.group(1).strip().rstrip(":"), bold.group(2)
            end = _last_point(tail)
            if end is None:
                self.out.errors.append(f"line {n}: education {school!r} needs a date after the name (for example '- Jun 2020')")
                return
            self.edu = self.add("education", f"edu|{norm_text(school)}", {"school": school, "end": end}, n, line)
            return
        if self.edu is None or "degree" in self.edu.fields:
            self.out.errors.append(f"line {n}: expected '**School** - Month YYYY' then a degree line, found {line.strip()!r}")
            return
        degree, _, rest = line.partition("|")
        self.edu.fields["degree"] = degree.strip()
        if rest.strip():
            self.edu.fields["text"] = rest.strip()
            self.edu.fields.update(facets.derive_facets(rest, self.lx))
        self.edu.key = f"edu|{norm_text(self.edu.fields['school'])}|{norm_text(degree)}"

    def _projects(self, n: int, line: str) -> None:
        heading = re.match(r"^###\s+(.+)$", line)
        bold = _BOLD.match(line.strip()) if not line.lstrip().startswith(("-", "*+")) else None
        if heading or bold:
            name = (heading.group(1) if heading else bold.group(1)).strip()
            fields = {"name": name, "text": name, **facets.derive_facets(name, self.lx)}
            self.prj = self.add("project", f"prj|{norm_text(name)}", fields, n, line)
            self.prj_text_set = False
            return
        text = _strip_bullet(line)
        is_bullet = bool(re.match(r"^\s*[-*+•]\s+", line))
        if self.prj is None:
            if not is_bullet:
                self.out.errors.append(f"line {n}: expected a project heading ('### Name' or '**Name**'), found {line.strip()!r}")
                return
            fields = {"name": text[:60], "text": text, **facets.derive_facets(text, self.lx)}
            self.add("project", f"prj|{norm_text(text[:60])}", fields, n, line)
            return
        if not is_bullet and not self.prj_text_set:
            self.prj.fields["text"] = text
            self.prj.fields.update(facets.derive_facets(f"{self.prj.fields['name']}. {text}", self.lx))
            self.prj_text_set = True
            return
        self.achievement(text, n, text, "projects", self.prj)

    def _skills(self, n: int, line: str) -> None:
        text = _strip_bullet(line)
        match = _CATEGORY.match(text)
        category = (match.group("b") or match.group("p")).strip().rstrip(":") if match else None
        items = match.group("items") if match else text
        for item in _split_top_level(items):
            inner = re.match(r"^(.+?)\s*\((.+)\)$", item)
            names = [inner.group(1).strip(), *_split_top_level(inner.group(2))] if inner else [item]
            for name in names:
                self.add("skill", f"skl|{norm_text(name)}", {"name": name, "category": category}, n, name)

    def _certifications(self, n: int, line: str) -> None:
        text = _strip_bullet(line)
        year = re.search(r"\b(19|20)\d{2}\b", text)
        name = re.sub(r"[\s,(–—-]*\(?\b(?:19|20)\d{2}\b\)?\s*$", "", text).strip(" ,") if year else text
        fields = {"name": name}
        if year:
            fields["year"] = year.group(0)
        self.add("certification", f"crt|{norm_text(name)}", fields, n, text)


def parse_resume(text: str, lexicon: Lexicon) -> ParseResult:
    """Turn resume.md into candidate facts. Anything the grammar cannot place is an error or is reported."""
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    parser = _Parser(lexicon)
    section: str | None = None
    skipped: dict[str, list[int]] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = re.match(r"^##\s+(.+?)\s*#*$", line)
        if heading:
            parser.finish_experience()
            parser.finish_education()
            parser.prj = None
            section = _section_of(heading.group(1))
            if section is None:
                skipped.setdefault(f"section '{heading.group(1).strip()}'", []).append(n)
            continue
        if re.match(r"^#\s", line):
            continue
        if section is None:
            skipped.setdefault("preamble" if not skipped else next(reversed(skipped)), []).append(n)
            continue
        parser.feed(section, n, line)
    parser.finish_experience()
    parser.finish_education()
    for name, lines in skipped.items():
        parser.out.skipped.append(f"{name} (line {lines[0]}, {len(lines)} line(s)) was not imported")
    return parser.out


# --- diff -----------------------------------------------------------------------------------

@dataclass
class Change:
    result: str                      # unchanged | added | changed | removed
    action: str                      # none | create | update | unstale | mark_stale | skip_reviewed | skip_manual
    candidate: Candidate | None = None
    fact: Fact | None = None

    @property
    def label(self) -> str:
        if self.candidate is not None:
            return f"{self.candidate.kind:<13} {store.heading(self.candidate.kind, self.candidate.fields)}"
        return f"{self.fact.kind:<13} {store.heading(self.fact.kind, self.fact.fm)}"


@dataclass
class ImportPlan:
    source_rel: str
    source_sha: str
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def count(self, result: str) -> int:
        return sum(1 for c in self.changes if c.result == result)

    @property
    def pending_review(self) -> int:
        return sum(1 for c in self.changes if c.action == "skip_reviewed")

    @property
    def actionable(self) -> bool:
        return any(c.action in ("create", "update", "unstale", "mark_stale") for c in self.changes)


def _words(text: str) -> set[str]:
    return set(norm_text(text).split())


def _jaccard(a: str, b: str) -> float:
    left, right = _words(a), _words(b)
    return len(left & right) / len(left | right) if left | right else 0.0


def _same(candidate: Candidate, fact: Fact) -> bool:
    if candidate.kind == "achievement":
        return True  # the key already contains the text hash
    return all(fact.get(k) == v for k, v in candidate.fields.items())


def _decide(result: str, fact: Fact | None) -> str:
    if result == "added":
        return "create"
    if fact is not None and fact.get("origin") == "manual":
        return "skip_manual" if result in ("changed", "removed") else "none"
    if result == "unchanged":
        return "unstale" if fact is not None and fact.get("stale") else "none"
    if result == "changed":
        return "update" if fact.status == "claimed" else "skip_reviewed"
    return "none" if fact.get("stale") else "mark_stale"


def plan_import(root: Path, source_rel: str = "resume.md") -> ImportPlan:
    root = Path(root)
    path = root / source_rel
    if not path.is_file():
        raise MemoryOpError(f"{source_rel} does not exist in this workspace")
    data = path.read_bytes()
    plan = ImportPlan(source_rel, store.sha256_hex(data))
    career = store.load_career(root)
    if career.errors:
        first = career.errors[0]
        raise MemoryOpError(f"the career memory has errors ({first.code} {first.path}: {first.message}); run `careeros validate`")
    parsed = parse_resume(data.decode("utf-8"), store.lexicon_for(root, career))
    plan.skipped, plan.errors = parsed.skipped, parsed.errors
    if parsed.errors:
        return plan
    by_id = career.by_id()
    existing = [
        f for f in career.facts
        if f.get("import_key") and isinstance(f.get("source"), dict) and f.get("source").get("path") == source_rel
        and f.status != "retired"
    ]
    order = {f.id: (int((f.get("source") or {}).get("line") or 0), f.id) for f in existing}
    by_key = {str(f.get("import_key")): f for f in existing}
    matched: dict[int, tuple[Fact, str]] = {}
    for i, cand in enumerate(parsed.candidates):
        fact = by_key.get(cand.key)
        if fact is not None:
            matched[i] = (fact, "unchanged" if _same(cand, fact) else "changed")
    taken = {fact.id for fact, _ in matched.values()}
    loose = [f for f in sorted(existing, key=lambda f: order[f.id]) if f.id not in taken]

    def group(fact: Fact) -> str:
        parent = by_id.get(str(fact.get("parent")))
        return str(parent.get("import_key")) if parent is not None and parent.get("import_key") else "summary"

    pairs = []
    for i, cand in enumerate(parsed.candidates):
        if i in matched or cand.kind != "achievement":
            continue
        for fact in loose:
            if fact.kind == "achievement" and group(fact) == (cand.parent_key or "summary"):
                score = _jaccard(str(cand.fields["text"]), str(fact.get("text")))
                if score >= _MIN_OVERLAP:
                    pairs.append((-score, i, order[fact.id], fact))
    for _score, i, _line, fact in sorted(pairs, key=lambda p: p[:3]):
        if i not in matched and fact.id not in taken:
            matched[i] = (fact, "changed")
            taken.add(fact.id)
    for i, cand in enumerate(parsed.candidates):
        if i in matched:
            fact, result = matched[i]
            plan.changes.append(Change(result, _decide(result, fact), cand, fact))
        else:
            plan.changes.append(Change("added", "create", cand, None))
    for fact in loose:
        if fact.id not in taken:
            plan.changes.append(Change("removed", _decide("removed", fact), None, fact))
    return plan


# --- apply ----------------------------------------------------------------------------------

@dataclass
class ImportResult:
    status: str                      # complete | noop
    plan: ImportPlan
    backup: Path | None = None


def _source_block(plan: ImportPlan, cand: Candidate) -> dict:
    return {"kind": "resume", "path": plan.source_rel, "line": cand.line, "sha256": cand.line_sha}


def apply_import(root: Path, source_rel: str = "resume.md", *, actor: str = "user") -> ImportResult:
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        plan = plan_import(root, source_rel)
        if plan.errors:
            raise MemoryOpError("\n".join(plan.errors))
        sources = store.load_sources(root)
        entry = {
            "path": source_rel, "sha256": plan.source_sha, "imported_at": models.utc_now(),
            "framework_version": versions.installed_version(), "pending_review": plan.pending_review,
        }
        recorded = sources.get(source_rel, {})
        if not plan.actionable and recorded.get("sha256") == plan.source_sha and recorded.get("pending_review") == plan.pending_review:
            return ImportResult("noop", plan)
        now = entry["imported_at"]
        taken = set(career.by_id())
        resolved = {str(f.get("import_key")): f.id for f in career.facts if f.get("import_key")}
        writes: dict[Path, bytes] = {}
        specs: list[dict] = []
        for change in plan.changes:
            cand, fact = change.candidate, change.fact
            if change.action == "create":
                new_id = ids.new_id(store.ID_KIND[cand.kind], taken)
                taken.add(new_id)
                resolved[cand.key] = new_id
                fields = dict(cand.fields)
                if cand.kind == "achievement":
                    fields = {"parent": resolved.get(cand.parent_key) if cand.parent_key else None, **fields}
                fm = store.new_frontmatter(cand.kind, new_id, fields, _source_block(plan, cand), "imported", now)
                fm["import_key"] = cand.key
                created = store.new_fact(root, cand.kind, fm)
                writes[created.path] = store.render(created)
                specs.append(event_spec(new_id, "memory.fact_added", f"imported {cand.kind}: {store.heading(cand.kind, fm)}",
                                        actor=actor, new="claimed"))
            elif change.action in ("update", "unstale", "mark_stale"):
                fm = dict(fact.fm)
                if change.action == "update":
                    fm.update(cand.fields)
                    fm["source"], fm["import_key"] = _source_block(plan, cand), cand.key
                    fm.pop("stale", None)
                    note = "updated from the resume"
                elif change.action == "unstale":
                    fm.pop("stale", None)
                    note = "its source line is back in the resume"
                else:
                    fm["stale"] = True
                    note = "marked stale: its source line is gone from the resume"
                fm["updated_at"] = now
                updated = Fact(fm, fact.body, fact.path)
                writes[updated.path] = store.render(updated)
                specs.append(event_spec(fact.id, "memory.fact_updated", f"{fact.kind}: {note}", actor=actor,
                                        prev=fact.status, new=fact.status))
        backup = backup_helpers.new_backup_dir(root, "memory-import")
        sources[source_rel] = entry
        writes[root / store.SOURCES_REL] = store.render_sources(sources)
        items = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in writes if p.exists()]
        backup_helpers.backup_files(backup, items)
        files = [
            {"path": p.relative_to(root).as_posix(), "existed": p.exists(),
             "before_sha256": store.sha256_hex(p.read_bytes()) if p.exists() else None, "after_sha256": None}
            for p in writes
        ]
        manifest = {
            "version": 1, "kind": "memory-import", "created_at": now, "tool_version": versions.installed_version(),
            "source": source_rel, "source_sha256": plan.source_sha, "status": "pending", "files": files,
        }
        backup_helpers.write_manifest(backup, manifest)
        tx = Transaction()
        try:
            for path, data in writes.items():
                tx.write(path, data)
            specs.append({
                "type": "memory.imported", "actor": actor, "source": "cli",
                "action": (f"imported {source_rel}: {plan.count('added')} added, {plan.count('changed')} changed, "
                           f"{plan.count('removed')} removed, {plan.count('unchanged')} unchanged, "
                           f"{plan.pending_review} need review"),
                "artifacts": [f"{backup.relative_to(root).as_posix()}/manifest.json", source_rel],
            })
            ledger.append_events(root, specs)
        except BaseException:
            tx.rollback()
            manifest["status"] = "rolled_back"
            backup_helpers.write_manifest(backup, manifest)
            raise
        for record in files:
            current = root / record["path"]
            record["after_sha256"] = store.sha256_hex(current.read_bytes()) if current.exists() else None
        manifest["status"] = "complete"
        backup_helpers.write_manifest(backup, manifest)
        return ImportResult("complete", plan, backup)
````

- [ ] **Step 5: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/test_memory_importer.py -q`
Expected: `29 passed`.

- [ ] **Step 6: Run everything, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `411 passed`.

````bash
git add careeros/core/memory/importer.py tests/test_memory_importer.py tests/fixtures/resume_sample.md
git commit -m "memory: deterministic resume importer with reviewable diff, backup and rollback" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 6: The `memory` and `check` commands

**Files:**
- Create: `careeros/cli/memory.py`, `careeros/cli/check.py`, `tests/test_cli_memory.py`
- Modify: `careeros/cli/main.py`

**Interfaces:**
- Consumes: Tasks 3-5 and `careeros.cli._util` (`resolve_root`, `fail`, `is_interactive`, `default_actor`, `confirm_or_exit`, `echo_json`, exit codes).
- Produces: `careeros memory {import,add,update,confirm,verify,dispute,retire,list,show,status}` and `careeros check PATH|-`, with the exit codes in the Global Constraints. `check --json` prints only JSON on standard output. `check --record` appends a `draft.checked` event whose `artifacts` is `["sha256:<hash of the exact text>"]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_memory.py`:

````python
"""The memory and check commands: exit codes, the terminal guard, JSON purity, recording."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from helpers import add_job, make_workspace, snapshot
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger
from careeros.core.memory import store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"
GOOD = "Reduced AWS costs by 35% using Kafka-based pipelines at Acme Corp."
BAD = "Reduced AWS costs by 50% using Kafka. I worked at FakeCorp."


def run(root: Path | None, *args: str, input: str | None = None):
    argv = list(args)
    if root is not None:
        argv += ["--workspace", str(root)]
    return runner.invoke(app, argv, input=input)


@pytest.fixture
def root(tmp_path: Path, clock) -> Path:
    return make_workspace(tmp_path / "ws")


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_util, "is_interactive", lambda: True)


def events(root: Path, prefix: str) -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith(prefix)]


# --- check ---------------------------------------------------------------------------------

def test_check_exits_0_for_a_supported_draft_and_prints_the_narrow_guarantee(career, tmp_path: Path) -> None:
    root, _ = career
    draft = tmp_path / "draft.txt"
    draft.write_text(GOOD + "\nI enjoy mentoring.", encoding="utf-8")
    result = run(root, "check", str(draft))
    assert result.exit_code == 0, result.output
    assert "Evidence check: PASSED" in result.output and "Not evaluated (1 sentence(s)" in result.output
    assert "claimed rather than confirmed" in result.output
    assert "A pass does not mean every claim in the draft was detected." in result.output


def test_check_exits_1_with_codes_and_fixes_for_an_unsupported_draft(career, tmp_path: Path) -> None:
    root, _ = career
    draft = tmp_path / "draft.txt"
    draft.write_text(BAD, encoding="utf-8")
    result = run(root, "check", str(draft))
    assert result.exit_code == 1
    assert "FAILED" in result.output and "CHK001" in result.output and "CHK004" in result.output and "fix:" in result.output


def test_check_reads_standard_input(career) -> None:
    root, _ = career
    assert run(root, "check", "-", input=GOOD).exit_code == 0
    assert run(root, "check", "-", input=BAD).exit_code == 1


def test_check_json_prints_only_json(career) -> None:
    root, _ = career
    result = run(root, "check", "-", "--json", input=BAD)
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False and {f["code"] for f in data["review_required"]} >= {"CHK001", "CHK004"}
    assert set(data["not_evaluated"]) == {"sentences", "categories"}


def test_check_usage_errors_exit_2(career, tmp_path: Path) -> None:
    root, _ = career
    assert run(root, "check", str(tmp_path / "missing.txt")).exit_code == 2
    assert run(root, "check", "-", "--against", "nonexistent-job", input=GOOD).exit_code == 2


def test_check_will_not_run_against_a_broken_memory(career) -> None:
    root, ids = career
    store.load_career(root).by_id()[ids["costs"]].path.write_text("garbage", encoding="utf-8")
    result = run(root, "check", "-", input=GOOD)
    assert result.exit_code == 2 and "MEM001" in result.output


def test_check_against_a_job_allows_naming_its_company(career) -> None:
    root, _ = career
    job = add_job(root, "initech-eng", company="Initech", title="Backend Engineer")
    letter = "I am excited about the Backend Engineer role at Initech."
    assert run(root, "check", "-", "--against", job["id"], input=letter).exit_code == 0
    assert run(root, "check", "-", input=letter).exit_code == 1


def test_check_allow_and_require_confirmed(career) -> None:
    root, _ = career
    assert run(root, "check", "-", "--allow", "Flink", input="Your team runs Flink at scale.").exit_code == 0
    assert run(root, "check", "-", input="Your team runs Flink at scale.").exit_code == 1
    assert run(root, "check", "-", "--require-confirmed", input=GOOD).exit_code == 1


def test_check_fails_closed_on_an_empty_memory(root: Path) -> None:
    result = run(root, "check", "-", input="Anything.")
    assert result.exit_code == 1 and "CHK030" in result.output


def test_check_record_writes_the_hash_of_the_exact_draft(career) -> None:
    root, _ = career
    assert run(root, "check", "-", "--record", input=GOOD).exit_code == 0
    assert run(root, "check", "-", "--record", input=BAD).exit_code == 1
    recorded = events(root, "draft.checked")
    assert len(recorded) == 2 and recorded[0]["artifacts"] == [f"sha256:{hashlib.sha256(GOOD.encode()).hexdigest()}"]
    assert "passed" in recorded[0]["action"] and "failed" in recorded[1]["action"]
    assert ledger.verify_chain(root) == []
    assert not events(root, "draft.checked")[0].get("entity")


def test_check_without_record_leaves_the_workspace_untouched(career) -> None:
    root, _ = career
    before, ledger_bytes = snapshot(root), (root / "ledger.jsonl").read_bytes()
    run(root, "check", "-", input=GOOD)
    assert snapshot(root) == before and (root / "ledger.jsonl").read_bytes() == ledger_bytes


# --- memory import -------------------------------------------------------------------------

def test_import_defaults_to_a_dry_run(root: Path) -> None:
    shutil.copy(FIXTURE, root / "resume.md")
    before = snapshot(root)
    result = run(root, "memory", "import")
    assert result.exit_code == 0 and "Dry run: nothing was changed" in result.output
    assert "+ experience" in result.output and "Not imported: section 'Awards'" in result.output
    assert snapshot(root) == before and not (root / "career").exists()


def test_import_apply_needs_a_terminal_or_yes(root: Path) -> None:
    shutil.copy(FIXTURE, root / "resume.md")
    assert run(root, "memory", "import", "--apply").exit_code == 2
    assert not (root / "career").exists()
    result = run(root, "memory", "import", "--apply", "--yes")
    assert result.exit_code == 0 and "Backup and manifest" in result.output
    assert len(store.load_career(root).facts) > 20
    again = run(root, "memory", "import", "--apply", "--yes")
    assert again.exit_code == 0 and "Nothing to import" in again.output


def test_import_apply_in_a_terminal_asks_first(root: Path, interactive) -> None:
    shutil.copy(FIXTURE, root / "resume.md")
    declined = run(root, "memory", "import", "--apply", input="n\n")
    assert declined.exit_code == 1 and not (root / "career").exists()
    assert run(root, "memory", "import", "--apply", input="y\n").exit_code == 0


def test_import_reports_grammar_errors_and_changes_nothing(root: Path) -> None:
    (root / "resume.md").write_text("## Experience\n\n### Acme Engineer\n- no pipe\n", encoding="utf-8")
    result = run(root, "memory", "import", "--apply", "--yes")
    assert result.exit_code == 1 and "expected '### Employer | Title'" in result.output and not (root / "career").exists()
    assert run(root, "memory", "import", "--apply", "--dry-run").exit_code == 2


def test_import_of_a_missing_file_fails_cleanly(root: Path) -> None:
    result = run(root, "memory", "import")
    assert result.exit_code == 1 and "does not exist" in result.output


# --- confirm / verify guards ---------------------------------------------------------------

def test_confirm_and_verify_refuse_without_a_terminal_and_say_how(career) -> None:
    root, ids = career
    for args in (("confirm", ids["costs"]), ("verify", ids["costs"], "--evidence", "evd_0000000000")):
        result = run(root, "memory", *args)
        assert result.exit_code == 2 and "interactive terminal" in result.output and "! careeros memory" in result.output
    assert store.load_career(root).by_id()[ids["costs"]].status == "claimed" and not events(root, "memory.fact_confirmed")


def test_confirm_in_a_terminal_records_the_decision_or_the_refusal(career, interactive) -> None:
    root, ids = career
    assert run(root, "memory", "confirm", ids["costs"], input="n\n").exit_code == 1
    assert events(root, "memory.declined") and store.load_career(root).by_id()[ids["costs"]].status == "claimed"
    result = run(root, "memory", "confirm", ids["costs"], input="y\n")
    assert result.exit_code == 0 and store.load_career(root).by_id()[ids["costs"]].status == "confirmed"
    assert events(root, "memory.fact_confirmed")[0]["actor"] == "user"


def test_verify_with_evidence_end_to_end(career, interactive) -> None:
    root, ids = career
    run(root, "memory", "confirm", ids["costs"], input="y\n")
    added = run(root, "memory", "add", "--kind", "evidence", "--set", "url=https://example.com/report",
                "--set", "note=Finance report", "--supports", ids["costs"])
    assert added.exit_code == 0, added.output
    evidence_id = added.output.split()[2]
    result = run(root, "memory", "verify", ids["costs"], "--evidence", evidence_id, input="y\n")
    assert result.exit_code == 0 and store.load_career(root).by_id()[ids["costs"]].status == "verified"


# --- add / update / dispute / retire / read ---------------------------------------------------

def test_add_update_dispute_retire_round_trip(career) -> None:
    root, ids = career
    added = run(root, "memory", "add", "--kind", "achievement", "--set", f"parent={ids['globex']}",
                "--text", "Mentored 4 engineers.", "--quote", "I mentored four people")
    assert added.exit_code == 0 and "claimed" in added.output
    fact_id = added.output.split()[2]
    assert run(root, "memory", "update", fact_id, "--text", "Mentored 5 engineers.").exit_code == 0
    assert run(root, "memory", "dispute", fact_id, "--reason", "it was four").exit_code == 0
    assert run(root, "memory", "retire", fact_id, "--reason", "duplicate").exit_code == 2  # disputed -> retired needs a person
    assert store.load_career(root).by_id()[fact_id].status == "disputed"


def test_add_without_a_quote_is_refused(career) -> None:
    root, ids = career
    result = run(root, "memory", "add", "--kind", "skill", "--set", "name=Rust")
    assert result.exit_code == 1 and "--quote" in result.output


def test_update_of_a_confirmed_fact_needs_a_terminal(career, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ids = career
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    run(root, "memory", "confirm", ids["costs"], input="y\n")
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    result = run(root, "memory", "update", ids["costs"], "--text", "Reduced AWS costs by 99%.")
    assert result.exit_code == 2 and "interactive terminal" in result.output
    assert "35%" in store.load_career(root).by_id()[ids["costs"]].get("text")


def test_list_show_and_status(career) -> None:
    root, ids = career
    listing = run(root, "memory", "list", "--kind", "experience")
    assert listing.exit_code == 0 and listing.output.count("experience") >= 2 and "Acme Corp" in listing.output
    as_json = json.loads(run(root, "memory", "list", "--status", "claimed", "--json").stdout)
    assert len(as_json) == len(ids)
    shown = run(root, "memory", "show", ids["costs"])
    assert "Reduced AWS costs by 35%" in shown.output and "status: claimed" in shown.output
    assert run(root, "memory", "show", "ach_zzzzzzzzzz").exit_code == 1
    status = json.loads(run(root, "memory", "status", "--json").stdout)
    assert status["facts"] == len(ids) and status["claimed_share"] == 1.0 and status["errors"] == []
    assert "still only claimed" in run(root, "memory", "status").output


def test_ledger_append_refuses_memory_event_types(career) -> None:
    root, _ = career
    result = run(root, "ledger", "append", "--type", "memory.fact_confirmed", "--action", "forged")
    assert result.exit_code == 2 and "reserved" in result.output
    assert run(root, "ledger", "append", "--type", "draft.checked", "--action", "ok").exit_code == 0


def test_validate_reports_memory_problems(career) -> None:
    root, ids = career
    assert run(root, "validate").exit_code == 0
    store.load_career(root).by_id()[ids["costs"]].path.write_text("garbage", encoding="utf-8")
    result = run(root, "validate")
    assert result.exit_code == 1 and "MEM001" in result.output


def test_an_empty_draft_is_a_usage_error_not_a_pass(career) -> None:
    root, _ = career
    assert run(root, "check", "-", input="  \n").exit_code == 2
````

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_cli_memory.py -q`
Expected: failures; the commands do not exist yet (`No such command 'memory'`, exit code 2).

- [ ] **Step 3: Create `careeros/cli/memory.py`**

````python
"""careeros memory: import a resume and maintain the facts your drafts are checked against."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import typer
import yaml

from careeros.cli import _util
from careeros.core.memory import importer, ops, store
from careeros.core.memory.ops import MemoryOpError
from careeros.core.workspace import WorkspaceError

memory_app = typer.Typer(help="Import your resume and maintain the career facts that drafts are checked against.", no_args_is_help=True)
_SYMBOL = {"added": "+", "changed": "~", "removed": "-"}


def _confirm() -> Callable[[str], bool] | None:
    return (lambda prompt: typer.confirm(prompt, default=False)) if _util.is_interactive() else None


def _human_only(command: str, fact_id: str) -> None:
    if not _util.is_interactive():
        typer.echo(
            f"careeros memory {command} records YOUR decision and needs an interactive terminal. "
            f"Ask the user to run it themselves (in Claude Code: `! careeros memory {command} {fact_id}`).",
            err=True,
        )
        raise typer.Exit(_util.EXIT_USAGE)


def _run(action: Callable[[], object]) -> object:
    try:
        return action()
    except MemoryOpError as exc:
        _util.fail(str(exc), _util.EXIT_USAGE if "interactive terminal" in str(exc) else _util.EXIT_FAIL)
    except WorkspaceError as exc:
        _util.fail(str(exc))


def _pairs(values: Optional[list[str]]) -> dict:
    out: dict = {}
    for item in values or ():
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            _util.fail(f"--set expects key=value, not {item!r}", _util.EXIT_USAGE)
        out[key.strip()] = value.strip()
    return out


def _print_plan(plan: importer.ImportPlan) -> None:
    typer.echo(f"Import plan for {plan.source_rel}")
    for change in plan.changes:
        if change.result == "unchanged":
            continue
        symbol = _SYMBOL[change.result]
        note = {"skip_reviewed": "  (reviewed fact: left alone, needs your review)",
                "skip_manual": "  (manual fact: left alone)",
                "mark_stale": "  (its line is gone: will be marked stale)"}.get(change.action, "")
        typer.echo(f"  {symbol} {change.label}{note}")
    typer.echo(f"  = {plan.count('unchanged')} unchanged")
    typer.echo(
        f"Summary: {plan.count('added')} added, {plan.count('changed')} changed, {plan.count('removed')} removed, "
        f"{plan.count('unchanged')} unchanged; {plan.pending_review} need review"
    )
    for change in plan.changes:
        if change.action == "skip_reviewed":
            typer.echo(f"Needs review: {change.fact.id}\n  now:      {change.fact.get('text') or change.label.strip()}\n"
                       f"  resume:   {change.candidate.fields.get('text') or change.label.strip()}")
    for line in plan.skipped:
        typer.echo(f"Not imported: {line}")


@memory_app.command("import")
def import_cmd(
    source: str = typer.Option("resume.md", "--source", help="File to import, relative to the workspace"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and change nothing (the default)"),
    apply: bool = typer.Option(False, "--apply", help="Apply the plan"),
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (needed when there is no terminal)"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Turn resume.md into career facts. Shows a plan first; --apply makes the changes."""
    if dry_run and apply:
        _util.fail("choose --dry-run or --apply, not both", _util.EXIT_USAGE)
    root = _util.resolve_root(workspace)
    plan = _run(lambda: importer.plan_import(root, source))
    if plan.errors:
        for error in plan.errors:
            typer.echo(f"error: {error}", err=True)
        raise typer.Exit(_util.EXIT_FAIL)
    _print_plan(plan)
    if not apply:
        if not plan.actionable and plan.pending_review == 0:
            typer.echo("Nothing to import: the career memory already matches the resume.")
        else:
            typer.echo("Dry run: nothing was changed. Run again with --apply to import.")
        return
    if not plan.actionable and plan.pending_review == 0:
        typer.echo("Nothing to import: the career memory already matches the resume.")
        return
    if not _util.confirm_or_exit("Apply this import?", assume_yes=yes):
        typer.echo("Cancelled; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    result = _run(lambda: importer.apply_import(root, source, actor=actor or _util.default_actor()))
    if result.status == "noop":
        typer.echo("Nothing to import: the career memory already matches the resume.")
        return
    typer.echo(f"Imported. Backup and manifest: {result.backup.relative_to(root).as_posix()}")


@memory_app.command("add")
def add_cmd(
    kind: str = typer.Option(..., "--kind", help=f"One of: {', '.join(store.KINDS)}"),
    text: Optional[str] = typer.Option(None, "--text", help="The claim, for achievements, projects and preferences"),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="key=value for the kind's other fields; repeatable"),
    supports: Optional[list[str]] = typer.Option(None, "--supports", help="Evidence only: ID of a fact it backs; repeatable"),
    quote: Optional[str] = typer.Option(None, "--quote", help="The user's own words (required except for evidence)"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Add a fact the user has stated. It starts as `claimed`."""
    root = _util.resolve_root(workspace)
    fields = _pairs(set_)
    if text is not None:
        fields["text"] = text
    if supports:
        fields["supports"] = list(supports)
    if kind == "evidence":
        fields["kind"] = "document" if "path" in fields else "link"
    fact = _run(lambda: ops.add_fact(root, kind, fields, quote=quote, actor=actor or _util.default_actor()))
    typer.echo(f"Added {fact.kind} {fact.id} (claimed)")


@memory_app.command("update")
def update_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    text: Optional[str] = typer.Option(None, "--text"),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="key=value; repeatable"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Change a fact. Changing a confirmed or verified fact resets it to claimed and needs a terminal."""
    root = _util.resolve_root(workspace)
    changes = _pairs(set_)
    if text is not None:
        changes["text"] = text
    fact = _run(lambda: ops.update_fact(root, fact_id, changes, actor=actor or _util.default_actor(), confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Updated {fact.id}; it is claimed until you confirm it again.")


@memory_app.command("confirm")
def confirm_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Affirm that a claimed fact is true. Needs an interactive terminal."""
    root = _util.resolve_root(workspace)
    _human_only("confirm", fact_id)
    fact = _run(lambda: ops.confirm_fact(root, fact_id, actor="user", confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; the fact stays claimed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Confirmed {fact.id}.")


@memory_app.command("verify")
def verify_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    evidence: str = typer.Option(..., "--evidence", help="ID of an evidence record that lists this fact under supports"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Mark a confirmed fact as verified by evidence. Needs an interactive terminal."""
    root = _util.resolve_root(workspace)
    _human_only("verify", fact_id)
    fact = _run(lambda: ops.verify_fact(root, fact_id, evidence, actor="user", confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; the fact stays confirmed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Verified {fact.id} with {evidence}.")


@memory_app.command("dispute")
def dispute_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    reason: str = typer.Option(..., "--reason"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Record that a fact is wrong. It stops supporting any claim."""
    root = _util.resolve_root(workspace)
    fact = _run(lambda: ops.dispute_fact(root, fact_id, reason, actor=actor or _util.default_actor(), confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Disputed {fact.id}.")


@memory_app.command("retire")
def retire_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    reason: str = typer.Option(..., "--reason"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Retire a fact that no longer applies. The file stays, so the ledger's references remain valid."""
    root = _util.resolve_root(workspace)
    fact = _run(lambda: ops.retire_fact(root, fact_id, reason, actor=actor or _util.default_actor(), confirm=_confirm()))
    if fact is None:
        typer.echo("Declined; nothing was changed.")
        raise typer.Exit(_util.EXIT_FAIL)
    typer.echo(f"Retired {fact.id}.")


def _loaded(root: Path) -> store.Career:
    career = store.load_career(root)
    if career.errors:
        for issue in career.errors:
            typer.echo(f"error: {issue.code} {issue.path}: {issue.message}", err=True)
        _util.fail("the career memory has errors; run `careeros validate`", _util.EXIT_USAGE)
    return career


@memory_app.command("list")
def list_cmd(
    kind: Optional[str] = typer.Option(None, "--kind"),
    status: Optional[str] = typer.Option(None, "--status"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """List facts."""
    career = _loaded(_util.resolve_root(workspace))
    facts = [f for f in career.facts if (kind is None or f.kind == kind) and (status is None or f.status == status)]
    if as_json:
        _util.echo_json([{"id": f.id, "kind": f.kind, "status": f.status, "origin": f.get("origin"),
                          "stale": bool(f.get("stale")), "summary": store.heading(f.kind, f.fm)} for f in facts])
        return
    for f in facts:
        flag = " (stale)" if f.get("stale") else ""
        typer.echo(f"{f.id}  {f.kind:<13} {f.status:<9} {store.heading(f.kind, f.fm)}{flag}")


@memory_app.command("show")
def show_cmd(
    fact_id: str = typer.Argument(..., metavar="ID"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Show one fact in full."""
    fact = _loaded(_util.resolve_root(workspace)).by_id().get(fact_id)
    if fact is None:
        _util.fail(f"no fact with id {fact_id}")
    if as_json:
        _util.echo_json(fact.fm)
    else:
        typer.echo(yaml.safe_dump(fact.fm, sort_keys=False, allow_unicode=True, width=10_000).rstrip())


@memory_app.command("status")
def status_cmd(
    as_json: bool = typer.Option(False, "--json"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Summarise the memory: counts by kind and status, stale imports, how much is still only claimed."""
    root = _util.resolve_root(workspace)
    career = store.load_career(root)
    by_kind: dict[str, dict[str, int]] = {}
    for f in career.facts:
        by_kind.setdefault(f.kind, {}).setdefault(f.status, 0)
        by_kind[f.kind][f.status] += 1
    active = career.active()
    claimed = sum(1 for f in active if f.status == "claimed")
    data = {
        "facts": len(career.facts), "by_kind": by_kind, "claimed_share": round(claimed / len(active), 2) if active else 0.0,
        "stale_facts": sum(1 for f in career.facts if f.get("stale")),
        "stale_imports": [i.path for i in career.issues if i.code == "MEM006"],
        "pending_review": sum(int(e.get("pending_review") or 0) for e in store.load_sources(root).values()),
        "errors": [i.to_dict() for i in career.errors],
    }
    if as_json:
        _util.echo_json(data)
        return
    typer.echo(f"{data['facts']} fact(s); {claimed} of {len(active)} active fact(s) are still only claimed.")
    for kind, counts in sorted(by_kind.items()):
        typer.echo(f"  {kind:<13} " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    if data["stale_facts"]:
        typer.echo(f"{data['stale_facts']} fact(s) are stale: their source line is gone from the resume.")
    for path in data["stale_imports"]:
        typer.echo(f"{path} changed since it was imported: run `careeros memory import`.")
    if data["pending_review"]:
        typer.echo(f"{data['pending_review']} reviewed fact(s) differ from the resume: run `careeros memory import` to see them.")
    for issue in career.errors:
        typer.echo(f"error: {issue.code} {issue.path}: {issue.message}")
````

- [ ] **Step 4: Create `careeros/cli/check.py`**

````python
"""careeros check: do the checkable claims in a draft trace to the career memory?"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional

import typer

from careeros.cli import _util
from careeros.core import ledger
from careeros.core.memory import check as core_check
from careeros.core.memory import store
from careeros.core.workspace import WorkspaceError, ensure_writable, resolve_job


def _read_draft(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _util.fail(f"cannot read {path}: {exc}", _util.EXIT_USAGE)


def _print(result: core_check.CheckResult) -> None:
    typer.echo("Evidence check: " + ("PASSED" if result.ok else "FAILED"))
    typer.echo(f"\nSupported ({len(result.supported)})")
    for item in result.supported:
        tag = "claimed" if item["claimed_only"] else "confirmed"
        typer.echo(f"  {item['claim']}  <- {', '.join(item['facts'][:3])} ({tag})")
    if result.allowed_mentions:
        typer.echo(f"\nUnverified mentions (passed with --allow, not evidence): {', '.join(result.allowed_mentions)}")
    typer.echo(f"\nReview required ({len(result.review_required)})")
    for f in result.review_required:
        typer.echo(f"  {f.code}  {f.atom or '(memory)'}  {f.reason}")
        if f.sentence:
            typer.echo(f"         sentence: {f.sentence}")
        typer.echo(f"         fix: {f.fix}")
    typer.echo(f"\nNot evaluated ({len(result.not_evaluated)} sentence(s) with nothing the checker can assess)")
    for sentence in result.not_evaluated:
        typer.echo(f"  - {sentence}")
    typer.echo("  Also not assessed: " + "; ".join(core_check.NOT_EVALUATED_CATEGORIES) + ".")
    for f in result.info:
        typer.echo(f"\nNote {f.code}: {f.reason}. {f.fix}")
    if result.ok:
        typer.echo(
            f"\nPassed: all {len(result.supported)} detected checkable claims (numbers, years, durations, technologies, "
            f"employers, schools, titles, certifications) are supported by your career memory; "
            f"{len(result.not_evaluated)} sentence(s) had nothing the checker can evaluate and {result.claimed_support} "
            f"supporting fact(s) are claimed rather than confirmed. A pass does not mean every claim in the draft was detected."
        )
    else:
        typer.echo(f"\nFailed: {len(result.review_required)} item(s) need review. Remove or rewrite them, or ask the user whether they are true.")


def check_cmd(
    draft: str = typer.Argument(..., metavar="PATH", help="The draft to check, or - to read it from standard input"),
    against: Optional[str] = typer.Option(None, "--against", help="Job the draft is for (its company and title may be named)"),
    allow: Optional[list[str]] = typer.Option(None, "--allow", help="A term to mention without evidence (listed as unverified); repeatable"),
    require_confirmed: bool = typer.Option(False, "--require-confirmed", help="Fail claims backed only by claimed facts"),
    record: bool = typer.Option(False, "--record", help="Write a draft.checked event to the ledger"),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable results and nothing else"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    workspace: Optional[Path] = _util.WorkspaceOption,
) -> None:
    """Check a draft against your career memory. Exit 0 only when every detected claim is supported."""
    root = _util.resolve_root(workspace)
    text = _read_draft(draft)
    if not text.strip():
        _util.fail("the draft is empty; there is nothing to check", _util.EXIT_USAGE)
    career = store.load_career(root)
    if career.errors:
        for issue in career.errors:
            typer.echo(f"error: {issue.code} {issue.path}: {issue.message}", err=True)
        _util.fail("the career memory has errors, so the draft cannot be checked; run `careeros validate`", _util.EXIT_USAGE)
    target = None
    if against:
        try:
            job = resolve_job(root, against)
        except WorkspaceError as exc:
            _util.fail(str(exc), _util.EXIT_USAGE)
        target = (job.company, job.title)
    result = core_check.run_check(
        root, text, allow=list(allow or ()), against=target, require_confirmed=require_confirmed, career=career
    )
    if record:
        try:
            ensure_writable(root)
            ledger.append_event(
                root, type="draft.checked", actor=actor or _util.default_actor(),
                entity=job.id if against else None, source="cli",
                action=(f"checked draft: {'passed' if result.ok else 'failed'} ({len(result.supported)} supported, "
                        f"{len(result.review_required)} need review, {len(result.not_evaluated)} not evaluated)"),
                artifacts=[f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"],
            )
        except WorkspaceError as exc:
            _util.fail(f"the check ran but could not be recorded: {exc}")
    if as_json:
        _util.echo_json(result.to_dict())
    else:
        _print(result)
    raise typer.Exit(_util.EXIT_OK if result.ok else _util.EXIT_FAIL)
````

- [ ] **Step 5: Register the commands**

In `careeros/cli/main.py`:

1. After `from careeros.cli.archive import archive_cmd` add `from careeros.cli.check import check_cmd`.
2. After `from careeros.cli.ledger import ledger_app` add `from careeros.cli.memory import memory_app` (keep the imports alphabetical: `ledger`, then `memory`, then `migrate`).
3. Replace `app.add_typer(ledger_app, name="ledger")` with:

````python
app.command("check")(check_cmd)
app.add_typer(ledger_app, name="ledger")
app.add_typer(memory_app, name="memory")
````

- [ ] **Step 6: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/test_cli_memory.py -q`
Expected: `26 passed`.

- [ ] **Step 7: Run everything, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `437 passed`.

````bash
git add careeros/cli/memory.py careeros/cli/check.py careeros/cli/main.py tests/test_cli_memory.py
git commit -m "cli: careeros memory and careeros check" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 7: The evidence gate in the skills, and the memory skill

**Files:**
- Create: `careeros/templates/workspace/.claude/skills/memory/SKILL.md`, `tests/test_skills_evidence.py`
- Modify: the `SKILL.md` of `prep`, `apply`, `outreach`, `follow-up`, `humanize`, `interview` and `onboard`, and `careeros/templates/workspace/CLAUDE.md` (all under `careeros/templates/workspace/`). **Do not touch** `careeros/templates/gpt-workspace/` (GPT Work cannot run local commands; the docs say so).

**Interfaces:**
- Consumes: `careeros check` and `careeros memory ...` (Task 6) as commands the skills tell the agent to run; `scaffold(root, refresh=True, runtime="claude")` to deliver them.
- Produces: the `## EVIDENCE GATE (mandatory)` section, placed immediately before each skill's first `## STEP`/`## MODE` heading, with a skill-specific `**Applies to:**` line; the `memory` skill; the `CLAUDE.md` routing row and layout line.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_evidence.py`:

````python
"""The evidence gate is mandatory wording in every Claude Code drafting skill, and refresh delivers it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from careeros.workspace.scaffold import scaffold

SKILLS = ("prep", "apply", "outreach", "follow-up", "humanize", "interview")
GATE_PHRASES = (
    "## EVIDENCE GATE (mandatory)",
    "careeros check <file> --against <job-id> --record",
    "Re-run the check after every edit. Never skip it and never ignore a failure.",
    "do not show the draft",
    "Never use `--allow` to get past a claim about the user's own history",
    "Never call a draft \"verified\" or \"true\"",
    "still only claimed, not confirmed",
    "Exit 2",
)
TEMPLATES = Path(__file__).parent.parent / "careeros" / "templates"


def skill_text(name: str) -> str:
    return (TEMPLATES / "workspace" / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", SKILLS)
def test_every_drafting_skill_carries_the_full_gate(skill: str) -> None:
    text = skill_text(skill)
    for phrase in GATE_PHRASES:
        assert phrase in text, f"{skill} is missing: {phrase}"
    assert text.count("## EVIDENCE GATE (mandatory)") == 1
    assert "**Applies to:**" in text


@pytest.mark.parametrize("skill,needle", [
    ("prep", "tailored resume"), ("apply", "free-text answer"), ("outreach", "connection note"),
    ("follow-up", "follow-up message"), ("humanize", "never facts"), ("interview", "STAR story"),
])
def test_each_gate_names_what_it_covers(skill: str, needle: str) -> None:
    assert needle in skill_text(skill)


def test_the_gate_comes_before_the_skills_first_step() -> None:
    for skill in SKILLS:
        text = skill_text(skill)
        first_step = min(m.start() for m in re.finditer(r"^## (STEP|MODE)", text, re.M))
        assert text.index("## EVIDENCE GATE") < first_step, skill


def test_the_memory_skill_covers_import_add_and_the_human_only_commands() -> None:
    text = skill_text("memory")
    for phrase in ("careeros memory import", "--apply --yes", "--quote", "! careeros memory confirm",
                   "Never try to pipe an answer into them", "claimed"):
        assert phrase in text


def test_claude_md_routes_to_the_memory_skill_and_describes_career(tmp_path: Path) -> None:
    text = (TEMPLATES / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")
    assert ".claude/skills/memory/SKILL.md" in text and "career/" in text and "careeros check" in text


def test_onboarding_imports_the_resume_into_the_memory() -> None:
    text = skill_text("onboard")
    assert "careeros memory import" in text and "claimed" in text


def test_refresh_delivers_the_gate_to_an_existing_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    (root / ".claude" / "skills" / "outreach" / "SKILL.md").write_text("old copy\n", encoding="utf-8")
    refreshed = scaffold(root, refresh=True, runtime="claude")
    assert ".claude/skills/outreach/SKILL.md" in refreshed and ".claude/skills/memory/SKILL.md" in refreshed
    assert "## EVIDENCE GATE (mandatory)" in (root / ".claude" / "skills" / "outreach" / "SKILL.md").read_text(encoding="utf-8")


def test_the_gpt_skills_do_not_claim_a_check_they_cannot_run() -> None:
    for path in (TEMPLATES / "gpt-workspace").rglob("*.md"):
        assert "careeros check" not in path.read_text(encoding="utf-8")
````

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_skills_evidence.py -q`
Expected: most tests fail (the gate text, the `memory` skill and the routing do not exist yet).

- [ ] **Step 3: Create the memory skill**

Create `careeros/templates/workspace/.claude/skills/memory/SKILL.md`:

````markdown
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
````

- [ ] **Step 4: Insert the gate, the routing and the onboarding step**

Run this one-off script from the repository root (it asserts every anchor exists exactly once, so it fails loudly instead of editing the wrong place):

````bash
python3 - <<'PYEOF'
base = "careeros/templates/workspace/"
GATE = """## EVIDENCE GATE (mandatory)

{applies}

Every claim in text you write for the user must trace to their career memory (`career/`). Before you show, send, paste or save any draft as final:

1. Save the final text after your last edit (after `humanize`, if you used it) to a file, or pass it on standard input with `-`.
2. Run `careeros check <file> --against <job-id> --record`. Leave out `--against` when the text is not for one job.
3. **Exit 0:** tell the user the result in these words, with the real count: "Evidence check passed: every checkable claim (numbers, years, technologies, employers, schools, titles, certifications) is supported by your career memory. It does not evaluate prose, and N supporting fact(s) are still only claimed, not confirmed." Never call a draft "verified" or "true".
4. **Exit 1:** do not show the draft. For every finding, remove or rewrite the claim, or ask the user whether it is true. If they say it is, add it with `careeros memory add ... --quote "<their exact words>"` and run the check again. `CHK010` means the claims in one sentence do not come from a single fact: rewrite the sentence so it says only what one fact says, or ask. Never use `--allow` to get past a claim about the user's own history; it is only for names or terms that come from the job or from other people.
5. **Exit 2:** the career memory is missing or broken. Tell the user, run `careeros validate`, and stop.

Re-run the check after every edit. Never skip it and never ignore a failure.

---

"""
APPLIES = {
    "prep": ("## STEP 0 — LOAD CONTEXT", "**Applies to:** the tailored resume (STEP 4B) and everything in the tailoring brief that states something about the user, including the cover-letter angle (STEP 4). Check each before you show it."),
    "apply": ("## STEP 0 — IDENTIFY THE JOB", "**Applies to:** the cover letter (STEP 1) and every free-text answer you type into the form (STEP 4). Check them before you show the letter or fill any field."),
    "outreach": ("## MODE A — LINKEDIN CONNECTION REQUEST", "**Applies to:** every connection note (A3) and every email (B2). Check each before you show it or send it."),
    "follow-up": ("## STEP 0 — LOAD CONTEXT", "**Applies to:** every follow-up message drafted in STEP 3 (LinkedIn, email and application). Check each before STEP 4 shows it."),
    "humanize": ("## STEP 1 — RUN THE CHECKLIST", "**Applies to:** the rewritten text (STEP 2). Humanizing may change wording, never facts: do not add a number, name, technology or claim that the original did not contain. The skill that called you runs its own gate after you finish; a direct \"humanize this\" request about the user's career text gets the gate here."),
    "interview": ("## STEP 0 — LOAD CONTEXT", "**Applies to:** the STAR story outlines (STEP 3) and any answer you draft for the user (STEP 5). Check them before you write the interview prep file."),
}
for skill, (anchor, applies) in APPLIES.items():
    path = f"{base}.claude/skills/{skill}/SKILL.md"
    text = open(path, encoding="utf-8").read()
    assert text.count("\n" + anchor + "\n") == 1, (skill, "anchor not found exactly once")
    open(path, "w", encoding="utf-8").write(text.replace("\n" + anchor + "\n", "\n" + GATE.format(applies=applies) + anchor + "\n"))

path = base + "CLAUDE.md"
text = open(path, encoding="utf-8").read()
old = "### Discovery\n"
assert text.count(old) == 1
text = text.replace(old, """### Career memory
| Trigger | Skill |
|---------|-------|
| "import my resume" / "remember that [fact]" / "add this achievement" / "what do you know about me?" / "confirm [fact]" | `.claude/skills/memory/SKILL.md` |

Every text you write for the user (resume, cover letter, outreach note, follow-up, interview answer) is checked with `careeros check` before you show it; see the EVIDENCE GATE in each skill.

### Discovery
""")
old = "activity.md             — append-only action log (source of truth for follow-up cadence)\n"
assert text.count(old) == 1
text = text.replace(old, old + "career/                 — your career memory: one file per fact, each with its source and status (claimed, confirmed, verified)\n")
open(path, "w", encoding="utf-8").write(text)

path = base + ".claude/skills/onboard/SKILL.md"
text = open(path, encoding="utf-8").read()
old = "If they skipped Q9, don't create this file — `prep` and `apply` will ask for it when needed.\n"
assert text.count(old) == 1
text = text.replace(old, old + "\nIf you created `resume.md`, import it into the career memory: run `careeros memory import`, show the plan, and after the user agrees run `careeros memory import --apply --yes`. Explain that the facts start as *claimed* and that every draft will be checked against them. (If the `careeros` command is not available, tell the user to run it later; do not skip the explanation.)\n")
open(path, "w", encoding="utf-8").write(text)
print("skills, CLAUDE.md and onboard patched")
PYEOF
````

- [ ] **Step 5: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/test_skills_evidence.py -q`
Expected: `18 passed`.

- [ ] **Step 6: Run everything, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `455 passed` (the existing scaffold and refresh tests must still pass with the extra skill).

````bash
git add careeros/templates/workspace/ tests/test_skills_evidence.py
git commit -m "skills: mandatory evidence gate in six drafting skills; memory skill and routing" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

---

### Task 8: Documentation, version, end-to-end test and acceptance on a copy

**Files:**
- Create: `docs/career-memory.md`, `tests/test_memory_e2e.py`
- Modify: `mkdocs.yml`, `README.md`, `CHANGELOG.md`, `pyproject.toml`, `docs/END_TO_END_TEST.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the released-feature documentation, version `0.4.0`, and an acceptance record containing counts and exit codes only (no personal text).

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_memory_e2e.py`:

````python
"""One realistic pass through the whole feature: import, confirm, check, retire, audit."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import make_workspace
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger
from careeros.core.memory import store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"


def run(root: Path, *args: str, input: str | None = None):
    return runner.invoke(app, [*args, "--workspace", str(root)], input=input)


def test_the_whole_flow(tmp_path: Path, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_workspace(tmp_path / "ws")
    shutil.copy(FIXTURE, root / "resume.md")
    resume_before = (root / "resume.md").read_bytes()

    # 1. import (dry run, then apply) and a second import changes nothing
    assert run(root, "memory", "import").exit_code == 0 and not (root / "career").exists()
    assert run(root, "memory", "import", "--apply", "--yes").exit_code == 0
    assert (root / "resume.md").read_bytes() == resume_before
    assert "Nothing to import" in run(root, "memory", "import", "--apply", "--yes").output

    # 2. a faithful draft passes, with its support reported as claimed
    faithful = "At Acme Corp I reduced AWS costs by 35% using Kafka-based pipelines."
    passed = run(root, "check", "-", "--record", input=faithful)
    assert passed.exit_code == 0 and "claimed rather than confirmed" in passed.output

    # 3. the user confirms the two facts it rests on; --require-confirmed now passes
    assert run(root, "check", "-", "--require-confirmed", input=faithful).exit_code == 1
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    career = store.load_career(root)
    cost = next(f for f in career.facts if "35%" in str(f.get("text")))
    acme = career.by_id()[cost.get("parent")]
    for fact in (cost, acme):
        assert run(root, "memory", "confirm", fact.id, input="y\n").exit_code == 0
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    assert run(root, "check", "-", input=faithful).exit_code == 0

    # 4. a tampered draft fails with the expected findings and a non-zero exit
    tampered = json.loads(run(root, "check", "-", "--json", input=(
        "Reduced AWS costs by 50% using Kafka. I worked at FakeCorp. I reduced costs by 60% with Kafka. I hold an MBA from Fake University."
    )).stdout)
    assert tampered["ok"] is False
    found = {f["code"] for f in tampered["review_required"]}
    assert {"CHK001", "CHK004", "CHK010", "CHK005"} <= found

    # 5. retiring the fact it relied on breaks the draft that used it
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    assert run(root, "memory", "retire", cost.id, "--reason", "I am not sure of this number", input="y\n").exit_code == 0
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    assert run(root, "check", "-", input=faithful).exit_code == 1

    # 6. the audit trail and the workspace are intact
    types = [e["type"] for e in ledger.read_events(root)]
    assert types.count("memory.imported") == 1 and "memory.fact_confirmed" in types and "memory.fact_retired" in types
    assert types.count("draft.checked") == 1
    assert ledger.verify_chain(root) == []
    assert run(root, "validate").exit_code == 0
````

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_memory_e2e.py -q`
Expected: `1 passed` (it exercises the finished feature, so it needs no implementation of its own).

- [ ] **Step 3: Write the user documentation**

Create `docs/career-memory.md`:

````markdown
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

Changing a confirmed or verified fact resets it to `claimed` and needs a terminal.

## Commands

| Command | What it does |
|---|---|
| `careeros memory import [--source FILE] [--apply] [--yes]` | Read `resume.md` (or another file), show a deterministic plan, and with `--apply` write it. Backs up every file it changes. |
| `careeros memory add --kind K --text "..." --set key=value --quote "your words"` | Add a fact you stated. Kinds: `experience`, `achievement`, `project`, `skill`, `education`, `certification`, `preference`, `goal`, `constraint`, `identity`, `evidence`. |
| `careeros memory update ID --text "..."` / `--set key=value` | Change a fact. |
| `careeros memory confirm ID` / `verify ID --evidence EVD` | Record your own decision. Interactive terminal only. |
| `careeros memory dispute ID --reason "..."` / `retire ID --reason "..."` | Mark a fact wrong or no longer applicable. |
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

Sections it does not know, and lines before the first `##`, are reported as *not imported*. A heading it cannot read (for example an experience heading without a `|`) is an error and **nothing is written**. Re-importing:

- never overwrites a `confirmed` or `verified` fact: it lists it under *needs review* with the proposed change;
- never touches facts you added or edited by hand;
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
| `CHK030` | the memory has no usable facts |
| `CHK031` | a source file changed since it was imported |

### `--allow` and `--against`

`--against JOB` lets a cover letter name the company and title it applies to. Working there still needs an experience record. `--allow TERM` lets a draft mention something that comes from the job post or from another person (a technology the team uses, a name); it is listed as an **unverified mention** and is never evidence. In a sentence with any other claim, an allowed term cannot stand in for a missing fact: the sentence is flagged.

### Common false positives

The checker prefers a false alarm to a missed fabrication. A person's name or a place it does not know raises `CHK009`: pass it with `--allow "Name"`, or add yourself with `careeros memory add --kind identity --set name="Your Name" --quote "..."` so your own name is known. Add real technologies you use as skills so they are recognised and supported.

## Memory validation

`careeros validate` reports problems in `career/`: `MEM001` unreadable file, `MEM002` missing key or bad or duplicate id, `MEM003` dangling reference, `MEM004` invalid status, origin, source or date, `MEM005` confirmed without a decision or verified without evidence, `MEM006` source changed since import, `MEM007` source file missing, `MEM008` stale fact, and `LED003` when a fact the ledger mentions has no file.

## GPT Work

The evidence gate is Claude Code only for now: GPT Work cannot run local commands, so its skills are unchanged and its drafts are not checked.
````

In `mkdocs.yml`, add this line to `nav` directly after `  - Workspace health and audit: foundation.md`:

````yaml
  - Career memory and evidence: career-memory.md
````

In `README.md`:

1. In the "Workspace layout" block, add this line after the `activity.md             — append-only action log` line:

````text
career/                 — your career memory: one file per fact, with its source and status
````

2. Add this section immediately before `## Requirements`:

````markdown
## Career memory and the evidence check

```bash
careeros memory import           # turn resume.md into career facts (shows a plan first)
careeros memory import --apply   # make the changes
careeros memory confirm <id>     # you affirm a fact (needs a terminal)
careeros check draft.md          # does every checkable claim trace to your facts?
```

Facts imported from your resume start as *claimed*; only you can confirm them. Each Claude Code drafting skill runs `careeros check` on its final text and fixes or asks about anything unsupported. A pass means the claims the checker could detect are supported, never that the whole draft is true. See `docs/career-memory.md`.
````

In `CHANGELOG.md`, insert this above `## 0.3.0 — Foundation`:

````markdown
## 0.4.0 — Career memory and the evidence check

### Added
- `careeros memory` (`import`, `add`, `update`, `confirm`, `verify`, `dispute`, `retire`, `list`, `show`, `status`) and a `career/` folder of provenance-tracked facts. Facts have a status (`claimed`, `confirmed`, `verified`, `disputed`, `retired`) that is separate from their source, and imports only ever create `claimed` facts.
- `careeros memory import`: a deterministic parse of `resume.md` with a reviewable diff. It never overwrites a confirmed fact, never touches facts you added or edited, marks facts whose line left the resume as stale instead of deleting them, backs up every file it changes, and rolls back if the ledger append fails.
- `careeros check`: compares the numbers, years, durations, technologies, employers, schools, titles and certifications in a draft with your facts, requires the claims in one sentence to come from a single fact, and reports what it did not evaluate. Exit 0 means every detected claim is supported, not that the draft is true.
- A mandatory EVIDENCE GATE in the `prep`, `apply`, `outreach`, `follow-up`, `humanize` and `interview` skills, and a `memory` skill. GPT Work is unchanged.
- `validate` reports `MEM001`-`MEM008` for the memory and `LED003` for memory ids the ledger mentions but no file has. New reserved ledger types: `memory.*`; `draft.checked` is recorded by `check --record`.

### Changed
- The verified-backup helpers moved to `careeros/core/backup.py`; migration behaviour is unchanged.

### Notes
- Run `careeros upgrade` in existing workspaces to receive the new skills.
- Known limits: the checker cannot see claims outside its detection rules (see `docs/career-memory.md`), a person's name raises `CHK009` unless allowed or added as an identity, and the importer understands the `resume.md` layout documented there.
````

In `pyproject.toml`, change `version = "0.3.0"` to `version = "0.4.0"`, then refresh the installed metadata so `careeros.__version__` reports it:

````bash
.venv/bin/pip install -e ".[dev]" -q
.venv/bin/python -c "import careeros; print(careeros.__version__)"   # expect 0.4.0
````

- [ ] **Step 4: Full suite, docs build, and the wheel contents**

Run each and keep the output for the report:

````bash
.venv/bin/python -m pytest -q                      # expect: 456 passed
.venv/bin/python -m mkdocs build --strict -q       # expect: exit status 0 (the Material "warning" banner is not a docs warning)
echo "mkdocs strict exit: $?"
TMP=$(mktemp -d) && .venv/bin/pip wheel . --no-deps -q -w "$TMP" && unzip -l "$TMP"/careeros-0.4.0-*.whl | grep -E "data/lexicon.yaml|skills/memory/SKILL.md"
# expect two lines. If the wheel cannot be built because the network is unavailable for the build backend, say so in the report.
````

- [ ] **Step 5: Run the whole suite on Python 3.11**

Python 3.11 is the supported minimum. Use the existing 3.11 environment if one exists, otherwise create one:

````bash
python3.11 -m venv "$TMPDIR/venv311" 2>/dev/null || /Users/kaushal/.local/bin/python3.11 -m venv "$TMPDIR/venv311"
"$TMPDIR/venv311/bin/pip" install -q -e ".[dev]"
"$TMPDIR/venv311/bin/python" --version
"$TMPDIR/venv311/bin/python" -m pytest -q          # expect: 456 passed
````

- [ ] **Step 6: Acceptance on a copy of the real workspace**

The real workspace `~/Projects/job-search` must not change. The previous acceptance run recorded a checksum of every file; do the same.

````bash
REAL="$HOME/Projects/job-search"
ACC=$(mktemp -d)
( cd "$REAL" && find . -type f -not -path './.git/*' -exec shasum -a 256 {} + | sort -k2 ) > "$ACC/real-before.txt"
cp -R "$REAL" "$ACC/ws"
# the real workspace is schema 0, so migrate the COPY first
.venv/bin/careeros migrate --yes -w "$ACC/ws"; echo "migrate exit: $?"
.venv/bin/careeros memory import -w "$ACC/ws"; echo "import dry-run exit: $?"      # record the summary counts only
.venv/bin/careeros memory import --apply --yes -w "$ACC/ws"; echo "import apply exit: $?"
.venv/bin/careeros memory import --apply --yes -w "$ACC/ws"; echo "second import exit: $?"   # expect: Nothing to import
cmp "$REAL/resume.md" "$ACC/ws/resume.md" && echo "resume.md identical"
.venv/bin/careeros memory status -w "$ACC/ws"
.venv/bin/careeros validate -w "$ACC/ws"; echo "validate exit: $?"
.venv/bin/careeros ledger verify -w "$ACC/ws"; echo "ledger verify exit: $?"
````

Then generate one faithful and three tampered drafts from the copy's own memory and check them. Save this helper as `$ACC/drafts.py` (it is not committed and prints no personal text):

````python
"""Write four drafts from a workspace's career memory: one faithful, three tampered. Prints no personal text."""
import re
import sys
from pathlib import Path

import yaml

from careeros.core.memory import lexicon as lexicon_module
from careeros.core.memory import store

root, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
career = store.load_career(root)
achievements = [f for f in career.facts if f.kind == "achievement" and f.get("metrics") and f.get("technologies")]
first = achievements[0]
raw = first.get("metrics")[0]["raw"]
bumped = re.sub(r"\d+(?:\.\d+)?", lambda m: str(int(float(m.group(0))) + 7), raw, count=1)
(out / "faithful.txt").write_text(first.get("text") + "\n", encoding="utf-8")
(out / "invented_metric.txt").write_text(first.get("text").replace(raw, bumped, 1) + "\n", encoding="utf-8")
(out / "invented_employer.txt").write_text("I worked at Fakecorpp Holdings for two years.\n", encoding="utf-8")
known = {t for f in career.facts for t in (f.get("technologies") or ())} | {str(f.get("name")) for f in career.facts if f.kind == "skill"}
bundled = yaml.safe_load(lexicon_module.BUNDLED.read_text(encoding="utf-8"))["technologies"]
invented_tech = next(t["name"] for t in bundled if t["name"] not in known)
(out / "invented_technology.txt").write_text(f"I built the platform with {invented_tech}.\n", encoding="utf-8")
# an unrepresented relationship: a number from one fact beside a technology from another that never mentions it
for a in achievements:
    metric = a.get("metrics")[0]["raw"]
    carriers = [f for f in achievements if any(m["raw"] == metric for m in f.get("metrics"))]
    for b in achievements:
        for tech in b.get("technologies"):
            if all(tech not in c.get("technologies") for c in carriers):
                (out / "unrepresented_relationship.txt").write_text(f"Reduced costs by {metric} using {tech}.\n", encoding="utf-8")
                raise SystemExit(0)
raise SystemExit("no unrepresented relationship could be built from this memory")
````
````bash
.venv/bin/python "$ACC/drafts.py" "$ACC/ws" "$ACC/drafts"
for f in "$ACC"/drafts/*.txt; do
  .venv/bin/careeros check "$f" -w "$ACC/ws" --json > "$ACC/out.json"; rc=$?
  echo "$(basename "$f"): exit $rc, codes: $(.venv/bin/python -c "import json;print(sorted({f['code'] for f in json.load(open('$ACC/out.json'))['review_required']}))")"
done
# expect: faithful.txt exit 0; the other four exit 1 with CHK001, CHK004 (or CHK009), CHK003 and CHK010 respectively
( cd "$REAL" && find . -type f -not -path './.git/*' -exec shasum -a 256 {} + | sort -k2 ) > "$ACC/real-after.txt"
cmp "$ACC/real-before.txt" "$ACC/real-after.txt" && echo "real workspace unchanged"
````

If the faithful draft does not exit 0, or a tampered draft exits 0, **stop and report it**; do not adjust the draft to make it pass. A real resume may contain a layout the grammar cannot place; in that case the import prints the line and nothing is written: report the message (the line number and shape, not the personal text) and stop.

- [ ] **Step 7: Record the acceptance run**

Append a section to `docs/END_TO_END_TEST.md` titled `## Career memory (0.4.0)` containing: the date, the commit under test, the method (copy, migrate the copy, checksums of the real workspace before and after), then a table of commands, exit codes and counts exactly as observed in Step 6 (number of facts by kind, summary counts of the import, the five draft results, `validate` and `ledger verify` results). Record counts, codes and exit statuses only: no resume text, names, employers or figures from the real workspace. State plainly anything not covered (the skills cannot be run live; they are covered by the wording tests only).

- [ ] **Step 8: Commit**

````bash
git add docs/career-memory.md docs/END_TO_END_TEST.md mkdocs.yml README.md CHANGELOG.md pyproject.toml tests/test_memory_e2e.py
git commit -m "docs and release: career memory documentation, 0.4.0, end-to-end test and acceptance record" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0191bMVfYrxF4bguT3GiNw8Y"
````

- [ ] **Step 9: Final report**

Report which commands you actually ran and their real output: the full suite on 3.14 and on 3.11 (counts), the strict docs build, the wheel check, the acceptance run results, and anything that did not run. Do not claim a result you did not see. Do not merge or push.

---

## Spec coverage

| Spec section | Where |
|---|---|
| §3-4 decisions D1-D7, tree, frontmatter, kind fields | Tasks 3-5, the tests named in the Review Focus |
| §5 statuses and provenance | Task 3 (`ops.py`, `store.py`), Task 6 guards |
| §6 import: grammar, diff, apply, backup, rollback, ledger | Task 5 |
| §7 operations and events | Tasks 3 and 6 |
| §8 checker: layers, atoms, relationships, `--allow`, `--against`, output, exit codes, guarantee wording | Tasks 4 and 6 |
| §9 lexicon | Task 2 |
| §10 validation `MEM001`-`MEM008`, `LED003` | Task 3 |
| §11 CLI | Task 6 |
| §12 skills and the evidence gate | Task 7 |
| §13 out of scope | nothing in this plan builds those items |
| §14 tests | one test group per task; end to end in Task 8 |
| §16 acceptance on a copy | Task 8, Step 6 |
| §17 build order | Task order |
| §18 clarifications | Task 1, Step 5 |
