import json
import pytest
from datetime import datetime, timezone
from careeros.core.activity import ActivityEvent, ActivityLogger


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_logger_writes_jsonl(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    event = logger.new_event("workspace_created", "init", "Workspace initialized")
    logger.log(event)

    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    lines = [l for l in content.strip().split("\n") if l]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "workspace_created"
    assert parsed["status"] == "success"
    assert parsed["session_id"] == logger.session_id


def test_multiple_events_appended(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    for i in range(3):
        logger.log(logger.new_event("test_event", "test", f"Event {i}"))

    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    lines = [l for l in content.strip().split("\n") if l]
    assert len(lines) == 3


def test_custom_session_id(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage, session_id="my-session")
    event = logger.new_event("test", "test", "summary")
    logger.log(event)

    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    parsed = json.loads(content.strip())
    assert parsed["session_id"] == "my-session"


def test_event_has_iso_timestamp(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    event = logger.new_event("test", "test", "summary")
    # timestamp must be a valid ISO-8601 string
    datetime.fromisoformat(event.timestamp)


def test_no_secret_fields_in_event():
    event = ActivityEvent(
        event_type="test", action="test", summary="summary",
        agent_runtime="local", session_id="s1", status="success",
    )
    serialized = json.dumps(
        {f: getattr(event, f) for f in event.__dataclass_fields__}
    )
    for forbidden in ("password", "token", "api_key", "secret", "credential"):
        assert forbidden not in serialized


def test_entity_fields_optional(tmp_workspace):
    logger = ActivityLogger(tmp_workspace.storage)
    event = logger.new_event(
        "job_matched", "match", "Matched job at Acme",
        entity_type="job", entity_id="job-123"
    )
    logger.log(event)
    content = tmp_workspace.storage.read(f"activity/{_today()}.jsonl").decode()
    parsed = json.loads(content.strip())
    assert parsed["entity_type"] == "job"
    assert parsed["entity_id"] == "job-123"
