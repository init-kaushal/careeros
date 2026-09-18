from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import uuid
from careeros.storage.interface import StorageProvider


@dataclass
class ActivityEvent:
    event_type: str
    action: str
    status: str
    summary: str
    agent_runtime: str
    session_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entity_type: str | None = None
    entity_id: str | None = None
    reason: str | None = None


class ActivityLogger:
    def __init__(self, storage: StorageProvider, session_id: str | None = None) -> None:
        self._storage = storage
        self.session_id = session_id or str(uuid.uuid4())

    def log(self, event: ActivityEvent) -> None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = f"activity/{date}.jsonl"
        line = json.dumps(asdict(event)) + "\n"
        self._storage.append(path, line.encode())

    def new_event(
        self,
        event_type: str,
        action: str,
        summary: str,
        status: str = "success",
        agent_runtime: str = "local",
        **kwargs,
    ) -> ActivityEvent:
        return ActivityEvent(
            event_type=event_type,
            action=action,
            summary=summary,
            status=status,
            agent_runtime=agent_runtime,
            session_id=self.session_id,
            **kwargs,
        )
