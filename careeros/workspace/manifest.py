from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import uuid

CAREEROS_VERSION = "0.1.0"
SUPPORTED_SCHEMA_VERSION = "1"


class UnsupportedSchemaVersion(Exception):
    pass


@dataclass
class Manifest:
    careeros_version: str
    schema_version: str
    workspace_id: str
    created_at: str
    storage_type: str
    migrations_applied: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> "Manifest":
        obj = json.loads(data)
        return cls(**obj)

    @classmethod
    def create_new(cls, storage_type: str = "local") -> "Manifest":
        return cls(
            careeros_version=CAREEROS_VERSION,
            schema_version=SUPPORTED_SCHEMA_VERSION,
            workspace_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            storage_type=storage_type,
        )


def check_schema_compatibility(manifest: Manifest) -> None:
    if int(manifest.schema_version) > int(SUPPORTED_SCHEMA_VERSION):
        raise UnsupportedSchemaVersion(
            f"Workspace schema version {manifest.schema_version} is newer than "
            f"this CareerOS supports ({SUPPORTED_SCHEMA_VERSION}). Upgrade CareerOS."
        )
