from dataclasses import dataclass
from careeros.storage.interface import StorageProvider
from careeros.workspace.manifest import Manifest, check_schema_compatibility
from careeros.workspace.migrations import run_pending

MANIFEST_PATH = "manifest.json"


@dataclass
class WorkspaceContext:
    manifest: Manifest
    storage: StorageProvider


def init_workspace(storage: StorageProvider) -> WorkspaceContext:
    if storage.exists(MANIFEST_PATH):
        raise FileExistsError(
            "Workspace already exists at this path. Use 'careeros workspace status' to inspect it."
        )
    manifest = Manifest.create_new()
    newly = run_pending(storage, manifest.migrations_applied)
    manifest.migrations_applied.extend(newly)
    storage.atomic_write(MANIFEST_PATH, manifest.to_json().encode())
    return WorkspaceContext(manifest=manifest, storage=storage)


def open_workspace(storage: StorageProvider) -> WorkspaceContext:
    if not storage.exists(MANIFEST_PATH):
        raise FileNotFoundError(
            "No CareerOS workspace found. Run 'careeros onboard' to create one."
        )
    manifest = Manifest.from_json(storage.read(MANIFEST_PATH).decode())
    check_schema_compatibility(manifest)
    newly = run_pending(storage, manifest.migrations_applied)
    if newly:
        manifest.migrations_applied.extend(newly)
        storage.atomic_write(MANIFEST_PATH, manifest.to_json().encode())
    return WorkspaceContext(manifest=manifest, storage=storage)
