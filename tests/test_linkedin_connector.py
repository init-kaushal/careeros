"""Tests for LinkedInConnector.

Every test here drives a mock `Page`. None may construct a real browser:
`tests/conftest.py` has an autouse guard, but the guard is a backstop, not
the barrier — launch_browser binds the machine-global Chrome profile, which
can carry the user's real LinkedIn session, and this class *sends* things.
So each send test asserts the mock was actually the object driven, rather
than assuming a passing return value means no real page was touched.
"""

import ast
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from careeros.browser.connect import LinkedInConnector
from careeros.browser.connect.linkedin import (
    _ADD_NOTE_SELECTOR,
    _CONNECTED_SELECTOR,
    _CONNECT_SELECTOR,
    _NOTE_SELECTOR,
    _PENDING_SELECTOR,
    _SEND_SELECTOR,
)

PROFILE = "https://www.linkedin.com/in/jane-doe/"
NOTE = "Hi Jane - I saw the Senior SRE opening at Acme and would love to connect."


def _locator(*, present=True):
    """One mock locator that is also its own `.first`.

    The connector always reads `page.locator(sel).first`, so the object a
    test configures has to be reachable through `.first`. Pointing it at
    itself keeps the per-state assertions (`send.click.assert_not_called()`)
    aimed at the same object the connector actually touched.
    """
    loc = MagicMock()
    loc.first = loc
    loc.count.return_value = 1 if present else 0
    loc.is_visible.return_value = present
    return loc


def _make_page(
    *,
    pending=False,
    connected=False,
    connect=True,
    add_note=True,
    note_area=True,
    filled=None,
    send=True,
    send_still_visible=False,
):
    """A mock Page whose six probed selectors are separately configurable.

    Dispatch is on exact selector equality against the module's own
    constants, not a substring guess, so a selector the connector uses that
    this builder does not know about fails loudly instead of silently
    returning a truthy MagicMock (which would make every `count() > 0` and
    `is_visible()` check pass and hide the behaviour under test).
    """
    locs = {
        "pending": _locator(present=pending),
        "connected": _locator(present=connected),
        "connect": _locator(present=connect),
        "add_note": _locator(present=add_note),
        "note": _locator(present=note_area),
        "send": _locator(present=send),
    }
    locs["note"].input_value.return_value = NOTE if filled is None else filled
    # is_visible is consulted twice on the send control: once to decide the
    # click is possible, once afterwards to verify the modal closed.
    locs["send"].is_visible.side_effect = [send, send_still_visible]

    by_selector = {
        _PENDING_SELECTOR: locs["pending"],
        _CONNECTED_SELECTOR: locs["connected"],
        _CONNECT_SELECTOR: locs["connect"],
        _ADD_NOTE_SELECTOR: locs["add_note"],
        _NOTE_SELECTOR: locs["note"],
        _SEND_SELECTOR: locs["send"],
    }

    page = MagicMock()

    def locator(selector):
        if selector not in by_selector:
            raise AssertionError("connector used an unregistered selector: " + selector)
        return by_selector[selector]

    page.locator.side_effect = locator
    return page, locs


def _assert_nothing_was_submitted(locs):
    locs["send"].click.assert_not_called()
    locs["note"].fill.assert_not_called()


class TestCanHandle:
    def test_handles_a_www_profile_url(self):
        assert LinkedInConnector().can_handle("https://www.linkedin.com/in/jane-doe/") is True

    def test_handles_a_profile_url_without_www(self):
        assert LinkedInConnector().can_handle("https://linkedin.com/in/jane") is True

    def test_handles_a_country_subdomain_profile_url(self):
        # LinkedIn serves the same profile from uk./in./de. hosts.
        assert LinkedInConnector().can_handle("https://uk.linkedin.com/in/jane-doe") is True

    def test_handles_a_profile_url_with_a_query_string(self):
        assert LinkedInConnector().can_handle(
            "https://www.linkedin.com/in/jane-doe?miniProfileUrn=abc"
        ) is True

    def test_rejects_a_linkedin_jobs_url(self):
        # A job posting is not a person; sending a connection request there
        # is meaningless, and LinkedInFiller already owns that URL shape.
        assert LinkedInConnector().can_handle("https://www.linkedin.com/jobs/view/1234567") is False

    def test_rejects_a_linkedin_company_url(self):
        assert LinkedInConnector().can_handle("https://www.linkedin.com/company/acme/") is False

    def test_rejects_a_linkedin_people_search_url(self):
        assert LinkedInConnector().can_handle(
            "https://www.linkedin.com/search/results/people/?keywords=sre"
        ) is False

    def test_rejects_a_non_linkedin_url(self):
        assert LinkedInConnector().can_handle("https://boards.greenhouse.io/acme/jobs/1") is False

    def test_rejects_an_empty_string(self):
        assert LinkedInConnector().can_handle("") is False

    def test_rejects_a_profile_path_with_no_slug(self):
        # "/in/" alone names nobody, so there is no human to contact.
        assert LinkedInConnector().can_handle("https://www.linkedin.com/in/") is False

    def test_rejects_a_lookalike_host(self):
        # The whole point of the check: this host is NOT linkedin.com, and a
        # naive `"linkedin.com/in/" in url` substring test would accept it.
        assert LinkedInConnector().can_handle("https://linkedin.com.evil.example/in/jane") is False

    def test_rejects_a_linkedin_profile_url_embedded_in_another_hosts_path(self):
        assert LinkedInConnector().can_handle(
            "https://evil.example/https://www.linkedin.com/in/jane"
        ) is False

    def test_rejects_a_malformed_url(self):
        # urlparse raises ValueError on an unterminated IPv6 literal; the
        # check must answer False rather than propagate.
        assert LinkedInConnector().can_handle("http://[::1/in/jane") is False

    def test_is_deterministic(self):
        connector = LinkedInConnector()
        assert [connector.can_handle(PROFILE) for _ in range(5)] == [True] * 5
        assert [connector.can_handle("https://example.com/x") for _ in range(5)] == [False] * 5

    def test_takes_no_page_and_touches_no_network(self):
        # Asserted rather than assumed: any socket use inside can_handle
        # raises, so a lookup-based implementation would fail here.
        connector = LinkedInConnector()
        with patch("socket.socket", side_effect=AssertionError("can_handle opened a socket")):
            with patch("socket.create_connection", side_effect=AssertionError("can_handle connected")):
                with patch("socket.getaddrinfo", side_effect=AssertionError("can_handle resolved a host")):
                    assert connector.can_handle(PROFILE) is True
                    assert connector.can_handle("https://example.com/in/jane") is False
        # Sanity check that the patch targets are the real names, so the
        # assertion above is not vacuous.
        assert hasattr(socket, "getaddrinfo")


class TestSendConnectionRequestOrdinaryNonCompletions:
    def test_returns_false_when_already_connected(self):
        page, locs = _make_page(connected=True)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        _assert_nothing_was_submitted(locs)
        locs["connect"].click.assert_not_called()

    def test_already_connected_wins_even_when_a_connect_button_exists(self):
        # A profile page also renders "People also viewed" cards, each with
        # its own Connect button. The 1st-degree badge is the authoritative
        # signal for the profile named in the URL, so it must be consulted
        # before any Connect button is clicked.
        page, locs = _make_page(connected=True, connect=True)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["connect"].click.assert_not_called()
        _assert_nothing_was_submitted(locs)

    def test_returns_false_when_request_already_pending(self):
        page, locs = _make_page(pending=True)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        _assert_nothing_was_submitted(locs)
        locs["connect"].click.assert_not_called()

    def test_pending_wins_even_when_a_connect_button_exists(self):
        page, locs = _make_page(pending=True, connect=True)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["connect"].click.assert_not_called()
        _assert_nothing_was_submitted(locs)

    def test_returns_false_when_there_is_no_connect_button(self):
        page, locs = _make_page(connect=False)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        _assert_nothing_was_submitted(locs)

    def test_returns_false_rather_than_raising_for_all_three(self):
        # The contract the operations layer depends on: these are outcomes,
        # not exceptions. Kept as one explicit statement of the shared
        # property, alongside the per-state tests above.
        connector = LinkedInConnector()
        for kwargs in ({"connected": True}, {"pending": True}, {"connect": False}):
            page, _ = _make_page(**kwargs)
            assert connector.send_connection_request(page, PROFILE, NOTE) is False


class TestSendConnectionRequestCompletedSend:
    def test_returns_true_and_the_note_reaches_the_textarea(self):
        page, locs = _make_page()
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is True
        # The consequence, not just the return value: the reviewed note text
        # went into the invite textarea, and the send control was clicked.
        locs["add_note"].click.assert_called_once()
        locs["note"].fill.assert_called_once_with(NOTE)
        locs["send"].click.assert_called_once()
        locs["connect"].click.assert_called_once()

    def test_drives_the_mock_page_that_was_passed_in(self):
        page, locs = _make_page()
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is True
        page.goto.assert_called_once()
        assert page.goto.call_args.args[0] == PROFILE
        # Six selector probes on the injected mock; had a real page been
        # built instead, this mock would be untouched.
        assert page.locator.call_count >= 6

    def test_returns_true_when_post_send_verification_raises(self):
        # Mirrors GreenhouseFiller: the send click already fired, so a
        # failure while verifying is not evidence that nothing was sent.
        page, locs = _make_page()
        locs["send"].is_visible.side_effect = [True, Exception("boom")]
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is True
        locs["send"].click.assert_called_once()

    def test_returns_false_when_the_send_control_is_still_visible_afterwards(self):
        # The modal did not close, so the invite was not accepted.
        page, locs = _make_page(send_still_visible=True)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["send"].click.assert_called_once()


class TestSendConnectionRequestRefusesToSendSomethingElse:
    def test_returns_false_without_sending_when_the_page_truncated_the_note(self):
        # LinkedIn's invite textarea carries maxlength=300, so fill() with a
        # longer note lands silently clipped mid-word. Refuse rather than
        # transmit text the user never reviewed.
        page, locs = _make_page(filled=NOTE[:30])
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["note"].fill.assert_called_once_with(NOTE)
        locs["send"].click.assert_not_called()

    def test_returns_false_when_the_note_textarea_cannot_be_revealed(self):
        # Without a textarea the only way to proceed is a noteless invite,
        # which is not what was approved.
        page, locs = _make_page(note_area=False)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["send"].click.assert_not_called()

    def test_returns_false_when_the_send_control_is_absent(self):
        page, locs = _make_page(send=False)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["send"].click.assert_not_called()

    def test_fills_the_note_even_when_the_add_note_button_is_absent(self):
        # Some invite modals open with the textarea already showing. If the
        # textarea is reachable, the absence of the button is not a failure.
        page, locs = _make_page(add_note=False)
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is True
        locs["add_note"].click.assert_not_called()
        locs["note"].fill.assert_called_once_with(NOTE)


class TestSendConnectionRequestFailures:
    def test_returns_false_for_a_url_it_cannot_handle_without_navigating(self):
        page, locs = _make_page()
        assert LinkedInConnector().send_connection_request(
            page, "https://www.linkedin.com/jobs/view/1", NOTE
        ) is False
        page.goto.assert_not_called()
        _assert_nothing_was_submitted(locs)

    def test_returns_false_when_navigation_raises(self):
        page, locs = _make_page()
        page.goto.side_effect = Exception("net::ERR_ABORTED")
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        page.locator.assert_not_called()

    def test_returns_false_when_a_state_probe_raises(self):
        # An indeterminate page state must not fall through to sending:
        # the output of this method is visible to another human.
        page, locs = _make_page()
        # count() is reached unconditionally; is_visible() would be
        # short-circuited away when the badge is absent.
        locs["pending"].count.side_effect = Exception("detached")
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        locs["connect"].click.assert_not_called()
        _assert_nothing_was_submitted(locs)

    def test_returns_false_when_the_connect_click_raises(self):
        page, locs = _make_page()
        locs["connect"].click.side_effect = Exception("click failed")
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False
        _assert_nothing_was_submitted(locs)

    def test_returns_false_when_the_send_click_raises(self):
        page, locs = _make_page()
        locs["send"].click.side_effect = Exception("click failed")
        assert LinkedInConnector().send_connection_request(page, PROFILE, NOTE) is False


class TestSelectorSafety:
    def test_the_six_probed_selectors_are_distinct(self):
        selectors = [
            _PENDING_SELECTOR, _CONNECTED_SELECTOR, _CONNECT_SELECTOR,
            _ADD_NOTE_SELECTOR, _NOTE_SELECTOR, _SEND_SELECTOR,
        ]
        assert len(set(selectors)) == len(selectors)

    @pytest.mark.parametrize("selector", [
        _PENDING_SELECTOR, _CONNECTED_SELECTOR, _CONNECT_SELECTOR,
    ])
    def test_profile_state_selectors_are_scoped_to_the_top_card(self, selector):
        # Unscoped, `button:has-text("Connect")` can resolve to a Connect
        # button in a "People also viewed" card and invite the wrong person.
        # Every comma-separated alternative must carry the scope.
        for alternative in selector.split(","):
            assert alternative.strip().startswith("main "), alternative

    def test_the_send_selector_cannot_match_send_without_a_note(self):
        # The invite modal shows "Send without a note" next to the confirm
        # button. A `:has-text("Send")` match would hit it and transmit the
        # invite with the reviewed note discarded, so substring matching is
        # forbidden here.
        assert ":has-text" not in _SEND_SELECTOR


class TestNoPresentationLeak:
    """`careeros/operations/` is AST-purity-enforced, but that guard only

    parses that package. The operations layer calls this connector, so a
    print here would reach a non-CLI caller — the exact defect GenericFiller
    shipped with. Enforced for this package too rather than trusted.
    """

    MODULES = sorted(
        (Path(__file__).resolve().parent.parent / "careeros" / "browser" / "connect").glob("**/*.py")
    )

    def test_there_is_something_to_check(self):
        # Guards against this class silently passing on an empty glob.
        assert len(self.MODULES) >= 2

    def test_no_presentation_library_imports(self):
        forbidden = {"rich", "typer", "click"}
        for path in self.MODULES:
            imported = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            assert not (imported & forbidden), path.name + " imports " + str(imported & forbidden)

    def test_no_print_input_or_exit_calls(self):
        for path in self.MODULES:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in ("print", "input"), path.name + " calls " + node.func.id
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    is_sys_exit = (
                        node.func.attr == "exit"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "sys"
                    )
                    assert not is_sys_exit, path.name + " calls sys.exit()"


class TestShape:
    def test_platform_label(self):
        assert LinkedInConnector.platform == "LinkedIn"

    def test_is_not_a_filler(self):
        # The plan is explicit: a connector must not subclass or implement
        # Filler, whose fill() signature takes a job, a profile and three
        # file paths.
        from careeros.browser.fillers.base import Filler

        assert Filler not in LinkedInConnector.__mro__
        assert not hasattr(LinkedInConnector, "fill")


class TestAnEmptyNoteIsRefused:
    """A noteless invite is a different act from the approved one.

    The approval binds a note digest, so an empty note means the note was
    lost between drafting and sending. LinkedIn would send the bare invite
    and it cannot be recalled, so this refuses at the point of action rather
    than trusting the drafter's own validation — the same reasoning that has
    execute_follow_up re-check a closed relationship propose already checked.
    """

    @pytest.mark.parametrize("note", ["", "   ", "\n\t "], ids=["empty", "spaces", "whitespace"])
    def test_a_blank_note_sends_nothing(self, note):
        page = MagicMock()
        sent = LinkedInConnector().send_connection_request(
            page, "https://www.linkedin.com/in/jane-doe", note,
        )
        assert sent is False
        # Refused before navigation, so nothing about the page was touched.
        page.goto.assert_not_called()
