# Phase 11b — Job-Tailored Resume Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `careeros apply --job <id>` uploads a resume tailored to that job — assembled only from verbatim spans of the master resume — instead of the alphabetically-last file in `resumes/versions/`.

**Architecture:** An LLM selects and orders spans of the master resume for a given job description; three deterministic guards (heading allowlist, `verify_quote`, duplicate collapse) reject anything it did not copy verbatim. The survivors render to HTML and then to PDF through the Playwright Chromium already in the dependency tree, and `apply` looks up the per-job variant by exact path rather than scanning alphabetically.

**Tech Stack:** Python 3.11+, typer, rich, litellm, pydantic v2, playwright (Chromium `page.pdf()`), pytest

**Spec:** `docs/superpowers/specs/2026-09-22-phase11b-resume-variants-design.md`

## Global Constraints

- All workspace I/O goes through the `StorageProvider` protocol. Never write a workspace file with `open()` or `pathlib`; `render_pdf` therefore returns bytes rather than writing a path.
- Never compile a quote as a regex. `verify_quote` matches with `str.find` plus index arithmetic and stays that way.
- Never edit anything under `docs/superpowers/`. Those are historical records.
- No hardcoded test counts in README or any doc. A previous phase deliberately deleted them.
- Untrusted text reaches the model only inside `wrap_untrusted()`, with instructions in the `system` message and content in the `user` message.
- Verification always runs against exactly the capped text the model saw, never the full file.
- `MAX_QUOTE_CHARS = 200` in `careeros/skills/resume_evidence.py` is reused unchanged. Do not parameterize it, do not raise it.
- Rendering uses an ephemeral `chromium.launch(headless=True)`. Never `launch_persistent_context` — that profile belongs to browsing sessions and Phase 9c isolated it.
- `set_content` only. No `page.goto`, no external asset references in the HTML template.
- Every new public entry point that a CLI command depends on carries a never-raises contract, and the adversarial tests for it are written in the same task, not deferred.
- No test may assert only that a call did not raise.
- Commit with plain `git commit`. Repo-local user config is already correct; never pass `-c user.name` or `-c user.email`.
- Run `.venv/bin/python -m pytest` before every commit. Baseline at the start of this plan is 569 passed, 6 skipped.

---

## File Structure

| File | Responsibility |
|---|---|
| `careeros/core/models.py` (modify) | `VariantSection`, `ResumeVariant`, and the three storage-path helpers every consumer shares |
| `careeros/skills/resume_evidence.py` (modify) | Expose `normalize_quote` so dedup outside the module uses the same form `verify_quote` matches on |
| `careeros/skills/resume_variant.py` (create) | Pure selection: one LLM call, three deterministic guards, never raises |
| `careeros/render/__init__.py` (create) | Package marker |
| `careeros/render/resume_html.py` (create) | Pure: `ResumeVariant` + `Profile` → escaped HTML with inline CSS |
| `careeros/render/resume_pdf.py` (create) | Thin: HTML → PDF bytes via ephemeral Chromium; `RendererUnavailable` |
| `careeros/cli/resume_cmd.py` (modify) | `careeros resume variant --job <id>` |
| `careeros/core/resume_select.py` (create) | `select_resume` — exact per-job lookup, then top-level-only fallback |
| `careeros/cli/apply_cmd.py` (modify) | Use `select_resume`; state which resume is uploading and whether it is tailored |
| `careeros/cli/discover_and_apply_cmd.py` (modify) | Same, with selection moved inside the per-job loop |
| `README.md` (modify) | Document the command, the layout, and the verbatim guarantee |

---

## Task 1: `ResumeVariant` model and the pure selector

**Files:**
- Modify: `careeros/core/models.py` (add after the `Evidence`/`Skill` block, before `class Preferences`)
- Modify: `careeros/skills/resume_evidence.py` (add one public function)
- Create: `careeros/skills/resume_variant.py`
- Test: `tests/test_resume_variant.py`

**Interfaces:**
- Consumes: `verify_quote(quote: str, source_text: str) -> int | None` and `MAX_QUOTE_CHARS` from `careeros/skills/resume_evidence.py`; `Evidence(quote, line, source_file)` and `Skills` from `careeros/core/models.py`; `wrap_untrusted(text) -> str` from `careeros/skills/sanitize.py`.
- Produces:
  - `normalize_quote(text: str) -> str` in `resume_evidence.py`
  - `VariantSection(heading: str, entries: list[Evidence])`
  - `ResumeVariant(job_id, job_company, job_title, generated_at, source_file, sections, dropped)` with `entry_count() -> int`, and static methods `variant_dir(job_id) -> str`, `pdf_path(job_id) -> str`, `json_path(job_id) -> str`
  - `VariantResult(sections: tuple[VariantSection, ...], dropped: tuple[str, ...], error: str | None)` with `entry_count() -> int`
  - `select_variant_content(jd_text: str, master_text: str, skills: Skills, source_file: str, model: str | None = None) -> VariantResult`
  - `ALLOWED_HEADINGS: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests for the model and path helpers**

Create `tests/test_resume_variant.py`:

```python
import json
from unittest.mock import MagicMock, patch

from careeros.core.models import Evidence, ResumeVariant, Skill, Skills, VariantSection
from careeros.skills.resume_evidence import normalize_quote

# NOTE: do not import from careeros.skills.resume_variant yet. That module
# does not exist until Step 8, and a module-level import of it here would
# make this whole file fail at collection — including the four model tests
# Step 5 asks you to run. Step 6 adds those imports.

_MASTER = """Alice Johnson
Senior Site Reliability Engineer

EXPERIENCE
Site Reliability Engineer - MegaCorp (2018-present)
  - Built distributed monitoring handling 1M events/sec
  - Wrote C++ tooling and a .NET migration shim

SKILLS
Python, Go, Kubernetes, Terraform
"""


def _variant(job_id="acme-sre-abc1"):
    return ResumeVariant(
        job_id=job_id,
        job_company="Acme",
        job_title="Senior SRE",
        generated_at="2026-09-22T00:00:00+00:00",
        source_file="resumes/master.md",
        sections=[
            VariantSection(
                heading="Skills",
                entries=[Evidence(
                    quote="Python, Go, Kubernetes, Terraform",
                    line=10,
                    source_file="resumes/master.md",
                )],
            )
        ],
    )


def test_variant_paths_are_namespaced_per_job():
    assert ResumeVariant.variant_dir("abc1") == "resumes/versions/abc1/"
    assert ResumeVariant.pdf_path("abc1") == "resumes/versions/abc1/resume.pdf"
    assert ResumeVariant.json_path("abc1") == "resumes/versions/abc1/variant.json"


def test_variant_round_trips_through_json():
    loaded = ResumeVariant.model_validate_json(_variant().model_dump_json())
    assert loaded.job_id == "acme-sre-abc1"
    assert loaded.sections[0].heading == "Skills"
    assert loaded.sections[0].entries[0].line == 10
    assert loaded.entry_count() == 1


def test_entry_count_sums_across_sections():
    v = _variant()
    v.sections.append(VariantSection(
        heading="Experience",
        entries=[
            Evidence(quote="a", line=1, source_file="resumes/master.md"),
            Evidence(quote="b", line=2, source_file="resumes/master.md"),
        ],
    ))
    assert v.entry_count() == 3


def test_normalize_quote_matches_the_form_verify_quote_uses():
    # Phase 11a's I3 finding was a dedup key computed differently from the
    # one the verifier matches on, reporting one item as both kept and
    # dropped. Anything deduping quotes must use exactly this function.
    assert normalize_quote("  Python,   GO  ") == normalize_quote("python, go")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_variant.py -v`
Expected: FAIL at import — `cannot import name 'ResumeVariant' from 'careeros.core.models'`

- [ ] **Step 3: Add the models**

In `careeros/core/models.py`, immediately after the `class Skills` block and before `class Preferences`:

```python
class VariantSection(BaseModel):
    heading: str
    entries: list[Evidence] = []


class ResumeVariant(BaseModel):
    """A job-tailored resume assembled only from verbatim spans of the master.

    Every entry is an Evidence record whose quote verify_quote located in
    source_file, so each line of the rendered document traces to a line
    number in the master resume. `dropped` names what the model proposed
    that did not survive the guards.
    """

    job_id: str
    job_company: str = ""
    job_title: str = ""
    generated_at: str
    source_file: str
    sections: list[VariantSection] = []
    dropped: list[str] = []

    def entry_count(self) -> int:
        return sum(len(s.entries) for s in self.sections)

    # Path helpers live here so the command, the selector, and apply cannot
    # drift apart on where a variant is stored. Per-job subdirectories keep
    # tailored output out of the untailored fallback pool.
    @staticmethod
    def variant_dir(job_id: str) -> str:
        return "resumes/versions/" + job_id + "/"

    @staticmethod
    def pdf_path(job_id: str) -> str:
        return ResumeVariant.variant_dir(job_id) + "resume.pdf"

    @staticmethod
    def json_path(job_id: str) -> str:
        return ResumeVariant.variant_dir(job_id) + "variant.json"
```

- [ ] **Step 4: Expose `normalize_quote`**

In `careeros/skills/resume_evidence.py`, directly below the existing `_normalize` function:

```python
def normalize_quote(text: str) -> str:
    """Public name for the normalization verify_quote matches on.

    Any dedup of quotes outside this module must use exactly this form.
    Phase 11a's I3 finding was a dedup key built from raw text while the
    matcher used the normalized form, so one skill was reported as both
    stored and dropped.
    """
    return _normalize(text)
```

- [ ] **Step 5: Run the model tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_variant.py -v`
Expected: all four tests PASS. They can only pass because Step 1 deliberately
left `careeros.skills.resume_variant` unimported; if you added that import
early, the file fails at collection instead.

- [ ] **Step 6: Write the failing tests for the selector**

Append to `tests/test_resume_variant.py`. Add these imports at the top of the
file, replacing the NOTE comment from Step 1:

```python
import pytest

from careeros.skills.resume_variant import (
    ALLOWED_HEADINGS,
    VariantResult,
    select_variant_content,
)
```

Then append:

```python
def _resp(payload):
    msg = MagicMock()
    msg.content = json.dumps(payload) if not isinstance(payload, str) else payload
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _call(payload, master=_MASTER, skills=None):
    with patch("careeros.skills.resume_variant.litellm.completion", return_value=_resp(payload)):
        return select_variant_content(
            "We need an SRE with Kubernetes experience.",
            master,
            skills if skills is not None else Skills(),
            "resumes/master.md",
        )


def test_verbatim_span_becomes_an_evidence_entry():
    result = _call({"sections": [
        {"heading": "Skills", "quotes": ["Python, Go, Kubernetes, Terraform"]}
    ]})
    assert result.error is None
    assert len(result.sections) == 1
    assert result.sections[0].heading == "Skills"
    entry = result.sections[0].entries[0]
    assert entry.quote == "Python, Go, Kubernetes, Terraform"
    assert entry.line == 10
    assert entry.source_file == "resumes/master.md"
    assert result.dropped == ()


def test_fabricated_span_is_dropped_and_named():
    result = _call({"sections": [
        {"heading": "Skills", "quotes": ["Rust, Haskell, and Erlang"]}
    ]})
    assert result.sections == ()
    assert result.dropped == ("Rust, Haskell, and Erlang",)


def test_heading_outside_the_allowlist_drops_the_section():
    # A heading is rendered text on the finished document, so a free-text
    # heading is a fabrication surface like any other claim.
    result = _call({"sections": [
        {"heading": "Perfect Match For This Role",
         "quotes": ["Python, Go, Kubernetes, Terraform"]}
    ]})
    assert result.sections == ()
    assert result.dropped == ("section heading: Perfect Match For This Role",)


def test_every_allowed_heading_is_accepted():
    for heading in ALLOWED_HEADINGS:
        result = _call({"sections": [
            {"heading": heading, "quotes": ["Python, Go, Kubernetes, Terraform"]}
        ]})
        assert result.sections[0].heading == heading, heading


def test_duplicate_quote_is_collapsed_not_double_reported():
    result = _call({"sections": [
        {"heading": "Skills", "quotes": ["Python, Go, Kubernetes, Terraform"]},
        {"heading": "Experience", "quotes": ["python, go, kubernetes, terraform"]},
    ]})
    assert result.entry_count() == 1
    # The collapsed duplicate is not a failure, so it must not be reported
    # as dropped — that was Phase 11a's I3 defect.
    assert result.dropped == ()


def test_section_with_no_surviving_entry_is_omitted():
    result = _call({"sections": [
        {"heading": "Skills", "quotes": ["Python, Go, Kubernetes, Terraform"]},
        {"heading": "Projects", "quotes": ["invented project"]},
    ]})
    assert [s.heading for s in result.sections] == ["Skills"]
    assert result.dropped == ("invented project",)


def test_overlong_span_is_rejected_by_the_inherited_quote_cap():
    long_master = "x" * 400
    result = _call({"sections": [{"heading": "Summary", "quotes": ["x" * 300]}]},
                   master=long_master)
    assert result.sections == ()
    assert len(result.dropped) == 1


def test_verification_runs_against_the_capped_text_the_model_saw():
    from careeros.skills.resume_variant import _MASTER_CAP
    master = ("A" * _MASTER_CAP) + "\nBeyond the cap span"
    result = _call({"sections": [{"heading": "Summary", "quotes": ["Beyond the cap span"]}]},
                   master=master)
    assert result.sections == ()


def test_skills_are_a_hint_and_never_contribute_text():
    skills = Skills(skills=[Skill(name="Kubernetes", source="resumes/master.md")])
    with patch("careeros.skills.resume_variant.litellm.completion",
               return_value=_resp({"sections": []})) as comp:
        select_variant_content("jd", _MASTER, skills, "resumes/master.md")
    user_msg = comp.call_args.kwargs["messages"][1]["content"]
    assert "Kubernetes" in user_msg


def test_jd_and_resume_are_both_wrapped_as_untrusted():
    with patch("careeros.skills.resume_variant.litellm.completion",
               return_value=_resp({"sections": []})) as comp:
        select_variant_content("JD BODY", _MASTER, Skills(), "resumes/master.md")
    messages = comp.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"].count("<untrusted_content>") == 2
    assert "JD BODY" in messages[1]["content"]


def test_llm_failure_is_reported_distinctly_from_nothing_verified():
    with patch("careeros.skills.resume_variant.litellm.completion",
               side_effect=RuntimeError("invalid api key")):
        result = select_variant_content("jd", _MASTER, Skills(), "resumes/master.md")
    assert result.sections == ()
    assert result.error == "invalid api key"


def test_nothing_verified_is_not_an_error():
    result = _call({"sections": [{"heading": "Skills", "quotes": ["nope"]}]})
    assert result.error is None


@pytest.mark.parametrize("payload", [
    None,
    [],
    "not json at all",
    "",
    "```",
    {},
    {"sections": None},
    {"sections": "a string"},
    {"sections": 7},
    {"sections": [None]},
    {"sections": ["a string"]},
    {"sections": [[]]},
    {"sections": [{"heading": None, "quotes": ["Python, Go, Kubernetes, Terraform"]}]},
    {"sections": [{"heading": 7, "quotes": ["Python, Go, Kubernetes, Terraform"]}]},
    {"sections": [{"heading": "Skills"}]},
    {"sections": [{"heading": "Skills", "quotes": None}]},
    {"sections": [{"heading": "Skills", "quotes": "a string"}]},
    {"sections": [{"heading": "Skills", "quotes": [None]}]},
    {"sections": [{"heading": "Skills", "quotes": [7]}]},
    {"sections": [{"heading": "Skills", "quotes": [{}]}]},
    {"sections": [{"heading": "Skills", "quotes": [["nested"]]}]},
    {"sections": [{"heading": "Skills", "quotes": [""]}]},
    {"sections": [{"heading": "", "quotes": ["Python, Go, Kubernetes, Terraform"]}]},
])
def test_malformed_payload_returns_a_result_instead_of_raising(payload):
    # Phase 11a shipped a Critical because {"skills": null} escaped its
    # per-candidate guard and crashed a caller that trusted the never-raises
    # contract. These are written here in the same task, not deferred.
    result = _call(payload)
    assert isinstance(result, VariantResult)
```

- [ ] **Step 7: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_variant.py -v`
Expected: FAIL — `No module named 'careeros.skills.resume_variant'`

- [ ] **Step 8: Write the selector**

Create `careeros/skills/resume_variant.py`:

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import litellm

from careeros.core.models import Evidence, Skills, VariantSection
from careeros.skills.resume_evidence import normalize_quote, verify_quote
from careeros.skills.sanitize import wrap_untrusted

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

# 11a caps ingest content at 4000, roughly one page. A two-page resume would
# have its tail invisible to the selector, so this is larger. Verification
# runs against exactly this capped text, never the full file.
_MASTER_CAP = 8000

_SKILL_HINT_CAP = 500

# Headings are rendered text on the finished document, so a free-text heading
# is a fabrication surface like any other claim: nothing otherwise stops a
# model titling a section "Perfect Match For This Role".
ALLOWED_HEADINGS = ("Summary", "Experience", "Skills", "Education", "Projects")

_DROPPED_LABEL_CAP = 80

_VARIANT_INSTRUCTIONS = """\
You are tailoring a resume to one job description.
Return ONLY valid JSON — no markdown, no explanation.

You may ONLY select text that already appears in the resume. Every "quote"
must be a span copied from the resume character for character, 200
characters or fewer. Do not paraphrase, reword, summarise, translate, or
stitch together separate spans. Do not write any new text of your own. If
you cannot find a verbatim span, omit it.

Select the spans most relevant to the job description and order them most
relevant first. Prefer spans evidencing a skill the job asks for. Aim for
at most 12 spans in total.

"heading" must be exactly one of: Summary, Experience, Skills, Education, Projects

{"sections": [{"heading": "<heading>", "quotes": ["<verbatim span>"]}]}
"""


@dataclass(frozen=True)
class VariantResult:
    sections: tuple[VariantSection, ...]
    dropped: tuple[str, ...]
    # Set only when the LLM call itself failed (bad API key, network error,
    # provider outage) — distinct from "the model answered but nothing it
    # proposed verified," which is a normal outcome and not an error.
    error: str | None = None

    def entry_count(self) -> int:
        return sum(len(s.entries) for s in self.sections)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _note(dropped: list[str], label: str) -> None:
    label = label[:_DROPPED_LABEL_CAP]
    if label and label not in dropped:
        dropped.append(label)


def select_variant_content(
    jd_text: str,
    master_text: str,
    skills: Skills,
    source_file: str,
    model: str | None = None,
) -> VariantResult:
    """Select verbatim spans of the master resume relevant to one job.

    Total, never raises: any LLM failure, parse failure, or malformed section
    or quote yields a VariantResult rather than propagating an exception.
    The `resume variant` command depends on this holding for every possible
    model response, not just well-formed ones.

    `skills` is a hint only. It supplies the names of skills already carrying
    verified Evidence so the selector can favour spans the job asks about; it
    can never contribute text, because every quote must still be a verbatim
    span of the capped master resume.
    """
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    capped = master_text[:_MASTER_CAP]

    hint = ", ".join(
        s.name for s in skills.skills if isinstance(s.name, str) and s.name
    )[:_SKILL_HINT_CAP]

    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _VARIANT_INSTRUCTIONS},
                {"role": "user", "content": (
                    "Verified skills (hint for ranking only — never copy from this list): "
                    + hint
                    + "\n\nJob description:\n" + wrap_untrusted(jd_text)
                    + "\n\nResume:\n" + wrap_untrusted(capped)
                )},
            ],
        )
    except Exception as exc:
        return VariantResult(sections=(), dropped=(), error=str(exc) or type(exc).__name__)

    try:
        payload = _parse_json(resp.choices[0].message.content)
    except Exception:
        return VariantResult(sections=(), dropped=())

    raw_sections = payload.get("sections") if isinstance(payload, dict) else None
    sections_in = raw_sections if isinstance(raw_sections, list) else []

    sections: list[VariantSection] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for raw_section in sections_in:
        if not isinstance(raw_section, dict):
            continue

        raw_heading = raw_section.get("heading")
        heading = raw_heading.strip() if isinstance(raw_heading, str) else ""
        if heading not in ALLOWED_HEADINGS:
            _note(dropped, "section heading: " + (heading or "<missing>"))
            continue

        raw_quotes = raw_section.get("quotes")
        quotes_in = raw_quotes if isinstance(raw_quotes, list) else []

        entries: list[Evidence] = []
        for quote in quotes_in:
            try:
                if not isinstance(quote, str):
                    raise ValueError("quote is not a string")
                key = normalize_quote(quote)
                if not key:
                    raise ValueError("quote is empty")
                if key in seen:
                    # A collapsed duplicate is not a failure and must not be
                    # reported as dropped.
                    continue
                line = verify_quote(quote, capped)
                if line is None:
                    raise ValueError("quote did not verify")
                entries.append(Evidence(quote=quote, line=line, source_file=source_file))
                seen.add(key)
            except Exception:
                # A malformed or unverifiable span is a dropped span, not a
                # dead section.
                _note(dropped, quote.strip() if isinstance(quote, str) else repr(quote))
                continue

        if entries:
            sections.append(VariantSection(heading=heading, entries=entries))

    return VariantResult(sections=tuple(sections), dropped=tuple(dropped))
```

- [ ] **Step 9: Run the full file to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_resume_variant.py -v`
Expected: PASS, all tests

- [ ] **Step 10: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 569 prior tests still pass, plus the new ones. No failures.

- [ ] **Step 11: Commit**

```bash
git add careeros/core/models.py careeros/skills/resume_evidence.py careeros/skills/resume_variant.py tests/test_resume_variant.py
git commit -m "feat: add ResumeVariant model and verbatim-only variant selector

Three deterministic guards reject anything the model did not copy from the
master resume: a heading allowlist, verify_quote unchanged, and duplicate
collapse on the same normalized form the verifier matches on.

The never-raises contract ships with its adversarial tests rather than
deferring them; Phase 11a's Critical lived in exactly that deferred gap."
```

---

## Task 2: HTML renderer

**Files:**
- Create: `careeros/render/__init__.py`
- Create: `careeros/render/resume_html.py`
- Test: `tests/test_resume_html.py`

**Interfaces:**
- Consumes: `ResumeVariant`, `VariantSection`, `Evidence`, `Profile` from `careeros/core/models.py`.
- Produces: `build_resume_html(variant: ResumeVariant, profile: Profile) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_html.py`:

```python
from careeros.core.models import Evidence, Profile, ResumeVariant, VariantSection
from careeros.render.resume_html import build_resume_html


def _ev(quote, line=1):
    return Evidence(quote=quote, line=line, source_file="resumes/master.md")


def _variant(sections=None):
    return ResumeVariant(
        job_id="acme-sre-abc1",
        job_company="Acme",
        job_title="Senior SRE",
        generated_at="2026-09-22T00:00:00+00:00",
        source_file="resumes/master.md",
        sections=sections if sections is not None else [
            VariantSection(heading="Skills", entries=[_ev("Python, Go, Kubernetes", 10)]),
        ],
    )


def _profile():
    return Profile(
        name="Alice Johnson",
        email="alice@example.com",
        title="Senior Site Reliability Engineer",
        location="Berlin",
    )


def test_contact_header_comes_from_the_profile():
    html = build_resume_html(_variant(), _profile())
    assert "Alice Johnson" in html
    assert "alice@example.com" in html
    assert "Senior Site Reliability Engineer" in html
    assert "Berlin" in html


def test_headings_and_entries_render_in_order():
    variant = _variant([
        VariantSection(heading="Experience", entries=[_ev("Built monitoring", 6)]),
        VariantSection(heading="Skills", entries=[_ev("Python, Go", 10)]),
    ])
    html = build_resume_html(variant, _profile())
    assert html.index("Experience") < html.index("Skills")
    assert html.index("Built monitoring") < html.index("Python, Go")


def test_markup_in_a_quote_is_escaped_not_rendered():
    # Verbatim resume text becomes markup here. A resume saying
    # "C++ & <legacy> tooling" must not produce broken output, and must not
    # be able to inject markup.
    variant = _variant([VariantSection(
        heading="Experience",
        entries=[_ev("Wrote C++ & <legacy> tooling")],
    )])
    html = build_resume_html(variant, _profile())
    assert "<legacy>" not in html
    assert "&lt;legacy&gt;" in html
    assert "C++ &amp; " in html


def test_markup_in_a_profile_field_is_escaped():
    profile = Profile(name="Alice <script>alert(1)</script>", email="a@b.c")
    html = build_resume_html(_variant(), profile)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_no_external_asset_references():
    # Rendering must never touch the network. Inline CSS only.
    html = build_resume_html(_variant(), _profile())
    for marker in ("http://", "https://", "<link", "@import", "<script", "src="):
        assert marker not in html, marker


def test_section_with_no_entries_is_not_rendered():
    variant = _variant([
        VariantSection(heading="Skills", entries=[_ev("Python, Go", 10)]),
        VariantSection(heading="Projects", entries=[]),
    ])
    html = build_resume_html(variant, _profile())
    assert "Projects" not in html
    assert "Skills" in html


def test_line_numbers_and_dropped_list_are_not_in_the_document():
    # The sidecar carries provenance; the document an employer sees is clean.
    variant = _variant()
    variant.dropped = ["some rejected span"]
    html = build_resume_html(variant, _profile())
    assert "some rejected span" not in html
    assert "resumes/master.md" not in html


def test_empty_profile_still_renders_a_document():
    html = build_resume_html(_variant(), Profile())
    assert html.startswith("<!DOCTYPE html>")
    assert "Python, Go, Kubernetes" in html
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_html.py -v`
Expected: FAIL — `No module named 'careeros.render'`

- [ ] **Step 3: Create the package marker**

Create `careeros/render/__init__.py` as an empty file.

- [ ] **Step 4: Write the renderer**

Create `careeros/render/resume_html.py`:

```python
from __future__ import annotations

import html

from careeros.core.models import Profile, ResumeVariant

# Inline only. An external stylesheet or font would make rendering depend on
# the network, and render_pdf deliberately never loads a URL.
_CSS = """
@page { size: Letter; margin: 0; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt;
       line-height: 1.4; color: #111; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 2pt 0; letter-spacing: 0.3pt; }
p.contact { font-size: 9.5pt; color: #444; margin: 0 0 14pt 0; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 0.6pt;
     border-bottom: 1px solid #999; padding-bottom: 2pt;
     margin: 14pt 0 6pt 0; }
ul { margin: 0; padding-left: 16pt; }
li { margin-bottom: 4pt; }
"""


def _esc(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def build_resume_html(variant: ResumeVariant, profile: Profile) -> str:
    """Render a variant to a self-contained HTML document.

    Every quote and profile field is escaped: verbatim resume text becomes
    markup here, so text like "C++ & <legacy> tooling" must render as
    written rather than as broken or injected markup.

    The document carries no provenance annotations. Line numbers and the
    dropped list live in variant.json; this is what an employer sees.
    """
    contact = [b for b in (profile.title, profile.location, profile.email) if b]

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>" + _esc(profile.name or "Resume") + "</title>",
        "<style>" + _CSS + "</style>",
        "</head><body>",
        "<h1>" + _esc(profile.name) + "</h1>",
        '<p class="contact">' + " &middot; ".join(_esc(b) for b in contact) + "</p>",
    ]

    for section in variant.sections:
        if not section.entries:
            continue
        parts.append("<h2>" + _esc(section.heading) + "</h2>")
        parts.append("<ul>")
        for entry in section.entries:
            parts.append("<li>" + _esc(entry.quote) + "</li>")
        parts.append("</ul>")

    parts.append("</body></html>")
    return "\n".join(parts)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_html.py -v`
Expected: PASS

Note: `test_no_external_asset_references` asserts `src=` is absent, so do not add any `<img>`. It also asserts `<script` is absent — the CSS block uses `<style>`, which is fine.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures

- [ ] **Step 7: Commit**

```bash
git add careeros/render/__init__.py careeros/render/resume_html.py tests/test_resume_html.py
git commit -m "feat: render a resume variant to self-contained escaped HTML

Every quote and profile field is escaped, because verbatim resume text
becomes markup here. Inline CSS with no external asset references, so
rendering never touches the network."
```

---

## Task 3: PDF renderer

**Files:**
- Create: `careeros/render/resume_pdf.py`
- Test: `tests/test_resume_pdf.py`

**Interfaces:**
- Consumes: nothing from earlier tasks; takes an HTML string.
- Produces: `render_pdf(html_text: str) -> bytes` and `class RendererUnavailable(RuntimeError)`

`render_pdf` returns **bytes**, not a path. All workspace I/O goes through `StorageProvider`, so the caller writes those bytes with `storage.atomic_write`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_pdf.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import Error as PlaywrightError

from careeros.render.resume_pdf import RendererUnavailable, render_pdf

_HTML = "<!DOCTYPE html><html><body><h1>Alice Johnson</h1></body></html>"


def _playwright_stub(pdf_bytes=b"%PDF-1.4 fake"):
    page = MagicMock()
    page.pdf.return_value = pdf_bytes
    browser = MagicMock()
    browser.new_page.return_value = page
    chromium = MagicMock()
    chromium.launch.return_value = browser
    p = MagicMock()
    p.chromium = chromium
    ctx = MagicMock()
    ctx.__enter__.return_value = p
    ctx.__exit__.return_value = False
    return ctx, p, browser, page


def test_returns_the_pdf_bytes_playwright_produced():
    ctx, _p, _browser, _page = _playwright_stub(b"%PDF-1.4 real")
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        assert render_pdf(_HTML) == b"%PDF-1.4 real"


def test_uses_an_ephemeral_headless_chromium_not_the_persistent_profile():
    # launch_persistent_context uses the browsing profile Phase 9c isolated.
    # Rendering through it would contend with a live session.
    ctx, p, _browser, _page = _playwright_stub()
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        render_pdf(_HTML)
    p.chromium.launch.assert_called_once_with(headless=True)
    assert not p.chromium.launch_persistent_context.called


def test_sets_content_directly_and_never_loads_a_url():
    ctx, _p, _browser, page = _playwright_stub()
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        render_pdf(_HTML)
    page.set_content.assert_called_once_with(_HTML)
    assert not page.goto.called


def test_pdf_is_letter_sized_and_written_to_no_path():
    ctx, _p, _browser, page = _playwright_stub()
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        render_pdf(_HTML)
    kwargs = page.pdf.call_args.kwargs
    assert kwargs["format"] == "Letter"
    assert "path" not in kwargs


def test_browser_is_closed_even_when_pdf_generation_fails():
    ctx, _p, browser, page = _playwright_stub()
    page.pdf.side_effect = RuntimeError("boom")
    with patch("careeros.render.resume_pdf.sync_playwright", return_value=ctx):
        with pytest.raises(RuntimeError, match="boom"):
            render_pdf(_HTML)
    browser.close.assert_called_once()


def test_missing_chromium_raises_renderer_unavailable_with_the_install_hint():
    # Playwright browsers are not installed by `pip install`, so this is the
    # expected first-run path, not an edge case. It must not surface as a raw
    # Playwright traceback.
    err = PlaywrightError(
        "BrowserType.launch: Executable doesn't exist at /x/chrome-headless-shell"
    )
    with patch("careeros.render.resume_pdf.sync_playwright", side_effect=err):
        with pytest.raises(RendererUnavailable) as exc:
            render_pdf(_HTML)
    assert "playwright install chromium" in str(exc.value)


def test_other_playwright_errors_are_not_disguised_as_missing_chromium():
    err = PlaywrightError("Target page, context or browser has been closed")
    with patch("careeros.render.resume_pdf.sync_playwright", side_effect=err):
        with pytest.raises(PlaywrightError):
            render_pdf(_HTML)


@pytest.mark.integration
def test_real_chromium_renders_a_pdf():
    # Requires `playwright install chromium`. Skipped by default like every
    # other browser-dependent test in this suite.
    data = render_pdf(_HTML)
    assert data.startswith(b"%PDF")
    assert len(data) > 500
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_pdf.py -v -m "not integration"`
Expected: FAIL — `No module named 'careeros.render.resume_pdf'`

- [ ] **Step 3: Write the renderer**

Create `careeros/render/resume_pdf.py`:

```python
from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

_MARGIN = {"top": "0.6in", "bottom": "0.6in", "left": "0.7in", "right": "0.7in"}

_INSTALL_HINT = (
    "Chromium is not installed for Playwright. Run: playwright install chromium"
)

# Substrings Playwright uses when the browser binary is absent.
_MISSING_MARKERS = ("Executable doesn't exist", "playwright install")


class RendererUnavailable(RuntimeError):
    """Chromium is not available, so no PDF can be produced.

    Playwright browsers are not installed by `pip install`, so this is an
    expected first-run condition rather than an edge case. Callers turn it
    into an actionable message instead of a traceback.
    """


def render_pdf(html_text: str) -> bytes:
    """Render an HTML document to PDF bytes with an ephemeral Chromium.

    Returns bytes rather than writing a file: all workspace I/O goes through
    the StorageProvider protocol, so the caller persists these with
    storage.atomic_write.

    Deliberately does NOT use launch_persistent_context. That profile is the
    one Phase 9c isolated for browsing sessions, and rendering through it
    would contend with a live session and raise BrowserProfileBusy. Nothing
    here loads a URL — set_content only — so rendering never touches the
    network.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html_text)
                return page.pdf(format="Letter", margin=_MARGIN, print_background=True)
            finally:
                browser.close()
    except PlaywrightError as exc:
        if any(marker in str(exc) for marker in _MISSING_MARKERS):
            raise RendererUnavailable(_INSTALL_HINT) from exc
        raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_pdf.py -v -m "not integration"`
Expected: PASS. The `integration`-marked test is deselected.

- [ ] **Step 5: Confirm the integration test is skipped by default**

Run: `.venv/bin/python -m pytest tests/test_resume_pdf.py -q`
Expected: the non-integration tests pass; `test_real_chromium_renders_a_pdf` is skipped or deselected per this repo's existing marker configuration. If it instead *runs and fails* because Chromium is absent, that is a plan defect — report it rather than installing a browser.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures

- [ ] **Step 7: Commit**

```bash
git add careeros/render/resume_pdf.py tests/test_resume_pdf.py
git commit -m "feat: render HTML to PDF bytes via an ephemeral Chromium

Returns bytes so the caller persists through StorageProvider. Never uses
launch_persistent_context, which would contend with the browsing profile
Phase 9c isolated, and never loads a URL.

Missing Chromium raises a typed RendererUnavailable carrying the install
command, because pip install does not install Playwright browsers."
```

---

## Task 4: `careeros resume variant --job <id>`

**Files:**
- Modify: `careeros/cli/resume_cmd.py`
- Test: `tests/test_resume_cmd.py` (append; do not alter existing tests)

**Interfaces:**
- Consumes: `select_variant_content(jd_text, master_text, skills, source_file, model=None) -> VariantResult` (Task 1); `ResumeVariant` and its path helpers (Task 1); `build_resume_html(variant, profile) -> str` (Task 2); `render_pdf(html_text) -> bytes` and `RendererUnavailable` (Task 3).
- Produces: the `resumes/versions/<job_id>/resume.pdf` and `variant.json` files Task 5's `select_resume` looks up.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_cmd.py`. The existing `_workspace` helper in that file creates a workspace and writes `resumes/master.md`; reuse it.

```python
from datetime import datetime, timezone

from careeros.core.models import Evidence as _Ev
from careeros.core.models import Job, Profile, ResumeVariant, VariantSection
from careeros.render.resume_pdf import RendererUnavailable
from careeros.skills.resume_variant import VariantResult


def _job(storage, job_id="acme-sre-abc1"):
    now = datetime.now(timezone.utc).isoformat()
    Job(
        id=job_id, source="browse", url="https://example.com/j",
        company="Acme", title="Senior SRE",
        description="We need Kubernetes and Go.",
        stage="saved", created_at=now, updated_at=now,
    ).save(storage)
    return job_id


def _variant_result(quotes=("Python, Go",), dropped=()):
    return VariantResult(
        sections=(VariantSection(
            heading="Skills",
            entries=[_Ev(quote=q, line=2, source_file="resumes/master.md") for q in quotes],
        ),),
        dropped=tuple(dropped),
    )


def _patches(result, pdf=b"%PDF-1.4 x"):
    return (
        patch("careeros.cli.resume_cmd.select_variant_content", return_value=result),
        patch("careeros.cli.resume_cmd.render_pdf", return_value=pdf),
    )


def test_variant_writes_the_pdf_and_the_sidecar(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    assert storage.exists(ResumeVariant.pdf_path(job_id))
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-1.4 x"
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.job_id == job_id
    assert sidecar.job_company == "Acme"
    assert sidecar.job_title == "Senior SRE"
    assert sidecar.source_file == "resumes/master.md"
    assert sidecar.sections[0].entries[0].quote == "Python, Go"
    assert sidecar.sections[0].entries[0].line == 2


def test_variant_passes_the_job_description_to_the_selector(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    with patch("careeros.cli.resume_cmd.select_variant_content",
               return_value=_variant_result()) as sel, \
         patch("careeros.cli.resume_cmd.render_pdf", return_value=b"%PDF"):
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert "Kubernetes" in sel.call_args.args[0]
    assert sel.call_args.args[1] == "SKILLS\nPython, Go\n"
    assert sel.call_args.args[3] == "resumes/master.md"


def test_variant_names_every_dropped_span(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(dropped=("invented span",)))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    assert "invented span" in result.output
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.dropped == ["invented span"]


def test_variant_refuses_to_write_when_nothing_verified(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(VariantResult(sections=(), dropped=("a", "b")))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert not storage.exists(ResumeVariant.pdf_path(job_id))
    assert not storage.exists(ResumeVariant.json_path(job_id))


def test_variant_reports_an_llm_failure_distinctly(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(VariantResult(sections=(), dropped=(), error="invalid api key"))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert "invalid api key" in result.output
    assert not storage.exists(ResumeVariant.pdf_path(job_id))


def test_variant_reports_missing_chromium_actionably(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    with patch("careeros.cli.resume_cmd.select_variant_content",
               return_value=_variant_result()), \
         patch("careeros.cli.resume_cmd.render_pdf",
               side_effect=RendererUnavailable("Chromium is not installed for "
                                               "Playwright. Run: playwright install chromium")):
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert "playwright install chromium" in result.output
    assert not storage.exists(ResumeVariant.pdf_path(job_id))


def test_variant_regeneration_overwrites_only_on_success(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-first")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-first"

    sel, rnd = _patches(VariantResult(sections=(), dropped=(), error="network down"))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-first"

    sel, rnd = _patches(_variant_result(quotes=("Python, Go",)), pdf=b"%PDF-second")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-second"


def test_variant_fails_clearly_for_an_unknown_job(tmp_path):
    ws, _storage = _workspace(tmp_path)
    result = runner.invoke(app, ["resume", "variant", "--job", "nope-xyz", "--workspace", ws])
    assert result.exit_code == 1
    assert "nope-xyz" in result.output


def test_variant_fails_clearly_when_the_master_is_missing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    job_id = _job(storage)
    result = runner.invoke(app, ["resume", "variant", "--job", job_id,
                                 "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "resume ingest" in result.output


def test_variant_records_an_activity_event_on_success_and_failure(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    sel, rnd = _patches(VariantResult(sections=(), dropped=()))
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])

    events = [
        json.loads(line)
        for p in storage.list("activity/")
        for line in storage.read(p).decode().splitlines() if line.strip()
    ]
    variant_events = [e for e in events if e["action"] == "variant"]
    assert [e["status"] for e in variant_events] == ["success", "failed"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_cmd.py -v`
Expected: the new tests FAIL (no such command `variant`); every pre-existing test in the file still PASSES.

- [ ] **Step 3: Add the imports and helper**

In `careeros/cli/resume_cmd.py`, extend the imports:

```python
from careeros.core.models import Job, Profile, ResumeVariant, Skills
from careeros.render.resume_html import build_resume_html
from careeros.render.resume_pdf import RendererUnavailable, render_pdf
from careeros.skills.resume_variant import select_variant_content
```

and add, below the existing `_record_failed`:

```python
_JD_CAP = 4000


def _record_variant(runtime, job_id: str, summary: str, status: str) -> None:
    runtime.record_activity(runtime.new_event(
        "resume_variant", "variant", summary,
        status=status, entity_type="job", entity_id=job_id,
    ))
```

- [ ] **Step 4: Add the command**

Append to `careeros/cli/resume_cmd.py`:

```python
@resume_app.command()
def variant(
    job: str = typer.Option(..., "--job", help="Job id to tailor the resume for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    """Build a job-tailored resume from verbatim spans of your master resume."""
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_record = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    if not runtime.storage.exists(_MASTER):
        _record_variant(runtime, job, "No resume at " + _MASTER, "failed")
        rprint("[red]No resume at " + _MASTER
               + ". Run 'careeros resume ingest <path>' first.[/red]")
        raise typer.Exit(1)

    master_text = runtime.storage.read(_MASTER).decode()
    skills = Skills.load_or_empty(runtime.storage)
    profile = Profile.load_or_empty(runtime.storage)
    jd_text = (job_record.description or "")[:_JD_CAP]

    rprint("Tailoring resume for " + job_record.company + " / " + job_record.title + "...")
    result = select_variant_content(jd_text, master_text, skills, _MASTER, model=model)

    if result.dropped:
        rprint("[yellow]Dropped " + str(len(result.dropped))
               + " span(s) the model could not copy verbatim: "
               + "; ".join(result.dropped) + "[/yellow]")

    if result.entry_count() == 0:
        # An empty resume is worse than an untailored one, so refuse to write
        # anything and leave any existing variant exactly as it was.
        if result.error:
            rprint("[red]Tailoring failed: " + result.error
                   + ". No variant was written.[/red]")
            _record_variant(runtime, job, "Tailoring failed: " + result.error, "failed")
        else:
            rprint("[red]No span could be verified against " + _MASTER
                   + ". No variant was written.[/red]")
            _record_variant(runtime, job, "Nothing verified against " + _MASTER, "failed")
        raise typer.Exit(1)

    variant_doc = ResumeVariant(
        job_id=job,
        job_company=job_record.company,
        job_title=job_record.title,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_file=_MASTER,
        sections=list(result.sections),
        dropped=list(result.dropped),
    )

    try:
        pdf_bytes = render_pdf(build_resume_html(variant_doc, profile))
    except RendererUnavailable as exc:
        rprint("[red]" + str(exc) + "[/red]")
        _record_variant(runtime, job, "Renderer unavailable", "failed")
        raise typer.Exit(1)

    # PDF first, then the sidecar: selection keys off the PDF, so a torn
    # write degrades to "variant works, audit missing" rather than a sidecar
    # promising a file that is not there.
    runtime.storage.atomic_write(ResumeVariant.pdf_path(job), pdf_bytes)
    runtime.storage.atomic_write(
        ResumeVariant.json_path(job), variant_doc.model_dump_json(indent=2).encode()
    )

    _record_variant(
        runtime, job,
        "Tailored resume for " + job_record.company + " / " + job_record.title + ": "
        + str(variant_doc.entry_count()) + " evidence-backed entries, "
        + str(len(result.dropped)) + " dropped",
        "success",
    )

    for section in variant_doc.sections:
        rprint("  " + section.heading + ": " + str(len(section.entries)) + " entry(ies)")
    rprint("[green]Wrote " + ResumeVariant.pdf_path(job) + " ("
           + str(variant_doc.entry_count()) + " evidence-backed entries).[/green]")
    rprint("Evidence: " + ResumeVariant.json_path(job))
```

Add at the top of the file, with the other imports:

```python
from datetime import datetime, timezone
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_cmd.py -v`
Expected: PASS, including every pre-existing test

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/resume_cmd.py tests/test_resume_cmd.py
git commit -m "feat: add careeros resume variant --job <id>

Writes resumes/versions/<job_id>/resume.pdf and variant.json. Refuses to
write anything when nothing verified — an empty resume is worse than an
untailored one — and leaves an existing variant untouched on every failure
path. An LLM error reads differently from 'nothing verified', which was a
Phase 11a review finding."
```

---

## Task 5: Job-keyed resume selection in `apply` and `discover-and-apply`

**Files:**
- Create: `careeros/core/resume_select.py`
- Modify: `careeros/cli/apply_cmd.py` (remove `_RESUME_EXTENSIONS` at line 30; replace the block at lines 70-80)
- Modify: `careeros/cli/discover_and_apply_cmd.py` (remove `_RESUME_EXTENSIONS` at line 28; replace lines 206-210 and 219)
- Test: `tests/test_resume_select.py` (create), `tests/test_apply_cmd.py` (modify), `tests/test_discover_and_apply_cmd.py` (modify)

**Interfaces:**
- Consumes: `ResumeVariant` and its path helpers (Task 1).
- Produces: `select_resume(storage: StorageProvider, job_id: str) -> ResumeChoice | None`, `ResumeChoice(path: str, storage_path: str, tailored: bool, variant: ResumeVariant | None)`, `RESUME_EXTENSIONS: tuple[str, ...]`

**Read before starting — a hazard in the existing tests.** `tests/test_apply_cmd.py`'s `_mock_runtime` sets `storage.exists.return_value = True` unconditionally. Once selection consults `storage.exists`, every existing apply test silently takes the tailored branch and claims a tailored resume that does not exist. You must make that mock path-aware. Do **not** weaken any existing assertion to accommodate this; if an existing test genuinely encodes the old alphabetical behaviour, say so in your report rather than quietly changing it.

- [ ] **Step 1: Write the failing tests for the selector**

Create `tests/test_resume_select.py`:

```python
from careeros.core.models import Evidence, ResumeVariant, VariantSection
from careeros.core.resume_select import select_resume
from careeros.storage.filesystem import LocalFilesystemStorage


def _storage(tmp_path):
    return LocalFilesystemStorage(str(tmp_path))


def _write_variant(storage, job_id):
    storage.atomic_write(ResumeVariant.pdf_path(job_id), b"%PDF-tailored")
    doc = ResumeVariant(
        job_id=job_id, job_company="Acme", job_title="Senior SRE",
        generated_at="2026-09-22T00:00:00+00:00", source_file="resumes/master.md",
        sections=[VariantSection(heading="Skills", entries=[
            Evidence(quote="Python, Go", line=2, source_file="resumes/master.md")
        ])],
    )
    storage.atomic_write(ResumeVariant.json_path(job_id), doc.model_dump_json().encode())


def test_no_resume_at_all_returns_none(tmp_path):
    assert select_resume(_storage(tmp_path), "job1") is None


def test_falls_back_to_the_alphabetically_last_top_level_file(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/a-resume.pdf", b"a")
    storage.atomic_write("resumes/versions/b-resume.pdf", b"b")
    choice = select_resume(storage, "job1")
    assert choice.tailored is False
    assert choice.storage_path == "resumes/versions/b-resume.pdf"
    assert choice.variant is None
    assert choice.path.endswith("resumes/versions/b-resume.pdf")


def test_exact_per_job_variant_wins_over_the_fallback(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/zzz-resume.pdf", b"generic")
    _write_variant(storage, "job1")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.storage_path == ResumeVariant.pdf_path("job1")
    assert choice.variant is not None
    assert choice.variant.entry_count() == 1


def test_one_jobs_variant_does_not_become_another_jobs_resume(tmp_path):
    # storage.list is rglob-based, so without a top-level-only filter the
    # variant directory joins the alphabetical pool and job2 silently gets
    # the resume tailored for job1.
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/a-resume.pdf", b"generic")
    _write_variant(storage, "zzz-job1")
    choice = select_resume(storage, "job2")
    assert choice.tailored is False
    assert choice.storage_path == "resumes/versions/a-resume.pdf"


def test_variant_with_no_readable_sidecar_is_still_the_tailored_file(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write(ResumeVariant.pdf_path("job1"), b"%PDF-tailored")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is None


def test_corrupt_sidecar_does_not_break_selection(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write(ResumeVariant.pdf_path("job1"), b"%PDF-tailored")
    storage.atomic_write(ResumeVariant.json_path("job1"), b"{not json")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is None


def test_docx_is_accepted_in_the_fallback_pool(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/mine.docx", b"d")
    choice = select_resume(storage, "job1")
    assert choice.storage_path == "resumes/versions/mine.docx"


def test_unrelated_extensions_are_ignored(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/notes.txt", b"t")
    storage.atomic_write("resumes/versions/mine.pdf", b"p")
    choice = select_resume(storage, "job1")
    assert choice.storage_path == "resumes/versions/mine.pdf"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_select.py -v`
Expected: FAIL — `No module named 'careeros.core.resume_select'`

- [ ] **Step 3: Write the selector**

Create `careeros/core/resume_select.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from careeros.core.models import ResumeVariant
from careeros.storage.interface import StorageProvider

RESUME_EXTENSIONS = (".pdf", ".docx")
_VERSIONS_PREFIX = "resumes/versions/"


@dataclass(frozen=True)
class ResumeChoice:
    path: str          # absolute filesystem path, ready for the form uploader
    storage_path: str  # workspace-relative, for display and audit
    tailored: bool
    variant: ResumeVariant | None = None


def select_resume(storage: StorageProvider, job_id: str) -> ResumeChoice | None:
    """Pick the resume to upload for one job.

    A variant tailored to this job wins. Otherwise the alphabetically-last
    top-level file in resumes/versions/ is used, untailored. Returns None
    when there is no resume at all.
    """
    pdf_path = ResumeVariant.pdf_path(job_id)
    if storage.exists(pdf_path):
        variant = None
        json_path = ResumeVariant.json_path(job_id)
        if storage.exists(json_path):
            try:
                variant = ResumeVariant.model_validate_json(storage.read(json_path).decode())
            except Exception:
                # The sidecar is descriptive, not load-bearing. A missing or
                # corrupt one costs the provenance display, not the upload.
                variant = None
        return ResumeChoice(
            path=storage.resolve(pdf_path),
            storage_path=pdf_path,
            tailored=True,
            variant=variant,
        )

    # Top-level files only. storage.list is rglob-based, so without this
    # filter a per-job variant directory joins the alphabetical pool and one
    # job's tailored resume silently becomes every other job's resume.
    candidates = sorted(
        p for p in storage.list(_VERSIONS_PREFIX)
        if p.endswith(RESUME_EXTENSIONS) and "/" not in p[len(_VERSIONS_PREFIX):]
    )
    if not candidates:
        return None
    chosen = candidates[-1]
    return ResumeChoice(path=storage.resolve(chosen), storage_path=chosen, tailored=False)
```

- [ ] **Step 4: Run the selector tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_select.py -v`
Expected: PASS

- [ ] **Step 5: Wire `apply` to the selector**

In `careeros/cli/apply_cmd.py`: delete the `_RESUME_EXTENSIONS = (".pdf", ".docx")` line, add `from careeros.core.resume_select import select_resume` to the imports, and replace the block currently at lines 70-80 with:

```python
    # Find resume (before profile load so failure is fast and clear)
    resume_choice = select_resume(runtime.storage, job_id)
    if resume_choice is None:
        rprint("[red]No resume found in resumes/versions/ — add one first.[/red]")
        raise typer.Exit(1)
    resume_path = resume_choice.path
```

Then, immediately before the `# Review loop` comment (so it is on screen at the approval prompt), add:

```python
    if resume_choice.tailored:
        detail = ""
        if resume_choice.variant is not None:
            detail = " — " + str(resume_choice.variant.entry_count()) + " evidence-backed entries"
        rprint("[green]Resume: tailored for this job" + detail + "[/green]")
    else:
        rprint("[yellow]Resume: " + resume_choice.storage_path
               + " — NOT tailored to this job. Run 'careeros resume variant --job "
               + job_id + "' to tailor it.[/yellow]")
```

- [ ] **Step 6: Fix the path-blind mock and add apply tests**

In `tests/test_apply_cmd.py`, replace the `storage.exists.return_value = True` line inside `_mock_runtime` with a path-aware side effect, and add a `tailored` switch:

```python
def _mock_runtime(tmp_path, resume_filename="resume.pdf", approved=True, tailored=False):
    storage = MagicMock()
    storage.list.return_value = ["resumes/versions/" + resume_filename]
    storage.resolve.return_value = str(tmp_path / "resumes" / "versions" / resume_filename)
    # Path-aware: a blanket True would make every test claim a tailored
    # variant that does not exist.
    tailored_pdf = "resumes/versions/acme-sre-abc1/resume.pdf"
    tailored_json = "resumes/versions/acme-sre-abc1/variant.json"
    storage.exists.side_effect = lambda p: (
        tailored if p in (tailored_pdf, tailored_json) else True
    )
    storage.read.return_value = b"{}"
    runtime = MagicMock()
    runtime.storage = storage
    runtime.request_approval.return_value = ApprovalResult(approved=approved)
    return runtime
```

Then append:

```python
class TestApplyResumeSelection:
    def test_untailored_fallback_is_announced_as_not_tailored(self, tmp_path):
        runtime = _mock_runtime(tmp_path)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Acme,"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["--job", "acme-sre-abc1"])
        assert "NOT tailored" in result.output

    def test_tailored_variant_is_used_and_announced(self, tmp_path):
        runtime = _mock_runtime(tmp_path, tailored=True)
        with patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=_make_job()), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.PolicyConfig.load", return_value=PolicyConfig()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear Acme,"), \
             patch("careeros.cli.apply_cmd.Prompt.ask", return_value="q"):
            result = runner.invoke(apply_app, ["--job", "acme-sre-abc1"])
        assert "tailored for this job" in result.output
        assert "NOT tailored" not in result.output
        runtime.storage.resolve.assert_any_call("resumes/versions/acme-sre-abc1/resume.pdf")
```

If the existing tests in this file patch a different set of names than the list above, match whatever they already do rather than inventing a new pattern — read two neighbouring tests first and copy their shape.

- [ ] **Step 7: Wire `discover-and-apply` to the selector**

In `careeros/cli/discover_and_apply_cmd.py`: delete `_RESUME_EXTENSIONS`, add `from careeros.core.resume_select import select_resume`, and **delete** the pre-loop block at lines 206-210:

```python
    resume_entries = sorted([...])
    resume_path = runtime.storage.resolve(resume_entries[-1]) if resume_entries else None
```

Selection must move inside the per-job loop, because a variant is per job and a value resolved once before the loop cannot be. Replace the `if resume_path is None:` guard at line 219 with, placed **after** `job_id = p["job_id"]` is assigned:

```python
        resume_choice = select_resume(runtime.storage, job_id)
        if resume_choice is None:
            rprint("[yellow]No resume found — skipping auto-apply for "
                   + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue
        resume_path = resume_choice.path
        if resume_choice.tailored:
            rprint("Resume: tailored for " + p["company"] + ".")
        else:
            rprint("Resume: " + resume_choice.storage_path + " — NOT tailored to this job.")
```

Check the surrounding lines: the existing guard sits before `job_id = p["job_id"]`, so the order of those statements must be swapped. Read lines 214-230 and place the new block where `job_id` is already defined. This command never generates a variant — it runs unattended, and generating documents nobody reviewed is out of scope by design.

- [ ] **Step 8: Add a discover-and-apply test**

Append to `tests/test_discover_and_apply_cmd.py`, matching the fixture style already used in that file:

```python
def test_auto_apply_prefers_a_tailored_variant_per_job(tmp_path):
    """Each job gets its own variant; selection happens inside the loop."""
    # Follow the existing fixtures in this file for workspace + policy setup.
    # Assert that when resumes/versions/<job_id>/resume.pdf exists for the
    # eligible job, the filler receives that path rather than the top-level
    # fallback, and that a second eligible job without a variant still
    # receives the fallback in the same run.
```

Replace that docstring-only placeholder with a real test built on the file's existing helpers — read them first. The assertion that matters: **two eligible jobs in one run, one with a variant and one without, must receive different resume paths.** A single-job test cannot catch a selection that was hoisted out of the loop.

- [ ] **Step 9: Run the touched test files**

Run: `.venv/bin/python -m pytest tests/test_resume_select.py tests/test_apply_cmd.py tests/test_discover_and_apply_cmd.py -v`
Expected: PASS, including every pre-existing test in the two modified files

- [ ] **Step 10: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures

- [ ] **Step 11: Commit**

```bash
git add careeros/core/resume_select.py careeros/cli/apply_cmd.py careeros/cli/discover_and_apply_cmd.py tests/test_resume_select.py tests/test_apply_cmd.py tests/test_discover_and_apply_cmd.py
git commit -m "feat: select a per-job resume variant in apply and discover-and-apply

Replaces the alphabetical scan duplicated across both commands with one
exact per-job lookup, and states at the approval prompt whether the resume
going up is tailored.

Also fixes a latent defect: storage.list is rglob-based, so any
subdirectory under resumes/versions/ already polluted the alphabetical
pool. Without a top-level-only filter, one job's variant would become
every other job's resume."
```

---

## Task 6: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the shipped behaviour of every earlier task. Verify each claim against the code before writing it.

- [ ] **Step 1: Add the command to the commands list**

In `README.md`, immediately after the existing `careeros resume ingest [path]` bullet, add:

```markdown
- **`careeros resume variant --job <id>`** — build a resume tailored to one job,
  assembled only from verbatim spans of your master resume. The model selects and
  orders; it never writes new text, and any span it cannot copy exactly is dropped
  and named. Stored as `resumes/versions/<job_id>/resume.pdf` with a
  `variant.json` sidecar recording which line of `resumes/master.md` backs every
  line of the document. `apply` uses it automatically.
```

- [ ] **Step 2: Extend the workspace layout tree**

Replace the `resumes/` block in the workspace-layout tree with:

```
  resumes/
    master.md                 ← your stored resume, read by `careeros resume ingest`
    versions/
      my-resume.pdf           ← your own files; used when a job has no tailored variant
      <job_id>/
        resume.pdf            ← job-tailored variant, uploaded by `careeros apply`
        variant.json          ← which line of master.md backs each line of the variant
```

- [ ] **Step 3: Note the Chromium requirement**

Find where the README documents installation or prerequisites. Add one sentence, in the existing voice, stating that resume variant rendering and browser automation both need `playwright install chromium`, which `pip install` does not do. If the README already says this for browser automation, extend that sentence rather than adding a second one.

- [ ] **Step 4: Correct the roadmap pointer**

The "what's planned next" sentence near the end currently reads `per-job resume variants (evidence-backed resume ingestion itself has shipped)`. Per-job variants have now shipped too. Rewrite it to name only what actually remains: a second real `AgentRuntime` and outreach expansion.

- [ ] **Step 5: Verify every claim against the code**

Re-read your four edits against the shipped implementation. Specifically confirm: the command name and its `--job` flag match Task 4; the two file paths match `ResumeVariant.pdf_path` / `json_path`; "dropped and named" is true of both the command output and the sidecar; and `apply` really does use the variant automatically. Do not add a test count anywhere.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures (documentation-only change, but confirm nothing was touched by accident)

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: document job-tailored resume variants

Adds the resume variant command, the per-job storage layout, and the
Chromium prerequisite pip install does not cover."
```

---

## Plan Self-Review

**1. Spec coverage.** Every spec section maps to a task: storage layout → Tasks 1 (path helpers) and 5 (selection); selection → Task 5; generation and the three guards → Task 1; constants `_MASTER_CAP` and the reused `MAX_QUOTE_CHARS` → Task 1 Steps 6/8; rendering, escaping, ephemeral Chromium, `set_content`-only, `RendererUnavailable` → Tasks 2 and 3; the `variant` command and its write order → Task 4; the failure table → Task 4 Steps 1/4 (every row has a test); regeneration semantics → Task 4's `test_variant_regeneration_overwrites_only_on_success`; the `skills`-as-hint rule → Task 1's `test_skills_are_a_hint_and_never_contribute_text`; testing table → the six test files; migration (none) → no task needed, and Task 1 adds only new models with defaults.

**2. Placeholder scan.** One deliberate exception: Task 5 Step 8 gives a test's *intent and required assertion* rather than its body, because `tests/test_discover_and_apply_cmd.py`'s fixtures were not read while writing this plan and inventing helper names is how three Phase 10 briefs went wrong. The step states exactly what must be asserted and instructs reading the existing helpers first. Task 6 Steps 3 and 4 likewise describe edits to README prose whose current wording must be read in place.

**3. Type consistency.** `select_variant_content(jd_text, master_text, skills, source_file, model=None)` is called positionally in Task 4 exactly as defined in Task 1, and Task 4's test asserts `args[0]`, `args[1]`, `args[3]`. `entry_count()` is a plain method on both `VariantResult` and `ResumeVariant`, deliberately the same shape so a reader moving between Tasks 1, 4 and 5 never has to remember which one needs parentheses. `render_pdf` returns `bytes` everywhere. `ResumeChoice.path` is absolute and `.storage_path` relative, used accordingly in Task 5.
