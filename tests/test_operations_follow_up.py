from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from careeros.core.models import (
    Approval, CadencePolicy, Company, Job, OutreachMessage, Person, PolicyConfig,
    Profile,
)
from careeros.operations.approvals import PENDING, SUPERSEDED
from careeros.operations.errors import (
    CadenceExhausted, DraftFailed, EntityNotFound, NotDueForFollowUp, PolicyBlocked,
    RelationshipClosed,
)
from careeros.operations.follow_up import propose_follow_up
from careeros.operations.outreach import make_message_id
from careeros.runtime.factory import open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
MESSAGE_ID = make_message_id(JOB_ID, PERSON_ID)
PRIOR_DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp..."
FOLLOW_UP_DRAFT = "Hi Jane, just bumping this to the top of your inbox."

DAYS_BETWEEN_TOUCHES = 5
MAX_TOUCHES = 3


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


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
    CadencePolicy(
        days_between_touches=DAYS_BETWEEN_TOUCHES, max_touches=MAX_TOUCHES,
        max_follow_ups_per_run=5,
    ).save(storage)
    return open_local_runtime(storage, session_id=session_id)


def _seed_message(storage, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    fields = dict(
        id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID, draft_text=PRIOR_DRAFT,
        send_state="sent", referral_state="research", created_at=now,
        sent_at=_iso(DAYS_BETWEEN_TOUCHES + 2),
        last_touched_at=_iso(DAYS_BETWEEN_TOUCHES + 2), touch_count=1,
    )
    fields.update(overrides)
    OutreachMessage(**fields).save(storage)


def _propose(runtime, draft=FOLLOW_UP_DRAFT, model=None):
    with patch("careeros.operations.follow_up.generate_follow_up_message", return_value=draft):
        return propose_follow_up(
            runtime, JOB_ID, PERSON_ID, model=model, action_label="follow_up",
        )


def _log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


class TestProposeFollowUpHappyPath:
    def test_writes_the_draft_logs_and_opens_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)

        proposal = _propose(runtime)

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.draft_text == FOLLOW_UP_DRAFT
        assert "follow_up_drafted" in _log(runtime.storage)

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == PENDING
        assert set(approval.payload.keys()) == {
            "message_id", "job_id", "person_id", "draft_sha256", "touch_number",
        }
        assert approval.payload["message_id"] == MESSAGE_ID
        assert approval.payload["job_id"] == JOB_ID
        assert approval.payload["person_id"] == PERSON_ID
        assert approval.payload["touch_number"] == "2"

        assert proposal.message_id == MESSAGE_ID
        assert proposal.draft_text == FOLLOW_UP_DRAFT
        assert proposal.recipient_name == "Jane Doe"
        assert proposal.recipient_email == "jane@acme.com"
        assert proposal.touch_number == 2

    def test_leaves_sent_at_and_touch_count_alone(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        original = OutreachMessage.load(runtime.storage, MESSAGE_ID)

        _propose(runtime)

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.sent_at == original.sent_at
        assert message.last_touched_at == original.last_touched_at
        assert message.touch_count == original.touch_count

    def test_re_proposing_supersedes_the_prior_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)

        first = _propose(runtime)
        second = _propose(runtime, draft="A completely different bump.")

        assert Approval.load(runtime.storage, first.approval_id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.approval_id).state == PENDING
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == (
            "A completely different bump."
        )


class TestNotDueForFollowUp:
    def test_raises_with_correct_day_counts_and_writes_nothing(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage,
            last_touched_at=_iso(1), sent_at=_iso(1), touch_count=1,
        )

        with pytest.raises(NotDueForFollowUp) as exc:
            _propose(runtime)

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.days_since == 1
        assert exc.value.days_required == DAYS_BETWEEN_TOUCHES

        # No approval ever appeared, and the draft was never rewritten.
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == PRIOR_DRAFT

    def test_legacy_message_sent_before_cadence_fields_existed_counts_as_touch_one(
        self, tmp_path
    ):
        """A message sent before this phase has sent_at set but
        last_touched_at=None and touch_count=0 (their model defaults).
        Reading those fields raw would permanently exclude it from follow-up.
        It must be treated as one real touch, due once days_between_touches
        have passed since sent_at, with the next follow-up numbered 2.
        """
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage,
            sent_at=_iso(DAYS_BETWEEN_TOUCHES + 1),
            last_touched_at=None, touch_count=0,
        )

        proposal = _propose(runtime)

        assert proposal.touch_number == 2

    def test_a_never_sent_message_is_refused(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage, sent_at=None, last_touched_at=None, touch_count=0,
        )

        with pytest.raises(NotDueForFollowUp):
            _propose(runtime)

        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


class TestCadenceExhausted:
    def test_raises_at_the_cap(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage,
            touch_count=MAX_TOUCHES, last_touched_at=_iso(DAYS_BETWEEN_TOUCHES + 5),
        )

        with pytest.raises(CadenceExhausted) as exc:
            _propose(runtime)

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.touch_count == MAX_TOUCHES
        assert exc.value.max_touches == MAX_TOUCHES
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


class TestRelationshipClosed:
    @pytest.mark.parametrize("referral_state", ["referral_confirmed", "closed"])
    def test_terminal_referral_state_is_refused(self, tmp_path, referral_state):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage, referral_state=referral_state)

        with pytest.raises(RelationshipClosed) as exc:
            _propose(runtime)

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.reason == referral_state
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []

    def test_a_set_closed_reason_is_refused(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage, closed_reason="person left the company")

        with pytest.raises(RelationshipClosed) as exc:
            _propose(runtime)

        assert exc.value.reason == "person left the company"
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


class TestCadencePolicyMissing:
    def test_propagates_file_not_found(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        init_workspace(storage)
        now = datetime.now(timezone.utc).isoformat()
        Profile(name="Alice Smith", email="alice@example.com").save(storage)
        Job(id=JOB_ID, source="browse", url="https://example.com/job",
            company="Acme Corp", title="Senior SRE", stage="saved",
            created_at=now, updated_at=now).save(storage)
        Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
        Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
               email="jane@acme.com", researched_at=now).save(storage)
        _seed_message(storage)
        runtime = open_local_runtime(storage, session_id="sess-1")
        # No CadencePolicy saved.

        with pytest.raises(FileNotFoundError):
            _propose(runtime)


class TestEntityNotFound:
    def test_a_missing_person_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        runtime.storage.delete("people/" + PERSON_ID + ".json")

        with pytest.raises(EntityNotFound):
            _propose(runtime)

    def test_no_prior_outreach_message_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        # No OutreachMessage seeded at all.

        with pytest.raises(EntityNotFound):
            _propose(runtime)


class TestPolicyBlocked:
    def test_logs_then_raises_without_attempting_the_draft(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(runtime.storage)

        with patch("careeros.operations.follow_up.generate_follow_up_message") as mock_gen:
            with pytest.raises(PolicyBlocked) as exc:
                propose_follow_up(
                    runtime, JOB_ID, PERSON_ID, action_label="follow_up",
                )

        assert exc.value.rule == "blocked_company:Acme Corp"
        mock_gen.assert_not_called()
        assert "policy_blocked" in _log(runtime.storage)
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == PRIOR_DRAFT


class TestDraftFailed:
    def test_an_empty_draft_raises_draft_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)

        with pytest.raises(DraftFailed):
            _propose(runtime, draft="")

        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == PRIOR_DRAFT
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []
