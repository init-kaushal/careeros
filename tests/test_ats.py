import json
import pytest
import urllib.error
from unittest.mock import patch, MagicMock
from careeros.sources.ats import fetch_greenhouse, fetch_lever, ATSFetchError


GH_FIXTURE = {
    "jobs": [
        {
            "id": 123456,
            "title": "Senior SRE",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123456",
            "location": {"name": "San Francisco, CA"},
            "content": "<p>We are looking for a <b>Senior SRE</b> with Python experience.</p>",
        }
    ]
}

LEVER_FIXTURE = [
    {
        "id": "abc-def-123",
        "text": "Staff Engineer",
        "hostedUrl": "https://jobs.lever.co/stripe/abc-def-123",
        "categories": {"location": "New York, NY"},
        "descriptionPlain": "Great role at Stripe.",
    }
]


def _mock_urlopen(payload: object, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_greenhouse_parses_jobs():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(GH_FIXTURE)):
        result = fetch_greenhouse("acme")
    assert len(result) == 1
    assert result[0]["source_id"] == "123456"
    assert result[0]["title"] == "Senior SRE"
    assert result[0]["location"] == "San Francisco, CA"
    assert result[0]["url"] == "https://boards.greenhouse.io/acme/jobs/123456"


def test_fetch_greenhouse_strips_html():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(GH_FIXTURE)):
        result = fetch_greenhouse("acme")
    assert "<p>" not in result[0]["description"]
    assert "<b>" not in result[0]["description"]
    assert "Senior SRE" in result[0]["description"]


def test_fetch_greenhouse_empty_jobs():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen({"jobs": []})):
        result = fetch_greenhouse("acme")
    assert result == []


def test_fetch_lever_parses_postings():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(LEVER_FIXTURE)):
        result = fetch_lever("stripe")
    assert len(result) == 1
    assert result[0]["source_id"] == "abc-def-123"
    assert result[0]["title"] == "Staff Engineer"
    assert result[0]["location"] == "New York, NY"
    assert result[0]["url"] == "https://jobs.lever.co/stripe/abc-def-123"


def test_fetch_lever_description_plain():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(LEVER_FIXTURE)):
        result = fetch_lever("stripe")
    assert result[0]["description"] == "Great role at Stripe."


def test_fetch_greenhouse_404_raises():
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        with pytest.raises(ATSFetchError):
            fetch_greenhouse("unknown-company")


def test_fetch_greenhouse_timeout_raises():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(ATSFetchError):
            fetch_greenhouse("acme")


def test_fetch_lever_404_raises():
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        with pytest.raises(ATSFetchError):
            fetch_lever("unknown-company")


def test_description_capped_at_4000():
    long_content = "<p>" + "x" * 5000 + "</p>"
    fixture = {"jobs": [{**GH_FIXTURE["jobs"][0], "content": long_content}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(fixture)):
        result = fetch_greenhouse("acme")
    assert len(result[0]["description"]) <= 4000
