from unittest.mock import MagicMock, patch


def test_extract_company_info_parses_llm_json():
    from careeros.skills.company_research import extract_company_info
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = (
        '{"industry": "Software", "size": "51-200", "notes": "Series B startup"}'
    )
    with patch("careeros.skills.company_research.litellm.completion", return_value=mock_resp):
        result = extract_company_info("Acme Corp is a software company...")
    assert result["industry"] == "Software"
    assert result["size"] == "51-200"
    assert result["notes"] == "Series B startup"


def test_extract_company_info_handles_llm_failure():
    from careeros.skills.company_research import extract_company_info
    with patch("careeros.skills.company_research.litellm.completion", side_effect=Exception("boom")):
        result = extract_company_info("some content")
    assert result["industry"] is None
    assert result["size"] is None
    assert result["notes"] is None


def test_extract_company_info_handles_malformed_json():
    from careeros.skills.company_research import extract_company_info
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "not json at all"
    with patch("careeros.skills.company_research.litellm.completion", return_value=mock_resp):
        result = extract_company_info("some content")
    assert result["industry"] is None
