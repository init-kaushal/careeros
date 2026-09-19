from unittest.mock import MagicMock, patch


def test_extract_compensation_data_parses_llm_json():
    from careeros.skills.compensation_research import extract_compensation_data
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = (
        '{"base_min": 180000, "base_max": 220000, "bonus": "10-15%", "equity": "0.01-0.05%", "confidence": "medium"}'
    )
    with patch("careeros.skills.compensation_research.litellm.completion", return_value=mock_resp):
        result = extract_compensation_data("Senior SRE at Acme Corp: $180K-$220K base...")
    assert result["base_min"] == 180000
    assert result["base_max"] == 220000
    assert result["bonus"] == "10-15%"
    assert result["equity"] == "0.01-0.05%"
    assert result["confidence"] == "medium"


def test_extract_compensation_data_handles_llm_failure():
    from careeros.skills.compensation_research import extract_compensation_data
    with patch("careeros.skills.compensation_research.litellm.completion", side_effect=Exception("boom")):
        result = extract_compensation_data("some content")
    assert result["base_min"] is None
    assert result["base_max"] is None
    assert result["bonus"] is None
    assert result["equity"] is None
    assert result["confidence"] == "low"


def test_extract_compensation_data_handles_malformed_json():
    from careeros.skills.compensation_research import extract_compensation_data
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "not json at all"
    with patch("careeros.skills.compensation_research.litellm.completion", return_value=mock_resp):
        result = extract_compensation_data("some content")
    assert result["confidence"] == "low"


def test_extract_compensation_data_handles_thin_page_content():
    from careeros.skills.compensation_research import extract_compensation_data
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = (
        '{"base_min": null, "base_max": null, "bonus": null, "equity": null, "confidence": "low"}'
    )
    with patch("careeros.skills.compensation_research.litellm.completion", return_value=mock_resp):
        result = extract_compensation_data("")
    assert result["base_min"] is None
    assert result["confidence"] == "low"
