from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from careeros.browser.session import check_board_sessions


def _patch_launch(monkeypatch, context, record=None):
    @contextmanager
    def fake_launch(headless=False):
        if record is not None:
            record["opens"] = record.get("opens", 0) + 1
            record["headless"] = headless
            record["entered"] = True
        try:
            yield context, MagicMock()
        finally:
            if record is not None:
                record["exited"] = True

    monkeypatch.setattr("careeros.browser.session.launch_browser", fake_launch)


def _context_with(cookies_by_domain):
    context = MagicMock()
    context.cookies.side_effect = lambda domain: cookies_by_domain.get(domain, [])
    return context


def test_authorized_when_session_cookie_present(monkeypatch):
    context = _context_with(
        {"https://www.linkedin.com": [{"name": "li_at", "value": "x"}]}
    )
    _patch_launch(monkeypatch, context)
    assert check_board_sessions(["linkedin"]) == {"linkedin": True}


def test_unauthorized_when_session_cookie_absent(monkeypatch):
    context = _context_with(
        {"https://www.linkedin.com": [{"name": "bcookie", "value": "x"}]}
    )
    _patch_launch(monkeypatch, context)
    assert check_board_sessions(["linkedin"]) == {"linkedin": False}


def test_cookie_on_a_different_domain_does_not_authorize(monkeypatch):
    # li_at present, but only under Indeed's domain.
    context = _context_with(
        {"https://www.indeed.com": [{"name": "li_at", "value": "x"}]}
    )
    _patch_launch(monkeypatch, context)
    assert check_board_sessions(["linkedin"]) == {"linkedin": False}


def test_multiple_boards_resolved_in_one_context_open(monkeypatch):
    context = _context_with(
        {
            "https://www.linkedin.com": [{"name": "li_at", "value": "x"}],
            "https://wellfound.com": [],
        }
    )
    record = {}
    _patch_launch(monkeypatch, context, record)
    result = check_board_sessions(["linkedin", "wellfound"])
    assert result == {"linkedin": True, "wellfound": False}
    assert record["opens"] == 1


def test_unknown_board_is_false_and_opens_no_browser(monkeypatch):
    record = {}
    _patch_launch(monkeypatch, MagicMock(), record)
    assert check_board_sessions(["nonsense"]) == {"nonsense": False}
    assert "entered" not in record


def test_empty_input_opens_no_browser(monkeypatch):
    record = {}
    _patch_launch(monkeypatch, MagicMock(), record)
    assert check_board_sessions([]) == {}
    assert "entered" not in record


def test_runs_headless(monkeypatch):
    context = _context_with({"https://www.linkedin.com": []})
    record = {}
    _patch_launch(monkeypatch, context, record)
    check_board_sessions(["linkedin"])
    assert record["headless"] is True


def test_context_is_closed_even_when_cookie_read_raises(monkeypatch):
    # The self-deadlock invariant: every caller opens the profile again
    # immediately afterwards, and launch_persistent_context holds an exclusive
    # lock. A context left open would make the pre-flight deadlock against the
    # run it is clearing.
    context = MagicMock()
    context.cookies.side_effect = RuntimeError("boom")
    record = {}
    _patch_launch(monkeypatch, context, record)
    assert check_board_sessions(["linkedin"]) == {"linkedin": False}
    assert record["exited"] is True


def test_one_board_failing_does_not_stop_the_others(monkeypatch):
    context = MagicMock()

    def cookies(domain):
        if domain == "https://www.linkedin.com":
            raise RuntimeError("transient browser error")
        return [{"name": "_wellfound_session", "value": "x"}]

    context.cookies.side_effect = cookies
    record = {}
    _patch_launch(monkeypatch, context, record)
    result = check_board_sessions(["linkedin", "wellfound"])
    assert result == {"linkedin": False, "wellfound": True}
    assert record["opens"] == 1
    assert record["exited"] is True
