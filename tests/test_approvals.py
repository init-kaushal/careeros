import pytest

from careeros.core.models import Approval
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _storage(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    return storage


class TestApprovalModel:
    def test_save_then_load_roundtrips_every_field(self, tmp_path):
        storage = _storage(tmp_path)
        approval = Approval(
            id="send-outreach-abc123", action="send_outreach",
            summary="Send outreach email to Jane Doe?", state="pending",
            entity_type="outreach_message", entity_id="job__person",
            payload={"message_id": "job__person"},
            created_at="2026-09-22T00:00:00+00:00",
        )
        approval.save(storage)
        loaded = Approval.load(storage, "send-outreach-abc123")
        assert loaded == approval

    def test_defaults_are_pending_with_empty_payload(self):
        approval = Approval(
            id="a-1", action="send_outreach", summary="s",
            created_at="2026-09-22T00:00:00+00:00",
        )
        assert approval.state == "pending"
        assert approval.payload == {}
        assert approval.decided_at is None
        assert approval.decided_by is None
        assert approval.reason is None
        assert approval.executed_at is None
        assert approval.detail is None

    def test_load_missing_raises_file_not_found(self, tmp_path):
        storage = _storage(tmp_path)
        with pytest.raises(FileNotFoundError):
            Approval.load(storage, "nope")

    def test_saves_under_approvals_directory(self, tmp_path):
        storage = _storage(tmp_path)
        Approval(id="a-1", action="send_outreach", summary="s",
                 created_at="2026-09-22T00:00:00+00:00").save(storage)
        assert storage.exists("approvals/a-1.json")


from careeros.operations.approvals import (
    APPROVED, DECLINED, EXECUTED, FAILED, PENDING, SUPERSEDED,
    list_pending, mark_executed, mark_failed, open_approval, payload_value,
    require_state, resolve_approval,
)
from careeros.operations.errors import ApprovalNotGranted, MalformedApproval
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_local_runtime


def _runtime(tmp_path, session_id="sess-1"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    return open_local_runtime(storage, session_id=session_id)


def _open(runtime, entity_id="job__person", summary="Send outreach email to Jane Doe?"):
    return open_approval(
        runtime, "send_outreach", summary,
        {"message_id": entity_id},
        entity_type="outreach_message", entity_id=entity_id,
        action_label="outreach",
    )


def _log(storage):
    from datetime import datetime, timezone
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


class TestOpenApproval:
    def test_writes_a_pending_record_and_logs_requested(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        assert approval.state == PENDING
        assert approval.payload == {"message_id": "job__person"}
        assert Approval.load(runtime.storage, approval.id).state == PENDING
        assert "approval_requested" in _log(runtime.storage)

    def test_supersedes_a_prior_pending_for_the_same_entity(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _open(runtime)
        second = _open(runtime)
        assert first.id != second.id
        assert Approval.load(runtime.storage, first.id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.id).state == PENDING
        assert "approval_superseded" in _log(runtime.storage)

    def test_does_not_supersede_a_different_entity(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _open(runtime, entity_id="job-a__person")
        _open(runtime, entity_id="job-b__person")
        assert Approval.load(runtime.storage, first.id).state == PENDING

    def test_does_not_supersede_an_already_decided_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        first = _open(runtime)
        resolve_approval(runtime, first.id, ApprovalResult(approved=True), action_label="outreach")
        _open(runtime)
        assert Approval.load(runtime.storage, first.id).state == APPROVED


class TestResolveApproval:
    def test_approve_records_state_decider_and_reason(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolved = resolve_approval(
            runtime, approval.id,
            ApprovalResult(approved=True, reason="user said yes in chat"),
            action_label="outreach",
        )
        assert resolved.state == APPROVED
        assert resolved.decided_by == "local"
        assert resolved.reason == "user said yes in chat"
        assert resolved.decided_at is not None

    def test_decline_records_declined(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolved = resolve_approval(
            runtime, approval.id, ApprovalResult(approved=False), action_label="outreach"
        )
        assert resolved.state == DECLINED
        assert "approval_declined" in _log(runtime.storage)

    def test_populates_the_activity_event_reason_field(self, tmp_path):
        import json
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(
            runtime, approval.id,
            ApprovalResult(approved=True, reason="user said yes in chat"),
            action_label="outreach",
        )
        events = [json.loads(line) for line in _log(runtime.storage).strip().split("\n")]
        granted = [e for e in events if e["event_type"] == "approval_granted"]
        assert len(granted) == 1
        assert granted[0]["reason"] == "user said yes in chat"
        assert granted[0]["action"] == "outreach"
        assert granted[0]["agent_runtime"] == "local"

    def test_refuses_to_resolve_twice(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        with pytest.raises(ApprovalNotGranted) as exc:
            resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        assert exc.value.state == APPROVED


class TestRequireState:
    def test_returns_the_approval_when_the_state_matches(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        assert require_state(runtime.storage, approval.id, APPROVED).id == approval.id

    @pytest.mark.parametrize("state", [PENDING, DECLINED, EXECUTED, FAILED, SUPERSEDED])
    def test_raises_with_the_state_it_found(self, tmp_path, state):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        approval.model_copy(update={"state": state}).save(runtime.storage)
        with pytest.raises(ApprovalNotGranted) as exc:
            require_state(runtime.storage, approval.id, APPROVED)
        assert exc.value.state == state


class TestMarkExecutedAndFailed:
    def test_mark_executed_sets_state_and_timestamp(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        resolve_approval(runtime, approval.id, ApprovalResult(approved=True), action_label="outreach")
        marked = mark_executed(runtime, approval.id)
        assert marked.state == EXECUTED
        assert marked.executed_at is not None

    def test_mark_executed_refuses_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        with pytest.raises(ApprovalNotGranted) as exc:
            mark_executed(runtime, approval.id)
        assert exc.value.state == PENDING

    def test_mark_failed_records_detail(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        marked = mark_failed(runtime, approval.id, "SMTPAuthenticationError")
        assert marked.state == FAILED
        assert marked.detail == "SMTPAuthenticationError"


class TestPayloadValue:
    def test_returns_the_value(self, tmp_path):
        runtime = _runtime(tmp_path)
        approval = _open(runtime)
        assert payload_value(approval, "message_id") == "job__person"

    @pytest.mark.parametrize("payload", [{}, {"message_id": ""}])
    def test_missing_or_empty_raises_malformed(self, payload):
        approval = Approval(id="a-1", action="send_outreach", summary="s",
                            payload=payload, created_at="2026-09-22T00:00:00+00:00")
        with pytest.raises(MalformedApproval) as exc:
            payload_value(approval, "message_id")
        assert exc.value.key == "message_id"


class TestListPending:
    def test_returns_only_pending_and_skips_the_keep_file(self, tmp_path):
        runtime = _runtime(tmp_path)
        pending = _open(runtime, entity_id="job-a__person")
        decided = _open(runtime, entity_id="job-b__person")
        resolve_approval(runtime, decided.id, ApprovalResult(approved=True), action_label="outreach")
        ids = [a.id for a in list_pending(runtime.storage)]
        assert ids == [pending.id]

    def test_ignores_an_unparseable_record(self, tmp_path):
        runtime = _runtime(tmp_path)
        pending = _open(runtime)
        runtime.storage.atomic_write("approvals/garbage.json", b"{not json")
        assert [a.id for a in list_pending(runtime.storage)] == [pending.id]

    def test_empty_when_nothing_pending(self, tmp_path):
        runtime = _runtime(tmp_path)
        assert list_pending(runtime.storage) == []


class TestProtocolDeclaresRuntimeName:
    def test_agent_runtime_name_is_part_of_the_protocol(self):
        from careeros.runtime.base import AgentRuntime
        assert "agent_runtime_name" in AgentRuntime.__annotations__
