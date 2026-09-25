from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

_HOST = "linkedin.com"
_PROFILE_PREFIX = "/in/"

_NAV_TIMEOUT_MS = 20000
# After clicking Connect / Add a note the invite modal animates in; after
# clicking Send the modal is replaced by a toast. The second wait is longer
# because that one is a network round-trip, not a local transition.
_MODAL_WAIT_MS = 1500
_CONFIRM_WAIT_MS = 4000

# Every profile-state probe is scoped to the top card's action bar. A
# LinkedIn profile page also renders "People also viewed" / "More profiles
# for you" cards, each carrying its own Connect button and degree badge, so
# an unscoped `button:has-text("Connect")` can resolve to a *different human
# being* — the one failure mode here that no later check could catch, since
# the invite would go out correctly formed to the wrong person.
_TOP_CARD = "main .pv-top-card, main .pv-top-card-v2-ctas, main .pvs-profile-actions"


def _scoped(suffix: str) -> str:
    return ", ".join(scope + " " + suffix for scope in _TOP_CARD.split(", "))


# "Pending" is the label LinkedIn gives the button once an invitation is
# outstanding (its aria-label offers to withdraw). The degree badge reading
# "1st" is the signal for an existing connection; matching on the text as
# well as the class means a 2nd- or 3rd-degree badge does not count.
_PENDING_SELECTOR = _scoped('button:has-text("Pending")')
_CONNECTED_SELECTOR = (
    _scoped('.dist-value:has-text("1st")')
    + ", "
    + _scoped('.distance-badge:has-text("1st")')
)
_CONNECT_SELECTOR = _scoped('button:has-text("Connect")')

# Inside the invite modal, which is rendered in a portal outside <main>, so
# these two are deliberately unscoped.
_ADD_NOTE_SELECTOR = 'button[aria-label="Add a note"], button:has-text("Add a note")'
_NOTE_SELECTOR = 'textarea#custom-message, textarea[name="message"]'
# Exact-text / aria-label matching only. `button:has-text("Send")` would
# also match "Send without a note", which sits right beside the confirm
# button in the modal — clicking it transmits the invitation with the
# reviewed note silently discarded.
_SEND_SELECTOR = (
    'button[aria-label="Send now"], button[aria-label="Send invitation"], '
    'button:text-is("Send")'
)


def _visible(page: Page, selector: str) -> bool:
    locator = page.locator(selector).first
    return locator.count() > 0 and locator.is_visible()


class LinkedInConnector:
    """Sends one LinkedIn connection request with a note, on a live page.

    Deliberately not a `Filler` and not an implementation of that Protocol:
    `Filler.fill` takes a job, a profile and three file paths, none of which
    mean anything here. The two share only the shape of the contract —
    `False` for an outcome, an exception for nothing.

    Nothing in this class prints or prompts. `careeros/operations/` is
    purity-enforced by an AST guard, but that guard parses only that
    package, so a print here would leak straight through the operations
    layer to a non-CLI caller.
    """

    platform = "LinkedIn"

    def can_handle(self, linkedin_url: str) -> bool:
        """Pure URL pattern check — no browser needed. Must be deterministic.

        Stricter than the fillers' substring checks, on purpose. A filler
        that matches the wrong URL merely fails to fill a form; a wrong
        match here sends a message to a person. So the host is parsed and
        compared rather than searched for, which rejects both
        `linkedin.com.evil.example/in/x` and a linkedin.com URL embedded in
        someone else's path.
        """
        try:
            parsed = urllib.parse.urlparse(linkedin_url)
        except ValueError:
            # urlparse raises on a malformed authority (an unterminated IPv6
            # literal, say). Unparseable is not handleable.
            return False
        host = parsed.netloc.lower().rsplit("@", 1)[-1].rsplit(":", 1)[0]
        if host != _HOST and not host.endswith("." + _HOST):
            return False
        if not parsed.path.startswith(_PROFILE_PREFIX):
            return False
        # `/in/` on its own names nobody. Require a slug.
        return bool(parsed.path[len(_PROFILE_PREFIX):].strip("/"))

    def send_connection_request(self, page: Page, linkedin_url: str, note: str) -> bool:
        """Invite the person at linkedin_url, attaching note.

        Returns True only when the invitation was sent. Returns False —
        rather than raising — for every ordinary reason the request cannot
        be completed: already connected, an invitation already pending, no
        Connect button on the page. Those are page states a user will hit
        routinely, not errors, and the caller maps False to
        `ConnectionNotSent` the way a filler's False maps to
        `FillIncomplete`.
        """
        # Re-checked rather than trusted to the caller: every path below
        # drives a real browser against whatever this URL points at.
        if not self.can_handle(linkedin_url):
            return False

        # A noteless invitation is a different act from the one that was
        # approved. The whole approval binds a note digest, so an empty note
        # means the caller lost the note somewhere between drafting and here
        # — and LinkedIn would happily send the bare invite, which cannot be
        # recalled. Refused at the point of action rather than trusted to the
        # drafter's own validation, for the same reason execute_follow_up
        # re-checks a closed relationship that propose already checked.
        if not note.strip():
            return False

        try:
            page.goto(linkedin_url, timeout=_NAV_TIMEOUT_MS)
        except Exception:
            return False

        try:
            # Read the positive no-send signals before looking for a Connect
            # button. Both states leave the top card without one, so the
            # absence check would catch them anyway — but reading the
            # explicit signal means a stale or offscreen Connect node cannot
            # be mistaken for an invitable profile.
            if _visible(page, _PENDING_SELECTOR):
                return False
            if _visible(page, _CONNECTED_SELECTOR):
                return False

            connect = page.locator(_CONNECT_SELECTOR).first
            if connect.count() == 0 or not connect.is_visible():
                return False
            connect.click()
            page.wait_for_timeout(_MODAL_WAIT_MS)
        except Exception:
            # A probe or click that raises means the page state could not be
            # established. For a writer whose output is visible to another
            # person, indeterminate must not fall through to sending.
            return False

        try:
            # The modal opens with "Add a note" and "Send without a note"
            # side by side; some variants show the textarea already. Click
            # the button when it is there, then insist on the textarea.
            add_note = page.locator(_ADD_NOTE_SELECTOR).first
            if add_note.count() > 0 and add_note.is_visible():
                add_note.click()
                page.wait_for_timeout(_MODAL_WAIT_MS)

            note_area = page.locator(_NOTE_SELECTOR).first
            if note_area.count() == 0 or not note_area.is_visible():
                # No textarea means the only way forward is a noteless
                # invite, which is not what was reviewed and approved.
                # LinkedIn withholds notes once the free-invite allowance
                # for the month is spent, so this is a real state.
                return False

            note_area.fill(note)
            # The textarea carries maxlength=300, so fill() with a longer
            # note lands silently clipped — a sentence stopping mid-word,
            # delivered to a human. Read back what the page actually holds
            # and refuse. The drafting skill validates the cap too; this is
            # the last check before transmission, where truncation is the
            # page's doing rather than ours.
            if note_area.input_value() != note:
                return False
        except Exception:
            return False

        try:
            send = page.locator(_SEND_SELECTOR).first
            if send.count() == 0 or not send.is_visible():
                return False
            send.click()
        except Exception:
            return False

        # Same post-submit verification as GreenhouseFiller, and for the
        # same reason: the click has already fired, so an exception while
        # verifying is not evidence that nothing was sent. Answering False
        # here would leave the record saying "not sent" while the recipient
        # sees an invitation — and the next run would send a second one,
        # which is also visible to them.
        try:
            page.wait_for_timeout(_CONFIRM_WAIT_MS)
            send_still_present = send.is_visible()
        except Exception:
            send_still_present = False

        return not send_still_present
