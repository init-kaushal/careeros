from unittest.mock import MagicMock, patch


def test_classify_person_role_returns_llm_classification():
    from careeros.skills.people_research import classify_person_role
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "em"
    with patch("careeros.skills.people_research.litellm.completion", return_value=mock_resp):
        result = classify_person_role("Jane Doe", "Engineering Manager")
    assert result == "em"


def test_classify_person_role_normalizes_whitespace_and_case():
    from careeros.skills.people_research import classify_person_role
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "  RECRUITER  \n"
    with patch("careeros.skills.people_research.litellm.completion", return_value=mock_resp):
        result = classify_person_role("John Smith", "Technical Recruiter")
    assert result == "recruiter"


def test_classify_person_role_falls_back_to_ic_on_failure():
    from careeros.skills.people_research import classify_person_role
    with patch("careeros.skills.people_research.litellm.completion", side_effect=Exception("boom")):
        result = classify_person_role("Jane Doe", "Software Engineer")
    assert result == "ic"


def test_classify_person_role_falls_back_to_ic_on_unrecognized_output():
    from careeros.skills.people_research import classify_person_role
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "not a valid category"
    with patch("careeros.skills.people_research.litellm.completion", return_value=mock_resp):
        result = classify_person_role("Jane Doe", "Some Title")
    assert result == "ic"
