from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from careeros.core.models import (
    Approval, Company, Job, OutreachMessage, Person, PolicyConfig, Profile,
)
from careeros.operations.approvals import PENDING, SUPERSEDED
from careeros.operations.errors import DraftFailed, EntityNotFound, PolicyBlocked
from careeros.operations.outreach import (
    make_message_id, propose_outreach_send,
)
from careeros.runtime.factory import open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
MESSAGE_ID = JOB_ID + "__" + PERSON_ID
DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp..."


def _runtime(tmp_path, with_email=True, session_id="sess-1"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
           title="Engineering Manager",
           email=("jane@acme.com" if with_email else None),
           researched_at=now).save(storage)
    return open_local_runtime(storage, session_id=session_id)


def _propose(runtime, draft=DRAFT):
    with patch("careeros.operations.outreach.generate_outreach_message", return_value=draft):
        return propose_outreach_send(runtime, JOB_ID, PERSON_ID)


def _log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


class TestProposeOutreachSend:
    def test_returns_a_proposal_with_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        assert proposal.message_id == MESSAGE_ID
        assert proposal.draft_text == DRAFT
        assert proposal.recipient_name == "Jane Doe"
        assert proposal.recipient_email == "jane@acme.com"
        assert proposal.subject == "Regarding Senior SRE at Acme Corp"
        assert proposal.already_sent_at is None
        assert Approval.load(runtime.storage, proposal.approval_id).state == PENDING

    def test_persists_the_draft_before_any_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        message = OutreachMessage.load(runtime.storage, proposal.message_id)
        assert message.draft_text == DRAFT
        assert message.send_state == "drafted"
        assert "outreach_drafted" in _log(runtime.storage)

    def test_records_the_draft_digest_in_the_payload(self, tmp_path):
        import hashlib
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.payload["draft_sha256"] == hashlib.sha256(DRAFT.encode()).hexdigest()
        assert approval.payload["message_id"] == MESSAGE_ID
        assert approval.payload["job_id"] == JOB_ID
        assert approval.payload["person_id"] == PERSON_ID

    def test_a_missing_person_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(EntityNotFound):
            with patch("careeros.operations.outreach.generate_outreach_message", return_value=DRAFT):
                propose_outreach_send(runtime, JOB_ID, "nobody")

    def test_an_empty_draft_raises_draft_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        with pytest.raises(DraftFailed):
            _propose(runtime, draft="")

    def test_a_blocked_company_logs_then_raises_without_drafting(self, tmp_path):
        runtime = _runtime(tmp_path)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(runtime.storage)
        with patch("careeros.operations.outreach.generate_outreach_message") as mock_gen:
            with pytest.raises(PolicyBlocked) as exc:
                propose_outreach_send(runtime, JOB_ID, PERSON_ID)
        assert exc.value.rule == "blocked_company:Acme Corp"
        mock_gen.assert_not_called()
        assert "policy_blocked" in _log(runtime.storage)
        assert not runtime.storage.exists("outreach/" + MESSAGE_ID + ".json")

    def test_regenerating_supersedes_the_prior_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _propose(runtime)
        second = _propose(runtime, draft="A different draft entirely.")
        assert Approval.load(runtime.storage, first.approval_id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.approval_id).state == PENDING
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == "A different draft entirely."

    def test_carries_referral_state_forward(self, tmp_path):
        runtime = _runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
                        draft_text="old", referral_state="referral_requested",
                        created_at=now).save(runtime.storage)
        _propose(runtime)
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).referral_state == "referral_requested"

    def test_surfaces_a_prior_send_and_preserves_it_across_regeneration(self, tmp_path):
        runtime = _runtime(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
                        draft_text="the one we already sent", send_state="sent",
                        sent_at=now, created_at=now).save(runtime.storage)

        first = _propose(runtime)
        assert first.already_sent_at == now
        assert "send ANOTHER" in first.summary

        # Regenerating must not lose the fact that a send already happened:
        # the first propose rewrote the message to send_state="drafted".
        second = _propose(runtime, draft="second attempt")
        assert second.already_sent_at == now

    def test_summary_is_a_plain_question_when_never_sent(self, tmp_path):
        runtime = _runtime(tmp_path)
        proposal = _propose(runtime)
        assert proposal.summary == (
            "Send outreach email to Jane Doe re: Acme Corp — Senior SRE?"
        )


class TestMakeMessageId:
    def test_matches_the_historical_format(self):
        assert make_message_id(JOB_ID, PERSON_ID) == MESSAGE_ID

    def test_slugifies_unsafe_values_instead_of_raising(self):
        assert "/" not in make_message_id("../../etc/passwd", "..")
