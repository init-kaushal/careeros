import json
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli import outreach_cmd
from careeros.cli.outreach_cmd import (
    CLOSE_CMD_ACTION_LABEL, CONNECT_CMD_ACTION_LABEL, FOLLOW_UP_CMD_ACTION_LABEL,
    MAX_REGENERATIONS, REVIEW_CMD_ACTION_LABEL, outreach_app,
)
from careeros.core.models import (
    Approval, Company, ConnectionRequest, Job, Person, Profile,
)
from careeros.operations.approvals import (
    APPROVED, DECLINED, EXECUTED, PENDING, SUPERSEDED, list_by_state, list_pending,
)
from careeros.operations.connect import ACTION as CONNECT_ACTION
from careeros.operations.errors import ConnectionAlreadySent
from careeros.operations.outreach import make_message_id
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
REQUEST_ID = make_message_id(JOB_ID, PERSON_ID)
CANONICAL = "https://www.linkedin.com/in/jane-doe"

NOTE = (
    "Hi Jane — I work on SRE tooling and saw the Senior SRE role at Acme. "
    "Would like to connect."
)
SECOND_NOTE = "Hi Jane — a second, better note about the Senior SRE role."

# A note shaped the way an LLM actually writes one. Every bracketed span
# below is *deleted* from the display when Rich markup is on, while the raw
# stored bytes are what LinkedInConnector types into the invite modal.
BRACKETED_NOTE = (
    "Hi Jane — I saw the [posting](https://x.com/job) you shared and am "
    "[available] from June. My CV is [resume.pdf]. Would like to connect."
)
# And the one that does not merely lose characters: it looks like a closing
# tag, so Text.from_markup raises rich.errors.MarkupError, which is not an
# OperationError and would therefore escape the command's handlers entirely.
CLOSING_TAG_NOTE = "Hi Jane — I am a [/b] strong fit for the Senior SRE role."


def _unwrapped(output: str) -> str:
    """Rich hard-wraps console output at the terminal width, splitting even a
    short bracketed span across two lines. Collapse it before matching."""
    return " ".join(output.split())


def _setup(tmp_path, *, linkedin_url=CANONICAL, person_name="Jane Doe"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name=person_name, role_category="em",
           title="Engineering Manager", linkedin_url=linkedin_url,
           researched_at=now).save(storage)
    return str(tmp_path), storage


class _FakeBrowser:
    """Stands in for `launch_browser`, and counts whether it was entered.

    Patched at `careeros.operations.connect.launch_browser` — the name that
    module bound with a `from ... import` at import time. Patching
    `careeros.browser.driver.launch_browser` instead would leave that
    binding pointing at the real function, so every `entered == 0` assertion
    below would pass without proving anything. conftest's autouse
    `sync_playwright` guard is a second barrier behind this one, not the one
    these tests rely on.
    """

    def __init__(self, teardown_exc=None):
        self.entered = 0
        self.kwargs = None
        self.teardown_exc = teardown_exc

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    def __enter__(self):
        self.entered += 1
        return (MagicMock(), MagicMock())

    def __exit__(self, *exc_info):
        if self.teardown_exc is not None:
            raise self.teardown_exc
        return False


@contextmanager
def _patched(
    *, note=NOTE, notes=None, sent=True, teardown_exc=None, authorized=True,
    confirm=True, sent_request_to=None,
):
    """Every collaborator this command reaches, patched where it is bound.

    `confirm=None` leaves `Confirm.ask` alone, which is what the two
    real-prompt tests need: patching it with a Mock means neither its
    `default=` nor its yes/no parsing ever executes.
    """
    browser = _FakeBrowser(teardown_exc)
    connector = MagicMock()
    connector.send_connection_request.return_value = sent
    generate = (
        MagicMock(side_effect=list(notes)) if notes is not None
        else MagicMock(return_value=note)
    )
    with ExitStack() as stack:
        stack.enter_context(patch(
            "careeros.operations.connect.check_board_sessions",
            return_value={"linkedin": authorized},
        ))
        stack.enter_context(patch(
            "careeros.operations.connect.generate_connection_note", generate,
        ))
        stack.enter_context(patch(
            "careeros.operations.connect.launch_browser", browser,
        ))
        stack.enter_context(patch(
            "careeros.operations.connect.CONNECTOR", connector,
        ))
        if sent_request_to is not None:
            stack.enter_context(patch(
                "careeros.operations.connect._sent_request_to",
                side_effect=list(sent_request_to),
            ))
        if confirm is not None:
            stack.enter_context(patch(
                "careeros.runtime.local.Confirm.ask", return_value=confirm,
            ))
        yield browser, connector, generate


def _invoke(ws_path, **kwargs):
    return runner.invoke(
        outreach_app,
        ["connect", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path],
        **kwargs,
    )


def _log_events(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = "activity/" + date + ".jsonl"
    if not storage.exists(path):
        return []
    return [json.loads(line) for line in storage.read(path).decode().splitlines() if line]


def _connect_approvals(storage):
    found = []
    for state in (PENDING, APPROVED, DECLINED, EXECUTED, SUPERSEDED, "failed"):
        found += [a for a in list_by_state(storage, state) if a.action == CONNECT_ACTION]
    return found


def _only_connect_approval(storage, state):
    matching = [a for a in _connect_approvals(storage) if a.state == state]
    assert len(matching) == 1, [(a.id, a.state) for a in _connect_approvals(storage)]
    return matching[0]


class TestConnectHappyPath:
    def test_one_request_is_sent_and_the_approval_is_marked_executed(self, tmp_path):
        """Driven through the real prompts, so `choices=` and `default=` run.

        `Prompt.ask` and `Confirm.ask` are both left unpatched here: a Mock
        in either place means neither the choice validation nor the
        confirmation default ever executes, which is how four
        safety-relevant regressions shipped green in the previous phase.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched(confirm=None) as (browser, connector, generate):
            result = _invoke(ws_path, input="a\ny\n")

        assert result.exit_code == 0, result.output
        assert generate.call_count == 1
        # Exactly one invitation, with exactly the reviewed bytes, to exactly
        # the canonical profile URL bound into the approval.
        assert connector.send_connection_request.call_count == 1
        args = connector.send_connection_request.call_args[0]
        assert args[1] == CANONICAL
        assert args[2] == NOTE
        assert browser.entered == 1

        approval = _only_connect_approval(storage, EXECUTED)
        assert approval.entity_id == REQUEST_ID
        record = ConnectionRequest.load(storage, REQUEST_ID)
        assert record.send_state == "sent"
        assert record.sent_at
        assert record.note_text == NOTE
        assert "Jane Doe" in _unwrapped(result.output)

    def test_the_browser_that_sends_is_headful(self, tmp_path):
        """§6.4: the user watches the invitation being sent the first time.

        The command gets this for free — `execute_connection_request` passes
        `headless=False` explicitly rather than relying on launch_browser's
        default — so this asserts the property the command is responsible
        for delivering, not a parameter it passes.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched() as (browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        assert browser.kwargs == {"headless": False}


class TestTheNoteShownIsTheNoteSent:
    """The reviewed bytes must be the transmitted bytes.

    This command is the exact case this project's worst bug lived in: the
    note is displayed for approval and then typed into LinkedIn's invite
    modal. With Rich's default markup on, a bracketed span handed to `Panel`
    is deleted from the display while the stored bytes go out unchanged, so
    the reviewer approves a note that is not the note the recipient reads.
    """

    def test_every_bracketed_span_survives_to_the_screen(self, tmp_path):
        ws_path, storage = _setup(tmp_path)

        with _patched(note=BRACKETED_NOTE) as (_browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        shown = _unwrapped(result.output)
        # Asserted span by span rather than as one substring, because Rich
        # wraps the panel body: it is the *characters* that must survive, and
        # each of these is deleted outright when markup is on. A
        # bracket-free substring would pass with the bug present, which is
        # what gave false confidence the last time this was checked.
        for span in ("[posting]", "[available]", "[resume.pdf]"):
            assert span in shown, span + " was eaten by rich markup"
        # And the bytes that went out are the bytes that were on screen.
        assert connector.send_connection_request.call_args[0][2] == BRACKETED_NOTE

    def test_a_closing_tag_shaped_note_does_not_crash_the_command(self, tmp_path):
        """MarkupError fires at print time, before the prompt is even shown.

        It is not an OperationError, so it escapes every handler in the
        command: with markup on, the user gets a traceback instead of a note
        to approve or reject.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched(note=CLOSING_TAG_NOTE) as (_browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "[/b]" in _unwrapped(result.output)
        assert connector.send_connection_request.call_args[0][2] == CLOSING_TAG_NOTE

    def test_a_bracketed_span_in_the_profile_url_survives_to_the_screen(self, tmp_path):
        """The URL on screen is the profile that will actually be navigated.

        `canonical_linkedin_url` preserves the slug's own characters — a
        vanity slug's case is untouched, and so is a bracket in it — so a
        "[bold]"-shaped span in the stored URL is deleted from the display
        while `execute_connection_request` navigates the stored bytes. The
        user would approve an invitation to a profile they were never shown.
        """
        bracketed_url = "https://www.linkedin.com/in/jane[bold]doe"
        ws_path, storage = _setup(tmp_path, linkedin_url=bracketed_url)

        with _patched() as (_browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        # Matched on the review line itself, not anywhere in the output. The
        # success line at the end also carries the URL, so a bare
        # `"[bold]" in output` passed with the review line's protection
        # removed — the same false confidence a bracket-free substring gives.
        profile_lines = [
            line for line in result.output.splitlines() if "Profile:" in line
        ]
        assert profile_lines, result.output
        assert "[bold]" in profile_lines[0]
        assert connector.send_connection_request.call_args[0][1] == bracketed_url

    def test_a_closing_tag_in_the_panel_title_does_not_crash_the_command(self, tmp_path):
        """The title is external data too, and raises identically.

        It is built from the person's name, which `careeros research people`
        scrapes off a LinkedIn search page — so it is no more trustworthy
        than the drafted note, and `verbatim_panel_args` exists precisely so
        a call site cannot protect the body and forget the title.

        Scoped to the review display, and deliberately not more than that:
        `Confirm.ask` is patched here, and with it real this run would still
        raise MarkupError — `LocalRuntime.request_approval` hands the
        approval summary to `Confirm.ask` as a raw str, so a "[/b]"-shaped
        person name or company takes the confirmation prompt down. That is
        pre-existing and shared: `outreach send` and `apply` reach the same
        line and fail identically, verified by reproducing it. Fixing it
        means changing `careeros/runtime/local.py`, which is outside this
        command and needs its own tests for those two callers.
        """
        ws_path, storage = _setup(tmp_path, person_name="Jane [/b] Doe")

        with _patched() as (_browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Jane [/b] Doe" in _unwrapped(result.output)
        assert connector.send_connection_request.call_count == 1


class TestRefusingToSend:
    def test_declining_the_gate_resolves_declined_and_opens_no_browser(self, tmp_path):
        ws_path, storage = _setup(tmp_path)

        with _patched(confirm=False) as (browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        assert browser.entered == 0
        connector.send_connection_request.assert_not_called()

        approval = _only_connect_approval(storage, DECLINED)
        assert approval.entity_id == REQUEST_ID
        assert ConnectionRequest.load(storage, REQUEST_ID).send_state == "declined"
        events = {event["event_type"] for event in _log_events(storage)}
        assert "connection_request_declined" in events
        assert "connection_request_sent" not in events

    def test_quitting_declines_rather_than_leaving_the_approval_pending(self, tmp_path):
        """Phase 12's finding: an abandoned review must not strand `pending`.

        A pending approval is the one state something else can still act on
        — `careeros approvals` lists it, and an agent draining the queue
        could execute it — so walking away from the review has to close it.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched() as (browser, connector, _generate):
            result = _invoke(ws_path, input="q\n")

        assert result.exit_code == 0, result.output
        assert browser.entered == 0
        connector.send_connection_request.assert_not_called()
        assert not [a for a in list_pending(storage) if a.action == CONNECT_ACTION]

        approval = _only_connect_approval(storage, DECLINED)
        assert approval.entity_id == REQUEST_ID
        assert ConnectionRequest.load(storage, REQUEST_ID).send_state == "declined"

    def test_a_stray_enter_at_the_review_prompt_cannot_send(self, tmp_path):
        """Both prompts real, both defaults exercised.

        The review prompt defaults to accept — the user got here by naming
        this one person, exactly as in `outreach send` — but accepting only
        reaches the approval gate, whose `Confirm.ask(default=False)` says
        no. So two stray Enters decline; they do not send an invitation into
        somebody's notifications.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched(confirm=None) as (browser, connector, _generate):
            result = _invoke(ws_path, input="\n\n")

        assert result.exit_code == 0, result.output
        assert browser.entered == 0
        connector.send_connection_request.assert_not_called()
        assert _only_connect_approval(storage, DECLINED).entity_id == REQUEST_ID

    def test_an_unrecognised_keystroke_is_not_read_as_accept(self, tmp_path):
        """`choices=` is what makes anything but a/r/q re-prompt.

        Without it the review loop's final `break` is reached by every
        keystroke that is not "q" or "r", so a typo or a stray paste is read
        as accept and carries an irreversible invitation to the approval
        gate. A Mock in place of `Prompt.ask` never runs the validation at
        all, which is why this test drives the real prompt.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched(confirm=None) as (browser, connector, _generate):
            result = _invoke(ws_path, input="x\nq\n")

        assert result.exit_code == 0, result.output
        assert browser.entered == 0
        connector.send_connection_request.assert_not_called()
        # Rich's own rejection line, which is printed only when choices= is set.
        assert "select one of the available options" in _unwrapped(result.output)
        assert _only_connect_approval(storage, DECLINED).entity_id == REQUEST_ID


class TestRegeneration:
    def test_the_regenerated_note_is_the_one_that_sends(self, tmp_path):
        ws_path, storage = _setup(tmp_path)

        with _patched(notes=[NOTE, SECOND_NOTE]) as (_browser, connector, generate):
            result = _invoke(ws_path, input="r\na\n")

        assert result.exit_code == 0, result.output
        assert generate.call_count == 2
        # The note on screen when the user accepted is the note transmitted,
        # and the stored record agrees.
        assert connector.send_connection_request.call_args[0][2] == SECOND_NOTE
        assert ConnectionRequest.load(storage, REQUEST_ID).note_text == SECOND_NOTE
        shown = _unwrapped(result.output)
        assert SECOND_NOTE in shown

        # The first proposal's approval was superseded by the re-propose, so
        # the stale id can never execute against the replaced note.
        assert _only_connect_approval(storage, EXECUTED)
        assert [a for a in _connect_approvals(storage) if a.state == SUPERSEDED]

    def test_regenerate_is_withdrawn_after_MAX_REGENERATIONS(self, tmp_path):
        """The shared cap, not a second literal.

        `MAX_REGENERATIONS` bounds paid LLM calls for `apply`, `send` and
        `review` alike, and this command reuses it rather than picking its
        own number. Asserted through the real prompt, so the narrowed
        `choices=["a", "q"]` is what rejects the sixth "r".
        """
        ws_path, storage = _setup(tmp_path)
        notes = ["Hi Jane — draft number " + str(i) for i in range(MAX_REGENERATIONS + 2)]

        with _patched(notes=notes, confirm=None) as (_browser, connector, generate):
            result = _invoke(ws_path, input="r\n" * MAX_REGENERATIONS + "r\na\ny\n")

        assert result.exit_code == 0, result.output
        # One initial draft plus MAX_REGENERATIONS redrafts, and no more.
        assert generate.call_count == MAX_REGENERATIONS + 1
        assert "select one of the available options" in _unwrapped(result.output)
        assert (
            connector.send_connection_request.call_args[0][2]
            == notes[MAX_REGENERATIONS]
        )


class TestConnectionAlreadySent:
    """The one refusal here that means "never again", not "try again".

    Raised from `propose_connection_request` *and* from
    `execute_connection_request` — the refusal exists at the point of action
    as well as at propose — so the command has to report it on both paths.
    """

    def _seed_prior_send(self, storage):
        prior_id = make_message_id("other-job-xyz9", PERSON_ID)
        ConnectionRequest(
            id=prior_id, job_id="other-job-xyz9", person_id=PERSON_ID,
            linkedin_url=CANONICAL, note_text="An earlier note.",
            send_state="sent", sent_at="2026-09-01T10:00:00+00:00",
        ).save(storage)
        return prior_id

    def test_a_refusal_at_propose_is_reported_and_nothing_is_proposed(self, tmp_path):
        ws_path, storage = _setup(tmp_path)
        prior_id = self._seed_prior_send(storage)

        with _patched() as (browser, connector, generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert browser.entered == 0
        connector.send_connection_request.assert_not_called()
        # Refused before the LLM is called, so nothing was paid for either.
        generate.assert_not_called()
        assert not _connect_approvals(storage)

        shown = _unwrapped(result.output)
        assert "already sent" in shown
        assert prior_id in shown
        # The permanence is the part that distinguishes this from every
        # other refusal, so it has to be said in words.
        assert "permanent" in shown.lower()
        assert "Nothing was sent" in shown

    def test_a_refusal_at_execute_is_reported_and_nothing_is_sent(self, tmp_path):
        """The race the propose-time check cannot close.

        `_sent_request_to` answers None for propose and finds a prior send
        for execute, which is what another process sending in between looks
        like from here. Task 5 added the refusal at the point of action for
        exactly this, and consuming the approval happens *after* it, so the
        browser is never reached.
        """
        ws_path, storage = _setup(tmp_path)
        prior = ConnectionRequest(
            id=make_message_id("other-job-xyz9", PERSON_ID), job_id="other-job-xyz9",
            person_id=PERSON_ID, linkedin_url=CANONICAL, note_text="An earlier note.",
            send_state="sent", sent_at="2026-09-01T10:00:00+00:00",
        )

        with _patched(sent_request_to=[None, prior]) as (browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert browser.entered == 0
        connector.send_connection_request.assert_not_called()

        shown = _unwrapped(result.output)
        assert "already sent" in shown
        assert "permanent" in shown.lower()
        assert "Nothing was sent" in shown
        # This refusal fires before mark_executed, so the approval is left
        # `approved` and nothing claims an invitation went out.
        assert _only_connect_approval(storage, APPROVED)
        assert ConnectionRequest.load(storage, REQUEST_ID).sent_at is None


class TestTeardownFailure:
    def test_a_teardown_failure_is_warned_about_not_called_plain_success(self, tmp_path):
        """The invitation went out; closing the browser afterwards did not.

        Reporting this as unqualified success would hide a durable anomaly,
        and reporting it as a failure would be a lie — the request is in
        somebody's notifications. `ApplyResult.teardown_failed` has the same
        contract and `apply` words it the same way.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched(teardown_exc=RuntimeError("target page closed")) as (
            browser, connector, _generate,
        ):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        assert connector.send_connection_request.call_count == 1
        shown = _unwrapped(result.output)
        assert "Warning" in shown
        assert "teardown" in shown
        # Still reported as sent, because it was.
        record = ConnectionRequest.load(storage, REQUEST_ID)
        assert record.send_state == "sent"
        assert record.sent_at
        events = {event["event_type"] for event in _log_events(storage)}
        assert "connection_request_teardown_failed" in events


class TestUnusableLinkedInUrl:
    def test_the_exceptions_own_remedy_is_printed_verbatim(self, tmp_path):
        """EntityNotFound carries the remedy, and it embeds what was pasted.

        The message names `careeros people update --linkedin-url`, which is
        the whole value of it, so the command prints `str(exc)` rather than
        wording of its own. It prints it *verbatim*, because the
        unusable-URL variant embeds `repr()` of whatever the user stored: a
        bracketed span there is deleted from the display, showing them a URL
        they never pasted, and a closing-tag-shaped one raises MarkupError.
        """
        ws_path, storage = _setup(
            tmp_path, linkedin_url="https://example.com/[profile]/jane",
        )

        with _patched() as (browser, connector, generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert browser.entered == 0
        generate.assert_not_called()

        shown = _unwrapped(result.output)
        assert "careeros people update" in shown
        assert "--linkedin-url" in shown
        assert "[profile]" in shown, "the pasted URL was eaten by rich markup"

    def test_a_missing_url_names_the_flag_that_sets_one(self, tmp_path):
        ws_path, storage = _setup(tmp_path, linkedin_url=None)

        with _patched() as (browser, connector, generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 1
        assert browser.entered == 0
        generate.assert_not_called()
        shown = _unwrapped(result.output)
        assert "careeros people update " + PERSON_ID + " --linkedin-url" in shown


class TestOtherFlowsApprovalsAreUntouched:
    def test_a_pending_approval_from_another_action_survives_the_send(self, tmp_path):
        """Scoped to this flow's own approval id, like every sibling command.

        The `send_outreach` approval seeded here deliberately carries the
        *same* entity_id, so an action-blind supersede or resolve would
        reach it. It belongs to another reviewer and must come out of this
        run exactly as it went in.
        """
        ws_path, storage = _setup(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        Approval(
            id="send-outreach-other-aaa111", action="send_outreach",
            summary="Send an outreach email to Jane Doe?", state=PENDING,
            entity_type="outreach_message", entity_id=REQUEST_ID,
            payload={"person_id": PERSON_ID, "job_id": JOB_ID},
            created_at=now,
        ).save(storage)

        with _patched() as (_browser, connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        assert connector.send_connection_request.call_count == 1
        other = Approval.load(storage, "send-outreach-other-aaa111")
        assert other.state == PENDING
        assert other.decided_at is None
        assert other.reason is None

    def test_a_pending_approval_from_another_action_survives_a_quit(self, tmp_path):
        ws_path, storage = _setup(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        Approval(
            id="apply-to-job-other-bbb222", action="apply_to_job",
            summary="Apply to Acme Corp — Senior SRE?", state=PENDING,
            entity_type="job", entity_id=JOB_ID, payload={"job_id": JOB_ID},
            created_at=now,
        ).save(storage)

        with _patched() as (browser, _connector, _generate):
            result = _invoke(ws_path, input="q\n")

        assert result.exit_code == 0, result.output
        assert browser.entered == 0
        assert Approval.load(storage, "apply-to-job-other-bbb222").state == PENDING


class TestAuditTrailProperties:
    def test_every_event_carries_this_commands_own_action_label(self, tmp_path):
        """A distinct label is what separates this entry point from the rest.

        Its value is load-bearing, not cosmetic: `outreach send`,
        `outreach follow-up`, `outreach review` and `outreach close` each
        stamp their own, and folding this one under any of them would make
        the audit log unable to say which entry point put an invitation in
        somebody's notifications.
        """
        assert CONNECT_CMD_ACTION_LABEL == "outreach-connect"
        assert CONNECT_CMD_ACTION_LABEL not in (
            "outreach", FOLLOW_UP_CMD_ACTION_LABEL, REVIEW_CMD_ACTION_LABEL,
            CLOSE_CMD_ACTION_LABEL,
        )

        ws_path, storage = _setup(tmp_path)

        with _patched() as (_browser, _connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        events = _log_events(storage)
        assert events
        assert {event["action"] for event in events} == {CONNECT_CMD_ACTION_LABEL}

    def test_every_event_is_stamped_as_the_local_runtime(self, tmp_path):
        """Interactive, so `local` — not `automation`.

        This command opens a LocalRuntime, which is what makes the
        confirmation gate a real terminal prompt rather than an
        auto-approval, and the stamp is how the log records that a person
        was at the keyboard.
        """
        ws_path, storage = _setup(tmp_path)

        with _patched() as (_browser, _connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        events = _log_events(storage)
        assert events
        assert {event["agent_runtime"] for event in events} == {"local"}

    def test_the_decline_trail_is_stamped_the_same_way(self, tmp_path):
        ws_path, storage = _setup(tmp_path)

        with _patched(confirm=False) as (_browser, _connector, _generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 0, result.output
        declines = [
            event for event in _log_events(storage)
            if event["event_type"] == "connection_request_declined"
        ]
        assert len(declines) == 1
        assert declines[0]["action"] == CONNECT_CMD_ACTION_LABEL


class _RecordingConsole:
    """A console that remembers its `print` kwargs, then delegates.

    CliRunner strips colour, so a style is invisible in `result.output`.
    Recording the call is the only way to assert on one without asserting on
    ANSI escapes that Rich will not emit to a non-tty in the first place.
    """

    def __init__(self, real):
        self._real = real
        self.calls = []

    def print(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self._real.print(*args, **kwargs)


class TestConnectionNotSent:
    def test_an_ordinary_non_completion_is_reported_as_a_refusal(self, tmp_path):
        """The connector's False — already connected, no Connect button.

        `ConnectionNotSent` is shaped like `FillIncomplete` and means the
        same thing, so it is reported the way `apply` reports that one: a
        refusal the user can act on, styled yellow, not a red error. The
        style is asserted because that is the *only* difference from the
        generic handler — without this the whole `except ConnectionNotSent`
        clause could be deleted with the suite still green.
        """
        ws_path, storage = _setup(tmp_path)
        recording = _RecordingConsole(outreach_cmd.console)

        with _patched(sent=False) as (browser, connector, _generate), \
                patch.object(outreach_cmd, "console", recording):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert browser.entered == 1
        assert connector.send_connection_request.call_count == 1
        shown = _unwrapped(result.output)
        assert "was not sent" in shown
        assert ConnectionRequest.load(storage, REQUEST_ID).sent_at is None
        assert "yellow" in [call.get("style") for call in recording.calls]


class TestSessionAndPolicyRefusals:
    def test_a_signed_out_linkedin_session_is_reported_before_drafting(self, tmp_path):
        ws_path, storage = _setup(tmp_path)

        with _patched(authorized=False) as (browser, connector, generate):
            result = _invoke(ws_path, input="a\n")

        assert result.exit_code == 1
        assert browser.entered == 0
        generate.assert_not_called()
        assert "linkedin" in _unwrapped(result.output).lower()


def test_connection_already_sent_is_an_operation_error_subclass():
    """The generic handler would swallow it if it were not caught first.

    Guards the ordering inside the command: `ConnectionAlreadySent` is an
    `OperationError`, so its own `except` clause has to come before the
    generic one or the permanence message never prints.
    """
    from careeros.operations.errors import OperationError
    assert issubclass(ConnectionAlreadySent, OperationError)
