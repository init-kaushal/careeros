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


def test_numeric_last_used_does_not_raise_and_is_coerced():
    import json
    payload = json.dumps({"skills": [
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": 2026}
    ]})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert [s.name for s in result.skills.skills] == ["Python"]
    assert result.skills.skills[0].last_used == "2026"


def test_non_dict_payload_yields_empty_result_without_raising():
    with patch("litellm.completion", return_value=_resp("[1, 2, 3]")):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []
    assert result.dropped == ()


def test_non_string_quote_is_dropped_not_fatal():
    import json
    payload = json.dumps({"skills": [
        {"name": "Broken", "quote": ["not", "a", "string"], "last_used": "2026"},
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
    ]})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert [s.name for s in result.skills.skills] == ["Python"]
    assert result.dropped == ("Broken",)


def test_a_later_verifiable_duplicate_is_kept_when_the_first_fails():
    # The first candidate for a name carrying a bad quote must not
    # permanently exclude the name.
    import json
    payload = json.dumps({"skills": [
        {"name": "Python", "quote": "Expert in Python since 2009", "last_used": "2026"},
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
    ]})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert [s.name for s in result.skills.skills] == ["Python"]
    assert result.dropped == ()


def test_null_skills_payload_yields_empty_result_without_raising():
    # A plausible model response when told to omit every skill it cannot
    # support. {"skills": null} must not reach `for candidate in None`.
    import json
    payload = json.dumps({"skills": None})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []
    assert result.dropped == ()
    assert result.error is None


def test_non_list_skills_payload_yields_empty_result_without_raising():
    import json
    payload = json.dumps({"skills": "Python, Go"})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []
    assert result.dropped == ()


def test_non_string_name_is_skipped_not_fatal():
    # A candidate whose "name" is not a string must be skipped outright,
    # never coerced with str() into a fabricated skill name, and must not
    # take the rest of the batch down with it.
    import json
    payload = json.dumps({"skills": [
        {"name": 123, "quote": "Python, Go, Kubernetes", "last_used": "2026"},
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
    ]})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert [s.name for s in result.skills.skills] == ["Python"]
    assert result.dropped == ()


def test_case_differing_duplicate_appears_in_exactly_one_list():
    # verified_names must be compared on the same normalized form used for
    # `seen`, or a duplicate differing only in case lands in both the
    # stored skills and the dropped list.
    import json
    payload = json.dumps({"skills": [
        {"name": "Python", "quote": "Expert in Python since 2009", "last_used": "2026"},
        {"name": "python", "quote": "Python, Go, Kubernetes", "last_used": "2026"},
    ]})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    stored_names = {s.name for s in result.skills.skills}
    assert stored_names == {"python"}
    assert result.dropped == ()


def test_dict_last_used_does_not_raise_and_is_stringified():
    # `last_used` is stored, not verified — totality must hold for any
    # JSON-representable value the model puts there, not just strings.
    import json
    payload = json.dumps({"skills": [
        {"name": "Python", "quote": "Python, Go, Kubernetes", "last_used": {"year": 2020}},
    ]})
    with patch("litellm.completion", return_value=_resp(payload)):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert [s.name for s in result.skills.skills] == ["Python"]
    assert result.skills.skills[0].last_used == str({"year": 2020})


def test_llm_call_failure_sets_the_error_field():
    with patch("litellm.completion", side_effect=RuntimeError("bad api key")):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []
    assert result.dropped == ()
    assert result.error == "bad api key"


def test_unparseable_response_does_not_set_the_error_field():
    # The LLM call succeeded; the model just answered with garbage. That is
    # not the "API key or network is broken" failure mode `error` exists for.
    with patch("litellm.completion", return_value=_resp("not json at all")):
        result = ingest_resume(_RESUME, "resumes/master.md")
    assert result.skills.skills == []
    assert result.error is None
