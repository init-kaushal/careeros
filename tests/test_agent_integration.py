"""Proves an approval survives a real process boundary.

The shell-driven runtime this phase targets cannot round-trip to a human
inside one process, so the propose and execute halves genuinely run as
separate interpreters here — an in-process test cannot demonstrate that the
approval record, and not in-memory state, is what carries the decision.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from careeros.core.models import Approval, Company, Job, Person, Profile
from careeros.operations.approvals import EXECUTED
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

REPO_ROOT = Path(__file__).resolve().parent.parent

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp and would love to chat."

PROPOSE = """
import json, sys
from unittest.mock import patch
from careeros.operations.approval_queue import queue_only
from careeros.operations.outreach import propose_outreach_send
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-propose")
with patch("careeros.operations.outreach.generate_outreach_message", return_value=sys.argv[3]):
    proposal = propose_outreach_send(runtime, sys.argv[1], sys.argv[2], action_label="agent")
print(json.dumps({"approval_id": proposal.approval_id, "summary": proposal.summary}))
"""

EXECUTE = """
import sys
from unittest.mock import patch
from careeros.operations.approval_queue import queue_only
from careeros.operations.approvals import resolve_approval
from careeros.operations.outreach import execute_outreach_send
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-execute")
resolve_approval(
    runtime, sys.argv[1],
    ApprovalResult(approved=True, reason="user said yes in chat"),
    action_label="agent",
)
with patch("careeros.operations.outreach.send_email") as mock_send:
    result = execute_outreach_send(runtime, sys.argv[1], action_label="agent")
    assert mock_send.call_count == 1, mock_send.call_count
print(result.recipient_name)
"""


def _seed(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
           title="Engineering Manager", email="jane@acme.com",
           researched_at=now).save(storage)
    return storage


def _run(script, args, workspace):
    env = dict(os.environ)
    env["CAREEROS_WORKSPACE"] = str(workspace)
    # Strip SMTP credentials: these subprocesses run with python -c and no test runner,
    # so the suite-wide smtplib patch won't protect them. If send_email's patch is ever
    # broken or removed, this prevents silent mail with real credentials.
    for key in list(env.keys()):
        if key.startswith("CAREEROS_SMTP"):
            del env[key]
    completed = subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_two_processes_complete_one_approval_gated_send(tmp_path):
    storage = _seed(tmp_path)

    proposed = json.loads(_run(PROPOSE, [JOB_ID, PERSON_ID, DRAFT], tmp_path))
    approval_id = proposed["approval_id"]

    # The first process is gone. Only the workspace carries the state forward.
    assert Approval.load(storage, approval_id).state == "pending"

    assert _run(EXECUTE, [approval_id], tmp_path) == "Jane Doe"

    approval = Approval.load(storage, approval_id)
    assert approval.state == EXECUTED
    assert approval.decided_by == "claude_code"
    assert approval.reason == "user said yes in chat"
    assert approval.executed_at is not None


def test_the_activity_log_attributes_both_halves_to_the_agent_runtime(tmp_path):
    storage = _seed(tmp_path)
    proposed = json.loads(_run(PROPOSE, [JOB_ID, PERSON_ID, DRAFT], tmp_path))
    _run(EXECUTE, [proposed["approval_id"]], tmp_path)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events = [
        json.loads(line)
        for line in storage.read("activity/" + date + ".jsonl").decode().strip().split("\n")
    ]
    by_type = {e["event_type"]: e for e in events}

    assert by_type["approval_granted"]["agent_runtime"] == "claude_code"
    assert by_type["approval_granted"]["reason"] == "user said yes in chat"
    assert by_type["outreach_sent"]["agent_runtime"] == "claude_code"
    assert by_type["outreach_sent"]["action"] == "agent"
    assert {e["session_id"] for e in events} == {"agent-propose", "agent-execute"}


def test_the_second_process_cannot_execute_without_a_decision(tmp_path):
    _seed(tmp_path)
    proposed = json.loads(_run(PROPOSE, [JOB_ID, PERSON_ID, DRAFT], tmp_path))

    script = """
import sys
from careeros.operations.approval_queue import queue_only
from careeros.operations.errors import ApprovalNotGranted
from careeros.operations.outreach import execute_outreach_send
from careeros.runtime.factory import open_agent_runtime

runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-execute")
try:
    execute_outreach_send(runtime, sys.argv[1], action_label="agent")
except ApprovalNotGranted as exc:
    print(exc.state)
else:
    raise AssertionError("executed a pending approval")
"""
    assert _run(script, [proposed["approval_id"]], tmp_path) == "pending"
