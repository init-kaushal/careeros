import hashlib
import socket
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from careeros.browser.connect import LinkedInConnector
from careeros.browser.driver import BrowserProfileBusy
from careeros.core.models import (
    Approval, Company, ConnectionRequest, Job, Person, PolicyConfig, Profile,
)
from careeros.operations.approvals import PENDING, SUPERSEDED
from careeros.operations.connect import propose_connection_request
from careeros.operations.errors import (
    BoardSessionRequired, BrowserUnavailable, ConnectionAlreadySent, DraftFailed,
    EntityNotFound, PolicyBlocked,
)
from careeros.operations.outreach import make_message_id
from careeros.runtime.factory import open_local_runtime
from careeros.skills.connection_note import NOTE_CHAR_LIMIT
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
REQUEST_ID = make_message_id(JOB_ID, PERSON_ID)
NOTE = "Hi Jane — I work on SRE tooling and saw the Senior SRE role at Acme. Would like to connect."
CANONICAL = "https://www.linkedin.com/in/jane-doe"


def _runtime(tmp_path, linkedin_url=CANONICAL, session_id="sess-1"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
           title="Engineering Manager", linkedin_url=linkedin_url,
           researched_at=now).save(storage)
    return open_local_runtime(storage, session_id=session_id)


def _seed_request(storage, **overrides):
    fields = dict(
        id=REQUEST_ID, job_id=JOB_ID, person_id=PERSON_ID, linkedin_url=CANONICAL,
        note_text="An older note.", send_state="drafted", sent_at=None,
    )
    fields.update(overrides)
    ConnectionRequest(**fields).save(storage)


def _propose(runtime, note=NOTE, authorized=True, model=None):
    """Every propose in this file goes through here.

    Both patch targets are names `careeros.operations.connect` binds itself,
    never the origin modules: this module does `from ... import` at import
    time, so patching `careeros.browser.session.check_board_sessions` or
    `careeros.skills.connection_note.generate_connection_note` would leave
    the already-bound references untouched and every assertion below vacuous.
    """
    with patch("careeros.operations.connect.check_board_sessions",
               return_value={"linkedin": authorized}):
        with patch("careeros.operations.connect.generate_connection_note",
                   return_value=note):
            return propose_connection_request(
                runtime, JOB_ID, PERSON_ID, model=model, action_label="connect",
            )


def _log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


def _approvals(storage):
    return [p for p in storage.list("approvals/") if p.endswith(".json")]


def _connections(storage):
    return [p for p in storage.list("connections/") if p.endswith(".json")]


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class TestPatchTargetsAreReal:
    def test_this_module_binds_every_name_the_tests_patch(self):
        """Guards every patch in this file against being vacuous.

        `mock.patch` does raise on a missing attribute, so this is belt and
        braces — but the failure this documents is not a missing name, it is
        a name that moved back to being looked up on its origin module,
        which would make the assert_not_called assertions below silently
        stop proving anything.
        """
        import careeros.operations.connect as connect_mod

        for name in ("check_board_sessions", "generate_connection_note", "CONNECTOR"):
            assert hasattr(connect_mod, name), "connect.py no longer binds " + name


class TestProposeConnectionRequestHappyPath:
    def test_persists_the_record_logs_and_opens_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)

        proposal = _propose(runtime)

        record = ConnectionRequest.load(runtime.storage, REQUEST_ID)
        assert record.id == REQUEST_ID
        assert record.job_id == JOB_ID
        assert record.person_id == PERSON_ID
        assert record.linkedin_url == CANONICAL
        assert record.note_text == NOTE
        assert record.send_state == "drafted"
        assert record.sent_at is None

        assert "connection_note_drafted" in _log(runtime.storage)

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == PENDING
        assert approval.action == "send_connection_request"
        assert set(approval.payload.keys()) == {
            "person_id", "job_id", "linkedin_url", "note_sha256",
        }
        assert approval.payload["person_id"] == PERSON_ID
        assert approval.payload["job_id"] == JOB_ID
        assert approval.payload["linkedin_url"] == CANONICAL

        assert proposal.request_id == REQUEST_ID
        assert proposal.note_text == NOTE
        assert proposal.recipient_name == "Jane Doe"
        assert proposal.linkedin_url == CANONICAL
        assert "Jane Doe" in proposal.summary

    def test_the_persisted_note_and_the_payload_digest_agree(self, tmp_path):
        """The one binding execute_connection_request will re-verify.

        Asserted as "the digest of what was stored" rather than "the digest
        of the constant", so a propose that hashed one string and persisted
        another — the failure the digest exists to catch — cannot pass.
        """
        runtime = _runtime(tmp_path)

        proposal = _propose(runtime)

        record = ConnectionRequest.load(runtime.storage, REQUEST_ID)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.payload["note_sha256"] == _digest(record.note_text)
        assert approval.payload["note_sha256"] == _digest(NOTE)

    def test_the_payload_url_and_the_record_url_agree(self, tmp_path):
        # §6.3: this URL decides which human being is contacted, so the
        # record and the approval must not be able to disagree about it.
        runtime = _runtime(tmp_path, linkedin_url="www.linkedin.com/in/jane-doe/")

        proposal = _propose(runtime)

        record = ConnectionRequest.load(runtime.storage, REQUEST_ID)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.payload["linkedin_url"] == record.linkedin_url == CANONICAL

    def test_the_approval_is_bound_to_the_connection_request_entity(self, tmp_path):
        runtime = _runtime(tmp_path)

        proposal = _propose(runtime)

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.entity_type == "connection_request"
        assert approval.entity_id == REQUEST_ID

    def test_the_model_argument_is_forwarded_to_the_drafter(self, tmp_path):
        runtime = _runtime(tmp_path)

        with patch("careeros.operations.connect.check_board_sessions",
                   return_value={"linkedin": True}):
            with patch("careeros.operations.connect.generate_connection_note",
                       return_value=NOTE) as mock_gen:
                propose_connection_request(
                    runtime, JOB_ID, PERSON_ID, model="some-model",
                    action_label="connect",
                )

        assert mock_gen.call_args.kwargs["model"] == "some-model"

    def test_re_proposing_supersedes_the_prior_open_approval(self, tmp_path):
        runtime = _runtime(tmp_path)

        first = _propose(runtime)
        second = _propose(runtime, note="A completely different note.")

        assert Approval.load(runtime.storage, first.approval_id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.approval_id).state == PENDING
        record = ConnectionRequest.load(runtime.storage, REQUEST_ID)
        assert record.note_text == "A completely different note."


class TestEntitiesMustExist:
    @pytest.mark.parametrize("missing", ["job", "person", "company"])
    def test_a_missing_entity_raises_entity_not_found(self, tmp_path, missing):
        runtime = _runtime(tmp_path)
        paths = {
            "job": "jobs/" + JOB_ID + ".json",
            "person": "people/" + PERSON_ID + ".json",
            "company": "companies/" + COMPANY_ID + ".json",
        }
        runtime.storage.delete(paths[missing])

        with pytest.raises(EntityNotFound):
            _propose(runtime)


class TestLinkedInUrlIsRequired:
    @pytest.mark.parametrize("stored", [None, "", "   "])
    def test_an_absent_url_names_the_flag_that_can_set_it(self, tmp_path, stored):
        """The error has to name a remedy that exists.

        `--linkedin-url` was added to `careeros people update` in this phase
        precisely so this message is not a dead end: the field is otherwise
        only ever written by `careeros research people`, so a hand-added
        person could never acquire one. The exact spelling is asserted
        because a message naming a flag that does not parse is worse than no
        message at all.
        """
        runtime = _runtime(tmp_path, linkedin_url=stored)

        with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
            with pytest.raises(EntityNotFound) as exc:
                _propose(runtime)

        # A whitespace-only value is semantically absent, so it must get the
        # absent message rather than "has '   ' on file, which is not a
        # LinkedIn profile URL" — same remedy, but a worse explanation of a
        # record the user cannot see.
        assert "has no LinkedIn URL" in str(exc.value)
        assert "careeros people update" in str(exc.value)
        assert "--linkedin-url" in str(exc.value)
        assert PERSON_ID in str(exc.value)
        mock_gen.assert_not_called()
        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []


class TestLinkedInUrlNormalization:
    """A stored URL only has to be navigable at the moment it becomes load-bearing.

    `LinkedInConnector.can_handle` parses the host strictly and so answers
    False for a scheme-less string, which is deliberate and stays that way.
    But two storage paths write non-canonical values: `people update
    --linkedin-url` takes whatever the user pasted, and `careeros research
    people` stores the raw href the people-search scraper captured — which
    can be root-relative. So normalization happens here, before the URL is
    persisted onto the record or bound into the payload.
    """

    @pytest.mark.parametrize("stored", [
        # A scheme-less paste, the common case from `people update`.
        "www.linkedin.com/in/jane-doe",
        # A root-relative href, as the people-search scraper can capture it.
        "/in/jane-doe",
        # A protocol-relative href, the other shape HTML can carry.
        "//www.linkedin.com/in/jane-doe",
        # http must be upgraded, not navigated to.
        "http://www.linkedin.com/in/jane-doe",
        # Cosmetic variation around an already-valid URL.
        "https://www.linkedin.com/in/jane-doe/",
        "  https://www.linkedin.com/in/jane-doe  ",
        "https://WWW.LinkedIn.COM/in/jane-doe",
        # Tracking parameters and fragments identify nothing.
        "https://www.linkedin.com/in/jane-doe?miniProfileUrn=abc",
        "/in/jane-doe?trk=people_search",
        "https://www.linkedin.com/in/jane-doe#experience",
        # A sub-page of the profile: the slug is the identity, and the top
        # card the connector reads only renders on the profile root.
        "https://www.linkedin.com/in/jane-doe/recent-activity/all/",
    ])
    def test_a_non_canonical_url_is_canonicalized_everywhere_it_lands(
        self, tmp_path, stored,
    ):
        runtime = _runtime(tmp_path, linkedin_url=stored)

        proposal = _propose(runtime)

        record = ConnectionRequest.load(runtime.storage, REQUEST_ID)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert record.linkedin_url == CANONICAL
        assert approval.payload["linkedin_url"] == CANONICAL
        assert proposal.linkedin_url == CANONICAL

    @pytest.mark.parametrize("stored,expected", [
        # LinkedIn serves the same profile from uk./de./in. hosts, and
        # can_handle accepts them. Normalization must not quietly move the
        # request to a different host than the one on file, so www is not
        # forced on — which also means a bare linkedin.com stays bare.
        ("uk.linkedin.com/in/jane-doe", "https://uk.linkedin.com/in/jane-doe"),
        ("linkedin.com/in/jane-doe", "https://linkedin.com/in/jane-doe"),
        ("http://de.linkedin.com/in/jane-doe/", "https://de.linkedin.com/in/jane-doe"),
    ])
    def test_the_host_on_file_is_preserved_rather_than_rewritten(
        self, tmp_path, stored, expected,
    ):
        runtime = _runtime(tmp_path, linkedin_url=stored)

        proposal = _propose(runtime)

        assert proposal.linkedin_url == expected
        assert LinkedInConnector().can_handle(proposal.linkedin_url) is True

    def test_the_slug_keeps_its_own_case(self, tmp_path):
        # The host is lowercased because hosts are case-insensitive; a
        # vanity slug is not, so rewriting it would be a guess about a value
        # that decides which profile page is opened.
        runtime = _runtime(tmp_path, linkedin_url="WWW.LINKEDIN.COM/in/Jane-Doe")

        proposal = _propose(runtime)

        assert proposal.linkedin_url == "https://www.linkedin.com/in/Jane-Doe"

    def test_the_normalized_url_is_one_the_connector_will_actually_navigate(
        self, tmp_path,
    ):
        """Normalization is only worth anything if it lands inside can_handle.

        Asserted against the real connector rather than a restatement of the
        expected string, so the two cannot drift: if can_handle ever tightens
        further, this fails instead of shipping a record execute cannot use.
        """
        runtime = _runtime(tmp_path, linkedin_url="/in/jane-doe")

        proposal = _propose(runtime)

        assert LinkedInConnector().can_handle(proposal.linkedin_url) is True

    def test_an_already_canonical_url_is_left_byte_identical(self, tmp_path):
        runtime = _runtime(tmp_path)

        proposal = _propose(runtime)

        assert proposal.linkedin_url == CANONICAL

    @pytest.mark.parametrize("stored", [
        # Not a URL at all.
        "jane-doe",
        "not a url",
        # A LinkedIn-shaped path on somebody else's host.
        "https://example.com/in/jane-doe",
        "example.com/in/jane-doe",
        # The lookalike host can_handle exists to reject.
        "https://linkedin.com.evil.example/in/jane-doe",
        # LinkedIn, but not a person.
        "https://www.linkedin.com/company/acme",
        "https://www.linkedin.com/jobs/view/1234567",
        # "/in/" names nobody.
        "https://www.linkedin.com/in/",
        "/in/",
        # Not an http(s) URL. The last of these is the one that needs the
        # scheme check on its own: its host and path are both a valid
        # profile, so without that check it would be silently promoted to
        # https — a guess about a value the user typed wrong.
        "javascript:alert(1)",
        "mailto:jane@acme.com",
        "ftp://www.linkedin.com/in/jane-doe",
        # urlparse itself refuses this one.
        "http://[::1/in/jane-doe",
        # Credentials and explicit ports never appear in a real profile URL,
        # and rewriting either would silently change where the browser goes.
        "https://evil@www.linkedin.com/in/jane-doe",
        "https://www.linkedin.com:8443/in/jane-doe",
    ])
    def test_an_unnormalizable_url_is_refused_and_names_the_flag(self, tmp_path, stored):
        runtime = _runtime(tmp_path, linkedin_url=stored)

        with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
            with pytest.raises(EntityNotFound) as exc:
                _propose(runtime)

        assert "--linkedin-url" in str(exc.value)
        assert stored in str(exc.value)
        # Refused before any spend and before anything was written.
        mock_gen.assert_not_called()
        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []

    def test_normalization_touches_no_network(self, tmp_path):
        # Asserted rather than assumed: a normalizer that resolved the host
        # to check it would both be slow and make this decision depend on
        # DNS, and the whole check runs before the session check on purpose.
        runtime = _runtime(tmp_path, linkedin_url="/in/jane-doe")

        with patch("socket.socket", side_effect=AssertionError("opened a socket")):
            with patch("socket.getaddrinfo", side_effect=AssertionError("resolved a host")):
                proposal = _propose(runtime)

        assert proposal.linkedin_url == CANONICAL
        # Sanity check that the patched names are the real ones.
        assert hasattr(socket, "getaddrinfo")


class TestDuplicateRequestRefusal:
    """A second connection request is visible in the recipient's notifications.

    §6.3 says correctness cannot rest on the platform's idempotence, so the
    refusal lives here. It reads `sent_at` rather than `send_state` because a
    re-draft rewrites send_state while sent_at is only ever written by a
    real send.
    """

    def test_a_person_with_a_sent_request_is_refused(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_request(
            runtime.storage, send_state="sent", sent_at="2026-09-20T10:00:00+00:00",
        )

        with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
            with pytest.raises(ConnectionAlreadySent) as exc:
                _propose(runtime)

        assert exc.value.person_id == PERSON_ID
        assert exc.value.sent_at == "2026-09-20T10:00:00+00:00"
        assert "Jane Doe" in str(exc.value)
        mock_gen.assert_not_called()
        assert _approvals(runtime.storage) == []
        # The prior record is left exactly as it was found.
        assert ConnectionRequest.load(runtime.storage, REQUEST_ID).note_text == (
            "An older note."
        )

    def test_sent_at_is_the_fact_read_not_send_state(self, tmp_path):
        """A record whose send_state says "sent" but which never sent.

        careeros never writes this pair itself, but a re-draft rewrites
        send_state and an out-of-band edit can produce anything. sent_at is
        the durable fact, so this must re-propose rather than refuse — a
        stale send_state must not permanently lock a person out.
        """
        runtime = _runtime(tmp_path)
        _seed_request(runtime.storage, send_state="sent", sent_at=None)

        proposal = _propose(runtime)

        assert proposal.note_text == NOTE
        assert ConnectionRequest.load(runtime.storage, REQUEST_ID).note_text == NOTE

    def test_a_sent_request_for_a_different_job_still_refuses(self, tmp_path):
        """You connect with a human once, not once per job.

        The record id is derived from (job_id, person_id), so a second job
        produces a second record id — reading only this job's record would
        miss the request that already reached this person.
        """
        runtime = _runtime(tmp_path)
        other_id = make_message_id("other-job-xyz9", PERSON_ID)
        _seed_request(
            runtime.storage, id=other_id, job_id="other-job-xyz9",
            send_state="sent", sent_at="2026-09-01T10:00:00+00:00",
        )

        with pytest.raises(ConnectionAlreadySent):
            _propose(runtime)

        assert _approvals(runtime.storage) == []

    def test_a_sent_request_to_a_different_person_does_not_refuse(self, tmp_path):
        runtime = _runtime(tmp_path)
        other_id = make_message_id(JOB_ID, "acme-corp-john-roe")
        _seed_request(
            runtime.storage, id=other_id, person_id="acme-corp-john-roe",
            send_state="sent", sent_at="2026-09-01T10:00:00+00:00",
        )

        proposal = _propose(runtime)

        assert proposal.note_text == NOTE

    def test_a_not_yet_sent_request_is_regenerated(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_request(runtime.storage, note_text="An older note.", sent_at=None)

        proposal = _propose(runtime)

        record = ConnectionRequest.load(runtime.storage, REQUEST_ID)
        assert record.note_text == NOTE
        assert record.sent_at is None
        assert proposal.request_id == REQUEST_ID

    def test_a_corrupt_record_does_not_hide_a_real_sent_one(self, tmp_path):
        # One unparseable file in connections/ must not make the duplicate
        # check answer "nobody has been contacted".
        runtime = _runtime(tmp_path)
        runtime.storage.atomic_write("connections/garbage.json", b"{not json")
        other_id = make_message_id("other-job-xyz9", PERSON_ID)
        _seed_request(
            runtime.storage, id=other_id, job_id="other-job-xyz9",
            send_state="sent", sent_at="2026-09-01T10:00:00+00:00",
        )

        with pytest.raises(ConnectionAlreadySent):
            _propose(runtime)

    def test_a_corrupt_record_alone_does_not_block_a_first_request(self, tmp_path):
        runtime = _runtime(tmp_path)
        runtime.storage.atomic_write("connections/garbage.json", b"{not json")
        runtime.storage.atomic_write("connections/notes.txt", b"not a record at all")

        proposal = _propose(runtime)

        assert proposal.note_text == NOTE


class TestPolicyBlocked:
    def test_logs_policy_blocked_before_raising(self, tmp_path):
        """The audit trail has to show the block the caller only sees as an exception.

        Logging after the raise would be unreachable; not logging at all
        would leave a policy refusal invisible to anyone reading the
        activity log, which is the only durable record a refusal leaves.
        """
        runtime = _runtime(tmp_path)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(runtime.storage)

        with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
            with pytest.raises(PolicyBlocked) as exc:
                _propose(runtime)

        assert exc.value.rule == "blocked_company:Acme Corp"
        assert "policy_blocked" in _log(runtime.storage)
        mock_gen.assert_not_called()
        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []


class TestSessionIsCheckedBeforeDrafting:
    """A signed-out user must not pay for an LLM call before being told to sign in.

    12b's lesson, and the spec names it. "The error surfaced" and "no LLM
    call happened" are different properties; only the second is the one this
    class exists for, so every test here asserts the drafter, not just the
    exception.
    """

    def test_a_signed_out_user_is_told_to_sign_in_and_never_drafts(self, tmp_path):
        runtime = _runtime(tmp_path)

        with patch("careeros.operations.connect.check_board_sessions",
                   return_value={"linkedin": False}) as cbs:
            with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
                with pytest.raises(BoardSessionRequired) as exc:
                    propose_connection_request(
                        runtime, JOB_ID, PERSON_ID, action_label="connect",
                    )

        assert exc.value.board == "linkedin"
        cbs.assert_called_once_with(["linkedin"])
        mock_gen.assert_not_called()
        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []

    def test_a_missing_linkedin_key_reads_as_unauthorized(self, tmp_path):
        # check_board_sessions answers a dict; a name it does not know is
        # simply absent, and absent must never read as authorized.
        runtime = _runtime(tmp_path)

        with patch("careeros.operations.connect.check_board_sessions", return_value={}):
            with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
                with pytest.raises(BoardSessionRequired):
                    propose_connection_request(
                        runtime, JOB_ID, PERSON_ID, action_label="connect",
                    )

        mock_gen.assert_not_called()

    def test_a_locked_profile_surfaces_as_browser_unavailable_and_never_drafts(
        self, tmp_path,
    ):
        runtime = _runtime(tmp_path)

        with patch("careeros.operations.connect.check_board_sessions",
                   side_effect=BrowserProfileBusy("already in use")):
            with patch("careeros.operations.connect.generate_connection_note") as mock_gen:
                with pytest.raises(BrowserUnavailable) as exc:
                    propose_connection_request(
                        runtime, JOB_ID, PERSON_ID, action_label="connect",
                    )

        assert exc.value.profile_busy is True
        # The detail carries through from the original exception rather than
        # being replaced by a generic message; callers report it verbatim.
        assert str(exc.value) == "already in use"
        mock_gen.assert_not_called()
        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []

    def test_the_order_is_session_then_draft_on_the_happy_path_too(self, tmp_path):
        """Positive proof, not just the signed-out negative.

        A propose that drafted first and checked the session afterwards
        would still raise BoardSessionRequired for a signed-out user — the
        two tests above would pass while the LLM call had already been paid
        for on the happy path. This records the actual order.
        """
        runtime = _runtime(tmp_path)
        calls = []

        def _session(names):
            calls.append("session")
            return {"linkedin": True}

        def _draft(*args, **kwargs):
            calls.append("draft")
            return NOTE

        with patch("careeros.operations.connect.check_board_sessions", _session):
            with patch("careeros.operations.connect.generate_connection_note", _draft):
                propose_connection_request(
                    runtime, JOB_ID, PERSON_ID, action_label="connect",
                )

        assert calls == ["session", "draft"]


class TestDraftFailureConversion:
    """`generate_connection_note` returns '' and never raises.

    That is the house convention across all four drafting skills, so §6.2's
    requirement that an over-cap or empty note *is* a DraftFailed exists
    nowhere unless this layer performs the conversion.
    """

    @pytest.mark.parametrize("returned", ["", "   ", "\n\t "])
    def test_an_empty_note_becomes_draft_failed(self, tmp_path, returned):
        runtime = _runtime(tmp_path)

        with pytest.raises(DraftFailed):
            _propose(runtime, note=returned)

        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []

    def test_an_over_cap_note_becomes_draft_failed(self, tmp_path):
        # The skill already refuses over-cap by returning '', so this is
        # defence in depth at the layer the spec puts the requirement on —
        # and it is what stops a note LinkedIn would clip mid-word from ever
        # reaching a record or an approval.
        runtime = _runtime(tmp_path)

        with pytest.raises(DraftFailed):
            _propose(runtime, note="x" * (NOTE_CHAR_LIMIT + 1))

        assert _approvals(runtime.storage) == []
        assert _connections(runtime.storage) == []

    def test_a_note_exactly_at_the_cap_is_accepted(self, tmp_path):
        runtime = _runtime(tmp_path)
        at_cap = "y" * NOTE_CHAR_LIMIT

        proposal = _propose(runtime, note=at_cap)

        assert proposal.note_text == at_cap

    def test_a_draft_failure_leaves_a_prior_record_untouched(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_request(runtime.storage, note_text="An older note.")

        with pytest.raises(DraftFailed):
            _propose(runtime, note="")

        assert ConnectionRequest.load(runtime.storage, REQUEST_ID).note_text == (
            "An older note."
        )
        assert _approvals(runtime.storage) == []
