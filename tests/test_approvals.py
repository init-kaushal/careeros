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
