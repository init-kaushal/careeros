import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from careeros.core.models import Job
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_claude_code_runtime, open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def _make_job(job_id):
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id=job_id, source="manual", url="https://example.com/job",
        company="Acme", title="Engineer", stage="saved",
        created_at=now, updated_at=now,
    )


def test_same_workspace_readable_writable_across_runtimes(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)

    local_runtime = open_local_runtime(storage, session_id="local-session")
    job_a = _make_job("acme-eng-aaa1")
    job_a.save(local_runtime.storage)
    local_runtime.record_activity(local_runtime.new_event(
        "job_added", "browse", "Job saved via local runtime",
        entity_type="job", entity_id="acme-eng-aaa1",
    ))

    callback = MagicMock(return_value=ApprovalResult(approved=True))
    claude_runtime = open_claude_code_runtime(storage, approval_callback=callback, session_id="claude-session")
    job_b = _make_job("acme-eng-bbb2")
    job_b.save(claude_runtime.storage)
    claude_runtime.record_activity(claude_runtime.new_event(
        "job_added", "browse", "Job saved via claude_code runtime",
        entity_type="job", entity_id="acme-eng-bbb2",
    ))

    # Both jobs visible from either runtime instance
    assert Job.load(local_runtime.storage, "acme-eng-aaa1").company == "Acme"
    assert Job.load(local_runtime.storage, "acme-eng-bbb2").company == "Acme"
    assert Job.load(claude_runtime.storage, "acme-eng-aaa1").company == "Acme"
    assert Job.load(claude_runtime.storage, "acme-eng-bbb2").company == "Acme"

    # Activity log for today has both agent_runtime values
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_lines = storage.read("activity/" + date + ".jsonl").decode().strip().split("\n")
    assert len(log_lines) == 2
    agent_runtimes = set()
    session_ids = set()
    for line in log_lines:
        event = json.loads(line)
        agent_runtimes.add(event["agent_runtime"])
        session_ids.add(event["session_id"])
    assert agent_runtimes == {"local", "claude_code"}
    assert session_ids == {"local-session", "claude-session"}


def test_read_write_workspace_text_roundtrip_across_runtimes(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)

    local_runtime = open_local_runtime(storage, session_id="local-session")
    local_runtime.write_workspace("notes/scratch.txt", "hello from local")

    callback = MagicMock(return_value=ApprovalResult(approved=True))
    claude_runtime = open_claude_code_runtime(storage, approval_callback=callback, session_id="claude-session")
    assert claude_runtime.read_workspace("notes/scratch.txt") == "hello from local"
