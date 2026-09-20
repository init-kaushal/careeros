from __future__ import annotations

from dataclasses import dataclass

from careeros.core.dedup import is_same_posting
from careeros.core.models import Job, Sighting
from careeros.storage.interface import StorageProvider

# Fields a merge may fill in. stage, applied_at, and notes are deliberately
# absent: user state is structurally excluded, not guarded by a conditional a
# later edit could weaken.
_ENRICHABLE = (
    "location", "description", "salary_min", "salary_max",
    "remote", "requirements", "source_id",
)


def _is_absent(value: object) -> bool:
    """Absence is type-specific, never truthiness.

    remote=False and salary_min=0 are PRESENT values; a `not value` test
    would silently overwrite both.
    """
    if value is None:
        return True
    if isinstance(value, (list, str)) and len(value) == 0:
        return True
    return False


@dataclass(frozen=True)
class SaveOutcome:
    job: Job
    created: bool
    enriched: tuple[str, ...]


class JobStore:
    """The single seam through which new Job records are created.

    Creation only. Updates to a known job (stage changes, notes) call
    Job.save directly — routing those through dedup would be nonsense, since
    a job always matches itself.
    """

    def __init__(self, storage: StorageProvider) -> None:
        self._storage = storage

    def save_new(self, job: Job, *, force: bool = False) -> SaveOutcome:
        if not force:
            for existing in Job.list_all(self._storage):
                if is_same_posting(job, existing):
                    return self._merge(job, existing)
        job.save(self._storage)
        return SaveOutcome(job=job, created=True, enriched=())

    def _merge(self, incoming: Job, existing: Job) -> SaveOutcome:
        updates: dict = {}
        enriched: list[str] = []
        for field in _ENRICHABLE:
            if _is_absent(getattr(existing, field)) and not _is_absent(getattr(incoming, field)):
                updates[field] = getattr(incoming, field)
                enriched.append(field)

        sightings = list(existing.sightings)
        if not sightings:
            # Seed with the existing record's own first sighting so the list
            # is complete from the first merge onward, without backfilling
            # every workspace.
            sightings.append(Sighting(
                source=existing.source, url=existing.url, seen_at=existing.created_at,
            ))
        sightings.append(Sighting(
            source=incoming.source, url=incoming.url, seen_at=incoming.created_at,
        ))
        updates["sightings"] = sightings

        if enriched:
            updates["updated_at"] = incoming.created_at

        merged = existing.model_copy(update=updates)
        merged.save(self._storage)
        return SaveOutcome(job=merged, created=False, enriched=tuple(enriched))
