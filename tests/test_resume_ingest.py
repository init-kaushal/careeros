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
