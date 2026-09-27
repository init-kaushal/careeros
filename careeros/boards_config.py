from __future__ import annotations

import json

from careeros.storage.interface import StorageProvider

_BOARDS_PATH = "config/boards.json"

BROWSER_BOARD_NAMES = frozenset({"linkedin", "indeed", "wellfound"})
API_BOARD_NAMES = frozenset({"greenhouse", "lever"})
ALL_KNOWN_BOARDS = BROWSER_BOARD_NAMES | API_BOARD_NAMES


def load_registered_boards(storage: StorageProvider) -> list[str]:
    if not storage.exists(_BOARDS_PATH):
        return []
    try:
        raw = json.loads(storage.read(_BOARDS_PATH).decode())
        return [str(b) for b in raw.get("boards", []) if isinstance(b, str)]
    except Exception:
        return []


def save_registered_boards(storage: StorageProvider, boards: list[str]) -> None:
    storage.atomic_write(_BOARDS_PATH, json.dumps({"boards": boards}, indent=2).encode())


def register_board(storage: StorageProvider, name: str) -> None:
    """Add `name` to the registered list if not already present."""
    current = load_registered_boards(storage)
    if name not in current:
        save_registered_boards(storage, current + [name])
