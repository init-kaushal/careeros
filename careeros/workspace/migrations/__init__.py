from typing import Callable
from careeros.storage.interface import StorageProvider

MigrationFn = Callable[[StorageProvider], None]

MIGRATIONS: list[tuple[str, MigrationFn]] = []


def register(name: str):
    def decorator(fn: MigrationFn) -> MigrationFn:
        MIGRATIONS.append((name, fn))
        return fn
    return decorator


def run_pending(storage: StorageProvider, applied: list[str]) -> list[str]:
    newly_applied: list[str] = []
    for mig_name, fn in MIGRATIONS:
        if mig_name not in applied:
            fn(storage)
            newly_applied.append(mig_name)
    return newly_applied


# Must be last — imports trigger @register decorators; register must be defined first
from careeros.workspace.migrations import m001_initial  # noqa: F401, E402
