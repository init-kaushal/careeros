import json
from unittest.mock import MagicMock, patch

import pytest

from careeros.core.models import Evidence, ResumeVariant, Skill, Skills, VariantSection
from careeros.skills.resume_evidence import normalize_quote
from careeros.skills.resume_variant import (
    ALLOWED_HEADINGS,
    VariantResult,
    select_variant_content,
)

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


def test_heading_casing_and_padding_are_normalized_to_the_canonical_form():
    # A model varying casing is plausible, and losing verified content to it
    # is avoidable. The stored heading is still the canonical allowlist
    # spelling regardless of what casing or padding the model sent.
    for raw_heading in ("SKILLS", "skills", " Skills "):
        result = _call({"sections": [
            {"heading": raw_heading, "quotes": ["Python, Go, Kubernetes, Terraform"]}
        ]})
        assert result.sections[0].heading == "Skills", raw_heading


def test_unknown_heading_is_still_dropped_and_named_with_the_original_text():
    result = _call({"sections": [
        {"heading": "sKiLLZ", "quotes": ["Python, Go, Kubernetes, Terraform"]}
    ]})
    assert result.sections == ()
    assert result.dropped == ("section heading: sKiLLZ",)


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
    # A malformed payload must yield an empty selection, not merely a
    # non-raising one: asserting only isinstance would still pass if a
    # regression started returning fabricated sections.
    assert result.entry_count() == 0
    assert result.sections == ()
    assert result.error is None
    assert isinstance(result.dropped, tuple)
