from unittest.mock import patch

import pytest

from careeros.sources.ats import ATSFetchError
from careeros.sources.greenhouse import GreenhouseSource
from careeros.sources.lever import LeverSource

_GH_RAW = [
    {"source_id": "1", "title": "Senior SRE",
     "url": "https://boards.greenhouse.io/stripe/jobs/1",
     "location": "SF", "description": "jd"},
]
_LV_RAW = [
    {"source_id": "a", "title": "Staff Engineer",
     "url": "https://jobs.lever.co/acme/a",
     "location": "Remote", "description": "jd"},
]


def test_greenhouse_name():
    assert GreenhouseSource("Stripe").name == "greenhouse"


def test_lever_name():
    assert LeverSource("Acme").name == "lever"


def test_greenhouse_fetch_returns_postings_with_configured_company():
    with patch("careeros.sources.greenhouse.fetch_greenhouse", return_value=_GH_RAW) as f:
        out = GreenhouseSource("Stripe").fetch("stripe")
    f.assert_called_once_with("stripe")
    assert len(out) == 1
    assert out[0].company == "Stripe"          # configured, never derived from the slug
    assert out[0].source == "greenhouse"
    assert out[0].title == "Senior SRE"
    assert out[0].url == "https://boards.greenhouse.io/stripe/jobs/1"
    assert out[0].location == "SF"
    assert out[0].description == "jd"
    assert out[0].source_id == "1"


def test_lever_fetch_returns_postings_with_configured_company():
    with patch("careeros.sources.lever.fetch_lever", return_value=_LV_RAW) as f:
        out = LeverSource("Acme").fetch("acme")
    f.assert_called_once_with("acme")
    assert out[0].company == "Acme"
    assert out[0].source == "lever"
    assert out[0].url == "https://jobs.lever.co/acme/a"


def test_greenhouse_fetch_propagates_ats_error():
    # The caller decides how to report an unreachable board; swallowing it
    # here would make a bad slug indistinguishable from an empty board.
    with patch("careeros.sources.greenhouse.fetch_greenhouse",
               side_effect=ATSFetchError("not_found")):
        with pytest.raises(ATSFetchError):
            GreenhouseSource("Stripe").fetch("nope")


def test_lever_fetch_propagates_ats_error():
    with patch("careeros.sources.lever.fetch_lever",
               side_effect=ATSFetchError("not_found")):
        with pytest.raises(ATSFetchError):
            LeverSource("Acme").fetch("nope")


def test_greenhouse_skips_posting_missing_a_url_without_aborting_the_batch():
    raw = [{"source_id": "1", "title": "A", "location": None, "description": None},
           {"source_id": "2", "title": "B", "url": "https://x.test/2",
            "location": None, "description": None}]
    with patch("careeros.sources.greenhouse.fetch_greenhouse", return_value=raw):
        out = GreenhouseSource("Stripe").fetch("stripe")
    assert [p.title for p in out] == ["B"]


def test_empty_board_returns_empty_list():
    with patch("careeros.sources.greenhouse.fetch_greenhouse", return_value=[]):
        assert GreenhouseSource("Stripe").fetch("stripe") == []


def test_postings_from_ats_maps_every_field_and_skips_urlless_items():
    from careeros.sources.base import postings_from_ats
    raw = [
        {"source_id": "1", "title": "A", "location": "SF", "description": "jd",
         "url": "https://x.test/1"},
        {"source_id": "2", "title": "B", "location": None, "description": None},
    ]
    out = postings_from_ats("greenhouse", "Stripe", raw)
    assert [p.title for p in out] == ["A"]
    assert out[0].source == "greenhouse"
    assert out[0].company == "Stripe"
    assert out[0].location == "SF"
    assert out[0].description == "jd"
    assert out[0].source_id == "1"
