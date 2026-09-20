from __future__ import annotations

import json
from dataclasses import dataclass

from careeros.sources.base import JobSource
from careeros.sources.greenhouse import GreenhouseSource
from careeros.sources.lever import LeverSource
from careeros.storage.interface import StorageProvider

_SOURCES_PATH = "config/sources.json"

_BUILDERS = {"greenhouse": GreenhouseSource, "lever": LeverSource}

# Public so `workspace validate` can report a typo'd source name without
# reaching into this module's private connector registry.
KNOWN_SOURCES = frozenset(_BUILDERS)


@dataclass(frozen=True)
class BoardEntry:
    source: str
    board: str
    company: str


def load_board_entries(storage: StorageProvider) -> list[BoardEntry]:
    """Read API board entries from config/sources.json.

    Entries without a `board`, or naming a source with no connector, are
    skipped: legacy Phase 1 entries are inert now exactly as they always were.
    Surfacing them is `workspace validate`'s job, not this reader's.
    """
    if not storage.exists(_SOURCES_PATH):
        return []
    try:
        raw = json.loads(storage.read(_SOURCES_PATH).decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    entries = []
    for item in raw.get("sources", []):
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        board = item.get("board")
        if not board or source not in _BUILDERS:
            continue
        entries.append(BoardEntry(
            source=source, board=board, company=item.get("company") or board,
        ))
    return entries


def build_source(entry: BoardEntry) -> JobSource:
    return _BUILDERS[entry.source](entry.company)
