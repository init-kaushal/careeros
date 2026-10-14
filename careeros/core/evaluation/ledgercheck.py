"""EVL009: the evaluation file against its `evaluation.completed` ledger event (spec 16.4). Pure and read-only.

The expected event is the one with `type == "evaluation.completed"` and `entity == record.id` that has the highest
integer `seq` (chain order; no timestamps). `evaluation.rejected` and `evaluation.result_replaced` are ignored. A
record with no `result:` and no such event (a skeleton, or a job never evaluated) yields nothing. The findings are
warnings whose messages start with `ambiguous`, `missing`, `malformed` or `mismatched`.
"""

from __future__ import annotations

import re

from careeros.core.evaluation.canon import result_sha256
from careeros.core.models import Issue

COMPLETED = "evaluation.completed"
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_FIX = "run `careeros evaluate <job>`; it records a fresh completion for the file as it is now"


def _seq(event: dict) -> int | None:
    seq = event.get("seq")
    return seq if type(seq) is int else None


def latest_completion(record_id: str, events: list[dict]) -> dict | None:
    """The `evaluation.completed` event of this entity with the highest integer `seq`, or None."""
    best, best_seq = None, None
    for event in events:
        if not isinstance(event, dict) or event.get("type") != COMPLETED or event.get("entity") != record_id:
            continue
        seq = _seq(event)
        if seq is not None and (best_seq is None or seq > best_seq):
            best, best_seq = event, seq
    return best


def _is_path(artifact: object) -> bool:
    return isinstance(artifact, str) and artifact.rsplit("/", 1)[-1] == "evaluation.md"


def verify(record, all_records, events) -> list[Issue]:
    """EVL009 findings (zero or one) for one loaded record. `all_records` are every evaluation file in the workspace."""
    event = latest_completion(record.id, events)
    result = record.result
    if result is None and event is None:
        return []

    def warn(message: str) -> list[Issue]:
        return [Issue("warning", "EVL009", record.rel, message, _FIX)]

    twins = sorted(r.rel for r in all_records if r is not record and r.rel != record.rel and r.id == record.id)
    if twins:
        return warn(f"ambiguous: evaluation id {record.id} also appears in {', '.join(twins)}")
    if event is None:
        return warn("missing: the file has a result but the ledger has no evaluation.completed event for it")
    artifacts = event.get("artifacts")
    if not isinstance(artifacts, list):
        return warn(f"malformed: the completion event (seq {event.get('seq')}) has no artifacts list")
    hashes = [a for a in artifacts if isinstance(a, str) and a.startswith("sha256:")]
    paths = [a for a in artifacts if _is_path(a)]
    if len(hashes) != 1 or not _HASH.fullmatch(hashes[0]):
        return warn(f"malformed: the completion event (seq {event.get('seq')}) needs exactly one sha256:<64 lowercase "
                    "hex> artifact")
    if len(paths) != 1:
        return warn(f"malformed: the completion event (seq {event.get('seq')}) needs exactly one evaluation.md path "
                    "artifact")
    if paths[0] != record.rel:
        return warn(f"ambiguous: the latest completion event names {paths[0]}, not {record.rel}")
    try:
        if result is None:
            raise ValueError("no result")
        actual = "sha256:" + result_sha256(result)
    except (TypeError, ValueError, AttributeError, KeyError):
        actual = None
    if actual != hashes[0]:
        return warn(f"mismatched: result: {'cannot be hashed or is absent' if actual is None else 'hashes to ' + actual}"
                    f" but the latest completion event (seq {event.get('seq')}) recorded {hashes[0]}")
    return []
