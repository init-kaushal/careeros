import dataclasses
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
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in obj.items() if k in known})

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
    try:
        manifest_ver = int(manifest.schema_version)
        supported_ver = int(SUPPORTED_SCHEMA_VERSION)
    except ValueError:
        raise UnsupportedSchemaVersion(
            f"schema_version '{manifest.schema_version}' is not a valid integer"
        )
    if manifest_ver > supported_ver:
        raise UnsupportedSchemaVersion(
            f"Workspace schema version {manifest.schema_version} is newer than supported {SUPPORTED_SCHEMA_VERSION}"
        )
