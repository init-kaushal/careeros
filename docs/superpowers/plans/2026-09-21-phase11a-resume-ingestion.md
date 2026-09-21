# Phase 11a Evidence-Backed Resume Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every skill stored in a CareerOS profile carries a quote from the resume that has been deterministically verified to exist, so a skill the model invented cannot reach an employer.

**Architecture:** One LLM call returns candidate skills each claiming a verbatim quote; a pure verifier then checks each quote actually occurs in the source text and records its line. Unverifiable candidates are dropped and reported. `onboard` and a new `careeros resume ingest` both use this one extractor, so a stored skill means the same thing regardless of how it got there.

**Tech Stack:** Python 3.11+, pydantic v2 (`BaseModel`, nullable defaults), LiteLLM (`system` + `user` message pair), Typer, Rich, pytest with `unittest.mock.patch`.

**Spec:** `docs/superpowers/specs/2026-09-21-phase11a-resume-ingestion-design.md`

## Global Constraints

- **Quote matching normalizes whitespace and case, and NOTHING else.** No fuzzy matching, no partial matching, no stemming, no regex. The normalization that makes matching forgiving is exactly what admits a fabricated quote.
- **Never compile a quote as a regex.** Resumes contain `C++`, `.NET`, `(2018–present)`. Use plain containment over normalized text.
- **A quote longer than `MAX_QUOTE_CHARS = 200` is rejected as evidence**, even if it genuinely occurs. An unbounded quote verifies trivially and cites nothing.
- `last_used` is inferred and deliberately unverified — a soft ranking input, never a factual claim. The quote is the hard guarantee.
- Untrusted text into an LLM is wrapped: instructions in a `system` message, `wrap_untrusted(text)` in a `user` message (the Phase 9b pattern).
- Input capped at `_CONTENT_CAP = 4000`, declared locally in the new module as every other skill module does.
- All workspace I/O goes through the `StorageProvider` protocol.
- Activity logs are append-only; `status` is one of `success` / `failed` / `blocked`.
- String concatenation only for user-facing strings; no f-strings with user data.
- Tests run fully offline: `litellm.completion` mocked at the boundary; no network.
- A workspace is required exactly when writing to the activity log.
- Commits use plain `git commit` — the repo-local git identity is already configured. Do NOT pass `-c user.name`/`-c user.email`.

## Correction to the spec, already ruled

The spec places `Evidence` in `careeros/skills/resume_evidence.py` as a **frozen dataclass**. Implement it instead as a **pydantic `BaseModel` declared in `careeros/core/models.py`**, beside `Skill`. Two reasons:

1. `Skill` is a pydantic `BaseModel` serialized via `model_dump_json`. Phase 10 hit exactly this and resolved it the same way — `Sighting` is a `BaseModel` in `models.py` for precisely this reason. A nested frozen dataclass needs different handling and buys nothing here.
2. The spec's placement would make `careeros/core/models.py` import from `careeros/skills/`. Phase 9c already accepted one core→elsewhere inversion deliberately; adding a second by accident is how a cycle eventually arrives. `Evidence` is a data model, so it belongs with the data models, and `skills/` imports from `core/` in the conventional direction.

`verify_quote` and `MAX_QUOTE_CHARS` still live in `careeros/skills/resume_evidence.py` as the spec says.

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `careeros/skills/resume_evidence.py` | `MAX_QUOTE_CHARS`, `verify_quote`. Pure: no I/O, no LLM, no storage. |
| `careeros/skills/resume_ingest.py` | `IngestResult`, `ingest_resume`. One LLM call plus verification. |
| `careeros/cli/resume_cmd.py` | The `careeros resume` Typer app. The only module here that prints. |
| `tests/test_resume_evidence.py`, `tests/test_resume_ingest.py`, `tests/test_resume_cmd.py` | |

**Modify:**

| File | Change |
|---|---|
| `careeros/core/models.py` | Add `Evidence`; add `Skill.evidence`. |
| `careeros/skills/profile_extract.py` | Delete the skills half; return `Profile`. |
| `careeros/cli/onboard.py` | Call `extract_basic_profile` for profile, `ingest_resume` for skills. |
| `careeros/cli/main.py` | Register `resume_app`. |
| `tests/test_profile_extract.py` | Migrate 10 tests off the two-call shape. |
| `tests/test_onboard.py` | Update the patched return value; patch `ingest_resume`. |
| `README.md`, `ROADMAP.md` | Document `resume ingest`; record the 11a/11b split. |

---

### Task 1: `Evidence`, `Skill.evidence`, and the verifier

**Files:**
- Modify: `careeros/core/models.py` (add `Evidence` above `class Skill`; add one field to `Skill`)
- Create: `careeros/skills/resume_evidence.py`
- Create: `tests/test_resume_evidence.py`

**Interfaces:**
- Produces: `Evidence` (pydantic `BaseModel`: `quote: str`, `line: int`, `source_file: str`); `Skill.evidence: Evidence | None = None`; `MAX_QUOTE_CHARS = 200`; `verify_quote(quote: str, source_text: str) -> int | None`. Used by Tasks 2, 3, 4.

This task carries the phase's entire fabrication-resistance property in about fifteen lines. The two guards below are the ones that would otherwise let verification pass while meaning nothing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_evidence.py`:

```python
from careeros.core.models import Evidence, Skill
from careeros.skills.resume_evidence import MAX_QUOTE_CHARS, verify_quote

_RESUME = """Alice Johnson
Senior Site Reliability Engineer

EXPERIENCE
Site Reliability Engineer — MegaCorp (2018–present)
  - Built distributed monitoring handling 1M events/sec
  - Wrote C++ tooling and a .NET migration shim

SKILLS
Python, Go, Kubernetes, Terraform
"""


def test_exact_quote_returns_its_one_indexed_line():
    assert verify_quote("Python, Go, Kubernetes, Terraform", _RESUME) == 10


def test_quote_on_first_line_returns_one_not_zero():
    assert verify_quote("Alice Johnson", _RESUME) == 1


def test_whitespace_differences_still_match():
    assert verify_quote("Python,   Go,  Kubernetes, Terraform", _RESUME) == 10


def test_case_differences_still_match():
    assert verify_quote("PYTHON, GO, KUBERNETES, TERRAFORM", _RESUME) == 10


def test_absent_quote_returns_none():
    assert verify_quote("Rust, Haskell", _RESUME) is None


def test_quote_with_regex_metacharacters_matches_literally():
    # Resumes are full of these. Compiled as a pattern they raise or mismatch.
    assert verify_quote("C++ tooling", _RESUME) == 7
    assert verify_quote(".NET migration shim", _RESUME) == 7
    assert verify_quote("(2018–present)", _RESUME) == 5


def test_regex_pattern_does_not_match_as_a_pattern():
    # ".*" must be treated as literal text, not "anything".
    assert verify_quote(".*", _RESUME) is None


def test_overlong_quote_is_rejected_even_though_it_occurs():
    # An unbounded quote verifies trivially: hand back the whole resume and
    # every skill "checks out". Evidence that cites everything cites nothing.
    assert len(_RESUME) > MAX_QUOTE_CHARS
    assert verify_quote(_RESUME, _RESUME) is None


def test_quote_exactly_at_the_limit_is_accepted():
    quote = _RESUME[:MAX_QUOTE_CHARS]
    assert verify_quote(quote, _RESUME) == 1


def test_repeated_quote_returns_the_first_line():
    source = "Kubernetes\nsomething else\nKubernetes\n"
    assert verify_quote("Kubernetes", source) == 1


def test_quote_spanning_a_line_break_matches_and_returns_the_starting_line():
    # A wrapped bullet is one span to the model and two lines to us. The
    # per-line pass cannot see it, so the whole-document fallback must.
    assert verify_quote("Alice Johnson Senior Site Reliability Engineer", _RESUME) == 1


def test_quote_spanning_a_line_break_deeper_in_the_document():
    assert verify_quote("MegaCorp (2018–present) - Built distributed", _RESUME) == 5


def test_words_not_adjacent_in_the_source_do_not_match():
    # The fallback normalizes the whole document, so it must still require
    # contiguity — otherwise any bag of words in the resume "verifies".
    assert verify_quote("Alice Kubernetes", _RESUME) is None


def test_empty_quote_returns_none():
    assert verify_quote("", _RESUME) is None
    assert verify_quote("   ", _RESUME) is None


def test_empty_source_returns_none():
    assert verify_quote("Python", "") is None


def test_evidence_round_trips_through_json():
    skill = Skill(
        name="Python",
        evidence=Evidence(quote="Python, Go", line=10, source_file="resumes/master.md"),
    )
    loaded = Skill.model_validate_json(skill.model_dump_json())
    assert loaded.evidence is not None
    assert loaded.evidence.quote == "Python, Go"
    assert loaded.evidence.line == 10
    assert loaded.evidence.source_file == "resumes/master.md"


def test_skill_without_evidence_still_loads():
    # A pre-Phase-11a skills.json has no "evidence" key at all. The spec's
    # "no migration needed" claim depends on this.
    import json
    legacy = {"name": "Python", "level": "expert", "source": "resume"}
    assert "evidence" not in legacy
    skill = Skill.model_validate_json(json.dumps(legacy))
    assert skill.evidence is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_evidence.py -v`
Expected: FAIL with `ImportError: cannot import name 'Evidence' from 'careeros.core.models'`

- [ ] **Step 3: Add `Evidence` and `Skill.evidence` to `careeros/core/models.py`**

Declare `Evidence` immediately above `class Skill`:

```python
class Evidence(BaseModel):
    """Where a skill was found in a resume, verified to actually be there.

    `quote` is verbatim source text whose presence was checked by
    careeros.skills.resume_evidence.verify_quote; `line` is where it was
    found. A Skill carrying Evidence is one the model could not have
    invented.
    """

    quote: str
    line: int
    source_file: str
```

Add the field to `Skill`, after `last_used`:

```python
    evidence: Evidence | None = None
```

- [ ] **Step 4: Create `careeros/skills/resume_evidence.py`**

```python
from __future__ import annotations

# A quote longer than this is rejected as evidence even when it genuinely
# occurs. An unbounded quote verifies trivially — hand back the whole
# resume and every skill "checks out". Evidence that cites everything
# cites nothing.
MAX_QUOTE_CHARS = 200


def _normalize(text: str) -> str:
    """Collapse whitespace and fold case. Deliberately nothing else.

    No fuzzy matching, no partial matching, no stemming. The normalization
    that makes matching forgiving is exactly what lets a fabricated quote
    through, and here a false match is an invented skill wearing a citation
    indistinguishable from a real one.
    """
    return " ".join(text.lower().split())


def verify_quote(quote: str, source_text: str) -> int | None:
    """Return the 1-indexed line where `quote` occurs, or None.

    Plain containment, never a regex: resumes are full of `C++`, `.NET`
    and `(2018–present)`, which as patterns either raise or match wrongly.
    """
    if not quote or not quote.strip():
        return None
    if len(quote) > MAX_QUOTE_CHARS:
        return None

    needle = _normalize(quote)
    if not needle:
        return None

    lines = source_text.split("\n")
    for index, line in enumerate(lines, start=1):
        if needle in _normalize(line):
            return index

    # The quote may span a line break in the source; fall back to locating
    # it in the normalized whole and reporting the line the match starts on.
    haystack = _normalize(source_text)
    if needle not in haystack:
        return None
    prefix_words = len(haystack[: haystack.index(needle)].split())
    seen = 0
    for index, line in enumerate(lines, start=1):
        words = len(_normalize(line).split())
        if seen + words > prefix_words:
            return index
        seen += words
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_evidence.py -v`
Expected: 17 PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 17 tests added, no failures. If a pre-existing test fails because `Skill` gained a field, it is comparing a whole serialized dict — update the expectation, do not remove the field.

- [ ] **Step 7: Commit**

```bash
git add careeros/core/models.py careeros/skills/resume_evidence.py tests/test_resume_evidence.py
git commit -m "feat: add Evidence model and deterministic quote verifier (Phase 11a)"
```

---

### Task 2: `ingest_resume`

**Files:**
- Create: `careeros/skills/resume_ingest.py`
- Create: `tests/test_resume_ingest.py`

**Interfaces:**
- Consumes: `Evidence` (Task 1) from `careeros.core.models`; `verify_quote` (Task 1); `wrap_untrusted` from `careeros.skills.sanitize`; `Skill`, `Skills` from `careeros.core.models`.
- Produces: `IngestResult` (frozen dataclass: `skills: Skills`, `dropped: tuple[str, ...]`); `ingest_resume(resume_text: str, source_file: str, model: str | None = None) -> IngestResult`. Used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_ingest.py`:

```python
from unittest.mock import MagicMock, patch

from careeros.skills.resume_ingest import ingest_resume

_RESUME = """Alice Johnson
SKILLS
Python, Go, Kubernetes
"""


def _resp(text):
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message.content = text
    return r


def _payload(*skills):
    import json
    return json.dumps({"skills": list(skills)})


def test_verified_candidates_become_skills_with_evidence():
    payload = _payload(
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
    )
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")

    assert result.dropped == ()
    assert len(result.skills.skills) == 1
    skill = result.skills.skills[0]
    assert skill.name == "Python"
    assert skill.evidence is not None
    assert skill.evidence.quote == "Python, Go, Kubernetes"
    assert skill.evidence.line == 3
    assert skill.evidence.source_file == "resumes/master.md"


def test_fabricated_skill_is_dropped():
    # The test this phase exists for: a skill whose quote is nowhere in the
    # resume never reaches the profile.
    payload = _payload(
        {"name": "Rust", "quote": "Expert in Rust since 2015", "last_used": "2026"},
    )
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")

    assert result.skills.skills == []
    assert result.dropped == ("Rust",)


def test_mixed_candidates_keep_exactly_the_real_ones():
    payload = _payload(
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
        {"name": "Rust", "quote": "Expert in Rust since 2015", "last_used": "2026"},
        {"name": "Go", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
    )
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")

    assert [s.name for s in result.skills.skills] == ["Python", "Go"]
    assert result.dropped == ("Rust",)


def test_last_used_is_stored_without_verification():
    # last_used is an inference across lines, not a quotable string.
    payload = _payload(
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2018"},
    )
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills[0].last_used == "2018"


def test_duplicate_names_are_collapsed_keeping_the_first():
    payload = _payload(
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
        {"name": "python", "quote": "Python, Go", "last_used": "2020"},
    )
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert len(result.skills.skills) == 1
    assert result.skills.skills[0].last_used == "2026"


def test_input_is_capped_so_late_content_cannot_be_cited():
    from careeros.skills.resume_ingest import _CONTENT_CAP
    long_resume = ("filler line\n" * 5000) + "SKILLS\nRust\n"
    assert len(long_resume) > _CONTENT_CAP
    payload = _payload({"name": "Rust", "quote": "Rust", "last_used": "2026"})
    with patch("litellm.completion", return_value=_resp(payload)) as mock:
        result = ingest_resume(long_resume, "resumes/master.md")

    sent = mock.call_args.kwargs["messages"][1]["content"]
    assert len(sent) < len(long_resume)
    assert result.dropped == ("Rust",)


def test_llm_exception_yields_an_empty_result_without_raising():
    with patch("litellm.completion", side_effect=RuntimeError("boom")):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []
    assert result.dropped == ()


def test_unparseable_response_yields_an_empty_result():
    with patch("litellm.completion", return_value=_resp("not json at all")):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []


def test_uses_system_plus_wrapped_user_messages():
    payload = _payload({"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"})
    with patch("litellm.completion", return_value=_resp(payload)) as mock:
        ingest_resume(_RESUME, "resumes/master.md")

    messages = mock.call_args.kwargs["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "<untrusted_content>" in messages[1]["content"]
    assert "<untrusted_content>" not in messages[0]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.skills.resume_ingest'`

- [ ] **Step 3: Create `careeros/skills/resume_ingest.py`**

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import litellm

from careeros.core.models import Evidence, Skill, Skills
from careeros.skills.resume_evidence import verify_quote
from careeros.skills.sanitize import wrap_untrusted

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_CONTENT_CAP = 4000

_INGEST_INSTRUCTIONS = """\
Extract the candidate's technical skills from the resume below.
Return ONLY valid JSON — no markdown, no explanation.
Limit to the 20 most prominent skills.

For each skill you MUST supply a "quote": a short verbatim span copied
exactly from the resume, 200 characters or fewer, that demonstrates the
skill. Copy it character for character. Do not paraphrase, summarise, or
construct a quote. If you cannot find a verbatim span for a skill, omit
that skill entirely.

"last_used" is the year the skill was most recently used, inferred from
the role the evidence sits under, or null if unclear.

{"skills": [{"name": "<skill>", "quote": "<verbatim span>", "last_used": "<year or null>"}]}
"""


@dataclass(frozen=True)
class IngestResult:
    skills: Skills
    dropped: tuple[str, ...]


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def ingest_resume(
    resume_text: str, source_file: str, model: str | None = None
) -> IngestResult:
    """Extract skills, keeping only those whose quote is verifiably present.

    Never raises: any LLM or parse failure yields an empty IngestResult,
    matching the sentinel pattern every other skill in this codebase uses.
    """
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    capped = resume_text[:_CONTENT_CAP]

    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _INGEST_INSTRUCTIONS},
                {"role": "user", "content": "Resume:\n" + wrap_untrusted(capped)},
            ],
        )
        payload = _parse_json(resp.choices[0].message.content)
    except Exception:
        return IngestResult(skills=Skills(), dropped=())

    verified: list[Skill] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for candidate in payload.get("skills", []):
        if not isinstance(candidate, dict):
            continue
        name = (candidate.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue

        line = verify_quote(candidate.get("quote") or "", capped)
        if line is None:
            seen.add(key)
            dropped.append(name)
            continue

        seen.add(key)
        verified.append(Skill(
            name=name,
            last_used=candidate.get("last_used"),
            source=source_file,
            evidence=Evidence(
                quote=candidate["quote"], line=line, source_file=source_file,
            ),
        ))

    return IngestResult(skills=Skills(skills=verified), dropped=tuple(dropped))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_ingest.py -v`
Expected: 9 PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 9 tests added since Task 1, no failures.

- [ ] **Step 6: Commit**

```bash
git add careeros/skills/resume_ingest.py tests/test_resume_ingest.py
git commit -m "feat: add evidence-verified resume ingestion skill (Phase 11a)"
```

---

### Task 3: `careeros resume ingest`

**Files:**
- Create: `careeros/cli/resume_cmd.py`
- Create: `tests/test_resume_cmd.py`
- Modify: `careeros/cli/main.py`

**Interfaces:**
- Consumes: `ingest_resume`, `IngestResult` (Task 2).
- Produces: `resume_app`, registered as `careeros resume`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_cmd.py`. Follow the conventions in `tests/test_workspace_cmd.py` for workspace setup — read that file first and reuse its fixture shape rather than inventing one.

```python
import json
from unittest.mock import patch

from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core.models import Evidence, Skill, Skills
from careeros.skills.resume_ingest import IngestResult
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    storage.atomic_write("resumes/master.md", b"SKILLS\nPython, Go\n")
    return str(tmp_path), storage


def _result(names=("Python",), dropped=()):
    skills = [
        Skill(
            name=n,
            source="resumes/master.md",
            evidence=Evidence(quote="Python, Go", line=2, source_file="resumes/master.md"),
        )
        for n in names
    ]
    return IngestResult(skills=Skills(skills=skills), dropped=tuple(dropped))


def test_ingest_with_no_path_reads_master(tmp_path):
    ws, storage = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result()) as ing:
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert result.exit_code == 0
    assert ing.call_args.args[0] == "SKILLS\nPython, Go\n"
    assert ing.call_args.args[1] == "resumes/master.md"


def test_ingest_writes_skills_with_evidence(tmp_path):
    ws, storage = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result()):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    raw = json.loads(storage.read("profile/skills.json").decode())
    assert raw["skills"][0]["evidence"]["quote"] == "Python, Go"
    assert raw["skills"][0]["evidence"]["line"] == 2


def test_ingest_with_a_path_replaces_master_then_ingests(tmp_path):
    ws, storage = _workspace(tmp_path)
    new_resume = tmp_path / "updated.md"
    new_resume.write_text("SKILLS\nRust, Zig\n")
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result()) as ing:
        result = runner.invoke(app, ["resume", "ingest", str(new_resume), "--workspace", ws])
    assert result.exit_code == 0
    assert storage.read("resumes/master.md").decode() == "SKILLS\nRust, Zig\n"
    assert ing.call_args.args[0] == "SKILLS\nRust, Zig\n"


def test_ingest_logs_verified_and_dropped_counts(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=("Python",), dropped=("Rust",))):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert len(ingested) == 1
    assert "1" in ingested[0]["summary"]


def test_ingest_names_the_dropped_skills_in_output(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=("Python",), dropped=("Rust", "Haskell"))):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert "Rust" in result.output
    assert "Haskell" in result.output


def test_zero_verified_skills_exits_non_zero(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=(), dropped=("Rust",))):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert result.exit_code == 1
    assert "Rust" in result.output


def test_missing_path_exits_one_naming_it(tmp_path):
    ws, _ = _workspace(tmp_path)
    result = runner.invoke(app, ["resume", "ingest", str(tmp_path / "nope.md"), "--workspace", ws])
    assert result.exit_code == 1
    assert "nope.md" in result.output


def test_requires_a_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "missing.json")
    result = runner.invoke(app, ["resume", "ingest"])
    assert result.exit_code == 1
    assert "No workspace configured" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_cmd.py -v`
Expected: FAIL — `resume` is not a registered command.

- [ ] **Step 3: Create `careeros/cli/resume_cmd.py`**

```python
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from careeros.config import GlobalConfig
from careeros.runtime.factory import open_local_runtime
from careeros.skills.resume_ingest import ingest_resume
from careeros.storage.filesystem import LocalFilesystemStorage

resume_app = typer.Typer(name="resume", help="Ingest and inspect your resume.")

_MASTER = "resumes/master.md"


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


@resume_app.command()
def ingest(
    path: str = typer.Argument(None, help="Resume file to ingest; defaults to the stored master"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    """Extract skills from your resume, keeping only evidence-backed ones."""
    # A workspace is required because this writes both the skill file and an
    # audit event.
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    if path:
        source = Path(path).expanduser()
        if not source.exists():
            rprint("[red]File not found: " + str(source) + "[/red]")
            raise typer.Exit(1)
        try:
            text = source.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            rprint("[red]Could not read " + str(source) + ": " + type(exc).__name__ + "[/red]")
            raise typer.Exit(1)
        runtime.storage.atomic_write(_MASTER, text.encode())
    else:
        if not runtime.storage.exists(_MASTER):
            rprint("[red]No resume at " + _MASTER + ". Run 'careeros onboard' first.[/red]")
            raise typer.Exit(1)
        text = runtime.storage.read(_MASTER).decode()

    rprint("Ingesting " + _MASTER + "...")
    result = ingest_resume(text, _MASTER, model=model)

    verified = len(result.skills.skills)
    result.skills.save(runtime.storage)

    runtime.record_activity(runtime.new_event(
        "resume_ingested", "ingest",
        "Ingested " + _MASTER + ": " + str(verified) + " verified, "
        + str(len(result.dropped)) + " dropped",
        entity_type="resume",
    ))

    if result.dropped:
        rprint("[yellow]Dropped " + str(len(result.dropped))
               + " skill(s) with no verifiable quote: " + ", ".join(result.dropped)
               + "[/yellow]")

    if verified == 0:
        rprint("[red]No skills could be verified against " + _MASTER + ".[/red]")
        raise typer.Exit(1)

    rprint("[green]Stored " + str(verified) + " evidence-backed skill(s).[/green]")
```

- [ ] **Step 4: Register the app in `careeros/cli/main.py`**

Add the import beside the others:

```python
from careeros.cli.resume_cmd import resume_app
```

and the registration after the `workspace` line:

```python
app.add_typer(resume_app, name="resume")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_cmd.py -v`
Expected: 8 PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 8 tests added since Task 2, no failures.

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/resume_cmd.py tests/test_resume_cmd.py careeros/cli/main.py
git commit -m "feat: add 'careeros resume ingest' command (Phase 11a)"
```

---

### Task 4: Onboard switches to the one extractor

**Files:**
- Modify: `careeros/skills/profile_extract.py` (delete the skills half; change the return type)
- Modify: `careeros/cli/onboard.py:58-70`
- Modify: `tests/test_profile_extract.py` (10 tests, most feeding two mock responses)
- Modify: `tests/test_onboard.py:28`

**Interfaces:**
- Consumes: `ingest_resume` (Task 2).
- Produces: `extract_basic_profile(resume_text: str, model: str | None = None) -> Profile` — **a signature change** from `tuple[Profile, Skills]`.

This is the task that makes the guarantee mean something: after it, there is exactly one extractor and `profile/skills.json` always means the same thing.

**The integration hazard is the test file.** `tests/test_profile_extract.py` has 10 tests and a `_mock_completion(texts: list[str])` helper that feeds `side_effect` a list of responses, because the function makes two LLM calls. After this change it makes one. Every test passing a two-element list must drop the second element, and every `profile, skills = ...` unpack must become `profile = ...`. Update them properly — do not leave a test asserting on a skills value that no longer exists, and do not weaken an assertion to make it pass.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_onboard.py`:

```python
def test_onboard_uses_evidence_backed_ingestion_for_skills(tmp_path, resume_file, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    from careeros.core.models import Evidence, Profile, Skill, Skills
    from careeros.skills.resume_ingest import IngestResult

    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    ingested = IngestResult(
        skills=Skills(skills=[Skill(
            name="Kubernetes",
            source="resumes/master.md",
            evidence=Evidence(quote="Kubernetes", line=1, source_file="resumes/master.md"),
        )]),
        dropped=("Rust",),
    )
    runner = CliRunner()
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("careeros.cli.onboard.ingest_resume", return_value=ingested):
        result, ws_path = _run_onboard(runner, tmp_path, resume_file)

    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("profile/skills.json").decode())
    assert raw["skills"][0]["evidence"]["quote"] == "Kubernetes"


def test_onboard_completes_when_nothing_verifies(tmp_path, resume_file, monkeypatch):
    # A sparse profile is the honest outcome of the guarantee; it must not
    # be a fatal one.
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    from careeros.core.models import Profile, Skills
    from careeros.skills.resume_ingest import IngestResult

    profile = Profile(name="Alice Johnson", title="Senior SRE")
    empty = IngestResult(skills=Skills(), dropped=("Rust", "Go"))
    runner = CliRunner()
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("careeros.cli.onboard.ingest_resume", return_value=empty):
        result, ws_path = _run_onboard(runner, tmp_path, resume_file)

    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(ws_path)
    assert json.loads(storage.read("profile/skills.json").decode())["skills"] == []
```

Add `json` and `LocalFilesystemStorage` to that file's imports if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_onboard.py -v`
Expected: FAIL — `careeros.cli.onboard` has no attribute `ingest_resume`.

- [ ] **Step 3: Strip the skills half from `careeros/skills/profile_extract.py`**

Delete `_SKILLS_INSTRUCTIONS` entirely, delete the second `litellm.completion` call and the `Skill`/`Skills` handling that follows it, and drop `Skill, Skills` from the `careeros.core.models` import. Change the signature and return:

```python
def extract_basic_profile(
    resume_text: str,
    model: str | None = None,
) -> Profile:
    """Extract identity fields from a resume.

    Skills are NOT extracted here — careeros.skills.resume_ingest.ingest_resume
    owns those, because a skill needs verified evidence and this does not
    produce any.
    """
```

The function must still return an empty `Profile()` rather than raising on any failure — that behaviour is unchanged and onboarding depends on it.

- [ ] **Step 4: Rewire `careeros/cli/onboard.py`**

Add the import:

```python
from careeros.skills.resume_ingest import ingest_resume
```

Replace the extraction block (around lines 58-70) so the profile and the skills come from their own calls:

```python
    rprint("\nExtracting profile from resume...")
    profile = extract_basic_profile(resume_text)
    ingested = ingest_resume(resume_text, "resumes/master.md")
    skills = ingested.skills

    rprint("\n[bold]Extracted profile:[/bold]")
    rprint(f"  Name:       {profile.name or '(not found)'}")
    rprint(f"  Title:      {profile.title or '(not found)'}")
    rprint(f"  Experience: {profile.years_of_experience or '?'} years")
    rprint(f"  Skills:     {len(skills.skills)} verified")
    if ingested.dropped:
        rprint("[yellow]  Dropped " + str(len(ingested.dropped))
               + " skill(s) with no verifiable quote: "
               + ", ".join(ingested.dropped) + "[/yellow]")
```

Leave the rest of onboarding — the confirm prompt, the saves, the activity events — exactly as it is.

- [ ] **Step 5: Migrate `tests/test_profile_extract.py`**

The file has exactly 10 tests. Eight of them pass a two-element list to `_mock_completion`
because the function made two calls; after this change it makes one. Here is every change,
by test:

| Test (line) | Change |
|---|---|
| `test_returns_profile_and_skills` (17) | Rename to `test_returns_profile`. Drop `skills_json` from the list and the `skills_json` local. Change the unpack to `profile = extract_basic_profile("dummy resume")`. **Delete the four `skills.*` assertions** — that behaviour now lives in `tests/test_resume_ingest.py`. Keep the three `profile.*` assertions verbatim. |
| `test_handles_null_fields` (32) | Drop `skills_json` from the list; change the unpack to `profile = ...`. Keep every profile assertion. |
| `test_makes_exactly_two_api_calls` (44) | Rename to `test_makes_exactly_one_api_call`; drop `skills_json`; change the assertion to `mock_comp.call_count == 1`. |
| `test_uses_default_model` (54) | Drop `skills_json`. No other change. |
| `test_respects_model_env_var` (66) | Drop `skills_json`. No other change. |
| `test_model_param_overrides_env` (78) | Drop `skills_json`. No other change. |
| `test_returns_empty_sentinel_on_llm_failure` (90) | Change any `profile, skills = ...` unpack to `profile = ...`; assert on `Profile()` only. Drop a `skills == Skills()` assertion if present. |
| `test_returns_empty_sentinel_on_malformed_json` (97) | Already passes a one-element list — leave the mock alone. Change the unpack if present; drop any skills assertion. |
| `test_resume_text_truncated_at_4000_chars` (104) | Drop `skills_json`. The truncation assertion stays; it is now the *only* call, so any `call_args_list[0]` indexing still works. |
| `test_resume_text_included_in_prompt` (118) | Drop `skills_json`. No other change. |

Remove `Skills` from the file's imports if nothing references it afterwards. Do not delete
any test outright — every one above retains a profile-side assertion worth keeping.

Then `tests/test_onboard.py`:

- Line 28's `patch("careeros.cli.onboard.extract_basic_profile", return_value=(profile, skills))`
  becomes `return_value=profile`.
- The same fixture must also patch `careeros.cli.onboard.ingest_resume`, or the real
  function runs during onboarding tests and attempts a live LLM call. Give it an
  `IngestResult(skills=skills, dropped=())` built from the fixture's existing `skills`
  value, so the tests that assert on stored skills keep working unchanged.

- [ ] **Step 6: Run the affected tests**

Run: `.venv/bin/python -m pytest tests/test_profile_extract.py tests/test_onboard.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: the two new onboard tests added; any deleted skills-only tests subtracted. Report the actual total rather than adjusting anything to hit a number.

- [ ] **Step 8: Commit**

```bash
git add careeros/skills/profile_extract.py careeros/cli/onboard.py tests/test_profile_extract.py tests/test_onboard.py
git commit -m "feat: onboard uses evidence-backed ingestion for skills (Phase 11a)"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md`
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes: the finished command from Task 3.
- Produces: nothing code-facing.

- [ ] **Step 1: Document the command in `README.md`**

Under **Workspace**, after the `onboard` bullet:

```markdown
- **`careeros resume ingest [path]`** — re-extract your skills from your resume, keeping
  only those backed by a verbatim quote that is verified to appear in the file. Skills the
  model could not evidence are dropped and named, rather than silently stored. Pass a path
  to replace your stored master resume first.
```

Amend the `onboard` bullet so it stops implying an unqualified extraction:

```markdown
- **`careeros onboard`** — interactive wizard that creates a workspace, extracts a
  structured profile from your resume via LLM, and writes it to `profile/profile.json`.
  Skills are extracted separately and kept only when backed by a verified quote.
```

- [ ] **Step 2: Record the 11a/11b split in `ROADMAP.md`**

Replace the Phase 11 heading and add a status paragraph, keeping the existing "What it builds" bullets that belong to 11b:

```markdown
## Phase 11 — Deep Resume Intelligence + Resume Variants

**Status: 11a shipped.** Evidence-backed ingestion and `careeros resume ingest` are done:
every stored skill carries a verbatim quote whose presence in the resume was
deterministically verified, and a skill the model cannot evidence is dropped rather than
stored. `onboard` uses the same extractor, so a stored skill means one thing.

**11b — resume variants — remains.** It was split out because it needs a decision 11a did
not: `apply` hands a file path to the ATS form uploader, so a tailored "variant" has to be
an uploadable `.pdf`/`.docx`, and the project has no document renderer. That question gets
its own spec rather than riding along with an extraction task.
```

Leave the Phase 11 "Why" and the variant-related bullets in place — they still describe 11b accurately.

- [ ] **Step 3: Verify no stale claims remain**

Run: `grep -rn "top technical skills\|skills.json" README.md docs/getting-started.md --exclude-dir=superpowers`
Expected: any hit describes skills as evidence-backed, or describes the file's location only. Fix any wording that implies skills are extracted without verification.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: unchanged from Task 4 — this task touches no code.

- [ ] **Step 5: Commit**

```bash
git add README.md ROADMAP.md
git commit -m "docs: document resume ingest and record the 11a/11b split"
```
