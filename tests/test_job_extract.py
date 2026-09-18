import pytest
from unittest.mock import patch, MagicMock
from careeros.skills.job_extract import extract_job_fields, DEFAULT_LLM_MODEL


def _mock_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


_FULL = '{"company":"Acme","title":"Senior SRE","location":"SF, CA","remote":false,"salary_min":150000,"salary_max":200000,"currency":"USD","requirements":["Python","Kubernetes"],"summary":"Great SRE role."}'
_PARTIAL = '{"company":"Stripe","title":"Engineer","location":null,"remote":null,"salary_min":null,"salary_max":null,"currency":null,"requirements":[],"summary":null}'
_FENCED = '```json\n' + _FULL + '\n```'


def test_full_extraction():
    with patch("litellm.completion", return_value=_mock_resp(_FULL)):
        result = extract_job_fields("dummy jd")
    assert result["company"] == "Acme"
    assert result["salary_min"] == 150000
    assert result["requirements"] == ["Python", "Kubernetes"]


def test_partial_extraction_null_salary():
    with patch("litellm.completion", return_value=_mock_resp(_PARTIAL)):
        result = extract_job_fields("dummy jd")
    assert result["company"] == "Stripe"
    assert result["salary_min"] is None


def test_fenced_json_stripped():
    with patch("litellm.completion", return_value=_mock_resp(_FENCED)):
        result = extract_job_fields("dummy jd")
    assert result["company"] == "Acme"


def test_llm_failure_returns_empty_dict():
    with patch("litellm.completion", side_effect=Exception("API error")):
        result = extract_job_fields("dummy jd")
    assert result == {}


def test_bad_json_returns_empty_dict():
    with patch("litellm.completion", return_value=_mock_resp("not valid json")):
        result = extract_job_fields("dummy jd")
    assert result == {}


def test_uses_default_model(monkeypatch):
    monkeypatch.delenv("CAREEROS_MODEL", raising=False)
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields("dummy jd")
    assert mock_c.call_args.kwargs["model"] == DEFAULT_LLM_MODEL


def test_respects_careeros_model_env_var(monkeypatch):
    monkeypatch.setenv("CAREEROS_MODEL", "gpt-4o-mini")
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields("dummy jd")
    assert mock_c.call_args.kwargs["model"] == "gpt-4o-mini"


def test_model_param_overrides_env(monkeypatch):
    monkeypatch.setenv("CAREEROS_MODEL", "gpt-4o-mini")
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields("dummy jd", model="ollama/llama3.2")
    assert mock_c.call_args.kwargs["model"] == "ollama/llama3.2"


def test_jd_text_in_prompt():
    jd = "Looking for a Senior SRE with Python experience"
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields(jd)
    messages = mock_c.call_args.kwargs["messages"]
    assert jd in messages[0]["content"]


def test_jd_truncated_to_4000_chars():
    long_jd = "x" * 6000
    with patch("litellm.completion", return_value=_mock_resp(_FULL)) as mock_c:
        extract_job_fields(long_jd)
    content = mock_c.call_args.kwargs["messages"][0]["content"]
    assert "x" * 4001 not in content
